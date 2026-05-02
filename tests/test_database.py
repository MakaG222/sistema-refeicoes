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


# ── Slow query log (opt-in via SLOW_QUERY_THRESHOLD_MS) ──────────────────────


class TestSlowQueryLog:
    """Verifica que `SLOW_QUERY_THRESHOLD_MS` activa logging de queries lentas.

    Sem o env var, `_new_conn()` devolve `sqlite3.Connection` vanilla — zero
    overhead. Com threshold > 0, devolve `_TracingConnection` que mede tempo
    via `time.perf_counter` e loga via WARNING quando a duração excede o
    limite.
    """

    def test_threshold_zero_uses_vanilla_connection(self, monkeypatch, app):
        """SLOW_QUERY_THRESHOLD_MS ausente/0 → Connection normal (sem overhead)."""
        from core import database

        monkeypatch.delenv("SLOW_QUERY_THRESHOLD_MS", raising=False)
        with app.app_context():
            conn = database._new_conn()
            try:
                # Connection vanilla, NÃO _TracingConnection
                assert type(conn).__name__ == "Connection"
                assert not isinstance(conn, database._TracingConnection)
            finally:
                conn.close()

    def test_threshold_positive_installs_tracing_connection(self, monkeypatch, app):
        """SLOW_QUERY_THRESHOLD_MS > 0 → instala _TracingConnection."""
        from core import database

        monkeypatch.setenv("SLOW_QUERY_THRESHOLD_MS", "100")
        with app.app_context():
            conn = database._new_conn()
            try:
                assert isinstance(conn, database._TracingConnection)
            finally:
                conn.close()

    def test_slow_query_is_logged(self, monkeypatch, app, caplog):
        """Query que excede o threshold deve aparecer no log com '[slow_query]'."""
        import logging

        from core import database

        monkeypatch.setenv("SLOW_QUERY_THRESHOLD_MS", "0.001")  # 1µs — qq query passa
        caplog.set_level(logging.WARNING, logger="core.database")

        with app.app_context():
            conn = database._new_conn()
            try:
                conn.execute("SELECT 1")
            finally:
                conn.close()

        slow_logs = [r for r in caplog.records if "[slow_query]" in r.getMessage()]
        assert len(slow_logs) >= 1, "esperava log [slow_query] mas não apareceu"
        # Pelo menos UMA mensagem deve referir a nossa query SELECT 1.
        # (Os PRAGMAs de setup também são instrumentados — não os filtramos
        # porque eles também são "queries que valem a pena medir".)
        msgs = [r.getMessage() for r in slow_logs]
        assert any("SELECT 1" in m for m in msgs), (
            f"SELECT 1 não apareceu nos logs: {msgs}"
        )
        # E todas as mensagens devem incluir a duração em ms
        assert all("ms" in m for m in msgs)

    def test_fast_query_not_logged(self, monkeypatch, app, caplog):
        """Query mais rápida que threshold NÃO deve aparecer no log."""
        import logging

        from core import database

        # Threshold ridiculamente alto — nada vai logar
        monkeypatch.setenv("SLOW_QUERY_THRESHOLD_MS", "999999")
        caplog.set_level(logging.WARNING, logger="core.database")

        with app.app_context():
            conn = database._new_conn()
            try:
                conn.execute("SELECT 1")
            finally:
                conn.close()

        slow_logs = [r for r in caplog.records if "[slow_query]" in r.getMessage()]
        assert slow_logs == [], (
            f"não esperava logs slow_query mas apareceram: {slow_logs}"
        )

    def test_executemany_also_traced(self, monkeypatch, app, caplog):
        """executemany() também é instrumentado (separado de execute)."""
        import logging

        from core import database

        monkeypatch.setenv("SLOW_QUERY_THRESHOLD_MS", "0.001")
        caplog.set_level(logging.WARNING, logger="core.database")

        with app.app_context():
            conn = database._new_conn()
            try:
                conn.execute("CREATE TEMP TABLE t_test_executemany (x INT)")
                conn.executemany(
                    "INSERT INTO t_test_executemany VALUES (?)", [(1,), (2,), (3,)]
                )
            finally:
                conn.close()

        many_logs = [r for r in caplog.records if "[slow_query/many]" in r.getMessage()]
        assert len(many_logs) >= 1, "executemany não foi traced"

    def test_truncate_sql_collapses_whitespace_and_limits_length(self):
        from core.database import _truncate_sql

        # Whitespace múltiplo é colapsado
        assert _truncate_sql("SELECT  *\n  FROM   tbl") == "SELECT * FROM tbl"
        # Trunca com elipse Unicode
        long = "SELECT " + "x," * 200 + "y FROM tbl"
        out = _truncate_sql(long, limit=50)
        assert len(out) == 51  # 50 chars + … (1 char)
        assert out.endswith("…")

    def test_invalid_threshold_env_var_falls_back_to_zero(self, monkeypatch, app):
        """Valor não-numérico em SLOW_QUERY_THRESHOLD_MS → tratado como 0 (off)."""
        from core import database

        monkeypatch.setenv("SLOW_QUERY_THRESHOLD_MS", "isto-nao-e-numero")
        # Não levanta — devolve 0.0
        assert database._slow_query_threshold_ms() == 0.0
        with app.app_context():
            conn = database._new_conn()
            try:
                # Vanilla Connection (threshold=0 → não instala tracing)
                assert not isinstance(conn, database._TracingConnection)
            finally:
                conn.close()
