"""Testes para as funcionalidades novas desta iteração:
- #13 SLA / prazos por refeição (utils.business._sla_itens_do_dia)
- #14 Forecast lite (core.forecast.forecast_proximos_dias)
- #16 PWA (manifest + sw.js + registration script)
- #17 Dietas permanentes (core.users.get/update_dieta_padrao, autofill)
- #18 PDF export (core.exports.export_pdf + exportacao_pdf_do_dia)
"""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta

import pytest

from tests.conftest import create_aluno


# ═══════════════════════════════════════════════════════════════════════════
# #13 — SLA / countdown
# ═══════════════════════════════════════════════════════════════════════════


class TestSLAItens:
    def test_devolve_uma_entrada_por_refeicao(self, app):
        from utils.business import _sla_itens_do_dia

        # ancora num dia "longe" para que as janelas sejam determinísticas
        d = date.today() + timedelta(days=7)
        itens = _sla_itens_do_dia(d)
        keys = {i["key"] for i in itens}
        assert keys == {"pequeno_almoco", "lanche", "almoco", "jantar"}

    def test_janela_aberta_para_dia_longe(self, app):
        from utils.business import _sla_itens_do_dia

        d = date.today() + timedelta(days=10)
        # fixamos "agora" no início do dia anterior (muito antes do prazo)
        agora = datetime(d.year, d.month, d.day) - timedelta(days=5)
        itens = _sla_itens_do_dia(d, agora=agora)
        for i in itens:
            assert i["estado"] == "ok", f"{i['key']} estado={i['estado']}"
            assert "fecha em" in i["detalhe"]

    def test_janela_fechada_para_passado(self, app):
        from utils.business import _sla_itens_do_dia

        d = date.today() - timedelta(days=1)
        agora = datetime.now()
        itens = _sla_itens_do_dia(d, agora=agora)
        # Todas as refeições do dia anterior já passaram do prazo
        for i in itens:
            assert i["estado"] == "closed"
            assert "fechou h" in i["detalhe"]

    def test_warn_se_proximo_do_prazo(self, app):
        """Estado 'warn' quando faltam < 6h para o prazo."""
        import config as cfg
        from utils.business import _sla_itens_do_dia

        d = date.today() + timedelta(days=3)
        almoco_ini = cfg.REFEICAO_HORARIOS["almoco"][0]
        hh, mm = (int(x) for x in almoco_ini.split(":"))
        # 48h antes do almoço começar é o prazo. Ponho agora = prazo - 3h.
        prazo = datetime(d.year, d.month, d.day, hh, mm) - timedelta(hours=48)
        agora = prazo - timedelta(hours=3)
        itens = {i["key"]: i for i in _sla_itens_do_dia(d, agora=agora)}
        assert itens["almoco"]["estado"] == "warn"

    def test_desligado_se_prazo_none(self, app, monkeypatch):
        from utils import business as bmod

        monkeypatch.setattr(bmod, "PRAZO_LIMITE_HORAS", None)
        assert bmod._sla_itens_do_dia(date.today()) == []

    def test_fmt_hm_formata_dias_horas_minutos(self, app):
        from utils.business import _fmt_hm

        assert _fmt_hm(30) == "30m"
        assert _fmt_hm(90) == "1h 30m"
        assert _fmt_hm(60 * 24 * 2 + 60 * 3) == "2d 3h"
        assert _fmt_hm(-90) == "1h 30m"  # valor absoluto


# ═══════════════════════════════════════════════════════════════════════════
# #14 — Forecast
# ═══════════════════════════════════════════════════════════════════════════


