"""
tests/test_database.py — Testes para core/database.py
"""

import sqlite3


# ── wal_checkpoint ────────────────────────────────────────────────────────────


def test_wal_checkpoint_runs_without_error(app):
    """wal_checkpoint executa sem lançar exceção."""
    from core.database import wal_checkpoint

    with app.app_context():
        wal_checkpoint()  # não deve lançar


def test_wal_checkpoint_handles_exception(app, monkeypatch):
    """wal_checkpoint suprime exceções internas silenciosamente."""
    from core import database

    def bad_conn():
        raise sqlite3.OperationalError("simulated error")

    monkeypatch.setattr(database, "_new_conn", bad_conn)

    # Deve suprimir e não relançar
    database.wal_checkpoint()


# ── sqlite_quick_check ────────────────────────────────────────────────────────


def test_sqlite_quick_check_returns_true(app):
    """sqlite_quick_check retorna True numa BD saudável."""
    from core.database import sqlite_quick_check

    with app.app_context():
        result = sqlite_quick_check()
    assert result is True


def test_sqlite_quick_check_returns_false_on_error(app, monkeypatch):
    """sqlite_quick_check retorna False quando ocorre uma exceção."""
    from core import database

    def bad_db():
        raise sqlite3.OperationalError("cannot open database")

    monkeypatch.setattr(database, "db", bad_db)

    result = database.sqlite_quick_check()
    assert result is False


def test_sqlite_quick_check_returns_false_when_row_none(app, monkeypatch):
    """sqlite_quick_check retorna False quando fetchone() devolve None."""
    from core import database

    class FakeCursor:
        def fetchone(self):
            return None

    class FakeConn:
        def execute(self, *a, **kw):
            return FakeCursor()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

    monkeypatch.setattr(database, "db", lambda: FakeConn())

    result = database.sqlite_quick_check()
    assert result is False


def test_sqlite_quick_check_returns_false_when_not_ok(app, monkeypatch):
    """sqlite_quick_check retorna False quando quick_check não devolve 'ok'."""
    from core import database

    class FakeCursor:
        def fetchone(self):
            # sqlite3.Row-like: indexable by position
            return ["corrupt"]

    class FakeConn:
        def execute(self, *a, **kw):
            return FakeCursor()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

    monkeypatch.setattr(database, "db", lambda: FakeConn())

    result = database.sqlite_quick_check()
    assert result is False


# ── close_request_db ──────────────────────────────────────────────────────────


def test_close_request_db_closes_connection(app):
    """close_request_db fecha e remove a conexão de g._sr_db."""
    from core.database import close_request_db, db

    with app.test_request_context("/"):
        from flask import g

        # Forçar a criação de uma conexão no contexto da request
        db()
        assert g._sr_db is not None

        close_request_db()

        assert g._sr_db is None


def test_close_request_db_no_connection(app):
    """close_request_db não falha quando não existe conexão em g."""
    from core.database import close_request_db

    with app.test_request_context("/"):
        # Sem conexão criada — não deve lançar
        close_request_db()


def test_close_request_db_with_exception_on_close(app, monkeypatch):
    """close_request_db suprime exceções ao fechar a conexão."""
    from core.database import close_request_db

    class BadConn:
        def close(self):
            raise sqlite3.OperationalError("already closed")

    with app.test_request_context("/"):
        from flask import g

        g._sr_db = BadConn()
        # Não deve relançar
        close_request_db()
        assert g._sr_db is None


def test_close_request_db_with_exc_argument(app):
    """close_request_db aceita argumento exc (usado pelo teardown do Flask)."""
    from core.database import close_request_db

    with app.test_request_context("/"):
        # Deve aceitar exc=None e exc=Exception sem falhar
        close_request_db(exc=None)
        close_request_db(exc=RuntimeError("teardown error"))


# ── db() outside request context ─────────────────────────────────────────────


def test_db_outside_request_context_returns_connection():
    """db() fora do contexto de request devolve uma nova conexão SQLite."""
    from core.database import db as get_db

    conn = get_db()
    try:
        assert isinstance(conn, sqlite3.Connection)
    finally:
        conn.close()


def test_db_falls_back_when_flask_import_fails(monkeypatch):
    """db() usa _new_conn() quando flask não pode ser importado."""
    import builtins
    from core import database

    real_import = builtins.__import__

    def mock_import(name, *args, **kwargs):
        if name == "flask":
            raise ImportError("flask not available")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", mock_import)

    conn = database.db()
    try:
        assert isinstance(conn, sqlite3.Connection)
    finally:
        conn.close()


def test_close_request_db_falls_back_when_flask_import_fails(monkeypatch):
    """close_request_db não falha quando flask não pode ser importado."""
    import builtins
    from core import database

    real_import = builtins.__import__

    def mock_import(name, *args, **kwargs):
        if name == "flask":
            raise ImportError("flask not available")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", mock_import)

    # Deve executar silenciosamente sem lançar
    database.close_request_db()
