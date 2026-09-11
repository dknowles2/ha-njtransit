#!/usr/bin/env python3
"""Record where every NJ Transit train physically is, beside what Penn's board says.

NJ Transit's authenticated RailData API reports, for each running train, its
last identified *track circuit* -- the signalling system's occupancy detector.
That is a physical fact about where the train is standing, and at New York
Penn the circuits carry the prefix of A interlocking, which is the throat that
feeds the platforms. If those circuit names decode to platform numbers, this is
the thing every model in this repository has been missing: knowledge of the
platform before DepartureVision posts it.

Nobody knows the decode yet. This collects the evidence to learn it -- every
train's circuit, minute by minute, next to the track the public board
eventually posts for it -- so the mapping can fall out of counts rather than
be guessed from one train whose circuit happened to contain an "11".

Two feeds, one log:

* the RailData `getVehicleData` call, which lists *running* trains only. A
  departure not yet activated is absent, and its set is on the feed under the
  inbound service's number. That absence is itself the turn linkage: the set
  arrives on a circuit as one train and leaves on it as another.
* the public departure board for Penn, which is where the answer appears,
  about nine minutes before departure.

Storage is a change log. A line is written for a train only when its circuit,
next stop, or board state changes, plus one heartbeat per poll -- the same
shape as `collect_nypenn.py`, for the same reasons, and readable by the same
kind of replay.

**Credentials.** The API issues a token good for 24 hours in exchange for a
username and password, and allows ten such exchanges a day. Both are read from
a `key = value` file (`~/.njtransit` by default) that should be owner-only;
neither is ever written to the log, the journal, or stdout. The token is cached
beside the log with owner-only permissions and refreshed when it is older than
23 hours, never more often.

Usage:
    python scripts/collect_raildata.py --out raildata.jsonl

Runs until interrupted. Safe to restart against the same file.

Everything below is stdlib. This is a research tool, not shipped code.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from types import FrameType
from typing import Any

RAILDATA = "https://raildata.njtransit.com/api/TrainData"
BOARD = "https://www.njtransit.com/api/graphql/graphql"
STATION = "New York Penn Station"

INTERVAL = 60
TIMEOUT = 20
USER_AGENT = "ha-njtransit-research/1.0 (+https://github.com/dknowles2/ha-njtransit)"

# 24 hours in production per the spec; refreshed a little early so a poll
# never lands on an expired one. Ten exchanges a day are allowed; this uses
# one, and the daily reset happens at midnight so a restart cannot drift past
# the limit within a day.
TOKEN_LIFETIME = 23 * 3600

# What a train has to change for it to be worth a line. Position ticks are
# the point of the file, so the circuit is here; the board columns are here
# because the posting of a track is exactly the event being paired with it.
WATCHED = ("circuit", "next_stop", "board_track", "board_status")

BOARD_QUERY = (
    f'query {{ getTrainDepartureScreens(station: "{STATION}") '
    "{ items { trainID departureDate track status line } } }"
)


def read_credentials(path: Path) -> tuple[str, str]:
    """Return (username, password) from a `key = value` file, never echoed."""
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.partition("=")
        if sep:
            values[key.strip().lower()] = value.strip()
    try:
        return values["username"], values["password"]
    except KeyError as missing:
        raise SystemExit(f"{path}: no {missing.args[0]} line") from None


def _post(
    url: str,
    form: dict[str, str] | None = None,
    body: bytes | None = None,
    content_type: str | None = None,
) -> Any:
    """POST and decode JSON, with the identifying User-Agent."""
    headers = {"User-Agent": USER_AGENT}
    if form is not None:
        body = urllib.parse.urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    elif content_type:
        headers["Content-Type"] = content_type
    request = urllib.request.Request(url, data=body, headers=headers)
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return json.load(response)


def token_for(credentials: Path, cache: Path, clock: Any = time) -> str:
    """Return a usable token, exchanging credentials only when the cache is stale."""
    if cache.is_file():
        try:
            saved = json.loads(cache.read_text(encoding="utf-8"))
            if clock.time() - float(saved["at"]) < TOKEN_LIFETIME and saved.get(
                "token"
            ):
                return str(saved["token"])
        except ValueError, KeyError:
            pass
    username, password = read_credentials(credentials)
    reply = _post(
        f"{RAILDATA}/getToken", form={"username": username, "password": password}
    )
    token = reply.get("UserToken")
    if not token:
        raise SystemExit("getToken returned no token; check the credentials file")
    old = os.umask(0o077)
    try:
        cache.write_text(
            json.dumps({"at": clock.time(), "token": token}), encoding="utf-8"
        )
    finally:
        os.umask(old)
    return str(token)


def vehicles(token: str) -> list[dict[str, Any]]:
    """Return every running train."""
    reply = _post(f"{RAILDATA}/getVehicleData", form={"token": token})
    if isinstance(reply, dict):
        if "errorMessage" in reply:
            raise ValueError(reply["errorMessage"])
        reply = reply.get("TRAINS", [])
    if not isinstance(reply, list):
        raise TypeError(f"expected a list of trains, got {type(reply).__name__}")
    return reply


def board() -> dict[str, dict[str, Any]]:
    """Return the public Penn board, keyed by train number."""
    reply = _post(
        BOARD,
        body=json.dumps({"query": BOARD_QUERY}).encode(),
        content_type="application/json",
    )
    items = reply["data"]["getTrainDepartureScreens"]["items"]
    return {str(item["trainID"]): item for item in items}


def collect(
    out: Path, credentials: Path, interval: int, once: bool = False, clock: Any = time
) -> None:
    """Poll until interrupted, appending every change to `out`."""
    cache = out.with_suffix(".token")
    seen: dict[str, dict[str, Any]] = {}
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
                token = token_for(credentials, cache, clock)
                trains = vehicles(token)
                posted = board()
            except (
                urllib.error.URLError,
                TypeError,
                ValueError,
                TimeoutError,
                KeyError,
            ) as error:
                # Never fatal, and never the credentials: the error text from
                # urllib carries the URL, not the form body.
                write({"type": "error", "t": int(started), "error": str(error)[:200]})
                print(f"poll failed: {str(error)[:200]}", file=sys.stderr)
            else:
                changes = 0
                on_board = set(posted)
                for train in trains:
                    train_id = str(train.get("ID"))
                    row = posted.get(train_id, {})
                    current = {
                        "circuit": train.get("ICS_TRACK_CKT") or None,
                        "next_stop": train.get("NEXT_STOP") or None,
                        "board_track": row.get("track") or None,
                        "board_status": row.get("status") or None,
                    }
                    if seen.get(train_id) == current:
                        continue
                    seen[train_id] = current
                    changes += 1
                    write(
                        {
                            "type": "change",
                            "t": int(started),
                            "train_id": train_id,
                            "line": train.get("TRAIN_LINE"),
                            "direction": train.get("DIRECTION"),
                            "sec_late": train.get("SEC_LATE"),
                            "sched_dep": train.get("SCHED_DEP_TIME"),
                            "board_dep": row.get("departureDate"),
                            "lat": train.get("LATITUDE"),
                            "lon": train.get("LONGITUDE"),
                            **current,
                        }
                    )
                # Trains on the board but not yet running: their track posting
                # is the answer half of a pair whose question half (the circuit)
                # arrives later under the same number, or earlier under another.
                for train_id in on_board - {str(t.get("ID")) for t in trains}:
                    row = posted[train_id]
                    current = {
                        "circuit": None,
                        "next_stop": None,
                        "board_track": row.get("track") or None,
                        "board_status": row.get("status") or None,
                    }
                    if seen.get(train_id) == current:
                        continue
                    seen[train_id] = current
                    changes += 1
                    write(
                        {
                            "type": "change",
                            "t": int(started),
                            "train_id": train_id,
                            "line": row.get("line"),
                            "direction": None,
                            "sec_late": None,
                            "sched_dep": None,
                            "board_dep": row.get("departureDate"),
                            "lat": None,
                            "lon": None,
                            **current,
                        }
                    )
                write(
                    {
                        "type": "poll",
                        "t": int(started),
                        "running": len(trains),
                        "on_board": len(posted),
                        "changes": changes,
                    }
                )

            if once:
                return
            remaining = interval - (clock.time() - started)
            while running and remaining > 0:
                clock.sleep(min(1, remaining))
                remaining = interval - (clock.time() - started)


def main() -> int:
    """Run the collector."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("raildata.jsonl"))
    parser.add_argument("--credentials", type=Path, default=Path.home() / ".njtransit")
    parser.add_argument("--interval", type=int, default=INTERVAL)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    if args.interval < 30:
        # 40,000 calls a day is the documented limit; sixty seconds is 1,440.
        print("refusing to poll faster than every 30 seconds", file=sys.stderr)
        return 2
    if not args.credentials.is_file():
        print(f"{args.credentials}: not found", file=sys.stderr)
        return 2
    if args.credentials.stat().st_mode & 0o077:
        print(
            f"{args.credentials}: readable by others; chmod 600 it first",
            file=sys.stderr,
        )
        return 2

    print(f"polling every {args.interval}s into {args.out}", file=sys.stderr)
    collect(args.out, args.credentials, args.interval, once=args.once)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
