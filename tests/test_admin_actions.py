"""
tests/test_admin_actions.py — Testes abrangentes para blueprints/admin/routes.py
=================================================================================
Cobre acoes CRUD de utilizadores, calendario, companhias, menus, importacao CSV,
e paginas GET do admin.
"""

import io
import os

os.environ.setdefault("ENV", "development")
os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production")

from tests.conftest import create_aluno, create_system_user, get_csrf, login_as


# ═══════════════════════════════════════════════════════════════════════════
# Utilizadores — Criar
# ═══════════════════════════════════════════════════════════════════════════


class TestAdminCriarUser:
    def test_criar_user_success(self, app, client):
        with app.app_context():
            create_system_user("aa_adm", "admin", pw="aa_adm123")
        login_as(client, "aa_adm", "aa_adm123")
        token = get_csrf(client)
        resp = client.post(
            "/admin/utilizadores",
            data={
                "acao": "criar",
                "nii": "aa_cr1",
                "ni": "AC1",
                "nome": "Aluno Criar Test",
                "ano": "1",
                "perfil": "aluno",
                "pw": "Secure12",
                "csrf_token": token,
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        from core.database import db

        with db() as conn:
            row = conn.execute(
                "SELECT * FROM utilizadores WHERE NII='aa_cr1'"
            ).fetchone()
        assert row is not None
        assert row["Nome_completo"] == "Aluno Criar Test"


# ═══════════════════════════════════════════════════════════════════════════
# Utilizadores — Editar User
# ═══════════════════════════════════════════════════════════════════════════


class TestAdminEditUser:
    def test_edit_user_success(self, app, client):
        with app.app_context():
            create_aluno("aa_ed1", "AE1", "Edit Test", pw="editpw123")
        login_as(client, "aa_adm", "aa_adm123")
        token = get_csrf(client)
        resp = client.post(
            "/admin/utilizadores",
            data={
                "acao": "editar_user",
                "nii": "aa_ed1",
                "nome": "New Name",
                "ni": "AE1",
                "ano": "2",
                "perfil": "aluno",
                "email": "",
                "telemovel": "",
                "pw": "",
                "csrf_token": token,
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        from core.database import db

        with db() as conn:
            row = conn.execute(
                "SELECT * FROM utilizadores WHERE NII='aa_ed1'"
            ).fetchone()
        assert row["Nome_completo"] == "New Name"
        assert row["ano"] == 2

    def test_edit_user_invalid_nii(self, app, client):
        login_as(client, "aa_adm", "aa_adm123")
        token = get_csrf(client)
        resp = client.post(
            "/admin/utilizadores",
            data={
                "acao": "editar_user",
                "nii": "",
                "nome": "Name",
                "ni": "X1",
                "ano": "1",
                "perfil": "aluno",
                "email": "",
                "telemovel": "",
                "pw": "",
                "csrf_token": token,
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert "NII" in resp.data.decode()

    def test_edit_user_empty_nome(self, app, client):
        login_as(client, "aa_adm", "aa_adm123")
        token = get_csrf(client)
        resp = client.post(
            "/admin/utilizadores",
            data={
                "acao": "editar_user",
                "nii": "aa_ed1",
                "nome": "",
                "ni": "AE1",
                "ano": "1",
                "perfil": "aluno",
                "email": "",
                "telemovel": "",
                "pw": "",
                "csrf_token": token,
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert "Nome" in resp.data.decode()

    def test_edit_user_invalid_ni(self, app, client):
        login_as(client, "aa_adm", "aa_adm123")
        token = get_csrf(client)
        resp = client.post(
            "/admin/utilizadores",
            data={
                "acao": "editar_user",
                "nii": "aa_ed1",
                "nome": "Valid Name",
                "ni": "invalid ni!@#",
                "ano": "1",
                "perfil": "aluno",
                "email": "",
                "telemovel": "",
                "pw": "",
                "csrf_token": token,
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert "NI" in resp.data.decode()

    def test_edit_user_invalid_ano(self, app, client):
        login_as(client, "aa_adm", "aa_adm123")
        token = get_csrf(client)
        resp = client.post(
            "/admin/utilizadores",
            data={
                "acao": "editar_user",
                "nii": "aa_ed1",
                "nome": "Valid Name",
                "ni": "AE1",
                "ano": "99",
                "perfil": "aluno",
                "email": "",
                "telemovel": "",
                "pw": "",
                "csrf_token": token,
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert "Ano" in resp.data.decode()

    def test_edit_user_invalid_perfil(self, app, client):
        login_as(client, "aa_adm", "aa_adm123")
        token = get_csrf(client)
        resp = client.post(
            "/admin/utilizadores",
            data={
                "acao": "editar_user",
                "nii": "aa_ed1",
                "nome": "Valid Name",
                "ni": "AE1",
                "ano": "1",
                "perfil": "hacker",
                "email": "",
                "telemovel": "",
                "pw": "",
                "csrf_token": token,
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert "Perfil" in resp.data.decode()

    def test_edit_user_invalid_email(self, app, client):
        login_as(client, "aa_adm", "aa_adm123")
        token = get_csrf(client)
        resp = client.post(
            "/admin/utilizadores",
            data={
                "acao": "editar_user",
                "nii": "aa_ed1",
                "nome": "Valid Name",
                "ni": "AE1",
                "ano": "1",
                "perfil": "aluno",
                "email": "not-an-email",
                "telemovel": "",
                "pw": "",
                "csrf_token": token,
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert "Email" in resp.data.decode()

    def test_edit_user_invalid_phone(self, app, client):
        login_as(client, "aa_adm", "aa_adm123")
        token = get_csrf(client)
        resp = client.post(
            "/admin/utilizadores",
            data={
                "acao": "editar_user",
                "nii": "aa_ed1",
                "nome": "Valid Name",
                "ni": "AE1",
                "ano": "1",
                "perfil": "aluno",
                "email": "",
                "telemovel": "abc",
                "pw": "",
                "csrf_token": token,
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200


# ═══════════════════════════════════════════════════════════════════════════
# Utilizadores — Editar Contactos
# ═══════════════════════════════════════════════════════════════════════════


class TestAdminEditContactos:
    def test_edit_contactos_success(self, app, client):
        with app.app_context():
            create_aluno("aa_ct1", "ACT1", "Contacto Test", pw="contact123")
        login_as(client, "aa_adm", "aa_adm123")
        token = get_csrf(client)
        resp = client.post(
            "/admin/utilizadores",
            data={
                "acao": "editar_contactos",
                "nii": "aa_ct1",
                "email": "test@example.com",
                "telemovel": "912345678",
                "csrf_token": token,
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        from core.database import db

        with db() as conn:
            row = conn.execute(
                "SELECT email, telemovel FROM utilizadores WHERE NII='aa_ct1'"
            ).fetchone()
        assert row["email"] == "test@example.com"
        assert row["telemovel"] == "912345678"

    def test_edit_contactos_invalid_email(self, app, client):
        login_as(client, "aa_adm", "aa_adm123")
        token = get_csrf(client)
        resp = client.post(
            "/admin/utilizadores",
            data={
                "acao": "editar_contactos",
                "nii": "aa_ct1",
                "email": "bad-email",
                "telemovel": "",
                "csrf_token": token,
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert "Email" in resp.data.decode()

    def test_edit_contactos_invalid_phone(self, app, client):
        login_as(client, "aa_adm", "aa_adm123")
        token = get_csrf(client)
        resp = client.post(
            "/admin/utilizadores",
            data={
                "acao": "editar_contactos",
                "nii": "aa_ct1",
                "email": "",
                "telemovel": "abc",
                "csrf_token": token,
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200


# ═══════════════════════════════════════════════════════════════════════════
# Utilizadores — Reset PW, Desbloquear, Eliminar
# ═══════════════════════════════════════════════════════════════════════════


class TestAdminResetPW:
    def test_reset_pw_success(self, app, client):
        with app.app_context():
            create_aluno("aa_rp1", "ARP1", "Reset PW Test", pw="resetpw123")
        login_as(client, "aa_adm", "aa_adm123")
        token = get_csrf(client)
        resp = client.post(
            "/admin/utilizadores",
            data={
                "acao": "reset_pw",
                "nii": "aa_rp1",
                "csrf_token": token,
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert "Password" in resp.data.decode() or "resetada" in resp.data.decode()


class TestAdminDesbloquear:
    def test_desbloquear_success(self, app, client):
        with app.app_context():
            create_aluno("aa_ub1", "AUB1", "Unblock Test", pw="unblock123")
            from core.database import db

            with db() as conn:
                conn.execute(
                    "UPDATE utilizadores SET locked_until='2099-12-31' WHERE NII='aa_ub1'"
                )
                conn.commit()
        login_as(client, "aa_adm", "aa_adm123")
        token = get_csrf(client)
        resp = client.post(
            "/admin/utilizadores",
            data={
                "acao": "desbloquear",
                "nii": "aa_ub1",
                "csrf_token": token,
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        from core.database import db

        with db() as conn:
            row = conn.execute(
                "SELECT locked_until FROM utilizadores WHERE NII='aa_ub1'"
            ).fetchone()
        assert row["locked_until"] is None


class TestAdminEliminar:
    def test_eliminar_success(self, app, client):
        with app.app_context():
            create_aluno("aa_del1", "AD1", "Delete Test", pw="delete123")
        login_as(client, "aa_adm", "aa_adm123")
        token = get_csrf(client)
        resp = client.post(
            "/admin/utilizadores",
            data={
                "acao": "eliminar",
                "nii": "aa_del1",
                "csrf_token": token,
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        from core.database import db

        with db() as conn:
            row = conn.execute(
                "SELECT * FROM utilizadores WHERE NII='aa_del1'"
            ).fetchone()
        assert row is None

    def test_eliminar_nonexistent(self, app, client):
        login_as(client, "aa_adm", "aa_adm123")
        token = get_csrf(client)
        resp = client.post(
            "/admin/utilizadores",
            data={
                "acao": "eliminar",
                "nii": "aa_ghost",
                "csrf_token": token,
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200


# ═══════════════════════════════════════════════════════════════════════════
# Calendario
# ═══════════════════════════════════════════════════════════════════════════


class TestAdminCalendario:
    def test_adicionar_single_day(self, app, client):
        login_as(client, "aa_adm", "aa_adm123")
        token = get_csrf(client)
        resp = client.post(
            "/admin/calendario",
            data={
                "acao": "adicionar",
                "dia_de": "2099-06-15",
                "dia_ate": "",
                "tipo": "normal",
                "nota": "Test entry",
                "csrf_token": token,
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        from core.database import db

        with db() as conn:
            row = conn.execute(
                "SELECT * FROM calendario_operacional WHERE data='2099-06-15'"
            ).fetchone()
        assert row is not None
        assert row["tipo"] == "normal"

    def test_adicionar_date_range(self, app, client):
        login_as(client, "aa_adm", "aa_adm123")
        token = get_csrf(client)
        resp = client.post(
            "/admin/calendario",
            data={
                "acao": "adicionar",
                "dia_de": "2099-07-01",
                "dia_ate": "2099-07-03",
                "tipo": "feriado",
                "nota": "",
                "csrf_token": token,
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        from core.database import db

        with db() as conn:
            rows = conn.execute(
                "SELECT * FROM calendario_operacional WHERE data BETWEEN '2099-07-01' AND '2099-07-03'"
            ).fetchall()
        assert len(rows) == 3

    def test_adicionar_empty_date(self, app, client):
        login_as(client, "aa_adm", "aa_adm123")
        token = get_csrf(client)
        resp = client.post(
            "/admin/calendario",
            data={
                "acao": "adicionar",
                "dia_de": "",
                "dia_ate": "",
                "tipo": "normal",
                "nota": "",
                "csrf_token": token,
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

    def test_adicionar_invalid_date_range(self, app, client):
        login_as(client, "aa_adm", "aa_adm123")
        token = get_csrf(client)
        resp = client.post(
            "/admin/calendario",
            data={
                "acao": "adicionar",
                "dia_de": "2099-08-10",
                "dia_ate": "2099-08-01",
                "tipo": "normal",
                "nota": "",
                "csrf_token": token,
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

    def test_remover(self, app, client):
        from core.database import db

        with app.app_context():
            with db() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO calendario_operacional(data,tipo) VALUES ('2099-09-01','normal')"
                )
                conn.commit()
        login_as(client, "aa_adm", "aa_adm123")
        token = get_csrf(client)
        resp = client.post(
            "/admin/calendario",
            data={
                "acao": "remover",
                "dia": "2099-09-01",
                "csrf_token": token,
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        with db() as conn:
            row = conn.execute(
                "SELECT * FROM calendario_operacional WHERE data='2099-09-01'"
            ).fetchone()
        assert row is None


# ═══════════════════════════════════════════════════════════════════════════
# Companhias
# ═══════════════════════════════════════════════════════════════════════════


class TestAdminCompanhias:
    def test_criar_turma(self, app, client):
        login_as(client, "aa_adm", "aa_adm123")
        token = get_csrf(client)
        resp = client.post(
            "/admin/companhias",
            data={
                "acao": "criar_turma",
                "nome_turma": "Turma AA Test",
                "ano_turma": "1",
                "descricao": "Test class",
                "csrf_token": token,
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        from core.database import db

        with db() as conn:
            row = conn.execute(
                "SELECT * FROM turmas WHERE nome='Turma AA Test'"
            ).fetchone()
        assert row is not None

    def test_eliminar_turma(self, app, client):
        from core.database import db

        with app.app_context():
            with db() as conn:
                conn.execute(
                    "INSERT INTO turmas (nome, ano, descricao) VALUES ('TurmaDelAA','2','to delete')"
                )
                conn.commit()
                tid = conn.execute(
                    "SELECT id FROM turmas WHERE nome='TurmaDelAA'"
                ).fetchone()["id"]
        login_as(client, "aa_adm", "aa_adm123")
        token = get_csrf(client)
        resp = client.post(
            "/admin/companhias",
            data={
                "acao": "eliminar_turma",
                "tid": str(tid),
                "csrf_token": token,
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        with db() as conn:
            row = conn.execute("SELECT * FROM turmas WHERE id=?", (tid,)).fetchone()
        assert row is None

    def test_mover_aluno(self, app, client):
        with app.app_context():
            create_aluno("aa_mv1", "AMV1", "Mover Test", ano="1", pw="mover123")
        login_as(client, "aa_adm", "aa_adm123")
        token = get_csrf(client)
        resp = client.post(
            "/admin/companhias",
            data={
                "acao": "mover_aluno",
                "nii_m": "aa_mv1",
                "novo_ano": "3",
                "csrf_token": token,
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        from core.database import db

        with db() as conn:
            row = conn.execute(
                "SELECT ano FROM utilizadores WHERE NII='aa_mv1'"
            ).fetchone()
        assert row["ano"] == 3

    def test_promover_todos(self, app, client):
        with app.app_context():
            create_aluno("aa_pr1", "APR1", "Promo Test1", ano="4", pw="promo123")
            create_aluno("aa_pr2", "APR2", "Promo Test2", ano="4", pw="promo456")
        login_as(client, "aa_adm", "aa_adm123")
        token = get_csrf(client)
        resp = client.post(
            "/admin/companhias",
            data={
                "acao": "promover_todos",
                "ano_origem": "4",
                "csrf_token": token,
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        from core.database import db

        with db() as conn:
            rows = conn.execute(
                "SELECT ano FROM utilizadores WHERE NII IN ('aa_pr1','aa_pr2')"
            ).fetchall()
        for row in rows:
            assert row["ano"] == 5


# ═══════════════════════════════════════════════════════════════════════════
# Menus
# ═══════════════════════════════════════════════════════════════════════════


class TestAdminMenus:
    def test_save_menu(self, app, client):
        login_as(client, "aa_adm", "aa_adm123")
        token = get_csrf(client)
        resp = client.post(
            "/admin/menus",
            data={
                "data": "2099-10-01",
                "pequeno_almoco": "Pao com manteiga",
                "lanche": "Fruta",
                "almoco_normal": "Arroz com frango",
                "almoco_veg": "Arroz com legumes",
                "almoco_dieta": "Caldo",
                "jantar_normal": "Sopa e peixe",
                "jantar_veg": "Sopa de legumes",
                "jantar_dieta": "Caldo light",
                "csrf_token": token,
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        from core.database import db

        with db() as conn:
            row = conn.execute(
                "SELECT * FROM menus_diarios WHERE data='2099-10-01'"
            ).fetchone()
        assert row is not None
        assert row["almoco_normal"] == "Arroz com frango"


# ═══════════════════════════════════════════════════════════════════════════
# Importar CSV
# ═══════════════════════════════════════════════════════════════════════════


class TestAdminImportarCSV:
    def test_preview_csv(self, app, client):
        login_as(client, "aa_adm", "aa_adm123")
        token = get_csrf(client)
        csv_data = io.BytesIO(
            b"aa_csv1,ACV1,CSV Test One,1\naa_csv2,ACV2,CSV Test Two,2\n"
        )
        resp = client.post(
            "/admin/importar-csv",
            data={
                "acao": "preview",
                "csvfile": (csv_data, "test.csv"),
                "csrf_token": token,
            },
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        assert resp.status_code == 200
        body = resp.data.decode()
        assert "aa_csv1" in body or "CSV Test" in body

    def test_confirmar_csv(self, app, client):
        login_as(client, "admin", "admin123")
        token = get_csrf(client)
        raw_csv = "aacsv30,ACV30,CSV Confirm Test,1,aluno,csvpass123\n"
        resp = client.post(
            "/admin/importar-csv",
            data={
                "acao": "confirmar",
                "raw_csv": raw_csv,
                "csrf_token": token,
            },
            follow_redirects=False,
        )
        # Confirm redirects to utilizadores page
        assert resp.status_code in (302, 303)

    def test_preview_no_file(self, app, client):
        login_as(client, "aa_adm", "aa_adm123")
        token = get_csrf(client)
        resp = client.post(
            "/admin/importar-csv",
            data={
                "acao": "preview",
                "csrf_token": token,
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200


# ═══════════════════════════════════════════════════════════════════════════
# GET Pages
# ═══════════════════════════════════════════════════════════════════════════


class TestAdminGETPages:
    def test_admin_home(self, app, client):
        login_as(client, "aa_adm", "aa_adm123")
        resp = client.get("/admin")
        assert resp.status_code == 200

    def test_admin_log(self, app, client):
        login_as(client, "aa_adm", "aa_adm123")
        resp = client.get("/admin/log")
        assert resp.status_code == 200

    def test_admin_log_with_filters(self, app, client):
        login_as(client, "aa_adm", "aa_adm123")
        resp = client.get(
            "/admin/log?q_nome=test&q_por=admin&d0=2020-01-01&d1=2099-12-31&limite=10"
        )
        assert resp.status_code == 200

    def test_admin_audit(self, app, client):
        login_as(client, "aa_adm", "aa_adm123")
        resp = client.get("/admin/auditoria")
        assert resp.status_code == 200

    def test_admin_audit_with_filters(self, app, client):
        login_as(client, "aa_adm", "aa_adm123")
        resp = client.get("/admin/auditoria?actor=admin&action=login&limite=10")
        assert resp.status_code == 200

    def test_admin_backup_download(self, app, client):
        login_as(client, "aa_adm", "aa_adm123")
        resp = client.get("/admin/backup-download")
        assert resp.status_code == 200
        assert "application" in resp.content_type

    def test_admin_utilizadores_get(self, app, client):
        login_as(client, "aa_adm", "aa_adm123")
        resp = client.get("/admin/utilizadores")
        assert resp.status_code == 200

    def test_admin_utilizadores_search(self, app, client):
        login_as(client, "aa_adm", "aa_adm123")
        resp = client.get("/admin/utilizadores?q=Test&ano=1")
        assert resp.status_code == 200

    def test_admin_calendario_get(self, app, client):
        login_as(client, "aa_adm", "aa_adm123")
        resp = client.get("/admin/calendario")
        assert resp.status_code == 200

    def test_admin_companhias_get(self, app, client):
        login_as(client, "aa_adm", "aa_adm123")
        resp = client.get("/admin/companhias")
        assert resp.status_code == 200

    def test_admin_menus_get(self, app, client):
        login_as(client, "aa_adm", "aa_adm123")
        resp = client.get("/admin/menus")
        assert resp.status_code == 200

    def test_admin_importar_csv_get(self, app, client):
        login_as(client, "aa_adm", "aa_adm123")
        resp = client.get("/admin/importar-csv")
        assert resp.status_code == 200
