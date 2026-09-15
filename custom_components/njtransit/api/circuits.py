"""Decode the RailData feed's track circuits into public platform numbers.

``getVehicleData`` reports every running train's last track circuit -- a
signaling-system name like ``AA-AAJO13ATK`` -- rather than a platform. At a
station whose circuit naming has been worked out, that name says which
platform the train is standing on, and it says so before the departure board
does: at New York Penn the board posts about ten minutes ahead of departure,
where the circuit typically shows the set on its platform twenty minutes
ahead (SPEC 2.9).

The decode is per station and a formula, not a lookup. Penn's interlocking
numbers the platform tracks in the opposite direction to the public signs,
and the two scales meet at 11::

    platform = 22 - n

where ``n`` is the number after ``AJO`` in ``AA-AAJO13ATK`` or the two digits
after ``-A`` in ``AA-A190TK``. Written down as a hypothesis against five
pairs on the first night, confirmed on the next ten it had never seen, and
holding at 229 of 231 board postings over the following days. Only circuits
ending ``TK`` are track circuits; the rest (``R``, ``P``, ``N``, ``UP``,
``DP`` ...) are route and points indications a train may sit on while it
waits at a signal, and decode to nothing.

Stations without an entry in :data:`DECODERS` decode to nothing, which is
the honest answer rather than a wrong one. Adding a station means recording
its feed against its board for a few days and finding the rule, the way
``scripts/collect_raildata.py`` and ``scripts/occupancy.py`` did for Penn.
"""

from __future__ import annotations

import re
from collections.abc import Callable

_PENN_AJO = re.compile(r"AJO(\d+)[AB]TK$")
_PENN_A_SERIES = re.compile(r"-A(\d\d)\dTK$")

# Penn's platforms run 1 to 21; anything outside that is a circuit the formula
# does not describe, not a platform nobody has heard of.
_PENN_PLATFORMS = range(1, 22)


def _penn(circuit: str) -> str | None:
    """Return the platform a New York Penn circuit stands for."""
    found = _PENN_AJO.search(circuit) or _PENN_A_SERIES.search(circuit)
    if not found:
        return None
    platform = 22 - int(found.group(1))
    return str(platform) if platform in _PENN_PLATFORMS else None


DECODERS: dict[str, Callable[[str], str | None]] = {
    "NY": _penn,
}
"""Circuit decoders by two-character station code."""


def decode_platform(station_code: str, circuit: str | None) -> str | None:
    """Return the public platform ``circuit`` stands for at a station.

    :param station_code: The station's two-character code, e.g. ``NY``.
    :param circuit: The feed's ``ICS_TRACK_CKT`` value.
    :return: A platform number as the signs show it, or ``None`` when the
        station has no decoder or the circuit is not a platform track.
    """
    if not circuit:
        return None
    decoder = DECODERS.get(station_code.upper())
    if decoder is None:
        return None
    return decoder(circuit.strip().upper())


def has_decoder(station_code: str) -> bool:
    """Return whether platform circuits are understood at a station."""
    return station_code.upper() in DECODERS
