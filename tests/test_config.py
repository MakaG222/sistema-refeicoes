"""
tests/test_config.py — Testes para config.py
"""

import json
import logging
import sys


# ── JsonFormatter ─────────────────────────────────────────────────────────────


def _get_json_formatter():
    """Import JsonFormatter by calling configure_logging on a mock app."""
    import types

    mock_app = types.SimpleNamespace(
        logger=logging.getLogger("test_json_formatter_helper")
    )
    # Remove any existing handlers to avoid duplication
    mock_app.logger.handlers.clear()

    # Temporarily patch is_production to True so JsonFormatter is used
    import config as cfg

    original = cfg.is_production
    cfg.is_production = True
    try:
        cfg.configure_logging(mock_app)
    finally:
        cfg.is_production = original

    # The handler added by configure_logging uses JsonFormatter
    for h in mock_app.logger.handlers:
        if isinstance(h, logging.StreamHandler) and hasattr(h.formatter, "format"):
            fmt = h.formatter
            if fmt.__class__.__name__ == "JsonFormatter":
                return fmt
    raise RuntimeError("JsonFormatter not found")


def test_json_formatter_basic():
    """JsonFormatter.format() retorna JSON válido com campos esperados."""
    fmt = _get_json_formatter()
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg="hello world",
        args=(),
        exc_info=None,
    )
    result = fmt.format(record)
    data = json.loads(result)
    assert data["level"] == "INFO"
    assert data["logger"] == "test"
    assert data["msg"] == "hello world"
    assert "ts" in data


def test_json_formatter_with_exception():
    """JsonFormatter inclui campo 'exception' quando exc_info está presente."""
    fmt = _get_json_formatter()
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        exc_info = sys.exc_info()

    record = logging.LogRecord(
        name="test",
        level=logging.ERROR,
        pathname="",
        lineno=0,
        msg="error occurred",
        args=(),
        exc_info=exc_info,
    )
    result = fmt.format(record)
    data = json.loads(result)
    assert "exception" in data
    assert "ValueError" in data["exception"]


def test_json_formatter_no_exception():
    """JsonFormatter não inclui campo 'exception' quando não há exc_info."""
    fmt = _get_json_formatter()
    record = logging.LogRecord(
        name="test",
        level=logging.WARNING,
        pathname="",
        lineno=0,
        msg="warning msg",
        args=(),
        exc_info=None,
    )
    result = fmt.format(record)
    data = json.loads(result)
    assert "exception" not in data


# ── configure_logging ─────────────────────────────────────────────────────────


def test_configure_logging_production(monkeypatch):
    """configure_logging em produção usa JsonFormatter."""
    import types
    import config as cfg

    monkeypatch.setattr(cfg, "is_production", True)

    mock_logger = logging.getLogger("test_configure_production")
    mock_logger.handlers.clear()
    mock_app = types.SimpleNamespace(logger=mock_logger)

    cfg.configure_logging(mock_app)

    assert len(mock_app.logger.handlers) >= 1
    handler = mock_app.logger.handlers[-1]
    assert handler.formatter.__class__.__name__ == "JsonFormatter"
    assert mock_app.logger.level == logging.INFO


def test_configure_logging_development(monkeypatch):
    """configure_logging em desenvolvimento usa Formatter simples."""
    import types
    import config as cfg

    monkeypatch.setattr(cfg, "is_production", False)

    mock_logger = logging.getLogger("test_configure_development")
    mock_logger.handlers.clear()
    mock_app = types.SimpleNamespace(logger=mock_logger)

    cfg.configure_logging(mock_app)

    assert len(mock_app.logger.handlers) >= 1
    handler = mock_app.logger.handlers[-1]
    assert isinstance(handler.formatter, logging.Formatter)
    assert mock_app.logger.level == logging.INFO


