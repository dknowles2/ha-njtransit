"""Data coordinators for the NJ Transit integration.

A config entry is a *commute* -- an origin and a destination -- so several
entries can share an origin, and every entry shares the system status feed.
Giving each entry its own coordinators would poll the same board twice for two
commutes out of the same station, so the shared ones are kept in
``hass.data`` and reference counted. See :class:`CoordinatorStore`.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api.exceptions import (
    NJTransitAuthError,
    NJTransitConnectionError,
    NJTransitError,
    NJTransitNotFoundError,
)
from .api.models import (
    DepartureBoard,
    RailLine,
    ScheduledTrip,
    Station,
    SystemAlert,
    TrainRun,
)
from .api.parsing import TZ, now_local
from .api.source import RailSource
from .const import DOMAIN, ROUTE_INTERVAL, SOURCE_WEBSITE, STATIC_INTERVAL
from .track_history import TrackHistory

_LOGGER = logging.getLogger(__name__)


@dataclass
class StaticData:
    """Reference data that changes on the order of timetable revisions."""

    stations: tuple[Station, ...] = ()
    lines: tuple[RailLine, ...] = ()


@dataclass
class RouteData:
    """The trains that serve a commute, and when they run.

    ``train_ids`` is the destination filter: the board is narrowed to these
    rather than to rows whose label happens to mention the destination, which
    is what lets a transfer itinerary count as a usable train.
    """

    train_ids: frozenset[str] = frozenset()
    trips: tuple[ScheduledTrip, ...] = ()
    complete: bool = True
    """``False`` when resolution failed and the caller should fall back to
    matching the board's destination label."""


class NJTransitCoordinator[T](DataUpdateCoordinator[T]):
    """Shared error translation for this integration's coordinators.

    Coordinators come in two kinds, and the difference is who owns them.

    A *per-entry* coordinator (route, progress) belongs to one commute and
    is built the way Home Assistant expects: bound to the entry being set
    up, so the entry's unload shuts it down and its auth failures start that
    entry's reauth.

    A *shared* coordinator (reference data, alerts, a station's board) is
    used by every commute in a :class:`CoordinatorStore`, and must **not**
    be bound to whichever entry happened to build it. Home Assistant
    registers a bound coordinator's shutdown on that entry's unload, so a
    shared board created during entry A's setup died the moment A was
    reloaded -- an options change, a reconfigure -- and entry B's sensors
    went unavailable for good while B's entry read as loaded. Shared
    coordinators are therefore built with no config entry, and the two
    things that binding would have provided are supplied by hand: the
    store shuts them down when the last commute leaves, and an auth failure
    is reported to every entry in the store through ``auth_failed``.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        client: RailSource,
        name: str,
        interval: timedelta,
        *,
        shared: bool = False,
    ) -> None:
        """Initialize the coordinator.

        :param shared: Whether this coordinator outlives any one config
            entry. Shared coordinators are not bound to the entry being set
            up -- see the class docstring for why that matters.
        """
        if shared:
            super().__init__(
                hass,
                _LOGGER,
                config_entry=None,
                name=f"{DOMAIN} {name}",
                update_interval=interval,
            )
        else:
            super().__init__(
                hass,
                _LOGGER,
                name=f"{DOMAIN} {name}",
                update_interval=interval,
            )
        self.client = client
        self.auth_failed: Callable[[], None] | None = None
        """Called when the source rejects the credentials. Set by the store
        on shared coordinators, which have no entry of their own to reauth."""

    async def async_first_refresh(self) -> None:
        """Refresh once during setup, failing setup if it fails.

        The unbound equivalent of ``async_config_entry_first_refresh``,
        which insists on a bound entry. Logging is the ordinary refresh
        path's: the first failure is an error, repeats are debug.
        """
        await self.async_refresh()
        if not self.last_update_success:
            raise ConfigEntryNotReady(str(self.last_exception))

    def _reject_credentials(self, err: NJTransitAuthError) -> UpdateFailed:
        """Return the exception to raise for a rejected credential.

        A bound coordinator raises ``ConfigEntryAuthFailed`` and Home
        Assistant starts its entry's reauth. A shared one has no entry, so
        it tells the store, which starts reauth on every entry using it,
        and then fails the refresh like any other error.
        """
        if self.config_entry is not None:
            raise ConfigEntryAuthFailed(str(err)) from err
        if self.auth_failed is not None:
            self.auth_failed()
        return UpdateFailed(f"Credentials rejected: {err}")


class SystemStatusCoordinator(NJTransitCoordinator[tuple[SystemAlert, ...]]):
    """Polls the system-wide service alert feed.

    Shared by every config entry -- the feed is not per-station.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        client: RailSource,
        name: str,
        interval: timedelta,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(hass, client, name, interval, shared=True)

    async def _async_update_data(self) -> tuple[SystemAlert, ...]:
        try:
            return await self.client.system_status()
        except NJTransitAuthError as err:
            raise self._reject_credentials(err) from err
        except NJTransitConnectionError as err:
            raise UpdateFailed(f"Could not reach NJ Transit: {err}") from err
        except NJTransitError as err:
            # Not a transport problem, so the endpoint most likely changed.
            _LOGGER.warning("System status request failed: %s", err)
            raise UpdateFailed(str(err)) from err


