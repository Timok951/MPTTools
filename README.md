# MPTTools

## PostgreSQL setup

1. Copy `TIP/.env.example` to `TIP/.env`.
2. Create a PostgreSQL database and update the credentials in `TIP/.env`.
3. Run migrations:

```bash
.venv\Scripts\python.exe TIP\manage.py migrate
```

4. Initialize role groups:

```bash
.venv\Scripts\python.exe TIP\manage.py init_roles
```

5. Start the application:

```bash
.venv\Scripts\python.exe TIP\manage.py runserver
```

## PostgreSQL database objects

The project now ships PostgreSQL-oriented database objects via migrations:

- `inventory_equipment_stock_view`
- `inventory_request_summary_view`
- `inventory_active_checkout_view`
- `reject_stale_requests(...)`
- `finish_abandoned_timers(...)`
- `restock_low_stock_consumables(...)`
- `inventory_db_audit_event` with row-level audit triggers

These objects are created by `inventory` migration `0001_postgresql_database_objects`.
If you want to run the SQL objects manually on PostgreSQL, use:

`db/postgresql/001_inventory_objects.sql`

## Backup / restore regulation

Recommended учебный регламент:

- Daily logical backup with `pg_dump`
- Store at least the latest local backup plus one archival copy
- Before restore, stop active writes to the application
- After restore, verify login, equipment list, API schema, and one admin procedure

Example commands:

```bash
pg_dump -h localhost -p 5432 -U postgres -d mpttools -Fc -f backup.dump
pg_restore -h localhost -p 5432 -U postgres -d mpttools --clean --if-exists backup.dump
```

Manual SQL apply:

```bash
psql -h localhost -p 5432 -U postgres -d mpttools -f db/postgresql/001_inventory_objects.sql
```

## Automatic server backup

The project includes a Django management command for PostgreSQL backups:

```bash
.venv\Scripts\python.exe TIP\manage.py create_server_backup
```

Optional arguments:

- `--label nightly` adds a suffix to the dump filename
- `--keep 14` keeps only the latest 14 dump files
- `--output-dir G:\Backups\MPTTools` stores dumps outside the project folder
- `--pg-dump-path "C:\Program Files\PostgreSQL\17\bin\pg_dump.exe"` uses an explicit PostgreSQL client path

Environment variables in `TIP/.env`:

- `BACKUP_DIR` - backup directory, default `TIP/backups/postgresql`
- `BACKUP_KEEP_COUNT` - number of recent `.dump` files to keep, default `14`
- `PG_DUMP_PATH` - path to `pg_dump`, default `pg_dump`
- `BACKUP_INTERVAL_SECONDS` - interval between dumps in the Docker backup service, default `86400` (24h)

Docker automatic backup:

1. Make sure your PostgreSQL credentials are present in `TIP/.env`.
2. Start the dedicated backup container:

```bash
docker compose -f docker-compose.backup.yml up -d
```

3. Backups will be written to:

`backups/postgresql`

4. To inspect logs:

```bash
docker logs -f mpttools-backup
```

5. To stop the auto-backup container:

```bash
docker compose -f docker-compose.backup.yml down
```

Included Docker files:

- `docker-compose.backup.yml`
- `docker/backup/backup-cron.sh`
- `docker/backup/backup-loop.sh`

## Validation

```bash
.venv\Scripts\python.exe TIP\manage.py check
.venv\Scripts\python.exe TIP\manage.py test inventory.tests
```
