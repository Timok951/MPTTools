FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_DIR=/app/TIP

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends bash curl \
    && rm -rf /var/lib/apt/lists/*

COPY . /app

RUN python -m pip install --upgrade pip \
    && pip install --no-cache-dir \
        Django==5.2.11 \
        djangorestframework==3.17.1 \
        psycopg2-binary==2.9.11 \
    && chmod +x /app/entrypoint.sh

EXPOSE 8000
