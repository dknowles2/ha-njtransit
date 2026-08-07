"""Sensors for the NJ Transit integration."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api.models import CrowdLevel, Departure, SystemAlert, TrainRun
from .api.parsing import alert_line_codes, now_local
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
            FavoriteDepartureSensor(entry),
            ProgressSensor(entry),
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

        return _details(
            departures[self._index], self.favorites, self.runtime.status.data
        )


def _details(
    departure: Departure,
    favorites: frozenset[str],
    alerts: tuple[SystemAlert, ...] | None,
) -> dict[str, Any]:
    """Return the attribute payload for one departure.

    Shared so the favourite sensor reports exactly what the numbered
    departure sensors do -- an automation should not have to care which
    entity it read a train from.
    """
    return {
        "train_id": departure.train_id,
        "favorite": departure.train_id.upper() in favorites,
        "destination": departure.destination,
        "line": departure.line,
        "track": departure.track,
        "status": departure.status.value,
        "status_raw": departure.status_raw,
        "status_text": departure.status_text,
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
            for alert in alerts or ()
            if departure.train_id.upper() in alert.train_ids
        ],
    }


class FavoriteDepartureSensor(NJTransitEntity, SensorEntity):
    """When the next train you actually catch leaves.

    Distinct from ``next_departure``, which is whichever usable train is
    soonest. Someone with a fixed routine cares about *their* train, and a
    lock-screen countdown for a service they were never going to board is
    noise.

    ``None`` when no favourite runs again today, and when no favourites are
    configured at all -- an empty list means "not using this", not "every
    train qualifies".
    """

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_translation_key = "next_favorite"

    def __init__(self, entry: NJTransitConfigEntry) -> None:
        """Initialize the sensor."""
        super().__init__(entry, "next-favorite")

    @property
    def _next(self) -> Departure | None:
        """Return the soonest upcoming favourite, if any."""
        if not self.favorites:
            return None
        return next(
            (d for d in self.departures if d.train_id.upper() in self.favorites),
            None,
        )

    @property
    def native_value(self) -> datetime | None:
        """Return the scheduled departure of the next favourite."""
        departure = self._next
        return departure.scheduled if departure else None

    @property
    def _commute(self) -> dict[str, Any]:
        """Return facts about the commute rather than about one departure.

        `favorites` was already here for the same reason: this is the entity an
        automation is pointed at, so it is where anything the automation needs
        about the commute as a whole has to be readable.

        The coordinates are what let a countdown be suppressed when you are
        nowhere near the station it is counting down to. They are absent rather
        than zeroed when unknown -- `0, 0` is a real place in the Atlantic, and
        a distance measured against it would be silently enormous, which reads
        as "you are never close enough" rather than "this is not known".
        """
        commute: dict[str, Any] = {"favorites": sorted(self.favorites)}
        coordinates = self.runtime.origin_coordinates
        if coordinates is not None:
            commute["origin_latitude"] = coordinates[0]
            commute["origin_longitude"] = coordinates[1]
        return commute

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return the same details the numbered departure sensors report."""
        departure = self._next
        if departure is None:
            return self._commute
        return {
            **_details(departure, self.favorites, self.runtime.status.data),
            **self._commute,
        }


