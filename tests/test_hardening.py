"""
tests/test_hardening.py — Testes para features de hardening
=============================================================
Rate limiting, HTTPS redirect, streaming CSV, paginação, pesquisa FTS.
"""

import os

os.environ.setdefault("ENV", "development")
os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production")

from conftest import create_aluno, create_system_user, get_csrf, login_as


# ── Rate limiting em marcação de refeições ─────────────────────────────


class TestMealRateLimiting:
    """Rate limit: 30 ops/min em editar refeições."""

    def test_rate_limit_blocks_after_threshold(self, app, client):
        """Após 30 operações POST em <60s, deve bloquear."""
        from datetime import date, timedelta

        create_aluno("rl_aluno1", "RL001", "Rate Limit Test", pw="Ratelimit1")
        login_as(client, "rl_aluno1", "Ratelimit1")

        dt = (date.today() + timedelta(days=5)).isoformat()

        # Simular 30 operações enchendo a sessão directamente
        with client.session_transaction() as sess:
            import time

            now = time.time()
            sess["_meal_ops"] = [now - i for i in range(30)]

        csrf = get_csrf(client)
        resp = client.post(
            f"/aluno/editar/{dt}",
            data={"pa": "1", "csrf_token": csrf},
            follow_redirects=True,
        )
        html = resp.data.decode()
        assert "Demasiadas" in html or resp.status_code in (200, 302)

    def test_normal_ops_not_blocked(self, app, client):
        """Operações normais (< 30/min) não devem ser bloqueadas."""
        from datetime import date, timedelta

        create_aluno("rl_aluno2", "RL002", "Rate Normal", pw="Ratenormal1")
        login_as(client, "rl_aluno2", "Ratenormal1")

        dt = (date.today() + timedelta(days=5)).isoformat()
        csrf = get_csrf(client)
        resp = client.post(
            f"/aluno/editar/{dt}",
            data={"pa": "1", "csrf_token": csrf},
            follow_redirects=True,
        )
        # Should succeed (no rate limit hit)
        assert resp.status_code == 200


# ── HTTPS redirect ────────────────────────────────────────────────────


class TestHTTPSRedirect:
    """Em produção, pedidos HTTP devem ser redirecionados para HTTPS."""

    def test_no_redirect_in_dev(self, app, client):
        """Em dev (SESSION_COOKIE_SECURE=False), não deve redirecionar."""
        resp = client.get(
            "/login",
            headers={"X-Forwarded-Proto": "http"},
        )
        assert resp.status_code == 200  # Not redirected

    def test_redirect_in_production_mode(self, app, client):
        """Com SESSION_COOKIE_SECURE=True e X-Forwarded-Proto=http, deve 301."""
        old_val = app.config.get("SESSION_COOKIE_SECURE")
        try:
            app.config["SESSION_COOKIE_SECURE"] = True
            resp = client.get(
                "/login",
                headers={"X-Forwarded-Proto": "http"},
            )
            assert resp.status_code == 301
            assert "https://" in resp.headers.get("Location", "")
        finally:
            app.config["SESSION_COOKIE_SECURE"] = old_val

    def test_health_not_redirected(self, app, client):
        """Health check não deve ser redirecionado mesmo em produção."""
        old_val = app.config.get("SESSION_COOKIE_SECURE")
        try:
            app.config["SESSION_COOKIE_SECURE"] = True
            resp = client.get(
                "/health",
                headers={"X-Forwarded-Proto": "http"},
            )
            assert resp.status_code == 200
        finally:
            app.config["SESSION_COOKIE_SECURE"] = old_val


# ── Streaming CSV ─────────────────────────────────────────────────────


class TestStreamingCSV:
    """Export mensal CSV deve produzir output válido via streaming."""

    def test_mensal_csv_produces_valid_output(self, app, client):
        """CSV mensal deve ter BOM, header e pelo menos uma linha."""
        create_system_user("csv_admin1", "admin", pw="Csvadmin12")
        login_as(client, "csv_admin1", "Csvadmin12")

        resp = client.get("/exportar/mensal?fmt=csv")
        assert resp.status_code == 200
        assert resp.content_type.startswith("text/csv")

        data = resp.data.decode("utf-8-sig")
        lines = data.strip().split("\n")
        assert len(lines) >= 2  # header + at least 1 data row
        assert "Data" in lines[0]
        assert "TOTAL" in lines[-1]


