FROM python:3.11-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1
COPY klip-core /app/klip-core
COPY klip-scanner/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt && pip install --no-cache-dir -e /app/klip-core
COPY klip-scanner /app
CMD ["python", "main.py"]