class DepartureCoordinator(NJTransitCoordinator[DepartureBoard]):
    """Polls one station's departure board.

    Shared between commutes that leave from the same station.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        client: RailSource,
        station: str,
        interval: timedelta,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(hass, client, f"departures {station}", interval, shared=True)
        self.station = station

    async def _async_update_data(self) -> DepartureBoard:
        try:
            return await self.client.departures(self.station)
        except NJTransitAuthError as err:
            raise self._reject_credentials(err) from err
        except NJTransitConnectionError as err:
            raise UpdateFailed(f"Could not reach NJ Transit: {err}") from err
        except NJTransitError as err:
            _LOGGER.warning("Departure board for %s failed: %s", self.station, err)
            raise UpdateFailed(str(err)) from err


class StaticCoordinator(NJTransitCoordinator[StaticData]):
    """Fetches the station and line reference data."""

    def __init__(self, hass: HomeAssistant, client: RailSource) -> None:
        """Initialize the coordinator."""
        super().__init__(hass, client, "reference data", STATIC_INTERVAL, shared=True)

    async def _async_update_data(self) -> StaticData:
        try:
            return StaticData(
                stations=await self.client.stations(),
                lines=await self.client.train_lines(),
            )
        except NJTransitAuthError as err:
            raise self._reject_credentials(err) from err
        except NJTransitConnectionError as err:
            raise UpdateFailed(f"Could not reach NJ Transit: {err}") from err
        except NJTransitError as err:
            _LOGGER.warning("Reference data request failed: %s", err)
            raise UpdateFailed(str(err)) from err


# When the route coordinator re-resolves the day's trains. After the RailData
# schedule's own "after 1:30 AM would be better" guidance, with a margin.
ROUTE_REFRESH_AT = time(1, 45)


def until_next(at: time, now: datetime) -> timedelta:
    """Return how long until the next local occurrence of ``at``."""
    local = now.astimezone(TZ)
    target = local.replace(
        hour=at.hour, minute=at.minute, second=at.second, microsecond=0
    )
    if target <= local:
        target += timedelta(days=1)
    return target - local


class RouteCoordinator(NJTransitCoordinator[RouteData]):
    """Resolves which trains serve a commute, once a day.

    Pure timetable data, so a daily refresh is ample. Querying per date also
    means weekend and holiday timetables fall out without any special
    handling.

    A failure here degrades rather than fails: the coordinator returns
    ``complete=False`` and entities fall back to matching the board's
    destination label. Losing the better filter is worth far less than losing
    the departures entirely.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        client: RailSource,
        origin: str,
        destination: str,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass, client, f"route {origin} to {destination}", ROUTE_INTERVAL
        )
        self.origin = origin
        self.destination = destination

    async def _async_update_data(self) -> RouteData:
        # Each refresh lands the next one at the same early-morning moment
        # rather than a day after whenever setup happened. The RailData
        # source publishes a day's schedule from that day's midnight and
        # nothing about tomorrow before it, so a refresh at 15:00 would
        # leave a commute with yesterday's trains until 15:00 the next day.
        # The website is indifferent -- it is asked for today and tomorrow
        # either way -- so the cadence is shared rather than per source.
        now = now_local()
        self.update_interval = until_next(ROUTE_REFRESH_AT, now)
        today = now.date()
        tomorrow = today + timedelta(days=1)

        trips: list[ScheduledTrip] = []
        complete = True
        for service_date in (today, tomorrow):
            try:
                trips.extend(await self._trips_for(service_date))
            except NJTransitError as err:
                _LOGGER.warning(
                    "Could not resolve %s -> %s for %s: %s",
                    self.origin,
                    self.destination,
                    service_date,
                    err,
                )
                complete = False

        if not trips:
            # Keep whatever was resolved previously rather than blanking the
            # filter, which would widen every entity to the whole board.
            if self.data is not None:
                return RouteData(
                    train_ids=self.data.train_ids,
                    trips=self.data.trips,
                    complete=False,
                )
            return RouteData(complete=False)

        # One-seat rides only. A transfer itinerary is a real way to make the
        # journey, but surfacing it means a board row headsigned somewhere
        # else entirely -- train 880 reads "Hoboken" on a Penn Station board
        # -- with nothing to say where you change or that you must. Anyone
        # who wants the connection can find it; nobody wants to discover it
        # by being on the wrong train. Roughly 23 direct trains a day for
        # Short Hills to New York Penn, against 18 more reachable only by
        # changing.
        usable = [trip for trip in trips if not trip.has_transfer]

        # Except where nothing runs direct -- Gladstone to New York Penn has
        # no one-seat ride at all. An empty board there would read as "no
        # trains" rather than "no direct trains", which is worse than showing
        # the connections.
        if not usable:
            _LOGGER.info(
                "No direct service %s -> %s; falling back to transfer itineraries",
                self.origin,
                self.destination,
            )
            usable = trips

        return RouteData(
            train_ids=frozenset(trip.train_id for trip in usable),
            trips=tuple(sorted(usable, key=lambda trip: trip.departure)),
            complete=complete,
        )

    async def _trips_for(self, service_date: date) -> tuple[ScheduledTrip, ...]:
        """Return one service day's trips, treating no service as empty."""
        try:
            return await self.client.scheduled_trips(
                self.origin, self.destination, on=service_date
            )
        except NJTransitNotFoundError:
            # Indistinguishable from an unrecognized station name -- the
            # endpoint gives both the same generic error. The config flow
            # validates names up front so this is read as no service.
            _LOGGER.debug(
                "No service %s -> %s on %s",
                self.origin,
                self.destination,
                service_date,
            )
            return ()


