"""Recording track assignments, so a prediction can be evaluated later.

This records; it does not predict. The distinction is deliberate. Two days of
this integration's own recorder history showed 8 of 10 New York Penn trains
departing from a different track than the same train used the previous
weekday, with no service disruption on either evening -- barely above chance
across the ten tracks NJ Transit actually uses there. Shipping a predictor
built on that would be shipping a coin flip with a confidence percentage
printed next to it.

The honest reason it may be weak is structural. At a terminal, departure track
is decided by which equipment turns into the train and where it berthed, and
SPEC 3.8 establishes that arrival track is unavailable from this API at any
price. Every signal reachable from a departure board is a proxy for that
hidden variable.

So this collects evidence against a pre-registered bar -- 60% top-1 accuracy
at New York Penn -- and ``scripts/analyze_tracks.py`` scores candidate models
against it offline. If nothing clears the bar, the answer is to ship exclusion
hints or nothing, decided on data rather than on how much work went into the
collection.

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
        # Trains seen on the board *before* they had a track. Only for those
        # can `assigned_at` be trusted -- see `record`.
        self._trackless: set[tuple[str, str]] = set()

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
        """Note the track of every row that has one."""
        now = now_local()
        changed = False

        for departure in board.departures:
            key = day_key(board.station, departure.scheduled.date())
            ident = (key, departure.train_id)

            if departure.track is None:
                # Watching a train go from no track to a track is the only way
                # to know *when* it was assigned. A train that already had one
                # the first time we looked -- because Home Assistant started
                # mid-window -- gets a null `assigned_at` rather than a
                # plausible-looking wrong one.
                self._trackless.add(ident)
                continue

            existing = self._index.get(ident)
            if existing is None:
                self._days.setdefault(key, []).append(
                    record := {
                        "train_id": departure.train_id,
                        "track": departure.track,
                        "scheduled": departure.scheduled.strftime("%H:%M"),
                        "line": departure.line,
                        "assigned_at": (
                            _seconds_before(departure.scheduled, now)
                            if ident in self._trackless
                            else None
                        ),
                        # Only set once a train is moved, so its absence means
                        # "assigned once and left alone" rather than "unknown".
                        "first_track": None,
                    }
                )
                self._index[ident] = record
                changed = True
            elif existing["track"] != departure.track:
                # Keep the track the train actually left from as `track`, and
                # remember the original. How often Penn reassigns is itself
                # part of what makes a prediction worth having or not.
                if not existing.get("first_track"):
                    existing["first_track"] = existing["track"]
                existing["track"] = departure.track
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
            "tracks_seen": sorted({record["track"] for record in records}),
            "reassigned": reassigned,
            # The headline number for "how much lead time would a prediction
            # actually buy" -- how long before departure the board itself
            # answers the question.
            "median_assigned_at_seconds": _median(timed),
            "assignments_timed": len(timed),
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
        self._trackless = {ident for ident in self._trackless if ident[0] not in stale}

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
