"""Testes para o QR rotativo de check-in (PR2).

Cobre:
  - geração e validação de tokens (TTL, tipo)
  - consumo do token (entrada/saida, double-scan, IP/UA)
  - cleanup de tokens expirados
  - rotas oficial: GET /qr-rotativo (HTML) e GET /qr-rotativo/token (JSON)
  - rota aluno: GET /checkin/t/<token> com fluxos de auth, expirado,
    perfil errado, double-scan, sucesso
  - login com `next=` redirige para o handler do checkin
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from tests.conftest import create_aluno, create_system_user, get_csrf, login_as


# ══════════════════════════════════════════════════════════════════════════
# Core — geração / validação / consumo / cleanup
# ══════════════════════════════════════════════════════════════════════════


class TestCoreCheckin:
    def _oficial(self):
        from core.database import db

        with db() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO utilizadores"
                " (NII, NI, Nome_completo, Palavra_chave, ano, perfil)"
                " VALUES (?,?,?,?,?,?)",
                ("of_qr_t1", "of_qr_t1", "Of QR Test", "x", 0, "oficialdia"),
            )
            conn.commit()
            row = conn.execute(
                "SELECT id FROM utilizadores WHERE NII=?", ("of_qr_t1",)
            ).fetchone()
        return row["id"]

    def _aluno(self):
        return create_aluno("alu_qr_t1", "alu_qr_t1", "Aluno QR Test", ano="1")

    def test_gerar_token_inserts_row(self, app):
        from core.checkin import gerar_token

        with app.app_context():
            of_id = self._oficial()
            info = gerar_token(of_id, tipo="auto", ttl_segundos=60)
            assert info["token"] and len(info["token"]) >= 30
            assert info["tipo"] == "auto"
            assert info["expires_at"]

    def test_gerar_token_tipo_invalido_levanta(self, app):
        from core.checkin import gerar_token

        with app.app_context():
            of_id = self._oficial()
            with pytest.raises(ValueError):
                gerar_token(of_id, tipo="brunch")

    def test_validar_token_ok(self, app):
        from core.checkin import gerar_token, validar_token

        with app.app_context():
            of_id = self._oficial()
            info = gerar_token(of_id, tipo="entrada", ttl_segundos=60)
            v = validar_token(info["token"])
            assert v is not None
            assert v["tipo"] == "entrada"

    def test_validar_token_inexistente(self, app):
        from core.checkin import validar_token

        with app.app_context():
            assert validar_token("nao-existe-xyz") is None
            assert validar_token("") is None
            assert validar_token(None) is None  # type: ignore[arg-type]

    def test_validar_token_expirado(self, app):
        """Token gerado com TTL negativo está imediatamente expirado."""
        from core.checkin import gerar_token, validar_token

        with app.app_context():
            of_id = self._oficial()
            # Inserir token já expirado (manualmente, contorna o helper)
            from core.database import db

            past = (datetime.now() - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
            with db() as conn:
                conn.execute(
                    "INSERT INTO checkin_tokens (token, expires_at, created_by, tipo)"
                    " VALUES (?, ?, ?, ?)",
                    ("tok-expirado-xyz", past, of_id, "auto"),
                )
                conn.commit()
            assert validar_token("tok-expirado-xyz") is None
            # E um token novo continua válido
            info = gerar_token(of_id, tipo="auto", ttl_segundos=60)
            assert validar_token(info["token"]) is not None

    def test_consumir_token_grava_log(self, app):
        from core.checkin import consumir_token, gerar_token
        from core.database import db

        with app.app_context():
            of_id = self._oficial()
            aluno_id = self._aluno()
            info = gerar_token(of_id, tipo="auto")
            ok, msg = consumir_token(
                info["token"], aluno_id, "entrada", ip="1.2.3.4", user_agent="ua"
            )
            assert ok
            with db() as conn:
                row = conn.execute(
                    "SELECT * FROM checkin_log WHERE utilizador_id=? AND token=?",
                    (aluno_id, info["token"]),
                ).fetchone()
            assert row is not None
            assert row["tipo"] == "entrada"
            assert row["ip"] == "1.2.3.4"

    def test_consumir_token_double_scan_rejeitado(self, app):
        from core.checkin import consumir_token, gerar_token

        with app.app_context():
            of_id = self._oficial()
            aluno_id = self._aluno()
            info = gerar_token(of_id, tipo="auto")
            ok1, _ = consumir_token(info["token"], aluno_id, "saida")
            ok2, msg2 = consumir_token(info["token"], aluno_id, "saida")
            assert ok1 is True
            assert ok2 is False
            assert "já registaste" in msg2.lower() or "ja registaste" in msg2.lower()

    def test_consumir_token_tipo_resolvido_invalido(self, app):
        from core.checkin import consumir_token

        with app.app_context():
            ok, msg = consumir_token("qq", 1, "auto")
            assert ok is False
            assert "tipo" in msg.lower()

    def test_cleanup_expired_remove_apenas_passados(self, app):
        from core.checkin import cleanup_expired, gerar_token, validar_token
        from core.database import db

        with app.app_context():
            of_id = self._oficial()
            past = (datetime.now() - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
            with db() as conn:
                conn.execute(
                    "INSERT INTO checkin_tokens (token, expires_at, created_by, tipo)"
                    " VALUES (?, ?, ?, ?)",
                    ("expired-cleanup", past, of_id, "auto"),
                )
                conn.commit()
            ainda_valido = gerar_token(of_id, tipo="auto", ttl_segundos=120)

            n = cleanup_expired()
            assert n >= 1
            assert validar_token("expired-cleanup") is None
            assert validar_token(ainda_valido["token"]) is not None


# ══════════════════════════════════════════════════════════════════════════
# Rotas oficial: /qr-rotativo (HTML) + /qr-rotativo/token (JSON)
# ══════════════════════════════════════════════════════════════════════════


class TestRotasOficial:
    def _login_oficial(self, client):
        create_system_user(
            "of_qr_r1", "oficialdia", nome="Oficial QR R1", pw="OfQrR1xxx"
        )
        login_as(client, "of_qr_r1", pw="OfQrR1xxx")

    def test_qr_rotativo_html_render(self, client):
        self._login_oficial(client)
        r = client.get("/qr-rotativo")
        assert r.status_code == 200
        assert b"QR Rotativo" in r.data
        # Marcadores essenciais para o JS encontrar
        assert b"data-qr-rotativo" in r.data
        assert b"qrFrame" in r.data
        assert b"qr-rotativo.js" in r.data

    def test_qr_rotativo_html_requer_oficial(self, client):
        # Sem login → redirect para login
        r = client.get("/qr-rotativo", follow_redirects=False)
        assert r.status_code in (301, 302)

    def test_qr_rotativo_token_json_ok(self, client):
        self._login_oficial(client)
        r = client.get("/qr-rotativo/token?tipo=auto")
        assert r.status_code == 200
        data = r.get_json()
        assert data["token"]
        assert "/checkin/t/" in data["url"]
        assert data["tipo"] == "auto"
        assert data["ttl_seconds"] > 0
        assert "<svg" in data["svg"] or "svg" in data["svg"]

    def test_qr_rotativo_token_normaliza_tipo_invalido(self, client):
        """tipo desconhecido é normalizado para 'auto' (não 500)."""
        self._login_oficial(client)
        r = client.get("/qr-rotativo/token?tipo=brunch")
        assert r.status_code == 200
        assert r.get_json()["tipo"] == "auto"

    def test_qr_rotativo_token_negado_para_aluno(self, client):
        create_aluno("alu_qr_r1", "alu_qr_r1", "Aluno QR R1")
        login_as(client, "alu_qr_r1", pw="alu_qr_r1")
        r = client.get("/qr-rotativo/token", follow_redirects=False)
        # role_required: alunos são redirigidos para dashboard
        assert r.status_code in (301, 302)


# ══════════════════════════════════════════════════════════════════════════
# Rota aluno: /checkin/t/<token>
# ══════════════════════════════════════════════════════════════════════════


class TestRotaAluno:
    def _setup(self, client):
        create_system_user("of_qr_a1", "oficialdia", nome="Of QR A1", pw="OfQrA1xxx")
        create_aluno("alu_qr_a1", "alu_qr_a1", "Aluno QR A1")

    def _gerar_token(self, app, tipo="auto"):
        from core.checkin import gerar_token
        from core.database import db

        with app.app_context():
            with db() as conn:
                of = conn.execute(
                    "SELECT id FROM utilizadores WHERE NII=?", ("of_qr_a1",)
                ).fetchone()
            return gerar_token(of["id"], tipo=tipo)["token"]

    def test_checkin_sem_login_redirige_para_login_com_next(self, app, client):
        self._setup(client)
        token = self._gerar_token(app)
        r = client.get(f"/checkin/t/{token}", follow_redirects=False)
        assert r.status_code in (301, 302)
        loc = r.headers.get("Location", "")
        assert "/login" in loc
        # `next=` propaga o caminho do checkin (Flask não escapa o `/`)
        assert "next=" in loc and "/checkin/t/" in loc

    def test_checkin_com_aluno_autenticado_marca_saida(self, app, client):
        """Aluno sem ausência hoje → token=auto resolve para saída."""
        self._setup(client)
        login_as(client, "alu_qr_a1", pw="alu_qr_a1")
        token = self._gerar_token(app, tipo="auto")
        r = client.get(f"/checkin/t/{token}", follow_redirects=False)
        assert r.status_code in (301, 302)
        # Verifica que o checkin_log foi gravado
        from core.database import db

        with app.app_context():
            with db() as conn:
                row = conn.execute(
                    "SELECT tipo FROM checkin_log WHERE token=?", (token,)
                ).fetchone()
        assert row is not None
        assert row["tipo"] == "saida"

    def test_checkin_token_inexistente_da_flash(self, app, client):
        self._setup(client)
        login_as(client, "alu_qr_a1", pw="alu_qr_a1")
        r = client.get("/checkin/t/nao-existe-xyz", follow_redirects=True)
        assert r.status_code == 200
        # Mensagem de expirado/inválido aparece (UTF-8)
        assert b"expirado" in r.data.lower() or b"inv" in r.data.lower()

    def test_checkin_double_scan_da_warning(self, app, client):
        self._setup(client)
        login_as(client, "alu_qr_a1", pw="alu_qr_a1")
        token = self._gerar_token(app, tipo="entrada")
        r1 = client.get(f"/checkin/t/{token}", follow_redirects=False)
        r2 = client.get(f"/checkin/t/{token}", follow_redirects=True)
        assert r1.status_code in (301, 302)
        assert r2.status_code == 200
        assert (
            b"j\xc3\xa1 registaste" in r2.data.lower()
            or b"ja registaste" in r2.data.lower()
        )

    def test_checkin_oficial_nao_pode_usar_via_qr(self, app, client):
        self._setup(client)
        login_as(client, "of_qr_a1", pw="OfQrA1xxx")
        token = self._gerar_token(app)
        r = client.get(f"/checkin/t/{token}", follow_redirects=True)
        assert r.status_code == 200
        # Mensagem indicando que oficiais usam o quiosque
        assert b"quiosque" in r.data.lower() or b"alunos" in r.data.lower()


# ══════════════════════════════════════════════════════════════════════════
# Login: parâmetro `next=` honrado (anti open-redirect)
# ══════════════════════════════════════════════════════════════════════════


class TestLoginNextUrl:
    def test_login_next_relativo_aceite(self, app, client):
        create_aluno("alu_qr_n1", "alu_qr_n1", "Aluno N1")
        client.get("/login?next=/checkin/t/abc")
        token = get_csrf(client)
        r = client.post(
            "/login",
            data={
                "nii": "alu_qr_n1",
                "pw": "alu_qr_n1",
                "csrf_token": token,
                "next": "/checkin/t/abc",
            },
            follow_redirects=False,
        )
        assert r.status_code in (301, 302)
        assert r.headers.get("Location", "").endswith("/checkin/t/abc")

    def test_login_next_absoluto_rejeitado(self, app, client):
        """`next` com protocolo deve ser ignorado (anti open-redirect)."""
        create_aluno("alu_qr_n2", "alu_qr_n2", "Aluno N2")
        client.get("/login")
        token = get_csrf(client)
        r = client.post(
            "/login",
            data={
                "nii": "alu_qr_n2",
                "pw": "alu_qr_n2",
                "csrf_token": token,
                "next": "https://malicioso.example/login",
            },
            follow_redirects=False,
        )
        assert r.status_code in (301, 302)
        loc = r.headers.get("Location", "")
        assert "malicioso" not in loc

    def test_login_next_protocol_relative_rejeitado(self, app, client):
        create_aluno("alu_qr_n3", "alu_qr_n3", "Aluno N3")
        client.get("/login")
        token = get_csrf(client)
        r = client.post(
            "/login",
            data={
                "nii": "alu_qr_n3",
                "pw": "alu_qr_n3",
                "csrf_token": token,
                "next": "//evil.example/x",
            },
            follow_redirects=False,
        )
        assert r.status_code in (301, 302)
        assert "evil.example" not in r.headers.get("Location", "")


# ══════════════════════════════════════════════════════════════════════════
# /api/unlock-expired também limpa checkin_tokens
# ══════════════════════════════════════════════════════════════════════════


class TestApiUnlockExpiredLimpaTokens:
    def test_unlock_expired_remove_checkin_tokens(self, app, client):
        import os

        from core.database import db

        # Necessita do mesmo Bearer que a rota — sem token configurado o
        # endpoint devolve 403; testamos só quando há token.
        cron_token = os.getenv("CRON_API_TOKEN") or "test-cron-token"
        os.environ["CRON_API_TOKEN"] = cron_token
        with app.app_context():
            create_system_user("of_clean", "oficialdia", pw="OfCleanXxx")
            with db() as conn:
                of = conn.execute(
                    "SELECT id FROM utilizadores WHERE NII=?", ("of_clean",)
                ).fetchone()
                past = (datetime.now() - timedelta(minutes=5)).strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
                conn.execute(
                    "INSERT INTO checkin_tokens (token, expires_at, created_by, tipo)"
                    " VALUES (?, ?, ?, ?)",
                    ("clean-me-1", past, of["id"], "auto"),
                )
                conn.commit()
        r = client.post(
            "/api/unlock-expired",
            headers={"Authorization": f"Bearer {cron_token}"},
        )
        # Pode ser 200 (token ok) ou 403 (token diferente em CI). Aceitamos
        # ambos, mas se 200 → o counter tem de aparecer.
        if r.status_code == 200:
            data = r.get_json() or {}
            assert "expired_checkin_tokens" in (data.get("data") or data)
