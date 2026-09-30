#!/usr/bin/env bash
# The simulator: one looping stream per line of /sim/streams.txt, pushed to MediaMTX.
# Runs in the `simulator` container (docker compose --profile sim up simulator).
#
# Each stream is a copy of /sim/media/sim.mp4 (made by prepare.sh), not a re-encode.
# A stream whose name is in /tmp/unplugged is held down: that is how unplug.sh
# pulls a camera's cable (T2.5) and how replugging puts it back.
set -euo pipefail
STREAMS="${SIM_STREAMS:-/sim/streams.txt}"
MEDIA="${SIM_MEDIA:-/sim/media/sim.mp4}"
TARGET="${SIM_TARGET:-rtsp://mediamtx:8554}"
[ -s "$STREAMS" ] || { echo "no streams in $STREAMS: run python -m ivaas_pipeline.sim setup first"; exit 2; }
[ -s "$MEDIA" ] || { echo "no footage at $MEDIA: run deploy/simulator/prepare.sh first"; exit 2; }
mkdir -p /tmp/unplugged

stream() {
  local path="$1" name="${1//\//_}"
  while true; do
    if [ -e "/tmp/unplugged/$name" ]; then sleep 1; continue; fi
    # -re: real time, as a camera would send it; -stream_loop -1: forever
    ffmpeg -hide_banner -loglevel error -re -stream_loop -1 -i "$MEDIA" -c copy \
      -f rtsp -rtsp_transport tcp "$TARGET/$path" || true
    sleep 1  # the media server restarted, or the stream was unplugged: try again
  done
}

count=0
while read -r path; do
  [ -n "$path" ] || continue
  stream "$path" &
  count=$((count + 1))
done < "$STREAMS"
echo "simulating $count cameras from $MEDIA into $TARGET"
wait
