#!/usr/bin/env bash
# T2.4 drill: take the cloud away from a running edge node, bring it back, and check
# that every event counted meanwhile arrives, in order, exactly once.
#
#   ./deploy/simulator/outage.sh [MINUTES]      (default 2; the proposal's test is 120)
#
# The API is *paused*, not stopped: its connections hang rather than being refused,
# which is what a black-holed WAN looks like to the node. The portal is down for the
# duration. The node keeps decoding and counting throughout.
#
# Two things are checked:
# 1. the pipeline's own spool: whatever the footage counted during the outage is
#    delivered afterwards, with nothing dropped;
# 2. synthetic crossings sent as the node across the outage (`ivaas_pipeline.sim
#    inject`): the crates the API ends up counting equal the crates sent, exactly.
#    This is the part that always has events, whatever the footage does.
# A drill in which nothing was counted cannot pass: it is reported inconclusive.
set -euo pipefail
cd "$(dirname "$0")/../.."
MINUTES="${1:-2}"
SECONDS_OUT="$(python3 -c "print(int(float('$MINUTES') * 60))")"
counters() {
  docker compose --profile edge exec -T pipeline python -m ivaas_pipeline.loadreport --delivery
}
field() { python3 -c "import json,sys;print(int(json.loads(sys.argv[1])['$2']))" "$1"; }
OUT="$(mktemp)"

before="$(counters)"
echo "before:  $before"
# crossings every 3 s from just before the outage until just after it
COUNT=$(( SECONDS_OUT / 3 + 5 ))
docker compose --profile edge exec -T pipeline python -m ivaas_pipeline.sim inject \
  --count "$COUNT" --every 3 >"$OUT" 2>/dev/null &
INJECTOR=$!
sleep 5
docker compose pause api >/dev/null
trap 'docker compose unpause api >/dev/null 2>&1 || true' EXIT
echo "API paused for ${MINUTES} min (the node cannot reach the cloud); $COUNT synthetic crossings in flight"
sleep "$SECONDS_OUT"
during="$(counters)"
echo "outage:  $during"
docker compose unpause api >/dev/null
trap - EXIT
echo "API back; waiting for the spools to drain"
start=$(date +%s)
while true; do
  now="$(counters)"
  [ "$(field "$now" pending)" -eq 0 ] && break
  if [ $(( $(date +%s) - start )) -gt 900 ]; then echo "FAIL: spool still holds $(field "$now" pending) after 15 min"; exit 1; fi
  sleep 5
done
wait "$INJECTOR" || true
drained=$(( $(date +%s) - start ))
queued=$(( $(field "$now" queued) - $(field "$before" queued) ))
delivered=$(( $(field "$now" delivered) - $(field "$before" delivered) ))
dropped=$(( $(field "$now" dropped) - $(field "$before" dropped) ))
injected="$(tail -1 "$OUT")"; rm -f "$OUT"
echo "pipeline: counted $queued event(s) during the drill, delivered $delivered, dropped $dropped; drained in ${drained}s"
echo "injected: $injected"
python3 - "$injected" "$queued" "$delivered" "$dropped" <<'PY'
import json, sys
inj = json.loads(sys.argv[1]) if sys.argv[1].startswith("{") else None
queued, delivered, dropped = (int(x) for x in sys.argv[2:5])
problems = []
if dropped or delivered != queued:
    problems.append(f"pipeline delivered {delivered} of {queued}, dropped {dropped}")
if inj is None:
    problems.append("the synthetic sender did not report")
elif inj["still_pending"] or inj["crates_counted"] != inj["crates_sent"]:
    problems.append(f"API counted {inj['crates_counted']} of {inj['crates_sent']} synthetic crates")
if problems:
    print("FAIL: " + "; ".join(problems)); sys.exit(1)
if not queued and not (inj and inj["crossings_sent"]):
    print("INCONCLUSIVE: nothing was counted during the outage, so nothing was tested"); sys.exit(2)
print(f"PASS: all {inj['crates_sent']} synthetic crates counted exactly once after the outage; "
      f"pipeline {delivered}/{queued} delivered, none dropped")
PY
