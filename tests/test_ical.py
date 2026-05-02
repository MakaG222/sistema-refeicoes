"""tests/test_ical.py — utils/ical.py + /aluno/refeicoes.ics route.

Cobre:
  - Geração de VCALENDAR vazio quando aluno não tem refeições marcadas
  - Geração de VEVENTs por tipo de refeição (PA, lanche, almoço, jantar)
  - Escape RFC 5545 de caracteres especiais (vírgula, ponto e vírgula, \\)
  - Variantes especiais (estufa, sai unidade)
  - Route /aluno/refeicoes.ics — auth, mimetype, content-disposition
  - Clamping do parâmetro `days` (1-90)
"""

from __future__ import annotations

from utils.ical import (
    _ics_escape,
    _meal_summary,
    build_meals_ics,
)


# ══════════════════════════════════════════════════════════════════════════
# Unit tests — utils/ical.py
# ══════════════════════════════════════════════════════════════════════════


class TestIcsEscape:
    def test_escapa_comma(self):
        assert _ics_escape("a,b") == "a\\,b"

    def test_escapa_semicolon(self):
        assert _ics_escape("a;b") == "a\\;b"

    def test_escapa_backslash(self):
        assert _ics_escape("a\\b") == "a\\\\b"

    def test_escapa_newline(self):
        assert _ics_escape("a\nb") == "a\\nb"

    def test_remove_carriage_return(self):
        # \r sozinho não é parte do escape RFC 5545 — removemos para evitar
        # corromper o line ending CRLF que adicionamos a nível de linha.
        assert _ics_escape("a\rb") == "ab"

    def test_combina_multiplos_escapes(self):
        assert _ics_escape("a, b; c\\d") == "a\\, b\\; c\\\\d"


class TestMealSummary:
    def test_pequeno_almoco_marcado(self):
        assert (
            _meal_summary("pequeno_almoco", {"pequeno_almoco": 1}) == "Pequeno-Almoço"
        )

    def test_pequeno_almoco_nao_marcado(self):
        assert _meal_summary("pequeno_almoco", {"pequeno_almoco": 0}) is None

    def test_lanche_marcado(self):
        assert _meal_summary("lanche", {"lanche": 1}) == "Lanche"

    def test_almoco_normal(self):
        out = _meal_summary("almoco", {"almoco": "Normal", "almoco_estufa": 0})
        assert out == "Almoço Normal"

    def test_almoco_vegetariano_estufa(self):
        out = _meal_summary("almoco", {"almoco": "Vegetariano", "almoco_estufa": 1})
        assert out == "Almoço Vegetariano ♨"

    def test_jantar_normal(self):
        out = _meal_summary(
            "jantar",
            {"jantar_tipo": "Normal", "jantar_estufa": 0, "jantar_sai_unidade": 0},
        )
        assert out == "Jantar Normal"

    def test_jantar_sai_unidade_substitui_outras_marcas(self):
        # Sai unidade tem prioridade — não interessa a variante/estufa
        out = _meal_summary(
            "jantar",
            {"jantar_tipo": "Dieta", "jantar_estufa": 1, "jantar_sai_unidade": 1},
        )
        assert "sai da unidade" in out

    def test_jantar_sem_tipo(self):
        assert _meal_summary("jantar", {"jantar_tipo": None}) is None


class TestBuildMealsIcs:
    """Geração end-to-end do .ics."""

    def test_calendario_vazio_quando_sem_refeicoes(self):
        out = build_meals_ics(uid_aluno=1, nome="Test", refeicoes_por_data={})
        assert "BEGIN:VCALENDAR" in out
        assert "END:VCALENDAR" in out
        assert "BEGIN:VEVENT" not in out  # sem events
        # Header obrigatório RFC 5545
        assert "VERSION:2.0" in out
        assert "PRODID:" in out

    def test_uses_crlf_line_endings(self):
        """RFC 5545 §3.1 exige CRLF (\\r\\n) entre linhas."""
        out = build_meals_ics(uid_aluno=1, nome="Test", refeicoes_por_data={})
        assert "\r\n" in out
        # Não deve ter \n sem \r antes (excepto em escapes \\n dentro de TEXT)
        # Forma simplificada: contar \r\n e \n: devem bater
        assert out.count("\n") == out.count("\r\n")

    def test_evento_almoco_simples(self):
        refeicoes = {
            "2026-04-20": {
                "almoco": "Normal",
                "almoco_estufa": 0,
                "pequeno_almoco": 0,
                "lanche": 0,
                "jantar_tipo": None,
            }
        }
        out = build_meals_ics(uid_aluno=42, nome="Maria", refeicoes_por_data=refeicoes)

        assert "BEGIN:VEVENT" in out
        assert "END:VEVENT" in out
        assert "SUMMARY:Almoço Normal" in out
        # UID estável, único por (aluno, data, tipo)
        assert "UID:42-2026-04-20-almoco@refeicoes.escolanaval" in out
        # DTSTART/DTEND no formato local floating
        assert "DTSTART:20260420T120000" in out
        assert "DTEND:20260420T140000" in out

    def test_multiplas_refeicoes_no_mesmo_dia(self):
        """Um dia com PA + lanche + almoço + jantar → 4 VEVENTs."""
        refeicoes = {
            "2026-04-20": {
                "pequeno_almoco": 1,
                "lanche": 1,
                "almoco": "Vegetariano",
                "almoco_estufa": 1,
                "jantar_tipo": "Dieta",
                "jantar_estufa": 0,
                "jantar_sai_unidade": 0,
            }
        }
        out = build_meals_ics(uid_aluno=1, nome="Test", refeicoes_por_data=refeicoes)
        assert out.count("BEGIN:VEVENT") == 4
        assert "Pequeno-Almoço" in out
        assert "Lanche" in out
        assert "Almoço Vegetariano ♨" in out
        assert "Jantar Dieta" in out

    def test_uid_estavel_para_re_import(self):
        """O mesmo (aluno, data, tipo) gera sempre o mesmo UID — re-import
        actualiza em vez de duplicar."""
        ref = {"2026-04-20": {"almoco": "Normal", "almoco_estufa": 0}}
        out1 = build_meals_ics(uid_aluno=7, nome="A", refeicoes_por_data=ref)
        out2 = build_meals_ics(uid_aluno=7, nome="A", refeicoes_por_data=ref)
        # Extrair UID
        import re

        uid1 = re.search(r"UID:(.+)", out1).group(1).strip()
        uid2 = re.search(r"UID:(.+)", out2).group(1).strip()
        assert uid1 == uid2

    def test_data_invalida_e_ignorada(self):
        """Datas mal formadas no input não fazem crash."""
        refeicoes = {
            "data-totalmente-inválida": {"almoco": "Normal"},
            "2026-04-20": {"almoco": "Normal", "almoco_estufa": 0},
        }
        out = build_meals_ics(uid_aluno=1, nome="X", refeicoes_por_data=refeicoes)
        # Só o evento da data válida aparece
        assert out.count("BEGIN:VEVENT") == 1
        assert "20260420" in out

    def test_nome_com_caracteres_especiais_escapado(self):
        """Nome com vírgulas/semicolons escapado em X-WR-CALNAME."""
        out = build_meals_ics(
            uid_aluno=1,
            nome="Silva, João; Filho",
            refeicoes_por_data={},
        )
        # Escape feito: vírgula e ponto-vírgula precedidos de \
        assert "Silva\\, João\\; Filho" in out


