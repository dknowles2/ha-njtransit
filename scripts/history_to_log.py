#!/usr/bin/env python3
"""Rewrite the integration's track history in the nypenn.live change-log shape.

Two reasons to want the same data in a second shape.

The first is durability. `njtransit.track_history` is a rolling window: the
integration keeps about a month and drops the oldest day, so the August rows
that started issue #35 are already gone from Home Assistant and survive only in
whichever diagnostics snapshot last captured them. A change log is append-only
and nobody's window slides over it.

The second is that the nypenn.live tools already read this shape, and a
departure the official board posted is exactly what their feed calls
`track_source: confirmed`. Written this way, our own history loads into
`nypenn.py` beside theirs and gets the same replay, the same `truth`, the same
step function -- rather than a third loader with its own subtly different
rules.

**What this is honest about.** The store records *that* a track was posted and
how long before departure, not the minute-by-minute board the collector sees.
So each departure becomes one `change` at the moment its track went up, and one
`poll` at that same instant to say the board was looked at then. There are no
heartbeats in between, because none were recorded. That makes `truth` and the
per-tier tables meaningful on this file and the lead-time table meaningless --
at T-30 nothing was observed, and the reader will correctly say so. A record
that never got a track is written at its departure time with `track` null,
which is when we can be sure it was on the board.

Nothing is invented to fill the gaps. A reassignment is carried as an extra
`first_track` field rather than as a second event with a made-up timestamp,
and every field the store had is passed through, so the file is lossless and
the extra keys are simply ignored by readers that do not know them.

Usage:
    python scripts/history_to_log.py njtransit.track_history --out history.jsonl

Everything below is stdlib. This is an analysis tool, not shipped code.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/New_York")

# The store keys each day's rows as "Station|YYYY-MM-DD".
KEY_SEPARATOR = "|"


def departure_epoch(day: date, scheduled: str) -> int:
    """Return the scheduled departure as a Unix timestamp.

    `scheduled` is a bare "HH:MM"; the day comes from the store key. Local time
    at the station, which is what the board shows and what the collector's
    `departure_time` means too.
    """
    hour, _, minute = scheduled.partition(":")
    local = datetime.combine(day, time(int(hour), int(minute)), tzinfo=TZ)
    return int(local.timestamp())


def records(store: dict[str, Any]) -> list[dict[str, Any]]:
    """Return change-log lines for every row in the store, oldest first."""
    out: list[dict[str, Any]] = []
    days = store.get("data", store).get("days", {})
    for key, rows in days.items():
        station, _, iso = key.partition(KEY_SEPARATOR)
        day = date.fromisoformat(iso)
        for row in rows:
            departs = departure_epoch(day, row["scheduled"])
            assigned = row.get("assigned_at")
            # The one instant the store can vouch for. A track that was never
            # posted has no such instant, and the departure minute is the last
            # moment the row was certainly on the board.
            seen_at = departs - assigned if assigned is not None else departs
            change = {
                "type": "change",
                "t": seen_at,
                "station": station,
                "train_id": str(row["train_id"]),
                "departure_time": departs,
                "line": row.get("line"),
                "destination": None,
                "last_seen_on_track": None,
                "withheld": False,
                "track": row.get("track"),
                # Ours is the official board. That is precisely what their feed
                # means by this value, so the two files agree on what a truth
                # looks like without a translation layer.
                "track_source": "confirmed" if row.get("track") else None,
                "top3": None,
            }
            # Everything the store knew that the log shape has no slot for.
            # Readers that do not know these keys ignore them; a reader that
            # wants them has not lost anything.
            for extra in (
                "assigned_at",
                "first_track",
                "seen_trackless",
                "delay_at_assignment",
                "final_status",
                "final_delay",
                "worst_delay",
            ):
                change[extra] = row.get(extra)
            out.append(change)
            out.append({"type": "poll", "t": seen_at, "rows": None, "changes": 1})
    out.sort(key=lambda r: (r["t"], r["type"] != "change"))
    return out


def main() -> int:
    """Convert a store file to a change log."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "store", type=Path, help="njtransit.track_history from .storage"
    )
    parser.add_argument("--out", type=Path, required=True, help="change log to write")
    parser.add_argument(
        "--station", help="only this station (default: every station in the store)"
    )
    args = parser.parse_args()

    store = json.loads(args.store.read_text(encoding="utf-8"))
    lines = records(store)
    if args.station:
        lines = [r for r in lines if r.get("station", args.station) == args.station]

    header = {
        "type": "note",
        "source": "homeassistant njtransit.track_history",
        "converted_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "store_version": store.get("version"),
        "changes": sum(1 for r in lines if r["type"] == "change"),
    }
    with args.out.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(header, separators=(",", ":")) + "\n")
        for record in lines:
            handle.write(json.dumps(record, separators=(",", ":")) + "\n")
    print(f"wrote {header['changes']} departures to {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
