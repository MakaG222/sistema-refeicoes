"""
tests/test_refeicoes.py — Testes do core de refeições
=====================================================
"""

from datetime import date, timedelta

from core.database import db
from core.meals import get_totais_dia, refeicao_get, refeicao_save

from tests.conftest import create_aluno, get_csrf, login_as


# ── Helpers ────────────────────────────────────────────────────────────────────


def _future_date(days=10):
    """Retorna uma data futura editável (dentro do prazo)."""
    return date.today() + timedelta(days=days)


def _setup_aluno(app):
    """Cria um aluno de teste e retorna (uid, nii)."""
    with app.app_context():
        uid = create_aluno("T_REF_01", "901", "Aluno Refeicoes Teste", "2")
        return uid, "T_REF_01"


# ── Testes directos à camada de dados ─────────────────────────────────────────


class TestRefeicaoSave:
    def test_save_basic_meal(self, app):
        """Guardar refeição básica (PA + Almoço Normal)."""
        uid, _ = _setup_aluno(app)
        d = _future_date()
        r = {
            "pequeno_almoco": 1,
            "lanche": 0,
            "almoco": "Normal",
            "jantar_tipo": None,
            "jantar_sai_unidade": 0,
        }
        ok = refeicao_save(uid, d, r, alterado_por="teste")
        assert ok is True

        got = refeicao_get(uid, d)
        assert got["pequeno_almoco"] == 1
        assert got["lanche"] == 0
        assert got["almoco"] == "Normal"
        assert got["jantar_tipo"] is None

    def test_save_vegetarian_meal(self, app):
        """Guardar refeição vegetariana."""
        uid, _ = _setup_aluno(app)
        d = _future_date(11)
        r = {
            "pequeno_almoco": 1,
            "lanche": 1,
            "almoco": "Vegetariano",
            "jantar_tipo": "Vegetariano",
            "jantar_sai_unidade": 0,
        }
        ok = refeicao_save(uid, d, r)
        assert ok is True

        got = refeicao_get(uid, d)
        assert got["almoco"] == "Vegetariano"
        assert got["jantar_tipo"] == "Vegetariano"

    def test_save_diet_meal(self, app):
        """Guardar refeição de dieta."""
        uid, _ = _setup_aluno(app)
        d = _future_date(12)
        r = {
            "pequeno_almoco": 0,
            "lanche": 0,
            "almoco": "Dieta",
            "jantar_tipo": "Dieta",
            "jantar_sai_unidade": 0,
        }
        ok = refeicao_save(uid, d, r)
        assert ok is True

        got = refeicao_get(uid, d)
        assert got["almoco"] == "Dieta"
        assert got["jantar_tipo"] == "Dieta"

    def test_update_meal_overwrites(self, app):
        """Atualizar refeição substitui valores anteriores."""
        uid, _ = _setup_aluno(app)
        d = _future_date(13)
        refeicao_save(
            uid,
            d,
            {
                "pequeno_almoco": 1,
                "lanche": 1,
                "almoco": "Normal",
                "jantar_tipo": "Normal",
                "jantar_sai_unidade": 0,
            },
        )
        # Actualizar: remover almoco, mudar jantar
        refeicao_save(
            uid,
            d,
            {
                "pequeno_almoco": 0,
                "lanche": 0,
                "almoco": None,
                "jantar_tipo": "Vegetariano",
                "jantar_sai_unidade": 1,
            },
        )
        got = refeicao_get(uid, d)
        assert got["pequeno_almoco"] == 0
        assert got["lanche"] == 0
        assert got["almoco"] is None
        assert got["jantar_tipo"] == "Vegetariano"
        assert got["jantar_sai_unidade"] == 1

    def test_get_nonexistent_returns_defaults(self, app):
        """Refeição inexistente retorna zeros/None."""
        uid, _ = _setup_aluno(app)
        got = refeicao_get(uid, date(2099, 12, 31))
        assert got["pequeno_almoco"] == 0
        assert got["lanche"] == 0
        assert got["almoco"] is None
        assert got["jantar_tipo"] is None

    def test_detention_blocks_sai_unidade(self, app):
        """Se aluno está detido, jantar_sai_unidade é forçado a 0."""
        uid, _ = _setup_aluno(app)
        d = _future_date(14)
        # Criar detenção para esse dia
        with db() as conn:
            conn.execute(
                "INSERT INTO detencoes (utilizador_id, detido_de, detido_ate, motivo, criado_por) VALUES (?,?,?,?,?)",
                (uid, d.isoformat(), d.isoformat(), "Teste", "teste"),
            )
            conn.commit()

        # Tentar guardar com sai=1
        r = {
            "pequeno_almoco": 1,
            "lanche": 1,
            "almoco": "Normal",
            "jantar_tipo": "Normal",
            "jantar_sai_unidade": 1,
        }
        refeicao_save(uid, d, r)
        got = refeicao_get(uid, d)
        # Deve estar forçado a 0 por causa da detenção
        assert got["jantar_sai_unidade"] == 0


