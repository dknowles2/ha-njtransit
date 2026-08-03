# Agent instructions for ha-njtransit

`ha-njtransit` is a HACS custom component for Home Assistant that surfaces NJ
Transit rail departures and service alerts for a specific commute.

- **[SPEC.md](SPEC.md) is the source of truth.** Read it before writing code.
  It records the API surface, the constraints that were established
  empirically, and *why* each design decision went the way it did. Several of
  those decisions look wrong until you know what was tried.
- [CONTRIBUTING.md](CONTRIBUTING.md) — fork-and-PR mechanics.

## The one thing to understand first

There is no official NJ Transit API here. This talks to the private GraphQL
endpoint behind njtransit.com: no auth, no documentation, no compatibility
promise, introspection disabled, and a WAF in front. Everything known about it
was reverse-engineered, and it can change without notice.

Two consequences shape the whole codebase:

1. **Never widen a GraphQL field selection casually.** Asking for a field the
   server cannot populate nulls the *entire* response, not just that field.
   `getTrainStations { latitude }` returns `data: null` because
   `Station.latitude` is non-nullable in the schema and null in the data.
   Selections are pinned per query in `api/queries.py` and mirror what
   njtransit.com itself sends. See SPEC §3.1.
2. **Always use named operations with `variables`.** Inline arguments are
   rejected by the WAF with `{"status":400,"message":"Malformed request"}` —
   which is not GraphQL-shaped and needs its own error branch. See SPEC §3.2.

## Verifying against the real API

`scripts/extract_ops.py` re-derives the site's own GraphQL operations from its
JS bundles. This is the substitute for introspection:

```sh
uv run python scripts/extract_ops.py               # list operations
uv run python scripts/extract_ops.py --diff ops.json  # non-zero exit on drift
```

It also reports root fields the bundles reference but that appear in no parsed
operation — queries assigned to minified constants, which the regex cannot see.
`getTrainScheduleStationsRailForDV` is one such, and the integration depends on
it, so do not assume the named-operation list is complete.

## Fixtures are evidence, not scaffolding

`tests/fixtures/` holds a coherent capture taken during a live Morris & Essex
disruption on 2026-08-03 — every query issued within the same minute, so
cross-feed correlation can be tested end to end.

The set exists to pin one specific fact: **neither feed is a superset of the
other.** Alerts named trains `309, 6311, 6324, 6607`; the board simultaneously
showed `6320` and `6311` cancelled. Train `6320` was cancelled on the board and
absent from the alert feed entirely.

Correlating the two is the reason this integration exists rather than a
`rest:` sensor. **If a change stops the disruption binary sensor firing on
`6320` from these fixtures, that is a regression in the core purpose**, not a
test that needs updating.

Do not regenerate these fixtures to make a test pass.

## Traps this API sets

- **Take station names from `getTrainScheduleStationsRailForDV` and use them
  verbatim.** Its titles are the one vocabulary both the board and the trip
  planner accept. Never synthesize a name: most titles end in `Station` or
  `Terminal`, but `MetLife Stadium` and `Penn Station New York` do not.
- **That list has alias rows, so `title` is not a key.** 177 rows describe 167
  stations — New York Penn appears three times. Dedupe by `pentaStationID`,
  which is also the stable identity for unique IDs. SPEC §3.5.
- **Outside that list, tolerance varies per station and per consumer.** The
  board fuzzy-matches; the planner does not, and inconsistently — `Hoboken`,
  `Hoboken Station` and `Hoboken Terminal` all work while bare `Short Hills`
  fails. Never infer a rule from a station that happens to work.
- **A wrong station name is indistinguishable from no service.** Both surface
  as a generic "unable to find trips". This is the nastiest failure mode in the
  API, and code that swallows it will be silent in exactly the case users
  report.
- **Line names are a fourth vocabulary, and the board's is useless.**
  `lineAbbreviation` is `M&E`, matching neither the alert feed's `MNE` nor
  `getTrainLines`. Correlate on the board's `line` *titles*, which match
  `getTrainLines` exactly for twelve of thirteen lines — the exception being
  `Morristown Line`. Unresolvable lines fail open. SPEC §6.4.
- **The trip planner returns exactly 3 trips per call, always.** A single query
  for Short Hills → NY Penn yields 4 trains; the real service day has 51.
  Anything that needs the day's trains must page (SPEC §2.6). This was a bug in
  the spec itself before it was caught — the `51 trains, not 4` assertion in
  the tests is its regression guard.
- **A `Hoboken` train on a Penn Station board is usually correct.** This is
  the most common false alarm about the destination filter. Train `880` reads
  `Hoboken`; the planner routes it via a Newark Broad Street transfer onto
  `6258`, reaching Penn at 7:03 PM — ahead of the direct train leaving 21
  minutes later. Check the planner itinerary before "fixing" the filter.
- **The planner will route you by bus and subway if you let it.** `travelMode`
  is the one call parameter not copied from njtransit.com, which sends
  `BCTLXR`. With every mode on, the planner picked bus-and-subway for `880`
  (1 hr 17 min) and never offered the 53-minute rail transfer on any page, so
  the board would have shown the wrong arrival. Send `CT`. It costs six
  trains a day, all bus-dependent hour-plus journeys. SPEC §2.5.
