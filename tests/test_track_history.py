"""Recording track assignments.

The thing worth getting right here is not the storage -- it is knowing which
observations are trustworthy. A track seen the first time a train appears on
the board says nothing about *when* it was assigned, and treating it as if it
did would poison the one measurement that decides whether predicting a track
is worth anything: how much lead time a prediction actually buys over just
reading the board.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant

from custom_components.njtransit.api.models import (
    Departure,
    DepartureBoard,
    TrainStatus,
)
from custom_components.njtransit.api.parsing import TZ
from custom_components.njtransit.const import TRACK_HISTORY_DAYS
from custom_components.njtransit.track_history import TrackHistory

STATION = "New York Penn Station"


def departure(
    train_id: str,
    *,
    at: datetime,
    track: str | None = None,
) -> Departure:
    """Return a board row with only the fields this module reads."""
    return Departure(
        train_id=train_id,
        scheduled=at,
        destination="Dover",
        line="Morristown Line",
        line_abbreviation="M&E",
        status=TrainStatus.ON_TIME,
        status_raw="",
        track=track,
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


def test_a_row_without_a_track_records_nothing(
    history: TrackHistory, freezer_at: FrozenDateTimeFactory
) -> None:
    """Most of a terminal's board has no track for most of the day."""
    when = datetime(2026, 8, 4, 18, 30, tzinfo=TZ)
    history.record(board(departure("6643", at=when)))

    assert history.days_for(STATION) == {}


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
    }
