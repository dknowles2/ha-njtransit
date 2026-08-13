#!/usr/bin/env python3
"""Score nypenn.live's track predictions, and line them up against ours.

`scripts/collect_nypenn.py` records their feed as a change log. This replays
it into one row per departure -- what they predicted, when they first said it,
and what the official board posted in the end -- and grades the result.

Two questions, and they are not the same question:

**How good is nypenn.live?** Answerable from their feed alone, because it
carries both halves: a prediction while `track_source` is `high`, `medium` or
`low`, and the answer once it turns `confirmed`, which is the official board
(DepartureVision) rather than anything they worked out.

**Are they better than us?** Answerable only against our own recorded history,
which is what `analyze_tracks.py` is for. That tool already scores models
leave-one-day-out on a pre-registered 60% bar, so rather than build a second
scoreboard with its own subtly different rules, this exposes their prediction
as one more model for that harness. Their model does not learn from the
held-out day -- it does not learn from us at all -- so holding a day out
changes nothing for it, which is exactly what makes the comparison fair: both
are asked the same question about the same departures and graded identically.

The lead time matters as much as the accuracy. Penn posts nothing until about
T-10, so a prediction is only worth something to the extent it arrives before
that; a model that is right 90% of the time but only speaks at T-9 has told
you what you were about to be told anyway. `report` breaks the numbers out by
how far ahead the prediction stood.

Usage:
    python scripts/nypenn.py nypenn.jsonl

Everything below is stdlib. This is a research tool, not shipped code.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import NamedTuple
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/New_York")

# `confirmed` means the official board has posted the track. Everything else
# is their model talking.
TRUTH = "confirmed"
TIERS = ("high", "medium", "low")

# How far before departure to ask "what were they saying at this point". The
# last one is inside the window where Penn has usually posted anyway, and is
# here to show the accuracy climbing as the answer becomes public.
LEADS = (30, 20, 15, 10, 5)

# A poll has to have happened within this long of the instant being asked
# about, or that departure is counted as unobserved at that lead rather than
# as silence. Without this a collector that was stopped overnight reads as a
# site that predicts nothing.
OBSERVED_WITHIN = timedelta(minutes=3)


class Snapshot(NamedTuple):
    """One thing the feed said about one departure, and when."""

    at: datetime
    track: str | None
    source: str | None
    top3: tuple[tuple[str, int], ...]

    @property
    def ranked(self) -> list[str]:
        """Return their candidates, best first.

        `top3` only appears on the less confident tiers. When it is absent the
        single track they name is the whole of their answer, and a one-element
        ranking is the honest representation of that -- padding it would
        invent a second and third choice they never made.
        """
        if self.top3:
            return [track for track, _ in self.top3]
        return [self.track] if self.track else []


class Departure(NamedTuple):
    """One train, everything the feed ever said about it, and the outcome."""

    train_id: str
    scheduled: datetime
    line: str
    destination: str
    history: tuple[Snapshot, ...]

    @property
    def day(self) -> date:
        """Return the service day, which is the departure's own local date."""
        return self.scheduled.date()

    @property
    def truth(self) -> str | None:
        """Return the track the board ended up posting.

        The last confirmed value rather than the first: a track that is posted
        and then changed is a real event at Penn, and the one that matters to
        someone standing there is the one they left from. `reassigned` keeps
        the distinction visible instead of burying it.
        """
        confirmed = [s.track for s in self.history if s.source == TRUTH and s.track]
        return confirmed[-1] if confirmed else None

    @property
    def reassigned(self) -> bool:
        """Return whether the posted track changed after it was first posted."""
        confirmed = [s.track for s in self.history if s.source == TRUTH and s.track]
        return len(set(confirmed)) > 1

    @property
    def posted_at(self) -> datetime | None:
        """Return when the official board first posted a track."""
        for snapshot in self.history:
            if snapshot.source == TRUTH and snapshot.track:
                return snapshot.at
        return None

    @property
    def first_prediction(self) -> Snapshot | None:
        """Return their earliest actual prediction, if they ever made one."""
        for snapshot in self.history:
            if snapshot.source in TIERS and snapshot.track:
                return snapshot
        return None

    def at(self, when: datetime) -> Snapshot | None:
        """Return what they were saying at `when`, prediction or not.

        A step function: the feed reports a state, and that state stands until
        the feed reports a different one.
        """
        standing = None
        for snapshot in self.history:
            if snapshot.at > when:
                break
            standing = snapshot
        return standing

    def prediction_at(self, when: datetime) -> Snapshot | None:
        """Return their prediction standing at `when`, if it was still a guess.

        Once the board has posted, their row carries the posted track and
        scoring it would be scoring DepartureVision against itself. So a
        confirmed state at this instant is not a prediction and returns None,
        even though something was certainly on screen.
        """
        standing = self.at(when)
        if standing is None or standing.source not in TIERS or not standing.track:
            return None
        return standing


