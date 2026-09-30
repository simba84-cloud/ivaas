#!/usr/bin/env bash
# T2.5 drill: pull one simulated camera's cable and time how long until the platform
# says so, then plug it back in.
#
#   IVAAS_ADMIN_USER=... IVAAS_ADMIN_PASSWORD=... ./deploy/simulator/unplug.sh <bay id> <stream path>
#
# Pass: the camera shows offline in the API within 60 s (the proposal's target).
# The edge node's own report of the camera is shown too, when a node is enrolled.
set -euo pipefail
cd "$(dirname "$0")/../.."
BAY="${1:?usage: unplug.sh <bay id> <stream path>}"
STREAM="${2:?usage: unplug.sh <bay id> <stream path>}"
API="${IVAAS_API:-http://localhost:${IVAAS_PORTAL_PORT:-8080}/api/v1}"
TOKEN=$(curl -sf -X POST "$API/auth/login" -H 'content-type: application/json' \
  -d "{\"username\":\"${IVAAS_ADMIN_USER:?}\",\"password\":\"${IVAAS_ADMIN_PASSWORD:?}\"}" |
  python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')
status() {
  curl -sf "$API/bays/$BAY/cameras" -H "Authorization: Bearer $TOKEN" |
    python3 -c "import sys,json;print(next((c['status'] for c in json.load(sys.stdin) if c['stream_path']=='$STREAM'),'unknown'))"
}
node_view() {
  curl -sf "$API/edge/nodes" -H "Authorization: Bearer $TOKEN" | python3 -c "
import sys, json
for n in json.load(sys.stdin):
    for c in n['cameras']:
        if c['name'] and '$STREAM'.endswith(c['name'].lower().replace(' ', '-')):
            print(n['name'], 'reports', 'connected' if c['connected'] else 'not connected')
" || true
}
wait_for() {
  local want="$1" start=$(date +%s)
  while [ "$(status)" != "$want" ]; do
    [ $(( $(date +%s) - start )) -gt 180 ] && { echo "FAIL: still not $want after 180 s"; exit 1; }
    sleep 2
  done
  echo $(( $(date +%s) - start ))
}
name="${STREAM//\//_}"
echo "before: $(status)"
docker compose --profile sim exec -T simulator sh -c "touch /tmp/unplugged/$name; pkill -f 'rtsp://.*/$STREAM\$' || true"
echo "unplugged $STREAM"
took=$(wait_for offline)
echo "API reports it offline after ${took}s"
sleep 35; node_view  # the node's next heartbeat
docker compose --profile sim exec -T simulator rm -f "/tmp/unplugged/$name"
back=$(wait_for online)
echo "plugged back in; online again after ${back}s"
[ "$took" -le 60 ] && echo "PASS: offline within 60 s ($took s)" || { echo "FAIL: took $took s"; exit 1; }
