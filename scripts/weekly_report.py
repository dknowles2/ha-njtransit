#!/usr/bin/env python3
"""Score the signalled platform weekly, and keep the answers.

What it produces, per run:

* a dated Markdown report, and a copy as `latest.md`;
* rows appended to `signal.csv`, one per service day scored, so drift is a
  file you can plot rather than a set of reports someone has to read and
  compare.

This report used to carry three more sections: a table of eight hand-written
models and a fitted ranker that predicted the track from the board's own
history, the weights that ranker learned, and how nypenn.live's published
predictions scored. They ran weekly from September to November 2026 against a
bar set before any data existed -- 60% top-1 -- and the best of them sat
between 23% and 28% the whole time, while the collection behind it tripled.
That settled it: the board's history does not say which track a train will
use, and more of it does not help. They were removed rather than left running,
along with the diagnostics fetch from Home Assistant they needed. Git history
has them; the last commit carrying them is the merge of #80.

Usage:
    python scripts/weekly_report.py --out ~/nypenn/reports \\
        --raildata-log ~/nypenn/raildata.jsonl

Everything below is stdlib. This is an operations tool, not shipped code.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import signal_accuracy

SIGNAL_COLUMNS = [
    "run",
    "day",
    "posted",
    "covered",
    "agreed",
    "healed",
    "corrections",
    "median_lead",
    "median_head_start",
]

# How far behind the run the collector's log may fall before the report says
# so. Twelve hours rather than a day: the collector polls every thirty seconds,
# so even half a day of silence is well over a thousand failed polls and not
# something that happens quietly.
#
# This exists because it already happened. The host lost outbound network for
# 57 hours in August and the collector -- correctly -- logged the failures and
# carried on. The weekly report went on printing the same accuracy tables from
# frozen data, and a stale section reads exactly like a current one. The
# numbers were not wrong; they were just answering a question about last
# Tuesday while appearing to answer one about today.
STALE_AFTER = timedelta(hours=12)

# How much of a log's tail is read to find its newest record. Every record is
# one line of well under a kilobyte, so this always spans several of them
# without reading the 900MB in front.
_TAIL_BYTES = 64 * 1024


def last_record_at(path: Path) -> datetime | None:
    """Return when the newest record in a collector log was written.

    Read from the tail rather than by scanning the file. The first line in the
    window may be cut in half, and the last may be half-written if the
    collector is appending at that moment, so any line that does not parse is
    skipped rather than trusted.
    """
    with path.open("rb") as file:
        file.seek(0, 2)
        size = file.tell()
        file.seek(max(0, size - _TAIL_BYTES))
        tail = file.read().decode("utf-8", errors="replace")
    for line in reversed(tail.splitlines()):
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict) and isinstance(record.get("t"), (int, float)):
            return datetime.fromtimestamp(record["t"], UTC)
    return None


def signal_section(
    logs: list[Path], days: int | None, run_at: datetime
) -> tuple[str, list[dict[str, Any]]]:
    """Return how the signalled platform did, from the collector's own logs.

    A window is applied here rather than in the report body. The change log
    grows by about 300MB a month and the collector host is the machine running
    this, so a run that loaded all of it to print a fortnight would be killed
    on the host it exists to report from.

    `run_at` is passed rather than read from the clock so the staleness check
    is against the moment the report claims to describe.
    """
    present = [path for path in logs if path.is_file()]
    if not present:
        return "No collector logs found.\n", []

    lines = []
    newest = [at for at in (last_record_at(path) for path in present) if at]
    if not newest:
        lines += ["**The collector logs hold no readable records.**", ""]
    elif run_at - max(newest) > STALE_AFTER:
        behind = run_at - max(newest)
        lines += [
            (
                f"**Stale: collection stops "
                f"{behind.total_seconds() / 3600:.0f} hours before this run** "
                f"(last record {max(newest).strftime('%Y-%m-%d %H:%M %Z')}). "
                "Everything below describes that earlier period, not this one."
            ),
            "",
        ]

    since = (
        datetime.now(signal_accuracy.TZ).date() - timedelta(days=days)
        if days is not None
        else None
    )
    section, rows = signal_accuracy.report(signal_accuracy.load(*present, since=since))
    return "\n".join(lines) + section, rows


def append_csv(path: Path, columns: list[str], rows: list[dict[str, Any]]) -> None:
    """Append rows, writing the header only when the file is new.

    Append rather than rewrite: these files are the record of what was true on
    a given week, and a run that recomputed history would erase exactly the
    drift they exist to show.
    """
    if not rows:
        return
    new = not path.exists()
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        if new:
            writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    """Score the collector's log and write the report."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True, help="report directory")
    parser.add_argument(
        "--raildata-log",
        type=Path,
        nargs="+",
        required=True,
        help="collector change logs to score the signalled platform from",
    )
    parser.add_argument(
        "--signal-days",
        type=int,
        default=14,
        help="how many days of collector log to score (default: 14)",
    )
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    started = datetime.now(UTC)
    run = started.strftime("%Y-%m-%d")

    section, signal_rows = signal_section(args.raildata_log, args.signal_days, started)
    parts = [
        f"# Signalled platform — {run}",
        "",
        "## New York Penn: signalled platform vs the board",
        "",
        section,
        "",
    ]
    append_csv(
        args.out / "signal.csv",
        SIGNAL_COLUMNS,
        [{"run": run, **row} for row in signal_rows],
    )

    report = args.out / f"{run}.md"
    report.write_text("\n".join(parts), encoding="utf-8")
    (args.out / "latest.md").write_text("\n".join(parts), encoding="utf-8")
    print(f"wrote {report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