def test_configure_logging_sets_sqlite_level(monkeypatch):
    """configure_logging configura o logger sqlite3 para WARNING."""
    import types
    import config as cfg

    monkeypatch.setattr(cfg, "is_production", False)

    mock_logger = logging.getLogger("test_configure_sqlite")
    mock_logger.handlers.clear()
    mock_app = types.SimpleNamespace(logger=mock_logger)

    cfg.configure_logging(mock_app)

    assert logging.getLogger("sqlite3").level == logging.WARNING


# ── SECRET_KEY generation ─────────────────────────────────────────────────────


def test_secret_key_generated_when_not_set(monkeypatch):
    """Em dev sem SECRET_KEY, um token aleatório é gerado."""
    monkeypatch.setenv("ENV", "development")
    monkeypatch.delenv("SECRET_KEY", raising=False)

    # Reload the module with the patched env
    if "config" in sys.modules:
        del sys.modules["config"]
    import config as cfg

    assert cfg.SECRET_KEY  # não vazio
    assert len(cfg.SECRET_KEY) >= 32


def test_secret_key_uses_env_var(monkeypatch):
    """SECRET_KEY usa o valor da variável de ambiente quando definida."""
    monkeypatch.setenv("ENV", "development")
    monkeypatch.setenv("SECRET_KEY", "my-custom-key-123")

    if "config" in sys.modules:
        del sys.modules["config"]
    import config as cfg

    assert cfg.SECRET_KEY == "my-custom-key-123"

    # Restore default for other tests
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-not-for-production")
    del sys.modules["config"]
    import config  # noqa: F401


def test_secret_key_raises_in_production_without_key(monkeypatch):
    """Em produção sem SECRET_KEY definida, RuntimeError é lançado."""
    monkeypatch.setenv("ENV", "production")
    monkeypatch.delenv("SECRET_KEY", raising=False)

    if "config" in sys.modules:
        del sys.modules["config"]

    import pytest

    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        import config  # noqa: F401

    # Restore for subsequent tests
    monkeypatch.setenv("ENV", "development")
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-not-for-production")
    if "config" in sys.modules:
        del sys.modules["config"]
    import config  # noqa: F401


def test_secret_key_production_with_key(monkeypatch):
    """Em produção com SECRET_KEY definida, o módulo carrega sem erros."""
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("SECRET_KEY", "production-key-abcdef1234567890")

    if "config" in sys.modules:
        del sys.modules["config"]

    import config as cfg

    assert cfg.SECRET_KEY == "production-key-abcdef1234567890"
    assert cfg.is_production is True

    # Restore for subsequent tests
    monkeypatch.setenv("ENV", "development")
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-not-for-production")
    del sys.modules["config"]
    import config  # noqa: F401


# ── print_startup_banner ──────────────────────────────────────────────────────


def test_print_startup_banner_development(capsys):
    """print_startup_banner imprime banner com aviso de modo desenvolvimento."""
    import config as cfg

    cfg.print_startup_banner("/tmp/test.db")
    captured = capsys.readouterr()
    assert "MODO DESENVOLVIMENTO" in captured.out
    assert "/tmp/test.db" in captured.out


def test_print_startup_banner_no_cron_token(monkeypatch, capsys):
    """print_startup_banner avisa quando CRON_API_TOKEN não está definido."""
    import config as cfg

    monkeypatch.setattr(cfg, "CRON_API_TOKEN", "")
    cfg.print_startup_banner("/tmp/test.db")
    captured = capsys.readouterr()
    assert "CRON_API_TOKEN" in captured.out


def test_print_startup_banner_production(monkeypatch, capsys):
    """print_startup_banner em produção não imprime aviso de dev."""
    import config as cfg

    monkeypatch.setattr(cfg, "is_production", True)
    monkeypatch.setattr(cfg, "CRON_API_TOKEN", "some-token")
    cfg.print_startup_banner("/mnt/data/sistema.db")
    captured = capsys.readouterr()
    assert "MODO DESENVOLVIMENTO" not in captured.out
    assert "/mnt/data/sistema.db" in captured.out
