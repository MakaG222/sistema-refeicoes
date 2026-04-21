"""Funções de gestão de passwords e utilizadores.

Funções de resultado seguem o padrão tuple[bool, str]:
    (True, "")           — sucesso
    (False, "mensagem")  — erro com motivo legível
"""

from __future__ import annotations

from flask import current_app
from werkzeug.security import (
    check_password_hash,
    generate_password_hash as _gen_pw_hash,
)

from core.auth_db import user_id_by_nii
from core.database import db

from utils.helpers import _audit
from utils.validators import _val_ano, _val_ni, _val_nii, _val_nome, _val_perfil


def generate_password_hash(password: str) -> str:
    """Wrapper que usa pbkdf2:sha256 como fallback quando scrypt não está disponível."""
    try:
        return _gen_pw_hash(password)
    except (ValueError, AttributeError):
        return _gen_pw_hash(password, method="pbkdf2:sha256")


# Top passwords comuns — bloqueadas mesmo cumprindo os requisitos abaixo.
# Lista curta (pragmática); não substitui o user fazer melhor, mas trava o óbvio.
_COMMON_PASSWORDS = frozenset(
    {
        "password",
        "password1",
        "password123",
        "passw0rd",
        "qwerty123",
        "abc12345",
        "12345678a",
        "admin123",
        "admin1234",
        "senha1234",
        "senha123",
        "12345678",
        "123456789",
        "11111111",
        "aaaaaaaa",
        "letmein1",
        "welcome1",
        "iloveyou1",
        "monkey123",
        "dragon123",
        "football1",
        "baseball1",
        "sunshine1",
        "princess1",
        "superman1",
        "batman123",
        "marinha1",
        "escolanaval1",
    }
)


def _validate_password(pw: str, *, nii: str = "", ni: str = "") -> tuple[bool, str]:
    """Valida requisitos de password.

    Requisitos (v1.1.1):
    - Mínimo 8 caracteres
    - Pelo menos uma letra E pelo menos um dígito
    - Não pode ser apenas 1 classe de caracteres
    - Não pode estar na blacklist de passwords comuns
    - Não pode ser igual ao NII ou NI do utilizador (se fornecidos)

    `nii` e `ni` são opcionais — se o chamador souber quem é o user,
    passa-os para bloquear passwords triviais `= NII`.
    """
    if not pw or len(pw) < 8:
        return False, "A password deve ter pelo menos 8 caracteres."
    has_letter = any(c.isalpha() for c in pw)
    has_digit = any(c.isdigit() for c in pw)
    if not (has_letter and has_digit):
        return False, "A password deve conter pelo menos uma letra e um n\u00famero."
    lower = pw.lower().strip()
    if lower in _COMMON_PASSWORDS:
        return False, "Esta password \u00e9 demasiado comum. Escolhe outra."
    if nii and lower == str(nii).lower():
        return False, "A password n\u00e3o pode ser igual ao NII."
    if ni and lower == str(ni).lower():
        return False, "A password n\u00e3o pode ser igual ao NI."
    return True, ""


def _check_password(stored_hash: str, password: str) -> bool:
    """Verifica password — suporta hashes werkzeug e passwords em claro (migração transparente)."""
    if not stored_hash:
        return False
    if stored_hash.startswith(("pbkdf2:", "scrypt:", "argon2:")):
        return check_password_hash(stored_hash, password)
    # Password em claro (legado) — comparação simples + migração automática ao login
    return password == stored_hash


def _migrate_password_hash(uid: int, plain_password: str) -> None:
    """Migra password em claro para hash werkzeug na BD (chamado após login bem-sucedido)."""
    try:
        new_hash = generate_password_hash(plain_password)
        with db() as conn:
            conn.execute(
                "UPDATE utilizadores SET Palavra_chave=? WHERE id=?", (new_hash, uid)
            )
            conn.commit()
    except Exception as exc:
        current_app.logger.warning(f"_migrate_password_hash uid={uid}: {exc}")


