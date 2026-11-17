"use strict";
// Tests for the weather map's maths (docs/map.js). Run: node --test tests/js/*.test.js
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const M = require("../../docs/map.js");

const outline = JSON.parse(fs.readFileSync(path.join(__dirname, "../../docs/us-states.json"), "utf8"));
const allPoints = outline.states.flatMap((s) => s.rings.flat());
const projection = M.makeProjection(allPoints);
const near = (a, b, tol, msg) => assert.ok(Math.abs(a - b) <= tol, `${msg || ""} expected ${a} within ${tol} of ${b}`);

// Real stadium cities and the state each is in (several sit within a mile or two of a state line or river).
const CITIES = [
  ["Laramie", -105.59, 41.31, "Wyoming"], ["Boone", -81.67, 36.22, "North Carolina"], ["Lincoln", -96.70, 40.81, "Nebraska"],
  ["Miami", -80.19, 25.76, "Florida"], ["Seattle", -122.33, 47.61, "Washington"], ["Columbia SC", -81.03, 34.00, "South Carolina"],
  ["Cincinnati", -84.51, 39.10, "Ohio"], ["Covington KY", -84.51, 39.08, "Kentucky"], ["Kansas City MO", -94.58, 39.10, "Missouri"],
  ["Kansas City KS", -94.63, 39.11, "Kansas"], ["Texarkana TX", -94.05, 33.43, "Texas"], ["Memphis", -90.05, 35.15, "Tennessee"],
  ["Washington DC", -77.04, 38.90, "District of Columbia"], ["Philadelphia", -75.17, 39.95, "Pennsylvania"], ["New Orleans", -90.07, 29.95, "Louisiana"],
  ["Orchard Park", -78.74, 42.77, "New York"], ["Foxborough", -71.26, 42.09, "Massachusetts"], ["Minneapolis", -93.26, 44.98, "Minnesota"],
  ["Boise", -116.20, 43.62, "Idaho"], ["Tucson", -110.97, 32.22, "Arizona"], ["Stillwater", -97.06, 36.12, "Oklahoma"],
  ["Ann Arbor", -83.74, 42.28, "Michigan"], ["Morgantown", -79.96, 39.63, "West Virginia"], ["Missoula", -113.99, 46.87, "Montana"],
];
const stateAt = (lon, lat) => outline.states.find((s) => M.insideRings(lon, lat, s.rings));

test("the outline is the contiguous states and DC, and every real stadium city is in the right state", () => {
  assert.equal(outline.states.length, 49);
  assert.ok(!outline.states.some((s) => ["Alaska", "Hawaii"].includes(s.name)));
  for (const [city, lon, lat, want] of CITIES) assert.equal(stateAt(lon, lat)?.name, want, city);
});

test("points in the Gulf of Mexico, the Atlantic and Canada are outside every state", () => {
  for (const [lon, lat] of [[-90, 26], [-70, 35], [-100, 52], [-120, 30]]) assert.equal(stateAt(lon, lat), undefined, `${lon},${lat}`);
});

test("the projection puts every outline point inside the drawing with a margin", () => {
  for (const [lon, lat] of allPoints) {
    const [x, y] = projection.project(lon, lat);
    assert.ok(x >= 9.9 && x <= M.WIDTH - 9.9 && y >= 9.9 && y <= M.HEIGHT - 9.9, `${lon},${lat} -> ${x},${y}`);
  }
});

test("projecting and inverting gives back the same place", () => {
  for (const [, lon, lat] of CITIES) {
    const [x, y] = projection.project(lon, lat);
    const [lon2, lat2] = projection.invert(x, y);
    near(lon, lon2, 1e-6, "lon");
    near(lat, lat2, 1e-6, "lat");
  }
});

test("west is left and north is up", () => {
  const seattle = projection.project(-122.33, 47.61);
  const miami = projection.project(-80.19, 25.76);
  const maine = projection.project(-69.0, 45.0);
  assert.ok(seattle[0] < miami[0] && seattle[1] < miami[1]);
  assert.ok(maine[0] > miami[0] - 1 && maine[1] < miami[1]);
});

test("map distances stay in proportion to real distances (the projection isn't distorting badly)", () => {
  const places = CITIES.map(([, lon, lat]) => [lon, lat]);
  const ratios = [];
  for (let i = 0; i < places.length; i++) {
    for (let j = i + 1; j < places.length; j++) {
      const real = M.haversineMiles(...places[i], ...places[j]);
      if (real < 300) continue; // very short hops are dominated by rounding
      const [x1, y1] = projection.project(...places[i]);
      const [x2, y2] = projection.project(...places[j]);
      ratios.push(Math.hypot(x2 - x1, y2 - y1) / real);
    }
  }
  const sorted = ratios.slice().sort((a, b) => a - b);
  const median = sorted[Math.floor(sorted.length / 2)];
  assert.ok(ratios.length > 100);
  for (const r of ratios) assert.ok(Math.abs(r / median - 1) < 0.08, `ratio ${r} vs median ${median}`);
});

