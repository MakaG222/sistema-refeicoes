"""
tests/test_auth.py — Testes de autenticação, autorização e endpoints críticos
=============================================================================
Executa com:  pytest tests/ -v
"""

# Fixtures (app, client, csrf_token) importadas automaticamente do conftest.py


# ── Health endpoint ────────────────────────────────────────────────────────────


class TestHealth:
    def test_health_returns_200(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_health_returns_json(self, client):
        resp = client.get("/health")
        data = resp.get_json()
        assert data is not None
        assert data["status"] == "ok"
        assert data["db"] == "ok"
        assert "ts" in data
        assert "latency_ms" in data

    def test_health_no_auth_required(self, client):
        """Health endpoint deve ser público (sem login)."""
        resp = client.get("/health")
        assert resp.status_code == 200


# ── Autenticação ───────────────────────────────────────────────────────────────


class TestLogin:
    def test_login_page_loads(self, client):
        resp = client.get("/login")
        assert resp.status_code == 200
        assert b"login" in resp.data.lower() or b"sistema" in resp.data.lower()

    def test_login_redirect_unauthenticated(self, client):
        """Acesso a rota protegida sem sessão deve redirecionar para login."""
        resp = client.get("/aluno")
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_login_invalid_credentials(self, client, csrf_token):
        resp = client.post(
            "/login",
            data={
                "nii": "utilizador_inexistente_9999",
                "password": "wrongpassword",
                "csrf_token": csrf_token,
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        # Deve mostrar mensagem de erro, não entrar na app
        assert (
            b"login" in resp.data.lower()
            or b"incorret" in resp.data.lower()
            or b"inv" in resp.data.lower()
        )

    def test_login_empty_fields(self, client, csrf_token):
        resp = client.post(
            "/login",
            data={
                "nii": "",
                "password": "",
                "csrf_token": csrf_token,
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

    def test_login_system_account_admin(self, client, csrf_token):
        """Login com conta de sistema 'admin' deve funcionar em desenvolvimento."""
        resp = client.post(
            "/login",
            data={
                "nii": "admin",
                "password": "admin123",
                "csrf_token": csrf_token,
            },
            follow_redirects=False,
        )
        # Deve redirecionar para dashboard (não ficar em /login)
        assert resp.status_code in (302, 200)
        if resp.status_code == 302:
            assert "/login" not in resp.headers.get("Location", "/login")


# ── Logout ─────────────────────────────────────────────────────────────────────


class TestLogout:
    def _login_admin(self, client, csrf_token):
        """Utilitário: faz login como admin."""
        client.post(
            "/login",
            data={
                "nii": "admin",
                "password": "admin123",
                "csrf_token": csrf_token,
            },
        )

    def test_logout_requires_post(self, client):
        """GET para /logout deve retornar 405 Method Not Allowed."""
        resp = client.get("/logout")
        assert resp.status_code == 405

    def test_logout_requires_csrf(self, client, csrf_token):
        """POST para /logout sem token CSRF deve retornar 403."""
        self._login_admin(client, csrf_token)
        resp = client.post("/logout", data={"csrf_token": "token_invalido"})
        assert resp.status_code in (400, 403)

    def test_logout_clears_session(self, client, csrf_token):
        """Logout com CSRF válido deve limpar sessão e redirecionar para login."""
        self._login_admin(client, csrf_token)

        # Obter token CSRF da sessão actual
        with client.session_transaction() as sess:
            token = sess.get("_csrf_token", "")

        resp = client.post("/logout", data={"csrf_token": token})
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

        # Verificar sessão limpa
        with client.session_transaction() as sess:
            assert "user" not in sess


# ── Autorização / proteção de rotas ───────────────────────────────────────────


class TestAuthorization:
    ADMIN_ONLY_ROUTES = [
        "/admin",
        "/admin/utilizadores",
        "/admin/backup",
    ]
    LOGIN_REQUIRED_ROUTES = [
        "/aluno",
        "/dashboard",
    ]

    def test_admin_routes_require_login(self, client):
        """Rotas de admin devem redirecionar para login quando não autenticado."""
        for route in self.ADMIN_ONLY_ROUTES:
            resp = client.get(route)
            assert resp.status_code in (302, 403, 404), (
                f"{route} deveria redirecionar ou retornar 403, got {resp.status_code}"
            )

    def test_protected_routes_require_login(self, client):
        """Rotas protegidas devem redirecionar para login quando não autenticado."""
        for route in self.LOGIN_REQUIRED_ROUTES:
            resp = client.get(route)
            assert resp.status_code == 302, (
                f"{route} deveria redirecionar, got {resp.status_code}"
            )
            if resp.status_code == 302:
                assert "/login" in resp.headers.get("Location", "")


# ── CSRF ────────────────────────────────────────────────────────────────────────


class TestCSRF:
    def test_csrf_token_generated_on_login_page(self, client):
        """GET /login deve criar um token CSRF na sessão."""
        client.get("/login")
        with client.session_transaction() as sess:
            assert "_csrf_token" in sess
            assert len(sess["_csrf_token"]) > 10

    def test_post_without_csrf_rejected(self, client):
        """POST sem CSRF para rota protegida deve ser rejeitado."""
        # Primeiro fazer GET para criar sessão com token
        client.get("/login")
        # Tentar POST com token errado
        resp = client.post(
            "/login",
            data={
                "nii": "admin",
                "password": "admin123",
                "csrf_token": "token_completamente_errado",
            },
        )
        # Deve falhar (redirect de volta para login ou 403)
        assert resp.status_code in (200, 302, 400, 403)
        if resp.status_code == 302:
            # Se redireciona, não deve ser para dentro da app
            location = resp.headers.get("Location", "")
            assert "/admin" not in location and "/aluno" not in location


class TestLoginAuditAndIP:
    def test_login_event_records_real_ip(self, client):
        client.get("/login")
        with client.session_transaction() as sess:
            token = sess.get("_csrf_token", "")
        resp = client.post(
            "/login",
            data={"nii": "admin", "password": "admin123", "csrf_token": token},
            environ_overrides={"REMOTE_ADDR": "10.9.8.7"},
            follow_redirects=False,
        )
        assert resp.status_code in (200, 302)

        from core.database import db

        with db() as conn:
            row = conn.execute(
                "SELECT ip, sucesso FROM login_eventos WHERE nii=? ORDER BY id DESC LIMIT 1",
                ("admin",),
            ).fetchone()
            assert row is not None
            assert row["sucesso"] == 1
            assert row["ip"] == "10.9.8.7"

    def test_login_creates_audit_for_system_account(self, client):
        client.get("/login")
        with client.session_transaction() as sess:
            token = sess.get("_csrf_token", "")
        resp = client.post(
            "/login",
            data={"nii": "cozinha", "password": "cozinha123", "csrf_token": token},
            follow_redirects=False,
        )
        assert resp.status_code in (200, 302)

        from core.database import db

        with db() as conn:
            row = conn.execute(
                "SELECT actor, action, detail FROM admin_audit_log WHERE actor=? AND action='login' ORDER BY id DESC LIMIT 1",
                ("cozinha",),
            ).fetchone()
            assert row is not None
            assert "perfil=cozinha" in (row["detail"] or "")


class TestExportRelatorioValidation:
    def _login_admin(self, client):
        client.get("/login")
        with client.session_transaction() as sess:
            token = sess.get("_csrf_token", "")
        resp = client.post(
            "/login",
            data={"nii": "admin", "password": "admin123", "csrf_token": token},
            follow_redirects=False,
        )
        assert resp.status_code in (200, 302)

    def test_export_relatorio_invalid_fmt_rejected(self, client):
        self._login_admin(client)
        resp = client.get("/exportar/relatorio?d0=2026-03-01&fmt=pdf")
        assert resp.status_code == 400

    def test_export_relatorio_invalid_date_rejected(self, client):
        self._login_admin(client)
        resp = client.get("/exportar/relatorio?d0=data-invalida&fmt=csv")
        assert resp.status_code == 400


# ── IP Rate Limiting ─────────────────────────────────────────────────────────


class TestIPRateLimiting:
    def test_ip_blocked_after_20_failures(self, app, client):
        """20+ falhas do mesmo IP devem bloquear tentativas seguintes."""
        from core.auth_db import reg_login

        with app.app_context():
            for i in range(20):
                reg_login(f"fake_{i}", 0, ip="10.0.0.99")

        client.get("/login")
        with client.session_transaction() as sess:
            token = sess.get("_csrf_token", "")

        resp = client.post(
            "/login",
            data={"nii": "admin", "password": "admin123", "csrf_token": token},
            environ_overrides={"REMOTE_ADDR": "10.0.0.99"},
            follow_redirects=True,
        )
        html = resp.data.decode()
        assert "tentativas" in html.lower() or "aguarda" in html.lower()

    def test_different_ip_not_blocked(self, app, client):
        """Bloquear um IP não deve afetar outros IPs."""
        from core.auth_db import reg_login

        with app.app_context():
            for i in range(25):
                reg_login(f"fake_{i}", 0, ip="10.0.0.88")

        client.get("/login")
        with client.session_transaction() as sess:
            token = sess.get("_csrf_token", "")

        resp = client.post(
            "/login",
            data={"nii": "admin", "password": "admin123", "csrf_token": token},
            environ_overrides={"REMOTE_ADDR": "10.0.0.77"},
            follow_redirects=False,
        )
        # IP diferente deve conseguir fazer login
        assert resp.status_code in (200, 302)

    def test_recent_failures_by_ip_counts_correctly(self, app):
        """Função recent_failures_by_ip conta apenas falhas (não sucessos)."""
        from core.auth_db import recent_failures_by_ip, reg_login

        with app.app_context():
            reg_login("u1", 0, ip="172.16.0.5")
            reg_login("u2", 0, ip="172.16.0.5")
            reg_login("u3", 1, ip="172.16.0.5")  # sucesso — não conta
            reg_login("u4", 0, ip="172.16.0.6")  # IP diferente — não conta
            count = recent_failures_by_ip("172.16.0.5", 10)
            assert count == 2