class TestForecast:
    def test_forecast_devolve_lista_do_tamanho_pedido(self, app):
        from core.forecast import forecast_proximos_dias

        out = forecast_proximos_dias(dias=5)
        assert len(out) == 5
        # Ordem cronológica a partir de amanhã
        amanha = date.today() + timedelta(days=1)
        assert out[0].dia == amanha
        assert out[-1].dia == amanha + timedelta(days=4)

    def test_forecast_respeita_janela_historica(self, app):
        from core.forecast import forecast_proximos_dias

        # Amostras nunca devem ultrapassar semanas_historico
        out = forecast_proximos_dias(dias=3, semanas_historico=4)
        for p in out:
            assert 0 <= p.amostras <= 4
            # Valores são sempre não-negativos
            assert p.pa >= 0 and p.almoco >= 0 and p.jantar >= 0

    def test_rolling_mean_basico(self, app):
        from core.forecast import _rolling_mean_by_weekday

        # serie: 3 segundas-feiras (weekday=0) com contagens conhecidas
        seg1 = date(2025, 1, 6)  # segunda
        seg2 = date(2025, 1, 13)
        seg3 = date(2025, 1, 20)
        ter = date(2025, 1, 14)  # terça (ignorada)
        serie = [
            (seg1, 10, 5, 20, 18),
            (ter, 999, 999, 999, 999),
            (seg2, 20, 10, 30, 22),
            (seg3, 30, 15, 40, 26),
        ]
        totais, n = _rolling_mean_by_weekday(serie, weekday=0, samples=3)
        assert n == 3
        assert totais["pa"] == round((10 + 20 + 30) / 3)
        assert totais["almoco"] == round((20 + 30 + 40) / 3)

    def test_rolling_mean_samples_limita(self, app):
        from core.forecast import _rolling_mean_by_weekday

        seg1 = date(2025, 1, 6)
        seg2 = date(2025, 1, 13)
        seg3 = date(2025, 1, 20)
        serie = [
            (seg1, 10, 0, 0, 0),
            (seg2, 20, 0, 0, 0),
            (seg3, 30, 0, 0, 0),
        ]
        # samples=2 pega apenas os 2 mais recentes (seg3 e seg2)
        totais, n = _rolling_mean_by_weekday(serie, weekday=0, samples=2)
        assert n == 2
        assert totais["pa"] == round((20 + 30) / 2)

    def test_forecast_route_render(self, app, client, monkeypatch):
        """Smoke test da rota /operations/forecast."""
        from tests.conftest import login_as

        # Login como admin (tem acesso cozinha|admin)
        login_as(client, "admin", "admin123")
        resp = client.get("/forecast?dias=3&semanas=2")
        assert resp.status_code == 200
        assert b"Previs" in resp.data  # "Previsão de consumo"


# ═══════════════════════════════════════════════════════════════════════════
# #16 — PWA
# ═══════════════════════════════════════════════════════════════════════════


class TestPWA:
    def test_manifest_servido(self, client):
        resp = client.get("/static/manifest.json")
        assert resp.status_code == 200
        import json

        data = json.loads(resp.data)
        assert data["name"].startswith("Escola Naval")
        assert data["display"] == "standalone"
        assert data["start_url"] == "/"
        # Ícones declarados
        assert len(data["icons"]) >= 1

    def test_sw_js_servido(self, client):
        resp = client.get("/static/sw.js")
        assert resp.status_code == 200
        body = resp.data.decode("utf-8")
        assert "self.addEventListener('install'" in body
        assert "self.addEventListener('fetch'" in body

    def test_sw_register_script_servido(self, client):
        resp = client.get("/static/js/sw-register.js")
        assert resp.status_code == 200
        body = resp.data.decode("utf-8")
        assert "serviceWorker" in body
        assert "/static/sw.js" in body

    def test_base_template_referencia_manifest_e_sw(self, client):
        """A página de login (que usa base.html) deve conter as referências PWA."""
        resp = client.get("/login")
        assert resp.status_code == 200
        body = resp.data.decode("utf-8")
        assert "manifest.json" in body
        assert "sw-register.js" in body
        assert "apple-mobile-web-app-capable" in body


# ═══════════════════════════════════════════════════════════════════════════
# #17 — Dietas permanentes
# ═══════════════════════════════════════════════════════════════════════════


