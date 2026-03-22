"""Tests for core/backup.py — backup, restore, validation, listing."""

import os
import sqlite3
import time

from core.backup import (
    do_backup,
    ensure_daily_backup,
    limpar_backups_antigos,
    list_backups,
    restore_backup,
    validate_backup,
)


# ── do_backup ─────────────────────────────────────────────────────────────


def test_do_backup_creates_file(tmp_path, monkeypatch):
    """do_backup copies the source DB into BACKUP_DIR with timestamp."""
    src_file = tmp_path / "test.db"
    src_file.write_text("data")
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()

    monkeypatch.setattr("core.constants.BASE_DADOS", str(src_file))
    monkeypatch.setattr("core.constants.BACKUP_DIR", str(backup_dir))
    monkeypatch.setattr("core.backup.BACKUP_DIR", str(backup_dir))

    result = do_backup()
    assert result is True
    files = list(backup_dir.glob("*.db"))
    assert len(files) == 1
    assert files[0].read_text() == "data"


def test_do_backup_missing_source(tmp_path, monkeypatch):
    """do_backup returns False when the source DB does not exist."""
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()

    monkeypatch.setattr("core.constants.BASE_DADOS", str(tmp_path / "nonexistent.db"))
    monkeypatch.setattr("core.constants.BACKUP_DIR", str(backup_dir))
    monkeypatch.setattr("core.backup.BACKUP_DIR", str(backup_dir))

    result = do_backup()
    assert result is False


# ── ensure_daily_backup ──────────────────────────────────────────────────


def test_ensure_daily_creates_once(tmp_path, monkeypatch):
    """Running ensure_daily_backup twice in the same day creates only one file."""
    src_file = tmp_path / "test.db"
    src_file.write_text("data")
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()

    monkeypatch.setattr("core.constants.BASE_DADOS", str(src_file))
    monkeypatch.setattr("core.constants.BACKUP_DIR", str(backup_dir))
    monkeypatch.setattr("core.backup.BACKUP_DIR", str(backup_dir))
    monkeypatch.setattr("core.backup.BACKUP_RETENCAO_DIAS", None)  # skip cleanup

    ensure_daily_backup()
    ensure_daily_backup()

    files = list(backup_dir.glob("*.db"))
    assert len(files) == 1


def test_ensure_daily_skips_if_exists(tmp_path, monkeypatch):
    """If the daily backup file already exists, ensure_daily_backup does not overwrite it."""
    from datetime import datetime

    src_file = tmp_path / "test.db"
    src_file.write_text("new-data")
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()

    monkeypatch.setattr("core.constants.BASE_DADOS", str(src_file))
    monkeypatch.setattr("core.constants.BACKUP_DIR", str(backup_dir))
    monkeypatch.setattr("core.backup.BACKUP_DIR", str(backup_dir))
    monkeypatch.setattr("core.backup.BACKUP_RETENCAO_DIAS", None)

    # Pre-create the expected backup file with old content
    ts_date = datetime.now().strftime("%Y%m%d")
    existing = backup_dir / f"test_{ts_date}.db"
    existing.write_text("old-data")

    ensure_daily_backup()

    # Content unchanged — file was not overwritten
    assert existing.read_text() == "old-data"


def test_ensure_daily_triggers_cleanup(tmp_path, monkeypatch):
    """ensure_daily_backup also runs limpar_backups_antigos."""
    src_file = tmp_path / "test.db"
    src_file.write_text("data")
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()

    monkeypatch.setattr("core.constants.BASE_DADOS", str(src_file))
    monkeypatch.setattr("core.constants.BACKUP_DIR", str(backup_dir))
    monkeypatch.setattr("core.backup.BACKUP_DIR", str(backup_dir))
    monkeypatch.setattr("core.backup.BACKUP_RETENCAO_DIAS", 1)

    # Create an old backup that should be cleaned
    old = backup_dir / "test_old.db"
    old.write_text("old")
    old_mtime = time.time() - 5 * 86400
    os.utime(old, (old_mtime, old_mtime))

    ensure_daily_backup()

    assert not old.exists(), "Old backup should have been cleaned up"


