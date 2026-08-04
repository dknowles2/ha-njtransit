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
        self._seen = {
            departure.train_id: self._snapshot(departure, alerted)
            for departure in self._upcoming()
        }

    def _upcoming(self) -> list[Departure]:
        """Return this commute's departures inside the lookahead window."""
        horizon = now_local() + self._lookahead
        return [
            departure for departure in self.departures if departure.scheduled <= horizon
        ]

    def _alerted_trains(self) -> frozenset[str]:
        """Return trains named in a live incident, ignoring advisories."""
        alerts: tuple[SystemAlert, ...] = tuple(
            alert for alert in self.runtime.status.data or () if not alert.is_advisory
        )
        return frozenset(train_id for alert in alerts for train_id in alert.train_ids)

    def _snapshot(self, departure: Departure, alerted: frozenset[str]) -> _Seen:
        """Return the comparable facts about a departure."""
        delay = departure.delay_minutes
        return _Seen(
            cancelled=departure.status is TrainStatus.CANCELLED,
            track=departure.track,
            # Threshold-crossing, not "late at all". A train drifting 1 -> 2
            # minutes is not an event, and `None` means no realtime data yet,
            # which is not the same as on time.
            over_threshold=delay is not None and delay >= self._threshold,
            alerted=departure.train_id in alerted,
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        """Compare this poll against the last and fire what changed."""
        alerted = self._alerted_trains()
        current = {
            departure.train_id: (departure, self._snapshot(departure, alerted))
            for departure in self._upcoming()
        }
        previous = self._seen
        # Remember first, so an exception mid-loop cannot make the next poll
        # replay everything it already fired.
        self._seen = {train_id: seen for train_id, (_, seen) in current.items()}

        if previous is not None:
            for train_id, (departure, seen) in current.items():
                was = previous.get(train_id)
                if was is None:
                    # A train entering the lookahead window is not news; it is
                    # the clock moving. Only its later changes are.
                    continue
                self._fire_changes(departure, was, seen)

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
