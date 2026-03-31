"""
tests/test_admin_menus.py — Testes de menus e capacidades (admin/cozinha)
=========================================================================
"""

from datetime import date, timedelta

from tests.conftest import create_system_user, get_csrf, login_as


def _future_date(days=5):
    return (date.today() + timedelta(days=days)).isoformat()


class TestAdminMenus:
    def test_cozinha_can_view_menus(self, app, client):
        """Cozinha pode aceder à página de menus."""
        create_system_user("coz_menu1", "cozinha")
        login_as(client, "coz_menu1", "coz_menu1123")
        resp = client.get("/admin/menus")
        assert resp.status_code == 200

    def test_cozinha_can_save_menu(self, app, client):
        """Cozinha pode guardar um menu."""
        create_system_user("coz_menu2", "cozinha")
        login_as(client, "coz_menu2", "coz_menu2123")
        token = get_csrf(client)
        d = _future_date()
        resp = client.post(
            "/admin/menus",
            data={
                "csrf_token": token,
                "data": d,
                "pequeno_almoco": "Pão com manteiga",
                "lanche": "Bolacha",
                "almoco_normal": "Bacalhau",
                "almoco_veg": "Tofu",
                "almoco_dieta": "Grelhado",
                "jantar_normal": "Sopa + Prato",
                "jantar_veg": "Legumes",
                "jantar_dieta": "Cozido",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 302

        # Verificar que o menu foi guardado
        resp2 = client.get(f"/admin/menus?d={d}")
        assert resp2.status_code == 200
        assert b"Bacalhau" in resp2.data

    def test_cozinha_can_save_capacity(self, app, client):
        """Cozinha pode guardar capacidades."""
        create_system_user("coz_menu3", "cozinha")
        login_as(client, "coz_menu3", "coz_menu3123")
        token = get_csrf(client)
        d = _future_date(6)
        resp = client.post(
            "/admin/menus",
            data={
                "csrf_token": token,
                "data": d,
                "cap_pequeno_almoco": "200",
                "cap_lanche": "150",
                "cap_almoco": "180",
                "cap_jantar": "170",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 302

    def test_admin_can_view_menus(self, app, client):
        """Admin pode aceder à página de menus."""
        create_system_user("adm_menu1", "admin")
        login_as(client, "adm_menu1", "adm_menu1123")
        resp = client.get("/admin/menus")
        assert resp.status_code == 200

    def test_aluno_cannot_access_menus(self, app, client):
        """Aluno não pode aceder à página de menus."""
        from tests.conftest import create_aluno

        create_aluno("alu_menu1", "AM1", "Aluno Menu")
        login_as(client, "alu_menu1", "alu_menu1")
        resp = client.get("/admin/menus", follow_redirects=False)
        assert resp.status_code in (302, 403)


class TestAdminAuditExport:
    def test_admin_can_export_audit(self, app, client):
        """Admin pode exportar audit log como CSV."""
        create_system_user("adm_aud1", "admin")
        login_as(client, "adm_aud1", "adm_aud1123")
        resp = client.get("/admin/auditoria/exportar")
        assert resp.status_code == 200
        assert resp.content_type.startswith("text/csv")
        assert b"Timestamp" in resp.data

    def test_non_admin_cannot_export_audit(self, app, client):
        """Não-admin não pode exportar audit log."""
        create_system_user("coz_aud1", "cozinha")
        login_as(client, "coz_aud1", "coz_aud1123")
        resp = client.get("/admin/auditoria/exportar", follow_redirects=False)
        assert resp.status_code in (302, 403)
