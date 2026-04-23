#!/usr/bin/env bash
# Периодический бэкап без cron (удобно на bind-mount и при параллельных compose-проектах).
set -euo pipefail

INTERVAL="${BACKUP_INTERVAL_SECONDS:-86400}"

echo "PostgreSQL backup loop: interval ${INTERVAL}s"

while true; do
  if bash /scripts/backup-cron.sh; then
    echo "$(date -Iseconds) backup cycle OK"
  else
    echo "$(date -Iseconds) backup cycle FAILED" >&2
  fi
  sleep "${INTERVAL}"
done
