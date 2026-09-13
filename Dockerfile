FROM python:3.12-alpine

WORKDIR /app

RUN apk add --no-cache busybox-suid tzdata \
    && pip install --no-cache-dir requests beautifulsoup4

ENV TZ=Europe/Paris

COPY flemmix-scraper-v16.py /app/flemmix-scraper-v16.py
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
