"""Scheduled departures as a calendar.

Backed by the paged trip planner, which is pure timetable data -- it carries
no realtime component at all. So this is what is *scheduled* to run, and a
cancelled train still appears as an event. Cancellations are folded into the
summary for departures close enough to show on the live board, which is as
much realtime as there is to apply.
"""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api.models import ScheduledTrip, TrainStatus
from .api.parsing import now_local
from .coordinator import NJTransitConfigEntry
from .entity import NJTransitEntity

CANCELLED_PREFIX = "CANCELLED — "


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NJTransitConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the calendar for a commute."""
    if entry.runtime_data.destination:
        async_add_entities([DepartureCalendar(entry)])


class DepartureCalendar(NJTransitEntity, CalendarEntity):
    """Timetabled departures for one commute."""

    _attr_translation_key = "departures"

    def __init__(self, entry: NJTransitConfigEntry) -> None:
        """Initialize the calendar."""
        super().__init__(entry, "calendar")
        self._destination = entry.runtime_data.destination or ""

    async def async_added_to_hass(self) -> None:
        """Also follow the schedule, which is a different coordinator."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self.runtime.route.async_add_listener(self.async_write_ha_state)
        )

    @property
    def _trips(self) -> tuple[ScheduledTrip, ...]:
        """Return the resolved timetable for this commute."""
        route = self.runtime.route.data
        return route.trips if route else ()

    def _cancelled_trains(self) -> frozenset[str]:
        """Return trains the live board currently shows as cancelled.

        Only covers the board's own window -- roughly the next couple of
        hours. Beyond that there is no realtime signal to apply.
        """
        board = self.coordinator.data
        if board is None:
            return frozenset()
        return frozenset(
            departure.train_id
            for departure in board.departures
            if departure.status is TrainStatus.CANCELLED
        )

    def _event_for(
        self, trip: ScheduledTrip, cancelled: frozenset[str]
    ) -> CalendarEvent:
        """Build a calendar event for one timetabled journey."""
        summary = f"Train {trip.train_id} to {self._destination}"
        if trip.train_id in cancelled:
            summary = f"{CANCELLED_PREFIX}{summary}"

        description = (
            f"Scheduled journey time {trip.duration}." if trip.duration else ""
        )
        if trip.has_transfer:
            changes = " then ".join(trip.train_ids)
            description = f"{description} Change trains: {changes}.".strip()

        return CalendarEvent(
            start=trip.departure,
            end=trip.arrival,
            summary=summary,
            description=description or None,
            location=self.runtime.origin,
            # Stable across refreshes so subscribers do not see every event
            # disappear and come back each day.
            uid=f"{self.unique_id}-{trip.departure.date().isoformat()}-{trip.train_id}",
        )

    @property
    def event(self) -> CalendarEvent | None:
        """Return the next departure as an event."""
        now = now_local()
        cancelled = self._cancelled_trains()
        for trip in self._trips:
            if trip.arrival >= now:
                return self._event_for(trip, cancelled)
        return None

    async def async_get_events(
        self,
        hass: HomeAssistant,
        start_date: datetime,
        end_date: datetime,
    ) -> list[CalendarEvent]:
        """Return events overlapping a range.

        Only today and tomorrow are resolved, so a month view shows those two
        days and nothing else. That is a real limitation and it is upstream's:
        the trip planner returns three itineraries per call, so covering a
        month would take roughly 700 requests.
        """
        cancelled = self._cancelled_trains()
        return [
            self._event_for(trip, cancelled)
            for trip in self._trips
            if trip.departure < end_date and trip.arrival > start_date
        ]
