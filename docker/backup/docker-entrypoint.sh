#!/bin/bash
set -euo pipefail

BACKUP_CRON_SCHEDULE="${BACKUP_CRON_SCHEDULE:-0 2 * * *}"

printf '%s bash /usr/local/bin/backup-cron.sh >> /var/log/backup-cron.log 2>&1\n' "$BACKUP_CRON_SCHEDULE" > /etc/cron.d/mpttools-backup
chmod 0644 /etc/cron.d/mpttools-backup
crontab /etc/cron.d/mpttools-backup

touch /var/log/backup-cron.log

echo "Starting backup cron with schedule: $BACKUP_CRON_SCHEDULE"
cron && tail -F /var/log/backup-cron.log
