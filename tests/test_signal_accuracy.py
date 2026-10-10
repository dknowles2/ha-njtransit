"""Scoring the signalled platform against the board that followed it.

The test that matters most here is `test_board_revising_itself_is_not_a_miss`.
Scoring against the board's first posting is the obvious reading of "was the
signal right", it is what the first hand-rolled version of this measurement
did, and it is wrong: it counted the signal as having missed on departures
where the signal named the track the train actually left from and the board
had merely not settled yet. Six weeks of collection contained ten of those.

The rest guard the narrowness of the claim. A signal only counts if it existed
before the board spoke -- otherwise the head-start number is measuring a
sighting that arrived after the answer was already public -- and a platform
the feed never showed has to come back as no answer rather than a guess.
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from occupancy import TZ
from signal_accuracy import by_day, load, report, summarize

SCHEDULED = "06-Oct-2026 05:31:00 PM"
DAY = date(2026, 10, 6)


def _at(hour: int, minute: int) -> int:
    """Return an epoch second on the service day, in Penn's timezone."""
    return int(datetime(2026, 10, 6, hour, minute, tzinfo=TZ).timestamp())


def _change(
    when: int,
    *,
    train: str = "6273",
    circuit: str | None = None,
    board: str | None = None,
    direction: str = "Westbound",
    line: str = "Montclair-Boonton Line",
    scheduled: str = SCHEDULED,
) -> dict[str, object]:
    """One collector change record, with only the fields scoring reads."""
    return {
        "type": "change",
        "t": when,
        "train_id": train,
        "sched_dep": scheduled,
        "direction": direction,
        "line": line,
        "circuit": circuit,
        "board_track": board,
    }


def _log(tmp_path: Path, *records: dict[str, object]) -> Path:
    path = tmp_path / "raildata.jsonl"
    path.write_text(
        "".join(json.dumps(record) + "\n" for record in records), encoding="utf-8"
    )
    return path


class TestWhatCountsAsAnAnswer:
    def test_platform_seen_before_the_board_posts_is_scored(
        self, tmp_path: Path
    ) -> None:
        runs = load(
            _log(
                tmp_path,
                _change(_at(16, 46), circuit="JO-AJO13ATK"),
                _change(_at(17, 21), board="9"),
            )
        )
        assert len(runs) == 1
        assert runs[0].signal == "9"
        assert runs[0].covered
        assert runs[0].agreed

    def test_platform_only_seen_after_the_board_posts_is_no_answer(
        self, tmp_path: Path
    ) -> None:
        """A sighting that arrives after the posting has beaten nothing."""
        runs = load(
            _log(
                tmp_path,
                _change(_at(17, 21), board="9"),
                _change(_at(17, 25), circuit="JO-AJO13ATK"),
            )
        )
        assert runs[0].signal is None
        assert not runs[0].covered
        assert runs[0].lead_minutes is None

    def test_switch_circuits_are_not_platforms(self, tmp_path: Path) -> None:
        """Route and points indications decode to nothing, so they say nothing."""
        runs = load(
            _log(
                tmp_path,
                _change(_at(16, 46), circuit="AA-83UP"),
                _change(_at(16, 50), circuit="AA-141DP"),
                _change(_at(17, 21), board="9"),
            )
        )
        assert runs[0].signal is None

    def test_the_last_sighting_before_the_posting_wins(self, tmp_path: Path) -> None:
        """The client remembers the most recent platform, so scoring must too."""
        runs = load(
            _log(
                tmp_path,
                _change(_at(16, 46), circuit="JO-AJO13ATK"),
                _change(_at(17, 10), circuit="JO-AJO15ATK"),
                _change(_at(17, 21), board="7"),
            )
        )
        assert runs[0].signal == "7"
        assert runs[0].agreed

    def test_a_departure_the_board_never_posted_is_not_scored(
        self, tmp_path: Path
    ) -> None:
        runs = load(_log(tmp_path, _change(_at(16, 46), circuit="JO-AJO13ATK")))
        assert runs == []

    def test_arrivals_are_not_scored(self, tmp_path: Path) -> None:
        """An eastbound train has no departure board to be right about."""
        runs = load(
            _log(
                tmp_path,
                _change(_at(16, 46), circuit="JO-AJO13ATK", direction="Eastbound"),
                _change(_at(17, 21), board="9", direction="Eastbound"),
            )
        )
        assert runs == []


