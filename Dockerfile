FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1
ENV PYTHONUTF8=1
ENV OUTPUT_DIR=/app/data
ENV UPDATE_INTERVAL_SECONDS=21600

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY filter.py .

RUN mkdir -p /app/data

CMD ["sh", "-c", "while true; do python filter.py; sleep ${UPDATE_INTERVAL_SECONDS}; done"]
