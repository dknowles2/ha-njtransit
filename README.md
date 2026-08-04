# NJ Transit for Home Assistant

[![HACS Custom][hacs-shield]][hacs]
[![Build and Test][build-shield]][build]

A Home Assistant integration for NJ Transit rail departures and service alerts,
scoped to a commute you actually take.

> [!WARNING]
> This uses the private GraphQL endpoint behind njtransit.com. There is no
> official API, no documentation, and no compatibility promise. It can break
> without notice. See [Reliability](#reliability).

## Contents

- [Why a commute, not a station](#why-a-commute-not-a-station)
- [Installation](#installation)
- [Configuration](#configuration)
- [Options](#options)
- [Entities](#entities)
- [Automation examples](#automation-examples)
- [Dashboard notes](#dashboard-notes)
- [Reliability](#reliability)
- [Troubleshooting](#troubleshooting)

## Why a commute, not a station

Each config entry is an **origin and a destination**, not just a station. That
is what lets the integration answer "is my commute broken?" rather than "what
is on the board?"

The difference matters because **neither NJ Transit feed is a superset of the
other.** During a recorded Morris & Essex disruption, the alert feed named
trains 309, 6311, 6324 and 6607, while the departure board simultaneously
showed train 6320 cancelled — and said nothing about it in any alert. Watching
either feed alone misses real problems. This integration correlates the two on
train number, which is its reason to exist.

Commutes sharing an origin share a single departure-board poll, so adding
`Short Hills → New York Penn` and `Short Hills → Hoboken` costs one request per
interval, not two.

### Which trains count

"Next usable train" is not simply the top of the board. Departures qualify if
**either**:

1. The trip planner says the train serves your origin/destination pair, or
2. The board's destination label shares a significant word with your
   destination.

Either is enough, deliberately. The planner alone goes stale and would have
dropped the cancelled train 6320 — exactly the failure the integration exists
to prevent. The label alone misses transfer itineraries.

Only **direct, one-seat rides** are surfaced. A Hoboken-bound train that
reaches Penn Station via a change at Newark Broad Street is a genuine way to
travel, but the board has no way to tell you where you change or that you must,
so a row headsigned for somewhere you are not going is worse than a row that is
missing. For Short Hills → New York Penn that is roughly 23 trains a day, with
18 more reachable only by changing.

Where a station pair has **no** direct service at any hour, the filter fails
open and transfer itineraries return — an empty board would read as "no trains"
rather than "no direct trains".

## Installation

### HACS

1. HACS → ⋮ → **Custom repositories**
2. Add `https://github.com/dknowles2/ha-njtransit`, category **Integration**
3. Install **NJ Transit**, then restart Home Assistant

### Manual

Download `njtransit.zip` from the [latest release][releases] and extract it into
`config/custom_components/njtransit/`, then restart Home Assistant.

The archive extracts to the integration directory directly — `manifest.json`
should end up at `config/custom_components/njtransit/manifest.json`.

## Configuration

Settings → Devices & Services → **Add Integration** → **NJ Transit**.

Pick an **origin** and, optionally, a **destination**. Add the integration
again for each additional commute, including the reverse direction for the trip
home.

Station names come from NJ Transit's own list and must be used verbatim — most
end in `Station` or `Terminal`, but `MetLife Stadium` and
`Penn Station New York` do not. The config flow presents them as a dropdown, so
there is nothing to type.

### Omitting the destination

An entry with no destination reports the **whole board** for that station,
unfiltered, and no calendar is created. Useful for a station you pass through
rather than commute along. The destination is part of an entry's identity and
cannot be changed later — add another commute instead.

## Options

Settings → Devices & Services → NJ Transit → the entry → **Configure**. Per
commute, so directions can differ.

| Option | Default | Range | Notes |
|---|---|---|---|
| Departure board poll interval | 60 s | 30–3600 | NJ Transit caches for 30 s; polling faster gains nothing |
| Service alert poll interval | 120 s | 30–3600 | |
| Upcoming departures to show | 3 | 1–10 | Number of `departure_*` sensors created |
| Delay before disrupted | 10 min | 1–60 | How late counts as disrupted |
| Lookahead | 90 min | 15–240 | Departures beyond this are ignored by the disruption sensor |
| Favorite trains | none | — | Picked from the day's direct trains, labelled by departure time |

Changing options reloads the entry, so entities go `unavailable` for a few
seconds.

**Lookahead is the option to reach for first if alerts feel wrong.** At 90
minutes the disruption sensor reports on trains an hour and a half out, which
during a commute window can mean trains you were never going to take.

## Entities

`<commute>` below is the entry title slugified, e.g.
`short_hills_station_to_new_york_penn_station`.

### `binary_sensor.<commute>_commute_disrupted`

`device_class: problem`. **The entity this integration exists for.** On when
any of three independent conditions holds for a departure inside the lookahead
window:

1. A train is cancelled on the board.
2. A train is running at or past the delay threshold.
3. A train is named in a live alert.

The third is what a departure board alone cannot give you; the first is what an
alert feed alone cannot.

| Attribute | Description |
|---|---|
| `reasons` | Human-readable list, e.g. `Train 6320 (8:12 AM) is cancelled` |
| `affected_trains` | Train IDs behind those reasons |
| `upcoming_trains` | Every train in the lookahead window |
| `delay_threshold` | The configured threshold, in minutes |
| `lookahead_minutes` | The configured window |

`reasons` is the attribute to build automations on — see
[Automation examples](#automation-examples).

### `sensor.<commute>_next_departure`, `sensor.<commute>_departure_2`, …

`device_class: timestamp`. The state is the scheduled departure as a real
timestamp, so `as_timestamp()` and relative-time rendering work.

The index is stable in a way a raw board position is not: `departure_2` is
always the second train you could actually take, not whatever happens to be
second on the screen.

The state is `unknown`, not `unavailable`, when no more trains run today — the
integration is working, there is simply no train.

| Attribute | Description |
|---|---|
| `train_id` | A **string**, not a number — Trenton's board carries Amtrak IDs like `A79` |
| `destination` | Headsign text, e.g. `New York -SEC` |
| `line` | e.g. `Morristown Line` |
| `track` | Platform, or `null` until assigned |
| `status` | Normalized: `on_time`, `delayed`, `cancelled`, `boarding`, `all_aboard`, `departed`, `unknown` |
| `status_raw` | The board's own text, e.g. `in 21 Min`. Empty until realtime data exists |
| `status_text` | One phrase combining status and delay — see below |
| `delay_minutes` | Minutes late, or `null` when nothing is known |
| `inline_message` | Per-train note from the board |
| `crowding` | Worst level across the consist |
| `cars` | Per-car number, position and crowding |
| `alerts` | Alert messages naming this train |

**`status_text`** is the one to display. `status` and `delay_minutes` are
separate and neither is sufficient alone — the enum cannot say *how* late, and
the delay is `null` for a cancelled train:

| `status` | `delay_minutes` | `status_text` |
|---|---|---|
| `cancelled` | anything | `Cancelled` |
| `delayed` | 22 | `22 min late` |
| `boarding` | `null` | `Boarding` |
| `on_time` | 0 | `On time` |
| `on_time` | `null` | `""` |

Empty rather than `On time` when there is no realtime data yet, which is normal
for departures more than about an hour out. **Nothing being known is not the
same as knowing the train is punctual** — the same reason `delay_minutes` is
nullable.

### `event.<commute>_train_event`

Discrete things that happen to a train, as opposed to the binary sensor's
"is it broken right now". Event types:

| Type | Fires when |
|---|---|
| `cancelled` | A train becomes cancelled |
| `delayed` | A train crosses the delay threshold |
| `track_changed` | A train is moved **after** a track was published |
| `alerted` | A train is newly named in a live alert |

Only transitions fire. An ongoing problem does not re-fire every poll, and
nothing fires for the state that existed at startup — otherwise every restart
would replay the morning's problems.

A first track assignment is deliberately not a `track_changed`; the board
simply had no track yet. Being *moved* is the actionable case, and the event
carries `previous_track` alongside the new one.

Each event carries `train_id`, `scheduled`, `destination`, `track`,
`status_text` and `delay_minutes`, so an automation can act without a second
lookup.

This is the entity to build on rather than diffing `reasons` by hand.

### `sensor.<commute>_next_favorite`

`device_class: timestamp`. When the next train **you actually catch** leaves,
as opposed to whichever usable train is soonest.

Set the **Favorite trains** option to a list of train numbers. NJ Transit
numbers are stable across weekdays — the 7:48 from Short Hills is 6662 every
weekday — and they are what alerts name, so they correlate cleanly.

`unknown` when no favourite runs again today, and when no favourites are
configured. An empty list means "not using this", not "every train qualifies".

Carries the same attributes as the numbered departure sensors, so an
automation need not care which entity it read a train from, plus:

| Attribute | Description |
|---|---|
| `favorites` | The configured train numbers, normalized |

Every departure sensor also gains a `favorite` boolean, so a dashboard can
highlight your train in a list without a second lookup.

### `sensor.<commute>_delay`

`device_class: duration`, minutes. How late the next usable train is running.

`unknown` when the board has no realtime data for that train yet. Reporting
zero would claim the train is on time when nothing is actually known.

### `sensor.<commute>_crowding`

`device_class: enum`: `light`, `moderate`, `heavy`, `unknown`. The worst level
across the consist, so it errs toward warning you.

The `positions` attribute maps `front` / `middle` / `back` to their levels,
which makes "sit at the back" answerable. Absent for most departures — the
board only carries consist data for imminent ones.

### `sensor.<commute>_service_alerts` and `sensor.<commute>_planned_advisories`

Count of alerts on the lines this commute runs on. Live incidents and planned
advisories are separate entities because they want different reactions: one
means leave now, the other means remember it next weekend.

| Attribute | Description |
|---|---|
| `messages` | Alert text |
| `urls` | Links to full advisories, where provided |
| `lines` | Line codes the alerts cover |
| `train_ids` | Every train named across the alerts |
| `affects_my_trains` | The subset that are on **your** board right now |

`affects_my_trains` is the useful one — it is the intersection the disruption
sensor is built from.

### `calendar.<commute>_departures`

Scheduled departures for today and tomorrow, one event per train.

- **Summary** — `Train 6328 to New York Penn Station`, prefixed
  `CANCELLED — ` when the live board says so
- **Description** — scheduled journey time, plus `Change trains:` where an
  itinerary requires it
- **Location** — the origin station
- **Start / end** — scheduled departure and arrival

The calendar is the **timetable**, not realtime. A cancelled train still
appears as an event; the cancellation is folded into the summary only for
departures close enough to be on the live board.

Not created for entries without a destination.

## Automation examples

### Blueprint: commute disruption alert

[![Import blueprint][import-shield]][import]

The alert automation is packaged as a blueprint. Import it with the badge
above, or Settings → Automations & Scenes → Blueprints → **Import Blueprint**
and paste:

```
https://github.com/dknowles2/ha-njtransit/blob/main/blueprints/automation/njtransit/commute_disruption.yaml
```

> [!NOTE]
> HACS won't install this — a HACS repository has one category, and this one
> is `integration`. Import by URL instead; it works from any repository.

Three inputs: the **commute** to watch, an optional **schedule** limiting it to
hours you actually travel, and the **actions** to run. Your actions get two
variables:

| Variable | Contents |
|---|---|
| `fresh_reasons` | List of newly appeared problems |
| `commute_name` | e.g. `Short Hills Station to New York Penn Station` |

A typical notification body is `{{ fresh_reasons | join('\n') }}`.

One automation per direction. How late a train must be before it counts is the
**delay before disrupted** option on the integration entry, not a blueprint
input — `reasons` is generated from it.

### Blueprint: favorite train Live Activity

[![Import blueprint][import-live-shield]][import-live]

Puts your next favourite train on the iPhone Lock Screen and Dynamic Island as
departure approaches, using [Live Activities][live-activities]. Requires
iOS 17.2+ and Home Assistant 2026.7 or later.

Set the commute's **Favorite trains** option first — this blueprint reads
`sensor.<commute>_next_favorite`, which is `unknown` until there is a favourite
to report.

The activity appears once the train is inside the lead time, refreshes when its
status or track changes, and clears after departure. Updates reuse the same
`tag`, so they are silent.

The countdown is a `chronometer`, which ticks on the phone. That is why the
automation does not push once a minute — only a real change needs sending, and
iOS rate-limits Live Activity updates.

**Dismissing it.** Live Activities cannot carry buttons, and iOS does not
report when you swipe one away, so a plain notification with an **On board**
button is sent alongside the first one. Tapping it ends the activity and stays
quiet for the rest of the window — without it, a second favourite departing
soon after the one you actually caught starts counting down at you on the
train.

That needs somewhere to remember you boarded, so the blueprint takes an
`input_boolean` helper. It is reset when the commute schedule ends, or at 3am
if you are not using one. Leave the input empty to skip the button; the
activity still appears and still clears after departure.

### Or write it yourself

Trigger on the `reasons` **attribute**, not on the sensor turning on. If a
second train fails during an ongoing disruption the sensor is already `on`, and
a state trigger stays silent. Diffing the attribute also means a problem
clearing does not notify, since the list only shrinks.

```yaml
- alias: NJ Transit - commute disruption alert
  mode: queued
  max: 5
  triggers:
    - trigger: state
      entity_id: binary_sensor.short_hills_station_to_new_york_penn_station_commute_disrupted
      attribute: reasons
  variables:
    previous: >-
      {{ (trigger.from_state.attributes.reasons
          if trigger.from_state is not none else []) or [] }}
    current: >-
      {{ (trigger.to_state.attributes.reasons
          if trigger.to_state is not none else []) or [] }}
    fresh: "{{ current | reject('in', previous) | list }}"
  conditions:
    # from_state is None on restart and on a manual run, where "new" is
    # meaningless. Without this, restarting mid-disruption re-alerts.
    - condition: template
      value_template: >-
        {{ trigger is defined and trigger.from_state is not none
           and fresh | count > 0 }}
    - condition: state
      entity_id: schedule.morning_commute
      state: "on"
  actions:
    - action: notify.mobile_app_your_phone
      data:
        title: 🚆 Morning commute disrupted
        message: "{{ fresh | join('\n') }}"
```

To alert on delays as small as five minutes, lower the **delay before
disrupted** option rather than changing this automation — `reasons` is
generated from it.

### Announce the next train when you ask

```yaml
- alias: NJ Transit - next train
  triggers:
    - trigger: conversation
      command:
        - when is the next train
  actions:
    - set_conversation_response: >-
        {% set s = states.sensor.short_hills_station_to_new_york_penn_station_next_departure %}
        {% if s.state in ['unknown', 'unavailable'] %}
          No more trains today.
        {% else %}
          Train {{ s.attributes.train_id }} at
          {{ (s.state | as_datetime | as_local).strftime('%-I:%M') }}
          {%- if s.attributes.status_text %}, {{ s.attributes.status_text }}{% endif %}.
        {% endif %}
```

## Dashboard notes

The departure sensors are timestamps, so Home Assistant's native entity rows
render them as live relative time ("in 6 minutes") that ticks on its own clock.

If you build a table instead, compute minutes-until from the state — but note
it refreshes when an entity changes, not on a clock, so it updates roughly
every poll interval rather than every minute.

Display `status_text` rather than deriving your own from `status` and
`delay_minutes`; that is what it is for.

## Reliability

The endpoint is private and undocumented, so this integration is built
defensively:

- GraphQL field selections are **pinned**, not broad. Requesting a field the
  server cannot populate nulls the *entire* response, not just that field.
- Unknown status values degrade to `unknown` rather than raising. The
  vocabulary is undocumented and assumed incomplete.
- Times are bare wall-clock strings with no date and no zone; everything is
  resolved in `America/New_York` with explicit midnight-rollover handling.
- The test suite runs against **recorded real-world payloads**, including a
  live disruption, so upstream drift surfaces as a test failure rather than a
  broken install.

That reduces the blast radius. It does not eliminate it. If NJ Transit changes
the endpoint, this will need updating.

## Troubleshooting

**Everything is `unavailable` after a config change.** Options changes reload
the entry. Give it a few seconds.

**A train appears that is headed somewhere else.** Check the trip planner
before assuming it is a bug — a Hoboken-headsigned train can reach Penn Station
via Newark Broad Street ahead of the direct one. Only direct trains are shown
by default, so this should be rare.

**A departure shows no status.** `status_raw` is empty and `delay_minutes` is
`null` until the board has realtime data, which is normal beyond about an hour
out.

**The calendar shows a train that was cancelled.** The calendar is the
timetable. Cancellations are folded in only for departures on the live board.

**Something else.** Download diagnostics from the integration's device page —
Settings → Devices & Services → NJ Transit → the device → ⋮ → **Download
diagnostics**. It contains no credentials, because the endpoint takes none.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). If you're working on this with an AI
agent, [AGENTS.md](AGENTS.md) and [SPEC.md](SPEC.md) carry the context that
isn't obvious from the code — the API's traps in particular.

## Disclaimer

Not affiliated with, endorsed by, or supported by NJ Transit. Train and
schedule data belongs to NJ Transit; the artwork shipped with this integration
is original and is not their logo or wordmark.

[hacs]: https://github.com/hacs/integration
[hacs-shield]: https://img.shields.io/badge/HACS-Custom-41BDF5.svg
[build]: https://github.com/dknowles2/ha-njtransit/actions/workflows/build-and-test.yml
[build-shield]: https://github.com/dknowles2/ha-njtransit/actions/workflows/build-and-test.yml/badge.svg
[releases]: https://github.com/dknowles2/ha-njtransit/releases/latest
[import]: https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Fdknowles2%2Fha-njtransit%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fnjtransit%2Fcommute_disruption.yaml
[import-shield]: https://my.home-assistant.io/badges/blueprint_import.svg
[import-live]: https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Fdknowles2%2Fha-njtransit%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fnjtransit%2Ffavorite_live_activity.yaml
[import-live-shield]: https://my.home-assistant.io/badges/blueprint_import.svg
[live-activities]: https://companion.home-assistant.io/docs/notifications/live-activities/
