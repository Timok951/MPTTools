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

exec python manage.py runserver 0.0.0.0:8000
