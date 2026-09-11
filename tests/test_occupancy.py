"""Decoding Penn's track circuits, and replaying them as of an instant.

The decode is a formula found by looking at five pairs and confirmed on the
next ten. The pairs below are real ones from the collector, so a change to the
formula that still fits a made-up example cannot slip past.

The replay has one property that matters more than any other: `at(when)`
must not see a sighting later than `when`. A feature built from it at T-30 is
meant to be something knowable at T-30, and the way that goes wrong is the
same way m4 went wrong -- quietly, with a better number.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from occupancy import TZ, Occupancy, Sighting, decode, line_key, load

T0 = datetime(2026, 9, 11, 18, 0, tzinfo=TZ)


class TestTheDecode:
    def test_known_pairs_from_the_collector(self) -> None:
        """Real circuits and the platforms DepartureVision posted for them."""
        assert decode("AA-AAJO11ATK") == "11"
        assert decode("AA-AAJO9ATK") == "13"
        assert decode("JO-AJO13ATK") == "9"
        assert decode("JO-AJO15ATK") == "7"
        assert decode("AA-AAJO10ATK") == "12"
        assert decode("AA-AAJO12ATK") == "10"
        assert decode("AA-A190TK") == "3"
        assert decode("AA-A180TK") == "4"
        assert decode("AA-A200TK") == "2"
        assert decode("AA-A132TK") == "9"

    def test_the_b_half_of_a_platform_is_the_same_platform(self) -> None:
        """Arrivals stop on the B end; the first version only matched A, and
        found zero turns because of it."""
        assert decode("AA-AAJO11BTK") == "11"

    def test_switch_and_route_circuits_decode_to_nothing(self) -> None:
        """A train can sit on these for minutes waiting at a signal. They are
        not platforms, and reading a number out of them gave four wrong
        answers on the first night."""
        for circuit in ("AA-0652TK", "AA-49DP", "AA-77P", "AA-137N", "AA-145R"):
            assert decode(circuit) is None, circuit

    def test_nothing_is_nothing(self) -> None:
        assert decode(None) is None
        assert decode("") is None


class TestLineKeys:
    def test_both_sources_reduce_to_one_key(self) -> None:
        """The feed and the track history name the same line differently."""
        assert line_key("Northeast Corridor Line") == line_key("Northeast Corrdr")
        assert line_key("Morristown Line") == line_key("Morris & Essex Line")


def sighting(
    minutes: float,
    train: str,
    platform: str | None,
    *,
    direction: str = "Eastbound",
    vanished: bool = False,
) -> Sighting:
    return Sighting(
        at=T0 + timedelta(minutes=minutes),
        train_id=train,
        direction=direction,
        line="Northeast Corridor Line",
        platform=platform,
        vanished=vanished,
    )


class TestAsOf:
    """What `at(when)` may and may not see."""

    def test_a_later_sighting_does_not_exist_yet(self) -> None:
        """The leak. A set that parks at 18:10 is not parked at 18:05."""
        occ = Occupancy([sighting(10, "3284", "11", vanished=True)])

        assert occ.at(T0 + timedelta(minutes=5)) == {}
        assert "11" in occ.at(T0 + timedelta(minutes=10))

    def test_an_arrival_counts_only_once_it_stops_reporting(self) -> None:
        """While the feed still shows it moving, it has not parked."""
        occ = Occupancy(
            [
                sighting(0, "3284", "11"),
                sighting(1, "3284", "11", vanished=True),
            ]
        )

        assert occ.at(T0 + timedelta(seconds=30)) == {}
        assert occ.at(T0 + timedelta(minutes=1))["11"].train_id == "3284"

    def test_a_departure_seen_leaving_frees_the_platform(self) -> None:
        occ = Occupancy(
            [
                sighting(0, "3284", "11", vanished=True),
                sighting(20, "3201", "11", direction="Westbound"),
                sighting(40, "3201", None, direction="Westbound"),
            ]
        )

        assert occ.at(T0 + timedelta(minutes=30))["11"].train_id == "3201"
        assert occ.at(T0 + timedelta(minutes=40)) == {}

    def test_a_set_parked_too_long_is_assumed_gone(self) -> None:
        """Four arrivals on platform 8 in an hour, none ever seen leaving."""
        occ = Occupancy([sighting(0, "3236", "8", vanished=True)])

        assert "8" in occ.at(T0 + timedelta(hours=3))
        assert "8" not in occ.at(T0 + timedelta(hours=5))


def test_load_marks_the_last_report_as_the_vanishing(tmp_path: Path) -> None:
    log = tmp_path / "raildata.jsonl"
    rows = [
        {
            "type": "change",
            "t": int(T0.timestamp()),
            "train_id": "3284",
            "direction": "Eastbound",
            "line": "NEC",
            "circuit": "AA-0652TK",
        },
        {
            "type": "change",
            "t": int(T0.timestamp()) + 60,
            "train_id": "3284",
            "direction": "Eastbound",
            "line": "NEC",
            "circuit": "AA-AAJO11BTK",
        },
        {"type": "poll", "t": int(T0.timestamp()) + 60},
    ]
    log.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")

    occ = load(log)

    assert [s.vanished for s in occ.sightings] == [False, True]
    assert occ.at(T0 + timedelta(minutes=2))["11"].inbound is True
