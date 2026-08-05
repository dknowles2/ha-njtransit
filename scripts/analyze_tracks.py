#!/usr/bin/env python3
"""Score track-prediction models against recorded history.

The integration records track assignments but deliberately does not predict
from them (see ``custom_components/njtransit/track_history.py``). This is where
the decision gets made, on data rather than on hope: it scores candidate models
leave-one-day-out against a pre-registered bar of **60% top-1 accuracy**, which
is roughly where a prediction starts changing what a passenger does instead of
decorating a screen they were going to keep watching anyway.

Input is one or more diagnostics downloads, one per commute:

    Settings -> Devices & Services -> NJ Transit -> Download diagnostics

Usage:
    python scripts/analyze_tracks.py njtransit-*.json
    python scripts/analyze_tracks.py --station "New York Penn Station" dump.json

Everything below is stdlib. This is an analysis tool, not shipped code.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from typing import NamedTuple

BAR = 0.60

# Below this many seconds before departure, an assignment counts as late.
# NJ Transit's first quartile at New York Penn is 8.9 minutes, so this is
# roughly the slowest tenth. Mirrors TRACK_OVERDUE_LEAD in event.py.
LATE_ASSIGNMENT = 8 * 60

# How close two departures have to be for one's track to rule out the other's.
# Deliberately generous: a wrong exclusion costs more than a missing one.
CONFLICT_WINDOW = timedelta(minutes=10)


class Observation(NamedTuple):
    """One recorded departure, and what became of it."""

    station: str
    day: date
    train_id: str
    track: str | None
    scheduled: datetime
    line: str
    assigned_at: int | None
    reassigned: bool
    delay_at_assignment: int | None
    final_status: str | None
    final_delay: int | None

    @property
    def weekday(self) -> int:
        """Return the day of week, Monday being 0."""
        return self.day.weekday()

    @property
    def is_amtrak(self) -> bool:
        """Return whether this is an Amtrak service.

        They must be split out of anything about *when* a track is posted.
        Amtrak announces at a median of 13 minutes but leaves 16% until the
        departure minute itself, against 1% for NJ Transit -- two different
        operating practices, and pooling them buries both.
        """
        return self.line == "Amtrak"

    @property
    def went_wrong(self) -> bool:
        """Return whether this train ended up cancelled or meaningfully late."""
        if self.final_status == "cancelled":
            return True
        return self.final_delay is not None and self.final_delay >= 5


def load(paths: list[Path]) -> list[Observation]:
    """Read observations out of diagnostics downloads."""
    observations: list[Observation] = []
    for path in paths:
        with path.open(encoding="utf-8") as file:
            payload = json.load(file)

        # A download wraps the integration's own dict in metadata; a hand-made
        # export may not.
        data = payload.get("data", payload)
        history = data.get("track_history")
        if not history:
            print(f"{path.name}: no track history in this dump", file=sys.stderr)
            continue

        station = history["station"]
        for iso, records in history.get("days", {}).items():
            day = date.fromisoformat(iso)
            for record in records:
                hour, _, minute = record["scheduled"].partition(":")
                observations.append(
                    Observation(
                        station=station,
                        day=day,
                        train_id=record["train_id"],
                        track=record["track"],
                        scheduled=datetime.combine(day, datetime.min.time()).replace(
                            hour=int(hour), minute=int(minute)
                        ),
                        line=record.get("line", ""),
                        assigned_at=record.get("assigned_at"),
                        reassigned=bool(record.get("first_track")),
                        delay_at_assignment=record.get("delay_at_assignment"),
                        final_status=record.get("final_status"),
                        final_delay=record.get("final_delay"),
                    )
                )
    return observations


def describe(observations: list[Observation]) -> None:
    """Print what was collected, before any model is asked about it."""
    days = sorted({observation.day for observation in observations})
    tracks = Counter(o.track for o in observations if o.track)
    timed = [
        observation.assigned_at
        for observation in observations
        if observation.assigned_at is not None
    ]
    reassigned = sum(1 for observation in observations if observation.reassigned)

    print(f"  observations       {len(observations)}")
    print(f"  days               {len(days)}  ({days[0]} .. {days[-1]})")
    print(f"  distinct trains    {len({o.train_id for o in observations})}")
    print(f"  tracks in use      {len(tracks)}  {sorted(tracks, key=_track_order)}")
    print(f"  reassigned         {reassigned}  ({_pct(reassigned, len(observations))})")

    if timed:
        print(
            "  lead time          "
            f"median {statistics.median(timed) / 60:.1f} min, "
            f"p10 {_quantile(timed, 0.10) / 60:.1f} min, "
            f"p90 {_quantile(timed, 0.90) / 60:.1f} min "
            f"(n={len(timed)})"
        )
    else:
        print("  lead time          not measured yet")

    # How long a track sits between consecutive departures. This is the whole
    # basis for treating an occupied track as unavailable, and if the
    # distribution reaches down to a couple of minutes then it is not a basis
    # at all.
    gaps = _reuse_gaps(observations)
    if gaps:
        print(
            "  track reuse gap    "
            f"min {min(gaps):.0f} min, p10 {_quantile(gaps, 0.10):.0f} min, "
            f"median {statistics.median(gaps):.0f} min (n={len(gaps)})"
        )


def lateness_vs_outcome(observations: list[Observation]) -> None:
    """Test whether a late track assignment predicts a bad commute.

    The hypothesis, from a decade of riding it: when the track is not up by
    about ten minutes out, the commute is going to be rough.

    The confound is that `assigned_at` is measured against the *scheduled*
    time, so a train already running late gets its track posted late by
    definition. If every late assignment sits on an already-delayed train, this
    is a restatement of the board rather than a warning from it -- so the
    interesting population is trains still reported **on time** when their
    track finally appeared, and those are reported separately below.
    """
    njt = [o for o in observations if not o.is_amtrak]
    timed = [o for o in njt if o.assigned_at is not None]
    never = [o for o in njt if o.track is None]

    print("\n  late track assignment vs outcome (NJ Transit only)")
    if not timed and not never:
        print("    nothing timed yet\n")
        return

    # Outcome fields were added after collection began, so early rows carry
    # neither. Reporting those as "0% went wrong" would read as a finding when
    # it is an absence -- the exact mistake this whole analysis exists to
    # avoid making about track prediction.
    if not any(o.final_status is not None or o.final_delay is not None for o in njt):
        print("    outcomes not recorded yet -- no rows carry a final status\n")
        return

    def rate(rows: list[Observation], label: str) -> None:
        if not rows:
            print(f"    {label:<34} --")
            return
        bad = sum(1 for o in rows if o.went_wrong)
        print(f"    {label:<34} {bad:3}/{len(rows):<4} = {bad / len(rows):5.0%}")

    on_time_at_assignment = [o for o in timed if o.delay_at_assignment in (0, None)]
    print("    all trains:")
    rate(
        [o for o in timed if (o.assigned_at or 0) >= LATE_ASSIGNMENT],
        "assigned normally (>= 8 min)",
    )
    rate(
        [o for o in timed if (o.assigned_at or 0) < LATE_ASSIGNMENT],
        "assigned late (< 8 min)",
    )
    rate(never, "never assigned a track")

    # The version that actually tests the hypothesis rather than restating the
    # board: the train looked fine at the moment its track was posted late.
    print("    reported on time when the track appeared:")
    rate(
        [o for o in on_time_at_assignment if (o.assigned_at or 0) >= LATE_ASSIGNMENT],
        "assigned normally",
    )
    rate(
        [o for o in on_time_at_assignment if (o.assigned_at or 0) < LATE_ASSIGNMENT],
        "assigned late",
    )
    print()


def _reuse_gaps(observations: list[Observation]) -> list[float]:
    """Return minutes between consecutive departures from the same track."""
    by_track: dict[tuple[date, str], list[datetime]] = defaultdict(list)
    for observation in observations:
        if observation.track is None:
            continue
        by_track[observation.day, observation.track].append(observation.scheduled)

    gaps: list[float] = []
    for times in by_track.values():
        times.sort()
        gaps.extend(
            (later - earlier).total_seconds() / 60 for earlier, later in pairwise(times)
        )
    return gaps


# --- models -----------------------------------------------------------------
#
# Each takes `history` -- observations from every day *except* the target's --
# and `same_day`, the rest of the target's own day with the target removed.
#
# The split is the whole point. Anything a model learns from the target's own
# record is leakage, and it is not a hypothetical: an earlier version of this
# script passed the held-out day to every model and m2 scored a perfect 100%
# by reading the answer off the target itself. Only same-day *context* is
# legitimate, and only because at prediction time the board really has already
# published tracks for the trains leaving in the next few minutes.
#
# Returning an empty list counts as unanswered, and `answered` is reported
# alongside accuracy: a model that only speaks up on the easy cases is not
# competing on the same terms as one that always answers.


def m0_global_mode(
    history: list[Observation], same_day: list[Observation], target: Observation
) -> list[str]:
    """The commonest track at the station. The baseline to beat."""
    counts = Counter(o.track for o in history if o.track)
    return [track for track, _ in counts.most_common()]


def m1_by_train(
    history: list[Observation], same_day: list[Observation], target: Observation
) -> list[str]:
    """What this train number usually does."""
    counts = Counter(
        o.track for o in history if o.track and o.train_id == target.train_id
    )
    return [track for track, _ in counts.most_common()]


def m2_by_train_and_weekday(
    history: list[Observation], same_day: list[Observation], target: Observation
) -> list[str]:
    """As m1, but only the same day of the week."""
    counts = Counter(
        o.track
        for o in history
        if o.track and o.train_id == target.train_id and o.weekday == target.weekday
    )
    return [track for track, _ in counts.most_common()]


def m3_by_time_slot(
    history: list[Observation], same_day: list[Observation], target: Observation
) -> list[str]:
    """What leaves around this time, whatever it is numbered.

    Train numbers survive timetable changes less well than departure times do,
    and a renumbered service is invisible to m1.
    """
    counts = Counter(
        o.track
        for o in history
        if o.track and abs(_minutes(o.scheduled) - _minutes(target.scheduled)) <= 5
    )
    return [track for track, _ in counts.most_common()]


def m4_by_train_minus_conflicts(
    history: list[Observation], same_day: list[Observation], target: Observation
) -> list[str]:
    """m1, with tracks that are busy at that moment removed."""
    busy = {
        o.track
        for o in same_day
        if o.track and abs(o.scheduled - target.scheduled) <= CONFLICT_WINDOW
    }
    ranked = m1_by_train(history, same_day, target) or m0_global_mode(
        history, same_day, target
    )
    return [track for track in ranked if track not in busy] or ranked


MODELS = {
    "m0 global mode": m0_global_mode,
    "m1 by train": m1_by_train,
    "m2 by train+weekday": m2_by_train_and_weekday,
    "m3 by time slot": m3_by_time_slot,
    "m4 m1 - conflicts": m4_by_train_minus_conflicts,
}


def evaluate(observations: list[Observation]) -> None:
    """Score every model leave-one-day-out and print the table."""
    days = sorted({observation.day for observation in observations})
    if len(days) < 2:
        print("\n  not enough days to hold one out yet\n")
        return

    print(f"\n  {'model':<22}{'top-1':>8}{'top-3':>8}{'answered':>10}")
    print(f"  {'-' * 46}")

    for name, model in MODELS.items():
        hits = top3 = answered = total = 0
        for held_out in days:
            history = [o for o in observations if o.day != held_out]
            day = [o for o in observations if o.day == held_out]
            for target in day:
                total += 1
                # The target is removed from its own day's context, or every
                # model can find the answer sitting next to the question.
                context = [o for o in day if o.train_id != target.train_id]
                ranked = model(history, context, target)
                if not ranked:
                    continue
                answered += 1
                if ranked[0] == target.track:
                    hits += 1
                if target.track in ranked[:3]:
                    top3 += 1

        flag = "  <-- clears bar" if total and hits / total >= BAR else ""
        print(
            f"  {name:<22}{_pct(hits, total):>8}{_pct(top3, total):>8}"
            f"{_pct(answered, total):>10}{flag}"
        )

    print(
        f"\n  bar is {BAR:.0%} top-1. Below it, a prediction does not change "
        "what you do\n  on the platform, and the honest ship is exclusion "
        "hints or nothing.\n"
    )


def _minutes(when: datetime) -> int:
    """Return minutes past midnight."""
    return when.hour * 60 + when.minute


def _track_order(track: str) -> tuple[int, str]:
    """Sort numeric tracks numerically and lettered ones after."""
    return (int(track), "") if track.isdigit() else (10**6, track)


def _quantile(values: list[float] | list[int], fraction: float) -> float:
    """Return a quantile without requiring numpy."""
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(fraction * len(ordered)))
    return float(ordered[index])


def _pct(part: int, whole: int) -> str:
    """Return a percentage, or a dash when there is nothing to divide."""
    return f"{part / whole:.0%}" if whole else "-"


def main() -> int:
    """Run the analysis."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", type=Path, help="diagnostics JSON files")
    parser.add_argument("--station", help="only this station")
    args = parser.parse_args()

    observations = load(args.paths)
    if args.station:
        observations = [o for o in observations if o.station == args.station]

    if not observations:
        print("no observations found", file=sys.stderr)
        return 1

    stations = sorted({observation.station for observation in observations})
    for station in stations:
        subset = [o for o in observations if o.station == station]
        print(f"\n{station}")
        describe(subset)
        lateness_vs_outcome(subset)
        evaluate(subset)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
