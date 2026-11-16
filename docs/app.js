"use strict";

const STALE_HOURS = 12;
const FACTOR_LABELS = { wind: "Wind", cold: "Cold", heat: "Heat", rain: "Rain", snow: "Snow", storm: "Storm" };
const $ = (id) => document.getElementById(id);

const state = { games: [], league: "all", day: "all", q: "", showAll: false, sort: "score", dir: "desc" };

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

function prepare(g) {
  const kickoff = new Date(g.kickoff_utc);
  const w = g.weather;
  return Object.assign({}, g, {
    kickoff,
    day: dayKey(kickoff),
    hasScore: g.score != null && !g.indoor,
    sortTemp: w ? w.feels_like_min_f : null,
    sortWind: w ? Math.max(w.wind_mph, w.gust_mph) : null,
    sortPrecip: w ? w.snow_in * 10 + w.rain_in : null,
    haystack: [g.matchup, g.game, g.venue, g.location].join(" ").toLowerCase(),
  });
}

function visible() {
  const q = state.q.trim().toLowerCase();
  return state.games.filter((g) => {
    if (!state.showAll && !g.hasScore) return false;
    if (state.league !== "all" && g.league !== state.league) return false;
    if (state.day !== "all" && g.day !== state.day) return false;
    if (q && !g.haystack.includes(q)) return false;
    return true;
  });
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

function severity(score) {
  return score >= 30 ? "sev-severe" : score >= 10 ? "sev-moderate" : "sev-calm";
}

function precipText(w) {
  if (w.snow_in >= 0.1) return { main: `${w.snow_in}″ snow`, sub: "" };
  if (w.rain_in >= 0.01) return { main: `${w.rain_in.toFixed(2)}″ rain`, sub: "" };
  return { main: "None", sub: "" };
}

function statBlock(label, main, sub) {
  return el("span", { class: "stat", "data-label": label }, el("span", { text: main }), sub ? el("span", { class: "sub", text: sub }) : null);
}

function renderGame(g, maxScore) {
  const w = g.weather;
  const li = el("li", { class: "game" });

  const score = el("span", { class: "score " + (g.hasScore ? severity(g.score) : "noscore") });
  score.append(el("span", { class: "num", text: g.hasScore ? g.score.toFixed(1) : "–" }));
  if (g.hasScore) {
    const pct = Math.max(2, Math.min(100, (g.score / maxScore) * 100));
    score.append(el("span", { class: "bar", "aria-hidden": "true" }, el("i", { style: `width:${pct}%` })));
  }

  const place = g.venue.includes(g.location) ? g.venue : `${g.venue} · ${g.location}`;
  const who = el("span", { class: "who" }, el("strong", { text: `${g.league === "CFB" ? "🎓" : "🏈"} ${g.game}` }), el("small", { text: place }));

  const when = el("span", { class: "when" },
    el("span", { text: dayFmt.format(g.kickoff) }), " ",
    el("span", { class: "t", text: g.time_tba ? "TBA" : timeFmt.format(g.kickoff) }));

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
  for (const t of g.tags || []) flags.append(el("span", { class: "chip " + t, text: t }));

  const detail = renderDetail(g);
  const btn = el("button", { class: "summary", type: "button", "aria-expanded": "false" }, score, who, when, stats, flags);
  btn.addEventListener("click", () => {
    const open = btn.getAttribute("aria-expanded") === "true";
    btn.setAttribute("aria-expanded", String(!open));
    detail.hidden = open;
  });
  detail.hidden = true;
  li.append(btn, detail);
  return li;
}

function renderDetail(g) {
  const d = el("div", { class: "detail" });
  d.append(el("p", { text: g.matchup }));
  if (g.weather) {
    d.append(el("p", { class: "muted", text: `${g.weather.conditions}. Feels like ${g.weather.feels_like_min_f}° to ${g.weather.feels_like_max_f}° during the game.` }));
  }
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
  return d;
}

function render() {
  const rows = sorted(visible());
  const list = $("list");
  list.replaceChildren();
  const maxScore = Math.max(60, ...rows.map((g) => (g.hasScore ? g.score : 0)));
  rows.forEach((g) => list.append(renderGame(g, maxScore)));

  $("empty").hidden = rows.length > 0 || !state.games.length;
  $("count").textContent = rows.length ? `${rows.length} game${rows.length === 1 ? "" : "s"}` : "";
  for (const b of document.querySelectorAll("#head button")) {
    const on = b.dataset.sort === state.sort;
    b.setAttribute("aria-pressed", String(on));
    b.dataset.arrow = on ? (state.dir === "asc" ? "▲" : "▼") : "";
  }
}

// ---- wiring -------------------------------------------------------------

function populateDays() {
  const sel = $("day");
  const seen = new Map();
  for (const g of state.games.slice().sort((a, b) => a.kickoff - b.kickoff)) {
    if (g.hasScore && !seen.has(g.day)) seen.set(g.day, dayFmt.format(g.kickoff));
  }
  for (const [k, label] of seen) sel.append(el("option", { value: k, text: label }));
  if (!seen.has(state.day)) state.day = "all";
  sel.value = state.day;
}

function setupControls() {
  const league = $("league");
  const syncLeague = () => league.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.v === state.league)));
  league.addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    state.league = b.dataset.v; store("league", state.league); syncLeague(); render();
  });
  syncLeague();

  $("day").addEventListener("change", (e) => { state.day = e.target.value; render(); });
  $("q").addEventListener("input", (e) => { state.q = e.target.value; render(); });
  $("showAll").checked = state.showAll;
  $("showAll").addEventListener("change", (e) => { state.showAll = e.target.checked; store("showAll", state.showAll); render(); });

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
  try {
    const res = await fetch("data.json", { cache: "no-cache" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    state.games = data.games.map(prepare);
    showUpdated(data.generated_at);
  } catch (err) {
    $("updated").textContent = "";
    const e = $("error");
    e.textContent = "Couldn't load the forecast data. Try reloading in a minute.";
    e.hidden = false;
    console.error(err);
    return;
  }
  populateDays();
  setupControls();
  render();
}

main();
