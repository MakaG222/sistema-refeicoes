"""
tests/test_ausencias.py — Testes de ausências
==============================================
"""

from datetime import date, timedelta

from core.database import db
from utils.business import _horarios_sobrepoe, _refeicoes_afetadas

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


class TestHorariosSobrepoe:
    """Testa a função _horarios_sobrepoe."""

    def test_overlap_total(self):
        assert _horarios_sobrepoe("08:00", "20:00", "12:00", "14:00") is True

    def test_overlap_parcial_inicio(self):
        assert _horarios_sobrepoe("11:00", "13:00", "12:00", "14:00") is True

    def test_overlap_parcial_fim(self):
        assert _horarios_sobrepoe("13:00", "15:00", "12:00", "14:00") is True

    def test_sem_overlap_antes(self):
        assert _horarios_sobrepoe("08:00", "10:00", "12:00", "14:00") is False

    def test_sem_overlap_depois(self):
        assert _horarios_sobrepoe("15:00", "17:00", "12:00", "14:00") is False

    def test_limite_exato_nao_sobrepoe(self):
        """Fim igual a início não sobrepõe."""
        assert _horarios_sobrepoe("10:00", "12:00", "12:00", "14:00") is False


class TestRefeicoeAfetadas:
    """Testa _refeicoes_afetadas — mapeia horários a refeições."""

    def test_dia_inteiro_none(self):
        """Sem horas → todas as refeições afetadas."""
        r = _refeicoes_afetadas(None, None)
        assert all(r.values())

    def test_manha_afeta_pa(self):
        """07:00-10:00 afeta PA mas não almoço/jantar."""
        r = _refeicoes_afetadas("07:00", "10:00")
        assert r["pequeno_almoco"] is True
        assert r["almoco"] is False
        assert r["jantar"] is False

    def test_meio_dia_afeta_almoco(self):
        """10:00-14:00 afeta almoço."""
        r = _refeicoes_afetadas("10:00", "14:00")
        assert r["almoco"] is True
        assert r["pequeno_almoco"] is False
        assert r["jantar"] is False

    def test_tarde_noite_afeta_lanche_jantar(self):
        """16:00-21:00 afeta lanche e jantar."""
        r = _refeicoes_afetadas("16:00", "21:00")
        assert r["lanche"] is True
        assert r["jantar"] is True
        assert r["pequeno_almoco"] is False

    def test_todo_dia_afeta_tudo(self):
        """06:00-22:00 afeta tudo."""
        r = _refeicoes_afetadas("06:00", "22:00")
        assert all(r.values())


