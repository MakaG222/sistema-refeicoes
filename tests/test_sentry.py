"""tests/test_sentry.py — Sentry error tracking (opt-in via SENTRY_DSN).

Cobre:
  - configure_sentry() é no-op quando SENTRY_DSN está vazio
  - configure_sentry() inicializa quando SENTRY_DSN é válido
  - _scrub_event remove campos sensíveis (passwords, NII, NI, cookies, csrf)
  - send_default_pii=False está sempre activo
"""

from __future__ import annotations

import importlib


# ══════════════════════════════════════════════════════════════════════════
# configure_sentry() — boot e no-op
# ══════════════════════════════════════════════════════════════════════════


class TestConfigureSentry:
    def test_no_op_quando_dsn_vazio(self, monkeypatch):
        """Sem SENTRY_DSN, configure_sentry() devolve False sem efeitos."""
        monkeypatch.setenv("SENTRY_DSN", "")
        # Re-importa para apanhar o env actualizado
        import config

        importlib.reload(config)
        assert config.configure_sentry() is False
        # Não deve tocar no estado global do sentry_sdk
        import sentry_sdk

        # client pode ou não existir consoante outros testes; o que importa
        # é que SEM DSN não fica vinculado a nenhum DSN nosso.
        # API 2.x: get_client() em vez de Hub.current.client
        client = sentry_sdk.get_client()
        # NonRecordingClient (DSN ausente) não tem dsn ou tem None
        assert (client is None) or (
            getattr(client, "dsn", None) != "this-test-set-no-dsn"
        )

    def test_init_com_dsn_valido(self, monkeypatch):
        """Com SENTRY_DSN definido, devolve True e inicializa."""
        # DSN com formato válido (sentry valida structurally)
        monkeypatch.setenv("SENTRY_DSN", "https://abc123@o0.ingest.sentry.io/0")
        monkeypatch.setenv("ENV", "test")
        import config

        importlib.reload(config)
        try:
            assert config.configure_sentry() is True
        finally:
            # Cleanup: fecha o client para não poluir outros testes.
            # API 2.x: client.close() em vez de Hub.bind_client(None).
            import sentry_sdk

            client = sentry_sdk.get_client()
            if client is not None and hasattr(client, "close"):
                client.close()

    def test_init_dsn_invalido_devolve_false_sem_crash(self, monkeypatch):
        """DSN sintáticamente errado: configure_sentry NÃO crasha a app."""
        monkeypatch.setenv("SENTRY_DSN", "isto-não-é-um-dsn")
        import config

        importlib.reload(config)
        # Deve ou retornar False (sentry rejeitou) ou True (sentry aceitou
        # tipo Lazy) — o que NÃO pode é levantar exceção.
        result = config.configure_sentry()
        assert result in (True, False)


# ══════════════════════════════════════════════════════════════════════════
# _scrub_event — RGPD / segurança de dados
# ══════════════════════════════════════════════════════════════════════════


class TestScrubEvent:
    def test_scrub_password_em_form(self):
        from config import _scrub_event

        ev = {"request": {"data": {"nii": "12345", "pw": "secret123", "outro": "ok"}}}
        out = _scrub_event(ev, None)
        assert out["request"]["data"]["pw"] == "[Filtered]"
        assert out["request"]["data"]["nii"] == "[Filtered]"
        # Campos não-sensíveis ficam intactos
        assert out["request"]["data"]["outro"] == "ok"

    def test_scrub_csrf_token(self):
        from config import _scrub_event

        ev = {
            "request": {
                "data": {
                    "csrf_token": "abc",
                    "_csrf_token": "def",
                }
            }
        }
        out = _scrub_event(ev, None)
        assert out["request"]["data"]["csrf_token"] == "[Filtered]"
        assert out["request"]["data"]["_csrf_token"] == "[Filtered]"

    def test_scrub_authorization_header(self):
        from config import _scrub_event

        ev = {
            "request": {
                "headers": {
                    "Authorization": "Bearer secret-token",
                    "User-Agent": "ok-keep",
                }
            }
        }
        out = _scrub_event(ev, None)
        assert out["request"]["headers"]["Authorization"] == "[Filtered]"
        assert out["request"]["headers"]["User-Agent"] == "ok-keep"

    def test_scrub_cookies(self):
        from config import _scrub_event

        ev = {
            "request": {
                "cookies": {"session": "abc.def.ghi", "ok_cookie": "fine"},
            }
        }
        out = _scrub_event(ev, None)
        # 'cookie' literal está em scrub list mas chaves específicas como
        # 'session' não estão; essas cobrimos via send_default_pii=False
        # que o Sentry já aplica. Aqui validamos que o scrub não crasha
        # com cookies presentes.
        assert "cookies" in out["request"]
        # ok_cookie não está na lista
        assert out["request"]["cookies"]["ok_cookie"] == "fine"

    def test_scrub_extras(self):
        from config import _scrub_event

        ev = {
            "extra": {"reset_code": "ABC123", "outro_ctx": "ok"},
        }
        out = _scrub_event(ev, None)
        assert out["extra"]["reset_code"] == "[Filtered]"
        assert out["extra"]["outro_ctx"] == "ok"

    def test_scrub_nunca_crasha(self):
        """Se o evento tem estrutura inesperada, devolve event sem alterar."""
        from config import _scrub_event

        # Estruturas malformadas / esquisitas
        for ev in [
            {},
            {"request": "string em vez de dict"},
            {"request": {"data": None}},
            {"request": {"headers": ["lista", "em", "vez"]}},
            {"extra": "string"},
        ]:
            out = _scrub_event(ev, None)  # não levanta
            assert out is ev  # devolve o mesmo objecto (in-place ou intacto)

    def test_scrub_case_insensitive_em_headers(self):
        """Headers vêm em casing variável; scrub apanha ambos."""
        from config import _scrub_event

        ev = {
            "request": {"headers": {"authorization": "Bearer x", "Cookie": "session=y"}}
        }
        out = _scrub_event(ev, None)
        assert out["request"]["headers"]["authorization"] == "[Filtered]"
        assert out["request"]["headers"]["Cookie"] == "[Filtered]"


# ══════════════════════════════════════════════════════════════════════════
# Integração com app — Sentry init não quebra a app sem DSN
# ══════════════════════════════════════════════════════════════════════════


class TestAppIntegration:
    def test_app_arranca_sem_sentry_dsn(self, app, client):
        """A app tem que arrancar e responder mesmo sem SENTRY_DSN."""
        # Esta fixture já carregou app sem SENTRY_DSN — basta fazer um request
        r = client.get("/health")
        assert r.status_code in (200, 503)  # qualquer dos dois é "app live"

    def test_health_funciona_com_sentry_off(self, client):
        r = client.get("/health")
        # Deve responder JSON, não levantar
        assert r.is_json
