"""Which API a commute reads from, and the client that reads it.

Kept apart from ``__init__`` so diagnostics and the config flow can ask
without importing the whole setup path.
"""

from __future__ import annotations

from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api.client import NJTransitClient
from .api.raildata import RailDataClient
from .api.source import RailSource
from .const import (
    CONF_ACCOUNT,
    CONF_SOURCE,
    DEFAULT_SOURCE,
    SOURCE_RAILDATA,
)
from .coordinator import NJTransitAccountConfigEntry, NJTransitConfigEntry
from .raildata_store import RailDataStorage


def source_of(entry: NJTransitConfigEntry) -> str:
    """Return which API an entry reads from.

    Entries created before the choice existed carry no ``source`` and read
    from the website, which is what they always did.
    """
    return str(entry.data.get(CONF_SOURCE, DEFAULT_SOURCE))


def account_entry_for(
    hass: HomeAssistant, entry: NJTransitConfigEntry
) -> NJTransitAccountConfigEntry | None:
    """Return the RailData account entry a commute references, if any."""
    account_id = entry.data.get(CONF_ACCOUNT)
    if not account_id:
        return None
    return hass.config_entries.async_get_entry(account_id)


def store_key(hass: HomeAssistant, entry: NJTransitConfigEntry) -> str:
    """Return which shared store an entry belongs to.

    One per source, and for RailData one per account: the client holds the
    token, and two entries on different accounts must not share one. A
    commute no longer carries the username itself -- it is read from the
    account entry it references.
    """
    source = source_of(entry)
    if source != SOURCE_RAILDATA:
        return source
    account = account_entry_for(hass, entry)
    username = account.data.get(CONF_USERNAME, "") if account is not None else ""
    return f"{source}:{username}"


def build_client(hass: HomeAssistant, entry: NJTransitAccountConfigEntry) -> RailSource:
    """Return a client for a RailData account entry's credentials.

    Only ever called for an entry that carries its own username and
    password -- an account entry itself, never a commute, which since the
    RailData account entry holds none of its own.
    """
    session = async_get_clientsession(hass)
    username = str(entry.data[CONF_USERNAME])
    return RailDataClient(
        session,
        username,
        str(entry.data[CONF_PASSWORD]),
        RailDataStorage(hass, username),
    )


def website_client(hass: HomeAssistant) -> RailSource:
    """Return a client for the website source, which needs no credentials."""
    return NJTransitClient(async_get_clientsession(hass))
