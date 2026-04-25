FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY filter.py .

ENV OUTPUT_DIR=/app/data
ENV PYTHONUTF8=1

CMD ["python", "filter.py"]
