"""Setup, teardown, and coordinator sharing.

The reference counting here is the subtlest thing in the integration: two
commutes out of the same station must share one board poll, and unloading
either must not take the board away from the other. Both directions are
tested, because getting one wrong leaks and the other silently breaks the
surviving entry.
"""

from __future__ import annotations

from typing import Any

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.njtransit.const import (
    CONF_DEPARTURE_INTERVAL,
    CONF_DESTINATION,
    CONF_DESTINATION_ID,
    CONF_ORIGIN,
    CONF_ORIGIN_ID,
    DOMAIN,
    MIN_INTERVAL,
)
from custom_components.njtransit.coordinator import store_for

from .conftest import install_api_mock

SHORT_HILLS = "Short Hills Station"
NY_PENN = "New York Penn Station"
HOBOKEN = "Hoboken Terminal"


def make_entry(
    origin: str = SHORT_HILLS,
    origin_id: str = "RT",
    destination: str | None = NY_PENN,
    destination_id: str = "NY",
    options: dict[str, Any] | None = None,
) -> MockConfigEntry:
    """Return a config entry for one commute."""
    data: dict[str, Any] = {CONF_ORIGIN: origin, CONF_ORIGIN_ID: origin_id}
    if destination:
        data[CONF_DESTINATION] = destination
        data[CONF_DESTINATION_ID] = destination_id

    unique_id = f"{origin_id}-{destination_id}" if destination else origin_id
    return MockConfigEntry(
        domain=DOMAIN,
        title=f"{origin} to {destination}" if destination else origin,
        data=data,
        options=options or {},
        unique_id=unique_id,
    )


