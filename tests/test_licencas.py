"""
tests/test_licencas.py — Testes de licenças de saída
=====================================================
"""

from datetime import date, timedelta

import sistema_refeicoes_v8_4 as sr

from tests.conftest import create_aluno, create_system_user, get_csrf, login_as


def _future_date(days=10):
    """Retorna uma data futura editável."""
    return date.today() + timedelta(days=days)


def _next_weekday(days_ahead=10):
    """Retorna a próxima quarta-feira futura (dia útil permitido para 1º ano)."""
    d = date.today() + timedelta(days=days_ahead)
    # Avançar para quarta-feira (weekday=2)
    while d.weekday() != 2:
        d += timedelta(days=1)
    return d


def _next_friday(days_ahead=10):
    """Retorna a próxima sexta-feira futura (fim de semana - sem limite)."""
    d = date.today() + timedelta(days=days_ahead)
    while d.weekday() != 4:
        d += timedelta(days=1)
    return d


# ── Testes directos à BD ──────────────────────────────────────────────────────


class TestLicencaDB:
    def test_create_licenca_antes_jantar(self, app):
        """Criar licença antes_jantar na BD."""
        uid = create_aluno("T_LIC_01", "801", "Licenca Teste A", "3")
        d = _next_weekday(20)

        with sr.db() as conn:
            conn.execute(
                "INSERT INTO licencas (utilizador_id, data, tipo) VALUES (?,?,?)",
                (uid, d.isoformat(), "antes_jantar"),
            )
            conn.commit()
            row = conn.execute(
                "SELECT tipo FROM licencas WHERE utilizador_id=? AND data=?",
                (uid, d.isoformat()),
            ).fetchone()
        assert row is not None
        assert row["tipo"] == "antes_jantar"

    def test_create_licenca_apos_jantar(self, app):
        """Criar licença apos_jantar na BD."""
        uid = create_aluno("T_LIC_02", "802", "Licenca Teste B", "3")
        d = _next_weekday(21)

        with sr.db() as conn:
            conn.execute(
                "INSERT INTO licencas (utilizador_id, data, tipo) VALUES (?,?,?)",
                (uid, d.isoformat(), "apos_jantar"),
            )
            conn.commit()
            row = conn.execute(
                "SELECT tipo FROM licencas WHERE utilizador_id=? AND data=?",
                (uid, d.isoformat()),
            ).fetchone()
        assert row is not None
        assert row["tipo"] == "apos_jantar"

    def test_licenca_unique_per_day(self, app):
        """Apenas 1 licença por aluno por dia (UNIQUE constraint)."""
        import sqlite3

        uid = create_aluno("T_LIC_03", "803", "Licenca Teste C", "3")
        d = _next_weekday(22)

        with sr.db() as conn:
            conn.execute(
                "INSERT INTO licencas (utilizador_id, data, tipo) VALUES (?,?,?)",
                (uid, d.isoformat(), "antes_jantar"),
            )
            conn.commit()
            # Tentar inserir segunda licença — deve falhar com UNIQUE
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO licencas (utilizador_id, data, tipo) VALUES (?,?,?)",
                    (uid, d.isoformat(), "apos_jantar"),
                )

    def test_licenca_invalid_tipo_rejected(self, app):
        """Tipo inválido de licença é rejeitado pelo CHECK constraint."""
        import sqlite3

        uid = create_aluno("T_LIC_04", "804", "Licenca Teste D", "3")
        d = _next_weekday(23)

        with sr.db() as conn:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO licencas (utilizador_id, data, tipo) VALUES (?,?,?)",
                    (uid, d.isoformat(), "tipo_invalido"),
                )


import pytest


# ── Testes das regras de licença ──────────────────────────────────────────────


class TestRegraLicenca:
    def test_rules_1st_year(self, app):
        """1º ano: 1 dia útil (quarta), weekends permitidos."""
        import app as app_module

        regras = app_module._regras_licenca(1, "100")
        assert regras["max_dias_uteis"] == 1

    def test_rules_2nd_year(self, app):
        """2º ano: 2 dias úteis."""
        import app as app_module

        regras = app_module._regras_licenca(2, "200")
        assert regras["max_dias_uteis"] == 2

    def test_rules_3rd_year(self, app):
        """3º ano: 3 dias úteis."""
        import app as app_module

        regras = app_module._regras_licenca(3, "300")
        assert regras["max_dias_uteis"] == 3

    def test_rules_4th_year(self, app):
        """4º ano: todos os dias."""
        import app as app_module

        regras = app_module._regras_licenca(4, "400")
        assert regras["max_dias_uteis"] == 4

    def test_rules_ni_prefix_7(self, app):
        """NI começando com '7' tem acesso total."""
        import app as app_module

        regras = app_module._regras_licenca(1, "700")
        assert regras["max_dias_uteis"] == 4

    def test_detained_cannot_mark_licence(self, app):
        """Aluno detido não pode marcar licença."""
        import app as app_module

        uid = create_aluno("T_LIC_DET", "810", "Detido Licenca", "3")
        d = _next_weekday(24)

        # Criar detenção
        with sr.db() as conn:
            conn.execute(
                "INSERT INTO detencoes (utilizador_id, detido_de, detido_ate, motivo, criado_por) VALUES (?,?,?,?,?)",
                (uid, d.isoformat(), d.isoformat(), "Teste", "teste"),
            )
            conn.commit()

        pode, motivo = app_module._pode_marcar_licenca(uid, d, 3, "810")
        assert pode is False
        assert motivo  # Deve ter mensagem explicativa


