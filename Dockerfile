# KLIPAURA — single image containing all modules.
# Override CMD per Railway service:
#   HITL API:       uvicorn hitl_server:app --app-dir klip-dispatch --host 0.0.0.0 --port ${PORT:-8080}
#   Worker:         python /app/klip-avatar/worker.py
#   Selector:       python /app/klip-selector/scheduler.py
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ── Source trees ────────────────────────────────────────────────────────────
COPY infrastructure /app/infrastructure
COPY klip-core      /app/klip-core
COPY klip-avatar    /app/klip-avatar
COPY klip-dispatch  /app/klip-dispatch
COPY klip-funnel    /app/klip-funnel
COPY klip-scanner   /app/klip-scanner
COPY klip-selector  /app/klip-selector
COPY config         /app/config
COPY data           /app/data

# ── Root-level shared files ────────────────────────────────────────────────
COPY requirements.txt /app/requirements.txt

# ── Python packages ──────────────────────────────────────────────────────────
RUN pip install --no-cache-dir -e /app/klip-core
RUN pip install --no-cache-dir -r /app/requirements.txt
RUN pip install --no-cache-dir -r /app/klip-avatar/core_v1/requirements.txt
RUN pip install --no-cache-dir -r /app/klip-funnel/requirements.txt
# klip-selector has its own requirements (APScheduler, paapi5, etc.)
RUN test -f /app/klip-selector/requirements.txt && pip install --no-cache-dir -r /app/klip-selector/requirements.txt || true

# ── Python path — every module importable from /app ──────────────────────────
ENV PYTHONPATH="/app:/app/klip-avatar/core_v1:/app/klip-core:/app/klip-dispatch:/app/klip-funnel:/app/klip-scanner:/app/klip-selector"

ENV PYTHONUNBUFFERED=1
ENV PORT=8080
EXPOSE 8080

# Default: HITL dispatch API. Override CMD in Railway service settings.
CMD ["sh", "-c", "exec uvicorn hitl_server:app --app-dir klip-dispatch --host 0.0.0.0 --port ${PORT:-8080}"]
