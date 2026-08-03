"""Tests for the raw-payload parsers.

Cases are drawn from the recorded disruption capture wherever possible, so
they exercise real upstream quirks -- inconsistent casing, doubled spaces,
`Update:` prefixes, partial crowding data -- rather than invented input.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from custom_components.njtransit.api.models import CrowdLevel, TrainStatus
from custom_components.njtransit.api.parsing import (
    TZ,
    compute_delay,
    expand_line,
    extract_train_ids,
    parse_alerts,
    parse_board,
    parse_cars,
    parse_countdown,
    parse_crowd_level,
    parse_lines,
    parse_stations,
    parse_status,
    parse_trips,
    resolve_time,
)

from .conftest import load_payload

# The moment the disruption fixtures were captured.
CAPTURED_AT = datetime(2026, 8, 3, 8, 20, tzinfo=TZ)


def at(hour: int, minute: int = 0) -> datetime:
    """Return a local datetime on the capture date."""
    return datetime(2026, 8, 3, hour, minute, tzinfo=TZ)


class TestResolveTime:
    """Bare wall-clock strings resolved against a reference."""

    def test_resolves_against_the_reference_date(self) -> None:
        assert resolve_time("8:25 AM", at(8, 0)) == at(8, 25)

    def test_tolerates_upstream_double_spacing(self) -> None:
        """Alert prose contains "the  7:44 AM arrival" with two spaces."""
        assert resolve_time("7:44  AM", at(7, 0)) == at(7, 44)

    def test_rolls_past_midnight(self) -> None:
        """A board fetched at 23:50 listing 12:05 AM means tomorrow."""
        reference = at(23, 50)
        assert resolve_time("12:05 AM", reference) == at(23, 50).replace(
            hour=0, minute=5
        ) + timedelta(days=1)

    def test_keeps_a_recently_departed_train_on_today(self) -> None:
        """Within the grace window, an earlier time is not tomorrow's."""
        assert resolve_time("7:45 AM", at(8, 20)) == at(7, 45)

    @pytest.mark.parametrize("value", ["", "   ", None, "not a time", "25:00 XM"])
    def test_returns_none_for_unusable_input(self, value: str | None) -> None:
        assert resolve_time(value, at(8, 0)) is None

    def test_result_is_timezone_aware(self) -> None:
        resolved = resolve_time("8:25 AM", at(8, 0))
        assert resolved is not None
        assert resolved.tzinfo is not None

    def test_converts_a_foreign_zone_reference(self) -> None:
        """A UTC reference still resolves against Eastern wall-clock time."""
        reference = datetime(2026, 8, 3, 12, 0, tzinfo=ZoneInfo("UTC"))  # 08:00 ET
        assert resolve_time("8:25 AM", reference) == at(8, 25)

    def test_spring_forward_gap(self) -> None:
        """2:30 AM does not exist on the spring transition; do not crash."""
        reference = datetime(2026, 3, 8, 1, 0, tzinfo=TZ)
        assert resolve_time("2:30 AM", reference) is not None

    def test_fall_back_ambiguity_resolves_to_the_first_occurrence(self) -> None:
        """1:30 AM happens twice; take the earlier, safer reading."""
        reference = datetime(2026, 11, 1, 0, 30, tzinfo=TZ)
        resolved = resolve_time("1:30 AM", reference)
        assert resolved is not None
        assert resolved.fold == 0


class TestStatus:
    """Board status normalization."""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Cancelled", TrainStatus.CANCELLED),
            ("CANCELLED", TrainStatus.CANCELLED),
            ("cancelled", TrainStatus.CANCELLED),
            ("in 21 Min", TrainStatus.ON_TIME),
            ("in 4 Min", TrainStatus.ON_TIME),
            ("BOARDING", TrainStatus.BOARDING),
            ("ALL ABOARD", TrainStatus.ALL_ABOARD),
            ("Late", TrainStatus.DELAYED),
            ("Delayed", TrainStatus.DELAYED),
            ("", TrainStatus.UNKNOWN),
            (None, TrainStatus.UNKNOWN),
            ("Something New Upstream", TrainStatus.UNKNOWN),
        ],
    )
    def test_normalizes(self, raw: str | None, expected: TrainStatus) -> None:
        assert parse_status(raw) == expected

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("in 21 Min", 21), ("in 4 min", 4), ("", None), (None, None), ("Late", None)],
    )
    def test_countdown(self, raw: str | None, expected: int | None) -> None:
        assert parse_countdown(raw) == expected


