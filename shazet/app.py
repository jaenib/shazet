from __future__ import annotations

import hmac
from pathlib import Path
from urllib.parse import quote_plus

from fastapi import APIRouter, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import config, db, ingest, tracklist, worker

BASE = config.BASE_PATH
TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="shazet", docs_url=None, redoc_url=None)
router = APIRouter(prefix=BASE)
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))
templates.env.globals["BASE"] = BASE
templates.env.globals["CONFIDENCE_HIGH"] = config.CONFIDENCE_HIGH
templates.env.globals["CONFIDENCE_LOW"] = config.CONFIDENCE_LOW
templates.env.globals["quote_plus"] = quote_plus


@app.on_event("startup")
def startup():
    config.ensure_dirs()
    db.init_db()
    worker.start_worker()
    worker.requeue_unfinished()


@app.get("/", include_in_schema=False)
def root_redirect():
    return RedirectResponse(f"{BASE}/")


def _submission_allowed(token: str) -> bool:
    expected = config.submit_token()
    if not expected:
        return True
    return hmac.compare_digest(token.strip(), expected)


@router.get("/health")
def health():
    return {"ok": True}


@router.get("/", response_class=HTMLResponse)
def home(request: Request):
    with db.connect() as conn:
        running = db.active_sets(conn)
        recent = [record for record in db.list_sets(conn, limit=12) if record["status"] in {"done", "failed"}]
    return templates.TemplateResponse(
        "home.html",
        {
            "request": request,
            "running": running,
            "recent": recent,
            "token_required": bool(config.submit_token()),
        },
    )


@router.post("/submit")
async def submit(
    request: Request,
    source_url: str = Form(""),
    token: str = Form(""),
    force: str = Form(""),
    upload: UploadFile | None = File(None),
):
    if not _submission_allowed(token):
        raise HTTPException(status_code=403, detail="wrong access code")

    source_url = source_url.strip()
    has_upload = upload is not None and (upload.filename or "").strip()

    if not source_url and not has_upload:
        raise HTTPException(status_code=400, detail="provide a URL or an audio upload")
    if source_url and not ingest.is_supported_url(source_url):
        raise HTTPException(status_code=400, detail="only http(s) URLs are supported")

    with db.connect() as conn:
        # History first: a URL we already analyzed returns the existing tracklist.
        if source_url and not force:
            existing = db.find_done_set_by_url(conn, source_url)
            if existing is not None:
                return RedirectResponse(f"{BASE}/sets/{existing['id']}", status_code=303)

        if has_upload:
            set_id = db.create_set(
                conn,
                title=Path(upload.filename).stem,
                source_url="",
                source_kind="upload",
                segment_length=config.SEGMENT_LENGTH_SECONDS,
            )
        else:
            set_id = db.create_set(
                conn,
                title="",
                source_url=source_url,
                source_kind="url",
                segment_length=config.SEGMENT_LENGTH_SECONDS,
            )

    if has_upload:
        payload = await upload.read()
        try:
            ingest.store_upload(payload, upload.filename, set_id)
        except ingest.IngestError as exc:
            with db.connect() as conn:
                db.update_set(conn, set_id, status="failed", error=str(exc))
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    worker.enqueue(set_id)
    return RedirectResponse(f"{BASE}/sets/{set_id}", status_code=303)


@router.get("/sets", response_class=HTMLResponse)
def sets_index(request: Request, q: str = ""):
    with db.connect() as conn:
        sets = db.list_sets(conn, query=q.strip())
    return templates.TemplateResponse("sets.html", {"request": request, "sets": sets, "q": q.strip()})


@router.get("/sets/{set_id}", response_class=HTMLResponse)
def set_detail(request: Request, set_id: int):
    with db.connect() as conn:
        record = db.get_set(conn, set_id)
        if record is None:
            raise HTTPException(status_code=404)
        if record["duplicate_of"]:
            return RedirectResponse(f"{BASE}/sets/{record['duplicate_of']}")
        segments = db.get_segments(conn, set_id)

    entries = tracklist.build_entries(segments, int(record["segment_length"]))
    matched = sum(1 for segment in segments if segment["matched"])
    return templates.TemplateResponse(
        "set_detail.html",
        {
            "request": request,
            "record": record,
            "entries": entries,
            "segment_count": len(segments),
            "matched_count": matched,
            "format_timestamp": tracklist.format_timestamp,
            "is_running": record["status"] not in {"done", "failed"},
        },
    )


@router.get("/sets/{set_id}/export.txt", response_class=PlainTextResponse)
def export_txt(set_id: int):
    entries, record = _entries_for_export(set_id)
    return PlainTextResponse(
        tracklist.entries_to_text(entries),
        headers={"Content-Disposition": f'attachment; filename="set-{set_id}-tracklist.txt"'},
    )


@router.get("/sets/{set_id}/export.cue", response_class=PlainTextResponse)
def export_cue(set_id: int):
    entries, record = _entries_for_export(set_id)
    return PlainTextResponse(
        tracklist.entries_to_cue(entries, record["title"] or f"set {set_id}"),
        headers={"Content-Disposition": f'attachment; filename="set-{set_id}.cue"'},
    )


def _entries_for_export(set_id: int):
    with db.connect() as conn:
        record = db.get_set(conn, set_id)
        if record is None:
            raise HTTPException(status_code=404)
        if record["duplicate_of"]:
            return _entries_for_export(record["duplicate_of"])
        segments = db.get_segments(conn, set_id)
    return tracklist.build_entries(segments, int(record["segment_length"])), record


@router.get("/tracks", response_class=HTMLResponse)
def tracks_index(request: Request, q: str = ""):
    with db.connect() as conn:
        tracks = db.list_tracks(conn, query=q.strip())
    return templates.TemplateResponse("tracks.html", {"request": request, "tracks": tracks, "q": q.strip()})


@router.get("/tracks/{track_key}/sets")
def track_sets(track_key: str):
    with db.connect() as conn:
        return JSONResponse(db.sets_for_track(conn, track_key))


@router.get("/api/sets/{set_id}/status")
def set_status(set_id: int):
    with db.connect() as conn:
        record = db.get_set(conn, set_id)
    if record is None:
        raise HTTPException(status_code=404)
    return {
        "id": record["id"],
        "status": record["status"],
        "error": record["error"],
        "progress_done": record["progress_done"],
        "progress_total": record["progress_total"],
        "duplicate_of": record["duplicate_of"],
    }


app.include_router(router)
app.mount(f"{BASE}/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
