"""Validadores de input — funções reutilizáveis para sanitização server-side."""

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


def _val_email(v):
    """Valida email. Devolve string limpa ou None se vazio, False se inválido."""
    v = (v or "").strip()[:254]
    if not v:
        return None
    return v if _RE_EMAIL.match(v) else False


def _val_phone(v):
    """Valida telemóvel. Devolve string limpa ou None se vazio, False se inválido."""
    v = (v or "").strip()[:20]
    if not v:
        return None
    return v if _RE_PHONE.match(v) else False


def _val_nii(v):
    """Valida NII (alfanumérico, 1-20 chars). Devolve string ou None se inválido."""
    v = (v or "").strip()[:20]
    return v if v and _RE_ALNUM.match(v) else None


def _val_ni(v):
    """Valida NI (alfanumérico, até 20 chars). Pode ser vazio."""
    v = (v or "").strip()[:20]
    if not v:
        return ""
    return v if _RE_ALNUM.match(v) else None


def _val_nome(v, max_len=_MAX_NOME):
    """Valida nome (não-vazio, limitado). Devolve string ou None se vazio."""
    v = (v or "").strip()[:max_len]
    return v if v else None


def _val_ano(v):
    """Valida ano escolar (0-8). 0 = concluído. Devolve int ou None se inválido."""
    try:
        a = int(v)
        return a if 0 <= a <= 8 else None
    except (TypeError, ValueError):
        return None


def _val_perfil(v):
    """Valida perfil contra whitelist. Devolve string ou None se inválido."""
    v = (v or "").strip().lower()
    return v if v in _PERFIS_VALIDOS else None


def _val_tipo_calendario(v):
    """Valida tipo de calendário. Fallback para 'normal'."""
    v = (v or "").strip().lower()
    return v if v in _TIPOS_CALENDARIO else "normal"


def _val_refeicao(v):
    """Valida opção de refeição. Fallback para string vazia."""
    v = (v or "").strip()
    return v if v in _REFEICAO_OPCOES else ""


def _val_text(v, max_len=_MAX_TEXT):
    """Limita texto a max_len caracteres."""
    return (v or "").strip()[:max_len]


def _val_int_id(v):
    """Valida ID numérico. Devolve int ou None."""
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _val_date_range(d1, d2, max_dias=_MAX_DATE_RANGE):
    """Valida range de datas. Devolve (ok, msg_erro)."""
    if d2 < d1:
        return False, "Data final anterior \u00e0 inicial."
    if (d2 - d1).days > max_dias:
        return False, f"Intervalo m\u00e1ximo permitido: {max_dias} dias."
    return True, ""


def _val_cap(v, max_val=9999):
    """Valida capacidade (inteiro 0-max_val). Devolve int ou None."""
    try:
        c = int(v)
        return c if 0 <= c <= max_val else None
    except (TypeError, ValueError):
        return None
