"""The disruption blueprint, driven as a real automation.

Its whole job is deciding when *not* to speak. The delay in a reason string
wobbles every poll -- "is 6 minutes late" becomes "is 7 minutes late" -- and
diffing raw text once produced five notifications in seven minutes on a train
whose situation had not changed. Everything below is a variation on that.

The blueprint takes its action as an input, so these tests hand it a call to a
mocked service and read back what it was given.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import async_mock_service

BLUEPRINT = "njtransit/commute_disruption.yaml"
SOURCE = (
    Path(__file__).resolve().parent.parent
    / "blueprints/automation/njtransit/commute_disruption.yaml"
)

SENSOR = "binary_sensor.short_hills_station_to_new_york_penn_station_commute_disrupted"

LATE = "Train 6624 (8:52 AM) is {} minutes late"
CANCELLED = "Train 6320 (8:12 AM) is cancelled"
OTHER_LATE = "Train 6311 (8:28 AM) is {} minutes late"


@pytest.fixture(name="alerts")
def alerts_fixture(hass: HomeAssistant) -> list[ServiceCall]:
    """Record what the blueprint decided was worth saying."""
    return async_mock_service(hass, "notify", "commute")


async def install(hass: HomeAssistant, **inputs: Any) -> None:
    """Load the real blueprint and build an automation from it."""

    def copy() -> None:
        destination = Path(hass.config.path("blueprints/automation/njtransit"))
        destination.mkdir(parents=True, exist_ok=True)
        shutil.copy(SOURCE, destination / SOURCE.name)

    await hass.async_add_executor_job(copy)

    assert await async_setup_component(
        hass,
        "automation",
        {
            "automation": {
                "alias": "disruption under test",
                "use_blueprint": {
                    "path": BLUEPRINT,
                    "input": {
                        "disruption_sensor": SENSOR,
                        "notification_action": [
                            {
                                "action": "notify.commute",
                                "data": {
                                    "message": "{{ fresh_reasons | join('; ') }}",
                                    "title": "{{ commute_name }}",
                                },
                            }
                        ],
                        **inputs,
                    },
                },
            }
        },
    )
    await hass.async_block_till_done()


def set_reasons(hass: HomeAssistant, reasons: list[str]) -> None:
    """Publish a new set of reasons on the disruption sensor."""
    hass.states.async_set(
        SENSOR,
        "on" if reasons else "off",
        {
            "friendly_name": "Short Hills Station to New York Penn Station "
            "Commute disrupted",
            "reasons": reasons,
        },
    )


def messages(calls: list[ServiceCall]) -> list[str]:
    """Return the message text of each alert sent."""
    return [call.data["message"] for call in calls]


async def test_a_new_problem_is_announced(
    hass: HomeAssistant, alerts: list[ServiceCall]
) -> None:
    """The baseline. Everything below asserts silence."""
    await install(hass)
    set_reasons(hass, [])
    await hass.async_block_till_done()

    set_reasons(hass, [CANCELLED])
    await hass.async_block_till_done()

    assert messages(alerts) == [CANCELLED]


async def test_a_delay_drifting_inside_its_band_is_silent(
    hass: HomeAssistant, alerts: list[ServiceCall]
) -> None:
    """The failure this blueprint was rewritten for.

    A train sitting at 5, then 6, then 7 minutes late is one unchanged fact
    wearing three different strings. Diffing the raw text announced each one:
    five notifications in seven minutes, observed in the wild.
    """
    await install(hass)
    set_reasons(hass, [])
    await hass.async_block_till_done()

    set_reasons(hass, [LATE.format(5)])
    await hass.async_block_till_done()
    assert len(alerts) == 1, "the first report of a late train is news"

    for minutes in (6, 7, 8, 9):
        set_reasons(hass, [LATE.format(minutes)])
        await hass.async_block_till_done()

    assert len(alerts) == 1, "drifting inside a five-minute band spoke again"


async def test_crossing_into_a_worse_band_is_announced(
    hass: HomeAssistant, alerts: list[ServiceCall]
) -> None:
    """Debouncing must not become silence. Ten minutes is a different problem."""
    await install(hass)
    set_reasons(hass, [])
    await hass.async_block_till_done()

    set_reasons(hass, [LATE.format(6)])
    await hass.async_block_till_done()
    set_reasons(hass, [LATE.format(12)])
    await hass.async_block_till_done()

    assert messages(alerts) == [LATE.format(6), LATE.format(12)]


async def test_a_train_recovering_says_nothing(
    hass: HomeAssistant, alerts: list[ServiceCall]
) -> None:
    """News, but not bad news, and not what an alert is for.

    Comparing bands for equality rather than for an increase announced a train
    improving from 12 minutes late to 6 -- which reads on a phone exactly like
    a fresh problem.
    """
    await install(hass)
    set_reasons(hass, [])
    await hass.async_block_till_done()

    set_reasons(hass, [LATE.format(12)])
    await hass.async_block_till_done()
    set_reasons(hass, [LATE.format(6)])
    await hass.async_block_till_done()

    assert messages(alerts) == [LATE.format(12)]


async def test_the_message_carries_the_real_delay_not_the_band(
    hass: HomeAssistant, alerts: list[ServiceCall]
) -> None:
    """Only the decision to speak is debounced, never the wording.

    Bucketing is a comparison device. A notification reading "is 10 minutes
    late" when the train is 12 would be the debouncing leaking into the text.
    """
    await install(hass)
    set_reasons(hass, [])
    await hass.async_block_till_done()

    set_reasons(hass, [LATE.format(12)])
    await hass.async_block_till_done()

    assert messages(alerts) == ["Train 6624 (8:52 AM) is 12 minutes late"]


async def test_a_second_train_failing_mid_disruption_is_announced(
    hass: HomeAssistant, alerts: list[ServiceCall]
) -> None:
    """The reason this triggers on the attribute rather than on the state.

    The sensor is already `on`, so a state trigger would stay silent through
    the entire rest of the disruption.
    """
    await install(hass)
    set_reasons(hass, [])
    await hass.async_block_till_done()

    set_reasons(hass, [LATE.format(6)])
    await hass.async_block_till_done()
    set_reasons(hass, [LATE.format(6), CANCELLED])
    await hass.async_block_till_done()

    assert messages(alerts) == [LATE.format(6), CANCELLED]


async def test_an_unchanged_cancellation_is_not_repeated(
    hass: HomeAssistant, alerts: list[ServiceCall]
) -> None:
    """Cancellations have no volatile part, so they are compared as-is."""
    await install(hass)
    set_reasons(hass, [])
    await hass.async_block_till_done()

    set_reasons(hass, [CANCELLED])
    await hass.async_block_till_done()
    set_reasons(hass, [CANCELLED, OTHER_LATE.format(7)])
    await hass.async_block_till_done()

    assert messages(alerts) == [CANCELLED, OTHER_LATE.format(7)]


async def test_a_problem_clearing_is_not_an_alert(
    hass: HomeAssistant, alerts: list[ServiceCall]
) -> None:
    """The list only shrinks, so there is nothing fresh in it."""
    await install(hass)
    set_reasons(hass, [])
    await hass.async_block_till_done()

    set_reasons(hass, [CANCELLED])
    await hass.async_block_till_done()
    set_reasons(hass, [])
    await hass.async_block_till_done()

    assert messages(alerts) == [CANCELLED]


async def test_nothing_is_sent_outside_the_commute_window(
    hass: HomeAssistant, alerts: list[ServiceCall]
) -> None:
    """A 3am cancellation must not wake anyone."""
    hass.states.async_set("schedule.commute", "off")
    await install(hass, commute_window=["schedule.commute"])
    set_reasons(hass, [])
    await hass.async_block_till_done()

    set_reasons(hass, [CANCELLED])
    await hass.async_block_till_done()

    assert alerts == []


async def test_an_unset_window_does_not_block_everything(
    hass: HomeAssistant, alerts: list[ServiceCall]
) -> None:
    """An empty list means "no restriction", not "never".

    Read the other way this input would silently switch the whole automation
    off for everyone who left it alone, which is its default.
    """
    await install(hass)
    set_reasons(hass, [])
    await hass.async_block_till_done()

    set_reasons(hass, [CANCELLED])
    await hass.async_block_till_done()

    assert messages(alerts) == [CANCELLED]
