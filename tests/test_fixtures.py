"""Guards on the recorded fixtures themselves.

These do not test integration code. They assert that the fixtures still encode
the real-world facts the design depends on, so that quietly regenerating one to
make a test pass fails loudly here instead.

See AGENTS.md, "Fixtures are evidence, not scaffolding".
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from .conftest import load_fixture, load_payload

ALL_FIXTURES = (
    ("system_status_disruption", "getSystemStatus"),
    ("departures_short_hills_disruption", "getTrainDepartureScreens"),
    ("stations_rail_dv", "getTrainScheduleStationsRailForDV"),
    ("train_lines", "getTrainLines"),
    ("trip_planner_short_hills_to_ny", "getTripPlannerSchedule"),
    ("trip_planner_short_hills_to_hoboken", "getTripPlannerSchedule"),
    ("trip_planner_multimodal_short_hills_to_ny", "getTripPlannerSchedule"),
)

# Matches "train 6612" and "train #6607" alike. Deliberately \w rather than
# \d: Trenton's board carries Amtrak services with IDs like "A79".
TRAIN_RE = re.compile(r"\btrain\s+#?(\w{1,5})\b", re.IGNORECASE)


def train_ids(message: str) -> set[str]:
    """Return train IDs named in an alert, excluding suggested substitutes."""
    return set(TRAIN_RE.findall(message.split("Please take")[0]))


def rail_blocks(trips: list[dict[str, Any]]) -> set[str]:
    """Return the commuter-rail train IDs across a planner response."""
    return {
        leg["block"]
        for trip in trips
        for leg in trip["legs"]
        if leg["block"] and leg["routeType"] == "C"
    }


@pytest.mark.parametrize(("name", "root_field"), ALL_FIXTURES)
def test_fixture_is_a_successful_response(name: str, root_field: str) -> None:
    """Every fixture parses and carries data rather than a GraphQL error."""
    payload = load_fixture(name)
    assert "errors" not in payload.get("data", {}), f"{name} recorded an error"
    assert payload["data"][root_field], f"{name} recorded an empty payload"


def test_neither_feed_is_a_superset_of_the_other(
    system_status: list[dict[str, Any]],
    departure_board: dict[str, Any],
) -> None:
    """The disagreement that justifies this integration is still captured.

    Train 6320 was cancelled on the Short Hills board while the alert feed said
    nothing about it. A REST sensor watching only system status misses it.
    """
    alerted: set[str] = set()
    for alert in system_status:
        if alert["abbreviation"] == "MNE" and alert["advisoryAlert"] == "0":
            alerted |= train_ids(alert["message"])

    cancelled = {
        item["trainID"]
        for item in departure_board["items"]
        if "cancel" in item["status"].casefold()
    }

    assert "6320" in cancelled, "the board no longer shows 6320 cancelled"
    assert "6320" not in alerted, "6320 is now in the alert feed; capture is stale"
    assert alerted - cancelled, "alerts no longer name trains absent from the board"


def test_board_status_casing_is_inconsistent(
    departure_board: dict[str, Any],
) -> None:
    """Both "Cancelled" and "CANCELLED" appear, in a single response.

    This is why status normalization is case-insensitive. If upstream ever
    makes it consistent, that is worth noticing rather than silently relying
    on.
    """
    statuses = {
        item["status"]
        for item in departure_board["items"]
        if "cancel" in item["status"].casefold()
    }
    assert len(statuses) > 1, f"casing is now consistent: {statuses}"


def test_capacity_is_partial(departure_board: dict[str, Any]) -> None:
    """Crowding data covers only imminent departures, not the whole board."""
    items = departure_board["items"]
    with_capacity = [item for item in items if item["capacity"]]
    assert with_capacity, "no crowding data captured"
    assert len(with_capacity) < len(items), "capacity is now populated for every row"


def test_board_stops_are_always_empty(departure_board: dict[str, Any]) -> None:
    """The board never populates per-train stops, despite offering the field.

    The site lazy-loads them via getTrainStopList. If this ever starts
    returning data, per-train tracking becomes free and SPEC 2.2 is wrong.
    """
    assert all(not item["stops"] for item in departure_board["items"])


def test_a_single_planner_call_returns_three_trips() -> None:
    """The planner's fixed page size is why RouteCoordinator must page.

    A single call yields 4 rail trains for this pair; the real service day has
    51. See SPEC 2.6 -- this was a bug in the spec before it was caught.
    """
    trips = load_payload("trip_planner_short_hills_to_ny", "getTripPlannerSchedule")
    assert len(trips) == 3
    assert len(rail_blocks(trips)) == 4


def test_the_site_default_travel_mode_spends_a_slot_on_a_bus() -> None:
    """Why TRIP_PLANNER_DEFAULTS does not send BCTLXR.

    Recorded with the site's own travelMode. The planner offered train 880 to
    Hoboken, the 126 bus to Port Authority and the subway to Penn Station --
    1 hr 17 min, when a direct train left 21 minutes later and arrived 13
    minutes earlier. With only three slots per call (SPEC 2.6) that is a slot
    a usable train could have had, and the itinerary still put 880 on the
    board because the destination filter keys on the first rail leg.

    If this fixture ever contains only rail legs, the endpoint's behaviour
    changed and the travelMode override deserves rechecking -- not deleting.
    """
    trips = load_payload(
        "trip_planner_multimodal_short_hills_to_ny", "getTripPlannerSchedule"
    )
    modes = {leg["routeType"] for trip in trips for leg in trip["legs"]}
    assert "B" in modes, "no bus leg -- the recorded multimodal case is gone"
    assert rail_blocks(trips) == {"880"}


def test_commutes_from_one_origin_share_trains() -> None:
    """Two destinations from the same origin legitimately overlap.

    Train 411 is a Gladstone train to Summit, usable for either commute
    depending on the connection. Both entries surfacing it is correct.
    """
    to_ny = rail_blocks(
        load_payload("trip_planner_short_hills_to_ny", "getTripPlannerSchedule")
    )
    to_hoboken = rail_blocks(
        load_payload("trip_planner_short_hills_to_hoboken", "getTripPlannerSchedule")
    )

    assert to_ny & to_hoboken, "the commutes no longer share any train"
    assert to_ny - to_hoboken, "the commutes are no longer distinguishable"
    assert to_hoboken - to_ny, "the commutes are no longer distinguishable"


def test_station_list_covers_the_documented_commute() -> None:
    """The canonical station list still carries the stable penta IDs."""
    stations = load_payload("stations_rail_dv", "getTrainScheduleStationsRailForDV")
    by_title = {station["title"]: station["pentaStationID"] for station in stations}

    assert by_title["Short Hills Station"] == "RT"
    assert by_title["New York Penn Station"] == "NY"
    assert by_title["Hoboken Terminal"] == "HB"


def test_station_titles_are_not_unique() -> None:
    """Penta IDs carry alias titles, so the picker has to dedupe.

    Without this, New York Penn shows up three times in the config flow. See
    SPEC 3.5.
    """
    stations = load_payload("stations_rail_dv", "getTrainScheduleStationsRailForDV")
    penta_ids = {station["pentaStationID"] for station in stations}

    assert len(penta_ids) < len(stations), "aliases are gone; dedupe may be removable"
    aliases = [s["title"] for s in stations if s["pentaStationID"] == "NY"]
    assert len(aliases) > 1, f"NY Penn no longer aliased: {aliases}"


def test_station_titles_do_not_all_share_a_suffix() -> None:
    """Names cannot be synthesized by appending " Station"."""
    stations = load_payload("stations_rail_dv", "getTrainScheduleStationsRailForDV")
    unsuffixed = [
        station["title"]
        for station in stations
        if not station["title"].endswith(("Station", "Terminal"))
    ]
    assert unsuffixed, "every title is now suffixed; SPEC 3.5 needs revisiting"


def test_line_umbrella_codes_split_downstream() -> None:
    """MNE in the alert feed covers two distinct line codes.

    Alerts use MNE as an umbrella; getTrainLines splits Morristown from the
    Gladstone Branch. Correlation has to expand it.
    """
    lines = load_payload("train_lines", "getTrainLines")
    abbreviations = {line["abbreviation"] for line in lines}

    assert {"MNE", "MNEG"} <= abbreviations
