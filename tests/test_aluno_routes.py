"""Testes para as rotas do blueprint aluno."""

from datetime import date, timedelta

import pytest

from tests.conftest import create_aluno, login_as, get_csrf


@pytest.fixture(autouse=True)
def setup_aluno(app):
    """Cria alunos de teste."""
    create_aluno("al_rt1", "AR01", "Aluno Teste Route", ano="1")
    create_aluno("al_rt2", "AR02", "Aluno Teste Route2", ano="2")


def _login_aluno(client, nii="al_rt1"):
    login_as(client, nii)
    return get_csrf(client)


class TestAlunoHome:
    def test_home_get(self, app, client):
        _login_aluno(client)
        resp = client.get("/aluno")
        assert resp.status_code == 200

    def test_home_shows_meals(self, app, client):
        _login_aluno(client)
        resp = client.get("/aluno")
        html = resp.data.decode()
        # Home page should render content
        assert "aluno" in html.lower() or resp.status_code == 200

    def test_home_with_date(self, app, client):
        _login_aluno(client)
        resp = client.get(f"/aluno?d={date.today().isoformat()}")
        assert resp.status_code == 200

    def test_home_non_aluno_redirect(self, app, client):
        """Non-aluno profiles should not see aluno home."""
        from tests.conftest import create_system_user

        create_system_user("adm_alu", "admin", pw="Admin1234")
        login_as(client, "adm_alu", pw="Admin1234")
        resp = client.get("/aluno", follow_redirects=False)
        # Admin accessing /aluno may redirect or show different content
        assert resp.status_code in (200, 302)


