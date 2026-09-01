"""The weekly report, and the ways it can lie without being wrong.

Every number it prints is computed by code tested elsewhere. What is left is
presentation, and presentation has its own failure: a section built from data
that stopped arriving days ago looks exactly like one built from data that
arrived this morning. That is not hypothetical -- the collector's host lost
outbound network for 57 hours in August, the poller correctly logged failures
and carried on, and the report went on printing the same tables from frozen
logs.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from weekly_report import nypenn_section

RUN_AT = datetime(2026, 8, 28, 11, 0, tzinfo=UTC)
DEPARTURE = datetime(2026, 8, 26, 18, 30, tzinfo=UTC)


def log(tmp_path: Path, *, last_poll: datetime, withheld: bool = False) -> Path:
    """Write a change log whose most recent poll is `last_poll`."""
    departure = int(DEPARTURE.timestamp())
    records: list[dict[str, Any]] = [
        {
            "type": "change",
            "t": departure - 1800,
            "train_id": "6675",
            "departure_time": departure,
            "line": "M&E",
            "destination": "Dover",
            "last_seen_on_track": None,
            "track": None if withheld else "9",
            "track_source": "high",
            "top3": None,
            "withheld": withheld,
        },
        {
            "type": "change",
            "t": departure - 300,
            "train_id": "6675",
            "departure_time": departure,
            "line": "M&E",
            "destination": "Dover",
            "last_seen_on_track": None,
            "track": "9",
            "track_source": "confirmed",
            "top3": None,
        },
        {"type": "poll", "t": int(last_poll.timestamp()), "rows": 9, "changes": 0},
    ]
    path = tmp_path / "nypenn.jsonl"
    path.write_text(
        "".join(json.dumps(record) + "\n" for record in records), encoding="utf-8"
    )
    return path


def test_a_frozen_collection_says_so(tmp_path: Path) -> None:
    """The failure this file exists for.

    The tables below the warning are not wrong -- they describe the period
    they were computed from perfectly well. They are just answering a question
    about last Tuesday while appearing to answer one about today.
    """
    stale = log(tmp_path, last_poll=RUN_AT - timedelta(hours=57))

    section = nypenn_section([stale], RUN_AT)

    assert "Stale" in section, "a two-day-old collection was reported as current"
    assert "57 hours" in section


def test_a_current_collection_is_not_flagged(tmp_path: Path) -> None:
    """A warning on every report is a warning nobody reads."""
    fresh = log(tmp_path, last_poll=RUN_AT - timedelta(minutes=3))

    assert "Stale" not in nypenn_section([fresh], RUN_AT)


def test_a_paywalled_week_does_not_read_as_a_quiet_one(tmp_path: Path) -> None:
    """The second way this report can lie without printing a wrong number.

    `high` and `medium` are subscriber-only now, so their confident tiers
    reach the collector with the track stripped out and drop out of every
    table here. What is left is scored correctly and describes their `low`
    tier -- a week they predicted well, presented as a week they said almost
    nothing, unless the section says which it is.
    """
    locked = log(tmp_path, last_poll=RUN_AT - timedelta(minutes=3), withheld=True)

    section = nypenn_section([locked], RUN_AT)

    assert "withheld behind their paywall" in section, (
        "a withheld prediction was reported as a train they did not predict"
    )
    assert "| high | - | - | 0 | 1 |" in section


def test_a_readable_week_is_not_captioned_as_paywalled(tmp_path: Path) -> None:
    """The caveat has to describe this week, not the state of the world."""
    readable = log(tmp_path, last_poll=RUN_AT - timedelta(minutes=3))

    assert "withheld behind their paywall" not in nypenn_section([readable], RUN_AT)


def test_a_missing_log_is_not_silently_empty(tmp_path: Path) -> None:
    """No file at all reads as "they predicted nothing" unless it is named."""
    assert "No nypenn.live logs found" in nypenn_section(
        [tmp_path / "absent.jsonl"], RUN_AT
    )
