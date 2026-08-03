"""Tests for the GraphQL transport and the planner paging loop."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from datetime import date, datetime, timedelta
from typing import Any

import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)
from yarl import URL

from custom_components.njtransit.api.client import NJTransitClient
from custom_components.njtransit.api.exceptions import (
    NJTransitAPIError,
    NJTransitConnectionError,
    NJTransitNotFoundError,
    NJTransitRequestError,
)
from custom_components.njtransit.api.parsing import TZ
from custom_components.njtransit.api.queries import (
    ENDPOINT,
    PLANNER_DATE_FORMAT,
    PLANNER_TIME_FORMAT,
)

from .conftest import load_fixture

SERVICE_DATE = date(2026, 8, 4)


@pytest.fixture(name="client_for")
async def client_for_fixture() -> AsyncIterator[
    Callable[[AiohttpClientMocker], NJTransitClient]
]:
    """Return a factory for clients backed by a mocked session.

    Sessions are closed on teardown; leaking them makes aiohttp complain
    loudly enough to bury real failures.
    """
    sessions = []

    def build(mocker: AiohttpClientMocker) -> NJTransitClient:
        session = mocker.create_session(asyncio.get_running_loop())
        sessions.append(session)
        return NJTransitClient(session)

    yield build

    for session in sessions:
        await session.close()


class TestExecute:
    """Error handling in the shared request path."""

    async def test_returns_the_root_field(
        self, client_for: Callable[[AiohttpClientMocker], NJTransitClient]
    ) -> None:
        mocker = AiohttpClientMocker()
        mocker.post(
            ENDPOINT,
            json={
                "data": {
                    "getTrainLines": [
                        {
                            "id": "1",
                            "title": "Morris & Essex Line",
                            "abbreviation": "MNE",
                        },
                    ]
                }
            },
        )
        lines = await client_for(mocker).train_lines()
        assert [line.abbreviation for line in lines] == ["MNE"]

    async def test_waf_rejection_is_not_a_graphql_error(
        self, client_for: Callable[[AiohttpClientMocker], NJTransitClient]
    ) -> None:
        """The WAF answers in its own shape, before GraphQL sees anything.

        It has no `data` key at all, so it must not be mistaken for a
        successful response with a null payload.
        """
        mocker = AiohttpClientMocker()
        mocker.post(
            ENDPOINT,
            status=400,
            json={"status": 400, "message": "Malformed request"},
        )
        with pytest.raises(NJTransitRequestError, match="Malformed request"):
            await client_for(mocker).train_lines()

    async def test_graphql_errors_nested_under_data(
        self, client_for: Callable[[AiohttpClientMocker], NJTransitClient]
    ) -> None:
        """Application-level failures nest their errors inside `data`."""
        mocker = AiohttpClientMocker()
        mocker.post(
            ENDPOINT,
            status=400,
            json={
                "data": {
                    "errors": [
                        {"message": "unable to find trips"},
                    ]
                }
            },
        )
        with pytest.raises(NJTransitAPIError, match="unable to find trips"):
            await client_for(mocker).train_lines()

    async def test_graphql_errors_at_the_top_level(
        self, client_for: Callable[[AiohttpClientMocker], NJTransitClient]
    ) -> None:
        """Validation failures put errors beside `data`, not inside it."""
        mocker = AiohttpClientMocker()
        mocker.post(
            ENDPOINT,
            status=400,
            json={
                "data": {},
                "errors": [{"message": 'Cannot query field "transfers"'}],
            },
        )
        with pytest.raises(NJTransitAPIError, match="transfers"):
            await client_for(mocker).train_lines()

    async def test_null_payload_is_not_found(
        self, client_for: Callable[[AiohttpClientMocker], NJTransitClient]
    ) -> None:
        """An unrecognized station returns null rather than erroring."""
        mocker = AiohttpClientMocker()
        mocker.post(ENDPOINT, json={"data": {"getTrainDepartureScreens": None}})
        with pytest.raises(NJTransitNotFoundError):
            await client_for(mocker).departures("Nowhere")

    async def test_missing_data_key(
        self, client_for: Callable[[AiohttpClientMocker], NJTransitClient]
    ) -> None:
        mocker = AiohttpClientMocker()
        mocker.post(ENDPOINT, json={"whatever": True})
        with pytest.raises(NJTransitAPIError):
            await client_for(mocker).train_lines()

    async def test_non_object_payload(
        self, client_for: Callable[[AiohttpClientMocker], NJTransitClient]
    ) -> None:
        mocker = AiohttpClientMocker()
        mocker.post(ENDPOINT, json=["not", "an", "object"])
        with pytest.raises(NJTransitConnectionError, match="expected an object"):
            await client_for(mocker).train_lines()

    async def test_transport_failure(
        self, client_for: Callable[[AiohttpClientMocker], NJTransitClient]
    ) -> None:
        mocker = AiohttpClientMocker()
        mocker.post(ENDPOINT, exc=TimeoutError())
        with pytest.raises(NJTransitConnectionError):
            await client_for(mocker).train_lines()


class TestEndpoints:
    """Each public call, against the recorded capture."""

    async def test_system_status(
        self, client_for: Callable[[AiohttpClientMocker], NJTransitClient]
    ) -> None:
        mocker = AiohttpClientMocker()
        mocker.post(ENDPOINT, json=load_fixture("system_status_disruption"))
        alerts = await client_for(mocker).system_status()
        assert any(a.line_abbreviation == "MNE" and not a.is_advisory for a in alerts)

    async def test_departures(
        self, client_for: Callable[[AiohttpClientMocker], NJTransitClient]
    ) -> None:
        mocker = AiohttpClientMocker()
        mocker.post(ENDPOINT, json=load_fixture("departures_short_hills_disruption"))
        board = await client_for(mocker).departures("Short Hills Station")
        assert board.station == "Short Hills Station"
        assert len(board.departures) == 19

    async def test_stations_keeps_aliases(
        self, client_for: Callable[[AiohttpClientMocker], NJTransitClient]
    ) -> None:
        mocker = AiohttpClientMocker()
        mocker.post(ENDPOINT, json=load_fixture("stations_rail_dv"))
        stations = await client_for(mocker).stations()
        assert len({s.penta_id for s in stations}) < len(stations)


class FakePlanner:
    """Serves planner pages from an observed service day.

    Mimics the endpoint's one hard constraint: **exactly three itineraries per
    call**, starting from the requested time. That is what forces the paging
    loop to exist at all.
    """

    PAGE_SIZE = 3

    def __init__(self, trips: list[dict[str, Any]]) -> None:
        self.trips = sorted(trips, key=lambda trip: trip["departure"])
        self.requests: list[datetime] = []

    def page_for(self, requested: datetime) -> dict[str, Any]:
        """Return the response for a request at ``requested``."""
        self.requests.append(requested)
        upcoming = [
            trip
            for trip in self.trips
            if datetime.fromisoformat(trip["departure"]).replace(tzinfo=TZ) >= requested
        ][: self.PAGE_SIZE]

        if not upcoming:
            return {"data": {"getTripPlannerSchedule": None}}

        return {
            "data": {
                "getTripPlannerSchedule": [
                    {
                        "duration": trip["duration"],
                        "legs": [
                            {
                                "block": trip["train_id"],
                                "route": "MNE",
                                "routeType": "C",
                                "onStopDescription": "SHORT HILLS",
                                "onStopTime": datetime.fromisoformat(
                                    trip["departure"]
                                ).strftime(PLANNER_TIME_FORMAT),
                                "offStopDescription": "NEW YORK PENN STATION",
                                "offStopTime": (
                                    datetime.fromisoformat(trip["departure"])
                                    + timedelta(minutes=39)
                                ).strftime(PLANNER_TIME_FORMAT),
                            },
                            # The sentinel leg the planner appends.
                            {
                                "block": None,
                                "route": "MNE",
                                "routeType": "C",
                                "onStopDescription": "NEW YORK PENN STATION",
                                "onStopTime": "9:31 AM",
                                "offStopDescription": None,
                                "offStopTime": "9:31 AM",
                            },
                        ],
                    }
                    for trip in upcoming
                ]
            }
        }

    def install(self, mocker: AiohttpClientMocker) -> None:
        """Wire this planner into a mocked session."""

        async def respond(
            method: str, url: URL, data: dict[str, Any]
        ) -> AiohttpClientMockResponse:
            variables = data["variables"]
            requested = datetime.strptime(
                f"{variables['date']} {variables['time']}",
                f"{PLANNER_DATE_FORMAT} {PLANNER_TIME_FORMAT}",
            ).replace(tzinfo=TZ)
            return AiohttpClientMockResponse(
                method="POST", url=URL(ENDPOINT), json=self.page_for(requested)
            )

        mocker.post(ENDPOINT, side_effect=respond)


@pytest.fixture(name="observed_day")
def observed_day_fixture() -> list[dict[str, Any]]:
    """Return the observed Short Hills -> NY Penn service day."""
    return load_fixture("planner_day_short_hills_to_ny")["trips"]


class TestScheduledTrips:
    """The paging loop."""

    async def test_covers_the_whole_service_day(
        self,
        client_for: Callable[[AiohttpClientMocker], NJTransitClient],
        observed_day: list[dict[str, Any]],
    ) -> None:
        """51 trains, not 4.

        A single planner call returns three itineraries -- four distinct
        trains for this pair. The real service day has 51. This number is the
        regression guard for a bug the spec itself had before it was caught;
        see SPEC 2.6.
        """
        mocker = AiohttpClientMocker()
        planner = FakePlanner(observed_day)
        planner.install(mocker)

        trips = await client_for(mocker).scheduled_trips(
            "Short Hills Station", "New York Penn Station", on=SERVICE_DATE
        )

        assert len(trips) == 51
        assert len(planner.requests) < 30, "paging got less efficient"

    async def test_returns_trips_in_departure_order(
        self,
        client_for: Callable[[AiohttpClientMocker], NJTransitClient],
        observed_day: list[dict[str, Any]],
    ) -> None:
        mocker = AiohttpClientMocker()
        FakePlanner(observed_day).install(mocker)
        trips = await client_for(mocker).scheduled_trips(
            "Short Hills Station", "New York Penn Station", on=SERVICE_DATE
        )
        departures = [trip.departure for trip in trips]
        assert departures == sorted(departures)

    async def test_deduplicates_trains_seen_on_overlapping_pages(
        self,
        client_for: Callable[[AiohttpClientMocker], NJTransitClient],
        observed_day: list[dict[str, Any]],
    ) -> None:
        mocker = AiohttpClientMocker()
        FakePlanner(observed_day).install(mocker)
        trips = await client_for(mocker).scheduled_trips(
            "Short Hills Station", "New York Penn Station", on=SERVICE_DATE
        )
        train_ids = [trip.train_id for trip in trips]
        assert len(train_ids) == len(set(train_ids))

    async def test_survives_a_window_that_never_advances(
        self, client_for: Callable[[AiohttpClientMocker], NJTransitClient]
    ) -> None:
        """Three itineraries sharing a departure time must not loop forever.

        Without the nudge the cursor would never move past them.
        """
        stuck = [
            {
                "train_id": f"90{n}",
                "departure": "2026-08-04T09:00:00",
                "duration": "39 min",
            }
            for n in range(3)
        ]
        mocker = AiohttpClientMocker()
        planner = FakePlanner(stuck)
        planner.install(mocker)

        trips = await client_for(mocker).scheduled_trips(
            "Short Hills Station", "New York Penn Station", on=SERVICE_DATE
        )

        assert len(trips) == 3
        assert len(planner.requests) < 40, "the page cap was hit instead of the nudge"

    async def test_no_service_at_all_raises(
        self, client_for: Callable[[AiohttpClientMocker], NJTransitClient]
    ) -> None:
        mocker = AiohttpClientMocker()
        mocker.post(ENDPOINT, json={"data": {"getTripPlannerSchedule": None}})
        with pytest.raises(NJTransitNotFoundError):
            await client_for(mocker).scheduled_trips(
                "Short Hills Station", "Nowhere", on=SERVICE_DATE
            )

    async def test_running_off_the_end_of_the_day_is_not_an_error(
        self,
        client_for: Callable[[AiohttpClientMocker], NJTransitClient],
        observed_day: list[dict[str, Any]],
    ) -> None:
        """The last page returning nothing is how the loop normally ends."""
        mocker = AiohttpClientMocker()
        FakePlanner(observed_day[:5]).install(mocker)
        trips = await client_for(mocker).scheduled_trips(
            "Short Hills Station", "New York Penn Station", on=SERVICE_DATE
        )
        assert len(trips) == 5

    async def test_sends_the_formats_the_planner_demands(
        self,
        client_for: Callable[[AiohttpClientMocker], NJTransitClient],
        observed_day: list[dict[str, Any]],
    ) -> None:
        """An ISO date returns HTTP 500; the planner wants MM/DD/YYYY."""
        mocker = AiohttpClientMocker()
        planner = FakePlanner(observed_day[:3])
        planner.install(mocker)
        await client_for(mocker).scheduled_trips(
            "Short Hills Station", "New York Penn Station", on=SERVICE_DATE
        )
        sent = mocker.mock_calls[0][2]["variables"]
        assert sent["date"] == "08/04/2026"
        assert sent["time"].endswith(("AM", "PM"))
