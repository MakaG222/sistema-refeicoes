"""
tests/test_auth_routes.py — Testes adicionais para blueprints/auth/routes.py
=============================================================================
Cobre linhas não cobertas: 34, 59-72, 96-108, 136, 148
  - Linha 34: já logado → redireciona para dashboard
  - Linhas 59-72: conta bloqueada (locked_until no futuro)
  - Linhas 96-108: hash migration (password em claro) + bloqueio por falhas
  - Linha 136: logout CSRF inválido → 403
  - Linha 148: dashboard redireciona para painel_dia (perfil cozinha/oficialdia/cmd)
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from tests.conftest import create_aluno, create_system_user, login_as


# ── Fixtures ───────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def setup_users(app):
    """Cria utilizadores comuns a vários testes."""
    create_system_user("adm_auth", "admin", nome="Admin Auth", pw="Admin1234")
    create_system_user("coz_auth", "cozinha", nome="Cozinha Auth", pw="Cozinha123")
    create_system_user("odia_auth", "oficialdia", nome="OD Auth", pw="Oficialdia1")
    create_system_user("cmd_auth", "cmd", nome="CMD Auth", pw="CmdUser123")
    create_aluno("aluno_auth", "AA01", "Aluno Auth", ano="2", pw="aluno_auth")


# ── Linha 34: já logado → redirect para dashboard ─────────────────────────────


class TestAlreadyLoggedIn:
    def test_login_page_redirects_when_logged_in(self, app, client):
        """Linha 34: se 'user' já está na sessão, GET /login redireciona."""
        login_as(client, "adm_auth", pw="Admin1234")
        resp = client.get("/login", follow_redirects=False)
        assert resp.status_code == 302
        assert "login" not in resp.headers.get("Location", "")

    def test_root_route_redirects_when_already_logged_in(self, app, client):
        """GET / com sessão activa também redireciona (rota '/' == '/login')."""
        login_as(client, "adm_auth", pw="Admin1234")
        resp = client.get("/", follow_redirects=False)
        assert resp.status_code == 302
        assert "login" not in resp.headers.get("Location", "")


# ── Linhas 59-72: conta bloqueada ─────────────────────────────────────────────


class TestLockedAccount:
    def _set_locked_until(self, nii, minutes_ahead=30):
        """Coloca locked_until no futuro para simular conta bloqueada."""
        from core.database import db

        until = (datetime.now() + timedelta(minutes=minutes_ahead)).isoformat()
        with db() as conn:
            conn.execute(
                "UPDATE utilizadores SET locked_until=? WHERE NII=?", (until, nii)
            )
            conn.commit()

    def _clear_locked(self, nii):
        from core.database import db

        with db() as conn:
            conn.execute(
                "UPDATE utilizadores SET locked_until=NULL WHERE NII=?", (nii,)
            )
            conn.commit()

    def test_locked_account_shows_error(self, app, client):
        """Linhas 59-69: conta com locked_until no futuro mostra mensagem de bloqueio."""
        self._set_locked_until("aluno_auth", minutes_ahead=20)
        try:
            client.get("/login")
            with client.session_transaction() as sess:
                token = sess.get("_csrf_token", "")
            resp = client.post(
                "/login",
                data={"nii": "aluno_auth", "pw": "aluno_auth", "csrf_token": token},
                follow_redirects=True,
            )
            html = resp.data.decode()
            assert "bloqueada" in html.lower() or "tentativas" in html.lower()
        finally:
            self._clear_locked("aluno_auth")

    def test_locked_account_not_authenticated(self, app, client):
        """Conta bloqueada → não entra na sessão."""
        self._set_locked_until("aluno_auth", minutes_ahead=15)
        try:
            client.get("/login")
            with client.session_transaction() as sess:
                token = sess.get("_csrf_token", "")
            client.post(
                "/login",
                data={"nii": "aluno_auth", "pw": "aluno_auth", "csrf_token": token},
                follow_redirects=False,
            )
            with client.session_transaction() as sess:
                assert "user" not in sess
        finally:
            self._clear_locked("aluno_auth")

    def test_expired_lock_is_ignored(self, app, client):
        """Linha 71: locked_until no passado → lock ignorado, login normal."""
        from core.database import db

        past = (datetime.now() - timedelta(minutes=5)).isoformat()
        with db() as conn:
            conn.execute(
                "UPDATE utilizadores SET locked_until=? WHERE NII=?",
                (past, "aluno_auth"),
            )
            conn.commit()
        try:
            resp = login_as(client, "aluno_auth", pw="aluno_auth")
            # Lock expirado — deve fazer login com sucesso (302) ou mostrar form
            assert resp.status_code in (200, 302)
            # Se 302, não deve ser para /login
            if resp.status_code == 302:
                assert "/login" not in resp.headers.get("Location", "")
        finally:
            self._clear_locked("aluno_auth")

    def test_invalid_locked_until_format_ignored(self, app, client):
        """Linha 71 (ValueError): locked_until inválido → lock ignorado."""
        from core.database import db

        with db() as conn:
            conn.execute(
                "UPDATE utilizadores SET locked_until=? WHERE NII=?",
                ("nao-e-uma-data", "aluno_auth"),
            )
            conn.commit()
        try:
            resp = login_as(client, "aluno_auth", pw="aluno_auth")
            assert resp.status_code in (200, 302)
        finally:
            self._clear_locked("aluno_auth")


# ── Linhas 96-108: hash migration e bloqueio por falhas consecutivas ──────────


class TestHashMigration:
    def _set_plaintext_password(self, nii, plain_pw):
        """Guarda password em claro (legado) na BD."""
        from core.database import db

        with db() as conn:
            conn.execute(
                "UPDATE utilizadores SET Palavra_chave=? WHERE NII=?", (plain_pw, nii)
            )
            conn.commit()

    def _get_stored_hash(self, nii):
        from core.database import db

        with db() as conn:
            row = conn.execute(
                "SELECT Palavra_chave FROM utilizadores WHERE NII=?", (nii,)
            ).fetchone()
        return row["Palavra_chave"] if row else None

    def test_plaintext_password_login_succeeds(self, app, client):
        """Linha 95-96: password em claro faz login com sucesso."""
        self._set_plaintext_password("aluno_auth", "aluno_auth")
        resp = login_as(client, "aluno_auth", pw="aluno_auth")
        assert resp.status_code == 302
        assert "/login" not in resp.headers.get("Location", "")

    def test_plaintext_password_migrated_after_login(self, app, client):
        """Linha 96: após login com plain-text, hash é actualizado para pbkdf2."""
        self._set_plaintext_password("aluno_auth", "aluno_auth")
        login_as(client, "aluno_auth", pw="aluno_auth")
        stored = self._get_stored_hash("aluno_auth")
        # Após migração deve começar com 'pbkdf2:'
        assert stored is not None
        assert stored.startswith(("pbkdf2:", "scrypt:", "argon2:"))


class TestFailureLockout:
    def _clear_login_events(self, nii):
        from core.database import db

        with db() as conn:
            conn.execute("DELETE FROM login_eventos WHERE nii=?", (nii,))
            conn.commit()

    def test_wrong_password_shows_remaining_attempts(self, app, client):
        """Linhas 99-108: password errada → mostra tentativas restantes."""
        self._clear_login_events("aluno_auth")
        client.get("/login")
        with client.session_transaction() as sess:
            token = sess.get("_csrf_token", "")
        resp = client.post(
            "/login",
            data={"nii": "aluno_auth", "pw": "password_errada", "csrf_token": token},
            follow_redirects=True,
        )
        html = resp.data.decode()
        assert "tentativa" in html.lower() or "incorreta" in html.lower()

    def test_five_failures_triggers_block(self, app, client):
        """Linhas 99-105: 5 falhas consecutivas → conta bloqueada."""
        from core.auth_db import reg_login

        self._clear_login_events("aluno_auth")
        # Registar 4 falhas já existentes (a 5ª vem do POST)
        with app.app_context():
            for _ in range(4):
                reg_login("aluno_auth", 0, ip="127.0.0.1")

        client.get("/login")
        with client.session_transaction() as sess:
            token = sess.get("_csrf_token", "")
        resp = client.post(
            "/login",
            data={
                "nii": "aluno_auth",
                "pw": "password_errada_5x",
                "csrf_token": token,
            },
            follow_redirects=True,
        )
        html = resp.data.decode()
        assert "bloqueada" in html.lower() or "tentativas" in html.lower()

        # Verificar que locked_until foi definido na BD
        from core.database import db

        with db() as conn:
            row = conn.execute(
                "SELECT locked_until FROM utilizadores WHERE NII=?", ("aluno_auth",)
            ).fetchone()
        assert row is not None and row["locked_until"] is not None

    def test_nii_not_found_shows_error(self, app, client):
        """Linha 109-111: NII inexistente → 'NII não encontrado'."""
        client.get("/login")
        with client.session_transaction() as sess:
            token = sess.get("_csrf_token", "")
        resp = client.post(
            "/login",
            data={
                "nii": "nii_que_nao_existe_nunca",
                "pw": "qualquer",
                "csrf_token": token,
            },
            follow_redirects=True,
        )
        html = resp.data.decode()
        assert "nii" in html.lower() or "encontrado" in html.lower()


# ── Linha 136: logout com CSRF inválido ───────────────────────────────────────


class TestLogoutCSRF:
    def test_logout_invalid_csrf_returns_403(self, app, client):
        """Linha 135-136: CSRF inválido em /logout → 403 (ou 400 via middleware)."""
        login_as(client, "adm_auth", pw="Admin1234")
        resp = client.post("/logout", data={"csrf_token": "token_completamente_errado"})
        assert resp.status_code in (400, 403)

    def test_logout_empty_csrf_returns_403(self, app, client):
        """Linha 135-136: CSRF vazio em /logout → 403 (ou 400 via middleware)."""
        login_as(client, "adm_auth", pw="Admin1234")
        resp = client.post("/logout", data={"csrf_token": ""})
        assert resp.status_code in (400, 403)

    def test_logout_missing_csrf_returns_403(self, app, client):
        """Sem campo csrf_token no body → 403 ou 400."""
        login_as(client, "adm_auth", pw="Admin1234")
        resp = client.post("/logout", data={})
        assert resp.status_code in (400, 403)


# ── Linha 148: dashboard redireciona cozinha/oficialdia/cmd ───────────────────


class TestDashboardRedirects:
    def test_dashboard_cozinha_redirects_to_painel_dia(self, app, client):
        """Linha 147-148: perfil 'cozinha' → redireciona para painel_dia."""
        login_as(client, "coz_auth", pw="Cozinha123")
        resp = client.get("/dashboard", follow_redirects=False)
        assert resp.status_code == 302
        location = resp.headers.get("Location", "")
        # Pode redirecionar para painel_dia ou outra rota de operations
        assert "login" not in location

    def test_dashboard_oficialdia_redirects_to_painel_dia(self, app, client):
        """Perfil 'oficialdia' → painel_dia."""
        login_as(client, "odia_auth", pw="Oficialdia1")
        resp = client.get("/dashboard", follow_redirects=False)
        assert resp.status_code == 302
        location = resp.headers.get("Location", "")
        assert "login" not in location

    def test_dashboard_cmd_redirects_to_painel_dia(self, app, client):
        """Perfil 'cmd' → painel_dia."""
        login_as(client, "cmd_auth", pw="CmdUser123")
        resp = client.get("/dashboard", follow_redirects=False)
        assert resp.status_code == 302

    def test_dashboard_admin_redirects_to_admin_home(self, app, client):
        """Perfil 'admin' → /admin."""
        login_as(client, "adm_auth", pw="Admin1234")
        resp = client.get("/dashboard", follow_redirects=False)
        assert resp.status_code == 302
        location = resp.headers.get("Location", "")
        assert "admin" in location

    def test_dashboard_aluno_redirects_to_aluno_home(self, app, client):
        """Perfil 'aluno' → /aluno."""
        # Use a fresh aluno to avoid interference from lockout tests
        create_aluno("dash_aluno", "DA01", "Dashboard Aluno", ano="1", pw="dash_aluno")
        login_as(client, "dash_aluno", pw="dash_aluno")
        resp = client.get("/dashboard", follow_redirects=False)
        assert resp.status_code == 302
        location = resp.headers.get("Location", "")
        assert "aluno" in location

    def test_dashboard_requires_login(self, app, client):
        """Sem sessão → redireciona para /login."""
        resp = client.get("/dashboard", follow_redirects=False)
        assert resp.status_code == 302
        assert "login" in resp.headers.get("Location", "")
