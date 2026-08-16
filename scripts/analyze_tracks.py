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
from collections.abc import Callable
from datetime import date, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from typing import NamedTuple

BAR = 0.60

# nypenn.live covers this station and no other, so its predictions are only
# ever offered against these rows -- scoring them against Short Hills would
# credit them with silence about a station they never claimed to cover.
NY_PENN = "New York Penn Station"

# Below this many seconds before departure, an assignment counts as late.
# The measured p10 at New York Penn is 5.7 minutes (n=236), so six is roughly
# the slowest tenth. Mirrors TRACK_OVERDUE_LEAD in event.py and has to move
# with it, or the analysis grades a threshold the integration is not using.
LATE_ASSIGNMENT = 6 * 60

# How close two departures have to be for one's track to rule out the other's.
# Deliberately generous: a wrong exclusion costs more than a missing one.
CONFLICT_WINDOW = timedelta(minutes=10)

# How far ahead a prediction has to be made to be worth making. Penn posts at
# a median of 9.2 minutes, so anything later than this is telling someone what
# the board is about to tell them anyway. It is also the lead nypenn.live is
# scored at, so the two sides answer at the same moment.
PREDICT_LEAD = 30 * 60

# The measured p10 of the gap between two departures reusing one track at New
# York Penn (n=1020). A track that emptied more recently than this can still
# host the next train, but rarely does, so it is a penalty and not a veto.
REUSE_GAP_P10 = 31 * 60

# How fast an old observation stops mattering in m7. A week, so that last
# Thursday still counts for half and a month ago barely registers -- chosen
# because the timetable itself runs on a weekly cycle, not because it scored
# well. Nothing below was tuned after the fact.
RECENCY_HALF_LIFE = 7.0

# What a track used by *this train* is worth against one merely used by the
# same line around the same time. Six to one, following the gap between
# `m1 by train` and `m3 by time slot`.
TRAIN_WEIGHT = 6.0
LINE_WEIGHT = 1.0


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
    worst_delay: int | None
    outcome_from: str | None = None
    """The station this row's outcome was borrowed from, if it was.

    ``None`` means the outcome is the station's own, or that there is none.
    Kept so a report can say how much of its evidence is second-hand rather
    than presenting a recovered outcome as a directly observed one."""

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
        """Return whether this train ended up cancelled or meaningfully late.

        Judged on the worst the train ever looked, not on the last thing the
        board said about it. Rows recorded before that distinction existed
        carry no `worst_delay` at all and fall back to `final_delay`, which for
        those rows is almost always null -- they cannot answer this question
        and should not be counted as having answered it "no".
        """
        if self.final_status == "cancelled":
            return True
        worst = self.worst_delay if self.worst_delay is not None else self.final_delay
        return worst is not None and worst >= 5

    @property
    def outcome_known(self) -> bool:
        """Return whether this row can say how the train turned out at all.

        A terminal publishes no lateness, so at New York Penn this is false for
        almost every row. Reporting `went_wrong` rates without it counts
        "the board never said" as "the train was fine".
        """
        return self.final_status == "cancelled" or self.worst_delay is not None


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
                        # `.get`, unlike the two keys above it: a row with
                        # no track is the normal case for most of a terminal's
                        # board, and an older record may predate the field
                        # entirely. A train id and a time are what make a
                        # record mean anything, so those stay required.
                        track=record.get("track"),
                        scheduled=datetime.combine(day, datetime.min.time()).replace(
                            hour=int(hour), minute=int(minute)
                        ),
                        line=record.get("line", ""),
                        assigned_at=record.get("assigned_at"),
                        reassigned=bool(record.get("first_track")),
                        delay_at_assignment=record.get("delay_at_assignment"),
                        final_status=record.get("final_status"),
                        final_delay=record.get("final_delay"),
                        worst_delay=record.get("worst_delay"),
                    )
                )
    return observations


