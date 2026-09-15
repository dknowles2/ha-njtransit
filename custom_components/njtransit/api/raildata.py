"""Client for NJ Transit's RailData API.

RailData is the documented, registration-gated API behind DepartureVision:
``https://raildata.njtransit.com/api/TrainData``. Every call is a form POST
carrying a token that ``getToken`` issues in exchange for a developer
account's username and password. Compared with the website's GraphQL
endpoint it is sturdier -- documented, versioned, with a compatibility
promise -- and it carries one thing the website cannot: ``getVehicleData``,
the signalling system's view of where every train is standing, which at New
York Penn says which platform a departure will leave from some ten minutes
before the board does (SPEC 2.9).

Three limits shape the design, all from the documentation:

* **``getToken`` may be called ten times a day**, and a token lasts 24
  hours. So the token is held in memory *and* handed to an injected store
  that survives restarts, and a fresh one is requested only when the held
  one is a day old or the API says it is invalid. A Home Assistant that
  restarts eleven times in a day must not lock itself out until midnight.
* **``getStationSchedule`` may be called five times a day.** It is the only
  source of a station's full day, which the route coordinator needs, so
  each station-day fetched is kept in the same store and never fetched
  twice. Two commutes between the same two stations cost two calls a day,
  restarts included.
* **Realtime calls are capped at 40,000 a day.** A board poll a minute is
  1,440; a signalling poll beside it doubles that. Comfortable, but not
  something to spend on faster polling than the feed refreshes.

Credentials are held only on this object. They are never logged, never put
in an exception message, and never written anywhere by this module; the
integration keeps them in the config entry the way Home Assistant keeps
every integration's credentials.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import replace
from datetime import date, datetime, timedelta
from typing import Any, Protocol

import aiohttp

from .circuits import has_decoder
from .exceptions import (
    NJTransitAPIError,
    NJTransitAuthError,
    NJTransitConnectionError,
    NJTransitError,
    NJTransitNotFoundError,
    NJTransitQuotaError,
)
from .models import (
    DepartureBoard,
    RailLine,
    ScheduledTrip,
    Station,
    SystemAlert,
    TrainRun,
    _same_station,
)
from .parsing import now_local
from .raildata_parsing import (
    LINES,
    ScheduleCall,
    join_schedules,
    parse_board,
    parse_messages,
    parse_schedule,
    parse_sightings,
    parse_stations,
    parse_train_run,
)

_LOGGER = logging.getLogger(__name__)

ENDPOINT = "https://raildata.njtransit.com/api/TrainData"

DEFAULT_TIMEOUT = 30.0

# A token is good for 24 hours. Renewing an hour early keeps a poll from
# landing on an expired one and spending a retry.
TOKEN_LIFETIME = timedelta(hours=23)

# How long the station list is trusted before being fetched again. Stations
# open on the order of years.
STATION_LIST_LIFETIME = timedelta(hours=24)

# Station-days older than this are dropped from the store. Yesterday's is
# kept because a service day runs past midnight (SPEC 2.9).
SCHEDULE_RETENTION = timedelta(days=1)

_STORE_TOKEN = "token"
_STORE_SCHEDULES = "schedules"

_CODE_RE = re.compile(r"^[A-Z]{2}$")


class RailDataStore(Protocol):
    """Where the client keeps what it must not fetch twice.

    Injected rather than owned, because persistence is the framework's job
    and this package must not import Home Assistant. The integration backs
    it with a :class:`homeassistant.helpers.storage.Store`; tests back it
    with a dict.
    """

    async def load(self) -> dict[str, Any] | None:
        """Return what was last saved, or ``None`` the first time."""

    async def save(self, data: dict[str, Any]) -> None:
        """Persist ``data``, replacing what was saved before."""


class RailDataClient:
    """Talks to NJ Transit's RailData API.

    Presents the same surface as :class:`~.client.NJTransitClient` -- see
    :class:`~.source.RailSource` -- so the integration can use either.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        username: str,
        password: str,
        store: RailDataStore | None = None,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        endpoint: str = ENDPOINT,
    ) -> None:
        """Initialize the client.

        :param session: Session to use. Injected rather than created so this
            module stays free of any framework's lifecycle.
        :param username: RailData developer account username.
        :param password: RailData developer account password.
        :param store: Where to keep the token and fetched schedules across
            restarts. Without one, both live only as long as this object.
        :param timeout: Per-request timeout in seconds.
        :param endpoint: The API base, overridable for the test environment
            NJ Transit asks developers to use first.
        """
        self._session = session
        self._username = username
        self._password = password
        self._store = store
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._endpoint = endpoint.rstrip("/")

        self._token: str | None = None
        self._token_issued: datetime | None = None
        self._token_lock = asyncio.Lock()

        self._data: dict[str, Any] | None = None
        self._data_lock = asyncio.Lock()

        self._stations: tuple[Station, ...] = ()
        self._stations_fetched: datetime | None = None
        self._codes: dict[str, str] = {}
        self._schedule_lock = asyncio.Lock()

    # -- persistence -------------------------------------------------------

    async def _stored(self) -> dict[str, Any]:
        """Return the persisted state, loading it on first use."""
        async with self._data_lock:
            if self._data is None:
                loaded = await self._store.load() if self._store else None
                self._data = dict(loaded) if isinstance(loaded, dict) else {}
            return self._data

    async def _persist(self) -> None:
        """Write the persisted state back, if there is somewhere to write."""
        if self._store is None or self._data is None:
            return
        await self._store.save(self._data)

    # -- tokens --------------------------------------------------------------

    def _token_usable(self, issued: datetime | None) -> bool:
        """Whether a token issued at ``issued`` should still be presented."""
        return issued is not None and now_local() - issued < TOKEN_LIFETIME

    async def _token_for_request(self) -> str:
        """Return a token, requesting one only when nothing held will do."""
        if self._token and self._token_usable(self._token_issued):
            return self._token

        async with self._token_lock:
            # Re-check under the lock: a concurrent caller may have renewed.
            if self._token and self._token_usable(self._token_issued):
                return self._token

            saved = (await self._stored()).get(_STORE_TOKEN)
            if isinstance(saved, dict):
                issued = _parse_iso(saved.get("issued"))
                value = saved.get("value")
                if isinstance(value, str) and value and self._token_usable(issued):
                    self._token, self._token_issued = value, issued
                    return value

            return await self._request_token()

    async def _request_token(self) -> str:
        """Exchange the credentials for a new token. Costs one of ten a day."""
        payload = await self._post(
            "getToken", {"username": self._username, "password": self._password}
        )
        if payload is None:
            # The documented answer for a missing username, password or
            # account. Not retryable, so not a connection error.
            raise NJTransitAuthError("getToken returned nothing for these credentials")
        if not isinstance(payload, dict):
            raise NJTransitAPIError(
                f"getToken returned {type(payload).__name__}, expected an object"
            )
        _raise_for_error_message("getToken", payload)

        if str(payload.get("Authenticated", "")).casefold() != "true":
            raise NJTransitAuthError("RailData rejected the username or password")
        token = payload.get("UserToken")
        if not isinstance(token, str) or not token:
            raise NJTransitAPIError("getToken authenticated but returned no token")

        issued = now_local()
        self._token, self._token_issued = token, issued
        data = await self._stored()
        data[_STORE_TOKEN] = {"value": token, "issued": issued.isoformat()}
        await self._persist()
        _LOGGER.debug("Obtained a new RailData token")
        return token

    async def _discard_token(self) -> None:
        """Forget a token the API has declared invalid."""
        self._token, self._token_issued = None, None
        data = await self._stored()
        if data.pop(_STORE_TOKEN, None) is not None:
            await self._persist()

    async def authenticate(self, *, fresh: bool = False) -> None:
        """Confirm the credentials work.

        :param fresh: Exchange the credentials now, even if a usable token is
            held. A held token proves nothing about a *newly entered*
            password, so a config flow asks for this; setup does not, and
            rides on whatever the store has.
        :raise NJTransitAuthError: They do not.
        :raise NJTransitQuotaError: Today's token requests are used up.
        :raise NJTransitConnectionError: The API was unreachable.
        """
        if not fresh:
            await self._token_for_request()
            return
        async with self._token_lock:
            await self._request_token()

    # -- transport -----------------------------------------------------------

    async def _post(self, method: str, form: dict[str, str]) -> Any:
        """POST a form and return the decoded JSON body.

        :raise NJTransitConnectionError: The endpoint was unreachable or
            answered with something that was not JSON.
        """
        try:
            async with self._session.post(
                f"{self._endpoint}/{method}",
                data=form,
                timeout=self._timeout,
            ) as response:
                # Errors come back as JSON with a 200, so read the body
                # before deciding anything.
                return await response.json(content_type=None)
        except aiohttp.ClientError as err:
            raise NJTransitConnectionError(
                f"{method} could not reach the endpoint: {err}"
            ) from err
        except TimeoutError as err:
            raise NJTransitConnectionError(f"{method} timed out") from err
        except ValueError as err:
            raise NJTransitConnectionError(
                f"{method} returned a non-JSON response"
            ) from err

    async def _call(self, method: str, **form: str) -> Any:
        """Run a token-bearing call and return its payload.

        A token the API declares invalid is discarded and replaced once. Any
        other error message is raised as-is, and a null body -- the
        documented answer to an empty token -- is treated the same way as
        the website's null payload: not found.

        :raise NJTransitQuotaError: A daily limit was hit.
        :raise NJTransitAPIError: The API answered with an error message.
        :raise NJTransitNotFoundError: The payload was null.
        """
        token = await self._token_for_request()
        payload = await self._post(method, {**form, "token": token})

        if _error_message(payload) and "invalid token" in _error_message(payload):
            _LOGGER.debug("RailData token rejected; requesting a new one")
            await self._discard_token()
            token = await self._token_for_request()
            payload = await self._post(method, {**form, "token": token})

        if isinstance(payload, dict):
            _raise_for_error_message(method, payload)
        if payload is None:
            raise NJTransitNotFoundError(f"{method} returned nothing")
        return payload

    # -- station names -------------------------------------------------------

    def remember(self, title: str, code: str) -> None:
        """Record that ``title`` is station ``code``.

        A config entry stores both the title it was set up with and the
        two-character code, and the code is the same identifier the website
        uses. Telling the client up front means a commute set up against the
        website's spelling (``Short Hills Station``) resolves without a
        station-list fetch, and without depending on the fuzzy match below.
        """
        self._codes[title.strip().casefold()] = code.strip().upper()

    async def _code_for(self, station: str) -> str:
        """Resolve a station title, or a code, to its two-character code.

        :raise NJTransitNotFoundError: Nothing matched.
        """
        key = station.strip().casefold()
        if key in self._codes:
            return self._codes[key]
        if _CODE_RE.match(station.strip().upper()):
            return station.strip().upper()

        stations = await self.stations()
        for candidate in stations:
            if candidate.title.casefold() == key:
                self._codes[key] = candidate.penta_id
                return candidate.penta_id
        # The website's list says "Short Hills Station" where this API says
        # "Short Hills"; compare on the words that distinguish stations.
        for candidate in stations:
            if _same_station(candidate.title, station) and _same_station(
                station, candidate.title
            ):
                self._codes[key] = candidate.penta_id
                return candidate.penta_id
        raise NJTransitNotFoundError(f"RailData does not know a station {station!r}")

    # -- the source interface ------------------------------------------------

    async def system_status(self) -> tuple[SystemAlert, ...]:
        """Return every current rail service message.

        ``getStationMSG`` with neither a station nor a line is read as the
        whole feed. RailData carries live messages only, so nothing here is
        ever a planned advisory.
        """
        payload = await self._call("getStationMSG", station="", line="")
        return parse_messages(payload if isinstance(payload, list) else None)

    async def departures(self, station: str) -> DepartureBoard:
        """Return a station's departure board.

        At a station whose track circuits are decoded, the signalling feed is
        read beside the board and any departure it shows standing on a
        platform gets that platform as ``signalled_track``. A failure there
        costs the signal, never the board: the board is the product and the
        signal is the bonus.

        :param station: A title from :meth:`stations`, a title the client
            was told about with :meth:`remember`, or a two-character code.
        :raise NJTransitNotFoundError: The station was not recognized.
        """
        code = await self._code_for(station)
        payload = await self._call("getTrainSchedule19Rec", station=code, line="")
        if not isinstance(payload, dict):
            raise NJTransitAPIError(
                f"getTrainSchedule19Rec returned {type(payload).__name__}, "
                "expected an object"
            )
        board = parse_board(station, code, payload)
        if not has_decoder(code) or not board.departures:
            return board

        try:
            vehicles = await self._call("getVehicleData")
        except NJTransitError as err:
            _LOGGER.debug("Signalling feed unavailable for %s: %s", station, err)
            return board

        sightings = {
            sighting.train_id: sighting
            for sighting in parse_sightings(
                code, vehicles if isinstance(vehicles, list) else None
            )
        }
        departures = []
        for departure in board.departures:
            sighting = sightings.get(departure.train_id)
            # A sighting names the train's scheduled origin departure. When
            # both sides have one they must agree, or the set on the
            # platform is a different run under the same number.
            if sighting is not None and (
                sighting.scheduled is None or sighting.scheduled == departure.scheduled
            ):
                departure = replace(departure, signalled_track=sighting.platform)
            departures.append(departure)
        return replace(board, departures=tuple(departures))

    async def stations(self) -> tuple[Station, ...]:
        """Return the station list, one row per station.

        Cached for a day. Unlike the website's list this one carries no
        alias rows, so it is already one entry per station.
        """
        fetched = self._stations_fetched
        if self._stations and fetched and now_local() - fetched < STATION_LIST_LIFETIME:
            return self._stations
        payload = await self._call("getStationList")
        stations = parse_stations(payload if isinstance(payload, list) else None)
        if not stations:
            raise NJTransitAPIError("getStationList returned no stations")
        self._stations, self._stations_fetched = stations, now_local()
        return stations

    async def train_lines(self) -> tuple[RailLine, ...]:
        """Return every rail line.

        RailData publishes its lines as an appendix rather than an endpoint,
        so this is a table. The codes are the website alert feed's, which
        keeps the alert sensors' line matching identical across sources.
        """
        return LINES

    async def train_run(self, train: str) -> TrainRun:
        """Return where a train is along its route.

        :raise NJTransitNotFoundError: The train is not running today. The
            endpoint answers an unknown or idle train with an empty stop
            list, and does not distinguish the two.
        """
        payload = await self._call("getTrainStopList", train=train)
        run = parse_train_run(train, payload if isinstance(payload, dict) else None)
        if not run.stops:
            raise NJTransitNotFoundError(f"getTrainStopList has no stops for {train}")
        return run

    async def scheduled_trips(
        self,
        origin: str,
        destination: str,
        on: date | None = None,
    ) -> tuple[ScheduledTrip, ...]:
        """Return every direct train between two stations for a day.

        Built by joining the two stations' schedules on train number, which
        finds one-seat rides only (SPEC 2.9). The schedule for a day is
        published from that day's midnight, so asking about tomorrow returns
        nothing rather than spending one of five daily calls on an answer
        the API does not have yet.

        :param on: Service date, defaulting to today.
        """
        service_date = on or now_local().date()
        if service_date != now_local().date():
            _LOGGER.debug("RailData has no schedule for %s yet", service_date)
            return ()

        origin_code = await self._code_for(origin)
        destination_code = await self._code_for(destination)
        origin_calls = await self._schedule(origin_code, service_date)
        destination_calls = await self._schedule(destination_code, service_date)
        return join_schedules(origin_calls, destination_calls)

    async def _schedule(
        self, code: str, service_date: date
    ) -> tuple[ScheduleCall, ...]:
        """Return one station's day, fetching it at most once."""
        key = f"{code}|{service_date.isoformat()}"
        async with self._schedule_lock:
            data = await self._stored()
            schedules = data.setdefault(_STORE_SCHEDULES, {})
            if not isinstance(schedules, dict):
                schedules = data[_STORE_SCHEDULES] = {}

            if key not in schedules:
                payload = await self._call(
                    "getStationSchedule", station=code, NJTOnly="true"
                )
                schedules[key] = _compact_schedule(
                    code, payload if isinstance(payload, list) else None
                )
                _prune_schedules(schedules, service_date)
                await self._persist()

            return parse_schedule(code, schedules[key])


