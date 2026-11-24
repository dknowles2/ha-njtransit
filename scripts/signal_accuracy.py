#!/usr/bin/env python3
"""Score the signalled platform against the departure board that followed it.

`collect_raildata.py` records two things a minute apart: what the signalling
system says a train is standing on, and what the public board says. The claim
this integration ships -- that the first is right, and arrives twenty minutes
before the second -- is a claim about those two columns, and until now it was
measured by hand each time somebody asked. This module is that measurement,
written down once so it is the same number every week.

**Scored against the board's last posting, not its first.** This is the one
judgement call in here and it changed the answer. The board sometimes posts a
track and corrects it within the minute -- ten runs in the first six weeks of
collection, and on two of them the signal had already named the corrected
track. Scoring against the first posting marked those as misses: the signal
was penalised for being right before the board had finished being wrong. The
track a passenger walks to is the last one posted, so that is the answer, and
`corrections` reports how often the two differed so the choice stays visible
rather than buried.

What counts as a signal is deliberately narrow: the last platform sighting at
or before the moment the board first posts. That is exactly what the
integration would have been showing at that moment -- `RailDataClient` keeps
the most recent platform sighting per train and run (SPEC 2.9) -- so the score
describes the shipped behaviour and not a better rule invented afterwards.
Sightings that arrive later are not scored, but they are not wasted either:
`healed` counts the misses where the feed went on to show the right platform
before departure, which the client would have picked up, because the number
that matters to somebody standing in Penn is how long a wrong answer was on
their phone rather than whether one was ever computed.

Coverage is the other half and the weaker half: a signal is only possible for
a train whose set is sighted on a platform circuit at all, which is about two
departures in three. A platform the feed never showed is reported as no
answer, never as a guess.

Usage:
    python scripts/signal_accuracy.py --log ~/nypenn/raildata.jsonl
    python scripts/signal_accuracy.py --log ~/nypenn/raildata.jsonl --days 7

Everything below is stdlib. This is an analysis tool, not shipped code.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, NamedTuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

from occupancy import TZ, decode

# The feed's own format for a scheduled departure, e.g. `14-Sep-2026 09:27:00
# PM`.
SCHED_FORMAT = "%d-%b-%Y %I:%M:%S %p"

# `sched_dep` is the vehicle feed's `SCHED_DEP_TIME`, and it is *not* a
# constant per run: it is the scheduled time of whatever stop the train is
# working towards, so it advances all the way to the end of the line. In one
# recent window 6,233 of 6,458 train-days carried more than one value.
#
# A run is therefore keyed by train number and a gap in the record stream, not
# by that field. Keying on it looked like it worked -- only a fragment holding
# a board posting becomes a scored run, and that is usually one of them -- but
# it quietly split a run's later sightings into fragments of their own, which
# undercounted `healed`. The value is still read, once, off the record that
# carried the board's first posting: the train is standing at the terminal at
# that moment, so the stop it is working towards is this departure.
RUN_GAP = timedelta(hours=3)

# Only departures are scored. The collector records both directions -- an
# eastbound train's last circuit is how an arrival is spotted -- but the board
# this is measured against is a departure board, and an arrival has no posting
# to be right or wrong about.
DEPARTING = "Westbound"


class Run(NamedTuple):
    """One departure, the signal that preceded it, and what the board said."""

    day: date
    train_id: str
    scheduled: datetime
    line: str | None

    board_first: str
    """The first track the board posted. Never empty: a run with no posting is
    not a `Run` at all, because there is nothing to score against."""

    board_first_at: datetime
    board_final: str
    """The last track the board posted, which is the one that was walked to."""

    signal: str | None
    """Platform the signalling system had this train on when the board spoke,
    or None when it had never shown it on one."""

    signal_first_at: datetime | None
    """When the platform was *first* visible, which is what the lead measures.
    A set is often sighted, dropped from the feed and sighted again; the head
    start is from the first sighting, since that is when the answer existed."""

    healed: bool
    """A wrong signal that the feed corrected before departure.

    False for every run whose signal was right, so this is only ever read
    alongside a miss.
    """

    @property
    def covered(self) -> bool:
        """Whether the signal spoke before the board did."""
        return self.signal is not None

    @property
    def agreed(self) -> bool:
        """Whether the signal named the track the train actually left from."""
        return self.signal is not None and self.signal == self.board_final

    @property
    def corrected(self) -> bool:
        """Whether the board changed its own answer after first posting it."""
        return self.board_first != self.board_final

    @property
    def lead_minutes(self) -> float | None:
        """Minutes between the first platform sighting and the timetable."""
        if self.signal_first_at is None:
            return None
        return (self.scheduled - self.signal_first_at).total_seconds() / 60

    @property
    def head_start_minutes(self) -> float | None:
        """Minutes the signal beat the board's first posting by."""
        if self.signal_first_at is None:
            return None
        return (self.board_first_at - self.signal_first_at).total_seconds() / 60


