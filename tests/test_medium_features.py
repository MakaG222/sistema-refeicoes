"""
tests/test_medium_features.py — Testes para funcionalidades de média prioridade
=================================================================================
- Ementa visível no aluno_editar
- Exportação do histórico individual (CSV/Excel)
- Relatório mensal
- Reset de password via CMD
"""

from datetime import date, timedelta

from core.database import db
from conftest import create_aluno, create_system_user, get_csrf, login_as


# ─── Ementa no aluno_editar ──────────────────────────────────────────────


class TestEmentaAlunoEditar:
    def test_ementa_visivel_no_editar(self, app, client):
        """A ementa do dia deve aparecer na página de edição de refeições."""
        nii = "991"
        create_aluno(nii, "T91", "Teste Ementa", ano="1")

        # Inserir ementa para daqui a 5 dias (garante prazo editável)
        amanha = date.today() + timedelta(days=5)
        with db() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO menus_diarios
                   (data, pequeno_almoco, lanche, almoco_normal, almoco_veg,
                    almoco_dieta, jantar_normal, jantar_veg, jantar_dieta)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    amanha.isoformat(),
                    "Pão com manteiga",
                    "Bolacha",
                    "Bacalhau",
                    "Tofu grelhado",
                    "Sopa legumes",
                    "Frango assado",
                    "Legumes salteados",
                    "Canja",
                ),
            )
            conn.commit()

        login_as(client, nii)
        resp = client.get(f"/aluno/editar/{amanha.isoformat()}", follow_redirects=True)
        html = resp.data.decode()
        assert "Ementa" in html
        assert "Bacalhau" in html
        assert "Tofu grelhado" in html

    def test_ementa_ausente_nao_mostra_card(self, app, client):
        """Se não há ementa para o dia, o card não deve aparecer."""
        nii = "991"
        # Usar uma data futura sem ementa (longe o suficiente para ser editável)
        futuro = date.today() + timedelta(days=6)
        with db() as conn:
            conn.execute(
                "DELETE FROM menus_diarios WHERE data=?", (futuro.isoformat(),)
            )
            conn.commit()

        login_as(client, nii)
        resp = client.get(f"/aluno/editar/{futuro.isoformat()}", follow_redirects=True)
        html = resp.data.decode()
        # O card de ementa não deve aparecer
        assert "Ementa —" not in html


# ─── Exportação do histórico individual ──────────────────────────────────


class TestExportacaoHistorico:
    def test_export_csv_historico_aluno(self, app, client):
        """O aluno pode exportar o seu histórico em CSV."""
        nii = "992"
        uid = create_aluno(nii, "T92", "Teste Export", ano="1")

        # Inserir refeição
        hoje = date.today()
        with db() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO refeicoes
                   (utilizador_id, data, pequeno_almoco, lanche, almoco, jantar_tipo, jantar_sai_unidade)
                   VALUES (?,?,1,1,'Normal','Vegetariano',0)""",
                (uid, hoje.isoformat()),
            )
            conn.commit()

        login_as(client, nii)
        resp = client.get("/aluno/exportar-historico?fmt=csv")
        assert resp.status_code == 200
        assert "text/csv" in resp.content_type
        content = resp.data.decode("utf-8-sig")
        assert "Data" in content
        assert "PA" in content
        assert hoje.isoformat() in content

    def test_export_xlsx_historico_aluno(self, app, client):
        """O aluno pode exportar o seu histórico em Excel."""
        nii = "992"
        login_as(client, nii)
        resp = client.get("/aluno/exportar-historico?fmt=xlsx")
        assert resp.status_code == 200
        assert "spreadsheetml" in resp.content_type

    def test_botoes_export_visiveis_no_historico(self, app, client):
        """Os botões de exportação aparecem na página de histórico."""
        nii = "992"
        login_as(client, nii)
        resp = client.get("/aluno/historico", follow_redirects=True)
        html = resp.data.decode()
        assert "Exportar CSV" in html
        assert "Exportar Excel" in html


# ─── Relatório mensal ────────────────────────────────────────────────────


class TestRelatorioMensal:
    def test_export_mensal_csv(self, app, client):
        """Cozinha pode exportar relatório mensal em CSV."""
        login_as(client, "cozinha", pw="cozinha123")
        hoje = date.today()
        mes = hoje.strftime("%Y-%m")
        resp = client.get(f"/exportar/mensal?mes={mes}&fmt=csv")
        assert resp.status_code == 200
        assert "text/csv" in resp.content_type
        content = resp.data.decode("utf-8-sig")
        assert "TOTAL" in content
        assert "Data" in content

    def test_export_mensal_xlsx(self, app, client):
        """Admin pode exportar relatório mensal em Excel."""
        login_as(client, "admin", pw="admin123")
        hoje = date.today()
        mes = hoje.strftime("%Y-%m")
        resp = client.get(f"/exportar/mensal?mes={mes}&fmt=xlsx")
        assert resp.status_code == 200
        assert "spreadsheetml" in resp.content_type

    def test_export_mensal_invalid_fmt(self, app, client):
        """Formato inválido retorna 400."""
        login_as(client, "admin", pw="admin123")
        resp = client.get("/exportar/mensal?fmt=pdf")
        assert resp.status_code == 400


# ─── Reset de password via CMD ───────────────────────────────────────────


class TestCMDResetPassword:
    def test_cmd_reset_password_own_year(self, app, client):
        """CMD pode resetar password de aluno do seu ano."""
        nii_cmd = "cmd1"
        nii_aluno = "993"
        create_system_user(nii_cmd, "cmd", nome="CMD Teste", ano="1", pw="cmd1123")
        create_aluno(nii_aluno, "T93", "Aluno Reset", ano="1")

        login_as(client, nii_cmd, pw="cmd1123")
        csrf = get_csrf(client)
        resp = client.post(
            f"/cmd/reset-password/{nii_aluno}",
            data={"csrf_token": csrf, "ano": "1", "d": date.today().isoformat()},
            follow_redirects=True,
        )
        html = resp.data.decode()
        assert "resetada" in html.lower()

    def test_cmd_cannot_reset_other_year(self, app, client):
        """CMD não pode resetar password de aluno de outro ano."""
        nii_cmd = "cmd1"
        nii_aluno = "994"
        create_aluno(nii_aluno, "T94", "Aluno Outro Ano", ano="3")

        login_as(client, nii_cmd, pw="cmd1123")
        csrf = get_csrf(client)
        resp = client.post(
            f"/cmd/reset-password/{nii_aluno}",
            data={"csrf_token": csrf, "ano": "3", "d": date.today().isoformat()},
            follow_redirects=True,
        )
        html = resp.data.decode()
        assert "teu ano" in html.lower() or "Só podes" in html

    def test_cmd_cannot_reset_system_account(self, app, client):
        """CMD não pode resetar password de contas de sistema."""
        nii_cmd = "cmd1"
        login_as(client, nii_cmd, pw="cmd1123")
        csrf = get_csrf(client)
        resp = client.post(
            "/cmd/reset-password/cozinha",
            data={"csrf_token": csrf, "ano": "1", "d": date.today().isoformat()},
            follow_redirects=True,
        )
        html = resp.data.decode()
        assert "alunos" in html.lower()

    def test_reset_button_visible_on_editar(self, app, client):
        """O botão de reset deve aparecer na página de edição do aluno."""
        nii_cmd = "cmd1"
        nii_aluno = "993"
        login_as(client, nii_cmd, pw="cmd1123")
        resp = client.get(f"/cmd/editar-aluno/{nii_aluno}?ano=1", follow_redirects=True)
        html = resp.data.decode()
        assert "Resetar password" in html
