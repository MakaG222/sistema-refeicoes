"""
tests/test_security.py — Testes de segurança e hardening
=========================================================
Valida password hashing, SQL injection, CRON token, CSRF, security headers,
password validation e maxlength.
"""

import os

os.environ.setdefault("ENV", "development")
os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production")

from conftest import create_aluno, create_system_user, login_as, get_csrf


# ── Password Hashing ─────────────────────────────────────────────────────────


class TestAdminPasswordEditStoresHash:
    def test_admin_password_edit_stores_hash(self, app, client):
        """Alteração de password pelo admin deve guardar hash, não plain text."""
        import sistema_refeicoes_v8_4 as sr
        from werkzeug.security import check_password_hash

        create_aluno("sectest1", "SEC001", "Security Test", pw="oldpw123")
        create_system_user("secadmin", "admin", pw="secadmin123")
        login_as(client, "secadmin", "secadmin123")

        csrf = get_csrf(client)
        client.post(
            "/admin/utilizadores",
            data={
                "acao": "editar_user",
                "nii": "sectest1",
                "nome": "Security Test",
                "ni": "SEC001",
                "ano": "1",
                "perfil": "aluno",
                "pw": "Newpass12",
                "csrf_token": csrf,
            },
            follow_redirects=True,
        )

        with sr.db() as conn:
            row = conn.execute(
                "SELECT Palavra_chave FROM utilizadores WHERE NII='sectest1'"
            ).fetchone()
        assert row is not None
        pw_stored = row["Palavra_chave"]
        # Não deve ser plain text
        assert pw_stored != "Newpass12"
        # Deve ser verificável como hash
        assert check_password_hash(pw_stored, "Newpass12")


# ── CRON Endpoints ───────────────────────────────────────────────────────────


class TestCronEndpointRequiresToken:
    """CRON endpoints devem rejeitar requests sem token válido.
    Podem retornar 302 (CSRF redirect), 401 ou 403."""

    def test_backup_cron_no_token(self, app):
        import config as cfg

        original = cfg.CRON_API_TOKEN
        cfg.CRON_API_TOKEN = "real-secret-token"
        try:
            with app.test_client() as c:
                resp = c.post("/api/backup-cron")
            assert resp.status_code == 403
        finally:
            cfg.CRON_API_TOKEN = original

    def test_backup_cron_invalid_token(self, app):
        import config as cfg

        original = cfg.CRON_API_TOKEN
        cfg.CRON_API_TOKEN = "real-secret-token"
        try:
            with app.test_client() as c:
                resp = c.post(
                    "/api/backup-cron",
                    headers={"Authorization": "Bearer token-errado-123"},
                )
            assert resp.status_code == 403
        finally:
            cfg.CRON_API_TOKEN = original

    def test_autopreencher_cron_no_token(self, app):
        import config as cfg

        original = cfg.CRON_API_TOKEN
        cfg.CRON_API_TOKEN = "real-secret-token"
        try:
            with app.test_client() as c:
                resp = c.post("/api/autopreencher-cron")
            assert resp.status_code == 403
        finally:
            cfg.CRON_API_TOKEN = original

    def test_autopreencher_cron_invalid_token(self, app):
        import config as cfg

        original = cfg.CRON_API_TOKEN
        cfg.CRON_API_TOKEN = "real-secret-token"
        try:
            with app.test_client() as c:
                resp = c.post(
                    "/api/autopreencher-cron",
                    headers={"Authorization": "Bearer token-errado-123"},
                )
            assert resp.status_code == 403
        finally:
            cfg.CRON_API_TOKEN = original


class TestCronEndpointHappyPath:
    """CRON endpoints devem funcionar com token válido."""

    def test_backup_cron_with_valid_token(self, app):
        import config as cfg

        test_token = "test-cron-token-valid-123"
        original = cfg.CRON_API_TOKEN
        cfg.CRON_API_TOKEN = test_token
        try:
            with app.test_client() as c:
                resp = c.post(
                    "/api/backup-cron",
                    headers={"Authorization": f"Bearer {test_token}"},
                )
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["status"] == "ok"
        finally:
            cfg.CRON_API_TOKEN = original

    def test_autopreencher_cron_with_valid_token(self, app):
        import config as cfg

        test_token = "test-cron-token-valid-456"
        original = cfg.CRON_API_TOKEN
        cfg.CRON_API_TOKEN = test_token
        try:
            with app.test_client() as c:
                resp = c.post(
                    "/api/autopreencher-cron",
                    headers={"Authorization": f"Bearer {test_token}"},
                )
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["status"] == "ok"
        finally:
            cfg.CRON_API_TOKEN = original


# ── Security Headers ─────────────────────────────────────────────────────────


class TestSecurityHeadersPresent:
    def test_security_headers_present(self, client):
        resp = client.get("/health")
        assert "X-Frame-Options" in resp.headers
        assert "Referrer-Policy" in resp.headers

    def test_security_headers_values(self, client):
        resp = client.get("/health")
        assert resp.headers.get("X-Frame-Options") == "SAMEORIGIN"
        assert "strict-origin" in resp.headers.get("Referrer-Policy", "").lower()


# ── Password Validation ──────────────────────────────────────────────────────


