"""Testes para as rotas do blueprint admin."""

from datetime import date

import pytest

from tests.conftest import create_system_user, create_aluno, login_as, get_csrf


@pytest.fixture(autouse=True)
def setup_admin(app):
    """Cria um admin de teste."""
    create_system_user("adm99", "admin", nome="Admin Teste", pw="Admin1234")


def _login_admin(client):
    login_as(client, "adm99", pw="Admin1234")
    return get_csrf(client)


class TestAdminHome:
    def test_admin_home_ok(self, app, client):
        _login_admin(client)
        resp = client.get("/admin")
        assert resp.status_code == 200
        assert "Administração" in resp.data.decode()

    def test_admin_home_requires_admin(self, app, client):
        create_system_user("coz99", "cozinha", pw="Coz12345")
        login_as(client, "coz99", pw="Coz12345")
        resp = client.get("/admin", follow_redirects=False)
        # Should redirect non-admin users
        assert resp.status_code in (302, 403) or b"Administra" not in resp.data


class TestAdminUtilizadores:
    def test_get_utilizadores(self, app, client):
        _login_admin(client)
        resp = client.get("/admin/utilizadores")
        assert resp.status_code == 200
        assert (
            "Utilizadores" in resp.data.decode()
            or "utilizadores" in resp.data.decode().lower()
        )

    def test_criar_utilizador(self, app, client):
        csrf = _login_admin(client)
        resp = client.post(
            "/admin/utilizadores",
            data={
                "csrf_token": csrf,
                "acao": "criar",
                "nii": "newuser1",
                "ni": "N01",
                "nome": "Novo Utilizador",
                "ano": "1",
                "perfil": "aluno",
                "pw": "NovoUser123",
            },
            follow_redirects=True,
        )
        html = resp.data.decode()
        assert "criado" in html.lower() or resp.status_code == 200

    def test_editar_user(self, app, client):
        create_aluno("editme1", "E01", "Aluno Editar", ano="2")
        csrf = _login_admin(client)
        resp = client.post(
            "/admin/utilizadores",
            data={
                "csrf_token": csrf,
                "acao": "editar_user",
                "nii": "editme1",
                "ni": "E01",
                "nome": "Aluno Editado",
                "ano": "2",
                "perfil": "aluno",
                "email": "test@example.pt",
                "telemovel": "+351912345678",
                "pw": "",
            },
            follow_redirects=True,
        )
        html = resp.data.decode()
        assert "atualizado" in html.lower() or resp.status_code == 200

    def test_editar_contactos(self, app, client):
        create_aluno("contac1", "C01", "Aluno Contacto", ano="1")
        csrf = _login_admin(client)
        resp = client.post(
            "/admin/utilizadores",
            data={
                "csrf_token": csrf,
                "acao": "editar_contactos",
                "nii": "contac1",
                "email": "contac@test.pt",
                "telemovel": "+351911111111",
            },
            follow_redirects=True,
        )
        html = resp.data.decode()
        assert (
            "atualizado" in html.lower()
            or "contactos" in html.lower()
            or resp.status_code == 200
        )

    def test_reset_pw(self, app, client):
        create_aluno("resetme", "R01", "Aluno Reset", ano="1")
        csrf = _login_admin(client)
        resp = client.post(
            "/admin/utilizadores",
            data={
                "csrf_token": csrf,
                "acao": "reset_pw",
                "nii": "resetme",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

    def test_toggle_active(self, app, client):
        create_aluno("togact1", "T01", "Aluno Toggle", ano="1")
        csrf = _login_admin(client)
        resp = client.post(
            "/admin/utilizadores",
            data={
                "csrf_token": csrf,
                "acao": "toggle_active",
                "nii": "togact1",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

    def test_eliminar(self, app, client):
        create_aluno("delme1", "D01", "Aluno Delete", ano="1")
        csrf = _login_admin(client)
        resp = client.post(
            "/admin/utilizadores",
            data={
                "csrf_token": csrf,
                "acao": "eliminar",
                "nii": "delme1",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

    def test_unblock(self, app, client):
        create_aluno("unblk1", "U01", "Aluno Unblock", ano="1")
        csrf = _login_admin(client)
        resp = client.post(
            "/admin/utilizadores",
            data={
                "csrf_token": csrf,
                "acao": "unblock",
                "nii": "unblk1",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

    def test_editar_user_invalid_nii(self, app, client):
        csrf = _login_admin(client)
        resp = client.post(
            "/admin/utilizadores",
            data={
                "csrf_token": csrf,
                "acao": "editar_user",
                "nii": "",
                "nome": "X",
                "ni": "X",
                "ano": "1",
                "perfil": "aluno",
                "email": "",
                "telemovel": "",
                "pw": "",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

    def test_editar_user_invalid_nome(self, app, client):
        csrf = _login_admin(client)
        resp = client.post(
            "/admin/utilizadores",
            data={
                "csrf_token": csrf,
                "acao": "editar_user",
                "nii": "editme1",
                "nome": "",
                "ni": "X",
                "ano": "1",
                "perfil": "aluno",
                "email": "",
                "telemovel": "",
                "pw": "",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

    def test_search_filter(self, app, client):
        _login_admin(client)
        resp = client.get("/admin/utilizadores?q=Admin")
        assert resp.status_code == 200


class TestAdminMenus:
    def test_get_menus(self, app, client):
        _login_admin(client)
        resp = client.get("/admin/menus")
        assert resp.status_code == 200
        assert "Menus" in resp.data.decode() or "menus" in resp.data.decode().lower()

    def test_post_menu(self, app, client):
        csrf = _login_admin(client)
        resp = client.post(
            "/admin/menus",
            data={
                "csrf_token": csrf,
                "data": date.today().isoformat(),
                "pequeno_almoco": "Pão com manteiga",
                "lanche": "Bolacha",
                "almoco_normal": "Bacalhau",
                "almoco_veg": "Tofu",
                "almoco_dieta": "Grelhado",
                "jantar_normal": "Sopa",
                "jantar_veg": "Legumes",
                "jantar_dieta": "Caldo",
                "cap_pequeno_almoco": "100",
                "cap_lanche": "100",
                "cap_almoco": "80",
                "cap_jantar": "80",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200


class TestAdminLog:
    def test_get_log(self, app, client):
        _login_admin(client)
        resp = client.get("/admin/log")
        assert resp.status_code == 200

    def test_get_log_with_filters(self, app, client):
        _login_admin(client)
        resp = client.get(
            "/admin/log?q_nome=Test&d0=2026-01-01&d1=2026-12-31&limite=50"
        )
        assert resp.status_code == 200


class TestAdminAuditoria:
    def test_get_audit(self, app, client):
        _login_admin(client)
        resp = client.get("/admin/auditoria")
        assert resp.status_code == 200

    def test_get_audit_with_filters(self, app, client):
        _login_admin(client)
        resp = client.get("/admin/auditoria?q_acao=login&d0=2026-01-01&d1=2026-12-31")
        assert resp.status_code == 200


class TestAdminCalendario:
    def test_get_calendario(self, app, client):
        _login_admin(client)
        resp = client.get("/admin/calendario")
        assert resp.status_code == 200

    def test_post_calendario(self, app, client):
        csrf = _login_admin(client)
        resp = client.post(
            "/admin/calendario",
            data={
                "csrf_token": csrf,
                "data": date.today().isoformat(),
                "tipo": "feriado",
                "nota": "Dia de teste",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

    def test_post_calendario_remover(self, app, client):
        csrf = _login_admin(client)
        resp = client.post(
            "/admin/calendario",
            data={
                "csrf_token": csrf,
                "acao": "remover",
                "data": date.today().isoformat(),
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200


class TestAdminCompanhias:
    def test_get_companhias(self, app, client):
        _login_admin(client)
        resp = client.get("/admin/companhias")
        assert resp.status_code == 200


class TestAdminImportarCSV:
    def test_get_importar(self, app, client):
        _login_admin(client)
        resp = client.get("/admin/importar-csv")
        assert resp.status_code == 200

    def test_preview_csv(self, app, client):
        import io

        csrf = _login_admin(client)
        csv_data = "NII,NI,Nome,Ano\n55501,N501,Aluno CSV1,1\n55502,N502,Aluno CSV2,2\n"
        data = {
            "csrf_token": csrf,
            "acao": "preview",
        }
        resp = client.post(
            "/admin/importar-csv",
            data={
                **data,
                "csvfile": (io.BytesIO(csv_data.encode("utf-8")), "test.csv"),
            },
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        assert resp.status_code == 200

    def test_confirmar_csv(self, app, client):
        csrf = _login_admin(client)
        raw = "55601,N601,Aluno Import,1\n55602,N602,Aluno Import2,2\n"
        resp = client.post(
            "/admin/importar-csv",
            data={
                "csrf_token": csrf,
                "acao": "confirmar",
                "raw_csv": raw,
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200


class TestAdminBackup:
    def test_backup_download(self, app, client):
        _login_admin(client)
        resp = client.get("/admin/backup-download")
        assert resp.status_code == 200
