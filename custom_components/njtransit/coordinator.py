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
from datetime import date, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api.client import NJTransitClient
from .api.exceptions import (
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
from .api.parsing import now_local
from .const import DOMAIN, ROUTE_INTERVAL, STATIC_INTERVAL

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
    """Shared error translation for this integration's coordinators."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: NJTransitClient,
        name: str,
        interval: timedelta,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} {name}",
            update_interval=interval,
        )
        self.client = client


class SystemStatusCoordinator(NJTransitCoordinator[tuple[SystemAlert, ...]]):
    """Polls the system-wide service alert feed.

    Shared by every config entry -- the feed is not per-station.
    """

    async def _async_update_data(self) -> tuple[SystemAlert, ...]:
        try:
            return await self.client.system_status()
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
        client: NJTransitClient,
        station: str,
        interval: timedelta,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(hass, client, f"departures {station}", interval)
        self.station = station

    async def _async_update_data(self) -> DepartureBoard:
        try:
            return await self.client.departures(self.station)
        except NJTransitConnectionError as err:
            raise UpdateFailed(f"Could not reach NJ Transit: {err}") from err
        except NJTransitError as err:
            _LOGGER.warning("Departure board for %s failed: %s", self.station, err)
            raise UpdateFailed(str(err)) from err


class StaticCoordinator(NJTransitCoordinator[StaticData]):
    """Fetches the station and line reference data."""

    def __init__(self, hass: HomeAssistant, client: NJTransitClient) -> None:
        """Initialize the coordinator."""
        super().__init__(hass, client, "reference data", STATIC_INTERVAL)

    async def _async_update_data(self) -> StaticData:
        try:
            return StaticData(
                stations=await self.client.stations(),
                lines=await self.client.train_lines(),
            )
        except NJTransitConnectionError as err:
            raise UpdateFailed(f"Could not reach NJ Transit: {err}") from err
        except NJTransitError as err:
            _LOGGER.warning("Reference data request failed: %s", err)
            raise UpdateFailed(str(err)) from err


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
        client: NJTransitClient,
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
        today = now_local().date()
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

    static: StaticCoordinator
    status: SystemStatusCoordinator
    boards: dict[str, DepartureCoordinator] = field(default_factory=dict)
    _board_users: dict[str, set[str]] = field(default_factory=dict)
    _users: set[str] = field(default_factory=set)

    def claim(self, entry_id: str) -> None:
        """Record that an entry is using the shared coordinators."""
        self._users.add(entry_id)

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
        client: NJTransitClient,
        station: str,
        interval: timedelta,
        entry_id: str,
    ) -> DepartureCoordinator:
        """Return the board coordinator for a station, creating it if needed."""
        coordinator = self.boards.get(station)
        if coordinator is None:
            coordinator = DepartureCoordinator(hass, client, station, interval)
            self.boards[station] = coordinator
            await coordinator.async_config_entry_first_refresh()

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
        coordinator = self.boards.pop(station, None)
        if coordinator is not None:
            await coordinator.async_shutdown()


def store_for(hass: HomeAssistant) -> CoordinatorStore | None:
    """Return the shared store, if one exists."""
    store = hass.data.get(DOMAIN)
    return store if isinstance(store, CoordinatorStore) else None


class ProgressCoordinator(NJTransitCoordinator[TrainRun | None]):
    """Follows one train along its route.

    The stop list costs a request per train and cannot be batched from the
    board (SPEC 2.2), which is why per-train tracking was deferred. Following
    only the favourite makes it one request per poll instead of nineteen.

    Which train to follow is injected rather than computed here: choosing it
    needs the destination filter, which lives in the entity layer, and
    importing that from a coordinator would be circular.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        client: NJTransitClient,
        pick: Callable[[], str | None],
        interval: timedelta,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(hass, client, "train progress", interval)
        self._pick = pick

    async def _async_update_data(self) -> TrainRun | None:
        train_id = self._pick()
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

    client: NJTransitClient
    static: StaticCoordinator
    status: SystemStatusCoordinator
    board: DepartureCoordinator
    route: RouteCoordinator
    progress: ProgressCoordinator
    origin: str
    destination: str | None
    options: dict[str, Any] = field(default_factory=dict)
    """The options this entry was built with.

    Kept so the update listener can compare against the new ones and skip
    a reload when nothing structural changed."""


type NJTransitConfigEntry = ConfigEntry[EntryRuntime]
