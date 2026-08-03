#!/usr/bin/env python3
"""Extract GraphQL operations from njtransit.com's Nuxt bundles.

Introspection is disabled on the NJ Transit GraphQL endpoint, so the site's own
JS bundles are the authoritative source for what queries exist and which fields
they select. This re-derives them on demand.

Usage:
    python scripts/extract_ops.py                 # print a summary
    python scripts/extract_ops.py --json ops.json # dump full operation bodies
    python scripts/extract_ops.py --diff ops.json # compare against a saved dump

Run --diff in CI (or by hand when something breaks) to catch upstream drift
before it reaches users.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

import aiohttp

BASE = "https://www.njtransit.com"

# Pages whose bundles collectively reference every operation we care about.
# The homepage alone misses the DepartureVision queries.
PAGES = ["/", "/dv-to", "/schedules-and-fares"]

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"

CHUNK_RE = re.compile(r"/_nuxt/[A-Za-z0-9_-]+\.js")
QUERY_RE = re.compile(r"query\s+([A-Za-z0-9_]+)\s*(\([^)]*\))?\s*\{", re.DOTALL)

# Not every operation is written as `query Name { ... }` — some are anonymous
# templates assigned to a minified constant, so QUERY_RE misses them entirely.
# getTrainScheduleStationsRailForDV is one such, and the integration depends on
# it. Scanning for bare root-field identifiers catches those.
FIELD_RE = re.compile(r"\bget[A-Z][A-Za-z0-9_]{3,}\b")

# Bundles are full of framework and DOM `get*` helpers. Rather than blocklist
# them, require a transit-domain word — a heuristic, but it reduces ~83 hits of
# noise to a usable handful. Widen if a real endpoint is being missed.
DOMAIN_RE = re.compile(
    r"Train|Bus|Rail|Station|Stop|Trip|Schedule|System|Line|Fare|Advisory"
    r"|Alert|News|Menu|Departure|Arrival|Transit"
)

MAX_BODY = 20_000


def _extract(source: str) -> dict[str, dict[str, str]]:
    """Pull `query Name(args) { ... }` blocks out of a JS bundle."""
    found: dict[str, dict[str, str]] = {}
    for match in QUERY_RE.finditer(source):
        start = match.end() - 1
        depth = 0
        for i in range(start, min(start + MAX_BODY, len(source))):
            char = source[i]
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    found.setdefault(
                        match.group(1),
                        {
                            "args": " ".join((match.group(2) or "").split()),
                            "body": source[start : i + 1],
                        },
                    )
                    break
    return found


async def _fetch(session: aiohttp.ClientSession, url: str) -> str:
    async with session.get(url, headers={"User-Agent": UA}) as resp:
        resp.raise_for_status()
        return await resp.text()


async def collect() -> tuple[dict[str, dict[str, str]], set[str]]:
    ops: dict[str, dict[str, str]] = {}
    fields: set[str] = set()
    async with aiohttp.ClientSession() as session:
        chunks: set[str] = set()
        for page in PAGES:
            try:
                html = await _fetch(session, f"{BASE}{page}")
            except aiohttp.ClientError as err:
                print(f"warning: {page}: {err}", file=sys.stderr)
                continue
            chunks.update(CHUNK_RE.findall(html))

        print(f"found {len(chunks)} bundles", file=sys.stderr)

        async def one(path: str) -> None:
            try:
                source = await _fetch(session, f"{BASE}{path}")
            except aiohttp.ClientError as err:
                print(f"warning: {path}: {err}", file=sys.stderr)
                return
            ops.update(_extract(source))
            fields.update(f for f in FIELD_RE.findall(source) if DOMAIN_RE.search(f))

        await asyncio.gather(*(one(c) for c in sorted(chunks)))
    return ops, fields


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", type=Path, help="write full operation bodies here")
    parser.add_argument(
        "--diff", type=Path, help="compare against a previous --json dump"
    )
    args = parser.parse_args()

    ops, fields = asyncio.run(collect())
    if not ops:
        print("no operations found — bundle layout may have changed", file=sys.stderr)
        return 2

    print(f"\n{len(ops)} named operations:\n")
    for name in sorted(ops):
        print(f"  {name}{ops[name]['args']}")

    # Root fields referenced in the bundles but not covered by any operation we
    # could parse. These are real endpoints reachable by hand-written queries.
    covered = " ".join(op["body"] for op in ops.values())
    orphans = sorted(f for f in fields if f not in covered)
    if orphans:
        print(f"\n{len(orphans)} referenced but not in a parsed operation:\n")
        for name in orphans:
            print(f"  {name}")

    if args.json:
        args.json.write_text(json.dumps(ops, indent=1, sort_keys=True))
        print(f"\nwrote {args.json}", file=sys.stderr)

    if args.diff:
        old = json.loads(args.diff.read_text())
        added = sorted(set(ops) - set(old))
        removed = sorted(set(old) - set(ops))
        changed = sorted(
            n for n in set(ops) & set(old) if ops[n]["body"] != old[n]["body"]
        )
        if not (added or removed or changed):
            print("\nno drift", file=sys.stderr)
            return 0
        print("\nDRIFT DETECTED", file=sys.stderr)
        for label, names in (
            ("added", added),
            ("removed", removed),
            ("changed", changed),
        ):
            for name in names:
                print(f"  {label}: {name}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
