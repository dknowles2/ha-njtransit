"""Parsing RailData payloads into the shared models."""

from __future__ import annotations

from datetime import datetime

import pytest

from custom_components.njtransit.api.models import CrowdLevel, TrainStatus
from custom_components.njtransit.api.parsing import TZ, alert_line_codes
from custom_components.njtransit.api.raildata_parsing import (
    LINES,
    join_schedules,
    parse_alert_codes,
    parse_board,
    parse_delay,
    parse_messages,
    parse_schedule,
    parse_sightings,
    parse_stations,
    parse_timestamp,
    parse_train_run,
    translate_track,
)

from .conftest import load_raildata_fixture


class TestTimestamps:
    """Full timestamps, so no midnight guesswork."""

    def test_parses_the_feed_format(self) -> None:
        assert parse_timestamp("14-Sep-2026 09:32:00 PM") == datetime(
            2026, 9, 14, 21, 32, tzinfo=TZ
        )

    def test_tolerates_padding(self) -> None:
        assert parse_timestamp("  14-Sep-2026  09:32:00 PM ") == datetime(
            2026, 9, 14, 21, 32, tzinfo=TZ
        )

    @pytest.mark.parametrize("value", [None, "", "  ", "9:32 PM", "not a time"])
    def test_anything_else_is_none(self, value: str | None) -> None:
        assert parse_timestamp(value) is None


class TestBoard:
    """The Short Hills board, recorded 2026-09-14 21:27 EDT."""

    def test_parses_every_row(self) -> None:
        board = parse_board(
            "Short Hills Station",
            "RT",
            load_raildata_fixture("train_schedule19_short_hills"),
        )

        assert board.station == "Short Hills Station"
        assert len(board.departures) == 14
        assert board.banner_message is None
        assert board.fullscreen_message is None

    def test_a_live_row(self) -> None:
        """Train 6671: `in 9 Min`, 50 seconds late, six cars reporting."""
        board = parse_board(
            "Short Hills Station",
            "RT",
            load_raildata_fixture("train_schedule19_short_hills"),
        )
        train = next(d for d in board.departures if d.train_id == "6671")

        assert train.scheduled == datetime(2026, 9, 14, 21, 36, tzinfo=TZ)
        assert train.destination == "Dover"
        assert train.line == "Morristown Line"
        assert train.line_abbreviation == "M&E"
        assert train.status is TrainStatus.ON_TIME
        assert train.status_raw == "in 9 Min"
        assert train.track == "1"
        assert train.delay_minutes == 0
        assert train.crowding is CrowdLevel.LIGHT
        assert len(train.cars) == 6
        assert train.cars[0].position == "Back"
        assert train.signalled_track is None

    def test_a_row_without_realtime_data(self) -> None:
        """Blank status means nothing is known, not that the train is on time."""
        board = parse_board(
            "Short Hills Station",
            "RT",
            load_raildata_fixture("train_schedule19_short_hills"),
        )
        train = next(d for d in board.departures if d.train_id == "6676")

        assert train.status is TrainStatus.UNKNOWN
        assert train.status_raw == ""
        assert train.delay_minutes is None
        assert train.status_text == ""
        assert train.cars == ()

    def test_rows_past_midnight_keep_their_date(self) -> None:
        board = parse_board(
            "Short Hills Station",
            "RT",
            load_raildata_fixture("train_schedule19_short_hills"),
        )
        late = next(d for d in board.departures if d.train_id == "6603")
        assert late.scheduled == datetime(2026, 9, 15, 1, 43, 30, tzinfo=TZ)

    def test_new_york_statuses(self) -> None:
        """The Penn board writes BOARDING and DELAYED in capitals."""
        board = parse_board(
            "New York Penn Station",
            "NY",
            load_raildata_fixture("train_schedule19_new_york"),
        )
        by_id = {d.train_id: d for d in board.departures}

        assert by_id["3889"].status is TrainStatus.BOARDING
        assert by_id["3889"].track == "3"
        assert by_id["A177"].status is TrainStatus.DELAYED
        assert by_id["5155"].track is None

    def test_rows_without_an_id_or_time_are_dropped(self) -> None:
        board = parse_board(
            "X",
            "XX",
            {
                "ITEMS": [
                    {"TRAIN_ID": "", "SCHED_DEP_DATE": "14-Sep-2026 09:32:00 PM"},
                    {"TRAIN_ID": "1", "SCHED_DEP_DATE": "soon"},
                    {"TRAIN_ID": "2", "SCHED_DEP_DATE": "14-Sep-2026 09:32:00 PM"},
                ]
            },
        )
        assert [d.train_id for d in board.departures] == ["2"]

    def test_station_messages_become_the_banner(self) -> None:
        board = parse_board(
            "Newark Penn Station",
            "NP",
            {"ITEMS": [], "STATIONMSGS": load_raildata_fixture("station_msg")},
        )
        assert board.banner_message is not None
        assert board.banner_message.startswith("ATTENTION PASSENGERS")
        assert board.fullscreen_message == "Full Screen Test MSG for Jersey Ave."

    def test_an_empty_payload_is_an_empty_board(self) -> None:
        assert parse_board("X", "XX", None).departures == ()


