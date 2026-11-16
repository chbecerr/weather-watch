"use strict";

const STALE_HOURS = 12;
const TREND_MIN_MOVE = 5; // points; smaller moves are noise
const FACTOR_LABELS = { wind: "Wind", cold: "Cold", heat: "Heat", rain: "Rain", snow: "Snow", storm: "Storm" };
const $ = (id) => document.getElementById(id);

const LEAGUE_NAMES = { NFL: "NFL", CFB: "College" };

// Weather-event filters. These use their own, looser cutoffs on the forecast values (not the
// score's flags, which only appear once a factor adds 5+ points), so e.g. 0.1" of snow counts.
const EVENTS = [
  { key: "wind", label: "Wind", color: "wind", tip: "Sustained wind over 10 mph", test: (w) => w.wind_mph > 10 },
  { key: "cold", label: "Cold", color: "cold", tip: "Feels like 32° or colder", test: (w) => w.feels_like_min_f <= 32 },
  { key: "heat", label: "Heat", color: "heat", tip: "Feels like 90° or hotter", test: (w) => w.feels_like_max_f >= 90 },
  { key: "rain", label: "Rain", color: "rain", tip: "0.05″ of rain or more", test: (w) => w.rain_in >= 0.05 },
  { key: "snow", label: "Snow", color: "snow", tip: "0.1″ of snow or more", test: (w) => w.snow_in >= 0.1 },
  { key: "storm", label: "Storms", color: "storm", tip: "Thunderstorms in the forecast", test: (w) => /thunder/i.test(w.conditions || "") },
  { key: "likely", label: "Precip likely", color: "rain", tip: "A 50% or higher chance of rain or snow", test: (w) => (w.precip_chance ?? 0) >= 50 },
];
const state = {
  games: [], info: {}, hidden: {}, week: "this", autoNext: false,
  league: "all", day: "all", q: "", showAll: false, sort: "score", dir: "desc",
  favs: new Set(), mine: false, open: new Set(), events: new Set(),
};

// ---- tiny helpers -------------------------------------------------------

function el(tag, props, ...kids) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v == null || v === false) continue;
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (k.startsWith("data-") || k.startsWith("aria-") || k === "style" || k === "hidden" || k === "type") node.setAttribute(k, v === true ? "" : v);
    else node[k] = v;
  }
  for (const kid of kids.flat()) if (kid != null) node.append(kid);
  return node;
}

function store(key, value) {
  try {
    if (value === undefined) return JSON.parse(localStorage.getItem("ww." + key));
    localStorage.setItem("ww." + key, JSON.stringify(value));
  } catch (_) { /* private mode etc.: the page works without it */ }
  return null;
}

const dayFmt = new Intl.DateTimeFormat(undefined, { weekday: "short", month: "numeric", day: "numeric" });
const timeFmt = new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" });
const dayKey = (d) => `${d.getFullYear()}-${d.getMonth() + 1}-${d.getDate()}`;

function ago(ms) {
  const m = Math.round(ms / 60000);
  if (m < 2) return "just now";
  if (m < 90) return `${m} minutes ago`;
  const h = Math.round(m / 60);
  if (h < 48) return `${h} hours ago`;
  return `${Math.round(h / 24)} days ago`;
}

// ---- model --------------------------------------------------------------

// "Pittsburgh Steelers at Cincinnati Bengals" / "Minnesota Vikings vs San Francisco 49ers"
function teamsOf(matchup) {
  const m = /^(.*?) (?:at|vs\.?) (.*)$/i.exec(matchup || "");
  return m ? [m[1], m[2]] : [];
}
const isFav = (g) => g.teams.some((t) => state.favs.has(t));

function prepare(g) {
  const kickoff = new Date(g.kickoff_utc);
  const w = g.weather;
  return Object.assign({}, g, {
    kickoff,
    teams: teamsOf(g.matchup),
    day: dayKey(kickoff),
    hasScore: g.score != null && !g.indoor,
    sortTemp: w ? w.feels_like_min_f : null,
    sortWind: w ? Math.max(w.wind_mph, w.gust_mph) : null,
    sortPrecip: w ? w.snow_in * 10 + w.rain_in : null,
    haystack: [g.matchup, g.game, g.venue, g.location].join(" ").toLowerCase(),
  });
}

