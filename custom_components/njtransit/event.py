"""Discrete things that happen to a train.

The binary sensor answers "is my commute broken right now?", which is a state.
It cannot express "another train just failed while it was already broken", so
every automation built on it has to diff the `reasons` attribute by hand to
find what is new.

An event entity is the right shape for that, and it also gives a home to the
one genuinely actionable change the board reports and nothing surfaces: a
track reassignment, which typically lands minutes before departure while you
are standing on the wrong platform.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api.models import Departure, SystemAlert, TrainStatus
from .api.parsing import now_local
from .const import (
    CONF_DELAY_THRESHOLD,
    CONF_LOOKAHEAD,
    DEFAULT_DELAY_THRESHOLD,
    DEFAULT_LOOKAHEAD,
)
from .coordinator import NJTransitConfigEntry
from .entity import NJTransitEntity

EVENT_CANCELLED = "cancelled"
EVENT_DELAYED = "delayed"
EVENT_TRACK_CHANGED = "track_changed"
EVENT_ALERTED = "alerted"
EVENT_LINE_CANCELLATION = "line_cancellation"
EVENT_TRACK_OVERDUE = "track_overdue"

# How far ahead of one of your trains a cancellation counts as likely to
# affect it. Long enough to catch the service immediately before yours,
# short enough that an unrelated cancellation an hour earlier does not.
KNOCK_ON_LEAD = timedelta(minutes=30)

# When a missing track stops being normal and starts being news.
#
# Measured, not guessed: over 125 New York Penn assignments, NJ Transit posts a
# track a median of 9.0 minutes before departure with a quartile range of 0.2
# minutes -- 8.9 to 9.1. It is a scheduled process rather than a tendency,
# which is what makes a deviation meaningful at all. Eight minutes sits below
# the first quartile, so this fires for roughly the slowest tenth.
#
# Amtrak is excluded from those figures and would wreck this threshold: it
# announces at a median of 13 minutes but leaves 16% until the departure
# minute itself, against 1% for NJ Transit.
TRACK_OVERDUE_LEAD = timedelta(minutes=8)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NJTransitConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the train event entity for a commute."""
    async_add_entities([TrainEvent(entry)])


@dataclass(frozen=True)
class _Seen:
    """What was last true of a train, so a change can be recognized."""

    cancelled: bool
    track: str | None
    over_threshold: bool
    alerted: bool
    # Defaulted so that adding a fact to this snapshot does not force every
    # construction to restate the ones it does not care about. Both production
    # call sites pass it explicitly.
    track_overdue: bool = False