@dataclass
class CoordinatorStore:
    """Reference-counted coordinators shared across config entries.

    Two commutes out of Short Hills must share one board poll, and unloading
    either must not take the board away from the other. Both directions have
    tests; getting one wrong leaks and the other breaks the surviving entry.
    """

    client: RailSource
    """The one client every coordinator in this store polls through.

    Shared so that entries on the same RailData account share a token and
    the schedules it has fetched, both of which are rationed per day."""

    static: StaticCoordinator
    status: SystemStatusCoordinator
    history: TrackHistory
    account_entry_id: str | None = None
    """The RailData account entry that owns this store, if any.

    ``None`` for the website store, which has no credentials to reauth.
    A RailData store's coordinators report a rejected credential to this
    entry alone (SPEC 8.1) -- not to every commute claiming the store, which
    would open a reauth flow per commute for what is one broken password."""
    boards: dict[str, DepartureCoordinator] = field(default_factory=dict)
    _board_users: dict[str, set[str]] = field(default_factory=dict)
    _users: set[str] = field(default_factory=set)
    _recorders: dict[str, Callable[[], None]] = field(default_factory=dict)
    """Detach callbacks for the track recorder, one per station board.

    Held here rather than on the entry, because the board is shared: the
    recorder must outlive any single commute using that station and stop only
    when the board itself does."""

    def claim(self, entry_id: str) -> None:
        """Record that an entry is using the shared coordinators."""
        self._users.add(entry_id)

    @property
    def users(self) -> frozenset[str]:
        """Return the entry IDs currently using this store."""
        return frozenset(self._users)

    def adopt(
        self, hass: HomeAssistant, coordinator: NJTransitCoordinator[Any]
    ) -> None:
        """Give a shared coordinator the entry-level plumbing it lacks.

        With no entry of its own, a rejected credential would otherwise be
        a logged failure and nothing more. It is reported to the account
        entry, which is where reauth lives -- not to every commute claiming
        the store, which runs on the same credentials but does not hold
        them.
        """

        @callback
        def start_reauth() -> None:
            entry_id = self.account_entry_id
            if entry_id is None:
                return
            entry = hass.config_entries.async_get_entry(entry_id)
            if entry is not None:
                entry.async_start_reauth(hass)

        coordinator.auth_failed = start_reauth

    def release(self, entry_id: str) -> bool:
        """Drop an entry's claim.

        :return: ``True`` when no entries remain, so the caller can discard
            the store entirely.
        """
        self._users.discard(entry_id)
        return not self._users

    async def board_for(
        self,
        hass: HomeAssistant,
        client: RailSource,
        station: str,
        interval: timedelta,
        entry_id: str,
    ) -> DepartureCoordinator:
        """Return the board coordinator for a station, creating it if needed."""
        coordinator = self.boards.get(station)
        if coordinator is None:
            coordinator = DepartureCoordinator(hass, client, station, interval)
            self.adopt(hass, coordinator)
            self.boards[station] = coordinator
            await coordinator.async_first_refresh()
            self._recorders[station] = self.history.attach(coordinator)
            # The listener fires on subsequent updates only, so the board that
            # setup just fetched would otherwise go unrecorded until the next
            # poll -- and on a restart during the ten minutes when a terminal
            # publishes its tracks, that is the interesting one.
            if coordinator.data is not None:
                self.history.record(coordinator.data)

        self._board_users.setdefault(station, set()).add(entry_id)
        return coordinator

    async def release_board(self, station: str, entry_id: str) -> None:
        """Drop an entry's claim on a station's board.

        The coordinator is only discarded once no entry wants it. Shutting it
        down while another commute still polls that station is the bug this
        exists to prevent.
        """
        users = self._board_users.get(station)
        if users is None:
            return

        users.discard(entry_id)
        if users:
            return

        self._board_users.pop(station, None)
        detach = self._recorders.pop(station, None)
        if detach is not None:
            detach()
        coordinator = self.boards.pop(station, None)
        if coordinator is not None:
            await coordinator.async_shutdown()

    async def async_shutdown(self) -> None:
        """Shut down every coordinator this store owns, boards included.

        For the website store this runs when the last commute releases it.
        For a RailData store it runs only from the account entry's own
        unload (`_async_unload_account_entry` in ``__init__``) -- credentials
        belong to the account, not to any one commute, so a commute claim
        must never keep this store, or a client about to be replaced by a
        reauth, alive on its own."""
        for detach in list(self._recorders.values()):
            detach()
        self._recorders.clear()
        for coordinator in list(self.boards.values()):
            await coordinator.async_shutdown()
        self.boards.clear()
        self._board_users.clear()
        await self.static.async_shutdown()
        await self.status.async_shutdown()
        await self.history.async_flush()