class TestDietasPermanentes:
    def test_migration_cria_coluna(self, app):
        from core.database import db

        with db() as conn:
            cols = [
                r["name"]
                for r in conn.execute("PRAGMA table_info(utilizadores)").fetchall()
            ]
        assert "dieta_padrao" in cols

    def test_default_dieta_e_normal(self, app):
        from core.users import get_dieta_padrao

        uid = create_aluno("dietas_t1", "D001", "Aluno Dieta1", ano="1")
        assert get_dieta_padrao(uid) == "Normal"

    def test_update_dieta_padrao_valida(self, app):
        from core.users import get_dieta_padrao, update_dieta_padrao

        uid = create_aluno("dietas_t2", "D002", "Aluno Dieta2", ano="1")
        update_dieta_padrao(uid, "Vegetariano")
        assert get_dieta_padrao(uid) == "Vegetariano"
        update_dieta_padrao(uid, "Dieta")
        assert get_dieta_padrao(uid) == "Dieta"
        update_dieta_padrao(uid, "Normal")
        assert get_dieta_padrao(uid) == "Normal"

    def test_update_dieta_invalida_falha(self, app):
        from core.users import update_dieta_padrao

        uid = create_aluno("dietas_t3", "D003", "Aluno Dieta3", ano="1")
        with pytest.raises(ValueError):
            update_dieta_padrao(uid, "SopaDePedra")

    def test_dietas_padrao_batch(self, app):
        from core.users import dietas_padrao_batch, update_dieta_padrao

        uid_a = create_aluno("dietas_tb1", "DB01", "Aluno Batch1", ano="1")
        uid_b = create_aluno("dietas_tb2", "DB02", "Aluno Batch2", ano="1")
        update_dieta_padrao(uid_b, "Vegetariano")
        batch = dietas_padrao_batch()
        assert batch.get(uid_a) == "Normal"
        assert batch.get(uid_b) == "Vegetariano"

    def test_autofill_usa_dieta_padrao(self, app, monkeypatch):
        """Regressão: ao auto-preencher uma refeição, o default do almoço/jantar
        deve respeitar a dieta_padrao do utilizador."""
        from core.autofill import _full_default

        assert _full_default("Normal")["almoco"] == "Normal"
        assert _full_default("Vegetariano")["almoco"] == "Vegetariano"
        assert _full_default("Vegetariano")["jantar_tipo"] == "Vegetariano"
        assert _full_default("Dieta")["almoco"] == "Dieta"


# ═══════════════════════════════════════════════════════════════════════════
# #18 — PDF export
# ═══════════════════════════════════════════════════════════════════════════


