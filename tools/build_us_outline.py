"""Build docs/us-states.json: the contiguous US states, simplified, for the weather map.

Source: the `us-atlas` npm package (US Census Bureau cartographic boundaries, ISC license).

    npm pack us-atlas                              # in a new, empty directory
    tar -xzf us-atlas-*.tgz package/states-10m.json
    python3 -I tools/build_us_outline.py package/states-10m.json docs/us-states.json

Each shared border (a TopoJSON "arc") is simplified once, so neighbouring states keep matching
borders with no gaps or overlaps. Alaska, Hawaii and the territories are left out: the map is the
contiguous US. Standard library only.
"""

from __future__ import annotations

import json
import sys

LEFT_OUT = {"02", "15", "60", "66", "69", "72", "78"}  # AK, HI, AS, GU, MP, PR, VI
TOLERANCE = 0.02  # degrees (about 1.5 miles). 0.04 moved the Ohio River far enough that Cincinnati landed in Kentucky
DECIMALS = 2  # about 0.7 miles


def decode_arcs(topology: dict) -> list[list[tuple[float, float]]]:
    sx, sy = topology["transform"]["scale"]
    tx, ty = topology["transform"]["translate"]
    arcs = []
    for arc in topology["arcs"]:
        x = y = 0
        points = []
        for dx, dy in arc:
            x += dx
            y += dy
            points.append((x * sx + tx, y * sy + ty))
        arcs.append(points)
    return arcs


def simplify(points: list[tuple[float, float]], tolerance: float) -> list[tuple[float, float]]:
    """Douglas-Peucker, iterative. Endpoints are always kept."""
    if len(points) < 3:
        return points
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        first, last = stack.pop()
        (x1, y1), (x2, y2) = points[first], points[last]
        dx, dy = x2 - x1, y2 - y1
        length = (dx * dx + dy * dy) ** 0.5
        far, far_distance = -1, tolerance
        for i in range(first + 1, last):
            px, py = points[i]
            distance = (
                ((px - x1) ** 2 + (py - y1) ** 2) ** 0.5
                if length == 0
                else abs(dy * (px - x1) - dx * (py - y1)) / length
            )
            if distance > far_distance:
                far, far_distance = i, distance
        if far != -1:
            keep[far] = True
            stack += [(first, far), (far, last)]
    return [p for p, k in zip(points, keep) if k]


def ring_points(ring: list[int], arcs: list[list[tuple[float, float]]]) -> list[list[float]]:
    points: list[tuple[float, float]] = []
    for index in ring:
        arc = arcs[index] if index >= 0 else arcs[~index][::-1]
        points.extend(arc if not points else arc[1:])  # consecutive arcs share an end point
    return [[round(x, DECIMALS), round(y, DECIMALS)] for x, y in points]


def build(topology: dict) -> dict:
    arcs = [simplify(a, TOLERANCE) for a in decode_arcs(topology)]
    states = []
    for geometry in topology["objects"]["states"]["geometries"]:
        if geometry["id"] in LEFT_OUT:
            continue
        polygons = geometry["arcs"] if geometry["type"] == "MultiPolygon" else [geometry["arcs"]]
        rings = [ring_points(ring, arcs) for polygon in polygons for ring in polygon]
        states.append({"name": geometry["properties"]["name"], "fips": geometry["id"], "rings": rings})
    return {
        "source": "US Census Bureau cartographic boundaries via the us-atlas npm package (ISC license); "
                  "simplified, contiguous states and DC only. Built by tools/build_us_outline.py.",
        "states": sorted(states, key=lambda s: s["name"]),
    }


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 2
    with open(argv[1], encoding="utf-8") as fh:
        topology = json.load(fh)
    result = build(topology)
    with open(argv[2], "w", encoding="utf-8") as fh:
        json.dump(result, fh, separators=(",", ":"))
        fh.write("\n")
    points = sum(len(r) for s in result["states"] for r in s["rings"])
    print(f"{len(result['states'])} states, {points} points -> {argv[2]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
