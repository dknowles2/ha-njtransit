"""Turn RailData payloads into the models in :mod:`.models`.

RailData is the documented API behind DepartureVision, so its board rows carry
the same vocabulary as the website's -- the same line titles, the same
``M&E`` abbreviations, the same status phrases -- and the same models fit
without translation. What differs is the envelope: upper-case keys, every
value a string, and times as full timestamps (``14-Sep-2026 09:32:00 PM``)
rather than the website's bare clock, which removes the midnight-rollover
guesswork in :mod:`.parsing` entirely.

The same two rules as :mod:`.parsing` apply. Nothing here raises on
unrecognized input, and the raw upstream value is kept wherever a model has a
place for it.
"""

from __future__ import annotations

import html
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from .circuits import decode_platform
from .models import (
    Car,
    Departure,
    DepartureBoard,
    RailLine,
    ScheduledTrip,
    Station,
    Stop,
    SystemAlert,
    TrainRun,
)
from .parsing import TZ, extract_train_ids, parse_crowd_level, parse_status

_TIMESTAMP_FORMAT = "%d-%b-%Y %I:%M:%S %p"

# The alert feed on the website names lines by a code (`MNE`, `NEC`) that the
# entity layer matches board titles against through `getTrainLines`.
# RailData names them by their long titles, so both halves are provided here:
# the line list a RailData client reports, and the mapping from the titles
# its messages carry onto the same codes. Keeping the codes identical across
# sources is what lets one alert sensor serve both.
LINES: tuple[RailLine, ...] = (
    RailLine(id="AC", title="Atlantic City Rail Line", abbreviation="ATLC"),
    RailLine(id="BC", title="Bergen County Line", abbreviation="MNBN"),
    RailLine(id="GS", title="Gladstone Branch", abbreviation="MNEG"),
    RailLine(id="ML", title="Main Line", abbreviation="MNBN"),
    RailLine(id="MC", title="Montclair-Boonton Line", abbreviation="BNTN"),
    RailLine(id="ME", title="Morris & Essex Line", abbreviation="MNE"),
    RailLine(id="NC", title="North Jersey Coast Line", abbreviation="NJCL"),
    RailLine(id="NE", title="Northeast Corridor", abbreviation="NEC"),
    RailLine(id="PV", title="Pascack Valley Line", abbreviation="PASC"),
    RailLine(id="PR", title="Princeton Branch", abbreviation="PRIN"),
    RailLine(id="RV", title="Raritan Valley Line", abbreviation="RARV"),
)
"""Rail lines as RailData's appendix lists them, coded as the website does."""

_ALERT_CODES: dict[str, str] = {
    "atlantic city line": "ATLC",
    "atlantic city rail line": "ATLC",
    "bergen county line": "MNBN",
    "main line": "MNBN",
    "main-bergen county line": "MNBN",
    "port jervis line": "MNBN",
    "gladstone branch": "MNEG",
    "me line": "MNE",
    "morris & essex line": "MNE",
    "morristown line": "MNE",
    "montclair-boonton line": "BNTN",
    "montclair boonton line": "BNTN",
    "north jersey coast line": "NJCL",
    "northeast corridor": "NEC",
    "northeast corridor line": "NEC",
    "pascack valley line": "PASC",
    "princeton branch": "PRIN",
    "raritan valley line": "RARV",
}

# Appendix II of the RailData documentation: a few stations report the
# railroad's own track number where the public signs say something else.
# Keyed by station code, then by the value the feed sends.
_TRACK_TRANSLATIONS: dict[str, dict[str, str]] = {
    "ON": {"single": "1"},
    "MP": {"2": "1"},
    "UV": {"b": "2", "single": "1"},
    "NA": {"0": "A"},
    "TS": {"4": "E", "2": "F", "3": "H", "1": "G"},
    "ST": {"single": "S"},
}

# Stops a train does not pick up at cannot start a journey, and a terminating
# stop cannot either. Appendix III and IV of the documentation.
_DISCHARGE_ONLY_STOP_CODES = frozenset({"D"})
_ORIGINATES = "0"
_TERMINATES = "2"

# No journey on this network takes longer. The schedule's 27-hour window can
# hold two runs of one train number a day apart, and without a bound the
# join would pair this morning's departure with tomorrow morning's.
_LONGEST_JOURNEY = timedelta(hours=6)


