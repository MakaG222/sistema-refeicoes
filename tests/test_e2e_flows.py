"""Testes end-to-end dos fluxos críticos."""

from datetime import date, timedelta


from tests.conftest import create_aluno, get_csrf, login_as


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
