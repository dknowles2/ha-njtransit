"""Coordinator failure and degradation paths.

The happy paths are covered by test_init.py. These cover what happens when the
endpoint misbehaves, which for an undocumented API is not an edge case -- it
is the expected long-run behaviour, and it is where this integration either
degrades gracefully or falls over.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.njtransit.api.client import NJTransitClient
from custom_components.njtransit.api.parsing import TZ
from custom_components.njtransit.coordinator import (
    DepartureCoordinator,
    ProgressCoordinator,
    RouteCoordinator,
    StaticCoordinator,
    SystemStatusCoordinator,
    store_for,
)

from .conftest import install_api_mock
from .test_init import HOBOKEN, NY_PENN, SHORT_HILLS, make_entry, setup_entry

CAPTURED_AT = datetime(2026, 8, 3, 8, 20, tzinfo=TZ)

WAF_REJECTION = {"status": 400, "message": "Malformed request"}
SCHEMA_DRIFT = {"data": {"errors": [{"message": 'Cannot query field "whatever"'}]}}

# These tests drive _async_update_data directly, so the interval only has to
# be a valid timedelta; nothing schedules against it.
POLL = timedelta(seconds=60)


@pytest.fixture(name="at_capture_time", autouse=True)
def at_capture_time_fixture(freezer: FrozenDateTimeFactory) -> None:
    """Freeze the clock at the moment the fixtures were recorded."""
    freezer.move_to(CAPTURED_AT)


def client(hass: HomeAssistant) -> NJTransitClient:
    """Return a client using Home Assistant's shared session."""
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    return NJTransitClient(async_get_clientsession(hass))


class TestErrorTranslation:
    """Every coordinator turns an API failure into UpdateFailed.

    Letting a raw NJTransitError escape would mark the coordinator failed
    without the message reaching the log, so the distinction matters.
    """

    @pytest.mark.parametrize(
        ("override", "reason"),
        [
            (TimeoutError(), "unreachable"),
            (WAF_REJECTION, "WAF rejection"),
            (SCHEMA_DRIFT, "schema drift"),
        ],
        ids=["unreachable", "waf", "drift"],
    )
    async def test_status_coordinator(
        self,
        hass: HomeAssistant,
        aioclient_mock: AiohttpClientMocker,
        override: Any,
        reason: str,
    ) -> None:
        install_api_mock(aioclient_mock, {"SystemStatus": override})
        coordinator = SystemStatusCoordinator(hass, client(hass), "system status", POLL)

        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()

    @pytest.mark.parametrize(
        "override",
        [TimeoutError(), WAF_REJECTION, SCHEMA_DRIFT],
        ids=["unreachable", "waf", "drift"],
    )
    async def test_board_coordinator(
        self,
        hass: HomeAssistant,
        aioclient_mock: AiohttpClientMocker,
        override: Any,
    ) -> None:
        install_api_mock(aioclient_mock, {"TrainDepartureScreens": override})
        coordinator = DepartureCoordinator(hass, client(hass), SHORT_HILLS, POLL)

        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()

    @pytest.mark.parametrize(
        "override",
        [TimeoutError(), WAF_REJECTION, SCHEMA_DRIFT],
        ids=["unreachable", "waf", "drift"],
    )
    async def test_static_coordinator(
        self,
        hass: HomeAssistant,
        aioclient_mock: AiohttpClientMocker,
        override: Any,
    ) -> None:
        install_api_mock(aioclient_mock, {"TrainScheduleStationsRailForDV": override})
        coordinator = StaticCoordinator(hass, client(hass))

        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()


class TestRouteDegradation:
    """The route coordinator fails soft rather than raising.

    Everything else goes unavailable on failure. This one must not: losing the
    destination filter is survivable because `usable_departures` unions it with
    label matching, but losing the entity is not.
    """

    async def test_a_failure_is_reported_as_incomplete(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        install_api_mock(aioclient_mock, {"TripPlannerSchedule": TimeoutError()})
        coordinator = RouteCoordinator(hass, client(hass), SHORT_HILLS, NY_PENN)

        data = await coordinator._async_update_data()

        assert data.complete is False
        assert data.train_ids == frozenset()

    async def test_no_service_is_not_a_failure(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """An unserved pair and a typo produce the same generic error.

        The config flow validates names up front, so this is read as no
        service rather than propagated.
        """
        install_api_mock(
            aioclient_mock,
            {"TripPlannerSchedule": {"data": {"getTripPlannerSchedule": None}}},
        )
        coordinator = RouteCoordinator(hass, client(hass), SHORT_HILLS, NY_PENN)

        data = await coordinator._async_update_data()

        assert data.train_ids == frozenset()

    async def test_a_later_failure_keeps_the_previous_filter(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """Blanking the filter would widen every entity to the whole board.

        A stale filter is wrong in a small way; no filter is wrong in a large
        one.
        """
        install_api_mock(aioclient_mock)
        coordinator = RouteCoordinator(hass, client(hass), SHORT_HILLS, NY_PENN)
        first = await coordinator._async_update_data()
        assert first.train_ids
        coordinator.async_set_updated_data(first)

        aioclient_mock.clear_requests()
        install_api_mock(aioclient_mock, {"TripPlannerSchedule": TimeoutError()})
        second = await coordinator._async_update_data()

        assert second.train_ids == first.train_ids
        assert second.complete is False


class TestBoardSharing:
    """Reference counting edge cases beyond the two in test_init."""

    async def test_releasing_an_unknown_station_is_a_no_op(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """Unload paths must not depend on having claimed anything."""
        install_api_mock(aioclient_mock)
        entry = make_entry()
        await setup_entry(hass, entry)

        store = store_for(hass)
        assert store is not None

        await store.release_board("Nowhere", entry.entry_id)
        assert SHORT_HILLS in store.boards

    async def test_releasing_twice_is_harmless(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        install_api_mock(aioclient_mock)
        first = make_entry()
        second = make_entry(destination=HOBOKEN, destination_id="HB")
        await setup_entry(hass, first)
        await setup_entry(hass, second)

        store = store_for(hass)
        assert store is not None

        await store.release_board(SHORT_HILLS, first.entry_id)
        await store.release_board(SHORT_HILLS, first.entry_id)

        assert SHORT_HILLS in store.boards, "the second commute lost its board"


class TestProgressCoordinator:
    """Following one train, and knowing when not to."""

    async def test_returns_none_without_a_train_to_follow(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """No request at all -- that is what keeps this affordable.

        The stop list cannot be batched, so following every train would be a
        request each. Skipping when nothing is close is the whole reason
        per-train tracking is viable here.
        """
        mocker = AiohttpClientMocker()
        called = install_api_mock(mocker)
        session = mocker.create_session(asyncio.get_running_loop())
        try:
            coordinator = ProgressCoordinator(
                hass, NJTransitClient(session), lambda: None, timedelta(minutes=1)
            )
            await coordinator.async_refresh()
            assert coordinator.data is None
            assert "TrainStopList" not in called
        finally:
            await session.close()

    async def test_a_train_not_running_today_is_not_a_failure(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """Normal on a weekend, or after a timetable change."""
        mocker = AiohttpClientMocker()
        install_api_mock(
            mocker, {"TrainStopList": {"data": {"getTrainStopList": None}}}
        )
        session = mocker.create_session(asyncio.get_running_loop())
        try:
            coordinator = ProgressCoordinator(
                hass, NJTransitClient(session), lambda: "9999", timedelta(minutes=1)
            )
            await coordinator.async_refresh()
            assert coordinator.last_update_success is True
            assert coordinator.data is None
        finally:
            await session.close()
