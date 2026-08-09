"""The candidate track-prediction models, and the harness that scores them.

This is the code that decides whether a feature ships. Its failure mode is not
a crash -- it is a number that looks like a result. The first version of the
scoring loop handed each model the day it was being tested on, and `m2 by
train+weekday` came back at 100%: a perfect score, produced by reading the
answer. Nothing about that output looked wrong.

So the tests here are mostly about what a model must *not* be able to see.
"""

from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path

from custom_components.njtransit.api.parsing import TZ

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from analyze_tracks import (
    MODELS,
    Observation,
    _quantile,
    _reuse_gaps,
    m0_global_mode,
    m1_by_train,
    m3_by_time_slot,
    m4_by_train_minus_conflicts,
    score,
)

STATION = "New York Penn Station"


def observation(
    train_id: str,
    track: str | None,
    *,
    day: date = date(2026, 8, 5),
    at: tuple[int, int] = (18, 30),
) -> Observation:
    """Return a recorded departure carrying only what a model reads."""
    return Observation(
        station=STATION,
        day=day,
        train_id=train_id,
        track=track,
        scheduled=datetime(day.year, day.month, day.day, at[0], at[1], tzinfo=TZ),
        line="Morristown Line",
        assigned_at=540,
        reassigned=False,
        delay_at_assignment=None,
        final_status="boarding",
        final_delay=None,
        worst_delay=None,
    )


class TestNoModelCanSeeItsOwnAnswer:
    """The leakage guard, which is the whole reason this file exists."""

    def test_the_scoring_loop_withholds_the_held_out_day(self) -> None:
        """The bug that produced a perfect score from nothing.

        One train, a different track every day, and no other signal. A model
        that cannot see the held-out day must score zero -- there is nothing
        in the remaining days that predicts the answer. A model that *can* see
        it scores 100%, which is exactly what `m2 by train+weekday` reported
        on its first run.

        This asserts against the real loop rather than a hand-rolled split,
        because the split is the thing that was wrong.
        """
        rows = [
            observation("6613", "4", day=date(2026, 8, 3)),
            observation("6613", "9", day=date(2026, 8, 4)),
            observation("6613", "13", day=date(2026, 8, 5)),
        ]

        scores = score(rows)
        assert scores is not None
        for name, result in scores.items():
            assert result.hits == 0, f"{name} scored on data that cannot be predicted"

    def test_a_models_own_day_is_not_in_its_context_either(self) -> None:
        """The target is removed from the same-day context it is handed.

        m4 reads that context to find which tracks are busy. Left in, the
        target's own row marks its own track as occupied and the model rules
        out the right answer -- the mirror of leakage, and just as invisible
        in a percentage.

        The shape matters. m4 falls back to its unfiltered ranking when
        exclusion empties the list, so a train with only one candidate track
        cannot tell the two apart: it is rescued by the fallback either way.
        This train has two, so excluding the right one leaves the wrong one
        standing and the score moves from 2 to 0.
        """
        rows = [
            observation("6613", "4", day=date(2026, 8, 3), at=(18, 30)),
            observation("6613", "4", day=date(2026, 8, 4), at=(18, 30)),
            observation("6613", "7", day=date(2026, 8, 5), at=(18, 30)),
        ]

        scores = score(rows)
        assert scores is not None
        assert scores["m4 m1 - conflicts"].hits == 2

    def test_one_day_cannot_be_scored_at_all(self) -> None:
        """There is nothing to hold out, and a number here would be invented."""
        assert score([observation("6613", "4")]) is None

    def test_a_model_with_nothing_to_go_on_declines_to_answer(self) -> None:
        """An empty ranking is scored as unanswered, not as wrong.

        Returning a guess here would quietly convert "no data" into a wrong
        prediction, which flatters any model that guesses less often.
        """
        target = observation("6613", "9")

        assert m1_by_train([], [], target) == []
        assert m0_global_mode([], [], target) == []


class TestModelsRankRatherThanGuess:
    """Each returns an ordered list; top-1 and top-3 both read from it."""

    def test_the_commonest_track_comes_first(self) -> None:
        history = [
            observation("1", "4"),
            observation("2", "4"),
            observation("3", "7"),
        ]
        assert m0_global_mode(history, [], observation("9", None)) == ["4", "7"]

    def test_a_train_is_ranked_on_its_own_history_only(self) -> None:
        history = [
            observation("6613", "4"),
            observation("6613", "4"),
            observation("6613", "7"),
            observation("9999", "12"),
        ]
        assert m1_by_train(history, [], observation("6613", None)) == ["4", "7"]

    def test_the_time_slot_model_ignores_the_train_number(self) -> None:
        """A renumbered service is invisible to a train-number model.

        Timetable changes move numbers more readily than they move departure
        times, so this is the model that survives one.
        """
        history = [
            observation("1111", "4", at=(18, 32)),
            observation("2222", "4", at=(18, 27)),
            observation("3333", "9", at=(19, 30)),
        ]
        ranked = m3_by_time_slot(history, [], observation("6613", None, at=(18, 30)))
        assert ranked == ["4"], "a departure an hour away was treated as the same slot"


