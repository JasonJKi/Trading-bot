"""Tests for the SQLite backup helper."""
from __future__ import annotations

import gzip
import os
import sqlite3
import tempfile
import time
from pathlib import Path

import pytest

from src.core import backup


@pytest.fixture
def temp_db(monkeypatch):
    tmp_dir = tempfile.mkdtemp()
    db_path = Path(tmp_dir) / "trading.db"
    # Create a real SQLite DB with a row so backup has something to copy.
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("CREATE TABLE x (a INT)")
        conn.execute("INSERT INTO x VALUES (1)")
        conn.commit()
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    from src import config

    config._settings = None
    yield db_path
    # Cleanup
    for f in Path(tmp_dir).rglob("*"):
        try:
            f.unlink()
        except OSError:
            pass


def test_backup_writes_gzipped_copy(temp_db):
    out = backup.backup_database()
    assert out is not None
    assert out.suffix == ".gz"
    assert out.exists()
    # The gzipped file unpacks to a valid SQLite DB.
    with gzip.open(out, "rb") as fin:
        body = fin.read()
    assert body.startswith(b"SQLite format")


def test_backup_noop_when_not_sqlite(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://x/y")
    from src import config

    config._settings = None
    assert backup.backup_database() is None


def test_backup_prunes_old_files(temp_db):
    backup_dir = temp_db.parent / "backup"
    backup_dir.mkdir(exist_ok=True)
    # Create a stale file dated 30 days ago.
    stale = backup_dir / "trading-old.db.gz"
    stale.write_bytes(b"x")
    old_ts = time.time() - 30 * 86400
    os.utime(stale, (old_ts, old_ts))

    backup.backup_database(retention_days=14)
    assert not stale.exists(), "old backup should have been pruned"
