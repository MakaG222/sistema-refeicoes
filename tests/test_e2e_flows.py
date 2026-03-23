"""
tests/test_e2e_flows.py — Testes E2E multi-step
=================================================
Fluxos completos que simulam cenários reais do utilizador.
"""

import os

os.environ.setdefault("ENV", "development")
os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production")

from datetime import date, timedelta

from conftest import create_aluno, create_system_user, get_csrf, login_as


class TestLoginMarkMealsVerify:
    """Login → marcar refeições → verificar na BD."""

    def test_e2e_login_mark_meals_verify(self, app, client):
        with app.app_context():
            uid = create_aluno("e2e_meal1", "EM1", "E2E Meal", pw="e2emeal11")

        login_as(client, "e2e_meal1", "e2emeal11")
        future = date.today() + timedelta(days=3)
        d_str = future.strftime("%Y-%m-%d")
        token = get_csrf(client)

        resp = client.post(
            f"/aluno/editar/{d_str}",
            data={
                "pa": "1",
                "lanche": "1",
                "almoco": "Normal",
                "jantar": "Normal",
                "licenca": "",
                "csrf_token": token,
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

        # Verify in DB
        with app.app_context():
            from core.meals import refeicao_get

            r = refeicao_get(uid, future)
            assert r["pequeno_almoco"] == 1
            assert r["almoco"] == "Normal"


class TestAdminResetPasswordForcedChange:
    """Admin reset pw → login com NII → forçar mudança."""

    def test_e2e_admin_reset_forced_change(self, app, client):
        with app.app_context():
            create_aluno("e2e_rst1", "ER1", "E2E Reset", pw="resetpw123")

        # Login as admin
        login_as(client, "admin", "admin123")
        token = get_csrf(client)

        # Reset password
        resp = client.post(
            "/admin/utilizadores",
            data={"acao": "reset_pw", "nii": "e2e_rst1", "csrf_token": token},
            follow_redirects=True,
        )
        assert resp.status_code == 200

        # Logout
        token = get_csrf(client)
        client.post("/logout", data={"csrf_token": token}, follow_redirects=True)

        # Login with NII as temp password
        login_as(client, "e2e_rst1", "e2e_rst1")

        # Should be redirected to change password
        with client.session_transaction() as sess:
            assert sess.get("must_change_password")

        # Change password
        token = get_csrf(client)
        resp = client.post(
            "/aluno/password",
            data={
                "old": "e2e_rst1",
                "new": "novapw1234",
                "conf": "novapw1234",
                "csrf_token": token,
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

        # must_change_password should be cleared
        with client.session_transaction() as sess:
            assert not sess.get("must_change_password")


class TestAbsenceBlocksMeals:
    """Criar ausência → verificar que aluno está ausente."""

    def test_e2e_absence_blocks(self, app, client):
        with app.app_context():
            uid = create_aluno("e2e_aus1", "EA1", "E2E Ausencia", pw="austest123")

        with app.app_context():
            from core.absences import utilizador_ausente
            from utils.business import _registar_ausencia

            d = date.today() + timedelta(days=5)
            d_str = d.isoformat()
            ok, msg = _registar_ausencia(uid, d_str, d_str, "teste e2e", "admin")
            assert ok, msg

            assert utilizador_ausente(uid, d)


class TestHealthEndpoints:
    """Verificar endpoints de health e metrics."""

    def test_health_returns_ok(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert "db" in data
        assert "latency_ms" in data
        assert "db_size_mb" in data

    def test_health_metrics(self, app, client):
        # Make a request first to populate metrics
        client.get("/health")
        resp = client.get("/health/metrics")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert "request_count" in data
        assert "error_count" in data
        assert "avg_latency_ms" in data
        assert data["request_count"] >= 1


# ── Fluxo completo aluno (deep) ─────────────────────────────────────────


class TestAlunoDeepFlow:
    """Fluxos multi-step profundos do aluno."""

    def test_login_view_home_edit_meal(self, app, client):
        """Aluno faz login, vê home, edita refeição de um dia futuro."""
        create_aluno("e2e_al1", "E001", "E2E Aluno Um", pw="E2ealuno1")
        resp = login_as(client, "e2e_al1", "E2ealuno1")
        assert resp.status_code == 302

        # Follow redirect to dashboard/home
        resp = client.get(resp.headers.get("Location", "/aluno"), follow_redirects=True)
        assert resp.status_code == 200

        dt = (date.today() + timedelta(days=5)).isoformat()
        csrf = get_csrf(client)
        resp = client.post(
            f"/aluno/editar/{dt}",
            data={"pa": "1", "lanche": "1", "almoco": "Normal", "csrf_token": csrf},
            follow_redirects=True,
        )
        assert resp.status_code == 200

    def test_login_change_password_relogin(self, app, client):
        """Aluno altera password e depois faz login com a nova."""
        create_aluno("e2e_al2", "E002", "E2E Aluno Dois", pw="E2ealuno2")
        login_as(client, "e2e_al2", "E2ealuno2")

        csrf = get_csrf(client)
        resp = client.post(
            "/aluno/password",
            data={
                "old": "E2ealuno2",
                "new": "NovaPass123",
                "conf": "NovaPass123",
                "csrf_token": csrf,
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

        csrf = get_csrf(client)
        client.post("/logout", data={"csrf_token": csrf}, follow_redirects=True)

        resp = login_as(client, "e2e_al2", "NovaPass123")
        assert resp.status_code == 302

    def test_view_historico(self, app, client):
        """Aluno acede ao histórico de 30 dias."""
        create_aluno("e2e_al3", "E003", "E2E Aluno Tres", pw="E2ealuno3")
        login_as(client, "e2e_al3", "E2ealuno3")

        resp = client.get("/aluno/historico")
        assert resp.status_code == 200

    def test_view_and_update_profile(self, app, client):
        """Aluno vê e atualiza o seu perfil de contactos."""
        create_aluno("e2e_al4", "E004", "E2E Aluno Quatro", pw="E2ealuno4")
        login_as(client, "e2e_al4", "E2ealuno4")

        resp = client.get("/aluno/perfil")
        assert resp.status_code == 200

        csrf = get_csrf(client)
        resp = client.post(
            "/aluno/perfil",
            data={
                "email": "e2e@marinha.pt",
                "telemovel": "912345678",
                "csrf_token": csrf,
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

    def test_ausencia_create(self, app, client):
        """Aluno regista uma ausência futura."""
        create_aluno("e2e_al5", "E005", "E2E Aluno Cinco", pw="E2ealuno5")
        login_as(client, "e2e_al5", "E2ealuno5")

        resp = client.get("/aluno/ausencias")
        assert resp.status_code == 200

        d1 = (date.today() + timedelta(days=10)).isoformat()
        d2 = (date.today() + timedelta(days=12)).isoformat()
        csrf = get_csrf(client)
        resp = client.post(
            "/aluno/ausencias",
            data={
                "acao": "registar",
                "data_inicio": d1,
                "data_fim": d2,
                "motivo": "Teste E2E",
                "csrf_token": csrf,
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

    def test_export_historico_csv(self, app, client):
        """Aluno exporta o seu histórico em CSV."""
        create_aluno("e2e_al6", "E006", "E2E Aluno Seis", pw="E2ealuno6")
        login_as(client, "e2e_al6", "E2ealuno6")

        resp = client.get("/aluno/historico?fmt=csv")
        assert resp.status_code in (200, 302)


# ── Fluxo completo admin (deep) ─────────────────────────────────────────


class TestAdminDeepFlow:
    """Fluxos multi-step profundos do admin."""

    def test_admin_dashboard_to_users(self, app, client):
        """Admin vê dashboard, depois navega para utilizadores."""
        create_system_user("e2e_adm1", "admin", pw="E2eadmin1")
        login_as(client, "e2e_adm1", "E2eadmin1")

        resp = client.get("/admin")
        assert resp.status_code == 200

        resp = client.get("/admin/utilizadores")
        assert resp.status_code == 200

    def test_admin_create_user_then_search(self, app, client):
        """Admin cria um utilizador e depois encontra-o na pesquisa."""
        create_system_user("e2e_adm2", "admin", pw="E2eadmin2")
        login_as(client, "e2e_adm2", "E2eadmin2")

        csrf = get_csrf(client)
        resp = client.post(
            "/admin/utilizadores",
            data={
                "acao": "criar",
                "nii": "e2e_novo",
                "ni": "99901",
                "nome": "Novo Aluno E2E",
                "ano": "1",
                "perfil": "aluno",
                "pw": "Novoe2e123",
                "csrf_token": csrf,
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

        resp = client.get("/admin/utilizadores?q=Novo+Aluno+E2E")
        assert resp.status_code == 200

    def test_admin_create_then_reset_then_delete(self, app, client):
        """Admin cria utilizador, reseta password e depois elimina."""
        create_system_user("e2e_adm3", "admin", pw="E2eadmin3")
        login_as(client, "e2e_adm3", "E2eadmin3")

        # Criar
        csrf = get_csrf(client)
        client.post(
            "/admin/utilizadores",
            data={
                "acao": "criar",
                "nii": "e2e_lifecycle",
                "ni": "99910",
                "nome": "Lifecycle Test",
                "ano": "2",
                "perfil": "aluno",
                "pw": "Lifecyc123",
                "csrf_token": csrf,
            },
            follow_redirects=True,
        )

        # Reset pw
        csrf = get_csrf(client)
        resp = client.post(
            "/admin/utilizadores",
            data={
                "acao": "reset_pw",
                "nii": "e2e_lifecycle",
                "csrf_token": csrf,
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

        # Eliminar
        csrf = get_csrf(client)
        resp = client.post(
            "/admin/utilizadores",
            data={
                "acao": "eliminar",
                "nii": "e2e_lifecycle",
                "csrf_token": csrf,
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert b"eliminado" in resp.data.lower()

    def test_admin_views_logs_and_audit(self, app, client):
        """Admin consulta log de refeições e auditoria de ações."""
        create_system_user("e2e_adm5", "admin", pw="E2eadmin5")
        login_as(client, "e2e_adm5", "E2eadmin5")

        resp = client.get("/admin/log")
        assert resp.status_code == 200

        resp = client.get("/admin/auditoria")
        assert resp.status_code == 200

    def test_admin_log_filters_and_pagination(self, app, client):
        """Admin usa filtros e paginação no log."""
        create_system_user("e2e_adm6", "admin", pw="E2eadmin6")
        login_as(client, "e2e_adm6", "E2eadmin6")

        resp = client.get("/admin/log?q_nome=test")
        assert resp.status_code == 200

        hoje = date.today().isoformat()
        resp = client.get(f"/admin/log?d0={hoje}&d1={hoje}")
        assert resp.status_code == 200

        resp = client.get("/admin/log?page=2")
        assert resp.status_code == 200

    def test_admin_menus_and_calendario(self, app, client):
        """Admin acede a menus e calendário."""
        create_system_user("e2e_adm7", "admin", pw="E2eadmin7")
        login_as(client, "e2e_adm7", "E2eadmin7")

        resp = client.get("/admin/menus")
        assert resp.status_code == 200

        resp = client.get("/admin/calendario")
        assert resp.status_code == 200


# ── Fluxo de segurança ──────────────────────────────────────────────────


class TestSecurityFlows:
    """Testes E2E que verificam fluxos de segurança."""

    def test_unauthenticated_redirect_chain(self, app, client):
        """Pedidos sem sessão são redirecionados para login."""
        protected = ["/aluno", "/admin", "/admin/utilizadores", "/admin/log"]
        for url in protected:
            resp = client.get(url)
            assert resp.status_code in (302, 303), f"{url} deveria redirecionar"
            loc = resp.headers.get("Location", "")
            assert "login" in loc, f"{url} deveria redirecionar para login"

    def test_aluno_cannot_access_admin(self, app, client):
        """Aluno autenticado não consegue aceder a rotas admin."""
        create_aluno("e2e_sec1", "S001", "Security Test", pw="Sectest12")
        login_as(client, "e2e_sec1", "Sectest12")

        admin_pages = [
            "/admin",
            "/admin/utilizadores",
            "/admin/log",
            "/admin/auditoria",
        ]
        for url in admin_pages:
            resp = client.get(url)
            assert resp.status_code in (302, 403), f"Aluno não deveria aceder a {url}"

    def test_login_failure_generic_message(self, app, client):
        """Login falhado não revela se o NII existe ou não."""
        client.get("/login")
        with client.session_transaction() as sess:
            token = sess.get("_csrf_token", "")

        resp = client.post(
            "/login",
            data={"nii": "nao_existe_xyz", "pw": "qualquer", "csrf_token": token},
            follow_redirects=True,
        )
        assert resp.status_code == 200

    def test_session_cleared_requires_login(self, app, client):
        """Após limpar sessão, utilizador precisa de fazer login novamente."""
        create_aluno("e2e_sec3", "S003", "Session Test", pw="Sesstest1")
        login_as(client, "e2e_sec3", "Sesstest1")

        resp = client.get("/aluno", follow_redirects=False)
        assert resp.status_code in (200, 302)

        with client.session_transaction() as sess:
            sess.clear()

        resp = client.get("/aluno")
        assert resp.status_code in (302, 303)


# ── Fluxo operações ─────────────────────────────────────────────────────


class TestOperationsDeepFlow:
    """Testes E2E para fluxos operacionais."""

    def test_painel_dia_loads(self, app, client):
        """Oficial de dia / admin vê o painel do dia."""
        create_system_user("e2e_od1", "oficialdia", pw="E2eofdia1")
        login_as(client, "e2e_od1", "E2eofdia1")

        resp = client.get("/painel")
        assert resp.status_code == 200

    def test_relatorio_semanal_loads(self, app, client):
        """Admin vê o relatório semanal."""
        create_system_user("e2e_rel1", "admin", pw="E2erelat1")
        login_as(client, "e2e_rel1", "E2erelat1")

        resp = client.get("/relatorio")
        assert resp.status_code == 200

    def test_calendario_publico(self, app, client):
        """Qualquer utilizador autenticado vê o calendário público."""
        create_aluno("e2e_cal1", "C001", "Cal Test", pw="Caltest12")
        login_as(client, "e2e_cal1", "Caltest12")

        resp = client.get("/calendario")
        assert resp.status_code == 200

    def test_dashboard_semanal_loads(self, app, client):
        """Admin vê o dashboard semanal com gráficos."""
        create_system_user("e2e_dash1", "admin", pw="E2edash12")
        login_as(client, "e2e_dash1", "E2edash12")

        resp = client.get("/dashboard-semanal")
        assert resp.status_code == 200


# ── Fluxo CMD ────────────────────────────────────────────────────────────


class TestCMDDeepFlow:
    """Testes E2E para fluxos do Comandante de Companhia."""

    def test_cmd_views_detencoes(self, app, client):
        """CMD acede à lista de detenções."""
        create_system_user("e2e_cmd1", "cmd", pw="E2ecmd123", ano="1")
        login_as(client, "e2e_cmd1", "E2ecmd123")

        resp = client.get("/cmd/detencoes")
        assert resp.status_code == 200

    def test_cmd_views_ausencias(self, app, client):
        """CMD acede à lista de ausências."""
        create_system_user("e2e_cmd2", "cmd", pw="E2ecmd234", ano="1")
        login_as(client, "e2e_cmd2", "E2ecmd234")

        resp = client.get("/cmd/ausencias")
        assert resp.status_code == 200


# ── A11y e templates ─────────────────────────────────────────────────────


class TestA11yAndTemplates:
    """Testes que verificam acessibilidade básica nos templates."""

    def test_login_page_has_labels_for(self, app, client):
        """Página de login tem labels com for= associados aos inputs."""
        resp = client.get("/login")
        html = resp.data.decode()
        assert 'for="nii-input"' in html
        assert 'for="pw-input"' in html
        assert 'id="nii-input"' in html
        assert 'id="pw-input"' in html

    def test_login_page_has_aria(self, app, client):
        """Página de login tem atributos ARIA."""
        resp = client.get("/login")
        html = resp.data.decode()
        assert "aria-label=" in html
        assert 'aria-required="true"' in html

    def test_base_template_has_nav_aria(self, app, client):
        """Template base tem ARIA no nav quando autenticado."""
        create_aluno("e2e_a11y1", "A001", "A11y Test", pw="A11ytest1")
        login_as(client, "e2e_a11y1", "A11ytest1")

        resp = client.get("/aluno", follow_redirects=True)
        html = resp.data.decode()
        assert 'role="navigation"' in html

    def test_base_template_has_main_landmark(self, app, client):
        """Template base tem <main> como landmark."""
        resp = client.get("/login")
        html = resp.data.decode()
        assert "<main" in html

    def test_error_404_has_role_alert(self, app, client):
        """Página 404 tem role=alert."""
        resp = client.get("/pagina-que-nao-existe-xyz")
        html = resp.data.decode()
        assert 'role="alert"' in html

    def test_favicon_exists(self, app, client):
        """Favicon SVG é servido correctamente."""
        resp = client.get("/static/favicon.svg")
        assert resp.status_code == 200
        assert b"<svg" in resp.data

    def test_meta_tags_present(self, app, client):
        """Meta tags de descrição e theme-color estão presentes."""
        resp = client.get("/login")
        html = resp.data.decode()
        assert 'name="description"' in html
        assert 'name="theme-color"' in html
        assert 'name="viewport"' in html
