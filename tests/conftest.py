"""Shared test fixtures."""

from __future__ import annotations

import json
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)
from yarl import URL

from custom_components.njtransit.api.queries import ENDPOINT

FIXTURE_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(
    enable_custom_integrations: None,
) -> Generator[None]:
    """Let Home Assistant load this custom integration in tests."""
    yield


def load_fixture(name: str) -> Any:
    """Return the parsed contents of a recorded API response.

    Fixtures are a coherent capture -- every query issued within the same
    minute during a live Morris & Essex disruption -- so cross-feed
    correlation can be tested end to end. See AGENTS.md before changing them.
    """
    with (FIXTURE_DIR / f"{name}.json").open(encoding="utf-8") as file:
        return json.load(file)


def load_payload(name: str, root_field: str) -> Any:
    """Return just the root field's data from a recorded response."""
    return load_fixture(name)["data"][root_field]


@pytest.fixture(name="system_status")
def system_status_fixture() -> list[dict[str, Any]]:
    """Return the system status feed captured during the disruption."""
    return load_payload("system_status_disruption", "getSystemStatus")


@pytest.fixture(name="departure_board")
def departure_board_fixture() -> dict[str, Any]:
    """Return the Short Hills board captured during the disruption."""
    return load_payload("departures_short_hills_disruption", "getTrainDepartureScreens")


# Every operation posts to the same URL, so responses are dispatched by
# operation name rather than by endpoint.
DEFAULT_RESPONSES: dict[str, str] = {
    "SystemStatus": "system_status_disruption",
    "TrainDepartureScreens": "departures_short_hills_disruption",
    "TrainScheduleStationsRailForDV": "stations_rail_dv",
    "TrainLines": "train_lines",
    "TripPlannerSchedule": "trip_planner_short_hills_to_ny",
    "TrainStopList": "stop_list_6320",
}


def install_api_mock(
    aioclient_mock: AiohttpClientMocker,
    overrides: dict[str, Any] | None = None,
) -> list[str]:
    """Serve recorded fixtures, keyed by GraphQL operation name.

    :param overrides: Responses to use instead of the recorded fixture, keyed
        by operation name. A value may be a payload dict or an exception to
        raise.
    :return: A list that accumulates the operation names requested.
    """
    overrides = overrides or {}
    called: list[str] = []

    async def respond(
        method: str, url: URL, data: dict[str, Any]
    ) -> AiohttpClientMockResponse:
        operation = data["operationName"]
        called.append(operation)

        if operation in overrides:
            override = overrides[operation]
            if isinstance(override, Exception):
                raise override
            payload = override
        else:
            payload = load_fixture(DEFAULT_RESPONSES[operation])

        return AiohttpClientMockResponse(method="POST", url=URL(ENDPOINT), json=payload)

    aioclient_mock.post(ENDPOINT, side_effect=respond)
    return called
