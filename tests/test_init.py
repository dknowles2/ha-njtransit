"""Setup, teardown, and coordinator sharing.

The reference counting here is the subtlest thing in the integration: two
commutes out of the same station must share one board poll, and unloading
either must not take the board away from the other. Both directions are
tested, because getting one wrong leaks and the other silently breaks the
surviving entry.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState, current_entry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.njtransit import (
    async_setup_entry as njtransit_setup_entry,
)
from custom_components.njtransit.api.models import TrainRun
from custom_components.njtransit.api.parsing import TZ, parse_stops
from custom_components.njtransit.const import (
    CONF_DEPARTURE_COUNT,
    CONF_DEPARTURE_INTERVAL,
    CONF_DESTINATION,
    CONF_DESTINATION_ID,
    CONF_FAVORITE_TRAINS,
    CONF_ORIGIN,
    CONF_ORIGIN_ID,
    DOMAIN,
    MIN_INTERVAL,
)
from custom_components.njtransit.coordinator import store_for
from custom_components.njtransit.track_history import TrackHistory

from .conftest import install_api_mock, load_fixture

SHORT_HILLS = "Short Hills Station"
NY_PENN = "New York Penn Station"
HOBOKEN = "Hoboken Terminal"


def make_entry(
    origin: str = SHORT_HILLS,
    origin_id: str = "RT",
    destination: str | None = NY_PENN,
    destination_id: str = "NY",
    options: dict[str, Any] | None = None,
) -> MockConfigEntry:
    """Return a config entry for one commute."""
    data: dict[str, Any] = {CONF_ORIGIN: origin, CONF_ORIGIN_ID: origin_id}
    if destination:
        data[CONF_DESTINATION] = destination
        data[CONF_DESTINATION_ID] = destination_id

    unique_id = f"{origin_id}-{destination_id}" if destination else origin_id
    return MockConfigEntry(
        domain=DOMAIN,
        title=f"{origin} to {destination}" if destination else origin,
        data=data,
        options=options or {},
        unique_id=unique_id,
    )


async def setup_entry(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Add and set up a config entry."""
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