class TestDelay:
    """`SEC_LATE` is always present; a blank status is what makes it mean nothing."""

    @pytest.mark.parametrize(
        ("status", "seconds", "expected"),
        [
            ("in 9 Min", "50", 0),
            ("in 13 Min", "1328", 22),
            ("BOARDING", "-60", 0),
            ("DELAYED", "0", 0),
            ("", "600", None),
            ("in 9 Min", "", None),
            ("in 9 Min", None, None),
            ("in 9 Min", "lots", None),
        ],
    )
    def test_delay(
        self, status: str, seconds: str | None, expected: int | None
    ) -> None:
        assert parse_delay(status, seconds) == expected


class TestTrackTranslation:
    """Appendix II: a few stations report the railroad's number, not the sign's."""

    @pytest.mark.parametrize(
        ("code", "track", "expected"),
        [
            ("TS", "4", "E"),
            ("TS", "1", "G"),
            ("MP", "2", "1"),
            ("ST", "Single", "S"),
            ("UV", "B", "2"),
            ("NA", "0", "A"),
            # Everywhere else the number is the number.
            ("NY", "4", "4"),
            ("RT", "2", "2"),
        ],
    )
    def test_translates(self, code: str, track: str, expected: str) -> None:
        assert translate_track(code, track) == expected


class TestMessages:
    """`getStationMSG`, assembled from the documentation's examples."""

    def test_one_alert_per_line_in_scope(self) -> None:
        alerts = parse_messages(load_raildata_fixture("station_msg"))
        two_lines = [a for a in alerts if "overhead wire" in a.message]

        assert {a.line_abbreviation for a in two_lines} == {"NEC", "NJCL"}
        assert all(a.service == "Rail" for a in alerts)
        assert not any(a.is_advisory for a in alerts)

    def test_train_ids_are_extracted_and_substitutes_excluded(self) -> None:
        alerts = parse_messages(load_raildata_fixture("station_msg"))
        by_text = {a.message[:12]: a for a in alerts}

        assert by_text["NJCL train #"].train_ids == frozenset({"3240"})
        # "Please take train #3846" is the remedy, not a casualty.
        assert by_text["Northeast Co"].train_ids == frozenset()

    def test_the_live_message(self) -> None:
        """The one real message in the fixture: recorded 2026-09-14 22:10 EDT."""
        alerts = parse_messages(load_raildata_fixture("station_msg"))
        live = next(a for a in alerts if "6676" in a.message)

        assert live.line_abbreviation == "MNE"
        assert live.train_ids == frozenset({"6676"})
        assert live.url == "https://www.njtransit.com/node/2169694"
        assert live.message_html is None

    def test_a_station_banner_has_no_line(self) -> None:
        alerts = parse_messages(load_raildata_fixture("station_msg"))
        banner = next(a for a in alerts if "CROSS HONOR" in a.message)
        assert banner.line_abbreviation == ""

    def test_empty_messages_are_dropped(self) -> None:
        assert parse_messages([{"MSG_TEXT": " ", "MSG_LINE_SCOPE": "*Main Line"}]) == ()
        assert parse_messages(None) == ()

    @pytest.mark.parametrize(
        ("scope", "codes"),
        [
            ("*North Jersey Coast Line", {"NJCL"}),
            ("*Northeast Corridor Line,*North Jersey Coast Line", {"NEC", "NJCL"}),
            ("*Main Line, *Bergen County Line", {"MNBN"}),
            ("*Gladstone Branch", {"MNEG"}),
            ("*Morris & Essex Line", {"MNE"}),
            (" ", set()),
            (None, set()),
            ("*Hudson-Bergen Light Rail", set()),
        ],
    )
    def test_line_scopes(self, scope: str | None, codes: set[str]) -> None:
        assert parse_alert_codes(scope) == frozenset(codes)

    def test_the_line_table_matches_the_board_the_way_the_website_does(self) -> None:
        """A Short Hills board resolves to the same alert codes on both sources.

        `alert_line_codes` walks board titles through the line list to the
        umbrella codes the alert feed uses. RailData's table must produce the
        same answer as the website's `getTrainLines` did for the same board,
        or one alert sensor cannot serve both.
        """
        codes = alert_line_codes({"Morristown Line", "Gladstone Branch"}, LINES)
        assert codes == frozenset({"MNE", "MNEG"})


