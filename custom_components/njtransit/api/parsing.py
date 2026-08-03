"""Turn raw NJ Transit payloads into the models in :mod:`.models`.

Two themes run through everything here.

**Nothing raises on unrecognized input.** Every vocabulary this endpoint uses
is undocumented and assumed incomplete, so unknown statuses, colours and line
codes degrade to a sentinel while the raw value is preserved. A parser that
raises would take out the whole coordinator refresh over one odd board row.

**Times arrive as bare wall-clock strings** -- ``"8:25 AM"``, with no date, no
zone and no offset. Everything is resolved against ``America/New_York`` with
explicit midnight-rollover handling.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .models import (
    Car,
    CrowdLevel,
    Departure,
    DepartureBoard,
    RailLine,
    ScheduledTrip,
    Station,
    SystemAlert,
    TrainStatus,
)
from .queries import RAIL_ROUTE_TYPE, WALK_ROUTE_TYPE

TZ = ZoneInfo("America/New_York")

_CLOCK_FORMAT = "%I:%M %p"

# How far back a parsed time may fall before it is read as tomorrow's. A board
# fetched at 23:50 listing "12:05 AM" means the next day; one listing "11:40 PM"
# for a train that just left does not. Three hours separates the two without
# being so wide that a genuinely stale row rolls forward.
_ROLLOVER_GRACE = timedelta(hours=3)

# Matches "train 6612" and "train #6607" alike. `\w` rather than `\d` because
# train IDs are not numeric -- Trenton's board carries Amtrak "A79".
_TRAIN_RE = re.compile(r"\btrain\s+#?(\w{1,5})\b", re.IGNORECASE)

# Alerts often suggest an alternative: "Please take train #7877, the 9:14 PM
# departure...". That train is the remedy, not a casualty, and must not be
# reported as disrupted.
_SUBSTITUTE_MARKER = "please take"

_COUNTDOWN_RE = re.compile(r"\bin\s+(\d+)\s*min", re.IGNORECASE)

_STATUS_KEYWORDS: tuple[tuple[str, TrainStatus], ...] = (
    ("cancel", TrainStatus.CANCELLED),
    ("all aboard", TrainStatus.ALL_ABOARD),
    ("board", TrainStatus.BOARDING),
    ("depart", TrainStatus.DEPARTED),
    ("late", TrainStatus.DELAYED),
    ("delay", TrainStatus.DELAYED),
    ("on time", TrainStatus.ON_TIME),
)

_CROWD_COLORS = {
    "#0b6623": CrowdLevel.LIGHT,
    "#ffd300": CrowdLevel.MODERATE,
    "#d22b2b": CrowdLevel.HEAVY,
}

# The alert feed uses umbrella codes that cover more than one timetable line.
_LINE_UMBRELLAS = {
    "MNE": frozenset({"MNE", "MNEG"}),
    "BNTN": frozenset({"BNTN", "BNTNM"}),
    "MNBN": frozenset({"MNBN", "MNBNP"}),
    "NJCL": frozenset({"NJCL", "NJCLL"}),
}


def now_local() -> datetime:
    """Return the current time in the network's timezone."""
    return datetime.now(UTC).astimezone(TZ)


def resolve_time(value: str | None, reference: datetime) -> datetime | None:
    """Resolve a bare wall-clock string against a reference datetime.

    :param value: A time like ``"8:25 AM"``. Upstream sometimes pads with an
        extra space, which is tolerated.
    :param reference: The moment the payload was fetched, used to pick the
        date and to decide whether the time has rolled past midnight.
    :return: A timezone-aware datetime, or ``None`` if the value is empty or
        unparseable.
    """
    if not value or not value.strip():
        return None

    try:
        # DTZ007: deliberately naive. The payload carries no zone at all, so
        # there is nothing to parse one from -- only the hour and minute are
        # taken, and the zone comes from the reference below.
        clock = datetime.strptime(" ".join(value.split()), _CLOCK_FORMAT)  # noqa: DTZ007
    except ValueError:
        return None

    reference = reference.astimezone(TZ)
    resolved = reference.replace(
        hour=clock.hour,
        minute=clock.minute,
        second=0,
        microsecond=0,
    )

    # A time well behind the reference belongs to the following day. `fold=0`
    # leaves ambiguous times during the autumn DST transition resolving to the
    # first occurrence, which is the earlier and therefore safer reading.
    if resolved < reference - _ROLLOVER_GRACE:
        resolved += timedelta(days=1)
    return resolved


