#!/bin/zsh
#
# Break one real behaviour at a time and check the suite notices.
#
# Coverage says a line ran. It does not say anything asserted what the line
# did, and every gap this script has found so far was a line with 100%
# coverage sitting under a test that could not fail. Run it after changing
# behaviour, not on a schedule.
#
#   ./scripts/mutation_check.sh
#
# A SURVIVED line means the suite passes with that behaviour broken. A SKIP
# means the pattern no longer exists -- the entry needs rewriting or deleting,
# and is reported rather than silently passing.
#
# Source files are restored after each run, including on failure. Check `git
# status` afterwards regardless: an interrupted run leaves a mutation in place.

set -u
cd "$(dirname "$0")/.." || exit 1

failures=0

# Break one thing, run one suite, put the file back.
#
# `suite` is the command that should now fail. The card is TypeScript with its
# own runner, so a mutation there has to be judged by vitest -- running pytest
# against a broken card would pass and read as a gap in the Python tests.
mutate() {
  local desc=$1 file=$2 from=$3 to=$4 suite=$5
  cp "$file" /tmp/njt_mutation_backup
  .venv/bin/python - "$file" "$from" "$to" <<'PY'
import sys, pathlib
path = pathlib.Path(sys.argv[1])
source = path.read_text()
if sys.argv[2] not in source:
    sys.exit(3)
path.write_text(source.replace(sys.argv[2], sys.argv[3], 1))
PY
  if [ $? -ne 0 ]; then
    echo "  SKIP     $desc (pattern no longer present)"
    cp /tmp/njt_mutation_backup "$file"
    failures=$((failures + 1))
    return
  fi

  # Exit code, not output text: `-x` changes which line the summary lands on,
  # and grepping for "failed" misses the uppercase "FAILED" that `-x` prints.
  eval "$suite" >/dev/null 2>&1
  local code=$?
  cp /tmp/njt_mutation_backup "$file"

  if [ $code -ne 0 ]; then
    echo "  caught   $desc"
  else
    echo "  SURVIVED $desc"
    failures=$((failures + 1))
  fi
}

run() {
  mutate "$1" "$2" "$3" "$4" "uv run pytest -q -x"
}

# The card is not rebuilt here. vitest reads `frontend/src` directly, so the
# committed bundle is irrelevant to whether a mutation is caught.
run_card() {
  mutate "$1" "$2" "$3" "$4" "npm --prefix frontend test"
}

# Restores the exact ordering shipped in 2026.8.11, which broke the second
# commute of anyone running two.
run "the card is claimed after an await instead of before" \
  custom_components/njtransit/frontend.py \
  "    hass.data[_REGISTERED] = True

    # After the claim, deliberately. A missing bundle leaves the flag set, so
    # the entries behind this one skip rather than each re-checking a file
    # that cannot appear while Home Assistant is running.
    path = bundle_path()
    if not await hass.async_add_executor_job(path.is_file):
        return" \
  "    path = bundle_path()
    if not await hass.async_add_executor_job(path.is_file):
        return

    hass.data[_REGISTERED] = True"

run "delay threshold >= becomes >" \
  custom_components/njtransit/binary_sensor.py \
  "delay >= self._threshold" "delay > self._threshold"

run "track_overdue fires for cancelled trains" \
  custom_components/njtransit/event.py \
  "and not cancelled" "and True"

run "track_overdue window 6min -> 60min" \
  custom_components/njtransit/event.py \
  "TRACK_OVERDUE_LEAD = timedelta(minutes=6)" \
  "TRACK_OVERDUE_LEAD = timedelta(minutes=60)"

run "knock-on window 30min -> 300min" \
  custom_components/njtransit/event.py \
  "KNOCK_ON_LEAD = timedelta(minutes=30)" \
  "KNOCK_ON_LEAD = timedelta(minutes=300)"

run "direct-only filter disabled" \
  custom_components/njtransit/coordinator.py \
  "if not trip.has_transfer" "if True"