class TestComputeDelay:
    """Delay derived from countdown versus scheduled time."""

    def test_late_train(self) -> None:
        """Scheduled 8:25, counting down 21 minutes from 8:20 -> 16 late."""
        assert compute_delay("in 21 Min", at(8, 25), at(8, 20)) == 16

    def test_on_time_train_is_zero(self) -> None:
        assert compute_delay("in 5 Min", at(8, 25), at(8, 20)) == 0

    def test_early_is_clamped(self) -> None:
        """Trains do not depart early; never report a negative delay."""
        assert compute_delay("in 1 Min", at(8, 25), at(8, 20)) == 0

    def test_no_realtime_data_is_none_not_zero(self) -> None:
        """An empty status means "unknown", which is not "on time"."""
        assert compute_delay("", at(8, 25), at(8, 20)) is None

    def test_missing_schedule_is_none(self) -> None:
        assert compute_delay("in 5 Min", None, at(8, 20)) is None


class TestExtractTrainIds:
    """Train numbers pulled out of alert prose."""

    def test_bare_and_hashed_forms(self) -> None:
        message = "M and E train 6612 is late; M and E train #6607 is later"
        assert extract_train_ids(message) == {"6612", "6607"}

    def test_ignores_suggested_substitutes(self) -> None:
        """The substitute is the remedy, not a casualty."""
        message = (
            "M and E train #6324, the 8:54 AM departure from Summit, is cancelled. "
            "Please take train #7877, the 9:14 PM departure from PSNY."
        )
        assert extract_train_ids(message) == {"6324"}

    def test_handles_non_numeric_ids(self) -> None:
        """Trenton's board carries Amtrak services like A79."""
        assert extract_train_ids("Amtrak train A79 is delayed") == {"A79"}

    @pytest.mark.parametrize("message", ["", None, "Track work this weekend"])
    def test_line_level_alerts_name_no_train(self, message: str | None) -> None:
        assert extract_train_ids(message) == frozenset()

    def test_matches_the_real_disruption_feed(
        self, system_status: list[dict[str, Any]]
    ) -> None:
        """Against the recorded capture, the exact expected set comes out."""
        found: set[str] = set()
        for alert in system_status:
            if alert["abbreviation"] == "MNE" and alert["advisoryAlert"] == "0":
                found |= extract_train_ids(alert["message"])
        assert found == {"309", "6311", "6324", "6607"}


class TestExpandLine:
    """Umbrella line codes."""

    @pytest.mark.parametrize(
        ("code", "expected"),
        [
            ("MNE", {"MNE", "MNEG"}),
            ("BNTN", {"BNTN", "BNTNM"}),
            ("NEC", {"NEC"}),
            ("SOMETHINGNEW", {"SOMETHINGNEW"}),
        ],
    )
    def test_expands(self, code: str, expected: set[str]) -> None:
        assert expand_line(code) == expected