# ── limpar_backups_antigos ───────────────────────────────────────────────


def test_limpar_removes_old(tmp_path, monkeypatch):
    """Old backup files (beyond retention) are removed."""
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()

    monkeypatch.setattr("core.constants.BACKUP_DIR", str(backup_dir))
    monkeypatch.setattr("core.backup.BACKUP_DIR", str(backup_dir))
    monkeypatch.setattr("core.backup.BACKUP_RETENCAO_DIAS", 7)

    old_file = backup_dir / "test_20200101.db"
    old_file.write_text("old")
    # Set mtime to 30 days ago
    old_mtime = time.time() - 30 * 86400
    os.utime(old_file, (old_mtime, old_mtime))

    limpar_backups_antigos()

    assert not old_file.exists()


def test_limpar_keeps_recent(tmp_path, monkeypatch):
    """Recent backup files within retention period are kept."""
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()

    monkeypatch.setattr("core.constants.BACKUP_DIR", str(backup_dir))
    monkeypatch.setattr("core.backup.BACKUP_DIR", str(backup_dir))
    monkeypatch.setattr("core.backup.BACKUP_RETENCAO_DIAS", 30)

    recent_file = backup_dir / "test_recent.db"
    recent_file.write_text("recent")
    # mtime is now (default), well within 30 days

    limpar_backups_antigos()

    assert recent_file.exists()


def test_limpar_none_retention(tmp_path, monkeypatch):
    """When BACKUP_RETENCAO_DIAS is None, no files are deleted."""
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()

    monkeypatch.setattr("core.constants.BACKUP_DIR", str(backup_dir))
    monkeypatch.setattr("core.backup.BACKUP_DIR", str(backup_dir))
    monkeypatch.setattr("core.backup.BACKUP_RETENCAO_DIAS", None)

    old_file = backup_dir / "test_old.db"
    old_file.write_text("data")
    old_mtime = time.time() - 365 * 86400
    os.utime(old_file, (old_mtime, old_mtime))

    limpar_backups_antigos()

    assert old_file.exists()


# ── list_backups ─────────────────────────────────────────────────────────


def test_list_backups_returns_sorted(tmp_path, monkeypatch):
    """list_backups returns backups sorted by date, most recent first."""
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    monkeypatch.setattr("core.backup.BACKUP_DIR", str(backup_dir))

    # Create two backups with different mtimes
    old = backup_dir / "sistema_20260101.db"
    old.write_text("old")
    os.utime(old, (time.time() - 86400, time.time() - 86400))

    new = backup_dir / "sistema_20260322.db"
    new.write_text("newer-data")

    result = list_backups()
    assert len(result) == 2
    assert result[0]["name"] == "sistema_20260322.db"
    assert result[1]["name"] == "sistema_20260101.db"
    assert result[0]["size_mb"] >= 0


def test_list_backups_empty(tmp_path, monkeypatch):
    """list_backups returns empty list when no backups exist."""
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    monkeypatch.setattr("core.backup.BACKUP_DIR", str(backup_dir))

    result = list_backups()
    assert result == []


# ── validate_backup ──────────────────────────────────────────────────────


def _make_valid_backup(path):
    """Helper: create a valid SQLite backup file with utilizadores table."""
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE utilizadores (id INTEGER PRIMARY KEY, NII TEXT)")
    conn.execute("INSERT INTO utilizadores VALUES (1, 'admin')")
    conn.commit()
    conn.close()


def test_validate_backup_valid(tmp_path):
    """validate_backup returns True for a valid DB with utilizadores."""
    backup = tmp_path / "valid.db"
    _make_valid_backup(backup)

    valid, reason = validate_backup(str(backup))
    assert valid is True
    assert reason == ""


