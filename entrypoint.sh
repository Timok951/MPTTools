#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/app/TIP}"
cd "$APP_DIR"

echo "Waiting for PostgreSQL at ${DATABASE_HOST:-localhost}:${DATABASE_PORT:-5432}..."

python - <<'PY'
import os
import sys
import time

try:
    import psycopg2
    from psycopg2 import OperationalError
except ImportError:
    print("psycopg2 is required for DB wait loop", file=sys.stderr)
    sys.exit(1)

host = os.getenv("DATABASE_HOST", "localhost")
port = int(os.getenv("DATABASE_PORT", "5432") or "5432")
user = os.getenv("DATABASE_USERNAME", "postgres")
password = os.getenv("DATABASE_PASSWORD", "")
dbname = os.getenv("DATABASE_NAME", "postgres")

max_attempts = 45
for attempt in range(1, max_attempts + 1):
    try:
        conn = psycopg2.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            dbname=dbname,
            connect_timeout=3,
        )
        conn.close()
        print("PostgreSQL is ready.", file=sys.stderr)
        sys.exit(0)
    except OperationalError as exc:
        print(f"Attempt {attempt}/{max_attempts}: {exc}", file=sys.stderr)
        time.sleep(2)

print("PostgreSQL did not become ready in time.", file=sys.stderr)
sys.exit(1)
PY

echo "Running migrations..."
python manage.py migrate --noinput

echo "Initializing default role groups..."
python manage.py init_roles

RUN_TESTS_ON_START="${RUN_TESTS_ON_START:-true}"
TEST_SUITE_ON_START="${TEST_SUITE_ON_START:-inventory.tests}"
TEST_ARGS_ON_START="${TEST_ARGS_ON_START:---noinput --keepdb --verbosity 2}"

if [[ "${RUN_TESTS_ON_START,,}" == "true" || "${RUN_TESTS_ON_START}" == "1" || "${RUN_TESTS_ON_START,,}" == "yes" ]]; then
    echo "Ensuring stale Django test database is removed before test run..."
    python - <<'PY'
import os
import sys

try:
    import psycopg2
except ImportError:
    print("psycopg2 is required for test DB cleanup", file=sys.stderr)
    sys.exit(1)

host = os.getenv("DATABASE_HOST", "localhost")
port = int(os.getenv("DATABASE_PORT", "5432") or "5432")
user = os.getenv("DATABASE_USERNAME", "postgres")
password = os.getenv("DATABASE_PASSWORD", "")
db_name = os.getenv("DATABASE_NAME", "mpt_tools")
test_db_name = f"test_{db_name}"

try:
    conn = psycopg2.connect(
        host=host,
        port=port,
        user=user,
        password=password,
        dbname="postgres",
        connect_timeout=5,
    )
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT pg_terminate_backend(pid)
            FROM pg_stat_activity
            WHERE datname = %s AND pid <> pg_backend_pid()
            """,
            (test_db_name,),
        )
        cur.execute(f'DROP DATABASE IF EXISTS "{test_db_name}"')
    conn.close()
    print(f"Pre-test cleanup complete: {test_db_name}")
except Exception as exc:
    print(f"Pre-test DB cleanup skipped: {exc}", file=sys.stderr)
PY

    echo "Running test suite before server start: ${TEST_SUITE_ON_START}"
    echo "Test args: ${TEST_ARGS_ON_START}"
    read -r -a _test_args <<< "${TEST_ARGS_ON_START}"
    python -u manage.py test "${TEST_SUITE_ON_START}" "${_test_args[@]}"
    echo "Test suite completed successfully."
else
    echo "Skipping test suite on startup (RUN_TESTS_ON_START=${RUN_TESTS_ON_START})."
fi

if [[ -n "${DJANGO_SUPERUSER_USERNAME:-}" && -n "${DJANGO_SUPERUSER_PASSWORD:-}" ]]; then
    echo "Ensuring Django superuser exists..."
    python manage.py shell <<'PY'
import os
from django.contrib.auth import get_user_model

User = get_user_model()
username = os.environ.get("DJANGO_SUPERUSER_USERNAME", "").strip()
email = (os.environ.get("DJANGO_SUPERUSER_EMAIL") or "admin@localhost").strip()
password = os.environ.get("DJANGO_SUPERUSER_PASSWORD", "")

if username and password and not User.objects.filter(username=username).exists():
    User.objects.create_superuser(username, email, password)
    print("Superuser created:", username)
else:
    print("Superuser skipped (already exists or env incomplete).")
PY
fi

DJANGO_LISTEN_PORT="${DJANGO_LISTEN_PORT:-8000}"
exec python manage.py runserver "0.0.0.0:${DJANGO_LISTEN_PORT}"
