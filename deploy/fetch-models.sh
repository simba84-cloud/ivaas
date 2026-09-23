#!/usr/bin/env bash
# Download the production ONNX models into models/ from the GitHub release.
# Usage: ./deploy/fetch-models.sh [tag]   (default: models-v1)
set -euo pipefail
TAG="${1:-models-v1}"
BASE="https://github.com/simba84-cloud/ivaas/releases/download/${TAG}"
DEST="$(cd "$(dirname "$0")/.." && pwd)/models"
mkdir -p "$DEST"
for f in stacks-v2.onnx stacks-v2.json layers-v3.onnx layers-v3.json; do
  if [ -s "$DEST/$f" ]; then echo "have $f"; continue; fi
  echo "fetching $f"; curl -sSL --fail -o "$DEST/$f" "$BASE/$f"
done
ls -la "$DEST"
