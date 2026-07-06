// Poll job status on set pages and reload when the pipeline finishes.
(function () {
  const progress = document.getElementById("progress");
  if (progress) {
    const setId = progress.dataset.setId;
    const base = progress.dataset.base;
    const bar = progress.querySelector(".progress-bar");
    const text = progress.querySelector(".progress-text");

    const poll = async () => {
      try {
        const response = await fetch(`${base}/api/sets/${setId}/status`);
        if (!response.ok) return;
        const status = await response.json();
        if (status.status === "done" || status.status === "failed" || status.duplicate_of) {
          window.location.reload();
          return;
        }
        if (status.progress_total > 0) {
          const pct = Math.max(3, Math.round((100 * status.progress_done) / status.progress_total));
          bar.style.width = `${pct}%`;
          text.textContent = `${status.status} ${status.progress_done}/${status.progress_total}`;
        } else {
          text.textContent = status.status;
        }
      } finally {
        setTimeout(poll, 4000);
      }
    };
    setTimeout(poll, 4000);
  }

  // Show chosen filename on the upload label.
  document.querySelectorAll(".file-label input[type=file]").forEach((input) => {
    input.addEventListener("change", () => {
      const label = input.closest(".file-label");
      const span = label.querySelector("span");
      if (input.files.length) {
        span.textContent = input.files[0].name;
        label.classList.add("has-file");
      }
    });
  });

  // Expand "sets containing this track" on the tracks page.
  document.querySelectorAll(".track-sets").forEach((link) => {
    link.addEventListener("click", async (event) => {
      event.preventDefault();
      const base = link.dataset.base;
      const key = encodeURIComponent(link.dataset.trackKey);
      const response = await fetch(`${base}/tracks/${key}/sets`);
      if (!response.ok) return;
      const sets = await response.json();
      const row = link.closest("tr");
      let detail = row.nextElementSibling;
      if (detail && detail.classList.contains("track-detail")) {
        detail.remove();
        return;
      }
      detail = document.createElement("tr");
      detail.className = "track-detail";
      const cell = document.createElement("td");
      cell.colSpan = row.children.length;
      cell.innerHTML = sets
        .map((s) => {
          const t = new Date(s.offset_seconds * 1000).toISOString().substr(11, 8);
          return `<a href="${base}/sets/${s.id}">${s.title || "set " + s.id}</a> <span class="mono">@ ${t}</span>`;
        })
        .join(" &nbsp;·&nbsp; ");
      detail.appendChild(cell);
      row.after(detail);
    });
  });
})();
