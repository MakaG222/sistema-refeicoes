"""
tests/test_api_routes.py — Testes para blueprints/api/routes.py
================================================================
Cobre: /health edge cases, /health/metrics, cron endpoints, _api_error helper.
Linhas não cobertas: 32, 43-48, 70-73, 82-83, 95-99, 109-111, 147-149, 162-164
"""

from __future__ import annotations

import os
from unittest import mock


# ── /health — casos normais (já cobertos em test_auth.py, mas completados aqui) ──


class TestHealthEndpoint:
    def test_health_db_ok(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["db"] == "ok"

    def test_health_has_db_size(self, client):
        resp = client.get("/health")
        data = resp.get_json()
        # db_size_mb pode ser None (linha 83) se o path falhar, ou um float
        assert "db_size_mb" in data

    def test_health_db_size_none_when_path_fails(self, client):
        """Linha 82-83: db_size_mb = None quando getsize falha."""
        with mock.patch("os.path.getsize", side_effect=OSError("sem acesso")):
            resp = client.get("/health")
            data = resp.get_json()
        assert data.get("db_size_mb") is None

    def test_health_backup_no_backups(self, client, tmp_path):
        """Linha 95-97: sem backups → checks['backup'] = 'no_backups'."""
        with mock.patch("core.constants.BACKUP_DIR", str(tmp_path)):
            resp = client.get("/health")
            data = resp.get_json()
        assert data.get("backup") == "no_backups"

    def test_health_backup_warn_when_stale(self, client, tmp_path):
        """Linha 94-95: backup com mais de 48 h → checks['backup'] = 'warn'."""
        import time

        old_file = tmp_path / "old_backup.db"
        old_file.write_bytes(b"x")
        # Definir mtime para 3 dias atrás
        old_mtime = time.time() - 3 * 24 * 3600
        os.utime(str(old_file), (old_mtime, old_mtime))

        with mock.patch("core.constants.BACKUP_DIR", str(tmp_path)):
            resp = client.get("/health")
            data = resp.get_json()
        assert data.get("backup") == "warn"
        assert "last_backup_hours" in data

    def test_health_backup_recent(self, client, tmp_path):
        """Backup recente: last_backup_hours < 48, sem 'backup' warning."""
        import time

        new_file = tmp_path / "recent_backup.db"
        new_file.write_bytes(b"x")
        recent_mtime = time.time() - 1 * 3600  # 1 hora atrás
        os.utime(str(new_file), (recent_mtime, recent_mtime))

        with mock.patch("core.constants.BACKUP_DIR", str(tmp_path)):
            resp = client.get("/health")
            data = resp.get_json()
        assert "last_backup_hours" in data
        assert data.get("backup") != "warn"

    def test_health_backup_exception(self, client):
        """Linha 98-99: excepção ao iterar backups → checks['backup'] = 'unknown'."""
        # Fazer Path.glob levantar excepção para acionar o except
        from pathlib import Path

        def raising_glob(self, pattern):
            raise OSError("sem permissão")

        with mock.patch.object(Path, "glob", raising_glob):
            resp = client.get("/health")
            data = resp.get_json()
        assert data.get("backup") == "unknown"

    def test_health_disk_warn_when_low(self, client):
        """Linha 108-109: disco < 100 MB → disk = 'warn'."""
        fake_stat = mock.Mock()
        fake_stat.f_bavail = 10
        fake_stat.f_frsize = 1024 * 1024  # 10 MB livre
        with mock.patch("os.statvfs", return_value=fake_stat):
            resp = client.get("/health")
            data = resp.get_json()
        assert data.get("disk") == "warn"

    def test_health_disk_exception_ignored(self, client):
        """Linha 110-111: excepção em statvfs não deixa 'disk' na resposta."""
        with mock.patch("os.statvfs", side_effect=OSError("não suportado")):
            resp = client.get("/health")
            data = resp.get_json()
        # 'disk' pode não estar presente ou não ter valor 'warn'
        assert data.get("disk") != "warn"

    def test_health_db_error_returns_503(self, app, client):
        """Linhas 70-73: falha de BD → overall='error', HTTP 503."""

        def broken_db():
            class BrokenConn:
                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    pass

                def execute(self, *a, **kw):
                    raise Exception("BD simulada com falha")

            return BrokenConn()

        with mock.patch("blueprints.api.routes.db", broken_db):
            resp = client.get("/health")
        assert resp.status_code == 503
        data = resp.get_json()
        assert data["status"] == "error"
        assert data["db"] == "error"


# ── /health/metrics ────────────────────────────────────────────────────────────


class TestHealthMetrics:
    def test_metrics_returns_200(self, client):
        resp = client.get("/health/metrics")
        assert resp.status_code == 200

    def test_metrics_returns_expected_keys(self, client):
        resp = client.get("/health/metrics")
        data = resp.get_json()
        assert data["status"] == "ok"
        assert "request_count" in data
        assert "error_count" in data
        assert "avg_latency_ms" in data

    def test_metrics_no_auth_required(self, client):
        resp = client.get("/health/metrics")
        assert resp.status_code == 200

    def test_metrics_avg_latency_when_zero_requests(self, client):
        """avg_latency_ms não falha quando request_count == 0 (divisão por max(count,1))."""
        with mock.patch(
            "core.middleware.get_metrics",
            return_value={
                "request_count": 0,
                "error_count": 0,
                "total_latency_ms": 0.0,
            },
        ):
            resp = client.get("/health/metrics")
            data = resp.get_json()
        assert data["avg_latency_ms"] == 0.0


# ── _api_error helper ──────────────────────────────────────────────────────────


class TestApiErrorHelper:
    """Linha 32: _api_error devolve dict com status='error' e o status code correcto."""

    def test_api_error_default_status(self, app):
        with app.app_context():
            from blueprints.api.routes import _api_error

            body, code = _api_error("algo falhou")
        assert code == 500
        assert body["status"] == "error"
        assert body["error"] == "algo falhou"
        assert "ts" in body

    def test_api_error_custom_status(self, app):
        with app.app_context():
            from blueprints.api.routes import _api_error

            body, code = _api_error("não encontrado", 404)
        assert code == 404
        assert body["error"] == "não encontrado"


# ── Cron endpoints ─────────────────────────────────────────────────────────────


class TestCronToken:
    """Linhas 43-48: _verify_cron_token sem CRON_API_TOKEN em desenvolvimento."""

    def test_backup_cron_no_token_header_returns_403(self, client):
        """Sem header Authorization → 403."""
        resp = client.post("/api/backup-cron")
        assert resp.status_code == 403

    def test_backup_cron_wrong_token_returns_403(self, client):
        """Token errado quando CRON_API_TOKEN está definido → 403."""
        import config as cfg

        with mock.patch.object(cfg, "CRON_API_TOKEN", "correct-token-xyz"):
            resp = client.post(
                "/api/backup-cron",
                headers={"Authorization": "Bearer token_errado"},
            )
        assert resp.status_code == 403

    def test_backup_cron_valid_token_ok(self, client):
        """Token correcto → executa e devolve 200."""
        import config as cfg

        token = "test-cron-token-abc123"
        with mock.patch.object(cfg, "CRON_API_TOKEN", token):
            resp = client.post(
                "/api/backup-cron",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"

    def test_backup_cron_no_configured_token_dev_mode(self, app, client):
        """CRON_API_TOKEN vazio + não-produção → aceita Bearer 'dev' como fallback."""
        import config as cfg

        with (
            mock.patch.object(cfg, "CRON_API_TOKEN", ""),
            mock.patch.object(cfg, "is_production", False),
        ):
            # Token arbitrário deve falhar
            resp = client.post(
                "/api/backup-cron",
                headers={"Authorization": "Bearer qualquer_coisa"},
            )
            assert resp.status_code == 403

            # Token 'dev' deve passar
            resp = client.post(
                "/api/backup-cron",
                headers={"Authorization": "Bearer dev"},
            )
            assert resp.status_code in (200, 500)

    def test_backup_cron_no_configured_token_production_blocks(self, app, client):
        """Linhas 43-44: CRON_API_TOKEN vazio + produção → bloqueia."""
        import config as cfg

        with (
            mock.patch.object(cfg, "CRON_API_TOKEN", ""),
            mock.patch.object(cfg, "is_production", True),
        ):
            resp = client.post(
                "/api/backup-cron",
                headers={"Authorization": "Bearer qualquer_coisa"},
            )
        assert resp.status_code == 403

    def test_backup_cron_exception_returns_error(self, client):
        """Linhas 147-149: excepção em ensure_daily_backup → _api_error."""
        import config as cfg

        token = "test-err-token"
        with (
            mock.patch.object(cfg, "CRON_API_TOKEN", token),
            mock.patch(
                "blueprints.api.routes.ensure_daily_backup",
                side_effect=RuntimeError("disco cheio"),
            ),
        ):
            resp = client.post(
                "/api/backup-cron",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 500
        data = resp.get_json()
        assert data["status"] == "error"
        assert "disco cheio" in data["error"]

    def test_autopreencher_cron_no_token_returns_403(self, client):
        resp = client.post("/api/autopreencher-cron")
        assert resp.status_code == 403

    def test_autopreencher_cron_valid_token_ok(self, client):
        """Linha 160: autopreencher_refeicoes_semanais é chamado com token válido."""
        import config as cfg

        token = "test-cron-autop"
        with (
            mock.patch.object(cfg, "CRON_API_TOKEN", token),
            mock.patch(
                "blueprints.api.routes.autopreencher_refeicoes_semanais"
            ) as mock_auto,
        ):
            resp = client.post(
                "/api/autopreencher-cron",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 200
        mock_auto.assert_called_once()

    def test_autopreencher_cron_exception_returns_error(self, client):
        """Linhas 162-164: excepção → _api_error."""
        import config as cfg

        token = "test-auto-err"
        with (
            mock.patch.object(cfg, "CRON_API_TOKEN", token),
            mock.patch(
                "blueprints.api.routes.autopreencher_refeicoes_semanais",
                side_effect=ValueError("tabela em falta"),
            ),
        ):
            resp = client.post(
                "/api/autopreencher-cron",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 500
        data = resp.get_json()
        assert data["status"] == "error"
        assert "tabela em falta" in data["error"]

    def test_cron_bearer_prefix_required(self, client):
        """Sem prefixo 'Bearer ' → 403 mesmo com token correcto no header."""
        import config as cfg

        token = "test-prefix-token"
        with mock.patch.object(cfg, "CRON_API_TOKEN", token):
            resp = client.post(
                "/api/backup-cron",
                headers={"Authorization": token},  # sem 'Bearer '
            )
        assert resp.status_code == 403


# ── /api/vacuum-cron — manutenção da BD (VACUUM + PRAGMA optimize) ───────────


class TestVacuumCron:
    """Endpoint de manutenção mensal: reclama espaço fragmentado + analisa."""

    def test_vacuum_no_token_returns_403(self, client):
        resp = client.post("/api/vacuum-cron")
        assert resp.status_code == 403

    def test_vacuum_wrong_token_returns_403(self, client):
        import config as cfg

        with mock.patch.object(cfg, "CRON_API_TOKEN", "correct-vacuum-token"):
            resp = client.post(
                "/api/vacuum-cron",
                headers={"Authorization": "Bearer errado"},
            )
        assert resp.status_code == 403

    def test_vacuum_valid_token_returns_sizes(self, client):
        """Token válido → 200 com size_before, size_after, freed_bytes."""
        import config as cfg

        token = "test-vacuum-token-xyz"
        with mock.patch.object(cfg, "CRON_API_TOKEN", token):
            resp = client.post(
                "/api/vacuum-cron",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        # Campos obrigatórios da response
        for k in (
            "size_before_bytes",
            "size_after_bytes",
            "freed_bytes",
            "freed_pct",
            "optimize_ok",
        ):
            assert k in data, f"campo {k} em falta"
        # Tipos sensatos
        assert isinstance(data["size_before_bytes"], int)
        assert isinstance(data["size_after_bytes"], int)
        assert isinstance(data["optimize_ok"], bool)
        # VACUUM nunca deve aumentar o ficheiro
        assert data["size_after_bytes"] <= data["size_before_bytes"]
        assert data["freed_bytes"] >= 0

    def test_vacuum_freed_pct_is_zero_when_db_empty(self, client):
        """BD com size_before=0 → freed_pct=0 (sem ZeroDivisionError)."""
        import config as cfg

        token = "test-vacuum-token-empty"
        with (
            mock.patch.object(cfg, "CRON_API_TOKEN", token),
            mock.patch(
                "blueprints.api.routes.vacuum_database",
                return_value=(0, 0),
            ),
            mock.patch(
                "blueprints.api.routes.optimize_database",
                return_value=True,
            ),
        ):
            resp = client.post(
                "/api/vacuum-cron",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["freed_pct"] == 0.0
        assert data["freed_bytes"] == 0

    def test_vacuum_exception_returns_error(self, client):
        """Excepção em vacuum_database → 500 com _api_error."""
        import config as cfg

        token = "test-vacuum-token-err"
        with (
            mock.patch.object(cfg, "CRON_API_TOKEN", token),
            mock.patch(
                "blueprints.api.routes.vacuum_database",
                side_effect=RuntimeError("disco cheio"),
            ),
        ):
            resp = client.post(
                "/api/vacuum-cron",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 500
        data = resp.get_json()
        assert data["status"] == "error"
        assert "disco cheio" in data["error"]


# ── core.database — VACUUM / optimize unit tests ──────────────────────────────


class TestDatabaseMaintenance:
    """Testes directos das funções helper (sem passar pela rota)."""

    def test_db_file_size_returns_int(self, app):
        from core.database import db_file_size_bytes

        size = db_file_size_bytes()
        assert isinstance(size, int)
        assert size >= 0

    def test_vacuum_database_returns_tuple_of_ints(self, app):
        from core.database import vacuum_database

        before, after = vacuum_database()
        assert isinstance(before, int)
        assert isinstance(after, int)
        # VACUUM nunca aumenta o ficheiro
        assert after <= before

    def test_optimize_database_returns_bool(self, app):
        from core.database import optimize_database

        result = optimize_database()
        assert isinstance(result, bool)
        assert result is True  # PRAGMA optimize não deve falhar em BD válida


# ── /metrics — Prometheus text exposition ─────────────────────────────────────


class TestPrometheusMetrics:
    """Endpoint Prometheus em `/metrics`. Sem auth (pattern standard)."""

    def test_metrics_endpoint_returns_200(self, client):
        resp = client.get("/metrics")
        assert resp.status_code == 200

    def test_metrics_uses_prometheus_mimetype(self, client):
        """Content-Type deve ser text/plain com `version=0.0.4` (spec Prometheus)."""
        resp = client.get("/metrics")
        ct = resp.headers["Content-Type"]
        assert ct.startswith("text/plain")
        assert "version=0.0.4" in ct

    def test_metrics_includes_required_counters(self, client):
        """Mínimo de counters esperados no body."""
        # Faz pelo menos 1 request para haver dados
        client.get("/health")
        resp = client.get("/metrics")
        body = resp.data.decode("utf-8")
        # Counters obrigatórios
        for name in (
            "http_requests_total",
            "http_request_errors_total",
            "http_request_duration_milliseconds_total",
            "http_request_duration_milliseconds_avg",
            "db_size_bytes",
        ):
            assert f"# TYPE {name}" in body, f"falta # TYPE {name}"
            assert f"# HELP {name}" in body, f"falta # HELP {name}"

    def test_metrics_format_complies_with_prometheus_spec(self, client):
        """Cada métrica deve ter # HELP, # TYPE e linha de valor."""
        client.get("/health")
        resp = client.get("/metrics")
        lines = resp.data.decode("utf-8").splitlines()

        # Deve haver pelo menos 1 # HELP e 1 # TYPE
        helps = [le for le in lines if le.startswith("# HELP")]
        types = [le for le in lines if le.startswith("# TYPE")]
        assert len(helps) >= 4
        assert len(types) >= 4

        # Cada linha não-comentário deve ter formato `<name> <number>`
        # (ou `<name>{labels} <number>`)
        import re

        value_pattern = re.compile(
            r"^[a-zA-Z_][a-zA-Z0-9_]*(\{[^}]*\})?\s+[\d.eE+\-]+$"
        )
        non_comment = [le for le in lines if le and not le.startswith("#")]
        for line in non_comment:
            assert value_pattern.match(line), f"linha não-conforme: {line!r}"

    def test_metrics_per_route_label_present(self, client):
        """Após requests, deve haver linhas com label `route="..."`."""
        # Disparar requests para popular per-route metrics
        for _ in range(3):
            client.get("/health")

        resp = client.get("/metrics")
        body = resp.data.decode("utf-8")
        # Pelo menos 1 série per-route
        assert "http_requests_per_route_total{route=" in body

    def test_to_prometheus_text_handles_zero_requests(self):
        """Gerador funciona mesmo sem requests (avg=0, sem ZeroDivisionError)."""
        from core.middleware import (
            to_prometheus_text,
            _metrics,
            _metrics_lock,
            _route_metrics,
        )

        # Reset do estado in-memory para isolar este teste
        with _metrics_lock:
            _metrics["request_count"] = 0
            _metrics["error_count"] = 0
            _metrics["total_latency_ms"] = 0.0
            _route_metrics.clear()

        body = to_prometheus_text()
        assert "http_requests_total 0" in body
        assert "http_request_duration_milliseconds_avg 0" in body
        # Não levanta

    def test_prom_label_value_escaping(self):
        """Caracteres especiais em label values são escapados."""
        from core.middleware import _prom_escape_label_value

        assert _prom_escape_label_value('a"b') == 'a\\"b'
        assert _prom_escape_label_value("a\\b") == "a\\\\b"
        assert _prom_escape_label_value("a\nb") == "a\\nb"
