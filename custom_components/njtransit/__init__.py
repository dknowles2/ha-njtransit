"""NJ Transit rail departures and service alerts for Home Assistant."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any, Final

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .account import async_create_account_entry, find_account_entry, is_account_entry
from .api.client import NJTransitClient
from .api.exceptions import (
    NJTransitAuthError,
    NJTransitConnectionError,
    NJTransitError,
)
from .api.models import TrainRun
from .api.parsing import now_local
from .api.raildata import RailDataClient
from .api.source import RailSource
from .const import (
    CONF_ACCOUNT,
    CONF_DEPARTURE_INTERVAL,
    CONF_DESTINATION,
    CONF_DESTINATION_ID,
    CONF_FAVORITE_TRAINS,
    CONF_LOOKAHEAD,
    CONF_ORIGIN,
    CONF_ORIGIN_ID,
    CONF_STATUS_INTERVAL,
    CONFIG_ENTRY_VERSION,
    DEFAULT_DEPARTURE_INTERVAL,
    DEFAULT_LOOKAHEAD,
    DEFAULT_STATUS_INTERVAL,
    DOMAIN,
    MIN_INTERVAL,
    SOURCE_RAILDATA,
    SOURCE_WEBSITE,
)
from .coordinator import (
    AccountRuntime,
    CoordinatorStore,
    EntryRuntime,
    NJTransitAccountConfigEntry,
    NJTransitConfigEntry,
    ProgressCoordinator,
    RouteCoordinator,
    StaticCoordinator,
    SystemStatusCoordinator,
    forget_store,
    register_store,
    store_count,
    store_for,
)
from .entity import normalize_train_ids, usable_departures
from .frontend import async_register_card
from .sources import (
    account_entry_for,
    build_client,
    source_of,
    store_key,
    website_client,
)
from .track_history import TrackHistory

# Guards construction of the shared store against concurrent entry setup.
_SETUP_LOCK: Final = f"{DOMAIN}_setup_lock"

# Guards creation of a RailData account entry during migration, so two
# commute entries migrating on the same username in the same startup do not
# each create one -- see `async_migrate_entry`.
_MIGRATION_LOCK: Final = f"{DOMAIN}_migration_lock"

# The one track history, whichever sources are in use. Stores are per source
# (SPEC 2.9) but the history's storage key is not, and two objects writing to
# it would each discard the other's stations on every save.
_HISTORY: Final = f"{DOMAIN}_history"

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


async def _shared_history(hass: HomeAssistant) -> TrackHistory:
    """Return the one track history, loading it on first use.

    Not per source or per account: its storage key is not, and two writers
    would each discard the other's stations on every save.
    """
    history = hass.data.get(_HISTORY)
    if not isinstance(history, TrackHistory):
        history = TrackHistory(hass)
        await history.async_load()
        hass.data[_HISTORY] = history
    return history


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a config entry -- a commute, or a RailData account."""
    if is_account_entry(entry):
        return await _async_setup_account_entry(hass, entry)
    return await _async_setup_commute_entry(hass, entry)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry -- a commute, or a RailData account."""
    if is_account_entry(entry):
        return await _async_unload_account_entry(hass, entry)
    return await _async_unload_commute_entry(hass, entry)


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Move a pre-account RailData commute's credentials onto an account.

    Before the RailData account entry existed, every RailData commute entry carried its own username and
    password. Two commutes on one account each carried the *same* password,
    checked and reauthenticated independently even though they always shared
    one client underneath (SPEC 7.1). This collapses every such entry onto
    one account entry per username, found by username if a sibling commute
    has already been migrated, created otherwise.

    Website entries carry no credentials and are untouched beyond the
    version bump.
    """
    if entry.version > CONFIG_ENTRY_VERSION:
        # A newer version than this code understands. Refuse rather than
        # guess at what changed.
        return False

    if entry.version < CONFIG_ENTRY_VERSION:
        new_data = dict(entry.data)
        if source_of(entry) == SOURCE_RAILDATA and CONF_USERNAME in new_data:
            username = str(new_data.pop(CONF_USERNAME))
            password = str(new_data.pop(CONF_PASSWORD, ""))
            account = await _async_ensure_account_entry(hass, username, password)
            new_data[CONF_ACCOUNT] = account.entry_id
        hass.config_entries.async_update_entry(
            entry, data=new_data, version=CONFIG_ENTRY_VERSION
        )

    return True


