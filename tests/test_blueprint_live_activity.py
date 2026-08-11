"""The Live Activity blueprint, driven as a real automation.

Every blueprint bug this repository has had was invisible to the Python suite
and visible on a phone: a countdown to a cancelled train, a notification on a
day off, `as_timestamp` raising on `unknown` for the twenty hours a day there
is no train. None of them are logic the integration owns, so none of them could
be caught by testing the integration.

So the blueprint is loaded into Home Assistant here and triggered, exactly as
it is in production. The commute's sensors are set by hand rather than by
running the integration -- the blueprint reads entity ids and knows nothing
about where they came from, and faking them keeps a blueprint failure from
looking like an API failure.
"""

from __future__ import annotations

import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.njtransit.api.parsing import TZ

BLUEPRINT = "njtransit/favorite_live_activity.yaml"
SOURCE = (
    Path(__file__).resolve().parent.parent
    / "blueprints/automation/njtransit/favorite_live_activity.yaml"
)

PREFIX = "sensor.short_hills_station_to_new_york_penn_station"
FAVORITE = f"{PREFIX}_next_favorite"
PROGRESS = f"{PREFIX}_stops_away"
ROWS = [f"{PREFIX}_next_departure", f"{PREFIX}_departure_2", f"{PREFIX}_departure_3"]

# The blueprint derives the action it calls from the notify entity picked.
NOTIFY_ENTITY = "notify.test_phone"
NOTIFY_SERVICE = "mobile_app_test_phone"

MORNING = datetime(2026, 8, 5, 8, 0, tzinfo=TZ)


def departure(
    when: datetime | None,
    *,
    train_id: str = "6320",
    track: str | None = "2",
    status: str = "on_time",
    status_text: str = "On time",
    delay: int | None = 0,
    status_raw: str = "in 23 Min",
) -> tuple[str, dict[str, Any]]:
    """Return the state and attributes of one departure sensor.

    `status_raw` is the board's own wording, passed through untouched, and it
    is a live countdown -- so it changes every minute of every wait. It is a
    default here rather than a detail of one test because its presence is what
    makes a bare state trigger fire once a minute.
    """
    return (
        when.isoformat() if when else "unknown",
        {
            "device_class": "timestamp",
            "train_id": train_id,
            "track": track,
            "status": status,
            "status_text": status_text,
            "status_raw": status_raw,
            "delay_minutes": delay,
            "destination": "New York",
            "crowding": "unknown",
            "cars": [],
            "alerts": [],
            "favorites": ["6320"],
        },
    )


def riding(hass: HomeAssistant) -> None:
    """Put a journey on the progress sensor, as the integration would.

    `on_board` is the integration's reading of the *train* -- origin behind
    it, destination still ahead. It says nothing about where the phone is,
    which is the whole reason the window has to gate this.
    """
    hass.states.async_set(
        PROGRESS,
        "unknown",
        {
            "train_id": "6320",
            "on_board": True,
            "eta_at_destination": (MORNING + timedelta(minutes=40)).isoformat(),
            "stops_to_destination": 4,
            "next_stop": "Millburn",
            "minutes_late": 0,
        },
    )


def set_board(hass: HomeAssistant, rows: list[tuple[str, dict[str, Any]]]) -> None:
    """Put a board on the numbered departure sensors."""
    for entity, (state, attributes) in zip(ROWS, rows, strict=False):
        hass.states.async_set(entity, state, attributes)


@pytest.fixture(autouse=True)
def expected_lingering_timers() -> bool:
    """Allow the blueprint's own repeating timer to outlive a test.

    It refreshes on a five-minute `time_pattern` because crossing into the
    lead window is the passage of time, which no state change announces. That
    is a real registered timer, not a leak.
    """
    return True


@pytest.fixture(name="notifications")
def notifications_fixture(hass: HomeAssistant) -> list[ServiceCall]:
    """Record what the blueprint sends to the phone."""
    return async_mock_service(hass, "notify", NOTIFY_SERVICE)


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
                "alias": "live activity under test",
                "use_blueprint": {
                    "path": BLUEPRINT,
                    "input": {
                        "favorite_sensor": FAVORITE,
                        "notify_target": NOTIFY_ENTITY,
                        **inputs,
                    },
                },
            }
        },
    )
    await hass.async_block_till_done()


def pushes(calls: list[ServiceCall]) -> list[dict[str, Any]]:
    """Return only the calls that put something on the phone.

    `clear_notification` is how the blueprint tidies up and it fires on every
    quiet poll, so counting it as a notification would make "sent nothing"
    impossible to assert.
    """
    return [
        call.data for call in calls if call.data.get("message") != "clear_notification"
    ]


