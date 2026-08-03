"""Shared test fixtures."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

FIXTURE_DIR = Path(__file__).parent / "fixtures"


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
