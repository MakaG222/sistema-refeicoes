"""
tests/test_performance.py — Testes para otimizações de performance
===================================================================
- Conexão por request (reutilização via Flask g)
- Batch loading (eliminar N+1)
- WAL checkpoint
- Timeout de sessão
"""

from datetime import date, timedelta

from core.absences import ausencias_batch, detencoes_batch, licencas_batch
from core.database import _new_conn, close_request_db, db, wal_checkpoint
from core.meals import dias_operacionais_batch, refeicoes_batch
from conftest import create_aluno, login_as


# ─── Conexão por request ─────────────────────────────────────────────────


class TestRequestScopedConnection:
    def test_same_connection_within_request(self, app):
        """Dentro de um request, db() deve devolver a mesma conexão."""
        with app.test_request_context("/"):
            conn1 = db()
            conn2 = db()
            assert conn1 is conn2

    def test_new_conn_creates_independent_connections(self, app):
        """_new_conn() deve criar conexões independentes sempre."""
        conn1 = _new_conn()
        conn2 = _new_conn()
        assert conn1 is not conn2
        conn1.close()
        conn2.close()

    def test_teardown_closes_connection(self, app):
        """O teardown deve fechar a conexão do request."""
        with app.test_request_context("/"):
            conn = db()
            # Verificar que funciona
            conn.execute("SELECT 1").fetchone()
            close_request_db()
            # Após close, nova chamada cria nova conexão
            conn2 = db()
            assert conn2 is not conn


# ─── Batch loading ───────────────────────────────────────────────────────


class TestBatchLoading:
    def test_refeicoes_batch(self, app):
        """Batch de refeições carrega dados para intervalo de datas."""
        nii = "995"
        uid = create_aluno(nii, "T95", "Teste Batch", ano="1")
        hoje = date.today()
        with db() as conn:
            for i in range(3):
                d = hoje + timedelta(days=i)
                conn.execute(
                    """INSERT OR REPLACE INTO refeicoes
                       (utilizador_id, data, pequeno_almoco, lanche, almoco, jantar_tipo)
                       VALUES (?,?,1,1,'Normal','Vegetariano')""",
                    (uid, d.isoformat()),
                )
            conn.commit()
        ref_map, defaults = refeicoes_batch(uid, hoje, hoje + timedelta(days=2))
        assert len(ref_map) == 3
        assert ref_map[hoje.isoformat()]["almoco"] == "Normal"
        assert defaults["pequeno_almoco"] == 0

    def test_dias_operacionais_batch(self, app):
        """Batch de dias operacionais carrega calendário."""
        hoje = date.today()
        result = dias_operacionais_batch(hoje, hoje + timedelta(days=7))
        assert isinstance(result, dict)

    def test_ausencias_batch(self, app):
        """Batch de ausências devolve set de datas."""
        nii = "995"
        uid = create_aluno(nii, "T95", "Teste Batch", ano="1")
        hoje = date.today()
        amanha = hoje + timedelta(days=1)
        with db() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO ausencias (utilizador_id, ausente_de, ausente_ate) VALUES (?,?,?)",
                (uid, hoje.isoformat(), amanha.isoformat()),
            )
            conn.commit()
        result = ausencias_batch(uid, hoje, hoje + timedelta(days=5))
        assert hoje.isoformat() in result
        assert amanha.isoformat() in result

    def test_detencoes_batch(self, app):
        """Batch de detenções devolve set de datas."""
        nii = "996"
        uid = create_aluno(nii, "T96", "Teste Det Batch", ano="1")
        hoje = date.today()
        with db() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO detencoes (utilizador_id, detido_de, detido_ate, motivo) VALUES (?,?,?,?)",
                (uid, hoje.isoformat(), hoje.isoformat(), "teste"),
            )
            conn.commit()
        result = detencoes_batch(uid, hoje, hoje + timedelta(days=3))
        assert hoje.isoformat() in result

    def test_licencas_batch(self, app):
        """Batch de licenças devolve mapa {data: tipo}."""
        nii = "995"
        uid = create_aluno(nii, "T95", "Teste Batch", ano="1")
        hoje = date.today()
        with db() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO licencas (utilizador_id, data, tipo) VALUES (?,?,?)",
                (uid, hoje.isoformat(), "antes_jantar"),
            )
            conn.commit()
        result = licencas_batch(uid, hoje, hoje + timedelta(days=5))
        assert result.get(hoje.isoformat()) == "antes_jantar"


# ─── WAL checkpoint ──────────────────────────────────────────────────────


class TestWALCheckpoint:
    def test_wal_checkpoint_runs(self, app):
        """WAL checkpoint não dá erro."""
        wal_checkpoint()  # Não deve lançar exceção


# ─── Session timeout ─────────────────────────────────────────────────────


class TestSessionTimeout:
    def test_session_is_permanent_after_login(self, app, client):
        """Após login a sessão deve ser permanente (para timeout funcionar)."""
        nii = "995"
        login_as(client, nii)
        with client.session_transaction() as sess:
            assert sess.permanent is True

    def test_session_lifetime_is_3_min(self, app):
        """PERMANENT_SESSION_LIFETIME deve ser 180 segundos."""
        assert app.config["PERMANENT_SESSION_LIFETIME"] == 180
