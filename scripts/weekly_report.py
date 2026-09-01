#!/usr/bin/env python3
"""Re-run every track-prediction analysis and write down what it said.

The models are only interesting over time. A single 22% says the feature does
not ship; the same number a month later, against three times the data, says
whether that was a ceiling or a shortage. Nobody re-runs an analysis by hand
for six weeks, so this does it on a timer and keeps the answers.

What it produces, per run:

* a dated Markdown report -- the model table, the fitted weights, and how
  nypenn.live did over the same period;
* one row appended to a CSV, so drift is a file you can plot rather than a
  set of reports someone has to read and compare.

The CSV is the point. The interesting question left open by
`scripts/learn_tracks.py` is whether `train history` stays near zero as the
collection grows: if it does, a train's own past really is weak evidence, and
if it climbs, ten days was simply too few. One report cannot answer that. A
column of them can.

Diagnostics come from Home Assistant over ssh, by asking that host to curl its
own API. The token stays on the box that already has it -- nothing is copied
here, and this script never sees it.

Usage:
    python scripts/weekly_report.py --out ~/nypenn/reports
    python scripts/weekly_report.py --out ~/nypenn/reports --nypenn-log ~/nypenn/*.jsonl

Everything below is stdlib. This is an operations tool, not shipped code.
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import learn_tracks
import nypenn
from analyze_tracks import BAR, NY_PENN, Observation, load
from analyze_tracks import score as score_models

# Asking the Home Assistant host to curl itself, rather than reaching its API
# from here. The long-lived token lives on that box and stays there; this one
# needs nothing but ssh access it already has.
ENTRIES_CMD = (
    'T=$(cat /config/.claude-token); curl -s -H "Authorization: Bearer $T" '
    "http://localhost:8123/api/config/config_entries/entry"
)
DIAGNOSTICS_CMD = (
    'T=$(cat /config/.claude-token); curl -s -H "Authorization: Bearer $T" '
    "http://localhost:8123/api/diagnostics/config_entry/{entry}"
)

CSV_COLUMNS = [
    "run",
    "station",
    "days",
    "observations",
    "model",
    "top1",
    "top3",
    "answered",
]
WEIGHTS_COLUMNS = ["run", "feature", "weight", "spread"]

# How far behind the run the collected logs may fall before the nypenn section
# says so. Twelve hours rather than a day: the collector polls every minute, so
# even half a day of silence is thousands of failed attempts and not something
# that happens quietly.
#
# This exists because it already happened. The host lost outbound network for
# 57 hours in August and the collector -- correctly -- logged the failures and
# carried on. The weekly report went on printing the same accuracy tables from
# frozen data, and a stale section reads exactly like a current one. The
# numbers were not wrong; they were just answering a question about last
# Tuesday while appearing to answer one about today.
STALE_AFTER = timedelta(hours=12)


def _ssh(host: str, command: str) -> str:
    """Run a command on `host` and return its stdout."""
    result = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", host, command],
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    return result.stdout


def fetch_diagnostics(host: str, into: Path) -> list[Path]:
    """Download one diagnostics dump per commute, and return the paths.

    The entries are discovered rather than configured. Entry ids are per
    install and change when a commute is removed and re-added, and a hardcoded
    one fails by silently reporting on less data than exists.
    """
    entries = json.loads(_ssh(host, ENTRIES_CMD))
    written = []
    for entry in entries:
        if entry.get("domain") != "njtransit":
            continue
        dump = _ssh(host, DIAGNOSTICS_CMD.format(entry=entry["entry_id"]))
        if not dump.strip():
            print(f"no diagnostics for {entry['entry_id']}", file=sys.stderr)
            continue
        path = into / f"{entry['entry_id']}.json"
        path.write_text(dump, encoding="utf-8")
        written.append(path)
    return written


def _pct(part: int, whole: int) -> str:
    """Return a percentage, or a dash when there is nothing to divide."""
    return f"{part / whole:.0%}" if whole else "-"


def model_section(
    observations: list[Observation], learned: learn_tracks.Learned | None
) -> tuple[str, list[dict[str, Any]]]:
    """Return the model table as Markdown, and the same numbers as rows.

    The fitted model is passed in rather than fitted here: it takes about a
    minute in pure Python, and this section and the one below both want it.
    """
    scores = score_models(observations)
    if scores is None:
        return "Not enough days to hold one out yet.\n", []

    lines = ["| model | top-1 | top-3 | answered |", "|---|---|---|---|"]
    rows: list[dict[str, Any]] = []
    for name, result in scores.items():
        cleared = " ✅" if result.total and result.hits / result.total >= BAR else ""
        lines.append(
            f"| {name} | {_pct(result.hits, result.total)}{cleared} "
            f"| {_pct(result.top3, result.total)} "
            f"| {_pct(result.answered, result.total)} |"
        )
        rows.append(
            {
                "model": name,
                "top1": round(result.hits / result.total, 4) if result.total else "",
                "top3": round(result.top3 / result.total, 4) if result.total else "",
                "answered": round(result.answered / result.total, 4)
                if result.total
                else "",
            }
        )

    if learned is not None and learned.total:
        lines.append(
            f"| ml conditional logit | {_pct(learned.hits, learned.total)} "
            f"| {_pct(learned.top3, learned.total)} | 100% |"
        )
        rows.append(
            {
                "model": "ml conditional logit",
                "top1": round(learned.hits / learned.total, 4),
                "top3": round(learned.top3 / learned.total, 4),
                "answered": 1.0,
            }
        )
    return "\n".join(lines) + "\n", rows


def weight_section(
    learned: learn_tracks.Learned | None,
) -> tuple[str, list[dict[str, Any]]]:
    """Return what the fitted model decided mattered.

    Reported with the spread across folds, because a weight that swings from
    fold to fold is the model disagreeing with itself and must not be read as
    a finding. This is the column worth watching as the collection grows.
    """
    if learned is None or not learned.weights:
        return "No fitted weights this run.\n", []

    folds = learned.weights
    lines = ["| feature | weight | spread across folds |", "|---|---|---|"]
    rows = []
    for index, name in enumerate(learn_tracks.FEATURES):
        values = [fold[index] for fold in folds]
        weight = sum(values) / len(values)
        spread = (sum((v - weight) ** 2 for v in values) / len(values)) ** 0.5
        lines.append(f"| {name} | {weight:.2f} | ±{spread:.2f} |")
        rows.append(
            {"feature": name, "weight": round(weight, 4), "spread": round(spread, 4)}
        )
    order = sorted(rows, key=lambda r: -abs(float(r["weight"])))
    lines = lines[:2] + [
        f"| {r['feature']} | {r['weight']:.2f} | ±{r['spread']:.2f} |" for r in order
    ]
    return "\n".join(lines) + "\n", rows


def nypenn_section(logs: list[Path], run_at: datetime) -> str:
    """Return how nypenn.live did, from whatever has been collected.

    `run_at` is passed rather than read from the clock so the staleness check
    is against the moment the report claims to describe.
    """
    present = [path for path in logs if path.is_file()]
    if not present:
        return "No nypenn.live logs found.\n"

    departures, polls = nypenn.load(*present)
    if not departures:
        return "nypenn.live logs are present but empty.\n"

    with_truth = [d for d in departures if d.truth is not None]
    predicted = [d for d in with_truth if d.first_prediction]
    gaps = sorted(nypenn.head_start(predicted))
    days = sorted({d.day for d in departures})

    lines = []
    if polls:
        behind = run_at - max(polls)
        if behind > STALE_AFTER:
            warning = (
                f"**Stale: collection stops "
                f"{behind.total_seconds() / 3600:.0f} hours before this run** "
                f"(last poll {max(polls).strftime('%Y-%m-%d %H:%M %Z')}). "
                "Everything below describes that earlier period, not this one."
            )
            lines += [warning, ""]

    lines += [
        (
            f"{len(departures)} departures over {len(days)} day(s), "
            f"{len(polls)} polls. {len(with_truth)} reached a confirmed track."
        ),
        "",
        "| their confidence | top-1 | top-3 | n | held |",
        "|---|---|---|---|---|",
    ]
    by_tier = nypenn.accuracy_by_tier(departures)
    for tier, result in by_tier.items():
        lines.append(
            f"| {tier} | {_pct(result.hits, result.answered)} "
            f"| {_pct(result.top3, result.answered)} | {result.answered} "
            f"| {result.withheld} |"
        )
    held = sum(result.withheld for result in by_tier.values())
    if held:
        # Without this the table is a fair summary of the wrong thing. The
        # confident tiers empty out, `low` keeps being scored, and a week in
        # which they predicted perfectly well reads as a week they went quiet.
        lines += [
            "",
            (
                f"**{held} predictions were withheld behind their paywall** and "
                "are not in any number above. `high` and `medium` are "
                "subscriber-only; without a session the feed sends the tier "
                "with the track stripped out, so the rows scored here are "
                "very largely their `low` tier."
            ),
        ]
    if gaps:
        lines += [
            "",
            (
                f"Head start over the official board: median "
                f"{gaps[len(gaps) // 2]:.0f} min "
                f"({gaps[0]:.0f} to {gaps[-1]:.0f})."
            ),
        ]
    return "\n".join(lines) + "\n"


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
    """Fetch, analyse, and write the report."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True, help="report directory")
    parser.add_argument(
        "--host", default="root@hass", help="ssh target for Home Assistant"
    )
    parser.add_argument(
        "--nypenn-log",
        type=Path,
        nargs="*",
        default=[],
        help="nypenn.live change logs to score",
    )
    parser.add_argument(
        "--station", default=NY_PENN, help="station to model; the terminal by default"
    )
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    dumps_dir = args.out / "diagnostics"
    dumps_dir.mkdir(exist_ok=True)
    started = datetime.now(UTC)
    run = started.strftime("%Y-%m-%d")

    parts = [f"# Track prediction — {run}", ""]

    try:
        dumps = fetch_diagnostics(args.host, dumps_dir)
    except (subprocess.SubprocessError, OSError, json.JSONDecodeError) as error:
        # Not fatal. The nypenn half needs nothing from Home Assistant, and a
        # report that says the fetch failed is more use than no report at all
        # -- a silent gap in the CSV looks like a week nothing changed.
        parts += [f"**Diagnostics fetch failed:** `{error}`", ""]
        dumps = []

    observations = (
        [o for o in load(dumps) if o.station == args.station] if dumps else []
    )

    if observations:
        days = sorted({o.day for o in observations})
        parts += [
            f"## {args.station}",
            "",
            (
                f"{len(observations)} observations over {len(days)} days "
                f"({days[0]} to {days[-1]})."
            ),
            "",
        ]
        learned = learn_tracks.score(observations)
        table, rows = model_section(observations, learned)
        parts += [table, "", "## What the fitted model weighted", ""]
        weights, weight_rows = weight_section(learned)
        parts += [weights, ""]

        stamped = [
            {
                "run": run,
                "station": args.station,
                "days": len(days),
                "observations": len(observations),
                **row,
            }
            for row in rows
        ]
        append_csv(args.out / "models.csv", CSV_COLUMNS, stamped)
        append_csv(
            args.out / "weights.csv",
            WEIGHTS_COLUMNS,
            [{"run": run, **row} for row in weight_rows],
        )
    else:
        parts += ["No observations to model this run.", ""]

    parts += ["## nypenn.live", "", nypenn_section(args.nypenn_log, started), ""]

    report = args.out / f"{run}.md"
    report.write_text("\n".join(parts), encoding="utf-8")
    (args.out / "latest.md").write_text("\n".join(parts), encoding="utf-8")
    print(f"wrote {report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
