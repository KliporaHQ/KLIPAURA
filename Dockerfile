# KLIPAURA — single image for all services.
# Override CMD per Railway service:
#   HITL API:   uvicorn hitl_server:app --app-dir klip-dispatch --host 0.0.0.0 --port $PORT
#   Worker:     python /app/klip-avatar/worker.py
#   Selector:   python /app/klip-selector/scheduler.py
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install root deps first (separate layer for caching)
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# Copy all source
COPY . /app/

# Install sub-module requirements
RUN pip install --no-cache-dir -r /app/klip-avatar/core_v1/requirements.txt
RUN pip install --no-cache-dir -r /app/klip-funnel/requirements.txt
RUN test -f /app/klip-selector/requirements.txt && pip install --no-cache-dir -r /app/klip-selector/requirements.txt || true

ENV PYTHONPATH="/app:/app/klip-avatar/core_v1:/app/klip-dispatch:/app/klip-funnel:/app/klip-scanner:/app/klip-selector"
ENV PYTHONUNBUFFERED=1
ENV PORT=8080
EXPOSE 8080

CMD ["sh", "-c", "exec uvicorn hitl_server:app --app-dir klip-dispatch --host 0.0.0.0 --port ${PORT:-8080}"]