class TestTheBoardRevisingItself:
    def test_board_revising_itself_is_not_a_miss(self, tmp_path: Path) -> None:
        """The track walked to is the last one posted, not the first.

        Train 6291 on 2026-10-06: the board posted 1 at 19:28:25 and 4 thirty
        seconds later, and the signal had said 4 since 19:15. Scored against
        the first posting this reads as a miss.
        """
        runs = load(
            _log(
                tmp_path,
                _change(_at(19, 15), train="6291", circuit="AA-A180TK"),
                _change(_at(19, 28), train="6291", board="1"),
                _change(_at(19, 29), train="6291", board="4"),
            )
        )
        assert runs[0].signal == "4"
        assert runs[0].board_first == "1"
        assert runs[0].board_final == "4"
        assert runs[0].agreed
        assert runs[0].corrected

    def test_head_start_is_measured_from_the_first_posting(
        self, tmp_path: Path
    ) -> None:
        """A revision must not inflate the head start.

        The signal beat the board by the time to its *first* answer. Measuring
        to the correction would credit the signal with the board's indecision.
        """
        runs = load(
            _log(
                tmp_path,
                _change(_at(17, 1), circuit="JO-AJO13ATK"),
                _change(_at(17, 21), board="1"),
                _change(_at(17, 26), board="9"),
            )
        )
        assert runs[0].head_start_minutes == 20
        assert runs[0].lead_minutes == 30

    def test_corrections_are_counted_even_when_the_signal_was_silent(
        self, tmp_path: Path
    ) -> None:
        """The count describes the board, so it does not depend on us."""
        runs = load(
            _log(
                tmp_path,
                _change(_at(17, 21), board="1"),
                _change(_at(17, 26), board="9"),
            )
        )
        assert summarize(runs).corrections == 1
        assert summarize(runs).covered == 0


class TestHealing:
    def test_a_miss_the_feed_later_corrects_is_marked_healed(
        self, tmp_path: Path
    ) -> None:
        """Train 3825 on 2026-10-09: signalled 7, re-sighted on 3, left from 3."""
        runs = load(
            _log(
                tmp_path,
                _change(_at(7, 41), train="3825", circuit="JO-AJO15ATK"),
                _change(_at(8, 5), train="3825", board="3"),
                _change(_at(8, 6), train="3825", circuit="JO-AJO19ATK", board="3"),
            )
        )
        assert runs[0].signal == "7"
        assert not runs[0].agreed
        assert runs[0].healed

    def test_healing_is_judged_against_the_track_the_train_left_from(
        self, tmp_path: Path
    ) -> None:
        """A revised posting must not change what counts as a correction.

        Without this, grading healing against the board's *first* answer
        reads identically on every run where the board never revised itself,
        which is all but ten of them.
        """
        runs = load(
            _log(
                tmp_path,
                _change(_at(16, 46), circuit="JO-AJO13ATK"),
                _change(_at(17, 21), board="1"),
                _change(_at(17, 22), board="4"),
                _change(_at(17, 25), circuit="AA-A180TK", board="4"),
            )
        )
        assert runs[0].signal == "9"
        assert runs[0].board_first == "1"
        assert runs[0].board_final == "4"
        assert not runs[0].agreed
        assert runs[0].healed

    def test_a_correction_after_the_row_leaves_the_board_is_not_healing(
        self, tmp_path: Path
    ) -> None:
        """Once the row is gone the client has nothing left to correct."""
        runs = load(
            _log(
                tmp_path,
                _change(_at(7, 41), train="3825", circuit="JO-AJO15ATK"),
                _change(_at(8, 5), train="3825", board="3"),
                _change(_at(8, 20), train="3825", circuit="JO-AJO19ATK"),
            )
        )
        assert not runs[0].agreed
        assert not runs[0].healed

    def test_a_miss_never_corrected_is_not_healed(self, tmp_path: Path) -> None:
        runs = load(
            _log(
                tmp_path,
                _change(_at(16, 46), circuit="AA-A190TK"),
                _change(_at(17, 21), board="14"),
            )
        )
        assert runs[0].signal == "3"
        assert not runs[0].agreed
        assert not runs[0].healed

    def test_a_correct_signal_is_never_healed(self, tmp_path: Path) -> None:
        """`healed` is only meaningful next to a miss, so it stays false."""
        runs = load(
            _log(
                tmp_path,
                _change(_at(16, 46), circuit="JO-AJO13ATK"),
                _change(_at(17, 21), board="9"),
                _change(_at(17, 25), circuit="JO-AJO15ATK", board="9"),
            )
        )
        assert runs[0].agreed
        assert not runs[0].healed


