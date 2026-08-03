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

    Two signals, and a departure qualifies on **either**:

    1. Its train is in the set resolved from the trip planner. This catches
       journeys requiring a transfer -- a Gladstone train to Summit,
       connecting onward -- whose board label reads ``Summit`` and which a
       label match alone would discard.
    2. Its destination label shares a significant word with the configured
       destination. Board labels are short forms (``New York -SEC`` for
       ``New York Penn Station``), so neither equality nor a substring test
       works.

    **The union matters, and it is not merely defensive.** Treating the
    planner set as authoritative and skipping the label check silently drops
    real trains whenever that set is stale, partial, or built before a
    timetable change -- and "silently drops a cancelled train" is precisely
    the failure this integration exists to prevent. The recorded disruption
    demonstrates it: train 6320 is labelled ``New York`` and cancelled, but a
    single planner page resolves only four trains and 6320 is not among them.

    Neither signal is complete, so neither gets a veto. With no destination
    configured, every departure qualifies.
    """
    if board is None:
        return []

    train_ids = route.train_ids if route is not None else frozenset()
    wanted = _significant_words(destination) if destination else frozenset()

    if not train_ids and not wanted:
        return list(board.departures)

    return [
        departure
        for departure in board.departures
        if departure.train_id in train_ids
        or (wanted & _significant_words(departure.destination))
    ]


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
