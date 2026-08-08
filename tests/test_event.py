"""Train event entity.

The value of this entity is entirely in *when it does not fire*: not on the
first poll, not repeatedly for an ongoing problem, and not for a train simply
entering the lookahead window. Those are the cases here.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.njtransit.api.models import TrainStatus
from custom_components.njtransit.api.parsing import TZ, now_local
from custom_components.njtransit.const import CONF_LOOKAHEAD
from custom_components.njtransit.event import (
    EVENT_ALERTED,
    EVENT_CANCELLED,
    EVENT_DELAYED,
    EVENT_LINE_CANCELLATION,
    EVENT_TRACK_CHANGED,
    EVENT_TRACK_OVERDUE,
    TrainEvent,
)

from .conftest import install_api_mock, load_fixture
from .test_init import make_entry, setup_entry

ENTITY = "event.short_hills_station_to_new_york_penn_station_train_event"

# The capture is a morning board. Resolved against an afternoon clock those
# departures roll to tomorrow, fall outside the lookahead window, and every
# assertion here silently has nothing to work on -- so these tests pass or
# fail by time of day unless the clock is pinned.
CAPTURED_AT = datetime(2026, 8, 3, 8, 20, tzinfo=TZ)


@pytest.fixture(name="at_capture_time", autouse=True)
def at_capture_time_fixture(freezer: FrozenDateTimeFactory) -> None:
    """Freeze the clock at the moment the fixtures were recorded."""
    freezer.move_to(CAPTURED_AT)


def board_with(**changes: dict[str, Any]) -> dict[str, Any]:
    """Return the recorded board with specific rows altered.

    Keyed by train ID so a test says "6624 loses its track" rather than
    counting list positions.
    """
    payload = load_fixture("departures_short_hills_disruption")
    for item in payload["data"]["getTrainDepartureScreens"]["items"]:
        if item["trainID"] in changes:
            item.update(changes[item["trainID"]])
    return payload


def entity(hass: HomeAssistant) -> TrainEvent:
    """Return the event entity object itself."""
    component = hass.data["domain_entities"]["event"]
    found = component[ENTITY]
    assert isinstance(found, TrainEvent)
    return found


async def test_created_for_a_commute(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    install_api_mock(aioclient_mock)
    await setup_entry(hass, make_entry())

    state = hass.states.get(ENTITY)
    assert state is not None
    assert set(state.attributes["event_types"]) == {
        EVENT_CANCELLED,
        EVENT_DELAYED,
        EVENT_TRACK_CHANGED,
        EVENT_ALERTED,
        EVENT_LINE_CANCELLATION,
        EVENT_TRACK_OVERDUE,
    }


async def test_nothing_fires_on_the_first_poll(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The recorded board is mid-disruption, with two trains cancelled.

    Firing for those on startup would mean every Home Assistant restart
    replays the morning's problems -- exactly the failure this entity exists
    to remove from automations.
    """
    install_api_mock(aioclient_mock)
    await setup_entry(hass, make_entry())

    state = hass.states.get(ENTITY)
    assert state is not None
    assert state.state == "unknown"


