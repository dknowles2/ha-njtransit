"""What the nypenn.live collector writes down.

The collector is the only code that ever sees the live feed. Whatever it does
not record is not recoverable later, and the log is meant to be read months
from now against a site that will have changed again by then.

That is why the paywall is its problem rather than the reader's. A withheld
prediction arrives as a tier with the track taken out, which is the same shape
as a train nobody predicted to anything reading `track` alone. Working that
out at scoring time would mean inferring, from a log, what a website's billing
looked like on the day it was written.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from collect_nypenn import WATCHED, collect, interesting, withheld


def row(
    train_id: str, track: str | None, source: str | None, **extra: Any
) -> dict[str, Any]:
    """Return one departure as their feed sends it."""
    return {
        "train_id": train_id,
        "line": "NEC",
        "destination": "Trenton - SEC",
        "track": track,
        "track_source": source,
        "departure_time": 1788296580,
        "last_seen_on_track": None,
        "top3": None,
        "status": "",
        "cancelled": False,
        **extra,
    }


def board(tmp_path: Path, rows: list[dict[str, Any]]) -> str:
    """Serve a board from a file, and return a URL that fetches it."""
    path = tmp_path / "board.json"
    path.write_text(json.dumps(rows), encoding="utf-8")
    return path.as_uri()


def changes(log: Path) -> list[dict[str, Any]]:
    """Return the change lines of a collected log."""
    records = [
        json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()
    ]
    return [record for record in records if record["type"] == "change"]


def test_a_withheld_prediction_is_recorded_as_one() -> None:
    """The rule, stated once, in the place that watches the live feed.

    A named tier with no track is their lock. `confirmed` never carries one --
    it is the official board rather than their model -- and a row with no tier
    at all is a train they have said nothing about yet.
    """
    assert withheld(row("3949", None, "high")) is True
    assert withheld(row("3949", None, "medium")) is True
    assert withheld(row("3165", "10", "low")) is False
    assert withheld(row("6343", "8", "confirmed")) is False
    assert withheld(row("3363", None, None)) is False


def test_the_lock_is_written_into_the_log(tmp_path: Path) -> None:
    """What the reader gets, rather than what it has to reconstruct.

    Both rows here look identical to anything that only asks whether there is
    a track: one is a prediction we are not being shown, the other a train
    they have not predicted. The flag is the whole of the difference.
    """
    url = board(
        tmp_path,
        [row("3949", None, "high"), row("3951", None, None)],
    )
    log = tmp_path / "nypenn.jsonl"

    collect(log, interval=60, url=url, once=True)

    locked, quiet = changes(log)
    assert locked["train_id"] == "3949"
    assert locked["withheld"] is True
    assert locked["track"] is None, "their paywall sends the tier without the track"
    assert quiet["train_id"] == "3951"
    assert quiet["withheld"] is False


def test_the_lock_cannot_change_without_a_line_being_written() -> None:
    """Why the flag is derived rather than watched.

    The change log only writes a line when a watched field moves, so a fact
    that could change on its own would need watching of its own or it would
    be missed. This one cannot: it is computed from `track` and
    `track_source`, and both are already watched. Adding it to `WATCHED` would
    compare the same state twice, and the reason the log is small enough to
    leave running for weeks is that nothing is compared that need not be.
    """
    visible = row("3949", "10", "high")
    locked = row("3949", None, "high")

    assert withheld(visible) != withheld(locked)
    assert interesting(visible) != interesting(locked), (
        "their paywall closed on a train without the collector writing a line"
    )
    assert "withheld" not in WATCHED