def _alterar_password(nii: str, old: str, new: str) -> tuple[bool, str]:
    """Altera a password de um utilizador (requer password antiga correcta)."""
    uid = user_id_by_nii(nii)
    if not uid:
        return (
            False,
            "Conta de sistema \u2014 n\u00e3o \u00e9 poss\u00edvel alterar a password.",
        )
    with db() as conn:
        row = conn.execute(
            "SELECT Palavra_chave FROM utilizadores WHERE id=?", (uid,)
        ).fetchone()
    if not row:
        return False, "Utilizador n\u00e3o encontrado."
    ph = row["Palavra_chave"] or ""
    if not _check_password(ph, old):
        return False, "Password atual incorreta."
    # Recupera NI para validar que a nova password não é igual
    with db() as conn:
        meta = conn.execute("SELECT NI FROM utilizadores WHERE id=?", (uid,)).fetchone()
    user_ni = (meta["NI"] if meta else "") or ""
    pw_ok, pw_msg = _validate_password(new, nii=nii, ni=user_ni)
    if not pw_ok:
        return False, pw_msg
    new_hash = generate_password_hash(new)
    with db() as conn:
        conn.execute(
            """UPDATE utilizadores SET Palavra_chave=?, must_change_password=0,
                        password_updated_at=datetime('now','localtime') WHERE id=?""",
            (new_hash, uid),
        )
        conn.commit()
    return True, ""


def _criar_utilizador(
    nii: str, ni: str, nome: str, ano: str, perfil: str, pw: str
) -> tuple[bool, str]:
    """Cria um novo utilizador na BD."""
    try:
        if not all([nii, ni, nome, ano, perfil, pw]):
            return False, "Todos os campos s\u00e3o obrigat\u00f3rios."
        nii = _val_nii(nii)
        if not nii:
            return (
                False,
                "NII inv\u00e1lido (alfanum\u00e9rico, m\u00e1x. 20 caracteres).",
            )
        if _val_ni(ni) is None:
            return (
                False,
                "NI inv\u00e1lido (alfanum\u00e9rico, m\u00e1x. 20 caracteres).",
            )
        ni = _val_ni(ni)
        nome = _val_nome(nome)
        if not nome:
            return False, "Nome inv\u00e1lido ou vazio."
        ano_int = _val_ano(ano)
        if ano_int is None:
            return False, "Ano inv\u00e1lido (deve ser entre 0 e 8)."
        perfil = _val_perfil(perfil)
        if not perfil:
            return False, "Perfil inv\u00e1lido."
        pw = str(pw).strip()[:256]
        pw_ok, pw_msg = _validate_password(pw, nii=nii, ni=ni)
        if not pw_ok:
            return False, pw_msg
        pw_hash = generate_password_hash(pw)
        with db() as conn:
            conn.execute(
                """INSERT INTO utilizadores
              (NII,NI,Nome_completo,Palavra_chave,ano,perfil,must_change_password,password_updated_at)
              VALUES (?,?,?,?,?,?,1,datetime('now','localtime'))""",
                (nii, ni, nome, pw_hash, ano_int, perfil),
            )
            conn.commit()
        _audit(
            "sistema", "criar_utilizador", f"NII={nii} perfil={perfil} ano={ano_int}"
        )
        return True, ""
    except Exception as e:
        current_app.logger.error(f"_criar_utilizador({nii}): {e}")
        return False, str(e)


def _reset_pw(nii: str) -> tuple[bool, str]:
    """Reset de password — usa o NII como password temporária (must_change_password=1)."""
    nova_hash = generate_password_hash(nii)
    with db() as conn:
        cur = conn.execute(
            """UPDATE utilizadores SET Palavra_chave=?, must_change_password=1,
                              password_updated_at=datetime('now','localtime') WHERE NII=?""",
            (nova_hash, nii),
        )
        conn.commit()
    if cur.rowcount:
        _audit("sistema", "reset_password", f"NII={nii}")
        return True, "ok"
    return False, "NII n\u00e3o encontrado."


def _unblock_user(nii: str) -> None:
    """Desbloqueia um utilizador (remove locked_until)."""
    with db() as conn:
        conn.execute("UPDATE utilizadores SET locked_until=NULL WHERE NII=?", (nii,))
        conn.commit()


def _eliminar_utilizador(nii: str) -> bool:
    """Elimina um utilizador da BD."""
    with db() as conn:
        cur = conn.execute("DELETE FROM utilizadores WHERE NII=?", (nii,))
        conn.commit()
    return cur.rowcount > 0
