# OOU Cooperative app image — one small copy runs per client on the VPS.
# Caddy (separate container) handles HTTPS + routing, so no nginx here.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Runtime libs used by Pillow / reportlab / qrcode.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libjpeg62-turbo zlib1g libfreetype6 \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY . .

# Entrypoint waits for Postgres to be ready, then starts gunicorn.
COPY deploy/vps/docker-entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8000
ENTRYPOINT ["/entrypoint.sh"]