// Everything except the weather-event chips; the chips' counts are computed over this set.
function matchesBase(g) {
  if (g.week !== state.week) return false;
  if (!state.showAll && !g.hasScore) return false;
  if (state.league !== "all" && g.league !== state.league) return false;
  if (state.day !== "all" && g.day !== state.day) return false;
  const q = state.q.trim().toLowerCase();
  if (q && !g.haystack.includes(q)) return false;
  if (state.mine && !isFav(g)) return false;
  return true;
}

const matchesEvent = (g, ev) => Boolean(g.weather) && ev.test(g.weather);

// Picking several chips shows games that match any of them.
function visible() {
  const picked = EVENTS.filter((e) => state.events.has(e.key));
  return state.games.filter((g) => matchesBase(g) && (!picked.length || picked.some((e) => matchesEvent(g, e))));
}

const SORTS = {
  score: (g) => g.score,
  game: (g) => g.game.toLowerCase(),
  kickoff: (g) => g.kickoff.getTime(),
  temp: (g) => g.sortTemp,
  wind: (g) => g.sortWind,
  precip: (g) => g.sortPrecip,
};
// Which direction feels natural the first time a column is clicked.
const DEFAULT_DIR = { score: "desc", game: "asc", kickoff: "asc", temp: "asc", wind: "desc", precip: "desc" };

function sorted(list) {
  const key = SORTS[state.sort];
  const sign = state.dir === "asc" ? 1 : -1;
  return list.slice().sort((a, b) => {
    const x = key(a), y = key(b);
    if (x == null && y == null) return a.kickoff - b.kickoff;
    if (x == null) return 1; // games without data always sink to the bottom
    if (y == null) return -1;
    return (x < y ? -1 : x > y ? 1 : a.kickoff - b.kickoff) * sign;
  });
}

// ---- rendering ----------------------------------------------------------

function trendChip(tr) {
  const up = tr.delta > 0;
  const label = `${up ? "Up" : "Down"} ${Math.abs(tr.delta).toFixed(1)} points since ${tr.hours} hours ago (was ${tr.prev.toFixed(1)})`;
  return el("span", { class: "trend " + (up ? "up" : "down"), title: label },
    el("span", { "aria-hidden": "true", text: `${up ? "▲" : "▼"}${Math.round(Math.abs(tr.delta))}` }),
    el("span", { class: "sr", text: label }));
}

function sparkline(history) {
  const pts = (history || [])
    .map(([t, v]) => [new Date(t).getTime(), v])
    .filter(([t, v]) => Number.isFinite(t) && Number.isFinite(v));
  if (pts.length < 2) return null;
  const W = 220, H = 36, pad = 4;
  const t0 = pts[0][0], t1 = pts[pts.length - 1][0];
  const vmax = Math.max(10, ...pts.map((p) => p[1]));
  const x = (t) => pad + (t1 === t0 ? 0 : ((t - t0) / (t1 - t0)) * (W - 2 * pad));
  const y = (v) => H - pad - (v / vmax) * (H - 2 * pad);
  const NS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.setAttribute("class", "spark");
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", `Score history, oldest to newest: ${pts.map((p) => p[1].toFixed(0)).join(", ")}`);
  const path = document.createElementNS(NS, "path");
  path.setAttribute("d", pts.map(([t, v], i) => `${i ? "L" : "M"}${x(t).toFixed(1)} ${y(v).toFixed(1)}`).join(" "));
  const dot = document.createElementNS(NS, "circle");
  const last = pts[pts.length - 1];
  dot.setAttribute("cx", x(last[0]).toFixed(1)); dot.setAttribute("cy", y(last[1]).toFixed(1)); dot.setAttribute("r", "3");
  svg.append(path, dot);
  return svg;
}

function severity(score) {
  return score >= 30 ? "sev-severe" : score >= 10 ? "sev-moderate" : "sev-calm";
}

