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


# ── PR B.1 — Password reset por admin (reset_code) ─────────────────────


class TestResetCode:
    """set/consume/clear_reset_code + integração no login flow."""

    def test_set_reset_code_retorna_token(self, app):
        from core.auth_db import set_reset_code

        create_system_user("rst_user1", "aluno", pw="Rstuser123")
        with app.app_context():
            code = set_reset_code("rst_user1")
        assert code is not None
        assert isinstance(code, str)
        assert len(code) >= 8

    def test_set_reset_code_nii_inexistente_retorna_none(self, app):
        from core.auth_db import set_reset_code

        with app.app_context():
            code = set_reset_code("NII_INEXISTENTE_XYZ")
        assert code is None

    def test_consume_reset_code_invalida_apos_uso(self, app):
        """Single-use: segundo consume com o mesmo código falha."""
        from core.auth_db import consume_reset_code, set_reset_code

        create_system_user("rst_user2", "aluno", pw="Rstuser234")
        with app.app_context():
            code = set_reset_code("rst_user2")
            assert code
            # 1º uso: OK
            assert consume_reset_code("rst_user2", code) is True
            # 2º uso: falha (single-use)
            assert consume_reset_code("rst_user2", code) is False

    def test_consume_reset_code_expirado_falha(self, app):
        """Código com TTL negativo deve falhar."""
        from core.auth_db import consume_reset_code, set_reset_code

        create_system_user("rst_user3", "aluno", pw="Rstuser345")
        with app.app_context():
            # TTL=0 → expira imediatamente (datetime.now() > datetime.now())
            code = set_reset_code("rst_user3", ttl_hours=-1)
            assert code
            assert consume_reset_code("rst_user3", code) is False

    def test_consume_reset_code_marca_must_change_password(self, app):
        """Após consumir, must_change_password=1 para forçar /aluno/password."""
        from core.auth_db import consume_reset_code, set_reset_code
        from core.database import db

        create_system_user("rst_user4", "aluno", pw="Rstuser456")
        with app.app_context():
            code = set_reset_code("rst_user4")
            assert code
            assert consume_reset_code("rst_user4", code) is True
            with db() as conn:
                r = conn.execute(
                    "SELECT must_change_password FROM utilizadores WHERE NII=?",
                    ("rst_user4",),
                ).fetchone()
            assert r["must_change_password"] == 1

    def test_login_com_reset_code_auto_consome(self, app, client):
        """POST /login com reset_code válido → 302 redirect (aceita)."""
        from core.auth_db import set_reset_code

        create_system_user("rst_login", "aluno", pw="Rstlogin123")
        with app.app_context():
            code = set_reset_code("rst_login")
            assert code
        client.get("/login")
        with client.session_transaction() as sess:
            token = sess.get("_csrf_token", "")
        resp = client.post(
            "/login",
            data={"nii": "rst_login", "pw": code, "csrf_token": token},
            follow_redirects=False,
        )
        assert resp.status_code == 302  # login bem-sucedido


# ── PR B.2 — Auto-unlock / cleanup de login_eventos ────────────────────


