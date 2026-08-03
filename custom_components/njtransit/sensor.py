"""Sensors for the NJ Transit integration."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api.models import CrowdLevel, SystemAlert
from .api.parsing import alert_line_codes
from .const import (
    CONF_DEPARTURE_COUNT,
    DEFAULT_DEPARTURE_COUNT,
    MAX_DEPARTURE_COUNT,
)
from .coordinator import NJTransitConfigEntry
from .entity import NJTransitEntity

RAIL_SERVICE = "Rail"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NJTransitConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the sensors for a commute."""
    count = min(
        int(entry.options.get(CONF_DEPARTURE_COUNT, DEFAULT_DEPARTURE_COUNT)),
        MAX_DEPARTURE_COUNT,
    )

    async_add_entities(
        [
            *(DepartureSensor(entry, index) for index in range(count)),
            DelaySensor(entry),
            CrowdingSensor(entry),
            AlertSensor(entry, advisories=False),
            AlertSensor(entry, advisories=True),
        ]
    )


class DepartureSensor(NJTransitEntity, SensorEntity):
    """When the Nth usable train leaves.

    "Usable" means it serves the configured destination, so the index is
    stable in a way a raw board index is not: ``departure_2`` is always the
    second train you could actually take, not whatever happens to be second on
    the screen.
    """

    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, entry: NJTransitConfigEntry, index: int) -> None:
        """Initialize the sensor."""
        super().__init__(entry, f"departure-{index}")
        self._index = index
        if index:
            self._attr_translation_key = "departure"
            self._attr_translation_placeholders = {"index": str(index + 1)}
        else:
            self._attr_translation_key = "next_departure"

    @property
    def native_value(self) -> datetime | None:
        """Return the scheduled departure.

        ``None`` when no more trains run tonight. That is unknown, not
        unavailable -- the integration is working, there simply is no train.
        """
        departures = self.departures
        if self._index >= len(departures):
            return None
        return departures[self._index].scheduled

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return details of this departure."""
        departures = self.departures
        if self._index >= len(departures):
            return None

        departure = departures[self._index]
        return {
            "train_id": departure.train_id,
            "destination": departure.destination,
            "line": departure.line,
            "track": departure.track,
            "status": departure.status.value,
            "status_raw": departure.status_raw,
            "delay_minutes": departure.delay_minutes,
            "inline_message": departure.inline_message,
            "crowding": departure.crowding.value,
            "cars": [
                {
                    "number": car.number,
                    "position": car.position,
                    "crowding": car.level.value,
                }
                for car in departure.cars
            ],
            "alerts": [
                alert.message
                for alert in self.runtime.status.data or ()
                if departure.train_id in alert.train_ids
            ],
        }


class DelaySensor(NJTransitEntity, SensorEntity):
    """How late the next usable train is running."""

    _attr_device_class = SensorDeviceClass.DURATION
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_translation_key = "delay"

    def __init__(self, entry: NJTransitConfigEntry) -> None:
        """Initialize the sensor."""
        super().__init__(entry, "delay")

    @property
    def native_value(self) -> int | None:
        """Return minutes late.

        ``None`` when the board has no realtime data for this train yet, which
        is normal for departures more than about an hour out. Reporting zero
        would claim the train is on time when nothing is actually known.
        """
        departures = self.departures
        if not departures:
            return None
        return departures[0].delay_minutes


class CrowdingSensor(NJTransitEntity, SensorEntity):
    """How full the next usable train is."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_translation_key = "crowding"

    def __init__(self, entry: NJTransitConfigEntry) -> None:
        """Initialize the sensor."""
        super().__init__(entry, "crowding")
        # Set here rather than as a class attribute: SensorEntity declares
        # this as an instance variable, so a ClassVar override fails mypy.
        self._attr_options = [level.value for level in CrowdLevel]

    @property
    def native_value(self) -> str | None:
        """Return the worst crowding level across the consist."""
        departures = self.departures
        if not departures:
            return None
        return departures[0].crowding.value

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return crowding by position, so "sit at the back" is answerable."""
        departures = self.departures
        if not departures or not departures[0].cars:
            return None

        by_position: dict[str, list[str]] = {}
        for car in departures[0].cars:
            by_position.setdefault(car.position.casefold(), []).append(car.level.value)
        return {"positions": by_position}


class AlertSensor(NJTransitEntity, SensorEntity):
    """Service alerts for the lines this commute runs on.

    Live incidents and planned advisories are separate entities, because they
    want different reactions: one means leave now, the other means remember it
    next weekend.
    """

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "alerts"

    def __init__(self, entry: NJTransitConfigEntry, *, advisories: bool) -> None:
        """Initialize the sensor."""
        super().__init__(entry, "advisories" if advisories else "alerts")
        self._advisories = advisories
        self._attr_translation_key = "advisories" if advisories else "alerts"

    async def async_added_to_hass(self) -> None:
        """Also follow the status feed, which is a different coordinator."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self.runtime.status.async_add_listener(self._handle_status_update)
        )

    @callback
    def _handle_status_update(self) -> None:
        """Refresh when the alert feed changes."""
        self.async_write_ha_state()

    @property
    def _line_codes(self) -> frozenset[str]:
        """Return the alert-feed codes covering this commute's lines.

        Empty means no line could be resolved, in which case every rail alert
        is reported. Failing open is deliberate: a missed delay alert is worse
        than a noisy one, and the line vocabularies do not line up cleanly
        across feeds.
        """
        board = self.coordinator.data
        if board is None:
            return frozenset()

        static = self.runtime.static.data
        lines = static.lines if static else ()
        return alert_line_codes(
            {departure.line for departure in board.departures}, lines
        )

    def _matching(self) -> list[SystemAlert]:
        """Return the alerts relevant to this commute."""
        codes = self._line_codes
        return [
            alert
            for alert in self.runtime.status.data or ()
            if alert.service == RAIL_SERVICE
            and alert.is_advisory is self._advisories
            and (not codes or alert.line_abbreviation in codes)
        ]

    @property
    def native_value(self) -> int:
        """Return how many alerts are current."""
        return len(self._matching())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the alert text, and which of our trains it names."""
        matching = self._matching()
        named = {train_id for alert in matching for train_id in alert.train_ids}
        board = self.coordinator.data
        ours = (
            {departure.train_id for departure in board.departures} if board else set()
        )

        return {
            "messages": [alert.message for alert in matching],
            "urls": [alert.url for alert in matching if alert.url],
            "lines": sorted({alert.line_abbreviation for alert in matching}),
            "train_ids": sorted(named),
            "affects_my_trains": sorted(named & ours),
        }
