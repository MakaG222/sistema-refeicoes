"""
tests/test_authorization.py — Testes de fronteira de permissões
================================================================
Verifica que cada perfil só acede às rotas que lhe são permitidas.
"""

import os

os.environ.setdefault("ENV", "development")
os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production")

from conftest import create_aluno, create_system_user, login_as


# ── Routes organized by minimum required role ──────────────────────────

ADMIN_ONLY_ROUTES = [
    "/admin",
    "/admin/utilizadores",
    "/admin/importar-csv",
    "/admin/log",
    "/admin/auditoria",
    "/admin/companhias",
]

ELEVATED_ROUTES = [
    "/painel",
    "/relatorio",
]

CMD_ROUTES = [
    "/cmd/ausencias",
    "/cmd/detencoes",
]


class TestAlunoCannotAccessAdmin:
    """Aluno não deve aceder a nenhuma rota admin."""

    def test_aluno_blocked_from_admin_routes(self, app, client):
        create_aluno("auth_aluno1", "AA001", "Auth Aluno", pw="Authaluno1")
        login_as(client, "auth_aluno1", "Authaluno1")

        for route in ADMIN_ONLY_ROUTES:
            resp = client.get(route, follow_redirects=False)
            assert resp.status_code in (302, 403), (
                f"Aluno got {resp.status_code} on {route}"
            )
            if resp.status_code == 302:
                loc = resp.headers.get("Location", "")
                assert "/login" in loc or "/dashboard" in loc, (
                    f"Aluno redirect for {route} went to {loc}"
                )

    def test_aluno_blocked_from_operations(self, app, client):
        create_aluno("auth_aluno2", "AA002", "Auth Aluno2", pw="Authaluno2")
        login_as(client, "auth_aluno2", "Authaluno2")

        for route in ELEVATED_ROUTES:
            resp = client.get(route, follow_redirects=False)
            assert resp.status_code in (302, 403), (
                f"Aluno got {resp.status_code} on {route}"
            )

    def test_aluno_blocked_from_cmd(self, app, client):
        create_aluno("auth_aluno3", "AA003", "Auth Aluno3", pw="Authaluno3")
        login_as(client, "auth_aluno3", "Authaluno3")

        for route in CMD_ROUTES:
            resp = client.get(route, follow_redirects=False)
            assert resp.status_code in (302, 403), (
                f"Aluno got {resp.status_code} on {route}"
            )


class TestCozinhaCannotAccessAdmin:
    """Cozinha não deve aceder a rotas de admin (exceto menus)."""

    def test_cozinha_blocked_from_admin(self, app, client):
        create_system_user("auth_coz1", "cozinha", pw="Authcoz123")
        login_as(client, "auth_coz1", "Authcoz123")

        blocked = ["/admin", "/admin/utilizadores", "/admin/log", "/admin/auditoria"]
        for route in blocked:
            resp = client.get(route, follow_redirects=False)
            assert resp.status_code in (302, 403), (
                f"Cozinha got {resp.status_code} on {route}"
            )

    def test_cozinha_can_access_painel(self, app, client):
        create_system_user("auth_coz2", "cozinha", pw="Authcoz234")
        login_as(client, "auth_coz2", "Authcoz234")

        resp = client.get("/painel", follow_redirects=False)
        assert resp.status_code == 200


class TestOficialdiaAccess:
    """Oficial de dia tem acesso a operações mas não a admin puro."""

    def test_oficialdia_blocked_from_admin(self, app, client):
        create_system_user("auth_od1", "oficialdia", pw="Authod1234")
        login_as(client, "auth_od1", "Authod1234")

        blocked = ["/admin", "/admin/utilizadores", "/admin/log", "/admin/auditoria"]
        for route in blocked:
            resp = client.get(route, follow_redirects=False)
            assert resp.status_code in (302, 403), (
                f"Oficialdia got {resp.status_code} on {route}"
            )

    def test_oficialdia_can_access_painel(self, app, client):
        create_system_user("auth_od2", "oficialdia", pw="Authod2345")
        login_as(client, "auth_od2", "Authod2345")

        resp = client.get("/painel", follow_redirects=False)
        assert resp.status_code == 200


class TestUnauthenticated:
    """Pedidos sem login devem redirecionar para /login."""

    def test_protected_routes_redirect(self, client):
        routes = [
            "/admin",
            "/admin/utilizadores",
            "/painel",
            "/aluno",
            "/aluno/historico",
            "/cmd/ausencias",
            "/relatorio",
            "/dashboard-semanal",
        ]
        for route in routes:
            resp = client.get(route, follow_redirects=False)
            assert resp.status_code == 302, f"Unauth {route} gave {resp.status_code}"
            assert "/login" in resp.headers.get("Location", ""), (
                f"Unauth {route} didn't redirect to login"
            )

    def test_health_is_public(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_login_is_public(self, client):
        resp = client.get("/login")
        assert resp.status_code == 200


class TestErrorTemplates:
    """Verifica que templates de erro respondem correctamente."""

    def test_404_renders(self, client):
        resp = client.get("/pagina-que-nao-existe")
        assert resp.status_code == 404
        assert b"encontrad" in resp.data

    def test_400_via_bad_csrf(self, app, client):
        """POST sem CSRF válido deve dar 400 ou redirect."""
        create_system_user("auth_err1", "admin", pw="Autherr123")
        login_as(client, "auth_err1", "Autherr123")
        resp = client.post("/admin/utilizadores", data={"csrf_token": "bad"})
        assert resp.status_code in (400, 302)