def load(*paths: Path) -> tuple[list[Departure], list[datetime]]:
    """Replay change logs into departures, and return the poll times too.

    The polls are not decoration. They are what separates "they said nothing"
    from "nobody was listening", and every accuracy number below is restricted
    to instants that were actually observed.

    More than one log because collection has moved hosts once already and will
    again. Merging them here rather than concatenating the files keeps the
    collected data immutable -- an append-only log that something rewrote is
    no longer evidence of anything. Overlapping logs are safe: a repeated
    state is the same step in the same place.
    """
    rows: dict[tuple[str, int], list[Snapshot]] = defaultdict(list)
    meta: dict[tuple[str, int], tuple[str, str]] = {}
    polls: list[datetime] = []

    for path in paths:
        with path.open(encoding="utf-8") as file:
            for line in file:
                line = line.strip()
                if not line:
                    continue
                record = json.loads(line)
                kind = record.get("type")
                if kind == "poll":
                    polls.append(datetime.fromtimestamp(record["t"], TZ))
                    continue
                if kind != "change":
                    continue

                identity = (str(record["train_id"]), int(record["departure_time"]))
                top3 = tuple(
                    (str(entry["track"]), int(entry["pct"]))
                    for entry in record.get("top3") or []
                )
                rows[identity].append(
                    Snapshot(
                        at=datetime.fromtimestamp(record["t"], TZ),
                        track=record.get("track"),
                        source=record.get("track_source"),
                        top3=top3,
                    )
                )
                meta[identity] = (
                    record.get("line") or "",
                    record.get("destination") or "",
                )

    departures = []
    for (train_id, departure_time), history in rows.items():
        line, destination = meta[(train_id, departure_time)]
        departures.append(
            Departure(
                train_id=train_id,
                scheduled=datetime.fromtimestamp(departure_time, TZ),
                line=line,
                destination=destination,
                history=tuple(sorted(history)),
            )
        )
    return sorted(departures, key=lambda d: d.scheduled), sorted(polls)


def observed(polls: list[datetime], when: datetime) -> bool:
    """Return whether the collector was awake around `when`."""
    return any(abs(poll - when) <= OBSERVED_WITHIN for poll in polls)


class Accuracy(NamedTuple):
    """How a set of predictions did."""

    hits: int
    top3: int
    answered: int
    """Departures where a prediction was standing."""
    asked: int
    """Departures that were observed at all, prediction or not."""


def accuracy_at(
    departures: list[Departure], polls: list[datetime], lead: int
) -> Accuracy:
    """Score what they were saying `lead` minutes before each departure."""
    hits = top3 = answered = asked = 0
    for departure in departures:
        if departure.truth is None:
            continue
        when = departure.scheduled - timedelta(minutes=lead)
        if not observed(polls, when):
            continue
        asked += 1
        standing = departure.prediction_at(when)
        if standing is None:
            continue
        answered += 1
        ranked = standing.ranked
        if ranked and ranked[0] == departure.truth:
            hits += 1
        if departure.truth in ranked[:3]:
            top3 += 1
    return Accuracy(hits=hits, top3=top3, answered=answered, asked=asked)


def accuracy_by_tier(departures: list[Departure]) -> dict[str, Accuracy]:
    """Score their first prediction for each train, split by how sure they were.

    Their own page colours these differently and hides `low` by default, so a
    single pooled number would describe something no user of that site sees.
    """
    scores: dict[str, list[Departure]] = {tier: [] for tier in TIERS}
    for departure in departures:
        first = departure.first_prediction
        if departure.truth is None or first is None or first.source not in TIERS:
            continue
        scores[first.source].append(departure)

    results = {}
    for tier, rows in scores.items():
        hits = sum(
            1
            for row in rows
            if (first := row.first_prediction)
            and first.ranked
            and first.ranked[0] == row.truth
        )
        top3 = sum(
            1
            for row in rows
            if (first := row.first_prediction) and row.truth in first.ranked[:3]
        )
        results[tier] = Accuracy(
            hits=hits, top3=top3, answered=len(rows), asked=len(rows)
        )
    return results


