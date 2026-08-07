"""NJ Transit rail departures and service alerts for Home Assistant."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any, Final

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api.client import NJTransitClient
from .api.exceptions import NJTransitError
from .api.models import TrainRun
from .api.parsing import now_local
from .const import (
    CONF_DEPARTURE_INTERVAL,
    CONF_DESTINATION,
    CONF_FAVORITE_TRAINS,
    CONF_LOOKAHEAD,
    CONF_ORIGIN,
    CONF_STATUS_INTERVAL,
    DEFAULT_DEPARTURE_INTERVAL,
    DEFAULT_LOOKAHEAD,
    DEFAULT_STATUS_INTERVAL,
    DOMAIN,
    MIN_INTERVAL,
)
from .coordinator import (
    CoordinatorStore,
    EntryRuntime,
    NJTransitConfigEntry,
    ProgressCoordinator,
    RouteCoordinator,
    StaticCoordinator,
    SystemStatusCoordinator,
    store_for,
)
from .entity import normalize_train_ids, usable_departures
from .track_history import TrackHistory

# Guards construction of the shared store against concurrent entry setup.
_SETUP_LOCK: Final = f"{DOMAIN}_setup_lock"

# How long a train can stay latched, measured from when following began.
#
# Only a backstop against a stalled feed: a journey running normally stops
# being followed the moment the destination falls behind it, which for any
# real commute leg is well inside this. It is deliberately *not* measured
# against scheduled arrival, because stop-list times are bare wall-clock
# strings that roll into tomorrow once they pass (SPEC 3.6) -- a stale run's
# arrival time keeps receding, so a bound built on it never fires at all.
FOLLOW_LIMIT: Final = timedelta(hours=2)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.CALENDAR,
    Platform.EVENT,
    Platform.SENSOR,
]


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

    # Entries for one domain are set up concurrently, and building the shared
    # store awaits several times. Without a lock, two commutes racing through
    # here both see no store, both build one, and the second assignment wins --
    # leaving the loser's entry holding an orphaned store. That means duplicate
    # status and reference-data polling, no board sharing between commutes out
    # of the same station, and two TrackHistory objects writing to one storage
    # key, where the last save silently discards the other station's history.
    #
    # `setdefault` never awaits, so every entry gets the same lock object.
    async with hass.data.setdefault(_SETUP_LOCK, asyncio.Lock()):
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
            history = TrackHistory(hass)
            await history.async_load()
            store = CoordinatorStore(static=static, status=status, history=history)
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

    # When the current latch began. Held so the backstop below can bound how
    # long one train is followed without depending on times the feed reports.
    latched: dict[str, Any] = {"train": None, "since": None}

    def keep_following(run: TrainRun) -> bool:
        """Return whether to stay with the train already being followed.

        True only once the origin is behind it and the destination is still
        ahead, which is precisely the window where the board can no longer
        help: a departed train is dropped from it entirely.

        This does latch onto a train you watched leave without boarding, and
        there is no way to tell the difference from here -- Home Assistant
        cannot see which platform you are standing on. The Live Activity's
        Dismiss button is the answer to that, and it already existed.
        """
        if destination is None:
            return False
        if run.stops_until(origin) is not None:
            return False
        if run.stops_until(destination) is None:
            return False

        now = now_local()
        if latched["train"] != run.train_id:
            latched["train"] = run.train_id
            latched["since"] = now
        return now - latched["since"] <= FOLLOW_LIMIT

    def pick_favorite(following: TrainRun | None) -> str | None:
        """Return the favourite worth following right now, if any.

        Gated on the lookahead window so this is not a request a minute, all
        day, for a train nobody is waiting for. Defined here rather than in
        the coordinator because choosing needs the destination filter, which
        lives in the entity layer.
        """
        if following is not None and keep_following(following):
            return following.train_id

        favorites = normalize_train_ids(entry.options.get(CONF_FAVORITE_TRAINS))
        if not favorites:
            return None

        horizon = now_local() + timedelta(
            minutes=int(entry.options.get(CONF_LOOKAHEAD, DEFAULT_LOOKAHEAD))
        )
        for departure in usable_departures(board.data, route.data, destination):
            if departure.scheduled > horizon:
                break
            if departure.train_id.upper() in favorites:
                return departure.train_id
        return None

    # Where the origin is, so an automation can ask whether you are near enough
    # to catch anything leaving it. Once, at setup: stations do not move.
    # A failure here is not a setup failure -- proximity is a convenience laid
    # on top of a working commute, and the alternative to a coordinate is an
    # automation that does not filter by location, which is how this behaved
    # before the lookup existed.
    try:
        origin_coordinates = await client.station_coordinates(origin)
    except NJTransitError:
        origin_coordinates = None

    progress = ProgressCoordinator(
        hass,
        client,
        pick_favorite,
        _interval(entry, CONF_DEPARTURE_INTERVAL, DEFAULT_DEPARTURE_INTERVAL),
    )
    # Not a first_refresh: a train not running today is normal, and must not
    # take setup down with it.
    await progress.async_refresh()

    entry.runtime_data = EntryRuntime(
        client=client,
        static=store.static,
        status=store.status,
        board=board,
        route=route,
        progress=progress,
        history=store.history,
        origin=origin,
        destination=destination,
        origin_coordinates=origin_coordinates,
        options=dict(entry.options),
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
        await store.history.async_flush()
        hass.data.pop(DOMAIN, None)

    return True


# Options every entity reads live. Changing one of these needs no reload,
# and reloading for them is expensive out of proportion: it tears down every
# entity and re-pages the trip planner (~18 requests) to rebuild state that
# did not depend on the option in the first place.
HOT_OPTIONS: Final = frozenset({CONF_FAVORITE_TRAINS})


async def _async_reload_entry(hass: HomeAssistant, entry: NJTransitConfigEntry) -> None:
    """Reload when options change, unless nothing structural did."""
    runtime = entry.runtime_data
    previous = runtime.options
    changed = {
        key
        for key in set(entry.options) | set(previous)
        if entry.options.get(key) != previous.get(key)
    }
    runtime.options = dict(entry.options)

    if changed and changed <= HOT_OPTIONS:
        # Nudge the entities so the new value is reflected at once, and let
        # the progress coordinator re-pick which train it is following.
        runtime.board.async_update_listeners()
        await runtime.progress.async_request_refresh()
        return

    await hass.config_entries.async_reload(entry.entry_id)
