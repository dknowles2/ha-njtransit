"""The RailData source, end to end: config flow, setup, entities, reauth.

A commute on this source does not hold its own credentials -- it references
a separate *account* entry (`account.py`), so `make_raildata_entry` below
always takes one. `setup_raildata_commute` is the one-call shortcut most
tests want: it sets up an account and a commute referencing it and hands
back both.
"""

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

from custom_components.njtransit.account import account_unique_id
from custom_components.njtransit.api.parsing import TZ
from custom_components.njtransit.api.raildata import RailDataClient
from custom_components.njtransit.config_flow import _ADD_ACCOUNT
from custom_components.njtransit.const import (
    CONF_ACCOUNT,
    CONF_DESTINATION,
    CONF_DESTINATION_ID,
    CONF_ENTRY_TYPE,
    CONF_ORIGIN,
    CONF_ORIGIN_ID,
    CONF_SOURCE,
    DOMAIN,
    ENTRY_TYPE_ACCOUNT,
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


def make_account_entry(
    username: str = "someone", password: str = "secret"
) -> MockConfigEntry:
    """Return a RailData account entry, not yet added to `hass`."""
    return MockConfigEntry(
        domain=DOMAIN,
        title=f"RailData ({username})",
        data={
            CONF_ENTRY_TYPE: ENTRY_TYPE_ACCOUNT,
            CONF_USERNAME: username,
            CONF_PASSWORD: password,
        },
        unique_id=account_unique_id(username),
    )


def make_raildata_entry(
    account_entry_id: str,
    origin: str = "Short Hills",
    origin_id: str = "RT",
    destination: str | None = "New York Penn Station",
    destination_id: str = "NY",
    options: dict[str, Any] | None = None,
) -> MockConfigEntry:
    """Return a config entry for one commute on the RailData source."""
    data: dict[str, Any] = {
        CONF_ORIGIN: origin,
        CONF_ORIGIN_ID: origin_id,
        CONF_SOURCE: SOURCE_RAILDATA,
        CONF_ACCOUNT: account_entry_id,
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


async def setup_account(
    hass: HomeAssistant, username: str = "someone", password: str = "secret"
) -> MockConfigEntry:
    """Add and set up a RailData account entry."""
    account = make_account_entry(username, password)
    account.add_to_hass(hass)
    await hass.config_entries.async_setup(account.entry_id)
    await hass.async_block_till_done()
    return account


async def setup_raildata_commute(
    hass: HomeAssistant, *, username: str = "someone", **kwargs: Any
) -> tuple[MockConfigEntry, MockConfigEntry]:
    """Set up a RailData account and one commute entry referencing it.

    :return: The commute entry, then the account entry.
    """
    account = await setup_account(hass, username)
    commute = make_raildata_entry(account.entry_id, **kwargs)
    await setup_entry(hass, commute)
    return commute, account


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
        # No account exists yet: straight to credentials, nothing to choose.
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "raildata_account"

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

        accounts = [
            entry
            for entry in hass.config_entries.async_entries(DOMAIN)
            if entry.data.get(CONF_ENTRY_TYPE) == ENTRY_TYPE_ACCOUNT
        ]
        assert len(accounts) == 1
        account = accounts[0]
        assert account.data[CONF_USERNAME] == "someone"
        assert account.data[CONF_PASSWORD] == "secret"

        assert result["data"] == {
            CONF_ORIGIN: "Short Hills",
            CONF_ORIGIN_ID: "RT",
            CONF_DESTINATION: "New York Penn Station",
            CONF_DESTINATION_ID: "NY",
            CONF_SOURCE: SOURCE_RAILDATA,
            CONF_ACCOUNT: account.entry_id,
        }

    async def test_the_flow_signs_in_once(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """Ten a day; checking the password, listing stations, validating
        the origin, and setting up the new account entry must all ride on
        one token."""
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
        # The flow's token is stored, and the account entry it creates --
        # and the commute entry riding on that account -- pick it up.
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
        assert result["step_id"] == "raildata_account"
        assert result["errors"] == {"base": error}

    async def test_the_same_commute_on_either_source_is_one_commute(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """Switching source is a reconfigure, not a second entry."""
        install_api_mock(aioclient_mock)
        install_raildata_mock(aioclient_mock)
        await setup_raildata_commute(hass)

        result = await start_flow(hass, SOURCE_WEBSITE)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_ORIGIN: SHORT_HILLS, CONF_DESTINATION: NY_PENN}
        )

        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "already_configured"


class TestAccountSelection:
    """Choosing between RailData accounts already set up."""

    async def test_an_existing_account_is_offered_and_skips_a_sign_in(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        install_api_mock(aioclient_mock)
        called = install_raildata_mock(aioclient_mock)
        _first, account = await setup_raildata_commute(hass)

        result = await start_flow(hass, SOURCE_RAILDATA)
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "raildata"
        values = [option["value"] for option in _account_options(result)]
        assert account.entry_id in values
        assert _ADD_ACCOUNT in values

        called.clear()
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_ACCOUNT: account.entry_id}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_ORIGIN: "New York Penn Station", CONF_DESTINATION: "Hoboken"},
        )
        await hass.async_block_till_done()

        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert result["data"][CONF_ACCOUNT] == account.entry_id
        # The account was already loaded and already authenticated; choosing
        # it must not spend another of the day's ten sign-ins.
        assert sum(1 for c in called if c["method"] == "getToken") == 0

    async def test_choosing_an_account_that_is_not_currently_loaded(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """Picking an account that is not loaded right now -- unloaded, or
        mid-retry -- still works: a fresh client is built from its stored
        credentials rather than reusing a live one that does not exist."""
        install_api_mock(aioclient_mock)
        called = install_raildata_mock(aioclient_mock)
        _commute, account = await setup_raildata_commute(hass)
        assert await hass.config_entries.async_unload(account.entry_id)
        assert account.state is ConfigEntryState.NOT_LOADED

        called.clear()
        result = await start_flow(hass, SOURCE_RAILDATA)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_ACCOUNT: account.entry_id}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_ORIGIN: "New York Penn Station", CONF_DESTINATION: "Short Hills"},
        )
        await hass.async_block_till_done()

        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert result["data"][CONF_ACCOUNT] == account.entry_id
        # A token this account's storage already held is reused; no new
        # sign-in is spent building a fresh client for it.
        assert sum(1 for c in called if c["method"] == "getToken") == 0

    async def test_adding_a_new_account_when_one_exists(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        install_api_mock(aioclient_mock)
        install_raildata_mock(aioclient_mock)
        await setup_raildata_commute(hass, username="someone")

        result = await start_flow(hass, SOURCE_RAILDATA)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_ACCOUNT: _ADD_ACCOUNT}
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "raildata_account"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_USERNAME: "someone-else", CONF_PASSWORD: "secret"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_ORIGIN: "Short Hills"}
        )
        await hass.async_block_till_done()

        assert result["type"] is FlowResultType.CREATE_ENTRY
        accounts = [
            entry
            for entry in hass.config_entries.async_entries(DOMAIN)
            if entry.data.get(CONF_ENTRY_TYPE) == ENTRY_TYPE_ACCOUNT
        ]
        assert {account.data[CONF_USERNAME] for account in accounts} == {
            "someone",
            "someone-else",
        }

    async def test_retyping_a_known_username_reuses_the_account(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """ "Add a new account" for a username already known is a reuse, not
        a second sign-in for the same account."""
        install_api_mock(aioclient_mock)
        called = install_raildata_mock(aioclient_mock)
        _first, account = await setup_raildata_commute(hass)

        result = await start_flow(hass, SOURCE_RAILDATA)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_ACCOUNT: _ADD_ACCOUNT}
        )
        called.clear()
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_USERNAME: "someone", CONF_PASSWORD: "wrong-typed"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_ORIGIN: "New York Penn Station", CONF_DESTINATION: "Hoboken"},
        )
        await hass.async_block_till_done()

        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert result["data"][CONF_ACCOUNT] == account.entry_id
        assert sum(1 for c in called if c["method"] == "getToken") == 0
        accounts = [
            entry
            for entry in hass.config_entries.async_entries(DOMAIN)
            if entry.data.get(CONF_ENTRY_TYPE) == ENTRY_TYPE_ACCOUNT
        ]
        assert len(accounts) == 1


