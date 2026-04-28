"""core/migrations — Migrações versionadas da base de dados.

Cada migração é uma função registada com um nome único.
Migrações aplicadas são guardadas na tabela `_migracoes`.
Ordem de execução: pela posição na lista MIGRATIONS.
"""

from __future__ import annotations

import logging
import sqlite3

from core.database import db
from utils.passwords import generate_password_hash

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tabela de controlo
# ---------------------------------------------------------------------------

_CREATE_TABLE = (
    "CREATE TABLE IF NOT EXISTS _migracoes (nome TEXT PRIMARY KEY, aplicada_em TEXT)"
)


def _applied(conn: sqlite3.Connection) -> set[str]:
    """Retorna nomes de migrações já aplicadas."""
    conn.execute(_CREATE_TABLE)
    return {r["nome"] for r in conn.execute("SELECT nome FROM _migracoes").fetchall()}


def _mark(conn: sqlite3.Connection, name: str) -> None:
    conn.execute(
        "INSERT INTO _migracoes VALUES(?, datetime('now','localtime'))", (name,)
    )


# ---------------------------------------------------------------------------
# Migrações individuais
# ---------------------------------------------------------------------------


def _add_email_telemovel(conn: sqlite3.Connection) -> None:
    """Adiciona colunas email e telemovel à tabela utilizadores."""
    cols = [
        r["name"] for r in conn.execute("PRAGMA table_info(utilizadores)").fetchall()
    ]
    if "email" not in cols:
        conn.execute("ALTER TABLE utilizadores ADD COLUMN email TEXT")
    if "telemovel" not in cols:
        conn.execute("ALTER TABLE utilizadores ADD COLUMN telemovel TEXT")


def _add_is_active(conn: sqlite3.Connection) -> None:
    """Adiciona coluna is_active à tabela utilizadores."""
    cols = [
        r["name"] for r in conn.execute("PRAGMA table_info(utilizadores)").fetchall()
    ]
    if "is_active" not in cols:
        conn.execute(
            "ALTER TABLE utilizadores ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1"
        )


def _add_turma_id(conn: sqlite3.Connection) -> None:
    """Adiciona coluna turma_id à tabela utilizadores."""
    cols = [
        r["name"] for r in conn.execute("PRAGMA table_info(utilizadores)").fetchall()
    ]
    if "turma_id" not in cols:
        conn.execute(
            "ALTER TABLE utilizadores ADD COLUMN turma_id INTEGER REFERENCES turmas(id)"
        )


def _add_estufa_columns(conn: sqlite3.Connection) -> None:
    """Adiciona colunas almoco_estufa e jantar_estufa à tabela refeicoes."""
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(refeicoes)").fetchall()]
    if "almoco_estufa" not in cols:
        conn.execute("ALTER TABLE refeicoes ADD COLUMN almoco_estufa BOOLEAN DEFAULT 0")
    if "jantar_estufa" not in cols:
        conn.execute("ALTER TABLE refeicoes ADD COLUMN jantar_estufa BOOLEAN DEFAULT 0")


def _add_licenca_horas(conn: sqlite3.Connection) -> None:
    """Adiciona colunas hora_saida e hora_entrada à tabela licencas."""
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(licencas)").fetchall()]
    if "hora_saida" not in cols:
        conn.execute("ALTER TABLE licencas ADD COLUMN hora_saida TEXT")
    if "hora_entrada" not in cols:
        conn.execute("ALTER TABLE licencas ADD COLUMN hora_entrada TEXT")


def _add_ausencia_horarios(conn: sqlite3.Connection) -> None:
    """Adiciona colunas hora_inicio, hora_fim, estufa_almoco, estufa_jantar à tabela ausencias."""
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(ausencias)").fetchall()]
    if "hora_inicio" not in cols:
        conn.execute("ALTER TABLE ausencias ADD COLUMN hora_inicio TEXT")
    if "hora_fim" not in cols:
        conn.execute("ALTER TABLE ausencias ADD COLUMN hora_fim TEXT")
    if "estufa_almoco" not in cols:
        conn.execute("ALTER TABLE ausencias ADD COLUMN estufa_almoco INTEGER DEFAULT 0")
    if "estufa_jantar" not in cols:
        conn.execute("ALTER TABLE ausencias ADD COLUMN estufa_jantar INTEGER DEFAULT 0")


