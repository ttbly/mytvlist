FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    OUTPUT_DIR=/app/data

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY filter.py .

RUN mkdir -p /app/data

VOLUME ["/app/data"]

CMD ["python", "/app/filter.py"]