async def test_a_train_inside_the_window_reaches_the_phone(
    hass: HomeAssistant,
    notifications: list[ServiceCall],
    freezer: FrozenDateTimeFactory,
) -> None:
    """The baseline. Everything below asserts something is *not* sent."""
    freezer.move_to(MORNING)
    leaves = MORNING + timedelta(minutes=12)
    await install(hass)

    set_board(hass, [departure(leaves)])
    hass.states.async_set(FAVORITE, *departure(leaves))
    await hass.async_block_till_done()

    sent = pushes(notifications)
    assert sent, "nothing reached the phone for a train 12 minutes out"
    activity = sent[0]
    assert activity["data"]["live_update"] is True
    assert activity["data"]["chronometer"] is True
    assert activity["data"]["when"] == int(leaves.timestamp())
    assert "6320" in activity["title"]


async def test_no_train_at_all_renders_without_raising(
    hass: HomeAssistant,
    notifications: list[ServiceCall],
    caplog: pytest.LogCaptureFixture,
    freezer: FrozenDateTimeFactory,
) -> None:
    """The failure that reached the log, and the reason this file exists.

        as_timestamp got invalid input 'unknown' ... but no default was
        specified

    Variables render on every trigger regardless of which branch the actions
    take, so an expression only safe when an activity is showing brings the
    whole automation down for the twenty hours a day there is no train.
    """
    freezer.move_to(MORNING)
    await install(hass)

    set_board(hass, [departure(None)])
    hass.states.async_set(FAVORITE, *departure(None))
    await hass.async_block_till_done()

    assert "as_timestamp" not in caplog.text
    assert "Error rendering variables" not in caplog.text
    assert pushes(notifications) == []


async def test_nothing_is_sent_outside_the_commute_window(
    hass: HomeAssistant,
    notifications: list[ServiceCall],
    freezer: FrozenDateTimeFactory,
) -> None:
    """A schedule that excludes a day has to actually exclude it.

    The window spent its first weeks resetting the dismissed flag and nothing
    else, so a commute schedule running Tuesday to Thursday still counted down
    on a Friday. Location cannot substitute: on a day worked from home you are
    exactly where you would be if you were about to travel.
    """
    freezer.move_to(MORNING)
    leaves = MORNING + timedelta(minutes=12)
    hass.states.async_set("schedule.commute", "off")
    await install(hass, commute_window=["schedule.commute"])

    set_board(hass, [departure(leaves)])
    hass.states.async_set(FAVORITE, *departure(leaves))
    await hass.async_block_till_done()

    assert pushes(notifications) == []


async def test_the_arrival_countdown_is_gated_by_the_window_too(
    hass: HomeAssistant,
    notifications: list[ServiceCall],
    freezer: FrozenDateTimeFactory,
) -> None:
    """The second time a day off got a notification, and the harder half.

    The departure countdown was gated and the arrival countdown was not, on
    the reasoning that a window closing at 10:00 must not cut off a train
    arriving at 10:30. That rested on `on_board` meaning "you are on this
    train". It does not -- it means the train has passed your station, which
    happens every weekday whether or not you were aboard.

    So the favourite ran on a Monday off, left Short Hills, flipped
    `on_board`, and put an arrival countdown on a Lock Screen at 07:35. The
    only reason it stopped was the owner reaching for Dismiss.
    """
    freezer.move_to(MORNING)
    hass.states.async_set("schedule.commute", "off")
    await install(hass, commute_window=["schedule.commute"])

    # The board has dropped this train: it has left. Nothing here is about
    # where the phone is, which is the whole problem.
    riding(hass)
    # The favourite is a trigger and the progress sensor is not, so the
    # journey has to be in place before this line or nothing re-evaluates.
    hass.states.async_set(FAVORITE, "unknown", {"favorites": ["6320"]})
    await hass.async_block_till_done()

    assert pushes(notifications) == []


async def test_the_arrival_countdown_still_runs_inside_the_window(
    hass: HomeAssistant,
    notifications: list[ServiceCall],
    freezer: FrozenDateTimeFactory,
) -> None:
    """The gate must not silence the half that is actually useful.

    Gating on the window and gating on location are different decisions: the
    zones that make sense for waiting are exactly the places you are not once
    the train is moving, so `riding` stays ungated on presence.
    """
    freezer.move_to(MORNING)
    hass.states.async_set("schedule.commute", "on")
    await install(hass, commute_window=["schedule.commute"])

    riding(hass)
    # The favourite is a trigger and the progress sensor is not, so the
    # journey has to be in place before this line or nothing re-evaluates.
    hass.states.async_set(FAVORITE, "unknown", {"favorites": ["6320"]})
    await hass.async_block_till_done()

    [sent] = pushes(notifications)
    assert "Millburn" in str(sent)


