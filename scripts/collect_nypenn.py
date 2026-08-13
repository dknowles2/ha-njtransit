#!/usr/bin/env python3
"""Record what nypenn.live predicts, so it can be scored like any other model.

nypenn.live publishes a departure board for New York Penn Station with a track
prediction attached to trains the official board has not posted yet -- the
thing `scripts/analyze_tracks.py` is trying to decide whether we can do at all.
Its endpoint is open and unauthenticated, and its own page polls it every five
seconds; this polls once a minute and identifies itself.

What makes it scoreable is that the same feed carries the answer. Each row has
a `track_source`:

    confirmed   the official board (DepartureVision) has posted the track.
                This is ground truth, not a prediction.
    high        their model, confident.
    medium/low  their model, less so, and then `top3` carries the ranked
                candidates with a percentage each.

So a train appears first as a prediction and later as the truth about that
same prediction, and no separate outcome feed is needed.

Storage is a change log, not a series of snapshots. Polling every minute for a
day is about 57MB of near-identical boards; writing only what changed is a few
hundred kilobytes and reconstructs to exactly the same thing, because what is
being recorded is a step function -- a prediction holds until it is replaced.

Every poll also writes a heartbeat, which is not redundant: without it there is
no way to tell an hour in which nothing changed from an hour in which nothing
was watching, and the difference decides whether "their last prediction before
T-30" means anything.

Usage:
    python scripts/collect_nypenn.py --out nypenn-2026-08.jsonl

Runs until interrupted. Safe to stop and restart against the same file; the
reader treats a restart as the gap it is.

Everything below is stdlib. This is a research tool, not shipped code.
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from types import FrameType
from typing import Any

ENDPOINT = "https://nypenn.live/api/departures"

# Their page polls this every 5 seconds. Once a minute is an order of
# magnitude lighter than a single tab someone left open, and the prediction it
# is recording does not move faster than that.
INTERVAL = 60

# Saying who this is and where to complain, because an unattended poller that
# cannot be identified is how a small site ends up blocking a whole subnet.
USER_AGENT = "ha-njtransit-research/1.0 (+https://github.com/dknowles2/ha-njtransit)"

TIMEOUT = 20

# What a row has to change for it to be worth a line. `last_seen_on_track`
# is deliberately not in here: it ticks whenever their tracker sees the set
# again, which is constantly, and it does not change the prediction. It is
# still written when a line is written, because it is the only visible hint at
# what their model is actually reading.
WATCHED = ("track", "track_source", "top3")


def fetch(url: str) -> list[dict[str, Any]]:
    """Return the current board, or raise."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        payload = json.load(response)
    if not isinstance(payload, list):
        raise TypeError(f"expected a list of departures, got {type(payload).__name__}")
    return payload


def key(row: dict[str, Any]) -> tuple[str, int]:
    """Return what identifies one departure across polls.

    The train number alone is not enough. Numbers repeat every day, and this
    is meant to be left running for weeks, so the scheduled departure has to
    be part of it or Tuesday's 6675 silently overwrites Monday's.
    """
    return (str(row.get("train_id")), int(row.get("departure_time") or 0))


def interesting(row: dict[str, Any]) -> dict[str, Any]:
    """Return the fields whose change is worth recording."""
    return {field: row.get(field) for field in WATCHED}


def collect(
    out: Path, interval: int, url: str, once: bool = False, clock: Any = time
) -> None:
    """Poll until interrupted, appending every change to `out`.

    Flushed per line on purpose. This is meant to be left running for weeks
    and then killed, and a buffered final day would be the one day someone
    actually wanted.
    """
    seen: dict[tuple[str, int], dict[str, Any]] = {}
    running = True

    def stop(signum: int, frame: FrameType | None) -> None:
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    with out.open("a", encoding="utf-8") as file:

        def write(record: dict[str, Any]) -> None:
            file.write(json.dumps(record, separators=(",", ":")) + "\n")
            file.flush()

        while running:
            started = clock.time()
            try:
                board = fetch(url)
            except (
                urllib.error.URLError,
                TypeError,
                ValueError,
                TimeoutError,
            ) as error:
                # Never fatal. A poller that dies on one bad response loses
                # the rest of the month to a thirty-second outage.
                write({"type": "error", "t": int(started), "error": str(error)})
                print(f"poll failed: {error}", file=sys.stderr)
                board = []
            else:
                changes = 0
                for row in board:
                    identity = key(row)
                    current = interesting(row)
                    if seen.get(identity) == current:
                        continue
                    seen[identity] = current
                    changes += 1
                    write(
                        {
                            "type": "change",
                            "t": int(started),
                            "train_id": row.get("train_id"),
                            "departure_time": row.get("departure_time"),
                            "line": row.get("line"),
                            "destination": row.get("destination"),
                            "last_seen_on_track": row.get("last_seen_on_track"),
                            **current,
                        }
                    )
                write(
                    {
                        "type": "poll",
                        "t": int(started),
                        "rows": len(board),
                        "changes": changes,
                    }
                )

            if once:
                return

            # Against the start of the poll, not the end, so a slow response
            # does not walk the schedule later and later.
            remaining = interval - (clock.time() - started)
            while running and remaining > 0:
                clock.sleep(min(1, remaining))
                remaining = interval - (clock.time() - started)


def main() -> int:
    """Run the collector."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("nypenn.jsonl"),
        help="change log to append to (default: nypenn.jsonl)",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=INTERVAL,
        help=f"seconds between polls (default: {INTERVAL})",
    )
    parser.add_argument("--url", default=ENDPOINT, help="endpoint to poll")
    parser.add_argument(
        "--once", action="store_true", help="poll a single time and exit"
    )
    args = parser.parse_args()

    if args.interval < 15:
        # Their own page uses five seconds, but their own page is one tab that
        # gets closed. This is unattended and long-running.
        print("refusing to poll faster than every 15 seconds", file=sys.stderr)
        return 2

    print(f"polling {args.url} every {args.interval}s into {args.out}", file=sys.stderr)
    collect(args.out, args.interval, args.url, once=args.once)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