class TestRefeicaoAuditLog:
    def test_meal_change_logged(self, app):
        """Alteração de refeição cria entrada no log de auditoria."""
        uid, _ = _setup_aluno(app)
        d = _future_date(15)

        refeicao_save(
            uid,
            d,
            {
                "pequeno_almoco": 0,
                "lanche": 0,
                "almoco": None,
                "jantar_tipo": None,
                "jantar_sai_unidade": 0,
            },
            alterado_por="test_user",
        )
        refeicao_save(
            uid,
            d,
            {
                "pequeno_almoco": 1,
                "lanche": 0,
                "almoco": "Normal",
                "jantar_tipo": None,
                "jantar_sai_unidade": 0,
            },
            alterado_por="test_user",
        )

        with db() as conn:
            logs = conn.execute(
                "SELECT campo, valor_antes, valor_depois, alterado_por FROM refeicoes_log WHERE utilizador_id=? AND data_refeicao=?",
                (uid, d.isoformat()),
            ).fetchall()
        assert len(logs) > 0
        campos = {r["campo"] for r in logs}
        assert "pequeno_almoco" in campos or "almoco" in campos


class TestTotaisDia:
    def test_totals_count_correctly(self, app):
        """Totais do dia são calculados correctamente."""
        d = _future_date(16)
        # Criar 2 alunos com refeições diferentes
        uid1 = create_aluno("T_TOT_01", "951", "Totais A", "1")
        uid2 = create_aluno("T_TOT_02", "952", "Totais B", "1")

        refeicao_save(
            uid1,
            d,
            {
                "pequeno_almoco": 1,
                "lanche": 1,
                "almoco": "Normal",
                "jantar_tipo": "Normal",
                "jantar_sai_unidade": 0,
            },
        )
        refeicao_save(
            uid2,
            d,
            {
                "pequeno_almoco": 1,
                "lanche": 0,
                "almoco": "Vegetariano",
                "jantar_tipo": "Dieta",
                "jantar_sai_unidade": 0,
            },
        )

        t = get_totais_dia(d.isoformat())
        assert t["pa"] >= 2
        assert t["alm_norm"] >= 1
        assert t["alm_veg"] >= 1
        assert t["jan_dieta"] >= 1


# ── Testes via HTTP (aluno_editar) ────────────────────────────────────────────


class TestAlunoEditarEndpoint:
    def test_aluno_can_mark_meals_via_form(self, app, client):
        """Aluno consegue marcar refeições via POST com hidden inputs."""
        uid, nii = _setup_aluno(app)
        d = _future_date(5)

        login_as(client, nii)
        token = get_csrf(client)

        resp = client.post(
            f"/aluno/editar/{d.isoformat()}",
            data={
                "csrf_token": token,
                "pa": "1",
                "lanche": "1",
                "almoco": "Normal",
                "jantar": "Vegetariano",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

        got = refeicao_get(uid, d)
        assert got["pequeno_almoco"] == 1
        assert got["lanche"] == 1
        assert got["almoco"] == "Normal"
        assert got["jantar_tipo"] == "Vegetariano"

    def test_aluno_unmark_meals(self, app, client):
        """Aluno consegue desmarcar refeições (valores '0' ou vazios)."""
        uid, nii = _setup_aluno(app)
        d = _future_date(6)

        # Primeiro marcar directamente na BD
        refeicao_save(
            uid,
            d,
            {
                "pequeno_almoco": 1,
                "lanche": 1,
                "almoco": "Normal",
                "jantar_tipo": "Normal",
                "jantar_sai_unidade": 0,
            },
        )

        login_as(client, nii)
        token = get_csrf(client)

        # Agora desmarcar via form
        resp = client.post(
            f"/aluno/editar/{d.isoformat()}",
            data={
                "csrf_token": token,
                "pa": "0",
                "lanche": "0",
                "almoco": "",
                "jantar": "",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

        got = refeicao_get(uid, d)
        assert got["pequeno_almoco"] == 0
        assert got["lanche"] == 0
        assert got["almoco"] is None

    def test_aluno_editar_requires_login(self, client):
        """Acesso à edição de refeições sem login redireciona."""
        d = _future_date(19)
        resp = client.get(f"/aluno/editar/{d.isoformat()}")
        assert resp.status_code == 302
        assert "/login" in resp.headers.get("Location", "")