async def test_a_cancelled_board_is_not_counted_down_to(
    hass: HomeAssistant,
    notifications: list[ServiceCall],
    freezer: FrozenDateTimeFactory,
) -> None:
    """During a line suspension the honest Live Activity is no Live Activity.

    A cancelled service stays on the board -- that is the point of it being
    there -- so "the next train out" is not "the next train you could board".
    A chronometer ticking toward a departure reads as a train that is coming,
    and a Lock Screen has no room for the caveat.
    """
    freezer.move_to(MORNING)
    await install(hass, follow_any_train=True)

    cancelled = [
        departure(
            MORNING + timedelta(minutes=offset),
            train_id=train,
            status="cancelled",
            status_text="Cancelled",
            delay=None,
        )
        for offset, train in ((12, "6918"), (72, "6920"), (132, "6922"))
    ]
    set_board(hass, cancelled)
    hass.states.async_set(FAVORITE, *departure(None))
    await hass.async_block_till_done()

    assert pushes(notifications) == []


async def test_the_fallback_skips_past_a_cancelled_first_row(
    hass: HomeAssistant,
    notifications: list[ServiceCall],
    freezer: FrozenDateTimeFactory,
) -> None:
    """One cancelled train is not a suspension: take the next one you can board."""
    freezer.move_to(MORNING)
    boardable = MORNING + timedelta(minutes=20)
    await install(hass, follow_any_train=True)

    set_board(
        hass,
        [
            departure(
                MORNING + timedelta(minutes=12),
                train_id="6918",
                status="cancelled",
                status_text="Cancelled",
                delay=None,
            ),
            departure(boardable, train_id="6920"),
            departure(MORNING + timedelta(minutes=80), train_id="6922"),
        ],
    )
    hass.states.async_set(FAVORITE, *departure(None))
    await hass.async_block_till_done()

    sent = pushes(notifications)
    assert sent, "a boardable train was available and nothing was sent"
    assert "6920" in sent[-1]["title"]
    assert sent[-1]["data"]["when"] == int(boardable.timestamp())


async def test_an_update_does_not_interrupt(
    hass: HomeAssistant,
    notifications: list[ServiceCall],
    freezer: FrozenDateTimeFactory,
) -> None:
    """The activity replaces what is on screen; it does not announce itself.

    Reusing the `tag` is what stops these stacking, and it was mistaken for
    what stops them making a sound. It is not: without an interruption level
    iOS alerts on every push, so a train whose track appeared and whose status
    slipped buzzed the wrist for each. The companion notification has always
    set this; the activity, which is sent far more often, did not.
    """
    freezer.move_to(MORNING)
    await install(hass)

    hass.states.async_set(FAVORITE, *departure(MORNING + timedelta(minutes=12)))
    await hass.async_block_till_done()

    [activity] = pushes(notifications)
    assert activity["data"]["push"]["interruption-level"] == "passive"


async def test_the_boards_own_countdown_is_not_news(
    hass: HomeAssistant,
    notifications: list[ServiceCall],
    freezer: FrozenDateTimeFactory,
) -> None:
    """A minute passing is not a change worth a push.

    `status_raw` ticks from "in 23 Min" to "in 22 Min" once a minute and a
    bare state trigger fires on any attribute, so the phone was handed a fresh
    copy of an unchanged activity every minute of the wait. The chronometer on
    the phone is what is supposed to be counting down between updates -- that
    is the whole reason the blueprint claims not to push once a minute.
    """
    freezer.move_to(MORNING)
    leaves = MORNING + timedelta(minutes=23)
    await install(hass)

    hass.states.async_set(FAVORITE, *departure(leaves))
    await hass.async_block_till_done()
    assert len(pushes(notifications)) == 1, "the first activity did not go out"

    hass.states.async_set(FAVORITE, *departure(leaves, status_raw="in 22 Min"))
    await hass.async_block_till_done()

    assert len(pushes(notifications)) == 1, "a minute of the countdown woke the phone"


async def test_a_track_appearing_is_news(
    hass: HomeAssistant,
    notifications: list[ServiceCall],
    freezer: FrozenDateTimeFactory,
) -> None:
    """The other half of the same guard, and the reason it lists fields.

    Suppressing the minute tick is only safe if everything a commuter reads
    still gets through. A track appearing is the one people are waiting on --
    it is why they are looking at the Lock Screen at all.
    """
    freezer.move_to(MORNING)
    leaves = MORNING + timedelta(minutes=23)
    await install(hass)

    hass.states.async_set(FAVORITE, *departure(leaves, track=None))
    await hass.async_block_till_done()

    hass.states.async_set(
        FAVORITE, *departure(leaves, track="4", status_raw="in 22 Min")
    )
    await hass.async_block_till_done()

    sent = pushes(notifications)
    assert len(sent) == 2, "the track was posted and the activity did not say so"
    assert "Track 4" in sent[-1]["title"]