run "assigned_at measured backwards" \
  custom_components/njtransit/track_history.py \
  "_seconds_before(departure.scheduled, now)" \
  "_seconds_before(now, departure.scheduled)"

run "favourite matching becomes case-sensitive" \
  custom_components/njtransit/__init__.py \
  "departure.train_id.upper() in favorites" \
  "departure.train_id in favorites"

run "a blank delay overwrites a recorded one" \
  custom_components/njtransit/track_history.py \
  "            if delay is not None:" "            if True:"

run "worst_delay follows the last value instead of the peak" \
  custom_components/njtransit/track_history.py \
  "if worst is None or delay > worst:" "if True:"

run "a cancellation can be overwritten" \
  custom_components/njtransit/track_history.py \
  "if current == TrainStatus.CANCELLED.value:" "if False:"

run "alert train ids keep the prose's casing" \
  custom_components/njtransit/api/parsing.py \
  "found.upper() for found in _TRAIN_RE.findall(head)" \
  "found for found in _TRAIN_RE.findall(head)"

run "alert matching stops normalizing the board id" \
  custom_components/njtransit/binary_sensor.py \
  "if departure.train_id.upper() in alert.train_ids:" \
  "if departure.train_id in alert.train_ids:"

# The blueprint is not Python, but it is where the bugs a user actually feels
# have come from, and `run` only cares about text. Each of these is a real
# defect that reached a phone.

run "blueprint: as_timestamp loses its default" \
  blueprints/automation/njtransit/favorite_live_activity.yaml \
  "as_timestamp(scheduled, 0)) | int }}" \
  "as_timestamp(scheduled)) | int }}"

run "blueprint: fallback stops skipping cancelled trains" \
  blueprints/automation/njtransit/favorite_live_activity.yaml \
  "and state_attr(row, 'status') != 'cancelled' -%}" \
  "-%}"

run "blueprint: the arrival countdown ignores the window" \
  blueprints/automation/njtransit/favorite_live_activity.yaml \
  "       and not is_state(boarded, 'on')
       and in_window }}" \
  "       and not is_state(boarded, 'on') }}"

run "blueprint: the commute window stops gating" \
  blueprints/automation/njtransit/favorite_live_activity.yaml \
  "and not is_state(boarded, 'on') and nearby | bool(false)
       and in_window }}" \
  "and not is_state(boarded, 'on') and nearby | bool(false) }}"

run "blueprint: an unset helper breaks config validation" \
  blueprints/automation/njtransit/favorite_live_activity.yaml \
  '              entity_id: "{{ boarded }}"
          - action: "{{ notify_action }}"' \
  '              entity_id: !input boarded_helper
          - action: "{{ notify_action }}"'

run "blueprint: the activity interrupts on every update again" \
  blueprints/automation/njtransit/favorite_live_activity.yaml \
  "                push:
                  interruption-level: passive" \
  "                push:
                  interruption-level: active"

