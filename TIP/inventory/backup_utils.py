import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings
from django.core.management.base import CommandError
from django.utils import timezone


def _sanitize_label(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-._")


@dataclass(frozen=True)
class PostgreSQLBackupConfig:
    db_name: str
    db_user: str
    db_password: str
    db_host: str
    db_port: str
    output_dir: Path
    keep_count: int
    pg_dump_path: str


@dataclass(frozen=True)
class PostgreSQLBackupResult:
    backup_path: Path
    removed_files: tuple[Path, ...]
    command: tuple[str, ...]


def get_postgresql_backup_config(
    *,
    output_dir: str | None = None,
    keep_count: int | None = None,
    pg_dump_path: str | None = None,
) -> PostgreSQLBackupConfig:
    db_cfg = settings.DATABASES["default"]
    if not str(db_cfg["ENGINE"]).endswith("postgresql"):
        raise CommandError("Automatic server backup is available only for PostgreSQL.")

    default_output_dir = settings.BASE_DIR / "backups" / "postgresql"
    resolved_output_dir = Path(output_dir or os.getenv("BACKUP_DIR") or default_output_dir)
    resolved_keep_count = keep_count if keep_count is not None else int(os.getenv("BACKUP_KEEP_COUNT", "14"))
    resolved_pg_dump_path = pg_dump_path or os.getenv("PG_DUMP_PATH", "pg_dump")

    return PostgreSQLBackupConfig(
        db_name=str(db_cfg.get("NAME", "")).strip(),
        db_user=str(db_cfg.get("USER", "postgres")).strip(),
        db_password=str(db_cfg.get("PASSWORD", "")).strip(),
        db_host=str(db_cfg.get("HOST", "localhost")).strip(),
        db_port=str(db_cfg.get("PORT", "5432")).strip(),
        output_dir=resolved_output_dir,
        keep_count=max(resolved_keep_count, 1),
        pg_dump_path=resolved_pg_dump_path,
    )


def _backup_filename(*, db_name: str, label: str = "", now=None) -> str:
    stamp = (now or timezone.now()).strftime("%Y%m%d_%H%M%S")
    safe_label = _sanitize_label(label)
    parts = [db_name, stamp]
    if safe_label:
        parts.append(safe_label)
    return "_".join(parts) + ".dump"


def prune_old_backups(output_dir: Path, keep_count: int) -> tuple[Path, ...]:
    dump_files = sorted(output_dir.glob("*.dump"), key=lambda path: path.stat().st_mtime, reverse=True)
    to_remove = dump_files[keep_count:]
    for path in to_remove:
        path.unlink(missing_ok=True)
    return tuple(to_remove)


def create_postgresql_backup(
    config: PostgreSQLBackupConfig,
    *,
    label: str = "",
    now=None,
) -> PostgreSQLBackupResult:
    config.output_dir.mkdir(parents=True, exist_ok=True)
    backup_path = config.output_dir / _backup_filename(db_name=config.db_name, label=label, now=now)

    command = (
        config.pg_dump_path,
        "-h",
        config.db_host,
        "-p",
        config.db_port,
        "-U",
        config.db_user,
        "-d",
        config.db_name,
        "-Fc",
        "-f",
        str(backup_path),
    )

    env = os.environ.copy()
    if config.db_password:
        env["PGPASSWORD"] = config.db_password

    try:
        subprocess.run(command, check=True, env=env, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise CommandError(
            f"pg_dump was not found: {config.pg_dump_path}. Set PG_DUMP_PATH in TIP/.env or install PostgreSQL client tools."
        ) from exc
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        stdout = (exc.stdout or "").strip()
        details = stderr or stdout or str(exc)
        raise CommandError(f"PostgreSQL backup failed: {details}") from exc

    removed_files = prune_old_backups(config.output_dir, config.keep_count)
    return PostgreSQLBackupResult(
        backup_path=backup_path,
        removed_files=removed_files,
        command=command,
    )


def restore_postgresql_custom_dump(
    dump_path: Path,
    config: PostgreSQLBackupConfig,
    *,
    pg_restore_path: str | None = None,
) -> None:
    """Restore a custom-format (-Fc) pg_dump file created by create_postgresql_backup."""
    resolved = pg_restore_path or os.getenv("PG_RESTORE_PATH", "pg_restore")
    command = (
        resolved,
        "-h",
        config.db_host,
        "-p",
        config.db_port,
        "-U",
        config.db_user,
        "-d",
        config.db_name,
        "--clean",
        "--if-exists",
        "--no-owner",
        "--no-acl",
        str(dump_path),
    )
    env = os.environ.copy()
    if config.db_password:
        env["PGPASSWORD"] = config.db_password
    try:
        subprocess.run(command, check=True, env=env, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise CommandError(
            f"pg_restore was not found: {resolved}. Set PG_RESTORE_PATH in TIP/.env or install PostgreSQL client tools."
        ) from exc
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        stdout = (exc.stdout or "").strip()
        details = stderr or stdout or str(exc)
        raise CommandError(f"PostgreSQL restore failed: {details}") from exc
