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
            ("AA-AAJO13ATK", "9"),
            ("AA-AAJO11BTK", "11"),
            ("AA-AAJO21ATK", "1"),
            ("AA-AAJO1ATK", "21"),
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
            "AA-AAJO13AR",
            "AA-AAJO13AP",
            "AA-AAJO13AUP",
            "AA-A190DP",
            # Track circuits that are not platforms: the throat and beyond.
            "AA-A2TK",
            "AA-AAJO0ATK",
            "AA-AAJO22ATK",
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