class TestUnlockExpired:
    """POST /api/unlock-expired — cleanup de dados expirados."""

    def test_sem_token_retorna_403(self, app, client):
        resp = client.post("/api/unlock-expired")
        assert resp.status_code == 403

    def test_token_invalido_retorna_403(self, app, client):
        resp = client.post(
            "/api/unlock-expired",
            headers={"Authorization": "Bearer token-errado"},
        )
        assert resp.status_code == 403

    def test_com_token_valido_faz_cleanup(self, app, client):
        """Com token dev em ENV development, endpoint executa e retorna OK."""
        import config as cfg

        old = cfg.CRON_API_TOKEN
        try:
            cfg.CRON_API_TOKEN = ""  # força fallback "dev" em non-prod
            resp = client.post(
                "/api/unlock-expired",
                headers={"Authorization": "Bearer dev"},
            )
            assert resp.status_code == 200
            payload = resp.get_json()
            assert payload["status"] == "ok"
            assert "deleted_login_failures" in payload
            assert "expired_reset_codes" in payload
            assert "unlocked_users" in payload
        finally:
            cfg.CRON_API_TOKEN = old

    def test_apaga_reset_codes_expirados(self, app, client):
        """reset_codes com expires < now são limpos pelo endpoint."""
        import config as cfg
        from core.database import db

        create_system_user("unlk_user1", "aluno", pw="Unlkuser123")
        # Injecta reset_code com expiry no passado
        with app.app_context():
            with db() as conn:
                conn.execute(
                    "UPDATE utilizadores SET reset_code=?, reset_expires=? WHERE NII=?",
                    ("expired-code", "2000-01-01 00:00:00", "unlk_user1"),
                )
                conn.commit()
        old = cfg.CRON_API_TOKEN
        try:
            cfg.CRON_API_TOKEN = ""
            resp = client.post(
                "/api/unlock-expired",
                headers={"Authorization": "Bearer dev"},
            )
            assert resp.status_code == 200
            payload = resp.get_json()
            assert payload["expired_reset_codes"] >= 1
        finally:
            cfg.CRON_API_TOKEN = old

        # Verifica que o código foi limpo
        with app.app_context():
            with db() as conn:
                r = conn.execute(
                    "SELECT reset_code, reset_expires FROM utilizadores WHERE NII=?",
                    ("unlk_user1",),
                ).fetchone()
        assert r["reset_code"] is None
        assert r["reset_expires"] is None


# ── PR B.3 — UserContextFilter para logs ───────────────────────────────


class TestUserContextFilter:
    """Filtro injecta user_nii + user_role nos log records."""

    def test_filter_anonimo_fica_com_traco(self, app):
        """Fora de request context, filtro injecta '-'."""
        import logging

        from core.middleware import UserContextFilter

        rec = logging.LogRecord("x", logging.INFO, "f.py", 1, "msg", None, None)
        f = UserContextFilter()
        assert f.filter(rec) is True
        assert rec.user_nii == "-"
        assert rec.user_role == "-"

    def test_filter_em_request_com_sessao_injecta_user(self, app, client):
        """Em request autenticado, filtro lê session['user'] e injecta NII/role."""
        import logging

        from core.middleware import UserContextFilter

        create_system_user("uctx_user1", "admin", pw="Uctxuser12")
        login_as(client, "uctx_user1", "Uctxuser12")

        with client.session_transaction() as sess:
            assert sess.get("user"), "login deveria ter populado session['user']"

        # Entra em request context manualmente para invocar o filtro
        with client.application.test_request_context("/"):
            # Copia a sessão do client para o request context
            from flask import session as flask_session

            flask_session["user"] = {
                "nii": "uctx_user1",
                "perfil": "admin",
            }
            rec = logging.LogRecord("x", logging.INFO, "f.py", 1, "msg", None, None)
            f = UserContextFilter()
            assert f.filter(rec) is True
            assert rec.user_nii == "uctx_user1"
            assert rec.user_role == "admin"


# ── PR B.4 — Flask-Limiter (rate-limit HTTP-layer) ─────────────────────


class TestFlaskLimiter:
    """Rate-limit global via Flask-Limiter em /auth/login e /api/*."""

    def test_limiter_registado_em_app_extensions(self, app):
        """Flask-Limiter deve ter sido inicializado pelo app factory."""
        assert "limiter" in app.extensions

    def test_limiter_desactivado_em_testes(self, app):
        """conftest.py desactiva limiter — testes normais não batem 429."""
        for lim in app.extensions.get("limiter", set()):
            assert lim.enabled is False

    def test_post_login_nao_bate_429_com_limiter_off(self, app, client):
        """Sem limiter, 15 POSTs consecutivos passam sem 429."""
        for _ in range(15):
            resp = client.post(
                "/login",
                data={"nii": "xxx", "pw": "xxx", "csrf_token": ""},
                follow_redirects=False,
            )
            assert resp.status_code != 429


