"""Config and options flow."""

from __future__ import annotations

from typing import Any

import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.njtransit.api.models import Station
from custom_components.njtransit.config_flow import canonical_stations
from custom_components.njtransit.const import (
    CONF_DELAY_THRESHOLD,
    CONF_DEPARTURE_INTERVAL,
    CONF_DESTINATION,
    CONF_DESTINATION_ID,
    CONF_ORIGIN,
    CONF_ORIGIN_ID,
    DOMAIN,
)

from .conftest import install_api_mock, load_payload
from .test_init import HOBOKEN, NY_PENN, SHORT_HILLS, make_entry, setup_entry


async def start_flow(hass: HomeAssistant) -> Any:
    """Begin the user flow."""
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )


class TestCanonicalStations:
    """Collapsing the alias rows."""

    def test_collapses_aliases_to_one_per_station(self) -> None:
        """177 upstream rows describe 167 stations."""
        stations = tuple(
            Station(
                title=row["title"],
                penta_id=row["pentaStationID"],
                accessible=row["accessible"],
            )
            for row in load_payload(
                "stations_rail_dv", "getTrainScheduleStationsRailForDV"
            )
        )
        collapsed = canonical_stations(stations)

        assert len(collapsed) == 167
        assert len({station.penta_id for station in collapsed}) == len(collapsed)

    @pytest.mark.parametrize(
        ("aliases", "expected"),
        [
            # Longest wins: the most descriptive of the aliases.
            (
                ["MSU Station", "Montclair State University Station"],
                "Montclair State University Station",
            ),
            (
                ["Matawan Station", "Aberdeen Matawan Station"],
                "Aberdeen Matawan Station",
            ),
            # Equal length falls back to alphabetical, so the choice is
            # stable if upstream reorders the list.
            (
                ["Penn Station New York", "New York Penn Station"],
                "New York Penn Station",
            ),
        ],
    )
    def test_picks_the_most_descriptive_alias(
        self, aliases: list[str], expected: str
    ) -> None:
        stations = tuple(
            Station(title=title, penta_id="XX", accessible=None) for title in aliases
        )
        assert canonical_stations(stations)[0].title == expected

    def test_is_order_independent(self) -> None:
        """Upstream reordering must not change which alias is chosen."""
        aliases = ["NY Penn Station", "New York Penn Station", "Penn Station New York"]
        forward = tuple(Station(t, "NY", None) for t in aliases)
        backward = tuple(Station(t, "NY", None) for t in reversed(aliases))
        assert canonical_stations(forward) == canonical_stations(backward)


class TestUserFlow:
    """Creating a commute."""

    async def test_creates_an_entry(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        install_api_mock(aioclient_mock)
        result = await start_flow(hass)
        assert result["type"] is FlowResultType.FORM

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_ORIGIN: SHORT_HILLS, CONF_DESTINATION: NY_PENN},
        )
        await hass.async_block_till_done()

        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert result["title"] == f"{SHORT_HILLS} to {NY_PENN}"
        assert result["data"] == {
            CONF_ORIGIN: SHORT_HILLS,
            CONF_ORIGIN_ID: "RT",
            CONF_DESTINATION: NY_PENN,
            CONF_DESTINATION_ID: "NY",
        }

    async def test_destination_is_optional(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        install_api_mock(aioclient_mock)
        result = await start_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_ORIGIN: SHORT_HILLS}
        )
        await hass.async_block_till_done()

        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert CONF_DESTINATION not in result["data"]

    async def test_unique_id_is_the_pair(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """Keyed on origin alone, the second commute would look duplicate."""
        install_api_mock(aioclient_mock)
        result = await start_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_ORIGIN: SHORT_HILLS, CONF_DESTINATION: NY_PENN},
        )
        await hass.async_block_till_done()

        entry = hass.config_entries.async_entries(DOMAIN)[0]
        assert entry.unique_id == "RT-NY"

    async def test_a_second_destination_from_one_origin_is_allowed(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """Short Hills to Hoboken alongside Short Hills to New York."""
        install_api_mock(aioclient_mock)
        await setup_entry(hass, make_entry())

        result = await start_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_ORIGIN: SHORT_HILLS, CONF_DESTINATION: HOBOKEN},
        )
        await hass.async_block_till_done()

        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert len(hass.config_entries.async_entries(DOMAIN)) == 2

    async def test_the_same_commute_twice_is_rejected(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        install_api_mock(aioclient_mock)
        await setup_entry(hass, make_entry())

        result = await start_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_ORIGIN: SHORT_HILLS, CONF_DESTINATION: NY_PENN},
        )

        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "already_configured"

    async def test_origin_and_destination_must_differ(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        install_api_mock(aioclient_mock)
        result = await start_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_ORIGIN: SHORT_HILLS, CONF_DESTINATION: SHORT_HILLS},
        )

        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == {"base": "same_station"}

    async def test_unrecognized_origin_is_reported(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """A null board is how the endpoint rejects a station."""
        install_api_mock(
            aioclient_mock,
            {"TrainDepartureScreens": {"data": {"getTrainDepartureScreens": None}}},
        )
        result = await start_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_ORIGIN: SHORT_HILLS}
        )

        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == {"base": "invalid_station"}

    async def test_unreachable_endpoint_aborts_before_the_form(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """The station list is needed to render the picker at all."""
        install_api_mock(
            aioclient_mock, {"TrainScheduleStationsRailForDV": TimeoutError()}
        )
        result = await start_flow(hass)

        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "cannot_connect"


class TestOptionsFlow:
    """Adjusting a commute."""

    async def test_saves_options(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        install_api_mock(aioclient_mock)
        entry = make_entry()
        await setup_entry(hass, entry)

        result = await hass.config_entries.options.async_init(entry.entry_id)
        assert result["type"] is FlowResultType.FORM

        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {CONF_DEPARTURE_INTERVAL: 120, CONF_DELAY_THRESHOLD: 5},
        )
        await hass.async_block_till_done()

        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert entry.options[CONF_DEPARTURE_INTERVAL] == 120
        assert entry.options[CONF_DELAY_THRESHOLD] == 5

    async def test_changing_options_reloads_the_entry(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        install_api_mock(aioclient_mock)
        entry = make_entry()
        await setup_entry(hass, entry)

        result = await hass.config_entries.options.async_init(entry.entry_id)
        await hass.config_entries.options.async_configure(
            result["flow_id"], {CONF_DEPARTURE_INTERVAL: 300}
        )
        await hass.async_block_till_done()

        interval = entry.runtime_data.board.update_interval
        assert interval is not None
        assert interval.total_seconds() == 300
