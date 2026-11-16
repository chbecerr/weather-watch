"""Data file for the static website (docs/data.json)."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path


class UnhealthyData(Exception):
    """The report looks broken; don't publish it over a good earlier one."""


def build_payload(rows: list[dict], days: int, now: datetime | None = None) -> dict:
    """Wrap report rows for the website, refusing obviously broken reports.

    A report where games exist but none of the outdoor ones got a forecast
    means the weather service failed; publishing it would blank the site.
    """
    outdoor = [r for r in rows if not r["indoor"]]
    if outdoor and not any(r["weather"] for r in outdoor):
        raise UnhealthyData(
            f"none of the {len(outdoor)} outdoor games got a forecast "
            "(is api.weather.gov or the city lookup down?)"
        )
    now = now or datetime.now(timezone.utc)
    return {
        "generated_at": now.astimezone(timezone.utc).isoformat(timespec="seconds"),
        "days": days,
        "source": "US National Weather Service",
        "games": rows,
    }


def write_payload(payload: dict, path: str | os.PathLike) -> None:
    """Write atomically so a crash can't leave a half-written data file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)