class TestConflictExclusion:
    """m4, the only model that beat its baseline at Penn."""

    def test_a_track_in_use_at_that_moment_is_dropped(self) -> None:
        """Two trains cannot leave one platform at once.

        This is the single piece of physical knowledge available: the board
        cannot say which track a train will use, but it does say which are
        already spoken for.
        """
        target = observation("6613", None, at=(18, 30))
        history = [observation("6613", "4"), observation("6613", "7")]
        same_day = [observation("9999", "4", at=(18, 33))]

        assert m4_by_train_minus_conflicts(history, same_day, target) == ["7"]

    def test_a_departure_well_clear_does_not_block_a_track(self) -> None:
        target = observation("6613", None, at=(18, 30))
        history = [observation("6613", "4")]
        same_day = [observation("9999", "4", at=(20, 0))]

        assert m4_by_train_minus_conflicts(history, same_day, target) == ["4"]

    def test_excluding_everything_falls_back_rather_than_answering_nothing(
        self,
    ) -> None:
        """Every candidate busy is not the same as having no candidates.

        Returning empty would score as unanswered and quietly improve the
        model's apparent accuracy, because the cases it finds hardest would
        stop counting against it.
        """
        target = observation("6613", None, at=(18, 30))
        history = [observation("6613", "4")]
        same_day = [observation("9999", "4", at=(18, 31))]

        assert m4_by_train_minus_conflicts(history, same_day, target) == ["4"]

    def test_it_falls_back_to_the_baseline_for_an_unseen_train(self) -> None:
        """A train with no history is the common case early in collection."""
        target = observation("6613", None)
        history = [observation("1234", "11"), observation("5678", "11")]

        assert m4_by_train_minus_conflicts(history, [], target) == ["11"]


class TestReuseGaps:
    """How long a track sits between departures -- the basis for m4."""

    def test_gaps_are_measured_between_consecutive_uses_of_one_track(self) -> None:
        rows = [
            observation("1", "4", at=(18, 0)),
            observation("2", "4", at=(18, 30)),
            observation("3", "4", at=(19, 15)),
            observation("4", "7", at=(18, 5)),
        ]
        assert _reuse_gaps(rows) == [30, 45]

    def test_a_track_used_once_contributes_no_gap(self) -> None:
        assert _reuse_gaps([observation("1", "4")]) == []

    def test_rows_without_a_track_are_ignored(self) -> None:
        """Most of a terminal's board has no track for most of the day."""
        rows = [observation("1", None, at=(18, 0)), observation("2", None, at=(18, 30))]
        assert _reuse_gaps(rows) == []


class TestQuantile:
    """Hand-rolled because the analysis is stdlib-only."""

    def test_it_reports_a_value_from_the_data(self) -> None:
        values = list(range(1, 101))
        assert _quantile(values, 0.10) == 11
        assert _quantile(values, 0.90) == 91

    def test_a_single_value_is_every_quantile(self) -> None:
        assert _quantile([7], 0.10) == 7.0
        assert _quantile([7], 0.90) == 7.0

    def test_it_does_not_run_off_the_end(self) -> None:
        """`int(1.0 * len)` indexes one past the last element."""
        assert _quantile([1, 2, 3], 1.0) == 3.0


class TestTheScoringLoopItself:
    """Leave-one-day-out, end to end, on data with a known right answer."""

    def test_a_perfectly_predictable_station_scores_perfectly(self) -> None:
        """The control, in miniature.

        Short Hills scores 99% because each train really does use one track.
        If this cannot reproduce that on data engineered to be trivial, a low
        score at Penn says nothing about Penn.
        """
        days = [date(2026, 8, 3), date(2026, 8, 4), date(2026, 8, 5)]
        rows = [observation("6613", "4", day=day) for day in days]

        for held_out in days:
            history = [o for o in rows if o.day != held_out]
            target = next(o for o in rows if o.day == held_out)
            assert m1_by_train(history, [], target)[0] == target.track

    def test_a_random_station_does_not(self) -> None:
        """The negative control.

        A model that scored well here would be reading something other than
        the data.
        """
        days = [date(2026, 8, 3), date(2026, 8, 4), date(2026, 8, 5)]
        tracks = ["4", "9", "13"]
        rows = [
            observation("6613", track, day=day)
            for day, track in zip(days, tracks, strict=True)
        ]

        hits = 0
        for held_out in days:
            history = [o for o in rows if o.day != held_out]
            target = next(o for o in rows if o.day == held_out)
            ranked = m1_by_train(history, [], target)
            hits += bool(ranked and ranked[0] == target.track)

        assert hits == 0, "a model predicted a track it had never seen this train use"


def test_every_model_is_scored() -> None:
    """The table is built from MODELS, so a model added and not registered
    would simply never appear -- and its absence reads as it not being tried.
    """
    assert set(MODELS) == {
        "m0 global mode",
        "m1 by train",
        "m2 by train+weekday",
        "m3 by time slot",
        "m4 m1 - conflicts",
    }
