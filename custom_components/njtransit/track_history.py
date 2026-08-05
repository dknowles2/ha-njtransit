"""Recording what the board does, so two questions can be settled with data.

**Which track.** Two days of this integration's own history showed 8 of 10 New
York Penn trains departing from a different track than the same train used the
previous weekday, with no disruption on either evening. The reason it may never
work is structural: at a terminal, departure track follows from which equipment
turned into the train and where it berthed, and SPEC 3.8 establishes that
arrival track is unavailable from this API at any price. Every reachable signal
is a proxy for a hidden variable. ``scripts/analyze_tracks.py`` scores
candidates against a bar set before the data existed -- 60% top-1.

**When the track is posted.** The more promising question, and it came from
watching rather than modelling: New York Penn posts NJ Transit tracks a median
of 9.0 minutes before departure with an interquartile range of *0.2 minutes*.
That is a scheduled process, not a tendency, which is why a regular traveller
can feel a deviation from it. The claim to test is that a track arriving late
predicts a bad commute.

The confound is the whole difficulty: ``assigned_at`` is measured against the
*scheduled* time, so a train already running late has its track posted late by
definition. That would make lateness a restatement of "your train is delayed"
rather than a warning. ``delay_at_assignment`` is what separates the two -- a
late assignment on a train still reported on time is the interesting case.

So a row is opened the first time a train is seen at all, not the first time it
has a track, and it carries how the train turned out. Trains that never get a
track are not noise to be filtered out; they are the sharpest form of the
question.

Recording is free: the whole board is already in ``coordinator.data`` every
poll and all but a handful of rows are discarded. Nothing here issues a
request.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any, Final

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .api.models import DepartureBoard
from .api.parsing import now_local
from .const import DOMAIN, TRACK_HISTORY_DAYS, TRACK_HISTORY_SAVE_DELAY

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION: Final = 1
STORAGE_KEY: Final = f"{DOMAIN}.track_history"

_SEPARATOR: Final = "|"


def day_key(station: str, service_date: date) -> str:
    """Return the storage key for one station's service day.

    Records are bucketed by day rather than held in one flat list so that the
    station name is stored once per day instead of once per departure, and so
    that pruning is a dictionary comprehension rather than a filter over
    everything ever seen.
    """
    return f"{station}{_SEPARATOR}{service_date.isoformat()}"


def split_key(key: str) -> tuple[str, str]:
    """Return the station and ISO date encoded in a storage key."""
    station, _, iso = key.rpartition(_SEPARATOR)
    return station, iso


class TrackHistory:
    """Every track assignment this integration has watched being made.

    One instance per Home Assistant, shared by every config entry, because the
    board is shared: two commutes out of the same station must not record the
    same assignment twice.
    """

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the recorder."""
        self._store = Store[dict[str, Any]](hass, STORAGE_VERSION, STORAGE_KEY)
        self._days: dict[str, list[dict[str, Any]]] = {}
        # (day key, train) -> the record in ``_days``, so an update is a
        # dictionary lookup rather than a scan of the day.
        self._index: dict[tuple[str, str], dict[str, Any]] = {}

    async def async_load(self) -> None:
        """Read stored history, dropping anything past the window."""
        stored = await self._store.async_load()
        days = (stored or {}).get("days", {})
        if not isinstance(days, dict):
            # Corrupt or hand-edited. Starting over loses collection time,
            # which is the cheapest thing here to lose.
            _LOGGER.warning("Track history was not readable; starting a new one")
            days = {}

        self._days = {key: list(records) for key, records in days.items()}
        self._prune()
        self._reindex()

    @callback
    def attach(
        self, coordinator: DataUpdateCoordinator[DepartureBoard]
    ) -> Callable[[], None]:
        """Record every successful poll of a board.

        :return: A callable that stops recording, for the caller to hold until
            the last commute using that station goes away.
        """

        @callback
        def _record() -> None:
            board = coordinator.data
            if board is not None:
                self.record(board)

        return coordinator.async_add_listener(_record)

    @callback
    def record(self, board: DepartureBoard) -> None:
        """Note every departure, and what became of it.

        A row is opened the first time a train is seen at all, not the first
        time it has a track. The trains that never get one are not noise to be
        filtered out -- they are the whole point of the question this data
        exists to answer, and recording only the assigned ones made the most
        interesting case invisible.
        """
        now = now_local()
        changed = False

        for departure in board.departures:
            key = day_key(board.station, departure.scheduled.date())
            ident = (key, departure.train_id)
            record = self._index.get(ident)

            if record is None:
                record = {
                    "train_id": departure.train_id,
                    "scheduled": departure.scheduled.strftime("%H:%M"),
                    "line": departure.line,
                    "track": departure.track,
                    # Whether this train was ever seen *without* a track. Only
                    # then does `assigned_at` mean anything: a train that
                    # already had one when Home Assistant started was assigned
                    # at an unknown time, and a plausible-looking guess there
                    # would corrupt the one measurement that matters.
                    "seen_trackless": departure.track is None,
                    "assigned_at": None,
                    "delay_at_assignment": None,
                    # Only set once a train is moved, so its absence means
                    # "assigned once and left alone" rather than "unknown".
                    "first_track": None,
                    "final_status": departure.status.value,
                    "final_delay": departure.delay_minutes,
                }
                self._days.setdefault(key, []).append(record)
                self._index[ident] = record
                changed = True
                continue

            if departure.track is None:
                record["seen_trackless"] = True
            elif record["track"] is None:
                # The assignment, caught in the act. `delay_at_assignment` is
                # what separates a late assignment that predicts trouble from
                # one that merely reflects a train already known to be late --
                # measured against the scheduled time, a delayed train's track
                # is posted late by definition.
                record["track"] = departure.track
                if record.get("seen_trackless"):
                    record["assigned_at"] = _seconds_before(departure.scheduled, now)
                    record["delay_at_assignment"] = departure.delay_minutes
                changed = True
            elif record["track"] != departure.track:
                # Keep the track the train actually left from as `track`, and
                # remember the original.
                if not record.get("first_track"):
                    record["first_track"] = record["track"]
                record["track"] = departure.track
                changed = True

            # The board drops a train once it has gone, so whatever was last
            # seen is how it turned out.
            if record.get("final_status") != departure.status.value:
                record["final_status"] = departure.status.value
                changed = True
            if record.get("final_delay") != departure.delay_minutes:
                record["final_delay"] = departure.delay_minutes
                changed = True

        if changed:
            self._prune()
            self._schedule_save()

    def days_for(self, station: str) -> dict[str, list[dict[str, Any]]]:
        """Return the recorded days for one station, keyed by ISO date."""
        days: dict[str, list[dict[str, Any]]] = {}
        for key, records in sorted(self._days.items()):
            name, iso = split_key(key)
            if name == station:
                days[iso] = records
        return days

    def summary(self, station: str) -> dict[str, Any]:
        """Return enough to see whether collection is working, at a glance."""
        days = self.days_for(station)
        records = [record for day in days.values() for record in day]
        reassigned = sum(1 for record in records if record.get("first_track"))
        timed = [
            record["assigned_at"]
            for record in records
            if record.get("assigned_at") is not None
        ]
        return {
            "days_collected": len(days),
            "observations": len(records),
            "distinct_trains": len({record["train_id"] for record in records}),
            "tracks_seen": sorted(
                {record["track"] for record in records if record.get("track")}
            ),
            "reassigned": reassigned,
            # The headline number for "how much lead time would a prediction
            # actually buy" -- how long before departure the board itself
            # answers the question.
            "median_assigned_at_seconds": _median(timed),
            "assignments_timed": len(timed),
            # Seen on the board and gone again without a track ever appearing.
            # The sharpest version of "the track never came", and invisible
            # until rows were opened on first sight rather than on assignment.
            "never_assigned": sum(1 for record in records if not record.get("track")),
            "cancelled": sum(
                1 for record in records if record.get("final_status") == "cancelled"
            ),
        }

    def as_dict(self) -> dict[str, Any]:
        """Return the stored shape."""
        return {"days": self._days}

    async def async_flush(self) -> None:
        """Write now rather than waiting out the coalescing delay.

        Called when the last commute goes away: Home Assistant only flushes a
        pending delayed save at shutdown, and an integration being removed and
        re-added inside that window would otherwise lose the days between.
        """
        await self._store.async_save(self.as_dict())

    def _prune(self) -> None:
        """Drop days outside the retention window."""
        cutoff = now_local().date() - timedelta(days=TRACK_HISTORY_DAYS)
        stale = {key for key in self._days if not _within(key, cutoff)}
        if not stale:
            return

        for key in stale:
            del self._days[key]
        self._index = {
            ident: record
            for ident, record in self._index.items()
            if ident[0] not in stale
        }

    def _reindex(self) -> None:
        """Rebuild the lookup after loading."""
        self._index = {
            (key, record["train_id"]): record
            for key, records in self._days.items()
            for record in records
            if "train_id" in record
        }

    def _schedule_save(self) -> None:
        """Queue a write.

        Delayed and coalesced: a busy board can produce assignments every poll,
        and this file is the largest thing the integration owns. Home Assistant
        flushes a delayed save on shutdown, so nothing is lost by waiting.
        """
        self._store.async_delay_save(self.as_dict, TRACK_HISTORY_SAVE_DELAY)


def _within(key: str, cutoff: date) -> bool:
    """Return whether a storage key's date is on or after the cutoff."""
    _, iso = split_key(key)
    try:
        return date.fromisoformat(iso) >= cutoff
    except ValueError:
        # An unparseable key cannot be aged out on any later pass either, so
        # dropping it now is what keeps it from accumulating forever.
        return False


def _seconds_before(scheduled: datetime, now: datetime) -> int:
    """Return how long before departure something happened, never negative."""
    return max(0, int((scheduled - now).total_seconds()))


def _median(values: list[int]) -> int | None:
    """Return the median, or ``None`` for no values."""
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) // 2
