#!/usr/bin/env bash
# T2.4 drill: take the cloud away from a running edge node, bring it back, and check
# that every event it counted meanwhile arrives, in order, exactly once.
#
#   ./deploy/simulator/outage.sh [MINUTES]      (default 2; the proposal's test is 120)
#
# The API is *paused*, not stopped: its connections hang rather than being refused,
# which is what a WAN black-hole looks like to the node. Pausing also stops the
# portal for the duration. The node keeps decoding and counting throughout.
#
# Pass: nothing dropped, the spool drains to zero, and delivered == queued.
set -euo pipefail
cd "$(dirname "$0")/../.."
MINUTES="${1:-2}"
counters() {
  docker compose --profile edge exec -T pipeline python -m ivaas_pipeline.loadreport --delivery
}
field() { python3 -c "import json,sys;print(int(json.loads(sys.argv[1])['$2']))" "$1"; }

before="$(counters)"
echo "before:  $before"
docker compose pause api >/dev/null
echo "API paused for ${MINUTES} min (the node cannot reach the cloud)"
trap 'docker compose unpause api >/dev/null 2>&1 || true' EXIT
sleep "$(python3 -c "print(int(float('$MINUTES') * 60))")"
during="$(counters)"
echo "outage:  $during"
docker compose unpause api >/dev/null
trap - EXIT
echo "API back; waiting for the spool to drain"
start=$(date +%s)
while true; do
  now="$(counters)"
  [ "$(field "$now" pending)" -eq 0 ] && break
  if [ $(( $(date +%s) - start )) -gt 900 ]; then echo "FAIL: spool still holds $(field "$now" pending) after 15 min"; exit 1; fi
  sleep 5
done
drained=$(( $(date +%s) - start ))
queued=$(( $(field "$now" queued) - $(field "$before" queued) ))
delivered=$(( $(field "$now" delivered) - $(field "$before" delivered) ))
dropped=$(( $(field "$now" dropped) - $(field "$before" dropped) ))
backlog=$(field "$during" pending)
echo "after:   $now"
echo "events counted during the drill: $queued; backlog at reconnect: $backlog; drained in ${drained}s"
if [ "$dropped" -eq 0 ] && [ "$delivered" -eq "$queued" ]; then
  echo "PASS: $delivered of $queued delivered, none dropped"
else
  echo "FAIL: $delivered of $queued delivered, $dropped dropped"; exit 1
fi
