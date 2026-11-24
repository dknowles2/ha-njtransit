"""Decoding track circuits into platforms."""

from __future__ import annotations

import pytest

from custom_components.njtransit.api.circuits import decode_platform, has_decoder


class TestPenn:
    """New York Penn: platform = 22 - n."""

    @pytest.mark.parametrize(
        ("circuit", "platform"),
        [
            # The AJO series, letter A or B, from both ends of the scale.
            ("JO-AJO13ATK", "9"),
            ("JO-AJO11BTK", "11"),
            ("JO-AJO21ATK", "1"),
            ("JO-AJO1ATK", "21"),
            # And without a letter: train 6643 on 2026-09-15, seen here at
            # 16:23 and posted on track 6 at 16:36.
            ("JO-AJO16TK", "6"),
            # The prefix is not part of the rule.
            ("AA-AAJO13ATK", "9"),
            # The A-series, two digits then one more.
            ("AA-A190TK", "3"),
            ("AA-A150TK", "7"),
            # Recorded beside the board: train 3889 posted on 3, standing here.
            ("aa-a190tk", "3"),
        ],
    )
    def test_decodes_platform_circuits(self, circuit: str, platform: str) -> None:
        assert decode_platform("NY", circuit) == platform

    @pytest.mark.parametrize(
        "circuit",
        [
            # Route and points indications a train sits on at a signal.
            "JO-AJO13AR",
            "JO-AJO13AP",
            "JO-AJO13AUP",
            "AA-A190DP",
            # Track circuits that are not platforms: the throat and beyond.
            "AA-A2TK",
            "AA-062TK",
            "AA-1552TK",
            "JO-AJO0ATK",
            "JO-AJO22TK",
            "HO-7022TK",
            "",
        ],
    )
    def test_everything_else_decodes_to_nothing(self, circuit: str) -> None:
        assert decode_platform("NY", circuit) is None

    def test_none_is_tolerated(self) -> None:
        assert decode_platform("NY", None) is None


class TestOtherStations:
    """A station with no decoder answers nothing, never something wrong."""

    def test_unknown_station_decodes_nothing(self) -> None:
        assert decode_platform("RT", "AA-A190TK") is None
        assert not has_decoder("RT")

    def test_station_code_is_case_insensitive(self) -> None:
        assert has_decoder("ny")
        assert decode_platform("ny", "AA-A190TK") == "3"