class TestSetup:
    """An entry on the RailData source."""

    async def test_sets_up_and_polls_raildata(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        install_api_mock(aioclient_mock)
        called = install_raildata_mock(aioclient_mock)
        entry, _account = await setup_raildata_commute(hass)

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
        entry, _account = await setup_raildata_commute(hass)

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
        entry, _account = await setup_raildata_commute(
            hass, origin=SHORT_HILLS, destination=NY_PENN
        )

        assert entry.state is ConfigEntryState.LOADED
        asked = [c for c in called if c["method"] == "getTrainSchedule19Rec"]
        assert asked[0]["station"] == "RT"

    async def test_the_signaled_track_reaches_the_sensor(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """New York Penn, train 3889: the board says 3 and so does the circuit."""
        install_api_mock(aioclient_mock)
        install_raildata_mock(aioclient_mock)
        await setup_raildata_commute(
            hass,
            origin="New York Penn Station",
            origin_id="NY",
            destination="Trenton",
            destination_id="TR",
        )

        state = hass.states.get(
            "sensor.new_york_penn_station_to_trenton_next_departure"
        )
        assert state is not None
        assert state.attributes["train_id"] == "3889"
        assert state.attributes["track"] == "3"
        assert state.attributes["signaled_track"] == "3"
        assert state.attributes["track_source"] == "board"

    async def test_two_entries_on_one_account_share_a_client(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """One token, one station list, one schedule fetch per station-day."""
        install_api_mock(aioclient_mock)
        called = install_raildata_mock(aioclient_mock)
        account = await setup_account(hass)
        outbound = make_raildata_entry(account.entry_id)
        inbound = make_raildata_entry(
            account.entry_id,
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
        await setup_entry(hass, website)
        commute, account = await setup_raildata_commute(hass)

        assert website.runtime_data.board is not commute.runtime_data.board
        assert store_for(hass, SOURCE_WEBSITE) is not None
        assert store_for(hass, "raildata:someone") is not None
        assert isinstance(commute.runtime_data.client, RailDataClient)

        # One track history between them: its storage key is not per source,
        # and two writers would each discard the other's stations.
        assert website.runtime_data.history is commute.runtime_data.history

        # Unloading the commute alone must not take the store from the
        # account entry, which still claims it.
        assert await hass.config_entries.async_unload(commute.entry_id)
        assert store_for(hass, "raildata:someone") is not None

        assert await hass.config_entries.async_unload(account.entry_id)
        assert store_for(hass, "raildata:someone") is None
        assert store_for(hass, SOURCE_WEBSITE) is not None
        remaining = store_for(hass, SOURCE_WEBSITE)
        assert remaining is not None
        assert website.runtime_data.history is remaining.history

    async def test_a_commute_waits_for_its_account(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """An account that failed to load leaves its commutes retrying
        rather than crashing or building a credential-less store of their
        own."""
        install_api_mock(aioclient_mock)
        install_raildata_mock(aioclient_mock, authenticated=False)
        account = await setup_account(hass)
        assert account.state is ConfigEntryState.SETUP_ERROR

        commute = make_raildata_entry(account.entry_id)
        await setup_entry(hass, commute)

        assert commute.state is ConfigEntryState.SETUP_RETRY
        assert store_for(hass, "raildata:someone") is None

    async def test_bad_credentials_ask_for_reauth(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        install_api_mock(aioclient_mock)
        install_raildata_mock(aioclient_mock, authenticated=False)
        account = await setup_account(hass)

        assert account.state is ConfigEntryState.SETUP_ERROR
        flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
        assert [flow["context"]["source"] for flow in flows] == ["reauth"]
        assert flows[0]["context"]["entry_id"] == account.entry_id

    async def test_credentials_revoked_later_ask_for_reauth(
        self,
        hass: HomeAssistant,
        aioclient_mock: AiohttpClientMocker,
        freezer: FrozenDateTimeFactory,
    ) -> None:
        """Mid-life, the API rejects the token and then the password."""
        install_api_mock(aioclient_mock)
        install_raildata_mock(aioclient_mock)
        _commute, account = await setup_raildata_commute(hass)
        assert account.state is ConfigEntryState.LOADED

        aioclient_mock.clear_requests()
        install_api_mock(aioclient_mock)
        install_raildata_mock(
            aioclient_mock,
            {"getTrainSchedule19Rec": {"errorMessage": "Invalid token."}},
            authenticated=False,
        )
        # One board poll, and only that: the status coordinator polls every
        # two minutes and would raise the same thing, which is fine in life
        # and useless here, where the point is that the *board* path does.
        freezer.tick(61)
        async_fire_time_changed(hass)
        await hass.async_block_till_done()

        flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
        assert [flow["context"]["source"] for flow in flows] == ["reauth"]
        assert flows[0]["context"]["entry_id"] == account.entry_id

    async def test_a_rejected_credential_asks_the_account_once(
        self,
        hass: HomeAssistant,
        aioclient_mock: AiohttpClientMocker,
        freezer: FrozenDateTimeFactory,
    ) -> None:
        """The board is shared and bound to no entry, so a rejected
        credential is reported to the account entry alone -- not fanned out
        to every commute claiming the store, which would open a reauth flow
        per commute for what is one broken password."""
        install_api_mock(aioclient_mock)
        install_raildata_mock(aioclient_mock)
        account = await setup_account(hass)
        outbound = make_raildata_entry(account.entry_id)
        inbound = make_raildata_entry(
            account.entry_id,
            origin="New York Penn Station",
            origin_id="NY",
            destination="Short Hills",
            destination_id="RT",
        )
        await setup_entry(hass, outbound)
        await setup_entry(hass, inbound)

        aioclient_mock.clear_requests()
        install_api_mock(aioclient_mock)
        install_raildata_mock(
            aioclient_mock,
            {"getStationMSG": {"errorMessage": "Invalid token."}},
            authenticated=False,
        )
        freezer.tick(121)
        async_fire_time_changed(hass)
        await hass.async_block_till_done()

        flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
        assert len(flows) == 1
        assert flows[0]["context"]["entry_id"] == account.entry_id
        assert flows[0]["context"]["source"] == "reauth"

    async def test_an_unreachable_api_retries(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        install_api_mock(aioclient_mock)
        install_raildata_mock(aioclient_mock, {"getToken": TimeoutError()})
        account = await setup_account(hass)

        assert account.state is ConfigEntryState.SETUP_RETRY

    async def test_diagnostics_name_the_source_and_not_the_password(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        install_api_mock(aioclient_mock)
        install_raildata_mock(aioclient_mock)
        entry, account = await setup_raildata_commute(hass)

        diagnostics = await async_get_config_entry_diagnostics(hass, entry)
        assert diagnostics["config"]["source"] == SOURCE_RAILDATA
        assert "secret" not in str(diagnostics)

        account_diagnostics = await async_get_config_entry_diagnostics(hass, account)
        assert "secret" not in str(account_diagnostics)
        assert "someone" not in str(account_diagnostics)
        assert account_diagnostics["commutes_using_this_account"] == 1


class TestReauth:
    """New credentials for a RailData account."""

    async def test_reauth_rebuilds_the_store_even_while_commutes_use_it(
        self,
        hass: HomeAssistant,
        aioclient_mock: AiohttpClientMocker,
        freezer: FrozenDateTimeFactory,
    ) -> None:
        """A commute's claim on the store must never keep the old client --
        built from the password reauth just replaced -- alive underneath
        it. If it did, the corrected password would never actually reach
        anything."""
        install_api_mock(aioclient_mock)
        install_raildata_mock(aioclient_mock)
        account = await setup_account(hass)
        outbound = make_raildata_entry(account.entry_id)
        inbound = make_raildata_entry(
            account.entry_id,
            origin="New York Penn Station",
            origin_id="NY",
            destination="Short Hills",
            destination_id="RT",
        )
        await setup_entry(hass, outbound)
        await setup_entry(hass, inbound)
        old_client = store_for(hass, "raildata:someone").client  # type: ignore[union-attr]

        aioclient_mock.clear_requests()
        install_api_mock(aioclient_mock)
        install_raildata_mock(
            aioclient_mock,
            {"getStationMSG": {"errorMessage": "Invalid token."}},
            authenticated=False,
        )
        freezer.tick(121)
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
        flow = hass.config_entries.flow.async_progress_by_handler(DOMAIN)[0]

        aioclient_mock.clear_requests()
        install_api_mock(aioclient_mock)
        install_raildata_mock(aioclient_mock)
        result = await hass.config_entries.flow.async_configure(
            flow["flow_id"], {CONF_USERNAME: "someone", CONF_PASSWORD: "better"}
        )
        await hass.async_block_till_done()

        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "reauth_successful"
        assert account.state is ConfigEntryState.LOADED

        # The account's own reload is a background task, and so is the
        # reload it fans out to each commute claiming the old store -- their
        # relative order is not guaranteed, so a commute may briefly land in
        # Home Assistant's own setup-retry backoff before the account is
        # fully back. That backoff is real, bounded, and self-healing; give
        # it a few turns rather than requiring a single reload to land in
        # the right order every time.
        for _ in range(5):
            if (
                outbound.state is ConfigEntryState.LOADED
                and inbound.state is ConfigEntryState.LOADED
            ):
                break
            freezer.tick(6)
            async_fire_time_changed(hass)
            await hass.async_block_till_done()

        assert outbound.state is ConfigEntryState.LOADED
        assert inbound.state is ConfigEntryState.LOADED

        new_store = store_for(hass, "raildata:someone")
        assert new_store is not None
        assert new_store.client is not old_client
        assert outbound.runtime_data.client is new_store.client
        assert inbound.runtime_data.client is new_store.client

    async def test_accepts_new_credentials_and_reloads(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        install_api_mock(aioclient_mock)
        install_raildata_mock(aioclient_mock, authenticated=False)
        account = await setup_account(hass)
        commute = make_raildata_entry(account.entry_id)
        await setup_entry(hass, commute)
        flow = hass.config_entries.flow.async_progress_by_handler(DOMAIN)[0]
        assert flow["context"]["entry_id"] == account.entry_id
        # The commute raised `ConfigEntryNotReady` -- its account was not
        # loaded -- and is sitting in Home Assistant's own setup-retry
        # backoff rather than having joined the account's store.
        assert commute.state is ConfigEntryState.SETUP_RETRY

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
        assert account.data[CONF_PASSWORD] == "better"
        assert account.state is ConfigEntryState.LOADED

        # The waiting commute's own retry timer -- not exercised here, to
        # keep this deterministic -- would pick up the now-loaded account on
        # its own schedule. Nothing about the account's setup nudges it
        # directly, since it never claimed the store the account's
        # unload/reload fans reloads out through; a plain reload stands in
        # for that timer firing.
        assert await hass.config_entries.async_reload(commute.entry_id)
        # mypy narrows `commute.state` from the SETUP_RETRY assert above and
        # does not know the reload just above changed it.
        assert commute.state is ConfigEntryState.LOADED  # type: ignore[comparison-overlap]

    async def test_a_new_password_is_actually_checked(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """A token already in the store must not vouch for a password it
        was not issued against."""
        install_api_mock(aioclient_mock)
        install_raildata_mock(aioclient_mock)
        _commute, account = await setup_raildata_commute(hass)
        assert account.state is ConfigEntryState.LOADED

        aioclient_mock.clear_requests()
        install_api_mock(aioclient_mock)
        called = install_raildata_mock(aioclient_mock, authenticated=False)
        result = await account.start_reauth_flow(hass)
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
        await setup_account(hass)
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
        assert result["step_id"] == "raildata_account"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], CREDENTIALS
        )
        await hass.async_block_till_done()

        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "reconfigure_successful"
        assert entry.data[CONF_SOURCE] == SOURCE_RAILDATA
        assert CONF_USERNAME not in entry.data
        assert CONF_PASSWORD not in entry.data
        account = hass.config_entries.async_get_entry(entry.data[CONF_ACCOUNT])
        assert account is not None
        assert account.data[CONF_USERNAME] == "someone"
        # Titles are re-read in the new source's spelling; codes are kept.
        assert entry.data[CONF_ORIGIN] == "Short Hills"
        assert entry.data[CONF_ORIGIN_ID] == "RT"
        assert entry.data[CONF_DESTINATION] == "New York Penn Station"
        assert entry.state is ConfigEntryState.LOADED
        assert isinstance(entry.runtime_data.client, RailDataClient)

    async def test_raildata_to_website_drops_the_account(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        install_api_mock(aioclient_mock)
        install_raildata_mock(aioclient_mock)
        entry, _account = await setup_raildata_commute(hass)

        result = await entry.start_reconfigure_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": SOURCE_WEBSITE}
        )
        await hass.async_block_till_done()

        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "reconfigure_successful"
        assert entry.data[CONF_SOURCE] == SOURCE_WEBSITE
        assert CONF_ACCOUNT not in entry.data
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
        entry, _account = await setup_raildata_commute(hass)
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


def _account_options(result: Any) -> list[dict[str, str]]:
    """Return the account picker's options."""
    for key, selector in result["data_schema"].schema.items():
        if key == CONF_ACCOUNT:
            return list(selector.config["options"])
    raise AssertionError("no account field")
