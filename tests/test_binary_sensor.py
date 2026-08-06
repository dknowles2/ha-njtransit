"""The merged disruption signal.

The central test here is `test_fires_on_a_cancellation_absent_from_the_alert_feed`.
Train 6320 was cancelled on the Short Hills board while the alert feed said
nothing about it. If that stops firing, the integration has lost the thing it
exists to do -- fix the code, not the test.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.njtransit.api.parsing import TZ
from custom_components.njtransit.const import CONF_DELAY_THRESHOLD, CONF_LOOKAHEAD

from .conftest import install_api_mock, load_fixture
from .test_init import make_entry, setup_entry

ENTITY = "binary_sensor.short_hills_station_to_new_york_penn_station_commute_disrupted"

# The moment the disruption fixtures were captured. Board times are bare
# wall-clock strings resolved against "now", so the clock has to be frozen or
# every departure lands on the wrong day.
CAPTURED_AT = datetime(2026, 8, 3, 8, 20, tzinfo=TZ)


@pytest.fixture(name="at_capture_time", autouse=True)
def at_capture_time_fixture(freezer: FrozenDateTimeFactory) -> None:
    """Freeze the clock at the moment the fixtures were recorded."""
    freezer.move_to(CAPTURED_AT)


def board_with(items: list[dict[str, object]]) -> dict[str, object]:
    """Return a departure-board payload containing exactly these rows."""
    return {"data": {"getTrainDepartureScreens": {"items": items}}}


def row(
    train_id: str,
    *,
    departure: str = "8:30 AM",
    status: str = "",
    destination: str = "New York",
) -> dict[str, object]:
    """Return one board row."""
    return {
        "trainID": train_id,
        "departureDate": departure,
        "destination": destination,
        "line": "Morristown Line",
        "lineAbbreviation": "M&E",
        "status": status,
        "track": "2",
        "inlineMessage": "",
        "stops": [],
        "capacity": None,
    }


def no_alerts() -> dict[str, object]:
    """Return an empty system-status payload."""
    return {"data": {"getSystemStatus": []}}


def alert_saying(message: str) -> dict[str, object]:
    """Return a system-status payload carrying one live incident."""
    return {
        "data": {
            "getSystemStatus": [
                {
                    "abbreviation": "MNE",
                    "message": message,
                    "msg_richtext": message,
                    "msg_url": "",
                    "service": "Rail",
                    "advisoryAlert": "0",
                }
            ]
        }
    }


class TestTheReasonThisExists:
    """Cross-feed correlation."""

    async def test_fires_on_a_cancellation_absent_from_the_alert_feed(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """Train 6320: cancelled on the board, unmentioned in alerts.

        This is the exact case a system-status REST sensor misses, and it is
        the reason this integration is not a template sensor.
        """
        install_api_mock(aioclient_mock)
        await setup_entry(hass, make_entry())

        state = hass.states.get(ENTITY)
        assert state is not None
        assert state.state == "on"
        assert "6320" in state.attributes["affected_trains"]
        assert any("6320" in reason for reason in state.attributes["reasons"])

    async def test_fires_on_an_alert_when_the_board_looks_fine(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """The mirror case: alerts know something the board does not."""
        install_api_mock(
            aioclient_mock,
            {
                "TrainDepartureScreens": board_with([row("6607", status="in 5 Min")]),
                "TripPlannerSchedule": {"data": {"getTripPlannerSchedule": None}},
            },
        )
        await setup_entry(hass, make_entry())

        state = hass.states.get(ENTITY)
        assert state is not None
        assert state.state == "on", "an alert naming an on-time train was ignored"
        assert "6607" in state.attributes["affected_trains"]

    async def test_matches_an_alert_that_writes_the_train_id_in_lower_case(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """An alert naming `a624` is about the board's `A624`.

        The extractor matches `train` case-insensitively but returns whatever
        the prose wrote, so before the IDs were normalized this comparison
        failed and the alert vanished -- no reason, no affected train, no
        error. Only lines with lettered IDs can hit it, which is why every
        numeric fixture in this suite passed straight through it.
        """
        install_api_mock(
            aioclient_mock,
            {
                "TrainDepartureScreens": board_with([row("A624", status="in 5 Min")]),
                "SystemStatus": alert_saying("train a624 is operating 20 min late"),
                "TripPlannerSchedule": {"data": {"getTripPlannerSchedule": None}},
            },
        )
        await setup_entry(hass, make_entry())

        state = hass.states.get(ENTITY)
        assert state is not None
        assert state.state == "on", "a lower-case alert lost its train"
        assert "A624" in state.attributes["affected_trains"]

    async def test_matches_an_alert_when_the_board_writes_the_id_in_lower_case(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """The mirror case, and the reason both sides are normalized.

        Normalizing only the alert side would leave this failing. The board and
        the alert feed are different upstream systems, and one casing mismatch
        between them has already been found -- assuming the other direction is
        safe would be an assumption, not a finding.
        """
        install_api_mock(
            aioclient_mock,
            {
                "TrainDepartureScreens": board_with([row("a624", status="in 5 Min")]),
                "SystemStatus": alert_saying("Train A624 is operating 20 min late"),
                "TripPlannerSchedule": {"data": {"getTripPlannerSchedule": None}},
            },
        )
        await setup_entry(hass, make_entry())

        state = hass.states.get(ENTITY)
        assert state is not None
        assert state.state == "on", "a lower-case board id lost its alert"
        assert "a624" in state.attributes["affected_trains"]

    async def test_quiet_when_both_feeds_are_clean(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        install_api_mock(
            aioclient_mock,
            {
                "TrainDepartureScreens": board_with([row("9999", status="in 5 Min")]),
                "SystemStatus": no_alerts(),
                "TripPlannerSchedule": {"data": {"getTripPlannerSchedule": None}},
            },
        )
        await setup_entry(hass, make_entry())

        state = hass.states.get(ENTITY)
        assert state is not None
        assert state.state == "off"
        assert state.attributes["reasons"] == []


class TestDelayThreshold:
    """Lateness."""

    async def test_fires_at_the_threshold(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """Scheduled 8:30, counting down 20 minutes from 8:20 -> 10 late."""
        install_api_mock(
            aioclient_mock,
            {
                "TrainDepartureScreens": board_with(
                    [row("9999", departure="8:30 AM", status="in 20 Min")]
                ),
                "SystemStatus": no_alerts(),
                "TripPlannerSchedule": {"data": {"getTripPlannerSchedule": None}},
            },
        )
        await setup_entry(hass, make_entry(options={CONF_DELAY_THRESHOLD: 10}))

        state = hass.states.get(ENTITY)
        assert state is not None
        assert state.state == "on"

    async def test_stays_quiet_below_the_threshold(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        install_api_mock(
            aioclient_mock,
            {
                "TrainDepartureScreens": board_with(
                    [row("9999", departure="8:30 AM", status="in 15 Min")]
                ),
                "SystemStatus": no_alerts(),
                "TripPlannerSchedule": {"data": {"getTripPlannerSchedule": None}},
            },
        )
        await setup_entry(hass, make_entry(options={CONF_DELAY_THRESHOLD: 10}))

        state = hass.states.get(ENTITY)
        assert state is not None
        assert state.state == "off"

    async def test_unknown_delay_is_not_a_disruption(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """No realtime data yet is not evidence of a problem."""
        install_api_mock(
            aioclient_mock,
            {
                "TrainDepartureScreens": board_with([row("9999", status="")]),
                "SystemStatus": no_alerts(),
                "TripPlannerSchedule": {"data": {"getTripPlannerSchedule": None}},
            },
        )
        await setup_entry(hass, make_entry())

        state = hass.states.get(ENTITY)
        assert state is not None
        assert state.state == "off"


class TestLookahead:
    """The time window."""

    async def test_ignores_departures_beyond_the_window(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """A cancellation three hours out is not the next 90 minutes' problem."""
        install_api_mock(
            aioclient_mock,
            {
                "TrainDepartureScreens": board_with(
                    [row("9999", departure="11:30 AM", status="Cancelled")]
                ),
                "SystemStatus": no_alerts(),
                "TripPlannerSchedule": {"data": {"getTripPlannerSchedule": None}},
            },
        )
        await setup_entry(hass, make_entry(options={CONF_LOOKAHEAD: 90}))

        state = hass.states.get(ENTITY)
        assert state is not None
        assert state.state == "off"
        assert state.attributes["upcoming_trains"] == []

    async def test_a_wider_window_catches_it(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        install_api_mock(
            aioclient_mock,
            {
                "TrainDepartureScreens": board_with(
                    [row("9999", departure="11:30 AM", status="Cancelled")]
                ),
                "SystemStatus": no_alerts(),
                "TripPlannerSchedule": {"data": {"getTripPlannerSchedule": None}},
            },
        )
        await setup_entry(hass, make_entry(options={CONF_LOOKAHEAD: 240}))

        state = hass.states.get(ENTITY)
        assert state is not None
        assert state.state == "on"


class TestAdvisories:
    """Planned work is not a live disruption."""

    async def test_planned_advisories_do_not_trip_it(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """Weekend track work should not read as "leave now"."""
        advisory = {
            "abbreviation": "MNE",
            "message": "Morris & Essex Lines: Possible Delays train 9999",
            "msg_richtext": "",
            "msg_url": "",
            "service": "Rail",
            "advisoryAlert": "1",
        }
        install_api_mock(
            aioclient_mock,
            {
                "TrainDepartureScreens": board_with([row("9999", status="in 5 Min")]),
                "SystemStatus": {"data": {"getSystemStatus": [advisory]}},
                "TripPlannerSchedule": {"data": {"getTripPlannerSchedule": None}},
            },
        )
        await setup_entry(hass, make_entry())

        state = hass.states.get(ENTITY)
        assert state is not None
        assert state.state == "off"


async def test_reasons_are_readable(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Reasons end up in notifications, so they must read as English."""
    install_api_mock(
        aioclient_mock,
        {
            "TrainDepartureScreens": board_with(
                [row("6320", departure="8:25 AM", status="Cancelled")]
            ),
            "SystemStatus": no_alerts(),
            "TripPlannerSchedule": {"data": {"getTripPlannerSchedule": None}},
        },
    )
    await setup_entry(hass, make_entry())

    state = hass.states.get(ENTITY)
    assert state is not None
    assert state.attributes["reasons"] == ["Train 6320 (8:25 AM) is cancelled"]


async def test_each_train_is_reported_once(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A train both cancelled and alerted must not be listed twice."""
    alerts = load_fixture("system_status_disruption")
    install_api_mock(
        aioclient_mock,
        {
            "TrainDepartureScreens": board_with(
                [row("6311", departure="8:28 AM", status="CANCELLED")]
            ),
            "SystemStatus": alerts,
            "TripPlannerSchedule": {"data": {"getTripPlannerSchedule": None}},
        },
    )
    await setup_entry(hass, make_entry())

    state = hass.states.get(ENTITY)
    assert state is not None
    assert state.attributes["affected_trains"].count("6311") == 1
    assert len(state.attributes["reasons"]) == 1