class TrainEvent(NJTransitEntity, EventEntity):
    """Fires when something changes for a train on this commute.

    Only *transitions* fire. A train that is already cancelled does not
    re-fire on every poll, and nothing fires on the first update after a
    restart -- there is no "before" to compare against, and replaying a
    morning's problems at 3am because Home Assistant restarted is exactly the
    failure this is meant to remove from automations, not introduce.
    """

    _attr_translation_key = "train"

    def __init__(self, entry: NJTransitConfigEntry) -> None:
        """Initialize the entity."""
        super().__init__(entry, "train-event")
        # Set here rather than as a class attribute: EventEntity declares this
        # as an instance variable, so a list on the class trips RUF012.
        self._attr_event_types = [
            EVENT_CANCELLED,
            EVENT_DELAYED,
            EVENT_TRACK_CHANGED,
            EVENT_ALERTED,
            EVENT_LINE_CANCELLATION,
            EVENT_TRACK_OVERDUE,
        ]
        self._threshold = int(
            entry.options.get(CONF_DELAY_THRESHOLD, DEFAULT_DELAY_THRESHOLD)
        )
        self._lookahead = timedelta(
            minutes=int(entry.options.get(CONF_LOOKAHEAD, DEFAULT_LOOKAHEAD))
        )
        self._seen: dict[str, _Seen] | None = None

    async def async_added_to_hass(self) -> None:
        """Prime the baseline, and follow the alert feed as well."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self.runtime.status.async_add_listener(self._handle_coordinator_update)
        )

        # Record what is true at startup without firing for any of it. The
        # coordinator does not call the update handler on the initial add, so
        # without this the first poll afterwards would have nothing to compare
        # against and would silently swallow a cancellation that happened a
        # minute after Home Assistant booted.
        alerted = self._alerted_trains()
        publishes = self._publishes_tracks()
        self._seen = {
            departure.train_id: self._snapshot(departure, alerted, publishes=publishes)
            for departure, _ in self._watched()
        }

    def _upcoming(self) -> list[Departure]:
        """Return this commute's departures inside the lookahead window."""
        horizon = now_local() + self._lookahead
        return [
            departure for departure in self.departures if departure.scheduled <= horizon
        ]

    def _watched(self) -> list[tuple[Departure, str | None]]:
        """Return the departures to track, and why.

        Yours, plus trains on the same line from the same station that you
        *cannot* use. That second group is the point: a service you could
        have taken is already reported when it fails, but one you could not
        is exactly the one whose stops and passengers land on your train when
        it is cancelled.

        The board is fetched per station rather than per commute, so these
        rows are already in hand -- the destination filter simply discards
        them. No extra request, and no second commute to configure.
        """
        ours = self._upcoming()
        watched: list[tuple[Departure, str | None]] = [(d, None) for d in ours]

        board = self.coordinator.data
        if board is None or not ours:
            return watched

        our_ids = {departure.train_id for departure in ours}
        our_lines = {departure.line for departure in ours if departure.line}

        for departure in board.departures:
            if departure.train_id in our_ids or departure.line not in our_lines:
                continue
            # Only a train running ahead of yours can hand its stops over; one
            # behind cannot affect a train that has already gone.
            affected = next(
                (
                    mine.train_id
                    for mine in ours
                    if timedelta()
                    <= mine.scheduled - departure.scheduled
                    <= KNOCK_ON_LEAD
                ),
                None,
            )
            if affected is not None:
                watched.append((departure, affected))
        return watched

    def _publishes_tracks(self) -> bool:
        """Return whether this station is posting tracks at all right now.

        Without this, a station that never publishes -- or a feed that stops
        carrying the field -- would report every single train as overdue. The
        signal is "this train is late getting a track *while others are
        getting theirs*", which is only meaningful where others are.
        """
        board = self.coordinator.data
        if board is None:
            return False
        return any(departure.track for departure in board.departures)

    def _alerted_trains(self) -> frozenset[str]:
        """Return trains named in a live incident, ignoring advisories."""
        alerts: tuple[SystemAlert, ...] = tuple(
            alert for alert in self.runtime.status.data or () if not alert.is_advisory
        )
        return frozenset(train_id for alert in alerts for train_id in alert.train_ids)

    def _snapshot(
        self, departure: Departure, alerted: frozenset[str], *, publishes: bool
    ) -> _Seen:
        """Return the comparable facts about a departure."""
        delay = departure.delay_minutes
        cancelled = departure.status is TrainStatus.CANCELLED
        return _Seen(
            cancelled=cancelled,
            track=departure.track,
            # Threshold-crossing, not "late at all". A train drifting 1 -> 2
            # minutes is not an event, and `None` means no realtime data yet,
            # which is not the same as on time.
            over_threshold=delay is not None and delay >= self._threshold,
            alerted=departure.train_id in alerted,
            # A cancelled train is never getting a track, and saying so adds
            # nothing to having been told it is cancelled.
            track_overdue=(
                publishes
                and not cancelled
                and departure.track is None
                and departure.scheduled - now_local() <= TRACK_OVERDUE_LEAD
            ),
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        """Compare this poll against the last and fire what changed."""
        alerted = self._alerted_trains()
        publishes = self._publishes_tracks()
        current = {
            departure.train_id: (
                departure,
                self._snapshot(departure, alerted, publishes=publishes),
                affects,
            )
            for departure, affects in self._watched()
        }
        previous = self._seen
        # Remember first, so an exception mid-loop cannot make the next poll
        # replay everything it already fired.
        self._seen = {train_id: seen for train_id, (_, seen, _) in current.items()}

        if previous is not None:
            for train_id, (departure, seen, affects) in current.items():
                was = previous.get(train_id)
                if was is None:
                    # A train entering the lookahead window is not news; it is
                    # the clock moving. Only its later changes are.
                    continue
                if affects is None:
                    self._fire_changes(departure, was, seen)
                elif seen.cancelled and not was.cancelled:
                    # Everything else about someone else's train is noise --
                    # only losing it entirely reaches you.
                    self._fire(
                        EVENT_LINE_CANCELLATION, departure, affects_train=affects
                    )

        super()._handle_coordinator_update()

    def _fire_changes(self, departure: Departure, was: _Seen, now: _Seen) -> None:
        """Fire one event per thing that became true."""
        if now.cancelled and not was.cancelled:
            self._fire(EVENT_CANCELLED, departure)

        # A first assignment is not a change -- the board simply had no track
        # yet. The actionable case is being moved after one was published.
        if was.track is not None and now.track is not None and was.track != now.track:
            self._fire(EVENT_TRACK_CHANGED, departure, previous_track=was.track)

        # Cancelled already said the worse thing; "and also late" is noise.
        if now.over_threshold and not was.over_threshold and not now.cancelled:
            self._fire(EVENT_DELAYED, departure)

        if now.alerted and not was.alerted:
            self._fire(EVENT_ALERTED, departure)

        # Nothing is wrong on the board yet -- that is the point. The track is
        # simply not there when it should be, which a regular traveller reads
        # as trouble well before anything is announced. Whether it genuinely
        # leads a disruption or merely restates one is what `track_history`
        # is collecting the evidence to settle.
        if now.track_overdue and not was.track_overdue:
            self._fire(
                EVENT_TRACK_OVERDUE,
                departure,
                expected_by_minutes=int(TRACK_OVERDUE_LEAD.total_seconds() // 60),
            )

    def _fire(self, event_type: str, departure: Departure, **extra: Any) -> None:
        """Fire one event carrying enough context to act without a lookup."""
        self._trigger_event(
            event_type,
            {
                "train_id": departure.train_id,
                "scheduled": departure.scheduled.isoformat(),
                "destination": departure.destination,
                "track": departure.track,
                "status_text": departure.status_text,
                "delay_minutes": departure.delay_minutes,
                **extra,
            },
        )
        self.async_write_ha_state()
