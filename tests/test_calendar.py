"""Calendar platform."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.njtransit.api.parsing import TZ
from custom_components.njtransit.calendar import CANCELLED_PREFIX, DepartureCalendar

from .conftest import install_api_mock
from .test_init import make_entry, setup_entry

ENTITY = "calendar.short_hills_station_to_new_york_penn_station_departures"

CAPTURED_AT = datetime(2026, 8, 3, 8, 20, tzinfo=TZ)


@pytest.fixture(name="at_capture_time", autouse=True)
def at_capture_time_fixture(freezer: FrozenDateTimeFactory) -> None:
    """Freeze the clock at the moment the fixtures were recorded."""
    freezer.move_to(CAPTURED_AT)


def calendar_entity(hass: HomeAssistant) -> DepartureCalendar:
    """Return the calendar entity object itself.

    async_get_events is not reachable through the state machine, so tests
    that exercise ranges need the object.
    """
    component = hass.data["domain_entities"]["calendar"]
    entity = component[ENTITY]
    assert isinstance(entity, DepartureCalendar)
    return entity


class TestSetup:
    """When a calendar exists at all."""

    async def test_created_for_a_commute_with_a_destination(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        install_api_mock(aioclient_mock)
        await setup_entry(hass, make_entry())

        assert hass.states.get(ENTITY) is not None

    async def test_not_created_without_a_destination(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """With no destination there is no journey to schedule."""
        install_api_mock(aioclient_mock)
        await setup_entry(hass, make_entry(destination=None))

        assert not [
            state
            for state in hass.states.async_all("calendar")
            if "short_hills" in state.entity_id
        ]


class TestEvents:
    """Event contents."""

    async def test_next_departure_is_the_current_event(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        install_api_mock(aioclient_mock)
        await setup_entry(hass, make_entry())

        event = calendar_entity(hass).event
        assert event is not None
        assert event.summary.startswith("Train ")
        assert event.end > event.start

    async def test_arrival_is_the_event_end(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """End comes from the last rail leg, not a parsed duration string."""
        install_api_mock(aioclient_mock)
        await setup_entry(hass, make_entry())

        events = await calendar_entity(hass).async_get_events(
            hass, CAPTURED_AT - timedelta(days=1), CAPTURED_AT + timedelta(days=2)
        )
        assert events
        assert all(event.end > event.start for event in events)

    async def test_transfers_are_described(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """The Gladstone itinerary changes at Summit."""
        install_api_mock(aioclient_mock)
        await setup_entry(hass, make_entry())

        events = await calendar_entity(hass).async_get_events(
            hass, CAPTURED_AT - timedelta(days=1), CAPTURED_AT + timedelta(days=2)
        )
        described = [
            event
            for event in events
            if event.description and "Change trains" in event.description
        ]
        assert described, "no transfer itinerary was described"

    async def test_location_is_the_origin(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        install_api_mock(aioclient_mock)
        await setup_entry(hass, make_entry())

        event = calendar_entity(hass).event
        assert event is not None
        assert event.location == "Short Hills Station"

    async def test_uids_are_stable_and_unique(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """Otherwise subscribers see every event vanish and return daily."""
        install_api_mock(aioclient_mock)
        await setup_entry(hass, make_entry())

        entity = calendar_entity(hass)
        window = (CAPTURED_AT - timedelta(days=1), CAPTURED_AT + timedelta(days=2))
        first = await entity.async_get_events(hass, *window)
        second = await entity.async_get_events(hass, *window)

        uids = [event.uid for event in first]
        assert uids == [event.uid for event in second]
        assert len(uids) == len(set(uids))


class TestRealtimeOverlay:
    """The one piece of realtime the timetable gets."""

    async def test_cancellation_is_folded_into_the_summary(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """Train 6328 is in the timetable; mark it cancelled on the board."""
        install_api_mock(
            aioclient_mock,
            {
                "TrainDepartureScreens": {
                    "data": {
                        "getTrainDepartureScreens": {
                            "items": [
                                {
                                    "trainID": "6328",
                                    "departureDate": "9:55 AM",
                                    "destination": "New York",
                                    "line": "Morristown Line",
                                    "lineAbbreviation": "M&E",
                                    "status": "Cancelled",
                                    "track": "2",
                                    "inlineMessage": "",
                                    "stops": [],
                                    "capacity": None,
                                }
                            ]
                        }
                    }
                }
            },
        )
        await setup_entry(hass, make_entry())

        events = await calendar_entity(hass).async_get_events(
            hass, CAPTURED_AT - timedelta(days=1), CAPTURED_AT + timedelta(days=2)
        )
        cancelled = [e for e in events if e.summary.startswith(CANCELLED_PREFIX)]
        assert cancelled, "a cancelled train was not marked in the calendar"
        assert all("6328" in event.summary for event in cancelled)

    async def test_timetable_events_survive_a_clean_board(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """The calendar is the timetable, not the board."""
        install_api_mock(
            aioclient_mock,
            {
                "TrainDepartureScreens": {
                    "data": {"getTrainDepartureScreens": {"items": []}}
                }
            },
        )
        await setup_entry(hass, make_entry())

        events = await calendar_entity(hass).async_get_events(
            hass, CAPTURED_AT - timedelta(days=1), CAPTURED_AT + timedelta(days=2)
        )
        assert events
        assert not any(e.summary.startswith(CANCELLED_PREFIX) for e in events)


class TestRanges:
    """Range queries and the horizon."""

    async def test_filters_to_the_requested_range(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        install_api_mock(aioclient_mock)
        await setup_entry(hass, make_entry())

        entity = calendar_entity(hass)
        everything = await entity.async_get_events(
            hass, CAPTURED_AT - timedelta(days=1), CAPTURED_AT + timedelta(days=2)
        )
        narrow = await entity.async_get_events(
            hass, CAPTURED_AT, CAPTURED_AT + timedelta(hours=2)
        )
        assert len(narrow) < len(everything)

    async def test_a_month_view_returns_only_the_horizon(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """A real, documented limitation rather than a bug.

        The planner returns three itineraries per call, so covering a month
        would take roughly 700 requests. Only today and tomorrow are
        resolved.
        """
        install_api_mock(aioclient_mock)
        await setup_entry(hass, make_entry())

        events = await calendar_entity(hass).async_get_events(
            hass, CAPTURED_AT, CAPTURED_AT + timedelta(days=30)
        )
        # CalendarEvent.start is date | datetime; ours are always the
        # latter, since departures have a time.
        starts = [event.start for event in events]
        assert all(isinstance(start, datetime) for start in starts)
        dates = {start.date() for start in starts if isinstance(start, datetime)}
        assert len(dates) <= 2, f"horizon unexpectedly wide: {sorted(dates)}"

    async def test_a_past_range_is_empty_not_an_error(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        install_api_mock(aioclient_mock)
        await setup_entry(hass, make_entry())

        events = await calendar_entity(hass).async_get_events(
            hass, CAPTURED_AT - timedelta(days=30), CAPTURED_AT - timedelta(days=29)
        )
        assert events == []


async def test_unresolved_schedule_is_an_empty_calendar(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A planner failure must not take the entity down with it."""
    install_api_mock(
        aioclient_mock,
        {"TripPlannerSchedule": {"data": {"getTripPlannerSchedule": None}}},
    )
    await setup_entry(hass, make_entry())

    entity = calendar_entity(hass)
    assert entity.event is None
    assert (
        await entity.async_get_events(
            hass, CAPTURED_AT, CAPTURED_AT + timedelta(days=2)
        )
        == []
    )
