"""core/bootstrap — Inicialização da BD e arranque da app.

Responsabilidades:
  - ensure_schema(): cria tabelas base (DDL)
  - run_migrations(): aplica migrações versionadas
  - ensure_daily_backup(): backup diário
  - bootstrap_dev_accounts(): seed de contas dev (só em desenvolvimento)
"""

from __future__ import annotations

import logging

import click

log = logging.getLogger(__name__)

from core.auth_db import PERFIS_ADMIN, PERFIS_TESTE
from core.backup import (
    ensure_daily_backup,
    list_backups,
    restore_backup,
    validate_backup,
)
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
            # Alunos de teste forçam mudança de password; contas de sistema não
            must_change = 1 if perfil == "aluno" else 0
            if row is None:
                conn.execute(
                    """INSERT INTO utilizadores
                    (NII,NI,Nome_completo,Palavra_chave,ano,perfil,must_change_password,password_updated_at,is_active)
                    VALUES (?,?,?,?,?,?,?,datetime('now','localtime'),1)""",
                    (nii, nii, nome, pw_hash, ano, perfil, must_change),
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
        log.warning("bootstrap_dev_accounts falhou: %s", exc)
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


@click.command("backup")
def backup_command() -> None:
    """Cria um backup manual da base de dados."""
    from core.backup import do_backup

    if do_backup():
        click.echo("Backup criado com sucesso.")
    else:
        click.echo("Falha ao criar backup.", err=True)


@click.command("backup-list")
def backup_list_command() -> None:
    """Lista backups disponíveis."""
    backups = list_backups()
    if not backups:
        click.echo("Nenhum backup encontrado.")
        return
    click.echo(f"{'Ficheiro':<45} {'Tamanho':>10} {'Data':>20}")
    click.echo("-" * 78)
    for b in backups:
        click.echo(f"{b['name']:<45} {b['size_mb']:>8.2f} MB {b['modified']:>20}")
    click.echo(f"\n{len(backups)} backup(s) disponíveis.")


@click.command("restore")
@click.argument("backup_file")
@click.option("--yes", "-y", is_flag=True, help="Confirmar restauro sem prompt.")
def restore_command(backup_file: str, yes: bool) -> None:
    """Restaura a BD a partir de BACKUP_FILE.

    Cria automaticamente um backup de segurança antes do restauro.
    A app deve ser reiniciada após o restauro.
    """
    import os

    if not os.path.exists(backup_file):
        # Tentar na pasta de backups
        from core.constants import BACKUP_DIR

        candidate = os.path.join(BACKUP_DIR, backup_file)
        if os.path.exists(candidate):
            backup_file = candidate
        else:
            click.echo(f"Ficheiro não encontrado: {backup_file}", err=True)
            raise SystemExit(1)

    valid, reason = validate_backup(backup_file)
    if not valid:
        click.echo(f"Backup inválido: {reason}", err=True)
        raise SystemExit(1)

    click.echo(f"Ficheiro: {backup_file}")
    if not yes:
        click.confirm("Restaurar? A BD actual será substituída", abort=True)

    ok, msg = restore_backup(backup_file)
    if ok:
        click.echo(f"✓ {msg}")
        click.echo("⚠  Reinicia a aplicação para usar a BD restaurada.")
    else:
        click.echo(f"✗ {msg}", err=True)
        raise SystemExit(1)


@click.command("vacuum")
def vacuum_command() -> None:
    """Reclama espaço de páginas livres + reanalisa estatísticas (manutenção mensal).

    Faz `wal_checkpoint(TRUNCATE)` → `VACUUM` → `PRAGMA optimize`.

    VACUUM adquire lock exclusivo e re-escreve o ficheiro inteiro — operação
    cara em BDs grandes. Correr em janela de baixo tráfego (ex.: 03:00,
    1× por mês). Outras conexões esperam até 8s (`busy_timeout`) antes de
    falhar com "database is locked".

    Exemplos:
      flask vacuum                              # local / Docker exec
      docker compose exec app flask vacuum      # produção via docker
    """
    from core.database import (
        db_file_size_bytes,
        optimize_database,
        vacuum_database,
    )

    size_before = db_file_size_bytes()
    click.echo(
        f"Tamanho antes: {size_before:,} bytes ({size_before / 1024 / 1024:.2f} MB)"
    )
    click.echo("A correr VACUUM (pode demorar)…")

    before, after = vacuum_database()
    freed = max(0, before - after)
    freed_pct = (freed / before * 100) if before > 0 else 0.0

    click.echo(f"Tamanho depois: {after:,} bytes ({after / 1024 / 1024:.2f} MB)")
    click.echo(f"Libertado: {freed:,} bytes ({freed_pct:.2f}%)")

    click.echo("A correr PRAGMA optimize…")
    if optimize_database():
        click.echo("✓ Optimize OK")
    else:
        click.echo("✗ Optimize falhou (ver logs)", err=True)
        raise SystemExit(1)

    click.echo("✓ Manutenção concluída.")
