#!/usr/bin/env python3
"""What the signalling system said was standing on each Penn platform, as of when.

`collect_raildata.py` records every running train's last track circuit. At New
York Penn the platform circuits decode to a platform number, and this turns
that change log into the one question a prediction can actually ask: *at this
instant, which platforms held a parked set, and whose?*

The decode is a formula, not a table. Amtrak's interlocking numbers the
platform tracks in the opposite direction to the public signs, and the two
scales meet at 11:

    platform = 22 - n

where n is the number after `AJO` in a circuit like `AA-AAJO13ATK`, or the two
digits after `A` in one like `AA-A190TK`. Written down as a hypothesis against
five pairs on the first night, confirmed on the next ten it had never seen, and
holding at 59 of 62 after a full day. Only circuits ending `TK` are track
circuits; the others (`R`, `P`, `N`, `UP`, `DP` ...) are route and points
indications a train may sit on while waiting at a signal, and decode to nothing.

**The as-of rule is the whole point of this module.** `Occupancy.at(when)`
answers from records with `t <= when` and nothing later. A feature computed
from it at thirty minutes before departure is therefore something that could
actually have been known thirty minutes before departure -- the same
discipline `_known_by` imposes on the board, and the reason m4's number in
issue #35 is not to be trusted.

How a parked set is inferred, since the feed never says "parked":

* an **eastbound** train's last circuit before it vanishes, if it decodes to a
  platform, is a set parking there. The feed drops a train the moment its trip
  completes, which is usually still on a switch -- about one arrival in five
  is caught on the platform. This is the coverage limit and it is the feed's,
  not ours.
* a **westbound** train reported on a platform circuit is a set standing
  there under its outbound number. Rare more than a few minutes before the
  board posts, because the outbound number is assigned at about that moment.
* a set stops being counted as parked when a westbound train is seen leaving
  that platform for the throat, when another set arrives on it, or after
  `MAX_DWELL`, whichever is first.

Everything below is stdlib. This is an analysis tool, not shipped code.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import NamedTuple
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/New_York")

_AJO = re.compile(r"AJO(\d+)[AB]TK$")
_A_SERIES = re.compile(r"-A(\d\d)\dTK$")

# A set left parked longer than this is assumed to have gone to the yard
# unobserved. Midday arrivals on platform 8 never reappeared as departures --
# four of them in an hour, which one platform cannot hold -- so "still there"
# is not a safe default.
MAX_DWELL = timedelta(hours=4)

# The feed's own wording for lines, reduced to something the track history's
# wording also reduces to. Both call the Northeast Corridor by different
# abbreviations, and a feature that compares them raw would never match.
_LINE_KEYS = (
    ("northeast", "NEC"),
    ("coast", "NJCL"),
    ("gladstone", "GLAD"),
    ("morris", "M&E"),
    ("montclair", "MOBO"),
    ("raritan", "RARV"),
    ("bergen", "BERG"),
    ("pascack", "PASC"),
    ("main", "MAIN"),
)


def line_key(name: str | None) -> str | None:
    """Return a short key both data sources' line names reduce to."""
    if not name:
        return None
    lowered = name.lower()
    for needle, key in _LINE_KEYS:
        if needle in lowered:
            return key
    return lowered


def decode(circuit: str | None) -> str | None:
    """Return the public platform a track circuit stands for, or None."""
    if not circuit:
        return None
    found = _AJO.search(circuit) or _A_SERIES.search(circuit)
    if not found:
        return None
    platform = 22 - int(found.group(1))
    return str(platform) if 1 <= platform <= 21 else None


class Parked(NamedTuple):
    """A set standing on a platform, and how we know."""

    platform: str
    train_id: str
    """The number it arrived under, or is standing under."""
    line: str | None
    since: datetime
    """When the feed first showed it on the platform."""
    inbound: bool
    """Inferred from an arrival (True) or an activated outbound number."""


class Sighting(NamedTuple):
    at: datetime
    train_id: str
    direction: str | None
    line: str | None
    platform: str | None
    """Decoded platform, or None on a switch circuit."""
    vanished: bool
    """This record is the train's last before it left the feed."""


class Occupancy:
    """Every platform sighting, replayable as of any instant."""

    def __init__(self, sightings: list[Sighting]) -> None:
        self.sightings = sorted(sightings, key=lambda s: s.at)

    def train_at(self, train_id: str, when: datetime) -> str | None:
        """Return the platform this train number had last reported by `when`.

        Rare more than a few minutes before the board posts, because the
        outbound number is usually assigned at that moment -- but when it is
        there, it is the answer, and it was public.
        """
        platform = None
        for s in self.sightings:
            if s.at > when:
                break
            if s.train_id == train_id and s.platform is not None:
                platform = s.platform
            elif s.train_id == train_id:
                platform = None
        return platform

    def at(self, when: datetime) -> dict[str, Parked]:
        """Return the sets believed parked at `when`, keyed by platform.

        Reads only sightings at or before `when`. Later ones do not exist yet
        from the point of view of a prediction made then.
        """
        parked: dict[str, Parked] = {}
        for s in self.sightings:
            if s.at > when:
                break
            if s.platform is None:
                # A train reported on a switch. If it was last seen parked on
                # a platform under this number, it has left.
                for platform, p in list(parked.items()):
                    if p.train_id == s.train_id:
                        del parked[platform]
                continue
            arriving = s.direction == "Eastbound"
            if arriving and not s.vanished:
                # Still rolling; the parking is its final report.
                continue
            parked[s.platform] = Parked(
                platform=s.platform,
                train_id=s.train_id,
                line=line_key(s.line),
                since=s.at,
                inbound=arriving,
            )
        return {
            platform: p for platform, p in parked.items() if when - p.since <= MAX_DWELL
        }


def load(*paths: Path) -> Occupancy:
    """Replay collector logs into sightings."""
    by_train: dict[str, list[dict]] = defaultdict(list)
    for path in paths:
        with path.open(encoding="utf-8") as file:
            for line in file:
                if not line.strip():
                    continue
                record = json.loads(line)
                if record.get("type") == "change":
                    by_train[str(record["train_id"])].append(record)

    sightings: list[Sighting] = []
    for train_id, records in by_train.items():
        records.sort(key=lambda r: r["t"])
        with_circuit = [r for r in records if r.get("circuit")]
        last = with_circuit[-1]["t"] if with_circuit else None
        for r in records:
            if not r.get("circuit"):
                continue
            sightings.append(
                Sighting(
                    at=datetime.fromtimestamp(r["t"], TZ),
                    train_id=train_id,
                    direction=r.get("direction"),
                    line=r.get("line"),
                    platform=decode(r["circuit"]),
                    vanished=r["t"] == last,
                )
            )
    return Occupancy(sightings)