def _error_message(payload: Any) -> str:
    """Return a payload's ``errorMessage``, folded, or an empty string."""
    if isinstance(payload, dict):
        message = payload.get("errorMessage")
        if isinstance(message, str):
            return message.casefold()
    return ""


def _raise_for_error_message(method: str, payload: dict[str, Any]) -> None:
    """Raise the exception an ``errorMessage`` calls for, if there is one."""
    message = _error_message(payload)
    if not message:
        return
    if "usage limit" in message:
        raise NJTransitQuotaError(f"{method}: {payload['errorMessage']}")
    raise NJTransitAPIError(f"{method}: {payload['errorMessage']}")


def _parse_iso(value: Any) -> datetime | None:
    """Parse a stored ISO timestamp, tolerating anything else."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


# The fields `parse_schedule` reads. A station's day is a few hundred rows,
# and only these survive into the store so it stays a few tens of kilobytes.
_SCHEDULE_FIELDS = (
    "TRAIN_ID",
    "SCHED_DEP_DATE",
    "DWELL_TIME",
    "DESTINATION",
    "LINE",
    "STATION_POSITION",
    "STOP_CODE",
)


def _compact_schedule(
    code: str, payload: list[dict[str, Any]] | None
) -> list[dict[str, Any]]:
    """Reduce a ``getStationSchedule`` payload to what is worth keeping."""
    kept = []
    for block in payload or ():
        if not isinstance(block, dict):
            continue
        if str(block.get("STATION_2CHAR", "")).strip().upper() != code:
            continue
        kept.append(
            {
                "STATION_2CHAR": code,
                "ITEMS": [
                    {field: item.get(field) for field in _SCHEDULE_FIELDS}
                    for item in block.get("ITEMS") or ()
                    if isinstance(item, dict)
                ],
            }
        )
    return kept


def _prune_schedules(schedules: dict[str, Any], today: date) -> None:
    """Drop station-days too old to be asked about again."""
    cutoff = today - SCHEDULE_RETENTION
    for key in list(schedules):
        _, _, stamp = key.partition("|")
        try:
            day = date.fromisoformat(stamp)
        except ValueError:
            del schedules[key]
            continue
        if day < cutoff:
            del schedules[key]