def test_validate_backup_missing_file(tmp_path):
    """validate_backup returns False for nonexistent file."""
    valid, reason = validate_backup(str(tmp_path / "nope.db"))
    assert valid is False
    assert "não encontrado" in reason


def test_validate_backup_empty_file(tmp_path):
    """validate_backup returns False for empty file."""
    empty = tmp_path / "empty.db"
    empty.write_text("")

    valid, reason = validate_backup(str(empty))
    assert valid is False
    assert "vazio" in reason.lower()


def test_validate_backup_not_sqlite(tmp_path):
    """validate_backup returns False for non-SQLite file."""
    bad = tmp_path / "bad.db"
    bad.write_text("this is not a database")

    valid, reason = validate_backup(str(bad))
    assert valid is False


def test_validate_backup_missing_table(tmp_path):
    """validate_backup returns False when utilizadores table is missing."""
    backup = tmp_path / "no_table.db"
    conn = sqlite3.connect(str(backup))
    conn.execute("CREATE TABLE other (id INTEGER)")
    conn.commit()
    conn.close()

    valid, reason = validate_backup(str(backup))
    assert valid is False
    assert "utilizadores" in reason


# ── restore_backup ───────────────────────────────────────────────────────


def test_restore_backup_success(tmp_path, monkeypatch):
    """restore_backup replaces the active DB and creates safety backup."""
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    monkeypatch.setattr("core.backup.BACKUP_DIR", str(backup_dir))

    # Current DB
    db_path = tmp_path / "sistema.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE utilizadores (id INTEGER PRIMARY KEY, NII TEXT)")
    conn.execute("INSERT INTO utilizadores VALUES (1, 'current')")
    conn.commit()
    conn.close()
    monkeypatch.setattr("core.constants.BASE_DADOS", str(db_path))

    # Backup to restore
    backup = tmp_path / "backup_old.db"
    conn = sqlite3.connect(str(backup))
    conn.execute("CREATE TABLE utilizadores (id INTEGER PRIMARY KEY, NII TEXT)")
    conn.execute("INSERT INTO utilizadores VALUES (1, 'restored')")
    conn.commit()
    conn.close()

    ok, msg = restore_backup(str(backup))
    assert ok is True
    assert "sucesso" in msg

    # Verify DB was replaced
    conn = sqlite3.connect(str(db_path))
    row = conn.execute("SELECT NII FROM utilizadores WHERE id=1").fetchone()
    conn.close()
    assert row[0] == "restored"

    # Verify safety backup was created
    safety = list(backup_dir.glob("pre_restauro_*.db"))
    assert len(safety) == 1


def test_restore_backup_invalid_file(tmp_path, monkeypatch):
    """restore_backup rejects invalid backup files."""
    monkeypatch.setattr("core.constants.BASE_DADOS", str(tmp_path / "sistema.db"))

    ok, msg = restore_backup(str(tmp_path / "nonexistent.db"))
    assert ok is False
    assert "inválido" in msg.lower()


def test_restore_backup_removes_wal(tmp_path, monkeypatch):
    """restore_backup removes orphaned WAL and SHM files."""
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    monkeypatch.setattr("core.backup.BACKUP_DIR", str(backup_dir))

    db_path = tmp_path / "sistema.db"
    db_path.write_text("")  # placeholder
    monkeypatch.setattr("core.constants.BASE_DADOS", str(db_path))

    # Create WAL/SHM files
    wal = tmp_path / "sistema.db-wal"
    shm = tmp_path / "sistema.db-shm"
    wal.write_text("wal")
    shm.write_text("shm")

    # Create valid backup
    backup = tmp_path / "backup.db"
    _make_valid_backup(backup)

    ok, _ = restore_backup(str(backup))
    assert ok is True
    assert not wal.exists()
    assert not shm.exists()
