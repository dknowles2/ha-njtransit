# NJ Transit commuter app: user journeys

This is the implementation backlog expressed as journeys. Each one is something a real
person does with the app, written so that it can be turned into acceptance tests, tracked
as a story, and demoed when done. Screen names refer to `01-ux-spec.md`; server
capabilities refer to `02-architecture.md`.

Every journey has the same shape:

- **Trigger**: what starts it (a tap, a push, a time, a data change)
- **Preconditions**: what must already be true
- **Steps**: what the person sees and does, in order
- **System**: what the app and backend do underneath
- **Done when**: the observable outcome that makes this pass
- **Edge cases**: the variants that must also pass, each its own test
- **Data**: which upstream fields the journey depends on, so a feed gap is visible early
- **Phase**: MVP / v1 / v2, matching the delivery plan in `02-architecture.md`

Journey IDs are stable. Reference them in commits and tickets (`J-C3`, not "the platform
one").

Two rules run through all of them and are worth stating once rather than in every entry:

1. **Nothing known is not the same as on time.** Any place a journey shows a status, the
   "no realtime yet" state is its own branch, not a default of "On time".
2. **Only transitions notify.** Any journey that pushes must not push on app launch, on
   backend restart, or on a repeated poll that shows the same problem.

---

## A. First run and onboarding

### J-A1: Install and reach a useful screen in under a minute

- **Trigger**: First launch after install.
- **Preconditions**: None. No account, no permissions granted.
- **Steps**:
  1. Welcome screen explains the one idea: "Tell us your commute, not your station."
  2. App asks for location *while in use* with a one-line reason ("to suggest your
     station"). Skipping is allowed.
  3. Origin picker opens pre-filled with the nearest rail station if location was granted;
     full list of ~167 stations searchable underneath. The suggestion is a suggestion; the
     field is still required.
  4. Destination picker. "No destination: show the whole board" is an explicit option at
     the bottom, with one sentence on what that gives up (no journey filtering, no calendar,
     no onboard mode).
  5. A single toggle, on by default: "Also add the trip home" (creates the reverse commute).
  6. Land on the Home screen with live data.
- **System**: `GET /v1/stations`, `GET /v1/stations/nearby?lat&lon` (client calls this; the
  server never stores coordinates), `POST /v1/devices` to register anonymously,
  `POST /v1/commutes` (one or two). First board fetch is served from the backend's hot cache
  if that station is already polled; otherwise the backend starts polling it and the first
  response may take one upstream round-trip.
- **Done when**: A brand-new device sees the next usable train for its commute within 60
  seconds of first launch, with no account and no typed station name.
- **Edge cases**:
  - Location denied: no suggestion, list opens to search, nothing blocks.
  - Location granted but nearest-station lookup fails: same as denied, silently.
  - Nearest station is a light-rail or bus stop: not offered; rail only.
  - Station pair has no direct service: the app says so at pick time ("No direct trains
    between these. We'll show connecting itineraries.") rather than showing an empty board
    later.
  - Origin == destination: rejected inline.
  - Alias titles in the station list (SPEC 3.5): the picker shows one row per 2-char code.
- **Data**: station list (title + `penta_id` + accessible), proximity search (feet →
  metres), board.
- **Phase**: MVP

### J-A2: Pick favorite trains during onboarding

- **Trigger**: Step after J-A1, optional and skippable.
- **Preconditions**: A commute with a destination exists.
- **Steps**:
  1. "Which trains do you usually take?" shows today's direct trains for the commute as
     rows labelled by departure time and train number ("7:48 · 6662 · Morristown Line").
  2. Multi-select. Weekday/weekend toggle if today is a weekend, since the weekday number
     set differs.
  3. Continue. If skipped, the Home screen follows the next usable train instead and a
     gentle prompt appears later (J-G1).
- **System**: `GET /v1/commutes/{id}/trips?date=` (day's trips joined origin↔destination),
  `PATCH /v1/commutes/{id}` with `favorites: ["6662", "6664"]`.
- **Done when**: The Home screen's hero shows the next favorite rather than the next
  usable train.
- **Edge cases**:
  - Onboarding on a Saturday for a weekday commute: offer "show weekday trains".
  - Favorite not running today (holiday schedule): the hero falls back to next usable and
    says why ("Your 7:48 isn't running today").
  - Amtrak IDs like `A79` at Trenton: shown, selectable, treated as strings.
- **Data**: day's trips (planner or `getStationSchedule` join), train numbers stable
  weekday-to-weekday.
- **Phase**: MVP

### J-A3: Decide about notifications at the right moment

- **Trigger**: First time the app has something worth notifying about, not at install.
- **Preconditions**: A commute exists; OS notification permission not yet asked.
- **Steps**:
  1. The Home screen shows a card: "Want a heads-up if your 7:48 is cancelled or moves
     track?" with **Turn on** / **Not now**.
  2. Turn on triggers the OS prompt. Denial is remembered and the card does not return for
     30 days; Settings still offers it.
- **System**: `POST /v1/devices/{id}/push-token`. Server marks subscription as push-capable.
- **Done when**: A push arrives for the next real transition on that commute.
- **Edge cases**: User grants OS permission but leaves in-app toggles off; the reverse.
  Provisional (quiet) authorization on iOS is used so the first pushes land without a
  prompt.
- **Phase**: MVP

### J-A4: Optional account, and what it buys

- **Trigger**: User opens Settings → Account (global), or installs on a second device.
- **Preconditions**: None.
- **Steps**:
  1. Explain plainly: "Everything works without an account. An account syncs your commutes
     and favorites across devices and restores them if you get a new phone."
  2. Sign in with Apple / Google / email magic link.
  3. On success, device-scoped commutes are merged into the account.
- **System**: `POST /v1/auth/...`, `POST /v1/accounts/merge-device`. Subscriptions re-keyed from
  device to account; device token stays attached for push.
- **Done when**: Second device signed in to the same account sees the same commutes and
  favorites without re-entering them.
- **Edge cases**: Merge conflict (same origin/destination on both sides): keep one, union
  the favorites. Sign out: keep local copy, stop sync.
- **Phase**: v1

---

## B. Before leaving (home, office)

### J-B1: Glance at whether the commute is fine

- **Trigger**: Open the app, or look at the widget, or look at the Live Activity.
- **Preconditions**: At least one commute; inside the commute window if one is set,
  otherwise any time.
- **Steps**:
  1. The Home screen opens on the commute whose window is active (or the most recently
     viewed one).
  2. Top: a disruption banner *only if* something is wrong: "Train 6320 (8:12) is
     cancelled". Nothing at all when the commute is fine. The whole card tints green /
     amber / red by the worst thing affecting the train actually shown.
  3. Hero: minutes until the next favorite (or next usable), largest type on the page,
     ticking every 10 s client-side.
  4. Under it: track (or "Track 7 (likely)" if only the signaling system has it, or
     "Track overdue" inside 6 minutes when other trains have theirs), status text, busyness
     (only "Filling up"/"Busy" appear; quiet trains say nothing).
  5. Then how far away it is: "Just left Summit · 1 stop away" once a favorite is in the
     lookahead window.
  6. Then the board: the next N usable departures.
- **System**: `GET /v1/commutes/{id}/board` (ETag). Server computes `status_text`,
  `track_source`, `favorite`, `crowding`, `stops_away` once per (station, train) and
  serves it to every subscriber. Client ticks the countdown locally from `scheduled +
  delay_minutes`.
- **Done when**: On a normal morning the screen shows one number, one track, and no
  warnings; on a bad morning the reason is the first thing on screen.
- **Edge cases**:
  - No realtime yet (train > ~1h out): status area is empty, not "On time".
  - `delay_minutes` is 0: "On time". `null`: empty. These are different branches.
  - Cancelled favorite: hero routes to the next favorite/usable train, banner names the
    cancelled one, card does **not** tint red for the train it routed around.
  - No more trains today: hero says "No more trains today", board shows tomorrow's first
    three from the timetable, clearly labelled as scheduled.
  - Data stale (> 3 min since last successful upstream poll): "as of 3 min ago" pill;
    > 10 min: the tint desaturates and a "Live data unavailable" line appears.
- **Data**: board (19-row cap), day's trips, stop list for the favorite, alerts.
- **Phase**: MVP

### J-B2: Decide which of two trains to take

- **Trigger**: User scrolls the board or taps a row.
- **Preconditions**: J-B1.
- **Steps**:
  1. Board rows show time, train number, headsign, track, status text, busyness pill, and
     a star if favorite.
  2. Tap a row → Departure Detail: full stop list with the current position marker,
     scheduled vs. estimated times per stop, consist with per-car crowding
     (front/middle/back), any alert naming this train, and "this station usually posts the
     track ~9 min before departure" derived from track history.
  3. "Make this a favorite" on the detail screen.
- **System**: `GET /v1/departures/{station}/{train_id}?scheduled=` (stop list fetched on
  demand; the backend caches it per train for the poll interval since it costs one
  upstream call each).
- **Done when**: A user can see where a train is and how full it is before choosing.
- **Edge cases**: Consist data absent (not imminent): crowding section hidden, not
  "unknown". Transfer itinerary (fail-open case): shows "Change at Newark Broad St" from
  the trip's legs and never pretends it's one-seat.
- **Data**: stop list, cars, alerts by train_id, trips.
- **Phase**: MVP (detail), v1 (track-history stat)

### J-B3: Check the trip home while still at work

- **Trigger**: User switches direction.
- **Preconditions**: Two commutes (A→B and B→A).
- **Steps**:
  1. Swipe horizontally or tap the direction toggle at the top of the Home screen.
  2. Same layout for the reverse commute. The app remembers which one you looked at last,
     and defaults to whichever window is active.
- **System**: Both commutes on one device share the account's/device's subscription list;
  the backend already polls both stations.
- **Done when**: Switching direction is one gesture and never requires re-entering anything.
- **Edge cases**: Only one commute configured: the toggle offers "Add the trip home".
- **Phase**: MVP

### J-B4: Look at tomorrow (or the weekend)

- **Trigger**: Tap the calendar icon on the Home screen.
- **Preconditions**: Commute with a destination.
- **Steps**:
  1. Timetable view: today and tomorrow, one row per train, with journey time and
     "Change trains" where required. Cancellations from the live board fold into today's
     rows that are within the board's horizon.
  2. "Add to calendar" exports one event per favorite train (or all trains) for the day.
- **System**: `GET /v1/commutes/{id}/trips?date=`. Route data refreshes at 01:45 local
  because RailData publishes a day's schedule from that day's midnight.
- **Done when**: A user can see tomorrow's first train at 10pm tonight.
- **Edge cases**: Tomorrow's schedule not yet published (RailData source before 01:45):
  show "Tomorrow's schedule is published after midnight" rather than an empty list.
  Holiday schedule: label it.
- **Phase**: v1

---

## C. At the station / on the platform

### J-C1: Read the track from across the concourse

- **Trigger**: User is at the origin station, phone at arm's length.
- **Preconditions**: J-B1; train within ~15 minutes.
- **Steps**:
  1. Hero shows the countdown and track in the largest type. Tint colour is readable
     without reading text.
  2. If RailData signaling has the set on a platform before the board posts:
     "Track 7 (likely)". The moment the board posts, the hedge drops and the board's
     number wins, even if different.
  3. At 6 minutes out with no track while other trains have theirs: "Track overdue" in
     red. A cancelled train never shows this.
- **System**: Server merges `track` and `signaled_track` into `track_source`; signaled
  memory per (station, train, scheduled) held while the train is absent from the vehicle
  feed, cleared when it moves off a platform.
- **Done when**: On NY Penn departures, ~65% show a likely track a median of ~18–21 minutes
  before departure; every one shows the board's track once posted.
- **Edge cases**:
  - Signal and board disagree: board wins, and the history records a mismatch for the
    stats dataset.
  - Station without a circuit decoder: no "likely" ever appears; no "overdue" unless the
    station is posting tracks at all.
  - Amtrak trains: excluded from "overdue" (they post at T-13 median but 16% at T-0).
- **Data**: board `track`, RailData `getVehicleData` decoded via `circuits.py` (NY only).
- **Phase**: MVP (board track), v1 (signaled track, overdue)

### J-C2: The track changes while you're standing on the wrong platform

- **Trigger**: Board moves a train after a track was published.
- **Preconditions**: Push enabled; train is a favorite or the next usable; within
  lookahead.
- **Steps**:
  1. Time-sensitive push: "6662 moved to Track 4 (was 7)".
  2. Live Activity updates in place; hero updates on next fetch (push includes a silent
     refresh hint).
- **System**: Central transition detector fires `track_changed` once per (station, train)
  with `previous_track`; fan-out joins to subscriptions where this train is on the
  subscriber's board.
- **Done when**: A track move reaches the phone within one poll interval plus push latency
  (target < 90 s).
- **Edge cases**: First assignment is *not* a change (no push). Change after departure:
  ignored. Two changes in one interval: the latest wins, one push.
- **Phase**: MVP

### J-C3: The train is boarding / all aboard

- **Trigger**: Board status becomes `boarding` or `all_aboard`.
- **Preconditions**: J-C1.
- **Steps**: Hero status text becomes "Boarding" then "All aboard"; the countdown keeps
  ticking; Live Activity shows the same. No push (you're already there or you're not).
- **Done when**: Status changes appear within one poll.
- **Edge cases**: `all_aboard` before `boarding` (vocab is unstable): render whatever is
  current; `unknown` statuses render as empty, never as an error.
- **Phase**: MVP

### J-C4: Missed it. What's next?

- **Trigger**: The train the hero was following departs and the user is still at the
  origin (location says so, or they tap "I missed it").
- **Preconditions**: J-C1.
- **Steps**:
  1. The hero rolls to the next favorite, or next usable if "Follow any train once your
     favorites have gone" is on.
  2. If the user is still in the origin zone, Onboard mode (J-D1) is *not* entered.
- **System**: Client-side: location rule evaluated on-device. Server-side: the board
  drops the departed train; the next candidate is already in the payload.
- **Done when**: Missing a train never leaves the app counting down to an arrival you're not
  on.
- **Edge cases**: Location unavailable: fail open, enter onboard mode, offer "I'm not on
  this train" as a one-tap escape.
- **Phase**: v1

---

## D. Aboard

### J-D1: Ride with an arrival countdown

- **Trigger**: The followed train departs the origin.
- **Preconditions**: Favorite or followed train; stop list available.
- **Steps**:
  1. The Live Activity and the hero switch from "departs in" to "arrives in": ETA at the
     destination as scheduled arrival + current lateness (a lower bound, never earlier
     than schedule).
  2. Shows stops remaining and the next station: "Next: Newark Broad St · 3 stops to go".
  3. Clears when the train reaches the destination, not a fixed time after departure.
  4. **Dismiss** is always available (long-press on the notification, or a button in
     app) and stays quiet for the rest of the window.
- **System**: Server keeps polling the stop list for followed trains after departure (one
  call per followed train per interval, only while followed). Location gating is
  deliberately *not* applied once aboard.
- **Done when**: A rider sees a correct "stops to go" for the whole journey, and the
  activity ends at arrival.
- **Edge cases**:
  - Train is early to a stop: lateness floors at 0, ETA never earlier than schedule.
  - Stop list stops updating mid-run: show "last update N min ago", keep the previous
    estimate, do not clear.
  - The train terminates short (line_cancellation-style knock-on): if the destination
    disappears from the stop list, say so.
  - Commute window ends mid-ride: does not end the activity.
- **Data**: stop list `departed`, `scheduled`, per-stop status.
- **Phase**: MVP (in-app), v1 (Live Activity)

### J-D2: Connection decision on the train

- **Trigger**: User opens the app aboard to check whether they'll make a connection.
- **Preconditions**: J-D1.
- **Steps**: Detail screen shows lower-bound ETA at the destination and, if the commute
  is a transfer itinerary, the connecting train's scheduled departure and whether the
  lower bound still makes it.
- **Done when**: The app never says you'll make a connection that a lower-bound ETA says
  you won't.
- **Phase**: v2

---

## E. Disruptions

### J-E1: A favorite is cancelled

- **Trigger**: Board marks a train cancelled inside the lookahead window.
- **Preconditions**: Push enabled; inside commute window if set; user is somewhere the
  train could still be caught from (or location unknown).
- **Steps**:
  1. Push: "Your 7:48 (6662) is cancelled. Next: 8:02 (6664), on time, Track 3."
  2. Opening it lands on the Home screen with the banner and the hero already routed to
     the next train.
- **System**: `cancelled` transition → subscriber join → push with the *next* option
  precomputed server-side so the notification is actionable without opening the app.
- **Done when**: The push names the problem and the alternative in one line.
- **Edge cases**: Cancellation appears then reverts (vocab flap): a second push only if
  it re-cancels after having been uncancelled for a full poll. Cancelled train not on
  your favorites but the next usable: still notifies. Cancellation at startup/restart: no
  push.
- **Phase**: MVP

### J-E2: A train crosses the delay threshold

- **Trigger**: `delay_minutes >= threshold` for a train inside lookahead.
- **Preconditions**: Threshold per commute (default 10).
- **Steps**: Push: "6662 is running 12 min late". Lateness is tracked in five-minute
  bands: drifting 12 → 14 is silent, escalating 12 → 17 pushes again ("now 17 min late"),
  recovery never pushes, and crossing the threshold again after a recovery does. This is
  the disruption blueprint's bucketing rule, which exists because the unbucketed version
  sent five notifications in seven minutes about one train.
- **System**: Threshold varies per subscriber, so the central detector emits
  `delay_changed(from, to)` and the fan-out evaluates each subscriber's threshold
  crossing and band escalation against that subscriber's last delivered band.
- **Done when**: One push on crossing and one per worse band, per subscriber, at their
  threshold; a week of one train hovering around 10 minutes late produces one push a day.
- **Edge cases**: `delay_minutes` goes from `null` to 15 (first realtime data is already
  late): that is a crossing. Threshold lowered in Settings mid-disruption: no retroactive
  push.
- **Phase**: MVP

### J-E3: An alert names your train

- **Trigger**: A live service alert's extracted train IDs intersect your board.
- **Steps**: Push: "Alert for 6662: 'Train 6662 is operating 15–20 minutes late due to
  signal problems near Summit.'" Alerts tab opens with "Affects your trains" first.
- **System**: Train-number extraction from prose (SPEC 6.3) runs once server-side;
  `alerted` transition when a train is *newly* named.
- **Done when**: An alert naming a train on your board pushes once; the same alert
  re-polled does not.
- **Edge cases**: Alert names a train not on your board: in-app only, in the Alerts tab.
  Alert text edited upstream (same incident, new wording): treat as same alert if the
  train set is unchanged.
- **Phase**: MVP

### J-E4: The train ahead of yours was cancelled (knock-on)

- **Trigger**: A train on your line that you *cannot* use, departing within 30 minutes
  ahead of one you can, is cancelled.
- **Steps**: In-app only, never a push (it's an inference, not a fact): the affected
  train's board row and Departure detail show "Train 6311, just ahead of yours, was
  cancelled: expect extra stops and crowding", and it appears on the Alerts tab under the
  commute.
- **System**: `line_cancellation` with `affects_train`. Costs nothing extra: the station
  board already has the row.
- **Done when**: The inference appears against the right train and never contributes to
  the red tint or the disruption banner.
- **Phase**: v1

### J-E5: Nothing is wrong (and the app says nothing)

- **Trigger**: Any normal poll.
- **Steps**: No banner, no push, green tint. This is a journey because "silence when
  nothing is wrong" is the behaviour that makes every other alert trusted.
- **Done when**: A week of normal commutes produces zero pushes.
- **Phase**: MVP

### J-E6: The disruption clears

- **Trigger**: All reasons go away.
- **Steps**: Banner disappears; no "resolved" push by default (the list only shrinks);
  optional "all clear" toggle in Settings for people who want it.
- **Phase**: MVP (silent), v1 (optional all-clear)

---

## F. Alerts and advisories

### J-F1: Read live alerts for my lines

- **Trigger**: Alerts tab.
- **Steps**: Sections: "Affects your trains" (train named on your board), "Your lines"
  (live), "Planned" (advisories, track work, event service). Each row: line badge,
  message, link to the full advisory where upstream provides one, time first seen.
- **System**: `GET /v1/alerts?lines=` scoped to the commute's line codes (umbrella codes:
  `MNE` covers Morristown and Gladstone).
- **Edge cases**: RailData source carries no planned advisories: section hidden with a
  footnote, not shown empty. HTML variant present: render rich text safely.
- **Phase**: MVP

### J-F2: Remember a planned advisory for next weekend

- **Trigger**: User taps "Remind me" on a planned advisory.
- **Steps**: Local notification scheduled the evening before the advisory's date, or the
  morning of if no date is parseable.
- **Phase**: v2

### J-F3: Station-wide banner / takeover message

- **Trigger**: The board carries `banner_message` or `fullscreen_message`.
- **Steps**: Banner renders above the board; fullscreen takeover renders as a prominent
  card that can be collapsed but not dismissed while present.
- **Phase**: MVP

---

## G. Favorites and personalization

### J-G1: Discover favorites from behaviour

- **Trigger**: The same train has been the next usable train at open time on 3 of the last
  5 weekdays, and no favorites are set.
- **Steps**: One-time prompt on the Home screen: "You keep catching the 7:48. Make it a
  favorite?" Accept / dismiss.
- **System**: Client-side heuristic on local open history; nothing leaves the device.
- **Phase**: v1

### J-G2: Edit favorites

- **Trigger**: Commutes → the commute → Settings → Favorite trains, or the star on a board row.
- **Steps**: Same picker as J-A2; changes apply immediately (the integration learned that
  reloading for this was wrong).
- **Phase**: MVP

### J-G3: Set a commute window

- **Trigger**: Commutes → the commute → Settings → Commute window.
- **Steps**: Days + time range. Copy explains why this exists: "This is the only thing
  that can tell a day off from a commute. Location can't: on a WFH morning you're at home,
  which is exactly where you'd be before catching the train."
- **System**: Stored server-side (so push gating happens before send) and mirrored locally.
- **Edge cases**: Empty window = no time gating at all.
- **Phase**: MVP

### J-G4: Set "where I could still catch it from"

- **Trigger**: Commutes → the commute → Settings → Location.
- **Steps**: Either a radius from the origin station (default derived: half the distance
  from home to the station, floored at 2 units) or saved places (home, work). Plain
  explanation of the halving rule and that it silences the return leg on a WFH day.
- **System**: Evaluated on-device only. The server receives a boolean "eligible" with
  each Live Activity/alert acknowledgement, or the client suppresses locally. Coordinates
  never leave the phone.
- **Edge cases**: Location permission "while in use" only: rule applies only when app is
  foregrounded; explain the trade-off in copy. Permission denied or unknown: fail open.
- **Phase**: v1

### J-G5: Adjust delay threshold and lookahead

- **Trigger**: Commutes → the commute → Settings.
- **Steps**: Sliders with the same ranges as the integration (delay 1–60, lookahead
  15–240). Copy: "Lookahead is the setting to reach for first if alerts feel wrong."
- **Phase**: MVP

### J-G6: Multiple destinations for one origin

- **Trigger**: Commutes → the commute → Settings → "Also OK with…" (e.g. New York *or* Hoboken).
- **Steps**: Union of trip sets; board shows both; hero picks the earliest.
- **Phase**: v2 (SPEC 13.2 is open)

---

## H. Notifications and Live Activity

### J-H1: Live Activity starts before your train

- **Trigger**: A favorite enters the lead time (default 30 min before departure).
- **Preconditions**: iOS 16.2+ (17.2+ for push-to-start); Live Activity enabled; commute
  window active; location rule passes or is unknown.
- **Steps**:
  1. Lock Screen / Dynamic Island shows "6662 · Track 4 · 12 min late · Filling up" and a
     ticking countdown.
  2. Updates only on a real change (status, track, crowding, likely→confirmed track).
  3. On departure, switches to onboard (J-D1). Ends at arrival or on Dismiss.
- **System**: ActivityKit push-to-start token registered with the backend; updates via
  APNs Live Activity pushes with a per-activity budget; the backend suppresses no-op
  updates.
- **Done when**: A Live Activity for a normal commute sends fewer than 10 updates end to
  end.
- **Edge cases**: iOS rate limit hit: coalesce; the countdown ticks on-device regardless.
  App force-quit: push-to-start still works on 17.2+. Two favorites 10 minutes apart: one
  activity follows the earliest; after it departs and Dismiss is pressed, the second does
  not start.
- **Phase**: v1

### J-H2: Android ongoing notification

- **Trigger**: Same as J-H1.
- **Steps**: Persistent notification with chronometer, track, status; updates via FCM
  high-priority data messages; "Dismiss" action.
- **Phase**: v1

### J-H3: Dismiss and stay quiet

- **Trigger**: Dismiss on the activity/notification or in-app.
- **Steps**: Activity ends; no further activity or push for this commute until the window
  ends (or 3am if no window).
- **System**: Server-side "dismissed until" on the subscription so the suppression holds
  even if the phone is offline.
- **Phase**: v1

### J-H4: Notification preferences per commute

- **Trigger**: Commutes → the commute → Settings → Notifications.
- **Steps**: Toggles: cancelled, delayed (uses threshold), track changed, alert names my
  train, track overdue, all clear (off by default), Live Activity. Time-sensitive vs.
  default interruption level for the first four. Knock-on (`line_cancellation`) has no
  toggle because it never pushes.
- **Phase**: MVP (basic), v1 (interruption levels)

### J-H5: Quiet after a restart or reinstall

- **Trigger**: Backend restart, app reinstall, or device reboot during a disruption.
- **Steps**: Nothing pushes for problems that already existed. The app shows them on open.
- **System**: Transition detector needs a "before" state; on cold start it seeds from
  current state and emits nothing. Subscriptions store a "last delivered event" cursor.
- **Done when**: A backend deploy at 8am during a disruption produces zero duplicate
  pushes.
- **Phase**: MVP

---

## I. Widgets and watch

### J-I1: Home-screen widget

- **Steps**: Small: countdown + track + tint. Medium: hero row + next two departures.
  Widgets refresh on a timeline the app computes from the board (next change point), with
  a "as of" timestamp.
- **Phase**: v1

### J-I2: Lock-screen widget / watch complication

- **Steps**: Countdown and track only. Watch app: the hero and the board, nothing else.
- **Phase**: v2

---

## J. Multi-commute and switching

### J-J1: Add a second origin (different station)

- **Steps**: Commutes → Add commute. Same picker as onboarding. Commutes list becomes a
  horizontal pager on the Home screen, ordered by active window then recency.
- **System**: Backend polls the new station; commutes sharing an origin share a poll.
- **Phase**: MVP

### J-J2: Reorder, rename, delete a commute

- **Steps**: Long-press to reorder; rename is a label only (the origin/destination pair is
  the identity and can't be edited; delete and re-add instead, with copy saying so).
- **Phase**: MVP

### J-J3: Whole-board mode (no destination)

- **Steps**: A commute with no destination shows the full board, unfiltered, no favorites
  following, no onboard mode, no calendar. The hero is the top of the board.
- **Phase**: MVP

---

## K. Degraded, offline, and failure

### J-K1: Upstream source is down

- **Trigger**: Backend circuit breaker opens on the website endpoint (WAF change, schema
  drift) or RailData quota exhausted.
- **Steps**: Home screen shows the last known board with "Live data unavailable since
  8:04. Showing the timetable." The hero switches to scheduled times with an explicit
  "scheduled" label; tint goes neutral grey. No pushes are sent from stale data.
- **System**: Failover website ↔ RailData per station where both are available; status
  endpoint the app reads; fixtures-vs-live contract tests alarm on drift.
- **Done when**: An upstream outage never produces a false "on time" and never produces a
  push.
- **Phase**: MVP

### J-K2: Phone is offline

- **Steps**: Last board cached with its timestamp; countdown still ticks from scheduled
  times; a persistent "Offline" pill; pull-to-refresh retries.
- **Phase**: MVP

### J-K3: Backend is unreachable but upstream is fine

- **Steps**: Same as J-K2 from the user's view. Client backs off exponentially; widgets
  keep their last timeline.
- **Phase**: MVP

### J-K4: A station pair with no direct service at any hour

- **Steps**: Board fails open to transfer itineraries with "Change at …" on each row, and
  the onboarding warning from J-A1 was already shown.
- **Phase**: MVP

### J-K5: Board shows a train for somewhere you're not going

- **Steps**: The direct-only filter hides it. Detail screen for any row explains "Not
  shown on your board: reaches New York via a change at Newark Broad St."
- **Phase**: v1

### J-K6: Unknown status vocabulary

- **Steps**: A status string the normalizer doesn't recognise renders as the raw text in a
  neutral pill and reports itself to the backend's drift log. Never an error.
- **Phase**: MVP

### J-K7: Midnight rollover

- **Steps**: A 12:05am departure viewed at 11:50pm counts down 15 minutes, not 24 hours.
- **Phase**: MVP

---

## L. Settings, privacy, data

### J-L1: Understand what the app collects

- **Steps**: Privacy screen: "Your location never leaves your phone. We store your
  commutes, favorites and a push token. No account required." Link to policy.
- **Phase**: MVP

### J-L2: Delete everything

- **Steps**: Settings → Delete my data. Removes device registration, subscriptions,
  account if any. Confirmation, then the app returns to onboarding.
- **Phase**: MVP

### J-L3: Report bad data

- **Trigger**: Long-press a departure row → "This looks wrong".
- **Steps**: One-tap options: wrong track, wrong status, train missing, train shouldn't be
  here. Sends the row's canonical JSON + the upstream raw payload reference (server-side
  id, not the payload itself) so the drift can be investigated.
- **Phase**: v1

### J-L4: Station track-posting stats

- **Trigger**: Station detail, or the "?" next to "Track overdue".
- **Steps**: "New York Penn posts tracks a median of 9.0 min before departure (middle half:
  8.9–9.1). The signaling system usually knows ~20 min out and matched the board 229 of
  231 times." Updated from the live dataset.
- **Phase**: v1

---

## M. Operator journeys (internal, but they gate the product)

### J-M1: Detect upstream drift before users do

- **Steps**: Contract tests replay recorded fixtures against the live endpoints hourly;
  a schema or vocabulary change pages on-call and flips the station to the other source
  if available.
- **Phase**: MVP

### J-M2: Watch RailData quota

- **Steps**: Dashboard of calls/day per account against 40,000, sign-ins against 10,
  schedule downloads against 5; alert at 70%.
- **Phase**: MVP

### J-M3: Onboard a new station's circuit decoder

- **Steps**: Collect (circuit, board track) pairs for a station over N days; propose a
  decoder; validate agreement ≥ 95% over 200 postings before enabling "likely" there.
- **Phase**: v2

---

## Coverage matrix

| Area | MVP | v1 | v2 |
|---|---|---|---|
| Onboarding | A1 A2 A3 | A4 | |
| Before leaving | B1 B2 B3 | B4 | |
| At the station | C1 C2 C3 | C4 | |
| Aboard | D1 (in-app) | D1 (Live Activity) | D2 |
| Disruptions | E1 E2 E3 E5 E6 | E4 | |
| Alerts | F1 F3 | | F2 |
| Favorites | G2 G3 G5 | G1 G4 | G6 |
| Notifications | H4 H5 | H1 H2 H3 | |
| Widgets | | I1 | I2 |
| Multi-commute | J1 J2 J3 | | |
| Degraded | K1 K2 K3 K4 K6 K7 | K5 | |
| Settings/privacy | L1 L2 | L3 L4 | |
| Operator | M1 M2 | | M3 |
