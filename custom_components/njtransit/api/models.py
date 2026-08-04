"""Data models for the NJ Transit API.

Every vocabulary this endpoint uses is undocumented and assumed incomplete, so
the enums here all carry an ``UNKNOWN`` member and the dataclasses keep the
raw upstream value alongside the parsed one. Nothing in this module raises on
an unrecognized value.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class TrainStatus(StrEnum):
    """Normalized departure status.

    Upstream is inconsistent about casing -- ``Cancelled`` and ``CANCELLED``
    appear in a single response -- so matching is case-insensitive and
    anything unrecognized becomes :attr:`UNKNOWN` rather than raising.
    """

    ON_TIME = "on_time"
    DELAYED = "delayed"
    CANCELLED = "cancelled"
    BOARDING = "boarding"
    ALL_ABOARD = "all_aboard"
    DEPARTED = "departed"
    UNKNOWN = "unknown"


class CrowdLevel(StrEnum):
    """How full a car is, decoded from the board's colour coding."""

    LIGHT = "light"
    MODERATE = "moderate"
    HEAVY = "heavy"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Car:
    """A single rail car in a train's consist."""

    number: str
    """The car number, e.g. ``5558``."""

    color: str
    """The raw hex colour from the board, preserved for unknown values."""

    level: CrowdLevel
    """How full the car is."""

    position: str
    """``Front``, ``Middle`` or ``Back``."""


@dataclass(frozen=True)
class Departure:
    """One row of a station's departure board."""

    train_id: str
    """Train identifier. A string, not a number -- Trenton carries Amtrak
    services with IDs like ``A79``."""

    scheduled: datetime
    """Scheduled departure, timezone-aware."""

    destination: str
    """Headsign text, e.g. ``New York -SEC``. Short forms vary."""

    line: str
    """Human-readable line name, e.g. ``Morristown Line``."""

    line_abbreviation: str
    """Line code as the board reports it, e.g. ``M&E``."""

    status: TrainStatus
    """Normalized status."""

    status_raw: str
    """The board's own status text, e.g. ``in 21 Min``. Empty when no
    realtime data is available yet."""

    track: str | None = None
    """Platform, when assigned."""

    delay_minutes: int | None = None
    """Minutes behind schedule, or ``None`` when the board has no realtime
    data yet. ``None`` and ``0`` mean different things."""

    inline_message: str | None = None
    """Per-train note shown on the board, when present."""

    cars: tuple[Car, ...] = ()
    """Consist with crowding, when known. Empty for most rows -- the board
    only carries it for imminent departures."""

    @property
    def status_text(self) -> str:
        """Return one human-readable phrase for how this train is running.

        ``status`` and ``delay_minutes`` are separate fields, and neither is
        sufficient alone: the enum cannot say *how* late, and the delay is
        ``None`` for a cancelled train. Anything rendering a status has to
        combine them, so combine them once here rather than in every consumer.

        Empty when the board has no realtime data yet, which is normal for
        departures more than about an hour out. That is deliberately not
        ``"On time"`` -- nothing is known, which is not the same as knowing
        the train is punctual.
        """
        if self.status is TrainStatus.CANCELLED:
            return "Cancelled"
        if self.delay_minutes:
            return f"{self.delay_minutes} min late"
        if self.status is TrainStatus.BOARDING:
            return "Boarding"
        if self.status is TrainStatus.ALL_ABOARD:
            return "All aboard"
        if self.status is TrainStatus.DEPARTED:
            return "Departed"
        if self.delay_minutes == 0:
            return "On time"
        return ""

    @property
    def crowding(self) -> CrowdLevel:
        """Return the worst crowding level across the consist."""
        if not self.cars:
            return CrowdLevel.UNKNOWN
        order = (
            CrowdLevel.UNKNOWN,
            CrowdLevel.LIGHT,
            CrowdLevel.MODERATE,
            CrowdLevel.HEAVY,
        )
        return max((car.level for car in self.cars), key=order.index)


@dataclass(frozen=True)
class DepartureBoard:
    """A station's board, plus any station-wide messaging."""

    station: str
    """The station title this board was requested for."""

    departures: tuple[Departure, ...] = ()
    """Upcoming departures, in the order the board returned them."""

    banner_message: str | None = None
    """Station-wide banner, when set."""

    fullscreen_message: str | None = None
    """Station-wide takeover message, when set."""


@dataclass(frozen=True)
class SystemAlert:
    """One service alert from the system status feed."""

    line_abbreviation: str
    """Line code. Note this is an umbrella vocabulary -- ``MNE`` covers both
    the Morristown Line and the Gladstone Branch."""

    message: str
    """Plain-text alert body."""

    service: str
    """``Rail``, ``Light Rail`` or ``Bus``."""

    is_advisory: bool
    """``True`` for planned advisories (track work, event service), ``False``
    for live incidents. Inferred from ``advisoryAlert``; undocumented."""

    train_ids: frozenset[str] = field(default_factory=frozenset)
    """Train IDs named in the message body. Heuristically extracted, and
    empty for line-level alerts that name no train."""

    message_html: str | None = None
    """Rich-text variant, when upstream provides one."""

    url: str | None = None
    """Link to a fuller advisory page, when upstream provides one."""


