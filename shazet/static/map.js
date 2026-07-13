// Library map: a navigable 3D landscape of the growing library.
// Artists are text labels floating in space; genre seeds regions,
// co-occurrence pulls artists together, and color follows location.
// Drag to orbit, scroll or pinch to dive, shift-drag (or two fingers)
// to pan, click a name for details, double-click to fly to it.
(function () {
  const viewport = document.getElementById("map-viewport");
  if (!viewport) return;
  const canvas = document.getElementById("map-canvas");
  const ctx = canvas.getContext("2d");
  const empty = document.getElementById("map-empty");
  const panel = document.getElementById("map-panel");
  const base = viewport.dataset.base;

  // --- deterministic pseudo-randomness -----------------------------------
  function hashString(value) {
    let h = 2166136261;
    for (let i = 0; i < value.length; i++) {
      h ^= value.charCodeAt(i);
      h = Math.imul(h, 16777619);
    }
    return h >>> 0;
  }

  function mulberry32(seed) {
    return function () {
      seed |= 0;
      seed = (seed + 0x6d2b79f5) | 0;
      let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  // --- 3D layout -------------------------------------------------------------
  function layout(data) {
    const nodes = data.artists.map((artist) => {
      const rng = mulberry32(hashString(artist.name));
      const genreRng = mulberry32(hashString(artist.genre || "unknown"));
      // Genre anchors live in a hash-seeded 3D field.
      const gx = (genreRng() - 0.5) * 1050;
      const gy = (genreRng() - 0.5) * 620;
      const gz = (genreRng() - 0.5) * 1050;
      return {
        ...artist,
        x: gx + (rng() - 0.5) * 230,
        y: gy + (rng() - 0.5) * 230,
        z: gz + (rng() - 0.5) * 230,
        vx: 0, vy: 0, vz: 0,
        hidden: false,
      };
    });

    const byName = new Map(nodes.map((node) => [node.name, node]));
    const links = data.links
      .map(([a, b, weight, sets]) => ({ a: byName.get(a), b: byName.get(b), weight, sets: sets || [] }))
      .filter((link) => link.a && link.b);

    // A playlist links every pair of its artists (an n-node clique); damp
    // spring force on high-degree nodes so big clouds stay spread out.
    const degree = new Map();
    for (const link of links) {
      degree.set(link.a.name, (degree.get(link.a.name) || 0) + 1);
      degree.set(link.b.name, (degree.get(link.b.name) || 0) + 1);
    }

    const iterations = Math.min(200, 80 + nodes.length * 2);
    for (let step = 0; step < iterations; step++) {
      const cooling = 1 - step / iterations;

      // pairwise repulsion
      for (let i = 0; i < nodes.length; i++) {
        for (let j = i + 1; j < nodes.length; j++) {
          const a = nodes[i];
          const b = nodes[j];
          let dx = a.x - b.x;
          let dy = a.y - b.y;
          let dz = a.z - b.z;
          let distSq = dx * dx + dy * dy + dz * dz;
          if (distSq < 1) {
            const jitter = mulberry32(i * 7919 + j);
            dx = (jitter() - 0.5) * 2;
            dy = 1;
            dz = jitter() - 0.5;
            distSq = dx * dx + dy * dy + dz * dz;
          }
          const force = (3400 / distSq) * cooling * 0.01;
          a.vx += dx * force; a.vy += dy * force; a.vz += dz * force;
          b.vx -= dx * force; b.vy -= dy * force; b.vz -= dz * force;
        }
      }

      // co-occurrence springs
      for (const link of links) {
        const dx = link.b.x - link.a.x;
        const dy = link.b.y - link.a.y;
        const dz = link.b.z - link.a.z;
        const dist = Math.sqrt(dx * dx + dy * dy + dz * dz) || 1;
        const target = Math.max(70, 200 - link.weight * 25);
        const crowd = Math.max(degree.get(link.a.name) || 1, degree.get(link.b.name) || 1);
        const damp = 1 / Math.sqrt(1 + crowd / 8);
        const force = ((dist - target) / dist) * 0.02 * Math.min(link.weight, 5) * cooling * damp;
        link.a.vx += dx * force; link.a.vy += dy * force; link.a.vz += dz * force;
        link.b.vx -= dx * force; link.b.vy -= dy * force; link.b.vz -= dz * force;
      }

      // gentle pull toward the middle keeps islands in reach
      for (const node of nodes) {
        node.vx -= node.x * 0.0018 * cooling;
        node.vy -= node.y * 0.0024 * cooling;
        node.vz -= node.z * 0.0018 * cooling;
        node.x += Math.max(-14, Math.min(14, node.vx));
        node.y += Math.max(-14, Math.min(14, node.vy));
        node.z += Math.max(-14, Math.min(14, node.vz));
        node.vx *= 0.6; node.vy *= 0.6; node.vz *= 0.6;
      }
    }

    // genre anchors at the (hit-weighted) centroid of their artists
    const genreCenters = new Map();
    for (const node of nodes) {
      const key = node.genre || "";
      if (!key) continue;
      const center = genreCenters.get(key) || { x: 0, y: 0, z: 0, weight: 0 };
      center.x += node.x * node.hits;
      center.y += node.y * node.hits;
      center.z += node.z * node.hits;
      center.weight += node.hits;
      genreCenters.set(key, center);
    }
    const genres = [];
    for (const [name, center] of genreCenters) {
      genres.push({
        name,
        x: center.x / center.weight,
        y: center.y / center.weight,
        z: center.z / center.weight,
      });
    }

    // color is a function of location: hue walks the compass of the field,
    // saturation grows away from the center
    for (const node of nodes) {
      node.hue = ((Math.atan2(node.z, node.x) / Math.PI) * 180 + 360) % 360;
      const radius = Math.sqrt(node.x * node.x + node.z * node.z);
      node.sat = 45 + Math.min(1, radius / 560) * 40;
      node.light = 55 + (hashString(node.name) % 18);
    }
    for (const genre of genres) {
      genre.hue = ((Math.atan2(genre.z, genre.x) / Math.PI) * 180 + 360) % 360;
    }

    return { nodes, links, genres };
  }

  // --- camera & projection -----------------------------------------------------
  const FOCAL = 900;
  const NEAR = 70;
  const cam = { yaw: 0.55, pitch: 0.16, dist: 1500, target: { x: 0, y: 0, z: 0 } };

  let width = 0;
  let height = 0;
  const dpr = Math.min(2, window.devicePixelRatio || 1);

  function resize() {
    const rect = viewport.getBoundingClientRect();
    width = rect.width;
    height = rect.height;
    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(height * dpr);
    canvas.style.width = `${width}px`;
    canvas.style.height = `${height}px`;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    needsRender = true;
  }

  function project(p) {
    let x = p.x - cam.target.x;
    let y = p.y - cam.target.y;
    let z = p.z - cam.target.z;
    const cy = Math.cos(cam.yaw), sy = Math.sin(cam.yaw);
    const x1 = x * cy - z * sy;
    const z1 = x * sy + z * cy;
    const cp = Math.cos(cam.pitch), sp = Math.sin(cam.pitch);
    const y1 = y * cp - z1 * sp;
    const z2 = y * sp + z1 * cp;
    const zc = z2 + cam.dist;
    if (zc < NEAR) return null;
    const s = FOCAL / zc;
    return { x: width / 2 + x1 * s, y: height / 2 + y1 * s, s, zc };
  }

  // camera-space right/"screen-down" vectors in world coordinates (for panning)
  function cameraAxes() {
    const cy = Math.cos(cam.yaw), sy = Math.sin(cam.yaw);
    const cp = Math.cos(cam.pitch), sp = Math.sin(cam.pitch);
    return {
      right: { x: cy, y: 0, z: -sy },
      down: { x: -sp * sy, y: cp, z: -sp * cy },
    };
  }

  function panBy(dxPx, dyPx) {
    const k = cam.dist / FOCAL;
    const axes = cameraAxes();
    cam.target.x -= (dxPx * axes.right.x + dyPx * axes.down.x) * k;
    cam.target.y -= (dxPx * axes.right.y + dyPx * axes.down.y) * k;
    cam.target.z -= (dxPx * axes.right.z + dyPx * axes.down.z) * k;
  }

  // --- rendering -----------------------------------------------------------
  let result = null;
  let maxHits = 1;
  let hovered = null;
  let needsRender = true;
  let settled = false;
  const mouse = { x: -1, y: -1, active: false };

  function fontWorldSize(hits) {
    const t = Math.log(1 + hits) / Math.log(1 + Math.max(maxHits, 2));
    return 14 + t * 30;
  }

  const neighborCache = new Map();
  function neighborsOf(node) {
    if (!neighborCache.has(node.name)) {
      const names = new Set([node.name]);
      for (const link of result.links) {
        if (link.a.name === node.name) names.add(link.b.name);
        if (link.b.name === node.name) names.add(link.a.name);
      }
      neighborCache.set(node.name, names);
    }
    return neighborCache.get(node.name);
  }

  function draw() {
    ctx.clearRect(0, 0, width, height);
    if (!result) return;

    const related = hovered ? neighborsOf(hovered) : null;
    const drawables = [];

    for (const genre of result.genres) {
      const p = project(genre);
      if (!p) continue;
      drawables.push({ kind: "genre", item: genre, p });
    }
    let hoverCandidate = null;
    for (const node of result.nodes) {
      if (node.hidden) continue;
      const p = project(node);
      if (!p) continue;
      const px = fontWorldSize(node.hits) * p.s;
      if (px < 2.6) continue;
      node.px = p.x; node.py = p.y; node.ps = px; // cached for hit-testing
      drawables.push({ kind: "node", item: node, p, px });
      if (mouse.active) {
        const reach = Math.max(13, px * 0.6);
        const dx = mouse.x - p.x;
        const dy = mouse.y - p.y;
        if (dx * dx + dy * dy < reach * reach && (!hoverCandidate || p.s > hoverCandidate.p.s)) {
          hoverCandidate = { node, p };
        }
      }
    }

    drawables.sort((a, b) => b.p.zc - a.p.zc); // painter's: far first

    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    for (const d of drawables) {
      if (d.kind === "genre") {
        const px = Math.min(210, 130 * d.p.s);
        if (px < 9) continue;
        const fog = Math.max(0, Math.min(1, (d.p.s - 0.14) * 2.4));
        ctx.font = `700 ${px}px ui-monospace, "SF Mono", Menlo, monospace`;
        ctx.fillStyle = `hsla(${d.item.hue}, 60%, 70%, ${0.05 + fog * 0.13})`;
        ctx.fillText(d.item.name.toUpperCase(), d.p.x, d.p.y);
        continue;
      }
      const node = d.item;
      const px = Math.min(72, d.px);
      let alpha = Math.max(0.1, Math.min(1, (d.p.s - 0.16) * 2.6)) * 0.96;
      if (hovered) {
        if (node === hovered) alpha = 1;
        else if (related && related.has(node.name)) alpha = Math.max(alpha, 0.9);
        else alpha *= 0.16;
      }
      ctx.font = `${node === hovered ? "700 " : ""}${px}px "Iowan Old Style", "Palatino", Georgia, serif`;
      const color = `hsla(${node.hue}, ${node.sat}%, ${node.light}%, ${alpha})`;
      if (node === hovered || (related && related.has(node.name))) {
        ctx.shadowColor = color;
        ctx.shadowBlur = node === hovered ? 18 : 9;
      } else {
        ctx.shadowBlur = 0;
      }
      ctx.fillStyle = color;
      ctx.fillText(node.name, d.p.x, d.p.y);
    }
    ctx.shadowBlur = 0;

    const nextHover = hoverCandidate ? hoverCandidate.node : null;
    if (nextHover !== hovered) {
      hovered = nextHover;
      viewport.style.cursor = hovered ? "pointer" : "grab";
      needsRender = true; // restyle with the new highlight next frame
    }

    if (!settled) {
      settled = true;
      canvas.classList.add("settled");
    }
  }

  // --- fly-to animation ------------------------------------------------------
  let flight = null;
  function flyTo(node) {
    flight = {
      t0: performance.now(),
      ms: 750,
      from: { x: cam.target.x, y: cam.target.y, z: cam.target.z, dist: cam.dist },
      to: { x: node.x, y: node.y, z: node.z, dist: Math.max(340, cam.dist * 0.4) },
    };
  }

  function stepFlight(now) {
    if (!flight) return false;
    const t = Math.min(1, (now - flight.t0) / flight.ms);
    const e = t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2; // easeInOutQuad
    cam.target.x = flight.from.x + (flight.to.x - flight.from.x) * e;
    cam.target.y = flight.from.y + (flight.to.y - flight.from.y) * e;
    cam.target.z = flight.from.z + (flight.to.z - flight.from.z) * e;
    cam.dist = flight.from.dist + (flight.to.dist - flight.from.dist) * e;
    if (t >= 1) flight = null;
    return true;
  }

  // --- render loop -------------------------------------------------------------
  let lastInteract = performance.now();
  let lastFrame = performance.now();
  const inertia = { yaw: 0, pitch: 0 };
  const reducedMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function loop(now) {
    const dt = Math.min(50, now - lastFrame);
    lastFrame = now;

    if (stepFlight(now)) needsRender = true;

    if (Math.abs(inertia.yaw) > 0.00004 || Math.abs(inertia.pitch) > 0.00004) {
      cam.yaw += inertia.yaw * dt;
      cam.pitch = Math.max(-1.35, Math.min(1.35, cam.pitch + inertia.pitch * dt));
      inertia.yaw *= Math.pow(0.994, dt);
      inertia.pitch *= Math.pow(0.994, dt);
      needsRender = true;
    }

    // idle drift: the landscape turns slowly until you touch it again
    if (!reducedMotion && !flight && now - lastInteract > 6000) {
      cam.yaw += 0.000045 * dt;
      needsRender = true;
    }

    if (needsRender) {
      needsRender = false;
      draw();
    }
    requestAnimationFrame(loop);
  }

  // --- artist panel ------------------------------------------------------------
  function openPanel(node) {
    panel.hidden = false;
    document.getElementById("panel-artist").textContent = node.name;
    const genreChip = document.getElementById("panel-genre");
    genreChip.textContent = node.genre || "genre unknown";
    genreChip.style.borderColor = `hsl(${node.hue}, ${node.sat}%, 45%)`;
    document.getElementById("panel-meta").textContent =
      `${node.track_count} track${node.track_count === 1 ? "" : "s"} · ` +
      `${node.sets} set${node.sets === 1 ? "" : "s"} · ${node.hits} segment hits`;
    const list = document.getElementById("panel-tracks");
    list.innerHTML = "";
    for (const track of node.tracks) {
      const item = document.createElement("li");
      item.textContent = track.title;
      list.appendChild(item);
    }
    document.getElementById("panel-tracks-link").href =
      `${base}/tracks?q=${encodeURIComponent(node.name)}`;
  }

  panel.querySelector(".map-panel-close").addEventListener("click", () => {
    panel.hidden = true;
  });

  function hitTest(x, y) {
    if (!result) return null;
    let best = null;
    let bestDist = Infinity;
    for (const node of result.nodes) {
      if (node.hidden || node.px === undefined) continue;
      const reach = Math.max(16, (node.ps || 12) * 0.6);
      const dx = x - node.px;
      const dy = y - node.py;
      const distSq = dx * dx + dy * dy;
      if (distSq < reach * reach && distSq < bestDist) {
        best = node;
        bestDist = distSq;
      }
    }
    return best;
  }

  // --- controls ------------------------------------------------------------
  // One pointer orbits (shift-drag pans); two pointers pinch-dolly and pan.
  const pointers = new Map();
  let drag = null;
  let pinch = null;
  let movedPx = 0;

  viewport.addEventListener("pointerdown", (event) => {
    lastInteract = performance.now();
    pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
    viewport.setPointerCapture(event.pointerId);
    movedPx = 0;
    inertia.yaw = 0;
    inertia.pitch = 0;
    if (pointers.size === 1) {
      drag = { x: event.clientX, y: event.clientY, pan: event.shiftKey };
    } else {
      drag = null;
      pinch = null; // measured fresh on the next move
    }
  });

  viewport.addEventListener("pointermove", (event) => {
    const rect = viewport.getBoundingClientRect();
    mouse.x = event.clientX - rect.left;
    mouse.y = event.clientY - rect.top;
    mouse.active = event.pointerType !== "touch";
    if (!pointers.has(event.pointerId)) {
      needsRender = true; // plain hover
      return;
    }
    lastInteract = performance.now();
    pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });

    if (pointers.size === 2) {
      const [a, b] = [...pointers.values()];
      const dist = Math.hypot(a.x - b.x, a.y - b.y) || 1;
      const mx = (a.x + b.x) / 2;
      const my = (a.y + b.y) / 2;
      if (pinch) {
        cam.dist = Math.max(240, Math.min(5200, cam.dist * (pinch.dist / dist)));
        panBy(mx - pinch.mx, my - pinch.my);
      }
      pinch = { dist, mx, my };
      needsRender = true;
    } else if (drag) {
      const dx = event.clientX - drag.x;
      const dy = event.clientY - drag.y;
      movedPx += Math.abs(dx) + Math.abs(dy);
      if (drag.pan) {
        panBy(dx, dy);
      } else {
        cam.yaw -= dx * 0.0042;
        cam.pitch = Math.max(-1.35, Math.min(1.35, cam.pitch + dy * 0.0042));
        inertia.yaw = (-dx * 0.0042) / 16;
        inertia.pitch = (dy * 0.0042) / 16;
      }
      drag.x = event.clientX;
      drag.y = event.clientY;
      needsRender = true;
    }
  });

  function releasePointer(event) {
    const wasDragging = pointers.size > 0;
    pointers.delete(event.pointerId);
    pinch = null;
    if (pointers.size === 1) {
      const [p] = [...pointers.values()];
      drag = { x: p.x, y: p.y, pan: false };
    } else if (!pointers.size) {
      drag = null;
      // a press that barely moved is a tap/click: open (or close) the panel
      if (wasDragging && movedPx < 6) {
        const rect = viewport.getBoundingClientRect();
        const node = hitTest(event.clientX - rect.left, event.clientY - rect.top);
        if (node) openPanel(node);
        else panel.hidden = true;
        inertia.yaw = 0;
        inertia.pitch = 0;
      }
    }
  }
  viewport.addEventListener("pointerup", releasePointer);
  viewport.addEventListener("pointercancel", releasePointer);
  viewport.addEventListener("pointerleave", () => {
    mouse.active = false;
    needsRender = true;
  });

  viewport.addEventListener("dblclick", (event) => {
    const rect = viewport.getBoundingClientRect();
    const node = hitTest(event.clientX - rect.left, event.clientY - rect.top);
    if (node) flyTo(node);
  });

  viewport.addEventListener(
    "wheel",
    (event) => {
      event.preventDefault();
      lastInteract = performance.now();
      const factor = event.deltaY > 0 ? 1.1 : 1 / 1.1;
      cam.dist = Math.max(240, Math.min(5200, cam.dist * factor));
      needsRender = true;
    },
    { passive: false }
  );

  // --- source toggles ---------------------------------------------------------
  function buildSourceToggles(data) {
    const wrap = document.getElementById("map-sources");
    const list = document.getElementById("map-sources-list");
    if (!wrap || !list || !(data.sources || []).length) return;
    wrap.hidden = false;

    const enabled = new Set(data.sources.map((source) => source.id));

    function applyFilter() {
      for (const node of result.nodes) {
        node.hidden = !(node.set_ids || []).some((id) => enabled.has(id));
        if (node.hidden) node.px = undefined;
      }
      needsRender = true;
    }

    const kinds = [
      ["set", "sets"],
      ["playlist", "playlists"],
    ];
    for (const [kind, heading] of kinds) {
      const sources = data.sources.filter((source) => source.kind === kind);
      if (!sources.length) continue;

      const header = document.createElement("div");
      header.className = "map-sources-group";
      const title = document.createElement("span");
      title.textContent = heading;
      header.appendChild(title);
      for (const [label, on] of [["all", true], ["none", false]]) {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.textContent = label;
        btn.addEventListener("click", () => {
          for (const source of sources) {
            enabled[on ? "add" : "delete"](source.id);
            source.checkbox.checked = on;
          }
          applyFilter();
        });
        header.appendChild(btn);
      }
      list.appendChild(header);

      for (const source of sources) {
        const row = document.createElement("label");
        const checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        checkbox.checked = true;
        checkbox.addEventListener("change", () => {
          enabled[checkbox.checked ? "add" : "delete"](source.id);
          applyFilter();
        });
        source.checkbox = checkbox;
        row.appendChild(checkbox);
        const text = document.createElement("span");
        text.textContent = source.title;
        text.title = source.added_by ? `${source.title} — added by ${source.added_by}` : source.title;
        row.appendChild(text);
        list.appendChild(row);
      }
    }

    // Keep panel interaction from orbiting the map underneath.
    for (const type of ["pointerdown", "pointerup", "click", "dblclick", "wheel"]) {
      wrap.addEventListener(type, (event) => event.stopPropagation());
    }
  }

  // --- boot ------------------------------------------------------------------
  resize();
  window.addEventListener("resize", resize);

  fetch(`${base}/api/map`)
    .then((response) => response.json())
    .then((data) => {
      if (!data.artists.length) {
        empty.hidden = false;
        return;
      }
      empty.remove(); // display:flex in the CSS would beat the hidden attribute
      result = layout(data);
      maxHits = Math.max(...result.nodes.map((node) => node.hits), 1);
      buildSourceToggles(data);
      needsRender = true;
      requestAnimationFrame(loop);
    });
})();
