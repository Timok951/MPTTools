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

## Validation

```bash
.venv\Scripts\python.exe TIP\manage.py check
.venv\Scripts\python.exe TIP\manage.py test inventory.tests
```
