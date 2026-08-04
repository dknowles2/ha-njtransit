"""Where a train is along its route.

The recorded capture is train 6320 caught mid-run: three stops departed,
Short Hills already behind it, New York Penn still ahead. That asymmetry is
the point -- "how far away is it" and "has it already gone" are different
questions and the second one is easy to answer wrongly.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from custom_components.njtransit.api.models import TrainRun, TrainStatus
from custom_components.njtransit.api.parsing import TZ, parse_stops

from .conftest import load_payload

CAPTURED_AT = datetime(2026, 8, 4, 8, 28, tzinfo=TZ)


def run() -> TrainRun:
    """Return the recorded run."""
    return TrainRun(
        train_id="6320",
        stops=parse_stops(
            load_payload("stop_list_6320", "getTrainStopList"), CAPTURED_AT
        ),
    )


def test_parses_every_stop() -> None:
    stops = run().stops
    assert len(stops) == 9
    assert [stop.departed for stop in stops].count(True) == 3
    assert all(stop.scheduled is not None for stop in stops)
    assert all(stop.status is TrainStatus.ON_TIME for stop in stops)


def test_position_is_the_boundary_between_departed_and_not() -> None:
    """Where the train is, rather than where it is due."""
    train = run()
    assert train.last_departed is not None
    assert train.last_departed.name == "Millburn"
    assert train.next_stop is not None
    assert train.next_stop.name == "Maplewood"


def test_stops_until_counts_from_the_next_call() -> None:
    """Zero means "this station is next", not "the train is here"."""
    train = run()
    assert train.stops_until("Maplewood Station") == 0
    assert train.stops_until("South Orange Station") == 1
    assert train.stops_until("New York Penn Station") == 5


def test_a_station_already_passed_is_none_not_zero() -> None:
    """Short Hills is behind this train.

    Returning 0 would read as "arriving now" for a train that left four
    minutes ago -- the difference between catching it and watching it go.
    """
    assert run().stops_until("Short Hills Station") is None


def test_a_station_not_on_the_route_is_none() -> None:
    assert run().stops_until("Trenton Station") is None


def test_due_at_works_for_stops_behind_and_ahead() -> None:
    """Scheduled times stay readable either side of the train."""
    train = run()
    behind = train.due_at("Short Hills Station")
    ahead = train.due_at("New York Penn Station")
    assert behind is not None and ahead is not None
    assert behind.strftime("%I:%M %p").lstrip("0") == "8:24 AM"
    assert ahead.strftime("%I:%M %p").lstrip("0") == "9:12 AM"


@pytest.mark.parametrize(
    ("stop_name", "station", "expected"),
    [
        ("Short Hills", "Short Hills Station", True),
        ("New York Penn Station", "New York Penn Station", True),
        ("Hoboken", "Hoboken Terminal", True),
        # `penn` distinguishes nothing between these two, so a stop named only
        # "Penn Station" must match neither rather than both.
        ("Penn Station", "New York Penn Station", False),
        ("Penn Station", "Newark Penn Station", False),
        ("Millburn", "Short Hills Station", False),
        ("Newark Broad Street", "New York Penn Station", False),
    ],
)
def test_station_names_are_matched_across_vocabularies(
    stop_name: str, station: str, expected: bool
) -> None:
    """The stop list is a fourth naming vocabulary.

    It says `Short Hills` where the config flow stores `Short Hills Station`,
    so neither is a prefix of the other in every case.
    """
    train = TrainRun(
        train_id="1",
        stops=parse_stops([{"name": stop_name, "time": "8:00 AM"}], CAPTURED_AT),
    )
    assert (train.stops_until(station) is not None) is expected


def test_rows_without_a_name_are_dropped() -> None:
    """An unidentifiable stop would shift every count by one."""
    stops = parse_stops(
        [{"name": "Summit", "time": "8:20 AM"}, {"name": "", "time": "8:25 AM"}],
        CAPTURED_AT,
    )
    assert [stop.name for stop in stops] == ["Summit"]


def test_an_empty_run_answers_without_raising() -> None:
    train = TrainRun(train_id="9999")
    assert train.last_departed is None
    assert train.next_stop is None
    assert train.stops_until("Anywhere") is None
    assert train.due_at("Anywhere") is None
