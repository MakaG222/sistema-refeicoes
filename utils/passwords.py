"""Funções de gestão de passwords e utilizadores."""

import secrets
import string

from flask import current_app
from werkzeug.security import (
    check_password_hash,
    generate_password_hash as _gen_pw_hash,
)

import sistema_refeicoes_v8_4 as sr

from utils.helpers import _audit
from utils.validators import _val_ano, _val_ni, _val_nii, _val_nome, _val_perfil


def generate_password_hash(password: str) -> str:
    """Wrapper que usa pbkdf2:sha256 como fallback quando scrypt não está disponível."""
    try:
        return _gen_pw_hash(password)
    except (ValueError, AttributeError):
        return _gen_pw_hash(password, method="pbkdf2:sha256")


def _validate_password(pw: str) -> tuple:
    """Valida requisitos de password: mínimo 8 caracteres, letras e números."""
    if len(pw) < 8:
        return False, "A password deve ter pelo menos 8 caracteres."
    if pw.isdigit() or pw.isalpha():
        return False, "A password deve conter letras e n\u00fameros."
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
        with sr.db() as conn:
            conn.execute(
                "UPDATE utilizadores SET Palavra_chave=? WHERE id=?", (new_hash, uid)
            )
            conn.commit()
    except Exception as exc:
        current_app.logger.warning(f"_migrate_password_hash uid={uid}: {exc}")


def _alterar_password(nii, old, new):
    """Altera a password de um utilizador (requer password antiga correcta)."""
    uid = sr.user_id_by_nii(nii)
    if not uid:
        return (
            False,
            "Conta de sistema \u2014 n\u00e3o \u00e9 poss\u00edvel alterar a password.",
        )
    with sr.db() as conn:
        row = conn.execute(
            "SELECT Palavra_chave FROM utilizadores WHERE id=?", (uid,)
        ).fetchone()
    if not row:
        return False, "Utilizador n\u00e3o encontrado."
    ph = row["Palavra_chave"] or ""
    if not _check_password(ph, old):
        return False, "Password atual incorreta."
    pw_ok, pw_msg = _validate_password(new)
    if not pw_ok:
        return False, pw_msg
    new_hash = generate_password_hash(new)
    with sr.db() as conn:
        conn.execute(
            """UPDATE utilizadores SET Palavra_chave=?, must_change_password=0,
                        password_updated_at=datetime('now','localtime') WHERE id=?""",
            (new_hash, uid),
        )
        conn.commit()
    return True, ""


def _criar_utilizador(nii, ni, nome, ano, perfil, pw):
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
        pw_ok, pw_msg = _validate_password(pw)
        if not pw_ok:
            return False, pw_msg
        pw_hash = generate_password_hash(pw)
        with sr.db() as conn:
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


def _reset_pw(nii, nova_pw=None):
    """Reset de password (gera aleatória se não fornecida)."""
    if not nova_pw:
        alphabet = string.ascii_letters + string.digits
        nova_pw = "".join(secrets.choice(alphabet) for _ in range(10))
    nova_hash = generate_password_hash(nova_pw)
    with sr.db() as conn:
        cur = conn.execute(
            """UPDATE utilizadores SET Palavra_chave=?, must_change_password=1,
                              password_updated_at=datetime('now','localtime') WHERE NII=?""",
            (nova_hash, nii),
        )
        conn.commit()
    if cur.rowcount:
        _audit("sistema", "reset_password", f"NII={nii}")
        return True, nova_pw  # devolve a password em claro para mostrar ao admin
    return False, "NII n\u00e3o encontrado."


def _unblock_user(nii):
    """Desbloqueia um utilizador (remove locked_until)."""
    with sr.db() as conn:
        conn.execute("UPDATE utilizadores SET locked_until=NULL WHERE NII=?", (nii,))
        conn.commit()


def _eliminar_utilizador(nii):
    """Elimina um utilizador da BD."""
    with sr.db() as conn:
        cur = conn.execute("DELETE FROM utilizadores WHERE NII=?", (nii,))
        conn.commit()
    return cur.rowcount > 0
