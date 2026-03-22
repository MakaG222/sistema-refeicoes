"""core/bootstrap — Inicialização da BD e arranque da app.

Responsabilidades:
  - ensure_schema(): cria tabelas base (DDL)
  - run_migrations(): aplica migrações versionadas
  - ensure_daily_backup(): backup diário
  - bootstrap_dev_accounts(): seed de contas dev (só em desenvolvimento)
"""

from __future__ import annotations

import click

from core.auth_db import PERFIS_ADMIN, PERFIS_TESTE
from core.backup import ensure_daily_backup
from core.database import db, ensure_schema
from core.migrations import run_migrations
from utils.passwords import generate_password_hash

_APP_BOOTSTRAPPED = False


def ensure_extra_schema() -> None:
    """Backward-compat: aplica migrações versionadas via run_migrations()."""
    run_migrations()


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
                    "UPDATE utilizadores SET perfil=?, Nome_completo=?, ano=?,"
                    " must_change_password=CASE WHEN ? != 'aluno'"
                    " THEN 0 ELSE must_change_password END WHERE id=?",
                    (perfil, nome, ano, perfil, row["id"]),
                )
                if stored == p.get("senha", ""):
                    conn.execute(
                        "UPDATE utilizadores SET Palavra_chave=?,"
                        " password_updated_at=datetime('now','localtime') WHERE id=?",
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
    """Inicialização segura — corre uma vez no arranque.

    1. Cria schema base (DDL)
    2. Aplica migrações versionadas pendentes
    3. Backup diário
    """
    global _APP_BOOTSTRAPPED
    if _APP_BOOTSTRAPPED:
        return
    ensure_schema()
    try:
        applied = run_migrations()
        if applied:
            app.logger.info("Migrações aplicadas no arranque: %s", ", ".join(applied))
    except Exception as exc:
        app.logger.error("Erro ao correr migrações: %s", exc)
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


@click.command("migrate")
def migrate_command() -> None:
    """Aplica migrações pendentes da base de dados."""
    applied = run_migrations()
    if applied:
        for name in applied:
            click.echo(f"  ✓ {name}")
        click.echo(f"{len(applied)} migração(ões) aplicada(s).")
    else:
        click.echo("Base de dados já está atualizada.")
