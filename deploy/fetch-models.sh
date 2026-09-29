#!/usr/bin/env bash
# Download the production ONNX models into models/ from the GitHub release.
# Usage: ./deploy/fetch-models.sh [tag] [--faces]   (default tag: models-v1)
#   --faces  also fetch OpenCV's face models (YuNet, MIT; SFace, Apache-2.0). Only
#            needed where face recognition is switched on, with its legal basis recorded.
set -euo pipefail
FACES=0
ARGS=()
for a in "$@"; do [ "$a" = "--faces" ] && FACES=1 || ARGS+=("$a"); done
TAG="${ARGS[0]:-models-v1}"
BASE="https://github.com/simba84-cloud/ivaas/releases/download/${TAG}"
DEST="$(cd "$(dirname "$0")/.." && pwd)/models"
mkdir -p "$DEST"
for f in stacks-v2.onnx stacks-v2.json layers-v3.onnx layers-v3.json; do
  if [ -s "$DEST/$f" ]; then echo "have $f"; continue; fi
  echo "fetching $f"; curl -sSL --fail -o "$DEST/$f" "$BASE/$f"
done
if [ "$FACES" = 1 ]; then
  ZOO="https://github.com/opencv/opencv_zoo/raw/main/models"
  for f in face_detection_yunet/face_detection_yunet_2023mar.onnx \
           face_recognition_sface/face_recognition_sface_2021dec.onnx; do
    name="$(basename "$f")"
    if [ -s "$DEST/$name" ]; then echo "have $name"; continue; fi
    echo "fetching $name"; curl -sSL --fail -o "$DEST/$name" "$ZOO/$f"
  done
fi
# The person detector is exported from its public checkpoint rather than hosted here:
#   cd ml && uv run --extra train python -m ivaas_ml.export \
#       PekingU/rtdetr_r18vd_coco_o365 ../models/people-coco.onnx
ls -la "$DEST"
