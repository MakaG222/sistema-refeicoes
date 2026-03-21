"""Validadores de input — funções reutilizáveis para sanitização server-side.

Convenções de retorno:
- Campos opcionais: str (válido), None (vazio/ausente), False (inválido)
- Campos obrigatórios: str/int (válido), None (inválido)
- Ranges: tuple[bool, str] — (True, "") ou (False, "mensagem de erro")
"""

from __future__ import annotations

from datetime import date

from utils.constants import (
    _MAX_DATE_RANGE,
    _MAX_NOME,
    _MAX_TEXT,
    _PERFIS_VALIDOS,
    _RE_ALNUM,
    _RE_EMAIL,
    _RE_PHONE,
    _REFEICAO_OPCOES,
    _TIPOS_CALENDARIO,
)


def _val_email(v: str | None) -> str | None | bool:
    """Valida email. Devolve string limpa ou None se vazio, False se inválido."""
    v = (v or "").strip()[:254]
    if not v:
        return None
    return v if _RE_EMAIL.match(v) else False


def _val_phone(v: str | None) -> str | None | bool:
    """Valida telemóvel. Devolve string limpa ou None se vazio, False se inválido."""
    v = (v or "").strip()[:20]
    if not v:
        return None
    return v if _RE_PHONE.match(v) else False


def _val_nii(v: str | None) -> str | None:
    """Valida NII (alfanumérico, 1-20 chars). Devolve string ou None se inválido."""
    v = (v or "").strip()[:20]
    return v if v and _RE_ALNUM.match(v) else None


def _val_ni(v: str | None) -> str | None:
    """Valida NI (alfanumérico, até 20 chars). Pode ser vazio."""
    v = (v or "").strip()[:20]
    if not v:
        return ""
    return v if _RE_ALNUM.match(v) else None


def _val_nome(v: str | None, max_len: int = _MAX_NOME) -> str | None:
    """Valida nome (não-vazio, limitado). Devolve string ou None se vazio."""
    v = (v or "").strip()[:max_len]
    return v if v else None


def _val_ano(v: str | int | None) -> int | None:
    """Valida ano escolar (0-8). 0 = concluído. Devolve int ou None se inválido."""
    try:
        a = int(v)
        return a if 0 <= a <= 8 else None
    except (TypeError, ValueError):
        return None


def _val_perfil(v: str | None) -> str | None:
    """Valida perfil contra whitelist. Devolve string ou None se inválido."""
    v = (v or "").strip().lower()
    return v if v in _PERFIS_VALIDOS else None


def _val_tipo_calendario(v: str | None) -> str:
    """Valida tipo de calendário. Fallback para 'normal'."""
    v = (v or "").strip().lower()
    return v if v in _TIPOS_CALENDARIO else "normal"


def _val_refeicao(v: str | None) -> str:
    """Valida opção de refeição. Fallback para string vazia."""
    v = (v or "").strip()
    return v if v in _REFEICAO_OPCOES else ""


def _val_text(v: str | None, max_len: int = _MAX_TEXT) -> str:
    """Limita texto a max_len caracteres."""
    return (v or "").strip()[:max_len]


def _val_int_id(v: str | int | None) -> int | None:
    """Valida ID numérico. Devolve int ou None."""
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _val_date_range(
    d1: date, d2: date, max_dias: int = _MAX_DATE_RANGE
) -> tuple[bool, str]:
    """Valida range de datas. Devolve (ok, msg_erro)."""
    if d2 < d1:
        return False, "Data final anterior \u00e0 inicial."
    if (d2 - d1).days > max_dias:
        return False, f"Intervalo m\u00e1ximo permitido: {max_dias} dias."
    return True, ""


def _val_cap(v: str | int | None, max_val: int = 9999) -> int | None:
    """Valida capacidade (inteiro 0-max_val). Devolve int ou None."""
    try:
        c = int(v)
        return c if 0 <= c <= max_val else None
    except (TypeError, ValueError):
        return None
