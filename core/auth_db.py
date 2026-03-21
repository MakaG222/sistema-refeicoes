"""Funções de autenticação e lookup de utilizadores."""

from __future__ import annotations

import sqlite3

try:
    from werkzeug.security import check_password_hash as _wz_check_password_hash
except Exception:
    _wz_check_password_hash = None

from core.constants import PERFIS_ADMIN, PERFIS_TESTE
from core.database import db

# Re-exportar constantes para consumidores
__all__ = [
    "verify_password",
    "reg_login",
    "recent_failures",
    "recent_failures_by_ip",
    "block_user",
    "existe_admin",
    "user_by_nii",
    "user_by_ni",
    "user_id_by_nii",
    "PERFIS_ADMIN",
    "PERFIS_TESTE",
]


def verify_password(pw: str, stored: str) -> bool:
    """Verifica password; suporta hashes werkzeug e password em claro (legado)."""
    stored = stored or ""
    if stored.startswith(("pbkdf2:", "scrypt:", "argon2:")) and _wz_check_password_hash:
        try:
            return bool(_wz_check_password_hash(stored, pw))
        except Exception:
            return False
    return pw == stored


def reg_login(nii: str, ok: int, ip: str | None = None) -> None:
    """Regista evento de login na BD (com IP opcional)."""
    try:
        ip = (ip or "127.0.0.1")[:64]
        with db() as conn:
            conn.execute(
                "INSERT INTO login_eventos(nii,sucesso,ip) VALUES (?,?,?)",
                (nii, ok, ip),
            )
            conn.commit()
    except sqlite3.Error:
        pass


def recent_failures(nii: str, minutes: int = 10) -> int:
    """Conta tentativas falhadas recentes."""
    with db() as conn:
        modifier = f"-{minutes} minutes"
        r = conn.execute(
            """SELECT COUNT(*) c FROM login_eventos
               WHERE nii=? AND sucesso=0
               AND criado_em >= datetime('now','localtime',?)""",
            (nii, modifier),
        ).fetchone()
        return r["c"] if r else 0


def recent_failures_by_ip(ip: str, minutes: int = 15) -> int:
    """Conta tentativas falhadas recentes por IP."""
    with db() as conn:
        modifier = f"-{minutes} minutes"
        r = conn.execute(
            """SELECT COUNT(*) c FROM login_eventos
               WHERE ip=? AND sucesso=0
               AND criado_em >= datetime('now','localtime',?)""",
            (ip, modifier),
        ).fetchone()
        return r["c"] if r else 0


def block_user(nii: str, minutes: int = 15) -> None:
    with db() as conn:
        conn.execute(
            "UPDATE utilizadores SET locked_until=datetime('now','localtime',?) WHERE NII=?",
            (f"+{minutes} minutes", nii),
        )
        conn.commit()


def existe_admin() -> bool:
    with db() as conn:
        r = conn.execute(
            "SELECT COUNT(*) c FROM utilizadores WHERE perfil='admin'"
        ).fetchone()
        return bool(r and r["c"] > 0)


def user_by_nii(nii: str) -> dict | None:
    nii = (nii or "").strip()
    if not nii:
        return None
    with db() as conn:
        r = conn.execute(
            "SELECT * FROM utilizadores WHERE NII = ? COLLATE NOCASE", (nii,)
        ).fetchone()
        return dict(r) if r else None


def user_by_ni(ni: str) -> sqlite3.Row | None:
    ni = (ni or "").strip()
    if not ni:
        return None
    with db() as conn:
        r = conn.execute("SELECT * FROM utilizadores WHERE NI = ?", (ni,)).fetchone()
        return r


def user_id_by_nii(nii: str) -> int | None:
    u = user_by_nii(nii)
    return u["id"] if u else None