def head_start(departures: list[Departure]) -> list[float]:
    """Return how many minutes each prediction preceded the official posting.

    The entire case for a prediction at a terminal. Penn publishes nothing
    until roughly T-10; a prediction that lands at T-9 is not worth the code
    that produced it, however often it is right.
    """
    gaps = []
    for departure in departures:
        first = departure.first_prediction
        posted = departure.posted_at
        if first is None or posted is None:
            continue
        gaps.append((posted - first.at).total_seconds() / 60)
    return gaps


def lookup(departures: list[Departure], lead: int) -> dict[tuple[date, str], list[str]]:
    """Return their ranking per (day, train), for `analyze_tracks` to score.

    Keyed the way our own observations are, so their answer can be dropped
    into the same leave-one-day-out harness as every model we wrote and graded
    against the same bar, rather than compared across two scoreboards whose
    rules differ in ways nobody has checked.
    """
    answers: dict[tuple[date, str], list[str]] = {}
    for departure in departures:
        standing = departure.prediction_at(
            departure.scheduled - timedelta(minutes=lead)
        )
        if standing is None:
            continue
        answers[(departure.day, departure.train_id)] = standing.ranked
    return answers


def _pct(part: int, whole: int) -> str:
    """Return a percentage, or a dash when there is nothing to divide."""
    return f"{part / whole:.0%}" if whole else "-"


def report(departures: list[Departure], polls: list[datetime]) -> None:
    """Print everything the change log can say about nypenn.live."""
    with_truth = [d for d in departures if d.truth is not None]
    days = sorted({d.day for d in departures})

    print(f"\n  {len(departures)} departures seen over {len(days)} day(s)")
    if days:
        print(f"  {days[0]} to {days[-1]}, {len(polls)} polls")
    print(f"  {len(with_truth)} reached a confirmed track")
    if with_truth:
        reassigned = sum(1 for d in with_truth if d.reassigned)
        print(f"  {reassigned} of those were moved after posting")

    predicted = [d for d in with_truth if d.first_prediction]
    print(f"  {len(predicted)} were predicted before the board posted\n")

    gaps = head_start(predicted)
    if gaps:
        gaps.sort()
        median = gaps[len(gaps) // 2]
        print(f"  head start over the official board: median {median:.0f} min")
        print(f"  ({gaps[0]:.0f} min at worst, {gaps[-1]:.0f} min at best)\n")

    print(f"  {'their confidence':<20}{'top-1':>8}{'top-3':>8}{'n':>8}")
    print(f"  {'-' * 44}")
    for tier, result in accuracy_by_tier(departures).items():
        print(
            f"  {tier:<20}{_pct(result.hits, result.answered):>8}"
            f"{_pct(result.top3, result.answered):>8}{result.answered:>8}"
        )

    print(f"\n  {'minutes before':<20}{'top-1':>8}{'top-3':>8}{'spoke':>8}{'n':>8}")
    print(f"  {'-' * 52}")
    for lead in LEADS:
        result = accuracy_at(departures, polls, lead)
        print(
            f"  T-{lead:<18}{_pct(result.hits, result.answered):>8}"
            f"{_pct(result.top3, result.answered):>8}"
            f"{_pct(result.answered, result.asked):>8}{result.asked:>8}"
        )

    print(
        "\n  top-1 and top-3 are over the departures they answered; `spoke` is\n"
        "  how many of the observed departures they were willing to answer at\n"
        "  all. A model can buy accuracy by staying quiet, so the two columns\n"
        "  only mean something together.\n"
    )

    tracks = Counter(d.truth for d in with_truth if d.truth)
    if tracks:
        top = tracks.most_common(1)[0]
        print(
            f"  baseline: always guessing track {top[0]} would be right "
            f"{_pct(top[1], len(with_truth))}\n"
        )


def main() -> int:
    """Score a collected change log."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "logs", nargs="+", type=Path, help="change logs from collect_nypenn.py"
    )
    args = parser.parse_args()

    missing = [path for path in args.logs if not path.is_file()]
    if missing:
        for path in missing:
            print(f"{path}: no such file", file=sys.stderr)
        return 2

    departures, polls = load(*args.logs)
    if not departures:
        print("nothing recorded yet", file=sys.stderr)
        return 1

    report(departures, polls)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
