#!/bin/bash
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-/backups}"
KEEP_COUNT="${BACKUP_KEEP_COUNT:-14}"
BACKUP_LABEL="${BACKUP_LABEL:-docker}"
DB_HOST="${DATABASE_HOST:-postgres}"
DB_PORT="${DATABASE_PORT:-5432}"
DB_NAME="${DATABASE_NAME:-mpttools}"
DB_USER="${DATABASE_USERNAME:-postgres}"
DB_PASSWORD="${DATABASE_PASSWORD:-}"

mkdir -p "$BACKUP_DIR"

timestamp="$(date +%Y%m%d_%H%M%S)"
backup_path="$BACKUP_DIR/${DB_NAME}_${timestamp}_${BACKUP_LABEL}.dump"

export PGPASSWORD="$DB_PASSWORD"
pg_dump -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -Fc -f "$backup_path"

mapfile -t dump_files < <(ls -1t "$BACKUP_DIR"/*.dump 2>/dev/null || true)
if [ "${#dump_files[@]}" -gt "$KEEP_COUNT" ]; then
    for old_file in "${dump_files[@]:$KEEP_COUNT}"; do
        rm -f "$old_file"
    done
fi

echo "Backup created: $backup_path"
