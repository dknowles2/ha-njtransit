"""Migrating a pre-account RailData commute entry onto an account entry.

Before the RailData account entry existed, every commute carried its own username and password
(PR #81). `async_migrate_entry` in `__init__.py` moves those onto a shared
account entry the first time such a commute is set up again.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.njtransit import async_migrate_entry
from custom_components.njtransit.account import account_entries, is_account_entry
from custom_components.njtransit.const import (
    CONF_ACCOUNT,
    CONF_ORIGIN,
    CONF_ORIGIN_ID,
    CONF_SOURCE,
    CONFIG_ENTRY_VERSION,
    DOMAIN,
    SOURCE_RAILDATA,
)

from .conftest import install_api_mock, install_raildata_mock
from .test_init import setup_entry


def make_legacy_raildata_entry(
    origin: str = "Short Hills",
    origin_id: str = "RT",
    username: str = "someone",
    password: str = "secret",
    unique_id: str | None = None,
) -> MockConfigEntry:
    """A version-1 RailData commute, carrying its own credentials.

    This is the shape every RailData entry had before the account split --
    what `async_migrate_entry` has to move.
    """
    return MockConfigEntry(
        domain=DOMAIN,
        title=origin,
        data={
            CONF_ORIGIN: origin,
            CONF_ORIGIN_ID: origin_id,
            CONF_SOURCE: SOURCE_RAILDATA,
            CONF_USERNAME: username,
            CONF_PASSWORD: password,
        },
        unique_id=unique_id or origin_id,
        version=1,
    )


class TestMigration:
    """Moving credentials off a commute entry and onto an account entry."""

    async def test_a_legacy_entry_gets_an_account(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        install_api_mock(aioclient_mock)
        install_raildata_mock(aioclient_mock)
        entry = make_legacy_raildata_entry()
        await setup_entry(hass, entry)

        assert entry.version == CONFIG_ENTRY_VERSION
        assert CONF_USERNAME not in entry.data
        assert CONF_PASSWORD not in entry.data
        assert entry.data[CONF_SOURCE] == SOURCE_RAILDATA
        assert CONF_ACCOUNT in entry.data

        account = hass.config_entries.async_get_entry(entry.data[CONF_ACCOUNT])
        assert account is not None
        assert is_account_entry(account)
        assert account.data[CONF_USERNAME] == "someone"
        assert account.data[CONF_PASSWORD] == "secret"
        assert account.state is ConfigEntryState.LOADED
        assert entry.state is ConfigEntryState.LOADED

    async def test_two_commutes_on_one_username_collapse_to_one_account(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """The live instance this shipped against had exactly this shape:
        two commutes, one RailData username. Migration must not produce two
        account entries for it."""
        install_api_mock(aioclient_mock)
        install_raildata_mock(aioclient_mock)
        outbound = make_legacy_raildata_entry(
            origin="Short Hills", origin_id="RT", unique_id="RT-NY"
        )
        inbound = make_legacy_raildata_entry(
            origin="New York Penn Station", origin_id="NY", unique_id="NY-RT"
        )
        await setup_entry(hass, outbound)
        await setup_entry(hass, inbound)

        accounts = account_entries(hass)
        assert len(accounts) == 1
        assert outbound.data[CONF_ACCOUNT] == accounts[0].entry_id
        assert inbound.data[CONF_ACCOUNT] == accounts[0].entry_id
        assert outbound.state is ConfigEntryState.LOADED
        assert inbound.state is ConfigEntryState.LOADED

    async def test_migration_reuses_an_already_migrated_account(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """A commute migrated in a later startup than its sibling finds the
        account by username rather than creating a second one."""
        install_api_mock(aioclient_mock)
        install_raildata_mock(aioclient_mock)
        first = make_legacy_raildata_entry(origin="Short Hills", origin_id="RT")
        await setup_entry(hass, first)
        assert len(account_entries(hass)) == 1
        existing_account_id = first.data[CONF_ACCOUNT]

        second = make_legacy_raildata_entry(
            origin="New York Penn Station", origin_id="NY", unique_id="NY"
        )
        await setup_entry(hass, second)

        assert len(account_entries(hass)) == 1
        assert second.data[CONF_ACCOUNT] == existing_account_id

    async def test_a_different_username_gets_its_own_account(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        install_api_mock(aioclient_mock)
        install_raildata_mock(aioclient_mock)
        someone = make_legacy_raildata_entry(
            origin="Short Hills", origin_id="RT", username="someone"
        )
        someone_else = make_legacy_raildata_entry(
            origin="New York Penn Station",
            origin_id="NY",
            unique_id="NY",
            username="someone-else",
        )
        await setup_entry(hass, someone)
        await setup_entry(hass, someone_else)

        accounts = account_entries(hass)
        assert len(accounts) == 2
        assert someone.data[CONF_ACCOUNT] != someone_else.data[CONF_ACCOUNT]

    async def test_a_website_entry_is_untouched(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        install_api_mock(aioclient_mock)
        entry = MockConfigEntry(
            domain=DOMAIN,
            title="Short Hills Station",
            data={CONF_ORIGIN: "Short Hills Station", CONF_ORIGIN_ID: "RT"},
            unique_id="RT",
            version=1,
        )
        await setup_entry(hass, entry)

        assert entry.version == CONFIG_ENTRY_VERSION
        assert entry.data == {CONF_ORIGIN: "Short Hills Station", CONF_ORIGIN_ID: "RT"}
        assert not account_entries(hass)
        assert entry.state is ConfigEntryState.LOADED

    async def test_an_already_migrated_entry_is_left_alone(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """An entry already at the current version is not re-migrated --
        there is nothing to move and no account to find or create."""
        install_api_mock(aioclient_mock)
        install_raildata_mock(aioclient_mock)
        account = MockConfigEntry(
            domain=DOMAIN,
            title="RailData (someone)",
            data={
                "entry_type": "account",
                CONF_USERNAME: "someone",
                CONF_PASSWORD: "secret",
            },
            unique_id="account:someone",
        )
        account.add_to_hass(hass)
        await hass.config_entries.async_setup(account.entry_id)
        await hass.async_block_till_done()

        entry = MockConfigEntry(
            domain=DOMAIN,
            title="Short Hills",
            data={
                CONF_ORIGIN: "Short Hills",
                CONF_ORIGIN_ID: "RT",
                CONF_SOURCE: SOURCE_RAILDATA,
                CONF_ACCOUNT: account.entry_id,
            },
            unique_id="RT",
            version=CONFIG_ENTRY_VERSION,
        )
        await setup_entry(hass, entry)

        assert entry.data[CONF_ACCOUNT] == account.entry_id
        assert len(account_entries(hass)) == 1
        assert entry.state is ConfigEntryState.LOADED

    async def test_a_future_version_is_refused(self, hass: HomeAssistant) -> None:
        """A downgrade -- an older release seeing an entry a newer one
        wrote -- is refused rather than guessed at."""
        entry = MockConfigEntry(
            domain=DOMAIN,
            title="Short Hills",
            data={CONF_ORIGIN: "Short Hills", CONF_ORIGIN_ID: "RT"},
            unique_id="RT",
            version=CONFIG_ENTRY_VERSION + 1,
        )
        entry.add_to_hass(hass)

        assert await async_migrate_entry(hass, entry) is False

    async def test_calling_migrate_on_a_current_entry_is_a_no_op(
        self, hass: HomeAssistant
    ) -> None:
        """Home Assistant itself never calls this for an entry already at
        the current version -- this is the defensive path for anything that
        does."""
        data = {CONF_ORIGIN: "Short Hills", CONF_ORIGIN_ID: "RT"}
        entry = MockConfigEntry(
            domain=DOMAIN,
            title="Short Hills",
            data=data,
            unique_id="RT",
            version=CONFIG_ENTRY_VERSION,
        )
        entry.add_to_hass(hass)

        assert await async_migrate_entry(hass, entry) is True
        assert entry.version == CONFIG_ENTRY_VERSION
        assert dict(entry.data) == data
