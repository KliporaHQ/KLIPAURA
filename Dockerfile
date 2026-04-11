# KLIPAURA — default image runs the API (override CMD for worker/scheduler).
FROM python:3.12-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app:/app/klip-scanner:/app/klip-funnel

# Default when PORT is unset (local). Railway injects PORT for public binding + healthchecks.
ENV PORT=8080
EXPOSE 8080

# exec: uvicorn becomes PID 1 (signals). Shell expands PORT for Railway.
CMD ["sh", "-c", "exec uvicorn hitl_server:app --app-dir klip-dispatch --host 0.0.0.0 --port ${PORT:-8080}"]
