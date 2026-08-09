"""Reading the recorded history, and reporting on it honestly.

The models decide whether a prediction is worth shipping. This half decides
what a human is told, and it has already got that wrong once in the way that
matters most: it printed `0/27 = 0% went wrong` for a station that had never
published a single delay. A confident zero, from nothing, in the report the
verdict will be read off.

So these tests are largely about the difference between "no" and "we cannot
see".
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime
from pathlib import Path

import pytest

from custom_components.njtransit.api.parsing import TZ

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from analyze_tracks import (
    LATE_ASSIGNMENT,
    Observation,
    describe,
    lateness_vs_outcome,
    load,
    main,
)

PENN = "New York Penn Station"
HILLS = "Short Hills Station"


def observation(
    train_id: str = "6613",
    *,
    station: str = PENN,
    assigned_at: int | None = 600,
    track: str | None = "4",
    worst_delay: int | None = None,
    final_status: str | None = "boarding",
    delay_at_assignment: int | None = 0,
    line: str = "Morristown Line",
    outcome_from: str | None = None,
    day: date = date(2026, 8, 5),
) -> Observation:
    """Return a recorded departure carrying only what the report reads."""
    return Observation(
        station=station,
        day=day,
        train_id=train_id,
        track=track,
        scheduled=datetime(day.year, day.month, day.day, 18, 30, tzinfo=TZ),
        line=line,
        assigned_at=assigned_at,
        reassigned=False,
        delay_at_assignment=delay_at_assignment,
        final_status=final_status,
        final_delay=worst_delay,
        worst_delay=worst_delay,
        outcome_from=outcome_from,
    )


def main_with(argv: list[str]) -> int:
    """Run the tool's entry point with a given command line."""
    original = sys.argv
    sys.argv = ["analyze_tracks.py", *argv]
    try:
        return main()
    finally:
        sys.argv = original


def squeeze(text: str) -> str:
    """Collapse runs of spaces, so assertions do not pin column widths."""
    return " ".join(text.split())


def dump(tmp_path: Path, station: str, days: dict[str, list[dict]]) -> Path:
    """Write a diagnostics download the way Home Assistant produces one."""
    path = tmp_path / f"{station.replace(' ', '_')}.json"
    path.write_text(
        json.dumps({"data": {"track_history": {"station": station, "days": days}}})
    )
    return path


