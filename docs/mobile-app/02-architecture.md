# NJ Transit commuter app: architecture

This is the backend and client architecture for a standalone iOS-first, Android-second
commuter app, built from scratch, not on Home Assistant. The research corpus is
`ha-njtransit`: a working HA integration that spent real time discovering what NJ Transit's
two feeds actually do, as opposed to what a person would guess. Every hard-won fact in
SPEC.md (the API traps, the parsing rules, the derived signals) is a constraint on this
design, not background reading. Where a number appears below with no citation it comes
directly from SPEC.md or the `api/`, `coordinator.py`, `event.py`, `track_history.py`, or
`binary_sensor.py` source. Where I'm extrapolating past what the corpus proves, I say so.

This document is a companion to `03-user-journeys.md`, which was written against this
architecture's endpoint and data shapes; the two should stay in sync. It also assumes a
`01-ux-spec.md` will exist for screen-level detail, which this document does not attempt.

Sections 1 to 11 below correspond to the task's numbering.

---

## 1. Constraints inherited from upstream, restated for thousands of phones

### 1.1 Two sources, one decision per station

| | Website GraphQL | RailData API |
|---|---|---|
| Access | `POST njtransit.com/api/graphql/graphql`, no auth, WAF in front | `POST raildata.njtransit.com/api/TrainData/<method>`, form-encoded, needs a developer account + token |
| Documentation | None. Reverse-engineered from Nuxt bundles. Introspection disabled. | A versioned PDF ("RailData API v2.1"). Compatibility promise. |
| Quota | None documented. Site's own CDN sends `maxAge: 30`, so 30s is a courtesy floor, not an enforced limit. | Three hard daily caps, per account: `getToken` x10, `getStationSchedule` x5, realtime methods (everything else) x40,000. |
| Board cap | 19 rows, same field | 19 rows (`getTrainSchedule19Rec`), same cap |
| Arrivals | None. Departures only. | None. Same product (DepartureVision) behind it. |
| Track | Board only, posted late (median ~8.8 min before departure at NY Penn, terminal-dependent) | Board, same lateness, **plus** `getVehicleData`: the signaling system's last-known track circuit, decodable to a platform at stations whose circuit map has been worked out. Only NY Penn is decoded today. |
| Alerts | `getSystemStatus`: live incidents and planned advisories, `advisoryAlert` flag | `getStationMSG` with no station/line: live incidents only, `is_advisory` always false |
| Coordinates | Yes, via the trip planner (`getTripPlannerAlternates`) | No, at any price |

Both sources agree on the two facts that make this whole product possible and also make it
hard: **the board and the alert feed are neither a superset of the other**, and correlating
them on train number is the reason to build a backend instead of shipping a dumb API
client. The recorded 2026-08-03 disruption is the regression fixture for this: the alert
feed named trains 309, 6311, 6324, 6607; the board simultaneously showed 6320 cancelled,
named nowhere in an alert. A client that reads only one feed misses real failures in both
directions.

### 1.2 Structural constraints that don't get easier with scale

These don't change because the consumer becomes a service instead of a house. They shape
the normalizer and derived-signal engine regardless of how many phones are behind it:

- **A single null field nulls the whole response.** GraphQL selections must stay pinned to
  exactly what njtransit.com's own bundles request. A backend that widens a selection to
  "just grab everything, we're a real service now" will get `data: null` back the first time
  it asks for a non-nullable field upstream can't populate.
- **Bare wall-clock times, no date, no zone.** Every timestamp resolution needs the same
  3-hour-window rollover logic SPEC 6.1 describes, done once in the normalizer, in
  `America/New_York`, with explicit DST fold handling. Getting this wrong for one station is
  getting it wrong for every subscriber of that station.
- **Unstable status vocabulary**, inconsistent casing, assumed incomplete. The normalizer
  degrades to `unknown` and preserves the raw string; it never raises. A vocabulary change
  upstream should show up as a new value flowing through cleanly with a low-priority alert,
  not a 500.
