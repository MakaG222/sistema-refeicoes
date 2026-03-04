"""
tests/test_passwords.py — Testes de passwords e migração
========================================================
"""

from werkzeug.security import generate_password_hash

import sistema_refeicoes_v8_4 as sr

from tests.conftest import create_aluno, get_csrf, login_as


class TestCheckPassword:
    def test_check_werkzeug_hash(self, app):
        """_check_password funciona com hash werkzeug."""
        import app as app_module

        pw_hash = generate_password_hash("minha_password", method="pbkdf2")
        assert app_module._check_password(pw_hash, "minha_password") is True
        assert app_module._check_password(pw_hash, "password_errada") is False

    def test_check_plain_text_legacy(self, app):
        """_check_password funciona com password em texto simples (legado)."""
        import app as app_module

        assert (
            app_module._check_password("password_simples", "password_simples") is True
        )
        assert app_module._check_password("password_simples", "outra") is False

    def test_check_empty_hash_fails(self, app):
        """_check_password com hash vazio retorna False."""
        import app as app_module

        assert app_module._check_password("", "qualquer") is False
        assert app_module._check_password(None, "qualquer") is False


class TestMigratePasswordHash:
    def test_migrate_plain_to_hash(self, app):
        """_migrate_password_hash converte password em texto para hash."""
        import app as app_module

        uid = create_aluno("T_PW_MIG1", "501", "Password Migrate A", "1", pw="temp")

        # Definir password em texto simples directamente
        with sr.db() as conn:
            conn.execute(
                "UPDATE utilizadores SET Palavra_chave='pass_clara' WHERE id=?",
                (uid,),
            )
            conn.commit()

        # Migrar
        app_module._migrate_password_hash(uid, "pass_clara")

        # Verificar que agora é hash
        with sr.db() as conn:
            row = conn.execute(
                "SELECT Palavra_chave FROM utilizadores WHERE id=?", (uid,)
            ).fetchone()
        assert row["Palavra_chave"].startswith("scrypt:") or row[
            "Palavra_chave"
        ].startswith("pbkdf2:")


class TestFirstLoginPasswordChange:
    def test_first_login_forced_redirect(self, app, client):
        """Primeiro login com must_change_password redireciona para troca."""
        uid = create_aluno("T_PW_FC1", "510", "First Change A", "1", pw="510pw")

        # Forçar must_change_password
        with sr.db() as conn:
            conn.execute(
                "UPDATE utilizadores SET must_change_password=1 WHERE id=?", (uid,)
            )
            conn.commit()

        resp = login_as(client, "T_PW_FC1", "510pw")
        assert resp.status_code == 302

        # Aceder a qualquer rota deve redirecionar para troca de password
        resp = client.get("/aluno", follow_redirects=False)
        assert resp.status_code == 302
        location = resp.headers.get("Location", "")
        assert "password" in location.lower() or "pw" in location.lower()

    def test_password_change_clears_flag(self, app, client):
        """Trocar password limpa must_change_password."""
        uid = create_aluno("T_PW_FC2", "511", "First Change B", "1", pw="511pw")

        # Forçar must_change_password
        with sr.db() as conn:
            conn.execute(
                "UPDATE utilizadores SET must_change_password=1 WHERE id=?", (uid,)
            )
            conn.commit()

        login_as(client, "T_PW_FC2", "511pw")
        token = get_csrf(client)

        # Trocar password (campos: old, new, conf)
        resp = client.post(
            "/aluno/password",
            data={
                "csrf_token": token,
                "old": "511pw",
                "new": "novaPass123",
                "conf": "novaPass123",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 302

        # Verificar flag limpa
        with sr.db() as conn:
            row = conn.execute(
                "SELECT must_change_password FROM utilizadores WHERE id=?", (uid,)
            ).fetchone()
        assert row["must_change_password"] == 0


class TestAdminPasswordEdit:
    def test_admin_sets_hashed_password(self, app, client):
        """Admin ao editar password grava hash (não texto simples)."""
        create_aluno("T_PW_ADM1", "520", "Admin PW Test", "1", pw="520pw")

        login_as(client, "admin", "admin123")
        token = get_csrf(client)

        resp = client.post(
            "/admin/utilizadores",
            data={
                "csrf_token": token,
                "acao": "editar_user",
                "nii": "T_PW_ADM1",
                "nome": "Admin PW Test",
                "ni": "520",
                "ano": "1",
                "perfil": "aluno",
                "pw": "novaAdminPw",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 302

        # Verificar que password é hash, não texto simples
        with sr.db() as conn:
            row = conn.execute(
                "SELECT Palavra_chave FROM utilizadores WHERE NII='T_PW_ADM1'"
            ).fetchone()
        pw = row["Palavra_chave"]
        assert pw != "novaAdminPw", "Password está em texto simples!"
        assert pw.startswith("scrypt:") or pw.startswith("pbkdf2:")


class TestLoginWithNII:
    def test_login_with_nii(self, app, client):
        """Login funciona com NII como username."""
        create_aluno("T_NII_LOG", "530", "NII Login Test", "1", pw="T_NII_LOG")

        resp = login_as(client, "T_NII_LOG", "T_NII_LOG")
        assert resp.status_code == 302
        # Deve redirecionar para home, não ficar em login
        location = resp.headers.get("Location", "")
        assert "/login" not in location
