from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import shutil
import subprocess
from urllib.parse import unquote, urlsplit


AUTO_BACKUP_PREFIX = "nffl_auto_pre_stage_"
DEFAULT_BACKUP_DIR = Path("/league_runtime/backups")
DEFAULT_RETENTION_COUNT = 3
MINIMUM_VALID_DUMP_BYTES = 1024


@dataclass(frozen=True)
class NfflRolloverBackupResult:
    path: Path
    size_bytes: int
    removed_files: tuple[str, ...]


def _connection_parts(
    dsn: str,
) -> tuple[str, str, str, str, str]:
    parsed = urlsplit(str(dsn or "").strip())

    if parsed.scheme not in {"postgres", "postgresql"}:
        raise ValueError(
            "Postgres DSN must use postgres:// or postgresql://."
        )

    host = str(parsed.hostname or "").strip()
    port = str(parsed.port or 5432)
    user = unquote(str(parsed.username or "")).strip()
    password = unquote(str(parsed.password or ""))
    database = unquote(
        str(parsed.path or "").lstrip("/")
    ).strip()

    if not host:
        raise ValueError("Postgres DSN is missing a host.")

    if not user:
        raise ValueError("Postgres DSN is missing a user.")

    if not database:
        raise ValueError("Postgres DSN is missing a database.")

    return host, port, user, password, database


def _prune_automated_backups(
    backup_dir: Path,
    *,
    keep: int,
) -> tuple[str, ...]:
    keep = int(keep)

    if keep < 1:
        raise ValueError("Backup retention must keep at least one dump.")

    candidates = list(
        backup_dir.glob(
            f"{AUTO_BACKUP_PREFIX}*.dump"
        )
    )

    candidates.sort(
        key=lambda path: (
            path.stat().st_mtime_ns,
            path.name,
        ),
        reverse=True,
    )

    removed: list[str] = []

    for path in candidates[keep:]:
        path.unlink()
        removed.append(path.name)

    return tuple(removed)


def create_nffl_rollover_backup(
    *,
    dsn: str,
    source_season_year: int,
    target_season_year: int,
    backup_dir: str | Path = DEFAULT_BACKUP_DIR,
    retention_count: int = DEFAULT_RETENTION_COUNT,
    now: datetime | None = None,
) -> NfflRolloverBackupResult:
    """
    Create and verify a full PostgreSQL safety dump before NFFL staging.

    Only dumps created with AUTO_BACKUP_PREFIX participate in retention.
    Historical/manual backup files are deliberately untouched.
    """

    pg_dump = shutil.which("pg_dump")
    pg_restore = shutil.which("pg_restore")

    if not pg_dump:
        raise RuntimeError(
            "Automatic rollover backup requires pg_dump."
        )

    if not pg_restore:
        raise RuntimeError(
            "Automatic rollover backup requires pg_restore."
        )

    source_year = int(source_season_year)
    target_year = int(target_season_year)

    if target_year != source_year + 1:
        raise ValueError(
            "Rollover backup requires exactly the next season."
        )

    host, port, user, password, database = (
        _connection_parts(dsn)
    )

    root = Path(backup_dir)
    root.mkdir(
        parents=True,
        exist_ok=True,
    )

    timestamp = (
        now
        or datetime.now(timezone.utc)
    ).astimezone(timezone.utc).strftime(
        "%Y%m%dT%H%M%S_%fZ"
    )

    filename = (
        f"{AUTO_BACKUP_PREFIX}"
        f"{source_year}_to_{target_year}_"
        f"{timestamp}.dump"
    )

    final_path = root / filename
    temp_path = root / f".{filename}.tmp"

    if temp_path.exists():
        temp_path.unlink()

    env = os.environ.copy()

    if password:
        env["PGPASSWORD"] = password
    else:
        env.pop("PGPASSWORD", None)

    dump_cmd = [
        pg_dump,
        "--format=custom",
        "--no-owner",
        "--no-privileges",
        "--host",
        host,
        "--port",
        port,
        "--username",
        user,
        "--dbname",
        database,
        "--file",
        str(temp_path),
    ]

    try:
        result = subprocess.run(
            dump_cmd,
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=300,
        )

        if result.returncode != 0:
            detail = (
                str(result.stderr or "").strip()
                or "pg_dump returned a non-zero exit code."
            )
            raise RuntimeError(
                f"Automatic NFFL rollover backup failed: {detail}"
            )

        if not temp_path.is_file():
            raise RuntimeError(
                "Automatic NFFL rollover backup produced no dump file."
            )

        size_bytes = int(
            temp_path.stat().st_size
        )

        if size_bytes < MINIMUM_VALID_DUMP_BYTES:
            raise RuntimeError(
                "Automatic NFFL rollover backup is unexpectedly small: "
                f"{size_bytes} bytes."
            )

        verify = subprocess.run(
            [
                pg_restore,
                "--list",
                str(temp_path),
            ],
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )

        if verify.returncode != 0:
            detail = (
                str(verify.stderr or "").strip()
                or "pg_restore --list returned a non-zero exit code."
            )
            raise RuntimeError(
                "Automatic NFFL rollover backup validation failed: "
                f"{detail}"
            )

        if not str(verify.stdout or "").strip():
            raise RuntimeError(
                "Automatic NFFL rollover backup validation returned "
                "an empty archive listing."
            )

        os.chmod(
            temp_path,
            0o600,
        )

        os.replace(
            temp_path,
            final_path,
        )

        removed = _prune_automated_backups(
            root,
            keep=int(retention_count),
        )

        return NfflRolloverBackupResult(
            path=final_path,
            size_bytes=int(final_path.stat().st_size),
            removed_files=removed,
        )

    finally:
        if temp_path.exists():
            temp_path.unlink()