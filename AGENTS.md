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
- **Only one-seat rides reach the board.** Transfer itineraries are real —
  train `880` reads `Hoboken` yet reaches Penn ahead of the direct train
  leaving 21 minutes later — but the board cannot say where you change or
  that you must, so a row headsigned for somewhere you are not going is
  worse than a missing row. 23 direct trains a day for Short Hills to Penn,
  against 18 more reachable only by changing. Fails open where nothing runs
  direct, so a branch-to-branch pair shows connections rather than an empty
  board. SPEC §2.7.
- **"Direct" is not `len(train_ids) == 1`.** That counts rail legs only, so a
  train to Hoboken continuing by PATH looks like a one-seat ride. Use
  `has_transfer`, which is built on `transport_legs` and counts every leg you
  ride.
- **The planner will route you by bus and subway if you let it.** `travelMode`
  is the one call parameter not copied from njtransit.com, which sends
  `BCTLXR`. Send `C`: the other modes only buy itineraries §2.7 discards, and
  with three itineraries per call each one costs a slot a direct train could
  have had. 18 calls instead of 24, for more of what is wanted. SPEC §2.5.
- **A trip's arrival is the itinerary's, not the last rail leg's.** They
  differ whenever the journey continues by another mode, and the rail-leg
  reading always flatters: it called a 1 hr 7 min journey 42 minutes. Reach
  for `_terminal_time`, not `rail_legs[-1]`.
- **The stop list spells statuses differently from the board.** It writes
  `OnTime`; the board writes `on time`. Same field, same meaning, different
  spelling -- caught only because a recorded fixture asserted the parse. Its
  station names are a fourth vocabulary too: `Short Hills`, where the config
  flow stores `Short Hills Station`.
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
lands under new features, `fix:` under bug fixes. That is another reason to
get the prefix right.

**Labels affect the changelog section only, never the version.** Every release
is a patch bump. Mapping `enhancement` onto a minor bump the way a semver
project would means a `feat:` merged in August proposes `2026.9.0` -- a
September release, in August -- because in CalVer the minor position is the
month. `.github/release-drafter.yml` therefore has no major/minor label
mapping, and restoring one is not the fix for anything.

The autolabeler runs as its **own job** on `pull_request`. The combined
workflow silently never labelled anything: on a pull request the top-level
action gets `refs/pull/N/merge`, an ephemeral merge commit, so it forces
dry-run -- and dry-run suppresses label writes along with release writes. If
merged pull requests start arriving unlabelled again, check for
`forcing dry-run mode` in the Release Drafter log rather than the config.

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

The client is bundled rather than published to PyPI. **This integration is not
targeting Home Assistant Core**, so the packaging rule that would force `api/`
into a separate PyPI package does not apply.

The boundary stays anyway, and the test enforcing it stays. It is worth having
on its own merits — it keeps the reverse-engineered API surface testable
without a Home Assistant fixture, and it is the reason the traps in this file
can be verified against the live endpoint by a standalone script. If it fails,
do not add an exemption.

**Not submitting to core is not a lower quality bar.** Hold to what core would
ask for: full type coverage, a config flow with no YAML path, unique IDs on
every entity, no I/O in properties, translated strings, diagnostics, and tests
that would survive a core review. The reason to skip core is that the endpoint
is private and undocumented, not that the code should be looser.

## Before committing

Run the same checks CI runs, and make sure all three pass:

```sh
uv run coverage run -m pytest
uv run coverage report      # enforces fail_under = 97
uv run ruff check .
uv run ruff format --check .
uv run mypy .
```

`ruff format --check` is easy to skip because `ruff check` passes without it —
CI runs both in the same step, so skipping it fails the pull request on a
line-length reflow with everything else green.

`./scripts/mutation_check.sh` breaks one real behaviour at a time and checks
the suite notices. It is not part of CI and is not a routine step -- run it
after changing behaviour in `event.py`, `coordinator.py`, `binary_sensor.py`,
`track_history.py`, the Live Activity blueprint, or the card. Card mutations
are judged by vitest rather than pytest, via `run_card`; running the wrong
suite against a broken card passes and reads as a gap in the Python tests. Every gap it has found so
far was a fully covered line sitting under a test that could not fail:
`pick_favorite` never once matched a favourite, and the direct-only trip filter
was asserted only as "some trains resolved". A SKIP means a pattern went stale
and the entry needs rewriting.