class TestStations:
    """`getStationList`."""

    def test_parses_the_list(self) -> None:
        stations = parse_stations(load_raildata_fixture("station_list"))
        by_code = {s.penta_id: s for s in stations}

        assert len(stations) == 173
        assert by_code["RT"].title == "Short Hills"
        assert by_code["RT"].accessible is False
        assert by_code["NY"].title == "New York Penn Station"
        assert by_code["NY"].accessible is True

    def test_no_alias_rows(self) -> None:
        stations = parse_stations(load_raildata_fixture("station_list"))
        assert len({s.penta_id for s in stations}) == len(stations)

    def test_rows_without_a_code_are_dropped(self) -> None:
        assert parse_stations([{"STATIONNAME": "Nowhere", "STATION_2CHAR": ""}]) == ()


class TestStops:
    """`getTrainStopList`."""

    def test_parses_a_run(self) -> None:
        run = parse_train_run("6295", load_raildata_fixture("train_stop_list_6295"))

        assert run.train_id == "6295"
        assert run.stops[0].name == "New York Penn Station"
        assert run.stops[0].scheduled == datetime(2026, 9, 14, 21, 32, tzinfo=TZ)
        assert run.stops[0].departed is False
        assert run.stops[0].status is TrainStatus.BOARDING
        assert run.next_stop is run.stops[0]

    def test_scheduled_is_the_timetable_not_the_estimate(self) -> None:
        run = parse_train_run(
            "1",
            {
                "STOPS": [
                    {
                        "STATIONNAME": "Somewhere",
                        "TIME": "14-Sep-2026 09:40:00 PM",
                        "DEP_TIME": "14-Sep-2026 09:32:00 PM",
                        "DEPARTED": "YES",
                        "STOP_STATUS": "Delayed",
                    }
                ]
            },
        )
        assert run.stops[0].scheduled == datetime(2026, 9, 14, 21, 32, tzinfo=TZ)
        assert run.stops[0].departed is True
        assert run.stops[0].status is TrainStatus.DELAYED

    def test_an_unknown_train_has_no_stops(self) -> None:
        """The endpoint answers with every field null."""
        run = parse_train_run("9999", {"TRAIN_ID": None, "STOPS": None})
        assert run.train_id == "9999"
        assert run.stops == ()


