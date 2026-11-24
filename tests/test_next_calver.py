"""The CalVer rule the release draft is numbered by.

Release-drafter, left to itself, proposed `2026.9.4` in November 2026: it
bumps a position on the previous tag and cannot know a month has passed. These
pin the rule that replaced it -- this month's `.0`, then the next patch -- and
the two ways around it that would quietly mis-number a release.
"""

from __future__ import annotations

import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from next_calver import next_version

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "next_calver.py"


class TestTheRule:
    def test_a_new_month_starts_at_zero(self) -> None:
        """The case release-drafter got wrong."""
        assert next_version("2026.9.3", date(2026, 11, 24)) == "2026.11.0"

    def test_the_same_month_bumps_the_patch(self) -> None:
        assert next_version("2026.11.0", date(2026, 11, 24)) == "2026.11.1"
        assert next_version("2026.11.9", date(2026, 11, 30)) == "2026.11.10"

    def test_a_new_year_starts_at_zero(self) -> None:
        assert next_version("2026.12.4", date(2027, 1, 2)) == "2027.1.0"

    def test_the_month_is_not_zero_padded(self) -> None:
        """`2026.9.0`, as every existing tag is, never `2026.09.0`."""
        assert next_version("2026.8.12", date(2026, 9, 1)) == "2026.9.0"

    def test_no_release_yet_starts_at_zero(self) -> None:
        assert next_version(None, date(2026, 11, 24)) == "2026.11.0"
        assert next_version("", date(2026, 11, 24)) == "2026.11.0"

    def test_a_leading_v_is_read_through(self) -> None:
        """The release workflow tolerates one, so this must too."""
        assert next_version("v2026.11.2", date(2026, 11, 24)) == "2026.11.3"


class TestWhatItRefuses:
    def test_a_release_dated_ahead_never_goes_backwards(self) -> None:
        """A lower version on a newer build reads as a downgrade to HACS."""
        assert next_version("2026.12.0", date(2026, 11, 30)) == "2026.12.1"

    @pytest.mark.parametrize("tag", ["1.2.3", "2026.11", "latest", "2026.11.0-beta"])
    def test_a_tag_that_is_not_calver_fails_rather_than_guesses(self, tag: str) -> None:
        with pytest.raises(ValueError, match="year.month.patch"):
            next_version(tag, date(2026, 11, 24))


class TestTheCommand:
    def test_it_prints_one_version(self) -> None:
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--latest", "2000.1.0"],
            capture_output=True,
            text=True,
            check=True,
        )
        year, month, patch = result.stdout.strip().split(".")
        assert (len(year), patch) == (4, "0")
        assert 1 <= int(month) <= 12

    def test_a_bad_tag_fails_the_step(self) -> None:
        """The workflow must stop, not draft under a guessed number."""
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--latest", "nonsense"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 1
        assert "::error::" in result.stderr
        assert result.stdout == ""
