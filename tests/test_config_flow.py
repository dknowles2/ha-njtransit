"""Config and options flow."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from homeassistant.config_entries import SOURCE_USER, ConfigEntryState
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.njtransit.account import account_unique_id
from custom_components.njtransit.api.models import Station
from custom_components.njtransit.config_flow import canonical_stations
from custom_components.njtransit.const import (
    CONF_DELAY_THRESHOLD,
    CONF_DEPARTURE_INTERVAL,
    CONF_DESTINATION,
    CONF_DESTINATION_ID,
    CONF_FAVORITE_TRAINS,
    CONF_LOOKAHEAD,
    CONF_ORIGIN,
    CONF_ORIGIN_ID,
    CONF_SOURCE,
    DOMAIN,
    SOURCE_WEBSITE,
)

from .conftest import (
    install_api_mock,
    install_raildata_mock,
    load_fixture,
    load_payload,
)
from .test_account import make_account_entry
from .test_init import HOBOKEN, NY_PENN, SHORT_HILLS, make_entry, setup_entry


def suggested_value(result: Any, field: str) -> str | None:
    """Return a form field's pre-filled value, if the form offers one.

    Voluptuous carries a suggestion in the marker's `description`, not in the
    schema's values, so it takes a walk over the keys to read back.
    """
    for key in result["data_schema"].schema:
        if key == field:
            return (key.description or {}).get("suggested_value")
    return None


def suggested_origin(result: Any) -> str | None:
    """Return the origin field's pre-filled value, if the form offers one."""
    return suggested_value(result, CONF_ORIGIN)


async def start_menu(hass: HomeAssistant) -> Any:
    """Begin the user flow, stopping at the source menu."""
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )


