"""Camada de acesso à base de dados SQLite."""

from __future__ import annotations

import logging
import sqlite3

log = logging.getLogger(__name__)

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


def close_request_db(exc: BaseException | None = None) -> None:
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
        log.exception("wal_checkpoint: falha ao executar checkpoint")


def db_file_size_bytes() -> int:
    """Devolve tamanho actual da BD em bytes (0 se ficheiro ainda não existe)."""
    import os

    try:
        return os.path.getsize(core.constants.BASE_DADOS)
    except OSError:
        return 0


def vacuum_database() -> tuple[int, int]:
    """Reclama espaço de páginas livres (PRAGMA VACUUM) e devolve (antes, depois).

    VACUUM não pode correr dentro de uma transacção e adquire um lock
    exclusivo — corre numa conexão isolada, fora do contexto de request.
    Em WAL mode, fazemos primeiro `wal_checkpoint(TRUNCATE)` para libertar
    o WAL e depois VACUUM para reorganizar páginas do .db principal.

    Operação cara para BDs grandes (cópia inteira) — agendar em janela
    de baixo tráfego (ex.: 03:00 mensal). `busy_timeout` controla espera
    por outras conexões.

    Devolve `(size_before, size_after)` em bytes. Se algo falhar, devolve
    `(size_before, size_before)` e regista exception (não levanta).
    """
    size_before = db_file_size_bytes()
    conn = None
    try:
        conn = _new_conn()
        # Liberta WAL primeiro — VACUUM precisa de single-writer lock.
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        # VACUUM: rebuild completo do ficheiro, reclama espaço de páginas livres.
        conn.execute("VACUUM")
    except Exception:
        log.exception("vacuum_database: falha durante VACUUM")
        return size_before, size_before
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    return size_before, db_file_size_bytes()


def optimize_database() -> bool:
    """Corre `PRAGMA optimize` (barato — analyse selectivo das tabelas grandes).

    Pode correr semanal/diário sem custo significativo. SQLite decide
    internamente o que vale a pena reanalisar com base em heurísticas.
    Devolve True se executou sem erro, False caso contrário.
    """
    conn = None
    try:
        conn = _new_conn()
        conn.execute("PRAGMA optimize")
        return True
    except Exception:
        log.exception("optimize_database: falha em PRAGMA optimize")
        return False
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def sqlite_quick_check() -> bool:
    try:
        with db() as conn:
            row = conn.execute("PRAGMA quick_check").fetchone()
            return bool(row and row[0] == "ok")
    except Exception:
        log.exception("sqlite_quick_check: falha na verificação")
        return False


def ensure_schema() -> None:
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
