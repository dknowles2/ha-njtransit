"""The RailData source, end to end: config flow, setup, entities, reauth."""

from __future__ import annotations

from collections.abc import Generator
from datetime import datetime
from typing import Any
from unittest.mock import patch

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.njtransit.api.parsing import TZ
from custom_components.njtransit.api.raildata import RailDataClient
from custom_components.njtransit.const import (
    CONF_DESTINATION,
    CONF_DESTINATION_ID,
    CONF_ORIGIN,
    CONF_ORIGIN_ID,
    CONF_SOURCE,
    DOMAIN,
    SOURCE_RAILDATA,
    SOURCE_WEBSITE,
)
from custom_components.njtransit.coordinator import store_for
from custom_components.njtransit.diagnostics import (
    async_get_config_entry_diagnostics,
)

from .conftest import install_api_mock, install_raildata_mock
from .test_config_flow import start_flow, start_menu
from .test_init import NY_PENN, SHORT_HILLS, setup_entry

# When the RailData fixtures were recorded.
RECORDED_AT = datetime(2026, 9, 14, 21, 27, tzinfo=TZ)

CREDENTIALS = {CONF_USERNAME: "someone", CONF_PASSWORD: "secret"}


@pytest.fixture(autouse=True)
def frozen_clock() -> Generator[None]:
    """Pin the clock to the recording so the schedule is "today"."""
    with (
        patch(
            "custom_components.njtransit.api.raildata.now_local",
            return_value=RECORDED_AT,
        ),
        patch("custom_components.njtransit.now_local", return_value=RECORDED_AT),
        patch(
            "custom_components.njtransit.coordinator.now_local",
            return_value=RECORDED_AT,
        ),
    ):
        yield


def make_raildata_entry(
    origin: str = "Short Hills",
    origin_id: str = "RT",
    destination: str | None = "New York Penn Station",
    destination_id: str = "NY",
    username: str = "someone",
    options: dict[str, Any] | None = None,
) -> MockConfigEntry:
    """Return a config entry for one commute on the RailData source."""
    data: dict[str, Any] = {
        CONF_ORIGIN: origin,
        CONF_ORIGIN_ID: origin_id,
        CONF_SOURCE: SOURCE_RAILDATA,
        CONF_USERNAME: username,
        CONF_PASSWORD: "secret",
    }
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


class TestConfigFlow:
    """Choosing RailData at setup."""

    async def test_the_first_step_is_the_source_menu(self, hass: HomeAssistant) -> None:
        result = await start_menu(hass)
        assert result["type"] is FlowResultType.MENU
        assert result["menu_options"] == [SOURCE_WEBSITE, SOURCE_RAILDATA]

    async def test_creates_an_entry_with_credentials(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        install_api_mock(aioclient_mock)
        install_raildata_mock(aioclient_mock)

        result = await start_flow(hass, SOURCE_RAILDATA)
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "raildata"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], CREDENTIALS
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "commute"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_ORIGIN: "Short Hills", CONF_DESTINATION: "New York Penn Station"},
        )
        await hass.async_block_till_done()

        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert result["data"] == {
            CONF_ORIGIN: "Short Hills",
            CONF_ORIGIN_ID: "RT",
            CONF_DESTINATION: "New York Penn Station",
            CONF_DESTINATION_ID: "NY",
            CONF_SOURCE: SOURCE_RAILDATA,
            **CREDENTIALS,
        }

    async def test_the_flow_signs_in_once(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """Ten a day; checking the password, listing stations and validating
        the origin must all ride on one token."""
        install_api_mock(aioclient_mock)
        called = install_raildata_mock(aioclient_mock)
        result = await start_flow(hass, SOURCE_RAILDATA)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], CREDENTIALS
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_ORIGIN: "Short Hills"}
        )
        await hass.async_block_till_done()

        assert result["type"] is FlowResultType.CREATE_ENTRY
        # The flow's token is stored, and the entry it created picks it up.
        assert sum(1 for c in called if c["method"] == "getToken") == 1

    async def test_the_station_picker_is_raildatas_own(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """Its list has no alias rows and its own spelling of the names."""
        install_api_mock(aioclient_mock)
        install_raildata_mock(aioclient_mock)
        result = await start_flow(hass, SOURCE_RAILDATA)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], CREDENTIALS
        )

        options = _origin_options(result)
        assert "Short Hills" in options
        assert "Short Hills Station" not in options
        assert len(options) == 173

    async def test_the_suggestion_still_comes_from_home(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """The proximity lookup is the website's, and the codes line up."""
        hass.config.latitude = 40.7252
        hass.config.longitude = -74.3238
        install_api_mock(aioclient_mock)
        install_raildata_mock(aioclient_mock)
        result = await start_flow(hass, SOURCE_RAILDATA)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], CREDENTIALS
        )

        from .test_config_flow import suggested_origin

        assert suggested_origin(result) == "Short Hills"

    @pytest.mark.parametrize(
        ("override", "error"),
        [
            ({"getToken": {"Authenticated": "False", "UserToken": ""}}, "invalid_auth"),
            ({"getToken": {"errorMessage": "Daily usage limit:10."}}, "quota"),
            ({"getToken": TimeoutError()}, "cannot_connect"),
            ({"getToken": {"errorMessage": "Teapot"}}, "unknown"),
        ],
    )
    async def test_bad_credentials_stay_on_the_form(
        self,
        hass: HomeAssistant,
        aioclient_mock: AiohttpClientMocker,
        override: dict[str, Any],
        error: str,
    ) -> None:
        install_raildata_mock(aioclient_mock, override)
        result = await start_flow(hass, SOURCE_RAILDATA)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], CREDENTIALS
        )

        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "raildata"
        assert result["errors"] == {"base": error}

    async def test_the_same_commute_on_either_source_is_one_commute(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """Switching source is a reconfigure, not a second entry."""
        install_api_mock(aioclient_mock)
        install_raildata_mock(aioclient_mock)
        await setup_entry(hass, make_raildata_entry())

        result = await start_flow(hass, SOURCE_WEBSITE)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_ORIGIN: SHORT_HILLS, CONF_DESTINATION: NY_PENN}
        )

        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "already_configured"