def join_outcomes(observations: list[Observation]) -> list[Observation]:
    """Fill in unobservable outcomes from the same train seen elsewhere.

    A terminal publishes no lateness at all (SPEC 3.8), so a New York Penn row
    can say when its track was posted but never how the train turned out. The
    same physical train reaches a through station forty minutes later and is
    counted down there, which is the only account of its lateness that exists.
    Both ends of the commute are already recorded, so this costs nothing but a
    lookup.

    Matching is on train and service day. Train IDs are unique per direction,
    so there is no risk of pairing an outbound service with a return one.

    Adjacent days are searched because each station files a departure under
    *its own* scheduled date: a train leaving Penn at 23:50 reaches Short Hills
    after midnight and is filed a day later. Without that, every late-evening
    train -- the ones a commuter most wants to know about -- would silently
    fail to join.

    Only rows with no outcome of their own are filled, and each one records
    where its outcome came from.
    """
    donors: dict[tuple[date, str], Observation] = {}
    for observation in observations:
        if observation.outcome_known:
            donors[(observation.day, observation.train_id)] = observation

    joined: list[Observation] = []
    for observation in observations:
        if observation.outcome_known:
            joined.append(observation)
            continue

        donor = None
        for offset in (0, 1, -1):
            candidate = donors.get(
                (observation.day + timedelta(days=offset), observation.train_id)
            )
            if candidate is not None and candidate.station != observation.station:
                donor = candidate
                break

        if donor is None:
            joined.append(observation)
            continue

        joined.append(
            observation._replace(
                final_status=donor.final_status,
                final_delay=donor.final_delay,
                worst_delay=donor.worst_delay,
                outcome_from=donor.station,
            )
        )
    return joined


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

    # Split by operator, because pooling them describes neither. This line was
    # pooled at first and reported a p10 of 0.0 minutes for New York Penn,
    # which reads as "NJ Transit sometimes posts the track at departure" and
    # was entirely Amtrak: four of its seven assignments landed at or after the
    # scheduled minute, against three of thirty-three for NJ Transit.
    if timed:
        for label, rows in (
            ("lead time", [o for o in observations if not o.is_amtrak]),
            ("  of which Amtrak", [o for o in observations if o.is_amtrak]),
        ):
            values = [o.assigned_at for o in rows if o.assigned_at is not None]
            if not values:
                continue
            print(
                f"  {label:<17}  "
                f"median {statistics.median(values) / 60:.1f} min, "
                f"p10 {_quantile(values, 0.10) / 60:.1f} min, "
                f"p90 {_quantile(values, 0.90) / 60:.1f} min "
                f"(n={len(values)})"
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

    # A status of `boarding` is not an outcome. The first version of this
    # guard accepted any non-null `final_status`, which every row has, so it
    # went on to report "0/27 = 0% went wrong" for a station that had never
    # published a single delay -- an absence dressed up as a finding, which is
    # the exact mistake this whole analysis exists to avoid.
    if not any(o.outcome_known for o in njt):
        print(
            "    no outcomes observable here -- this station publishes no\n"
            "    lateness, so a rate would only be counting silence as good\n"
        )
        return

    def rate(rows: list[Observation], label: str) -> None:
        known = [o for o in rows if o.outcome_known]
        if not known:
            # "(0 unknown)" reads as rows existing whose outcome could not be
            # seen, which is a different and more interesting statement than
            # there being no rows in this bucket at all.
            note = f"        ({len(rows)} unknown)" if rows else ""
            print(f"    {label:<34}   --{note}")
            return
        bad = sum(1 for o in known if o.went_wrong)
        blind = len(rows) - len(known)
        borrowed = sum(1 for o in known if o.outcome_from)
        notes = []
        if blind:
            notes.append(f"{blind} unknown")
        if borrowed:
            # Said out loud because a recovered outcome is a different kind of
            # evidence from a directly observed one, and a reader deciding
            # whether to trust the rate is entitled to know which this is.
            notes.append(f"{borrowed} borrowed")
        suffix = f"  ({', '.join(notes)})" if notes else ""
        print(
            f"    {label:<34} {bad:3}/{len(known):<4} = {bad / len(known):5.0%}{suffix}"
        )

    on_time_at_assignment = [o for o in timed if o.delay_at_assignment in (0, None)]
    # Built from the constant rather than written out. These labels read "8
    # min" for a while after the threshold moved to 6, so the report described
    # a cut it was not making -- and the number in a label is the only thing
    # telling a reader what the two rows above and below actually divide.
    cutoff = LATE_ASSIGNMENT // 60
    print("    all trains:")
    rate(
        [o for o in timed if (o.assigned_at or 0) >= LATE_ASSIGNMENT],
        f"assigned normally (>= {cutoff} min)",
    )
    rate(
        [o for o in timed if (o.assigned_at or 0) < LATE_ASSIGNMENT],
        f"assigned late (< {cutoff} min)",
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


def _known_by(
    same_day: list[Observation], target: Observation, lead: int
) -> list[Observation]:
    """Return the day's departures whose track was already public at `lead`.

    m4 does not do this, and that is a real flaw in it: it excludes tracks
    used by departures either side of the target, including ones whose track
    is posted after the moment we would have had to predict. It was written
    before there was anything to compare against and its number is in the
    pre-registered table, so it is left alone rather than quietly improved --
    but it is optimistic, and m5 is the honest version of the same idea.

    `assigned_at` is how many seconds before its own departure that row's
    track went up. A row that never got one cannot have been read off a board
    at any time, so it is not evidence here.
    """
    cutoff = target.scheduled - timedelta(seconds=lead)
    return [
        o
        for o in same_day
        if o.track
        and o.assigned_at is not None
        and o.scheduled - timedelta(seconds=o.assigned_at) <= cutoff
    ]


def m5_free_track(
    history: list[Observation], same_day: list[Observation], target: Observation
) -> list[str]:
    """What is plausible for this line, minus what the board says is taken.

    The idea the nypenn.live comparison points at. Their confident tier is a
    sighting of the set on the platform, which no public feed gives us, but
    their unconfident tier is a model working from the same public facts we
    have -- and elimination is the strongest of those facts. A terminal has
    sixteen tracks, a train has to be on one of the few its line uses, and any
    track with a train already sitting on it is out.

    Three ingredients, in order of how much they contribute:

    * a prior over tracks this line uses around this time of day;
    * a hard exclusion of tracks occupied by a departure close enough to
      conflict, which is m4's insight but restricted to tracks whose
      assignment was actually public by then;
    * a soft penalty on tracks vacated too recently to be turned around, from
      the measured reuse gap -- the p10 is 31 minutes, so a track that emptied
      five minutes ago is possible and unlikely rather than impossible.
    """
    prior = Counter(
        o.track
        for o in history
        if o.track
        and o.line == target.line
        and abs(_minutes(o.scheduled) - _minutes(target.scheduled)) <= 60
    )
    if not prior:
        prior = Counter(o.track for o in history if o.track)
    if not prior:
        return []

    known = _known_by(same_day, target, PREDICT_LEAD)
    busy = {
        o.track for o in known if abs(o.scheduled - target.scheduled) <= CONFLICT_WINDOW
    }

    scored: list[tuple[float, str]] = []
    for track, count in prior.items():
        if track in busy:
            continue
        gaps = [
            (target.scheduled - o.scheduled).total_seconds()
            for o in known
            if o.track == track and o.scheduled < target.scheduled
        ]
        recent = min(gaps) if gaps else None
        penalty = 1.0 if recent is None else min(1.0, recent / REUSE_GAP_P10)
        scored.append((count * penalty, track))

    scored.sort(key=lambda pair: (-pair[0], _track_order(pair[1])))
    return [track for weight, track in scored if weight > 0]


def m6_last_track_used(
    history: list[Observation], same_day: list[Observation], target: Observation
) -> list[str]:
    """The track this train used the last time it ran.

    m1 asks what a train usually does, which buries a track that has moved:
    six weeks on track 3 and the last four days on track 11 still says 3. This
    asks what it did most recently, and then falls back through the rest in
    order of how recently they were used.

    Only days strictly *before* the target. The leave-one-day-out harness
    hands every model the whole of history minus the day being scored, which
    for a model built on the mode is harmless -- but "the last time it ran"
    reading a day that has not happened yet is the same class of bug as the
    100% that started this file, and it would look just as plausible.
    """
    prior = [
        o
        for o in history
        if o.track and o.train_id == target.train_id and o.day < target.day
    ]
    ranked: list[str] = []
    for observation in sorted(prior, key=lambda o: o.day, reverse=True):
        if observation.track and observation.track not in ranked:
            ranked.append(observation.track)
    return ranked


def m7_combined(
    history: list[Observation], same_day: list[Observation], target: Observation
) -> list[str]:
    """Every signal we have, weighted, in one ranking.

    m1 and m6 are the same evidence read two ways -- all of a train's history
    versus only its latest day -- and the decay here is what puts them on a
    continuum: an observation counts for half as much every `RECENCY_HALF_LIFE`
    days, so a week-old track still speaks and a month-old one barely does.
    m1 is this with an infinite half-life and m6 with one of zero.

    Where a train has no history of its own, the line's habit around this time
    of day carries the ranking instead -- weighted far below the train's own
    record, because `m3 by time slot` scores well below `m1 by train`.

    The availability penalty from m5 is applied last. It contributes almost
    nothing at a thirty-minute lead, for the reason m5 measured: there is
    nothing posted yet to eliminate. It is here because it costs nothing and
    becomes real if this is ever asked closer in.

    Both the half-life and the weights were fixed before this was scored, and
    neither was tuned afterwards. The alternative -- trying values until the
    table improves -- is how ten days of data produces a model that describes
    ten days of data.
    """
    weights: dict[str, float] = defaultdict(float)
    for o in history:
        if not o.track or o.day >= target.day:
            continue
        decay = 0.5 ** ((target.day - o.day).days / RECENCY_HALF_LIFE)
        if o.train_id == target.train_id:
            weights[o.track] += TRAIN_WEIGHT * decay
        elif (
            o.line == target.line
            and abs(_minutes(o.scheduled) - _minutes(target.scheduled)) <= 60
        ):
            weights[o.track] += LINE_WEIGHT * decay

    if not weights:
        return m0_global_mode(history, same_day, target)

    known = _known_by(same_day, target, PREDICT_LEAD)
    busy = {
        o.track for o in known if abs(o.scheduled - target.scheduled) <= CONFLICT_WINDOW
    }
    scored = []
    for track, weight in weights.items():
        if track in busy:
            continue
        gaps = [
            (target.scheduled - o.scheduled).total_seconds()
            for o in known
            if o.track == track and o.scheduled < target.scheduled
        ]
        recent = min(gaps) if gaps else None
        penalty = 1.0 if recent is None else min(1.0, recent / REUSE_GAP_P10)
        scored.append((weight * penalty, track))

    scored.sort(key=lambda pair: (-pair[0], _track_order(pair[1])))
    return [track for weight, track in scored if weight > 0]


# A model is asked three things: every day but the one being scored, the rest
# of that day's board, and the departure in question. It answers with tracks
# best first, or with nothing when it has no opinion.
Model = Callable[[list[Observation], list[Observation], Observation], list[str]]

MODELS: dict[str, Model] = {
    "m0 global mode": m0_global_mode,
    "m1 by train": m1_by_train,
    "m2 by train+weekday": m2_by_train_and_weekday,
    "m3 by time slot": m3_by_time_slot,
    "m4 m1 - conflicts": m4_by_train_minus_conflicts,
    "m5 free track": m5_free_track,
    "m6 last track used": m6_last_track_used,
    "m7 combined": m7_combined,
}


class Score(NamedTuple):
    """How one model did over every held-out day."""

    hits: int
    """Correct top-1 predictions."""

    top3: int
    """Targets whose track appeared anywhere in the first three."""

    answered: int
    """Targets the model was willing to answer at all."""

    total: int
    """Targets it was asked about, answered or not."""


def nypenn_model(
    answers: dict[tuple[date, str], list[str]],
) -> Callable[[list[Observation], list[Observation], Observation], list[str]]:
    """Return a model that answers with what nypenn.live predicted.

    A lookup wearing a model's signature. Their prediction was made from their
    own data at the time, so `history` and `same_day` are ignored -- and
    because it does not vary with what is held out, the leave-one-day-out loop
    scores it identically on every pass. That is the point: it is graded by
    the same harness, on the same departures, against the same bar, rather
    than by a second scoreboard whose rules would have to be trusted.

    A missing key is a departure they said nothing about, which is an empty
    ranking -- the harness already counts that as unanswered rather than
    wrong, and the `answered` column is where staying quiet shows up.
    """

    def model(
        history: list[Observation], same_day: list[Observation], target: Observation
    ) -> list[str]:
        return answers.get((target.day, target.train_id), [])

    return model


def score(
    observations: list[Observation],
    models: dict[str, Model] | None = None,
) -> dict[str, Score] | None:
    """Score every model leave-one-day-out.

    Separated from printing so the split can be tested. It is the part worth
    testing: an early version handed each model the day it was being scored
    on, and `m2 by train+weekday` came back at 100% top-1 -- a perfect number,
    produced entirely by reading the answer, and indistinguishable from
    success on the printed table.

    ``None`` when there is not yet a day to hold out.
    """
    days = sorted({observation.day for observation in observations})
    if len(days) < 2:
        return None

    scores: dict[str, Score] = {}
    for name, model in (models or MODELS).items():
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
        scores[name] = Score(hits=hits, top3=top3, answered=answered, total=total)
    return scores


def evaluate(
    observations: list[Observation], models: dict[str, Model] | None = None
) -> None:
    """Score every model leave-one-day-out and print the table."""
    scores = score(observations, models)
    if scores is None:
        print("\n  not enough days to hold one out yet\n")
        return

    print(f"\n  {'model':<22}{'top-1':>8}{'top-3':>8}{'answered':>10}")
    print(f"  {'-' * 46}")

    for name, result in scores.items():
        cleared = result.total and result.hits / result.total >= BAR
        flag = "  <-- clears bar" if cleared else ""
        print(
            f"  {name:<22}{_pct(result.hits, result.total):>8}"
            f"{_pct(result.top3, result.total):>8}"
            f"{_pct(result.answered, result.total):>10}{flag}"
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
    parser.add_argument(
        "--weekdays-only",
        action="store_true",
        help="drop Saturday/Sunday departures (fewer trains run, weaker signal)",
    )
    parser.add_argument(
        "--nypenn",
        type=Path,
        help="change log from collect_nypenn.py, scored alongside our models",
    )
    parser.add_argument(
        "--nypenn-lead",
        type=int,
        default=15,
        help=(
            "how many minutes before departure to take their prediction "
            "(default: 15, which is before Penn posts anything)"
        ),
    )
    args = parser.parse_args()

    observations = load(args.paths)
    if not observations:
        print("no observations found", file=sys.stderr)
        return 1

    # Before any station filter: the whole point is to borrow across stations,
    # and `--station "New York Penn Station"` would otherwise discard the only
    # rows that can say how those trains turned out.
    direct = sum(1 for o in observations if o.outcome_known)
    observations = join_outcomes(observations)
    recovered = sum(1 for o in observations if o.outcome_from)
    print(
        f"\noutcomes: {direct} observed directly, "
        f"{recovered} recovered from another station"
    )

    if args.station:
        observations = [o for o in observations if o.station == args.station]

    if args.weekdays_only:
        dropped = sum(1 for o in observations if o.weekday >= 5)
        observations = [o for o in observations if o.weekday < 5]
        print(f"\nweekdays-only: dropped {dropped} Saturday/Sunday observations")

    models = MODELS
    if args.nypenn:
        # Imported here rather than at the top so the tool still runs with no
        # nypenn log to hand, which is the normal case.
        import nypenn

        theirs, _ = nypenn.load(args.nypenn)
        answers = nypenn.lookup(theirs, args.nypenn_lead)
        models = {
            **MODELS,
            f"n1 nypenn.live T-{args.nypenn_lead}": nypenn_model(answers),
        }
        overlap = {
            (o.day, o.train_id) for o in observations if o.station == NY_PENN
        } & set(answers)
        print(
            f"\nnypenn.live: {len(answers)} predictions at T-{args.nypenn_lead}, "
            f"{len(overlap)} of them on departures we also recorded"
        )
        if not overlap:
            print(
                "  nothing to compare on yet -- their log and our diagnostics\n"
                "  have to cover the same days at New York Penn"
            )

    stations = sorted({observation.station for observation in observations})
    for station in stations:
        subset = [o for o in observations if o.station == station]
        print(f"\n{station}")
        describe(subset)
        lateness_vs_outcome(subset)
        evaluate(subset, models if station == NY_PENN else MODELS)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
