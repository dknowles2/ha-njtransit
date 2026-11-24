"""The weekly report, and the ways it can lie without being wrong.

Every number it prints is computed by `signal_accuracy`, tested on its own.
What is left is presentation, and presentation has its own failure: a section
built from data that stopped arriving days ago looks exactly like one built
from data that arrived this morning. That is not hypothetical -- the
collector's host lost outbound network for 57 hours in August, the poller
correctly logged failures and carried on, and the report went on printing the
same tables from frozen logs.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from datetime import date as dt_date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from signal_accuracy import TZ
from weekly_report import STALE_AFTER, last_record_at, signal_section


def raildata_log(
    tmp_path: Path, *, day: dt_date, last_poll: datetime | None = None
) -> Path:
    """Write a collector log with one scored departure on `day`.

    `last_poll`, when given, appends a heartbeat at that instant, which is
    what the staleness check reads.
    """

    def at(hour: int, minute: int) -> int:
        return int(
            datetime(day.year, day.month, day.day, hour, minute, tzinfo=TZ).timestamp()
        )

    scheduled = f"{day.strftime('%d-%b-%Y')} 05:31:00 PM"
    records: list[dict[str, object]] = [
        {
            "type": "change",
            "t": at(16, 46),
            "train_id": "6273",
            "sched_dep": scheduled,
            "direction": "Westbound",
            "line": "Montclair-Boonton Line",
            "circuit": "JO-AJO13ATK",
            "board_track": None,
        },
        {
            "type": "change",
            "t": at(17, 21),
            "train_id": "6273",
            "sched_dep": scheduled,
            "direction": "Westbound",
            "line": "Montclair-Boonton Line",
            "circuit": None,
            "board_track": "9",
        },
    ]
    if last_poll is not None:
        records.append(
            {
                "type": "poll",
                "t": int(last_poll.timestamp()),
                "running": 40,
                "on_board": 18,
                "changes": 3,
            }
        )
    path = tmp_path / "raildata.jsonl"
    path.write_text(
        "".join(json.dumps(record) + "\n" for record in records), encoding="utf-8"
    )
    return path


def test_the_signal_section_scores_the_collector_log(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    today = now.astimezone(TZ).date()
    section, rows = signal_section(
        [raildata_log(tmp_path, day=today, last_poll=now)], 14, now
    )
    assert "1/1 (100%)" in section
    assert rows[0]["agreed"] == 1


def test_the_signal_window_excludes_older_days(tmp_path: Path) -> None:
    """`--signal-days` is what keeps this runnable on the collector host."""
    now = datetime.now(UTC)
    old = now.astimezone(TZ).date() - timedelta(days=30)
    section, rows = signal_section(
        [raildata_log(tmp_path, day=old, last_poll=now)], 14, now
    )
    assert rows == []
    assert "No scored departures" in section


def test_a_missing_collector_log_is_not_silently_empty(tmp_path: Path) -> None:
    section, rows = signal_section([tmp_path / "absent.jsonl"], 14, datetime.now(UTC))
    assert "No collector logs found" in section
    assert rows == []


def test_a_frozen_collection_says_so(tmp_path: Path) -> None:
    """The failure this report has actually had: frozen data, current-looking."""
    now = datetime.now(UTC)
    frozen = now - STALE_AFTER - timedelta(hours=1)
    section, _ = signal_section(
        [raildata_log(tmp_path, day=frozen.astimezone(TZ).date(), last_poll=frozen)],
        14,
        now,
    )
    assert section.startswith("**Stale: collection stops 13 hours before this run**")


def test_a_current_collection_is_not_flagged(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    section, _ = signal_section(
        [
            raildata_log(
                tmp_path,
                day=now.astimezone(TZ).date(),
                last_poll=now - timedelta(minutes=1),
            )
        ],
        14,
        now,
    )
    assert "Stale" not in section


def test_an_unreadable_log_says_so(tmp_path: Path) -> None:
    """No newest record is not the same as a fresh one."""
    path = tmp_path / "raildata.jsonl"
    path.write_text("not json\n", encoding="utf-8")
    section, _ = signal_section([path], 14, datetime.now(UTC))
    assert "hold no readable records" in section


class TestLastRecordAt:
    def test_reads_the_newest_record(self, tmp_path: Path) -> None:
        when = datetime(2026, 11, 23, 18, 56, tzinfo=UTC)
        path = raildata_log(tmp_path, day=when.date(), last_poll=when)
        assert last_record_at(path) == when

    def test_skips_a_half_written_last_line(self, tmp_path: Path) -> None:
        """The collector may be mid-append when the report reads the file."""
        when = datetime(2026, 11, 23, 18, 56, tzinfo=UTC)
        path = raildata_log(tmp_path, day=when.date(), last_poll=when)
        with path.open("a", encoding="utf-8") as file:
            file.write('{"type": "poll", "t": 17')
        assert last_record_at(path) == when

    def test_reads_only_the_tail_of_a_long_log(self, tmp_path: Path) -> None:
        """The cut-off first line of the window must not be mistaken for data."""
        when = datetime(2026, 11, 23, 18, 56, tzinfo=UTC)
        path = tmp_path / "raildata.jsonl"
        filler = json.dumps({"type": "poll", "t": 0, "pad": "x" * 500}) + "\n"
        path.write_text(
            filler * 400
            + json.dumps({"type": "poll", "t": int(when.timestamp())})
            + "\n",
            encoding="utf-8",
        )
        assert last_record_at(path) == when

    def test_an_empty_log_has_no_newest_record(self, tmp_path: Path) -> None:
        path = tmp_path / "raildata.jsonl"
        path.write_text("", encoding="utf-8")
        assert last_record_at(path) is None