test("great-circle distance in miles", () => {
  assert.equal(M.haversineMiles(-80, 30, -80, 30), 0);
  near(M.haversineMiles(-74.006, 40.7128, -118.2437, 34.0522), 2445, 15, "NYC to LA");
  near(M.haversineMiles(-105.59, 41.31, -104.82, 41.14), 41, 5, "Laramie to Cheyenne");
});

test("colours: exact at the stops, blended between them, clamped outside", () => {
  const stops = M.LENSES.feels.stops;
  for (const [v, hex] of stops) assert.deepEqual(M.colorAt(stops, v), M.parseColor(hex), `stop ${v}`);
  assert.deepEqual(M.colorAt([[0, "#000000"], [10, "#ffffff"]], 5), [128, 128, 128]);
  assert.deepEqual(M.colorAt(stops, -50), M.parseColor(stops[0][1]));
  assert.deepEqual(M.colorAt(stops, 500), M.parseColor(stops[stops.length - 1][1]));
});

test("every lens: stops ascend, ticks are inside the scale, and cold is bluer than hot", () => {
  for (const [key, lens] of Object.entries(M.LENSES)) {
    const values = lens.stops.map((s) => s[0]);
    assert.deepEqual(values, values.slice().sort((a, b) => a - b), `${key} stops ascending`);
    for (const t of lens.ticks) assert.ok(t >= values[0] && t <= values[values.length - 1], `${key} tick ${t}`);
    assert.ok(lens.format(12).length > 0 && lens.tick(lens.ticks[0]).length > 0);
  }
  const cold = M.colorAt(M.LENSES.feels.stops, 5);
  const hot = M.colorAt(M.LENSES.feels.stops, 95);
  assert.ok(cold[2] > cold[0] && hot[0] > hot[2]);
  const calm = M.colorAt(M.LENSES.wind.stops, 0);
  const gale = M.colorAt(M.LENSES.wind.stops, 35);
  assert.ok(gale[0] < calm[0]); // windier is darker
});

test("lens values come from the forecast, and a missing chance of precipitation is skipped, not zero", () => {
  const w = { feels_like_min_f: 14, wind_mph: 18, precip_chance: 37 };
  assert.deepEqual([M.LENSES.feels.value(w), M.LENSES.wind.value(w), M.LENSES.precip.value(w)], [14, 18, 37]);
  assert.equal(M.LENSES.precip.value({ ...w, precip_chance: null }), null);
});

test("shading: a lone game colours its surroundings with its own value, with less data support further away", () => {
  const stations = [{ lon: -105.59, lat: 41.31, value: 14 }];
  const here = M.smoothAt(-105.59, 41.31, stations);
  near(here.value, 14, 1e-9);
  near(here.support, 1, 1e-9);
  const at = (miles) => M.smoothAt(-105.59, 41.31 + miles / 69.05, stations);
  near(at(100).support, Math.exp(-0.5), 0.01, "one sigma away");
  near(at(100).value, 14, 1e-9);
  assert.ok(at(100).support > at(200).support && at(200).support > at(300).support);
  assert.ok(at(200).support > M.MIN_SUPPORT * 0.9 && at(200).support < 0.2);
  assert.ok(at(500).support < 1e-6);
});

test("shading: the colour is an average weighted towards the nearer game", () => {
  const stations = [{ lon: -100, lat: 40, value: 0 }, { lon: -98, lat: 40, value: 100 }];
  const midpoint = M.smoothAt(-99, 40, stations).value;
  near(midpoint, 50, 0.5, "equidistant");
  // The influence is about 100 miles wide and these games are 106 miles apart, so they blend heavily:
  // the average leans towards the nearer game without jumping to it.
  const along = [-100, -99.7, -99.3, -99, -98.7, -98.3, -98].map((lon) => M.smoothAt(lon, 40, stations).value);
  assert.deepEqual(along, along.slice().sort((a, b) => a - b), "rises steadily from the cold game to the warm one");
  assert.ok(along[1] < 45 && along[5] > 55 && along[0] < 40 && along[6] > 60);
  const far = M.smoothAt(-60, 20, stations);
  assert.equal(far.support, 0);
  assert.ok(Number.isNaN(far.value));
});

test("shading: more games nearby means more support", () => {
  const one = M.smoothAt(-90, 35, [{ lon: -90, lat: 35.5, value: 50 }]).support;
  const four = M.smoothAt(-90, 35, [0, 1, 2, 3].map((i) => ({ lon: -90 + i * 0.2, lat: 35.5, value: 50 }))).support;
  assert.ok(four > 3 * one);
});