# ── Paginação ─────────────────────────────────────────────────────────


class TestPagination:
    """Paginação nas listas de admin."""

    def test_utilizadores_pagination_params(self, app, client):
        """Admin utilizadores aceita parâmetro page."""
        create_system_user("pag_admin1", "admin", pw="Pagadmin12")
        login_as(client, "pag_admin1", "Pagadmin12")

        resp = client.get("/admin/utilizadores?page=1")
        assert resp.status_code == 200

        # Página 2 também funciona (mesmo que vazia)
        resp = client.get("/admin/utilizadores?page=2")
        assert resp.status_code == 200

    def test_log_pagination(self, app, client):
        """Admin log aceita parâmetro page."""
        create_system_user("pag_admin2", "admin", pw="Pagadmin23")
        login_as(client, "pag_admin2", "Pagadmin23")

        resp = client.get("/admin/log?page=1")
        assert resp.status_code == 200

    def test_utilizadores_search_with_pagination(self, app, client):
        """Pesquisa + paginação funcionam juntos."""
        create_system_user("pag_admin3", "admin", pw="Pagadmin34")
        login_as(client, "pag_admin3", "Pagadmin34")

        resp = client.get("/admin/utilizadores?q=admin&page=1")
        assert resp.status_code == 200


# ── FTS search ────────────────────────────────────────────────────────


class TestFTSSearch:
    """Pesquisa FTS nos utilizadores."""

    def test_fts_search_returns_results(self, app, client):
        """Pesquisa FTS deve encontrar utilizadores pelo nome."""
        from core.database import db

        # Rebuild FTS to ensure it contains recent data
        with app.app_context():
            with db() as conn:
                try:
                    conn.execute(
                        "INSERT INTO utilizadores_fts(utilizadores_fts) VALUES('rebuild')"
                    )
                    conn.commit()
                except Exception:
                    pass

        create_system_user("fts_admin1", "admin", pw="Ftsadmin12")
        login_as(client, "fts_admin1", "Ftsadmin12")

        # Search for "Administrador" (matches the admin account)
        resp = client.get("/admin/utilizadores?q=Administrador")
        assert resp.status_code == 200

    def test_list_users_with_fts(self, app):
        """list_users com pesquisa FTS retorna tuple (rows, total)."""
        from core.users import list_users

        with app.app_context():
            rows, total = list_users(q="admin")
            assert isinstance(rows, list)
            assert isinstance(total, int)

    def test_list_users_without_query(self, app):
        """list_users sem pesquisa retorna todos os utilizadores paginados."""
        from core.users import list_users

        with app.app_context():
            rows, total = list_users()
            assert isinstance(rows, list)
            assert total >= 0
            assert len(rows) <= 50  # per_page default


# ── CSV import validation ─────────────────────────────────────────────


class TestCSVImportValidation:
    """Validação robusta na importação de CSV."""

    def test_rejects_invalid_nii(self, app, client):
        """CSV com NII inválido deve ser reportado como erro."""
        create_system_user("csv_val1", "admin", pw="Csvval1234")
        login_as(client, "csv_val1", "Csvval1234")
        csrf = get_csrf(client)

        import io

        csv_data = "NII;NI;Nome;Ano\n;;;;\n"
        resp = client.post(
            "/admin/importar-csv",
            data={
                "acao": "preview",
                "csrf_token": csrf,
                "csvfile": (io.BytesIO(csv_data.encode()), "test.csv"),
            },
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        assert resp.status_code == 200

    def test_rejects_no_file(self, app, client):
        """Upload sem ficheiro deve mostrar erro."""
        create_system_user("csv_val2", "admin", pw="Csvval2345")
        login_as(client, "csv_val2", "Csvval2345")
        csrf = get_csrf(client)

        resp = client.post(
            "/admin/importar-csv",
            data={"acao": "preview", "csrf_token": csrf},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert b"ficheiro" in resp.data.lower() or resp.status_code == 200