class ProgressSensor(NJTransitEntity, SensorEntity):
    """How far away your favourite train is, in stops.

    The board says when a train is *due*. Only the stop list says where it
    actually *is*, which is the difference between "the 7:33 is 4 late" and
    "the 7:33 has just left Summit, one stop away".

    Zero means this station is the next call -- the train is between the
    previous stop and here. ``None`` when it has already passed, is not
    running, or no favourite is close enough to be worth following.
    """

    _attr_native_unit_of_measurement = "stops"
    _attr_translation_key = "stops_away"

    def __init__(self, entry: NJTransitConfigEntry) -> None:
        """Initialize the sensor."""
        super().__init__(entry, "stops-away")
        # The worst lateness seen on the run being followed, and which run it
        # belongs to. Held because the evidence is destroyed as the journey
        # goes: a stop is only visibly overdue while the train has not reached
        # it, and the stop list carries no actual departure times. The moment
        # a late stop flips to departed, the next stop is still in the future
        # and a stateless estimate snaps back to "on time".
        #
        # That matters most on the longest leg. Newark Broad Street to New
        # York Penn is 22 minutes of a morning run with no intermediate stop
        # to be overdue for, so a train that arrived at Newark ten late would
        # have spent the whole final approach claiming a punctual arrival --
        # during exactly the stretch someone is deciding whether they will
        # make a connection.
        self._peak_late: tuple[str, int] | None = None

    async def async_added_to_hass(self) -> None:
        """Also follow the progress coordinator, which polls separately."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self.runtime.progress.async_add_listener(self._handle_progress_update)
        )

    @callback
    def _handle_progress_update(self) -> None:
        """Note how late the train has ever looked, then write state."""
        run = self.runtime.progress.data
        if run is None:
            self._peak_late = None
        else:
            observed = run.minutes_late(now_local())
            if observed is not None:
                train, peak = self._peak_late or (run.train_id, 0)
                if train != run.train_id:
                    peak = 0
                self._peak_late = (run.train_id, max(peak, observed))
        self._handle_coordinator_update()

    def _lateness(self, run: TrainRun, now: datetime) -> int | None:
        """Return the worst lateness seen on this run, or what is visible now."""
        observed = run.minutes_late(now)
        if self._peak_late is None or self._peak_late[0] != run.train_id:
            return observed
        if observed is None:
            return None
        return max(observed, self._peak_late[1])

    @property
    def native_value(self) -> int | None:
        """Return how many stops out the train is from this origin."""
        run = self.runtime.progress.data
        if run is None:
            return None
        return run.stops_until(self.runtime.origin)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return where the train is and when it is due."""
        run = self.runtime.progress.data
        if run is None:
            return None

        now = now_local()
        last = run.last_departed
        upcoming = run.next_stop
        destination = self.runtime.destination
        late = self._lateness(run, now)
        due_origin = run.due_at(self.runtime.origin)
        due_destination = run.due_at(destination) if destination else None
        eta_destination = (
            due_destination + timedelta(minutes=late)
            if due_destination is not None and late is not None
            else None
        )
        to_destination = run.stops_until(destination) if destination else None

        # Whether the journey has started. The origin falling behind the train
        # is the only observable that says so -- and once it does, the
        # departure board has already dropped this train, so everything below
        # is the only account of it left.
        on_board = (
            destination is not None
            and run.stops_until(self.runtime.origin) is None
            and to_destination is not None
        )

        return {
            "train_id": run.train_id,
            "last_departed": last.name if last else None,
            "next_stop": upcoming.name if upcoming else None,
            "due_at_origin": due_origin.isoformat() if due_origin else None,
            "due_at_destination": (
                due_destination.isoformat() if due_destination else None
            ),
            # Scheduled arrival moved by however late it is running. The
            # schedule alone stops being an answer at exactly the moment
            # someone starts asking.
            "eta_at_destination": (
                eta_destination.isoformat() if eta_destination else None
            ),
            "minutes_late": late,
            "stops_to_destination": to_destination,
            "on_board": on_board,
            "stops_total": len(run.stops),
            "stops_remaining": [stop.name for stop in run.stops if not stop.departed],
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
        # Both sides upper-cased before intersecting, the same convention
        # favourite matching uses. `extract_train_ids` normalizes the alert
        # side; the board side is normalized here because nothing guarantees
        # upstream is consistent between the two feeds either.
        ours = (
            {departure.train_id.upper() for departure in board.departures}
            if board
            else set()
        )

        return {
            "messages": [alert.message for alert in matching],
            "urls": [alert.url for alert in matching if alert.url],
            "lines": sorted({alert.line_abbreviation for alert in matching}),
            "train_ids": sorted(named),
            "affects_my_trains": sorted(named & ours),
        }
