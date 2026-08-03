"""Diagnostics output."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.njtransit.api.parsing import TZ
from custom_components.njtransit.diagnostics import (
    async_get_config_entry_diagnostics,
)

from .conftest import install_api_mock
from .test_init import make_entry, setup_entry

CAPTURED_AT = datetime(2026, 8, 3, 8, 20, tzinfo=TZ)


@pytest.fixture(name="at_capture_time", autouse=True)
def at_capture_time_fixture(freezer: FrozenDateTimeFactory) -> None:
    """Freeze the clock at the moment the fixtures were recorded."""
    freezer.move_to(CAPTURED_AT)


async def diagnostics_for(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> dict[str, Any]:
    """Set up a commute and return its diagnostics."""
    install_api_mock(aioclient_mock)
    entry = make_entry()
    await setup_entry(hass, entry)
    return await async_get_config_entry_diagnostics(hass, entry)


async def test_reports_the_configured_commute(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    diagnostics = await diagnostics_for(hass, aioclient_mock)

    assert diagnostics["config"]["origin"] == "Short Hills Station"
    assert diagnostics["config"]["destination"] == "New York Penn Station"


async def test_reports_coordinator_health(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Which coordinator is failing is the first question to answer."""
    diagnostics = await diagnostics_for(hass, aioclient_mock)

    for name in ("board", "status", "static", "route"):
        state = diagnostics["coordinators"][name]
        assert state["last_update_success"] is True, name
        assert state["has_data"] is True, name


async def test_explains_the_destination_filter(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """ "Why is my sensor empty" is nearly always this."""
    diagnostics = await diagnostics_for(hass, aioclient_mock)
    filtering = diagnostics["filter"]

    assert filtering["board_departures"] > 0
    assert filtering["resolved_train_ids"]
    assert filtering["usable_departures"]
    assert len(filtering["usable_departures"]) <= filtering["board_departures"]


async def test_exposes_the_line_vocabularies(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Four vocabularies name these lines; mismatches are why alerts vanish."""
    diagnostics = await diagnostics_for(hass, aioclient_mock)
    lines = diagnostics["lines"]

    assert "Morristown Line" in lines["board_titles"]
    assert "MNE" in lines["resolved_alert_codes"]
    assert "MNE" in lines["alert_codes_seen"]


async def test_includes_the_parsed_board(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    diagnostics = await diagnostics_for(hass, aioclient_mock)
    board = diagnostics["board"]

    assert board is not None
    assert board["station"] == "Short Hills Station"
    assert len(board["departures"]) == 19


async def test_includes_alerts_with_parsed_train_ids(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The extraction is heuristic, so its output has to be inspectable."""
    diagnostics = await diagnostics_for(hass, aioclient_mock)

    named = {
        train_id for alert in diagnostics["alerts"] for train_id in alert["train_ids"]
    }
    assert "6607" in named


async def test_output_is_json_serializable(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Diagnostics are downloaded as JSON, so datetimes must be converted.

    A raw datetime or frozenset in here fails at download time, which is
    exactly when someone is already trying to report a different bug.
    """
    diagnostics = await diagnostics_for(hass, aioclient_mock)
    json.dumps(diagnostics)


async def test_survives_a_failed_coordinator(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Diagnostics matter most when something is broken."""
    install_api_mock(
        aioclient_mock,
        {"TripPlannerSchedule": {"data": {"getTripPlannerSchedule": None}}},
    )
    entry = make_entry()
    await setup_entry(hass, entry)

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    assert diagnostics["filter"]["resolution_complete"] is False
    assert diagnostics["filter"]["resolved_train_ids"] == []
    # The board still works, so departures are still filtered by label.
    assert diagnostics["filter"]["usable_departures"]
