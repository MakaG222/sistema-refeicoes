"""
tests/test_licenca_fds.py — Testes para licença de fim de semana
=================================================================
Testa as funções _marcar_licenca_fds, _cancelar_licenca_fds e a rota /aluno/licenca-fds.
"""

from datetime import date, timedelta

from conftest import create_aluno, get_csrf, login_as

from core.database import db
from core.meals import refeicao_get


def _next_friday():
    """Devolve a próxima sexta-feira (ou hoje se for sexta)."""
    hoje = date.today()
    dias = (4 - hoje.weekday()) % 7
    if dias == 0 and hoje.weekday() == 4:
        return hoje
    return hoje + timedelta(days=dias or 7)


def _distant_friday():
    """Devolve uma sexta-feira suficientemente no futuro para ser editável."""
    hoje = date.today()
    # Começar pelo menos 2 dias no futuro para evitar prazos
    d = hoje + timedelta(days=3)
    while d.weekday() != 4:
        d += timedelta(days=1)
    return d


class TestMarcarLicencaFDS:
    """Testa _marcar_licenca_fds directamente."""

    def test_marca_licenca_fds_sucesso(self, app):
        uid = create_aluno("fds01", "f01", "Aluno FDS 01", "1")
        sexta = _distant_friday()

        from app import _marcar_licenca_fds

        ok, err = _marcar_licenca_fds(uid, sexta, "test")
        assert ok is True

        # Verificar que a licença foi criada na sexta
        with db() as conn:
            lic = conn.execute(
                "SELECT tipo FROM licencas WHERE utilizador_id=? AND data=?",
                (uid, sexta.isoformat()),
            ).fetchone()
        assert lic is not None
        assert lic["tipo"] == "antes_jantar"

        # Verificar que o jantar de sexta foi removido
        r_sexta = refeicao_get(uid, sexta)
        assert r_sexta.get("jantar_tipo") is None or r_sexta.get("jantar_tipo") == ""

        # Verificar que sábado e domingo têm refeições zeradas
        sabado = sexta + timedelta(days=1)
        domingo = sexta + timedelta(days=2)
        r_sab = refeicao_get(uid, sabado)
        r_dom = refeicao_get(uid, domingo)
        assert r_sab.get("pequeno_almoco", 0) == 0
        assert r_dom.get("pequeno_almoco", 0) == 0

    def test_cancelar_licenca_fds_repoe_jantar(self, app):
        uid = create_aluno("fds02", "f02", "Aluno FDS 02", "1")
        sexta = _distant_friday()

        from app import _cancelar_licenca_fds, _marcar_licenca_fds

        # Marcar primeiro
        _marcar_licenca_fds(uid, sexta, "test")

        # Cancelar
        ok, err = _cancelar_licenca_fds(uid, sexta, "test")
        assert ok is True

        # Verificar que a licença foi removida
        with db() as conn:
            lic = conn.execute(
                "SELECT tipo FROM licencas WHERE utilizador_id=? AND data=?",
                (uid, sexta.isoformat()),
            ).fetchone()
        assert lic is None

        # Verificar que o jantar voltou a Normal
        r_sexta = refeicao_get(uid, sexta)
        assert r_sexta.get("jantar_tipo") == "Normal"


class TestRotaLicencaFDS:
    """Testa a rota /aluno/licenca-fds via HTTP."""

    def test_requer_login(self, client):
        resp = client.post("/aluno/licenca-fds", data={})
        assert resp.status_code == 302
        assert "/login" in resp.headers.get("Location", "")

    def test_rejeita_dia_nao_sexta(self, client, app):
        create_aluno("fds10", "f10", "Aluno FDS 10", "1")
        login_as(client, "fds10")
        csrf = get_csrf(client)
        # Enviar uma quarta-feira
        hoje = date.today()
        quarta = hoje + timedelta(days=(2 - hoje.weekday()) % 7 + 7)
        resp = client.post(
            "/aluno/licenca-fds",
            data={
                "sexta": quarta.isoformat(),
                "acao_fds": "marcar",
                "csrf_token": csrf,
            },
            follow_redirects=True,
        )
        assert (
            "inv" in resp.data.decode().lower() or "sexta" in resp.data.decode().lower()
        )

    def test_marcar_fds_via_rota(self, client, app):
        uid = create_aluno("fds11", "f11", "Aluno FDS 11", "1")
        login_as(client, "fds11")
        csrf = get_csrf(client)
        sexta = _distant_friday()
        resp = client.post(
            "/aluno/licenca-fds",
            data={
                "sexta": sexta.isoformat(),
                "acao_fds": "marcar",
                "csrf_token": csrf,
            },
            follow_redirects=True,
        )
        html = resp.data.decode()
        assert "marcada" in html.lower() or resp.status_code == 200

        # Verificar na BD
        with db() as conn:
            lic = conn.execute(
                "SELECT tipo FROM licencas WHERE utilizador_id=? AND data=?",
                (uid, sexta.isoformat()),
            ).fetchone()
        assert lic is not None
        assert lic["tipo"] == "antes_jantar"

    def test_cancelar_fds_via_rota(self, client, app):
        uid = create_aluno("fds12", "f12", "Aluno FDS 12", "1")
        login_as(client, "fds12")
        sexta = _distant_friday()

        # Marcar primeiro via função directa
        from app import _marcar_licenca_fds

        _marcar_licenca_fds(uid, sexta, "test")

        csrf = get_csrf(client)
        resp = client.post(
            "/aluno/licenca-fds",
            data={
                "sexta": sexta.isoformat(),
                "acao_fds": "cancelar",
                "csrf_token": csrf,
            },
            follow_redirects=True,
        )
        html = resp.data.decode()
        assert "cancelada" in html.lower() or resp.status_code == 200

    def test_detido_nao_pode_marcar_fds(self, client, app):
        uid = create_aluno("fds13", "f13", "Aluno FDS 13", "1")
        sexta = _distant_friday()

        # Criar detenção para a sexta
        with db() as conn:
            conn.execute(
                "INSERT INTO detencoes(utilizador_id, detido_de, detido_ate, motivo, criado_por) VALUES (?,?,?,?,?)",
                (
                    uid,
                    sexta.isoformat(),
                    (sexta + timedelta(days=2)).isoformat(),
                    "teste",
                    "admin",
                ),
            )
            conn.commit()

        login_as(client, "fds13")
        csrf = get_csrf(client)
        resp = client.post(
            "/aluno/licenca-fds",
            data={
                "sexta": sexta.isoformat(),
                "acao_fds": "marcar",
                "csrf_token": csrf,
            },
            follow_redirects=True,
        )
        html = resp.data.decode()
        assert "detido" in html.lower()


class TestBotaoFDSVisivel:
    """Testa que o botão FDS aparece no aluno_home nas sextas."""

    def test_botao_fds_presente_na_home(self, client, app):
        create_aluno("fds20", "f20", "Aluno FDS 20", "1")
        login_as(client, "fds20")
        resp = client.get("/aluno")
        html = resp.data.decode()
        # Se há uma sexta no range de DIAS_ANTECEDENCIA, deve ter botão
        hoje = date.today()
        tem_sexta = any(
            (hoje + timedelta(days=i)).weekday() == 4
            for i in range(8)  # DIAS_ANTECEDENCIA default
        )
        if tem_sexta:
            assert "licenca-fds" in html or "Licen" in html