async def start_flow(hass: HomeAssistant, source: str = SOURCE_WEBSITE) -> Any:
    """Begin the user flow and choose a source, landing on its next step.

    For the website that is the commute form; for RailData it is the
    credentials form.
    """
    result = await start_menu(hass)
    assert result["type"] is FlowResultType.MENU
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": source}
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
            CONF_SOURCE: SOURCE_WEBSITE,
        }

    async def test_suggests_the_station_nearest_home(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """Home Assistant knows where home is; the endpoint knows what is near it.

        Between them the station someone actually leaves from is derivable,
        which beats hunting for it in a list of 167. Only a suggestion -- the
        dropdown is unchanged and the field stays required.
        """
        hass.config.latitude = 40.7252
        hass.config.longitude = -74.3238
        install_api_mock(aioclient_mock)

        result = await start_flow(hass)

        assert result["type"] is FlowResultType.FORM
        assert suggested_origin(result) == SHORT_HILLS

    async def test_the_suggestion_ranks_by_distance_not_by_reply_order(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """The recorded reply lists Millburn first, taken *at* Short Hills.

        Trusting the order would suggest the wrong station to someone standing
        on the right platform.
        """
        hass.config.latitude = 40.7252
        hass.config.longitude = -74.3238
        install_api_mock(aioclient_mock)
        first = load_fixture("nearest_stations_short_hills")["data"][
            "getTrainScheduleStationsRailForDVClose"
        ][0]
        assert first["title"] == "Millburn", "fixture no longer exercises the sort"

        result = await start_flow(hass)

        assert suggested_origin(result) == SHORT_HILLS

    @pytest.mark.parametrize(
        "override",
        [{"DVCloseStation": TimeoutError()}, {"DVCloseStation": {"data": {}}}],
        ids=["unreachable", "no payload"],
    )
    async def test_setup_still_works_without_a_suggestion(
        self,
        hass: HomeAssistant,
        aioclient_mock: AiohttpClientMocker,
        override: dict[str, object],
    ) -> None:
        """A convenience must never be able to block setup."""
        hass.config.latitude = 40.7252
        hass.config.longitude = -74.3238
        install_api_mock(aioclient_mock, override)

        result = await start_flow(hass)

        assert result["type"] is FlowResultType.FORM
        assert suggested_origin(result) is None

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_ORIGIN: SHORT_HILLS, CONF_DESTINATION: NY_PENN}
        )
        await hass.async_block_till_done()
        assert result["type"] is FlowResultType.CREATE_ENTRY

    async def test_no_suggestion_without_a_home_location(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """A fresh install has no coordinates, and 0,0 is in the Atlantic."""
        hass.config.latitude = 0.0
        hass.config.longitude = 0.0
        install_api_mock(aioclient_mock)

        result = await start_flow(hass)

        assert suggested_origin(result) is None

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
        assert suggested_value(result, CONF_ORIGIN) == SHORT_HILLS
        assert suggested_value(result, CONF_DESTINATION) == SHORT_HILLS

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
            result["flow_id"],
            {CONF_ORIGIN: SHORT_HILLS, CONF_DESTINATION: NY_PENN},
        )

        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == {"base": "invalid_station"}
        assert suggested_value(result, CONF_ORIGIN) == SHORT_HILLS
        assert suggested_value(result, CONF_DESTINATION) == NY_PENN

    async def test_rounds_home_coordinates_before_nearest_station_lookup(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """Home coordinates are rounded to ~1.1 km before calling njtransit.com."""
        hass.config.latitude = 40.7252987
        hass.config.longitude = -74.3238123
        install_api_mock(aioclient_mock)

        result = await start_flow(hass)

        assert result["type"] is FlowResultType.FORM
        close_calls = [
            call[2]
            for call in aioclient_mock.mock_calls
            if isinstance(call[2], dict)
            and call[2].get("operationName") == "DVCloseStation"
        ]
        assert len(close_calls) == 1
        assert close_calls[0]["variables"]["_latLong"] == "40.73,-74.32"

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

    async def test_minute_options_carry_minute_units(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """Delay threshold and lookahead show 'min' units alongside the 's' intervals."""
        install_api_mock(aioclient_mock)
        entry = make_entry()
        await setup_entry(hass, entry)

        result = await hass.config_entries.options.async_init(entry.entry_id)
        data_schema = result["data_schema"]
        assert data_schema is not None
        selectors = {str(key): value for key, value in data_schema.schema.items()}
        assert selectors[CONF_DELAY_THRESHOLD].config["unit_of_measurement"] == "min"
        assert selectors[CONF_LOOKAHEAD].config["unit_of_measurement"] == "min"

    async def test_account_entry_options_aborts_cleanly(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """A RailData account entry has no options of its own and must not crash."""
        install_api_mock(aioclient_mock)
        install_raildata_mock(aioclient_mock)
        account = make_account_entry()
        account.add_to_hass(hass)
        await hass.config_entries.async_setup(account.entry_id)
        await hass.async_block_till_done()
        assert account.state is ConfigEntryState.LOADED

        result = await hass.config_entries.options.async_init(account.entry_id)
        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "account_no_options"


class TestFavoritesSelector:
    """The favourite-train picker in the options form."""

    @staticmethod
    def _field(result: Any) -> Any:
        """Return the favourite_trains schema field from a form result."""
        for key, value in result["data_schema"].schema.items():
            if key == CONF_FAVORITE_TRAINS:
                return value
        raise AssertionError("favorite_trains not in the options schema")

    async def test_offers_the_trains_that_serve_this_commute(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """Labelled by time -- nobody memorises which number is the 7:33."""
        install_api_mock(aioclient_mock)
        entry = make_entry()
        await setup_entry(hass, entry)

        result = await hass.config_entries.options.async_init(entry.entry_id)
        selector = self._field(result)

        options = selector.config["options"]
        assert options, "no trains offered"
        assert selector.config["multiple"] is True
        # Free text stays available: the timetable is not the only truth.
        assert selector.config["custom_value"] is True

        labels = {option["value"]: option["label"] for option in options}
        assert any(
            value in label and ("AM" in label or "PM" in label)
            for value, label in labels.items()
        ), labels

    async def test_keeps_a_saved_favorite_that_is_not_running_today(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """A weekday favourite edited on a weekend must not vanish.

        Without this, opening the form on a day the train does not run would
        silently drop it from the selection on save.
        """
        install_api_mock(aioclient_mock)
        entry = make_entry(options={CONF_FAVORITE_TRAINS: ["Z999"]})
        await setup_entry(hass, entry)

        result = await hass.config_entries.options.async_init(entry.entry_id)
        values = {option["value"] for option in self._field(result).config["options"]}
        assert "Z999" in values

    async def test_falls_back_to_text_without_a_schedule(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """An unconfigurable option is worse than an unvalidated one."""
        install_api_mock(
            aioclient_mock,
            {"TripPlannerSchedule": {"data": {"getTripPlannerSchedule": []}}},
        )
        entry = make_entry()
        await setup_entry(hass, entry)

        selector = self._field(
            await hass.config_entries.options.async_init(entry.entry_id)
        )
        assert selector.config["multiple"] is True
        assert "options" not in selector.config


class TestAccountReconfigureAndReauth:
    """Account credential rotation via reconfigure and username-locked reauth."""

    async def test_account_reconfigure_updates_password(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        install_api_mock(aioclient_mock)
        install_raildata_mock(aioclient_mock)
        account = make_account_entry("someone")
        account.add_to_hass(hass)
        await hass.config_entries.async_setup(account.entry_id)
        await hass.async_block_till_done()

        result = await account.start_reconfigure_flow(hass)
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "reconfigure_account"
        assert result["description_placeholders"] == {"username": "someone"}

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_USERNAME: "someone", CONF_PASSWORD: "rotated"},
        )
        await hass.async_block_till_done()

        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "reconfigure_successful"
        assert account.data[CONF_USERNAME] == "someone"
        assert account.data[CONF_PASSWORD] == "rotated"
        assert account.state is ConfigEntryState.LOADED

    async def test_account_reconfigure_rejects_bad_password(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        install_api_mock(aioclient_mock)
        install_raildata_mock(aioclient_mock)
        account = make_account_entry("someone")
        account.add_to_hass(hass)
        await hass.config_entries.async_setup(account.entry_id)
        await hass.async_block_till_done()

        aioclient_mock.clear_requests()
        install_raildata_mock(aioclient_mock, authenticated=False)
        result = await account.start_reconfigure_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_USERNAME: "someone", CONF_PASSWORD: "wrong"},
        )

        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "reconfigure_account"
        assert result["errors"] == {"base": "invalid_auth"}
        assert account.data[CONF_PASSWORD] == "secret"

    async def test_account_reconfigure_aborts_on_different_username(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """Changing the username during reconfigure must abort without spending quota."""
        install_api_mock(aioclient_mock)
        install_raildata_mock(aioclient_mock)
        account = make_account_entry("someone")
        account.add_to_hass(hass)
        await hass.config_entries.async_setup(account.entry_id)
        await hass.async_block_till_done()

        aioclient_mock.clear_requests()
        called = install_raildata_mock(aioclient_mock)
        result = await account.start_reconfigure_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_USERNAME: "someone-else", CONF_PASSWORD: "rotated"},
        )

        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "wrong_account"
        assert sum(1 for c in called if c["method"] == "getToken") == 0
        assert account.unique_id == account_unique_id("someone")
        assert account.data[CONF_USERNAME] == "someone"
        assert account.data[CONF_PASSWORD] == "secret"

    async def test_reauth_aborts_on_different_username(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """Reauth must not let a different username diverge from entry.unique_id."""
        install_api_mock(aioclient_mock)
        install_raildata_mock(aioclient_mock)
        account = make_account_entry("someone")
        account.add_to_hass(hass)
        await hass.config_entries.async_setup(account.entry_id)
        await hass.async_block_till_done()

        aioclient_mock.clear_requests()
        called = install_raildata_mock(aioclient_mock)
        result = await account.start_reauth_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_USERNAME: "someone-else", CONF_PASSWORD: "better"},
        )

        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "wrong_account"
        assert sum(1 for c in called if c["method"] == "getToken") == 0
        assert account.unique_id == account_unique_id("someone")
        assert account.data[CONF_USERNAME] == "someone"
        assert account.data[CONF_PASSWORD] == "secret"

    async def test_reauth_accepts_case_folded_username_without_mutating_it(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """Case-only differences match the same account and preserve stored username."""
        install_api_mock(aioclient_mock)
        install_raildata_mock(aioclient_mock)
        account = make_account_entry("someone")
        account.add_to_hass(hass)
        await hass.config_entries.async_setup(account.entry_id)
        await hass.async_block_till_done()

        result = await account.start_reauth_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_USERNAME: "Someone", CONF_PASSWORD: "better"},
        )
        await hass.async_block_till_done()

        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "reauth_successful"
        assert account.data[CONF_USERNAME] == "someone"
        assert account.data[CONF_PASSWORD] == "better"


def test_strings_and_en_translation_stay_in_sync() -> None:
    """strings.json and translations/en.json must never drift."""
    root = Path(__file__).resolve().parents[1] / "custom_components" / "njtransit"
    strings = json.loads((root / "strings.json").read_text())
    en = json.loads((root / "translations" / "en.json").read_text())
    assert strings == en
    assert "account_no_options" in strings["options"]["abort"]
    assert "wrong_account" in strings["config"]["abort"]
    assert "reconfigure_account" in strings["config"]["step"]