- **Six station-name vocabularies, one stable key.** The 2-character code (`pentaStationID`
  on the website, `STATION_2CHAR` on RailData, the same string on both) is the only thing
  safe to use as an identifier anywhere in this system: in the DB schema, in API URLs, in
  cache keys, in push payloads. The six vocabularies (canonical website titles with alias
  rows, board headsigns, trip-planner display casing, stop-list names, proximity-search
  names, RailData's own titles) are display strings resolved once in the normalizer and
  never compared to each other directly again. This is the single most important porting
  decision from the HA integration: `_same_station()`'s word-set comparison in `models.py`
  and the alias-dedup logic in the config flow are not incidental code, they're the reason
  station identity works at all, and they move into the normalizer verbatim.
- **`getTripPlannerSchedule` (and RailData's schedule join) return three items per call and
  five calls a day respectively**, so a day's timetable is built by paging, not by asking
  once. This cost is per (origin, destination) pair, per day, not per subscriber (section
  1.3 covers what that means for cost at scale).
- **`getVehicleData` only lists trains that moved in the last 5 minutes.** A train sitting on
  its platform drops out of the feed while it waits. The signaled-track memory
  (`RailDataClient._signaled`, `_platforms` dict keyed on station/train/scheduled-departure)
  has to be held in server-side state exactly the way the HA client holds it in-process, and
  cleared on the same rules: replaced by a later sighting, cleared by a sighting that isn't a
  platform, dropped once the train leaves the board.
- **Alerts are prose, not structured data.** Train-number extraction
  (`r'\btrain\s+#?(\w{1,5})\b'`, minus "Please take" substitutes) runs once per alert message
  in the normalizer, not once per subscriber reading the alert.
- **No arrivals feed, and arrival track is unobtainable at any price** (website or RailData).
  The product's "aboard" mode has to be built on the stop list and a lower-bound ETA
  (`TrainRun.eta_at`), never a claim of a real arrival track.

None of this is solved by having a bigger server. It's solved by doing the parsing, the
station-name resolution, and the delay math exactly once per (station, train) per poll,
centrally, and serving every subscriber a read of that one canonical answer. That's the
core architectural move this whole document is built around, and it's why the ingestion
layer is designed around *stations*, not around *requests from phones*.

### 1.3 The constraint that does change with scale: RailData quota math

A single household polling one or two commutes, as the HA integration does, spends "under
3,000 calls a day" against a 40,000 budget, per the README. A backend serving a metro area
is not one household. Here is the arithmetic that matters:

```
Realtime budget per RailData account:        40,000 calls / day
Board poll at 60s (the vendor's own floor):    1,440 calls / day / station
Vehicle feed poll at 60s (signaled track):     1,440 calls / day / station
Both together, one station, full fidelity:     2,880 calls / day / station

Max stations, board only, one account:        40,000 / 1,440  ≈  27 stations
Max stations, board + vehicle, one account:   40,000 / 2,880  ≈  13 stations

Full-network board coverage (167 stations):   167 x 1,440    = 240,480 calls / day
                                               ≈ 6.0x a single account's entire budget,
                                               for boards alone, no vehicle feed at all
```

One account cannot cover the network at a live cadence. The `getStationSchedule` cap (5 a
day) is comfortable by comparison: it's one call per station-day, cached and never re-fetched
(exactly `RailDataClient._schedule`'s behavior today), so a day's full 167-station timetable
costs 167 calls once a day, well inside even a single account if spread across two calendar
days, and the actual product only needs the origin/destination pairs subscribers have
configured, which is a small fraction of 167 x 167.

Two things make this less alarming than the raw arithmetic suggests:

1. **RailData call volume is a function of stations, not subscribers**, because the
   ingestion layer polls per station and fans out (section 1.2's point, restated in dollars).
   100,000 MAU does not multiply this number. Only the number of *distinct stations someone
   somewhere is watching* does, and that's bounded by the 167-station network regardless of
   user count.
2. **The website has no comparable quota.** It's the thing to lean on for network-wide
   coverage; RailData is the thing to lean on for the one capability the website structurally
   cannot provide (signaled track).

That leads to the recommendation:

**Use the website GraphQL endpoint as the default ingestion source for board and alert
polling across the network**, the same default the HA integration ships with, because
nothing else can afford full coverage. **Use one (initially) RailData developer account
surgically**, scoped to:

- The daily schedule join, for every configured commute pair (well inside the 5/day
  `getStationSchedule` cap because it's cached per station-day, not per commute; two
  commutes sharing a station pair, or sharing one leg, cost no extra calls).
- `getVehicleData` polling, restricted to stations with a working circuit decoder (today:
  NY Penn only) and, at first, restricted further to the pre-departure lead-time window for
  trains someone is actually subscribed to, not a 24-hour poll of the whole station. At NY
  Penn's typical evening peak this is a handful of trains at a time, not the full 19-row
  board.
- A held-back reserve so account lockout from a bug never means the app goes fully dark;
  operational discipline here (a dashboard against the 40,000/day/10-a-day/5-a-day caps,
  alerting well before the ceiling) matters more than clever call-shaving.

Which stations "deserve" full-fidelity RailData coverage should be decided from
subscription telemetry once the app has users, not guessed up front from a list of
"obviously busy" stations. The architecture should make adding a station to the
high-fidelity tier a config change, not a code change: `has_decoder(station_code)` plus a
priority-ordered poll budget, not a hardcoded station list.

**Do not run multiple RailData developer accounts to multiply the quota.** The quota exists
specifically to bound one account's realtime traffic; sharding a commercial, multi-tenant
consumer across several personal-looking developer registrations to defeat that is the kind
of pattern that gets accounts disabled and, more importantly, is the kind of thing to ask NJ
Transit about directly rather than route around. RailData is a *documented, versioned*
API with, per the docs bundled with a developer account, a real point of contact behind it.
The actual unlock for a multi-tenant product is a conversation with NJ Transit about a
higher-volume or commercial-tier account, not a workaround. This is a business development
task, not an engineering one, and it should start early: the architecture below is designed
to work acceptably without it (website-primary, RailData-surgical) but would simplify a lot
if NJ Transit is willing to grant it.

### 1.4 Legal and ToS risk of the private endpoint at scale

The HA integration's own README states this plainly for a single household: "no
authentication, no documentation, no compatibility promise... it can break without notice."
That's a technical risk statement. At commercial, multi-tenant scale a second question
opens up that the HA project never had to answer: does querying an undocumented, WAF-fronted
endpoint behind a public agency's consumer website, at a volume representative of thousands
of daily users, expose the company to a terms-of-service or (in the US) CFAA-adjacent claim,
distinct from the risk profile of one enthusiast's home automation?

I don't have a basis in this corpus to answer that, and it isn't an engineering question.
**Recommendation: get this reviewed by counsel before this architecture goes to production
at any scale beyond a private beta**, specifically on: whether njtransit.com's terms of use
(if any) address automated access; whether the WAF's presence implies an access-control
posture a court would weigh differently than "public webpage, no login wall"; and whether
identifying the client honestly (a descriptive User-Agent, rate limiting to genuinely
site-like traffic patterns, no attempt to defeat the WAF beyond the named-operation/
`variables` requirement SPEC 3.2 already documents as necessary just to get a valid
response) changes that calculus. In parallel, the RailData relationship in section 1.3 is
also the legal team's best lever: a documented API with a developer agreement is a much
better foundation to build a company on than a reverse-engineered one, and reducing reliance
on the website over time (v2 in section 10) should be treated as risk reduction, not just an
engineering nice-to-have.

---

## 2. Topology

### 2.1 Diagram

```
 Upstream
 +----------------------------+     +----------------------------+
 |  njtransit.com GraphQL     |     |  RailData API               |
 |  (private, WAF, no quota)  |     |  (documented, quota-limited)|
 +--------------+-------------+     +--------------+---------------+
                |                                  |
                +----------------+-----------------+
                                 |
                       +---------v----------+
                       |  Ingestion/Poller   |   one scheduled job per
                       |  service            |   (station, source); demand-
                       |                      |   weighted interval, shared
                       +---------+----------+   across every subscriber
                                 |
                       +---------v----------+
                       |  Normalizer         |   6 station-name vocabularies
                       |                      |   -> one canonical model;
                       |                      |   time/status/crowding parsing
                       +---------+----------+
                                 |
                       +---------v----------+
                       |  Derived-signal     |   disruption eval, transition-
                       |  engine             |   only events, track-overdue,
                       |                      |   line_cancellation, signaled-
                       |                      |   track memory, favorite/
                       |                      |   stops-away/ETA lower bound
                       +----+-----------+----+
                            |           |
              +-------------v-+     +---v--------------+
              |  Store         |     |  Event stream     |
              |  Postgres:     |     |  Redis Streams:    |
              |   devices,     |     |  one entry per     |
              |   commutes,    |     |  transition, keyed |
              |   track history|     |  (station, train,  |
              |  Redis:        |     |   event_type)      |
              |   hot board    |     +---+----------------+
              |   cache, ETags |         |
              +-------+--------+  +------v-------+
                      |           |  Push fan-out |   joins each event to every
              +-------v--------+  |  worker       |   subscription watching that
              |  API gateway    |  |               |   train; evaluates each
              |  REST, auth,    |  |  per-subscriber|  subscriber's own delay
              |  ETag/304       |  |  threshold eval|  threshold; dedupes; decides
              +-------+--------+  +------+--------+  silent vs. alert vs. Live
                      |                  |            Activity update
              +-------v--------+  +------v-------+
              |  Mobile clients |  |  APNs / FCM   |
              |  iOS / Android  |<-+               |
              +-----------------+  +---------------+
```

### 2.2 Why per-station, not per-request

The load-bearing idea in this diagram, and the one thing that must not be compromised as
the product grows, is that **the ingestion layer's unit of work is a station, not a
subscriber**. Two commutes sharing an origin already share one board poll in the HA
integration (`CoordinatorStore.board_for`, refcounted); this architecture generalizes that
from "two config entries in one Home Assistant" to "N devices anywhere." A station with
10,000 subscribers and a station with 1 cost the backend the same one poll per interval.
This is what keeps section 1.3's RailData math from getting worse as MAU grows, and it's
what keeps the website-polling load proportional to network coverage rather than to user
count.

### 2.3 Component recommendations

| Component | Recommended | Alternative 1 | Alternative 2 | Why |
|---|---|---|---|---|
| Ingestion, normalizer, derived-signal engine | **Python 3.12+, asyncio** | Go | Node/TypeScript | The `api/` package in `ha-njtransit` already has zero Home Assistant imports, takes an injected `aiohttp.ClientSession`, and raises its own exceptions (AGENTS.md's layering rule, enforced by a test). It is, today, a standalone library that happens to live inside an HA repo. Extracting it to its own package and consuming it from both the HA integration and this backend means every hard-won parsing rule, every fixture-based regression test, and every future fix to upstream drift benefits both products from one change. Go or Node would mean re-deriving the same traps (the 3-trips-per-call planner bug, the `Morristown Line` alias, the letterless-circuit sighting rate) from scratch, with no guarantee of matching test coverage. This is a case where "best language for raw throughput" loses to "we already paid for this code to be correct." |
| API gateway | **Python (FastAPI)** for MVP, plan a Go rewrite of the hot path if it becomes the bottleneck | Go (from day one) | Node/TypeScript | One language across ingestion and API keeps a small team from context-switching, and the gateway's job (auth, ETag negotiation, projecting a cached canonical board onto a subscriber's commute filter) is not CPU-bound. Revisit only if connection concurrency or request volume at v2 scale makes it the bottleneck; Go's cheap goroutines suit a high-fanout HTTP layer well if that day comes. |
| Push fan-out worker | **Python** at MVP/v1, **Go** worth planning for v2 | Node/TypeScript | -- | Fan-out is the piece most likely to need real concurrency at scale (evaluating N subscriptions per transition event, calling two push providers with their own rate limits). Start where the rest of the backend already is; move this one worker to Go first if push volume, not API traffic, turns out to be the bottleneck. |
| Relational store | **Postgres** (managed: RDS, Neon, Supabase, Cloud SQL) | SQLite + Litestream (pure MVP, single region) | MySQL | Devices, commutes/subscriptions, accounts. Postgres from day one avoids a migration later; SQLite is viable at 1k MAU but not worth the eventual rewrite risk to save a small managed-Postgres bill. |
| Hot cache / dedupe / event stream | **Redis** (managed: Upstash, ElastiCache, Redis Cloud) | Memcached (cache only, no streams/sets) | -- | Current-board cache, ETag values, per-subscription "last notified state" (mirrors `event.py`'s `_Seen` snapshot, held server-side per subscription instead of per HA instance), and Redis Streams for the transition-event queue. Memcached can't hold the dedupe sets or streams this needs. |
| Track-history / time-series store | **Postgres** (a dedicated schema, or TimescaleDB if self-hosting/using Timescale Cloud) | ClickHouse | -- | Volume is small: a few hundred rows per polled station per day, retained 30-90 days, for a station count in the tens, not 167 (section 7). ClickHouse is the right call once retention and station coverage both grow a lot (v2+); it's premature operational overhead before then. |
| Queue between ingestion and fan-out | **Redis Streams** (reuse the Redis already deployed) | NATS JetStream | Kafka | The event volume at MVP/v1 (transitions per station-train, not per poll) doesn't justify a dedicated broker. Move to NATS/Kafka when a second independent consumer (e.g. an analytics pipeline reading the same transition stream) needs its own consumer group, or when Redis Streams' single-node throughput becomes the limit. |
| Push | **APNs (HTTP/2, token-based .p8 key)** direct, **FCM HTTP v1** direct | A unified provider (OneSignal, Courier) | -- | Live Activity content-state updates and ActivityKit push-to-start have specific payload shapes that are new enough, and central enough to this product's identity, to want direct control rather than a third-party abstraction's lag catching up to Apple's changes. Revisit a unified provider only if running two push integrations becomes a real operational burden, not before. |
| Hosting | **Fly.io or Render** for MVP/v1 | AWS (Fargate + RDS + ElastiCache) | Google Cloud Run + Cloud SQL + Memorystore | NJ Transit's user base is geographically concentrated in one timezone; there's no case for multi-region complexity here even at 100k MAU. Fly.io/Render minimize ops tax for a small team. Move to AWS/GCP if v2 scale or a compliance requirement (SOC2, VPC peering with a partner) demands it; the app-layer code doesn't need to change to make that move since none of the above choices are cloud-proprietary. |

### 2.4 Cost estimate by MAU

These are order-of-magnitude engineering estimates, not vendor quotes, and they lean on one
structural fact worth restating: **ingestion cost is close to flat across MAU tiers**,
because it's bounded by station count (167, in practice far fewer polled at high fidelity),
not subscriber count. What scales with MAU is API request volume (mostly cheap 304s behind
ETag), push volume, device/subscription row count, and observability/log volume.

| | 1,000 MAU | 10,000 MAU | 100,000 MAU |
|---|---|---|---|
| Ingestion + normalizer + derived-signal engine | 1 small instance, ~flat cost across all three tiers | same | same, maybe 2 for redundancy |
| API gateway | 1 small instance | 2-4 instances behind a load balancer | autoscaled fleet |
| Push fan-out worker | shared with gateway process | its own small instance | its own small fleet |
| Postgres (managed, small/medium/large + replica at top tier) | ~$15-25/mo | ~$50-100/mo | ~$200-400/mo |
| Redis (managed) | ~$10-15/mo | ~$30-50/mo | ~$150-300/mo |
| Push (APNs free; FCM free) | $0 | $0 | $0 |
| Observability/logging | ~$0-20/mo (free tiers) | ~$30/mo | ~$300-500/mo |
| Compute total | ~$40-80/mo | ~$150-250/mo | ~$800-1,500/mo |
| **Rough monthly total** | **~$100-150** | **~$300-500** | **~$1,500-2,500** |

The 100,000-MAU column assumes push notification volume in the low tens of thousands a day
(most days produce zero pushes per subscription per J-E5's stated design goal: silence is
the point) and API traffic dominated by ETag-cached 304 responses rather than full board
payloads. If those assumptions are wrong in production, push and gateway compute are the
line items to re-budget, not ingestion.

---

## 3. Client-server contract

### 3.1 Protocol decision

| | REST | GraphQL | gRPC | SSE / WebSocket |
|---|---|---|---|---|
| Fits a fixed, known set of views (board, detail, alerts, stations, favorites) | Yes | Over-provisioned: this product has no ad hoc query shapes to justify a query language | No real benefit; this isn't a service mesh | Fits push, not request/response |
| Caching (ETag / conditional GET) | Native, cheap | Needs custom plumbing per query | No standard equivalent | N/A |
| Works with iOS background execution limits | Yes: normal HTTP, resumes fine | Yes, same caveat as REST | Poor: persistent connections don't survive backgrounding reliably | Poor, same reason |
| Countdown ticking without polling | Solved client-side (section 3.4), independent of protocol | same | same | same |
| Debuggability (curl, Charles, browser devtools) | Best | Good, but harder to eyeball | Worst for a small team without existing gRPC tooling | Fine for SSE, awkward for WS |
| Recommendation | **Yes** | No | No | Not needed at MVP/v1; see below |

**Recommendation: REST, with ETag/If-None-Match, no SSE or WebSocket for MVP/v1.** Every
journey in `03-user-journeys.md` is written against this: the board is fetched with a
conditional GET, the countdown ticks locally, and time-critical updates arrive as push
notifications that include a "go re-fetch" hint rather than as a live stream. This is a
deliberate fit to how iOS treats backgrounded network connections (badly, for anything but
short-lived requests and push) and to the fact that the board itself only changes on the
order of once every 30-60 seconds even at its most volatile. A foreground poll every
15-30 seconds against a warm ETag is simpler to reason about, simpler to debug, and cheaper
to operate than holding a stream open per device. If a later measurement shows foreground
polling is too chatty at real scale, SSE is a narrow, additive change (one more endpoint),
not a rewrite; there's no reason to pay its complexity now.

GraphQL specifically is rejected for a reason worth stating plainly, because the upstream
side of this whole system uses it: this backend's value is that it returns
**server-computed, opinionated fields** (`status_text`, `track_source`, `favorite`,
`stops_away`) rather than raw upstream data a client would have to assemble. A query
language's flexibility is a mismatch for an API whose entire point is "don't make the
client re-derive what the server already knows."

### 3.2 Endpoints

| Method & path | Purpose |
|---|---|
| `GET /v1/stations` | Canonical station list (title, 2-char code, accessible), deduped one row per code. Cached client-side and bundled for offline first-run. |
| `GET /v1/stations/nearby?lat=&lon=` | Proximity search. The one endpoint where coordinates transit the backend; never persisted or attached to a device/account record (section 4.3). |
| `POST /v1/devices` | Register an anonymous device. Body carries an App Attest (iOS) / Play Integrity (Android) attestation and a device public key; returns a bearer credential for subsequent calls (section 9.2). |
| `POST /v1/devices/{id}/push-token` | Attach an APNs/FCM push token, called only once notification permission is granted (may be well after `POST /devices`, per J-A3). |
| `POST /v1/devices/{id}/live-activity/push-to-start-token` | Register the app-wide ActivityKit push-to-start token (iOS 17.2+). |
| `POST /v1/commutes/{id}/live-activity` | Register the per-activity update token once an activity has started (server- or client-started), so subsequent content updates can target it. |
| `POST /v1/commutes` | Create a commute: `origin` code, `destination` code or omitted (whole-board mode), initial options. |
| `GET /v1/commutes/{id}` | Read a commute's configuration. |
| `PATCH /v1/commutes/{id}` | Update options: `delay_threshold`, `lookahead_minutes`, `favorites`, `commute_window`, `notify_prefs`. Applies immediately, no reload semantics on the server side (there's no config-entry-reload concept here; the HA integration's "favorites apply live, other options reload the entry" distinction doesn't carry over because there's nothing analogous to unloading a config entry). |
| `DELETE /v1/commutes/{id}` | Remove a commute. |
| `GET /v1/commutes/{id}/board` | The merged, per-subscriber view: departures filtered to the commute, `status_text`/`track_source`/`favorite`/`crowding`/`stops_away` already computed, disruption summary, alert summary. ETag-cacheable. |
| `GET /v1/commutes/{id}/trips?date=` | The day's scheduled trips (timetable view, calendar-equivalent), refreshed server-side at 01:45 local same as `RouteCoordinator`. |
| `GET /v1/departures/{station}/{train_id}?scheduled=` | Departure detail: full stop list, per-car crowding, alerts naming this train. The `scheduled` query param disambiguates same-numbered runs on different days, the same disambiguation key `RailDataClient._signaled` uses internally. |
| `GET /v1/alerts?lines=` | Alerts scoped to a set of line codes (umbrella-resolved: `MNE` covers Morristown and Gladstone). |
| `POST /v1/auth/apple`, `POST /v1/auth/google`, `POST /v1/auth/email` | Optional account creation/sign-in (v1). |
| `POST /v1/accounts/merge-device` | Merge a device's local commutes into an authenticated account. |
| `DELETE /v1/me` | Delete everything: device registration, subscriptions, account if any (J-L2). |
| `POST /v1/reports` | "This looks wrong" (J-L3): the reported row's canonical JSON plus a server-side reference ID to the raw upstream payload that produced it (never the raw payload itself in the client-visible response). |
| `GET /v1/stations/{code}/history-summary` | The track-posting stats product surface (section 7): median assign time, IQR, signaled-vs-board agreement rate. |

Every response carries `as_of` (when the underlying poll was taken) alongside an `ETag`
header. A `304 Not Modified` is the expected common case for a foregrounded app polling on
a short interval.

### 3.3 Canonical Departure shape

This is the field-for-field server-side descendant of `Departure` in `ha-njtransit`'s
`models.py`, with the additions a multi-tenant, per-subscriber view needs. Every semantic
distinction from the source project is preserved deliberately, most importantly
**`delay_minutes: null` meaning "no realtime data yet" versus `0` meaning "confirmed on
time"**, and **`track`, `signaled_track`, and `track_source` staying three separate
fields** rather than being collapsed into one "best guess" value, because the board is the
official answer, the track-history dataset (section 7) is measured against the board's
posting time specifically, and folding a signaled platform into "the" track would corrupt
that measurement exactly the way `api/raildata.py`'s docstring warns against.

```jsonc
{
  "train_id": "6320",                 // string; Trenton carries Amtrak "A79"
  "scheduled": "2026-09-20T08:12:00-04:00",
  "destination": "New York -SEC",
  "line": "Morristown Line",
  "line_abbreviation": "M&E",
  "status": "cancelled",              // normalized enum, never raises on an unknown value
  "status_raw": "CANCELLED",          // preserved verbatim
  "status_text": "Cancelled",         // server-computed, combining status + delay; see below
  "delay_minutes": null,              // null = no realtime data yet; 0 = confirmed on time
  "track": null,                      // board-posted platform, or null
  "signaled_track": "7",              // RailData vehicle-feed platform, or null; NY Penn only today
  "track_source": "signaled",         // "board" | "signaled" | null; board always wins if both present
  "inline_message": null,
  "crowding": {
    "overall": "moderate",            // worst level across the consist
    "positions": { "front": "light", "middle": "moderate", "back": "unknown" }
  },
  "cars": [
    { "number": "5558", "color": "#0B6623", "level": "light", "position": "Front" }
  ],
  "favorite": true,                   // true if this train is one of the subscriber's favorites
  "alerts": [                         // live alerts naming this specific train
    { "message": "M and E train #6320...is cancelled", "url": null }
  ],
  "knocked_on_by": null               // { "train_id": "...", "scheduled": "..." } when a train
                                       // ahead of this one, on the same line, that this
                                       // subscriber cannot use, was just cancelled (line_cancellation)
}
```

`status_text` combination rules (identical to `Departure.status_text` in `models.py`):

| `status` | `delay_minutes` | `status_text` |
|---|---|---|
| `cancelled` | any | `Cancelled` |
| `delayed` | 22 | `22 min late` |
| `boarding` | `null` | `Boarding` |
| `on_time` | `0` | `On time` |
| `on_time` | `null` | `""` (nothing known yet, not "On time") |

### 3.4 The countdown ticks client-side

The server never sends a "minutes until" number. It sends `scheduled` (an absolute,
timezone-aware timestamp) and `delay_minutes`. The client computes the countdown locally
and re-renders it on its own clock, the same pattern the Lovelace card uses today
(recomputing every 10 seconds from the entity's timestamp state rather than waiting for a
poll) and the same reason the Live Activity blueprint uses a `chronometer` element instead
of pushing once a minute. This means the number on screen is correct between polls, correct
offline, and correct the instant the app opens, with zero network cost.

### 3.5 Delta / caching strategy

Every board and detail endpoint computes an ETag from a hash of the canonical response body.
Clients send `If-None-Match`; a `304` means "nothing changed since you last asked," which is
the overwhelmingly common case for any station polled more than once a minute given the
underlying data changes roughly at that cadence itself. A full commute board (up to a
handful of departures after destination filtering) serializes to a few KB, so there's no
need for field-level JSON Patch deltas at MVP/v1 scale; a full-body 200 on a genuine change
is cheap enough over HTTP/2 that shaving it further isn't worth the complexity. Revisit only
if payload size becomes measurably significant at v2 scale.

---

## 4. Subscription model

### 4.1 What a commute is, server-side

```jsonc
{
  "id": "cmt_...",
  "owner": { "type": "device", "id": "dev_..." },   // or { "type": "account", "id": "acc_..." }
  "origin": "RT",                                    // 2-char station code
  "destination": "NY",                                // 2-char code, or null: whole-board mode
  "options": {
    "delay_threshold_minutes": 10,                    // 1-60, default 10
    "lookahead_minutes": 90,                           // 15-240, default 90
    "favorites": ["6662", "6664"],
    "commute_window": {                                // null = no time gating at all
      "days": ["mon", "tue", "wed", "thu", "fri"],
      "start": "07:00", "end": "09:30",
      "tz": "America/New_York"
    },
    "notify_prefs": {
      "cancelled": true, "delayed": true, "track_changed": true,
      "alerted": true, "track_overdue": true,
      "all_clear": false,                              // knock-on is in-app only, no pref
      "live_activity": true
    }
  },
  "dismissed_until": null,                             // server-side suppression, see section 5.3
  "created_at": "...", "updated_at": "..."
}
```

`favorite_trains`, `delay_threshold`, and `lookahead` map directly onto the HA integration's
per-entry options (`CONF_FAVORITE_TRAINS`, `CONF_DELAY_THRESHOLD`, `CONF_LOOKAHEAD`), same
ranges and defaults. `commute_window` is new: it's the server-side home for the Live
Activity blueprint's schedule gate, because push-to-start decisions (section 5) need to be
evaluated server-side against something, and the only thing safe to hold server-side is a
day-of-week/time-of-day rule, never a location.

### 4.2 Anonymous by default, account for sync only

Every commute is created device-scoped, tied to the bearer credential issued at
`POST /devices` (section 9.2), with no account and no PII required. An optional account
(Sign in with Apple, Google, or an email magic link) exists for exactly one reason: syncing
commutes and favorites across a second device and restoring them after a phone replacement.
Signing in merges device-scoped commutes into the account (union favorites, keep one side on
an origin/destination conflict, per J-A4); signing out keeps a local copy and stops syncing.
Nothing about the disruption logic, the push pipeline, or the derived-signal engine cares
whether a commute is device- or account-owned; ownership only changes who else can read and
write it.

### 4.3 Privacy stance: location never reaches the server, with one named exception

The HA integration's Live Activity blueprint already proves this pattern works: the
location-gating rule (zones, or a radius derived as half the distance from home to the
station, floored at 2 units) runs entirely inside Home Assistant, which is to say entirely
on infrastructure the user controls, and it fails open (missing data shows the activity
rather than hides it, because a false negative here means silently missing your train). This
app has to reproduce that property without a local Home Assistant to lean on, which means
the rule moves to the client:

- The client stores its own last-known location and evaluates the zone/radius rule
  on-device, exactly the blueprint's logic (default radius: half the home-to-station
  distance, floored at 2 units; zones take precedence when set).
- **The server never receives coordinates for this purpose, ever, and never stores
  coordinates at all.** Where the client needs the server's help (the initial nearest-station
  suggestion at onboarding, `GET /stations/nearby?lat=&lon=`), coordinates transit the
  request path to be forwarded to the website's proximity lookup and are not logged, not
  persisted, and not attached to the device record. This is the one and only place
  coordinates touch the backend, and it should be called out in the privacy screen (J-L1)
  precisely because "never" needs to mean never, not "never except this one thing nobody
  mentions."
- Server-triggered actions that a location rule might veto (starting a Live Activity via
  push-to-start, sending a time-sensitive push) are triggered by the server on *time*
  criteria only (favorite entering lead time, commute window active), which the server can
  evaluate from `commute_window` without ever seeing where the phone is. The client applies
  the location veto locally: either by suppressing its own push-to-start-triggered activity
  render before the user ever sees it, or, for background pushes, by the notification
  extension checking last-known location before deciding whether to present an alert.
  Section 5.4 covers the iOS mechanics and flags what's confirmed engineering-feasible
  versus what needs a spike.

---

## 5. Push and notification pipeline

### 5.1 Compute once per (station, train), evaluate per subscriber at the edge

The derived-signal engine mirrors `event.py`'s transition detection, generalized from "one
Home Assistant instance's view of one commute" to "every subscriber's view of every train,"
without recomputing anything N times:

1. On every board poll, the normalizer produces one canonical `Departure` per (station,
   train). The engine diffs it against the last snapshot for that (station, train) and emits
   a small set of coarse, subscriber-independent facts onto the event stream: `cancelled`,
   `track_changed(previous_track, new_track)`, `delay_changed(from, to)`, `alerted(train_id)`,
   `line_cancellation(cancelled_train, affects_lines, scheduled)`, `track_overdue`. This is
   exactly `TrainEvent._snapshot`/`_fire_changes`'s job, moved server-side and run once
   regardless of how many subscribers care.
2. **The one condition that genuinely differs per subscriber is the delay threshold** (1-60
   minutes, per commute). The engine does not try to guess a threshold; it emits
   `delay_changed(from, to)` as a plain fact. The fan-out worker, for each active
   subscription whose commute includes this train (by resolved train-ID set or destination
   label match, same union rule as `entity.py`'s destination filter), checks whether `to`
   crosses *that subscriber's* threshold given what was last delivered to *that subscriber*
   (stored per-subscription, not globally). Two comparisons, both against the subscriber's
   own state: `now_over = to >= threshold` and `band(to) > last_delivered_band`, where
   `band(m) = m // 5`. A push fires when the train first crosses the threshold, and again
   only when it escalates into a *worse* five-minute band; drifting inside a band and
   recovering are both silent, and a train that recovers and then re-crosses does speak
   again. This is the `commute_disruption.yaml` bucketing rule (the unbucketed version
   produced five notifications in seven minutes about one train) applied per subscriber,
   without recomputing delay math per subscriber: one value computed once, one comparison
   per subscription. `last_delivered_band` lives beside `last_delivered_state` in the
   per-subscription record (section 5.2).
3. `line_cancellation` needs no extra request either, for the same reason it's free in the
   HA integration: the board is already fetched per station, and the affected train's own
   commute already has that row in hand. `affects_train` in the emitted event names which of
   *your* trains is running behind the cancelled one. It is **never pushed**: it's an
   inference, not a fact about the subscriber's train, and the UX spec (§5) keeps it in-app
   only, on the Alerts tab and Departure detail. The fan-out worker therefore does not join
   it to push subscriptions at all; it lands in the board payload as `knock_on` on the
   affected departure, and it never contributes to the disruption/tint logic, same
   separation `event.py`'s docstring insists on.

### 5.2 Dedupe and idempotency

Every emitted event carries a stable key: `(station, train_id, scheduled_iso, event_type)`,
plus, for `delay_changed`, the `to` value. The fan-out worker records, per
`(subscription_id, event_key)`, that a push was sent, in Redis with a TTL past the train's
scheduled departure (departure time plus one hour is enough headroom). Any replay of the
same event (a worker retry, an at-least-once delivery from the queue, a second instance
picking up the same message) is a no-op against that set. This is deliberately a separate
guard from the "did the value actually change" check in step 1: the value-changed check
stops duplicate *events*, the dedupe set stops duplicate *deliveries* of the same event.

### 5.3 Restart must not replay

This is the single most load-bearing correctness rule in the whole pipeline, stated three
times in the source project (event.py's docstring, README's automation section, and
J-H5) for a reason: a backend deploy or a pod restart during an active disruption must
produce zero pushes for problems that already existed before the restart. The mechanism:

- The derived-signal engine's per-(station, train) snapshot is itself persisted (Redis,
  short TTL matching the lookahead window), not held only in a process's memory. A fresh
  process reads the last snapshot before evaluating the next poll; if none exists (true cold
  start for that key), it **writes the current state as the baseline and emits nothing**,
  exactly `TrainEvent.async_added_to_hass`'s priming behavior.
- Per-subscription "last delivered event" state (section 5.1's `last_delivered_delay`, and
  equivalent cursors for cancelled/track/alerted) is what a subscriber's dedupe compares
  against, not "was this the first poll since the subscription was created." A subscription
  created mid-disruption sees the disruption on its first `GET /commutes/{id}/board` (the
  read path always reflects current truth) but gets no backdated push for the transition
  that already happened.

### 5.4 iOS Live Activity budget and lifecycle

Apple does not publish a hard numeric budget for Live Activity push updates or push-to-start
frequency; developer community reports commonly describe staying well under a few updates a
minute as safe and note the platform will silently drop excess updates rather than error.
**Treat this as a hard design constraint the same way the existing blueprint already does**:
update only on a real change (status, track, crowding-bucket, or the "likely" hedge
resolving to a confirmed board track), never on a timer, and let the countdown itself tick
locally via the activity's own timer-style content, not via repeated pushes. J-H1's stated
acceptance bar ("a Live Activity for a normal commute sends fewer than 10 updates end to
end") is a reasonable engineering target given that shape: start, maybe one or two real
changes, transition to onboard mode, end. Activities also have a bounded lifetime (several
hours; extended by staleness-date mechanics Apple has changed across iOS versions), which is
comfortably longer than any single commute leg, so this isn't expected to bind in practice.

Lifecycle, matching the existing blueprint's behavior one-to-one:

1. **Start**: on entering lead time (default 30 minutes before a favorite's departure, per
   commute), gated by `commute_window` (server-evaluated, time-only) and by the client's
   on-device location veto (section 4.3). Started either by the app in the foreground, or by
   the backend via an ActivityKit push-to-start call using the token registered at
   `POST /devices/{id}/live-activity/push-to-start-token`.
2. **Update**: only on the transition events from section 5.1 that are visually relevant
   (status, track, crowding). The backend suppresses a no-op content-state push even if some
   upstream field technically changed (e.g., `status_raw` re-sent with the same meaning).
3. **Transition to onboard mode**: on the train's departure from the origin (the stop list's
   first stop marked departed), switching from "departs in" to "arrives in," using the same
   lower-bound ETA logic as `TrainRun.eta_at`/`minutes_late` (never earlier than scheduled,
   floored at zero lateness). This needs the backend to keep polling the stop list for a
   followed train *after* it leaves the origin board, the same one-request-per-followed-train
   cost the HA integration already accepts for `ProgressCoordinator`.
4. **End**: at arrival (destination no longer has a pending stop on the run), or on Dismiss.
   Dismiss is server-recorded as `dismissed_until` on the subscription (section 4.1), not
   only a local device flag, so suppression holds even if the phone is briefly offline, the
   same problem the blueprint's `input_boolean` solves locally today.

**What needs a spike, flagged as inference rather than confirmed fact**: whether an
ActivityKit push-to-start invocation gives the app's extension code a chance to evaluate the
on-device location veto *before* the activity actually renders, or whether push-to-start
unconditionally creates the activity from the payload the moment it's delivered. If it's the
latter, the practical mitigation is the same either way: keep push-to-start triggers
conservative (time-only, as designed) and let the client's foreground checks and the Dismiss
flow be the backstop, rather than depending on a background veto that may not be
architecturally available. This should be validated against a current iOS SDK before v1
ships, not assumed from this document.

### 5.5 Android

No Live Activity or Dynamic Island equivalent exists on Android. The closest available
affordance, and what's recommended:

- **A high-priority FCM data message** wakes the app briefly on a real transition (same
  events as iOS), which updates **a single ongoing/ ongoing-style notification** (same
  notification ID reused, so it updates in place rather than stacking), showing countdown,
  track, and status, with a Dismiss action, mirroring J-H2's design.
- **A foreground service is not used to keep this alive continuously in the background.**
  Modern Android (12+) restricts starting foreground services from the background heavily,
  and doing so would also fight the OS's own battery-management heuristics in a way that
  risks the app being flagged. Recommend a foreground service only while the app is open and
  actively displaying the commute screen (an explicit, visible, user-initiated state), with
  the FCM-plus-ongoing-notification pattern as the backgrounded equivalent. This is inferred
  from general current Android platform direction rather than read from this corpus (which
  is iOS-only in its Live Activity work) and should be validated against whatever Android API
  level the app targets at v1, since background-execution rules have moved meaningfully
  release to release.

### 5.6 Silent versus alert

Not every content-state change deserves an interruption. Recommended split, matching the
per-commute toggles in section 4.1's `notify_prefs`:

| Change | Delivery |
|---|---|
| Cancelled | Alert (time-sensitive) |
| Crosses delay threshold | Alert (time-sensitive) |
| Track changed (post-publication) | Alert (time-sensitive), since this is the "you're on the wrong platform" case |
| Track changed (pre-departure, well outside lead time) | Silent content update only |
| Alert names your train | Alert |
| Track overdue | Alert |
| Knock-on (`line_cancellation`) | In-app only, never pushed (J-E4, UX §5) |
| All clear (disruption resolved) | Off by default; optional, per J-H4/J-E6 |
| Status text change with no threshold crossing (e.g., "Boarding") | Silent content update only, no push |

---

## 6. Mobile client architecture

### 6.1 Native, with a shared non-UI core

**Decision: native UI on both platforms (Swift/SwiftUI on iOS, Kotlin/Compose on Android),
sharing business logic (not UI) through a Kotlin Multiplatform module where it's worth the
overhead.**

| | Native x2 | KMP (shared core, native UI) | React Native | Flutter |
|---|---|---|---|---|
| Live Activity / ActivityKit, push-to-start | Full, first-party | Full (UI stays native) | Bridged; historically lags iOS releases and has had real gaps for exactly this API | Bridged; same category of risk |
| Widgets (WidgetKit / Glance) | Full, first-party | Full (UI stays native) | Bridged, immature | Bridged, immature |
| Shared logic (countdown math, threshold evaluation, offline caching, JSON models) | Duplicated in Swift and Kotlin, real risk of the two drifting on exactly the subtle cases this domain is full of (midnight rollover, DST fold, null-vs-zero delay) | Shared once, tested once | Shared (JS) | Shared (Dart) |
| Time to ship both platforms | Slowest | Slow, but the risky UI surfaces are native from day one | Fastest | Fastest |

The reasoning: this product's differentiators (Live Activity, Dynamic Island, home-screen
widget, eventual watch complication) are precisely the surfaces cross-platform frameworks
handle worst and where staying current with each iOS release matters most, because they *are*
the product's identity, not a peripheral feature. Paying for native UI twice buys reliability
exactly where it's least acceptable to have a bridge library lag Apple's SDK. Flutter and
React Native are rejected for that reason specifically, not on general merit.

Plain native x2 is rejected in favor of native-plus-KMP for one narrow reason: the domain
has already proven, in the HA integration's own test suite, that the tricky logic (bare
wall-clock parsing, the 3-hour rollover window, DST fold, `delay_minutes` null-vs-zero,
lower-bound ETA math, threshold-crossing comparisons) is exactly the kind of code that's easy
to get subtly wrong once and very easy to get wrong *differently* in a second language if
duplicated by hand. A shared Kotlin Multiplatform module holding the JSON models, the
countdown/ETA math, the threshold-crossing logic, and the offline cache, consumed by
SwiftUI via KMP's Objective-C/Swift interop and by Compose directly, keeps that logic tested
once against one set of fixtures (the same discipline the source project already uses) while
leaving every pixel of UI, and every Live Activity/widget integration, fully native.

### 6.2 Offline and degraded behavior

- The last successfully fetched board is cached locally with its `as_of` timestamp. On
  reconnect, a conditional GET reconciles it.
- The countdown keeps ticking from the cached `scheduled` timestamp with zero network
  dependency, per section 3.4; going offline never freezes the number on screen, it just
  stops it from ever changing meaning (status, track) until reconnect.
- Staleness is shown explicitly rather than silently: an "as of Nm ago" pill once the client
  hasn't gotten a fresh answer in longer than roughly 2x the expected poll interval, matching
  J-B1/J-K1's design (desaturated tint and an explicit "Live data unavailable" state past a
  harder threshold, never a silent "on time").