function precipText(w) {
  const chance = w.precip_chance != null && w.precip_chance >= 10 ? `${w.precip_chance}% chance` : "";
  if (w.snow_in >= 0.1) return { main: `${w.snow_in}″ snow`, sub: chance };
  if (w.rain_in >= 0.01) return { main: `${w.rain_in.toFixed(2)}″ rain`, sub: chance };
  return { main: "None", sub: chance };
}

function statBlock(label, main, sub) {
  return el("span", { class: "stat", "data-label": label }, el("span", { text: main }), sub ? el("span", { class: "sub", text: sub }) : null);
}

// Kickoff in the stadium's time zone, or null if that reads the same as the viewer's time.
function stadiumTime(g) {
  if (!g.venue_tz || g.time_tba) return null;
  try {
    const wall = (tz) => new Intl.DateTimeFormat("en-US", { hour: "numeric", minute: "2-digit", timeZone: tz }).format(g.kickoff);
    if (wall(g.venue_tz) === wall(undefined)) return null;
    return new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit", timeZone: g.venue_tz, timeZoneName: "short" }).format(g.kickoff);
  } catch (_) {
    return null;
  }
}

function renderGame(g, maxScore) {
  const w = g.weather;
  const li = el("li", { class: "game", "data-id": g.id });

  const score = el("span", { class: "score " + (g.hasScore ? severity(g.score) : "noscore") });
  score.append(el("span", { class: "num", text: g.hasScore ? g.score.toFixed(1) : "–" }));
  if (g.hasScore) {
    if (g.trend && Math.abs(g.trend.delta) >= TREND_MIN_MOVE) score.append(trendChip(g.trend));
    const pct = Math.max(2, Math.min(100, (g.score / maxScore) * 100));
    score.append(el("span", { class: "bar", "aria-hidden": "true" }, el("i", { style: `width:${pct}%` })));
  }

  const place = g.venue.includes(g.location) ? g.venue : `${g.venue} · ${g.location}`;
  const who = el("span", { class: "who" }, el("strong", { text: `${isFav(g) ? "★ " : ""}${g.league === "CFB" ? "🎓" : "🏈"} ${g.game}` }), el("small", { text: place }));

  const when = el("span", { class: "when" },
    el("span", { text: dayFmt.format(g.kickoff) }), " ",
    el("span", { class: "t", text: g.time_tba ? "TBA" : timeFmt.format(g.kickoff) }));
  const there = stadiumTime(g);
  if (there) when.append(el("span", { class: "local", text: `${there} there`, title: "Kickoff in the stadium's time zone" }));

  const stats = el("span", { class: "stats" });
  const flags = el("span", { class: "flags" });
  if (w) {
    const p = precipText(w);
    stats.append(
      statBlock("Temp", `${w.temp_f}°`, `feels ${w.feels_like_min_f}°`),
      statBlock("Wind", `${w.wind_mph} mph`, `gust ${w.gust_mph}`),
      statBlock("Precip", p.main, p.sub),
    );
  } else {
    stats.append(el("span", { class: "stat", "data-label": "Weather" }, el("span", { text: g.indoor ? "Indoor" : "No forecast" })));
  }
  if (g.status === "in") flags.append(el("span", { class: "chip LIVE", text: "LIVE" }));
  if (g.time_tba) flags.append(el("span", { class: "chip NOTE", text: "TIME TBA", title: "Kickoff time not set yet; the forecast uses ESPN's placeholder time" }));
  for (const t of g.tags || []) flags.append(el("span", { class: "chip " + t, text: t }));

  const detail = renderDetail(g);
  const startOpen = state.open.has(g.id);
  const btn = el("button", { class: "summary", type: "button", "aria-expanded": String(startOpen) }, score, who, when, stats, flags);
  btn.addEventListener("click", () => {
    const open = btn.getAttribute("aria-expanded") === "true";
    btn.setAttribute("aria-expanded", String(!open));
    detail.hidden = open;
    if (open) state.open.delete(g.id); else state.open.add(g.id);
  });
  detail.hidden = !startOpen;
  li.append(btn, detail);
  return li;
}