class Summary(NamedTuple):
    """How the signal did over a set of runs."""

    posted: int
    """Departures the board posted a track for, scored or not."""

    covered: int
    agreed: int
    healed: int
    corrections: int
    """Runs where the board's first posting was not its last."""

    lead: list[float]
    head_start: list[float]
    misses: list[Run]

    @property
    def accuracy(self) -> float | None:
        """Share of answered departures the signal got right."""
        return self.agreed / self.covered if self.covered else None

    @property
    def coverage(self) -> float | None:
        """Share of posted departures the signal answered at all."""
        return self.covered / self.posted if self.posted else None


def _parse_scheduled(text: str | None) -> datetime | None:
    """Return a timezone-aware scheduled departure, or None if unparseable."""
    if not text:
        return None
    try:
        return datetime.strptime(text, SCHED_FORMAT).replace(tzinfo=TZ)
    except ValueError:
        return None


def load(*paths: Path, since: date | None = None) -> list[Run]:
    """Replay collector logs into one `Run` per scored departure.

    Streamed a line at a time and filtered by `since` before anything is kept.
    The change log passes 300MB after a month and the whole of it is never the
    question being asked, so holding only the window asked for is the
    difference between a report that runs on the collector host and one that
    gets killed there.

    Runs the board never posted a track for are dropped. They are the majority
    of what the collector sees -- every poll of a departure an hour out -- and
    there is nothing in them to be right or wrong about.
    """
    by_train: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for path in paths:
        with path.open(encoding="utf-8") as file:
            for line in file:
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    # A half-written last line, which happens when the log is
                    # read while the collector is appending to it. Skipping it
                    # costs one record; refusing the whole file costs the
                    # report.
                    continue
                if record.get("type") != "change":
                    continue
                when = record.get("t")
                if when is None:
                    continue
                if (
                    since is not None
                    and datetime.fromtimestamp(when, TZ).date() < since
                ):
                    continue
                by_train[str(record["train_id"])].append(record)

    runs = []
    for train_id, records in by_train.items():
        records.sort(key=lambda r: r["t"])
        for group in _split_runs(records):
            run = _score_run(train_id, group)
            if run is not None:
                runs.append(run)
    return sorted(runs, key=lambda r: (r.scheduled, r.train_id))