- If the backend itself is unreachable but upstream is fine, the client's behavior from the
  user's point of view is identical to being offline (same cached board, same staleness UI),
  with exponential backoff on retry.

### 6.3 Background refresh strategy

`BGAppRefreshTask` (iOS) and Android's equivalent scheduled work are treated as opportunistic
cache-warming only, never as the mechanism something time-critical depends on, because their
actual cadence is not something the OS guarantees. **The backend's push pipeline is the
source of truth for "you need to know now"**, precisely because the backend already knows
the moment something changes (that's the entire point of computing transitions centrally in
section 5); the client's job on receiving a silent push is just to re-fetch and update its
cache and any active widget/Live Activity, not to be the thing that notices the change in
the first place.

### 6.4 Widget timeline design

WidgetKit (iOS) and Glance (Android) both work on a timeline-of-entries model rather than a
live connection. Recommended approach:

- Each timeline reload fetches the same `GET /commutes/{id}/board` response the app uses,
  and generates a small number of future entries locally from it: the current state, plus
  synthetic "still counting down" entries at fixed offsets, so the countdown visibly advances
  between reloads without a network call per tick.
- **Reload triggers**: a normal, infrequent scheduled reload (the OS doesn't guarantee a
  fixed budget for this; historically it's been on the order of dozens of reloads a day per
  widget, adaptively throttled, and should not be designed around a specific number), plus a
  reload requested immediately after any silent or alert push that touched this commute
  (`WidgetCenter.reloadTimelines`), so a track change reaches the widget promptly rather than
  waiting out an OS-controlled schedule.