run "blueprint: the board's own countdown pushes again" \
  blueprints/automation/njtransit/favorite_live_activity.yaml \
  "  changed: >-
    {{ riding" \
  "  changed: >-
    {{ true or riding"

run "disruption: bands compared for equality, not increase" \
  blueprints/automation/njtransit/commute_disruption.yaml \
  "{%- if (m[0] | int // 5) > was -%}" \
  "{%- if (m[0] | int // 5) != was -%}"

run "disruption: lateness diffed on raw text again" \
  blueprints/automation/njtransit/commute_disruption.yaml \
  "{%- set ns.bands = ns.bands + [m[0] | int // 5] -%}" \
  "{%- set ns.bands = ns.bands + [m[0] | int] -%}"

run "disruption: an unset window blocks everything" \
  blueprints/automation/njtransit/commute_disruption.yaml \
  "{{ windows | count == 0" \
  "{{ false"

# The analysis tool decides whether the whole track-prediction feature ships.
# Its failure mode is a number that looks like a result.

run "analysis: the held-out day leaks into history" \
  scripts/analyze_tracks.py \
  "history = [o for o in observations if o.day != held_out]" \
  "history = list(observations)"

run "analysis: the target stays in its own day's context" \
  scripts/analyze_tracks.py \
  "context = [o for o in day if o.train_id != target.train_id]" \
  "context = list(day)"

run "analysis: an unanswered target counts as answered" \
  scripts/analyze_tracks.py \
  "                if not ranked:
                    continue" \
  "                if False:
                    continue"

run "analysis: the station filter runs before the join" \
  scripts/analyze_tracks.py \
  "    direct = sum(1 for o in observations if o.outcome_known)
    observations = join_outcomes(observations)" \
  "    direct = sum(1 for o in observations if o.outcome_known)
    if args.station:
        observations = [o for o in observations if o.station == args.station]
    observations = join_outcomes(observations)"

run "analysis: an absence is reported as a rate again" \
  scripts/analyze_tracks.py \
  "if not any(o.outcome_known for o in njt):" \
  "if not any(o.final_status is not None for o in njt):"

run "analysis: the cut label stops following the constant" \
  scripts/analyze_tracks.py \
  'f"assigned late (< {cutoff} min)",' \
  '"assigned late (< 8 min)",'

# The example dashboard. Not shipped code, but it is the surface a commuter
# actually reads, and every bug it has had was visible only on a screen.

run "dashboard: the hero falls back to a cancelled train" \
  dashboards/trains.yaml \
  "        {%- if states(row) not in nothing
               and state_attr(row, 'status') != 'cancelled' -%}" \
  "        {%- if states(row) not in nothing -%}"

run "dashboard: overdue claimed where no track is posted" \
  dashboards/trains.yaml \
  "        {%- elif not posting %}*\`Track not posted\`*" \
  "        {%- elif false %}*\`Track not posted\`*"

run "dashboard: the crowding hint fires when nothing differs" \
  dashboards/trains.yaml \
  "{%- if ns.best and ns.high > ns.low %}" \
  "{%- if ns.best %}"

run "status_text drops the delay" \
  custom_components/njtransit/api/models.py \
  'return f"{self.delay_minutes} min late"' 'return "late"'

# The Lovelace card. Same failures as the dashboard it replaces, plus the two
# it can have that a markdown card could not: a countdown that stops counting,
# and a timer that outlives the card.

run_card "card: the hero falls back to a cancelled train" \
  frontend/src/commute.ts \
  'board.find((departure) => departure.status !== "cancelled")' \
  'board.find(() => true)'

run_card "card: overdue claimed where no track is posted" \
  frontend/src/commute.ts \
  'return board.some((departure) => departure.track !== null);' \
  'return true;'

run_card "card: the crowding hint fires when nothing differs" \
  frontend/src/pills.ts \
  'return best !== null && high > low ? best : null;' \
  'return best;'

run_card "card: the tenth departure sorts as text" \
  frontend/src/commute.ts \
  '(a, b) => trailingIndex(a) - trailingIndex(b),' \
  '(a, b) => a.localeCompare(b),'

run_card "card: the countdown stops recomputing" \
  frontend/src/departures-card.ts \
  'this._timer = setInterval(() => {
      this._now = new Date();
    }, TICK_MS);' \
  'this._timer = undefined;'

run_card "card: an unposted track tints the whole card" \
  frontend/src/pills.ts \
  'let worst: Tone = "accent";' \
  'let worst: Tone = "muted";'

run_card "card: the tint stops following the pills" \
  frontend/src/pills.ts \
  'if (pill && SEVERITY[pill.tone] > SEVERITY[worst]) {' \
  'if (false) {'

run_card "card: the tick timer outlives the card" \
  frontend/src/departures-card.ts \
  'if (this._timer) {
      clearInterval(this._timer);' \
  'if (false) {
      clearInterval(this._timer);'

echo
if [ $failures -eq 0 ]; then
  echo "all mutations caught"
else
  echo "$failures mutation(s) survived or went stale"
fi
exit $failures