def _add_dieta_padrao(conn: sqlite3.Connection) -> None:
    """Adiciona coluna dieta_padrao ('Normal'|'Vegetariano'|'Dieta') à tabela utilizadores.

    Passa a ser a preferência persistente do aluno — usada como default no
    autopreenchimento em vez de hard-coded "Normal". Pode ser sempre
    sobreposta por refeição através do form normal de edição.
    """
    cols = [
        r["name"] for r in conn.execute("PRAGMA table_info(utilizadores)").fetchall()
    ]
    if "dieta_padrao" not in cols:
        conn.execute(
            "ALTER TABLE utilizadores ADD COLUMN dieta_padrao TEXT "
            "NOT NULL DEFAULT 'Normal' "
            "CHECK(dieta_padrao IN ('Normal','Vegetariano','Dieta'))"
        )


def _repair_fts(conn: sqlite3.Connection) -> None:
    """Verifica e repara FTS5 se corrompida."""
    try:
        conn.execute("SELECT COUNT(*) FROM utilizadores_fts").fetchone()
        return  # FTS OK
    except Exception:
        pass

    log.warning("FTS corrompida — a recriar...")
    for trg in (
        "utilizadores_ai_fts",
        "utilizadores_ad_fts",
        "utilizadores_au_fts",
    ):
        try:
            conn.execute(f"DROP TRIGGER IF EXISTS {trg}")  # nosec B608
        except Exception:
            pass
    try:
        conn.execute("DROP TABLE IF EXISTS utilizadores_fts")
    except Exception as e:
        log.warning("DROP utilizadores_fts: %s", e)

    conn.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS utilizadores_fts"
        " USING fts5(Nome_completo, content='utilizadores', content_rowid='id')"
    )
    conn.execute(
        "INSERT OR IGNORE INTO utilizadores_fts(rowid, Nome_completo)"
        " SELECT id, Nome_completo FROM utilizadores"
    )
    conn.execute(
        "CREATE TRIGGER IF NOT EXISTS utilizadores_ai_fts"
        " AFTER INSERT ON utilizadores BEGIN"
        "  INSERT INTO utilizadores_fts(rowid, Nome_completo)"
        " VALUES (NEW.id, NEW.Nome_completo); END"
    )
    conn.execute(
        "CREATE TRIGGER IF NOT EXISTS utilizadores_ad_fts"
        " AFTER DELETE ON utilizadores BEGIN"
        "  INSERT INTO utilizadores_fts(utilizadores_fts, rowid)"
        " VALUES('delete', OLD.id); END"
    )
    conn.execute(
        "CREATE TRIGGER IF NOT EXISTS utilizadores_au_fts"
        " AFTER UPDATE OF Nome_completo ON utilizadores BEGIN"
        "  INSERT INTO utilizadores_fts(utilizadores_fts, rowid)"
        " VALUES('delete', OLD.id);"
        "  INSERT INTO utilizadores_fts(rowid, Nome_completo)"
        " VALUES (NEW.id, NEW.Nome_completo); END"
    )
    log.info("FTS recriada com sucesso.")


# ---------------------------------------------------------------------------
# Migrações de dados (one-off data fixes)
# ---------------------------------------------------------------------------


def _fix_reis_ni(conn: sqlite3.Connection) -> None:
    """Corrige NI da aluna Reis: 382 → 482."""
    reis = conn.execute(
        "SELECT id FROM utilizadores WHERE NI='382' AND ano='4'"
    ).fetchone()
    if reis:
        conn.execute("UPDATE utilizadores SET NI='482' WHERE id=?", (reis["id"],))
        log.info("NI da aluna Reis corrigido: 382→482")


def _fix_rafaela_nii(conn: sqlite3.Connection) -> None:
    """Corrige NII da Rafaela Fernandes: 20223 → 21223."""
    try:
        cur = conn.execute("UPDATE utilizadores SET NII='21223' WHERE NII='20223'")
        if cur.rowcount:
            log.info("NII Rafaela Fernandes corrigido: 20223→21223")
    except Exception as exc:
        log.warning("Migração Rafaela NII falhou: %s", exc)