async def setup_entry(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Add and set up a config entry."""
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


class TestSetup:
    """Entry setup."""

    async def test_sets_up(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        install_api_mock(aioclient_mock)
        entry = make_entry()
        await setup_entry(hass, entry)

        assert entry.state is ConfigEntryState.LOADED
        assert entry.runtime_data.origin == SHORT_HILLS
        assert entry.runtime_data.destination == NY_PENN

    async def test_resolves_the_destination_train_set(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """The board filter comes from the planner, not the label."""
        install_api_mock(aioclient_mock)
        entry = make_entry()
        await setup_entry(hass, entry)

        route = entry.runtime_data.route.data
        assert route is not None
        assert route.train_ids

    async def test_entry_without_a_destination_skips_route_resolution(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        install_api_mock(aioclient_mock)
        called = install_api_mock(aioclient_mock)
        entry = make_entry(destination=None)
        await setup_entry(hass, entry)

        assert entry.state is ConfigEntryState.LOADED
        assert "TripPlannerSchedule" not in called

    async def test_unreachable_endpoint_retries_setup(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """A transport failure is retryable, so setup must not be final."""
        install_api_mock(aioclient_mock, {"SystemStatus": TimeoutError()})
        entry = make_entry()
        await setup_entry(hass, entry)

        assert entry.state is ConfigEntryState.SETUP_RETRY


class TestIntervals:
    """Poll cadence."""

    async def test_options_drive_the_interval(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        install_api_mock(aioclient_mock)
        entry = make_entry(options={CONF_DEPARTURE_INTERVAL: 300})
        await setup_entry(hass, entry)

        interval = entry.runtime_data.board.update_interval
        assert interval is not None
        assert interval.total_seconds() == 300

    async def test_interval_is_floored_at_the_vendor_cadence(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """Responses carry a 30s cache hint; polling faster gains nothing."""
        install_api_mock(aioclient_mock)
        entry = make_entry(options={CONF_DEPARTURE_INTERVAL: 1})
        await setup_entry(hass, entry)

        interval = entry.runtime_data.board.update_interval
        assert interval is not None
        assert interval.total_seconds() == MIN_INTERVAL


class TestCoordinatorSharing:
    """Reference counting across entries."""

    async def test_two_commutes_from_one_origin_share_a_board(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """Otherwise Short Hills gets polled twice for no benefit."""
        install_api_mock(aioclient_mock)
        to_ny = make_entry()
        to_hoboken = make_entry(destination=HOBOKEN, destination_id="HB")
        await setup_entry(hass, to_ny)
        await setup_entry(hass, to_hoboken)

        assert to_ny.runtime_data.board is to_hoboken.runtime_data.board

        store = store_for(hass)
        assert store is not None
        assert list(store.boards) == [SHORT_HILLS]

    async def test_different_origins_get_their_own_boards(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        install_api_mock(aioclient_mock)
        outbound = make_entry()
        inbound = make_entry(
            origin=NY_PENN,
            origin_id="NY",
            destination=SHORT_HILLS,
            destination_id="RT",
        )
        await setup_entry(hass, outbound)
        await setup_entry(hass, inbound)

        assert outbound.runtime_data.board is not inbound.runtime_data.board

        store = store_for(hass)
        assert store is not None
        assert set(store.boards) == {SHORT_HILLS, NY_PENN}

    async def test_status_feed_is_shared(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """The alert feed is system-wide, not per-station."""
        install_api_mock(aioclient_mock)
        first = make_entry()
        second = make_entry(
            origin=NY_PENN, origin_id="NY", destination=HOBOKEN, destination_id="HB"
        )
        await setup_entry(hass, first)
        await setup_entry(hass, second)

        assert first.runtime_data.status is second.runtime_data.status
        assert first.runtime_data.static is second.runtime_data.static

    async def test_unloading_one_keeps_the_shared_board_alive(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """The bug this whole mechanism exists to prevent."""
        install_api_mock(aioclient_mock)
        to_ny = make_entry()
        to_hoboken = make_entry(destination=HOBOKEN, destination_id="HB")
        await setup_entry(hass, to_ny)
        await setup_entry(hass, to_hoboken)

        await hass.config_entries.async_unload(to_ny.entry_id)
        await hass.async_block_till_done()

        store = store_for(hass)
        assert store is not None
        assert SHORT_HILLS in store.boards, "the surviving commute lost its board"
        assert to_hoboken.state is ConfigEntryState.LOADED

    async def test_unloading_the_last_entry_tears_everything_down(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """The other direction: nothing may be left polling."""
        install_api_mock(aioclient_mock)
        to_ny = make_entry()
        to_hoboken = make_entry(destination=HOBOKEN, destination_id="HB")
        await setup_entry(hass, to_ny)
        await setup_entry(hass, to_hoboken)

        await hass.config_entries.async_unload(to_ny.entry_id)
        await hass.config_entries.async_unload(to_hoboken.entry_id)
        await hass.async_block_till_done()

        assert store_for(hass) is None

    async def test_unload_and_set_up_again(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """A reload must not leave a half-released store behind."""
        install_api_mock(aioclient_mock)
        entry = make_entry()
        await setup_entry(hass, entry)

        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        assert entry.state is ConfigEntryState.LOADED
        store = store_for(hass)
        assert store is not None
        assert list(store.boards) == [SHORT_HILLS]


class TestRouteDegradation:
    """The destination filter fails soft."""

    async def test_planner_failure_does_not_block_setup(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """Losing the better filter beats losing the departures entirely."""
        install_api_mock(
            aioclient_mock,
            {"TripPlannerSchedule": {"data": {"getTripPlannerSchedule": None}}},
        )
        entry = make_entry()
        await setup_entry(hass, entry)

        assert entry.state is ConfigEntryState.LOADED
        route = entry.runtime_data.route.data
        assert route is not None
        assert route.complete is False
        assert route.train_ids == frozenset()


@pytest.mark.parametrize("destination", [NY_PENN, None])
async def test_unload_is_clean(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    destination: str | None,
) -> None:
    """Unloading succeeds whether or not a destination was configured."""
    install_api_mock(aioclient_mock)
    entry = make_entry(destination=destination)
    await setup_entry(hass, entry)

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED
