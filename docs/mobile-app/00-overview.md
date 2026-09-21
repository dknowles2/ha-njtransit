# NJ Transit commuter app: end-to-end design

A standalone iOS/Android app with its own backend, built from scratch, for people who
take the same NJ Transit train every day. This is not the Home Assistant integration
repackaged. The integration is the research: two months of watching the real feeds taught
us what commuters actually need and where the data lies, and this design carries those
lessons into a product that doesn't need a home server.

Four documents:

| File | What it is | Owner |
|---|---|---|
| `00-overview.md` (this) | The E2E design, the decision log, and the map between the other three | synthesis |
| `01-ux-spec.md` | Screens, wireframes, notification behaviour, copy, acceptance criteria | UX designer |
| `02-architecture.md` | Topology, quota math, API contract, push pipeline, client stack, phases | architect |
| `03-user-journeys.md` | 53 journeys with IDs (`J-B1`), the implementation backlog | synthesis |

Read this one first. Read `03` to plan a sprint. Read `01` and `02` when building.

---

## 1. The product in one paragraph

You tell the app your commute (Short Hills → New York Penn, and back), and which trains
you usually catch. From then on it answers one question, glanceably, from the lock
screen: *is my train fine, and where do I stand?* A big countdown, the track (including
a "likely" track up to twenty minutes before the board posts it at NY Penn), how late it's
running, how full it is, and which stop it just left. It stays completely silent on a
normal day. On a bad day it tells you once, in one line, what broke and which train to
take instead. Once you're aboard it counts down to your arrival instead. Everything works
with no account; an account only syncs across devices.

## 2. What we're carrying over from the integration

These are the load-bearing findings. Each one shaped a decision below, and each is cited
back to where the integration learned it.

| Finding | Where it came from | What it forces |
|---|---|---|
| A commute, not a station, is the unit | README "Why a commute", SPEC §8.0 | Subscriptions are (origin, destination); the board is filtered to trains that serve the pair; the disruption question is "is *my* commute broken" |
| Neither feed is a superset of the other | README (train 6320 cancelled on the board, absent from alerts) | Backend correlates board and alerts on train number; the app never shows one without the other |
| `delay_minutes: null` ≠ `0` | `models.py`, README "status_text" | Every status render has a "nothing known" branch; `status_text` is computed server-side once |
| Track vs. signaled track vs. source | SPEC §2.9, `models.py` | Three fields, never merged; the board always wins; UI hedges with "(likely)" |
| NY Penn posts tracks at T-9.0 min, IQR 1.9 | `event.py`, SPEC §7.3 | "Track overdue" at 6 min is a measured signal, only at stations posting tracks, never for Amtrak |
| Only transitions notify; nothing replays on restart | `event.py` docstring | Server-side per-subscription "last delivered" state; cold start seeds silently |
| Delay pushes band at 5 minutes, re-fire only on escalation | `commute_disruption.yaml` | The per-subscriber fan-out compares bands, not raw minutes |
| `line_cancellation` is an inference | README events, `event.py` | In-app only, never pushed, never tints |
| Location can't tell WFH from commuting; a schedule can | README Live Activity section | Commute window is the primary gate; location is a client-side veto that fails open |
| The board drops a train the moment it leaves | SPEC §3.8, `TrainRun` | Onboard mode is fed by the stop list; ETA is a lower bound |
| RailData quotas are per account and small | SPEC §2.9 | Backend polls per station, never per user; RailData is used surgically; multiple accounts are a non-goal |
| Six station-name vocabularies keyed by one 2-char code | SPEC §3.5, §2.9 | The normalizer is a first-class service and its fixtures are contract tests |
| The private endpoint can break without notice | SPEC §12 | Drift detection, circuit breakers, failover, honest staleness in the UI |

## 3. E2E topology

```
           njtransit.com GraphQL              RailData API (quota-limited)
           (board, alerts, planner,           (schedule join, NY Penn
            coordinates)                       vehicle feed → signaled track)
                    │                                   │
                    └─────────────┬─────────────────────┘
                                  ▼
                 ┌──────────────────────────────────┐
                 │  Ingestion: one poll per          │  Python, asyncio; the
                 │  (station, source) per interval   │  extracted `api/` package
                 └──────────────┬───────────────────┘
                                ▼
                 ┌──────────────────────────────────┐
                 │  Normalizer → canonical Departure │  six vocabularies → one
                 └──────────────┬───────────────────┘
                                ▼
                 ┌──────────────────────────────────┐
                 │  Derived signals (once per train) │  disruption, transitions,
                 │  + signaled-track memory          │  overdue, knock-on, ETA
                 └───────┬───────────────┬──────────┘
                         ▼               ▼
              ┌────────────────┐  ┌──────────────────┐
              │ Postgres+Redis │  │ Redis Streams     │  one entry per transition
              │ hot board,     │  │ (station, train,  │
              │ ETags, history │  │  event_type)      │
              └───────┬────────┘  └────────┬─────────┘
                      ▼                    ▼
              ┌────────────────┐  ┌──────────────────┐
              │ REST /v1 API   │  │ Push fan-out:     │  per-subscriber threshold
              │ (FastAPI, ETag)│  │ join → dedupe →   │  and band, dismissed_until,
              └───────┬────────┘  │ APNs / FCM        │  Live Activity budget
                      │           └────────┬─────────┘
                      ▼                    ▼
              ┌──────────────────────────────────────┐
              │ iOS (SwiftUI) + Android (Compose)     │  shared KMP core for
              │ Home · Commutes · Alerts · Settings   │  countdown/ETA/threshold
              │ Live Activity · widgets · onboard     │  math and offline cache;
              │ location veto runs HERE, never server │  countdown ticks locally
              └──────────────────────────────────────┘
```

The one rule that must survive every future change: **the backend's unit of work is a
station, not a user.** A station with ten thousand subscribers and one with a single
subscriber cost the same one poll per interval. That's what keeps the RailData quota
math flat and the website load proportional to coverage, not to growth.

## 4. One event, traced end to end

8:03 AM. The board moves train 6662 from Track 4 to Track 7, three minutes before
departure. Here's the whole path, with the journey and document section for each hop.

| Step | Where | Doc |
|---|---|---|
| 1. Poller fetches the Short Hills board (one call, shared by every Short Hills commute) | Ingestion | `02` §2.2 |
| 2. Normalizer produces a canonical `Departure` for 6662 with `track: "7"` | Normalizer | `02` §3.3 |
| 3. Engine diffs against the last snapshot: `track` was `"4"`, now `"7"`, and a track *was* previously published, so this is `track_changed(previous="4", new="7")` and not a first assignment | Derived signals | `02` §5.1, `event.py` |
| 4. Event lands on the stream keyed `(RT, 6662, 2026-09-20T07:48, track_changed)` | Redis Streams | `02` §5.2 |
| 5. Fan-out joins to every subscription whose commute includes 6662 (favorite or on their board), checks `dismissed_until`, the commute window, and that this key hasn't been delivered | Fan-out | `02` §5.1–5.3 |
| 6. APNs time-sensitive push: "Train 6662 moved from Track 4 to Track 7." plus a Live Activity content update with the new track | APNs | `01` §5, `J-C2` |
| 7. Phone applies the location veto locally (are you somewhere this train could still be caught from?); if unknown, fails open and shows it | Client | `01` §5 "The location rule", `02` §4.3 |
| 8. Lock screen: Live Activity now reads `6662 · Track 7 · On time`; the countdown never stopped ticking because it's computed on-device from `scheduled + delay_minutes` | Client | `02` §3.4 |
| 9. Tap → Departure detail for 6662, with the stop list and the alert list | Client | `01` §4.2, deep link `njtransit://commute/{id}/departure/6662` |
| 10. Track history records (RT, 6662, posted 7:54, changed 8:03) for the station stats surface | Postgres | `02` §7 |

Target: step 1 to step 8 in under 90 seconds (one poll interval plus push latency).

## 5. Screens ↔ endpoints ↔ backend ↔ data

The map engineers need when a journey touches all three layers.

| Screen (`01`) | Primary journeys (`03`) | Endpoints (`02` §3.2) | Backend components | Upstream data |
|---|---|---|---|---|
| Onboarding | A1 A2 A3 A4 | `GET /v1/stations`, `GET /v1/stations/nearby`, `POST /v1/devices`, `POST /v1/commutes`, `PATCH /v1/commutes/{id}`, `POST /v1/devices/{id}/push-token` | API, station cache | station list, proximity search (website only), day's trips |
| Home | B1 B3 C1 C3 C4 E1–E6 K1–K7 | `GET /v1/commutes/{id}/board` (ETag) | API, hot cache, derived signals | board, alerts, stop list (favorite only), vehicle feed (NY) |
| Departure detail | B2 K5 L3 | `GET /v1/departures/{station}/{train}?scheduled=`, `POST /v1/reports` | API, stop-list cache | stop list, cars, alerts by train |
| Alerts | E3 E4 F1 F2 F3 | `GET /v1/alerts?lines=` | API | system status, station messages |
| Commutes list / Board mode | B3 J1 J2 J3 | `GET/POST/DELETE /v1/commutes` | API | board (unfiltered for board mode) |
| Favorites picker | A2 G1 G2 | `GET /v1/commutes/{id}/trips`, `PATCH /v1/commutes/{id}` | API, route refresh at 01:45 | planner / `getStationSchedule` join |
| Settings (global) | A4 H4 L1 L2 | `POST /v1/auth/*`, `POST /v1/accounts/merge-device`, `DELETE /v1/me` | API, accounts | — |
| Settings (per commute) | G3 G4 G5 G6 H4 | `PATCH /v1/commutes/{id}` | API; window and prefs mirrored to fan-out | — |
| Onboard mode | D1 D2 | board + departure detail | stop-list follower for followed trains | stop list (`departed`, per-stop status) |
| Live Activity / ongoing notification | H1 H2 H3 H5 | `POST /v1/devices/{id}/live-activity/push-to-start-token`, `POST /v1/commutes/{id}/live-activity` | fan-out, APNs/FCM | transitions stream |
| Widgets / watch | I1 I2 | `GET /v1/commutes/{id}/board` | API | board |
| Timetable / calendar | B4 | `GET /v1/commutes/{id}/trips?date=` | route refresh | planner / station schedule |
| Station stats | L4 C1 | `GET /v1/stations/{code}/history-summary` | track history | recorded postings |

## 6. Decision log

Decisions made while synthesising, including where the two specialists disagreed and how
it was settled. Each is final unless reopened deliberately.

| # | Decision | Rationale | Alternatives rejected |
|---|---|---|---|
| D1 | Standalone backend; the HA integration's `api/` package is extracted and shared | It already has no HA imports and carries every parsing trap plus recorded fixtures. Rewriting it in Go would re-derive the same bugs | Go/Node from day one |
| D2 | Website GraphQL is the primary ingestion source network-wide; RailData is used surgically (schedule join, NY Penn signaled track) | One RailData account's 40k/day covers ~27 stations at 60 s board-only, ~13 with the vehicle feed, against 167 stations. Multiple accounts are an explicit non-goal | RailData-primary; multi-account |
| D3 | REST + ETag, countdown ticks on-device | The value is server-computed opinionated fields; iOS background execution is hostile to streams; most foreground polls are 304s | GraphQL, gRPC, SSE/WebSocket |
| D4 | Native SwiftUI + Compose, shared KMP core for non-UI logic | Live Activity, widgets and complications are exactly where cross-platform bridges lag; the tricky math (rollover, DST, null-vs-zero) should exist once | Flutter, React Native, pure native x2 |
| D5 | Anonymous device-scoped by default; account is sync only | "Everything works without an account" is a product promise, not a nag. Account reframed away from RailData credentials, which never touch the app | Required sign-in |
| D6 | Location never reaches the server (one exception: the nearby-station lookup, never persisted) | The blueprint's zones/radius rule ports to the client as a veto on notifications that already fired on time-only criteria; fails open when unknown | Server-side geofencing |
| D7 | Onboarding creates both directions from one form | The integration needed two config entries only because HA made it so | One direction at a time |
| D8 | `line_cancellation` is in-app only, never pushed, no preference toggle | It's an inference. Pushing an inference at the same urgency as a fact trains people to ignore pushes. (UX designer's call; architecture and journeys updated to match) | Off-by-default push toggle |
| D9 | `delayed` pushes on threshold crossing, then only on escalation into a worse 5-minute band; never on recovery | Direct port of `commute_disruption.yaml`, which exists because the unbucketed rule sent five pushes in seven minutes. (UX designer's call, verified against the blueprint; architecture and journeys updated) | Once per crossing only |
| D10 | Hero numeral is exempt from full Dynamic Type scaling | Arm's-length legibility on a platform is the point; the card's own CSS already caps it. Flagged as a deliberate accessibility trade-off in `01` §7 | Scale everything |
| D11 | No track prediction, ever, from history | SPEC §7.2 set a 60% bar the data hasn't cleared. "Likely" only ever means the signaling system has seen the set on a platform | Predict from median posting |
| D12 | Silence is a feature with an acceptance test | A week of normal commutes must produce zero pushes (`J-E5`) | — |
| D13 | Fixture-based contract tests run hourly against live upstream in production | Drift detection is how the integration survived; at scale it's also the pager | Wait for user reports |
| D14 | iOS first, Android in v1 | Live Activity is the differentiator and the MVP validation surface | Simultaneous launch |

## 7. Phases

Condensed from `02` §10 and the coverage matrix in `03`.

**MVP (iOS only, anonymous, standard push).** Website ingestion for stations that
configured commutes touch; normalizer; disruption evaluation and the four core
transitions (cancelled, delayed, track_changed, alerted); REST API with ETag; onboarding
with nearest-station suggestion and both directions; Home with hero, tint-by-worst,
board, stops-away for favorites; Departure detail; Alerts; whole-board mode; per-commute
delay threshold, lookahead, commute window, favorites; offline and degraded states;
track-history recording starts now so the dataset exists by v1; quota dashboard and
drift contract tests. Journeys: A1–A3, B1–B3, C1–C3, D1 (in-app), E1–E3, E5, E6, F1, F3,
G2, G3, G5, H4, H5, J1–J3, K1–K4, K6, K7, L1, L2, M1, M2.

**v1 (Android, Live Activity, accounts).** ActivityKit push-to-start and content
updates with the no-op suppression and Dismiss; Android ongoing notification; home-screen
widget; optional account and multi-device sync; client-side location veto; circuit
breakers and website↔RailData failover; status page; station track-posting stats;
knock-on surfaced in-app; timetable/calendar view; behaviour-based favorite suggestions;
bad-data reports. Journeys: A4, B4, C4, D1 (Live Activity), E4, G1, G4, H1–H3, I1, K5, L3,
L4.

**v2.** Multi-destination commutes; per-station overdue thresholds from rolling history;
connection-aware ETA; lock-screen widget and watch complication; automated circuit-decoder
fitting with the 95%-over-200-postings gate; planned-advisory reminders; pursue an
elevated RailData tier with NJ Transit. Journeys: D2, F2, G6, I2, M3.

## 8. Risks that gate launch

The full register is in `02` §10. These three are the ones that can stop the product
rather than slow it:

1. **Legal exposure of the private endpoint at commercial scale.** Needs counsel before
   any broad public launch, independent of engineering mitigations. Not decided here.
2. **RailData quota.** Today's architecture works within one account by design, but
   "which stations get signaled-track fidelity" is a config decision driven by real
   subscription data, not a launch promise. An elevated tier from NJ Transit is the single
   biggest lever on the whole design.
3. **Website drift.** Mitigated (fixtures, breakers, failover, honest staleness), not
   eliminated. The app must never show a false "On time" or send a push from stale data
   (`J-K1`).

## 9. Open questions, combined

Deduplicated across all three documents, with where each lives.

| Question | Raised in | Blocks |
|---|---|---|
| Will NJ Transit grant a higher-volume RailData tier? | `02` §11.1 | v2 scale; broader signaled-track coverage |
| Legal risk tier of the private endpoint at scale | `02` §11.2, §1.4 | Public launch |
| Does ActivityKit push-to-start let client code veto before rendering? Needs a spike | `02` §11.3 | v1 location rule for Live Activity |
| Real Live Activity update budget under this usage pattern | `02` §11.4 | v1 tuning |
| Multi-destination commutes: third onboarding mode or an option? | `01` §11.2, `02` §11.5, SPEC §13.2 | v2 `J-G6` |
| Weekend/holiday schedule correctness, never watched from a running instance | `01` §11.3, `02` §11.7, SPEC §13.6 | Favorites copy in `J-A2` |
| Show, badge, or hide Amtrak rows at shared stations? | `01` §11.4 | Home board at Trenton/NY Penn |
| Machine-translate raw alert prose for Spanish, or English-only with disclaimer? | `01` §11.5, §8 | Localization |
| What action does the "favorite cancelled" hero offer beyond routing around it? | `01` §11.6 | `J-E1` polish |
| Any notifications at all for board-mode (no destination) entries? | `01` §11.7 | `J-J3` scope |
| Top-level "Board vs Commute" choice in onboarding for savvy users? | `01` §11.8 | `J-A1` variant |
| Auto-fit circuit decoders from collected pairs, or hand-derive per station? | `02` §11.6 | `J-M3` |
| Expose the normalized board as a partner API? | `02` §11.8 | Product strategy |
| Monetization tier, and how it preserves "no account needed"? | `02` §11.9 | Product strategy |

## 10. Where to start

If this were being built next week:

1. Extract `custom_components/njtransit/api/` into its own package with its fixtures
   (D1). Both the integration and the backend consume it. This is a day of work and it's
   the foundation of everything else.
2. Stand up ingestion + normalizer + `GET /v1/commutes/{id}/board` for one station pair,
   on Fly.io/Render with managed Postgres and Redis (`02` §2.3). Serve the canonical
   `Departure` JSON from `02` §3.3.
3. Build Home (`01` §4.1) in SwiftUI against it, with the countdown ticking on-device.
   That's `J-B1`, the journey the product exists for.
4. Add the transition engine and `track_changed` push. That's the trace in §4 above, and
   it exercises every layer once.
5. Then work the MVP journey list in `03` in the order of the coverage matrix.
