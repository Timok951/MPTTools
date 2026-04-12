FROM postgres:17

RUN apt-get update \
    && apt-get install -y --no-install-recommends cron bash \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /backup

COPY docker/backup/backup-cron.sh /usr/local/bin/backup-cron.sh
COPY docker/backup/docker-entrypoint.sh /usr/local/bin/docker-backup-entrypoint.sh

RUN chmod +x /usr/local/bin/backup-cron.sh /usr/local/bin/docker-backup-entrypoint.sh

ENTRYPOINT ["/usr/local/bin/docker-backup-entrypoint.sh"]