def parse_countdown(status: str | None) -> int | None:
    """Return the minutes in a board status like ``"in 21 Min"``.

    :return: ``None`` when the status is empty or carries no countdown, which
        is the normal state for departures more than about an hour out.
    """
    if not status:
        return None
    match = _COUNTDOWN_RE.search(status)
    return int(match.group(1)) if match else None


def parse_status(status: str | None) -> TrainStatus:
    """Normalize a board or stop-list status string.

    Matching is case-insensitive and substring-based because upstream is
    inconsistent -- ``Cancelled`` and ``CANCELLED`` appear in one response.
    A countdown like ``"in 4 Min"`` means the train is running normally.
    """
    if not status or not status.strip():
        return TrainStatus.UNKNOWN

    folded = status.casefold()
    for keyword, parsed in _STATUS_KEYWORDS:
        if keyword in folded:
            return parsed
    if _COUNTDOWN_RE.search(folded):
        return TrainStatus.ON_TIME
    return TrainStatus.UNKNOWN


def compute_delay(
    status: str | None,
    scheduled: datetime | None,
    reference: datetime,
) -> int | None:
    """Return how many minutes late a departure is running.

    The board counts down to *actual* departure while ``departureDate`` is the
    *scheduled* time, so the delay is the gap between them.

    :return: Minutes late, never negative. ``None`` when there is no realtime
        data to compare against -- which is not the same as zero.
    """
    countdown = parse_countdown(status)
    if countdown is None or scheduled is None:
        return None

    actual = reference.astimezone(TZ) + timedelta(minutes=countdown)
    delay = (actual - scheduled).total_seconds() / 60
    return max(0, round(delay))


def extract_train_ids(message: str | None) -> frozenset[str]:
    """Pull train IDs out of an alert's prose.

    Alert bodies name trains inconsistently -- ``train 6612`` and
    ``train #6607`` both occur -- and often suggest a substitute, which is
    excluded so it is not reported as disrupted.
    """
    if not message:
        return frozenset()

    head = re.split(_SUBSTITUTE_MARKER, message, flags=re.IGNORECASE)[0]
    return frozenset(_TRAIN_RE.findall(head))


def expand_line(abbreviation: str) -> frozenset[str]:
    """Map an alert's line code onto the timetable line codes it covers.

    The alert feed uses ``MNE`` as an umbrella over both the Morristown Line
    and the Gladstone Branch. Unknown codes map to themselves.
    """
    return _LINE_UMBRELLAS.get(abbreviation, frozenset({abbreviation}))


def parse_crowd_level(color: str | None) -> CrowdLevel:
    """Decode the board's per-car colour coding.

    Only green and yellow have been observed in the wild; red is assumed but
    unconfirmed, and anything else degrades rather than raising.
    """
    if not color:
        return CrowdLevel.UNKNOWN
    return _CROWD_COLORS.get(color.strip().casefold(), CrowdLevel.UNKNOWN)


def parse_cars(capacity: dict[str, Any] | None) -> tuple[Car, ...]:
    """Build a consist from a board row's ``capacity`` block.

    Absent capacity is normal -- the board only carries it for imminent
    departures -- and yields an empty tuple.
    """
    if not capacity:
        return ()

    cars: list[Car] = []
    for section in capacity.get("sections") or ():
        position = section.get("position") or ""
        for car in section.get("cars") or ():
            color = car.get("color") or ""
            cars.append(
                Car(
                    number=car.get("number") or "",
                    color=color,
                    level=parse_crowd_level(color),
                    position=position,
                )
            )
    return tuple(cars)