**Blueprints are tested too, and they have to be.**
`tests/test_blueprint_live_activity.py` and `tests/test_blueprint_disruption.py`
copy the real blueprint into the test config, build an automation from it and
drive it, asserting on what reaches a mocked notify service. Every
blueprint bug this repository has had was invisible to the Python suite and
visible on a phone -- a countdown to a cancelled train, a notification on a day
off, `as_timestamp` raising on `unknown`. None of that is logic the integration
owns, so no amount of testing the integration would have found it.

Two things that harness needs. Set an `expected_lingering_timers` fixture,
because the Live Activity blueprint's five-minute refresh is a real registered
timer rather than a leak. And filter `clear_notification` out of the recorded
calls before asserting nothing was sent -- it fires on every quiet poll, so
counting it makes "sent nothing" unassertable.

**The analysis tool is tested too.** `tests/test_analysis_models.py` covers the
models and, more importantly, `score()` -- which was split out of `evaluate()`
precisely so the leave-one-day-out split could be asserted rather than trusted.
Its failure mode is not a crash but a number that looks like a result: an early
version handed each model the day it was being scored on and `m2 by
train+weekday` reported 100% top-1, which is indistinguishable from success on
the printed table.

Note the shape a leakage test needs. m4 falls back to its unfiltered ranking
when exclusion empties the list, so a fixture where the train has one candidate
track cannot tell a leak from correct behaviour -- the fallback rescues both.
Give it two.

**The example dashboard is tested too.** Lovelace has no load-and-drive path,
so `tests/test_dashboard.py` lifts each card out of the YAML and puts it
through Home Assistant's own template engine against states set by hand. These
cards are pure rendering and every bug they have had was a rendering bug, so
that is enough.

Cards are found by a signature in their content rather than by index, so
reordering the dashboard cannot silently point a test at a different card. One
test asserts the two views are identical apart from the commute prefix, because
nothing else makes a fix land in both -- and one asserts the dashboard's overdue
threshold still matches `TRACK_OVERDUE_LEAD`, because nothing else links those
two numbers and the constant has moved once already.

**The Lovelace card is a second toolchain.** `frontend/` holds TypeScript and
Lit; `custom_components/njtransit/frontend/njtransit-card.js` is the built
bundle, and it is **committed**, because HACS copies files and runs nothing.

```sh
cd frontend
npm ci
npm run typecheck
npm test          # vitest, jsdom
npm run build     # rewrites the committed bundle
```

Edit the card and forget the build and you ship the old behaviour with no
Python consequence at all, so CI rebuilds and fails on a diff. Run
`npm run build` and commit the result in the same change as the source.

The card is served by `frontend.py` rather than published as a separate HACS
plugin repository. HACS installs one category per repository, so a plugin
would be a second thing to install, a second thing to update, and a version
that can silently disagree with the entity attributes it reads. That choice is
why the manifest declares `frontend` and `http`, and why `requirements.txt`
pins `home-assistant-frontend` — `pytest_homeassistant_custom_component`
leaves it out, and without it every test that sets up an entry fails on `No
module named 'hass_frontend'`.

`TRACK_OVERDUE_MINUTES` in `frontend/src/pills.ts` must equal the
integration's `TRACK_OVERDUE_LEAD`; `tests/test_frontend.py` reads the
TypeScript source and fails if they drift.

**vitest cannot see the styling, so check it in a browser.** jsdom does not
compute `color-mix`, so nothing in the suite can tell you a pill is
unreadable. Render the card against hand-built states in a real page —
serve the built bundle, stub `ha-card` with `display: block` and a background
(page CSS cannot reach into the card's shadow root), and put a light and a
dark wrapper side by side. Both bugs the card has had were found this way and
by nothing else: a cancelled train being told its track was "not posted yet",
and an amber pill at 3.33:1.

Contrast is measurable from the page and worth measuring rather than eyeing.
Computed styles come back as `oklab(...)` once the sheet mixes in oklab, so a
naive `rgb()` parser silently returns the same number for every element —
which reads as "all fine". Convert oklab to linear sRGB, composite the
translucent tint over the card background, and take the ratio there. The
`--ink` percentages in the stylesheet are the output of doing that, not
preferences.

**Never write `\d` in a Jinja regex.** Jinja decodes string literals with
`unicode-escape`, so `'(\d+)'` raises "invalid escape sequence ... will not
work in the future". The disruption blueprint carried that for weeks: its
lateness debouncing would have quietly stopped matching on some future Python
and gone back to announcing on every poll. Use `[0-9]`, which needs no escape.
Running `uv run pytest -W error::DeprecationWarning` is what surfaces this.

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