def _add_checkin_tokens(conn: sqlite3.Connection) -> None:
    """Cria tabelas checkin_tokens + checkin_log para QR rotativo (PR2).

    O paradigma anterior era estático: cada aluno tinha um QR fixo (NII)
    que era scaneado pelo kiosk do oficial. Inverte-se: oficial mostra um
    QR rotativo (URL com token TTL ~60s) que o aluno scaneia com a câmara
    do telemóvel — abre /checkin?token=… e regista entrada/saída.
    """
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS checkin_tokens (
          token       TEXT PRIMARY KEY,
          created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime')),
          expires_at  TEXT NOT NULL,
          created_by  INTEGER NOT NULL REFERENCES utilizadores(id) ON DELETE CASCADE,
          tipo        TEXT NOT NULL CHECK(tipo IN ('entrada','saida','auto'))
        );
        CREATE INDEX IF NOT EXISTS idx_checkin_tokens_exp ON checkin_tokens(expires_at);

        CREATE TABLE IF NOT EXISTS checkin_log (
          id            INTEGER PRIMARY KEY AUTOINCREMENT,
          utilizador_id INTEGER NOT NULL REFERENCES utilizadores(id) ON DELETE CASCADE,
          token         TEXT NOT NULL,
          tipo          TEXT NOT NULL CHECK(tipo IN ('entrada','saida')),
          ts            TEXT NOT NULL DEFAULT (datetime('now','localtime')),
          ip            TEXT,
          user_agent    TEXT,
          UNIQUE(utilizador_id, token)
        );
        CREATE INDEX IF NOT EXISTS idx_checkin_log_uid_ts ON checkin_log(utilizador_id, ts);
        CREATE INDEX IF NOT EXISTS idx_checkin_log_token ON checkin_log(token);
        """
    )


def _add_reset_code(conn: sqlite3.Connection) -> None:
    """Adiciona colunas reset_code + reset_expires à tabela utilizadores.

    Suporta o fluxo de password-reset por admin: admin gera um código único
    válido durante 24h, utilizador faz login com ele e é redirigido para
    /auth/change-password. Código invalida-se no uso (single-use).
    """
    cols = [
        r["name"] for r in conn.execute("PRAGMA table_info(utilizadores)").fetchall()
    ]
    if "reset_code" not in cols:
        conn.execute("ALTER TABLE utilizadores ADD COLUMN reset_code TEXT")
    if "reset_expires" not in cols:
        conn.execute("ALTER TABLE utilizadores ADD COLUMN reset_expires TEXT")


def _reset_aluno_creds(conn: sqlite3.Connection) -> None:
    """Reset credenciais dos alunos: password=hash(NII), must_change=1."""
    alunos = conn.execute(
        "SELECT id, NII FROM utilizadores WHERE perfil='aluno'"
    ).fetchall()
    count = 0
    for al in alunos:
        al = dict(al)
        nii = al["NII"]
        if not nii:
            continue
        pw_hash = generate_password_hash(nii)
        conn.execute(
            "UPDATE utilizadores SET Palavra_chave=?, must_change_password=1 WHERE id=?",
            (pw_hash, al["id"]),
        )
        count += 1
    log.info("Credenciais de %d alunos resetadas: pw=hash(NII), must_change=1", count)


# ---------------------------------------------------------------------------
# Registo ordenado de migrações
# ---------------------------------------------------------------------------

MIGRATIONS: list[tuple[str, callable]] = [
    # Schema migrations (ALTER TABLE)
    ("001_add_email_telemovel", _add_email_telemovel),
    ("002_add_is_active", _add_is_active),
    ("003_add_turma_id", _add_turma_id),
    ("004_add_estufa_columns", _add_estufa_columns),
    ("005_add_licenca_horas", _add_licenca_horas),
    ("006_add_ausencia_horarios", _add_ausencia_horarios),
    ("007_add_dieta_padrao", _add_dieta_padrao),
    ("008_add_reset_code", _add_reset_code),
    ("009_add_checkin_tokens", _add_checkin_tokens),
    # Data migrations (one-off fixes) — preserva nomes antigos para compat
    ("reis_ni_382_482", _fix_reis_ni),
    ("rafaela_nii_20223_21223", _fix_rafaela_nii),
    ("reset_creds_nii_v2", _reset_aluno_creds),
]

# Checks que correm sempre (não versionados) — ex: FTS pode corromper a qualquer momento
ALWAYS_RUN: list[callable] = [_repair_fts]


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def run_migrations(conn: sqlite3.Connection | None = None) -> list[str]:
    """Aplica migrações pendentes e checks always-run. Retorna nomes aplicados."""
    owns_conn = conn is None
    try:
        if owns_conn:
            conn = db()
        # Always-run checks (ex: FTS repair — pode corromper entre arranques)
        for fn in ALWAYS_RUN:
            try:
                fn(conn)
            except Exception as exc:
                log.warning("Always-run check %s falhou: %s", fn.__name__, exc)

        applied = _applied(conn)
        newly_applied: list[str] = []

        for name, fn in MIGRATIONS:
            if name in applied:
                continue
            log.info("A aplicar migração: %s", name)
            fn(conn)
            _mark(conn, name)
            newly_applied.append(name)

        conn.commit()
        if newly_applied:
            log.info("Migrações aplicadas: %s", ", ".join(newly_applied))
        return newly_applied
    except Exception as exc:
        log.error("Erro ao correr migrações: %s", exc)
        return []
    finally:
        if owns_conn:
            try:
                conn.close()
            except Exception:
                pass