- Small widget: countdown, track, tint. Medium: hero plus next two departures. Both carry the
  same `as_of` staleness indicator as the in-app view, never presenting a stale number as
  current without saying so.

---

## 7. Data retention and analytics

Track history is a first-class dataset here, not an afterthought, for the same reason it is
in the source project: the answer to "how much lead time does a track prediction have to
beat" only exists because someone measured it, and the answer ("NY Penn posts a median of
8.8 minutes before departure, IQR 1.9 minutes, n=236") is itself a product feature (J-L4), not
just an internal analysis input.

### 7.1 What to keep

Per (station, service date, train), mirroring `track_history.py`'s record shape exactly,
because every field in it earns its place for a documented reason in that module:

- `scheduled`, `line`, `track` (the track the train actually left from)
- `seen_trackless`: whether the train was ever observed *before* it had a track, because
  `assigned_at` only means something when this is true (a train that already had a track
  when the recorder first saw it, e.g. right after a deploy, would otherwise manufacture a
  fake lead-time measurement)
- `assigned_at`: seconds before scheduled departure that the track was posted
- `delay_at_assignment`: separates "a late assignment that predicts trouble" from "a late
  assignment that merely restates a train already known to be late," since `assigned_at` is
  measured against the *scheduled* time
- `first_track`: set only on reassignment (rare: one change in 164 observations at NY Penn),
  so its absence means "assigned once," not "unknown"
