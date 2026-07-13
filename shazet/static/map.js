// Library map: an everynoise-style landscape of the growing library.
// Artists are text labels; genre gives the hue, encounter count gives the
// size, and set co-occurrence pulls related artists together via a small
// force layout. The layout is seeded deterministically from names so the map
// stays recognizable between visits and only shifts as new music arrives.
(function () {
  const viewport = document.getElementById("map-viewport");
  if (!viewport) return;
  const canvas = document.getElementById("map-canvas");
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

  // Color is a function of location on the field (everynoise-style): the hue
  // walks the color wheel with the angle around the map center, so neighbors
  // share hues and each region of the landscape gets its own tint.
  function positionColor(x, y, name) {
    const hue = ((Math.atan2(y, x) / Math.PI) * 180 + 360) % 360;
    const radius = Math.sqrt(x * x + y * y);
    const saturation = 45 + Math.min(1, radius / 520) * 40;
    const lightness = 55 + (name ? hashString(name) % 18 : 8);
    return `hsl(${hue}, ${saturation}%, ${lightness}%)`;
  }

  // --- layout --------------------------------------------------------------
  function layout(data) {
    const nodes = data.artists.map((artist) => {
      const rng = mulberry32(hashString(artist.name));
      const genreRng = mulberry32(hashString(artist.genre || "unknown"));
      const genreAngle = genreRng() * Math.PI * 2;
      const genreRadius = 220 + genreRng() * 260;
      return {
        ...artist,
        x: Math.cos(genreAngle) * genreRadius + (rng() - 0.5) * 220,
        y: Math.sin(genreAngle) * genreRadius + (rng() - 0.5) * 220,
        vx: 0,
        vy: 0,
      };
    });

    const byName = new Map(nodes.map((node) => [node.name, node]));
    const links = data.links
      .map(([a, b, weight, sets]) => ({ a: byName.get(a), b: byName.get(b), weight, sets: sets || [] }))
      .filter((link) => link.a && link.b);

    // A playlist links every pair of its artists: a 100-track playlist is a
    // 100-node clique whose springs collapse it into an unreadable blob.
    // Damp spring force on high-degree nodes so big clouds stay spread out.
    const degree = new Map();
    for (const link of links) {
      degree.set(link.a.name, (degree.get(link.a.name) || 0) + 1);
      degree.set(link.b.name, (degree.get(link.b.name) || 0) + 1);
    }

    const iterations = Math.min(220, 80 + nodes.length * 2);
    for (let step = 0; step < iterations; step++) {
      const cooling = 1 - step / iterations;

      // pairwise repulsion
      for (let i = 0; i < nodes.length; i++) {
        for (let j = i + 1; j < nodes.length; j++) {
          const a = nodes[i];
          const b = nodes[j];
          let dx = a.x - b.x;
          let dy = a.y - b.y;
          let distSq = dx * dx + dy * dy;
          if (distSq < 1) {
            dx = (mulberry32(i * 7919 + j)() - 0.5) * 2;
            dy = 1;
            distSq = dx * dx + dy * dy;
          }
          const force = (2600 / distSq) * cooling;
          a.vx += dx * force * 0.01;
          a.vy += dy * force * 0.01;
          b.vx -= dx * force * 0.01;
          b.vy -= dy * force * 0.01;
        }
      }

      // co-occurrence springs
      for (const link of links) {
        const dx = link.b.x - link.a.x;
        const dy = link.b.y - link.a.y;
        const dist = Math.sqrt(dx * dx + dy * dy) || 1;
        const target = Math.max(60, 180 - link.weight * 25);
        const crowd = Math.max(degree.get(link.a.name) || 1, degree.get(link.b.name) || 1);
        const damp = 1 / Math.sqrt(1 + crowd / 8);
        const force = ((dist - target) / dist) * 0.02 * Math.min(link.weight, 5) * cooling * damp;
        link.a.vx += dx * force;
        link.a.vy += dy * force;
        link.b.vx -= dx * force;
        link.b.vy -= dy * force;
      }

      // gentle pull toward the middle keeps islands on screen
      for (const node of nodes) {
        node.vx -= node.x * 0.002 * cooling;
        node.vy -= node.y * 0.002 * cooling;
        node.x += Math.max(-14, Math.min(14, node.vx));
        node.y += Math.max(-14, Math.min(14, node.vy));
        node.vx *= 0.6;
        node.vy *= 0.6;
      }
    }

    // genre anchors at the centroid of their artists
    const genreCenters = new Map();
    for (const node of nodes) {
      const key = node.genre || "";
      if (!key) continue;
      const center = genreCenters.get(key) || { x: 0, y: 0, weight: 0 };
      center.x += node.x * node.hits;
      center.y += node.y * node.hits;
      center.weight += node.hits;
      genreCenters.set(key, center);
    }
    const genres = [];
    for (const [name, center] of genreCenters) {
      genres.push({ name, x: center.x / center.weight, y: center.y / center.weight });
    }
    return { nodes, links, genres };
  }

  // --- rendering -----------------------------------------------------------
  const world = { x: 0, y: 0, scale: 1 };

  function applyTransform() {
    canvas.style.transform = `translate(${world.x}px, ${world.y}px) scale(${world.scale})`;
  }

  function fontSize(hits, maxHits) {
    const t = Math.log(1 + hits) / Math.log(1 + Math.max(maxHits, 2));
    return 11 + t * 26;
  }

  function render(result) {
    canvas.innerHTML = "";
    const rect = viewport.getBoundingClientRect();
    const cx = rect.width / 2;
    const cy = rect.height / 2;
    const maxHits = Math.max(...result.nodes.map((node) => node.hits), 1);

    for (const genre of result.genres) {
      const label = document.createElement("span");
      label.className = "map-genre";
      label.textContent = genre.name.toLowerCase();
      label.style.left = `${cx + genre.x}px`;
      label.style.top = `${cy + genre.y}px`;
      label.style.color = positionColor(genre.x, genre.y, "");
      label.style.opacity = 0.3;
      canvas.appendChild(label);
    }

    for (const node of result.nodes) {
      const label = document.createElement("span");
      label.className = "map-node";
      label.textContent = node.name;
      label.style.left = `${cx + node.x}px`;
      label.style.top = `${cy + node.y}px`;
      label.style.fontSize = `${fontSize(node.hits, maxHits)}px`;
      label.style.color = positionColor(node.x, node.y, node.name);
      label.dataset.artist = node.name;
      label.addEventListener("mouseenter", () => highlight(node, result, true));
      label.addEventListener("mouseleave", () => highlight(node, result, false));
      label.addEventListener("click", (event) => {
        event.stopPropagation();
        openPanel(node);
      });
      node.el = label;
      canvas.appendChild(label);
    }
  }

  const neighborCache = new Map();
  function neighborsOf(node, result) {
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

  function highlight(node, result, on) {
    const related = neighborsOf(node, result);
    for (const other of result.nodes) {
      if (!other.el) continue;
      other.el.classList.toggle("dimmed", on && !related.has(other.name));
      other.el.classList.toggle("related", on && related.has(other.name) && other.name !== node.name);
    }
  }

  function openPanel(node) {
    panel.hidden = false;
    document.getElementById("panel-artist").textContent = node.name;
    const genreChip = document.getElementById("panel-genre");
    genreChip.textContent = node.genre || "genre unknown";
    genreChip.style.borderColor = positionColor(node.x, node.y, node.name);
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
  viewport.addEventListener("click", () => {
    panel.hidden = true;
  });

  // --- source toggles ---------------------------------------------------------
  // Every artist knows which sources (sets/playlists) it appeared in; unticking
  // a source hides artists that only exist through it. Positions stay fixed so
  // the map remains recognizable while filtering.
  function buildSourceToggles(data, result) {
    const wrap = document.getElementById("map-sources");
    const list = document.getElementById("map-sources-list");
    if (!wrap || !list || !(data.sources || []).length) return;
    wrap.hidden = false;

    const enabled = new Set(data.sources.map((source) => source.id));

    function applyFilter() {
      for (const node of result.nodes) {
        if (!node.el) continue;
        const visible = (node.set_ids || []).some((id) => enabled.has(id));
        node.el.style.display = visible ? "" : "none";
      }
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

    // Keep panel interaction from panning the map or closing the artist panel.
    for (const type of ["pointerdown", "click", "wheel"]) {
      wrap.addEventListener(type, (event) => event.stopPropagation());
    }
  }

  // --- pan & zoom ------------------------------------------------------------
  let dragging = null;
  viewport.addEventListener("pointerdown", (event) => {
    dragging = { x: event.clientX - world.x, y: event.clientY - world.y };
    viewport.setPointerCapture(event.pointerId);
  });
  viewport.addEventListener("pointermove", (event) => {
    if (!dragging) return;
    world.x = event.clientX - dragging.x;
    world.y = event.clientY - dragging.y;
    applyTransform();
  });
  viewport.addEventListener("pointerup", () => {
    dragging = null;
  });
  viewport.addEventListener(
    "wheel",
    (event) => {
      event.preventDefault();
      const rect = viewport.getBoundingClientRect();
      const mx = event.clientX - rect.left;
      const my = event.clientY - rect.top;
      const factor = event.deltaY < 0 ? 1.12 : 1 / 1.12;
      const next = Math.max(0.25, Math.min(4, world.scale * factor));
      world.x = mx - ((mx - world.x) * next) / world.scale;
      world.y = my - ((my - world.y) * next) / world.scale;
      world.scale = next;
      applyTransform();
    },
    { passive: false }
  );

  // --- boot ------------------------------------------------------------------
  fetch(`${base}/api/map`)
    .then((response) => response.json())
    .then((data) => {
      if (!data.artists.length) {
        empty.hidden = false;
        return;
      }
      empty.remove(); // display:flex in the CSS would beat the hidden attribute
      const result = layout(data);
      render(result);
      buildSourceToggles(data, result);
      canvas.classList.add("settled");
    });
})();
