"""
tests/test_operations.py — Testes para blueprints/operations/routes.py
======================================================================
"""

from datetime import date

from tests.conftest import create_aluno, get_csrf, login_as


# ── Helpers ────────────────────────────────────────────────────────────────


def _setup_alunos(app):
    """Cria alunos de teste para as rotas de operações."""
    with app.app_context():
        uid1 = create_aluno("ops_a1", "OA1", "Ops Aluno Um", ano="1", pw="opsaluno123")
        uid2 = create_aluno(
            "ops_a2", "OA2", "Ops Aluno Dois", ano="1", pw="opsaluno123"
        )
        uid3 = create_aluno(
            "ops_a3", "OA3", "Ops Aluno Tres", ano="2", pw="opsaluno123"
        )
        return uid1, uid2, uid3


# ═══════════════════════════════════════════════════════════════════════════
# PAINEL DIA
# ═══════════════════════════════════════════════════════════════════════════


class TestPainelDia:
    def test_painel_default(self, client):
        login_as(client, "admin", "admin123")
        resp = client.get("/painel")
        assert resp.status_code == 200

    def test_painel_with_date(self, client):
        login_as(client, "admin", "admin123")
        resp = client.get("/painel?d=2026-01-15")
        assert resp.status_code == 200

    def test_painel_backup_post(self, client):
        login_as(client, "admin", "admin123")
        token = get_csrf(client)
        resp = client.post(
            "/painel",
            data={"acao": "backup", "csrf_token": token},
            follow_redirects=True,
        )
        assert resp.status_code == 200


# ═══════════════════════════════════════════════════════════════════════════
# LISTA ALUNOS ANO
# ═══════════════════════════════════════════════════════════════════════════