class TestSchedule:
    """`getStationSchedule`, joined into journeys."""

    def test_parses_a_station_day(self) -> None:
        calls = parse_schedule(
            "RT", load_raildata_fixture("station_schedule_short_hills")
        )
        assert len(calls) == 105
        assert calls == tuple(sorted(calls, key=lambda c: c.departs))

    def test_only_the_requested_station_block_is_read(self) -> None:
        payload = load_raildata_fixture("station_schedule_short_hills")
        assert parse_schedule("NY", payload) == ()

    def test_arrival_comes_from_the_dwell(self) -> None:
        calls = parse_schedule(
            "RT", load_raildata_fixture("station_schedule_short_hills")
        )
        call = next(c for c in calls if c.train_id == "6636")
        assert (call.departs - call.arrives).total_seconds() == 60

    def test_joins_into_direct_trains(self) -> None:
        """23 direct trains a day, Short Hills to New York, as SPEC 2.7 counts."""
        trips = join_schedules(
            parse_schedule("RT", load_raildata_fixture("station_schedule_short_hills")),
            parse_schedule("NY", load_raildata_fixture("station_schedule_new_york")),
        )

        assert len(trips) == 23
        first = trips[0]
        assert first.train_id == "6602"
        assert first.departure == datetime(2026, 9, 14, 4, 49, 30, tzinfo=TZ)
        assert first.arrival == datetime(2026, 9, 14, 5, 36, tzinfo=TZ)
        assert first.duration == "46 min"
        assert first.train_ids == ("6602",)
        assert not first.has_transfer

    def test_the_other_direction_is_a_different_set(self) -> None:
        trips = join_schedules(
            parse_schedule("NY", load_raildata_fixture("station_schedule_new_york")),
            parse_schedule("RT", load_raildata_fixture("station_schedule_short_hills")),
        )
        assert len(trips) == 30
        assert all(trip.departure < trip.arrival for trip in trips)

    def test_a_station_joined_with_itself_yields_nothing(self) -> None:
        """Every New York call either originates or terminates there.

        A terminating call cannot start a journey and an originating one
        cannot end it. And the 27-hour window holds train 3201 twice, a day
        apart -- without the duration bound, this morning's 00:05 would
        pair with tomorrow's as a 1440-minute trip.
        """
        trips = join_schedules(
            parse_schedule("NY", load_raildata_fixture("station_schedule_new_york")),
            parse_schedule("NY", load_raildata_fixture("station_schedule_new_york")),
        )
        assert trips == ()

    def test_a_run_a_day_later_is_not_this_journey(self) -> None:
        """The 27-hour window can hold the same number twice.

        Here the only call at the destination is tomorrow's, a reachable
        stop 24 hours on. Without the duration bound it pairs.
        """
        origin = parse_schedule(
            "XX", _day("XX", [("1", "14-Sep-2026 09:00:00 AM", "1", "S")])
        )
        destination = parse_schedule(
            "YY", _day("YY", [("1", "15-Sep-2026 09:47:00 AM", "1", "S")])
        )
        assert join_schedules(origin, destination) == ()

    def test_the_nearest_later_call_is_the_arrival(self) -> None:
        origin = parse_schedule(
            "XX", _day("XX", [("1", "14-Sep-2026 09:00:00 AM", "1", "S")])
        )
        destination = parse_schedule(
            "YY",
            _day(
                "YY",
                [
                    ("1", "14-Sep-2026 09:47:00 AM", "1", "S"),
                    ("1", "15-Sep-2026 09:47:00 AM", "1", "S"),
                ],
            ),
        )
        trips = join_schedules(origin, destination)
        assert len(trips) == 1
        assert trips[0].duration == "47 min"

    def test_discharge_only_stops_are_not_boardable(self) -> None:
        origin = parse_schedule(
            "XX",
            [
                {
                    "STATION_2CHAR": "XX",
                    "ITEMS": [
                        {
                            "TRAIN_ID": "1",
                            "SCHED_DEP_DATE": "14-Sep-2026 09:00:00 AM",
                            "STATION_POSITION": "1",
                            "STOP_CODE": "D",
                        }
                    ],
                }
            ],
        )
        destination = parse_schedule(
            "YY",
            [
                {
                    "STATION_2CHAR": "YY",
                    "ITEMS": [
                        {
                            "TRAIN_ID": "1",
                            "SCHED_DEP_DATE": "14-Sep-2026 10:00:00 AM",
                            "STATION_POSITION": "2",
                        }
                    ],
                }
            ],
        )
        assert join_schedules(origin, destination) == ()


class TestSightings:
    """`getVehicleData`, reduced to platform sightings at one station."""

    def test_only_platform_circuits_at_a_decoded_station(self) -> None:
        sightings = parse_sightings("NY", load_raildata_fixture("vehicle_data"))
        by_id = {s.train_id: s for s in sightings}

        assert set(by_id) == {"3289", "3889"}
        assert by_id["3889"].platform == "3"
        assert by_id["3889"].scheduled == datetime(2026, 9, 14, 21, 35, tzinfo=TZ)

    def test_nothing_at_a_station_without_a_decoder(self) -> None:
        assert parse_sightings("RT", load_raildata_fixture("vehicle_data")) == ()

    def test_tolerates_an_empty_feed(self) -> None:
        assert parse_sightings("NY", None) == ()
        assert parse_sightings("NY", [{"ID": "", "ICS_TRACK_CKT": "AA-A190TK"}]) == ()


def _day(
    code: str, calls: list[tuple[str, str, str, str]]
) -> list[dict[str, str | list]]:
    """Return a one-station ``getStationSchedule`` payload from bare tuples."""
    return [
        {
            "STATION_2CHAR": code,
            "ITEMS": [
                {
                    "TRAIN_ID": train_id,
                    "SCHED_DEP_DATE": departs,
                    "STATION_POSITION": position,
                    "STOP_CODE": stop_code,
                }
                for train_id, departs, position, stop_code in calls
            ],
        }
    ]
