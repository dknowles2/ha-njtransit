"""The assignment model: one board at a time, under mutual exclusion.

Two things can go wrong here that a percentage would not reveal. The solver
can be subtly wrong -- a greedy pick dressed as an optimum -- so it is checked
against a matrix whose best assignment is known and whose greedy answer is
different. And the constraint can silently not bind, which would leave an
expensive reimplementation of `learn_tracks` scoring identically to it and
looking like a finding.
"""

from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path

from custom_components.njtransit.api.parsing import TZ

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from analyze_tracks import Observation
from assign_tracks import assign_day, score
from learn_tracks import FEATURES

STATION = "New York Penn Station"


def observation(
    train_id: str,
    track: str | None,
    *,
    day: date = date(2026, 8, 5),
    at: tuple[int, int] = (18, 30),
) -> Observation:
    """Return a recorded departure carrying only what the model reads."""
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


class TestTheConstraintBinds:
    """If exclusion never fires, this is `learn_tracks` with extra steps."""

    def test_two_trains_minutes_apart_cannot_share_a_platform(self) -> None:
        """The failure the whole model exists to prevent.

        Both trains have only ever used track 4, so an independent ranker
        gives both track 4 and one of those predictions is guaranteed wrong.
        Solving the board once is what makes the two answers consistent; an
        earlier version solved per train, handed each the assignment in which
        it had priority, and promised platform 4 to both.
        """
        history = [
            observation("6613", "4", day=date(2026, 8, 3)),
            observation("6613", "4", day=date(2026, 8, 4)),
            observation("3889", "4", day=date(2026, 8, 3)),
            observation("3889", "4", day=date(2026, 8, 4)),
        ]
        early = observation("6613", None, at=(18, 30))
        late = observation("3889", None, at=(18, 33))
        weights = [1.0, 1.0, 1.0, 1.0, 1.0, 0.0, -1.0] + [0.0] * (len(FEATURES) - 7)

        ranked = assign_day(history, [early, late], weights)

        assert ranked["6613"][0] != ranked["3889"][0], (
            "one platform was promised to two trains"
        )

    def test_the_earlier_train_keeps_its_first_choice(self) -> None:
        """Whoever is standing on the platform has it.

        Which also fixes the order the result can be read in: without a rule
        the loser of a tie is whichever the dict happened to reach first.
        """
        history = [
            observation("6613", "4", day=date(2026, 8, 3)),
            observation("3889", "4", day=date(2026, 8, 3)),
        ]
        early = observation("6613", None, at=(18, 30))
        late = observation("3889", None, at=(18, 33))

        ranked = assign_day(history, [late, early], [1.0] * len(FEATURES))

        assert ranked["6613"][0] == "4"

    def test_a_platform_is_reused_later_in_the_day(self) -> None:
        """The constraint is an occupied platform, not a train existing.

        This is what makes the problem an interval colouring rather than a
        matching: platform 4 serves both of these, hours apart, and a model
        that forbade that would be wrong about most of the timetable.
        """
        history = [
            observation("6613", "4", day=date(2026, 8, 3)),
            observation("3889", "4", day=date(2026, 8, 3)),
        ]
        morning = observation("6613", None, at=(8, 30))
        evening = observation("3889", None, at=(21, 0))

        ranked = assign_day(history, [morning, evening], [1.0] * len(FEATURES))

        assert ranked["6613"][0] == "4"
        assert ranked["3889"][0] == "4"


class TestItCannotSeeTheAnswer:
    """The same leakage guard as every other model in this table."""

    def test_the_held_out_day_is_unpredictable_and_scores_zero(self) -> None:
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
        assert result.hits == 0, "the model scored on data that cannot be predicted"