async def _async_ensure_account_entry(
    hass: HomeAssistant, username: str, password: str
) -> ConfigEntry:
    """Return the account entry for ``username``, creating it if needed.

    Locked so that two commute entries migrating for the same username in
    the same startup produce exactly one account entry rather than a race
    between them.
    """
    lock: asyncio.Lock = hass.data.setdefault(_MIGRATION_LOCK, asyncio.Lock())
    async with lock:
        existing = find_account_entry(hass, username)
        if existing is not None:
            return existing
        return await async_create_account_entry(hass, username, password)


# -- the RailData account --------------------------------------------------


async def _async_setup_account_entry(
    hass: HomeAssistant, entry: NJTransitAccountConfigEntry
) -> bool:
    """Set up a RailData account: one client, one token, one set of fetched
    schedules, shared by every commute entry that references it.

    Creates no entities. A commute entry depends on this one being loaded
    (see `_async_setup_commute_entry`) and reloads when it does.
    """
    key = f"{SOURCE_RAILDATA}:{entry.data.get(CONF_USERNAME, '')}"

    async with hass.data.setdefault(_SETUP_LOCK, asyncio.Lock()):
        store = store_for(hass, key)
        if store is None:
            client = build_client(hass, entry)
            assert isinstance(client, RailDataClient)
            try:
                # Not `fresh=True`: the config flow (or the migration that
                # created this entry) already checked this password, and
                # re-checking it on every restart would spend the ten-a-day
                # budget for nothing. This rides on whatever the account's
                # storage already holds.
                await client.authenticate()
            except NJTransitAuthError as err:
                raise ConfigEntryAuthFailed(str(err)) from err
            except NJTransitConnectionError as err:
                raise ConfigEntryNotReady(str(err)) from err

            static = StaticCoordinator(hass, client)
            status = SystemStatusCoordinator(
                hass,
                client,
                "system status",
                timedelta(seconds=DEFAULT_STATUS_INTERVAL),
            )
            await static.async_first_refresh()
            await status.async_first_refresh()
            history = await _shared_history(hass)
            store = CoordinatorStore(
                client=client,
                static=static,
                status=status,
                history=history,
                account_entry_id=entry.entry_id,
            )
            store.adopt(hass, static)
            store.adopt(hass, status)
            register_store(hass, key, store)

        store.claim(entry.entry_id)

    entry.runtime_data = AccountRuntime(client=store.client, store_key=key)
    return True


async def _async_unload_account_entry(
    hass: HomeAssistant, entry: NJTransitAccountConfigEntry
) -> bool:
    """Unload a RailData account.

    Torn down unconditionally, whether or not a commute still claims it: the
    account is the store's sole owner, so a claim is bookkeeping, not a vote
    against teardown -- a reauth replacing this account's credentials must
    never leave the old, now-wrong client running underneath commutes that
    happened to still be using it. Every commute claiming the store is
    reloaded afterward: if this was a reauth, they pick up a fresh client
    built from the corrected credentials; if the account was removed, they
    land on `ConfigEntryNotReady` -- a clear, retrying error state -- rather
    than keep polling a client that no longer exists.
    """
    key = entry.runtime_data.store_key
    store = store_for(hass, key)
    if store is None:
        return True

    dependents = [entry_id for entry_id in store.users if entry_id != entry.entry_id]
    store.release(entry.entry_id)

    await store.async_shutdown()
    forget_store(hass, key)
    if store_count(hass) == 0:
        hass.data.pop(_HISTORY, None)

    if hass.is_stopping:
        # Every commute is being unloaded right along with this account at
        # shutdown. Scheduling a reload for any of them here would just be a
        # task racing the shutdown itself -- "Task exception was never
        # retrieved" or `OperationNotAllowed` on every restart -- for work
        # that would be immediately undone anyway.
        return True

    for dependent_id in dependents:
        hass.config_entries.async_schedule_reload(dependent_id)

    return True


# -- a commute --------------------------------------------------------------


async def _async_setup_commute_entry(
    hass: HomeAssistant, entry: NJTransitConfigEntry
) -> bool:
    """Set up a commute from a config entry."""
    await async_register_card(hass)

    session = async_get_clientsession(hass)

    origin: str = entry.data[CONF_ORIGIN]
    destination: str | None = entry.data.get(CONF_DESTINATION)

    client: RailSource
    if source_of(entry) == SOURCE_RAILDATA:
        client, store = await _client_for_raildata_commute(
            hass, entry, origin, destination
        )
    else:
        client, store = await _client_for_website_commute(hass, entry)

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
    #
    # Always the website, whichever source the commute reads from. RailData
    # publishes no coordinates, and this is an anonymous one-shot lookup of
    # a public fact rather than a second feed to keep in step (SPEC 2.9).
    try:
        origin_coordinates = await NJTransitClient(session).station_coordinates(origin)
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
        store_key=store_key(hass, entry),
        origin_coordinates=origin_coordinates,
        options=dict(entry.options),
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    return True


