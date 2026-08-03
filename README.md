# NJ Transit for Home Assistant

[![HACS Custom][hacs-shield]][hacs]
[![Build and Test][build-shield]][build]

A Home Assistant integration for NJ Transit rail departures and service alerts,
scoped to a commute you actually take.

> [!WARNING]
> This uses the private GraphQL endpoint behind njtransit.com. There is no
> official API, no documentation, and no compatibility promise. It can break
> without notice. See [Reliability](#reliability).

## Why not just a REST sensor?

Because **neither NJ Transit feed tells you the whole story.** Captured during
a real Morris & Essex disruption:

- The system status feed reported live alerts for trains 6612, 6607, 6324,
  6311 and 6610.
- The Short Hills departure board at the same moment showed train **6320 to New
  York as cancelled** — mentioned nowhere in the alert feed.

Neither feed is a superset of the other, and the alert feed writes train
numbers into free prose (`M and E train #6607, the 7:07 AM departure...`).
Getting a dependable "is my commute broken?" signal means merging both and
correlating on train number, which is unpleasant in a Jinja template and
unremarkable in Python.

## What you get

Each config entry is a **commute** — an origin and a destination — so
`Short Hills → New York Penn` and `Short Hills → Hoboken` coexist, as do
reverse-direction entries for the trip home. Commutes sharing an origin share
one departure-board poll.

| Entity | What it tells you |
|---|---|
| `sensor.<commute>_next_departure` | Next usable train, as a real timestamp |
| `sensor.<commute>_departure_2` … | The ones after that |
| `sensor.<commute>_delay` | Minutes late, computed against the timetable |
| `sensor.<commute>_crowding` | Per-car crowding, front/middle/back |
| `sensor.<line>_alerts` | Live incidents on your line |
| `sensor.<line>_advisories` | Planned advisories (track work, event service) |
| `binary_sensor.<commute>_commute_disrupted` | The merged signal |
| `calendar.<commute>` | Scheduled departures |

"Next usable train" is filtered by the trains that genuinely serve your
origin/destination pair, resolved from the trip planner — so it includes trains
requiring a transfer (a Gladstone train to Summit, connecting onward) and
excludes board rows whose label merely mentions New York.

## Installation

### HACS

Add this repository as a custom repository (category: Integration), install
**NJ Transit**, and restart Home Assistant.

### Manual

Copy `custom_components/njtransit` into your Home Assistant `config` directory
and restart.

## Configuration

Settings → Devices & Services → Add Integration → **NJ Transit**. Pick an
origin and destination station. Add the integration again for each additional
commute.

Options (per commute): poll intervals, how many upcoming departures to expose,
and the delay threshold and lookahead window for the disruption sensor.

## Reliability

The endpoint is private and undocumented, so this integration is built
defensively: GraphQL field selections are pinned rather than broad, unknown
status values degrade instead of raising, and the test suite runs against
recorded real-world payloads so upstream drift surfaces as a test failure
rather than a broken install.

That reduces the blast radius. It does not eliminate it. If NJ Transit changes
the endpoint, this will need updating.

The calendar reflects the **timetable**, not realtime — a cancelled train still
appears as an event, though cancellations are folded into the summary for
departures close enough to be on the live board.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). If you're working on this with an AI
agent, [AGENTS.md](AGENTS.md) and [SPEC.md](SPEC.md) carry the context that
isn't obvious from the code.

## Disclaimer

Not affiliated with, endorsed by, or supported by NJ Transit.

[hacs]: https://github.com/hacs/integration
[hacs-shield]: https://img.shields.io/badge/HACS-Custom-41BDF5.svg
[build]: https://github.com/dknowles2/ha-njtransit/actions/workflows/build-and-test.yml
[build-shield]: https://github.com/dknowles2/ha-njtransit/actions/workflows/build-and-test.yml/badge.svg
