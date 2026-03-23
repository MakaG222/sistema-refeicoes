"""Funções de backup e restauro da base de dados."""

import logging
import shutil
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import core.constants
from core.constants import BACKUP_DIR, BACKUP_RETENCAO_DIAS

log = logging.getLogger(__name__)


# ── Backup ────────────────────────────────────────────────────────────────


def ensure_daily_backup() -> None:
    """Cria backup automático 1x por dia (nome inclui data).

    Também limpa backups além do período de retenção.
    """
    try:
        ts_date = datetime.now().strftime("%Y%m%d")
        stem = Path(core.constants.BASE_DADOS).stem
        dest = Path(BACKUP_DIR) / f"{stem}_{ts_date}.db"
        if not dest.exists() and Path(core.constants.BASE_DADOS).exists():
            shutil.copy2(core.constants.BASE_DADOS, dest)
            log.info("Backup diário criado: %s", dest)
    except Exception as e:
        log.warning("Falha a criar backup diário: %s", e)

    # Limpeza automática de backups antigos
    try:
        limpar_backups_antigos()
    except Exception as e:
        log.warning("Limpeza de backups falhou: %s", e)


def limpar_backups_antigos() -> None:
    """Remove backups mais antigos que BACKUP_RETENCAO_DIAS dias."""
    if BACKUP_RETENCAO_DIAS is None:
        return
    try:
        limite = datetime.now() - timedelta(days=BACKUP_RETENCAO_DIAS)
        removidos = 0
        for f in Path(BACKUP_DIR).glob("*.db"):
            try:
                if datetime.fromtimestamp(f.stat().st_mtime) < limite:
                    f.unlink()
                    removidos += 1
            except Exception:
                log.exception("limpar_backups_antigos: falha ao remover %s", f)
        if removidos:
            log.info(
                "%d backup(s) antigo(s) removido(s) (retenção: %d dias).",
                removidos,
                BACKUP_RETENCAO_DIAS,
            )
    except Exception as e:
        log.warning("limpar_backups_antigos falhou: %s", e)


def do_backup() -> bool:
    """Cria backup manual com timestamp (YYYYMMDD_HHMMSS)."""
    try:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        dest = Path(BACKUP_DIR) / f"{Path(core.constants.BASE_DADOS).stem}_{ts}.db"
        shutil.copy2(core.constants.BASE_DADOS, dest)
        log.info("Backup manual criado: %s", dest)
        return True
    except Exception as e:
        log.warning("Falha no backup manual: %s", e)
        return False


def list_backups() -> list[dict]:
    """Lista backups disponíveis, do mais recente ao mais antigo.

    Returns:
        Lista de dicts com keys: name, path, size_mb, modified.
    """
    backups = []
    try:
        for f in sorted(
            Path(BACKUP_DIR).glob("*.db"), key=lambda p: p.stat().st_mtime, reverse=True
        ):
            stat = f.stat()
            backups.append(
                {
                    "name": f.name,
                    "path": str(f),
                    "size_mb": round(stat.st_size / (1024 * 1024), 2),
                    "modified": datetime.fromtimestamp(stat.st_mtime).strftime(
                        "%Y-%m-%d %H:%M:%S"
                    ),
                }
            )
    except Exception as e:
        log.warning("Erro ao listar backups: %s", e)
    return backups


# ── Restauro ──────────────────────────────────────────────────────────────


def validate_backup(backup_path: str) -> tuple[bool, str]:
    """Valida que um ficheiro de backup é uma BD SQLite válida.

    Returns:
        (True, "") se válido, (False, "motivo") se inválido.
    """
    p = Path(backup_path)
    if not p.exists():
        return False, "Ficheiro não encontrado."
    if not p.suffix == ".db":
        return False, "Ficheiro não é .db."
    if p.stat().st_size == 0:
        return False, "Ficheiro vazio."
    try:
        conn = sqlite3.connect(str(p))
        row = conn.execute("PRAGMA quick_check").fetchone()
        # Verificar que tem a tabela principal
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        conn.close()
        if not row or row[0] != "ok":
            return False, "Integridade SQLite falhou."
        if "utilizadores" not in tables:
            return (
                False,
                "Tabela 'utilizadores' não encontrada — não é uma BD do sistema.",
            )
        return True, ""
    except sqlite3.Error as e:
        return False, f"Erro SQLite: {e}"
    except Exception as e:
        return False, f"Erro inesperado: {e}"


def restore_backup(backup_path: str) -> tuple[bool, str]:
    """Restaura a BD a partir de um ficheiro de backup.

    Procedimento:
      1. Valida integridade do backup
      2. Cria backup de segurança do estado actual (antes do restauro)
      3. Substitui a BD activa pelo backup

    Returns:
        (True, "mensagem") se sucesso, (False, "motivo") se falhou.
    """
    valid, reason = validate_backup(backup_path)
    if not valid:
        return False, f"Backup inválido: {reason}"

    db_path = core.constants.BASE_DADOS

    # 1. Backup de segurança do estado actual (antes do restauro)
    try:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        safety_dest = Path(BACKUP_DIR) / f"pre_restauro_{ts}.db"
        if Path(db_path).exists():
            shutil.copy2(db_path, safety_dest)
            log.info("Backup de segurança pré-restauro: %s", safety_dest)
    except Exception as e:
        return False, f"Falha ao criar backup de segurança: {e}"

    # 2. Substituir BD activa pelo backup
    try:
        shutil.copy2(backup_path, db_path)
        # Remover ficheiros WAL/SHM orphaned
        for suffix in ("-wal", "-shm"):
            wal = Path(db_path + suffix)
            if wal.exists():
                wal.unlink()
        log.info("BD restaurada a partir de: %s", backup_path)
        return (
            True,
            f"BD restaurada com sucesso. Backup de segurança em {safety_dest.name}.",
        )
    except Exception as e:
        log.error("Falha no restauro: %s", e)
        return False, f"Falha ao copiar backup: {e}"