def parse_departure(item: dict[str, Any], reference: datetime) -> Departure | None:
    """Build a :class:`.Departure` from one board row.

    :return: ``None`` when the row has no train ID or no parseable departure
        time, since neither can be usefully surfaced.
    """
    train_id = (item.get("trainID") or "").strip()
    scheduled = resolve_time(item.get("departureDate"), reference)
    if not train_id or scheduled is None:
        return None

    status_raw = item.get("status") or ""
    track = (item.get("track") or "").strip()
    inline = (item.get("inlineMessage") or "").strip()

    return Departure(
        train_id=train_id,
        scheduled=scheduled,
        destination=(item.get("destination") or "").strip(),
        line=(item.get("line") or "").strip(),
        line_abbreviation=(item.get("lineAbbreviation") or "").strip(),
        status=parse_status(status_raw),
        status_raw=status_raw,
        track=track or None,
        delay_minutes=compute_delay(status_raw, scheduled, reference),
        inline_message=inline or None,
        cars=parse_cars(item.get("capacity")),
    )


def parse_board(
    station: str,
    payload: dict[str, Any] | None,
    reference: datetime,
) -> DepartureBoard:
    """Build a :class:`.DepartureBoard` from a departure-screens payload."""
    payload = payload or {}
    departures = [
        departure
        for item in payload.get("items") or ()
        if (departure := parse_departure(item, reference)) is not None
    ]
    banner = (payload.get("bannerMsg") or "").strip()
    fullscreen = (payload.get("fullScreenMsg") or "").strip()

    return DepartureBoard(
        station=station,
        departures=tuple(departures),
        banner_message=banner or None,
        fullscreen_message=fullscreen or None,
    )


def parse_alert(item: dict[str, Any]) -> SystemAlert:
    """Build a :class:`.SystemAlert` from one system-status entry."""
    message = item.get("message") or ""
    html = (item.get("msg_richtext") or "").strip()
    url = (item.get("msg_url") or "").strip()

    return SystemAlert(
        line_abbreviation=(item.get("abbreviation") or "").strip(),
        message=message,
        service=(item.get("service") or "").strip(),
        is_advisory=item.get("advisoryAlert") == "1",
        train_ids=extract_train_ids(message),
        message_html=html or None,
        url=url or None,
    )


def parse_alerts(payload: list[dict[str, Any]] | None) -> tuple[SystemAlert, ...]:
    """Build alerts from a system-status payload."""
    return tuple(parse_alert(item) for item in payload or ())


def parse_stations(payload: list[dict[str, Any]] | None) -> tuple[Station, ...]:
    """Build the canonical station list.

    Alias rows are preserved as-is; deduplication by
    :attr:`~.Station.penta_id` is the caller's decision, since the config flow
    wants one entry per station while name resolution wants every alias.
    """
    stations = []
    for item in payload or ():
        title = (item.get("title") or "").strip()
        penta_id = (item.get("pentaStationID") or "").strip()
        if not title or not penta_id:
            continue
        stations.append(
            Station(
                title=title,
                penta_id=penta_id,
                accessible=item.get("accessible"),
            )
        )
    return tuple(stations)


def parse_lines(payload: list[dict[str, Any]] | None) -> tuple[RailLine, ...]:
    """Build the rail line list."""
    return tuple(
        RailLine(
            id=(item.get("id") or "").strip(),
            title=(item.get("title") or "").strip(),
            abbreviation=(item.get("abbreviation") or "").strip(),
        )
        for item in payload or ()
        if (item.get("abbreviation") or "").strip()
    )


def _terminal_time(trip: dict[str, Any]) -> str | None:
    """Return when the itinerary actually ends.

    Deliberately not the last *rail* leg's arrival. A mixed-mode itinerary
    continues past its last train, so reading the rail leg would report
    reaching the destination at the moment you reach a transfer point
    instead. The observed case: train 880 Short Hills 6:10 PM to Hoboken
    6:45 PM, then a bus and a subway, arriving Penn Station at 7:27 PM. The
    rail-leg reading called that a 35-minute trip.

    Scans from the end because walking connectors carry a null
    ``offStopTime``, as does the sentinel leg when the planner appends one.
    """
    for leg in reversed(trip.get("legs") or ()):
        if time := (leg.get("offStopTime") or "").strip():
            return time
    return None