# ══════════════════════════════════════════════════════════════════════════
# Integration test — /aluno/refeicoes.ics route
# ══════════════════════════════════════════════════════════════════════════


class TestAlunoRefeicoesIcsRoute:
    """Auth, mimetype, content-disposition + clamping de `days`."""

    def test_ics_requires_auth(self, client):
        """Sem login → redirect para /login (302)."""
        resp = client.get("/aluno/refeicoes.ics")
        assert resp.status_code in (302, 401)

    def test_ics_returns_text_calendar_mimetype(self, client):
        from conftest import create_aluno, login_as

        create_aluno("ics01", "ICS001", "Aluno ICS")
        login_as(client, "ics01")

        resp = client.get("/aluno/refeicoes.ics")
        assert resp.status_code == 200
        # mimetype deve ser text/calendar para calendar apps reconhecerem
        assert resp.headers["Content-Type"].startswith("text/calendar")
        # Content-Disposition: attachment com extensão .ics
        cd = resp.headers.get("Content-Disposition", "")
        assert "attachment" in cd
        assert ".ics" in cd
        # Body é VCALENDAR
        body = resp.data.decode("utf-8")
        assert body.startswith("BEGIN:VCALENDAR")
        assert body.rstrip().endswith("END:VCALENDAR")

    def test_ics_no_events_for_aluno_sem_marcacoes(self, client):
        """Aluno sem refeições marcadas → calendar vazio (sem VEVENTs)."""
        from conftest import create_aluno, login_as

        create_aluno("ics02", "ICS002", "Aluno Vazio")
        login_as(client, "ics02")

        resp = client.get("/aluno/refeicoes.ics")
        body = resp.data.decode("utf-8")
        assert "BEGIN:VEVENT" not in body

    def test_ics_days_param_clamped(self, client):
        """`days` fora de [1, 90] é clamped (não levanta nem aceita extremos)."""
        from conftest import create_aluno, login_as

        create_aluno("ics03", "ICS003", "Aluno Clamp")
        login_as(client, "ics03")

        # Acima do máx → 200 (clamped a 90)
        assert client.get("/aluno/refeicoes.ics?days=99999").status_code == 200
        # Abaixo do mín → 200 (clamped a 1)
        assert client.get("/aluno/refeicoes.ics?days=-5").status_code == 200
        # Lixo no param → fallback para default (30)
        assert client.get("/aluno/refeicoes.ics?days=abc").status_code == 200

    def test_ics_inclui_marcacoes_do_aluno(self, client):
        """Inserir refeição → aparece como VEVENT no .ics."""
        from datetime import date, timedelta

        from conftest import create_aluno, login_as
        from core.database import db

        uid = create_aluno("ics04", "ICS004", "Aluno Com Marcacao")
        # Marcar almoço normal para amanhã (dentro da janela default 30d)
        amanha = (date.today() + timedelta(days=1)).isoformat()
        with db() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO refeicoes
                   (utilizador_id, data, pequeno_almoco, lanche, almoco,
                    almoco_estufa, jantar_tipo, jantar_estufa, jantar_sai_unidade)
                   VALUES (?,?,0,0,'Normal',0,NULL,0,0)""",
                (uid, amanha),
            )
            conn.commit()

        login_as(client, "ics04")
        resp = client.get("/aluno/refeicoes.ics")
        body = resp.data.decode("utf-8")
        assert "BEGIN:VEVENT" in body
        assert "Almoço Normal" in body
        # UID estável referencia o uid_aluno + data
        assert f"UID:{uid}-{amanha}-almoco@" in body
