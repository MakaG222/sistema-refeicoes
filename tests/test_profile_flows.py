"""
tests/test_profile_flows.py — Testes de integração de acesso por perfil
=======================================================================
Verifica que cada perfil (admin, cmd, cozinha, oficialdia, aluno)
consegue aceder às suas rotas e é bloqueado nas rotas de outros perfis.
"""

from tests.conftest import create_aluno, login_as


# ── Admin ────────────────────────────────────────────────────────────────


class TestAdminAccess:
    def test_admin_access(self, client):
        login_as(client, "admin", "admin123")
        resp = client.get("/admin")
        assert resp.status_code == 200

    def test_admin_access_utilizadores(self, client):
        login_as(client, "admin", "admin123")
        resp = client.get("/admin/utilizadores")
        assert resp.status_code == 200


# ── CMD ──────────────────────────────────────────────────────────────────


class TestCmdAccess:
    def test_cmd_access_painel(self, client):
        login_as(client, "cmd1", "cmd1123")
        resp = client.get("/painel")
        assert resp.status_code == 200

    def test_cmd_denied_admin(self, client):
        login_as(client, "cmd1", "cmd1123")
        resp = client.get("/admin")
        # Should redirect or forbid — not 200
        assert resp.status_code in (302, 303, 403)


# ── Cozinha ──────────────────────────────────────────────────────────────


class TestCozinhaAccess:
    def test_cozinha_access(self, client):
        login_as(client, "cozinha", "cozinha123")
        resp = client.get("/painel")
        assert resp.status_code == 200


# ── Oficial de Dia ───────────────────────────────────────────────────────


class TestOficialdiaAccess:
    def test_oficialdia_access(self, client):
        login_as(client, "oficialdia", "oficial123")
        resp = client.get("/painel")
        assert resp.status_code == 200


# ── Aluno ────────────────────────────────────────────────────────────────


class TestAlunoAccess:
    def test_aluno_access_semana(self, app, client):
        with app.app_context():
            create_aluno("aluno_flow", "NI_flow", "Aluno Flow", ano="1", pw="flow123")
        login_as(client, "aluno_flow", "flow123")
        resp = client.get("/aluno")
        assert resp.status_code == 200

    def test_aluno_denied_admin(self, app, client):
        with app.app_context():
            create_aluno("aluno_deny", "NI_deny", "Aluno Denied", ano="1", pw="deny123")
        login_as(client, "aluno_deny", "deny123")
        resp = client.get("/admin")
        assert resp.status_code in (302, 303, 403)


# ── Unauthenticated ─────────────────────────────────────────────────────


class TestUnauthenticated:
    def test_unauthenticated_redirect(self, client):
        resp = client.get("/admin")
        assert resp.status_code in (302, 303)
        # Should redirect to login
        location = resp.headers.get("Location", "")
        assert "login" in location.lower()
