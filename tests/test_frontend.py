"""Serving the Lovelace card that ships with the integration.

Two lines of registration, and both of them fail in a way nobody sees until a
dashboard is open: a card that never loads looks exactly like a card that was
configured wrong. The interesting case is the second commute, because
registering one static path twice is an error and everyone's first version of
this registers per entry.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from homeassistant.components.frontend import DATA_EXTRA_MODULE_URL
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.njtransit.event import TRACK_OVERDUE_LEAD
from custom_components.njtransit.frontend import URL, bundle_path

from .conftest import install_api_mock
from .test_init import NY_PENN, SHORT_HILLS, make_entry, setup_entry

CARD_SOURCE = Path(__file__).resolve().parent.parent / "frontend/src/pills.ts"


async def test_the_card_is_served_and_asked_for(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Registering the path is not enough; the frontend has to be told."""
    install_api_mock(aioclient_mock)

    await setup_entry(hass, make_entry())

    assert URL in hass.data[DATA_EXTRA_MODULE_URL].urls


async def test_a_second_commute_does_not_register_it_again(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """One card, however many commutes.

    Registering the same static path twice raises, which would take the whole
    second entry down with it -- a user who adds their evening commute loses
    their morning one.
    """
    install_api_mock(aioclient_mock)

    await setup_entry(hass, make_entry())
    inbound = make_entry(
        origin=NY_PENN,
        origin_id="NY",
        destination=SHORT_HILLS,
        destination_id="RT",
    )
    await setup_entry(hass, inbound)

    assert inbound.state is ConfigEntryState.LOADED, "the second commute failed"
    assert URL in hass.data[DATA_EXTRA_MODULE_URL].urls


async def test_a_missing_bundle_does_not_stop_the_integration(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A source checkout with no build still gets its sensors.

    Serving a path that does not exist would be the worse failure: aiohttp
    raises on registration, so the whole entry goes down over a card.
    """
    install_api_mock(aioclient_mock)
    monkeypatch.setattr(
        "custom_components.njtransit.frontend.bundle_path",
        lambda: Path("/nonexistent/njtransit-card.js"),
    )

    entry = make_entry()
    await setup_entry(hass, entry)

    assert entry.runtime_data is not None
    assert URL not in hass.data[DATA_EXTRA_MODULE_URL].urls


def test_the_built_card_is_committed() -> None:
    """HACS copies files and runs nothing, so the bundle has to be in the tree.

    Deleting it is a one-line change with no Python consequence at all, which
    is why this is asserted here rather than left to the frontend job.
    """
    path = bundle_path()

    assert path.is_file(), f"{path} is missing -- run `npm run build` in frontend/"
    assert "njtransit-departures" in path.read_text()


def test_the_cards_overdue_threshold_matches_the_integration() -> None:
    """The card's red "Track overdue" pill illustrates `track_overdue`.

    Nothing links the two numbers, and this one has already moved once -- 8 to
    6, by hand, in five places. A card contradicting the event it illustrates
    is worse than having neither. The source is checked rather than the built
    bundle because the constant is inlined and unfindable after minification;
    the frontend job's rebuild-and-diff is what catches a stale build.
    """
    found = re.search(r"TRACK_OVERDUE_MINUTES = (\d+)", CARD_SOURCE.read_text())

    assert found is not None, "the card no longer names its threshold"
    assert int(found.group(1)) == int(TRACK_OVERDUE_LEAD.total_seconds() // 60)
