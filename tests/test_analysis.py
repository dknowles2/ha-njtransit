"""The analysis tool, where the track-prediction decision actually gets made.

Only `join_outcomes` is covered, because it is the part whose failure is
silent. A join that quietly matches nothing looks exactly like a station that
had nothing to report, and the conclusion drawn from it -- "no evidence either
way" -- is the same sentence in both cases.
"""

from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path

from custom_components.njtransit.api.parsing import TZ

# `scripts/` is a directory of tools rather than a package, so it is not
# importable without this. Kept here rather than in the tool so the tool stays
# a plain script anyone can run.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from analyze_tracks import Observation, join_outcomes

PENN = "New York Penn Station"
SHORT_HILLS = "Short Hills Station"


def observation(
    station: str,
    train_id: str,
    *,
    day: date = date(2026, 8, 5),
    worst_delay: int | None = None,
    final_status: str | None = "boarding",
    track: str | None = "4",
) -> Observation:
    """Return a recorded departure carrying only what the join reads."""
    return Observation(
        station=station,
        day=day,
        train_id=train_id,
        track=track,
        scheduled=datetime(day.year, day.month, day.day, 18, 30, tzinfo=TZ),
        line="Morristown Line",
        assigned_at=540,
        reassigned=False,
        delay_at_assignment=None,
        final_status=final_status,
        final_delay=worst_delay,
        worst_delay=worst_delay,
    )


def test_a_terminal_borrows_an_outcome_from_downstream() -> None:
    """The case this exists for.

    New York Penn publishes no lateness at all, so a row recorded there can say
    when its track appeared but never how the train turned out. Forty minutes
    later the same train is counted down at a through station.
    """
    joined = join_outcomes(
        [
            observation(PENN, "6613", worst_delay=None, final_status="boarding"),
            observation(SHORT_HILLS, "6613", worst_delay=12, final_status="delayed"),
        ]
    )

    penn = next(o for o in joined if o.station == PENN)
    assert penn.outcome_known is True
    assert penn.worst_delay == 12
    assert penn.went_wrong is True
    assert penn.outcome_from == SHORT_HILLS


def test_a_row_that_knows_its_own_outcome_is_left_alone() -> None:
    """Borrowing over a first-hand observation would be a downgrade."""
    joined = join_outcomes(
        [
            observation(PENN, "6613", worst_delay=3),
            observation(SHORT_HILLS, "6613", worst_delay=25),
        ]
    )

    penn = next(o for o in joined if o.station == PENN)
    assert penn.worst_delay == 3
    assert penn.outcome_from is None


def test_an_outcome_is_never_borrowed_from_the_same_station() -> None:
    """Two rows for one train at one station are the same sighting.

    Filling one from the other would invent corroboration that does not exist.
    """
    joined = join_outcomes(
        [
            observation(PENN, "6613", day=date(2026, 8, 5), worst_delay=None),
            observation(PENN, "6613", day=date(2026, 8, 6), worst_delay=9),
        ]
    )

    blank = next(o for o in joined if o.day == date(2026, 8, 5))
    assert blank.outcome_known is False
    assert blank.outcome_from is None


def test_a_train_crossing_midnight_still_joins() -> None:
    """Each station files a departure under its own scheduled date.

    A train leaving Penn at 23:50 reaches Short Hills after midnight and is
    filed a day later. Matching on the same day alone would drop exactly the
    late-evening trains a commuter most wants to know about.
    """
    joined = join_outcomes(
        [
            observation(PENN, "6699", day=date(2026, 8, 5), worst_delay=None),
            observation(SHORT_HILLS, "6699", day=date(2026, 8, 6), worst_delay=18),
        ]
    )

    penn = next(o for o in joined if o.station == PENN)
    assert penn.worst_delay == 18
    assert penn.outcome_from == SHORT_HILLS


def test_a_train_two_days_apart_is_a_different_journey() -> None:
    """The window is deliberately one day, not "any day".

    The same train number runs every weekday. Reaching further would pair
    Tuesday's service with Thursday's and report it as one journey.
    """
    joined = join_outcomes(
        [
            observation(PENN, "6613", day=date(2026, 8, 5), worst_delay=None),
            observation(SHORT_HILLS, "6613", day=date(2026, 8, 7), worst_delay=30),
        ]
    )

    penn = next(o for o in joined if o.station == PENN)
    assert penn.outcome_known is False


def test_a_train_seen_nowhere_else_stays_unknown() -> None:
    """Unknown has to survive the join, or every gap becomes a silent zero."""
    joined = join_outcomes([observation(PENN, "6613", worst_delay=None)])

    assert joined[0].outcome_known is False
    assert joined[0].went_wrong is False
    assert joined[0].outcome_from is None


def test_a_cancellation_counts_as_a_known_outcome_worth_borrowing() -> None:
    """Cancelled is the one outcome a terminal does report, and it travels."""
    joined = join_outcomes(
        [
            observation(SHORT_HILLS, "6320", worst_delay=None, final_status=None),
            observation(
                PENN, "6320", worst_delay=None, final_status="cancelled", track=None
            ),
        ]
    )

    hills = next(o for o in joined if o.station == SHORT_HILLS)
    assert hills.final_status == "cancelled"
    assert hills.went_wrong is True
    assert hills.outcome_from == PENN