# ── Testes via HTTP (oficial de dia — entradas/saídas) ────────────────────────


class TestLicencasEntradaSaida:
    def test_oficial_registar_saida(self, app, client):
        """Oficial de dia pode registar saída num aluno com licença."""
        uid = create_aluno("T_LIC_ES1", "820", "ES Teste Saida", "2")
        d = date.today()
        create_system_user("od_lic_test", "oficialdia")

        # Criar licença para hoje
        with sr.db() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO licencas (utilizador_id, data, tipo) VALUES (?,?,?)",
                (uid, d.isoformat(), "antes_jantar"),
            )
            conn.commit()
            lic = conn.execute(
                "SELECT id FROM licencas WHERE utilizador_id=? AND data=?",
                (uid, d.isoformat()),
            ).fetchone()

        login_as(client, "od_lic_test", "od_lic_test123")
        token = get_csrf(client)

        resp = client.post(
            f"/oficialdia/licencas-es?d={d.isoformat()}",
            data={
                "csrf_token": token,
                "acao": "saida",
                "lic_id": str(lic["id"]),
            },
            follow_redirects=False,
        )
        assert resp.status_code == 302

        with sr.db() as conn:
            row = conn.execute(
                "SELECT hora_saida FROM licencas WHERE id=?", (lic["id"],)
            ).fetchone()
        assert row["hora_saida"] is not None

    def test_oficial_registar_entrada(self, app, client):
        """Oficial de dia pode registar entrada num aluno com licença."""
        uid = create_aluno("T_LIC_ES2", "821", "ES Teste Entrada", "2")
        d = date.today()
        create_system_user("od_lic_test2", "oficialdia")

        # Criar licença com saída já registada
        with sr.db() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO licencas (utilizador_id, data, tipo, hora_saida) VALUES (?,?,?,?)",
                (uid, d.isoformat(), "apos_jantar", "14:00"),
            )
            conn.commit()
            lic = conn.execute(
                "SELECT id FROM licencas WHERE utilizador_id=? AND data=?",
                (uid, d.isoformat()),
            ).fetchone()

        login_as(client, "od_lic_test2", "od_lic_test2123")
        token = get_csrf(client)

        resp = client.post(
            f"/oficialdia/licencas-es?d={d.isoformat()}",
            data={
                "csrf_token": token,
                "acao": "entrada",
                "lic_id": str(lic["id"]),
            },
            follow_redirects=False,
        )
        assert resp.status_code == 302

        with sr.db() as conn:
            row = conn.execute(
                "SELECT hora_entrada FROM licencas WHERE id=?", (lic["id"],)
            ).fetchone()
        assert row["hora_entrada"] is not None


# ── Testes de sincronização licenças ↔ controlo presenças ─────────────────────


class TestLicencaSync:
    def test_dar_saida_presencas_syncs_licenca(self, app, client):
        """dar_saida no controlo_presencas atualiza hora_saida na licença."""
        uid = create_aluno("T_SYNC_01", "830", "Sync Saida", "2")
        d = date.today()
        create_system_user("od_sync1", "oficialdia")

        # Criar licença sem hora_saida
        with sr.db() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO licencas (utilizador_id, data, tipo) VALUES (?,?,?)",
                (uid, d.isoformat(), "antes_jantar"),
            )
            conn.commit()

        login_as(client, "od_sync1", "od_sync1123")
        token = get_csrf(client)

        resp = client.post(
            f"/presencas?d={d.isoformat()}",
            data={
                "csrf_token": token,
                "acao": "dar_saida",
                "ni": "830",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 302

        # Verificar que hora_saida foi atualizada na licença
        with sr.db() as conn:
            row = conn.execute(
                "SELECT hora_saida FROM licencas WHERE utilizador_id=? AND data=?",
                (uid, d.isoformat()),
            ).fetchone()
        assert row is not None
        assert row["hora_saida"] is not None

    def test_dar_entrada_presencas_syncs_licenca(self, app, client):
        """dar_entrada no controlo_presencas atualiza hora_entrada na licença."""
        uid = create_aluno("T_SYNC_02", "831", "Sync Entrada", "2")
        d = date.today()
        create_system_user("od_sync2", "oficialdia")

        # Criar licença com saída registada
        with sr.db() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO licencas (utilizador_id, data, tipo, hora_saida) VALUES (?,?,?,?)",
                (uid, d.isoformat(), "apos_jantar", "14:30"),
            )
            conn.commit()

        # Registar ausência (para poder dar entrada)
        import app as app_module

        app_module._registar_ausencia(
            uid, d.isoformat(), d.isoformat(), "Saiu", "teste"
        )

        login_as(client, "od_sync2", "od_sync2123")
        token = get_csrf(client)

        resp = client.post(
            f"/presencas?d={d.isoformat()}",
            data={
                "csrf_token": token,
                "acao": "dar_entrada",
                "ni": "831",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 302

        # Verificar hora_entrada sincronizada
        with sr.db() as conn:
            row = conn.execute(
                "SELECT hora_entrada FROM licencas WHERE utilizador_id=? AND data=?",
                (uid, d.isoformat()),
            ).fetchone()
        assert row is not None
        assert row["hora_entrada"] is not None