def parse_timestamp(value: str | None) -> datetime | None:
    """Parse a RailData timestamp like ``14-Sep-2026 09:32:00 PM``.

    :return: A timezone-aware datetime in the network's zone, or ``None``
        when the value is empty or unparseable.
    """
    if not value or not value.strip():
        return None
    try:
        # DTZ007: deliberately naive. The feed carries no zone; everything
        # NJ Transit publishes is Eastern, and the zone is attached below.
        naive = datetime.strptime(" ".join(value.split()), _TIMESTAMP_FORMAT)  # noqa: DTZ007
    except ValueError:
        return None
    return naive.replace(tzinfo=TZ)


def _text(item: dict[str, Any], key: str) -> str:
    """Return a stripped string field, tolerating nulls."""
    value = item.get(key)
    return str(value).strip() if value is not None else ""


def _seconds(value: str | None) -> int | None:
    """Parse a numeric string field, tolerating blanks."""
    if value is None:
        return None
    try:
        return int(str(value).strip())
    except ValueError:
        return None


def translate_track(station_code: str, track: str) -> str:
    """Return the track number as the platform signs show it."""
    return _TRACK_TRANSLATIONS.get(station_code.upper(), {}).get(
        track.casefold(), track
    )


def parse_delay(status_raw: str, sec_late: str | None) -> int | None:
    """Return how many minutes late a departure is running.

    ``SEC_LATE`` is populated for every row, including ones the board has no
    realtime data for yet, where it reads ``0`` or ``-60``. A blank status is
    what marks those rows, and for them the answer is ``None`` -- nothing is
    known, which is not the same as knowing the train is punctual. Floored
    to whole minutes, never negative, matching what the website's countdown
    arithmetic produces for the same train.
    """
    if not status_raw:
        return None
    seconds = _seconds(sec_late)
    if seconds is None:
        return None
    return max(0, seconds // 60)


def parse_cars(capacity: Iterable[dict[str, Any]] | None) -> tuple[Car, ...]:
    """Build a consist from a board row's ``CAPACITY`` block.

    The block is a list of vehicles, each with sections, each with cars; the
    website flattens the vehicle level away and so does this.
    """
    cars: list[Car] = []
    for vehicle in capacity or ():
        for section in vehicle.get("SECTIONS") or ():
            position = _text(section, "SECTION_POSITION")
            for car in section.get("CARS") or ():
                color = _text(car, "CUR_CAPACITY_COLOR")
                cars.append(
                    Car(
                        number=_text(car, "CAR_NO"),
                        color=color,
                        level=parse_crowd_level(color),
                        position=position,
                    )
                )
    return tuple(cars)


def parse_departure(item: dict[str, Any], station_code: str) -> Departure | None:
    """Build a :class:`.Departure` from one board row.

    :return: ``None`` when the row has no train ID or no parseable departure
        time, since neither can be usefully surfaced.
    """
    train_id = _text(item, "TRAIN_ID")
    scheduled = parse_timestamp(item.get("SCHED_DEP_DATE"))
    if not train_id or scheduled is None:
        return None

    status_raw = _text(item, "STATUS")
    track = _text(item, "TRACK")
    inline = _text(item, "INLINEMSG")

    return Departure(
        train_id=train_id,
        scheduled=scheduled,
        destination=_text(item, "DESTINATION"),
        line=_text(item, "LINE"),
        line_abbreviation=_text(item, "LINEABBREVIATION"),
        status=parse_status(status_raw),
        status_raw=status_raw,
        track=translate_track(station_code, track) if track else None,
        delay_minutes=parse_delay(status_raw, item.get("SEC_LATE")),
        inline_message=inline or None,
        cars=parse_cars(item.get("CAPACITY")),
    )


def _messages_of_type(
    messages: Iterable[dict[str, Any]] | None, kind: str
) -> str | None:
    """Join the station messages of one ``MSG_TYPE``, or ``None``."""
    texts = [
        _text(message, "MSG_TEXT")
        for message in messages or ()
        if _text(message, "MSG_TYPE").casefold() == kind and _text(message, "MSG_TEXT")
    ]
    return "\n".join(texts) or None


def parse_board(
    station: str,
    station_code: str,
    payload: dict[str, Any] | None,
) -> DepartureBoard:
    """Build a :class:`.DepartureBoard` from a ``getTrainSchedule`` payload.

    :param station: The title the board was asked for, kept on the model so
        it matches what the caller keyed on rather than the feed's own
        spelling of the name.
    :param station_code: The station's two-character code, for the track
        translations that a few stations need.
    """
    payload = payload or {}
    departures = [
        departure
        for item in payload.get("ITEMS") or ()
        if (departure := parse_departure(item, station_code)) is not None
    ]
    messages = payload.get("STATIONMSGS")
    return DepartureBoard(
        station=station,
        departures=tuple(departures),
        banner_message=_messages_of_type(messages, "banner"),
        fullscreen_message=_messages_of_type(messages, "fullscreen"),
    )


def _scopes(value: str | None) -> list[str]:
    """Split a ``MSG_LINE_SCOPE`` like ``*North Jersey Coast Line,*Main Line``."""
    if not value:
        return []
    return [
        part.strip().lstrip("*").strip()
        for part in value.split(",")
        if part.strip().lstrip("*").strip()
    ]


def parse_alert_codes(line_scope: str | None) -> frozenset[str]:
    """Return the alert-feed codes a message's line scope covers.

    Unknown titles are dropped rather than passed through: an unrecognized
    code would match no board line either way, and keeping it would make a
    diagnostics page read as though the alert feed had grown a new line.
    """
    return frozenset(
        code
        for scope in _scopes(line_scope)
        if (code := _ALERT_CODES.get(scope.casefold())) is not None
    )


def parse_messages(payload: list[dict[str, Any]] | None) -> tuple[SystemAlert, ...]:
    """Build alerts from a ``getStationMSG`` payload.

    One alert per line named in the message's scope, the way the website's
    feed repeats a multi-line alert once per line, so the per-commute alert
    sensors filter both sources identically. A message with no line scope --
    a station banner -- is kept with an empty line code, which the sensors
    read as "every line".

    Everything here is a live message. RailData has no planned-advisory
    flag, so ``is_advisory`` is always ``False`` and the advisories sensor
    stays at zero on this source (SPEC 2.9).
    """
    alerts: list[SystemAlert] = []
    seen: set[tuple[str, str]] = set()
    for item in payload or ():
        message = html.unescape(_text(item, "MSG_TEXT"))
        if not message:
            continue
        rich = _text(item, "MSG_RICHTEXT")
        url = _text(item, "MSG_URL")
        codes = sorted(parse_alert_codes(item.get("MSG_LINE_SCOPE"))) or [""]
        for code in codes:
            if (code, message) in seen:
                continue
            seen.add((code, message))
            alerts.append(
                SystemAlert(
                    line_abbreviation=code,
                    message=message,
                    service="Rail",
                    is_advisory=False,
                    train_ids=extract_train_ids(message),
                    message_html=rich or None,
                    url=url or None,
                )
            )
    return tuple(alerts)


def parse_stations(payload: list[dict[str, Any]] | None) -> tuple[Station, ...]:
    """Build the station list from ``getStationList``."""
    stations = []
    for item in payload or ():
        title = _text(item, "STATIONNAME")
        code = _text(item, "STATION_2CHAR").upper()
        if not title or not code:
            continue
        accessible = _text(item, "WHEELCHAIR_ACCESSIBLE").casefold()
        stations.append(
            Station(
                title=title,
                penta_id=code,
                accessible=(accessible == "true") if accessible else None,
            )
        )
    return tuple(stations)


def parse_stops(payload: list[dict[str, Any]] | None) -> tuple[Stop, ...]:
    """Build a train's stop list from ``getTrainStopList``.

    ``DEP_TIME`` is the scheduled time and ``TIME`` the feed's own estimate.
    The scheduled one is what :class:`.TrainRun` wants -- its lateness is
    measured as how far past the schedule the next stop has slipped, and an
    estimate that already includes the delay would read as always on time.
    """
    stops: list[Stop] = []
    for item in payload or ():
        name = _text(item, "STATIONNAME")
        if not name:
            continue
        stops.append(
            Stop(
                name=name,
                scheduled=parse_timestamp(item.get("DEP_TIME"))
                or parse_timestamp(item.get("TIME")),
                departed=_text(item, "DEPARTED").casefold() == "yes",
                status=parse_status(_text(item, "STOP_STATUS")),
            )
        )
    return tuple(stops)


def parse_train_run(train_id: str, payload: dict[str, Any] | None) -> TrainRun:
    """Build a :class:`.TrainRun` from ``getTrainStopList``."""
    payload = payload or {}
    return TrainRun(
        train_id=_text(payload, "TRAIN_ID") or train_id,
        stops=parse_stops(payload.get("STOPS")),
    )


@dataclass(frozen=True)
class ScheduleCall:
    """One train's timetabled call at one station."""

    train_id: str
    departs: datetime
    """Scheduled departure from this station."""
    arrives: datetime
    """Scheduled arrival, derived from the departure and the dwell time."""
    destination: str
    line: str
    position: str
    """``0`` originates here, ``1`` calls here, ``2`` terminates here."""
    stop_code: str

    @property
    def boardable(self) -> bool:
        """Whether a passenger can join the train at this stop."""
        return (
            self.position != _TERMINATES
            and self.stop_code not in _DISCHARGE_ONLY_STOP_CODES
        )

    @property
    def reachable(self) -> bool:
        """Whether a passenger can arrive here on this train."""
        return self.position != _ORIGINATES


def parse_schedule(
    station_code: str, payload: list[dict[str, Any]] | None
) -> tuple[ScheduleCall, ...]:
    """Build a station's day from ``getStationSchedule``.

    The endpoint answers with a list of stations, one when asked for one, so
    the block for the requested code is picked out rather than trusted to be
    first.
    """
    calls: list[ScheduleCall] = []
    for block in payload or ():
        if _text(block, "STATION_2CHAR").upper() != station_code.upper():
            continue
        for item in block.get("ITEMS") or ():
            train_id = _text(item, "TRAIN_ID")
            departs = parse_timestamp(item.get("SCHED_DEP_DATE"))
            if not train_id or departs is None:
                continue
            dwell = _seconds(item.get("DWELL_TIME")) or 0
            calls.append(
                ScheduleCall(
                    train_id=train_id,
                    departs=departs,
                    arrives=departs - timedelta(seconds=max(0, dwell)),
                    destination=_text(item, "DESTINATION"),
                    line=_text(item, "LINE"),
                    position=_text(item, "STATION_POSITION"),
                    stop_code=_text(item, "STOP_CODE").upper(),
                )
            )
    return tuple(sorted(calls, key=lambda call: call.departs))


def join_schedules(
    origin: Iterable[ScheduleCall], destination: Iterable[ScheduleCall]
) -> tuple[ScheduledTrip, ...]:
    """Return the one-seat rides between two stations' days.

    A train that calls at both, boardable at the origin and later at the
    destination, is a journey. Only direct trains fall out of this -- a
    transfer needs a planner, and RailData does not have one -- which is the
    filter SPEC 2.7 applies to the website's itineraries anyway. Where
    nothing runs direct the result is empty and the route coordinator falls
    back to label matching, so a branch-to-branch commute is not silently
    narrowed to nothing.
    """
    arrivals: dict[str, list[ScheduleCall]] = {}
    for call in destination:
        arrivals.setdefault(call.train_id, []).append(call)

    trips: list[ScheduledTrip] = []
    for call in origin:
        if not call.boardable:
            continue
        later = [
            arrival
            for arrival in arrivals.get(call.train_id, ())
            if arrival.reachable
            and call.departs < arrival.arrives <= call.departs + _LONGEST_JOURNEY
        ]
        if not later:
            continue
        arrival = min(later, key=lambda found: found.arrives)
        minutes = round((arrival.arrives - call.departs).total_seconds() / 60)
        trips.append(
            ScheduledTrip(
                train_id=call.train_id,
                departure=call.departs,
                arrival=arrival.arrives,
                duration=f"{minutes} min",
                train_ids=(call.train_id,),
                transport_legs=1,
            )
        )
    return tuple(sorted(trips, key=lambda trip: trip.departure))


@dataclass(frozen=True)
class Sighting:
    """A train the signalling system shows standing on a platform."""

    train_id: str
    platform: str
    scheduled: datetime | None
    """The train's scheduled departure from its origin, for matching a
    sighting to a board row rather than to yesterday's train of the same
    number."""


def parse_sightings(
    station_code: str, payload: list[dict[str, Any]] | None
) -> tuple[Sighting, ...]:
    """Return the trains ``getVehicleData`` shows on a platform at a station.

    Everything else in the feed -- trains elsewhere on the network, trains on
    a throat or a switch, stations without a decoder -- is dropped here, so
    the client only ever sees platform sightings it can act on.
    """
    sightings: list[Sighting] = []
    for item in payload or ():
        train_id = _text(item, "ID")
        platform = decode_platform(station_code, _text(item, "ICS_TRACK_CKT"))
        if not train_id or platform is None:
            continue
        sightings.append(
            Sighting(
                train_id=train_id,
                platform=platform,
                scheduled=parse_timestamp(item.get("SCHED_DEP_TIME")),
            )
        )
    return tuple(sightings)
