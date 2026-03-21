"""Tests for core/backup.py — do_backup, ensure_daily_backup, limpar_backups_antigos."""

import os
import time

from core.backup import do_backup, ensure_daily_backup, limpar_backups_antigos


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

    # Pre-create the expected backup file with old content
    ts_date = datetime.now().strftime("%Y%m%d")
    existing = backup_dir / f"test_{ts_date}.db"
    existing.write_text("old-data")

    ensure_daily_backup()

    # Content unchanged — file was not overwritten
    assert existing.read_text() == "old-data"


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
