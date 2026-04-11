FROM python:3.11-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1
COPY klip-core /app/klip-core
COPY klip-funnel/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt && pip install --no-cache-dir -e /app/klip-core
COPY klip-funnel /app
CMD ["uvicorn", "klip_funnel.main:app", "--host", "0.0.0.0", "--port", "8003"]
