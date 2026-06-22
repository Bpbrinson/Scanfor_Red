#!/usr/bin/env bash
#
# run.sh — launch Scanfor Red in Docker and open it in your browser.
#
# One-time: replace <dockerhub-user> below with your Docker Hub username
#           (or pass an image as the first argument / set SCANFOR_IMAGE).
#
# Usage:
#   bash run.sh                          # uses IMAGE below
#   bash run.sh youruser/scanfor-red     # or pass an image explicitly
#   SCANFOR_PORT=8080 bash run.sh        # use a different port
#
set -euo pipefail

IMAGE="${1:-${SCANFOR_IMAGE:-<dockerhub-user>/scanfor-red:latest}}"
PORT="${SCANFOR_PORT:-5000}"
URL="http://localhost:${PORT}"

echo "→ Scanfor Red"
echo "  image: ${IMAGE}"
echo "  url:   ${URL}   (press Ctrl+C to stop)"

# Open the browser a few seconds after the server starts.
# (macOS uses 'open'; Linux uses 'xdg-open'.)
( sleep 3
  if   command -v open     >/dev/null 2>&1; then open "${URL}"
  elif command -v xdg-open >/dev/null 2>&1; then xdg-open "${URL}"
  fi ) &

# Named volumes persist your screenshots + Excel reports across restarts.
exec docker run --rm -p "${PORT}:5000" \
  -v scanfor_screens:/app/Dashboard_Screenshot \
  -v scanfor_outputs:/app/outputs/json_to_excel \
  "${IMAGE}"
