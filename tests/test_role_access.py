"""
tests/test_role_access.py — Testes do decorator role_required
==============================================================
Verifica controlo de acesso por perfil: sessão, role, must_change_password.
"""

import os

os.environ.setdefault("ENV", "development")
os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production")

from conftest import create_aluno, create_system_user, login_as


class TestRoleRequired:
    """Testa que role_required bloqueia acessos não autorizados."""

    def test_unauthenticated_redirects_to_login(self, client):
        """Acesso sem login deve redirecionar para /login."""
        resp = client.get("/admin/utilizadores", follow_redirects=False)
        assert resp.status_code == 302
        assert "/login" in resp.headers.get("Location", "")

    def test_aluno_cannot_access_admin(self, app, client):
        """Aluno não deve aceder a rotas de admin."""
        create_aluno("roletest1", "RT001", "Role Test", pw="Roletest12")
        login_as(client, "roletest1", "Roletest12")
        resp = client.get("/admin/utilizadores", follow_redirects=False)
        assert resp.status_code == 302
        assert "/dashboard" in resp.headers.get("Location", "")

    def test_admin_can_access_admin(self, app, client):
        """Admin deve aceder a rotas de admin."""
        create_system_user("roleadm1", "admin", pw="Roleadm123")
        login_as(client, "roleadm1", "Roleadm123")
        resp = client.get("/admin/utilizadores", follow_redirects=False)
        assert resp.status_code == 200

    def test_must_change_password_redirects(self, app, client):
        """Utilizador com must_change_password deve ir para /aluno/password."""
        import sistema_refeicoes_v8_4 as sr

        create_system_user("rolemc1", "admin", pw="Rolemc1234")
        # Forçar must_change_password
        with sr.db() as conn:
            conn.execute(
                "UPDATE utilizadores SET must_change_password=1 WHERE NII='rolemc1'"
            )
            conn.commit()
        login_as(client, "rolemc1", "Rolemc1234")
        resp = client.get("/admin/utilizadores", follow_redirects=False)
        assert resp.status_code == 302
        assert "/aluno/password" in resp.headers.get("Location", "")