def _split_runs(records: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Split one train's records, in time order, into separate journeys.

    A train number runs once a service day, so a gap of hours is the next
    day's working rather than a pause in this one. The split is on the gap and
    not on the calendar date because a departure at 23:50 is recorded either
    side of midnight, and a date key would cut that run in half -- the same
    trap `analyze_tracks.join_outcomes` searches adjacent days to avoid.
    """
    groups: list[list[dict[str, Any]]] = []
    for record in records:
        at = datetime.fromtimestamp(record["t"], TZ)
        if groups and at - datetime.fromtimestamp(groups[-1][-1]["t"], TZ) <= RUN_GAP:
            groups[-1].append(record)
        else:
            groups.append([record])
    return groups


def _score_run(train_id: str, records: list[dict[str, Any]]) -> Run | None:
    """Build one `Run`, or None when this departure cannot be scored."""
    postings = [
        r for r in records if r.get("board_track") and r.get("direction") == DEPARTING
    ]
    if not postings:
        return None

    first, last = postings[0], postings[-1]
    # Read off the record that carried the first posting, where the train is
    # still standing at the terminal and the stop it is working towards is
    # this departure. See RUN_GAP for why it is not read off every record.
    scheduled = _parse_scheduled(first.get("sched_dep"))
    if scheduled is None:
        return None

    board_first = str(first["board_track"]).strip()
    board_final = str(last["board_track"]).strip()
    posted_at = datetime.fromtimestamp(first["t"], TZ)
    left_board_at = datetime.fromtimestamp(last["t"], TZ)

    # Platform sightings, split at the moment the board spoke. Everything at
    # or before it is what the integration could have been showing; everything
    # after it is only interesting for whether a miss would have healed.
    #
    # Healing is bounded by the last posting rather than by the timetable: a
    # wrong track is on the phone for exactly as long as the row is on the
    # board, and a late train's row outlives its scheduled time.
    before: list[tuple[datetime, str]] = []
    after: list[str] = []
    for record in records:
        platform = decode(record.get("circuit"))
        if platform is None:
            continue
        at = datetime.fromtimestamp(record["t"], TZ)
        if at <= posted_at:
            before.append((at, platform))
        elif at <= left_board_at:
            after.append(platform)

    signal = before[-1][1] if before else None
    signal_first_at = before[0][0] if before else None
    healed = signal is not None and signal != board_final and board_final in after

    return Run(
        day=scheduled.date(),
        train_id=train_id,
        scheduled=scheduled,
        line=first.get("line"),
        board_first=board_first,
        board_first_at=posted_at,
        board_final=board_final,
        signal=signal,
        signal_first_at=signal_first_at,
        healed=healed,
    )


def summarize(runs: list[Run]) -> Summary:
    """Reduce runs to the numbers a report prints."""
    covered = [run for run in runs if run.covered]
    return Summary(
        posted=len(runs),
        covered=len(covered),
        agreed=sum(1 for run in covered if run.agreed),
        healed=sum(1 for run in covered if run.healed),
        corrections=sum(1 for run in runs if run.corrected),
        lead=[run.lead_minutes for run in covered if run.lead_minutes is not None],
        head_start=[
            run.head_start_minutes
            for run in covered
            if run.head_start_minutes is not None
        ],
        misses=[run for run in covered if not run.agreed],
    )


def by_day(runs: list[Run]) -> dict[date, Summary]:
    """Summarize each service day separately, oldest first."""
    grouped: dict[date, list[Run]] = defaultdict(list)
    for run in runs:
        grouped[run.day].append(run)
    return {day: summarize(grouped[day]) for day in sorted(grouped)}


def _median(values: list[float]) -> str:
    """Return a median to one decimal, or a dash when there is nothing."""
    return f"{statistics.median(values):.1f}" if values else "-"


def _pct(part: int, whole: int) -> str:
    """Return a percentage, or a dash when there is nothing to divide."""
    return f"{part / whole:.0%}" if whole else "-"


def report(runs: list[Run]) -> tuple[str, list[dict[str, Any]]]:
    """Return the Markdown section, and the same numbers as CSV rows."""
    if not runs:
        return "No scored departures in this window.\n", []

    total = summarize(runs)
    lines = [
        "| day | posted | signalled first | right | median lead | head start |",
        "|---|---|---|---|---|---|",
    ]
    rows = []
    for day, summary in by_day(runs).items():
        lines.append(
            f"| {day} | {summary.posted} "
            f"| {summary.covered} ({_pct(summary.covered, summary.posted)}) "
            f"| {summary.agreed}/{summary.covered} "
            f"({_pct(summary.agreed, summary.covered)}) "
            f"| {_median(summary.lead)} min | {_median(summary.head_start)} min |"
        )
        rows.append(
            {
                "day": day.isoformat(),
                "posted": summary.posted,
                "covered": summary.covered,
                "agreed": summary.agreed,
                "healed": summary.healed,
                "corrections": summary.corrections,
                "median_lead": _median(summary.lead),
                "median_head_start": _median(summary.head_start),
            }
        )
    lines.append(
        f"| **all** | {total.posted} "
        f"| {total.covered} ({_pct(total.covered, total.posted)}) "
        f"| {total.agreed}/{total.covered} "
        f"({_pct(total.agreed, total.covered)}) "
        f"| {_median(total.lead)} min | {_median(total.head_start)} min |"
    )

    lines += [
        "",
        (
            f"Scored against the board's last posting. It revised its own "
            f"answer on {total.corrections} of {total.posted} departures; "
            "scoring against the first posting would count those revisions "
            "against the signal."
        ),
    ]
    if total.misses:
        healed = sum(1 for run in total.misses if run.healed)
        lines += [
            "",
            (
                f"{len(total.misses)} wrong. The feed went on to show the "
                f"right platform before departure on {healed} of them, which "
                "the client picks up; the rest were never visible."
            ),
            "",
            "| day | train | scheduled | signalled | left from | line |",
            "|---|---|---|---|---|---|",
        ]
        for run in total.misses:
            lines.append(
                f"| {run.day} | {run.train_id} "
                f"| {run.scheduled.strftime('%H:%M')} | {run.signal} "
                f"| {run.board_final} | {run.line or '-'} |"
            )
    return "\n".join(lines) + "\n", rows


def main() -> int:
    """Score the collector's logs and print the report."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--log",
        type=Path,
        nargs="+",
        required=True,
        help="collector change logs to score",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=None,
        help="only score the last N service days (default: all of them)",
    )
    args = parser.parse_args()

    since = None
    if args.days is not None:
        # Penn's day, not the host's: the collector may run in any
        # timezone and a service day is a local thing.
        since = datetime.now(TZ).date() - timedelta(days=args.days)
    runs = load(*args.log, since=since)
    section, _ = report(runs)
    print(section)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