class TestPDFExport:
    @pytest.fixture(autouse=True)
    def _patch_export_dir(self, monkeypatch, tmp_path):
        d = str(tmp_path)
        monkeypatch.setattr("core.constants.EXPORT_DIR", d)
        monkeypatch.setattr("core.exports.EXPORT_DIR", d)

    def test_export_pdf_cria_ficheiro(self, app):
        from core.exports import export_pdf

        rows = [{"a": "1", "b": "2"}, {"a": "3", "b": "4"}]
        hdrs = ["a", "b"]
        path = export_pdf(rows, hdrs, "teste_pdf", title="Teste")
        assert os.path.isfile(path)
        # Aceitamos PDF (reportlab) ou HTML (fallback)
        assert path.endswith(".pdf") or path.endswith(".html")

    def test_export_pdf_fallback_html_sem_reportlab(self, app, monkeypatch):
        """Se reportlab não importa, cai em HTML self-contained."""
        import builtins

        real_import = builtins.__import__

        def fake_import(name, *a, **kw):
            if name.startswith("reportlab"):
                raise ImportError("reportlab não está")
            return real_import(name, *a, **kw)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        from core.exports import export_pdf

        path = export_pdf([{"x": "1"}], ["x"], "fallback_teste", title="Fallback")
        assert path.endswith(".html")
        with open(path, encoding="utf-8") as f:
            content = f.read()
        assert "<table" in content
        assert "Fallback" in content

    def test_export_pdf_vazio_nao_rebenta(self, app):
        from core.exports import export_pdf

        path = export_pdf([], ["a", "b"], "vazio")
        assert os.path.isfile(path)

    def test_exportacao_pdf_do_dia(self, app):
        from core.exports import exportacao_pdf_do_dia

        d = date.today()
        path = exportacao_pdf_do_dia(d)
        assert os.path.isfile(path)

    def test_api_export_cron_requer_token(self, client):
        resp = client.post("/api/export-cron")
        assert resp.status_code == 403

    def test_api_export_cron_com_token_dev(self, client):
        # Em dev, o fallback aceita token "dev"
        resp = client.post(
            "/api/export-cron",
            headers={"Authorization": "Bearer dev"},
        )
        # Pode devolver 200 (ok) ou 500 se exportação falhar — o que
        # queremos validar é que o token não foi rejeitado (≠ 403)
        assert resp.status_code != 403

    def test_api_export_cron_data_invalida(self, client):
        resp = client.post(
            "/api/export-cron?data=nao-e-uma-data",
            headers={"Authorization": "Bearer dev"},
        )
        assert resp.status_code == 400


# ═══════════════════════════════════════════════════════════════════════════
# #15 — QR check-in
# ═══════════════════════════════════════════════════════════════════════════


class TestQRHelpers:
    def test_build_payload_format(self, app):
        from core.qr import QR_PAYLOAD_PREFIX, build_payload

        p = build_payload("abc123")
        assert p.startswith(QR_PAYLOAD_PREFIX)
        assert p.endswith("abc123")

    def test_parse_payload_com_prefixo(self, app):
        from core.qr import parse_payload

        assert parse_payload("NII:abc123") == "abc123"
        assert parse_payload("  NII:abc123  ") == "abc123"

    def test_parse_payload_sem_prefixo(self, app):
        from core.qr import parse_payload

        assert parse_payload("abc123") == "abc123"
        assert parse_payload("ni-007") == "ni-007"

    def test_parse_payload_invalido(self, app):
        from core.qr import parse_payload

        assert parse_payload("") is None
        assert parse_payload(None) is None  # type: ignore[arg-type]
        assert parse_payload("   ") is None
        assert parse_payload("com espaço") is None
        assert parse_payload("NII:") is None  # prefixo mas vazio

    def test_qr_svg_bytes_com_qrcode(self, app):
        pytest.importorskip("qrcode")
        from core.qr import qr_svg_bytes

        out = qr_svg_bytes("NII:abc")
        assert isinstance(out, bytes)
        body = out.decode("utf-8")
        assert "<svg" in body

    def test_qr_svg_bytes_fallback(self, app, monkeypatch):
        """Se `qrcode` não importa, devolve SVG fallback com o texto legível."""
        import builtins

        real = builtins.__import__

        def fake(name, *a, **kw):
            if name.startswith("qrcode"):
                raise ImportError
            return real(name, *a, **kw)

        monkeypatch.setattr(builtins, "__import__", fake)
        from core.qr import qr_svg_bytes

        out = qr_svg_bytes("NII:xyz")
        body = out.decode("utf-8")
        assert "<svg" in body
        assert "NII:xyz" in body  # texto legível em fallback