class TestSummaries:
    def test_runs_of_one_train_on_two_days_do_not_collapse(
        self, tmp_path: Path
    ) -> None:
        """A gap of hours is the next day's working, not a pause in this one."""
        runs = load(
            _log(
                tmp_path,
                _change(_at(16, 46), circuit="JO-AJO13ATK"),
                _change(_at(17, 21), board="9"),
                _change(
                    int(datetime(2026, 10, 7, 16, 46, tzinfo=TZ).timestamp()),
                    circuit="JO-AJO15ATK",
                    scheduled="07-Oct-2026 05:31:00 PM",
                ),
                _change(
                    int(datetime(2026, 10, 7, 17, 21, tzinfo=TZ).timestamp()),
                    board="7",
                    scheduled="07-Oct-2026 05:31:00 PM",
                ),
            )
        )
        assert len(runs) == 2
        assert sorted(by_day(runs)) == [DAY, date(2026, 10, 7)]

    def test_since_drops_older_days_before_they_are_grouped(
        self, tmp_path: Path
    ) -> None:
        """The window is applied while reading, not after."""
        path = _log(
            tmp_path,
            _change(
                int(datetime(2026, 9, 1, 16, 46, tzinfo=TZ).timestamp()),
                circuit="JO-AJO13ATK",
                scheduled="01-Sep-2026 05:31:00 PM",
            ),
            _change(
                int(datetime(2026, 9, 1, 17, 21, tzinfo=TZ).timestamp()),
                board="9",
                scheduled="01-Sep-2026 05:31:00 PM",
            ),
            _change(_at(16, 46), circuit="JO-AJO13ATK"),
            _change(_at(17, 21), board="9"),
        )
        assert [run.day for run in load(path, since=DAY)] == [DAY]

    def test_accuracy_and_coverage_are_none_rather_than_zero_when_empty(
        self,
    ) -> None:
        """Nothing measured is not the same as nothing right."""
        summary = summarize([])
        assert summary.accuracy is None
        assert summary.coverage is None

    def test_a_truncated_line_does_not_lose_the_file(self, tmp_path: Path) -> None:
        """The log is read while the collector is appending to it."""
        path = tmp_path / "raildata.jsonl"
        path.write_text(
            json.dumps(_change(_at(16, 46), circuit="JO-AJO13ATK"))
            + "\n"
            + json.dumps(_change(_at(17, 21), board="9"))
            + "\n"
            + '{"type": "change", "t": 17916',
            encoding="utf-8",
        )
        runs = load(path)
        assert len(runs) == 1
        assert runs[0].agreed


class TestReport:
    def test_the_section_names_the_scoring_choice(self, tmp_path: Path) -> None:
        """A reader must be able to see which posting was scored against."""
        runs = load(
            _log(
                tmp_path,
                _change(_at(16, 46), circuit="JO-AJO13ATK"),
                _change(_at(17, 21), board="1"),
                _change(_at(17, 22), board="9"),
            )
        )
        section, rows = report(runs)
        assert "last posting" in section
        assert "revised its own answer on 1 of 1" in section
        assert rows == [
            {
                "day": "2026-10-06",
                "posted": 1,
                "covered": 1,
                "agreed": 1,
                "healed": 0,
                "corrections": 1,
                "median_lead": "45.0",
                "median_head_start": "35.0",
            }
        ]

    def test_misses_are_listed_so_they_can_be_looked_up(self, tmp_path: Path) -> None:
        runs = load(
            _log(
                tmp_path,
                _change(_at(16, 46), circuit="AA-A190TK"),
                _change(_at(17, 21), board="14"),
            )
        )
        section, _ = report(runs)
        assert (
            "| 2026-10-06 | 6273 | 17:31 | 3 | 14 | Montclair-Boonton Line |" in section
        )

    def test_an_empty_window_says_so(self) -> None:
        section, rows = report([])
        assert "No scored departures" in section
        assert rows == []


class TestRunBoundaries:
    def test_the_advancing_scheduled_time_does_not_split_a_run(
        self, tmp_path: Path
    ) -> None:
        """`sched_dep` is the next stop's time and climbs all the way out.

        Keying a run on it looked harmless -- only the fragment holding a
        posting scores -- but it moved the later sightings into fragments of
        their own, so a miss the feed corrected read as one it never did.
        """
        runs = load(
            _log(
                tmp_path,
                _change(_at(16, 46), circuit="AA-A190TK"),
                _change(_at(17, 21), board="14"),
                _change(
                    _at(17, 25),
                    circuit="JO-AJO8ATK",
                    board="14",
                    scheduled="06-Oct-2026 05:44:00 PM",
                ),
            )
        )
        assert len(runs) == 1
        assert runs[0].signal == "3"
        assert runs[0].healed

    def test_a_departure_either_side_of_midnight_is_one_run(
        self, tmp_path: Path
    ) -> None:
        """A date key would cut the late-evening trains in half."""
        runs = load(
            _log(
                tmp_path,
                _change(
                    int(datetime(2026, 10, 6, 23, 40, tzinfo=TZ).timestamp()),
                    circuit="JO-AJO13ATK",
                    scheduled="06-Oct-2026 11:50:00 PM",
                ),
                _change(
                    int(datetime(2026, 10, 6, 23, 45, tzinfo=TZ).timestamp()),
                    board="9",
                    scheduled="06-Oct-2026 11:50:00 PM",
                ),
                _change(
                    int(datetime(2026, 10, 7, 0, 5, tzinfo=TZ).timestamp()),
                    circuit="AA-0553TK",
                    scheduled="07-Oct-2026 12:14:00 AM",
                ),
            )
        )
        assert len(runs) == 1
        assert runs[0].agreed

    def test_the_scheduled_time_comes_from_the_posting_record(
        self, tmp_path: Path
    ) -> None:
        """The train is at the terminal when the board first speaks."""
        runs = load(
            _log(
                tmp_path,
                _change(_at(16, 46), circuit="JO-AJO13ATK"),
                _change(_at(17, 21), board="9"),
                _change(
                    _at(17, 40),
                    circuit="AA-0553TK",
                    board="9",
                    scheduled="06-Oct-2026 06:02:00 PM",
                ),
            )
        )
        assert runs[0].scheduled.strftime("%H:%M") == "17:31"
        assert runs[0].lead_minutes == 45