function renderDetail(g) {
  const d = el("div", { class: "detail" });
  d.append(el("p", { text: g.matchup }));
  if (g.weather) {
    d.append(el("p", { class: "muted", text: `${g.weather.conditions}. Feels like ${g.weather.feels_like_min_f}° to ${g.weather.feels_like_max_f}° during the game.${g.weather.precip_chance != null ? ` Up to a ${g.weather.precip_chance}% chance of precipitation.` : ""}` }));
  }
  if (g.trend) {
    const move = Math.abs(g.trend.delta) < 0.5 ? "unchanged" : `${g.trend.delta > 0 ? "up" : "down"} ${Math.abs(g.trend.delta).toFixed(1)}`;
    d.append(el("p", { text: `Score ${g.score.toFixed(1)} now, ${g.trend.prev.toFixed(1)} about ${g.trend.hours} hours ago (${move}).` }));
  }
  const spark = sparkline(g.history);
  if (spark) d.append(el("div", { class: "sparkwrap" }, el("span", { class: "muted", text: "Score history" }), spark));
  const entries = Object.entries(g.factors || {}).filter(([, v]) => v > 0).sort((a, b) => b[1] - a[1]);
  if (entries.length) {
    const top = Math.max(...entries.map(([, v]) => v));
    d.append(el("ul", { class: "factors" }, entries.map(([k, v]) =>
      el("li", { class: k.toUpperCase() },
        el("span", { text: FACTOR_LABELS[k] || k }),
        el("span", { class: "track", "aria-hidden": "true" }, el("i", { class: "fill", style: `width:${(v / top) * 100}%` })),
        el("span", { class: "pts", text: `${v.toFixed(1)}` })))));
  } else if (g.weather) {
    d.append(el("p", { class: "muted", text: "Nothing crossed a comfort threshold, so this game scores 0." }));
  }
  if (g.note) d.append(el("p", { class: "muted", text: g.note }));

  if (!g.time_tba) {
    const there = stadiumTime(g);
    d.append(el("p", { class: "muted", text: `Kickoff ${timeFmt.format(g.kickoff)} your time${there ? ` · ${there} at the stadium` : ""}.` }));
  }
  const actions = el("div", { class: "actions" });
  for (const team of g.teams) {
    const fav = state.favs.has(team);
    const b = el("button", {
      type: "button", class: "favbtn", "data-team": team, "aria-pressed": String(fav),
      title: fav ? "Remove from my teams" : "Add to my teams", text: `${fav ? "★" : "☆"} ${team}`,
    });
    b.addEventListener("click", () => toggleFav(team, g.id));
    actions.append(b);
  }
  if (g.link && g.link.startsWith("https://www.espn.com/")) {
    actions.append(el("a", { href: g.link, target: "_blank", rel: "noopener noreferrer", text: "Game page on ESPN ↗" }));
  }
  if (actions.childNodes.length) d.append(actions);
  return d;
}

function buildEventChips() {
  const box = $("eventChips");
  for (const ev of EVENTS) {
    const chip = el("button", { type: "button", class: "evchip", "data-ev": ev.key, "aria-pressed": "false", title: ev.tip },
      el("span", { text: ev.label }), " ", el("span", { class: "n", text: "0" }));
    chip.style.setProperty("--c", `var(--c-${ev.color})`);
    box.append(chip);
  }
  box.addEventListener("click", (e) => {
    const chip = e.target.closest("button[data-ev]");
    if (!chip || chip.disabled) return;
    const k = chip.dataset.ev;
    if (state.events.has(k)) state.events.delete(k); else state.events.add(k);
    render();
  });
  $("clearEvents").addEventListener("click", () => { state.events.clear(); render(); });
}

function syncEventChips() {
  const pool = state.games.filter(matchesBase);
  for (const chip of document.querySelectorAll("#eventChips .evchip")) {
    const ev = EVENTS.find((e) => e.key === chip.dataset.ev);
    const n = pool.filter((g) => matchesEvent(g, ev)).length;
    const on = state.events.has(ev.key);
    chip.querySelector(".n").textContent = String(n);
    chip.setAttribute("aria-pressed", String(on));
    chip.disabled = n === 0 && !on; // never strand someone in a dead end, but let them undo a pick
    chip.title = chip.disabled ? `${ev.tip} (no games match right now)` : ev.tip;
  }
  $("clearEvents").hidden = state.events.size === 0;
}

