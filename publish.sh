#!/usr/bin/env bash
#
# publish.sh — build the image, push it to Docker Hub, and (re)start it locally.
#
# One-time: replace <dockerhub-user> below with your Docker Hub username
#           (or pass the image as arg 1, or set SCANFOR_IMAGE).
#
# Usage:
#   bash publish.sh                        # build + push + start (image below)
#   bash publish.sh youruser/scanfor-red   # explicit image name
#   SCANFOR_PLATFORMS=linux/arm64 bash publish.sh   # single-arch (faster)
#
set -euo pipefail
cd "$(dirname "$0")"        # always run from the project folder

IMAGE="${1:-${SCANFOR_IMAGE:-<dockerhub-user>/scanfor-red:latest}}"
PORT="${SCANFOR_PORT:-5000}"
NAME="${SCANFOR_NAME:-scanfor-red}"
PLATFORMS="${SCANFOR_PLATFORMS:-linux/amd64,linux/arm64}"
URL="http://localhost:${PORT}"

echo "==> 1/3  Building & pushing ${IMAGE}"
echo "         platforms: ${PLATFORMS}"
# A dedicated buildx builder (created once) supports multi-arch builds.
docker buildx inspect scanfor-builder >/dev/null 2>&1 \
  || docker buildx create --name scanfor-builder >/dev/null
docker buildx build --builder scanfor-builder \
  --platform "${PLATFORMS}" -t "${IMAGE}" --push .

echo "==> 2/3  Pulling the freshly pushed image"
docker pull "${IMAGE}"

echo "==> 3/3  (Re)starting container '${NAME}'"
docker rm -f "${NAME}" >/dev/null 2>&1 || true
docker run -d --name "${NAME}" --restart unless-stopped \
  -p "${PORT}:5000" \
  -v "$(pwd)/Dashboard_Screenshot:/app/Dashboard_Screenshot" \
  -v "$(pwd)/outputs/json_to_excel:/app/outputs/json_to_excel" \
  -v "$(pwd)/ticket_registry.json:/app/ticket_registry.json" \
  "${IMAGE}"

# Open the browser once the server is up.
( sleep 3
  if   command -v open     >/dev/null 2>&1; then open "${URL}"
  elif command -v xdg-open >/dev/null 2>&1; then xdg-open "${URL}"
  fi ) &

cat <<EOF

✅ Published ${IMAGE} and started '${NAME}'.
   open:    ${URL}
   logs:    docker logs -f ${NAME}
   stop:    docker stop ${NAME}
   start:   docker start ${NAME}
   remove:  docker rm -f ${NAME}
EOF
