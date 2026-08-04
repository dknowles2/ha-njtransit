"""Config flow for NJ Transit.

One config entry is one *commute*, keyed on the origin/destination pair, so
Short Hills to New York Penn and Short Hills to Hoboken coexist -- as do
reverse-direction entries for the trip home. Keying on the origin alone would
make the second commute look like a duplicate.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api.client import NJTransitClient
from .api.exceptions import NJTransitConnectionError, NJTransitError
from .api.models import Station
from .const import (
    CONF_DELAY_THRESHOLD,
    CONF_DEPARTURE_COUNT,
    CONF_DEPARTURE_INTERVAL,
    CONF_DESTINATION,
    CONF_DESTINATION_ID,
    CONF_FAVORITE_TRAINS,
    CONF_LOOKAHEAD,
    CONF_ORIGIN,
    CONF_ORIGIN_ID,
    CONF_STATUS_INTERVAL,
    DEFAULT_DELAY_THRESHOLD,
    DEFAULT_DEPARTURE_COUNT,
    DEFAULT_DEPARTURE_INTERVAL,
    DEFAULT_LOOKAHEAD,
    DEFAULT_STATUS_INTERVAL,
    DOMAIN,
    MAX_DEPARTURE_COUNT,
    MIN_INTERVAL,
)
from .coordinator import NJTransitConfigEntry


def canonical_stations(stations: tuple[Station, ...]) -> list[Station]:
    """Collapse the station list to one entry per station.

    Upstream returns 177 rows for 167 stations: seven identifiers carry alias
    titles, so New York Penn appears three times. Showing all of them makes
    the picker look broken.

    The longest title wins, ties broken alphabetically. Longest reads as the
    most descriptive of the aliases -- "Montclair State University Station"
    over "MSU Station", "Newark Liberty International Airport" over "EWR
    Newark Airport Station" -- and the tie-break keeps the choice stable
    across upstream reorderings.
    """
    best: dict[str, Station] = {}
    for station in stations:
        current = best.get(station.penta_id)
        if current is None or (-len(station.title), station.title) < (
            -len(current.title),
            current.title,
        ):
            best[station.penta_id] = station
    return sorted(best.values(), key=lambda station: station.title)


def station_options(stations: list[Station]) -> list[SelectOptionDict]:
    """Return picker options keyed by station title."""
    return [
        SelectOptionDict(value=station.title, label=station.title)
        for station in stations
    ]


class NJTransitConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for NJ Transit."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow."""
        self._stations: list[Station] = []

    async def _load_stations(self) -> list[Station]:
        """Fetch and collapse the canonical station list."""
        if self._stations:
            return self._stations

        client = NJTransitClient(async_get_clientsession(self.hass))
        self._stations = canonical_stations(await client.stations())
        return self._stations

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick an origin and destination."""
        errors: dict[str, str] = {}

        try:
            stations = await self._load_stations()
        except NJTransitConnectionError:
            return self.async_abort(reason="cannot_connect")
        except NJTransitError:
            return self.async_abort(reason="unknown")

        by_title = {station.title: station for station in stations}

        if user_input is not None:
            origin = by_title[user_input[CONF_ORIGIN]]
            destination = by_title.get(user_input.get(CONF_DESTINATION, ""))

            if destination is not None and destination.penta_id == origin.penta_id:
                errors["base"] = "same_station"
            else:
                unique_id = origin.penta_id
                if destination is not None:
                    unique_id = f"{origin.penta_id}-{destination.penta_id}"
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()

                try:
                    await self._validate_origin(origin.title)
                except NJTransitConnectionError:
                    errors["base"] = "cannot_connect"
                except NJTransitError:
                    errors["base"] = "invalid_station"
                else:
                    return self.async_create_entry(
                        title=self._title(origin, destination),
                        data=self._data(origin, destination),
                    )

        return self.async_show_form(
            step_id="user",
            data_schema=self._schema(stations),
            errors=errors,
        )

    async def _validate_origin(self, title: str) -> None:
        """Confirm the origin has a departure board.

        :raise NJTransitError: The station was not recognized, or the endpoint
            could not be reached.
        """
        client = NJTransitClient(async_get_clientsession(self.hass))
        await client.departures(title)

    @staticmethod
    def _schema(stations: list[Station]) -> vol.Schema:
        """Return the origin/destination form schema."""
        options = station_options(stations)
        selector = SelectSelector(
            SelectSelectorConfig(options=options, mode=SelectSelectorMode.DROPDOWN)
        )
        return vol.Schema(
            {
                vol.Required(CONF_ORIGIN): selector,
                vol.Optional(CONF_DESTINATION): selector,
            }
        )

    @staticmethod
    def _title(origin: Station, destination: Station | None) -> str:
        """Return the entry title."""
        if destination is None:
            return origin.title
        return f"{origin.title} to {destination.title}"

    @staticmethod
    def _data(origin: Station, destination: Station | None) -> dict[str, Any]:
        """Return the entry data."""
        data = {CONF_ORIGIN: origin.title, CONF_ORIGIN_ID: origin.penta_id}
        if destination is not None:
            data[CONF_DESTINATION] = destination.title
            data[CONF_DESTINATION_ID] = destination.penta_id
        return data

    @staticmethod
    @callback
    def async_get_options_flow(entry: NJTransitConfigEntry) -> NJTransitOptionsFlow:
        """Return the options flow."""
        return NJTransitOptionsFlow()


class NJTransitOptionsFlow(OptionsFlow):
    """Handle options for a commute.

    The destination is part of the unique ID, so it is deliberately not
    editable here -- changing it means adding another commute.
    """

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Adjust poll intervals and disruption thresholds."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        options = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_DEPARTURE_INTERVAL,
                        default=options.get(
                            CONF_DEPARTURE_INTERVAL, DEFAULT_DEPARTURE_INTERVAL
                        ),
                    ): _seconds(MIN_INTERVAL, 3600),
                    vol.Optional(
                        CONF_STATUS_INTERVAL,
                        default=options.get(
                            CONF_STATUS_INTERVAL, DEFAULT_STATUS_INTERVAL
                        ),
                    ): _seconds(MIN_INTERVAL, 3600),
                    vol.Optional(
                        CONF_DEPARTURE_COUNT,
                        default=options.get(
                            CONF_DEPARTURE_COUNT, DEFAULT_DEPARTURE_COUNT
                        ),
                    ): _count(1, MAX_DEPARTURE_COUNT),
                    vol.Optional(
                        CONF_DELAY_THRESHOLD,
                        default=options.get(
                            CONF_DELAY_THRESHOLD, DEFAULT_DELAY_THRESHOLD
                        ),
                    ): _count(1, 60),
                    vol.Optional(
                        CONF_LOOKAHEAD,
                        default=options.get(CONF_LOOKAHEAD, DEFAULT_LOOKAHEAD),
                    ): _count(15, 240),
                    vol.Optional(
                        CONF_FAVORITE_TRAINS,
                        default=list(options.get(CONF_FAVORITE_TRAINS, [])),
                    ): self._favorites_selector(
                        list(options.get(CONF_FAVORITE_TRAINS, []))
                    ),
                }
            ),
        )

    def _favorites_selector(self, current: list[str]) -> SelectSelector | TextSelector:
        """Return a picker of the trains that actually serve this commute.

        Sourced from the route coordinator, so the list is the day's direct
        services rather than every number on the board. Labelled by departure
        time, because nobody memorises which number is the 7:33.

        Falls back to free text when the entry is not loaded or the schedule
        could not be resolved -- an unconfigurable option would be worse than
        an unvalidated one.
        """
        entry = self.config_entry
        route = (
            entry.runtime_data.route.data
            if entry.state is ConfigEntryState.LOADED and hasattr(entry, "runtime_data")
            else None
        )
        if route is None or not route.trips:
            return TextSelector(
                TextSelectorConfig(multiple=True, type=TextSelectorType.TEXT)
            )

        seen: dict[str, str] = {}
        for trip in sorted(route.trips, key=lambda t: t.departure):
            clock = trip.departure.strftime("%I:%M %p").lstrip("0")
            seen.setdefault(trip.train_id, f"{trip.train_id} · {clock}")

        # A favourite saved from a weekday timetable must survive being edited
        # on a weekend, when it is in no trip and would otherwise vanish from
        # the form without anyone touching it.
        for train_id in current:
            seen.setdefault(train_id, train_id)

        return SelectSelector(
            SelectSelectorConfig(
                options=[
                    SelectOptionDict(value=train_id, label=label)
                    for train_id, label in seen.items()
                ],
                multiple=True,
                custom_value=True,
                mode=SelectSelectorMode.DROPDOWN,
                sort=False,
            )
        )


def _seconds(low: int, high: int) -> NumberSelector:
    """Return a selector for a duration in seconds."""
    return NumberSelector(
        NumberSelectorConfig(
            min=low,
            max=high,
            step=10,
            mode=NumberSelectorMode.BOX,
            unit_of_measurement="s",
        )
    )


def _count(low: int, high: int) -> NumberSelector:
    """Return a selector for a plain count."""
    return NumberSelector(
        NumberSelectorConfig(min=low, max=high, step=1, mode=NumberSelectorMode.BOX)
    )