def store_for(
    hass: HomeAssistant, key: str = SOURCE_WEBSITE
) -> CoordinatorStore | None:
    """Return the shared store for one data source, if one exists.

    Stores are per source rather than per domain because the two sources
    are different feeds: a board polled from the website and one polled from
    RailData are not the same board, and the RailData client is bound to an
    account. ``key`` is the source name, suffixed with the account for
    RailData -- see :func:`~.store_key`.
    """
    stores = hass.data.get(DOMAIN)
    if not isinstance(stores, dict):
        return None
    store = stores.get(key)
    return store if isinstance(store, CoordinatorStore) else None


def register_store(hass: HomeAssistant, key: str, store: CoordinatorStore) -> None:
    """Make ``store`` the shared store for a data source."""
    hass.data.setdefault(DOMAIN, {})[key] = store


def store_count(hass: HomeAssistant) -> int:
    """Return how many data sources currently have a store."""
    stores = hass.data.get(DOMAIN)
    return len(stores) if isinstance(stores, dict) else 0


def forget_store(hass: HomeAssistant, key: str) -> None:
    """Drop a data source's store once nothing uses it."""
    stores = hass.data.get(DOMAIN)
    if isinstance(stores, dict):
        stores.pop(key, None)
        if not stores:
            hass.data.pop(DOMAIN, None)