function toggleFav(team, gameId) {
  if (state.favs.has(team)) state.favs.delete(team); else state.favs.add(team);
  store("favs", [...state.favs]);
  render();
  // The list was rebuilt; put keyboard focus back where the user was.
  const again = [...document.querySelectorAll(".game")].find((li) => li.dataset.id === gameId);
  const btn = again && [...again.querySelectorAll(".favbtn")].find((b) => b.dataset.team === team);
  if (btn) btn.focus();
}

function render() {
  const rows = sorted(visible());
  const list = $("list");
  list.replaceChildren();
  const maxScore = Math.max(60, ...rows.map((g) => (g.hasScore ? g.score : 0)));
  rows.forEach((g) => list.append(renderGame(g, maxScore)));

  const inWeek = state.games.filter((g) => g.week === state.week && (state.showAll || g.hasScore)).length;
  const left = state.hidden[state.week] || 0;
  $("empty").hidden = rows.length > 0;
  $("empty").textContent = state.mine && state.favs.size === 0
    ? "You haven't picked any teams yet. Open a game and tap a team's star to add it to your teams."
    : inWeek === 0
      ? "No games with a forecast yet for this week. Forecasts reach about 7 days ahead, so check back later."
      : "No games match those filters.";
  $("count").textContent = [
    rows.length ? `${rows.length} game${rows.length === 1 ? "" : "s"}` : "",
    left ? `${left} not shown (no forecast yet)` : "",
  ].filter(Boolean).join(" · ");
  for (const b of document.querySelectorAll("#head button")) {
    const on = b.dataset.sort === state.sort;
    b.setAttribute("aria-pressed", String(on));
    b.dataset.arrow = on ? (state.dir === "asc" ? "▲" : "▼") : "";
  }
  syncEventChips();
  writeUrl();
}

// ---- shareable URLs -----------------------------------------------------
// Filters live in the query string (?week=next&league=NFL&sort=wind...) so a link keeps them.
// Favorites stay private to this browser and are not part of the link.

function readUrl() {
  const u = new URLSearchParams(location.search);
  const pick = (k, allowed) => (allowed.includes(u.get(k)) ? u.get(k) : null);
  const week = pick("week", ["this", "next"]);
  if (week) state.week = week;
  const league = pick("league", ["all", "NFL", "CFB"]);
  if (league) state.league = league;
  const sort = pick("sort", Object.keys(SORTS));
  const dir = pick("dir", ["asc", "desc"]);
  if (sort) { state.sort = sort; state.dir = dir || DEFAULT_DIR[sort]; }
  if (u.get("q")) state.q = u.get("q").slice(0, 80);
  if (u.get("day")) state.day = u.get("day"); // checked against the real days in populateDays
  if (u.get("indoor") === "1") state.showAll = true;
  state.events = new Set((u.get("event") || "").split(",").filter((k) => EVENTS.some((e) => e.key === k)));
  return Boolean(week);
}

function writeUrl() {
  const u = new URLSearchParams();
  if (state.week === "next" && !state.autoNext) u.set("week", "next");
  if (state.league !== "all") u.set("league", state.league);
  if (state.day !== "all") u.set("day", state.day);
  if (state.q.trim()) u.set("q", state.q.trim());
  if (state.sort !== "score" || state.dir !== "desc") { u.set("sort", state.sort); u.set("dir", state.dir); }
  if (state.showAll) u.set("indoor", "1");
  if (state.events.size) u.set("event", EVENTS.filter((e) => state.events.has(e.key)).map((e) => e.key).join(","));
  const qs = u.toString().replace(/%2C/g, ",");
  try { window.history.replaceState(null, "", location.pathname + (qs ? "?" + qs : "")); } catch (_) { /* e.g. sandboxed preview */ }
}

// ---- wiring -------------------------------------------------------------