class TestCrowding:
    """Per-car colour decoding."""

    @pytest.mark.parametrize(
        ("color", "expected"),
        [
            ("#0B6623", CrowdLevel.LIGHT),
            ("#0b6623", CrowdLevel.LIGHT),
            ("#FFD300", CrowdLevel.MODERATE),
            ("#123456", CrowdLevel.UNKNOWN),
            ("", CrowdLevel.UNKNOWN),
            (None, CrowdLevel.UNKNOWN),
        ],
    )
    def test_decodes(self, color: str | None, expected: CrowdLevel) -> None:
        assert parse_crowd_level(color) == expected

    def test_absent_capacity_is_empty_not_an_error(self) -> None:
        assert parse_cars(None) == ()
        assert parse_cars({}) == ()

    def test_flattens_sections_keeping_position(self) -> None:
        cars = parse_cars(
            {
                "sections": [
                    {
                        "position": "Front",
                        "cars": [{"color": "#0B6623", "number": "1"}],
                    },
                    {"position": "Back", "cars": [{"color": "#FFD300", "number": "2"}]},
                ]
            }
        )
        assert [(car.number, car.position, car.level) for car in cars] == [
            ("1", "Front", CrowdLevel.LIGHT),
            ("2", "Back", CrowdLevel.MODERATE),
        ]


class TestParseBoard:
    """The departure board, against the recorded capture."""

    def test_parses_every_usable_row(self, departure_board: dict[str, Any]) -> None:
        board = parse_board("Short Hills Station", departure_board, CAPTURED_AT)
        assert len(board.departures) == len(departure_board["items"])

    def test_normalizes_mixed_casing_cancellations(
        self, departure_board: dict[str, Any]
    ) -> None:
        """6320 and 6311 are cancelled with different casing upstream."""
        board = parse_board("Short Hills Station", departure_board, CAPTURED_AT)
        cancelled = {
            departure.train_id
            for departure in board.departures
            if departure.status is TrainStatus.CANCELLED
        }
        assert {"6320", "6311"} <= cancelled

    def test_preserves_raw_status(self, departure_board: dict[str, Any]) -> None:
        board = parse_board("Short Hills Station", departure_board, CAPTURED_AT)
        by_id = {departure.train_id: departure for departure in board.departures}
        assert by_id["6320"].status_raw == "Cancelled"
        assert by_id["6311"].status_raw == "CANCELLED"

    def test_crowding_is_partial(self, departure_board: dict[str, Any]) -> None:
        board = parse_board("Short Hills Station", departure_board, CAPTURED_AT)
        with_cars = [d for d in board.departures if d.cars]
        assert 0 < len(with_cars) < len(board.departures)

    def test_departures_without_realtime_have_no_delay(
        self, departure_board: dict[str, Any]
    ) -> None:
        board = parse_board("Short Hills Station", departure_board, CAPTURED_AT)
        no_status = [d for d in board.departures if not d.status_raw]
        assert no_status, "capture no longer has rows lacking realtime data"
        assert all(d.delay_minutes is None for d in no_status)

    def test_skips_rows_without_a_train_id(self) -> None:
        board = parse_board(
            "Nowhere",
            {"items": [{"trainID": "", "departureDate": "8:25 AM"}]},
            CAPTURED_AT,
        )
        assert board.departures == ()

    def test_empty_payload_is_an_empty_board(self) -> None:
        board = parse_board("Nowhere", None, CAPTURED_AT)
        assert board.departures == ()
        assert board.banner_message is None


class TestParseAlerts:
    """The system status feed, against the recorded capture."""

    def test_splits_live_incidents_from_advisories(
        self, system_status: list[dict[str, Any]]
    ) -> None:
        alerts = parse_alerts(system_status)
        mne = [a for a in alerts if a.line_abbreviation == "MNE"]
        assert [a for a in mne if not a.is_advisory]
        assert [a for a in mne if a.is_advisory]

    def test_empty_payload(self) -> None:
        assert parse_alerts(None) == ()