class TestReportingAnAbsence:
    """The bug that reached the report, and the shape of its fix."""

    def test_a_station_that_publishes_nothing_says_so(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """`boarding` is a status, not an outcome.

        The first guard here accepted any non-null `final_status` -- which
        every row has -- so it went on to print a rate for a terminal that
        cannot report lateness at all. A reader sees `0%` and concludes the
        trains were fine.
        """
        lateness_vs_outcome([observation(final_status="boarding")])

        printed = capsys.readouterr().out
        assert "no outcomes observable here" in printed
        assert "0%" not in printed

    def test_rows_that_cannot_answer_are_excluded_from_the_denominator(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """One train known bad and three unknown is 100%, not 25%.

        Counting silence as a good outcome is the same error in a different
        direction: it would make every station look reliable in proportion to
        how little it says.
        """
        rows = [observation("1", worst_delay=20)] + [
            observation(str(n), final_status="boarding") for n in (2, 3, 4)
        ]

        lateness_vs_outcome(rows)

        printed = squeeze(capsys.readouterr().out)
        assert "1/1 = 100%" in printed
        assert "3 unknown" in printed

    def test_a_borrowed_outcome_is_labelled_as_borrowed(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Second-hand evidence is a different thing from an observation.

        A terminal's outcomes are recovered from the other end of the commute.
        That is sound, and it is not the same as having watched the train, so
        a reader deciding how much to trust a rate is told which it is.
        """
        lateness_vs_outcome([observation(worst_delay=20, outcome_from=HILLS)])

        assert "1 borrowed" in capsys.readouterr().out

    def test_nothing_recorded_at_all_is_not_a_result_either(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        lateness_vs_outcome([])

        assert "nothing timed yet" in capsys.readouterr().out


class TestTheLabelsMatchTheCut:
    """A row's label is the only thing saying what it divides."""

    def test_the_threshold_in_the_label_is_the_one_being_applied(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """These read "8 min" for a while after the constant moved to 6.

        A hardcoded number in a label is a claim about the code that stops
        being true silently, and this report is what the verdict gets read
        off.
        """
        lateness_vs_outcome([observation(worst_delay=20)])

        printed = capsys.readouterr().out
        cutoff = LATE_ASSIGNMENT // 60
        assert f"assigned normally (>= {cutoff} min)" in printed
        assert f"assigned late (< {cutoff} min)" in printed

    def test_a_late_assignment_lands_in_the_late_row(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Just inside the threshold, so the comparison direction is pinned."""
        lateness_vs_outcome(
            [observation(assigned_at=LATE_ASSIGNMENT - 60, worst_delay=20)]
        )

        printed = capsys.readouterr().out
        late_row = next(
            line for line in printed.splitlines() if "assigned late" in line
        )
        assert "1/1" in late_row


class TestAmtrakIsNeverPooled:
    """Two operating practices, and pooling them describes neither."""

    def test_amtrak_is_excluded_from_the_outcome_rates(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        rows = [
            observation("A79", line="Amtrak", worst_delay=30),
            observation("6613", worst_delay=0),
        ]

        lateness_vs_outcome(rows)

        printed = squeeze(capsys.readouterr().out)
        assert "0/1 = 0%" in printed, "an Amtrak outcome reached an NJ Transit rate"

    def test_lead_times_are_reported_separately(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Pooled, Amtrak dragged the reported p10 to 0.0 minutes.

        It announces at a median of 13 minutes but leaves 16% until the
        departure minute itself, against 1% for NJ Transit. One line
        describing both describes neither.
        """
        rows = [observation(f"{n}", assigned_at=540) for n in range(5)] + [
            observation("A79", line="Amtrak", assigned_at=0)
        ]

        describe(rows)

        printed = capsys.readouterr().out
        assert "of which Amtrak" in printed
        njt_line = next(
            line
            for line in printed.splitlines()
            if line.strip().startswith("lead time")
        )
        assert "n=5" in njt_line, "Amtrak was counted in the NJ Transit lead time"


class TestLoadingADump:
    """Diagnostics downloads, which are the only input this tool has."""

    def test_it_reads_a_home_assistant_download(self, tmp_path: Path) -> None:
        path = dump(
            tmp_path,
            PENN,
            {
                "2026-08-05": [
                    {
                        "train_id": "6613",
                        "scheduled": "18:30",
                        "track": "4",
                        "line": "Morristown Line",
                        "assigned_at": 540,
                        "worst_delay": 12,
                        "final_status": "delayed",
                    }
                ]
            },
        )

        [observed] = load([path])

        assert observed.station == PENN
        assert observed.train_id == "6613"
        assert observed.day == date(2026, 8, 5)
        assert observed.scheduled.hour == 18
        assert observed.scheduled.minute == 30
        assert observed.worst_delay == 12

    def test_a_reassignment_is_read_off_first_track(self, tmp_path: Path) -> None:
        """`first_track` is only set when a train was moved, so its presence
        is the record of a reassignment rather than a separate flag."""
        path = dump(
            tmp_path,
            PENN,
            {
                "2026-08-05": [
                    {
                        "train_id": "6613",
                        "scheduled": "18:30",
                        "track": "7",
                        "first_track": "4",
                    }
                ]
            },
        )

        assert load([path])[0].reassigned is True

    def test_a_dump_without_history_is_skipped_and_reported(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Silently returning nothing would read as a station with no data.

        Every conclusion this tool draws is about how much was collected, so a
        file that contributed none of it has to say so.
        """
        path = tmp_path / "empty.json"
        path.write_text(json.dumps({"data": {}}))

        assert load([path]) == []
        assert "no track history in this dump" in capsys.readouterr().err

    def test_several_stations_load_into_one_set(self, tmp_path: Path) -> None:
        """Both ends of a commute are needed at once.

        A terminal's outcomes only exist on the other station's board, so
        loading them separately would leave the recovery with nothing to draw
        on.
        """
        penn = dump(
            tmp_path,
            PENN,
            {"2026-08-05": [{"train_id": "6613", "scheduled": "18:30", "track": "4"}]},
        )
        hills = dump(
            tmp_path,
            HILLS,
            {"2026-08-05": [{"train_id": "6613", "scheduled": "19:10", "track": "1"}]},
        )

        assert {o.station for o in load([penn, hills])} == {PENN, HILLS}


class TestTheStationFilterRunsAfterTheJoin:
    """`--station` narrows the report, not the evidence.

    Filtering first would discard the only rows that can say how a terminal's
    trains turned out, and the result would look exactly like a station with
    no outcomes -- the failure this whole file is about, reintroduced through
    a command-line flag.
    """

    def test_a_terminals_outcomes_survive_being_filtered_to_that_terminal(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        penn = dump(
            tmp_path,
            PENN,
            {
                "2026-08-05": [
                    {
                        "train_id": "6613",
                        "scheduled": "18:30",
                        "track": "4",
                        "assigned_at": 540,
                        "final_status": "boarding",
                    }
                ]
            },
        )
        hills = dump(
            tmp_path,
            HILLS,
            {
                "2026-08-05": [
                    {
                        "train_id": "6613",
                        "scheduled": "19:10",
                        "track": "1",
                        "worst_delay": 20,
                        "final_status": "delayed",
                    }
                ]
            },
        )

        assert main_with(["--station", PENN, str(penn), str(hills)]) == 0

        printed = squeeze(capsys.readouterr().out)
        assert "1 recovered from another station" in printed
        assert "no outcomes observable here" not in printed
        assert "1 borrowed" in printed
