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
of 8.8 minutes before departure with an interquartile range of *1.9 minutes*
(n=236; an early 125-observation sample read 0.2 and was wrong).
That is a regular process rather than a coincidence, which is why a traveller
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

**Outcomes are kept, not sampled.** Each row holds the worst it ever looked
rather than the last thing seen, because the last sighting is systematically
the emptiest: a train counts down "15 min late" for half an hour and then goes
ALL ABOARD with no delay field, so a last-wins rule records nothing. That is
not hypothetical -- it erased every delay in the first three days of
collection.

One limit no recording strategy fixes: **a terminal publishes no lateness at
all.** At New York Penn every row's status is either blank or ``BOARDING``,
because there is no arrival to count down; ``in 21 Min`` is something a through
station says. So ``delay_at_assignment`` -- the field that separates a warning
from a restatement -- is unobtainable there from this feed, and the outcome has
to be recovered by matching the train against a downstream station's board,
which is why both ends of a commute are worth recording.

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

from .api.models import DepartureBoard, TrainStatus
from .api.parsing import now_local
from .const import DOMAIN, TRACK_HISTORY_DAYS, TRACK_HISTORY_SAVE_DELAY

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION: Final = 1
STORAGE_KEY: Final = f"{DOMAIN}.track_history"

_SEPARATOR: Final = "|"

# What counts as a train having gone wrong, for the summary only. Matches the
# analysis script rather than the disruption sensor's configurable threshold:
# this is a fixed yardstick for "was the outcome bad", not a user preference.
LATE_ENOUGH_TO_COUNT: Final = 5


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
        # When the next write is owed. Held here rather than left to
        # `async_delay_save`, whose timer restarts on every call.
        self._save_due: datetime | None = None

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
                    "worst_delay": departure.delay_minutes,
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
                #
                # No `seen_trackless` check here, though the timing is only
                # trustworthy when it holds: reaching this line *means* it
                # does. A row is only created with a null track when the train
                # was seen without one, and a track is never cleared once set,
                # so a null track here always came with `seen_trackless`. The
                # guard that used to sit here could not fail, which read as
                # though a case existed that does not.
                record["track"] = departure.track
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

            # How it turned out. The board drops a train once it has gone, so
            # the temptation is to keep whatever was seen last -- but the last
            # sighting is the *least* informative one. A train counts down "15
            # min late" for half an hour, flips to ALL ABOARD with no delay
            # field at all, and a last-wins rule overwrites the measurement
            # with nothing. Observed: train 6666 fired a `delayed` event at 15
            # minutes and was recorded as `final_delay: None`, and across three
            # days not one row of 169 carried a delay. The outcome half of this
            # dataset was erasing itself.
            #
            # So each field keeps what it learned rather than what it saw last.
            if _outranks(departure.status.value, record.get("final_status")):
                record["final_status"] = departure.status.value
                changed = True

            delay = departure.delay_minutes
            if delay is not None:
                # Last known, not last seen: how late it was when it actually
                # left, ignoring the blank that follows.
                if record.get("final_delay") != delay:
                    record["final_delay"] = delay
                    changed = True
                # And the worst it ever looked, which is the honest answer to
                # "did this train go wrong" -- a train can be twenty minutes
                # late and then stop being reported at all.
                worst = record.get("worst_delay")
                if worst is None or delay > worst:
                    record["worst_delay"] = delay
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
            # The outcome side, reported here because its absence is invisible
            # otherwise: every model of "a late track predicts a bad commute"
            # needs bad commutes to have been recorded, and for three days none
            # were. A run of zeroes against a non-zero `observations` means the
            # board is publishing no lateness at this station -- which is what
            # a terminal does, and is a fact about the station rather than a
            # fault to chase.
            "ran_late": sum(
                1
                for record in records
                if (record.get("worst_delay") or 0) >= LATE_ENOUGH_TO_COUNT
            ),
            "delays_seen": sum(
                1 for record in records if record.get("worst_delay") is not None
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
        self._save_due = None
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
        # `async_delay_save` restarts its timer on every call, so calling it
        # from a handler that fires more often than the delay means it never
        # fires at all -- the write only happens at shutdown, and a crash loses
        # everything since boot. That is exactly what happened here once
        # `final_delay` began updating on almost every poll: 22 minutes of
        # recording with a 10-minute delay left the file untouched.
        #
        # So the deadline is held here and only the *remaining* time is handed
        # over. Each call reschedules toward the same fixed instant rather than
        # pushing it further away.
        now = now_local()
        if self._save_due is None:
            self._save_due = now + timedelta(seconds=TRACK_HISTORY_SAVE_DELAY)

        remaining = (self._save_due - now).total_seconds()
        if remaining <= 0:
            self._save_due = None
            remaining = 0

        self._store.async_delay_save(self.as_dict, remaining)


def _within(key: str, cutoff: date) -> bool:
    """Return whether a storage key's date is on or after the cutoff."""
    _, iso = split_key(key)
    try:
        return date.fromisoformat(iso) >= cutoff
    except ValueError:
        # An unparseable key cannot be aged out on any later pass either, so
        # dropping it now is what keeps it from accumulating forever.
        return False


def _outranks(new: str, current: str | None) -> bool:
    """Return whether a status should replace the one already recorded.

    Two statuses must not be lost to a later, vaguer sighting. A cancelled
    train stays cancelled however the board decorates the row afterwards, and
    ``unknown`` is the board declining to say anything -- which is not the same
    as saying the train is fine, and must not overwrite something that was.
    """
    if new == current:
        return False
    if current == TrainStatus.CANCELLED.value:
        return False
    return new != TrainStatus.UNKNOWN.value or current is None


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
