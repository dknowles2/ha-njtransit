#!/usr/bin/env python3
"""Learn the weights instead of choosing them, and see if it helps.

Every model in ``analyze_tracks.py`` is a rule someone wrote down: count by
train, prefer the recent, penalise a track that just emptied. m7 goes further
and combines them, but the combination weights (six to one, a week's
half-life) were picked by hand. The obvious question, and the one nypenn.live
is reported to answer with machine learning, is whether fitting those weights
to the data beats guessing them.

**The framing matters more than the algorithm.** Treating this as "pick one of
sixteen labels" makes every track a separate class the model has to learn per
train, which with a few hundred rows per fold is hopeless. Instead each
departure becomes sixteen candidate rows -- one per track -- described by how
well *that* track fits *this* train, and the model scores candidates and
softmaxes over them. This is a conditional logit, the standard shape for
"choose one of a varying set of options", and it has one weight per feature
rather than one per (track, train) pair. Eight weights against 1431
observations is a regime where a fitted answer means something.

Scored by the same leave-one-day-out protocol as everything else, for the same
reason: a number produced by a different harness cannot be compared with the
table it is meant to join.

Usage:
    python scripts/learn_tracks.py njtransit-*.json

Requires numpy, which Home Assistant already depends on, so it is present in
the dev environment. This is an analysis tool, not shipped code.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import NamedTuple

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from analyze_tracks import (
    NY_PENN,
    PREDICT_LEAD,
    REUSE_GAP_P10,
    Observation,
    _known_by,
    _minutes,
    load,
)

# Every platform seen at New York Penn. Fixed rather than derived so a fold
# whose training days happen to miss a rare track still scores it as a
# candidate rather than silently dropping it from the choice set.
TRACKS = [str(n) for n in range(1, 17)]

FEATURES = (
    "train history",
    "train history, recent",
    "last track used",
    "line at this hour",
    "station overall",
    "minutes since vacated",
    "never used by this train",
)

# Enough passes for the loss to stop moving on a problem this small; there is
# no early stopping because there is no validation split to stop against --
# the held-out day is the test set and looking at it would be the leak this
# whole file is arranged to avoid.
EPOCHS = 300
STEP = 0.5
L2 = 1e-3

RECENT_DAYS = 3.0


def candidates(
    history: list[Observation], same_day: list[Observation], target: Observation
) -> np.ndarray:
    """Return one feature row per track, in `TRACKS` order.

    Deliberately the same evidence the hand-written models read, so that a
    difference in score is a difference in how it is *combined* rather than in
    what was available. Anything here that they cannot see would make the
    comparison meaningless.
    """
    prior = [o for o in history if o.track and o.day < target.day]
    mine = [o for o in prior if o.train_id == target.train_id]
    recent = [o for o in mine if (target.day - o.day).days <= RECENT_DAYS]
    slot = [
        o
        for o in prior
        if o.line == target.line
        and abs(_minutes(o.scheduled) - _minutes(target.scheduled)) <= 60
    ]
    last = max(mine, key=lambda o: o.day).track if mine else None

    known = _known_by(same_day, target, PREDICT_LEAD)
    vacated: dict[str, float] = {}
    for o in known:
        if o.track and o.scheduled < target.scheduled:
            gap = (target.scheduled - o.scheduled).total_seconds()
            vacated[o.track] = min(gap, vacated.get(o.track, gap))

    rows = []
    for track in TRACKS:
        # Shares rather than counts, so a train with forty observations and one
        # with four are described on the same scale.
        rows.append(
            [
                sum(o.track == track for o in mine) / len(mine) if mine else 0.0,
                sum(o.track == track for o in recent) / len(recent) if recent else 0.0,
                1.0 if track == last else 0.0,
                sum(o.track == track for o in slot) / len(slot) if slot else 0.0,
                sum(o.track == track for o in prior) / len(prior) if prior else 0.0,
                # Clipped at the measured p10 of platform reuse: past that the
                # gap stops carrying information, and leaving it unbounded lets
                # a track empty since breakfast dominate the feature.
                min(vacated.get(track, REUSE_GAP_P10), REUSE_GAP_P10) / REUSE_GAP_P10,
                0.0 if any(o.track == track for o in mine) else 1.0,
            ]
        )
    return np.asarray(rows, dtype=float)


def fit(examples: list[tuple[np.ndarray, int]]) -> np.ndarray:
    """Fit the ranker by gradient descent on the softmax likelihood."""
    width = examples[0][0].shape[1]
    weights = np.zeros(width)
    for _ in range(EPOCHS):
        gradient = np.zeros(width)
        for features, answer in examples:
            scores = features @ weights
            scores -= scores.max()
            probability = np.exp(scores)
            probability /= probability.sum()
            # The gradient of the log-likelihood: push up the chosen row,
            # push down every row in proportion to how much it was believed.
            gradient += features[answer] - probability @ features
        weights += STEP * (gradient / len(examples) - L2 * weights)
    return weights


def rank(weights: np.ndarray, features: np.ndarray) -> list[str]:
    """Return the tracks best first."""
    order = np.argsort(-(features @ weights))
    return [TRACKS[i] for i in order]


class Learned(NamedTuple):
    """How the fitted ranker did, and what it decided mattered."""

    hits: int
    top3: int
    total: int
    weights: list[np.ndarray]
    """One set of weights per fold, kept so their spread can be reported.

    A weight that swings between folds is the model disagreeing with itself,
    and averaging it away would present that as a finding."""


def score(observations: list[Observation]) -> Learned | None:
    """Fit and score leave-one-day-out.

    Separated from printing for the same reason as `analyze_tracks.score`:
    the split is the part that can silently be wrong, and a model that trains
    on the day it is scored against reports a number nothing about the output
    would flag.

    ``None`` when there are not enough days to train on some and test on one.
    """
    days = sorted({o.day for o in observations})
    if len(days) < 3:
        return None

    hits = top3 = total = 0
    learned = []
    for held_out in days:
        train_days = [d for d in days if d != held_out]
        examples = []
        for day in train_days:
            # Each training example is built from the days before it, with the
            # held-out day withheld throughout -- otherwise the model learns
            # from features that were themselves computed from the test day.
            history = [o for o in observations if o.day < day and o.day != held_out]
            board = [o for o in observations if o.day == day]
            for target in board:
                if not target.track or target.track not in TRACKS:
                    continue
                context = [o for o in board if o.train_id != target.train_id]
                examples.append(
                    (
                        candidates(history, context, target),
                        TRACKS.index(target.track),
                    )
                )
        if not examples:
            continue

        weights = fit(examples)
        learned.append(weights)

        history = [o for o in observations if o.day != held_out]
        board = [o for o in observations if o.day == held_out]
        for target in board:
            if not target.track or target.track not in TRACKS:
                continue
            context = [o for o in board if o.train_id != target.train_id]
            ranked = rank(weights, candidates(history, context, target))
            total += 1
            hits += ranked[0] == target.track
            top3 += target.track in ranked[:3]

    return Learned(hits=hits, top3=top3, total=total, weights=learned)


def evaluate(observations: list[Observation]) -> None:
    """Score the ranker and print what it learned."""
    result = score(observations)
    if result is None:
        print("need at least three days to train on two and test on one")
        return
    hits, top3, total, learned = result
    if not total:
        print("nothing to score")
        return

    print(f"\n  {'model':<22}{'top-1':>8}{'top-3':>8}{'answered':>10}")
    print(f"  {'-' * 48}")
    print(
        f"  {'ml conditional logit':<22}{hits / total:>8.0%}{top3 / total:>8.0%}{1:>10.0%}"
    )

    print("\n  what it learned (mean weight across folds, larger is stronger)")
    mean = np.mean(learned, axis=0)
    spread = np.std(learned, axis=0)
    for name, value, deviation in sorted(
        zip(FEATURES, mean, spread, strict=True), key=lambda t: -abs(t[1])
    ):
        print(f"    {name:<26}{value:>8.2f}  +/- {deviation:.2f}")
    print(
        "\n  A weight whose spread across folds is as large as the weight "
        "itself\n  is not a finding; it is the model disagreeing with itself "
        "about a\n  feature that carries too little signal to pin down.\n"
    )


def main() -> int:
    """Fit and score the ranker."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", type=Path, help="diagnostics JSON files")
    parser.add_argument("--station", default=NY_PENN, help="station to model")
    args = parser.parse_args()

    observations = [o for o in load(args.paths) if o.station == args.station]
    if not observations:
        print(f"no observations for {args.station}", file=sys.stderr)
        return 1

    print(f"\n{args.station}: {len(observations)} observations")
    evaluate(observations)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