class TestListaAlunosAno:
    def test_lista_alunos_get(self, app, client):
        _setup_alunos(app)
        login_as(client, "admin", "admin123")
        resp = client.get("/alunos/1")
        assert resp.status_code == 200

    def test_marcar_ausente(self, app, client):
        uids = _setup_alunos(app)
        login_as(client, "admin", "admin123")
        token = get_csrf(client)
        resp = client.post(
            "/alunos/1",
            data={
                "acao": "marcar_ausente",
                "uid": str(uids[0]),
                "csrf_token": token,
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

    def test_marcar_presente(self, app, client):
        uids = _setup_alunos(app)
        login_as(client, "admin", "admin123")
        token = get_csrf(client)
        # First mark absent
        client.post(
            "/alunos/1",
            data={
                "acao": "marcar_ausente",
                "uid": str(uids[1]),
                "csrf_token": token,
            },
            follow_redirects=True,
        )
        # Then mark present
        token = get_csrf(client)
        resp = client.post(
            "/alunos/1",
            data={
                "acao": "marcar_presente",
                "uid": str(uids[1]),
                "csrf_token": token,
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200


# ═══════════════════════════════════════════════════════════════════════════
# RELATÓRIO SEMANAL
# ═══════════════════════════════════════════════════════════════════════════


class TestRelatorioSemanal:
    def test_relatorio_default(self, client):
        login_as(client, "admin", "admin123")
        resp = client.get("/relatorio")
        assert resp.status_code == 200

    def test_relatorio_with_d0(self, client):
        login_as(client, "admin", "admin123")
        resp = client.get("/relatorio?d0=2026-01-13")
        assert resp.status_code == 200


# ═══════════════════════════════════════════════════════════════════════════
# EXCEÇÕES DIA
# ═══════════════════════════════════════════════════════════════════════════


class TestExcecoesDia:
    def test_excecoes_get(self, client):
        login_as(client, "admin", "admin123")
        resp = client.get(f"/excecoes/{date.today().isoformat()}")
        assert resp.status_code == 200

    def test_excecoes_get_with_nii(self, app, client):
        _setup_alunos(app)
        login_as(client, "admin", "admin123")
        resp = client.get(f"/excecoes/{date.today().isoformat()}?nii=ops_a1")
        assert resp.status_code == 200

    def test_excecoes_post_add(self, app, client):
        _setup_alunos(app)
        login_as(client, "admin", "admin123")
        token = get_csrf(client)
        resp = client.post(
            f"/excecoes/{date.today().isoformat()}",
            data={
                "nii": "ops_a1",
                "pa": "on",
                "lanche": "on",
                "almoco": "Normal",
                "jantar": "Vegetariano",
                "sai": "",
                "csrf_token": token,
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200


# ═══════════════════════════════════════════════════════════════════════════
# AUSÊNCIAS
# ═══════════════════════════════════════════════════════════════════════════


class TestAusencias:
    def test_ausencias_get(self, client):
        login_as(client, "admin", "admin123")
        resp = client.get("/ausencias")
        assert resp.status_code == 200

    def test_ausencias_post_registar(self, app, client):
        _setup_alunos(app)
        login_as(client, "admin", "admin123")
        token = get_csrf(client)
        hoje = date.today().isoformat()
        resp = client.post(
            "/ausencias",
            data={
                "nii": "ops_a2",
                "de": hoje,
                "ate": hoje,
                "motivo": "Teste ausencia ops",
                "csrf_token": token,
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

    def test_ausencias_post_remover(self, app, client):
        _setup_alunos(app)
        login_as(client, "admin", "admin123")
        token = get_csrf(client)
        hoje = date.today().isoformat()
        # Register an absence first
        client.post(
            "/ausencias",
            data={
                "nii": "ops_a3",
                "de": hoje,
                "ate": hoje,
                "motivo": "Para remover",
                "csrf_token": token,
            },
            follow_redirects=True,
        )
        # Get the absence ID
        from core.database import db

        with app.app_context():
            with db() as conn:
                row = conn.execute(
                    "SELECT id FROM ausencias WHERE motivo='Para remover' LIMIT 1"
                ).fetchone()
                aus_id = row["id"] if row else "1"
        # Remove it
        token = get_csrf(client)
        resp = client.post(
            "/ausencias",
            data={
                "acao": "remover",
                "id": str(aus_id),
                "csrf_token": token,
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200


# ═══════════════════════════════════════════════════════════════════════════
# LICENÇAS / ENTRADAS / SAÍDAS
# ═══════════════════════════════════════════════════════════════════════════


class TestLicencasEntradasSaidas:
    def test_licencas_es_get(self, client):
        login_as(client, "admin", "admin123")
        resp = client.get("/oficialdia/licencas-es")
        assert resp.status_code == 200


# ═══════════════════════════════════════════════════════════════════════════
# CONTROLO PRESENÇAS
# ═══════════════════════════════════════════════════════════════════════════


class TestControloPresencas:
    def test_presencas_get(self, client):
        login_as(client, "admin", "admin123")
        resp = client.get("/presencas")
        assert resp.status_code == 200

    def test_presencas_consultar(self, app, client):
        _setup_alunos(app)
        login_as(client, "admin", "admin123")
        token = get_csrf(client)
        resp = client.post(
            "/presencas",
            data={
                "acao": "consultar",
                "ni": "OA1",
                "csrf_token": token,
            },
        )
        assert resp.status_code == 200

    def test_presencas_dar_saida(self, app, client):
        _setup_alunos(app)
        login_as(client, "admin", "admin123")
        token = get_csrf(client)
        resp = client.post(
            "/presencas",
            data={
                "acao": "dar_saida",
                "ni": "OA1",
                "csrf_token": token,
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

    def test_presencas_dar_entrada(self, app, client):
        _setup_alunos(app)
        login_as(client, "admin", "admin123")
        token = get_csrf(client)
        # Mark absent first
        client.post(
            "/presencas",
            data={
                "acao": "dar_saida",
                "ni": "OA2",
                "csrf_token": token,
            },
            follow_redirects=True,
        )
        # Then register entrada
        token = get_csrf(client)
        resp = client.post(
            "/presencas",
            data={
                "acao": "dar_entrada",
                "ni": "OA2",
                "csrf_token": token,
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200


# ═══════════════════════════════════════════════════════════════════════════
# IMPRIMIR ANO
# ═══════════════════════════════════════════════════════════════════════════


class TestImprimirAno:
    def test_imprimir_ano_get(self, app, client):
        _setup_alunos(app)
        login_as(client, "admin", "admin123")
        resp = client.get("/imprimir/1")
        assert resp.status_code == 200
        assert b"<!doctype html>" in resp.data or b"<!DOCTYPE html>" in resp.data
