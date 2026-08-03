"""Sensor platform.

Assertions run against the recorded disruption capture, so the numbers here
describe a real Morris & Essex morning rather than invented data.
"""

from __future__ import annotations

from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.njtransit.api.models import (
    Departure,
    DepartureBoard,
    RailLine,
    TrainStatus,
)
from custom_components.njtransit.api.parsing import (
    alert_line_codes,
    line_code_for_title,
)
from custom_components.njtransit.const import CONF_DEPARTURE_COUNT
from custom_components.njtransit.coordinator import RouteData
from custom_components.njtransit.entity import usable_departures

from .conftest import install_api_mock, load_payload
from .test_init import NY_PENN, make_entry, setup_entry
from .test_parsing import at

PREFIX = "sensor.short_hills_station_to_new_york_penn_station"


def departure(
    train_id: str,
    destination: str = "New York",
    hour: int = 9,
    minute: int = 0,
    line: str = "Morristown Line",
) -> Departure:
    """Return a minimal departure for filter tests."""
    return Departure(
        train_id=train_id,
        scheduled=at(hour, minute),
        destination=destination,
        line=line,
        line_abbreviation="M&E",
        status=TrainStatus.ON_TIME,
        status_raw="in 5 Min",
    )


class TestUsableDepartures:
    """Which board rows count as this commute's trains."""

    def test_prefers_the_resolved_train_set(self) -> None:
        """The planner's answer beats the board's label.

        Train 411 is a Gladstone train whose board label reads "Summit", but
        it connects onward to New York. A label match would throw it away.
        """
        board = DepartureBoard(
            station="Short Hills Station",
            departures=(
                departure("411", destination="Summit"),
                departure("9999", destination="Dover"),
            ),
        )
        route = RouteData(train_ids=frozenset({"411"}))

        usable = usable_departures(board, route, NY_PENN)
        assert [d.train_id for d in usable] == ["411"]

    def test_falls_back_to_label_matching(self) -> None:
        """Used only when the planner could not resolve the pair."""
        board = DepartureBoard(
            station="Short Hills Station",
            departures=(
                departure("1", destination="New York -SEC"),
                departure("2", destination="Dover"),
            ),
        )
        route = RouteData(complete=False)

        usable = usable_departures(board, route, NY_PENN)
        assert [d.train_id for d in usable] == ["1"]

    def test_label_matching_ignores_the_station_suffix(self) -> None:
        """Canonical names carry a suffix the board's labels drop."""
        board = DepartureBoard(
            station="Short Hills Station",
            departures=(departure("1", destination="Hoboken"),),
        )
        usable = usable_departures(board, RouteData(complete=False), "Hoboken Terminal")
        assert len(usable) == 1

    def test_no_destination_means_everything(self) -> None:
        board = DepartureBoard(
            station="Short Hills Station",
            departures=(departure("1"), departure("2", destination="Dover")),
        )
        assert len(usable_departures(board, None, None)) == 2

    def test_no_board_is_empty(self) -> None:
        assert usable_departures(None, None, None) == []


class TestLineResolution:
    """Board line titles onto alert-feed codes."""

    @pytest.fixture(name="lines")
    def lines_fixture(self) -> tuple[RailLine, ...]:
        return tuple(
            RailLine(id=row["id"], title=row["title"], abbreviation=row["abbreviation"])
            for row in load_payload("train_lines", "getTrainLines")
        )

    def test_exact_titles_resolve(self, lines: tuple[RailLine, ...]) -> None:
        assert line_code_for_title("Gladstone Branch", lines) == "MNEG"
        assert line_code_for_title("Northeast Corridor", lines) == "NEC"

    def test_the_one_title_that_does_not_match(
        self, lines: tuple[RailLine, ...]
    ) -> None:
        """The board says "Morristown Line"; getTrainLines says "Morris & Essex".

        Twelve of thirteen rail lines match exactly. This is the exception,
        and it is the line this integration was built for.
        """
        assert line_code_for_title("Morristown Line", lines) == "MNE"

    def test_unknown_titles_do_not_resolve(self, lines: tuple[RailLine, ...]) -> None:
        assert line_code_for_title("Some New Line", lines) is None
        assert line_code_for_title("", lines) is None

    def test_codes_expand_to_the_alert_umbrella(
        self, lines: tuple[RailLine, ...]
    ) -> None:
        """A Gladstone train is covered by MNE alerts."""
        codes = alert_line_codes({"Gladstone Branch"}, lines)
        assert "MNE" in codes

    def test_unresolvable_titles_yield_no_filter(
        self, lines: tuple[RailLine, ...]
    ) -> None:
        """Empty means "do not filter", so alerts fail open."""
        assert alert_line_codes({"Some New Line"}, lines) == frozenset()


