"""Snapshot tests for every entity this integration creates.

The per-platform tests assert specific behaviour. These assert the *whole*
surface at once: entity ids, names, device classes, units, and every attribute
value, all frozen against the recorded disruption capture.

That catches the class of change the behavioural tests miss -- an entity
silently disappearing, an attribute being renamed, a unit changing -- which
for a Home Assistant integration means someone's automation breaks without a
single test failing.

Regenerate deliberately, never reflexively:

    uv run pytest tests/test_snapshots.py --snapshot-update
"""

from __future__ import annotations

from datetime import datetime

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)
from syrupy.assertion import SnapshotAssertion

from custom_components.njtransit.api.parsing import TZ
from custom_components.njtransit.const import DOMAIN

from .conftest import install_api_mock
from .test_init import HOBOKEN, make_entry, setup_entry

CAPTURED_AT = datetime(2026, 8, 3, 8, 20, tzinfo=TZ)


@pytest.fixture(name="at_capture_time", autouse=True)
async def at_capture_time_fixture(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """Freeze the clock, and put Home Assistant in the network's timezone.

    Departure sensors hold timestamps resolved against "now", so without the
    freeze every snapshot differs on every run.

    The timezone matters for readability: Home Assistant renders calendar
    times in its own configured zone, which defaults to US/Pacific in tests.
    Snapshots would otherwise show a New Jersey train leaving at 06:30 when it
    leaves at 09:30, which reads as a bug every time someone opens the file.
    """
    await hass.config.async_set_time_zone("America/New_York")
    freezer.move_to(CAPTURED_AT)


def entity_states(hass: HomeAssistant) -> dict[str, dict[str, object]]:
    """Return every entity this integration created, in a stable order."""
    registry = er.async_get(hass)
    entries = [
        entry for entry in registry.entities.values() if entry.platform == DOMAIN
    ]

    states: dict[str, dict[str, object]] = {}
    for entry in sorted(entries, key=lambda entry: entry.entity_id):
        state = hass.states.get(entry.entity_id)
        if state is None:
            states[entry.entity_id] = {"state": "<missing>"}
            continue
        states[entry.entity_id] = {
            "state": state.state,
            "attributes": dict(sorted(state.attributes.items())),
            "unique_id": entry.unique_id,
        }
    return states


async def test_all_entities(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    snapshot: SnapshotAssertion,
) -> None:
    """Every entity for a commute, against the recorded disruption."""
    install_api_mock(aioclient_mock)
    await setup_entry(hass, make_entry())

    assert entity_states(hass) == snapshot


async def test_entities_without_a_destination(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    snapshot: SnapshotAssertion,
) -> None:
    """A commute with no destination gets no calendar and an unfiltered board."""
    install_api_mock(aioclient_mock)
    await setup_entry(hass, make_entry(destination=None))

    assert entity_states(hass) == snapshot


async def test_two_commutes_from_one_origin(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    snapshot: SnapshotAssertion,
) -> None:
    """Both commutes get a full, distinct entity set.

    They share a departure-board poll, which must not cause them to share
    entities or collide on unique IDs.
    """
    install_api_mock(aioclient_mock)
    await setup_entry(hass, make_entry())
    await setup_entry(hass, make_entry(destination=HOBOKEN, destination_id="HB"))

    assert entity_states(hass) == snapshot