function populateDays() {
  const sel = $("day");
  sel.replaceChildren(el("option", { value: "all", text: "All days" }));
  const seen = new Map();
  const inWeek = state.games.filter((g) => g.week === state.week && (state.showAll || g.hasScore));
  for (const g of inWeek.sort((a, b) => a.kickoff - b.kickoff)) {
    if (!seen.has(g.day)) seen.set(g.day, dayFmt.format(g.kickoff));
  }
  for (const [k, label] of seen) sel.append(el("option", { value: k, text: label }));
  if (!seen.has(state.day)) state.day = "all";
  sel.value = state.day;
}

function dateRange(first, last) {
  const a = dayFmt.format(new Date(first)), b = dayFmt.format(new Date(last));
  return a === b ? a : `${a} – ${b}`;
}

function renderWeekLabel() {
  const info = state.info[state.week] || {};
  const parts = Object.entries(info).map(([lg, w]) =>
    `${LEAGUE_NAMES[lg] || lg} ${w.label}${w.first ? ` (${dateRange(w.first, w.last)})` : ""}`);
  $("weeklabel").textContent = parts.join(" · ") + (state.autoNext ? ". Nothing left this week, so this is next week." : "");
}

function setupControls() {
  buildEventChips();
  const league = $("league");
  const syncLeague = () => league.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.v === state.league)));
  league.addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    state.league = b.dataset.v; store("league", state.league); syncLeague(); render();
  });
  syncLeague();

  const weekSeg = $("week");
  const syncWeek = () => weekSeg.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.v === state.week)));
  weekSeg.addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    state.week = b.dataset.v; state.autoNext = false; state.day = "all";
    syncWeek(); populateDays(); renderWeekLabel(); render();
  });
  syncWeek();

  $("day").addEventListener("change", (e) => { state.day = e.target.value; render(); });
  $("q").addEventListener("input", (e) => { state.q = e.target.value; render(); });
  $("q").value = state.q;
  $("mine").checked = state.mine;
  $("mine").addEventListener("change", (e) => { state.mine = e.target.checked; store("mine", state.mine); render(); });
  $("showAll").checked = state.showAll;
  $("showAll").addEventListener("change", (e) => { state.showAll = e.target.checked; store("showAll", state.showAll); populateDays(); render(); });

  $("head").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-sort]");
    if (!b) return;
    if (state.sort === b.dataset.sort) state.dir = state.dir === "asc" ? "desc" : "asc";
    else { state.sort = b.dataset.sort; state.dir = DEFAULT_DIR[state.sort]; }
    render();
  });
}

function showUpdated(generatedAt) {
  const t = new Date(generatedAt);
  const age = Date.now() - t.getTime();
  $("updated").textContent = `Forecast updated ${ago(age)} (${dayFmt.format(t)} ${timeFmt.format(t)}).`;
  if (age > STALE_HOURS * 3600e3) {
    const b = $("stale");
    b.textContent = `This data is ${ago(age).replace(" ago", "")} old, so the automatic refresh may have stopped. Treat the rankings with caution.`;
    b.hidden = false;
  }
}

async function main() {
  const saved = store("league");
  if (["all", "NFL", "CFB"].includes(saved)) state.league = saved;
  state.showAll = store("showAll") === true;
  state.mine = store("mine") === true;
  const savedFavs = store("favs");
  if (Array.isArray(savedFavs)) state.favs = new Set(savedFavs.filter((t) => typeof t === "string"));
  const weekFromUrl = readUrl(); // links override remembered preferences
  try {
    const res = await fetch("data.json", { cache: "no-cache" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    state.games = data.games.map(prepare);
    state.info = data.weeks || {};
    state.hidden = data.hidden || {};
    showUpdated(data.generated_at);
  } catch (err) {
    $("updated").textContent = "";
    const e = $("error");
    e.textContent = "Couldn't load the forecast data. Try reloading in a minute.";
    e.hidden = false;
    console.error(err);
    return;
  }
  const count = (w) => state.games.filter((g) => g.week === w && g.hasScore).length;
  if (!weekFromUrl && count("this") === 0 && count("next") > 0) { state.week = "next"; state.autoNext = true; }
  populateDays();
  setupControls();
  renderWeekLabel();
  render();
}

main();
