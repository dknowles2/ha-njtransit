"""Shared entity plumbing for the NJ Transit integration."""

from __future__ import annotations

import re

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api.models import Departure, DepartureBoard
from .const import DOMAIN
from .coordinator import (
    DepartureCoordinator,
    EntryRuntime,
    NJTransitConfigEntry,
    RouteData,
)

# Words that appear in so many station names they carry no signal. Without
# this, "Newark Penn Station" and "New York Penn Station" would match on
# "penn".
_NOISE_WORDS = frozenset({"station", "terminal", "penn", "the", "rail", "nj"})

_WORD_RE = re.compile(r"[a-z0-9]+")


def _significant_words(name: str) -> frozenset[str]:
    """Return the distinguishing words in a station name.

    Board labels are short forms of the canonical names -- ``New York -SEC``
    against ``New York Penn Station`` -- so comparison is on shared words
    rather than substrings.
    """
    return frozenset(_WORD_RE.findall(name.casefold())) - _NOISE_WORDS


def usable_departures(
    board: DepartureBoard | None,
    route: RouteData | None,
    destination: str | None,
) -> list[Departure]:
    """Return the departures that actually serve this commute.

    Preference order matters:

    1. The train IDs resolved from the trip planner. This is the good filter:
       it includes journeys requiring a transfer -- a Gladstone train to
       Summit, connecting onward -- whose board label reads ``Summit`` and
       which a label match would discard.
    2. A shared-word match against the board's destination label, used only
       when resolution failed. Board labels are short forms
       (``New York -SEC`` for ``New York Penn Station``), so neither equality
       nor a substring test works.
    3. Everything, when no destination is configured.
    """
    if board is None:
        return []

    if route is not None and route.train_ids:
        return [
            departure
            for departure in board.departures
            if departure.train_id in route.train_ids
        ]

    if destination:
        wanted = _significant_words(destination)
        return [
            departure
            for departure in board.departures
            if wanted & _significant_words(departure.destination)
        ]

    return list(board.departures)


class NJTransitEntity(CoordinatorEntity[DepartureCoordinator]):
    """Base for entities belonging to one commute."""

    _attr_has_entity_name = True

    def __init__(self, entry: NJTransitConfigEntry, key: str) -> None:
        """Initialize the entity."""
        super().__init__(entry.runtime_data.board)
        self.runtime: EntryRuntime = entry.runtime_data
        self._attr_unique_id = f"{entry.unique_id}-{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="NJ Transit",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url="https://www.njtransit.com/dv-to",
        )

    @property
    def departures(self) -> list[Departure]:
        """Return the departures serving this commute."""
        return usable_departures(
            self.coordinator.data,
            self.runtime.route.data,
            self.runtime.destination,
        )
