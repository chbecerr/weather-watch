"use strict";

// The weather map: a shaded national picture of one weather measure, with a dot per game.
//
// No map library and no outside requests: a bundled outline of the contiguous states (us-states.json),
// an Albers equal-area projection, and a canvas for the shading. The first half of this file is plain
// math (testable in Node: tests/js/map.test.js); the second half draws.
//
// How the shading is made. Each game pulls the colour towards its own value with a bell-shaped
// influence about SIGMA_MILES wide, so the colour at any point is the local average of nearby games.
// The opacity measures how much data is nearby: full where games are close together, fading to
// nothing where there are none (the Plains and Rockies have few games, and show as blank rather
// than as invented weather). The shading is an estimate; the dots are each game's own value.

(function (root) {
  const WIDTH = 975;
  const HEIGHT = 610;
  const PAD = 10;
  const SCALE = 2; // the canvas is drawn at twice the viewBox size so it stays sharp
  const GRID_COLUMNS = 160;
  const GRID_ROWS = 100;

  const SIGMA_MILES = 100;
  const MIN_SUPPORT = 0.12; // below this much nearby data (about 190 miles from a lone game) nothing is drawn
  const FULL_SUPPORT = 0.7; // at or above this the shading is fully opaque
  const MAX_OPACITY = 0.82;

  const MILES_PER_DEGREE = 69.05;
  const EARTH_MILES = 3958.8;
  const MIN_DOT_GAP = 9; // viewBox units
  const MAX_DOT_SHIFT = 12;

  const clamp = (x, lo, hi) => Math.min(hi, Math.max(lo, x));
  const rad = (deg) => (deg * Math.PI) / 180;
  const deg = (r) => (r * 180) / Math.PI;

  // ---- projection: Albers equal-area conic for the contiguous US -----------------------------------

  const PARALLELS = [29.5, 45.5];
  const ORIGIN = [-96, 37.5]; // lon, lat

  /** project(lon, lat) -> [x, y] in viewBox units; invert(x, y) -> [lon, lat]; fitted to `bounds` (lon/lat points). */
  function makeProjection(bounds) {
    const [p1, p2] = PARALLELS.map(rad);
    const n = (Math.sin(p1) + Math.sin(p2)) / 2;
    const C = Math.cos(p1) ** 2 + 2 * n * Math.sin(p1);
    const rho = (phi) => Math.sqrt(C - 2 * n * Math.sin(phi)) / n;
    const rho0 = rho(rad(ORIGIN[1]));
    const raw = (lon, lat) => {
      const r = rho(rad(lat));
      const theta = n * rad(lon - ORIGIN[0]);
      return [r * Math.sin(theta), rho0 - r * Math.cos(theta)]; // y up
    };
    const points = bounds.map(([lon, lat]) => raw(lon, lat));
    const xs = points.map((p) => p[0]);
    const ys = points.map((p) => p[1]);
    const cx = (Math.min(...xs) + Math.max(...xs)) / 2;
    const cy = (Math.min(...ys) + Math.max(...ys)) / 2;
    const scale = Math.min((WIDTH - 2 * PAD) / (Math.max(...xs) - Math.min(...xs)), (HEIGHT - 2 * PAD) / (Math.max(...ys) - Math.min(...ys)));
    return {
      project(lon, lat) {
        const [x, y] = raw(lon, lat);
        return [WIDTH / 2 + (x - cx) * scale, HEIGHT / 2 - (y - cy) * scale];
      },
      invert(px, py) {
        const x = (px - WIDTH / 2) / scale + cx;
        const y = (HEIGHT / 2 - py) / scale + cy;
        const r = Math.sqrt(x * x + (rho0 - y) ** 2);
        const theta = Math.atan2(x, rho0 - y);
        return [ORIGIN[0] + deg(theta / n), deg(Math.asin(clamp((C - (r * n) ** 2) / (2 * n), -1, 1)))];
      },
    };
  }

  function haversineMiles(lon1, lat1, lon2, lat2) {
    const dLat = rad(lat2 - lat1);
    const dLon = rad(lon2 - lon1);
    const a = Math.sin(dLat / 2) ** 2 + Math.cos(rad(lat1)) * Math.cos(rad(lat2)) * Math.sin(dLon / 2) ** 2;
    return 2 * EARTH_MILES * Math.asin(Math.min(1, Math.sqrt(a)));
  }

  /** Even-odd test: is the point inside any of these rings ([[lon, lat], ...])? */
  function insideRings(lon, lat, rings) {
    let inside = false;
    for (const ring of rings) {
      for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
        const [xi, yi] = ring[i];
        const [xj, yj] = ring[j];
        if (yi > lat !== yj > lat && lon < ((xj - xi) * (lat - yi)) / (yj - yi) + xi) inside = !inside;
      }
    }
    return inside;
  }

  // ---- colour scales ------------------------------------------------------------------------------

  function parseColor(hex) {
    const v = parseInt(hex.slice(1), 16);
    return [(v >> 16) & 255, (v >> 8) & 255, v & 255];
  }

  /** stops: [[value, "#rrggbb"], ...] ascending. Returns [r, g, b]; values outside the range take the end colours. */
  function colorAt(stops, value) {
    if (value <= stops[0][0]) return parseColor(stops[0][1]);
    for (let i = 1; i < stops.length; i++) {
      if (value <= stops[i][0]) {
        const [v0, c0] = stops[i - 1];
        const [v1, c1] = stops[i];
        const t = (value - v0) / (v1 - v0);
        const a = parseColor(c0);
        const b = parseColor(c1);
        return a.map((x, k) => Math.round(x + (b[k] - x) * t));
      }
    }
    return parseColor(stops[stops.length - 1][1]);
  }

  // Blue is cold and red is hot (ColorBrewer RdYlBu), which works for most kinds of colour blindness.
  const LENSES = {
    feels: {
      label: "Feels-like",
      note: "Coldest feels-like temperature during each game",
      stops: [[0, "#313695"], [20, "#4575b4"], [32, "#74add1"], [45, "#abd9e9"], [55, "#ffffbf"], [65, "#fee090"], [75, "#fdae61"], [85, "#f46d43"], [100, "#d73027"]],
      ticks: [0, 20, 40, 60, 80, 100],
      value: (w) => w.feels_like_min_f,
      format: (v) => `${Math.round(v)}°F`,
      tick: (v) => `${v}°`,
    },
    wind: {
      label: "Wind",
      note: "Sustained wind during each game (its highest hour)",
      stops: [[0, "#ffffd9"], [8, "#c7e9b4"], [14, "#7fcdbb"], [20, "#41b6c4"], [26, "#2c7fb8"], [35, "#253494"]],
      ticks: [0, 10, 20, 30],
      value: (w) => w.wind_mph,
      format: (v) => `${Math.round(v)} mph`,
      tick: (v) => `${v}`,
    },
    precip: {
      label: "Precipitation",
      note: "Chance of rain or snow during each game (its highest value)",
      stops: [[0, "#f7fbff"], [20, "#deebf7"], [40, "#9ecae1"], [60, "#4292c6"], [80, "#2171b5"], [100, "#084594"]],
      ticks: [0, 25, 50, 75, 100],
      value: (w) => (w.precip_chance == null ? null : w.precip_chance),
      format: (v) => `${Math.round(v)}% chance`,
      tick: (v) => `${v}%`,
    },
  };

  // ---- the shading --------------------------------------------------------------------------------

  const smoothstep = (e0, e1, x) => {
    const t = clamp((x - e0) / (e1 - e0), 0, 1);
    return t * t * (3 - 2 * t);
  };

  /** stations: [{lon, lat, value}]. Returns {value, support}: the local average and how much data backs it. */
  function smoothAt(lon, lat, stations) {
    let weight = 0;
    let weighted = 0;
    const reach = (4 * SIGMA_MILES) / MILES_PER_DEGREE; // beyond 4 sigma a game's influence is negligible
    for (const s of stations) {
      if (Math.abs(s.lat - lat) > reach) continue;
      const d = haversineMiles(lon, lat, s.lon, s.lat);
      if (d > 4 * SIGMA_MILES) continue;
      const w = Math.exp(-(d * d) / (2 * SIGMA_MILES * SIGMA_MILES));
      weight += w;
      weighted += w * s.value;
    }
    return { value: weight > 0 ? weighted / weight : NaN, support: weight };
  }

  /** The shading as a grid of cells: {columns, rows, value[], opacity[]}, opacity 0 where there is no data. */
  function computeField(stations, projection, columns = GRID_COLUMNS, rows = GRID_ROWS) {
    const value = new Float32Array(columns * rows);
    const opacity = new Float32Array(columns * rows);
    if (!stations.length) return { columns, rows, value, opacity };
    for (let j = 0; j < rows; j++) {
      for (let i = 0; i < columns; i++) {
        const [lon, lat] = projection.invert(((i + 0.5) / columns) * WIDTH, ((j + 0.5) / rows) * HEIGHT);
        const { value: v, support } = smoothAt(lon, lat, stations);
        value[j * columns + i] = v;
        opacity[j * columns + i] = MAX_OPACITY * smoothstep(MIN_SUPPORT, FULL_SUPPORT, support);
      }
    }
    return { columns, rows, value, opacity };
  }

  /** Nudge dots that would sit on top of each other apart (deterministically). points: [{x, y}], changed in place. */
  function declutter(points) {
    const home = points.map((p) => [p.x, p.y]);
    // Games at exactly the same spot (several in one city) go on a small ring around it, evenly spaced.
    const stacks = new Map();
    points.forEach((p, i) => {
      const key = `${p.x.toFixed(1)},${p.y.toFixed(1)}`;
      stacks.set(key, (stacks.get(key) || []).concat(i));
    });
    for (const members of stacks.values()) {
      if (members.length < 2) continue;
      const radius = Math.min(MAX_DOT_SHIFT, MIN_DOT_GAP / (2 * Math.sin(Math.PI / members.length)));
      members.forEach((index, k) => {
        const angle = (2 * Math.PI * k) / members.length - Math.PI / 2;
        points[index].x += radius * Math.cos(angle);
        points[index].y += radius * Math.sin(angle);
      });
    }
    // Then push apart any that are still too close.
    for (let round = 0; round < 12; round++) {
      let moved = false;
      for (let a = 0; a < points.length; a++) {
        for (let b = a + 1; b < points.length; b++) {
          let dx = points[b].x - points[a].x;
          let dy = points[b].y - points[a].y;
          let d = Math.hypot(dx, dy);
          if (d >= MIN_DOT_GAP) continue;
          if (d < 1e-6) {
            const angle = (a * 2.399963) % (2 * Math.PI); // golden angle: spreads exact duplicates evenly
            dx = Math.cos(angle);
            dy = Math.sin(angle);
            d = 1;
          }
          const push = (MIN_DOT_GAP - d) / 2;
          points[a].x -= (dx / d) * push;
          points[a].y -= (dy / d) * push;
          points[b].x += (dx / d) * push;
          points[b].y += (dy / d) * push;
          moved = true;
        }
      }
      for (let k = 0; k < points.length; k++) {
        const shiftX = points[k].x - home[k][0];
        const shiftY = points[k].y - home[k][1];
        const shift = Math.hypot(shiftX, shiftY);
        if (shift > MAX_DOT_SHIFT) {
          points[k].x = home[k][0] + (shiftX / shift) * MAX_DOT_SHIFT;
          points[k].y = home[k][1] + (shiftY / shift) * MAX_DOT_SHIFT;
        }
      }
      if (!moved) break;
    }
    return points;
  }

  /** The day (g.day) with the most games, earliest on a tie; null if there are none. */
  function busiestDay(games) {
    const counts = new Map();
    for (const g of games) counts.set(g.day, (counts.get(g.day) || 0) + 1);
    let best = null;
    for (const [day, n] of counts) if (best === null || n > counts.get(best)) best = day;
    return best;
  }

  // ---- drawing (browser only) ---------------------------------------------------------------------

  let outlinePromise = null;
  function loadOutline(url = "us-states.json") {
    if (!outlinePromise) {
      outlinePromise = fetch(url).then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      }).catch((err) => {
        outlinePromise = null; // let a later attempt retry
        throw err;
      });
    }
    return outlinePromise;
  }

  const NS = "http://www.w3.org/2000/svg";

  function ringPath(ring, projection, factor = 1) {
    return ring.map(([lon, lat], i) => {
      const [x, y] = projection.project(lon, lat);
      return `${i ? "L" : "M"}${(x * factor).toFixed(1)} ${(y * factor).toFixed(1)}`;
    }).join("") + "Z";
  }

  function projectionFor(outline) {
    return makeProjection(outline.states.flatMap((s) => s.rings.flat()));
  }

  /** Games the map can place under this lens: they have coordinates inside the map and a value. */
  function placeable(games, lens, projection) {
    const found = [];
    for (const g of games) {
      if (!g.weather || g.lat == null || g.lon == null) continue;
      const value = lens.value(g.weather);
      if (value == null || Number.isNaN(value)) continue;
      const [x, y] = projection.project(g.lon, g.lat);
      if (x < 0 || x > WIDTH || y < 0 || y > HEIGHT || g.lat < 24 || g.lat > 50 || g.lon < -125 || g.lon > -66) continue;
      found.push({ game: g, value, x, y });
    }
    return found;
  }

  function paintField(canvas, outline, projection, field, stops) {
    const ctx = canvas.getContext("2d");
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    const small = document.createElement("canvas");
    small.width = field.columns;
    small.height = field.rows;
    const sctx = small.getContext("2d");
    const image = sctx.createImageData(field.columns, field.rows);
    for (let k = 0; k < field.value.length; k++) {
      const a = field.opacity[k];
      if (a <= 0 || Number.isNaN(field.value[k])) continue;
      const [r, g, b] = colorAt(stops, field.value[k]);
      image.data.set([r, g, b, Math.round(a * 255)], k * 4);
    }
    sctx.putImageData(image, 0, 0);
    const clip = new Path2D(outline.states.map((s) => s.rings.map((ring) => ringPath(ring, projection, SCALE)).join("")).join(""));
    ctx.save();
    ctx.clip(clip, "nonzero");
    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = "high";
    ctx.drawImage(small, 0, 0, field.columns, field.rows, 0, 0, canvas.width, canvas.height);
    ctx.restore();
  }

  /**
   * Draw the map. options: {canvas, svg, outline, games, lens, selectedId, dimmedIds (Set), onSelect(id), onHover(id|null, x, y)}
   * Returns {shown, left, outline: projection} where `left` counts games that could not be placed.
   */
  function draw(options) {
    const { canvas, svg, outline, games, lens, selectedId, dimmedIds, onSelect, onHover } = options;
    const projection = projectionFor(outline);
    const placed = placeable(games, lens, projection);
    canvas.width = WIDTH * SCALE;
    canvas.height = HEIGHT * SCALE;
    const field = computeField(placed.map((p) => ({ lon: p.game.lon, lat: p.game.lat, value: p.value })), projection);
    paintField(canvas, outline, projection, field, lens.stops);

    svg.replaceChildren();
    const borders = document.createElementNS(NS, "path");
    borders.setAttribute("class", "mapborders");
    borders.setAttribute("d", outline.states.map((s) => s.rings.map((ring) => ringPath(ring, projection)).join("")).join(""));
    svg.append(borders);

    const dots = declutter(placed.map((p) => ({ x: p.x, y: p.y })));
    const order = placed.map((p, i) => i).sort((a, b) => {
      const rank = (i) => (placed[i].game.id === selectedId ? 2 : dimmedIds && dimmedIds.has(placed[i].game.id) ? 0 : 1);
      return rank(a) - rank(b); // dimmed first, the selected one last (on top)
    });
    for (const i of order) {
      const { game, value } = placed[i];
      const [r, g, b] = colorAt(lens.stops, value);
      const dot = document.createElementNS(NS, "circle");
      dot.setAttribute("cx", dots[i].x.toFixed(1));
      dot.setAttribute("cy", dots[i].y.toFixed(1));
      dot.setAttribute("r", game.id === selectedId ? 8 : 5.5);
      dot.setAttribute("fill", `rgb(${r},${g},${b})`);
      dot.setAttribute("class", "mapdot" + (game.id === selectedId ? " selected" : "") + (dimmedIds && dimmedIds.has(game.id) ? " dimmed" : ""));
      dot.setAttribute("tabindex", "0");
      dot.setAttribute("role", "button");
      dot.setAttribute("data-id", game.id);
      dot.setAttribute("aria-label", `${game.game}: ${lens.format(value)}. Show details.`);
      dot.addEventListener("click", () => onSelect && onSelect(game.id));
      dot.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onSelect && onSelect(game.id); }
      });
      dot.addEventListener("mouseenter", () => onHover && onHover(game.id, dots[i].x, dots[i].y));
      dot.addEventListener("focus", () => onHover && onHover(game.id, dots[i].x, dots[i].y));
      dot.addEventListener("mouseleave", () => onHover && onHover(null));
      dot.addEventListener("blur", () => onHover && onHover(null));
      svg.append(dot);
    }
    return { shown: placed.length, left: games.length - placed.length, values: placed.map((p) => ({ id: p.game.id, game: p.game.game, value: p.value })) };
  }

  /** Fill `element` with a gradient bar and tick labels for the lens. */
  function drawLegend(element, lens) {
    const first = lens.stops[0][0];
    const last = lens.stops[lens.stops.length - 1][0];
    const at = (v) => ((v - first) / (last - first)) * 100;
    element.replaceChildren();
    const bar = document.createElement("div");
    bar.className = "legendbar";
    bar.style.background = `linear-gradient(to right, ${lens.stops.map(([v, c]) => `${c} ${at(v).toFixed(1)}%`).join(", ")})`;
    const ticks = document.createElement("div");
    ticks.className = "legendticks";
    for (const v of lens.ticks) {
      const tick = document.createElement("span");
      tick.textContent = lens.tick(v);
      tick.style.left = `${at(v).toFixed(1)}%`;
      ticks.append(tick);
    }
    element.append(bar, ticks);
  }

  const api = {
    WIDTH, HEIGHT, LENSES, SIGMA_MILES, MIN_SUPPORT, FULL_SUPPORT, MAX_OPACITY, MIN_DOT_GAP, MAX_DOT_SHIFT,
    makeProjection, haversineMiles, insideRings, parseColor, colorAt, smoothAt, computeField, declutter, busiestDay,
    placeable, projectionFor, loadOutline, draw, drawLegend,
  };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.WeatherMap = api;
})(typeof window !== "undefined" ? window : globalThis);
