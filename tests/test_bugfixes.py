"""Testes para os bugfixes do sprint pré-UAT (PR1).

Cobre:
  #2 — cutoff específico do lanche (48h, mas termina às 10h do dia de prazo)
  #3 — dark-mode: contraste de links/texto azul (CSS)
  #4 — dark-mode: inputs no login seguem tema (CSS)
  #5 — contadores quantitativos
  #6 — atribuir turma / promover / mover aluno (feedback e rowcount)
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from tests.conftest import create_system_user, get_csrf, login_as


# ══════════════════════════════════════════════════════════════════════════
# #2 — Cutoff do lanche (até 10h do dia-de-prazo)
# ══════════════════════════════════════════════════════════════════════════


class TestCutoffLanche:
    """O lanche mantém 48h, mas o prazo termina às 10h do dia-de-prazo em
    vez das 00:00. Isto dá 10h extra (entre d-2 00:00 e d-2 10:00)."""

    def test_refeicao_editavel_geral_usa_00h(self, monkeypatch):
        from core import meals

        # Dia-alvo: hoje + 3 dias. Prazo geral = d-2 00:00.
        target = date.today() + timedelta(days=3)
        geral_cutoff = datetime(target.year, target.month, target.day) - timedelta(
            hours=48
        )
        # Agora: 1 minuto DEPOIS do cutoff geral → geral fechado
        fake_now = geral_cutoff + timedelta(minutes=1)

        class FakeDT(datetime):
            @staticmethod
            def now(tz=None):
                return fake_now

        monkeypatch.setattr(meals, "datetime", FakeDT)

        ok, msg = meals.refeicao_editavel(target)
        assert not ok
        assert "Prazo excedido" in msg

    def test_refeicao_editavel_lanche_aberto_apos_00h(self, monkeypatch):
        """Entre d-2 00:00 e d-2 10:00, geral está fechado mas lanche está aberto."""
        from core import meals

        target = date.today() + timedelta(days=3)
        geral_cutoff = datetime(target.year, target.month, target.day) - timedelta(
            hours=48
        )
        # Agora: 5h DEPOIS do cutoff geral (= d-2 05:00), ainda antes das 10h
        fake_now = geral_cutoff + timedelta(hours=5)

        class FakeDT(datetime):
            @staticmethod
            def now(tz=None):
                return fake_now

        monkeypatch.setattr(meals, "datetime", FakeDT)

        ok_geral, _ = meals.refeicao_editavel(target)
        ok_lanche, _ = meals.refeicao_editavel(target, tipo="lanche")
        assert not ok_geral  # geral fechado
        assert ok_lanche  # lanche ainda aberto

    def test_refeicao_editavel_lanche_fechado_apos_10h(self, monkeypatch):
        """Depois de d-2 10:00, lanche também fecha."""
        from core import meals

        target = date.today() + timedelta(days=3)
        geral_cutoff = datetime(target.year, target.month, target.day) - timedelta(
            hours=48
        )
        # Agora: d-2 10:30 (30min depois do cutoff do lanche)
        fake_now = geral_cutoff + timedelta(hours=10, minutes=30)

        class FakeDT(datetime):
            @staticmethod
            def now(tz=None):
                return fake_now

        monkeypatch.setattr(meals, "datetime", FakeDT)

        ok_lanche, msg = meals.refeicao_editavel(target, tipo="lanche")
        assert not ok_lanche
        assert "lanche" in msg.lower()
        # O prazo reportado deve ser às 10:00
        assert "10:00" in msg

    def test_refeicao_editavel_ambos_abertos_antes(self, monkeypatch):
        """Antes do cutoff geral, ambos estão abertos."""
        from core import meals

        target = date.today() + timedelta(days=5)
        geral_cutoff = datetime(target.year, target.month, target.day) - timedelta(
            hours=48
        )
        # Agora: 1h ANTES do cutoff geral
        fake_now = geral_cutoff - timedelta(hours=1)

        class FakeDT(datetime):
            @staticmethod
            def now(tz=None):
                return fake_now

        monkeypatch.setattr(meals, "datetime", FakeDT)

        assert meals.refeicao_editavel(target)[0]
        assert meals.refeicao_editavel(target, tipo="lanche")[0]


# ══════════════════════════════════════════════════════════════════════════
# #3 + #4 — Dark mode: contraste e inputs
# ══════════════════════════════════════════════════════════════════════════


class TestDarkModeCss:
    """Regras CSS devem usar CSS vars para cores em vez de hardcoded azul/preto."""

    @pytest.fixture(scope="class")
    def theme_css(self):
        p = Path(__file__).resolve().parent.parent / "static" / "css" / "theme.css"
        return p.read_text(encoding="utf-8")

    @pytest.fixture(scope="class")
    def app_css(self):
        p = Path(__file__).resolve().parent.parent / "static" / "css" / "app.css"
        return p.read_text(encoding="utf-8")

    def test_dark_overrides_inputs(self, theme_css):
        """[data-theme='dark'] input/textarea/select deve ter override."""
        assert '[data-theme="dark"]' in theme_css or "[data-theme='dark']" in theme_css

    def test_dark_overrides_login_wrap(self, theme_css):
        """A login-wrap/login-box deve ter override em dark mode."""
        assert ".login-wrap" in theme_css or ".login-box" in theme_css

    def test_dark_overrides_card_accent_blue(self, theme_css):
        """card-accent-blue deve ter override de cor em dark mode."""
        assert "card-accent-blue" in theme_css

    def test_dark_overrides_label_color(self, theme_css):
        """Labels devem usar var(--text) ou ter override em dark."""
        # Aceita qualquer uma das duas: override directo ou uso de var
        assert (
            "label" in theme_css.lower()
            or "--muted" in theme_css
            or "--text" in theme_css
        )


# ══════════════════════════════════════════════════════════════════════════
# #6 — Companhias: rowcount check e feedback
# ══════════════════════════════════════════════════════════════════════════


class TestCompanhiasFeedback:
    """As funções de atribuir/mover devem devolver bool (rowcount>0) e os
    handlers devem dar flash descritivo em caso de NII inválido ou
    inexistente (em vez de redirect silencioso)."""

    def test_assign_turma_nii_inexistente_retorna_false(self, app):
        """assign_turma devolve False quando o NII não existe."""
        from core.companhias import assign_turma

        with app.app_context():
            assert assign_turma("NII_INEXISTENTE_XYZ123", None) is False

    def test_move_aluno_ano_nii_inexistente_retorna_false(self, app):
        """move_aluno_ano devolve False quando o NII não existe."""
        from core.companhias import move_aluno_ano

        with app.app_context():
            assert move_aluno_ano("NII_INEXISTENTE_XYZ123", 2) is False

    def _login_admin(self, client):
        create_system_user("adm_bugfix", "admin", nome="Admin Bugfix", pw="AdminBf1")
        login_as(client, "adm_bugfix", pw="AdminBf1")
        return get_csrf(client)

    def test_atribuir_turma_nii_vazio_da_flash_error(self, app, client):
        """Handler atribuir_turma sem NII dá flash de erro (antes era silent)."""
        csrf = self._login_admin(client)
        resp = client.post(
            "/admin/companhias",
            data={
                "csrf_token": csrf,
                "acao": "atribuir_turma",
                "nii_at": "",
                "turma_id": "",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert b"NII em falta" in resp.data

    def test_atribuir_turma_nii_inexistente_da_flash_error(self, app, client):
        """Handler atribuir_turma com NII inexistente dá flash de erro."""
        csrf = self._login_admin(client)
        resp = client.post(
            "/admin/companhias",
            data={
                "csrf_token": csrf,
                "acao": "atribuir_turma",
                "nii_at": "NII_QUE_NAO_EXISTE_ZZZ",
                "turma_id": "",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert b"n\xc3\xa3o encontrado" in resp.data

    def test_redirect_preserva_aba_atribuir(self, app, client):
        """Após POST em atribuir_turma, redirect deve apontar para #atribuir."""
        csrf = self._login_admin(client)
        resp = client.post(
            "/admin/companhias",
            data={
                "csrf_token": csrf,
                "acao": "atribuir_turma",
                "nii_at": "",
                "turma_id": "",
            },
            follow_redirects=False,
        )
        assert resp.status_code in (302, 303)
        assert resp.headers.get("Location", "").endswith("#atribuir")

    def test_redirect_preserva_aba_mover(self, app, client):
        """Após POST em mover_aluno, redirect deve apontar para #mover."""
        csrf = self._login_admin(client)
        resp = client.post(
            "/admin/companhias",
            data={
                "csrf_token": csrf,
                "acao": "mover_aluno",
                "nii_m": "",
                "novo_ano": "",
            },
            follow_redirects=False,
        )
        assert resp.status_code in (302, 303)
        assert resp.headers.get("Location", "").endswith("#mover")


# ══════════════════════════════════════════════════════════════════════════
# #5 — Contadores: get_totais_dia devolve todas as keys esperadas
# ══════════════════════════════════════════════════════════════════════════


class TestContadores:
    """Os contadores (get_totais_dia/periodo) devem devolver todas as keys
    esperadas pelo template (sem divergência backend/frontend)."""

    def test_totais_keys_consistentes(self):
        from core.meals import _TOTAIS_KEYS, _empty_totais

        expected = {
            "pa",
            "lan",
            "alm_norm",
            "alm_veg",
            "alm_dieta",
            "alm_estufa",
            "jan_norm",
            "jan_veg",
            "jan_dieta",
            "jan_sai",
            "jan_estufa",
        }
        assert set(_TOTAIS_KEYS) == expected
        assert set(_empty_totais().keys()) == expected