class TestSetup:
    """Entry setup."""

    async def test_sets_up(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        install_api_mock(aioclient_mock)
        entry = make_entry()
        await setup_entry(hass, entry)

        assert entry.state is ConfigEntryState.LOADED
        assert entry.runtime_data.origin == SHORT_HILLS
        assert entry.runtime_data.destination == NY_PENN

    async def test_resolves_the_destination_train_set(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """The board filter comes from the planner, not the label."""
        install_api_mock(aioclient_mock)
        entry = make_entry()
        await setup_entry(hass, entry)

        route = entry.runtime_data.route.data
        assert route is not None
        # The specific set, not merely a non-empty one. "Some trains resolved"
        # passes against a filter that resolved the wrong trains, which is the
        # failure this exists to catch.
        # The exact set, not merely a non-empty one. The capture offers three
        # itineraries and only one is a one-seat ride: 411 changes to 6628 at
        # Summit, and 480 continues by bus. `assert route.train_ids` passed
        # whether the direct-only filter (SPEC 2.7) kept one train or all
        # four, so it could not tell the feature was working.
        assert route.train_ids == frozenset({"6328"})
        assert route.complete

    async def test_entry_without_a_destination_skips_route_resolution(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        install_api_mock(aioclient_mock)
        called = install_api_mock(aioclient_mock)
        entry = make_entry(destination=None)
        await setup_entry(hass, entry)

        assert entry.state is ConfigEntryState.LOADED
        assert "TripPlannerSchedule" not in called

    async def test_unreachable_endpoint_retries_setup(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """A transport failure is retryable, so setup must not be final."""
        install_api_mock(aioclient_mock, {"SystemStatus": TimeoutError()})
        entry = make_entry()
        await setup_entry(hass, entry)

        assert entry.state is ConfigEntryState.SETUP_RETRY


class TestIntervals:
    """Poll cadence."""

    async def test_options_drive_the_interval(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        install_api_mock(aioclient_mock)
        entry = make_entry(options={CONF_DEPARTURE_INTERVAL: 300})
        await setup_entry(hass, entry)

        interval = entry.runtime_data.board.update_interval
        assert interval is not None
        assert interval.total_seconds() == 300

    async def test_interval_is_floored_at_the_vendor_cadence(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """Responses carry a 30s cache hint; polling faster gains nothing."""
        install_api_mock(aioclient_mock)
        entry = make_entry(options={CONF_DEPARTURE_INTERVAL: 1})
        await setup_entry(hass, entry)

        interval = entry.runtime_data.board.update_interval
        assert interval is not None
        assert interval.total_seconds() == MIN_INTERVAL


class TestCoordinatorSharing:
    """Reference counting across entries."""

    async def test_two_commutes_from_one_origin_share_a_board(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """Otherwise Short Hills gets polled twice for no benefit."""
        install_api_mock(aioclient_mock)
        to_ny = make_entry()
        to_hoboken = make_entry(destination=HOBOKEN, destination_id="HB")
        await setup_entry(hass, to_ny)
        await setup_entry(hass, to_hoboken)

        assert to_ny.runtime_data.board is to_hoboken.runtime_data.board

        store = store_for(hass)
        assert store is not None
        assert list(store.boards) == [SHORT_HILLS]

    async def test_concurrent_setup_still_shares_one_store(
        self,
        hass: HomeAssistant,
        aioclient_mock: AiohttpClientMocker,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Home Assistant sets a domain's entries up in parallel.

        Every other test here sets them up one after another, which is why
        this went unnoticed: building the shared store awaits several times,
        so raced, both entries see no store, both build one, and the second
        assignment wins -- leaving the loser holding an orphan.

        The damage is not theoretical. It means duplicate polling of the
        system-wide alert feed, no board sharing between commutes out of one
        station, and two TrackHistory objects writing to a single storage key
        where each save silently discards the other station's collection.
        """
        install_api_mock(aioclient_mock)

        # The mocked transport resolves without ever suspending, so a plain
        # gather runs each setup start to finish and the race cannot happen.
        # Real setup awaits the network here. Forcing one yield inside the
        # critical section is what makes this test able to fail at all --
        # without it, it passes with the lock removed.
        original = TrackHistory.async_load

        async def slow_load(self: TrackHistory) -> None:
            await asyncio.sleep(0)
            await original(self)

        monkeypatch.setattr(TrackHistory, "async_load", slow_load)

        outbound = make_entry()
        inbound = make_entry(
            origin=NY_PENN,
            origin_id="NY",
            destination=SHORT_HILLS,
            destination_id="RT",
        )
        # The module's own setup is called directly, rather than driven
        # through `hass.config_entries.async_setup`. Two concurrent calls into
        # that race Home Assistant's entry state machine and raise
        # OperationNotAllowed depending on scheduling -- a flake that says
        # nothing about the code under test. Platform forwarding is stubbed for
        # the same reason: the store is what this is about.
        outbound.add_to_hass(hass)
        inbound.add_to_hass(hass)

        async def no_platforms(*args: Any, **kwargs: Any) -> None:
            return None

        monkeypatch.setattr(
            hass.config_entries, "async_forward_entry_setups", no_platforms
        )

        # `async_config_entry_first_refresh` reads the entry from a
        # ContextVar that `hass.config_entries.async_setup` would normally
        # set. Each gathered coroutine runs as its own task with its own copy
        # of the context, so setting it here is per-entry, not shared.
        async def setup(entry: MockConfigEntry) -> bool:
            current_entry.set(entry)
            # `async_config_entry_first_refresh` refuses to run outside this
            # state, which `hass.config_entries.async_setup` would normally
            # have set on the way in.
            entry.mock_state(hass, ConfigEntryState.SETUP_IN_PROGRESS)
            return await njtransit_setup_entry(hass, entry)

        await asyncio.gather(setup(outbound), setup(inbound))

        assert outbound.runtime_data.history is inbound.runtime_data.history
        assert outbound.runtime_data.status is inbound.runtime_data.status
        assert outbound.runtime_data.static is inbound.runtime_data.static

        # Nothing unloaded these, so their refresh timers outlive the test.
        store = store_for(hass)
        assert store is not None
        shared: list[DataUpdateCoordinator[Any]] = [
            store.static,
            store.status,
            *store.boards.values(),
        ]
        for coordinator in shared:
            await coordinator.async_shutdown()
        for entry in (outbound, inbound):
            await entry.runtime_data.route.async_shutdown()
            await entry.runtime_data.progress.async_shutdown()

    async def test_different_origins_get_their_own_boards(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        install_api_mock(aioclient_mock)
        outbound = make_entry()
        inbound = make_entry(
            origin=NY_PENN,
            origin_id="NY",
            destination=SHORT_HILLS,
            destination_id="RT",
        )
        await setup_entry(hass, outbound)
        await setup_entry(hass, inbound)

        assert outbound.runtime_data.board is not inbound.runtime_data.board

        store = store_for(hass)
        assert store is not None
        assert set(store.boards) == {SHORT_HILLS, NY_PENN}

    async def test_status_feed_is_shared(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """The alert feed is system-wide, not per-station."""
        install_api_mock(aioclient_mock)
        first = make_entry()
        second = make_entry(
            origin=NY_PENN, origin_id="NY", destination=HOBOKEN, destination_id="HB"
        )
        await setup_entry(hass, first)
        await setup_entry(hass, second)

        assert first.runtime_data.status is second.runtime_data.status
        assert first.runtime_data.static is second.runtime_data.static

    async def test_unloading_one_keeps_the_shared_board_alive(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """The bug this whole mechanism exists to prevent."""
        install_api_mock(aioclient_mock)
        to_ny = make_entry()
        to_hoboken = make_entry(destination=HOBOKEN, destination_id="HB")
        await setup_entry(hass, to_ny)
        await setup_entry(hass, to_hoboken)

        await hass.config_entries.async_unload(to_ny.entry_id)
        await hass.async_block_till_done()

        store = store_for(hass)
        assert store is not None
        assert SHORT_HILLS in store.boards, "the surviving commute lost its board"
        assert to_hoboken.state is ConfigEntryState.LOADED

    async def test_unloading_the_last_entry_tears_everything_down(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """The other direction: nothing may be left polling."""
        install_api_mock(aioclient_mock)
        to_ny = make_entry()
        to_hoboken = make_entry(destination=HOBOKEN, destination_id="HB")
        await setup_entry(hass, to_ny)
        await setup_entry(hass, to_hoboken)

        await hass.config_entries.async_unload(to_ny.entry_id)
        await hass.config_entries.async_unload(to_hoboken.entry_id)
        await hass.async_block_till_done()

        assert store_for(hass) is None

    async def test_unload_and_set_up_again(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """A reload must not leave a half-released store behind."""
        install_api_mock(aioclient_mock)
        entry = make_entry()
        await setup_entry(hass, entry)

        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        assert entry.state is ConfigEntryState.LOADED
        store = store_for(hass)
        assert store is not None
        assert list(store.boards) == [SHORT_HILLS]


class TestRouteDegradation:
    """The destination filter fails soft."""

    async def test_planner_failure_does_not_block_setup(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """Losing the better filter beats losing the departures entirely."""
        install_api_mock(
            aioclient_mock,
            {"TripPlannerSchedule": {"data": {"getTripPlannerSchedule": None}}},
        )
        entry = make_entry()
        await setup_entry(hass, entry)

        assert entry.state is ConfigEntryState.LOADED
        route = entry.runtime_data.route.data
        assert route is not None
        assert route.complete is False
        assert route.train_ids == frozenset()


@pytest.mark.parametrize("destination", [NY_PENN, None])
async def test_unload_is_clean(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    destination: str | None,
) -> None:
    """Unloading succeeds whether or not a destination was configured."""
    install_api_mock(aioclient_mock)
    entry = make_entry(destination=destination)
    await setup_entry(hass, entry)

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED


class TestOptionReloads:
    """Which option changes tear the entry down, and which do not."""

    async def test_changing_favorites_does_not_reload(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """Entities read favourites live, so a reload buys nothing.

        Reloading re-pages the trip planner and blanks every entity on the
        way through, which is a lot of disruption for an option no
        coordinator depends on.
        """
        install_api_mock(aioclient_mock)
        entry = make_entry()
        await setup_entry(hass, entry)

        before = entry.runtime_data
        sensor = hass.states.get(
            "sensor.short_hills_station_to_new_york_penn_station_next_departure"
        )
        assert sensor is not None and sensor.state not in ("unknown", "unavailable")

        hass.config_entries.async_update_entry(
            entry, options={**entry.options, CONF_FAVORITE_TRAINS: ["6624"]}
        )
        await hass.async_block_till_done()

        # Same runtime object means the entry was never torn down.
        assert entry.runtime_data is before
        after = hass.states.get(
            "sensor.short_hills_station_to_new_york_penn_station_next_departure"
        )
        assert after is not None and after.state == sensor.state
        # And the new value took effect anyway.
        favorite = hass.states.get(
            "sensor.short_hills_station_to_new_york_penn_station_next_favorite"
        )
        assert favorite is not None
        assert favorite.attributes["favorites"] == ["6624"]

    async def test_changing_departure_count_does_reload(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """This one changes how many entities exist, so it must."""
        install_api_mock(aioclient_mock)
        entry = make_entry()
        await setup_entry(hass, entry)

        before = entry.runtime_data
        hass.config_entries.async_update_entry(
            entry, options={**entry.options, CONF_DEPARTURE_COUNT: 5}
        )
        await hass.async_block_till_done()

        assert entry.runtime_data is not before
        created = [
            state
            for state in hass.states.async_all("sensor")
            if "departure" in state.entity_id and "next_favorite" not in state.entity_id
        ]
        assert len(created) == 5


async def test_the_progress_coordinator_follows_a_favorite(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """`pick_favorite` finding one, which nothing exercised before.

    It gates on the lookahead window, so against a morning capture and a real
    clock every departure has rolled to tomorrow and it returns None on the
    first comparison -- passing, while never reaching the branch that matters.
    Pinning the clock to the capture is what makes the success path reachable.
    """
    freezer.move_to(datetime(2026, 8, 3, 8, 20, tzinfo=TZ))
    install_api_mock(aioclient_mock)
    entry = make_entry(options={CONF_FAVORITE_TRAINS: ["6624"]})
    await setup_entry(hass, entry)

    run = entry.runtime_data.progress.data
    assert run is not None
    # Which train it followed, not merely that it followed one. The board's
    # soonest departure is not 6624, so "something was followed" would pass
    # against a picker that ignored favourites entirely.
    assert run.train_id == "6624"


async def test_the_origin_coordinates_reach_the_favourite_sensor(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """An automation cannot ask "am I near the station" without them.

    They ride on the favourite sensor because that is the entity an automation
    is pointed at, alongside `favorites`, which is on it for the same reason.
    """
    install_api_mock(aioclient_mock)
    await setup_entry(hass, make_entry())

    state = hass.states.get(
        "sensor.short_hills_station_to_new_york_penn_station_next_favorite"
    )
    assert state is not None
    assert state.attributes["origin_latitude"] == pytest.approx(40.725249)
    assert state.attributes["origin_longitude"] == pytest.approx(-74.323751)


async def test_setup_survives_the_coordinate_lookup_failing(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Proximity is a convenience laid on a working commute.

    The attributes are absent rather than zeroed: `0, 0` is a real place in
    the Atlantic, and a distance measured against it would be silently
    enormous -- which reads as "you are never near the station" rather than as
    "this is not known", and would suppress every notification for good.
    """
    install_api_mock(aioclient_mock, {"TripPlannerAlternates": TimeoutError()})
    entry = make_entry()
    await setup_entry(hass, entry)

    assert entry.state is ConfigEntryState.LOADED
    state = hass.states.get(
        "sensor.short_hills_station_to_new_york_penn_station_next_favorite"
    )
    assert state is not None
    assert "origin_latitude" not in state.attributes


async def test_the_train_you_boarded_is_not_swapped_for_the_next_one(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Once it has left, the board can no longer vouch for it.

    A departed train is dropped from the departure board entirely, so a
    chooser that re-reads the board every poll finds nothing to follow -- and
    the journey it stops describing is the one actually being taken. The
    recorded run has Short Hills behind the train and New York Penn ahead,
    which is exactly the window this has to cover.
    """
    freezer.move_to(datetime(2026, 8, 3, 8, 20, tzinfo=TZ))
    install_api_mock(aioclient_mock)
    entry = make_entry(options={CONF_FAVORITE_TRAINS: ["6624"]})
    await setup_entry(hass, entry)
    assert entry.runtime_data.progress.data is not None

    # Past the lookahead window, so nothing on the board would be picked now.
    freezer.move_to(datetime(2026, 8, 3, 9, 0, tzinfo=TZ))
    await entry.runtime_data.progress.async_refresh()

    run = entry.runtime_data.progress.data
    assert run is not None, "the tracker let go of the train mid-journey"
    assert run.train_id == "6624"


async def test_following_stops_well_after_the_train_should_have_arrived(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A feed that stops advancing must not pin the tracker forever.

    Nothing else ends the follow: the destination only falls behind the train
    when the stop list says so, and a stalled list never says so. Without a
    bound that is a request a minute for a journey that ended hours ago.

    The bound is measured from when following began rather than against the
    train's scheduled arrival, and that is not a stylistic choice -- stop-list
    times are bare wall-clock strings that roll into tomorrow once they pass
    (SPEC 3.6), so an arrival-based bound recedes as fast as the clock chases
    it and never fires at all. An earlier version of this test proved exactly
    that by failing.

    The favourite here is not on the board, so nothing but the latch can keep
    the tracker alive.
    """
    freezer.move_to(datetime(2026, 8, 3, 8, 30, tzinfo=TZ))
    install_api_mock(aioclient_mock)
    entry = make_entry(options={CONF_FAVORITE_TRAINS: ["9999"]})
    await setup_entry(hass, entry)
    assert entry.runtime_data.progress.data is None

    # Hand it a train mid-journey: origin behind, destination still ahead.
    entry.runtime_data.progress.async_set_updated_data(
        TrainRun(
            train_id="9999",
            stops=parse_stops(
                load_fixture("stop_list_6320")["data"]["getTrainStopList"],
                datetime(2026, 8, 3, 8, 28, tzinfo=TZ),
            ),
        )
    )
    await entry.runtime_data.progress.async_refresh()
    assert entry.runtime_data.progress.data is not None, "the latch never took"

    freezer.move_to(datetime(2026, 8, 3, 11, 0, tzinfo=TZ))
    await entry.runtime_data.progress.async_refresh()

    assert entry.runtime_data.progress.data is None


async def test_no_favorite_in_the_window_follows_nothing(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """The early return that keeps this from being a request a minute."""
    freezer.move_to(datetime(2026, 8, 3, 8, 20, tzinfo=TZ))
    install_api_mock(aioclient_mock)
    entry = make_entry(options={CONF_FAVORITE_TRAINS: ["9999"]})
    await setup_entry(hass, entry)

    assert entry.runtime_data.progress.data is None


async def test_a_favourite_matches_whatever_case_the_board_uses(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Train IDs are strings, and not all of them are digits.

    Numeric IDs make the casing in `pick_favorite` unobservable, so dropping
    it breaks nothing the rest of the suite can see. Trenton's board carries
    Amtrak services like `A79`, which is where it would bite -- a favourite
    stored uppercase silently never matching.
    """
    freezer.move_to(datetime(2026, 8, 3, 8, 20, tzinfo=TZ))
    payload = load_fixture("departures_short_hills_disruption")
    for item in payload["data"]["getTrainDepartureScreens"]["items"]:
        if item["trainID"] == "6624":
            item["trainID"] = "a624"

    install_api_mock(aioclient_mock, {"TrainDepartureScreens": payload})
    entry = make_entry(options={CONF_FAVORITE_TRAINS: ["A624"]})
    await setup_entry(hass, entry)

    run = entry.runtime_data.progress.data
    assert run is not None, "an uppercase favourite did not match a lowercase board"
