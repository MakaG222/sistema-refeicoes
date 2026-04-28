"""core/checkin — QR rotativo: tokens TTL curto + log de check-ins.

Paradigma: oficial-de-dia mostra um QR (URL com token) que rota a cada
~45-60s; aluno scaneia com a câmara nativa do telemóvel, abre `/checkin?token=…`
e o handler valida o token + regista entrada ou saída via
`core.operations.registar_*_presenca`.

Múltiplos alunos podem usar o mesmo token enquanto este estiver válido —
mas cada aluno só uma vez por token (UNIQUE(utilizador_id, token) na tabela
`checkin_log`), o que impede double-scan acidental.
"""

from __future__ import annotations

import logging
import secrets
import sqlite3
from datetime import datetime, timedelta
from typing import Any

from core.constants import CHECKIN_TOKEN_TTL_SECONDS
from core.database import db

log = logging.getLogger(__name__)

_TIPOS_VALIDOS = frozenset({"entrada", "saida", "auto"})


def gerar_token(
    oficial_id: int,
    tipo: str = "auto",
    ttl_segundos: int = CHECKIN_TOKEN_TTL_SECONDS,
) -> dict[str, Any]:
    """Cria um novo token rotativo para QR de check-in.

    Args:
        oficial_id: id do utilizador (oficial-dia ou admin) que gera.
        tipo: 'entrada' / 'saida' / 'auto' — 'auto' decide com base no
              estado actual do aluno (ausente → entrada; senão → saída).
        ttl_segundos: validade em segundos.

    Returns:
        dict com `token`, `expires_at` (ISO local), `tipo`.
    """
    if tipo not in _TIPOS_VALIDOS:
        raise ValueError(f"tipo inválido: {tipo!r}")

    token = secrets.token_urlsafe(24)  # ~32 chars, ~144 bits
    now = datetime.now()
    expires_at = now + timedelta(seconds=ttl_segundos)
    expires_str = expires_at.strftime("%Y-%m-%d %H:%M:%S")

    with db() as conn:
        conn.execute(
            "INSERT INTO checkin_tokens (token, expires_at, created_by, tipo)"
            " VALUES (?, ?, ?, ?)",
            (token, expires_str, oficial_id, tipo),
        )
        conn.commit()

    return {"token": token, "expires_at": expires_str, "tipo": tipo}


def validar_token(token: str) -> dict[str, Any] | None:
    """Devolve dict com info do token se válido (existe e não expirou),
    senão None.

    NOTA: não consome — apenas valida. O consumo é feito por `consumir_token`,
    que insere em `checkin_log` (com UNIQUE constraint que impede double-scan).
    """
    if not token or not isinstance(token, str):
        return None

    with db() as conn:
        row = conn.execute(
            "SELECT token, expires_at, created_by, tipo FROM checkin_tokens"
            " WHERE token=?",
            (token,),
        ).fetchone()

    if row is None:
        return None

    try:
        exp = datetime.strptime(row["expires_at"], "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        log.warning("validar_token: expires_at inválido para token=%r", token)
        return None

    if datetime.now() >= exp:
        return None

    return dict(row)


def consumir_token(
    token: str,
    aluno_id: int,
    tipo_resolvido: str,
    ip: str | None = None,
    user_agent: str | None = None,
) -> tuple[bool, str]:
    """Regista um check-in para `aluno_id` usando `token`.

    Args:
        token: token rotativo (já validado por `validar_token`).
        aluno_id: id do aluno autenticado que scaneou.
        tipo_resolvido: 'entrada' ou 'saida' (resolvido se token.tipo='auto').
        ip, user_agent: para auditoria.

    Returns:
        (True, msg) se registou, (False, motivo) se duplicado/inválido.
    """
    if tipo_resolvido not in ("entrada", "saida"):
        return False, f"Tipo resolvido inválido: {tipo_resolvido!r}"

    try:
        with db() as conn:
            conn.execute(
                "INSERT INTO checkin_log (utilizador_id, token, tipo, ip, user_agent)"
                " VALUES (?, ?, ?, ?, ?)",
                (aluno_id, token, tipo_resolvido, ip, user_agent),
            )
            conn.commit()
    except sqlite3.IntegrityError:
        # UNIQUE(utilizador_id, token) — aluno já fez scan deste token
        return False, "Já registaste check-in com este código."
    except sqlite3.Error as exc:
        log.exception("consumir_token: erro ao gravar checkin_log: %s", exc)
        return False, "Erro ao registar check-in."

    return True, "Check-in registado."


def cleanup_expired() -> int:
    """Apaga tokens cujo `expires_at` está no passado. Retorna nº apagado.

    Chamar periodicamente (ex: pelo cron `/api/unlock-expired` que já existe).
    Os logs em `checkin_log` ficam — só os tokens são limpos.
    """
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with db() as conn:
        cur = conn.execute(
            "DELETE FROM checkin_tokens WHERE expires_at < ?", (now_str,)
        )
        conn.commit()
    n = cur.rowcount or 0
    if n:
        log.info("cleanup_expired: removidos %d tokens expirados", n)
    return n


def get_checkin_log(uid: int, limit: int = 50) -> list[dict[str, Any]]:
    """Histórico de check-ins de um aluno (mais recentes primeiro)."""
    with db() as conn:
        rows = conn.execute(
            "SELECT id, token, tipo, ts, ip FROM checkin_log"
            " WHERE utilizador_id=? ORDER BY ts DESC LIMIT ?",
            (uid, limit),
        ).fetchall()
    return [dict(r) for r in rows]


__all__ = [
    "gerar_token",
    "validar_token",
    "consumir_token",
    "cleanup_expired",
    "get_checkin_log",
]
