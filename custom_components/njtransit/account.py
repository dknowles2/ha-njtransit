"""The RailData account entry.

A RailData commute used to carry its own username and password (PR #81), so
reauth and credential storage were duplicated across every commute on the
same account. This module holds the entry that now owns them instead: one
per account, typed once, reauthenticated once. Commute entries on the
RailData source store its `entry_id` (`CONF_ACCOUNT`) rather than
credentials of their own.

Kept apart from `config_flow` and `__init__` because both need it -- the
flow to create an account inline while adding a commute, `__init__`'s
migration to create one for a commute that predates this split -- and
neither should import the other.
"""

from __future__ import annotations

from homeassistant.config_entries import SOURCE_IMPORT, ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .const import CONF_ENTRY_TYPE, DOMAIN, ENTRY_TYPE_ACCOUNT


def is_account_entry(entry: ConfigEntry) -> bool:
    """Return whether ``entry`` is a RailData account rather than a commute."""
    return entry.data.get(CONF_ENTRY_TYPE) == ENTRY_TYPE_ACCOUNT


def account_unique_id(username: str) -> str:
    """Return the unique ID an account entry for ``username`` carries.

    Case-folded so ``Someone`` and ``someone`` are the one account -- RailData
    usernames are not case sensitive, and the two-entry-one-username case this
    is meant to collapse would otherwise survive a stray difference in case.
    """
    return f"account:{username.casefold()}"


def account_entries(hass: HomeAssistant) -> list[ConfigEntry]:
    """Return every RailData account entry, in no particular order."""
    return [
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if is_account_entry(entry)
    ]


def find_account_entry(hass: HomeAssistant, username: str) -> ConfigEntry | None:
    """Return the account entry for ``username``, if one already exists."""
    unique_id = account_unique_id(username)
    for entry in account_entries(hass):
        if entry.unique_id == unique_id:
            return entry
    return None


async def async_create_account_entry(
    hass: HomeAssistant, username: str, password: str
) -> ConfigEntry:
    """Create, add, and set up a new RailData account entry.

    Goes through the config flow's own `async_step_import` rather than
    constructing a `ConfigEntry` directly: that class's constructor takes
    several Home Assistant-internal arguments (`discovery_keys`,
    `subentries_data`, `minor_version`, ...) that are not a stable
    integration-facing API and have changed shape across releases, so
    building one by hand here would be one upgrade away from breaking
    account creation silently.

    The caller is expected to have already checked the credentials -- a
    config flow with `authenticate(fresh=True)`, or a migration reusing
    credentials an existing commute entry was already running on -- because
    this never spends a `getToken` call itself: `async_step_import` only
    ever calls `async_create_entry`, and the new entry's own setup
    authenticates without `fresh=True`, riding on whatever token the
    account's storage already holds.
    """
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_IMPORT},
        data={CONF_USERNAME: username, CONF_PASSWORD: password},
    )
    entry = result.get("result")
    if isinstance(entry, ConfigEntry):
        return entry

    # `async_step_import` sets the account's unique ID before creating it,
    # so a second call for a username that already has an account aborts
    # with `already_configured` instead of creating a duplicate -- the same
    # outcome `find_account_entry` gives a caller that checks first, for a
    # caller (or a second Home Assistant process sharing storage) that
    # raced it instead.
    existing = find_account_entry(hass, username)
    if existing is not None:
        return existing
    raise HomeAssistantError(
        f"Could not create a RailData account entry for {username}"
    )
