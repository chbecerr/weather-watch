"""Data file for the static website (docs/data.json)."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import history
from .scoring import SCORE_VERSION


SOON_DAYS = 5  # the Weather Service looks ~7 days ahead; inside this, a missing forecast is a problem


class UnhealthyData(Exception):
    """The report looks broken; don't publish it over a good earlier one."""


def build_payload(
    rows: list[dict],
    week_info: dict,
    hidden: dict,
    now: datetime | None = None,
    previous: dict | None = None,
    hidden_soon: dict | None = None,
) -> dict:
    """Wrap report rows for the website, refusing obviously broken reports.

    If games that kick off soon enough to have a forecast all lack one, the weather
    service failed; publishing that would blank the site. Games months away (the
    offseason, or a distant bowl game) legitimately have no forecast and don't count.
    `hidden_soon` is the count of such near-term games; without it, every game left
    out counts.
    """
    now = now or datetime.now(timezone.utc)
    cutoff = now + timedelta(days=SOON_DAYS)

    def soon(row):
        try:
            return datetime.fromisoformat(row["kickoff_utc"]) <= cutoff
        except (KeyError, TypeError, ValueError):
            return True

    shown = sum(1 for r in rows if r["week"] == "this" and not r["indoor"] and soon(r))
    left_out = (hidden_soon if hidden_soon is not None else hidden).get("this", 0)
    if left_out and not shown:
        raise UnhealthyData(
            f"none of this week's {left_out} near-term outdoor games got a forecast "
            "(is api.weather.gov or the city lookup down?)"
        )
    if previous is not None:  # even an empty dict starts a history
        history.apply(rows, previous, now)
    return {
        "generated_at": now.astimezone(timezone.utc).isoformat(timespec="seconds"),
        "source": "US National Weather Service",
        "score_version": SCORE_VERSION,
        "weeks": week_info,
        "hidden": hidden,
        "games": rows,
    }


def write_payload(payload: dict, path: str | os.PathLike, compact: bool = False) -> None:
    """Write atomically so a crash can't leave a half-written data file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    text = json.dumps(payload, separators=(",", ":"), ensure_ascii=False) if compact else json.dumps(payload, indent=1, ensure_ascii=False)
    tmp.write_text(text + "\n", encoding="utf-8")
    os.replace(tmp, path)
