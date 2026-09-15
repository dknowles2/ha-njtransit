"""The RailData transport: tokens, quotas, station names, and the signal."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Generator
from datetime import date, datetime, timedelta
from typing import Any
from unittest.mock import patch

import aiohttp
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.njtransit.api.exceptions import (
    NJTransitAPIError,
    NJTransitAuthError,
    NJTransitConnectionError,
    NJTransitNotFoundError,
    NJTransitQuotaError,
)
from custom_components.njtransit.api.parsing import TZ
from custom_components.njtransit.api.raildata import (
    ENDPOINT,
    TOKEN_LIFETIME,
    RailDataClient,
)
from custom_components.njtransit.api.raildata_parsing import LINES

from .conftest import TEST_TOKEN, MemoryStore, install_raildata_mock

# When the fixtures were recorded, so "today" lines up with the schedule.
RECORDED_AT = datetime(2026, 9, 14, 21, 27, tzinfo=TZ)

ClientFactory = Callable[..., RailDataClient]


@pytest.fixture(name="client_for")
async def client_for_fixture() -> AsyncIterator[ClientFactory]:
    """Return a factory for clients backed by a mocked session."""
    sessions = []

    def build(mocker: AiohttpClientMocker, **kwargs: Any) -> RailDataClient:
        session = mocker.create_session(asyncio.get_running_loop())
        sessions.append(session)
        return RailDataClient(session, "someone", "secret", **kwargs)

    yield build

    for session in sessions:
        await session.close()


@pytest.fixture(autouse=True)
def frozen_clock() -> Generator[None]:
    """Pin `now_local` to the recording, so the schedule is "today"."""
    with patch(
        "custom_components.njtransit.api.raildata.now_local",
        return_value=RECORDED_AT,
    ):
        yield


def token_requests(called: list[dict[str, Any]]) -> int:
    """Count how many times the credentials were exchanged."""
    return sum(1 for call in called if call["method"] == "getToken")


class TestTokens:
    """Ten a day, so every one of them has to count."""

    async def test_one_token_serves_every_call(self, client_for: ClientFactory) -> None:
        mocker = AiohttpClientMocker()
        called = install_raildata_mock(mocker)
        client = client_for(mocker)

        await client.stations()
        await client.departures("RT")
        await client.system_status()

        assert token_requests(called) == 1
        assert all(
            call.get("token") == TEST_TOKEN
            for call in called
            if call["method"] != "getToken"
        )

    async def test_the_password_is_sent_only_to_get_token(
        self, client_for: ClientFactory
    ) -> None:
        mocker = AiohttpClientMocker()
        install_raildata_mock(mocker)
        await client_for(mocker).stations()

        posted = [call[2] for call in mocker.mock_calls]
        with_password = [form for form in posted if "password" in (form or {})]
        assert len(with_password) == 1
        assert with_password[0]["username"] == "someone"

    async def test_a_stored_token_is_reused_across_restarts(
        self, client_for: ClientFactory
    ) -> None:
        """The whole reason the store exists."""
        mocker = AiohttpClientMocker()
        called = install_raildata_mock(mocker)
        store = MemoryStore()

        await client_for(mocker, store=store).stations()
        assert token_requests(called) == 1
        assert store.data is not None
        assert store.data["token"]["value"] == TEST_TOKEN

        # A "restart": a fresh client over the same store.
        await client_for(mocker, store=store).stations()
        assert token_requests(called) == 1

    async def test_a_stale_stored_token_is_replaced(
        self, client_for: ClientFactory
    ) -> None:
        mocker = AiohttpClientMocker()
        called = install_raildata_mock(mocker)
        issued = RECORDED_AT - TOKEN_LIFETIME - timedelta(minutes=1)
        store = MemoryStore(
            {"token": {"value": "old-token", "issued": issued.isoformat()}}
        )

        await client_for(mocker, store=store).stations()

        assert token_requests(called) == 1
        assert store.data is not None
        assert store.data["token"]["value"] == TEST_TOKEN
        # Replaced *before* being tried, not after the API rejected it: a
        # day-old token spent on a request is a request wasted.
        assert not any(call.get("token") == "old-token" for call in called)

    async def test_a_rejected_token_is_replaced_once(
        self, client_for: ClientFactory
    ) -> None:
        """The API said invalid; get a new one and retry, but only once."""
        mocker = AiohttpClientMocker()
        called = install_raildata_mock(mocker)
        issued = RECORDED_AT - timedelta(hours=1)
        store = MemoryStore(
            {"token": {"value": "revoked", "issued": issued.isoformat()}}
        )

        stations = await client_for(mocker, store=store).stations()

        assert stations
        assert token_requests(called) == 1
        # The first attempt carried the revoked token, the retry the new one.
        attempts = [c for c in called if c["method"] == "getStationList"]
        assert [c["token"] for c in attempts] == ["revoked", TEST_TOKEN]

    async def test_concurrent_first_calls_share_one_token_request(
        self, client_for: ClientFactory
    ) -> None:
        mocker = AiohttpClientMocker()
        called = install_raildata_mock(mocker)
        client = client_for(mocker)

        await asyncio.gather(
            client.stations(), client.system_status(), client.train_run("6295")
        )

        assert token_requests(called) == 1

    async def test_a_fresh_check_ignores_the_held_token(
        self, client_for: ClientFactory
    ) -> None:
        mocker = AiohttpClientMocker()
        called = install_raildata_mock(mocker)
        client = client_for(mocker)

        await client.authenticate()
        await client.authenticate()
        assert token_requests(called) == 1

        await client.authenticate(fresh=True)
        assert token_requests(called) == 2

    async def test_bad_credentials_are_not_retried(
        self, client_for: ClientFactory
    ) -> None:
        mocker = AiohttpClientMocker()
        called = install_raildata_mock(mocker, authenticated=False)
        client = client_for(mocker)

        with pytest.raises(NJTransitAuthError):
            await client.authenticate()
        with pytest.raises(NJTransitAuthError):
            await client.stations()

        assert token_requests(called) == 2
        assert "secret" not in str(called)

    async def test_the_daily_token_limit_is_its_own_error(
        self, client_for: ClientFactory
    ) -> None:
        mocker = AiohttpClientMocker()
        install_raildata_mock(
            mocker,
            {
                "getToken": {
                    "errorMessage": "Daily usage limit:10. Your current daily usage: 11"
                }
            },
        )
        with pytest.raises(NJTransitQuotaError):
            await client_for(mocker).authenticate()

    async def test_a_null_token_reply_is_an_auth_error(
        self, client_for: ClientFactory
    ) -> None:
        """Documented: null when the username, password or account is missing."""
        mocker = AiohttpClientMocker()
        install_raildata_mock(mocker, {"getToken": None})
        with pytest.raises(NJTransitAuthError):
            await client_for(mocker).authenticate()

    async def test_a_token_reply_of_the_wrong_shape_is_an_api_error(
        self, client_for: ClientFactory
    ) -> None:
        mocker = AiohttpClientMocker()
        install_raildata_mock(mocker, {"getToken": ["not", "an", "object"]})
        with pytest.raises(NJTransitAPIError, match="expected an object"):
            await client_for(mocker).authenticate()

    async def test_garbage_in_the_store_is_ignored(
        self, client_for: ClientFactory
    ) -> None:
        """A hand-edited or half-written file must not wedge the client."""
        mocker = AiohttpClientMocker()
        called = install_raildata_mock(mocker)
        store = MemoryStore(
            {
                "token": {"value": TEST_TOKEN, "issued": "yesterday-ish"},
                "schedules": "not a mapping",
            }
        )

        trips = await client_for(mocker, store=store).scheduled_trips("RT", "NY")

        assert len(trips) == 23
        assert token_requests(called) == 1
        assert store.data is not None
        assert isinstance(store.data["schedules"], dict)

    async def test_an_empty_token_is_an_api_error(
        self, client_for: ClientFactory
    ) -> None:
        mocker = AiohttpClientMocker()
        install_raildata_mock(
            mocker, {"getToken": {"Authenticated": "True", "UserToken": ""}}
        )
        with pytest.raises(NJTransitAPIError):
            await client_for(mocker).authenticate()


class TestTransport:
    """Error translation on the shared request path."""

    async def test_unreachable_is_a_connection_error(
        self, client_for: ClientFactory
    ) -> None:
        mocker = AiohttpClientMocker()
        install_raildata_mock(mocker, {"getStationList": TimeoutError()})
        with pytest.raises(NJTransitConnectionError, match="timed out"):
            await client_for(mocker).stations()

    async def test_a_client_error_is_a_connection_error(
        self, client_for: ClientFactory
    ) -> None:
        mocker = AiohttpClientMocker()
        install_raildata_mock(
            mocker, {"getStationList": aiohttp.ClientConnectionError("refused")}
        )
        with pytest.raises(NJTransitConnectionError, match="could not reach"):
            await client_for(mocker).stations()

    async def test_a_board_of_the_wrong_shape_is_an_api_error(
        self, client_for: ClientFactory
    ) -> None:
        mocker = AiohttpClientMocker()
        install_raildata_mock(mocker, {"getTrainSchedule19Rec": []})
        with pytest.raises(NJTransitAPIError, match="expected an object"):
            await client_for(mocker).departures("RT")

    async def test_non_json_is_a_connection_error(
        self, client_for: ClientFactory
    ) -> None:
        mocker = AiohttpClientMocker()
        install_raildata_mock(mocker)
        mocker.clear_requests()
        mocker.post(
            f"{ENDPOINT}/getToken",
            json={"Authenticated": "True", "UserToken": TEST_TOKEN},
        )
        mocker.post(f"{ENDPOINT}/getStationList", text="<html>maintenance</html>")
        with pytest.raises(NJTransitConnectionError, match="non-JSON"):
            await client_for(mocker).stations()

    async def test_other_error_messages_are_api_errors(
        self, client_for: ClientFactory
    ) -> None:
        mocker = AiohttpClientMocker()
        install_raildata_mock(
            mocker, {"getStationList": {"errorMessage": "Something else"}}
        )
        with pytest.raises(NJTransitAPIError, match="Something else"):
            await client_for(mocker).stations()

    async def test_a_realtime_quota_is_a_quota_error(
        self, client_for: ClientFactory
    ) -> None:
        mocker = AiohttpClientMocker()
        install_raildata_mock(
            mocker,
            {"getStationMSG": {"errorMessage": "Daily usage limit:40000."}},
        )
        with pytest.raises(NJTransitQuotaError):
            await client_for(mocker).system_status()

    async def test_a_null_payload_is_not_found(self, client_for: ClientFactory) -> None:
        mocker = AiohttpClientMocker()
        install_raildata_mock(mocker, {"getStationMSG": None})
        with pytest.raises(NJTransitNotFoundError):
            await client_for(mocker).system_status()

    async def test_an_empty_station_list_is_an_api_error(
        self, client_for: ClientFactory
    ) -> None:
        mocker = AiohttpClientMocker()
        install_raildata_mock(mocker, {"getStationList": []})
        with pytest.raises(NJTransitAPIError):
            await client_for(mocker).stations()


class TestStationNames:
    """Titles from either API, or a bare code, all resolve to the code."""

    @pytest.mark.parametrize(
        "station",
        [
            "RT",
            "rt",
            "Short Hills",
            "short hills",
            # The website's spelling, which a pre-existing entry carries.
            "Short Hills Station",
        ],
    )
    async def test_resolves(self, client_for: ClientFactory, station: str) -> None:
        mocker = AiohttpClientMocker()
        called = install_raildata_mock(mocker)
        board = await client_for(mocker).departures(station)

        assert board.station == station
        assert board.departures[0].train_id == "6671"
        asked = next(c for c in called if c["method"] == "getTrainSchedule19Rec")
        assert asked["station"] == "RT"

    async def test_a_remembered_title_skips_the_station_list(
        self, client_for: ClientFactory
    ) -> None:
        mocker = AiohttpClientMocker()
        called = install_raildata_mock(mocker)
        client = client_for(mocker)
        client.remember("Penn Station New York", "NY")

        await client.departures("Penn Station New York")

        assert "getStationList" not in {c["method"] for c in called}

    async def test_an_unknown_title_is_not_found(
        self, client_for: ClientFactory
    ) -> None:
        mocker = AiohttpClientMocker()
        install_raildata_mock(mocker)
        with pytest.raises(NJTransitNotFoundError):
            await client_for(mocker).departures("Platform Nine and Three Quarters")

    async def test_the_station_list_is_fetched_once(
        self, client_for: ClientFactory
    ) -> None:
        mocker = AiohttpClientMocker()
        called = install_raildata_mock(mocker)
        client = client_for(mocker)

        await client.stations()
        await client.departures("Short Hills")
        await client.departures("New York Penn Station")

        assert sum(1 for c in called if c["method"] == "getStationList") == 1


class TestSignal:
    """The one thing this source has that the website does not."""

    async def test_a_penn_departure_gets_its_signalled_track(
        self, client_for: ClientFactory
    ) -> None:
        """Train 3889, recorded on track 3 by the board and circuit AA-A190TK."""
        mocker = AiohttpClientMocker()
        install_raildata_mock(mocker)
        board = await client_for(mocker).departures("NY")
        by_id = {d.train_id: d for d in board.departures}

        assert by_id["3889"].track == "3"
        assert by_id["3889"].signalled_track == "3"
        assert by_id["3889"].track_source == "board"
        # Everything else on the board was not on a platform yet.
        assert all(
            d.signalled_track is None for d in board.departures if d.train_id != "3889"
        )

    async def test_the_signal_stands_in_before_the_board_posts(
        self, client_for: ClientFactory
    ) -> None:
        mocker = AiohttpClientMocker()
        board = _new_york_board()
        for item in board["ITEMS"]:
            if item["TRAIN_ID"] == "3889":
                item["TRACK"] = ""
        install_raildata_mock(mocker, {"getTrainSchedule19Rec": board})

        departures = (await client_for(mocker).departures("NY")).departures
        train = next(d for d in departures if d.train_id == "3889")

        assert train.track is None
        assert train.signalled_track == "3"
        assert train.track_source == "signalled"
        assert train.best_track == "3"

    async def test_a_sighting_of_another_run_of_the_same_number_is_ignored(
        self, client_for: ClientFactory
    ) -> None:
        mocker = AiohttpClientMocker()
        board = _new_york_board()
        for item in board["ITEMS"]:
            if item["TRAIN_ID"] == "3889":
                item["SCHED_DEP_DATE"] = "15-Sep-2026 09:35:00 PM"
        install_raildata_mock(mocker, {"getTrainSchedule19Rec": board})

        departures = (await client_for(mocker).departures("NY")).departures
        train = next(d for d in departures if d.train_id == "3889")

        assert train.signalled_track is None

    async def test_the_signalling_feed_is_not_read_elsewhere(
        self, client_for: ClientFactory
    ) -> None:
        """Short Hills has no decoder, so the call would be wasted."""
        mocker = AiohttpClientMocker()
        called = install_raildata_mock(mocker)
        await client_for(mocker).departures("RT")
        assert "getVehicleData" not in {c["method"] for c in called}

    async def test_losing_the_signal_keeps_the_board(
        self, client_for: ClientFactory
    ) -> None:
        mocker = AiohttpClientMocker()
        install_raildata_mock(mocker, {"getVehicleData": TimeoutError()})
        board = await client_for(mocker).departures("NY")

        assert len(board.departures) == 19
        assert all(d.signalled_track is None for d in board.departures)


class TestRuns:
    """`train_run`."""

    async def test_returns_the_stop_list(self, client_for: ClientFactory) -> None:
        run = await client_for(_mocked()).train_run("6295")
        assert run.train_id == "6295"
        assert run.stops[0].name == "New York Penn Station"

    async def test_an_idle_train_is_not_found(self, client_for: ClientFactory) -> None:
        mocker = AiohttpClientMocker()
        install_raildata_mock(
            mocker, {"getTrainStopList": {"TRAIN_ID": None, "STOPS": None}}
        )
        with pytest.raises(NJTransitNotFoundError):
            await client_for(mocker).train_run("9999")


class TestReference:
    """Lines and messages."""

    async def test_lines_are_the_table(self, client_for: ClientFactory) -> None:
        assert await client_for(_mocked()).train_lines() == LINES

    async def test_messages_become_alerts(self, client_for: ClientFactory) -> None:
        alerts = await client_for(_mocked()).system_status()
        assert {a.line_abbreviation for a in alerts} == {"", "NJCL", "NEC", "MNE"}

    async def test_a_non_list_message_payload_is_empty(
        self, client_for: ClientFactory
    ) -> None:
        mocker = AiohttpClientMocker()
        install_raildata_mock(mocker, {"getStationMSG": {"unexpected": True}})
        assert await client_for(mocker).system_status() == ()


class TestSchedules:
    """Five a day, so each station-day is fetched once and kept."""

    async def test_joins_the_two_stations(self, client_for: ClientFactory) -> None:
        trips = await client_for(_mocked()).scheduled_trips(
            "Short Hills Station", "New York Penn Station", on=date(2026, 9, 14)
        )
        assert len(trips) == 23
        assert trips[0].train_id == "6602"

    async def test_today_is_the_default(self, client_for: ClientFactory) -> None:
        trips = await client_for(_mocked()).scheduled_trips("RT", "NY")
        assert len(trips) == 23

    async def test_tomorrow_is_not_asked_for(self, client_for: ClientFactory) -> None:
        """The API has nothing to say about it until midnight."""
        mocker = AiohttpClientMocker()
        called = install_raildata_mock(mocker)
        trips = await client_for(mocker).scheduled_trips(
            "RT", "NY", on=date(2026, 9, 15)
        )
        assert trips == ()
        assert "getStationSchedule" not in {c["method"] for c in called}

    async def test_each_station_day_is_fetched_once(
        self, client_for: ClientFactory
    ) -> None:
        mocker = AiohttpClientMocker()
        called = install_raildata_mock(mocker)
        client = client_for(mocker)

        await client.scheduled_trips("RT", "NY")
        await client.scheduled_trips("NY", "RT")

        assert sum(1 for c in called if c["method"] == "getStationSchedule") == 2

    async def test_fetched_days_survive_a_restart(
        self, client_for: ClientFactory
    ) -> None:
        mocker = AiohttpClientMocker()
        called = install_raildata_mock(mocker)
        store = MemoryStore()

        await client_for(mocker, store=store).scheduled_trips("RT", "NY")
        assert store.data is not None
        assert set(store.data["schedules"]) == {"RT|2026-09-14", "NY|2026-09-14"}

        trips = await client_for(mocker, store=store).scheduled_trips("RT", "NY")

        assert len(trips) == 23
        assert sum(1 for c in called if c["method"] == "getStationSchedule") == 2

    async def test_only_what_the_join_needs_is_stored(
        self, client_for: ClientFactory
    ) -> None:
        mocker = AiohttpClientMocker()
        install_raildata_mock(mocker)
        store = MemoryStore()
        await client_for(mocker, store=store).scheduled_trips("RT", "NY")

        assert store.data is not None
        item = store.data["schedules"]["RT|2026-09-14"][0]["ITEMS"][0]
        assert "DIRECTION" not in item
        assert {"TRAIN_ID", "SCHED_DEP_DATE", "DWELL_TIME"} <= set(item)

    async def test_old_days_are_pruned(self, client_for: ClientFactory) -> None:
        mocker = AiohttpClientMocker()
        install_raildata_mock(mocker)
        store = MemoryStore(
            {
                "schedules": {
                    "RT|2026-09-12": [],
                    "RT|2026-09-13": [],
                    "garbage": [],
                }
            }
        )
        await client_for(mocker, store=store).scheduled_trips("RT", "NY")

        assert store.data is not None
        assert set(store.data["schedules"]) == {
            "RT|2026-09-13",
            "RT|2026-09-14",
            "NY|2026-09-14",
        }

    async def test_a_quota_hit_propagates(self, client_for: ClientFactory) -> None:
        mocker = AiohttpClientMocker()
        install_raildata_mock(
            mocker, {"getStationSchedule": {"errorMessage": "Daily usage limit:5."}}
        )
        with pytest.raises(NJTransitQuotaError):
            await client_for(mocker).scheduled_trips("RT", "NY")


def _mocked() -> AiohttpClientMocker:
    """Return a mocker serving every recorded fixture."""
    mocker = AiohttpClientMocker()
    install_raildata_mock(mocker)
    return mocker


def _new_york_board() -> dict[str, Any]:
    """Return a mutable copy of the recorded New York board."""
    from .conftest import load_raildata_fixture

    return load_raildata_fixture("train_schedule19_new_york")
