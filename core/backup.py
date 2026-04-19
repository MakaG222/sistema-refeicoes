"""Funções de backup e restauro da base de dados."""

import logging
import os
import shlex
import shutil
import sqlite3
import subprocess  # nosec B404 — uso restrito a upload_offsite, sem shell.
from datetime import datetime, timedelta
from pathlib import Path

import core.constants
from core.constants import BACKUP_DIR, BACKUP_RETENCAO_DIAS
from core.notifications import notify

log = logging.getLogger(__name__)


# ── Backup ────────────────────────────────────────────────────────────────


def ensure_daily_backup() -> None:
    """Cria backup automático 1x por dia (nome inclui data).

    Também limpa backups além do período de retenção e, se `BACKUP_UPLOAD_CMD`
    estiver configurado, replica o backup para destino offsite.
    """
    dest: Path | None = None
    created = False
    try:
        ts_date = datetime.now().strftime("%Y%m%d")
        stem = Path(core.constants.BASE_DADOS).stem
        dest = Path(BACKUP_DIR) / f"{stem}_{ts_date}.db"
        if not dest.exists() and Path(core.constants.BASE_DADOS).exists():
            shutil.copy2(core.constants.BASE_DADOS, dest)
            log.info("Backup diário criado: %s", dest)
            created = True
    except Exception as e:
        log.warning("Falha a criar backup diário: %s", e)
        notify(
            "Backup diário falhou",
            f"Não foi possível criar o backup diário local: {e}",
            severity="error",
        )

    # Replicação offsite (best-effort): se criou novo e BACKUP_UPLOAD_CMD definido.
    if created and dest is not None and os.getenv("BACKUP_UPLOAD_CMD", "").strip():
        if not upload_offsite(str(dest)):
            notify(
                "Upload offsite falhou",
                f"Backup local ok, mas replicação offsite falhou para {dest.name}.",
                severity="warning",
            )

    # Limpeza automática de backups antigos
    try:
        limpar_backups_antigos()
    except Exception as e:
        log.warning("Limpeza de backups falhou: %s", e)


def upload_offsite(path: str) -> bool:
    """Executa comando externo configurável para replicar backup offsite.

    Lê o template de `BACKUP_UPLOAD_CMD` — o token `{path}` é substituído pelo
    caminho do backup. O comando é executado sem shell (tokens via `shlex`),
    evitando injecção de shell.

    Exemplos:
        BACKUP_UPLOAD_CMD='rclone copy {path} remote:backups/'
        BACKUP_UPLOAD_CMD='aws s3 cp {path} s3://meu-bucket/backups/'
        BACKUP_UPLOAD_CMD='rsync -a {path} backup-host:/srv/backups/'

    Timeout configurável via `BACKUP_UPLOAD_TIMEOUT` (default 300s).

    Returns:
        True se o comando executou com sucesso (exit code 0), False caso contrário.
        Se `BACKUP_UPLOAD_CMD` não estiver definido, retorna False sem logs.
    """
    tpl = os.getenv("BACKUP_UPLOAD_CMD", "").strip()
    if not tpl:
        return False
    try:
        args = [a.replace("{path}", path) for a in shlex.split(tpl)]
        if not args:
            log.warning("BACKUP_UPLOAD_CMD vazio após shlex.split — a ignorar.")
            return False
        try:
            timeout = int(os.getenv("BACKUP_UPLOAD_TIMEOUT", "300"))
        except ValueError:
            timeout = 300
        # Template vem de env var do operador; args foram passados por shlex.split
        # (sem shell=True). A substituição de {path} é feita por valor, não por
        # expansão de shell.
        result = subprocess.run(  # noqa: S603  # nosec B603
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if result.returncode == 0:
            log.info("Upload offsite ok: %s", path)
            return True
        log.warning(
            "Upload offsite falhou (rc=%s) stdout=%r stderr=%r",
            result.returncode,
            (result.stdout or "")[-500:],
            (result.stderr or "")[-500:],
        )
        return False
    except subprocess.TimeoutExpired:
        log.exception("Upload offsite expirou timeout")
        return False
    except FileNotFoundError:
        log.exception("BACKUP_UPLOAD_CMD: executável não encontrado")
        return False
    except Exception:
        log.exception("Upload offsite falhou para %s", path)
        return False


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