async def test_cancellation_fires_once_then_stays_quiet(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A train already cancelled must not re-fire on every poll."""
    install_api_mock(aioclient_mock)
    await setup_entry(hass, make_entry())

    target = entity(hass)
    fired: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(
        target,
        "_trigger_event",
        lambda event_type, attrs=None: fired.append((event_type, attrs or {})),
    )

    seen = dict(target._seen or {})
    was = seen["6624"]
    target._fire_changes(
        next(d for d in target.departures if d.train_id == "6624"),
        was,
        type(was)(cancelled=True, track=was.track, over_threshold=False, alerted=False),
    )
    assert [event for event, _ in fired] == [EVENT_CANCELLED]

    fired.clear()
    already = type(was)(
        cancelled=True, track=was.track, over_threshold=False, alerted=False
    )
    target._fire_changes(
        next(d for d in target.departures if d.train_id == "6624"), already, already
    )
    assert fired == []


async def test_track_change_fires_but_first_assignment_does_not(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Being moved is news. Being told for the first time is not."""
    install_api_mock(aioclient_mock)
    await setup_entry(hass, make_entry())

    target = entity(hass)
    fired: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(
        target,
        "_trigger_event",
        lambda event_type, attrs=None: fired.append((event_type, attrs or {})),
    )
    departure = target.departures[0]
    seen = type(target._seen[departure.train_id])  # type: ignore[index]

    unassigned = seen(cancelled=False, track=None, over_threshold=False, alerted=False)
    assigned = seen(cancelled=False, track="2", over_threshold=False, alerted=False)
    moved = seen(cancelled=False, track="4", over_threshold=False, alerted=False)

    target._fire_changes(departure, unassigned, assigned)
    assert fired == [], "a first track assignment is not a change"

    target._fire_changes(departure, assigned, moved)
    assert [event for event, _ in fired] == [EVENT_TRACK_CHANGED]
    assert fired[0][1]["previous_track"] == "2"


async def test_delay_fires_on_crossing_the_threshold(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Drifting 1 -> 2 minutes is not an event; crossing the threshold is."""
    install_api_mock(aioclient_mock)
    await setup_entry(hass, make_entry(options={"delay_threshold": 10}))

    target = entity(hass)
    fired: list[str] = []
    monkeypatch.setattr(
        target,
        "_trigger_event",
        lambda event_type, attrs=None: fired.append(event_type),
    )
    departure = target.departures[0]
    seen = type(target._seen[departure.train_id])  # type: ignore[index]

    under = seen(cancelled=False, track="2", over_threshold=False, alerted=False)
    over = seen(cancelled=False, track="2", over_threshold=True, alerted=False)

    target._fire_changes(departure, under, under)
    assert fired == []

    target._fire_changes(departure, under, over)
    assert fired == [EVENT_DELAYED]

    fired.clear()
    target._fire_changes(departure, over, over)
    assert fired == [], "an ongoing delay must not re-fire"


async def test_cancelled_train_does_not_also_report_late(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancelled already said the worse thing."""
    install_api_mock(aioclient_mock)
    await setup_entry(hass, make_entry())

    target = entity(hass)
    fired: list[str] = []
    monkeypatch.setattr(
        target,
        "_trigger_event",
        lambda event_type, attrs=None: fired.append(event_type),
    )
    departure = target.departures[0]
    seen = type(target._seen[departure.train_id])  # type: ignore[index]

    before = seen(cancelled=False, track="2", over_threshold=False, alerted=False)
    after = seen(cancelled=True, track="2", over_threshold=True, alerted=False)

    target._fire_changes(departure, before, after)
    assert fired == [EVENT_CANCELLED]


async def test_being_named_in_an_alert_fires(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The signal a board alone cannot give you."""
    install_api_mock(aioclient_mock)
    await setup_entry(hass, make_entry())

    target = entity(hass)
    fired: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(
        target,
        "_trigger_event",
        lambda event_type, attrs=None: fired.append((event_type, attrs or {})),
    )
    departure = target.departures[0]
    seen = type(target._seen[departure.train_id])  # type: ignore[index]

    quiet = seen(cancelled=False, track="2", over_threshold=False, alerted=False)
    named = seen(cancelled=False, track="2", over_threshold=False, alerted=True)

    target._fire_changes(departure, quiet, named)
    assert [event for event, _ in fired] == [EVENT_ALERTED]
    assert fired[0][1]["train_id"] == departure.train_id
    assert "scheduled" in fired[0][1]


async def test_end_to_end_track_change_through_a_real_refresh(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Drive the actual coordinator, not just the diffing helper.

    The unit tests above call _fire_changes directly, which would keep
    passing if the entity stopped listening, stopped priming, or stopped
    writing state. This one moves a train and asserts the entity noticed.
    """
    install_api_mock(aioclient_mock)
    entry = make_entry()
    await setup_entry(hass, entry)

    target = entity(hass)
    assert target._seen, "baseline was not primed at startup"
    train = target.departures[0].train_id
    original = target._seen[train].track
    assert original is not None, "capture has no track to move"

    moved = "9" if original != "9" else "8"
    aioclient_mock.clear_requests()
    install_api_mock(
        aioclient_mock,
        {"TrainDepartureScreens": board_with(**{train: {"track": moved}})},
    )

    await entry.runtime_data.board.async_refresh()
    await hass.async_block_till_done()

    state = hass.states.get(ENTITY)
    assert state is not None
    assert state.attributes["event_type"] == EVENT_TRACK_CHANGED
    assert state.attributes["train_id"] == train
    assert state.attributes["previous_track"] == original
    assert state.attributes["track"] == moved


async def test_a_cancelled_train_you_cannot_use_still_reaches_you(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Train 6311 in the recorded capture is exactly this case.

    Morristown Line, 8:28 AM, terminating at Summit, cancelled -- and it
    does not serve this commute, so the destination filter drops it. It runs
    24 minutes ahead of the usable 8:52, which is how its stops and its
    passengers end up on that train.
    """
    install_api_mock(aioclient_mock)
    await setup_entry(hass, make_entry())

    target = entity(hass)
    watched = {train.train_id: affects for train, affects in target._watched()}

    assert "6311" in watched, "the cancelled Summit train is not being watched"
    assert watched["6311"] == "6624", "it should be tied to the train it precedes"
    assert watched["6624"] is None, "our own trains carry no affects_train"

    fired: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(
        target,
        "_trigger_event",
        lambda event_type, attrs=None: fired.append((event_type, attrs or {})),
    )
    departure = next(
        d for d in target.coordinator.data.departures if d.train_id == "6311"
    )
    assert target._seen is not None
    seen = type(target._seen[departure.train_id])
    running = seen(cancelled=False, track=None, over_threshold=False, alerted=False)
    gone = seen(cancelled=True, track=None, over_threshold=False, alerted=False)

    target._fire(EVENT_LINE_CANCELLATION, departure, affects_train="6624")
    assert fired[0][0] == EVENT_LINE_CANCELLATION
    assert fired[0][1]["train_id"] == "6311"
    assert fired[0][1]["affects_train"] == "6624"
    assert running != gone


async def test_a_train_behind_yours_is_not_watched(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """It cannot hand its stops to a train that has already left."""
    install_api_mock(aioclient_mock)
    await setup_entry(hass, make_entry())

    watched = {train.train_id: affects for train, affects in entity(hass)._watched()}
    # 6317 (10:02) trails the usable 6328 (09:55) and leads nothing within
    # the window, so it is none of our business.
    assert "6317" not in watched


async def test_a_cancellation_too_far_ahead_is_not_yours(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The knock-on window has a size, and the size is the point.

    A train handing its stops over has to be running *shortly* before yours.
    Widen the window and every earlier service on the line starts reporting as
    your problem; nothing in the default capture is far enough ahead to tell
    the difference, so this stretches the lookahead until two trains sit in
    the gap either side of it.

    6617 (9:19) leads 6328 (9:55) by 36 minutes and 851 (9:58) leads 6332
    (10:32) by 34 -- both just outside the thirty-minute window, and both
    swept in by any careless widening of it.
    """
    install_api_mock(aioclient_mock)
    await setup_entry(hass, make_entry(options={CONF_LOOKAHEAD: 180}))

    target = entity(hass)
    upcoming = {d.train_id for d in target._upcoming()}
    assert {"6328", "6332"} <= upcoming, "lookahead did not reach the far trains"

    watched = {train.train_id for train, affects in target._watched() if affects}
    assert "6617" not in watched
    assert "851" not in watched
    # And the window is not simply empty -- a nearby one still counts.
    assert watched, "no knock-on candidates at all, so this proves nothing"


async def test_other_lines_are_ignored(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Same station, different line, no shared fate."""
    install_api_mock(aioclient_mock)
    await setup_entry(hass, make_entry())

    target = entity(hass)
    lines = {d.line for d, affects in target._watched() if affects is not None}
    ours = {d.line for d in target._upcoming()}
    assert lines <= ours


async def test_a_track_that_never_arrives_fires(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    monkeypatch: pytest.MonkeyPatch,
    freezer: FrozenDateTimeFactory,
) -> None:
    """The signal a regular traveller reads before anything is announced.

    New York Penn posts NJ Transit tracks a median of 8.8 minutes before
    departure, with an interquartile range of 1.9 minutes and a p10 of 5.7
    (n=236). A train still without one inside six minutes is in the slowest
    tenth, and nothing else on the board says so.

    The asserted `expected_by_minutes` is the threshold itself, so this test
    fails deliberately if it moves. That is the point: the number is what the
    event means, and changing it silently would change what every automation
    built on it is reacting to.
    """
    install_api_mock(aioclient_mock, {"TrainDepartureScreens": board_with()})
    await setup_entry(hass, make_entry())

    target = entity(hass)
    fired: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(
        target,
        "_trigger_event",
        lambda event_type, attrs=None: fired.append((event_type, attrs or {})),
    )
    seen = type(target._seen[target.departures[0].train_id])  # type: ignore[index]
    # A genuinely overdue train has no track; the fixture row has one.
    departure = replace(target.departures[0], track=None)

    # Far enough out that a missing track is simply normal, then inside the
    # window where it is not.
    early = seen(cancelled=False, track=None, over_threshold=False, alerted=False)
    overdue = seen(
        cancelled=False,
        track=None,
        over_threshold=False,
        alerted=False,
        track_overdue=True,
    )

    target._fire_changes(departure, early, overdue)
    assert [event for event, _ in fired] == [EVENT_TRACK_OVERDUE]
    assert fired[0][1]["expected_by_minutes"] == 6
    assert fired[0][1]["track"] is None

    # Still overdue on the next poll is not a second piece of news.
    fired.clear()
    target._fire_changes(departure, overdue, overdue)
    assert fired == []


async def test_a_cancelled_train_is_not_reported_as_overdue(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """It is never getting a track, and cancellation already said the worse thing."""
    install_api_mock(aioclient_mock)
    await setup_entry(hass, make_entry())

    target = entity(hass)
    departure = target.departures[0]
    cancelled = replace(
        departure, status=TrainStatus.CANCELLED, track=None, scheduled=now_local()
    )

    assert (
        target._snapshot(cancelled, frozenset(), publishes=True).track_overdue is False
    )


async def test_a_station_publishing_no_tracks_reports_nothing_overdue(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Otherwise every train at such a station is permanently "overdue".

    The signal is "late getting a track while others are getting theirs",
    which is meaningless where nobody is.
    """
    install_api_mock(aioclient_mock)
    await setup_entry(hass, make_entry())

    target = entity(hass)
    # The first row of the capture is cancelled, which is excluded on its own
    # grounds -- this test is about the station, not the train.
    departure = replace(
        target.departures[0],
        track=None,
        status=TrainStatus.ON_TIME,
        scheduled=now_local(),
    )

    assert (
        target._snapshot(departure, frozenset(), publishes=False).track_overdue is False
    )
    assert (
        target._snapshot(departure, frozenset(), publishes=True).track_overdue is True
    )


async def test_a_train_still_far_out_is_not_overdue(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Most of a terminal's board has no track for most of the day."""
    install_api_mock(aioclient_mock)
    await setup_entry(hass, make_entry())

    target = entity(hass)
    departure = replace(
        target.departures[0],
        track=None,
        status=TrainStatus.ON_TIME,
        scheduled=now_local() + timedelta(minutes=40),
    )

    assert (
        target._snapshot(departure, frozenset(), publishes=True).track_overdue is False
    )


async def test_a_line_cancellation_reaches_you_through_a_real_poll(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The same path an automation actually takes.

    A sibling test builds the event by calling `_fire` directly, which proves
    the payload but skips the diffing loop that decides whether to fire at
    all -- the entity's whole reason to exist.
    """
    install_api_mock(aioclient_mock)
    entry = make_entry()
    await setup_entry(hass, entry)

    target = entity(hass)
    assert target._seen, "baseline was not primed at startup"
    ours = {d.train_id for d in target.departures}
    theirs = next((train_id for train_id in target._seen if train_id not in ours), None)
    assert theirs is not None, "capture has no unusable same-line train ahead"

    aioclient_mock.clear_requests()
    install_api_mock(
        aioclient_mock,
        {"TrainDepartureScreens": board_with(**{theirs: {"status": "Cancelled"}})},
    )
    await entry.runtime_data.board.async_refresh()
    await hass.async_block_till_done()

    state = hass.states.get(ENTITY)
    assert state is not None
    assert state.attributes["event_type"] == EVENT_LINE_CANCELLATION
    assert state.attributes["train_id"] == theirs
    assert state.attributes["affects_train"] in ours


async def test_a_train_arriving_in_the_window_fires_nothing(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A train appearing is the clock moving, not news.

    Without this the diff would announce every train as it drifts into the
    lookahead, which is a notification a minute and nothing to act on.
    """
    install_api_mock(aioclient_mock)
    entry = make_entry()
    await setup_entry(hass, entry)

    target = entity(hass)
    known = set(target._seen or {})
    assert known

    # A board carrying an extra departure the entity has never seen.
    payload = board_with()
    items = payload["data"]["getTrainDepartureScreens"]["items"]
    newcomer = dict(items[0])
    newcomer["trainID"] = "9999"
    items.append(newcomer)

    aioclient_mock.clear_requests()
    install_api_mock(aioclient_mock, {"TrainDepartureScreens": payload})
    await entry.runtime_data.board.async_refresh()
    await hass.async_block_till_done()

    assert "9999" in (target._seen or {}), "the newcomer was never watched"
    assert hass.states.get(ENTITY).state == "unknown"  # type: ignore[union-attr]


async def test_nothing_is_overdue_before_the_first_board_arrives(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A coordinator with no data cannot say whether anyone has a track."""
    install_api_mock(aioclient_mock)
    await setup_entry(hass, make_entry())

    target = entity(hass)
    target.coordinator.data = None  # type: ignore[assignment]

    assert target._publishes_tracks() is False