class TestRegistarAusenciaComHorarios:
    """Testa registo de ausências com hora_inicio/hora_fim e estufa."""

    def test_ausencia_parcial_sucesso(self, app):
        import app as app_module

        uid = create_aluno("T_AUS_H01", "651", "Parcial A", "1")
        d = _future_date(60)

        ok, err = app_module._registar_ausencia(
            uid,
            d.isoformat(),
            d.isoformat(),
            "Consulta",
            "teste",
            hora_inicio="10:00",
            hora_fim="14:00",
        )
        assert ok is True
        assert err == ""

        # Verificar que foi guardado na BD
        with db() as conn:
            row = conn.execute(
                "SELECT hora_inicio, hora_fim FROM ausencias WHERE utilizador_id=?",
                (uid,),
            ).fetchone()
        assert row["hora_inicio"] == "10:00"
        assert row["hora_fim"] == "14:00"

    def test_ausencia_hora_invalida(self, app):
        import app as app_module

        uid = create_aluno("T_AUS_H02", "652", "Parcial B", "1")
        d = _future_date(61)

        ok, err = app_module._registar_ausencia(
            uid,
            d.isoformat(),
            d.isoformat(),
            "",
            "teste",
            hora_inicio="25:00",
            hora_fim="14:00",
        )
        assert ok is False
        assert "Hora inválida" in err

    def test_ausencia_hora_inicio_sem_fim(self, app):
        import app as app_module

        uid = create_aluno("T_AUS_H03", "653", "Parcial C", "1")
        d = _future_date(62)

        ok, err = app_module._registar_ausencia(
            uid,
            d.isoformat(),
            d.isoformat(),
            "",
            "teste",
            hora_inicio="10:00",
            hora_fim=None,
        )
        assert ok is False
        assert "ambas" in err.lower()

    def test_ausencia_hora_inicio_depois_fim(self, app):
        import app as app_module

        uid = create_aluno("T_AUS_H04", "654", "Parcial D", "1")
        d = _future_date(63)

        ok, err = app_module._registar_ausencia(
            uid,
            d.isoformat(),
            d.isoformat(),
            "",
            "teste",
            hora_inicio="15:00",
            hora_fim="10:00",
        )
        assert ok is False
        assert "anterior" in err.lower()

    def test_ausencia_com_estufa_almoco(self, app):
        """Ausência com estufa_almoco marca almoco_estufa=1 em vez de limpar."""
        import app as app_module
        from core.meals import refeicao_save, refeicao_get

        uid = create_aluno("T_AUS_H05", "655", "Estufa A", "1")
        d = _future_date(64)

        refeicao_save(
            uid,
            d,
            {
                "pequeno_almoco": 1,
                "lanche": 0,
                "almoco": "Normal",
                "jantar_tipo": "Normal",
                "jantar_sai_unidade": 0,
            },
            alterado_por="teste",
        )

        ok, _ = app_module._registar_ausencia(
            uid,
            d.isoformat(),
            d.isoformat(),
            "Consulta",
            "teste",
            hora_inicio="10:00",
            hora_fim="14:00",
            estufa_almoco=True,
        )
        assert ok is True

        r = refeicao_get(uid, d)
        assert r.get("almoco_estufa") == 1
        assert r.get("almoco") == "Normal"  # mantido
        assert r.get("jantar_tipo") == "Normal"  # não afetado

    def test_ausencia_parcial_limpa_so_refeicoes_afetadas(self, app):
        """Ausência 10h-14h só limpa almoço, mantém PA e jantar."""
        import app as app_module
        from core.meals import refeicao_save, refeicao_get

        uid = create_aluno("T_AUS_H06", "656", "Parcial E", "1")
        d = _future_date(65)

        refeicao_save(
            uid,
            d,
            {
                "pequeno_almoco": 1,
                "lanche": 1,
                "almoco": "Vegetariano",
                "jantar_tipo": "Normal",
                "jantar_sai_unidade": 0,
            },
            alterado_por="teste",
        )

        ok, _ = app_module._registar_ausencia(
            uid,
            d.isoformat(),
            d.isoformat(),
            "",
            "teste",
            hora_inicio="10:00",
            hora_fim="14:00",
        )
        assert ok is True

        r = refeicao_get(uid, d)
        assert r.get("pequeno_almoco") == 1  # mantido
        assert r.get("lanche") == 1  # mantido
        assert r.get("almoco") is None  # limpo
        assert r.get("jantar_tipo") == "Normal"  # mantido

    def test_editar_ausencia_com_horarios(self, app):
        import app as app_module

        uid = create_aluno("T_AUS_H07", "657", "Edit Hora", "1")
        d = _future_date(66)

        app_module._registar_ausencia(uid, d.isoformat(), d.isoformat(), "", "teste")

        with db() as conn:
            aid = conn.execute(
                "SELECT id FROM ausencias WHERE utilizador_id=?", (uid,)
            ).fetchone()["id"]

        ok, err = app_module._editar_ausencia(
            aid,
            uid,
            d.isoformat(),
            d.isoformat(),
            "Motivo editado",
            hora_inicio="08:00",
            hora_fim="12:00",
            estufa_almoco=False,
            estufa_jantar=True,
        )
        assert ok is True

        with db() as conn:
            row = conn.execute(
                "SELECT hora_inicio, hora_fim, estufa_jantar FROM ausencias WHERE id=?",
                (aid,),
            ).fetchone()
        assert row["hora_inicio"] == "08:00"
        assert row["hora_fim"] == "12:00"
        assert row["estufa_jantar"] == 1


class TestAusenciasBatchDetalhadas:
    """Testa ausencias_batch_detalhadas."""

    def test_retorna_info_parcial(self, app):
        from core.absences import ausencias_batch_detalhadas

        uid = create_aluno("T_AUS_BD1", "661", "Batch Det A", "1")
        d = _future_date(70)

        with db() as conn:
            conn.execute(
                """INSERT INTO ausencias
                   (utilizador_id,ausente_de,ausente_ate,hora_inicio,hora_fim,estufa_almoco)
                   VALUES (?,?,?,?,?,?)""",
                (uid, d.isoformat(), d.isoformat(), "10:00", "14:00", 1),
            )
            conn.commit()

        result = ausencias_batch_detalhadas(uid, d, d)
        assert d.isoformat() in result
        info = result[d.isoformat()]
        assert info["parcial"] is True
        assert info["hora_inicio"] == "10:00"
        assert info["hora_fim"] == "14:00"
        assert info["estufa_almoco"] is True

    def test_dia_inteiro_nao_parcial(self, app):
        from core.absences import ausencias_batch_detalhadas

        uid = create_aluno("T_AUS_BD2", "662", "Batch Det B", "1")
        d = _future_date(71)

        with db() as conn:
            conn.execute(
                "INSERT INTO ausencias (utilizador_id,ausente_de,ausente_ate) VALUES (?,?,?)",
                (uid, d.isoformat(), d.isoformat()),
            )
            conn.commit()

        result = ausencias_batch_detalhadas(uid, d, d)
        info = result[d.isoformat()]
        assert info["parcial"] is False


class TestMigracaoAusenciaHorarios:
    """Testa que a migração 006 adiciona as colunas correctamente."""

    def test_migration_adds_columns(self, app):
        from core.migrations import _add_ausencia_horarios

        with db() as conn:
            _add_ausencia_horarios(conn)
            cols = [
                r["name"]
                for r in conn.execute("PRAGMA table_info(ausencias)").fetchall()
            ]
        assert "hora_inicio" in cols
        assert "hora_fim" in cols
        assert "estufa_almoco" in cols
        assert "estufa_jantar" in cols

    def test_migration_idempotent(self, app):
        """Correr migração duas vezes não falha."""
        from core.migrations import _add_ausencia_horarios

        with db() as conn:
            _add_ausencia_horarios(conn)
            _add_ausencia_horarios(conn)  # segunda vez — não deve falhar
