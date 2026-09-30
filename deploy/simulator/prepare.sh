#!/usr/bin/env bash
# Prepare the simulator's footage once: recorded video re-encoded to the camera spec.
#
#   ./deploy/simulator/prepare.sh <clip.mp4> [WIDTHxHEIGHT] [FPS]
#   ./deploy/simulator/prepare.sh services/pipeline/tests/golden/door-load.mp4 3840x2160 15
#
# The publisher then copies this file into every stream without re-encoding, so the
# simulator itself costs almost nothing and the load test measures the edge node, not
# the machine faking the cameras. Encoding 17 live 4K streams would saturate any CPU.
#
# Defaults match the POC's cameras: 4K (3840x2160) at 15 fps, H.264 with a keyframe
# every second (a new reader can start decoding within a second).
set -euo pipefail
CLIP="${1:?usage: prepare.sh <clip.mp4> [WIDTHxHEIGHT] [FPS]}"
SIZE="${2:-3840x2160}"
FPS="${3:-15}"
OUT="$(dirname "$0")/media/sim.mp4"
mkdir -p "$(dirname "$OUT")"
exec ffmpeg -hide_banner -loglevel warning -y -i "$CLIP" \
  -vf "scale=${SIZE/x/:}:flags=bicubic,fps=${FPS}" \
  -c:v libx264 -preset medium -profile:v high -pix_fmt yuv420p \
  -g "$FPS" -keyint_min "$FPS" -sc_threshold 0 -an \
  -movflags +faststart "$OUT"
