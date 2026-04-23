#!/usr/bin/env bash
set -uo pipefail

INTERVAL="${BACKUP_INTERVAL_SECONDS:-86400}"

echo "PostgreSQL backup loop: interval ${INTERVAL}s"

while true; do
  bash /usr/local/bin/backup-cron.sh || echo "backup-cron.sh failed (will retry after sleep)" >&2
  sleep "$INTERVAL"
done
