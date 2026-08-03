"""Transport for NJ Transit's private GraphQL endpoint."""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from typing import Any

import aiohttp

from .exceptions import (
    NJTransitAPIError,
    NJTransitConnectionError,
    NJTransitNotFoundError,
    NJTransitRequestError,
)
from .models import (
    DepartureBoard,
    RailLine,
    ScheduledTrip,
    Station,
    SystemAlert,
)
from .parsing import (
    TZ,
    now_local,
    parse_alerts,
    parse_board,
    parse_lines,
    parse_stations,
    parse_trips,
)
from .queries import (
    DEPARTURE_BOARD,
    ENDPOINT,
    PLANNER_DATE_FORMAT,
    PLANNER_TIME_FORMAT,
    STATIONS,
    SYSTEM_STATUS,
    TRAIN_LINES,
    TRIP_PLANNER,
    TRIP_PLANNER_DEFAULTS,
    Operation,
)

_LOGGER = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 30.0

# The planner returns exactly three trips per call, so a service day takes
# roughly two dozen. The cap is a backstop against a window that never
# advances, not a coverage target -- 24 calls covered 4:49 AM to 11:38 PM for
# Short Hills to New York Penn. See SPEC 2.6.
_PAGE_START = time(4, 0)
_MAX_PAGES = 40

# When a page returns nothing later than where it started, step forward by
# this much rather than looping on the same window forever.
_PAGE_NUDGE = timedelta(minutes=30)


