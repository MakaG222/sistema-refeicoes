"""core/bootstrap — Inicialização da BD, migrações e contas dev."""

from __future__ import annotations

import click

from core.auth_db import PERFIS_ADMIN, PERFIS_TESTE
from core.backup import ensure_daily_backup
from core.database import db, ensure_schema
from utils.passwords import generate_password_hash

_APP_BOOTSTRAPPED = False


def ensure_extra_schema() -> None:
    """Garante colunas extra e FTS5 nos utilizadores."""
    try:
        with db() as conn:
            cols = [
                r["name"]
                for r in conn.execute("PRAGMA table_info(utilizadores)").fetchall()
            ]
            if "email" not in cols:
                conn.execute("ALTER TABLE utilizadores ADD COLUMN email TEXT")
            if "telemovel" not in cols:
                conn.execute("ALTER TABLE utilizadores ADD COLUMN telemovel TEXT")
            if "is_active" not in cols:
                conn.execute(
                    "ALTER TABLE utilizadores ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1"
                )
            if "turma_id" not in cols:
                conn.execute(
                    "ALTER TABLE utilizadores ADD COLUMN turma_id INTEGER REFERENCES turmas(id)"
                )

            # Colunas estufa na tabela refeicoes
            ref_cols = [
                r["name"]
                for r in conn.execute("PRAGMA table_info(refeicoes)").fetchall()
            ]
            if "almoco_estufa" not in ref_cols:
                conn.execute(
                    "ALTER TABLE refeicoes ADD COLUMN almoco_estufa BOOLEAN DEFAULT 0"
                )
            if "jantar_estufa" not in ref_cols:
                conn.execute(
                    "ALTER TABLE refeicoes ADD COLUMN jantar_estufa BOOLEAN DEFAULT 0"
                )

            # Colunas extra na tabela licencas (entradas/saídas)
            lic_cols = [
                r["name"]
                for r in conn.execute("PRAGMA table_info(licencas)").fetchall()
            ]
            if "hora_saida" not in lic_cols:
                conn.execute("ALTER TABLE licencas ADD COLUMN hora_saida TEXT")
            if "hora_entrada" not in lic_cols:
                conn.execute("ALTER TABLE licencas ADD COLUMN hora_entrada TEXT")

            # Verificar e reparar FTS5 se necessário
            try:
                conn.execute("SELECT COUNT(*) FROM utilizadores_fts").fetchone()
            except Exception:
                print("[AVISO] FTS corrompida — a recriar...", flush=True)
                for trg in (
                    "utilizadores_ai_fts",
                    "utilizadores_ad_fts",
                    "utilizadores_au_fts",
                ):
                    try:
                        conn.execute(f"DROP TRIGGER IF EXISTS {trg}")
                    except Exception:
                        pass
                try:
                    conn.execute("DROP TABLE IF EXISTS utilizadores_fts")
                except Exception as e2:
                    print(f"[AVISO] DROP utilizadores_fts: {e2}", flush=True)

                conn.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS utilizadores_fts
USING fts5(Nome_completo, content='utilizadores', content_rowid='id')""")
                conn.execute(
                    "INSERT OR IGNORE INTO utilizadores_fts(rowid, Nome_completo) SELECT id, Nome_completo FROM utilizadores"
                )
                conn.execute("""CREATE TRIGGER IF NOT EXISTS utilizadores_ai_fts
AFTER INSERT ON utilizadores BEGIN
  INSERT INTO utilizadores_fts(rowid, Nome_completo) VALUES (NEW.id, NEW.Nome_completo);
END""")
                conn.execute("""CREATE TRIGGER IF NOT EXISTS utilizadores_ad_fts
AFTER DELETE ON utilizadores BEGIN
  INSERT INTO utilizadores_fts(utilizadores_fts, rowid) VALUES('delete', OLD.id);
END""")
                conn.execute("""CREATE TRIGGER IF NOT EXISTS utilizadores_au_fts
AFTER UPDATE OF Nome_completo ON utilizadores BEGIN
  INSERT INTO utilizadores_fts(utilizadores_fts, rowid) VALUES('delete', OLD.id);
  INSERT INTO utilizadores_fts(rowid, Nome_completo) VALUES (NEW.id, NEW.Nome_completo);
