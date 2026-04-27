"""SQLite backup helper.

Uses SQLite's online .backup API (via sqlite3.Connection.backup) so the
file is consistent even if the orchestrator is mid-write. The reconciler
runs every 30s, so we can't rely on cp.

Configurable retention: keep the last N daily snapshots, deletes older.
"""
from __future__ import annotations

import gzip
import logging
import os
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from src.config import get_settings

log = logging.getLogger(__name__)

DEFAULT_RETENTION = 14  # days


def _sqlite_path_from_url(url: str) -> Path | None:
    if not url.startswith("sqlite:///"):
        return None
    return Path(url.replace("sqlite:///", "", 1))


def backup_database(retention_days: int = DEFAULT_RETENTION) -> Path | None:
    """Snapshot the SQLite DB (gzipped) into data/backup/. Returns the new path.

    No-op (returns None) if DATABASE_URL isn't SQLite.
    """
    settings = get_settings()
    src = _sqlite_path_from_url(settings.database_url)
    if src is None:
        log.info("backup skipped: DATABASE_URL is not sqlite")
        return None
    if not src.exists():
        log.warning("backup skipped: %s does not exist", src)
        return None

    backup_dir = src.parent / "backup"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    tmp = backup_dir / f"trading-{stamp}.db"
    target = backup_dir / f"trading-{stamp}.db.gz"

    # Use SQLite's online backup so the file is consistent under concurrent writes.
    with sqlite3.connect(str(src)) as conn_src, sqlite3.connect(str(tmp)) as conn_dst:
        conn_src.backup(conn_dst)

    with open(tmp, "rb") as fin, gzip.open(target, "wb", compresslevel=6) as fout:
        shutil.copyfileobj(fin, fout)
    tmp.unlink(missing_ok=True)

    _prune_old_backups(backup_dir, retention_days)
    log.info("DB backup written: %s (%.1f KB)", target, target.stat().st_size / 1024)
    return target


def _prune_old_backups(backup_dir: Path, retention_days: int) -> None:
    if retention_days <= 0:
        return
    cutoff = datetime.now(timezone.utc).timestamp() - retention_days * 86400
    for f in backup_dir.glob("trading-*.db.gz"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
                log.info("pruned old backup %s", f.name)
        except OSError:
            log.exception("failed to prune %s", f)


def main() -> None:  # pragma: no cover - CLI
    from src.core.logging_setup import setup_logging

    setup_logging(get_settings().log_level)
    out = backup_database(int(os.environ.get("BACKUP_RETENTION_DAYS", DEFAULT_RETENTION)))
    if out:
        print(out)


if __name__ == "__main__":  # pragma: no cover
    main()
