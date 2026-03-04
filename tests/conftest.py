"""
tests/conftest.py — Fixtures partilhadas para todos os testes
==============================================================
"""

import os
import tempfile

import pytest

# Configurar ENV de teste antes de importar a app
os.environ.setdefault("ENV", "development")
os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production")


@pytest.fixture(scope="session")
def app():
    """Cria uma instância da app com BD temporária para os testes."""
    import sys

    sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

    # BD temporária isolada para os testes
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    os.environ["DB_PATH"] = tmp.name

    import sistema_refeicoes_v8_4 as sr

    sr.BASE_DADOS = tmp.name
    sr.ensure_schema()

    import app as app_module

    app_module._ensure_extra_schema()

    flask_app = app_module.app
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False

    yield flask_app

    # Cleanup
    os.unlink(tmp.name)


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def csrf_token(client):
    """Obtém um token CSRF válido através do endpoint de login."""
    client.get("/login")
    with client.session_transaction() as sess:
        return sess.get("_csrf_token", "test-token")


# ── Helpers reutilizáveis ─────────────────────────────────────────────────


def create_aluno(nii, ni, nome, ano="1", pw=None):
    """Cria um aluno de teste na BD. Retorna o user_id."""
    from werkzeug.security import generate_password_hash

    import sistema_refeicoes_v8_4 as sr

    if pw is None:
        pw = nii
    pw_hash = generate_password_hash(pw, method="pbkdf2")
    with sr.db() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO utilizadores
               (NII, NI, Nome_completo, Palavra_chave, ano, perfil, must_change_password)
               VALUES (?,?,?,?,?,'aluno',0)""",
            (nii, ni, nome, pw_hash, ano),
        )
        conn.commit()
        row = conn.execute("SELECT id FROM utilizadores WHERE NII=?", (nii,)).fetchone()
        return row["id"]


def create_system_user(nii, perfil, nome=None, ano="0", pw=None):
    """Cria um utilizador de sistema (cmd, oficialdia, etc.)."""
    from werkzeug.security import generate_password_hash

    import sistema_refeicoes_v8_4 as sr

    if pw is None:
        pw = nii + "123"
    if nome is None:
        nome = f"Test {perfil.title()}"
    pw_hash = generate_password_hash(pw, method="pbkdf2")
    with sr.db() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO utilizadores
               (NII, NI, Nome_completo, Palavra_chave, ano, perfil, must_change_password)
               VALUES (?,?,?,?,?,?,0)""",
            (nii, nii, nome, pw_hash, ano, perfil),
        )
        conn.commit()


def login_as(client, nii, pw=None):
    """Faz login com um utilizador e retorna a resposta."""
    if pw is None:
        pw = nii
    client.get("/login")
    with client.session_transaction() as sess:
        token = sess.get("_csrf_token", "")
    return client.post(
        "/login",
        data={"nii": nii, "pw": pw, "csrf_token": token},
        follow_redirects=False,
    )


def get_csrf(client):
    """Obtém o CSRF token da sessão actual."""
    with client.session_transaction() as sess:
        return sess.get("_csrf_token", "test-token")