class TestParseTrips:
    """Trip planner itineraries."""

    def test_extracts_rail_trains_in_order(self) -> None:
        trips = parse_trips(
            load_payload("trip_planner_short_hills_to_ny", "getTripPlannerSchedule"),
            CAPTURED_AT,
        )
        assert trips
        assert all(trip.train_id == trip.train_ids[0] for trip in trips)

    def test_identifies_transfers(self) -> None:
        """The Gladstone-to-Summit itinerary needs a change of train."""
        trips = parse_trips(
            load_payload("trip_planner_short_hills_to_ny", "getTripPlannerSchedule"),
            CAPTURED_AT,
        )
        assert any(trip.has_transfer for trip in trips)

    def test_skips_the_null_block_sentinel_leg(self) -> None:
        """The planner appends a leg with no block; it is not a train."""
        trips = parse_trips(
            load_payload("trip_planner_short_hills_to_ny", "getTripPlannerSchedule"),
            CAPTURED_AT,
        )
        assert all("" not in trip.train_ids for trip in trips)
        assert all(all(trip.train_ids) for trip in trips)

    def test_excludes_path_legs(self) -> None:
        """PATH blocks come from an unrelated namespace and must not leak."""
        trips = parse_trips(
            load_payload(
                "trip_planner_short_hills_to_hoboken", "getTripPlannerSchedule"
            ),
            CAPTURED_AT,
        )
        assert all("114992" not in trip.train_ids for trip in trips)

    def test_arrival_is_the_end_of_the_itinerary_not_the_last_train(self) -> None:
        """A mixed-mode itinerary continues after its last rail leg.

        Train 880 reaches Hoboken at 6:45 PM; the journey continues by bus
        and subway and reaches Penn Station at 7:27 PM. Reading the rail
        leg's arrival reported a 35-minute trip to New York, which is both
        wrong and flattering enough to look like the best option on the
        board.
        """
        trips = parse_trips(
            load_payload(
                "trip_planner_multimodal_short_hills_to_ny", "getTripPlannerSchedule"
            ),
            at(17, 43),
        )
        assert len(trips) == 1
        trip = trips[0]

        assert trip.train_ids == ("880",)
        assert trip.departure.strftime("%I:%M %p").lstrip("0") == "6:10 PM"
        assert trip.arrival.strftime("%I:%M %p").lstrip("0") == "7:27 PM"
        assert (trip.arrival - trip.departure).total_seconds() / 60 == 77

    def test_arrival_after_departure_across_midnight(self) -> None:
        """A journey crossing midnight must not arrive before it departs."""
        trips = parse_trips(
            [
                {
                    "duration": "45 min",
                    "legs": [
                        {
                            "block": "999",
                            "routeType": "C",
                            "onStopTime": "11:45 PM",
                            "offStopTime": "12:30 AM",
                        }
                    ],
                }
            ],
            at(23, 0),
        )
        assert len(trips) == 1
        assert trips[0].arrival > trips[0].departure

    def test_ignores_itineraries_with_no_rail_leg(self) -> None:
        trips = parse_trips(
            [{"duration": "10 min", "legs": [{"routeType": "W", "block": None}]}],
            CAPTURED_AT,
        )
        assert trips == ()


class TestParseStations:
    """The canonical station list."""

    def test_keeps_alias_rows(self) -> None:
        """Deduplication is the caller's decision, not the parser's."""
        stations = parse_stations(
            load_payload("stations_rail_dv", "getTrainScheduleStationsRailForDV")
        )
        ny_aliases = [s for s in stations if s.penta_id == "NY"]
        assert len(ny_aliases) > 1

    def test_skips_rows_missing_an_identifier(self) -> None:
        stations = parse_stations(
            [
                {"title": "Real Station", "pentaStationID": "RS"},
                {"title": "", "pentaStationID": "XX"},
                {"title": "No ID", "pentaStationID": ""},
            ]
        )
        assert [station.penta_id for station in stations] == ["RS"]


class TestParseLines:
    """The rail line list."""

    def test_parses_the_capture(self) -> None:
        lines = parse_lines(load_payload("train_lines", "getTrainLines"))
        abbreviations = {line.abbreviation for line in lines}
        assert {"MNE", "MNEG", "NEC"} <= abbreviations

    def test_skips_rows_without_an_abbreviation(self) -> None:
        assert parse_lines([{"id": "x", "title": "y", "abbreviation": ""}]) == ()