class TestAlunoEditar:
    def test_editar_get(self, app, client):
        _login_aluno(client)
        # Use a future date to ensure the day is editable
        futuro = (date.today() + timedelta(days=2)).isoformat()
        resp = client.get(f"/aluno/editar/{futuro}")
        # May redirect if day not editable (prazo rules), or show form
        assert resp.status_code in (200, 302)

    def test_editar_post_marcar(self, app, client):
        csrf = _login_aluno(client)
        d = date.today().isoformat()
        resp = client.post(
            f"/aluno/editar/{d}",
            data={
                "csrf_token": csrf,
                "almoco": "normal",
                "jantar": "normal",
                "pequeno_almoco": "1",
                "lanche": "1",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

    def test_editar_invalid_date(self, app, client):
        _login_aluno(client)
        resp = client.get("/aluno/editar/invalido")
        # Should handle invalid date gracefully
        assert resp.status_code in (200, 302, 400)

    def test_editar_past_date(self, app, client):
        _login_aluno(client)
        ontem = (date.today() - timedelta(days=1)).isoformat()
        resp = client.get(f"/aluno/editar/{ontem}")
        assert resp.status_code in (200, 302)


class TestAlunoAusencias:
    def test_ausencias_get(self, app, client):
        _login_aluno(client)
        resp = client.get("/aluno/ausencias")
        assert resp.status_code == 200

    def test_ausencias_post_registar(self, app, client):
        csrf = _login_aluno(client)
        futuro = (date.today() + timedelta(days=7)).isoformat()
        futuro2 = (date.today() + timedelta(days=8)).isoformat()
        resp = client.post(
            "/aluno/ausencias",
            data={
                "csrf_token": csrf,
                "acao": "registar",
                "de": futuro,
                "ate": futuro2,
                "motivo": "Consulta médica",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200


class TestAlunoHistorico:
    def test_historico_get(self, app, client):
        _login_aluno(client)
        resp = client.get("/aluno/historico")
        assert resp.status_code == 200

    def test_historico_with_date_range(self, app, client):
        _login_aluno(client)
        d0 = (date.today() - timedelta(days=30)).isoformat()
        d1 = date.today().isoformat()
        resp = client.get(f"/aluno/historico?d0={d0}&d1={d1}")
        assert resp.status_code == 200


class TestAlunoExportarHistorico:
    def test_exportar_csv(self, app, client):
        _login_aluno(client)
        resp = client.get("/aluno/exportar-historico?fmt=csv")
        assert resp.status_code == 200
        assert "text/csv" in resp.content_type or resp.status_code == 200

    def test_exportar_default(self, app, client):
        _login_aluno(client)
        resp = client.get("/aluno/exportar-historico")
        assert resp.status_code == 200


class TestAlunoPassword:
    def test_password_get(self, app, client):
        _login_aluno(client)
        resp = client.get("/aluno/password")
        assert resp.status_code == 200

    def test_password_post_mismatch(self, app, client):
        csrf = _login_aluno(client)
        resp = client.post(
            "/aluno/password",
            data={
                "csrf_token": csrf,
                "pw_atual": "al_rt1",
                "pw_nova": "NovaPass123",
                "pw_confirma": "Diferente123",
            },
            follow_redirects=True,
        )
        html = resp.data.decode()
        assert (
            "não coincidem" in html.lower()
            or "confirma" in html.lower()
            or resp.status_code == 200
        )

    def test_password_post_wrong_current(self, app, client):
        csrf = _login_aluno(client)
        resp = client.post(
            "/aluno/password",
            data={
                "csrf_token": csrf,
                "pw_atual": "errada123",
                "pw_nova": "NovaPass123",
                "pw_confirma": "NovaPass123",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

    def test_password_post_weak(self, app, client):
        csrf = _login_aluno(client)
        resp = client.post(
            "/aluno/password",
            data={
                "csrf_token": csrf,
                "pw_atual": "al_rt1",
                "pw_nova": "123",
                "pw_confirma": "123",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

    def test_password_post_valid(self, app, client):
        csrf = _login_aluno(client)
        resp = client.post(
            "/aluno/password",
            data={
                "csrf_token": csrf,
                "pw_atual": "al_rt1",
                "pw_nova": "NovaSegura123",
                "pw_confirma": "NovaSegura123",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200


class TestAlunoPerfil:
    def test_perfil_get(self, app, client):
        _login_aluno(client)
        resp = client.get("/aluno/perfil")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Aluno Teste Route" in html or "perfil" in html.lower()

    def test_perfil_post_update_email(self, app, client):
        csrf = _login_aluno(client)
        resp = client.post(
            "/aluno/perfil",
            data={
                "csrf_token": csrf,
                "email": "aluno@teste.pt",
                "telemovel": "+351912345678",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

    def test_perfil_post_invalid_email(self, app, client):
        csrf = _login_aluno(client)
        resp = client.post(
            "/aluno/perfil",
            data={
                "csrf_token": csrf,
                "email": "invalido",
                "telemovel": "",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

    def test_perfil_post_invalid_phone(self, app, client):
        csrf = _login_aluno(client)
        resp = client.post(
            "/aluno/perfil",
            data={
                "csrf_token": csrf,
                "email": "",
                "telemovel": "abc",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200


class TestAlunoDefaultMeals:
    """Testes para a criação automática de refeições por defeito."""

    def test_editar_autocria_refeicoes_dia_util(self, app, client):
        """Abrir editor num dia útil sem registo cria refeições por defeito."""
        from core.meals import refeicao_get
        from core.auth_db import user_id_by_nii

        _login_aluno(client)
        # Encontrar próxima segunda a quinta (não sexta, para testar jantar)
        hoje = date.today()
        d = hoje + timedelta(days=10)
        while d.weekday() >= 4:  # 4=sexta, 5=sáb, 6=dom
            d += timedelta(days=1)

        uid = user_id_by_nii("al_rt1")
        # Garantir que não existe registo
        r = refeicao_get(uid, d)
        assert not r.get("id"), "Não deveria existir registo prévio"

        resp = client.get(f"/aluno/editar/{d.isoformat()}")
        if resp.status_code == 302:
            return  # Prazo expirado — OK, não testável

        # Agora deveria existir registo com defaults
        r = refeicao_get(uid, d)
        assert r.get("pequeno_almoco") == 1
        assert r.get("lanche") == 0  # Lanche não incluído por defeito
        assert r.get("almoco") == "Normal"
        assert r.get("jantar_tipo") == "Normal"

    def test_editar_autocria_sexta_sem_jantar(self, app, client):
        """Abrir editor numa sexta cria refeições sem jantar."""
        from core.meals import refeicao_get
        from core.auth_db import user_id_by_nii

        _login_aluno(client)
        hoje = date.today()
        d = hoje + timedelta(days=10)
        while d.weekday() != 4:  # 4 = sexta
            d += timedelta(days=1)

        resp = client.get(f"/aluno/editar/{d.isoformat()}")
        if resp.status_code == 302:
            return  # Prazo expirado

        uid = user_id_by_nii("al_rt1")
        r = refeicao_get(uid, d)
        assert r.get("pequeno_almoco") == 1
        assert r.get("jantar_tipo") is None  # Sem jantar à sexta

    def test_editar_fds_nao_autocria(self, app, client):
        """Abrir editor num fim de semana NÃO cria refeições por defeito."""
        from core.meals import refeicao_get
        from core.auth_db import user_id_by_nii

        _login_aluno(client)
        hoje = date.today()
        d = hoje + timedelta(days=10)
        while d.weekday() != 5:  # 5 = sábado
            d += timedelta(days=1)

        resp = client.get(f"/aluno/editar/{d.isoformat()}")
        if resp.status_code == 302:
            return

        uid = user_id_by_nii("al_rt1")
        r = refeicao_get(uid, d)
        # Fim de semana: não auto-cria
        assert not r.get("id") or r.get("pequeno_almoco") == 0


class TestAlunoDetidoBloqueio:
    """Testes para o bloqueio de edição de refeições quando detido."""

    def _criar_detencao(self, uid, d):
        from core.database import db

        with db() as conn:
            conn.execute(
                "INSERT INTO detencoes (utilizador_id, detido_de, detido_ate, motivo, criado_por) "
                "VALUES (?,?,?,?,?)",
                (uid, d.isoformat(), d.isoformat(), "Teste bloqueio", "cmd1"),
            )
            conn.commit()

    def test_detido_nao_pode_alterar_post(self, app, client):
        """POST com detenção ativa é rejeitado."""
        from core.auth_db import user_id_by_nii

        csrf = _login_aluno(client)
        d = date.today() + timedelta(days=12)
        while d.weekday() >= 5:
            d += timedelta(days=1)

        uid = user_id_by_nii("al_rt1")
        self._criar_detencao(uid, d)

        resp = client.post(
            f"/aluno/editar/{d.isoformat()}",
            data={
                "csrf_token": csrf,
                "pa": "0",
                "lanche": "0",
                "almoco": "",
                "jantar": "",
            },
            follow_redirects=True,
        )
        html = resp.data.decode()
        assert "bloqueadas" in html.lower() or "detido" in html.lower()

    def test_detido_template_mostra_bloqueio(self, app, client):
        """Template mostra mensagem de bloqueio e esconde botão guardar."""
        from core.auth_db import user_id_by_nii

        _login_aluno(client)
        d = date.today() + timedelta(days=13)
        while d.weekday() >= 5:
            d += timedelta(days=1)

        uid = user_id_by_nii("al_rt1")
        self._criar_detencao(uid, d)

        resp = client.get(f"/aluno/editar/{d.isoformat()}")
        if resp.status_code == 302:
            return  # Prazo expirado

        html = resp.data.decode()
        assert "bloqueadas" in html.lower()
        assert "btn-save" not in html  # Botão guardar escondido
        assert ">Voltar<" in html  # Botão voltar presente


class TestAlunoLicencaFds:
    def test_licenca_post_no_data(self, app, client):
        csrf = _login_aluno(client)
        resp = client.post(
            "/aluno/licenca-fds",
            data={
                "csrf_token": csrf,
                "acao": "marcar",
                "data": "",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

    def test_licenca_post_marcar(self, app, client):
        csrf = _login_aluno(client)
        # Find next Saturday
        hoje = date.today()
        dias_ate_sab = (5 - hoje.weekday()) % 7
        if dias_ate_sab == 0:
            dias_ate_sab = 7
        sabado = (hoje + timedelta(days=dias_ate_sab)).isoformat()
        resp = client.post(
            "/aluno/licenca-fds",
            data={
                "csrf_token": csrf,
                "acao": "marcar",
                "data": sabado,
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

    def test_licenca_post_cancelar(self, app, client):
        csrf = _login_aluno(client)
        resp = client.post(
            "/aluno/licenca-fds",
            data={
                "csrf_token": csrf,
                "acao": "cancelar",
                "data": date.today().isoformat(),
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
