#!/usr/bin/env python3
"""Work out the version the next release should carry.

Tags are CalVer, `year.month.patch`, with no `v` (AGENTS.md, "Releases"). The
first release of a month is `.0`; every one after it in the same month is the
next patch. That rule is all this does, and it exists because release-drafter
cannot do it: the action only knows how to bump a semver position on the
previous tag, so on its own it proposed `2026.9.4` in November -- a September
version, two months late. The release-drafter workflow passes this script's
answer to the action's `version` input, which overrides whatever it resolved.

The month is New York's. That is where the trains are and where the person
cutting the release is, and a UTC month would roll over at 7 or 8pm local on
the last day.

Usage:
    python scripts/next_calver.py --latest 2026.9.3          # -> 2026.11.0 in November
    python scripts/next_calver.py --latest ""                # no release yet -> .0

Everything below is stdlib. This is a release tool, not shipped code.
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import date, datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/New_York")

# A leading `v` is tolerated because the release workflow tolerates one when
# stamping the manifest. Anything else is not a version this repository has
# ever used, and guessing past it would draft a release under a wrong number.
_CALVER = re.compile(r"v?(\d{4})\.(\d{1,2})\.(\d+)")


def next_version(latest: str | None, today: date) -> str:
    """Return the version for the next release.

    :param latest: The newest published release's tag, or None when there
        has never been one.
    :param today: The date the release would be cut, in New York.
    :raise ValueError: ``latest`` is not a CalVer tag.
    """
    if not latest:
        return f"{today.year}.{today.month}.0"

    found = _CALVER.fullmatch(latest.strip())
    if found is None:
        raise ValueError(f"latest release tag {latest!r} is not year.month.patch")
    year, month, patch = (int(part) for part in found.groups())

    if (year, month) == (today.year, today.month):
        return f"{year}.{month}.{patch + 1}"
    if (year, month) > (today.year, today.month):
        # A release dated after today: a clock or timezone disagreement, or a
        # hand-tagged slip. Going back to this month's `.0` would put a lower
        # version on a newer build, which HACS reads as a downgrade -- so stay
        # on the newer month and keep counting.
        return f"{year}.{month}.{patch + 1}"
    return f"{today.year}.{today.month}.0"


def main() -> int:
    """Print the next version for the newest release passed in."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--latest",
        required=True,
        help="the newest published release's tag; empty when there is none",
    )
    args = parser.parse_args()
    try:
        print(next_version(args.latest or None, datetime.now(TZ).date()))
    except ValueError as error:
        print(f"::error::{error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
