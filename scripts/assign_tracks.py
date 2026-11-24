#!/usr/bin/env python3
"""Predict a whole board at once, because that is how Penn assigns it.

Every other model here predicts one train in isolation and takes its best
track. That is wrong about the physics in a specific way: two trains leaving
within twenty minutes cannot use the same platform, so the independent
argmaxes can — and do — hand platform 4 to both the 18:28 and the 18:33.
Nothing in the per-train models can notice.

This solves the board instead. For each departure, take the trains scheduled
around it, score every (train, track) pair with the fitted ranker from
`learn_tracks`, and find the highest-scoring assignment in which no two of
them share a platform. That is a maximum-weight bipartite matching, solved
exactly by the Hungarian algorithm below.

**Why this is allowed to see the other trains.** Elimination (m4, m5) failed
because it needed other trains' *tracks*, which are not posted until about
nine minutes out — at a thirty-minute lead there are, measured, 0.00 of them
to eliminate. This needs only that the other trains *exist*, which is the
published timetable. The constraint is free where the evidence was not.

**Pre-registered expectation, written before the first run:** +2 to +4 points
of top-1 over the fitted ranker's 21%, so 23-25%. Most of the gain on top-1
rather than top-3, because the effect is refusing to give one platform to two
trains, and a ranking that was already right does not improve. It will not
reach the 60% bar. Recorded here so that whatever comes out is a result rather
than the ninth model tried until one looked good; issue #35 has been decided
on a pre-registered bar throughout and this is not the place to stop.

Everything below is stdlib. This is an analysis tool, not shipped code.
"""

from __future__ import annotations

import argparse
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import learn_tracks
from analyze_tracks import NY_PENN, Observation, load
from learn_tracks import TRACKS, candidates, fit

# How close two departures have to be for one platform to be unable to serve
# both. Measured rather than chosen: across 1431 departures the track a train
# got had been vacated within 20 minutes in 0.2% of cases, and within 30
# minutes in 6.1%. Twenty is where the constraint is nearly free; wider starts
# forbidding assignments that really happen.
ASSIGN_WINDOW = timedelta(minutes=20)


def _scored(
    history: list[Observation],
    same_day: list[Observation],
    target: Observation,
    weights: list[float],
) -> list[float]:
    """Return the ranker's score for each track, in `TRACKS` order."""
    rows = candidates(history, same_day, target)
    return [sum(f * w for f, w in zip(row, weights, strict=True)) for row in rows]


def assign_day(
    history: list[Observation], board: list[Observation], weights: list[float]
) -> dict[str, list[str]]:
    """Return a ranking per train for one day's board, under exclusion.

    Departures are taken in scheduled order and each claims the best track no
    train ahead of it has claimed within `ASSIGN_WINDOW`. Earlier trains get
    first refusal because that is what happens on the ground: the platform is
    occupied by whoever is standing on it.

    **This is not the bipartite matching it looks like.** A maximum-weight
    matching assigns each track to at most one train, which is true for a
    single instant and false for a day -- platform 4 serves thirty trains
    between six and midnight. The right object is a colouring of an interval
    graph, where two departures conflict only if their occupancies overlap,
    and greedy in left-endpoint order is the textbook exact method for
    *feasibility* there. It is not optimal for total score, and a train that
    would have been happier with a track its predecessor took cannot buy it
    back. Solving that exactly is a min-cost flow; it is not obviously worth
    it while the whole effect is worth a few points at most.

    Solved once for the whole board rather than once per train, so the
    predictions are consistent with each other. Solving per train gives every
    train the assignment in which it gets priority, and two trains can then
    both be promised the same platform -- which is precisely the failure this
    model exists to remove.
    """
    ranked: dict[str, list[str]] = {}
    claimed: list[tuple[Observation, str]] = []

    for target in sorted(board, key=lambda o: o.scheduled):
        context = [o for o in board if o.train_id != target.train_id]
        scores = _scored(history, context, target, weights)
        taken = {
            track
            for other, track in claimed
            if abs(other.scheduled - target.scheduled) <= ASSIGN_WINDOW
        }
        order = sorted(zip(scores, TRACKS, strict=True), key=lambda pair: -pair[0])
        free = [track for _, track in order if track not in taken]
        # Every platform busy is not a thing that happens at sixteen tracks and
        # a twenty-minute window, but a model that returned nothing here would
        # score as unanswered and quietly flatter itself.
        chosen = free[0] if free else order[0][1]
        claimed.append((target, chosen))
        ranked[target.train_id] = [chosen] + [
            track for _, track in order if track != chosen
        ]
    return ranked


def score(observations: list[Observation]) -> learn_tracks.Learned | None:
    """Fit and score leave-one-day-out, exactly as `learn_tracks` does.

    The same protocol, deliberately: a number produced by a different harness
    cannot be compared with the table it is meant to join.
    """
    days = sorted({o.day for o in observations})
    if len(days) < 3:
        return None

    hits = top3 = total = 0
    learned = []
    for held_out in days:
        examples = []
        for day in (d for d in days if d != held_out):
            history = [o for o in observations if o.day < day and o.day != held_out]
            board = [o for o in observations if o.day == day]
            for target in board:
                if not target.track or target.track not in TRACKS:
                    continue
                context = [o for o in board if o.train_id != target.train_id]
                examples.append(
                    (candidates(history, context, target), TRACKS.index(target.track))
                )
        if not examples:
            continue

        weights = fit(examples)
        learned.append(weights)

        history = [o for o in observations if o.day != held_out]
        board = [o for o in observations if o.day == held_out]
        predicted = assign_day(history, board, weights)
        for target in board:
            if not target.track or target.track not in TRACKS:
                continue
            ranked = predicted[target.train_id]
            total += 1
            hits += ranked[0] == target.track
            top3 += target.track in ranked[:3]

    return learn_tracks.Learned(
        hits=hits, top3=top3, total=total, weights=learned, by_day={}
    )


def main() -> int:
    """Fit and score the assignment model."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", type=Path, help="diagnostics JSON files")
    parser.add_argument("--station", default=NY_PENN, help="station to model")
    args = parser.parse_args()

    observations = [o for o in load(args.paths) if o.station == args.station]
    if not observations:
        print(f"no observations for {args.station}", file=sys.stderr)
        return 1

    print(f"\n{args.station}: {len(observations)} observations")
    result = score(observations)
    if result is None or not result.total:
        print("not enough days to hold one out")
        return 1

    print(f"\n  {'model':<24}{'top-1':>8}{'top-3':>8}")
    print(f"  {'-' * 40}")
    print(
        f"  {'assignment':<24}{result.hits / result.total:>8.1%}"
        f"{result.top3 / result.total:>8.1%}"
    )
    print(f"\n  {result.total} departures scored\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