- **A trip's arrival is the itinerary's, not the last rail leg's.** They
  differ whenever the journey continues by another mode, and the rail-leg
  reading always flatters: it called a 1 hr 7 min journey 42 minutes. Reach
  for `_terminal_time`, not `rail_legs[-1]`.
- **Train IDs are strings, not numbers.** Trenton's board carries Amtrak `A79`
  and SEPTA services.
- **Status casing is inconsistent** for the same semantic state — `Cancelled`
  and `CANCELLED` both appear in a single response. Normalize
  case-insensitively, keep the raw string, and degrade unknown values to
  `UNKNOWN` rather than raising. The vocabulary is undocumented and assumed
  incomplete.
- **Times are bare wall-clock strings** with no date and no zone. Everything is
  `America/New_York` with explicit midnight-rollover handling.

## Brand images

`custom_components/njtransit/brand/` holds the icons Home Assistant serves for
this integration. They are generated, not hand-drawn:

```sh
uv run python scripts/generate_brand.py
```

The artwork is **original and must stay that way**. NJ Transit's logo and
wordmark are their trademarks and this integration is unaffiliated, so
shipping their mark would contradict the README's own disclaimer. There is
deliberately no `logo.png` -- that is where the temptation to reproduce their
wordmark lives, and Home Assistant falls back to the icon without one.
`tests/test_brand.py` fails if a logo appears, which is the prompt to check
the artwork's provenance rather than a rule against ever having one.

## Releases

Publishing a GitHub release is the whole process. `release.yml` stamps the tag
into `manifest.json`, zips `custom_components/njtransit/` into
`njtransit.zip`, and attaches it -- that asset is what HACS downloads, named
by `hacs.json`.

If an upload ever fails, the release is left published with no asset -- a
broken install for everyone, and invisible until someone tries. Re-run the
workflow manually against the existing tag rather than deleting and re-cutting
the release:

```sh
gh workflow run release.yml -f tag=2026.8.0
```

The upload is idempotent, and the final step fails loudly if the asset is not
attached afterwards.

**Do not put a real version in `manifest.json`.** It carries the
`0000.0.0` placeholder deliberately, and the release workflow refuses to run
if that is missing, so a stale hand-edited version can never ship.
`tests/test_packaging.py` guards this and the other packaging invariants, so
they fail in the pull request rather than after a tag exists.

Release notes are drafted by release-drafter from merged pull requests, and
labels come from the Conventional Commits prefix in the PR title -- `feat:`
lands under new features and bumps the minor version, `fix:` under bug fixes.
That is another reason to get the prefix right.

**Tags are bare CalVer: `2026.8.0`, no `v` prefix.** This matches ha-pitboss
and pyschlage, and Home Assistant's own scheme.

CalVer is not automatic. release-drafter increments the *previous* tag, so the
first release of each month has to be tagged by hand -- `2026.9.0` when
September comes -- after which patch releases resolve on their own
(`2026.9.1`, `2026.9.2`). If a draft ever proposes something that is not
year.month.patch, that is release-drafter counting from the wrong baseline,
not a version to accept.

The release workflow strips a leading `v` before stamping the manifest, so a
`v`-prefixed tag would still produce a clean version. That is insurance
against a slip, not an invitation to change the convention.

## Layering

`custom_components/njtransit/api/` must not import `homeassistant`. It takes an
injected `aiohttp.ClientSession` and raises its own exceptions.

The client is bundled rather than published to PyPI, which is a deliberate
tradeoff: Home Assistant Core requires third-party API clients to be a separate
package, so core submission will mean extracting `api/` later. Keeping it
HA-free makes that a move rather than a rewrite. A test enforces the boundary —
if it fails, do not add an exemption.

## Before committing

Run the same checks CI runs, and make sure all three pass:

```sh
uv run coverage run -m pytest
uv run coverage report      # enforces fail_under = 97
uv run ruff check .
uv run mypy .
```

`uv run pytest` alone is fine for quick iteration, but run the coverage
variant before finishing — CI enforces the gate, and new code without tests
fails there rather than here.

Run `mypy .`, not `mypy custom_components/njtransit` — CI checks the whole
tree, and errors in `tests/` are easy to miss otherwise.

`tests/test_snapshots.py` freezes every entity's state and attributes against
the recorded capture, which is what catches an entity quietly disappearing or
an attribute being renamed. Update it deliberately, never to make a build go
green:

```sh
uv run pytest tests/test_snapshots.py --snapshot-update
```

A snapshot diff is a behaviour change. Read it before accepting it — if you
cannot explain a line of the diff, that line is the bug.

## Commit / PR conventions

- Commit subjects and PR titles follow Conventional Commits: `feat(api): ...`,
  `fix(sensor): ...`, `docs: ...`, `test: ...`, `ci: ...`.
- Keep PRs well-scoped — one logical change per PR rather than bundling
  unrelated fixes together, even when doing a broader sweep.
- When a change contradicts something in SPEC.md, update SPEC.md in the same
  PR. A spec that disagrees with the code is worse than no spec.
