"""Data file for the static website (docs/data.json)."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path


class UnhealthyData(Exception):
    """The report looks broken; don't publish it over a good earlier one."""


def build_payload(
    rows: list[dict], week_info: dict, hidden: dict, now: datetime | None = None
) -> dict:
    """Wrap report rows for the website, refusing obviously broken reports.

    If this week has outdoor games but none of them got a forecast, the weather
    service failed; publishing that would blank the site.
    """
    shown = sum(1 for r in rows if r["week"] == "this" and not r["indoor"])
    left_out = hidden.get("this", 0)
    if left_out and not shown:
        raise UnhealthyData(
            f"none of this week's {left_out} outdoor games got a forecast "
            "(is api.weather.gov or the city lookup down?)"
        )
    now = now or datetime.now(timezone.utc)
    return {
        "generated_at": now.astimezone(timezone.utc).isoformat(timespec="seconds"),
        "source": "US National Weather Service",
        "weeks": week_info,
        "hidden": hidden,
        "games": rows,
    }


def write_payload(payload: dict, path: str | os.PathLike) -> None:
    """Write atomically so a crash can't leave a half-written data file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)
