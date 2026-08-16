"""The fitted track ranker, and the ways a fitted model flatters itself.

A hand-written model that reads the answer produces a suspicious number. A
*fitted* one produces a beautiful number and an explanation for it, because
the weights will happily reorganise themselves around whatever leaked. The
first version of `analyze_tracks.score` handed each model the day it was being
tested on and reported 100%; the same mistake here would be harder to spot,
not easier, because a learned model is expected to be better than a rule.

So most of what is asserted below is about what the ranker cannot see: the
held-out day, and any day later than the one being predicted.
"""

from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path

import numpy as np

from custom_components.njtransit.api.parsing import TZ

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from analyze_tracks import Observation
from learn_tracks import FEATURES, TRACKS, candidates, fit, rank, score

STATION = "New York Penn Station"


def observation(
    train_id: str,
    track: str | None,
    *,
    day: date = date(2026, 8, 5),
    at: tuple[int, int] = (18, 30),
    line: str = "Morristown Line",
    assigned_at: int = 540,
) -> Observation:
    """Return a recorded departure carrying only what the ranker reads."""
    return Observation(
        station=STATION,
        day=day,
        train_id=train_id,
        track=track,
        scheduled=datetime(day.year, day.month, day.day, at[0], at[1], tzinfo=TZ),
        line=line,
        assigned_at=assigned_at,
        reassigned=False,
        delay_at_assignment=None,
        final_status="boarding",
        final_delay=None,
        worst_delay=None,
    )


def feature(rows: np.ndarray, track: str, name: str) -> float:
    """Return one feature of one candidate track, by name."""
    return float(rows[TRACKS.index(track)][FEATURES.index(name)])


class TestItCannotSeeTheAnswer:
    """The leakage guards. Everything else here is detail."""

    def test_the_held_out_day_is_unpredictable_and_scores_zero(self) -> None:
        """One train, a different track every day, and nothing else to go on.

        There is no signal here by construction, so an honest ranker must
        score zero. A ranker trained on the day it is scored against will find
        the answer sitting in its own features and score perfectly -- the same
        failure that produced `m2 by train+weekday` at 100%, but with a fitted
        model's air of authority over it.
        """
        rows = [
            observation("6613", track, day=day)
            for day, track in zip(
                (date(2026, 8, 3), date(2026, 8, 4), date(2026, 8, 5)),
                ("4", "9", "13"),
                strict=True,
            )
        ]

        result = score(rows)

        assert result is not None
        assert result.hits == 0, "the ranker scored on data that cannot be predicted"

    def test_a_later_day_is_not_a_feature(self) -> None:
        """Tomorrow is not evidence about today.

        The harness withholds only the held-out day, so every other day --
        including days after the target -- reaches the feature builder. The
        recency features would read them happily.
        """
        target = observation("6613", None, day=date(2026, 8, 5))
        history = [
            observation("6613", "3", day=date(2026, 8, 4)),
            observation("6613", "11", day=date(2026, 8, 6)),
            observation("6613", "11", day=date(2026, 8, 7)),
        ]

        rows = candidates(history, [], target)

        assert feature(rows, "3", "last track used") == 1.0
        assert feature(rows, "11", "last track used") == 0.0
        assert feature(rows, "11", "train history") == 0.0

    def test_two_days_are_not_enough_to_train_and_test(self) -> None:
        """One training day and one test day is not a fold, it is a coin toss."""
        assert (
            score(
                [
                    observation("6613", "4", day=date(2026, 8, 4)),
                    observation("6613", "4", day=date(2026, 8, 5)),
                ]
            )
            is None
        )


class TestTheFeatures:
    """What each candidate row says about its track."""

    def test_a_track_this_train_has_never_used_is_flagged(self) -> None:
        target = observation("6613", None, day=date(2026, 8, 5))
        history = [observation("6613", "3", day=date(2026, 8, 4))]

        rows = candidates(history, [], target)

        assert feature(rows, "3", "never used by this train") == 0.0
        assert feature(rows, "11", "never used by this train") == 1.0

    def test_history_is_a_share_not_a_count(self) -> None:
        """A train seen forty times and one seen four are on the same scale.

        Counts would make the weight on this feature depend on how long the
        collection has been running, which is not a property of the railway.
        """
        target = observation("6613", None, day=date(2026, 8, 5))
        history = [
            observation("6613", "3", day=date(2026, 8, 1)),
            observation("6613", "3", day=date(2026, 8, 2)),
            observation("6613", "7", day=date(2026, 8, 3)),
        ]

        rows = candidates(history, [], target)

        assert feature(rows, "3", "train history") == 2 / 3
        assert feature(rows, "7", "train history") == 1 / 3

    def test_the_vacancy_feature_only_reads_public_assignments(self) -> None:
        """The same as-of rule the hand-written models follow.

        A departure ten minutes before the target has its own track posted
        about nine minutes before *it* leaves, which is long after the moment
        this prediction is made. Reading it would be reading a blank board.
        """
        target = observation("6613", None, at=(18, 30))
        # Posted an hour ahead, so public well before the prediction moment.
        early = observation("3889", "3", at=(18, 5), assigned_at=3600)
        # Posted nine minutes ahead, so still secret at T-30.
        late = observation("3891", "7", at=(18, 25))

        rows = candidates([], [early, late], target)

        assert feature(rows, "3", "minutes since vacated") < 1.0, (
            "a track known to have emptied was treated as cold"
        )
        assert feature(rows, "7", "minutes since vacated") == 1.0

    def test_every_track_is_a_candidate_even_if_never_seen(self) -> None:
        """A fold whose training days missed a platform must still offer it.

        Deriving the choice set from the data would quietly make rare tracks
        unpredictable, and rare tracks are exactly where a wrong answer is
        least recoverable for a passenger.
        """
        rows = candidates([observation("6613", "3")], [], observation("6613", None))

        assert rows.shape == (16, len(FEATURES))


class TestFitting:
    """The optimiser itself, on a problem with a known answer."""

    def test_it_learns_which_feature_predicts(self) -> None:
        """Two features, one of which is the answer and one of which is noise."""
        examples = []
        for index in range(8):
            answer = index % len(TRACKS)
            rows = np.zeros((len(TRACKS), 2))
            rows[answer][0] = 1.0
            rows[(answer + 1) % len(TRACKS)][1] = 1.0
            examples.append((rows, answer))

        weights = fit(examples)

        assert weights[0] > weights[1], "the predictive feature was not preferred"

    def test_the_ranking_is_ordered_by_score(self) -> None:
        rows = np.zeros((len(TRACKS), 1))
        rows[TRACKS.index("9")][0] = 2.0
        rows[TRACKS.index("4")][0] = 1.0

        ranked = rank(np.array([1.0]), rows)

        assert ranked[0] == "9"
        assert ranked[1] == "4"
