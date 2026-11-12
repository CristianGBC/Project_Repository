FROM python:3.12-slim

WORKDIR /code
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY coordinator ./coordinator
COPY storage ./storage
COPY schemas ./schemas
COPY experiments ./experiments

ENV PYTHONUNBUFFERED=1
