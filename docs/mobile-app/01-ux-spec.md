# NJ Transit Rail — Mobile App UX Spec v0.1

Status: draft, for engineering + design review
Scope: iOS first, Android second. Standalone native app — no Home Assistant
dependency at runtime.

This document is derived entirely from the domain knowledge already proven
out in the `ha-njtransit` Home Assistant integration: its README, SPEC.md,
the `api/models.py` data vocabulary, the `njtransit-departures` Lovelace
card, the phone dashboard YAML, and the two automation blueprints
(commute-disruption alerts and the favorite-train Live Activity). That
corpus took months of observed API behavior to get right — null-vs-zero
delay, the late-track signal, fail-open filtering, restart-safe events — and
this spec's job is to carry those lessons into a from-scratch product
without re-learning them the hard way.

Where the app needs something the underlying feeds cannot supply, that is
called out explicitly rather than quietly assumed. See §12.

---

## 1. Design principles

Each principle below is something the integration learned by getting it
wrong first, or by a rider telling us what they actually do. The app should
inherit the lesson, not just the feature.

1. **A commute is the unit, not a station.** The whole product is organized
   around an origin/destination pair, because that is what lets it answer
   "is my commute broken?" instead of "what is on the board?" A station
   alone can't correlate a delay to whether it matters to you.
   *(README, "Why a commute, not a station"; SPEC §8.0.)*

2. **Neither feed is a superset of the other, so both get read.** The
   recorded disruption that this whole project is built around: the alert
   feed named trains 309, 6311, 6324, 6607, while the board simultaneously
   showed train 6320 cancelled and said nothing about it in any alert.
   "Is my train ok" has to merge the board and the alerts and correlate on
   train number — reading either alone will miss something real.
   *(SPEC §1; README intro.)*

3. **Nothing known is not the same as on time.** A departure more than
   about an hour out has no realtime data yet, and the app must show that
   as blank/neutral, never as "On time." Claiming punctuality you haven't
   observed is a small lie that erodes trust the first time it's wrong.
   *(models.py `Departure.status_text`; SPEC "empty rather than On time.")*

4. **Never cry wolf.** A card/screen is tinted or badged by the worst thing
   currently true of the train it's showing — not by anything already
   routed around. A cancellation the app has already fallen back past does
   not turn the hero red; that would make the warning color mean nothing by
   the third time it's wrong.
   *(README, departures card section; `pills.ts` `cardMood`.)*

5. **Read at arm's length.** The primary number on the primary screen has
   to be legible from a few feet away, on a platform, without reading
   glasses. Everything else — track, status, crowding — is secondary and
   smaller. The countdown itself is never colored by status; tinting the
   number you're straining to read makes it harder to read, not easier.
   *(README; `departures-card.ts` CSS comment on `.countdown`.)*

6. **Fail open, always.** Every filter and gate in the existing system
   defaults to *showing more* when it can't be sure, never to *showing
   less*: an unmatched line name means every alert for that mode is shown
   rather than none; a station/pair with no direct service falls back to
   transfer listings rather than an empty board; a Live Activity with no
   location data shows anyway. A suppressed notification is invisible —
   you don't find out it didn't fire until you've missed the train.
   *(SPEC §2.7, §6.4; blueprint, "It fails open, everywhere.")*

7. **Only transitions notify, and nothing re-fires on restart/relaunch.**
   An ongoing problem doesn't re-announce itself every poll, and whatever
   was already true when the app starts tracking a train is the baseline,
   not news. Otherwise every cold start would replay the whole morning's
   problems.
   *(event.py, `TrainEvent`; commute_disruption.yaml, the `from_state is
   not none` guard.)*

8. **Direct trains only, by default.** A row that tells you to board a
   train headed somewhere you're not going — because it becomes a one-seat
   ride only through a transfer the app can't describe — is worse than a
   row that's simply missing. Transfer itineraries are a real way to
   travel, but the feed can't say where you change or that you must, so
   they're excluded unless nothing direct exists at all.
   *(SPEC §2.7.)*

9. **A hedge that can be wrong is labeled as a hedge.** The signaling
   system's track guess is right 229 times out of 231, but it's still
   shown as `Track 4 (likely)`, not `Track 4` — because a number that
   silently flips is worse than one that told you it might. The board
   always wins the moment it posts, even if it disagrees.
   *(SPEC §2.9; `pills.ts` `trackPill`.)*

