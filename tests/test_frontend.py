"""Serving the Lovelace card that ships with the integration.

Two lines of registration, and both of them fail in a way nobody sees until a
dashboard is open: a card that never loads looks exactly like a card that was
configured wrong. The interesting case is the second commute, because
registering one static path twice is an error and everyone's first version of
this registers per entry.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

import pytest
from homeassistant.components.frontend import DATA_EXTRA_MODULE_URL
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.njtransit.event import TRACK_OVERDUE_LEAD
from custom_components.njtransit.frontend import (
    URL,
    async_register_card,
    bundle_path,
)

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


async def test_two_commutes_racing_claim_the_card_once(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bug this file already had a test for, and did not catch.

    `test_a_second_commute_does_not_register_it_again` sets its entries up one
    after another, so it never exercised the only case that fails. Home
    Assistant sets a domain's entries up *concurrently*, and the guard checked
    the claim, awaited a file stat, and only then set it -- both commutes read
    no claim, both registered, and the second raised

        RuntimeError: Added route will never be executed, method GET is
        already registered

    taking the whole second commute down over a card. It shipped that way in
    2026.8.11 and broke the evening commute for anyone running two.

    This calls the function directly rather than driving two entries, because
    two concurrent calls are exactly what two entries produce and nothing
    about the coordinators is involved in the failure.
    """
    assert await async_setup_component(hass, "frontend", {})

    # Forcing a suspension is what makes this able to fail at all, and the
    # first version of this test did not: the harness runs executor jobs
    # inline, so awaiting one never yields, a plain gather runs the first call
    # start to finish, and the second finds the claim already set. It passed
    # against the shipped bug. A real filesystem stat suspends.
    #
    # `test_concurrent_setup_still_shares_one_store` needs the same trick for
    # the same reason, and says so. Reading it first would have saved a
    # release.
    original = hass.async_add_executor_job

    async def yielding(target: Any, *args: Any) -> Any:
        await asyncio.sleep(0)
        return await original(target, *args)

    monkeypatch.setattr(hass, "async_add_executor_job", yielding)

    outcomes = await asyncio.gather(
        async_register_card(hass),
        async_register_card(hass),
        return_exceptions=True,
    )

    assert [o for o in outcomes if isinstance(o, Exception)] == []
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