class NJTransitClient:
    """Talks to NJ Transit's GraphQL endpoint.

    Every call is a named operation with its arguments in ``variables``. The
    WAF in front of the endpoint rejects inline arguments outright, so this is
    a hard requirement rather than a style choice.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        """Initialize the client.

        :param session: Session to use. Injected rather than created so this
            module stays free of any framework's lifecycle.
        :param timeout: Per-request timeout in seconds.
        """
        self._session = session
        self._timeout = aiohttp.ClientTimeout(total=timeout)

    async def _execute(
        self,
        operation: Operation,
        variables: dict[str, Any] | None = None,
    ) -> Any:
        """Run an operation and return its root field's payload.

        :raise NJTransitConnectionError: The endpoint was unreachable.
        :raise NJTransitRequestError: The WAF rejected the request.
        :raise NJTransitAPIError: GraphQL returned errors.
        :raise NJTransitNotFoundError: The payload was null.
        """
        body = {
            "operationName": operation.name,
            "variables": variables or {},
            "query": operation.document,
        }

        try:
            async with self._session.post(
                ENDPOINT,
                json=body,
                headers={"content-type": "application/json"},
                timeout=self._timeout,
            ) as response:
                # Errors come back as JSON with a non-200 status, so read the
                # body before deciding -- `raise_for_status()` here would
                # discard the only useful diagnostic.
                payload = await response.json(content_type=None)
        except aiohttp.ClientError as err:
            raise NJTransitConnectionError(
                f"{operation.name} could not reach the endpoint: {err}"
            ) from err
        except TimeoutError as err:
            raise NJTransitConnectionError(f"{operation.name} timed out") from err
        except ValueError as err:
            raise NJTransitConnectionError(
                f"{operation.name} returned a non-JSON response"
            ) from err

        if not isinstance(payload, dict):
            raise NJTransitConnectionError(
                f"{operation.name} returned {type(payload).__name__}, expected an object"
            )

        # The WAF answers before GraphQL sees the request, in its own shape.
        if "message" in payload and "data" not in payload:
            raise NJTransitRequestError(
                f"{operation.name} was rejected: {payload.get('message')}"
            )

        data = payload.get("data")
        if not isinstance(data, dict):
            raise NJTransitAPIError(f"{operation.name} returned no data")

        # Errors nest under `data` for application-level failures and sit at
        # the top level for validation failures.
        errors = data.get("errors") or payload.get("errors")
        if errors:
            message = errors[0].get("message", "unknown error")
            raise NJTransitAPIError(f"{operation.name}: {message}")

        result = data.get(operation.root_field)
        if result is None:
            raise NJTransitNotFoundError(
                f"{operation.name} returned no {operation.root_field}"
            )
        return result

    async def system_status(self) -> tuple[SystemAlert, ...]:
        """Return every current service alert, across all lines and modes."""
        return parse_alerts(await self._execute(SYSTEM_STATUS))

    async def departures(self, station: str) -> DepartureBoard:
        """Return a station's departure board.

        :param station: A station title from :meth:`stations`. Names are
            fuzzy-matched, but an unrecognized one raises.
        :raise NJTransitNotFoundError: The station was not recognized.
        """
        payload = await self._execute(DEPARTURE_BOARD, {"station": station})
        return parse_board(station, payload, now_local())

    async def stations(self) -> tuple[Station, ...]:
        """Return the canonical station list.

        Contains alias rows -- several stations appear under more than one
        title -- so deduplicate by :attr:`~.Station.penta_id` before showing
        these to a user.
        """
        return parse_stations(await self._execute(STATIONS))

    async def train_lines(self) -> tuple[RailLine, ...]:
        """Return every rail line."""
        return parse_lines(await self._execute(TRAIN_LINES))

    async def _planner_page(
        self,
        origin: str,
        destination: str,
        when: datetime,
    ) -> tuple[ScheduledTrip, ...]:
        """Return the three itineraries departing at or after ``when``."""
        variables = {
            **TRIP_PLANNER_DEFAULTS,
            "origin": origin,
            "destination": destination,
            "date": when.strftime(PLANNER_DATE_FORMAT),
            "time": when.strftime(PLANNER_TIME_FORMAT),
        }
        return parse_trips(await self._execute(TRIP_PLANNER, variables), when)

    async def scheduled_trips(
        self,
        origin: str,
        destination: str,
        on: date | None = None,
    ) -> tuple[ScheduledTrip, ...]:
        """Return every timetabled journey between two stations for a day.

        The planner returns exactly three itineraries per call regardless of
        how much of the day remains, so this pages: it walks the window
        forward past the latest departure each page returned, until the day is
        covered. A single call would return four trains for a pair whose real
        service day has fifty-one.

        This is pure timetable data. The planner carries no realtime
        component -- see SPEC 2.4.

        :param origin: Origin station title.
        :param destination: Destination station title.
        :param on: Service date, defaulting to today.
        :raise NJTransitNotFoundError: No itineraries exist. Note this is also
            what an unrecognized station name produces.
        """
        service_date = on or now_local().date()
        cursor = datetime.combine(service_date, _PAGE_START, tzinfo=TZ)
        end_of_day = cursor.replace(hour=23, minute=59)

        found: dict[str, ScheduledTrip] = {}
        pages = 0

        while cursor <= end_of_day and pages < _MAX_PAGES:
            try:
                trips = await self._planner_page(origin, destination, cursor)
            except NJTransitNotFoundError:
                # Running off the end of the service day is the normal way
                # this loop finishes, not a failure -- but only once
                # something was found.
                if found:
                    break
                raise
            pages += 1

            if not trips:
                break

            for trip in trips:
                found.setdefault(trip.train_id, trip)

            latest = max(trip.departure for trip in trips)
            nudged = latest + timedelta(minutes=1)
            # Guard: a window whose itineraries all share a departure time
            # would otherwise re-request the same page forever.
            cursor = nudged if nudged > cursor else cursor + _PAGE_NUDGE

        if pages >= _MAX_PAGES:
            _LOGGER.warning(
                "Stopped paging %s -> %s at %d requests; schedule may be incomplete",
                origin,
                destination,
                _MAX_PAGES,
            )

        return tuple(sorted(found.values(), key=lambda trip: trip.departure))