@dataclass(frozen=True)
class Station:
    """A rail station from the canonical station list."""

    title: str
    """Display name, and valid input to both the board and the trip planner.
    Not unique -- several stations carry alias titles."""

    penta_id: str
    """Stable two-letter identifier, e.g. ``RT`` for Short Hills. This is the
    key; titles are display strings."""

    accessible: bool | None = None
    """Whether the station is accessible, when upstream says."""


@dataclass(frozen=True)
class RailLine:
    """A rail line."""

    id: str
    """Upstream UUID."""

    title: str
    """Display name, e.g. ``Morris & Essex Line``."""

    abbreviation: str
    """Line code, e.g. ``MNE``."""


@dataclass(frozen=True)
class ScheduledTrip:
    """One timetabled journey between two stations.

    Built from the trip planner, which is pure timetable data -- it carries no
    realtime component at all.
    """

    train_id: str
    """The first rail leg's train, i.e. the one to board."""

    departure: datetime
    """Scheduled departure from the origin, timezone-aware."""

    arrival: datetime
    """Scheduled arrival at the destination, timezone-aware."""

    duration: str
    """Upstream's own duration text, e.g. ``39 min``."""

    train_ids: tuple[str, ...] = ()
    """Every rail train in the itinerary, in order. Longer than one element
    when the journey requires a transfer."""

    transport_legs: int = 1
    """Legs that carry you, of any mode. Walking connectors and the planner's
    sentinel leg do not count.

    Distinct from ``len(train_ids)``, which counts only rail. A journey by
    train to Hoboken and PATH onward has one train and two transport legs,
    and is emphatically not a one-seat ride."""

    @property
    def has_transfer(self) -> bool:
        """Whether this journey requires changing vehicles.

        Deliberately not ``len(train_ids) > 1``: that reads a train-to-PATH
        or train-to-bus change as no change at all, because neither adds a
        rail leg.
        """
        return self.transport_legs > 1


@dataclass(frozen=True)
class Stop:
    """One station on a train's run."""

    name: str
    """Station name as the stop list reports it, e.g. ``Short Hills``.

    A fourth vocabulary: shorter than the canonical titles the config flow
    uses (``Short Hills Station``) and than the board's headsigns. Match
    loosely."""

    scheduled: datetime | None
    """Scheduled time at this stop, timezone-aware. ``None`` when unparseable."""

    departed: bool
    """Whether the train has left this stop."""

    status: TrainStatus
    """Per-stop status. ``Delayed`` on one stop and ``OnTime`` on the rest is
    normal -- it describes that stop, not the whole run."""


@dataclass(frozen=True)
class TrainRun:
    """Where a train is along its route.

    Built from the stop list, which is the only feed that says where a train
    actually *is* rather than when it is due.
    """

    train_id: str
    stops: tuple[Stop, ...] = ()

    @property
    def last_departed(self) -> Stop | None:
        """Return the most recent stop the train has left."""
        departed = [stop for stop in self.stops if stop.departed]
        return departed[-1] if departed else None

    @property
    def next_stop(self) -> Stop | None:
        """Return the stop the train is heading for."""
        return next((stop for stop in self.stops if not stop.departed), None)

    def stops_until(self, station: str) -> int | None:
        """Return how many stops remain before reaching ``station``.

        Zero means the train is between the previous stop and this one --
        it is the next call. ``None`` when the station is not on this run, or
        the train has already passed it.
        """
        remaining = [stop for stop in self.stops if not stop.departed]
        for index, stop in enumerate(remaining):
            if _same_station(stop.name, station):
                return index
        return None

    def due_at(self, station: str) -> datetime | None:
        """Return the scheduled time at ``station``, if it is on this run."""
        for stop in self.stops:
            if _same_station(stop.name, station):
                return stop.scheduled
        return None


def _same_station(stop_name: str, station: str) -> bool:
    """Compare a stop-list name against a configured station title.

    The stop list says ``Short Hills``; the config flow stores ``Short Hills
    Station``. Neither is a prefix of the other in every case, so compare on
    the significant words both forms share.

    ``penn`` is dropped alongside the generic suffixes: it distinguishes
    nothing between New York Penn and Newark Penn, and leaving it in lets a
    stop named only ``Penn Station`` match both. This mirrors the noise-word
    list the destination filter uses for the same reason.
    """
    trim = {"station", "terminal", "the", "penn"}

    def words(value: str) -> set[str]:
        return {word for word in re.findall(r"[a-z0-9]+", value.casefold())} - trim

    stop_words = words(stop_name)
    return bool(stop_words) and stop_words <= words(station)
