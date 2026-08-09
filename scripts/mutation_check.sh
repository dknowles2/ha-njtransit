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

run() {
  local desc=$1 file=$2 from=$3 to=$4
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
  uv run pytest -q -x >/dev/null 2>&1
  local code=$?
  cp /tmp/njt_mutation_backup "$file"

  if [ $code -ne 0 ]; then
    echo "  caught   $desc"
  else
    echo "  SURVIVED $desc"
    failures=$((failures + 1))
  fi
}

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

run "status_text drops the delay" \
  custom_components/njtransit/api/models.py \
  'return f"{self.delay_minutes} min late"' 'return "late"'

echo
if [ $failures -eq 0 ]; then
  echo "all mutations caught"
else
  echo "$failures mutation(s) survived or went stale"
fi
exit $failures
