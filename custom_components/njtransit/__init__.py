"""NJ Transit rail departures and service alerts for Home Assistant."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api.client import NJTransitClient
from .const import (
    CONF_DEPARTURE_INTERVAL,
    CONF_DESTINATION,
    CONF_ORIGIN,
    CONF_STATUS_INTERVAL,
    DEFAULT_DEPARTURE_INTERVAL,
    DEFAULT_STATUS_INTERVAL,
    DOMAIN,
    MIN_INTERVAL,
)
from .coordinator import (
    CoordinatorStore,
    EntryRuntime,
    NJTransitConfigEntry,
    RouteCoordinator,
    StaticCoordinator,
    SystemStatusCoordinator,
    store_for,
)

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.SENSOR]


def _interval(entry: NJTransitConfigEntry, key: str, default: int) -> timedelta:
    """Return a poll interval from options, floored at the vendor's cadence."""
    seconds = entry.options.get(key, default)
    return timedelta(seconds=max(MIN_INTERVAL, int(seconds)))


async def async_setup_entry(hass: HomeAssistant, entry: NJTransitConfigEntry) -> bool:
    """Set up a commute from a config entry."""
    session = async_get_clientsession(hass)
    client = NJTransitClient(session)

    origin: str = entry.data[CONF_ORIGIN]
    destination: str | None = entry.data.get(CONF_DESTINATION)

    store = store_for(hass)
    if store is None:
        static = StaticCoordinator(hass, client)
        status = SystemStatusCoordinator(
            hass,
            client,
            "system status",
            _interval(entry, CONF_STATUS_INTERVAL, DEFAULT_STATUS_INTERVAL),
        )
        await static.async_config_entry_first_refresh()
        await status.async_config_entry_first_refresh()
        store = CoordinatorStore(static=static, status=status)
        hass.data[DOMAIN] = store

    store.claim(entry.entry_id)

    board = await store.board_for(
        hass,
        client,
        origin,
        _interval(entry, CONF_DEPARTURE_INTERVAL, DEFAULT_DEPARTURE_INTERVAL),
        entry.entry_id,
    )

    route = RouteCoordinator(hass, client, origin, destination or origin)
    if destination:
        # A failed resolution degrades to label matching rather than failing
        # setup, so this deliberately does not use
        # async_config_entry_first_refresh.
        await route.async_refresh()

    entry.runtime_data = EntryRuntime(
        client=client,
        static=store.static,
        status=store.status,
        board=board,
        route=route,
        origin=origin,
        destination=destination,
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: NJTransitConfigEntry) -> bool:
    """Unload a commute."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not unloaded:
        return False

    store = store_for(hass)
    if store is None:
        return True

    await store.release_board(entry.runtime_data.origin, entry.entry_id)
    if store.release(entry.entry_id):
        await store.static.async_shutdown()
        await store.status.async_shutdown()
        hass.data.pop(DOMAIN, None)

    return True


async def _async_reload_entry(hass: HomeAssistant, entry: NJTransitConfigEntry) -> None:
    """Reload when options change."""
    await hass.config_entries.async_reload(entry.entry_id)