test("the shaded field: opaque near games, clear far from any, never more opaque than the maximum", () => {
  const stations = [
    { lon: -81.03, lat: 34.0, value: 60 }, { lon: -82.4, lat: 34.3, value: 62 }, { lon: -80.8, lat: 35.2, value: 58 }, { lon: -81.7, lat: 36.2, value: 55 },
    { lon: -105.59, lat: 41.31, value: 14 },
  ];
  const field = M.computeField(stations, projection);
  assert.equal(field.value.length, field.columns * field.rows);
  const cell = (lon, lat) => {
    const [x, y] = projection.project(lon, lat);
    return Math.floor((y / M.HEIGHT) * field.rows) * field.columns + Math.floor((x / M.WIDTH) * field.columns);
  };
  near(field.opacity[cell(-81.5, 34.8)], M.MAX_OPACITY, 1e-6, "middle of the Carolinas cluster");
  assert.ok(field.opacity[cell(-105.59, 41.31)] > 0.3, "lone game still colours its own area");
  assert.ok(field.opacity[cell(-105.59, 41.31)] < M.MAX_OPACITY);
  assert.equal(field.opacity[cell(-120, 45)], 0, "Oregon, far from everything");
  assert.equal(field.opacity[cell(-95, 31)], 0, "east Texas, far from everything");
  near(field.value[cell(-81.5, 34.8)], 59, 3, "an average of the nearby games");
  assert.ok(Math.max(...field.opacity) <= M.MAX_OPACITY + 1e-6);
  assert.ok(field.value[cell(-105.59, 41.31)] < 20, "Wyoming stays cold, not pulled towards the Carolinas");
});

test("no games, no shading", () => {
  const field = M.computeField([], projection);
  assert.equal(Math.max(...field.opacity), 0);
});

test("several games at exactly the same spot (a city with three) end up in a small ring, none on top of another", () => {
  for (const n of [2, 3, 4, 5, 7]) {
    const stack = Array.from({ length: n }, () => ({ x: 500, y: 250 }));
    M.declutter(stack);
    for (let a = 0; a < n; a++) {
      for (let b = a + 1; b < n; b++) assert.ok(Math.hypot(stack[a].x - stack[b].x, stack[a].y - stack[b].y) >= M.MIN_DOT_GAP - 1e-6, `${n} games: ${a},${b}`);
      assert.ok(Math.hypot(stack[a].x - 500, stack[a].y - 250) <= M.MAX_DOT_SHIFT + 1e-9);
    }
  }
});

test("dots on top of each other are pushed apart, and nothing else moves", () => {
  const same = Array.from({ length: 5 }, () => ({ x: 400, y: 300 }));
  M.declutter(same);
  for (let a = 0; a < same.length; a++) {
    for (let b = a + 1; b < same.length; b++) assert.ok(Math.hypot(same[a].x - same[b].x, same[a].y - same[b].y) > M.MIN_DOT_GAP * 0.8, `${a},${b}`);
    assert.ok(Math.hypot(same[a].x - 400, same[a].y - 300) <= M.MAX_DOT_SHIFT + 1e-9);
  }
  const apart = [{ x: 100, y: 100 }, { x: 300, y: 300 }, { x: 109.5, y: 100 }];
  M.declutter(apart);
  assert.deepEqual([apart[1].x, apart[1].y], [300, 300]);
  const again = Array.from({ length: 5 }, () => ({ x: 400, y: 300 }));
  M.declutter(again);
  assert.deepEqual(again, same, "deterministic");
});

test("busiest day, earliest on a tie", () => {
  const games = ["Fri", "Sat", "Sat", "Sat", "Thu", "Fri"].map((day) => ({ day }));
  assert.equal(M.busiestDay(games), "Sat");
  assert.equal(M.busiestDay(["A", "B", "A", "B"].map((day) => ({ day }))), "A");
  assert.equal(M.busiestDay([]), null);
});

test("only games with a place on the map and a value are placed", () => {
  const w = { feels_like_min_f: 30, wind_mph: 5, precip_chance: 10 };
  const games = [
    { id: "ok", game: "A", weather: w, lat: 41.3, lon: -105.6 },
    { id: "dome", game: "B", weather: null, lat: 33.0, lon: -97.0 },
    { id: "nocoords", game: "C", weather: w, lat: null, lon: null },
    { id: "hawaii", game: "D", weather: w, lat: 21.3, lon: -157.9 },
    { id: "mexico", game: "E", weather: w, lat: 19.4, lon: -99.1 },
    { id: "nochance", game: "F", weather: { ...w, precip_chance: null }, lat: 40, lon: -90 },
  ];
  assert.deepEqual(M.placeable(games, M.LENSES.feels, projection).map((p) => p.game.id), ["ok", "nochance"]);
  assert.deepEqual(M.placeable(games, M.LENSES.precip, projection).map((p) => p.game.id), ["ok"]);
});