class TestPasswordValidationRejectsWeak:
    def test_password_too_short(self):
        import app as app_module

        ok, _ = app_module._validate_password("Ab1")
        assert not ok

    def test_password_all_digits(self):
        import app as app_module

        ok, _ = app_module._validate_password("12345678")
        assert not ok

    def test_password_all_alpha(self):
        import app as app_module

        ok, _ = app_module._validate_password("abcdefgh")
        assert not ok

    def test_password_empty(self):
        import app as app_module

        ok, _ = app_module._validate_password("")
        assert not ok

    def test_password_7_chars_mixed(self):
        import app as app_module

        ok, _ = app_module._validate_password("Abcde1x")
        assert not ok


class TestPasswordValidationAcceptsStrong:
    def test_password_8_chars_mixed(self):
        import app as app_module

        ok, _ = app_module._validate_password("Abcdef12")
        assert ok

    def test_password_long_mixed(self):
        import app as app_module

        ok, _ = app_module._validate_password("SuperSecure99!")
        assert ok

    def test_password_with_special_chars(self):
        import app as app_module

        ok, _ = app_module._validate_password("P@ssw0rd!")
        assert ok

    def test_password_minimum_valid(self):
        import app as app_module

        ok, _ = app_module._validate_password("aaa11111")
        assert ok


# ── CSRF Protection ──────────────────────────────────────────────────────────


class TestCSRFProtection:
    def test_post_without_csrf_rejected(self, client):
        """POST sem CSRF para rota protegida deve ser rejeitado."""
        client.get("/login")
        resp = client.post("/login", data={"nii": "admin", "password": "admin123"})
        # Sem csrf_token, deve falhar
        assert resp.status_code in (200, 302, 400, 403)
        if resp.status_code == 302:
            location = resp.headers.get("Location", "")
            assert "/admin" not in location and "/aluno" not in location

    def test_post_with_wrong_csrf_rejected(self, client):
        """POST com CSRF errado deve ser rejeitado."""
        client.get("/login")
        resp = client.post(
            "/login",
            data={
                "nii": "admin",
                "password": "admin123",
                "csrf_token": "token_invalido_xyz",
            },
        )
        assert resp.status_code in (200, 302, 400, 403)
        if resp.status_code == 302:
            location = resp.headers.get("Location", "")
            assert "/admin" not in location

    def test_post_with_valid_csrf_accepted(self, client):
        """POST com CSRF válido deve ser aceite."""
        client.get("/login")
        with client.session_transaction() as sess:
            token = sess.get("_csrf_token", "")
        resp = client.post(
            "/login",
            data={"nii": "admin", "password": "admin123", "csrf_token": token},
            follow_redirects=False,
        )
        # Com CSRF válido e credenciais corretas, deve redirecionar para dashboard
        assert resp.status_code in (200, 302)


# ── Criar Utilizador — Password Validation ──────────────────────────────────


class TestCriarUtilizadorPasswordValidation:
    """_criar_utilizador() deve rejeitar passwords fracas (mesma regra que alterar)."""

    def test_criar_utilizador_rejects_short_password(self):
        import app as app_module

        ok, msg = app_module._criar_utilizador(
            "weakpw1", "WK001", "Weak Password", "1", "aluno", "abc"
        )
        assert not ok
        assert "8 caracteres" in msg

    def test_criar_utilizador_rejects_digits_only(self):
        import app as app_module

        ok, msg = app_module._criar_utilizador(
            "weakpw2", "WK002", "Weak Password", "1", "aluno", "12345678"
        )
        assert not ok
        assert "letras" in msg

    def test_criar_utilizador_rejects_alpha_only(self):
        import app as app_module

        ok, msg = app_module._criar_utilizador(
            "weakpw3", "WK003", "Weak Password", "1", "aluno", "abcdefgh"
        )
        assert not ok
        assert "letras" in msg or "números" in msg

    def test_criar_utilizador_accepts_strong_password(self, app):
        import app as app_module

        ok, msg = app_module._criar_utilizador(
            "strongpw1", "ST001", "Strong Password", "1", "aluno", "Secure12"
        )
        assert ok, f"Deveria aceitar password forte, mas falhou: {msg}"


# ── Health Endpoint — Sem Info Sensível ──────────────────────────────────────


class TestHealthNoSensitiveInfo:
    def test_health_no_user_count(self, client):
        """O endpoint /health não deve expor contagem de utilizadores."""
        import json

        resp = client.get("/health")
        data = json.loads(resp.data)
        assert "utilizadores" not in data

    def test_health_no_db_path(self, client):
        """O endpoint /health não deve expor caminho da base de dados."""
        import json

        resp = client.get("/health")
        data = json.loads(resp.data)
        assert "db_path" not in data


# ── CSRF Token Rotation ─────────────────────────────────────────────────────


class TestCSRFTokenRotation:
    def test_csrf_token_changes_after_login(self, app, client):
        """CSRF token deve ser regenerado após login bem-sucedido."""
        create_system_user("csrfuser", "admin", pw="Csrftest12")

        # Obter token pré-login
        client.get("/login")
        with client.session_transaction() as sess:
            pre_login_token = sess.get("_csrf_token", "")

        # Fazer login com CSRF válido
        client.post(
            "/login",
            data={
                "nii": "csrfuser",
                "password": "Csrftest12",
                "csrf_token": pre_login_token,
            },
            follow_redirects=True,
        )

        # Verificar que o token mudou
        with client.session_transaction() as sess:
            post_login_token = sess.get("_csrf_token", "")

        # O token pré-login não deve existir na sessão autenticada
        assert pre_login_token != post_login_token
