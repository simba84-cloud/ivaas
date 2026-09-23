#!/usr/bin/env bash
# Publish a laptop webcam (or any local capture device) to IVaaS as a push camera.
# Register the camera first in the portal (Cameras -> Add camera -> "pushes its stream"),
# then run this with the stream path it shows, e.g.  ./deploy/webcam.sh poc-loading-bay/laptop-webcam
# Needs ffmpeg. macOS: device "0" is the built-in camera (ffmpeg -f avfoundation -list_devices true -i "").
set -euo pipefail
STREAM_PATH="${1:?usage: webcam.sh <stream-path> [device] [host]}"
DEVICE="${2:-0}"
HOST="${3:-localhost}"
case "$(uname -s)" in
  Darwin) INPUT=(-f avfoundation -framerate 30 -pixel_format nv12 -video_size 1280x720 -i "$DEVICE") ;;
  Linux)  INPUT=(-f v4l2 -framerate 30 -video_size 1280x720 -i "/dev/video${DEVICE}") ;;
  *) echo "unsupported OS"; exit 1 ;;
esac
exec ffmpeg -hide_banner -loglevel warning "${INPUT[@]}" -vf fps=15 \
  -c:v libx264 -preset ultrafast -tune zerolatency -pix_fmt yuv420p -g 30 \
  -f rtsp -rtsp_transport tcp "rtsp://${HOST}:8554/${STREAM_PATH}"