class TestSetup:
    """An entry on the RailData source."""

    async def test_sets_up_and_polls_raildata(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        install_api_mock(aioclient_mock)
        called = install_raildata_mock(aioclient_mock)
        entry = make_raildata_entry()
        await setup_entry(hass, entry)

        assert entry.state is ConfigEntryState.LOADED
        methods = {call["method"] for call in called}
        assert {"getToken", "getStationList", "getTrainSchedule19Rec"} <= methods
        board = entry.runtime_data.board.data
        assert board is not None
        assert board.departures[0].train_id == "6671"

    async def test_the_route_comes_from_the_station_schedules(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        install_api_mock(aioclient_mock)
        install_raildata_mock(aioclient_mock)
        entry = make_raildata_entry()
        await setup_entry(hass, entry)

        route = entry.runtime_data.route.data
        assert route is not None
        assert route.complete
        assert len(route.trips) == 23
        assert "6602" in route.train_ids

    async def test_the_website_titles_still_resolve(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """An entry made on the website and switched over keeps its names."""
        install_api_mock(aioclient_mock)
        called = install_raildata_mock(aioclient_mock)
        entry = make_raildata_entry(origin=SHORT_HILLS, destination=NY_PENN)
        await setup_entry(hass, entry)

        assert entry.state is ConfigEntryState.LOADED
        asked = [c for c in called if c["method"] == "getTrainSchedule19Rec"]
        assert asked[0]["station"] == "RT"

    async def test_the_signalled_track_reaches_the_sensor(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """New York Penn, train 3889: the board says 3 and so does the circuit."""
        install_api_mock(aioclient_mock)
        install_raildata_mock(aioclient_mock)
        entry = make_raildata_entry(
            origin="New York Penn Station",
            origin_id="NY",
            destination="Trenton",
            destination_id="TR",
        )
        await setup_entry(hass, entry)

        state = hass.states.get(
            "sensor.new_york_penn_station_to_trenton_next_departure"
        )
        assert state is not None
        assert state.attributes["train_id"] == "3889"
        assert state.attributes["track"] == "3"
        assert state.attributes["signalled_track"] == "3"
        assert state.attributes["track_source"] == "board"

    async def test_two_entries_on_one_account_share_a_client(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """One token, one station list, one schedule fetch per station-day."""
        install_api_mock(aioclient_mock)
        called = install_raildata_mock(aioclient_mock)
        outbound = make_raildata_entry()
        inbound = make_raildata_entry(
            origin="New York Penn Station",
            origin_id="NY",
            destination="Short Hills",
            destination_id="RT",
        )
        await setup_entry(hass, outbound)
        await setup_entry(hass, inbound)

        assert outbound.runtime_data.client is inbound.runtime_data.client
        assert sum(1 for c in called if c["method"] == "getToken") == 1
        assert sum(1 for c in called if c["method"] == "getStationSchedule") == 2

    async def test_sources_do_not_share_a_store(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        install_api_mock(aioclient_mock)
        install_raildata_mock(aioclient_mock)
        website = MockConfigEntry(
            domain=DOMAIN,
            title="Short Hills Station to Hoboken Terminal",
            data={
                CONF_ORIGIN: SHORT_HILLS,
                CONF_ORIGIN_ID: "RT",
                CONF_DESTINATION: "Hoboken Terminal",
                CONF_DESTINATION_ID: "HB",
            },
            unique_id="RT-HB",
        )
        raildata = make_raildata_entry()
        await setup_entry(hass, website)
        await setup_entry(hass, raildata)

        assert website.runtime_data.board is not raildata.runtime_data.board
        assert store_for(hass, SOURCE_WEBSITE) is not None
        assert store_for(hass, "raildata:someone") is not None
        assert isinstance(raildata.runtime_data.client, RailDataClient)

        # One track history between them: its storage key is not per source,
        # and two writers would each discard the other's stations.
        assert website.runtime_data.history is raildata.runtime_data.history

        assert await hass.config_entries.async_unload(raildata.entry_id)
        assert store_for(hass, "raildata:someone") is None
        assert store_for(hass, SOURCE_WEBSITE) is not None
        remaining = store_for(hass, SOURCE_WEBSITE)
        assert remaining is not None
        assert website.runtime_data.history is remaining.history

    async def test_bad_credentials_ask_for_reauth(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        install_api_mock(aioclient_mock)
        install_raildata_mock(aioclient_mock, authenticated=False)
        entry = make_raildata_entry()
        await setup_entry(hass, entry)

        assert entry.state is ConfigEntryState.SETUP_ERROR
        flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
        assert [flow["context"]["source"] for flow in flows] == ["reauth"]

    async def test_credentials_revoked_later_ask_for_reauth(
        self,
        hass: HomeAssistant,
        aioclient_mock: AiohttpClientMocker,
        freezer: FrozenDateTimeFactory,
    ) -> None:
        """Mid-life, the API rejects the token and then the password."""
        install_api_mock(aioclient_mock)
        install_raildata_mock(aioclient_mock)
        entry = make_raildata_entry()
        await setup_entry(hass, entry)
        assert entry.state is ConfigEntryState.LOADED

        aioclient_mock.clear_requests()
        install_api_mock(aioclient_mock)
        install_raildata_mock(
            aioclient_mock,
            {"getTrainSchedule19Rec": {"errorMessage": "Invalid token."}},
            authenticated=False,
        )
        freezer.tick(120)
        async_fire_time_changed(hass)
        await hass.async_block_till_done()

        flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
        assert [flow["context"]["source"] for flow in flows] == ["reauth"]

    async def test_an_unreachable_api_retries(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        install_api_mock(aioclient_mock)
        install_raildata_mock(aioclient_mock, {"getToken": TimeoutError()})
        entry = make_raildata_entry()
        await setup_entry(hass, entry)

        assert entry.state is ConfigEntryState.SETUP_RETRY

    async def test_diagnostics_name_the_source_and_not_the_password(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        install_api_mock(aioclient_mock)
        install_raildata_mock(aioclient_mock)
        entry = make_raildata_entry()
        await setup_entry(hass, entry)

        diagnostics = await async_get_config_entry_diagnostics(hass, entry)

        assert diagnostics["config"]["source"] == SOURCE_RAILDATA
        assert "secret" not in str(diagnostics)


class TestReauth:
    """New credentials for an existing entry."""

    async def test_accepts_new_credentials_and_reloads(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        install_api_mock(aioclient_mock)
        install_raildata_mock(aioclient_mock, authenticated=False)
        entry = make_raildata_entry()
        await setup_entry(hass, entry)
        flow = hass.config_entries.flow.async_progress_by_handler(DOMAIN)[0]

        # The account is fixed on NJ Transit's side.
        aioclient_mock.clear_requests()
        install_api_mock(aioclient_mock)
        install_raildata_mock(aioclient_mock)

        result = await hass.config_entries.flow.async_configure(
            flow["flow_id"], {CONF_USERNAME: "someone", CONF_PASSWORD: "better"}
        )
        await hass.async_block_till_done()

        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "reauth_successful"
        assert entry.data[CONF_PASSWORD] == "better"
        assert entry.state is ConfigEntryState.LOADED

    async def test_a_new_password_is_actually_checked(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """A token already in the store must not vouch for a password it
        was not issued against."""
        install_api_mock(aioclient_mock)
        install_raildata_mock(aioclient_mock)
        entry = make_raildata_entry()
        await setup_entry(hass, entry)
        assert entry.state is ConfigEntryState.LOADED

        aioclient_mock.clear_requests()
        install_api_mock(aioclient_mock)
        called = install_raildata_mock(aioclient_mock, authenticated=False)
        result = await entry.start_reauth_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_USERNAME: "someone", CONF_PASSWORD: "typo"}
        )

        assert result["errors"] == {"base": "invalid_auth"}
        assert sum(1 for c in called if c["method"] == "getToken") == 1

    async def test_rejected_credentials_stay_on_the_form(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        install_api_mock(aioclient_mock)
        install_raildata_mock(aioclient_mock, authenticated=False)
        entry = make_raildata_entry()
        await setup_entry(hass, entry)
        flow = hass.config_entries.flow.async_progress_by_handler(DOMAIN)[0]

        result = await hass.config_entries.flow.async_configure(
            flow["flow_id"], {CONF_USERNAME: "someone", CONF_PASSWORD: "still wrong"}
        )

        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "reauth_confirm"
        assert result["errors"] == {"base": "invalid_auth"}


class TestReconfigure:
    """Moving a commute between sources."""

    async def test_website_to_raildata(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        install_api_mock(aioclient_mock)
        install_raildata_mock(aioclient_mock)
        entry = MockConfigEntry(
            domain=DOMAIN,
            title=f"{SHORT_HILLS} to {NY_PENN}",
            data={
                CONF_ORIGIN: SHORT_HILLS,
                CONF_ORIGIN_ID: "RT",
                CONF_DESTINATION: NY_PENN,
                CONF_DESTINATION_ID: "NY",
            },
            unique_id="RT-NY",
        )
        await setup_entry(hass, entry)

        result = await entry.start_reconfigure_flow(hass)
        assert result["type"] is FlowResultType.MENU
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": SOURCE_RAILDATA}
        )
        assert result["step_id"] == "raildata"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], CREDENTIALS
        )
        await hass.async_block_till_done()

        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "reconfigure_successful"
        assert entry.data[CONF_SOURCE] == SOURCE_RAILDATA
        assert entry.data[CONF_USERNAME] == "someone"
        # Titles are re-read in the new source's spelling; codes are kept.
        assert entry.data[CONF_ORIGIN] == "Short Hills"
        assert entry.data[CONF_ORIGIN_ID] == "RT"
        assert entry.data[CONF_DESTINATION] == "New York Penn Station"
        assert entry.state is ConfigEntryState.LOADED
        assert isinstance(entry.runtime_data.client, RailDataClient)

    async def test_raildata_to_website_drops_the_credentials(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        install_api_mock(aioclient_mock)
        install_raildata_mock(aioclient_mock)
        entry = make_raildata_entry()
        await setup_entry(hass, entry)

        result = await entry.start_reconfigure_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": SOURCE_WEBSITE}
        )
        await hass.async_block_till_done()

        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "reconfigure_successful"
        assert entry.data[CONF_SOURCE] == SOURCE_WEBSITE
        assert CONF_USERNAME not in entry.data
        assert CONF_PASSWORD not in entry.data
        # The website's own spelling, which its planner insists on.
        assert entry.data[CONF_ORIGIN] == SHORT_HILLS
        assert entry.data[CONF_DESTINATION] == NY_PENN
        assert entry.state is ConfigEntryState.LOADED
        assert not isinstance(entry.runtime_data.client, RailDataClient)

    async def test_an_unreachable_station_list_aborts(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        install_api_mock(aioclient_mock)
        install_raildata_mock(aioclient_mock)
        entry = make_raildata_entry()
        await setup_entry(hass, entry)
        aioclient_mock.clear_requests()
        install_api_mock(
            aioclient_mock, {"TrainScheduleStationsRailForDV": TimeoutError()}
        )

        result = await entry.start_reconfigure_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": SOURCE_WEBSITE}
        )

        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "cannot_connect"


def _origin_options(result: Any) -> list[str]:
    """Return the origin picker's option values."""
    for key, selector in result["data_schema"].schema.items():
        if key == CONF_ORIGIN:
            return [option["value"] for option in selector.config["options"]]
    raise AssertionError("no origin field")