class ProgressCoordinator(NJTransitCoordinator[TrainRun | None]):
    """Follows one train along its route.

    The stop list costs a request per train and cannot be batched from the
    board (SPEC 2.2), which is why per-train tracking was deferred. Following
    only the favourite makes it one request per poll instead of nineteen.

    Which train to follow is injected rather than computed here: choosing it
    needs the destination filter, which lives in the entity layer, and
    importing that from a coordinator would be circular. The chooser is handed
    the run currently being followed so it can decide to stay with it -- which
    matters once the train has left, because the board drops a departed train
    and re-picking from scratch would hand the tracker to the *next* favourite
    while you are sitting on the previous one.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        client: RailSource,
        pick: Callable[[TrainRun | None], str | None],
        interval: timedelta,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(hass, client, "train progress", interval)
        self._pick = pick

    async def _async_update_data(self) -> TrainRun | None:
        # `self.data` is still the previous run here -- the coordinator only
        # replaces it once this returns.
        train_id = self._pick(self.data)
        if train_id is None:
            # No favourite worth following right now. Returning early is the
            # point: it is what keeps this from being a request per minute all
            # day for a train nobody is waiting for.
            return None

        try:
            return await self.client.train_run(train_id)
        except NJTransitNotFoundError:
            # A favourite that is not running today. Normal on a weekend or
            # after a timetable change, and not a reason to fail the refresh.
            _LOGGER.debug("Train %s is not in service", train_id)
            return None


@dataclass
class EntryRuntime:
    """Everything one config entry needs at runtime."""

    client: RailSource
    static: StaticCoordinator
    status: SystemStatusCoordinator
    board: DepartureCoordinator
    route: RouteCoordinator
    progress: ProgressCoordinator
    history: TrackHistory
    origin: str
    destination: str | None
    store_key: str
    """Which shared store this entry was set up against.

    Remembered rather than recomputed at unload, because a reconfigure
    rewrites the entry's data *before* reloading it: computing the key from
    the new data would look up the wrong store, release nothing, and leave
    the old store's coordinators polling with no entry left to stop them."""

    origin_coordinates: tuple[float, float] | None = None
    """Where the origin station is, when the endpoint would say.

    Looked up once at setup and never refreshed -- stations do not move, and
    it is not worth a request per poll to confirm that. ``None`` when the
    lookup failed or the title is one of the platform-level aliases the
    proximity operation does not recognize (SPEC 3.9)."""

    options: dict[str, Any] = field(default_factory=dict)
    """The options this entry was built with.

    Kept so the update listener can compare against the new ones and skip
    a reload when nothing structural changed."""


@dataclass
class AccountRuntime:
    """What a RailData account entry needs at runtime.

    Deliberately thin next to :class:`EntryRuntime` -- an account entry has
    no board, route or destination of its own. It exists to own the client
    and the store built from it, which every commute entry referencing it
    then shares."""

    client: RailSource
    store_key: str
    """The shared store this account's coordinators are registered under.

    Remembered rather than recomputed at unload for the same reason a
    commute remembers it: unload must release the store that was actually
    claimed, not one derived from data that may have already changed."""


type NJTransitConfigEntry = ConfigEntry[EntryRuntime]
type NJTransitAccountConfigEntry = ConfigEntry[AccountRuntime]