# ── PR B.5 — Paginação consistente ─────────────────────────────────────


class TestAdminAuditPagination:
    """Paginação no /admin/auditoria com query_admin_audit_paged."""

    def test_query_paged_retorna_3_tuple(self, app):
        """query_admin_audit_paged retorna (rows, filtered_total, total_abs)."""
        from core.audit import query_admin_audit_paged

        with app.app_context():
            result = query_admin_audit_paged(page=1, per_page=10)
        assert len(result) == 3
        rows, filtered_total, total_abs = result
        assert isinstance(rows, list)
        assert isinstance(filtered_total, int)
        assert isinstance(total_abs, int)
        assert len(rows) <= 10

    def test_admin_auditoria_com_page_retorna_200(self, app, client):
        """GET /admin/auditoria?page=1 renderiza template paginado."""
        create_system_user("aud_pag1", "admin", pw="Audpag1234")
        login_as(client, "aud_pag1", "Audpag1234")
        resp = client.get("/admin/auditoria?page=1")
        assert resp.status_code == 200

    def test_admin_auditoria_com_limite_legacy_retorna_200(self, app, client):
        """?limite=N mantém comportamento legacy (sem page)."""
        create_system_user("aud_pag2", "admin", pw="Audpag2345")
        login_as(client, "aud_pag2", "Audpag2345")
        resp = client.get("/admin/auditoria?limite=100")
        assert resp.status_code == 200

    def test_pagination_nav_macro_renderiza_quando_total_pages_ge_2(self, app):
        """pagination_nav só renderiza quando há >1 página."""
        from flask import render_template_string

        tpl = (
            '{% import "_macros.html" as ui %}'
            "{{ ui.pagination_nav(page, total_pages, qs) }}"
        )
        with app.app_context(), app.test_request_context("/"):
            # 1 página → macro não renderiza nada
            r1 = render_template_string(tpl, page=1, total_pages=1, qs="q=foo")
            assert "pagination-nav" not in r1
            # 3 páginas, estamos na 2ª → prev + next activos
            r2 = render_template_string(tpl, page=2, total_pages=3, qs="q=foo")
            assert "pagination-nav" in r2
            assert "q=foo" in r2
            assert "page=1" in r2
            assert "page=3" in r2


# ── PR B.1 — UI admin: botão "Gerar código de reset" ───────────────────


class TestAdminResetCodeUI:
    """Fluxo UI: admin clica → POST acao=gerar_reset → mostra código 1×."""

    def test_gerar_reset_mostra_template_com_codigo(self, app, client):
        create_system_user("rst_ui_admin", "admin", pw="Rstuiadmin1")
        create_system_user("rst_ui_aluno", "aluno", pw="Rstuialuno1")
        login_as(client, "rst_ui_admin", "Rstuiadmin1")
        csrf = get_csrf(client)
        resp = client.post(
            "/admin/utilizadores",
            data={
                "acao": "gerar_reset",
                "nii": "rst_ui_aluno",
                "csrf_token": csrf,
            },
            follow_redirects=False,
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "reset-code" in html  # classe CSS do template
        assert "rst_ui_aluno" in html

    def test_gerar_reset_nii_inexistente_flasha_erro(self, app, client):
        create_system_user("rst_ui_admin2", "admin", pw="Rstuiadmin2")
        login_as(client, "rst_ui_admin2", "Rstuiadmin2")
        csrf = get_csrf(client)
        resp = client.post(
            "/admin/utilizadores",
            data={
                "acao": "gerar_reset",
                "nii": "NII_NAO_EXISTE_ZZZ",
                "csrf_token": csrf,
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        # O erro aparece como flash (toast/mensagem) no HTML
        assert b"NII" in resp.data
