---
name: ha-njtransit-review
description: Reviews changes to the ha-njtransit Home Assistant integration. Use when reviewing a pull request, a branch, or uncommitted changes in this repository. Covers the undocumented NJ Transit GraphQL endpoint, the api/ layering boundary, and the fixture-based regression guards specific to this repo.
---

# Review ha-njtransit changes

Adapted from Home Assistant Core's `ha-review` and `ha-integration-knowledge`
skills (Apache-2.0). Modified for a HACS custom component: the quality-scale
machinery and core file paths do not apply, and the repository-specific
sections are new.

## Scope

Review the branch's changes plus any uncommitted ones against the base:

```sh
git diff "$(git merge-base origin/main HEAD)"
```

For a pull request, get context with `gh pr view` and `gh pr diff` first.

**Review only — do not change the code being reviewed.** Report in the console
by default; post to GitHub only when asked. Do not spawn subagents; this
repository is small enough to review directly.

Read [SPEC.md](../../../SPEC.md) before reviewing anything in `api/`. Several
decisions there look wrong until you know what was tried and measured.

## 1. GraphQL field selections — check this first

The endpoint has a schema/data mismatch that makes widening a selection
actively dangerous: a field that is non-nullable in the schema but null in the
data **nulls the entire response**, not just that field.

Flag any change that:

- adds a field to a query in `api/queries.py` without evidence the server
  populates it — "the schema has it" is not evidence, `Station.latitude` has it
  and returns null
- builds a query string by interpolation rather than named operation +
  `variables`. Inline arguments are rejected by the WAF with a non-GraphQL
  `{"status":400,"message":"Malformed request"}`
- introduces a shared or generic field selection reused across queries. The
  same type returns different populated fields depending on which root field
  reached it

## 2. Fixtures are evidence

`tests/fixtures/` is a coherent capture from a live disruption, every query
within the same minute.

**Flag any diff that modifies a fixture.** The legitimate reasons are narrow:
a genuine upstream API change, which deserves its own PR with a fresh coherent
capture and a SPEC.md note. Regenerating a fixture so a test passes is
inverting the test.

Specifically check that these still hold:

- the disruption binary sensor fires on train `6320` — cancelled on the board,
  absent from the alert feed. This is the integration's reason to exist.
- the Short Hills → NY Penn paging test still asserts **51 trains, not 4**.
  That number guards a bug the spec itself had: the planner returns exactly 3
  trips per call, so a single query silently yields a near-empty train set.

## 3. Layering

`custom_components/njtransit/api/` must not import `homeassistant`. There is a
test enforcing this. If a change makes that test fail, the fix is to move the
HA-facing logic out of `api/`, never to add an exemption.

Watch for subtler violations the test cannot catch: `api/` reaching for HA's
shared aiohttp session instead of the injected one, raising
`HomeAssistantError` or `UpdateFailed` instead of the module's own exceptions,
or importing HA constants "just for the string value".

## 4. Parsing robustness

This API's vocabularies are undocumented and assumed incomplete. Flag parsing
code that:

- raises on an unrecognized status, colour, or line code instead of degrading
  to `UNKNOWN` while preserving the raw value
- compares status strings case-sensitively — `Cancelled` and `CANCELLED` both
  appear in a single response
- treats `trainID` as numeric. Trenton's board carries Amtrak `A79`
- does naive date arithmetic on the bare wall-clock times. There is no date and
  no zone in the payload; everything is `America/New_York` with explicit
  midnight rollover, and DST needs `fold` handling
- conflates "no realtime data yet" with "zero delay". An empty `status` means
  the former and must stay `None`

## 5. Station names

Three vocabularies exist for the same station, and the planner's tolerance
varies per station — `Hoboken` works where `Short Hills` does not. Flag any
code that assumes one name works across queries, or that infers a naming rule
from a single station that happened to work.

Remember that a wrong name and a genuine no-service result are the same generic
error, so a change that swallows it will be silent in exactly the case users
report.

## 6. Home Assistant conventions

- Entity `unique_id` present and stable; commute entries key on the
  origin/destination **pair**, not the origin alone
- Coordinators shared via `hass.data` are refcounted — check both directions:
  unloading one of two commutes sharing an origin must not tear down the shared
  board coordinator, and unloading the last one must
- `unknown` vs `unavailable` used correctly. No matching departures overnight
  is `unknown` — the integration is working fine
- Blocking I/O off the event loop; no `time.sleep`, no sync `requests`
- New user-visible strings present in both `strings.json` and
  `translations/en.json`

## 7. Verification traps

- Run `mypy .`, not `mypy custom_components/njtransit` — errors in `tests/` are
  otherwise easy to miss, and CI checks the whole tree.
- A test suite that passes locally but hits the network is not passing. Check
  that new tests use recorded fixtures rather than live calls; a live-calling
  test will fail in CI or, worse, pass flakily depending on NJ Transit's
  current service state.
- If the claim is "verified against the live API", check whether the diff
  actually shows that, and be specific in the review about which parts were
  exercised against fixtures versus the real endpoint.

## Posting a review

When asked to post, use `gh pr review`. Lead with what is correct, then
findings ordered by severity. Distinguish blocking issues from suggestions.
Quote the specific line for each finding.
