"""
tests/test_ausencias.py — Testes de ausências
==============================================
"""

from datetime import date, timedelta

from core.database import db

from tests.conftest import create_aluno


def _future_date(days=10):
    return date.today() + timedelta(days=days)


class TestRegistarAusencia:
    def test_registar_ausencia_success(self, app):
        """Registar ausência com sucesso."""
        import app as app_module

        uid = create_aluno("T_AUS_01", "601", "Ausencia A", "1")
        d = _future_date(40)

        ok, err = app_module._registar_ausencia(
            uid, d.isoformat(), d.isoformat(), "Doente", "teste"
        )
        assert ok is True
        assert err == ""

    def test_registar_ausencia_multi_day(self, app):
        """Registar ausência de vários dias."""
        import app as app_module

        uid = create_aluno("T_AUS_02", "602", "Ausencia B", "1")
        d1 = _future_date(41)
        d2 = d1 + timedelta(days=3)

        ok, err = app_module._registar_ausencia(
            uid, d1.isoformat(), d2.isoformat(), "Férias", "teste"
        )
        assert ok is True

    def test_registar_ausencia_invalid_dates(self, app):
        """Datas inválidas devem falhar."""
        import app as app_module

        uid = create_aluno("T_AUS_03", "603", "Ausencia C", "1")

        ok, err = app_module._registar_ausencia(
            uid, "data-invalida", "2026-03-20", "Motivo", "teste"
        )
        assert ok is False

    def test_registar_ausencia_end_before_start(self, app):
        """Data fim antes de data início deve falhar."""
        import app as app_module

        uid = create_aluno("T_AUS_04", "604", "Ausencia D", "1")
        d1 = _future_date(44)
        d2 = d1 - timedelta(days=2)

        ok, err = app_module._registar_ausencia(
            uid, d1.isoformat(), d2.isoformat(), "Motivo", "teste"
        )
        assert ok is False


class TestTemAusenciaAtiva:
    def test_ausencia_ativa_true(self, app):
        """_tem_ausencia_ativa retorna True quando ausente."""
        import app as app_module

        uid = create_aluno("T_AUS_05", "605", "Ausencia E", "1")
        d = _future_date(45)

        with db() as conn:
            conn.execute(
                "INSERT INTO ausencias (utilizador_id, ausente_de, ausente_ate, motivo, criado_por) VALUES (?,?,?,?,?)",
                (uid, d.isoformat(), d.isoformat(), "Teste", "teste"),
            )
            conn.commit()

        assert app_module._tem_ausencia_ativa(uid, d) is True

    def test_ausencia_ativa_false(self, app):
        """_tem_ausencia_ativa retorna False quando não ausente."""
        import app as app_module

        uid = create_aluno("T_AUS_06", "606", "Ausencia F", "1")
        d = _future_date(46)
        assert app_module._tem_ausencia_ativa(uid, d) is False

    def test_ausencia_range(self, app):
        """Ausência multi-dia cobre todos os dias no intervalo."""
        import app as app_module

        uid = create_aluno("T_AUS_07", "607", "Ausencia G", "1")
        d1 = _future_date(47)
        d2 = d1 + timedelta(days=3)

        with db() as conn:
            conn.execute(
                "INSERT INTO ausencias (utilizador_id, ausente_de, ausente_ate, motivo, criado_por) VALUES (?,?,?,?,?)",
                (uid, d1.isoformat(), d2.isoformat(), "Período longo", "teste"),
            )
            conn.commit()

        # Todos os dias no intervalo devem estar activos
        for i in range(4):
            d = d1 + timedelta(days=i)
            assert app_module._tem_ausencia_ativa(uid, d) is True, (
                f"Dia {d} deveria estar ausente"
            )

        # Dia seguinte ao intervalo não deve estar
        assert app_module._tem_ausencia_ativa(uid, d2 + timedelta(days=1)) is False

    def test_remove_ausencia(self, app):
        """Remover ausência (dar entrada) funciona correctamente."""
        import app as app_module

        uid = create_aluno("T_AUS_08", "608", "Ausencia H", "1")
        d = _future_date(51)

        with db() as conn:
            conn.execute(
                "INSERT INTO ausencias (utilizador_id, ausente_de, ausente_ate, motivo, criado_por) VALUES (?,?,?,?,?)",
                (uid, d.isoformat(), d.isoformat(), "Teste", "teste"),
            )
            conn.commit()

        assert app_module._tem_ausencia_ativa(uid, d) is True

        # Remover
        with db() as conn:
            conn.execute(
                "DELETE FROM ausencias WHERE utilizador_id=? AND ausente_de=? AND ausente_ate=?",
                (uid, d.isoformat(), d.isoformat()),
            )
            conn.commit()

        assert app_module._tem_ausencia_ativa(uid, d) is False
