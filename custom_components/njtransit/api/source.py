"""The interface every data source presents to the integration.

Two sources exist. :class:`~.client.NJTransitClient` talks to the private
GraphQL endpoint behind njtransit.com, anonymously.
:class:`~.raildata.RailDataClient` talks to the registration-gated RailData
API with a developer account. Everything above this layer -- coordinators,
entities, the card, the track history -- sees only the models in
:mod:`.models`, so it neither knows nor cares which one is answering.

This is a :class:`~typing.Protocol` rather than a base class because the two
clients share no transport, no error handling and no parsing; the only thing
they have in common is the questions they answer.
"""

from __future__ import annotations

from datetime import date
from typing import Protocol

from .models import (
    DepartureBoard,
    RailLine,
    ScheduledTrip,
    Station,
    SystemAlert,
    TrainRun,
)


class RailSource(Protocol):
    """What the integration asks of a data source."""

    async def system_status(self) -> tuple[SystemAlert, ...]:
        """Return every current service alert."""

    async def departures(self, station: str) -> DepartureBoard:
        """Return a station's departure board.

        :param station: A title from :meth:`stations`.
        :raise NJTransitNotFoundError: The station was not recognized.
        """

    async def stations(self) -> tuple[Station, ...]:
        """Return the station list, possibly with alias rows."""

    async def train_lines(self) -> tuple[RailLine, ...]:
        """Return every rail line, in the alert feed's vocabulary."""

    async def train_run(self, train: str) -> TrainRun:
        """Return where a train is along its route.

        :raise NJTransitNotFoundError: The train is not running today.
        """

    async def scheduled_trips(
        self,
        origin: str,
        destination: str,
        on: date | None = None,
    ) -> tuple[ScheduledTrip, ...]:
        """Return every timetabled journey between two stations for a day.

        :raise NJTransitNotFoundError: No itineraries exist.
        """
