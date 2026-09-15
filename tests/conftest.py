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
from custom_components.njtransit.api.raildata import ENDPOINT as RAILDATA

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
    "DVCloseStation": "nearest_stations_short_hills",
    "TripPlannerAlternates": "station_coordinates_short_hills",
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


# -- RailData ------------------------------------------------------------------

RAILDATA_FIXTURE_DIR = FIXTURE_DIR / "raildata"

TEST_TOKEN = "test-token"

# Recorded 2026-09-14 21:27 EDT against the production API, every call inside
# the same minute, so the New York board and the vehicle feed describe the same
# instant: train 3889 is posted on track 3 and standing on circuit AA-A190TK,
# which decodes to 3. `station_msg` is mostly assembled from the examples in NJ
# Transit's own API documentation -- the live feed was empty at 21:27 -- plus
# the one real message it carried at 22:10, an M&E train running late.
RAILDATA_RESPONSES: dict[str, str | dict[str, str]] = {
    "getStationList": "station_list",
    "getStationMSG": "station_msg",
    "getVehicleData": "vehicle_data",
    "getTrainStopList": "train_stop_list_6295",
    "getTrainSchedule19Rec": {
        "RT": "train_schedule19_short_hills",
        "NY": "train_schedule19_new_york",
    },
    "getStationSchedule": {
        "RT": "station_schedule_short_hills",
        "NY": "station_schedule_new_york",
    },
}


def load_raildata_fixture(name: str) -> Any:
    """Return the parsed contents of a recorded RailData response."""
    with (RAILDATA_FIXTURE_DIR / f"{name}.json").open(encoding="utf-8") as file:
        return json.load(file)


def install_raildata_mock(
    aioclient_mock: AiohttpClientMocker,
    overrides: dict[str, Any] | None = None,
    *,
    authenticated: bool = True,
) -> list[dict[str, Any]]:
    """Serve recorded RailData fixtures, keyed by method name.

    :param overrides: Responses to use instead of the recorded fixture, keyed
        by method name. A value may be a payload or an exception to raise.
    :param authenticated: Whether ``getToken`` accepts the credentials.
    :return: A list that accumulates every request as ``{"method": ...,
        **form}``, so a test can count token requests or check which
        station was asked for. The password is never recorded.
    """
    overrides = overrides or {}
    called: list[dict[str, Any]] = []

    async def respond(
        method: str, url: URL, data: dict[str, Any]
    ) -> AiohttpClientMockResponse:
        name = url.path.rsplit("/", 1)[-1]
        form = {key: value for key, value in (data or {}).items() if key != "password"}
        called.append({"method": name, **form})

        if name in overrides:
            override = overrides[name]
            if isinstance(override, Exception):
                raise override
            payload = override
        elif name == "getToken":
            payload = (
                {"Authenticated": "True", "UserToken": TEST_TOKEN}
                if authenticated
                else {"Authenticated": "False", "UserToken": ""}
            )
        else:
            if data.get("token") != TEST_TOKEN:
                payload = {"errorMessage": "Invalid token."}
            else:
                fixture = RAILDATA_RESPONSES[name]
                if isinstance(fixture, dict):
                    fixture = fixture[str(data.get("station"))]
                payload = load_raildata_fixture(fixture)

        if payload is None:
            # The API's documented answer to a missing token or account is
            # the literal `null`, which is JSON, not an empty body.
            return AiohttpClientMockResponse(method="POST", url=url, text="null")
        return AiohttpClientMockResponse(method="POST", url=url, json=payload)

    for name in (*RAILDATA_RESPONSES, "getToken"):
        aioclient_mock.post(f"{RAILDATA}/{name}", side_effect=respond)
    return called


class MemoryStore:
    """A :class:`~.api.raildata.RailDataStore` that lives in a dict."""

    def __init__(self, data: dict[str, Any] | None = None) -> None:
        """Start with ``data`` as what was "saved" last time."""
        self.data = data
        self.saves = 0

    async def load(self) -> dict[str, Any] | None:
        """Return the last saved state."""
        return json.loads(json.dumps(self.data)) if self.data is not None else None

    async def save(self, data: dict[str, Any]) -> None:
        """Keep a copy, the way a file would."""
        self.data = json.loads(json.dumps(data))
        self.saves += 1
