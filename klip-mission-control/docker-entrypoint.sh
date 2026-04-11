#!/bin/sh
set -e
cd /app

# Railway healthcheck hits $PORT (Next). It must listen immediately — do not block on FastAPI.
# Uvicorn only accepts HTTP after lifespan startup completes; that can exceed any fixed wait and
# would leave PORT closed → "service unavailable" for the full healthcheck window.
#
# Next standalone uses HOSTNAME as the bind address. Docker/Kubernetes/Railway often set HOSTNAME
# to the container/pod id — binding there rejects public edge traffic; force all interfaces.
export HOSTNAME=0.0.0.0
export PORT="${PORT:-3000}"

uvicorn main:app --host 0.0.0.0 --port 8000 &
echo "Started uvicorn on :8000 (pid $!), Next on :${PORT} (host ${HOSTNAME})" >&2

exec node server.js
