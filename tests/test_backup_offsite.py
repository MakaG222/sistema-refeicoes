"""Tests for upload offsite and notification hooks em core/backup.py."""

from __future__ import annotations

import subprocess
import sys
from unittest.mock import patch

import pytest

from core import backup
from core.backup import ensure_daily_backup, upload_offsite


# ── upload_offsite ────────────────────────────────────────────────────────


def test_upload_offsite_disabled_returns_false(monkeypatch):
    monkeypatch.delenv("BACKUP_UPLOAD_CMD", raising=False)
    assert upload_offsite("/tmp/any.db") is False


def test_upload_offsite_runs_command(monkeypatch):
    # Usar o próprio python como comando "offsite" garantidamente disponível.
    monkeypatch.setenv(
        "BACKUP_UPLOAD_CMD",
        f'{sys.executable} -c "import sys;print(sys.argv[1])" {{path}}',
    )
    assert upload_offsite("/tmp/backup.db") is True


def test_upload_offsite_nonzero_exit_returns_false(monkeypatch):
    monkeypatch.setenv(
        "BACKUP_UPLOAD_CMD", f'{sys.executable} -c "import sys;sys.exit(2)"'
    )
    assert upload_offsite("/tmp/x.db") is False


def test_upload_offsite_missing_executable(monkeypatch):
    monkeypatch.setenv("BACKUP_UPLOAD_CMD", "definitely-not-a-binary-xyz {path}")
    assert upload_offsite("/tmp/x.db") is False


def test_upload_offsite_substitutes_path(monkeypatch):
    """O token {path} tem de ser substituído em cada argv, sem shell."""
    captured = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setenv("BACKUP_UPLOAD_CMD", "rclone copy {path} remote:backups/")
    with patch("core.backup.subprocess.run", side_effect=fake_run):
        assert upload_offsite("/data/backups/x.db") is True

    assert captured["args"] == [
        "rclone",
        "copy",
        "/data/backups/x.db",
        "remote:backups/",
    ]


def test_upload_offsite_handles_timeout(monkeypatch):
    def fake_run(args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args, timeout=1)

    monkeypatch.setenv("BACKUP_UPLOAD_CMD", "sleep 10 {path}")
    with patch("core.backup.subprocess.run", side_effect=fake_run):
        assert upload_offsite("/tmp/x.db") is False


def test_upload_offsite_invalid_timeout_uses_default(monkeypatch):
    captured = {}

    def fake_run(args, **kwargs):
        captured["timeout"] = kwargs.get("timeout")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setenv("BACKUP_UPLOAD_CMD", "echo {path}")
    monkeypatch.setenv("BACKUP_UPLOAD_TIMEOUT", "not-a-number")
    with patch("core.backup.subprocess.run", side_effect=fake_run):
        upload_offsite("/tmp/x.db")
    assert captured["timeout"] == 300


# ── ensure_daily_backup → chama upload_offsite e notify ────────────────────


@pytest.fixture
def _tmp_backup(tmp_path, monkeypatch):
    src_file = tmp_path / "test.db"
    src_file.write_text("data")
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()

    monkeypatch.setattr("core.constants.BASE_DADOS", str(src_file))
    monkeypatch.setattr("core.constants.BACKUP_DIR", str(backup_dir))
    monkeypatch.setattr("core.backup.BACKUP_DIR", str(backup_dir))

    return src_file, backup_dir


def test_ensure_daily_backup_calls_upload_when_cmd_set(_tmp_backup, monkeypatch):
    monkeypatch.setenv("BACKUP_UPLOAD_CMD", "echo {path}")
    calls = []

    def fake_upload(path):
        calls.append(path)
        return True

    monkeypatch.setattr(backup, "upload_offsite", fake_upload)
    ensure_daily_backup()
    assert len(calls) == 1
    assert calls[0].endswith(".db")


def test_ensure_daily_backup_notifies_on_upload_failure(_tmp_backup, monkeypatch):
    monkeypatch.setenv("BACKUP_UPLOAD_CMD", "echo {path}")
    monkeypatch.setattr(backup, "upload_offsite", lambda p: False)

    captured = []
    monkeypatch.setattr(
        backup, "notify", lambda t, m, severity="info": captured.append((t, severity))
    )
    ensure_daily_backup()
    assert any("Upload offsite" in t for t, _ in captured)
    assert any(s == "warning" for _, s in captured)


def test_ensure_daily_backup_no_upload_when_cmd_unset(_tmp_backup, monkeypatch):
    monkeypatch.delenv("BACKUP_UPLOAD_CMD", raising=False)
    calls = []
    monkeypatch.setattr(backup, "upload_offsite", lambda p: calls.append(p) or True)
    ensure_daily_backup()
    assert calls == []


def test_ensure_daily_backup_notifies_on_local_failure(tmp_path, monkeypatch):
    """Se o source não existir E o dest também não → copy falha e notifica."""
    src_file = tmp_path / "missing.db"  # não existe
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()

    monkeypatch.setattr("core.constants.BASE_DADOS", str(src_file))
    monkeypatch.setattr("core.constants.BACKUP_DIR", str(backup_dir))
    monkeypatch.setattr("core.backup.BACKUP_DIR", str(backup_dir))

    # Forçar shutil.copy2 a falhar quando (e se) for chamado
    def boom(*a, **kw):
        raise OSError("disk full")

    monkeypatch.setattr("core.backup.shutil.copy2", boom)

    captured = []
    monkeypatch.setattr(
        backup, "notify", lambda t, m, severity="info": captured.append((t, severity))
    )

    # Source não existe → não chega a copy2, mas cria o ficheiro para forçar o caminho
    src_file.write_text("data")
    ensure_daily_backup()

    assert any("Backup diário" in t for t, _ in captured)
    assert any(s == "error" for _, s in captured)
