"""Recording track assignments.

The thing worth getting right here is not the storage -- it is knowing which
observations are trustworthy. A track seen the first time a train appears on
the board says nothing about *when* it was assigned, and treating it as if it
did would poison the one measurement that decides whether predicting a track
is worth anything: how much lead time a prediction actually buys over just
reading the board.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant

from custom_components.njtransit.api.models import (
    Departure,
    DepartureBoard,
    TrainStatus,
)
from custom_components.njtransit.api.parsing import TZ
from custom_components.njtransit.const import (
    TRACK_HISTORY_DAYS,
    TRACK_HISTORY_SAVE_DELAY,
)
from custom_components.njtransit.track_history import TrackHistory

STATION = "New York Penn Station"


def departure(
    train_id: str,
    *,
    at: datetime,
    track: str | None = None,
    delay: int | None = None,
    status: TrainStatus = TrainStatus.ON_TIME,
) -> Departure:
    """Return a board row with only the fields this module reads."""
    return Departure(
        train_id=train_id,
        scheduled=at,
        destination="Dover",
        line="Morristown Line",
        line_abbreviation="M&E",
        status=status,
        status_raw="",
        track=track,
        delay_minutes=delay,
    )


def board(*departures: Departure, station: str = STATION) -> DepartureBoard:
    """Return a board carrying the given rows."""
    return DepartureBoard(station=station, departures=departures)


@pytest.fixture(name="history")
async def history_fixture(hass: HomeAssistant) -> TrackHistory:
    """Return a loaded, empty recorder."""
    history = TrackHistory(hass)
    await history.async_load()
    return history


@pytest.fixture(name="freezer_at")
def freezer_at_fixture(freezer: FrozenDateTimeFactory) -> FrozenDateTimeFactory:
    """Pin the clock.

    Retention, and `assigned_at` itself, are both measured against now. An
    unfrozen clock makes this suite pass or fail on the date it runs.
    """
    freezer.move_to(datetime(2026, 8, 4, 18, 0, tzinfo=TZ))
    return freezer


def test_a_row_is_opened_before_a_track_exists(
    history: TrackHistory, freezer_at: FrozenDateTimeFactory
) -> None:
    """Most of a terminal's board has no track for most of the day.

    Those rows are recorded anyway. A train that never gets a track is the
    sharpest form of the question this data exists to answer, and opening the
    row only on assignment made exactly that case invisible.
    """
    when = datetime(2026, 8, 4, 18, 30, tzinfo=TZ)
    history.record(board(departure("6643", at=when)))

    [record] = history.days_for(STATION)["2026-08-04"]
    assert record["train_id"] == "6643"
    assert record["track"] is None
    assert record["seen_trackless"] is True
    assert record["assigned_at"] is None
    assert history.summary(STATION)["never_assigned"] == 1


def test_assignment_seen_happening_is_timed(
    history: TrackHistory, freezer_at: FrozenDateTimeFactory
) -> None:
    """Trackless first, track second -- so the elapsed time is known."""
    when = datetime(2026, 8, 4, 18, 30, tzinfo=TZ)
    history.record(board(departure("6643", at=when)))

    freezer_at.move_to(datetime(2026, 8, 4, 18, 20, tzinfo=TZ))
    history.record(board(departure("6643", at=when, track="4")))

    [record] = history.days_for(STATION)["2026-08-04"]
    assert record["track"] == "4"
    assert record["assigned_at"] == 600
    assert record["scheduled"] == "18:30"


def test_a_track_present_on_first_sight_is_untimed(
    history: TrackHistory, freezer_at: FrozenDateTimeFactory
) -> None:
    """Home Assistant restarting mid-window must not fake a measurement.

    The track is still worth recording -- it is the outcome any model is
    scored against. What is unknowable is *when* it was assigned, and a
    confident-looking wrong number there is worse than a null.
    """
    when = datetime(2026, 8, 4, 18, 30, tzinfo=TZ)
    history.record(board(departure("6643", at=when, track="4")))

    [record] = history.days_for(STATION)["2026-08-04"]
    assert record["track"] == "4"
    assert record["assigned_at"] is None


def test_reassignment_keeps_both_tracks(
    history: TrackHistory, freezer_at: FrozenDateTimeFactory
) -> None:
    """`track` is where the train left from; `first_track` is where it was."""
    when = datetime(2026, 8, 4, 18, 30, tzinfo=TZ)
    history.record(board(departure("6643", at=when, track="4")))
    history.record(board(departure("6643", at=when, track="7")))
    history.record(board(departure("6643", at=when, track="8")))

    [record] = history.days_for(STATION)["2026-08-04"]
    assert record["track"] == "8"
    assert record["first_track"] == "4"


def test_an_unchanged_track_is_not_a_reassignment(
    history: TrackHistory, freezer_at: FrozenDateTimeFactory
) -> None:
    """The board repeats itself every poll for ten minutes."""
    when = datetime(2026, 8, 4, 18, 30, tzinfo=TZ)
    for _ in range(5):
        history.record(board(departure("6643", at=when, track="4")))

    [record] = history.days_for(STATION)["2026-08-04"]
    assert record["first_track"] is None
    assert history.summary(STATION)["reassigned"] == 0


def test_stations_are_kept_apart(
    history: TrackHistory, freezer_at: FrozenDateTimeFactory
) -> None:
    """Short Hills is the control group, not more rows for Penn."""
    when = datetime(2026, 8, 4, 18, 30, tzinfo=TZ)
    history.record(board(departure("6643", at=when, track="4")))
    history.record(
        board(departure("6643", at=when, track="1"), station="Short Hills Station")
    )

    assert history.summary(STATION)["observations"] == 1
    assert history.summary("Short Hills Station")["observations"] == 1


def test_a_train_after_midnight_belongs_to_its_own_day(
    history: TrackHistory, freezer_at: FrozenDateTimeFactory
) -> None:
    """The day is the departure's, not the poll's.

    A board fetched at 23:58 carrying a 12:05 AM train is the rollover case
    from SPEC 3.6, and bucketing it by the fetch date would file a Wednesday
    train under Tuesday for every model that reads this back.
    """
    freezer_at.move_to(datetime(2026, 8, 4, 23, 58, tzinfo=TZ))
    when = datetime(2026, 8, 5, 0, 5, tzinfo=TZ)
    history.record(board(departure("6699", at=when, track="2")))

    assert sorted(history.days_for(STATION)) == ["2026-08-05"]


async def test_history_survives_a_restart(
    hass: HomeAssistant, freezer_at: FrozenDateTimeFactory
) -> None:
    """Collection takes weeks, so losing it to a restart loses the project."""
    when = datetime(2026, 8, 4, 18, 30, tzinfo=TZ)

    history = TrackHistory(hass)
    await history.async_load()
    history.record(board(departure("6643", at=when, track="4")))
    # Writes are coalesced, so nothing has reached disk on its own yet.
    await history.async_flush()

    revived = TrackHistory(hass)
    await revived.async_load()

    assert revived.summary(STATION)["observations"] == 1


async def test_a_restart_does_not_duplicate_a_train(
    hass: HomeAssistant, freezer_at: FrozenDateTimeFactory
) -> None:
    """The index has to be rebuilt on load, or every restart re-appends."""
    when = datetime(2026, 8, 4, 18, 30, tzinfo=TZ)

    history = TrackHistory(hass)
    await history.async_load()
    history.record(board(departure("6643", at=when, track="4")))
    await history.async_flush()

    revived = TrackHistory(hass)
    await revived.async_load()
    revived.record(board(departure("6643", at=when, track="9")))

    [record] = revived.days_for(STATION)["2026-08-04"]
    assert record["track"] == "9"
    assert record["first_track"] == "4"


async def test_days_outside_the_window_are_dropped(
    hass: HomeAssistant,
    hass_storage: dict[str, object],
    freezer_at: FrozenDateTimeFactory,
) -> None:
    """Retention is bounded, or `.storage` grows without limit."""
    old = (
        datetime(2026, 8, 4, tzinfo=TZ) - timedelta(days=TRACK_HISTORY_DAYS + 1)
    ).date()
    hass_storage["njtransit.track_history"] = {
        "version": 1,
        "data": {
            "days": {
                f"{STATION}|{old.isoformat()}": [{"train_id": "1", "track": "4"}],
                f"{STATION}|2026-08-04": [{"train_id": "2", "track": "5"}],
            }
        },
    }

    history = TrackHistory(hass)
    await history.async_load()

    assert sorted(history.days_for(STATION)) == ["2026-08-04"]


async def test_a_key_with_no_usable_date_is_dropped(
    hass: HomeAssistant,
    hass_storage: dict[str, object],
    freezer_at: FrozenDateTimeFactory,
) -> None:
    """An unparseable key can never age out, so it has to go on sight."""
    hass_storage["njtransit.track_history"] = {
        "version": 1,
        "data": {"days": {"nonsense": [{"train_id": "1", "track": "4"}]}},
    }

    history = TrackHistory(hass)
    await history.async_load()

    assert history.days_for("nonsense") == {}
    assert history.days_for("") == {}


async def test_a_corrupt_file_starts_over_instead_of_raising(
    hass: HomeAssistant, hass_storage: dict[str, object]
) -> None:
    """A broken store must not take setup down with it."""
    hass_storage["njtransit.track_history"] = {
        "version": 1,
        "data": {"days": "not a mapping"},
    }

    history = TrackHistory(hass)
    await history.async_load()

    assert history.days_for(STATION) == {}


def test_summary_reports_what_collection_looks_like(
    history: TrackHistory, freezer_at: FrozenDateTimeFactory
) -> None:
    """The numbers that say whether the experiment is running."""
    first = datetime(2026, 8, 4, 18, 30, tzinfo=TZ)
    second = datetime(2026, 8, 4, 18, 45, tzinfo=TZ)

    history.record(board(departure("6643", at=first), departure("6647", at=second)))
    freezer_at.move_to(datetime(2026, 8, 4, 18, 20, tzinfo=TZ))
    history.record(
        board(
            departure("6643", at=first, track="4"),
            departure("6647", at=second, track="7"),
        )
    )

    summary = history.summary(STATION)
    assert summary["days_collected"] == 1
    assert summary["observations"] == 2
    assert summary["distinct_trains"] == 2
    assert summary["tracks_seen"] == ["4", "7"]
    assert summary["assignments_timed"] == 2
    # 600s and 1500s.
    assert summary["median_assigned_at_seconds"] == 1050


def test_the_delay_at_assignment_is_captured(
    history: TrackHistory, freezer_at: FrozenDateTimeFactory
) -> None:
    """The field that separates a warning from a restatement.

    `assigned_at` is measured against the *scheduled* time, so a train already
    running late has its track posted late by definition. Without knowing what
    the delay was at that moment, a late assignment cannot be told apart from
    a late train -- and only the first is worth knowing about.
    """
    when = datetime(2026, 8, 4, 18, 30, tzinfo=TZ)
    history.record(board(departure("6643", at=when)))

    freezer_at.move_to(datetime(2026, 8, 4, 18, 24, tzinfo=TZ))
    history.record(board(departure("6643", at=when, track="4", delay=11)))

    [record] = history.days_for(STATION)["2026-08-04"]
    assert record["assigned_at"] == 360
    assert record["delay_at_assignment"] == 11


def test_a_train_on_time_at_assignment_records_zero_not_none(
    history: TrackHistory, freezer_at: FrozenDateTimeFactory
) -> None:
    """`None` means the board had no realtime data; 0 means it said on time."""
    when = datetime(2026, 8, 4, 18, 30, tzinfo=TZ)
    history.record(board(departure("6643", at=when)))
    history.record(board(departure("6643", at=when, track="4", delay=0)))

    [record] = history.days_for(STATION)["2026-08-04"]
    assert record["delay_at_assignment"] == 0


def test_the_outcome_follows_the_train(
    history: TrackHistory, freezer_at: FrozenDateTimeFactory
) -> None:
    """Whatever was last seen is how it turned out -- the board drops it after."""
    when = datetime(2026, 8, 4, 18, 30, tzinfo=TZ)
    history.record(board(departure("6643", at=when, track="4", delay=0)))
    history.record(board(departure("6643", at=when, track="4", delay=6)))
    history.record(
        board(departure("6643", at=when, track="4", status=TrainStatus.CANCELLED))
    )

    [record] = history.days_for(STATION)["2026-08-04"]
    assert record["final_status"] == "cancelled"
    assert history.summary(STATION)["cancelled"] == 1


def test_a_train_that_never_got_a_track_is_counted(
    history: TrackHistory, freezer_at: FrozenDateTimeFactory
) -> None:
    """The case the whole hypothesis is about."""
    when = datetime(2026, 8, 4, 18, 30, tzinfo=TZ)
    for _ in range(3):
        history.record(board(departure("6643", at=when)))
    history.record(board(departure("6647", at=when, track="2")))

    summary = history.summary(STATION)
    assert summary["observations"] == 2
    assert summary["never_assigned"] == 1
    assert summary["tracks_seen"] == ["2"]


def test_median_lead_time_over_an_odd_number_of_assignments(
    history: TrackHistory, freezer_at: FrozenDateTimeFactory
) -> None:
    """Three trains, so the middle one is the answer rather than a mean."""
    times = [datetime(2026, 8, 4, 18, minute, tzinfo=TZ) for minute in (30, 40, 50)]
    history.record(board(*(departure(str(i), at=at) for i, at in enumerate(times))))

    freezer_at.move_to(datetime(2026, 8, 4, 18, 20, tzinfo=TZ))
    history.record(
        board(*(departure(str(i), at=at, track="4") for i, at in enumerate(times)))
    )

    # 600s, 1200s, 1800s.
    assert history.summary(STATION)["median_assigned_at_seconds"] == 1200


def test_summary_of_an_unwatched_station_is_empty(history: TrackHistory) -> None:
    assert history.summary("Trenton Station") == {
        "days_collected": 0,
        "observations": 0,
        "distinct_trains": 0,
        "tracks_seen": [],
        "reassigned": 0,
        "median_assigned_at_seconds": None,
        "assignments_timed": 0,
        "never_assigned": 0,
        "cancelled": 0,
    }


def test_a_constant_stream_of_changes_still_reaches_disk(
    history: TrackHistory,
    freezer_at: FrozenDateTimeFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The save deadline must not be pushed back by every poll.

    `Store.async_delay_save` restarts its timer on each call, so a recorder
    that marks itself dirty more often than the delay never writes at all --
    the data only lands at shutdown, and a crash loses everything since boot.
    Observed live: 22 minutes of recording against a 10-minute delay left the
    file untouched.

    The delay handed to the store must therefore shrink toward a fixed
    deadline rather than resetting to the full value.
    """
    when = datetime(2026, 8, 4, 18, 30, tzinfo=TZ)
    delays: list[float] = []

    def capture(_data: Callable[[], dict[str, Any]], delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr(history._store, "async_delay_save", capture)

    # A board that changes every minute, as a real one does.
    for minute in range(12):
        freezer_at.move_to(datetime(2026, 8, 4, 18, minute, tzinfo=TZ))
        history.record(board(departure("6643", at=when, track="4", delay=minute)))

    assert 0 in delays, f"the deadline was never reached, got {delays}"

    # Everything up to that first write counts down toward the fixed deadline
    # instead of resetting. After it, a fresh deadline starts -- which is why
    # this checks the first cycle rather than the whole sequence.
    countdown = delays[: delays.index(0) + 1]
    assert countdown == sorted(countdown, reverse=True), countdown
    assert countdown[0] == TRACK_HISTORY_SAVE_DELAY
