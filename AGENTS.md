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

- **Station names are not one vocabulary, they are three.** The board wants
  `Short Hills`, the trip planner wants `Short Hills Station`, and planner leg
  descriptions emit `SHORT HILLS`. Worse, the planner's tolerance varies per
  station: `Hoboken`, `Hoboken Station`, and `Hoboken Terminal` all work, while
  bare `Short Hills` fails. Never infer a rule from a station that happens to
  work. SPEC §3.5.
- **A wrong station name is indistinguishable from no service.** Both surface
  as a generic "unable to find trips". This is the nastiest failure mode in the
  API.
- **The trip planner returns exactly 3 trips per call, always.** A single query
  for Short Hills → NY Penn yields 4 trains; the real service day has 51.
  Anything that needs the day's trains must page (SPEC §2.6). This was a bug in
  the spec itself before it was caught — the `51 trains, not 4` assertion in
  the tests is its regression guard.
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
uv run pytest
uv run ruff check .
uv run mypy .
```

Run `mypy .`, not `mypy custom_components/njtransit` — CI checks the whole
tree, and errors in `tests/` are easy to miss otherwise.

## Commit / PR conventions

- Commit subjects and PR titles follow Conventional Commits: `feat(api): ...`,
  `fix(sensor): ...`, `docs: ...`, `test: ...`, `ci: ...`.
- Keep PRs well-scoped — one logical change per PR rather than bundling
  unrelated fixes together, even when doing a broader sweep.
- When a change contradicts something in SPEC.md, update SPEC.md in the same
  PR. A spec that disagrees with the code is worse than no spec.
