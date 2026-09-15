"""Rewriting the integration's store in the change-log shape.

The store is a rolling window and this is the durable copy of it, so the one
property that matters is that nothing is lost or invented on the way through:
every row becomes a record, every track survives, and a gap in what the store
knew stays a gap rather than being papered over with a plausible timestamp.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from history_to_log import departure_epoch, records

TZ = ZoneInfo("America/New_York")


def store(*rows: dict) -> dict:
    """Return a store holding one day at Penn with these rows."""
    return {
        "version": 1,
        "data": {"days": {"New York Penn Station|2026-08-20": list(rows)}},
    }


def test_the_departure_lands_on_the_right_instant() -> None:
    """A bare "HH:MM" plus the day key, read as station-local time.

    Local rather than UTC because that is what the board shows and what the
    collector's `departure_time` means, so the two files key identically.
    """
    from datetime import date

    epoch = departure_epoch(date(2026, 8, 20), "18:30")

    assert datetime.fromtimestamp(epoch, TZ) == datetime(2026, 8, 20, 18, 30, tzinfo=TZ)


def test_a_posted_track_becomes_a_confirmed_change_at_posting_time() -> None:
    """The one instant the store can vouch for is when the track went up."""
    row = {
        "train_id": 6613,
        "scheduled": "18:30",
        "line": "M&E",
        "track": "4",
        "assigned_at": 600,
        "first_track": None,
    }

    [change, poll] = records(store(row))

    assert change["track"] == "4"
    assert change["track_source"] == "confirmed"
    assert change["departure_time"] - change["t"] == 600
    assert poll["t"] == change["t"], "the board was looked at when the track was seen"


def test_a_train_that_never_got_a_track_is_not_given_one() -> None:
    """No track, no `confirmed`, and the record sits at departure -- the last
    moment the row was certainly on the board -- rather than at a guess."""
    row = {
        "train_id": 6613,
        "scheduled": "18:30",
        "line": "M&E",
        "track": None,
        "assigned_at": None,
    }

    [change, _] = records(store(row))

    assert change["track"] is None
    assert change["track_source"] is None
    assert change["t"] == change["departure_time"]


def test_a_reassignment_rides_along_rather_than_becoming_an_event() -> None:
    """The store knows the first track but not when it changed.

    A second event would need a timestamp the store never had. Carrying the
    field keeps the fact without inventing the time.
    """
    row = {
        "train_id": 6613,
        "scheduled": "18:30",
        "line": "M&E",
        "track": "4",
        "assigned_at": 600,
        "first_track": "7",
        "worst_delay": 12,
    }

    [change, _] = records(store(row))

    assert change["first_track"] == "7"
    assert change["worst_delay"] == 12


def test_nothing_is_dropped() -> None:
    """One change and one poll per row, however many rows."""
    rows = [
        {
            "train_id": n,
            "scheduled": f"{8 + n // 60:02d}:{n % 60:02d}",
            "line": "NEC",
            "track": "3",
            "assigned_at": 500,
        }
        for n in range(40)
    ]

    out = records(store(*rows))

    assert sum(1 for r in out if r["type"] == "change") == 40
    assert sum(1 for r in out if r["type"] == "poll") == 40
    assert out == sorted(out, key=lambda r: (r["t"], r["type"] != "change"))