10. **A late track is a signal in its own right, not a guess about which
    track.** The integration doesn't predict *which* platform — the data
    can't support that at anywhere near a useful hit rate (SPEC §7.2) —
    but it can tell you, from direct observation, that a track hasn't been
    posted when it normally would be by now. That's actionable on its own
    terms regardless of what it turns out to correlate with.
    *(SPEC §7.3; a rider's own words: "any time I observe a track
    assignment not happening by the T-10 mark I know I'm in for a rough
    commute.")*

11. **Only push what changed, and only what's on screen.** A countdown that
    ticks on the device doesn't need a push every minute; a push is earned
    by a real change to a field the user can actually see (track, status,
    crowding, train id). Pushing an identical face wastes the one channel
    that's supposed to mean "look now."
    *(blueprint `favorite_live_activity.yaml`, `changed` variable and its
    comment.)*

12. **Location answers "could you still catch this train," not "are you
    home."** Zones/radius gating exists to suppress a countdown you can't
    act on (already aboard, working from home), never to guess whether
    today is a commute day — that's what a schedule is for, because "home"
    looks identical whether you're about to leave for the train or not
    going in at all.
    *(blueprint, "The rule is one line... it covers three separate
    annoyances.")*

13. **An estimate that could be wrong in either direction is reported as a
    lower bound.** Once a train's left, its arrival estimate is scheduled
    time plus however late the last stop shows — never "it'll make up
    time." That's a number someone reads while deciding whether to run for
    a connection.
    *(models.py `TrainRun.minutes_late`/`eta_at` docstrings.)*

14. **An inference is labeled as an inference.** A cancelled train just
    ahead of yours on the same line, which didn't get its own alert,
    *probably* means your train is about to make its stops and get
    crowded — but that's a hypothesis about your journey, not a fact,
    and the copy has to say so.
    *(README/SPEC §9, `line_cancellation`: "It is an inference, not a fact
    about your journey.")*

15. **No destination means "show me everything," not "broken."** A station
    with no paired destination isn't a degraded commute — it's a different
    mode entirely (a board, not a commute), with no disruption concept, no
    favorites, and no notifications, because none of those are meaningful
    without a destination to filter against.
    *(README, "Omitting the destination.")*

16. **A quiet field stops being read.** Crowding, in particular, only says
    something when standing somewhere else on the train would actually
    help — three cars all "light" is three facts and no decision, so it's
    withheld entirely rather than printed as clutter.
    *(`pills.ts` `emptiestCars` comment.)*

---

## 2. Information architecture

### Tabs (bottom nav, iOS and Android)

```
┌─────────┬─────────┬─────────┬─────────┐
│  Home   │ Commutes│ Alerts  │Settings │
│   🚆    │   🔀    │   🔔    │   ⚙️    │
└─────────┴─────────┴─────────┴─────────┘
```

- **Home** — the commute currently "in play": hero countdown, board, and
  (when riding) the onboard arrival countdown. Defaults to whichever
  commute matches the active commute window and time of day; falls back to
  the most recently used one outside any window.
- **Commutes** — the list of configured commutes and boards (§4.5),
  direction switcher, add/remove.
- **Alerts** — every live incident and planned advisory across all
  configured commutes, "affects my trains" first (§4.3).
- **Settings** — account, per-commute options, notification and Live
  Activity configuration, about/diagnostics.

A commute with **no destination** ("board mode," principle 15) still lives
in the Commutes tab and can be opened into a reduced Home-like screen, but
it never becomes the default Home tab content and never appears in Alerts'
"affects my trains" grouping, because there's no destination to filter
alerts against.

### Screen inventory

| Screen | Reachable from |
|---|---|
| Onboarding flow (§3) | first launch |
| Home / Commute (§4.1) | Home tab |
| Departure detail (§4.2) | tap a row/hero on Home, a notification, a widget |
| Alerts (§4.3) | Alerts tab, notification tap on `alerted`/`line_cancellation` |
| Commutes list (§4.4) | Commutes tab |
| Board mode (§4.5) | Commutes tab, a no-destination entry |
| Favorites picker (§4.6) | Settings → a commute → Favorite trains |
| Settings — global (§4.7) | Settings tab |
| Settings — per commute (§4.7) | Commutes list → a commute → ⋮ → Settings |
| Onboard mode (§4.8) | automatic, once a tracked train departs; also reachable from Home while riding |
| Empty/degraded states (§4.9) | inline, wherever they apply |

### Deep-link targets

```
njtransit://commute/{commuteId}                  → Home, scoped to that commute
njtransit://commute/{commuteId}/departure/{trainId} → Departure detail
njtransit://commute/{commuteId}/onboard          → Onboard mode (redirects to
                                                    Home if not currently riding)
njtransit://commute/{commuteId}/board            → Board mode (no-destination entries)
njtransit://alerts                               → Alerts tab
njtransit://alerts/{commuteId}                   → Alerts, filtered to one commute
njtransit://settings/{commuteId}                 → Per-commute settings
```

Notification taps route by event type (full mapping in §5): `cancelled`,
`delayed`, `track_changed`, and `track_overdue` open Departure detail for
that train; `alerted` and `line_cancellation` open Alerts, scoped to the
commute; a Live Activity tap opens Home (or Onboard mode, if riding).
Widgets (§6) deep-link the same way a notification would for their size
class.

---

## 3. Onboarding

```
1. Welcome            2. Location            3. Origin + destination
┌─────────────────┐   ┌─────────────────┐   ┌─────────────────┐
│                 │   │                 │   │ Where do you     │
│   🚆 NJ Transit  │   │  Find your      │   │ commute?          │
│    for riders    │   │  home station?  │   │                   │
│                 │   │                 │   │ FROM               │
│  Know before you │   │ We'll suggest   │   │ [Short Hills   ▾] │
│  reach the       │   │ the nearest     │   │                   │
│  platform.       │   │ station using   │   │ TO                │
│                 │   │ your location,  │   │ [New York Penn ▾] │
│                 │   │ once. We don't  │   │                   │
│  [Get started]   │   │ track you.      │   │ We'll set up both │
│                 │   │                 │   │ directions —      │
│                 │   │ [Allow Once]    │   │ morning and        │
│                 │   │ [Not now]       │   │ evening.           │
│                 │   │                 │   │                   │
│                 │   │                 │   │ Don't have a      │
│                 │   │                 │   │ destination?      │
│                 │   │                 │   │ [Just show me     │
│                 │   │                 │   │  the board →]     │
└─────────────────┘   └─────────────────┘   └─────────────────┘
```

**Step 1 — Welcome.** No permission asks yet. Sets expectations: this is
for a commute you actually take, not a general timetable browser.

**Step 2 — Location, one-time.** Used only to rank the station picker by
distance and pre-select the nearest one, exactly like the integration's
setup-time suggestion. Framed as "Allow Once" (iOS precise-location one-shot
equivalent), never "Always" — the app has no ongoing need for background
location at this point in the flow. If denied or unavailable, the picker
still works; it just isn't pre-sorted, and starts on a plain alphabetical
list. This mirrors "it disappears silently if home is unset or the lookup
fails, because a convenience must never be able to block setup."
*(README, "Setup suggests your station.")*

**Step 3 — Origin + destination, both directions in one step.** A single
picker asks for FROM and TO. Submitting creates **two** commutes at once —
`FROM → TO` and `TO → FROM` — because a commuter needs both legs and asking
twice is friction the existing product doesn't have (the HA integration
requires two separate config entries only because that's how HA entries
work; the app should not carry that limitation forward). Station names are
presented as a picker, never free text — the underlying vocabulary has
alias rows and inconsistent suffixes that make typed input unreliable
*(SPEC §3.5)*.

**"No destination" branch.** Tapping "Just show me the board" skips to a
single-station picker and creates one board-mode entry (§4.5, §2 principle
15) instead of a commute pair. This path explicitly does *not* ask about
favorites or notifications afterward — those concepts don't apply to a
board — and drops the user straight into Board mode.

```
4. Favorite trains                5. Notifications (primed, not system)
┌─────────────────────┐          ┌─────────────────────┐
│ Which trains do you   │          │  🔔                  │
│ actually take?        │          │                       │
│                       │          │  Get a heads-up        │
│ Morning                │          │  before your train      │
│ ☑ 6662 · 7:48 AM       │          │  changes track, runs     │
│ ☐ 6320 · 8:12 AM       │          │  late, or gets            │
│ ☐ 6624 · 8:34 AM       │          │  cancelled — never for     │
│                       │          │  anything else.            │
│ You can change this     │          │                           │
│ later, and add more.    │          │  [Turn on notifications]  │
│                       │          │  [Skip for now]            │
│ Numbers stay the same    │          │                           │
│ every weekday — 6662     │          └─────────────────────┘
│ is your 7:48 whether       │
│ it's Monday or Thursday.    │
│                       │
│         [Continue]      │
└─────────────────────┘
```

**Step 4 — Favorite trains.** Listed by **departure time**, not by number —
"6662 · 7:48 AM," never a bare number entry field — because train numbers
are stable weekday to weekday but meaningless to type from memory. Sourced
from that day's actual direct-train list for the FROM→TO pair, same as the
integration's options-flow picker. This step is skippable; skipping means
the app falls back to "next usable train" everywhere a favorite would have
been shown (matching `next_departure` vs `next_favorite` fallback
behavior), and no Live Activity / onboard tracking is offered until a
favorite exists, since nothing else can say which train is "yours."
*(README, Options table, "Favorite trains"; SPEC §9, favourite picker.)*

**Step 5 — Notification permission, primed.** The system permission
prompt is never the first thing shown. A screen explains *why* first (this
matches "contextual priming" best practice and keeps the ask concrete: "get
a heads-up before your train changes track, runs late, or gets cancelled" —
i.e., named after the actual event types in §5, not a vague "stay
informed"). Only tapping "Turn on notifications" triggers the OS prompt.
Skipping is a real, supported path — see below.

**Step 6 — Optional account.**

```
6. Account (optional)
┌─────────────────────────────┐
│  Sign in for the best          │
│  experience (optional)          │
│                                 │
│  With an account:                 │
│  • Notifications keep working       │
│    even if the app's been           │
│    closed for days                  │
│  • Your commutes and favorites      │
│    sync to your other devices        │
│  • Earlier track info at NY Penn,    │
│    when it's available               │
│                                       │
│  Without one, everything above         │
│  works in this app, on this            │
│  device — Live Activities, the         │
│  board, alerts — just less              │
│  reliably in the background.            │
│                                         │
│  [Continue without an account]           │
│  [Create account / Sign in]              │
└─────────────────────────────┘
```

**What an account buys, concretely, and what doesn't need one.** This is a
deliberate translation of a real tradeoff the integration already lives
with, not a new invention: RailData rations sign-ins and realtime calls per
*account* (10 sign-ins/day, 5 schedule downloads/day, 40,000 realtime
calls/day — SPEC §2.9), and the reason to bother with it at all is that its
signaling-track signal beats the website's board by roughly 10–20 minutes
of lead time at New York Penn. A phone app can't hand every installed copy
its own such account without the same friction the integration already
avoids for HA users (a developer registration). So:

- **Without an account:** the app polls a shared backend that reads the
  public board (equivalent to the "website" source), stores commutes and
  favorites locally only, and delivers notifications via on-device
  background refresh — same mechanism as the existing Live Activity
  blueprint's polling, just running inside the app instead of Home
  Assistant. Everything in §4–§7 of this document works. The main cost is
  that iOS background refresh is not guaranteed to run promptly if the app
  hasn't been opened in a while, so a notification can arrive late.
- **With an account:** the backend delivers pushes server-side instead of
  relying on the device waking up on its own, and — where the backend's
  pooled RailData credentials have quota to spare — surfaces the earlier
  signaled-track hedge at New York Penn the same way the integration does.
  Commutes and favorites also sync across devices.

This mapping (account = more reliable delivery + occasional data-quality
upside, not a paywall on core features) is a product decision made for this
spec, not something read directly off the integration — flagged again in
§11 as something to validate against real backend capacity before
committing to the copy above.

**Ordering note:** notification priming (step 5) intentionally comes
*before* the account offer (step 6), because the account's main pitch is
about notification reliability, and priming should have already explained
why notifications matter at all — the account screen reads as "and here's
how to make that better," not as a novel ask.

---

## 4. Primary screens

### 4.1 Home / Commute

The screen this entire product exists for. Structure, top to bottom:
disruption banner (only when something's wrong), hero countdown, track/
status/crowding pills, stops-away progress (only while tracking a
favorite), then the board.

**Normal state, tracking a favorite, on time:**

```
┌───────────────────────────────────┐
│  Short Hills → New York Penn    ⋮ │
├───────────────────────────────────┤
│                                     │
│   12                                │
│   min                               │
│                                     │
│   7:48 AM · Train 6662              │
│                                     │
│   [Track 4]  [On time]              │
│                                     │
│   ────────────────────              │
│   3 stops away · next Millburn       │
│                                     │
├───────────────────────────────────┤
│  DEPARTS   TRAIN      TRK   STATUS   │
│  7:48      6662 ⭐     4              │
│  8:12      6320              Cancelled│
│  8:34      6624              22m late │
└───────────────────────────────────┘
```

**Delayed, track hedged (signaled, not yet posted):**

```
┌───────────────────────────────────┐
│  Short Hills → New York Penn    ⋮ │
├───────────────────────────────────┤   ← amber corner wash
│                                     │
│   4                                 │
│   min                               │
│                                     │
│   7:48 AM · Train 6662              │
│                                     │
│   [Track 4 (likely)] [6 min late]    │
│                                     │
│   ────────────────────              │
│   1 stop away · next Summit          │
│                                     │
├───────────────────────────────────┤
│  DEPARTS   TRAIN      TRK   STATUS   │
│  7:48      6662 ⭐     4?    6m late  │
│  8:12      6320       —     Cancelled│
│  8:34      6624       —              │
└───────────────────────────────────┘
```

Note the board's compact track cell: a confirmed board track shows bare
(`4`), a signaled-only guess shows the hedge mark (`4?`), matching the
existing card's `trackCell` behavior exactly *(pills.ts)*.

**Cancelled favorite (still shown, not hidden):**

```
┌───────────────────────────────────┐
│  Short Hills → New York Penn    ⋮ │
├───────────────────────────────────┤   ← red corner wash
│  ⚠ Train 6320 (8:12 AM) is cancelled│
├───────────────────────────────────┤
│                                     │
│   Cancelled                         │
│                                     │
│   8:12 AM · Train 6320              │
│                                     │
│   [Cancelled]                       │
│                                     │
│   Service alert for this train:      │
│   ┃ M&E train #6320, the 8:12 AM       │
│   ┃ departure, is cancelled.            │
│                                     │
├───────────────────────────────────┤
│  DEPARTS   TRAIN      TRK   STATUS   │
│  8:12      6320 ⭐    —     Cancelled│
│  8:34      6624              on time  │
└───────────────────────────────────┘
```

The favorite wins the hero slot **even when cancelled** — this is
deliberately the single most useful thing the screen can say, and the
existing card treats it as such *(commute.ts, `pickHero`: "the favourite
wins whenever it is on the board, *including when it is cancelled*")*.
Falling back to a non-favorite next train, by contrast, always skips
cancelled rows — a countdown to a train that isn't running is worse than
one to a train slightly further out.

**No favorite configured, following next usable train:**

```
┌───────────────────────────────────┐
│  Short Hills → New York Penn    ⋮ │
├───────────────────────────────────┤
│                                     │
│   18                                │
│   min                               │
│                                     │
│   8:12 AM · Train 6320              │
│                                     │
│   [Track not posted yet]            │
│                                     │
│   No favorite set. Pick one in        │
│   Settings to track it specifically.   │
├───────────────────────────────────┤
│  DEPARTS   TRAIN      TRK   STATUS   │
│  8:12      6320       —              │
│  8:34      6624       —              │
│  8:51      6631       —              │
└───────────────────────────────────┘
```

### 4.2 Departure detail

Opened by tapping the hero, any board row, or a notification. Everything
the Home screen's hero shows, plus the stop-by-stop position, per-car
crowding (when the board publishes it), alerts naming this specific train,
and factual track-timing context.

```
┌───────────────────────────────────┐
│  ←  Train 6662                     │
├───────────────────────────────────┤
│  7:48 AM → New York Penn            │
│  Morristown Line                    │
│                                     │
│  [Track 4]   [6 min late]           │
│                                     │
│  ── Where it is ──────────────      │
│  ✓ Short Hills          7:48 (dep)   │
│  ✓ Millburn              7:52         │
│  ● Maplewood             7:56 (next)  │
│    South Orange           8:00         │
│    Newark Broad St         8:11         │
│    ⋯ 4 more stops                       │
│    New York Penn (arr)     8:31          │
│                                     │
│  ── On board ─────────────────       │
│  Front    ●●●○  Light                 │
│  Middle   ●●●●  Heavy                 │
│  Back     ●●○○  Moderate               │
│  Back cars are emptier                  │
│                                     │
│  ── About this train ─────────       │
│  New York Penn typically posts a       │
│  track about 9 minutes before           │
│  departure. This one posted at           │
│  the usual time.                          │
│                                     │
│  No alerts currently name this           │
│  train.                                  │
└───────────────────────────────────┘
```

**"Where it is"** is built from the stop list, using `last_departed` /
`next_stop` exactly as the `stops_away` sensor does — a checkmark for
departed stops, a dot for the next call, plain text for the rest, collapsed
after a few rows with a "N more stops" disclosure rather than a full list
by default (the raw stop list can run 15–20 rows on a long line).

**"On board"** renders per-section crowding (front/middle/back) as the
primary read, with the "which cars are emptier" hint only appearing when
there's a real spread between sections — matching `emptiestCars`'s "only
worth mentioning if it would change where you stand." When the board
carries no `cars` data yet (true for most non-imminent departures — SPEC
§2.3 observed capacity on 5 of 19 rows), this section reads:

```
  ── On board ─────────────────
  Boarding info isn't published yet.
  It usually appears in the last
  20 minutes or so before departure.
```

**"About this train"** is deliberately *not* a per-train prediction. The
integration explicitly does not predict which track a train will use — its
own bar for shipping that (60% top-1 accuracy) hasn't been cleared *(SPEC
§7.2)* — so this section only ever states the station's general timing
pattern (median ~8.8 min before departure, IQR 1.9 min at New York Penn)
and whether *this* train's track posted inside or outside that norm. It
never says "likely Track 7 again" about a specific future train. If the
track hasn't posted and the deadline described in §5's `track_overdue`
event has passed, this becomes:

```
  ── About this train ─────────
  ⚠ No track yet, and other trains
  on this board already have
  theirs. That's unusual this close
  to departure.
```

Alerts naming this exact train (by number, via the same extraction the
integration performs — SPEC §6.3) render as quoted blocks under a header;
absent, the section reads "No alerts currently name this train" rather
than being hidden, so a rider knows the app checked.

### 4.3 Alerts

One list, split into two sections — **live incidents** first, **planned
advisories** second — because they call for different reactions: one means
leave now, the other means remember it for the weekend *(README, service
alerts sensors)*. Within "live," anything affecting one of the user's own
configured trains sorts to the top and is visually distinguished, mirroring
the phone dashboard's "leads with whether one names a train you might
actually be on."

```
┌───────────────────────────────────┐
│  Alerts                            │
├───────────────────────────────────┤
│  LIVE                               │
│                                     │
│  ⚠ Names your train                  │
│  ┃ M&E train #6320, the 8:12 AM         │
│  ┃ departure, is cancelled.              │
│    Short Hills → New York Penn            │
│                                     │
│  🚨 M&E train #6324, the 8:54 AM          │
│     departure from Summit, is up to        │
│     15 minutes late due to a signal         │
│     problem near Newark Broad St.            │
│                                     │
│  ── PLANNED ──────────────           │
│                                     │
│  ℹ Track work on the Morristown Line       │
│    weekends through October 12.              │
└───────────────────────────────────┘
```

An alert that names a train currently on one of the user's boards is
labeled **"Names your train"**; the `line_cancellation` inference (a
knock-on effect, not a fact about your own train — principle 14) is shown
as a distinctly weaker/secondary badge, e.g. **"May affect your train"**,
never mixed visually with a confirmed hit.

```
  🔶 May affect your train
  ┃ Train 6311, running just ahead of
  ┃ yours, was cancelled. Its stops
  ┃ and passengers may land on 6320.
    This is a possibility, not a
    confirmed delay.
```

Empty state: "No alerts on your lines right now." — never a blank screen
with no explanation, since "no alerts" and "the alert feed is down" must
read differently (§4.9).

### 4.4 Commutes list / switching direction

```
┌───────────────────────────────────┐
│  Commutes                     + Add │
├───────────────────────────────────┤
│  MORNING                            │
│  🟢 Short Hills → New York Penn      │
│     Next: 7:48 AM · On time            │
│                                     │
│  EVENING                            │
│  🟢 New York Penn → Short Hills      │
│     Next: 5:12 PM                      │
│                                     │
│  BOARDS                             │
│  📋 Newark Broad Street                │
│     Whole board, no destination         │
└───────────────────────────────────┘
```

Grouped by direction (Morning/Evening — inferred from which commute window,
if any, each entry belongs to; ungrouped and listed under "Commutes" if no
windows are configured), with board-mode entries in their own section so
they're never confused with a real commute that has disruption/favorite
semantics.

Tapping a row opens that commute's Home screen; Home's own header (§4.1)
also carries a direction switcher — tapping the commute name in the Home
header opens a lightweight sheet listing sibling commutes sharing an origin
or destination, so switching between the outbound and return leg doesn't
require leaving Home via the tab bar:

```
┌───────────────────────────────────┐
│  Switch commute                ✕   │
├───────────────────────────────────┤
│  ● Short Hills → New York Penn       │
│    New York Penn → Short Hills        │
│    Short Hills → Hoboken               │
└───────────────────────────────────┘
```

### 4.5 Favorites picker

Reached from a commute's settings. Lists **today's** direct trains, labeled
by departure time, sourced the same way the integration's options flow
builds its picker — from the resolved schedule, not free text — with the
same resilience the integration has to a weekend/holiday schedule that
doesn't include a weekday favorite:

```
┌───────────────────────────────────┐
│  ←  Favorite trains                │
├───────────────────────────────────┤
│  Pick the trains you actually        │
│  take. Numbers stay the same          │
│  across weekdays.                      │
│                                     │
│  ☑ 6662 · 7:48 AM                    │
│  ☐ 6320 · 8:12 AM                    │
│  ☑ 6624 · 8:34 AM                    │
│  ☐ 6631 · 8:51 AM                    │
│                                     │
│  Not running today but saved:          │
│  ☑ 3201 · Sat/Sun only                  │
│                                     │
│              [Done]                 │
└───────────────────────────────────┘
```

A previously saved favorite that isn't in *today's* resolved list (e.g. a
weekday train checked on a weekend) still appears, checked, under "Not
running today but saved" — re-adding any already-saved favorite missing
from today's list, exactly as the integration does, so a Saturday visit to
this screen can't silently drop a weekday train *(SPEC §9)*.

### 4.6 Settings

**Global settings:**

```
┌───────────────────────────────────┐
│  Settings                          │
├───────────────────────────────────┤
│  ACCOUNT                            │
│  Signed in as d***@gmail.com           │
│  Sync: commutes, favorites             │
│                                     │
│  COMMUTES                           │
│  Short Hills → New York Penn      ›    │
│  New York Penn → Short Hills      ›    │
│  Newark Broad Street (board)      ›    │
│                                     │
│  NOTIFICATIONS                      │
│  Push notifications          [On]      │
│  Lock screen / Dynamic Island [On]     │
│                                     │
│  ABOUT                              │
│  Data source status               ›    │
│  Send diagnostics                 ›    │
└───────────────────────────────────┘
```

**Per-commute settings:**

```
┌───────────────────────────────────┐
│  ←  Short Hills → New York Penn    │
├───────────────────────────────────┤
│  Favorite trains              5 › │
│                                     │
│  Delay before I'm notified          │
│  ●──────○───────  10 min           │
│                                     │
│  How far ahead to watch              │
│  ●───○──────────  90 min           │
│                                     │
│  COMMUTE WINDOW                     │
│  When you actually take this          │
│  commute. Outside this window,         │
│  nothing pushes, ever.                  │
│  Mon–Fri  7:00 AM – 9:30 AM        › │
│                                     │
│  LOCATION                           │
│  Only notify me if I could still        │
│  actually catch this train.              │
│  ● Use zones     ○ Use a radius        │
│  Home, Office                       › │
│                                     │
│  Remove this commute                 │
└───────────────────────────────────┘
```

Two sliders map directly to the integration's two most-reached-for tuning
knobs: **delay threshold** (default 10 min, README default) and
**lookahead** (default 90 min — called out in the README as "the option to
reach for first if alerts feel wrong," since at 90 minutes the window can
include trains never actually being taken). Commute window and location
gating are covered fully in §5.

### 4.7 Onboard mode

Automatic once a tracked train's scheduled departure passes while the app
still considers it "yours" (mirrors the Live Activity's `riding` state —
origin behind the train, destination still ahead, blueprint `on_board`).
The hero switches from a departure countdown to an **arrival** countdown;
everything about track/status becomes irrelevant and disappears.

```
┌───────────────────────────────────┐
│  Short Hills → New York Penn    ⋮ │
├───────────────────────────────────┤
│  🚆 You're on Train 6662             │
│                                     │
│   19                                │
│   min to New York Penn               │
│                                     │
│   4 stops left · next Newark Broad St│
│                                     │
│   6 min late  (lower-bound estimate)  │
│                                     │
│   [I got off / not on this train]      │
└───────────────────────────────────┘
```

The estimate is explicitly labeled a lower bound — matching
`TrainRun.eta_at`'s deliberate refusal to guess a train will make up time —
and there's no way to distinguish "definitely aboard" from "still on the
platform" in the underlying data, so a visible **"I got off / not on this
train"** control exists as the app's equivalent of the blueprint's Dismiss
action; tapping it returns to Home and starts tracking the next usable
train instead. Onboard mode does not end just because the commute window
closed — arrival is about a journey already under way, not about whether
to board (principle 12's corollary, and the blueprint's explicit fix for
having gotten this wrong once already).

### 4.8 Empty / degraded states

**No more trains today:**

```
┌───────────────────────────────────┐
│  Short Hills → New York Penn    ⋮ │
├───────────────────────────────────┤
│                                     │
│        Nothing on the board          │
│                                     │
│   No departures scheduled for          │
│   the rest of today.                    │
│                                     │
│   Next departure: tomorrow 5:12 AM      │
└───────────────────────────────────┘
```

**Every upcoming departure cancelled (line suspension):**

```
┌───────────────────────────────────┐
│  Short Hills → New York Penn    ⋮ │
├───────────────────────────────────┤  ← red wash, not muted
│  ⚠ Every upcoming departure is         │
│     cancelled.                          │
├───────────────────────────────────┤
│        Nothing on the board          │
│                                     │
│   Every train in the next couple       │
│   of hours is cancelled. Check           │
│   Alerts for what's going on.            │
│                                     │
│   [View alerts]                       │
└───────────────────────────────────┘
```

The distinction between "genuinely nothing scheduled" and "everything's
cancelled" is load-bearing — collapsing them into one generic empty state
is exactly the bug the phone dashboard YAML fixes explicitly (taking board
row zero blindly during a line suspension put a giant countdown to a
cancelled train at the top of the page).

**No realtime data yet (train far out):**

The hero still shows — countdown, clock, train number — but the status
pill area is simply absent, not replaced with "On time." This is principle
3, rendered: nothing claims punctuality it hasn't observed.

```
   62
   min

   9:10 AM · Train 6640

   (no status pill — nothing known yet)
```

**Data source down:**

```
┌───────────────────────────────────┐
│  Short Hills → New York Penn    ⋮ │
├───────────────────────────────────┤
│  ⚠ Can't reach NJ Transit's live       │
│     data right now. Showing the         │
│     last known board from 8:41 AM.       │
├───────────────────────────────────┤
│  (board rows, dimmed, "as of 8:41 AM"  │
│   watermark)                            │
└───────────────────────────────────┘
```

Never a blank screen or a spinner with no explanation — stale data,
clearly timestamped and visually distinguished (dimmed), is more useful
than nothing, and matches the integration's own posture toward its
undocumented upstream: expect drift, degrade visibly, never crash *(SPEC
§12, Reliability)*.

**Transfer-only itinerary (fails open):**

For a station pair with no one-seat ride at any hour — rare, but real for
some branch-to-branch pairs *(SPEC §2.7)* — the board falls back to
showing transfer itineraries rather than reading as "no trains." Because
the underlying feed can't say *where* the transfer happens or that one is
required (principle 8), rows in this state carry an explicit banner rather
than pretending to be normal direct-train rows:

```
┌───────────────────────────────────┐
│  Gladstone → New York Penn      ⋮ │
├───────────────────────────────────┤
│  ℹ No direct trains run this          │
│     route. These require changing       │
│     — we can't say where.                │
├───────────────────────────────────┤
│  DEPARTS   TRAIN      TRK   STATUS   │
│  7:41      411               ...      │
└───────────────────────────────────┘
```

---

## 5. Notifications, Live Activity, Dynamic Island, Android ongoing notification

### Event types

Directly from `event.py` and the two blueprints — nothing invented here,
only mapped onto push/in-app/both:

| Event | Fires when | Push? | Copy example |
|---|---|---|---|
| `cancelled` | A train becomes cancelled | Yes | "Train 6320 (8:12 AM) is cancelled." |
| `delayed` | Crosses the delay threshold (default 10 min) | Yes, once per 5-min band | "Train 6624 is 12 minutes late." |
| `track_changed` | Moved *after* a track was already posted | Yes | "Train 6662 moved from Track 4 to Track 7." |
| `alerted` | Newly named in a live incident | Yes | "Train 6612 is named in a service alert." |
| `line_cancellation` | A train you can't use, ahead of one you can, is cancelled | In-app only (Alerts) | "Train 6311, just ahead of yours, was cancelled." |
| `track_overdue` | 6 min to departure, still no track, others have theirs | Yes, once | "Track not posted for Train 6662 — 6 minutes out." |

**Why `line_cancellation` doesn't push.** It's explicitly an inference, not
a fact about the user's own train (principle 14, SPEC §9: "It is an
inference, not a fact about your journey... that is why it is a separate
event type"). Pushing an inference at the same urgency as a confirmed
cancellation is exactly the kind of noise that trains people to ignore
notifications; it's surfaced prominently in Alerts and on Departure detail
instead, where there's room for the hedge language.

**A first track assignment is never a `track_changed`.** The board simply
had no track yet; only being *moved after* a track was already published
is the actionable case, and the push carries the previous track for
comparison ("moved from Track 4 to Track 7") — never sent for a train's
first assignment.

### Rate limiting

- **Delay is announced in 5-minute bands, and only escalation speaks
  again.** A train drifting between 5, 6 and 7 minutes late notifies once;
  one crossing from 6 to 12 notifies again, because that's a genuine
  escalation. A train recovering says nothing. A train crossing back and
  forth over a band boundary can still notify more than once — repeatedly
  passing ten minutes late is worth hearing about twice. This is a direct
  port of `commute_disruption.yaml`'s bucketing logic, which exists
  because the unbucketed version produced five notifications in seven
  minutes about one train.
- **Cancellations and named-alert events have no volatile part** and
  notify exactly once each.
- **Nothing re-fires on relaunch/restart.** Whatever was already true the
  last time the app checked is the baseline; only what's *new* since then
  notifies. A cold start after being killed overnight must not replay a
  disruption that already happened and was already seen.
- **Live Activity refreshes are gated on visible change, not on poll
  interval.** A refresh is only pushed to the phone when one of the fields
  actually rendered on the activity has changed — train id, track,
  signaled track, status text, or crowding tier. The chronometer ticks on
  the device; sending an identical face every poll would burn the very
  rate-limited channel the countdown doesn't need.

### The dismiss interaction

**iOS.** Live Activities cannot carry buttons, and iOS never tells the app
when one is swiped away. A companion, silent (`passive` interruption
level) notification carries the actual Dismiss action. Because an action on
an iOS notification only surfaces on a long press, the companion
notification's own text says so explicitly: **"Hold this notification,
then tap Dismiss."** A plain tap instead opens the app (or a deep link, if
configured) — this wording exists specifically to prevent the failure the
blueprint already hit once: a notification that said "tap to dismiss" sent
people into the app on a plain tap, which looked exactly like the dismiss
silently doing nothing.

**Android.** Ongoing/ambient notifications on Android can carry inline
action buttons directly (no long-press requirement, no companion
notification needed) — so the Android equivalent is a single ongoing
notification with a visible **Dismiss** button and a visible **Open**
target, collapsing the two-notification iOS workaround into one. This is a
platform capability difference, not a UX regression; the underlying rule
("dismissing this ends tracking for the rest of the window") is identical.

**Effect of dismissing, either platform.** Ends the activity/notification
immediately and suppresses further tracking of that specific favorite for
the remainder of the commute window (or until 3 a.m., if no window is
configured) — without this, a second favorite departing soon after the one
actually caught would start counting down mid-ride.

### Commute window gating

A commute window (a weekly schedule, e.g. "Mon–Fri 7:00–9:30 AM") is the
**only** mechanism that can tell a day off from a commute — location can't,
because home looks the same whether or not the user is about to leave for
the station. Left unconfigured, there is no time gating at all. The window
closing also resets the dismissed flag (see above). The arrival half of
onboard tracking (§4.7) is **deliberately exempt** from the window once
riding: a window ending at 10:00 must not cut off the arrival countdown of
a train arriving at 10:30, because by then the question isn't "should you
board," it's "you already did." (The integration got this wrong once —
gating the arrival on `on_board` alone put a countdown on the lock screen
of someone who'd stayed home, because "the train left the station" isn't
the same fact as "you were on it" — the fix was to gate arrival on the
window too, but only up to when riding actually starts.)

### The location rule

**"Are you somewhere this train could still be caught from?"** — one rule,
covering three situations: working from home (evening notifications should
stay quiet), already aboard (the countdown to the *next* train shouldn't
start once you've left), and still in the city late (notifications should
keep going). Configured either as named **zones** (home, office, or a
radius around the station) or, if none are set, a single **radius** from
the origin station.

**Default radius, if the user sets none.** Half the straight-line distance
from home to the origin station, floored at 2 (miles or km, matching the
device/region unit — never converted, never shown with a unit label the
app can't be sure of). Floor-at-2 covers the walk to a nearby station;
halving is the largest radius that *cannot* reach home, so the far end of
a commute silences itself on a day worked from home without the user
choosing a number.

**Standing at the origin station always counts as "yes," independent of
zones.** A rider who walks to the station is not inside a home zone sized
for the house — measured live once during development, a person 36 m from
the station and 638 m from a 100 m-radius home zone had their activity
clear on the walk to the train every single morning. The station's own
radius is checked directly, in addition to whatever zones are configured.

**It fails open, everywhere, without exception.** No location permission,
no zones configured, coordinates unavailable, a zone that was renamed and
can no longer be measured — all of these mean **show it anyway**. A
suppressed countdown is invisible: the user doesn't learn the notification
was silenced, they just miss the train. This is non-negotiable and applies
identically to iOS and Android.

### Live Activity / Dynamic Island (iOS) content

```
Lock screen:                    Dynamic Island (compact):
┌─────────────────────────┐    ┌──────────┐
│ 🚆 Train 6662             │    │ 🚆   12m  │
│ Track 4 · 6 min late        │    └──────────┘
│ Filling up                    │
│                              │    Dynamic Island (expanded):
│         12 min                │    ┌─────────────────────┐
│      ●━━━━━━━○                │    │ 🚆 Train 6662          │
└─────────────────────────┘    │ Track 4 · 6 min late         │
                                │        12 min                │
                                └─────────────────────┘
```

Headline field order — train, track, status, crowding — is deliberate and
matches the blueprint's own reasoning: "which train and which track are
why you're looking, the status changes whether you leave, and how full it
is only changes which car you walk toward," so on a truncating surface
(Dynamic Island's compact form, an Apple Watch banner) crowding is the
first thing dropped, then status, never train or track.

Once riding, the activity switches to the arrival face:

```
┌─────────────────────────┐
│ 🚆 Train 6662             │
│ 3 stops · 4 min late         │
│                              │
│      Arriving in 19 min        │
│   ●━━━━━━━━━━━━━━━○           │
└─────────────────────────┘
```

### Android ongoing notification

Android's nearest equivalent is a persistent, updating foreground-style
notification (not a Live Activity — no direct Android analog exists), using
the same headline/content ordering and refreshed under the same
change-gated rule as iOS. Because Android notifications can carry inline
actions natively, Dismiss appears as a first-class button rather than a
hold-then-tap gesture:

```
┌─────────────────────────────────┐
│ 🚆 Train 6662                      │
│ Track 4 · 6 min late · Filling up    │
│ Departs in 12 min                     │
│                                      │
│  [Dismiss]              [Open]        │
└─────────────────────────────────┘
```

---

## 6. Widgets and watch complication

Every widget is read-only and reflects exactly what data is actually
available for that departure — never a placeholder value. A widget for a
train more than an hour out, for instance, must show no status text at all
rather than "On time" (principle 3 applies everywhere, not just the main
screens).

**Home screen — small (2×2):**

```
┌───────────────┐
│ 🚆  12 min       │
│ Trk 4 · 6m late    │
└───────────────┘
```
Countdown, track (or hedge/overdue state), and status only. Tapping opens
Departure detail.

**Home screen — medium (4×2):**

```
┌───────────────────────────────┐
│ 🚆 12 min          Track 4      │
│ Train 6662 · 6 min late           │
│ 3 stops away · next Millburn        │
└───────────────────────────────┘
```
Adds the stops-away line when a favorite is being tracked; when it isn't,
that line is replaced with "No favorite tracked" rather than left blank or
guessed.

**Lock screen widget (iOS 16+):**

```
🚆 12m · Trk 4
```
Single-line, matching the Dynamic Island's compact density — this is the
same content class as the compact Dynamic Island view, reused rather than
redesigned.

**Watch complication:**

```
Corner/rectangular:        Circular:
🚆 12m                       (12)
```
Countdown only, or countdown + track abbreviation if the complication
family has room. No crowding, no status text — the watch face is not a
platform for reading pills at arm's length; it's a glance for the number.

All widgets and the complication respect the same commute-window and
location gating as notifications (§5) when deciding *what to show*, but —
unlike a push — never disappear entirely; an out-of-window widget shows the
next scheduled commute's info in a visually muted state rather than going
blank, since a widget is pulled, not pushed, and a blank tile reads as
broken rather than as "not now."

---

## 7. Visual language

### Hero type scale

The existing card sets its countdown at `clamp(3rem, 17cqi, 4rem)` — 48 to
64px depending on available width — weight 750, tabular figures, and
explicitly **never tints the number itself**: "a delayed train is still
the number you are reading, and tinting it amber makes the one thing this
card exists to show harder to read, not easier" *(departures-card.ts)*.

Native translation: a large, heavy numeral style (SF Pro Rounded/Display
Black equivalent on iOS, a comparable heavy/condensed numeral face on
Android), sized 48–64pt depending on screen width, tabular/lining figures,
always rendered in the primary label color — never the status color.
The "min" unit label is small (~26% of the numeral's size), uppercase,
secondary-color, and is the one place Dynamic Type is allowed to scale
close to normally; the numeral itself is capped well before Accessibility
XXXL sizes to preserve the arm's-length legibility principle at any
supported type size — it should not become the dominant, page-breaking
element it would become if scaled 1:1 with the largest accessibility
sizes. (This is a deliberate departure from "everything scales," made in
service of the same principle that put it in `--primary-text-color` in the
first place; flagged for design review since it's a real Dynamic Type
tradeoff, not a default.)

### Status color semantics

Four tones, ported directly from `pills.ts`:

| Tone | Meaning | Example |
|---|---|---|
| `accent` | Neutral fact, nothing wrong | Confirmed track, on-time status |
| `muted` | Informational, not news | "Track not posted yet," no-status |
| `warn` (amber) | Degraded but running | Delay, "Filling up" crowding |
| `bad` (red) | Wrong / not happening | Cancelled, track overdue, "Busy" crowding |

**"Worst" and why it's computed the way it is.** A screen/card's overall
tint is the worst tone among the pills for the train it's *currently
displaying* — never across the whole board, and never `muted`, since "no
track posted yet" is the ordinary state of a board for most of the day and
isn't news *(pills.ts `cardMood`)*. This is what makes principle 4 hold
mechanically: a cancellation the screen has already routed past (because
the hero moved on to the next usable train) contributes nothing to that
next train's mood, because it's simply not one of its pills.

### Pills

Small, rounded, filled-at-low-opacity chips, each carrying exactly one
fact (track, status, or crowding) in reading order: **where** (track),
**how** (status), **how full** (crowding) — the same order a platform
sign gets read in. A `bad`-tone pill in the primary hero position pulses
gently (respecting Reduce Motion — see §8) to be findable without reading,
since it sits directly beside the number someone is straining to read at
distance.

### Line colors

The board's own line-color field is **not** a reliable line identity — it
doubles as the field used to flag cancellations on some rows, so it can't
be trusted as a stable per-line color source *(README/`departures-card.ts`
CSS comment)*. The app should maintain its own static line→color table,
independent of anything the feed sends per-row, seeded from NJ Transit's
public line branding. Only one value is confirmed from this corpus:
**Morristown Line = `#00953b`**, which is what the board happens to send
correctly for that line and which both the card and the YAML dashboard use
as their example/default accent. The remaining twelve lines' colors need
to be sourced from NJ Transit's own public brand materials before launch —
not invented here (see §11).

### Light / dark, contrast

Ported principle, not literal CSS: pill *foreground* color is a blend of
the tone and the primary text color rather than the tone alone, so the
same tone value stays legible against both a light and a dark surface —
darkening toward black on light, lifting toward white on dark — instead of
needing two separate palettes. The blend ratios in the existing
implementation were tuned by *measured* contrast, not eyeballed: amber
needed a noticeably higher proportion of text color than green or red to
clear WCAG AA at the pill's text size, because a light hue can only be
darkened by borrowing from the text color, never brightened. Native
implementation should re-derive equivalent ratios per platform's color
system (iOS semantic colors / Android dynamic color) and re-verify AA
contrast in both appearances rather than porting the oklab mix ratios
verbatim — the *target* (AA in both modes, tuned per-tone rather than
uniformly) is what must carry over, not the specific numbers.

### WCAG AA, Dynamic Type, one-handed reach

- Every text/background pairing used for a pill, status text, or alert
  quote must independently clear WCAG AA at the size it's rendered —
  verified per tone, per appearance mode, not assumed from one passing
  case.
- Dynamic Type / Android font scale is supported throughout except the
  hero numeral (see above); pills, table rows, and body text reflow and
  wrap normally at large accessibility sizes rather than truncating.
- **One-handed reach.** The hero and its pills sit in the upper 40–50% of
  the screen — deliberately not reachable one-handed, because reading
  doesn't require reach, only looking. Anything requiring a *tap*
  (switching commutes, dismissing, opening detail, the tab bar) sits in
  the lower half or is reachable via a single first-line row, matching how
  the existing card already puts its clickable board rows below a hero
  that's meant to be glanced at, not touched, most of the time.

---

## 8. Accessibility & localization

### VoiceOver / TalkBack order for the hero

The hero must read as **one grouped element carrying the full sentence**,
not as fragments a screen-reader user has to swipe through and reassemble.
Recommended single accessibility label, built the same way the visual
hero is composed:

> "Departs in 12 minutes, 7:48 AM, Train 6662. Track 4. On time. Front cars
> are emptier. 3 stops away, next Millburn."

Rationale: a sighted rider reading at arm's length gets this whole picture
in one glance; a screen-reader user swiping through six separate elements
(number, clock, train id, track pill, status pill, crowding hint) one at a
time gets a worse experience than the sighted one, which inverts the
product's own "read at a glance" premise. Sub-elements (individual pills,
the progress line) remain independently focusable for anyone who wants to
jump directly to one, but the *default* single-swipe experience on the
hero should be the composed sentence, not the first fragment. Board rows,
by contrast, are naturally list items and should announce as one row each
("7:48, Train 6662, favorite, Track 4, on time"), following the same
where-then-how-then-how-full order the pills use visually.

The `bad`-tone pulse animation must respect **Reduce Motion**: falls back
to a static, higher-contrast outline instead of animating, matching the
existing pill CSS's own `prefers-reduced-motion` guard.

### Spanish localization

NJ Transit's rail ridership includes a substantial Spanish-speaking
population, and the app's own chrome — navigation, settings, onboarding,
the copy strings in §9's glossary, composed status/pill text, notification
templates — should ship translated at launch, not as a post-launch
addition.

**What can and can't be localized, and why.** The app fully controls
`status_text`-equivalent phrases ("Cancelled," "6 min late," "On time")
because those are composed client-side from structured fields (`status`,
`delay_minutes`) — translating them is just a string table. What the app
does **not** control is the *raw prose* of a service alert
(`SystemAlert.message`) or a per-train inline board message
(`inline_message`) — both arrive as English sentences NJ Transit itself
wrote, with no structured equivalent underneath (SPEC §6.3's train-number
extraction runs on this same English prose via regex). Machine-translating
that prose client-side risks silently mistranslating a safety-relevant
detail ("do not board," a specific platform instruction) with no way to
audit it against the original. This is called out again in §12 as a real
data-availability limit, not a UX oversight — the honest interim behavior
is to show the original English alert text alongside a visible "Original
text from NJ Transit, not translated" note, rather than presenting a
machine translation as authoritative.

---

## 9. Copy guidelines and glossary

**Tone.** Direct, factual, short. No exclamation points on anything except
a genuinely urgent, freshly-arrived cancellation notification. Never
personify the train ("Train 6662 is trying its best") — state what's
known, flatly. Prefer the operator's own phrasing where one exists
(`status_text`) over an invented paraphrase.

**The "likely" hedge.** Anything derived from the signaling system rather
than the board's own posted answer carries `(likely)` in full text and a
bare `?` in compact/table contexts. Never present a signaled value with
the same visual confidence as a posted one — this is principle 9,
literally applied to copy.

**"Overdue" vs. "not yet."** A missing track is described as *overdue*
only when (a) the train is inside the overdue window (6 minutes to
departure) **and** (b) other trains on the same board currently have
tracks posted. Outside those conditions it's *never* alarming language —
"Track not posted yet," "Track due in about N min," or, for a station that
isn't posting tracks to anyone right now, plain "Track not posted." Getting
this distinction wrong is exactly how a signal like this stops being
trusted — a "warning" state that's actually the ordinary state of most of
the day trains it into being ignored.

### status_text glossary

| Underlying state | Displayed text |
|---|---|
| Cancelled | `Cancelled` |
| Delayed, N minutes | `N min late` |
| Boarding | `Boarding` |
| All aboard | `All aboard` |
| Departed | `Departed` |
| On time (confirmed) | `On time` |
| No realtime data yet | *(nothing shown — not "On time")* |

### Track glossary

| State | Compact (board row) | Full (hero / detail) |
|---|---|---|
| Board-confirmed track | `4` | `Track 4` |
| Signaled only, unconfirmed | `4?` | `Track 4 (likely)` |
| Cancelled train | *(blank — no track pill at all)* | *(same)* |
| Inside overdue window, station posting tracks | `⚠️` | `⚠️ Track overdue` |
| ≤10 min out, not yet overdue | `—` | `Track due any minute` |
| ≤25 min out | `—` | `Track due in ~N min` |
| Station not posting tracks at all | `—` | `Track not posted` |

### Crowding glossary

| Level | Shown as |
|---|---|
| Heavy | `Busy` |
| Moderate | `Filling up` |
| Light / unknown | *(no pill — a quiet field stops being read)* |
| No consist data yet | `Boarding info isn't published yet.` |

### Other terms

- **Commute** — an origin/destination pair with favorites, alerts, and
  notifications attached. Not the same as a **board** (below).
- **Board** — a station with no destination configured; a live departure
  list with no commute semantics, no favorites, no notifications.
- **Direct train** — a one-seat ride between your two stations. The app
  shows these by default; a transfer-only fallback is always labeled as
  such (§4.9).
- **Favorite train** — a specific train number you've marked as one you
  actually take, labeled everywhere by its departure time rather than its
  number, because the number means nothing to a rider until they look it
  up.
- **Signaled track** — a platform guess from the train-tracking system,
  available at New York Penn ahead of the board's own posting roughly
  two-thirds of the time; always marked `(likely)` until the board
  confirms it.
- **Disrupted** — any of: a train cancelled, a train past your delay
  threshold, or a train named in a live alert, within your lookahead
  window.
- **Live incident vs. planned advisory** — a live incident means something
  is wrong right now; a planned advisory is scheduled maintenance/event
  service, worth remembering, not worth leaving early for.

---

## 10. Acceptance criteria (per screen)

**Home / Commute**
- [ ] Hero shows a train with no realtime data with no status pill, never
      "On time."
- [ ] A cancelled favorite occupies the hero slot; a cancelled non-favorite
      never does when a usable alternative exists.
- [ ] The corner tint reflects only the hero's own pills' worst tone, never
      the full board's.
- [ ] "Every upcoming departure cancelled" and "nothing scheduled" render
      as visibly distinct empty states.
- [ ] Board row track cell shows a bare confirmed track, a `?`-suffixed
      signaled track, or a dash — never a raw `null`/blank cell with no
      explanation.
- [ ] Stops-away line appears only while a favorite is being actively
      tracked and is on the board.

**Departure detail**
- [ ] Stop list marks departed stops distinctly from the next call and
      from the remainder.
- [ ] Crowding section shows the "boarding info not published yet" state
      whenever the underlying `cars` payload is empty, never a fabricated
      value.
- [ ] "About this train" never states a predicted future track for any
      train; it only ever describes the general station pattern and
      whether *this* train's actual posting was inside or outside it.
- [ ] Alerts naming this train render as quotes; absence renders as an
      explicit "no alerts name this train," never a blank section.

**Alerts**
- [ ] Live incidents render above planned advisories, always, regardless
      of recency.
- [ ] An alert naming a train currently on one of the user's own boards is
      visually distinguished from one that doesn't.
- [ ] `line_cancellation` inferences render with a visibly weaker/hedged
      badge than a confirmed hit and are never labeled identically to one.
- [ ] Empty state reads "No alerts on your lines right now," distinct from
      a feed-down state.

**Commutes list / switching**
- [ ] Board-mode entries (no destination) are visually and structurally
      separated from commute entries.
- [ ] The direction switcher reachable from Home never requires a full tab
      navigation round-trip.

**Favorites picker**
- [ ] A saved favorite absent from today's resolved schedule still appears
      (checked) rather than silently disappearing.
- [ ] Favorites are always labeled by departure time; there is no raw
      numeric-entry path in the primary flow.

**Settings**
- [ ] Delay threshold and lookahead sliders take effect without requiring
      an app restart or re-login.
- [ ] Removing a commute does not affect its sibling (reverse-direction)
      commute's settings.
- [ ] Location gating mode (zones vs. radius) is mutually exclusive in the
      UI and the effective radius default is computed and displayed
      consistently with what actually gates notifications.

**Onboard mode**
- [ ] Triggers automatically only once the tracked train's scheduled
      departure has passed and the train is still "yours."
- [ ] Arrival estimate is visibly labeled as a lower bound.
- [ ] "I got off / not on this train" ends onboard tracking immediately
      and returns to normal next-train tracking.
- [ ] Onboard mode is never terminated solely by the commute window
      closing while still en route.

**Empty / degraded states**
- [ ] A stale/cached board is visually distinguished (dimming, timestamp)
      from a live one and never presented as current.
- [ ] Transfer-only fallback rows carry an explicit "no direct service,
      requires a change we can't describe" banner, never rendered as
      ordinary direct rows.

**Notifications / Live Activity / widgets**
- [ ] No push fires for a state already true before the app started
      tracking it (cold start / relaunch baseline).
- [ ] Delay pushes are bucketed in 5-minute bands and do not re-fire
      within a band.
- [ ] `line_cancellation` never generates a push notification, only an
      in-app Alerts entry.
- [ ] A Live Activity/ongoing notification refresh is suppressed unless at
      least one of {train id, track, signaled track, status text,
      crowding} changed since the last shown state.
- [ ] Dismiss ends both the activity/notification and further tracking of
      that favorite until the commute window resets (or 3 a.m., absent a
      window).
- [ ] With no location permission, no zones, and no computable radius, the
      activity/notification still shows — fail-open is unconditional.
- [ ] Widgets never render a placeholder/guessed value where the
      underlying data is genuinely unknown; they render the same
      "nothing known" state as the main screens.

---

## 11. Open UX questions

1. **What does "account" concretely buy, at what backend cost?** §3's
   framing (reliable push + occasional early-track upside) is a product
   decision made for this spec, not something read off the corpus — the
   corpus's RailData account tradeoff is HA-integration-specific and rate
   limits (10 sign-ins/day, 40,000 realtime calls/day *per account*) don't
   obviously scale to "one backend account shared across every app
   install." Needs a real backend architecture decision before the
   onboarding copy in §3 ships as-is.
2. **Multi-valued destination.** SPEC §13.2 leaves open whether a commute
   should accept *either* of two destinations (e.g., "New York *or*
   Hoboken, whichever comes first"). Cheap to compute (union two train-ID
   sets) but not designed here — does it become a third onboarding mode,
   or an option on an existing commute?
3. **Weekend/holiday favorite behavior at scale.** SPEC §13.6 notes this
   is reasoned about but not watched from a running instance across an
   actual weekend or holiday. The favorites-picker fallback (§4.6) assumes
   it behaves as designed; needs real-world confirmation before the copy
   ("Not running today but saved") is trusted as always-correct framing
   rather than sometimes hiding a real problem.
4. **How aggressively should the app show Amtrak/other-operator rows** at
   shared stations (e.g., New York Penn, Trenton)? SPEC §3.8 explicitly
   warns the two operators must never be pooled when reasoning about
   timing (very different track-posting distributions), but says nothing
   about whether a commuter app should show non-NJT rows on a shared board
   at all, badge them differently, or filter them out entirely.
5. **Machine translation of raw alert/inline-message prose.** §8 punts on
   this deliberately; worth a real decision (show-English-only vs.
   best-effort translation with a visible disclaimer vs. some hybrid) with
   input from NJ Transit's own multilingual practices, not just app UX.
6. **What should the "Following a favorite that's cancelled" hero state
   suggest as a next action**, beyond showing the cancellation? The web
   card and dashboard both simply show it; a phone app has room for a
   direct "switch to next usable train" action that isn't designed here.
7. **Should board-mode entries (§4.5, no destination) ever support
   *any* notification** — e.g., "notify me if this whole station's board
   goes red" — or does the "no destination = no commute semantics"
   principle (15) hold absolutely? Left undesigned; the corpus is silent
   because HA's board-mode entries create no calendar and imply no
   automation target either.
8. **Onboarding order for users who already know they want a board, not a
   commute.** §3 treats "no destination" as a branch off the
   origin/destination step; a returning or savvy user might want a
   top-level "Board" vs. "Commute" choice before ever seeing a
   destination field. Not resolved.

---

## 12. Wants that the data can't support yet

These are explicitly **not** designed into the screens above, because the
underlying feeds — website or RailData — cannot supply them today. Listed
here so they aren't quietly re-invented in a future design pass without
someone checking the data first.

- **An arrivals board.** Neither source publishes arrivals; DepartureVision
  and this API are departures-only, confirmed by an explicit root-field
  search finding nothing *(SPEC §3.8)*. There is no "who's showing up on
  Track 9 in the next 20 minutes" screen possible with current data.
- **Predicted track for a specific future train.** The integration
  measured this directly and set a 60%-top-1-accuracy bar before shipping
  any such feature; nothing has cleared it, and the honest fallback (a
  late-track *signal*, not a track *guess*) is what §4.2 and §5 implement
  instead *(SPEC §7.2)*.
- **Arrival track**, at any station, for any train. Track is reported only
  for departures from the station being queried; there's no signal for
  which platform an inbound train will use *(SPEC §3.8)*.
- **Crowding for a non-imminent train.** The `cars`/consist payload is
  only populated for departures close to leaving (roughly a quarter of
  board rows at any moment); a screen asking "how full will my 8:34 be"
  two hours out cannot be answered *(SPEC §2.3)*.
- **A live map / vehicle position.** The signaling-track decode exists
  only at New York Penn and only resolves to a platform number, not a
  coordinate; there is no general train-position feed to plot on a map
  *(SPEC §2.9)*.
- **Full itinerary detail for a transfer trip** — which platform to
  change at, how long the connection is, whether it's cross-platform.
  The train-ID set used to build the destination filter deliberately
  drops this detail even where the underlying trip planner briefly has it
  *(SPEC §8.0: "the second leg and total duration are dropped")*. The
  fail-open transfer rows in §4.9 can say a change is required; they
  cannot say where.
- **An "on-time percentage" or reliability score per train or line**,
  beyond the coarse track-timing statistics already measured. Nothing in
  the corpus computes or validates a per-line or per-train punctuality
  rate; inventing one for the app would be presenting a number nobody has
  checked.
- **Real-time seat availability**, as opposed to per-section crowding.
  Crowding is a color-coded per-car fullness estimate, not a seat count.
- **Push-quality reliability without some backend involvement.** A fully
  offline, no-account, no-backend app cannot deliver a dependable "your
  train is cancelled" push while backgrounded on iOS — this is a platform
  constraint, not a data one, but it directly limits what "works without
  an account" (§3) can promise.