END""")
                print("[INFO] FTS recriada com sucesso.", flush=True)

            # === Migrações pontuais (tabela de controlo) ===
            conn.execute(
                "CREATE TABLE IF NOT EXISTS _migracoes (nome TEXT PRIMARY KEY, aplicada_em TEXT)"
            )
            done = {
                r["nome"]
                for r in conn.execute("SELECT nome FROM _migracoes").fetchall()
            }

            if "reis_ni_382_482" not in done:
                reis = conn.execute(
                    "SELECT id FROM utilizadores WHERE NI='382' AND ano='4'"
                ).fetchone()
                if reis:
                    conn.execute(
                        "UPDATE utilizadores SET NI='482' WHERE id=?",
                        (reis["id"],),
                    )
                    print(
                        "[MIGRAÇÃO] NI da aluna Reis corrigido: 382→482",
                        flush=True,
                    )
                conn.execute(
                    "INSERT INTO _migracoes VALUES('reis_ni_382_482', datetime('now','localtime'))"
                )

            if "rafaela_nii_20223_21223" not in done:
                try:
                    cur = conn.execute(
                        "UPDATE utilizadores SET NII='21223' WHERE NII='20223'"
                    )
                    if cur.rowcount:
                        print(
                            f"[MIGRAÇÃO] NII Rafaela Fernandes corrigido: 20223→21223 (linhas={cur.rowcount})",
                            flush=True,
                        )
                except Exception as exc:
                    print(
                        f"[AVISO] Migração Rafaela NII 20223→21223 falhou: {exc}",
                        flush=True,
                    )
                conn.execute(
                    "INSERT INTO _migracoes VALUES('rafaela_nii_20223_21223', datetime('now','localtime'))"
                )

            if "reset_creds_nii_v2" not in done:
                alunos_reset = conn.execute(
                    "SELECT id, NII FROM utilizadores WHERE perfil='aluno'"
                ).fetchall()
                for al in alunos_reset:
                    al = dict(al)
                    nii = al["NII"]
                    if not nii:
                        continue
                    pw_hash = generate_password_hash(nii)
                    conn.execute(
                        "UPDATE utilizadores SET Palavra_chave=?, must_change_password=1 WHERE id=?",
                        (pw_hash, al["id"]),
                    )
                conn.execute(
                    "INSERT INTO _migracoes VALUES('reset_creds_nii_v2', datetime('now','localtime'))"
                )
                print(
                    "[MIGRAÇÃO] Credenciais alunos resetadas: pw=hash(NII), must_change=1",
                    flush=True,
                )

            conn.commit()
    except Exception as e:
        print(f"[ERRO] ensure_extra_schema: {e}", flush=True)


def bootstrap_dev_accounts(conn=None, *, is_production: bool = False) -> None:
    """Sincroniza PERFIS_ADMIN/PERFIS_TESTE para a BD em desenvolvimento."""
    if is_production:
        return
    perfis = {**PERFIS_ADMIN, **PERFIS_TESTE}
    if not perfis:
        return
    owns_conn = conn is None
    try:
        if owns_conn:
            conn = db()
        cols = {
            r["name"]
            for r in conn.execute("PRAGMA table_info(utilizadores)").fetchall()
        }
        if not cols:
            return
        for nii, p in perfis.items():
            row = conn.execute(
                "SELECT id, Palavra_chave FROM utilizadores WHERE NII=?", (nii,)
            ).fetchone()
            pw_hash = generate_password_hash(p.get("senha", ""))
            nome = p.get("nome", nii)
            perfil = p.get("perfil", "aluno")
            ano = str(p.get("ano", "") or "")
            if row is None:
                conn.execute(
                    """INSERT INTO utilizadores
                    (NII,NI,Nome_completo,Palavra_chave,ano,perfil,must_change_password,password_updated_at,is_active)
                    VALUES (?,?,?,?,?,?,0,datetime('now','localtime'),1)""",
                    (nii, nii, nome, pw_hash, ano, perfil),
                )
            else:
                stored = row["Palavra_chave"] or ""
                conn.execute(
                    "UPDATE utilizadores SET perfil=?, Nome_completo=?, ano=?, must_change_password=CASE WHEN ? != 'aluno' THEN 0 ELSE must_change_password END WHERE id=?",
                    (perfil, nome, ano, perfil, row["id"]),
                )
                if stored == p.get("senha", ""):
                    conn.execute(
                        "UPDATE utilizadores SET Palavra_chave=?, password_updated_at=datetime('now','localtime') WHERE id=?",
                        (pw_hash, row["id"]),
                    )
        if owns_conn:
            conn.commit()
    except Exception as exc:
        print(f"[AVISO] bootstrap_dev_accounts falhou: {exc}", flush=True)
    finally:
        if owns_conn and conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def init_app_once(app) -> None:
    """Inicialização segura — corre uma vez no arranque."""
    global _APP_BOOTSTRAPPED
    if _APP_BOOTSTRAPPED:
        return
    ensure_schema()
    ensure_extra_schema()
    try:
        ensure_daily_backup()
    except Exception as exc:
        app.logger.warning("Backup no bootstrap falhou: %s", exc)
    _APP_BOOTSTRAPPED = True


@click.command("seed-dev")
def seed_dev_command() -> None:
    """Seed development/test accounts (PERFIS_ADMIN + PERFIS_TESTE)."""
    with db() as conn:
        bootstrap_dev_accounts(conn=conn)
        conn.commit()
    click.echo("Dev accounts seeded.")