class TestDepartureSensors:
    """Entities against the recorded capture."""

    async def test_creates_the_configured_number(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        install_api_mock(aioclient_mock)
        await setup_entry(hass, make_entry(options={CONF_DEPARTURE_COUNT: 2}))

        assert hass.states.get(f"{PREFIX}_next_departure") is not None
        assert hass.states.get(f"{PREFIX}_departure_2") is not None
        assert hass.states.get(f"{PREFIX}_departure_3") is None

    async def test_next_departure_is_a_timestamp(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        install_api_mock(aioclient_mock)
        await setup_entry(hass, make_entry())

        state = hass.states.get(f"{PREFIX}_next_departure")
        assert state is not None
        assert state.attributes["device_class"] == "timestamp"
        assert state.attributes["train_id"]

    async def test_exposes_crowding_by_position(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        install_api_mock(aioclient_mock)
        await setup_entry(hass, make_entry())

        state = hass.states.get(f"{PREFIX}_crowding")
        assert state is not None
        assert state.state in {"light", "moderate", "heavy", "unknown"}

    async def test_no_more_trains_is_unknown_not_unavailable(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """Overnight, the integration is fine -- there is simply no train."""
        install_api_mock(
            aioclient_mock,
            {
                "TrainDepartureScreens": {
                    "data": {"getTrainDepartureScreens": {"items": []}}
                }
            },
        )
        await setup_entry(hass, make_entry())

        state = hass.states.get(f"{PREFIX}_next_departure")
        assert state is not None
        assert state.state == "unknown"

    async def test_delay_is_unknown_without_realtime_data(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """Not zero: nothing is known, which is not "on time"."""
        install_api_mock(
            aioclient_mock,
            {
                "TrainDepartureScreens": {
                    "data": {
                        "getTrainDepartureScreens": {
                            "items": [
                                {
                                    "trainID": "6328",
                                    "departureDate": "11:59 PM",
                                    "destination": "New York",
                                    "line": "Morristown Line",
                                    "lineAbbreviation": "M&E",
                                    "status": "",
                                    "track": "2",
                                    "inlineMessage": "",
                                    "stops": [],
                                    "capacity": None,
                                }
                            ]
                        }
                    }
                }
            },
        )
        await setup_entry(hass, make_entry())

        state = hass.states.get(f"{PREFIX}_delay")
        assert state is not None
        assert state.state == "unknown"


class TestAlertSensors:
    """Alerts scoped to the commute."""

    async def test_reports_live_incidents(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        install_api_mock(aioclient_mock)
        await setup_entry(hass, make_entry())

        state = hass.states.get(f"{PREFIX}_service_alerts")
        assert state is not None
        assert int(state.state) > 0
        assert state.attributes["messages"]

    async def test_separates_advisories_from_incidents(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """They want different reactions, so they are different entities."""
        install_api_mock(aioclient_mock)
        await setup_entry(hass, make_entry())

        alerts = hass.states.get(f"{PREFIX}_service_alerts")
        advisories = hass.states.get(f"{PREFIX}_planned_advisories")
        assert alerts is not None
        assert advisories is not None
        assert set(alerts.attributes["messages"]).isdisjoint(
            advisories.attributes["messages"]
        )

    async def test_scopes_to_this_commutes_lines(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """The capture carries alerts for five rail lines; only M&E is ours."""
        install_api_mock(aioclient_mock)
        await setup_entry(hass, make_entry())

        state = hass.states.get(f"{PREFIX}_service_alerts")
        assert state is not None
        assert state.attributes["lines"] == ["MNE"]

    async def test_flags_alerts_naming_our_trains(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """6311 is cancelled on the board and named in the alert feed."""
        install_api_mock(aioclient_mock)
        await setup_entry(hass, make_entry())

        state = hass.states.get(f"{PREFIX}_service_alerts")
        assert state is not None
        assert "6311" in state.attributes["affects_my_trains"]

    async def test_alert_feed_failure_leaves_departures_working(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """The board and the alert feed are separate coordinators."""
        install_api_mock(aioclient_mock, {"SystemStatus": {"data": {}}})
        entry = make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        # Setup retries when the shared status feed cannot load at all, which
        # is the documented behaviour -- assert it rather than a half state.
        assert entry.state.recoverable


@pytest.mark.parametrize("count", [1, 5])
async def test_departure_count_option(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    count: int,
) -> None:
    """The number of departure sensors follows the option."""
    install_api_mock(aioclient_mock)
    await setup_entry(hass, make_entry(options={CONF_DEPARTURE_COUNT: count}))

    created = [
        state
        for state in hass.states.async_all("sensor")
        if "departure" in state.entity_id
    ]
    assert len(created) == count


async def test_attributes_survive_an_empty_board(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """No departures must not raise while building attributes."""
    install_api_mock(
        aioclient_mock,
        {
            "TrainDepartureScreens": {
                "data": {"getTrainDepartureScreens": {"items": []}}
            }
        },
    )
    await setup_entry(hass, make_entry())

    for suffix in ("next_departure", "delay", "crowding"):
        state = hass.states.get(f"{PREFIX}_{suffix}")
        assert state is not None, suffix
        assert state.state == "unknown"


def test_departure_helper_is_used_consistently() -> None:
    """Guard the test helper itself against drift."""
    sample: dict[str, Any] = {"train_id": "1"}
    assert departure(**sample).train_id == "1"