def _transport_legs(trip: dict[str, Any]) -> int:
    """Count the legs of an itinerary you actually ride.

    Not ``len(rail_legs)``: the point is to recognize a train-to-PATH or
    train-to-bus change as a transfer, and neither adds a rail leg.

    Two kinds of leg are skipped. Walking connectors are not ridden. And the
    planner appends a sentinel with no ``offStopDescription`` -- identified
    that way rather than by a null block, because an observed subway leg
    carried a block of ``""`` while being a real leg.
    """
    return sum(
        1
        for leg in trip.get("legs") or ()
        if leg.get("routeType") != WALK_ROUTE_TYPE
        and leg.get("offStopDescription") is not None
    )


def parse_trip(trip: dict[str, Any], reference: datetime) -> ScheduledTrip | None:
    """Build a :class:`.ScheduledTrip` from one planner itinerary.

    Only commuter-rail legs yield train IDs: itineraries may include walking
    connectors and PATH legs, and PATH carries block IDs from an unrelated
    namespace. The planner also appends a sentinel leg with a null block,
    which is skipped.

    The *times*, however, come from the whole itinerary -- see
    :func:`_terminal_time`.

    :return: ``None`` when the itinerary contains no rail leg, which happens
        for all-walking or PATH-only results.
    """
    rail_legs = [
        leg
        for leg in trip.get("legs") or ()
        if leg.get("routeType") == RAIL_ROUTE_TYPE and (leg.get("block") or "").strip()
    ]
    if not rail_legs:
        return None

    departure = resolve_time(rail_legs[0].get("onStopTime"), reference)
    arrival = resolve_time(_terminal_time(trip), reference)
    if departure is None or arrival is None:
        return None

    # A journey crossing midnight resolves its arrival to the previous day,
    # because each time is resolved independently against the same reference.
    if arrival < departure:
        arrival += timedelta(days=1)

    train_ids = tuple(leg["block"].strip() for leg in rail_legs)
    return ScheduledTrip(
        transport_legs=_transport_legs(trip),
        train_id=train_ids[0],
        departure=departure,
        arrival=arrival,
        duration=(trip.get("duration") or "").strip(),
        train_ids=train_ids,
    )


def parse_trips(
    payload: list[dict[str, Any]] | None,
    reference: datetime,
) -> tuple[ScheduledTrip, ...]:
    """Build itineraries from a trip-planner payload."""
    return tuple(
        trip
        for item in payload or ()
        if (trip := parse_trip(item, reference)) is not None
    )


# The board names lines by title ("Morristown Line"), the alert feed by code
# ("MNE"), and getTrainLines by a third pairing of the two. Titles match
# getTrainLines exactly for 12 of the 13 rail lines; the M&E main line is the
# exception, where the board says "Morristown Line" and getTrainLines says
# "Morris & Essex Line".
_LINE_TITLE_ALIASES = {
    "morristown line": "MNE",
}


def line_code_for_title(
    title: str,
    lines: Iterable[RailLine],
) -> str | None:
    """Map a board line title onto a timetable line code.

    :return: The line code, or ``None`` when the title is unrecognized.
        Callers should fail open on ``None`` -- a missed delay alert is worse
        than a noisy one.
    """
    if not title:
        return None

    folded = title.strip().casefold()
    for line in lines:
        if line.title.casefold() == folded:
            return line.abbreviation
    return _LINE_TITLE_ALIASES.get(folded)


def alert_line_codes(
    titles: Iterable[str],
    lines: Iterable[RailLine],
) -> frozenset[str]:
    """Return the alert-feed codes covering a set of board line titles.

    The alert feed uses umbrella codes, so a Gladstone Branch train
    (``MNEG``) is covered by ``MNE`` alerts.

    :return: Codes to match alerts against. **Empty means "do not filter"**,
        which happens when no title could be resolved.
    """
    resolved = set()
    line_list = list(lines)
    for title in titles:
        code = line_code_for_title(title, line_list)
        if code is None:
            continue
        resolved.add(code)
        # Walk up to the umbrella the alert feed actually uses.
        resolved.update(
            umbrella for umbrella, covered in _LINE_UMBRELLAS.items() if code in covered
        )
    return frozenset(resolved)