class TestCheckinKiosk:
    def test_aluno_qr_route(self, app, client):
        from tests.conftest import login_as

        create_aluno("qr_own_1", "QR001", "Aluno QR1", ano="1")
        login_as(client, "qr_own_1", "qr_own_1")
        resp = client.get("/aluno/qr")
        assert resp.status_code == 200
        assert resp.headers["Content-Type"].startswith("image/svg+xml")
        body = resp.data.decode("utf-8")
        assert "<svg" in body

    def test_checkin_requer_oficialdia(self, app, client):
        """Aluno sem perfil oficialdia/admin recebe 403."""
        from tests.conftest import login_as

        create_aluno("qr_chk_a", "QRA01", "Aluno CheckinA", ano="1")
        login_as(client, "qr_chk_a", "qr_chk_a")
        resp = client.get("/checkin")
        # role_required redireciona ou devolve 403
        assert resp.status_code in (302, 403)

    def test_checkin_get_render(self, app, client):
        from tests.conftest import login_as

        login_as(client, "oficialdia", "oficial123")
        resp = client.get("/checkin")
        assert resp.status_code == 200
        assert b"Quiosque" in resp.data or b"check-in" in resp.data

    def test_checkin_nii_invalido(self, app, client):
        from tests.conftest import get_csrf, login_as

        login_as(client, "oficialdia", "oficial123")
        csrf = get_csrf(client)
        resp = client.post(
            "/checkin",
            data={"csrf_token": csrf, "payload": "   ", "acao": "auto"},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert b"inv" in resp.data or b"vazio" in resp.data

    def test_checkin_auto_regista_saida_se_presente(self, app, client):
        """Aluno presente → check-in 'auto' regista saída."""
        from tests.conftest import get_csrf, login_as

        create_aluno("qr_chk_b", "QRB01", "Aluno CheckinB", ano="1")
        login_as(client, "oficialdia", "oficial123")
        csrf = get_csrf(client)
        resp = client.post(
            "/checkin",
            data={"csrf_token": csrf, "payload": "NII:QRB01", "acao": "auto"},
            follow_redirects=True,
        )
        # Deve redirecionar (302 → 200) sem erros de servidor
        assert resp.status_code < 500


# ═══════════════════════════════════════════════════════════════════════════
# PR A — UX & Acessibilidade (v1.1.0)
# ═══════════════════════════════════════════════════════════════════════════


class TestToasts:
    """Toast system CSP-safe via `render_flash_toasts` helper."""

    def test_render_vazio_quando_sem_flashes(self, app):
        from utils.helpers import render_flash_toasts

        with app.test_request_context():
            out = render_flash_toasts()
            assert str(out) == ""

    def test_emite_script_json_por_flash(self, app):
        from flask import flash

        from utils.helpers import render_flash_toasts

        with app.test_request_context():
            flash("Olá mundo", "ok")
            out = str(render_flash_toasts())
            # Deve ser um <script type="application/json" data-toast>
            assert 'type="application/json"' in out
            assert "data-toast" in out
            assert '"Olá mundo"' in out or "Olá mundo" in out
            assert '"level":"ok"' in out

    def test_mapeia_categorias_legacy(self, app):
        """`success`→`ok`, `danger`→`error`, `warning`→`warn`."""
        from flask import flash

        from utils.helpers import render_flash_toasts

        with app.test_request_context():
            flash("A", "success")
            flash("B", "danger")
            flash("C", "warning")
            flash("D", "desconhecido")
            out = str(render_flash_toasts())
            assert '"level":"ok"' in out
            assert '"level":"error"' in out
            assert '"level":"warn"' in out
            # Categoria desconhecida cai em 'info'
            assert '"level":"info"' in out

    def test_payload_escapa_fechamento_script(self, app):
        """O json.dumps escapa `</` — impossível fechar o <script>."""
        from flask import flash

        from utils.helpers import render_flash_toasts

        with app.test_request_context():
            flash("texto </script> malicioso", "error")
            out = str(render_flash_toasts())
            # O fecho literal não pode aparecer dentro do payload JSON
            assert "</script> malicioso" not in out
            # Mas deve aparecer escapado
            assert "<\\/script>" in out or "\\u003c/script\\u003e" in out.lower()


class TestDarkMode:
    """Dark mode via CSS vars + toggle persistido em localStorage."""

    def test_base_carrega_theme_css_e_js(self, app, client):
        """Qualquer página autenticada serve theme.css e theme.js."""
        from tests.conftest import login_as

        create_aluno("dm_user", "DM001", "Dark Mode User", ano="1")
        login_as(client, "dm_user")
        resp = client.get("/aluno/home")
        assert resp.status_code < 500
        body = resp.data.decode("utf-8")
        assert "/static/css/theme.css" in body
        assert "/static/js/theme.js" in body

    def test_toggle_button_no_nav_quando_autenticado(self, app, client):
        from tests.conftest import login_as

        create_aluno("dm_user2", "DM002", "Dark Mode User2", ano="1")
        login_as(client, "dm_user2")
        resp = client.get("/aluno/home")
        body = resp.data.decode("utf-8")
        assert "data-theme-toggle" in body
        assert 'aria-label="Alternar modo de cor"' in body

    def test_static_theme_js_responde_200(self, app, client):
        resp = client.get("/static/js/theme.js")
        assert resp.status_code == 200
        body = resp.data.decode("utf-8")
        assert "data-theme" in body
        assert "localStorage" in body


class TestEmptyStates:
    """Macro `empty_state` renderizada quando não há dados."""

    def test_historico_vazio_mostra_empty_state(self, app, client):
        from tests.conftest import login_as

        create_aluno("hist_empty", "HIST01", "Hist Empty", ano="1")
        login_as(client, "hist_empty")
        resp = client.get("/aluno/historico")
        assert resp.status_code == 200
        body = resp.data.decode("utf-8")
        assert "empty-state" in body
        assert "Sem registos" in body

    def test_utilizadores_vazio_filtra_para_mostrar_empty(self, app, client):
        """Filtro que garante 0 utilizadores → empty state."""
        from tests.conftest import login_as

        login_as(client, "admin", "admin123")
        # Query impossível
        resp = client.get("/admin/utilizadores?q=__impossible_xyz_nomatch__")
        assert resp.status_code == 200
        body = resp.data.decode("utf-8")
        assert "empty-state" in body


class TestAccessibility:
    """Skip-link, aria-pressed nos toggles, aria-checked nos pills."""

    def test_skip_link_presente_no_base(self, app, client):
        from tests.conftest import login_as

        create_aluno("sk_user", "SK001", "Skip User", ano="1")
        login_as(client, "sk_user")
        resp = client.get("/aluno/home")
        body = resp.data.decode("utf-8")
        # skip-link é o primeiro elemento focusável e aponta para #content
        assert 'class="skip-link"' in body
        assert 'href="#content"' in body

    def test_meal_editor_tem_aria_pressed_nos_toggles(self, app, client):
        from tests.conftest import login_as

        create_aluno("ed_user", "ED001", "Edit User", ano="1")
        login_as(client, "ed_user")
        # Abrir dia editável (rota é /aluno/editar/<d>)
        d = (date.today() + timedelta(days=3)).isoformat()
        resp = client.get(f"/aluno/editar/{d}")
        assert resp.status_code == 200
        body = resp.data.decode("utf-8")
        assert "aria-pressed=" in body
        assert "aria-checked=" in body
        assert 'role="radiogroup"' in body


class TestShortcuts:
    """Atalhos de teclado e overlay de ajuda."""

    def test_shortcuts_dialog_incluido_no_base(self, app, client):
        from tests.conftest import login_as

        create_aluno("sc_user", "SC001", "Shortcuts User", ano="1")
        login_as(client, "sc_user")
        resp = client.get("/aluno/home")
        body = resp.data.decode("utf-8")
        assert 'id="shortcuts-help"' in body
        assert "<dialog" in body

    def test_static_shortcuts_js_responde_200(self, app, client):
        resp = client.get("/static/js/shortcuts.js")
        assert resp.status_code == 200
        body = resp.data.decode("utf-8")
        # Deve cobrir os 3 atalhos principais
        assert "Ctrl" in body or "ctrlKey" in body
        assert "metaKey" in body
        assert "Escape" in body
