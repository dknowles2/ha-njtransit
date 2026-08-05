# NJ Transit Home Assistant Integration — Design Spec

Status: v1 implemented
Scope of this document: v1 (departures + alerts)

Everything in the v1 scope below is built and tested. Where the implementation
taught us something this document got wrong, the section says so rather than being
quietly rewritten -- see §2.6 (the planner's page size), §3.5 (station aliases) and
§6.4 (the board's line vocabulary), each of which corrected a mistaken assumption
here.

## 1. Goals

Provide trustworthy, structured rail data for a specific commute, replacing hand-rolled
`rest:` + `template:` YAML.

The concrete problem v1 solves: **neither NJ Transit feed is a superset of the other.**
Observed 2026-08-03 08:04 ET:

- `getSystemStatus` reported 5 live M&E alerts, naming trains 6612, 6607, 6324, 6311, 6610.
- The Short Hills departure board simultaneously showed train **6320 to New York** as
  `Cancelled` — absent from the alert feed.
- Train 6311 appeared in both, as `CANCELLED` on the board and lowercase "cancelled" in
  alert prose.

A dependable "is my commute broken?" signal must merge both feeds and correlate on train
number. That correlation is the core value of this integration.

### v1 scope

- Destination-filtered next-train sensors with real timestamps
- Live delay in minutes
- Per-car crowding (see §2.3 — free, already in the board payload)
- Line alert / advisory sensors with parsed train numbers
- A merged disruption binary sensor
- A calendar of scheduled departures (§2.6)
- Config + options flow

### Deferred (not v1)

- Full itinerary detail from `getTripPlannerSchedule` — fares, `redNotes` / `footnotes`,
  walking connectors, PATH alternatives. v1 uses this query, but reduces it to train IDs
  and times (§2.5, §2.6); the richer itinerary payload is not surfaced.
- Nearest-station detection via `getTrainScheduleStationsRailForDVClose` + `device_tracker`
- Bus and Light Rail (`getBusDV5`, `getLightRailSchedule`, `getBusStops`)

## 2. API reference

Endpoint: `POST https://www.njtransit.com/api/graphql/graphql`
No authentication. Undocumented and unofficial.

Operation definitions below were extracted from the site's own Nuxt bundles
(`/_nuxt/*.js`), not guessed — they are the exact queries njtransit.com issues. Recovered
operations: `TrainDepartureScreens`, `TrainStopList`, `SystemStatus`, `SystemStatusGlobal`,
`Index`, `TripPlannerSchedule`, `LightRailSchedule`, `LightRailDV`, `DVCloseStation`,
`GetTripStops`, `BusDirections`, `BusStops`, `BreakingNews`, `PDFSchedules`, `Actions`,
`TripPlannerAlternates`, `TripPlannerGoogleAddress`, `TripPlannerReverseGoogleAddress`,
`WalkingDirections`, `TrainScheduleStationsLightRailForDV5`,
`TrainScheduleStationsLightRailForSchedulesLine`.

Re-run the extraction against fresh bundles whenever upstream drift is suspected; it is
the cheapest available substitute for introspection. `scripts/extract_ops.py --diff`
turns drift into a non-zero exit code.

The extractor also reports root fields the bundles reference but that appear in no parsed
operation — queries assigned to minified constants without a `query Name` header, which
the regex cannot see. That pass surfaced `getBusAlertsAdvisories`, `getBusRoutes`,
`getNewSchedules`, `getRecentDVStations`, `getTrainScheduleStationsRailForSchedules`, and
`getTrainSchedulesCurrentLightRail`. All are deferred-tier (bus / light rail / PDF
timetables) and none are needed for v1, but they are reachable with hand-written queries.

### 2.1 Operations used by v1

**`SystemStatus`** — note two fields absent from the version currently in the YAML setup:

```graphql
query SystemStatus {
  getSystemStatus { abbreviation message msg_richtext msg_url service advisoryAlert }
}
```

`msg_richtext` carries HTML markup; `msg_url` links to a full advisory page. Both were
empty on all live (`advisoryAlert: "0"`) alerts sampled, and are expected to be populated
for planned advisories. Treat both as optional.

**`TrainDepartureScreens`** — the site's full selection, considerably richer than the
version currently in use:

```graphql
query TrainDepartureScreens($station: String!) {
  getTrainDepartureScreens(station: $station) {
    items {
      background color departureDate destination inlineMessage line lineAbbreviation
      status track trainID
      stops { departed dropOff name status time }
      capacity { sections { position cars { color number } } }
    }
    bannerMsg twitterAccounts { title twitter } fullScreenMsg
  }
}
```

**`TrainScheduleStationsRailForDV`** — the canonical station vocabulary:

```graphql
query { getTrainScheduleStationsRailForDV { title pentaStationID accessible } }
```

Returns `{"title": "Short Hills Station", "pentaStationID": "RT", "accessible": false}`,
`{"title": "New York Penn Station", "pentaStationID": "NY", "accessible": true}`.

177 rows, but only **167 distinct stations** — the list carries alias titles, so `title` is
not a key. This supersedes `getTrainStations` for the config flow; see §3.5 for the
deduplication requirement and the naming traps.

### 2.2 `stops` is always empty on the board

Despite being requested by the site's own query, `items[].stops` came back as `[]` for all
19 rows. The site lazy-loads it via a separate `TrainStopList($train)` call on expand:

```graphql
query TrainStopList($train: String!) {
  getTrainStopList(train: $train) { departed dropOff name status time }
}
```

That call does return data (18 stops for train 6607). **Consequence:** per-train stop
detail costs one request per train and cannot be batched from the board. This is why stop
tracking is deferred rather than free.

### 2.3 `capacity` is real and free

Per-car crowding, already in the board payload at no extra request cost:

```json
{"sections": [{"position": "Front", "cars": [{"color": "#0B6623", "number": "5558"}]},
              {"position": "Middle", "cars": [...]}, {"position": "Back", "cars": [...]}]}
```

Observed colors: `#0B6623` (green) and `#FFD300` (yellow). Red is presumed but was not
observed — **the vocabulary is incomplete and must not be treated as closed.** Map known
colors to a `CrowdLevel` enum and fall through to `UNKNOWN`.

Present on only 5 of 19 rows — populated for imminent departures where consist data is
known. Absent `capacity` is normal, not an error.

`position` is `Front` / `Middle` / `Back`, which is directly actionable: "board the back
three cars."

### 2.4 `getTripPlannerSchedule.realtime` is dead — do not build on it

The trip planner declares, per leg:

```graphql
realtime {
  estimatedminutes estimatedtime trend adherence stopadherence tripcanceled
  lat long vehicle reliable polltime querytime speed stopped
}
```

On paper `adherence` and `tripcanceled` are structured equivalents of everything §6.2 and
§6.3 reconstruct heuristically. **Spiked 2026-08-03 ~09:20 ET, during an active M&E
disruption. `realtime` was `null` on every leg** — all three Short Hills → NY Penn
itineraries (`routeType: "C"`), and also every leg of two bus itineraries
(`routeType: "B"`, Newark → Jersey City and Newark → Irvington) queried for imminent
departures.

Null for rail *and* bus, on same-day near-future queries, means the block is not populated
on this endpoint at all. **Decision: v1 keeps the board as its realtime source and retains
the §6.2 / §6.3 heuristics.** Do not spend further effort here without new evidence.

### 2.5 The trip planner is still useful — as a timetable

The spike produced a better result than the one it was testing for. Trip-planner leg
`block` values are the same identifiers as board `trainID` values. Querying Short Hills →
New York Penn Station returned blocks `411`, `6628`, `6328`, `480`; the Short Hills board
at the same moment listed train IDs including `411`, `480`, and `6328` (`6628` departs
beyond the board's 19-row window).

This yields a **materially better destination filter than substring-matching the board's
`destination` label**:

1. Once per day, resolve the set of trains serving the configured origin → destination
   pair via `getTripPlannerSchedule` (pure timetable data — no realtime component, so
   daily refresh is ample).
2. Filter the live board to that train-ID set.

Why it is better:

- **It catches transfer itineraries.** Trip 0 was Gladstone train `411` to Summit, then
  `6628` onward to Penn. Its board label reads `Summit`, so a `destination` substring
  filter for "New York" would have discarded a perfectly good way to get to work.
- **It excludes look-alikes** whose label mentions New York but which are not usable
  itineraries.
- **It does not break when upstream rewords a label** (`"New York"` vs `"New York -SEC"`).

Cost: one extra daily query and a third station-name vocabulary (§3.5).

Call parameters, all required in practice — the query fails with a generic
"unable to find trips" error if `date`/`time` are omitted:

```
origin/destination : "Short Hills Station", "New York Penn Station"   (see §3.5)
timeOption         : "D"
date               : "08/03/2026"     MM/DD/YYYY  — "2026-08-03" returns HTTP 500
time               : "9:30 AM"        H:MM AM/PM
travelMode         : "CT"             rail + PATH — NOT the site default (below)
maxWalkingDistance : "1.00"
minimizeTime       : "T"
accessible         : the site sends the *strings* "true"/"false" despite the Boolean type
```

`routeType` vocabulary: `C` commuter rail, `B` bus, `T` PATH, `L` light rail, `X` NYC
subway, `W` walking connector. Non-rail legs carry blocks from unrelated ID spaces (PATH
`114992`, bus `126GV026`) and must be excluded from train-ID correlation by `routeType`.

**`travelMode` is the one parameter this integration does not copy from the site.**
njtransit.com sends `BCTLXR` — every mode. This sends `C`, commuter rail only, because
§2.7 keeps only one-seat rides and every itinerary the other modes buy is discarded on
arrival. Full-day sweep, Short Hills → New York Penn, same service day:

| `travelMode` | calls | what the extra modes add |
|---|---|---|
| `BCTLXR` (site) | 24 | bus and subway itineraries, all discarded |
| `CT` | 21 | PATH itineraries, all discarded |
| `C` (ours) | 18 | nothing to discard |

The planner returns exactly three itineraries per call (§2.6), so an itinerary that will be
discarded costs a slot a direct train could have had. Restricting the query is therefore
both cheaper and better covered — worth stating because that combination is unusual.

The other modes also degrade what they do return. Under `BCTLXR` the planner offered train
`880` as Hoboken + the `126` bus + the subway (1 hr 17 min) and **never surfaced, on any
page,** the Newark Broad Street rail transfer reaching Penn 24 minutes earlier.

### 2.7 Direct trains only

The destination filter keeps only **one-seat rides**. A transfer itinerary is a genuine way
to make the journey — train `880` reads `Hoboken` on the board yet reaches Penn Station at
7:03 PM via Newark Broad Street, ahead of the direct train leaving 21 minutes later — but
the board has no way to say where you change, or that you must. A row headsigned for
somewhere you are not going is worse than a row that is missing.

For Short Hills → New York Penn: **23 direct trains a day, and 18 more reachable only by
changing** (the 400-series Gladstone Branch services, plus Hoboken-bound `480`, `481`,
`626`, `682`, `880`, `882`).

**"Direct" cannot be `len(train_ids) == 1`.** `train_ids` counts rail legs only, so a train
to Hoboken continuing by PATH has one train ID and is not remotely a one-seat ride.
`ScheduledTrip.transport_legs` counts every leg you ride, of any mode; `has_transfer` is
`transport_legs > 1`. Walking connectors are excluded, as is the planner's sentinel leg —
identified by a null `offStopDescription` rather than a null block, because an observed
subway leg carried a block of `""` while being a real leg.

**The filter fails open when nothing runs direct.** For a pair with no one-seat ride at any
hour, an empty board would read as "no trains" rather than "no direct trains", so the
transfer itineraries come back and the fallback is logged. Branch-to-branch pairs are the
candidates — Gladstone → New York Penn resolved just one direct trip in a spot check, so
the margin is thin enough for this path to matter even where it does not trigger.

The union with the destination-label match (§9) is unaffected: label matching admits rows
whose headsign names the destination, and those are direct by construction. Train `6320`
— cancelled, labelled `New York`, absent from the planner — still reaches the board.

**A trip's arrival is the end of the itinerary, not the last rail leg's.** These differ
whenever the journey continues by another mode, and the rail-leg reading is always
flattering: train `480` reaches Hoboken at 10:12 AM and Penn Station at 10:37 AM via PATH,
which upstream's own `duration` calls `1 hr 7 min`. Reading the rail leg reported a
42-minute trip. This shipped, and the calendar event carried both the wrong end time and
the correct duration text side by side.

### 2.6 Paging the planner — required for correctness, and it yields the calendar

**The planner returns exactly 3 trips per call**, regardless of date, time, or day of week
(verified across weekday mornings, a future Tuesday, and a Saturday). Those 3 often
duplicate a train across different downstream itineraries, so a single call yields roughly
2 distinct trains.

This is a correctness problem for §2.5, not just a calendar problem. A single 9:30 AM query
for Short Hills → NY Penn returns 4 trains; **the actual service day has 51.** A
destination filter built from one call would hide the overwhelming majority of usable
trains. `RouteCoordinator` must page.

Paging algorithm — advance `time` past the latest departure seen, repeat until the day is
covered:

```
cur = 4:00 AM
while cur < 11:59 PM:
    trips = planner(date, cur)
    firsts = [first C-leg onStopTime for each trip]
    if not firsts: break
    record each (block, onStopTime, last C-leg offStopTime)
    cur = max(firsts) + 1 minute        # guard: if this does not advance, += 30 min
```

Measured for Short Hills → New York Penn on a clean weekday: **18 calls, 41 distinct
trains** at the `C` travel mode this integration sends (§2.5), of which 23 are direct and
survive the §2.7 filter. The original 24 calls / 51 trains measurement was taken at the
site's `BCTLXR`. Once per day per commute, either is negligible next to the board's
1440 polls/day.

The implemented loop needs **17 calls** for that same day, because it jumps to the latest
departure in each page rather than stepping. The observed schedule is recorded in
`tests/fixtures/planner_day_short_hills_to_ny.json` and drives a fake pager that reproduces
the three-per-call constraint, so the count is a test assertion rather than a note.

The guard matters: without it, a window where all three trips share a departure time loops
forever. Cap iterations regardless.

Because paging already produces `(train, departure time, arrival time)` for the whole
service day, the calendar (§9) is nearly free — it is the same data, presented differently.

### 2.7 Rail line codes (`getTrainLines`)

```
BNTNM  Montclair Line           MNBN   Main-Bergen County Line
NJCLL  NJ Coast Line (Bay Head) PASC   Pascack Valley Line
MNBNP  Port Jervis Line         ATLC   Atlantic City Rail Line
MNEG   Gladstone Branch         BNTN   Montclair-Boonton Line
PRIN   Princeton Shuttle        RARV   Raritan Valley Line
NJCL   North Jersey Coast Line  MNE    Morris & Essex Line
NEC    Northeast Corridor
```

`getSystemStatus.abbreviation` does **not** use this vocabulary consistently — see §6.4.

## 3. Hard constraints discovered

Each was observed directly and must be respected by the client.

### 3.1 A single null field nulls the entire response

`getTrainStations { latitude }` returns `data: null` for the whole query, because
`Station.latitude` is non-nullable in the schema but null in the data:

```
Cannot return null for non-nullable field Station.latitude
```

The same `Station` type returns `latitude` fine via `getStations`. **Consequence:** field
selections are pinned per query and never widened opportunistically. No generic
"fetch a Station" helper. Prefer the site's own selections (§2), which are known-good.

### 3.2 A WAF rejects inline arguments

```graphql
{ getSystemStatus(service: "Rail") { abbreviation } }   →  {"status":400,"message":"Malformed request"}
```

The same call as a named operation with `variables` succeeds. **Consequence:** always
issue named operations with a `variables` object, never string-interpolated arguments.
Note the WAF response is *not* GraphQL-shaped and needs its own error branch.

### 3.3 Introspection is disabled

`__schema` / `__type` are rejected by Apollo. Schema knowledge comes from the site bundles
(§2) plus error-message probing. **Consequence:** no codegen; types are hand-maintained,
and pinned fixtures make upstream drift a test failure rather than a runtime break.

### 3.4 Train IDs are not numeric

Trenton's board returns Amtrak `A79` and SEPTA services alongside NJ Transit trains.
`trainID` is a string. Alert correlation (§6.3) must tolerate non-numeric IDs and must not
assume every board row is an NJ Transit train.

### 3.5 Station naming, and why the canonical list is not a plain list

`getTrainScheduleStationsRailForDV` is the authoritative vocabulary. Its `title` values
work as input to **both** the board and the trip planner, which is the property that makes
it usable as the single source. Use them verbatim; do not synthesize names.

Two traps in that list:

**It contains alias rows, so `title` is not a key.** 177 rows resolve to 167 distinct
stations. Seven `pentaStationID`s carry multiple titles:

```
NY -> New York Penn Station | NY Penn Station | Penn Station New York
NA -> EWR Newark Airport Station | Liberty International Airport
      Newark Airport Rail Station | Newark Liberty International Airport
AM -> Aberdeen Matawan Station | Matawan Station
UV -> Montclair State University Station | MSU Station
OL -> Mount Olive Station | Mt. Olive Station
PH -> 30th Street Station Philadelphia | Philadelphia 30th Street Station
```

The config flow must dedupe by `pentaStationID` or the picker shows New York Penn three
times. `pentaStationID` (`RT` = Short Hills, `NY` = NY Penn, `HB` = Hoboken Terminal,
`ST` = Summit) is the stable key for unique IDs and the entry's stored identity; titles
are display strings and may be reworded upstream.

**The suffix is not a rule.** Most titles end in `Station` or `Terminal`, but
`MetLife Stadium`, `Liberty International Airport`, and `Penn Station New York` do not.
Never construct a name by appending ` Station`.

Outside that list, tolerance varies by consumer and by station, so nothing should rely on
it:

- The board fuzzy-matches. `Short Hills`, `Short Hills Station`, `NY Penn Station`, and
  `Penn Station New York` all resolve; an unknown name returns `null`.
- The trip planner does not, and is inconsistent about it: `Hoboken`, `Hoboken Station`,
  and `Hoboken Terminal` all succeed, while bare `Short Hills` fails outright.
- Planner *output* uses a third casing (`SHORT HILLS`) in leg descriptions. That is
  display text — never feed it back as input.

**A wrong name and a genuine no-service result are the same generic error**
("unable to find trips"). This is the nastiest failure mode in the API: code that swallows
it will be silent in exactly the case users report.
`getTripPlannerAlternates(title:)` resolves a fuzzy name to nearby canonical locations
with distances, and is the recovery path.

Ignore `getTrainStations` entirely; its `<stopId>_<LINE>` abbreviations require
deduplication and it carries the §3.1 null hazard.

### 3.6 Times are bare wall-clock strings

`departureDate` is `"8:25 AM"` — no date, no zone, no offset. Resolution assumes
`America/New_York` and requires explicit midnight-rollover handling: a board fetched at
23:50 containing `"12:05 AM"` refers to the following day.

### 3.7 Status vocabulary is unstable

Observed board values: `"in 21 Min"`, `"in 4 Min"`, `""`, `"Cancelled"`, `"CANCELLED"`.
Observed stop-list values: `"BOARDING"`, `"Late"`.

Casing is inconsistent for the same semantic state, the vocabulary is undocumented, and it
is assumed incomplete. **Consequence:** normalize case-insensitively into an enum, retain
the raw string on the entity, and degrade unknown values to `TrainStatus.UNKNOWN` with the
raw value preserved. Never raise.

## 4. Repository layout

```
custom_components/njtransit/
├── __init__.py           # setup/unload, coordinator wiring
├── manifest.json
├── config_flow.py
├── const.py
├── coordinator.py
├── sensor.py
├── binary_sensor.py
├── diagnostics.py
├── strings.json
├── translations/en.json
└── api/                  # NO Home Assistant imports — see §4.1
    ├── __init__.py
    ├── client.py         # transport, error handling
    ├── queries.py        # operations verbatim from §2
    ├── models.py         # frozen dataclasses
    └── parsing.py        # time, status, crowding, train-number extraction
tests/
├── fixtures/             # recorded payloads, incl. 2026-08-03 disruption capture
scripts/
└── extract_ops.py        # re-extract operations from site bundles (§2)
hacs.json
```

### 4.1 On the bundled client

The client is bundled rather than published separately, per decision. **This integration
does not target Home Assistant Core**, so the core rule requiring third-party API clients
to live in a separate published package does not apply, and `api/` stays in-tree.

The layering below is kept regardless, on its own merits: it keeps a reverse-engineered
API surface testable without a Home Assistant fixture, and lets the traps in this document
be checked against the live endpoint by a standalone script. Skipping core is a judgement
about a private, undocumented endpoint -- not a licence to hold the code to a lower
standard than core would.

To keep that a mechanical move rather than a rewrite: `api/` must not import
`homeassistant`, must accept an injected `aiohttp.ClientSession` rather than reaching for
HA's, and must raise its own exception types. Enforced by a test that walks `api/` for
`homeassistant` imports.

## 5. API client design

### 5.1 Transport

```python
class NJTransitClient:
    def __init__(
        self, session: aiohttp.ClientSession, *, timeout: float = 30.0
    ) -> None: ...
    async def system_status(self) -> tuple[SystemAlert, ...]: ...
    async def departures(self, station: str) -> DepartureBoard: ...
    async def stations(self) -> tuple[Station, ...]: ...
    async def train_lines(self) -> tuple[RailLine, ...]: ...
    async def scheduled_trips(
        self, origin: str, destination: str, on: date | None = None
    ) -> tuple[ScheduledTrip, ...]: ...
```

Collections are returned as tuples: everything downstream treats them as immutable
snapshots of one poll, and a coordinator handing out a mutable list invites an entity to
edit shared state.

An earlier draft of this sketch also listed `stop_list` and `nearest_stations`. Neither
exists, correctly — both belong to deferred features (§1), and adding client methods for
work that is not being done just creates untested surface. The `STOP_LIST` operation does
exist in `queries.py`, verified against the live endpoint but unused, so per-train tracking
starts from a known-good query rather than a guess.

A shared `_execute(operation, query, variables)` handles:

- HTTP non-200 → `NJTransitConnectionError`
- `{"status":400,"message":"Malformed request"}` (WAF, not GraphQL-shaped) →
  `NJTransitRequestError`
- `data.errors[]` present → `NJTransitAPIError` carrying the first message
- `data.<field>` is `null` → `NJTransitNotFoundError` (how an unknown station reports)

### 5.2 Exceptions

```
NJTransitError
├── NJTransitConnectionError    # transport; retryable
├── NJTransitRequestError       # WAF / malformed; NOT retryable, indicates a client bug
├── NJTransitAPIError           # GraphQL errors; likely upstream schema drift
└── NJTransitNotFoundError      # null payload; e.g. unknown station
```

Coordinators map `NJTransitConnectionError` → `UpdateFailed`, and
`NJTransitRequestError` / `NJTransitAPIError` → `UpdateFailed` plus a logged warning,
since those signal the endpoint changed under us.

### 5.3 Models

```python
class TrainStatus(StrEnum):
    ON_TIME = "on_time"
    DELAYED = "delayed"
    CANCELLED = "cancelled"
    BOARDING = "boarding"
    ALL_ABOARD = "all_aboard"
    DEPARTED = "departed"
    UNKNOWN = "unknown"


class CrowdLevel(StrEnum):
    LIGHT = "light"
    MODERATE = "moderate"
    HEAVY = "heavy"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Car:
    number: str
    color: str  # raw hex, preserved
    level: CrowdLevel


@dataclass(frozen=True)
class Departure:
    train_id: str  # may be non-numeric (§3.4)
    scheduled: datetime  # tz-aware, America/New_York
    destination: str
    line: str
    line_abbreviation: str
    track: str | None
    status: TrainStatus
    status_raw: str  # always preserved
    status_text: str  # derived: "Cancelled" / "22 min late" / "On time" / ""
    delay_minutes: int | None
    inline_message: str | None
    cars: tuple[Car, ...]  # empty when capacity absent (§2.3)


@dataclass(frozen=True)
class DepartureBoard:
    station: str
    departures: tuple[Departure, ...]
    banner_message: str | None
    fullscreen_message: str | None


@dataclass(frozen=True)
class SystemAlert:
    line_abbreviation: str
    message: str
    message_html: str | None  # msg_richtext
    url: str | None  # msg_url
    service: str  # "Rail" | "Light Rail" | "Bus"
    is_advisory: bool  # advisoryAlert == "1"
    train_ids: frozenset[str]  # parsed from message body (§6.3)


@dataclass(frozen=True)
class Station:
    title: str  # exactly what the board expects (§3.5)
    penta_id: str  # stable identifier, e.g. "RT"
    accessible: bool | None
```

`advisoryAlert` semantics (`"0"` = live incident, `"1"` = planned advisory) are inferred
from data, not documented. Consistent across all observed samples.

## 6. Parsing rules

### 6.1 Departure time resolution

Given board value `"8:25 AM"` and fetch time `now` (America/New_York):

1. Parse as a naive time-of-day.
2. Combine with `now.date()`.
3. If the result is more than 3 hours before `now`, add one day (rollover).
4. Localize via `zoneinfo`, handling `fold` for DST.

The 3-hour window rather than a strict `< now` comparison tolerates recently-departed
trains. DST transitions produce ambiguous or nonexistent local times twice a year; resolve
ambiguity to the first occurrence and log at debug.

### 6.2 Delay computation

Board `status` of the form `"in N Min"` counts down to *actual* departure, while
`departureDate` is the *scheduled* time:

```
delay_minutes = round(((now + timedelta(minutes=N)) - scheduled).total_seconds() / 60)
```

Clamp negatives to 0. When `status` is empty, delay is `None` — not 0. The distinction
matters: empty means "no realtime data yet", the normal state for departures more than
roughly an hour out.

There is no structured alternative — `realtime.adherence` would have replaced this, but it
is null on every leg (§2.4). This heuristic is the only delay signal available.

### 6.3 Train-number extraction from alert prose

Observed forms:

```
M and E train 6612, the  7:44 AM arrival into PSNY, is up to 15 minutes late ...
Update: M and E train #6607, the 7:07 AM departure from PSNY, ... is up to 25 minutes late ...
M and E train #6324, the 8:54 AM departure from Summit, ... is cancelled ...
```

Note the inconsistent `#`, the doubled space, the `Update:` prefix, and that a single
message may name a *substitute* train ("Please take train #7877"). Extract all matches of
`r'\btrain\s+#?(\w{1,5})\b'`, then drop any following the phrase "Please take" so
substitutes are not misreported as disrupted.

Heuristic by nature. Must never raise; a message yielding no train IDs is still a valid
line-level alert.

### 6.4 Line correlation

`getSystemStatus.abbreviation` → set of `getTrainLines.abbreviation`:

```
MNE  → {MNE, MNEG}      # umbrella covers Morristown + Gladstone
BNTN → {BNTN, BNTNM}    # Montclair-Boonton + Montclair
MNBN → {MNBN, MNBNP}    # Main-Bergen + Port Jervis
NJCL → {NJCL, NJCLL}    # Coast Line + Bay Head
```

All other codes map to themselves; unknown codes map to themselves and log at debug.

**There is a fourth line vocabulary, on the board itself.** `lineAbbreviation` reads `M&E`,
which matches neither the alert feed's `MNE` nor `getTrainLines`. It is display text and is
useless for correlation.

The board's `line` field carries full titles, and *those* match `getTrainLines.title`
exactly for twelve of the thirteen rail lines. The exception is the one this integration
was built for: the board says `Morristown Line` where `getTrainLines` says
`Morris & Essex Line`. So resolution is: exact title match against `getTrainLines`, then a
one-entry alias table for `Morristown Line`, then give up.

Giving up **fails open** — an unresolved line means no line filter, so every rail alert is
reported. A missed delay alert is worse than a noisy one, and this vocabulary is exactly
the sort of thing that shifts upstream without notice.

### 6.5 Crowding

`#0B6623` → `LIGHT`, `#FFD300` → `MODERATE`, unrecognized → `UNKNOWN` with the raw hex
preserved on the `Car`. Compare case-insensitively. Do not infer a red value until one is
observed (§2.3).

## 7. Coordinators

Three, so a failure in one does not blank the others:

| Coordinator | Query | Interval | Scope |
|---|---|---|---|
| `StaticCoordinator` | `getTrainScheduleStationsRailForDV`, `getTrainLines` | 24h | per hub |
| `RouteCoordinator` | `getTripPlannerSchedule` | 24h, plus on options change | per hub |
| `SystemStatusCoordinator` | `getSystemStatus` | default 120s | per hub |
| `DepartureCoordinator` | `getTrainDepartureScreens` | default 60s | per station |

`RouteCoordinator` pages `getTripPlannerSchedule` across the service day (§2.6) for each
date in the horizon, unioning blocks where `routeType == "C"`. It produces two things:

- the train-ID set used as the destination filter (§2.5)
- the full `(train, departure, arrival)` schedule backing the calendar (§9)

Horizon is **today and tomorrow** — roughly 48 calls per day per commute. Tomorrow matters
because "what time is my train in the morning" is asked the night before, and because
querying per-date makes weekend and holiday timetables fall out automatically.

It refreshes daily, staggered off midnight, and on options change. A failure degrades the
destination filter to label substring matching and leaves the calendar with stale or empty
data rather than failing setup — the fallback must exist and must be tested.

### 7.1 Coordinator sharing across entries

Since entries are commutes (§8.0), several may share an origin — and `getSystemStatus` is
global to begin with. Naively giving each entry its own coordinators would poll the same
board twice for two commutes out of Short Hills. Coordinators are therefore keyed and
refcounted in `hass.data`:

```python
hass.data[DOMAIN] = {
    "status": SystemStatusCoordinator,  # one per HA instance
    "static": StaticCoordinator,  # one per HA instance
    "boards": {"RT": DepartureCoordinator},  # one per origin pentaStationID
}
```

`RouteCoordinator` is per-entry and not shared — it is defined by the pair.

Setup increments a refcount and reuses any existing coordinator; unload decrements and
tears down only when the last referent goes. Getting this wrong is a leak in one direction
and a broken second entry in the other, so both paths need explicit tests: unload one of
two commutes sharing an origin and assert the board coordinator survives; unload the
second and assert it is gone.

Resulting traffic for the two-commute Short Hills case: one board poll every 60s, one
status poll every 120s, and two route queries per *day*. Adding the Hoboken commute to an
existing NY Penn setup costs one extra query per day and nothing per-poll.

All responses carry a `maxAge: 30` cache hint, so 30s is the vendor-sanctioned floor.
Enforce it as a hard minimum in the options flow.

Separate departure coordinators mean a user tracking both origin and destination boards
does not have one station's outage mark the other unavailable.

## 8. Config flow

### 8.0 A config entry is a *commute*, not a station

Multiple entries are explicitly supported: Short Hills → New York Penn and Short Hills →
Hoboken coexist as two entries, as do reverse-direction entries (New York Penn → Short
Hills) for the trip home.

Unique ID is the **pair**: `f"{origin_penta}-{destination_penta}"`, or `origin_penta`
alone when no destination is configured. Keying on origin alone would make the second
commute a duplicate and abort the flow.

Device name is the pair — `Short Hills → Hoboken` — so entity IDs read
`sensor.short_hills_hoboken_next_departure` and stay unambiguous when several commutes
share an origin.

**Train-ID sets legitimately overlap between commutes.** Verified 2026-08-03: Short Hills
→ NY Penn resolves to `{411, 480, 6328, 6628}` and Short Hills → Hoboken to
`{411, 480, 1652, 3835, 6628}`. Train `411` is a Gladstone train to Summit, usable for
either commute depending on what you transfer to. Both entries surfacing `411` as the next
departure is correct, not double-counting.

The consequence is that the train-ID set encodes *which trains to board*, not the full
itinerary — the second leg and total duration are dropped. That is acceptable for v1,
whose job is "when do I leave". Restoring itinerary detail means retaining the planner
trips rather than reducing them to a set, and belongs with the deferred trip-planner work.

### 8.1 Flow steps

**Step `user`** — creates a commute entry:

1. Fetch `getTrainScheduleStationsRailForDV`, sort by `title`.
2. `SelectSelector` for **origin station**.
3. `SelectSelector` for **destination** (optional).
4. Validate the origin with a board query; `NJTransitNotFoundError` → `invalid_station`.
5. If a destination was chosen, resolve the train-ID set via `getTripPlannerSchedule`
   (§2.5) using the ` Station`-suffixed name vocabulary (§3.5). No itineraries returned →
   warn but do not block; fall back to label matching.

Abort with `already_configured` if the origin/destination pair already exists.

**Options flow:** departure interval, status interval, number of upcoming-departure
sensors (default 3, max 10), disruption threshold and lookahead. The destination is part
of the unique ID and therefore *not* editable here — changing it means adding a new entry.

Reauth is not applicable — no credentials.

## 9. Entities

One device per config entry, named for the origin station.

### Sensors

Entities are named for the **commute**, not the origin or the line, because the device is
the commute. For Short Hills to New York Penn that is
`sensor.short_hills_station_to_new_york_penn_station_next_departure`.

| Entity | State | Key attributes |
|---|---|---|
The favourite picker is built from `RouteData.trips`, so it offers the day's direct
services labelled by departure time rather than a free-text box. It keeps
`custom_value`, and re-adds any already-saved favourite missing from today's trips --
otherwise editing options on a weekend would silently drop a weekday train. With no
resolved schedule it degrades to free text; an unconfigurable option is worse than an
unvalidated one.

| `<commute>_stops_away` | how far the favourite train is, from `getTrainStopList` | `train_id`, `last_departed`, `next_stop`, `due_at_origin`, `due_at_destination`, `stops_total`, `stops_remaining` |
| `<commute>_train_event` | discrete changes: cancelled, delayed, track_changed, alerted, line_cancellation | `train_id`, `scheduled`, `destination`, `track`, `status_text`, `delay_minutes`, `previous_track` |
| `<commute>_next_favorite` | next departure whose train is in `favorite_trains` (`device_class: timestamp`) | same as the departure sensors, plus `favorites` |
| `<commute>_next_departure` | next matching departure (`device_class: timestamp`) | `train_id`, `track`, `destination`, `line`, `status`, `status_raw`, `status_text`, `favorite`, `delay_minutes`, `inline_message`, `crowding`, `cars`, `alerts` |
| `<commute>_departure_2` … `_N` | 2nd..Nth matching departure | same |
| `<commute>_delay` | `delay_minutes` of next departure (`duration`, `min`) | — |
| `<commute>_crowding` | `CrowdLevel` of the next departure (`device_class: enum`) | `positions` |
| `<commute>_service_alerts` | count of live alerts on this commute's lines | `messages`, `urls`, `lines`, `train_ids`, `affects_my_trains` |
| `<commute>_planned_advisories` | count of planned advisories | same |

**Alert sensors are per commute, not per line.** An earlier draft of this table had
`sensor.<line>_alerts`, which was open question §13.3. Per commute won on two grounds:
`affects_my_trains` is only computable against a specific board, and a user with two
commutes on the same line wants each device to be self-contained rather than pointing at a
shared entity elsewhere. The cost is that a two-commute setup on one line carries two alert
sensors with near-identical contents, which is cheap — they read from one shared
coordinator.

Line scoping still happens, inside the sensor: alerts are narrowed to the codes resolved
from the board's line titles (§6.4), and an unresolvable line reports every rail alert
rather than none.

"Nth matching departure" indexes the filtered list, so `departure_2` always means "the
second train I could actually take" rather than whatever is second on the raw board.

**The filter is a union, not a preference order.** A departure qualifies if its train is in
the resolved train-ID set (§2.5) *or* its board label shares a significant word with the
destination. Both signals are incomplete, so neither gets a veto:

- The planner set catches transfer itineraries a label match would discard — the Gladstone
  train to Summit that connects onward.
- The label catches trains the planner set is missing, which happens whenever that set is
  stale, partially resolved, or predates a timetable change.

Treating the planner set as authoritative looks tidier and is wrong. It silently drops
real trains, and "silently drops a cancelled train" is the exact failure this integration
exists to prevent. The recorded disruption demonstrates it: train 6320 is labelled
`New York` and cancelled, and a single planner page resolves only four trains, none of
them 6320.

Word matching treats `penn`, `station` and `terminal` as noise. Without dropping `penn`,
Newark Penn and New York Penn match each other.

`next_departure` is `unknown` when no matching departures remain (e.g. overnight), not
`unavailable` — the integration is working, there is simply no train.

### Binary sensor

`binary_sensor.<origin>_commute_disrupted` — `device_class: problem`, on when **any**
departure within `lookahead` minutes (default 90) matching the destination filter:

- has `status is TrainStatus.CANCELLED`, or
- has `delay_minutes >= threshold` (default 10), or
- has a `train_id` appearing in a live `SystemAlert.train_ids` for a correlated line (§6.4)

Attributes: `reasons` (human-readable), `affected_trains`.

The third condition is what the current YAML cannot express, and is why train 6320's
cancellation would have gone unnoticed this morning.

### Calendar

`calendar.<origin>_<destination>` — one per commute, backed by the paged schedule (§2.6).

One `CalendarEvent` per scheduled departure:

| Field | Value |
|---|---|
| `start` | scheduled departure from origin (tz-aware) |
| `end` | scheduled arrival at destination — the last `C` leg's `offStopTime` |
| `summary` | `Train 6328 to New York Penn Station` |
| `description` | duration, transfers (`Change at Summit, 9:38 → 9:46`), line |
| `location` | origin station title |
| `uid` | `{entry_id}-{date}-{train_id}` — stable across refreshes |

**The calendar is timetable data, not realtime.** This must be stated in the README: a
cancelled train still appears as an event. As a concession, events falling inside the
board's live window get their status folded into the summary
(`CANCELLED — Train 6320 to New York Penn Station`), since both coordinators are already
in `hass.data`. Beyond that window there is no realtime signal to apply.

`async_get_events(start, end)` returns cached events clamped to the horizon. **A month
view will show only today and tomorrow populated.** That is a real, visible limitation and
is the direct consequence of a 3-trips-per-call upstream — covering a month would cost
roughly 700 requests. Document it rather than papering over it; do not silently return an
empty list for out-of-horizon ranges without logging at debug.

### Diagnostics

Dump last raw payloads; no redaction needed (no credentials, no personal data). Include
configured stations, the computed line correlation, and the paged schedule size — the
likely sources of user-reported bugs.

## 10. Testing

- **Fixtures**: recorded JSON under `tests/fixtures/`, captured 2026-08-03 ~08:20 ET during
  a live M&E disruption. The set is deliberately coherent — all queries issued within the
  same minute — so cross-feed correlation can be tested end to end:

  | Fixture | Contents |
  |---|---|
  | `system_status_disruption.json` | 5 MNE alerts (4 live), mixed `#` usage, `Update:` prefix, a "Please take" substitute |
  | `departures_short_hills_disruption.json` | 19 board rows, 2 cancellations with mismatched casing, `capacity` on 5 rows |
  | `trip_planner_short_hills_to_ny.json` | 3 itineraries incl. a Summit transfer and a PATH alternative |
  | `trip_planner_short_hills_to_hoboken.json` | second commute from the same origin — exercises the §8.0 overlap |
  | `stations_rail_dv.json` | 177 stations with `pentaStationID` |
  | `train_lines.json` | 13 rail lines |
  | `planner_day_short_hills_to_ny.json` | **derived, not a capture** — the observed 51-train service day, driving the fake pager |

  The pair exhibits the §1 divergence exactly: alerts name trains `309, 6311, 6324, 6607`;
  the board shows `6320` and `6311` cancelled. **`6320` is cancelled on the board and
  absent from the alert feed**, `6324` and `6607` are in alerts but off the board, and only
  `6311` appears in both. Any change that stops `binary_sensor` firing on `6320` from these
  fixtures is a regression in the integration's core purpose.

  The two planner fixtures pin the §8.0 overlap: NY Penn resolves to
  `{411, 480, 6328, 6628}` and Hoboken to `{411, 480, 1652, 3835, 6628}` — three shared,
  one NY-only, two Hoboken-only. Assert that two entries built from these produce distinct
  entity sets while both legitimately surface `411`.
- **Parsing tests**: table-driven over §6 — DST boundaries, midnight rollover, non-numeric
  train IDs, empty status, "Please take train #NNNN" exclusion, unknown crowding colors.
- **Paging tests** (§2.6): the loop is driven by recorded planner responses, so it needs
  its own fixture set — a full 24-call capture for one date. Cover the non-advancing
  window (all three trips share a departure time → the +30min guard fires), the iteration
  cap, and a mid-page failure leaving a partial day. Assert the Short Hills → NY Penn
  capture yields 51 trains, not 4: that number is the regression guard for the §2.6 bug.
- **Calendar tests**: event boundaries from `offStopTime`, `uid` stability across a
  refresh, out-of-horizon ranges, and the cancelled-train summary prefix inside the board
  window.
- **Client tests**: `aioresponses` covering each §5.2 error path, notably the non-GraphQL
  WAF response.
- **Config flow tests**: full coverage including `invalid_station` and duplicate abort.
- **Layering test**: no `homeassistant` import under `api/` (§4.1).
- **Snapshot tests**: `syrupy` for entity state, per current core convention. Request
  `HomeAssistantSnapshotExtension` explicitly rather than inheriting whichever `snapshot`
  fixture wins — syrupy and pytest-homeassistant-custom-component both register one, and
  plugin load order is not stable across environments. Getting this wrong passes locally
  and fails in CI reporting every snapshot as missing.
- **Coverage**: gated at 97%, currently 98%. Deliberately below the 100% `pyschlage` uses;
  a Home Assistant integration carries lifecycle branches that cost more to exercise than
  they are worth. Reasoning lives in `.coveragerc`.

## 11. Scaffolding

- `manifest.json`: `iot_class: cloud_polling`, `config_flow: true`,
  `integration_type: service`, no `requirements` (bundled client),
  `codeowners: ["@dknowles2"]`
- `hacs.json`: `{"name": "NJ Transit", "render_readme": true}`
- CI: ruff, mypy, pytest + coverage gate, hassfest, HACS validation
- `custom_components/njtransit/brand/`: icons served directly by Home Assistant, so no
  pull request against `home-assistant/brands` is needed. Generated by
  `scripts/generate_brand.py`; the artwork is original, and deliberately not NJ Transit's
  trademarked mark.

## 12. Risks

**The endpoint is private and undocumented.** Introspection is off, a WAF sits in front,
and NJ Transit has made no compatibility commitment. It can change without notice.
Mitigations: pinned fixtures turn drift into test failures, narrow field selections avoid
§3.1, and `scripts/extract_ops.py` re-derives the site's own operations on demand. This
remains the dominant risk and belongs in the README.

NJ Transit also operates a registration-gated official developer API, not evaluated here.
If it covers departures and alerts it would be a sturdier foundation and is likely a
prerequisite for core acceptance — worth evaluating before investing in deferred-tier
features.

## 13. Open questions

1. ~~Spike `getTripPlannerSchedule.realtime`~~ — **resolved 2026-08-03, see §2.4.** Null
   for rail and bus alike; not usable. Heuristics stay.
2. Should the destination be multi-valued (e.g. New York *or* Hoboken both acceptable)?
   §2.5 makes this cheap — union the train-ID sets from two planner queries.
3. ~~Per-line alert entities, or one entity with a line attribute?~~ — **resolved by the
   implementation, see §9.** Neither: alert sensors are per *commute*, scoped internally
   to that commute's lines. `affects_my_trains` is only computable against a specific
   board, which decided it.
4. Is the 19-row board cap universal? It held for Short Hills, Summit, and Trenton, which
   suggests a fixed limit and bounds any lookahead beyond roughly two hours.
5. Does `dropOff` on stop-list entries mark drop-off-only stops? Empty on all sampled rows.
6. Does the resolved train-ID set need a weekend/holiday variant? `RouteCoordinator`
   queries per date for today and tomorrow, which should handle this implicitly — but
   that has only been reasoned about, not watched across an actual weekend or holiday.
   Still open until someone confirms it from a running instance.