async def _client_for_raildata_commute(
    hass: HomeAssistant,
    entry: NJTransitConfigEntry,
    origin: str,
    destination: str | None,
) -> tuple[RailDataClient, CoordinatorStore]:
    """Return the shared client and store a RailData commute reads through.

    The store is built by the account entry, never by a commute -- a
    commute only ever joins one that already exists. If the account is not
    loaded yet, setup retries rather than building a second, credential-less
    store of its own. That retry rides on Home Assistant's own setup-retry
    backoff: a commute that lands here before its account is ready is not
    yet a claimant of the store, so it is not among the entries a later
    account reload or removal reloads directly (`_async_unload_account_
    entry`) -- it just tries again on its own schedule, which an account
    that has since become ready satisfies without anything further to do.
    """
    account_entry = account_entry_for(hass, entry)
    if account_entry is None or account_entry.state is not ConfigEntryState.LOADED:
        raise ConfigEntryNotReady("The RailData account for this commute is not loaded")

    async with hass.data.setdefault(_SETUP_LOCK, asyncio.Lock()):
        key = account_entry.runtime_data.store_key
        store = store_for(hass, key)
        if store is None:
            # The account claims to be loaded but its store is gone -- a
            # reload landed between the state check above and here. Either
            # way, joining nothing would silently orphan this commute.
            raise ConfigEntryNotReady("The RailData account's store is not ready")

        client = store.client
        assert isinstance(client, RailDataClient)
        # The entry stores the title alongside the code, and the code is the
        # identifier both APIs share. Telling the client saves it resolving
        # the title through a station list whose spelling may differ from
        # the one the entry was set up with.
        client.remember(origin, entry.data[CONF_ORIGIN_ID])
        if destination and CONF_DESTINATION_ID in entry.data:
            client.remember(destination, entry.data[CONF_DESTINATION_ID])

        store.claim(entry.entry_id)

    return client, store


async def _client_for_website_commute(
    hass: HomeAssistant, entry: NJTransitConfigEntry
) -> tuple[RailSource, CoordinatorStore]:
    """Return the shared client and store for the website source.

    Unlike RailData there is no account entry to build this: the first
    website commute through setup builds it, on behalf of every website
    commute after it.
    """
    key = SOURCE_WEBSITE

    # Entries for one domain are set up concurrently, and building the shared
    # store awaits several times. Without a lock, two commutes racing through
    # here both see no store, both build one, and the second assignment wins,
    # leaving the loser's entry holding an orphaned store.
    async with hass.data.setdefault(_SETUP_LOCK, asyncio.Lock()):
        store = store_for(hass, key)
        if store is None:
            client = website_client(hass)
            static = StaticCoordinator(hass, client)
            status = SystemStatusCoordinator(
                hass,
                client,
                "system status",
                _interval(entry, CONF_STATUS_INTERVAL, DEFAULT_STATUS_INTERVAL),
            )
            await static.async_first_refresh()
            await status.async_first_refresh()
            history = await _shared_history(hass)
            store = CoordinatorStore(
                client=client, static=static, status=status, history=history
            )
            store.adopt(hass, static)
            store.adopt(hass, status)
            register_store(hass, key, store)
        else:
            client = store.client

        store.claim(entry.entry_id)

    return client, store


async def _async_unload_commute_entry(
    hass: HomeAssistant, entry: NJTransitConfigEntry
) -> bool:
    """Unload a commute."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not unloaded:
        return False

    key = entry.runtime_data.store_key
    store = store_for(hass, key)
    if store is None:
        return True

    await store.release_board(entry.runtime_data.origin, entry.entry_id)
    released = store.release(entry.entry_id)
    if released and store.account_entry_id is None:
        # The website store has no account to own its lifecycle, so the
        # last commute leaving is what tears it down. A RailData store is
        # only ever torn down by its account entry (`_async_unload_account_
        # entry`), whether or not a commute is still claiming it.
        await store.async_shutdown()
        forget_store(hass, key)
        if store_count(hass) == 0:
            hass.data.pop(_HISTORY, None)

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
