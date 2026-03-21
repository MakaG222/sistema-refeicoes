"""Funções de backup da base de dados."""

import logging
import shutil
from datetime import datetime, timedelta
from pathlib import Path

import core.constants
from core.constants import BACKUP_DIR, BACKUP_RETENCAO_DIAS


def ensure_daily_backup():
    """Cria backup automático 1x por dia (nome inclui data)."""
    try:
        ts_date = datetime.now().strftime("%Y%m%d")
        stem = Path(core.constants.BASE_DADOS).stem
        dest = Path(BACKUP_DIR) / f"{stem}_{ts_date}.db"
        if not dest.exists() and Path(core.constants.BASE_DADOS).exists():
            shutil.copy2(core.constants.BASE_DADOS, dest)
            print(f"Backup diário criado: {dest}")
    except Exception as e:
        print(f"Falha a criar backup diário: {e}")


def limpar_backups_antigos():
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
                pass
        if removidos:
            print(
                f"{removidos} backup(s) antigo(s) removido(s) (retenção: {BACKUP_RETENCAO_DIAS} dias)."
            )
    except Exception as e:
        logging.warning(f"limpar_backups_antigos falhou: {e}")


def do_backup() -> bool:
    try:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        dest = Path(BACKUP_DIR) / f"{Path(core.constants.BASE_DADOS).stem}_{ts}.db"
        shutil.copy2(core.constants.BASE_DADOS, dest)
        print(f"Backup: {dest}")
        return True
    except Exception as e:
        print(f"Falha no backup: {e}")
        return False