- `final_status`, `final_delay`, `worst_delay`: the outcome, kept as *the worst it ever
  looked*, not the last sighting, because the last sighting is systematically the emptiest
  one (a train counting down "15 min late" flips to `ALL ABOARD` with no delay field at all
  on its very last update, and a naive last-write-wins design erases every delay this way,
  which is exactly what happened during the source project's first three days of collection)
- Signaled-vs-board agreement, per posting, at stations with a circuit decoder: did the
  signaled platform match the board's eventual posted track (the 229-of-231 figure)

### 7.2 Retention

Two tiers, extending the source project's flat 30-day window into something that scales:

- **Raw per-departure rows**: 30-90 days. Long enough to catch a weekday pattern versus
  coincidence (30 days covers roughly four of each weekday, the same reasoning behind
  `TRACK_HISTORY_DAYS`), and useful for debugging a specific user's "this looks wrong"
  report (`POST /v1/reports`) for a reasonable window after the fact.
- **Aggregated rollups** (daily/weekly median, IQR, agreement rate, per station): retained
  indefinitely. This is small (a handful of numbers per station per day) and it's the data
  the product surface actually reads from, so there's no reason to ever delete it, and every
  reason to keep the history for trend detection (a station whose posting time drifts later
  over a season is itself worth knowing about).

### 7.3 Product surface

`GET /v1/stations/{code}/history-summary` backs J-L4's station detail screen directly:
"New York Penn posts tracks a median of 9.0 min before departure (middle half: 8.9-9.1). The
signaling system usually knows about 20 min out and matched the board 229 of 231 times."
This is also the input that should replace the HA integration's single hardcoded
`TRACK_OVERDUE_LEAD = 6 minutes`: that constant was tuned against NY Penn's specific
distribution (p10 = 5.7 minutes) and explicitly is not expected to transfer to another
station's posting behavior. At backend scale, `track_overdue`'s threshold should be computed
per station from that station's own rolling p10, falling back to a conservative default
where a station has too little history yet to trust, rather than shipped as one global
number tuned for one terminal.

---

## 8. Reliability

### 8.1 Upstream drift detection

Keep the source project's core discipline and run it continuously in production rather than
on demand: **recorded-fixture contract tests replayed against the live endpoints on a
schedule** (hourly, per J-M1), asserting the same shape guarantees the HA integration's test
suite already encodes (field presence, the 19-row cap, the alias-row count in the station
list, the known status/line vocabularies as a floor, not a ceiling). A drift hit pages
on-call and, where a fallback source exists for that station, flips ingestion to it
automatically rather than serving broken data. This is the same instinct behind
`scripts/extract_ops.py --diff` in the source repo, running as an ongoing monitor instead of
a manual pre-commit check.

### 8.2 Circuit breakers and failover

Each station's ingestion job tracks recent error rates per source independently. Repeated
`NJTransitRequestError`/`NJTransitAPIError`-equivalent failures from the website trip a
circuit breaker for that station, which (a) stops hammering an endpoint that's clearly
returned something unexpected, and (b) fails over to RailData for that station if it's
within the RailData-covered set and the quota budget allows it, or (c) serves the last known
good board with an explicit staleness flag if neither source is healthy, never silently. The
`RailSource` protocol from the source project (both clients answering the same six-method
interface) is the right shape to carry forward here too: ingestion code should not know or
care which source answered, only whether the answer is fresh enough to trust.

### 8.3 Staleness signalling

Every response's `as_of` field (section 3.2) is the client's only source of truth about
freshness; there is no separate "is this stale" boolean to keep in sync, the client computes
it from `now - as_of` against the expected poll interval. This is deliberately the same
design already proven in J-B1/J-K1 ("as of 3 min ago" pill, degrading further past a harder
threshold) and needs no new invention here, only server-side discipline about setting
`as_of` accurately (the time the underlying poll succeeded, not the time the response was
served from cache).

### 8.4 Status page

A public status page (a hosted product like Instatus or a self-hosted `cachet`/`Uptime
Kuma`-style page, not a bespoke build) reporting per-source health (website / RailData) and
any active incident, in the same transparent spirit as the source README's own "Reliability"
section, which tells users plainly that the default source is private and can change without
notice. That honesty is a product asset worth keeping, not something to soften once there's
a company behind it.

---

## 9. Security

### 9.1 RailData credentials

Server-side only, in a secrets manager (AWS Secrets Manager, GCP Secret Manager, or
equivalent), never shipped in the app binary, never logged, never included in an exception
message. This is a direct carry-over of `api/raildata.py`'s own stated rule ("never log,
print or assert on a credential") and `AGENTS.md`'s instruction to the same effect; the only
change at backend scale is that the credential now belongs to the ingestion service's
runtime environment rather than a Home Assistant config entry, and the token-caching
discipline (`RailDataStore`, one token shared across every process polling that account,
refreshed only when a day old or rejected) needs a shared backing store (Redis or Postgres)
so a multi-instance ingestion fleet doesn't each independently spend one of the ten daily
`getToken` calls on startup.

### 9.2 API auth for anonymous devices

At `POST /devices`, the app presents an **App Attest** assertion (iOS) or **Play Integrity**
verdict (Android) alongside a device-generated public key. The server verifies the
attestation with Apple's/Google's own verification service before issuing a device
credential (a signed, short-lived bearer token, refreshed via the device's held private key)
used for every subsequent call. This binds API access to a real, non-jailbroken/non-rooted
app instance without requiring any user-identifying information, keeping the anonymous
device-scoped default (section 4.2) genuinely anonymous while still making it expensive to
mass-farm devices for abuse. Attestation is re-checked periodically (a reasonable interval to
validate at implementation time, not asserted here as a fixed number) rather than trusted
forever from one registration.

### 9.3 Abuse controls

- Per-device rate limits on subscription creation and on API call volume, sized generously
  enough that a real user with several commutes and a widget never brushes against it, but
  tight enough to make it impractical to use one device identity to generate meaningful
  backend or (indirectly) upstream load.
- Anomaly detection on the subscription graph: an implausible number of commutes attached to
  one device, or the same origin/destination pair created at a suspicious rate, is worth
  alerting on even before it's a proven attack, precisely because upstream quota (section
  1.3) is the scarce resource this whole system is built to protect.
- Public, unauthenticated endpoints (`/stations`, `/stations/nearby`) get IP-based throttling
  even though the data itself is not sensitive, because their cost is backend load and,
  transitively for the proximity lookup, a website request per call.

---

## 10. Phased delivery plan

This mirrors the coverage matrix in `03-user-journeys.md`; this section states what each
phase requires to exist on the backend and client to support that journey set, rather than
re-listing the journeys themselves.

### MVP

- Backend: ingestion + normalizer for the website source only, covering every station a
  configured commute touches (not the full 167 up front); Postgres + Redis; REST API with
  ETag; disruption evaluation and the core transition events (cancelled, delayed, alerted,
  track_changed); RailData used only for the daily schedule join (destination filtering) and
  NY Penn signaled track, both already proven safe at single-account scale.
- Client: iOS only, SwiftUI, anonymous device-scoped commutes, in-app countdown, basic push
  (APNs standard notifications, no Live Activity yet), favorites and stops-away, whole-board
  mode, offline/degraded handling, track history recording begins here (cheap, and the
  30-90 day clock needs to start early to be useful by v1).
- Reliability: fixture-based drift contract tests, RailData quota dashboard (J-M2).

### v1

- Backend: Live Activity push-to-start and content updates; per-subscriber threshold
  evaluation at fan-out fully productionized; server-side `dismissed_until`; optional
  account + multi-device sync; circuit breakers and website<->RailData failover; status
  page; expand RailData signaled-track decoding to additional hub stations if the
  collect-and-validate process (section 8, J-M3: 200+ postings, >=95% agreement before
  enabling) supports it.
- Client: Android app (Kotlin/Compose, FCM + ongoing notification), iOS Live Activity and
  home-screen widget, client-side location gating (zones/radius, ported from the HA
  blueprint's logic), commute window scheduling, knock-on (`line_cancellation`) surfaced
  in-app, station track-posting stats screen.

### v2

- Backend: per-station `track_overdue` thresholds computed from rolling history rather than
  a single tuned constant; multi-destination commutes (union of train-ID sets, flagged as
  open in the source project's own SPEC §13.2); scale-out per section 2.4's 100k-MAU column;
  pursue the NJ Transit commercial/elevated-quota relationship from section 1.3 in earnest,
  since it directly relaxes the biggest structural constraint on everything above it.
- Client: connection-aware ETA (does the lower bound still make the transfer), lock-screen
  widget / watch complication, Android home-screen widget.

### Risk register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Website endpoint changes or starts blocking high-volume consumers | High over a multi-year horizon | High: breaks board coverage network-wide | Fixture-based drift contract tests running hourly in production; circuit breaker + RailData failover per station; honest status page; pursue an official NJ Transit relationship to reduce reliance over time |
| RailData quota insufficient for full-network live coverage | Already true today at any real scale (section 1.3's arithmetic) | High if the product tries to over-promise signaled-track/full-fidelity coverage broadly | Architecture already scopes RailData to schedule joins plus a small, growable set of decoded hub stations; treat "which stations get RailData fidelity" as a config change driven by real subscription data, not a promise made up front |
| Running multiple RailData accounts to multiply quota | Low if this document is followed, but a real temptation under growth pressure | High: account suspension, and a materially worse legal/relationship position with NJ Transit than asking directly | Explicit non-goal, stated here; pursue an elevated account instead |
| iOS Live Activity update/lifetime budget throttles or drops updates | Medium | Medium: a stale or missing Live Activity content state | Update only on real change (already the design); the countdown itself is client-ticked and doesn't depend on push delivery; monitor delivery outcomes once in production |
| Signaled-track and track-overdue logic validated only at NY Penn | High (true today) | Medium: a feature that looks broadly available but only genuinely works at one station | Ship with honest per-station coverage messaging; expand via the same collect-then-validate process the source project used, with the 95%-over-200-postings gate from J-M3 |
| Six station-name vocabularies drift or a new alias appears upstream | Medium | High: silent mis-identification of a station, which breaks push targeting and the board | Port the exact fixtures and comparison logic (`_same_station`, alias-dedup by 2-char code) as contract tests, not just as production code |
| Legal exposure from commercial-scale use of the private endpoint | Unknown; genuinely needs counsel, not engineering judgment | High if realized | Explicit open question (section 11); get a legal review before any broad public launch, independent of the technical mitigations above |
| Privacy stance (no coordinates server-side) constrains push-to-start UX | Medium | Low to medium: some notifications may fire when a stricter, location-aware rule would have suppressed them | Hybrid design in section 4.3/5.4: server triggers on time only, client applies the location veto locally wherever the platform allows it; validate the push-to-start execution model against current iOS before relying on it further |

---

## 11. Open architectural questions

1. **Will NJ Transit grant a higher-volume or commercial RailData tier?** This is the single
   biggest lever on this whole design; everything in section 1.3 is a workaround for not
   having it yet. Worth pursuing before assuming the website-primary/RailData-surgical split
   is permanent.
2. **What's the actual legal risk tier of the private endpoint at commercial, multi-tenant
   scale?** Flagged in section 1.4 as needing counsel, not engineering judgment; unresolved
   here by design.
3. **Does an ActivityKit push-to-start invocation give client code a chance to apply the
   location veto before the activity renders, or does it render unconditionally from the
   server payload?** Section 5.4 designs around the conservative assumption but this needs a
   spike against a current iOS SDK, not an assumption carried indefinitely.
4. **What is the real, empirically observed Live Activity push update/lifetime budget for
   this app's usage pattern?** Apple doesn't publish a hard number; section 5.4's "fewer than
   10 updates per commute" target should be measured against production telemetry once there
   is any, not assumed to hold forever.
5. **Multi-destination commutes** (the source project's own SPEC §13.2, still open there
   too): is "New York *or* Hoboken, whichever comes first" worth the UI complexity, given
   it's architecturally cheap (union two train-ID sets)?
6. **Should circuit decoders be fit automatically from collected (circuit, board-track)
   pairs, rather than hand-derived per station the way NY Penn's `platform = 22 - n` formula
   was?** The source project's own methodology (collect, hypothesize, validate against a
   holdout) is sound but manual; at 20+ candidate hub stations, semi-automating the
   hypothesis-generation step (not the validation gate, which should stay a hard 95%-over-
   200-postings bar per J-M3) may be worth building.
7. **Weekend and holiday schedule correctness**, still an open, unverified question even in
   the source HA project (its SPEC §13.6): does the daily route-refresh design actually
   produce correct results across a real weekend and a real holiday, or only in reasoning
   about it? This needs to be watched from a running instance, the same way the source
   project flags it as unresolved.
8. **Does this backend ever expose its normalized board as a public/partner API?** Nothing
   in this document assumes so, but the normalizer's whole value (six vocabularies collapsed
   to one canonical model, kept fresh centrally) is exactly the kind of thing a transit-data
   consumer would want, and it's worth deciding deliberately rather than backing into it.
9. **Is there a monetization tier**, and how does it interact with the anonymous-by-default
   privacy stance from section 4.2? (More favorites, more hub stations with signaled-track
   fidelity, and multi-device sync are the natural candidates, but none of that is designed
   here, and any answer needs to preserve "everything works without an account" as a real
   default, not a nagged-at one.)

---

Everything above assumes the reader has read SPEC.md. Where this document restates a number
from there (delay quartiles, quota limits, the platform-decode formula), treat SPEC.md as
the source of truth if the two ever disagree, and update this document rather than letting
it drift, the same rule the source project applies to its own spec.
