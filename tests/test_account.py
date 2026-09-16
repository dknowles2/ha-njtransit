"""The RailData account entry helpers, in isolation from the config flow."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.njtransit.account import (
    account_entries,
    account_unique_id,
    async_create_account_entry,
    find_account_entry,
    is_account_entry,
)
from custom_components.njtransit.const import (
    CONF_ENTRY_TYPE,
    DOMAIN,
    ENTRY_TYPE_ACCOUNT,
)

from .conftest import install_api_mock, install_raildata_mock


def make_account_entry(username: str = "someone") -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title=f"RailData ({username})",
        data={
            CONF_ENTRY_TYPE: ENTRY_TYPE_ACCOUNT,
            CONF_USERNAME: username,
            CONF_PASSWORD: "secret",
        },
        unique_id=account_unique_id(username),
    )


class TestHelpers:
    def test_is_account_entry(self) -> None:
        assert is_account_entry(make_account_entry())
        commute = MockConfigEntry(domain=DOMAIN, data={})
        assert not is_account_entry(commute)

    def test_account_unique_id_is_case_folded(self) -> None:
        """RailData usernames are not case sensitive, and the two-entry-
        one-username case this exists to collapse would otherwise survive
        a stray difference in case."""
        assert account_unique_id("Someone") == account_unique_id("someone")

    async def test_account_entries_excludes_commutes(self, hass: HomeAssistant) -> None:
        account = make_account_entry()
        account.add_to_hass(hass)
        commute = MockConfigEntry(domain=DOMAIN, data={}, unique_id="RT")
        commute.add_to_hass(hass)

        assert account_entries(hass) == [account]
        assert find_account_entry(hass, "someone") == account
        assert find_account_entry(hass, "nobody") is None


class TestAsyncCreateAccountEntry:
    """Creating an account entry goes through the config flow's own
    `async_step_import` rather than constructing a `ConfigEntry` by hand."""

    async def test_creates_and_sets_up_an_account(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        install_api_mock(aioclient_mock)
        install_raildata_mock(aioclient_mock)

        account = await async_create_account_entry(hass, "someone", "secret")

        assert is_account_entry(account)
        assert account.data[CONF_USERNAME] == "someone"
        assert account.data[CONF_PASSWORD] == "secret"
        assert account.unique_id == account_unique_id("someone")

    async def test_a_second_call_for_the_same_username_gets_the_existing_account(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """The import flow sets the unique ID before creating the entry, so
        a second call for a username that already has an account aborts
        with `already_configured` instead of creating a duplicate. A caller
        that raced past its own `find_account_entry` check --
        `async_migrate_entry`'s lock only serializes callers within this
        process -- still gets back the one account that exists, rather than
        an error or a duplicate."""
        install_api_mock(aioclient_mock)
        install_raildata_mock(aioclient_mock)
        first = await async_create_account_entry(hass, "someone", "secret")

        second = await async_create_account_entry(hass, "someone", "secret")

        assert second.entry_id == first.entry_id
        assert len(account_entries(hass)) == 1

    async def test_a_flow_abort_with_no_matching_account_raises(
        self, hass: HomeAssistant
    ) -> None:
        """Belt-and-suspenders: if the flow ever aborts for a reason other
        than the unique ID this call itself set, and no account can be
        found either, this fails loudly rather than returning nothing
        useful to a caller that needs an account entry to reference."""
        with (
            patch(
                "homeassistant.config_entries.ConfigEntriesFlowManager.async_init",
                return_value={"type": FlowResultType.ABORT, "reason": "unknown"},
            ),
            pytest.raises(HomeAssistantError),
        ):
            await async_create_account_entry(hass, "someone", "secret")
