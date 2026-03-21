"""Camada de acesso à base de dados SQLite."""

import sqlite3

import core.constants
from core.schema import SCHEMA_SQL


def _new_conn() -> sqlite3.Connection:
    """Cria uma nova conexão SQLite com pragmas de performance."""
    conn = sqlite3.connect(core.constants.BASE_DADOS)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=8000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-4000")  # 4 MB cache
    return conn


def db() -> sqlite3.Connection:
    """Devolve conexão SQLite reutilizável por request (via Flask g) ou nova."""
    try:
        from flask import g, has_request_context

        if has_request_context():
            conn = getattr(g, "_sr_db", None)
            if conn is None:
                conn = _new_conn()
                g._sr_db = conn
            return conn
    except ImportError:
        pass
    return _new_conn()


def close_request_db(exc=None) -> None:
    """Fecha a conexão da request (chamado pelo teardown do Flask)."""
    try:
        from flask import g

        conn = getattr(g, "_sr_db", None)
        if conn is not None:
            g._sr_db = None
            try:
                conn.close()
            except Exception:
                pass
    except ImportError:
        pass


def wal_checkpoint() -> None:
    """Força checkpoint do WAL para libertar espaço no ficheiro -wal."""
    try:
        conn = _new_conn()
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.close()
    except Exception:
        pass


def sqlite_quick_check() -> bool:
    try:
        with db() as conn:
            row = conn.execute("PRAGMA quick_check").fetchone()
            return bool(row and row[0] == "ok")
    except Exception:
        return False


def ensure_schema():
    with db() as conn:
        fts_ok = False
        try:
            conn.execute("SELECT COUNT(*) FROM utilizadores_fts").fetchone()
            fts_ok = True
        except Exception:
            pass

        if not fts_ok:
            try:
                conn.execute("DROP TABLE IF EXISTS utilizadores_fts")
            except sqlite3.Error:
                pass

        conn.executescript(SCHEMA_SQL)

        if not fts_ok:
            try:
                conn.execute(
                    "INSERT INTO utilizadores_fts(utilizadores_fts) VALUES('rebuild')"
                )
            except sqlite3.Error:
                pass
        conn.commit()
