"""Config flow for NJ Transit.

One config entry is one *commute*, keyed on the origin/destination pair, so
Short Hills to New York Penn and Short Hills to Hoboken coexist -- as do
reverse-direction entries for the trip home. Keying on the origin alone would
make the second commute look like a duplicate.

The first question is which API to read from. The website needs nothing and
is the default; RailData needs a developer account and, in return, reports
the platform from the signalling system before the board posts it. The
choice is per commute and lives in the entry's data, so the reconfigure flow
is where it changes -- swapping the source swaps the client under every
coordinator, which is a reload rather than an option.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    SOURCE_RECONFIGURE,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
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
from .api.exceptions import (
    NJTransitAuthError,
    NJTransitConnectionError,
    NJTransitError,
    NJTransitQuotaError,
)
from .api.models import Station
from .api.raildata import RailDataClient
from .api.source import RailSource
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
    CONF_SOURCE,
    CONF_STATUS_INTERVAL,
    DEFAULT_DELAY_THRESHOLD,
    DEFAULT_DEPARTURE_COUNT,
    DEFAULT_DEPARTURE_INTERVAL,
    DEFAULT_LOOKAHEAD,
    DEFAULT_STATUS_INTERVAL,
    DOMAIN,
    MAX_DEPARTURE_COUNT,
    MIN_INTERVAL,
    SOURCE_RAILDATA,
    SOURCE_WEBSITE,
)
from .coordinator import NJTransitConfigEntry
from .raildata_store import RailDataStorage

SOURCES = (SOURCE_WEBSITE, SOURCE_RAILDATA)


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
        self._source: str = SOURCE_WEBSITE
        self._credentials: dict[str, str] = {}
        self._client: RailSource | None = None
        self._stations: list[Station] = []
        self._suggested_origin: str | None = None

    @property
    def _source_client(self) -> RailSource:
        """Return a client for the chosen source, building one on first use."""
        if self._client is None:
            session = async_get_clientsession(self.hass)
            if self._source == SOURCE_RAILDATA:
                self._client = self._raildata_client()
            else:
                self._client = NJTransitClient(session)
        return self._client

    def _raildata_client(self) -> RailDataClient:
        """Return a RailData client for the credentials entered so far.

        Backed by the account's store, so the token this flow requests is
        the one the entry it creates will find. Ten a day; the flow and the
        entry between them spend one.
        """
        username = self._credentials[CONF_USERNAME]
        return RailDataClient(
            async_get_clientsession(self.hass),
            username,
            self._credentials[CONF_PASSWORD],
            RailDataStorage(self.hass, username),
        )

    async def _load_stations(self) -> list[Station]:
        """Fetch and collapse the chosen source's station list."""
        if self._stations:
            return self._stations

        self._stations = canonical_stations(await self._source_client.stations())
        return self._stations

    # -- choosing a source ---------------------------------------------------

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose which API to read from."""
        return self.async_show_menu(step_id="user", menu_options=list(SOURCES))

    async def async_step_website(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Read from njtransit.com, which needs no account."""
        self._source = SOURCE_WEBSITE
        self._credentials = {}
        self._client = None
        self._stations = []
        if self.source == SOURCE_RECONFIGURE:
            return await self._reconfigure()
        return await self.async_step_commute()

    async def async_step_raildata(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Read from the RailData API, which needs a developer account."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self._source = SOURCE_RAILDATA
            self._credentials = {
                CONF_USERNAME: user_input[CONF_USERNAME].strip(),
                CONF_PASSWORD: user_input[CONF_PASSWORD],
            }
            self._client = None
            self._stations = []
            errors = await self._check_credentials()
            if not errors:
                if self.source == SOURCE_RECONFIGURE:
                    return await self._reconfigure()
                return await self.async_step_commute()

        return self.async_show_form(
            step_id="raildata",
            data_schema=_credentials_schema(user_input),
            errors=errors,
        )

    async def _check_credentials(self) -> dict[str, str]:
        """Exchange the credentials for a token, returning form errors."""
        client = self._raildata_client()
        try:
            await client.authenticate(fresh=True)
        except NJTransitAuthError:
            return {"base": "invalid_auth"}
        except NJTransitQuotaError:
            return {"base": "quota"}
        except NJTransitConnectionError:
            return {"base": "cannot_connect"}
        except NJTransitError:
            return {"base": "unknown"}
        # Keep the client that just signed in: the station list and the
        # origin check ride on the same token rather than each spending
        # another of the day's ten.
        self._client = client
        return {}

    # -- the commute -----------------------------------------------------------

    async def async_step_commute(
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

        if self._suggested_origin is None:
            self._suggested_origin = await self._suggest_origin(stations)

        return self.async_show_form(
            step_id="commute",
            data_schema=self._schema(stations, self._suggested_origin),
            errors=errors,
        )

    # -- changing source, and credentials ------------------------------------

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Switch an existing commute to the other source."""
        return self.async_show_menu(step_id="reconfigure", menu_options=list(SOURCES))

    async def _reconfigure(self) -> ConfigFlowResult:
        """Move the entry being reconfigured onto the chosen source.

        The stations stay -- the codes are the same on both APIs -- but the
        stored titles are re-read from the new source's list, because each
        API wants its own spelling: the website's planner rejects a bare
        ``Short Hills`` that RailData's list is happy to supply.
        """
        entry = self._get_reconfigure_entry()
        try:
            stations = await self._load_stations()
        except NJTransitConnectionError:
            return self.async_abort(reason="cannot_connect")
        except NJTransitError:
            return self.async_abort(reason="unknown")

        by_code = {station.penta_id: station for station in stations}
        updates: dict[str, Any] = {CONF_SOURCE: self._source, **self._credentials}
        origin = by_code.get(entry.data[CONF_ORIGIN_ID])
        if origin is not None:
            updates[CONF_ORIGIN] = origin.title
        destination = by_code.get(entry.data.get(CONF_DESTINATION_ID, ""))
        if destination is not None:
            updates[CONF_DESTINATION] = destination.title

        data = {
            key: value
            for key, value in entry.data.items()
            if key not in (CONF_USERNAME, CONF_PASSWORD)
        }
        return self.async_update_reload_and_abort(
            entry, data={**data, **updates}, reason="reconfigure_successful"
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        """Handle the RailData API rejecting the stored credentials."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for new credentials and check them."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            self._source = SOURCE_RAILDATA
            self._credentials = {
                CONF_USERNAME: user_input[CONF_USERNAME].strip(),
                CONF_PASSWORD: user_input[CONF_PASSWORD],
            }
            self._client = None
            errors = await self._check_credentials()
            if not errors:
                return self.async_update_reload_and_abort(
                    entry, data_updates=self._credentials
                )

        suggested = user_input or {CONF_USERNAME: entry.data.get(CONF_USERNAME, "")}
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=_credentials_schema(suggested),
            errors=errors,
        )

    async def _suggest_origin(self, stations: list[Station]) -> str | None:
        """Return the canonical title of the station nearest to home.

        Home Assistant already knows where home is, and the endpoint will say
        which stations are near a point, so the station someone actually
        leaves from is derivable rather than something to hunt for in a list
        of 167. It is only a default -- the dropdown is unchanged.

        Returns ``None`` whenever anything is missing or unrecognized. A
        suggestion is a convenience, and a wrong one is worse than none: it
        would be pre-selected, and pre-selected fields are the ones nobody
        reads.
        """
        latitude = self.hass.config.latitude
        longitude = self.hass.config.longitude
        if not latitude and not longitude:
            return None

        # Always the website. RailData publishes no coordinates, and this is
        # an anonymous lookup of a public fact, not a feed (SPEC 2.9).
        client = NJTransitClient(async_get_clientsession(self.hass))
        try:
            nearby = await client.nearest_stations(latitude, longitude)
        except NJTransitError:
            # Never block setup for a nicety.
            return None

        # The proximity search reports shorter names than the canonical list
        # ("Short Hills" against "Short Hills Station"), so match on the
        # identifier both carry rather than on the text.
        by_penta = {station.penta_id: station for station in stations}
        for candidate in nearby:
            station = by_penta.get(candidate.penta_id)
            if station is not None:
                return station.title
        return None

    async def _validate_origin(self, title: str) -> None:
        """Confirm the origin has a departure board.

        :raise NJTransitError: The station was not recognized, or the endpoint
            could not be reached.
        """
        await self._source_client.departures(title)

    @staticmethod
    def _schema(stations: list[Station], suggested: str | None = None) -> vol.Schema:
        """Return the origin/destination form schema."""
        options = station_options(stations)
        selector = SelectSelector(
            SelectSelectorConfig(options=options, mode=SelectSelectorMode.DROPDOWN)
        )
        # `description` rather than `default`: this pre-fills the field while
        # leaving it required, so a wrong guess is corrected rather than
        # silently accepted by someone pressing submit.
        origin: Any = vol.Required(CONF_ORIGIN)
        if suggested is not None:
            origin = vol.Required(
                CONF_ORIGIN, description={"suggested_value": suggested}
            )
        return vol.Schema(
            {
                origin: selector,
                vol.Optional(CONF_DESTINATION): selector,
            }
        )

    @staticmethod
    def _title(origin: Station, destination: Station | None) -> str:
        """Return the entry title."""
        if destination is None:
            return origin.title
        return f"{origin.title} to {destination.title}"

    def _data(self, origin: Station, destination: Station | None) -> dict[str, Any]:
        """Return the entry data."""
        data: dict[str, Any] = {
            CONF_ORIGIN: origin.title,
            CONF_ORIGIN_ID: origin.penta_id,
            CONF_SOURCE: self._source,
            **self._credentials,
        }
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


def _credentials_schema(suggested: dict[str, Any] | None) -> vol.Schema:
    """Return the RailData username/password form.

    The username is pre-filled on a retry so a typo in the password does not
    cost retyping both; the password never is.
    """
    username = (suggested or {}).get(CONF_USERNAME, "")
    return vol.Schema(
        {
            vol.Required(
                CONF_USERNAME, description={"suggested_value": username}
            ): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
            vol.Required(CONF_PASSWORD): TextSelector(
                TextSelectorConfig(type=TextSelectorType.PASSWORD)
            ),
        }
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
