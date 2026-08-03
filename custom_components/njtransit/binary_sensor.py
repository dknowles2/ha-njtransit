"""The merged disruption signal.

This is the entity the integration exists for. Neither NJ Transit feed is a
superset of the other: during the recorded disruption, the alert feed named
trains 309, 6311, 6324 and 6607 while the board simultaneously showed 6320
cancelled and said nothing about it anywhere else. Watching either feed alone
misses real problems.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
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


def _clock(when: datetime) -> str:
    """Format a departure time for a human-readable reason.

    `%-I` would be tidier but is not portable, so the leading zero is
    stripped by hand.
    """
    return when.strftime("%I:%M %p").lstrip("0")


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NJTransitConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the disruption sensor for a commute."""
    async_add_entities([DisruptionSensor(entry)])


class DisruptionSensor(NJTransitEntity, BinarySensorEntity):
    """Whether this commute is broken right now.

    Three independent conditions, any of which trips it:

    1. A train is cancelled on the board.
    2. A train is running at or beyond the delay threshold.
    3. A train is named in a live alert.

    The third is the one a departure board alone cannot give you, and the
    first is the one an alert feed alone cannot. Both are needed.
    """

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_translation_key = "disrupted"

    def __init__(self, entry: NJTransitConfigEntry) -> None:
        """Initialize the sensor."""
        super().__init__(entry, "disrupted")
        self._threshold = int(
            entry.options.get(CONF_DELAY_THRESHOLD, DEFAULT_DELAY_THRESHOLD)
        )
        self._lookahead = timedelta(
            minutes=int(entry.options.get(CONF_LOOKAHEAD, DEFAULT_LOOKAHEAD))
        )

    async def async_added_to_hass(self) -> None:
        """Also follow the alert feed, which is a different coordinator."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self.runtime.status.async_add_listener(self._handle_status_update)
        )

    @callback
    def _handle_status_update(self) -> None:
        """Refresh when the alert feed changes."""
        self.async_write_ha_state()

    def _upcoming(self) -> list[Departure]:
        """Return this commute's departures inside the lookahead window."""
        horizon = now_local() + self._lookahead
        return [
            departure for departure in self.departures if departure.scheduled <= horizon
        ]

    def _live_alerts(self) -> tuple[SystemAlert, ...]:
        """Return current live incidents, ignoring planned advisories."""
        return tuple(
            alert for alert in self.runtime.status.data or () if not alert.is_advisory
        )

    def _reasons(self) -> list[str]:
        """Return human-readable reasons this commute is disrupted."""
        alerts = self._live_alerts()
        reasons: list[str] = []

        for departure in self._upcoming():
            when = _clock(departure.scheduled)

            if departure.status is TrainStatus.CANCELLED:
                reasons.append(f"Train {departure.train_id} ({when}) is cancelled")
                continue

            delay = departure.delay_minutes
            if delay is not None and delay >= self._threshold:
                reasons.append(
                    f"Train {departure.train_id} ({when}) is {delay} minutes late"
                )
                continue

            # Only reached when the board looks fine, which is exactly the
            # case a board-only integration gets wrong.
            for alert in alerts:
                if departure.train_id in alert.train_ids:
                    reasons.append(
                        f"Train {departure.train_id} ({when}): {alert.message}"
                    )
                    break

        return reasons

    @property
    def is_on(self) -> bool:
        """Return whether the commute is disrupted."""
        return bool(self._reasons())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return why, and which trains are affected."""
        reasons = self._reasons()
        upcoming = self._upcoming()
        alerts = self._live_alerts()

        affected = [
            departure.train_id
            for departure in upcoming
            if departure.status is TrainStatus.CANCELLED
            or (
                departure.delay_minutes is not None
                and departure.delay_minutes >= self._threshold
            )
            or any(departure.train_id in alert.train_ids for alert in alerts)
        ]

        return {
            "reasons": reasons,
            "affected_trains": affected,
            "upcoming_trains": [departure.train_id for departure in upcoming],
            "delay_threshold": self._threshold,
            "lookahead_minutes": int(self._lookahead.total_seconds() // 60),
        }
