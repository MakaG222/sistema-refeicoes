"""
config.py — Configuração centralizada do Sistema de Refeições
=============================================================
Importar em app.py com:  from config import Config, is_production
"""

import os
import logging
import secrets

# ── Ambiente ────────────────────────────────────────────────────────────────
ENV = os.environ.get("ENV", "development").lower()
is_production: bool = ENV == "production"

# ── Chave secreta ────────────────────────────────────────────────────────────
SECRET_KEY: str = os.environ.get("SECRET_KEY", "")
if is_production and not SECRET_KEY:
    raise RuntimeError(
        "SECRET_KEY não definida! "
        "Define a variável de ambiente SECRET_KEY antes de arrancar em produção."
    )
SECRET_KEY = SECRET_KEY or secrets.token_hex(32)

# ── Token de cron ────────────────────────────────────────────────────────────
# Gerar com: python -c "import secrets; print(secrets.token_urlsafe(32))"
CRON_API_TOKEN: str = os.environ.get("CRON_API_TOKEN", "")

# ── Negócio ──────────────────────────────────────────────────────────────────
DIAS_ANTECEDENCIA: int = int(os.environ.get("DIAS_ANTECEDENCIA", "15"))
"""Alunos podem marcar refeições até N dias à frente (inclui fins-de-semana)."""

# ── Regras de licença por ano ────────────────────────────────────────────────
# Cada entrada: (max_dias_uteis_seg_qui, dias_permitidos)
# dias_permitidos: lista de weekday indexes (0=seg ... 6=dom)
LICENCA_REGRAS_ANO: dict[int, dict] = {
    1: {"max_dias_uteis": 1, "dias_permitidos": [2, 4, 5, 6]},  # só quarta + fds
    2: {"max_dias_uteis": 2, "dias_permitidos": [0, 1, 2, 3, 4, 5, 6]},
    3: {"max_dias_uteis": 3, "dias_permitidos": [0, 1, 2, 3, 4, 5, 6]},
}
LICENCA_REGRAS_ANO_DEFAULT: dict = {
    "max_dias_uteis": 4,
    "dias_permitidos": [0, 1, 2, 3, 4, 5, 6],
}
"""4º ano e acima: todos os dias. NI com prefixo '7' também usa este default."""

# ── Horários das refeições (para ausências inteligentes) ────────────────────
# Formato: (hora_inicio, hora_fim) — a ausência afeta a refeição se houver
# sobreposição com esta janela.
REFEICAO_HORARIOS: dict[str, tuple[str, str]] = {
    "pequeno_almoco": ("07:00", "09:30"),
    "almoco": ("12:00", "14:00"),
    "lanche": ("16:00", "17:30"),
    "jantar": ("19:00", "21:00"),
}

# ── Servidor ─────────────────────────────────────────────────────────────────
PORT: int = int(os.environ.get("PORT", "8080"))
DEBUG: bool = os.environ.get("DEBUG", "false").lower() == "true"

# ── Observabilidade (Sentry) ─────────────────────────────────────────────────
# Vazio = desligado (no-op completo). Sem DSN não há overhead, conexões, nada.
SENTRY_DSN: str = os.environ.get("SENTRY_DSN", "").strip()
# Sample rate de tracing: 0.0 = só erros, 1.0 = tudo (caro). Recomendado 0.05-0.10.
SENTRY_TRACES_SAMPLE_RATE: float = float(
    os.environ.get("SENTRY_TRACES_SAMPLE_RATE", "0.0")
)
# Release (commit SHA). Permite ver "este erro só aparece desde a release X".
SENTRY_RELEASE: str = os.environ.get("SENTRY_RELEASE", "").strip()


# ── Flask config dict ────────────────────────────────────────────────────────
class Config:
    """Classe de configuração para app.config.from_object(Config)."""

    SECRET_KEY = SECRET_KEY
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = is_production  # HTTPS obrigatório em produção
    SESSION_PERMANENT = True  # controlada via PERMANENT_SESSION_LIFETIME
    PERMANENT_SESSION_LIFETIME = 600  # 10 min de inatividade → sessão expira
    PREFERRED_URL_SCHEME = "https" if is_production else "http"

    # JSON
    JSON_SORT_KEYS = False


# ── Logging ──────────────────────────────────────────────────────────────────
def configure_logging(flask_app) -> None:
    """Configura o logger da app Flask — JSON em produção, legível em dev."""
    import json
    import sys

    class JsonFormatter(logging.Formatter):
        """Formatter que produz uma linha JSON por log entry."""

        def format(self, record: logging.LogRecord) -> str:
            rid = getattr(record, "request_id", "-")
            user_nii = getattr(record, "user_nii", "-")
            user_role = getattr(record, "user_role", "-")
            entry = {
                "ts": self.formatTime(record, self.datefmt),
                "level": record.levelname,
                "logger": record.name,
                "msg": record.getMessage(),
                "rid": rid,
                "user_nii": None if user_nii == "-" else user_nii,
                "user_role": None if user_role == "-" else user_role,
            }
            if record.exc_info and record.exc_info[0] is not None:
                entry["exception"] = self.formatException(record.exc_info)
            return json.dumps(entry, ensure_ascii=False)

    class DevFormatter(logging.Formatter):
        """Formatter de dev que inclui request_id se disponível."""

        def format(self, record: logging.LogRecord) -> str:
            if not hasattr(record, "request_id"):
                record.request_id = "-"  # type: ignore[attr-defined]
            if not hasattr(record, "user_nii"):
                record.user_nii = "-"  # type: ignore[attr-defined]
            if not hasattr(record, "user_role"):
                record.user_role = "-"  # type: ignore[attr-defined]
            return super().format(record)

    handler = logging.StreamHandler(sys.stdout)
    if is_production:
        handler.setFormatter(JsonFormatter(datefmt="%Y-%m-%dT%H:%M:%S"))
    else:
        handler.setFormatter(
            DevFormatter(
                "%(asctime)s %(levelname)s [%(name)s] [%(request_id)s]: %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
    flask_app.logger.addHandler(handler)
    flask_app.logger.setLevel(logging.INFO)
    logging.getLogger("sqlite3").setLevel(logging.WARNING)


# ── Sentry (error tracking) ──────────────────────────────────────────────────
# Scrub fields que podem conter dados pessoais sensíveis ou credenciais. Aplica-se
# a request.form, request.cookies, headers e session. Estes nunca chegam ao Sentry.
_SENTRY_SCRUB_FIELDS = frozenset(
    {
        # Credenciais
        "password",
        "pw",
        "old_password",
        "new_password",
        "csrf_token",
        "_csrf_token",
        # Identificadores pessoais (RGPD — alunos são menores)
        "nii",
        "ni",
        "Palavra_chave",
        "reset_code",
        # Tokens secretos
        "Authorization",
        "authorization",
        "cookie",
        "Cookie",
        "set-cookie",
        "Set-Cookie",
    }
)


def _scrub_event(event, _hint):
    """before_send hook: remove campos sensíveis de requests/sessões/extras
    antes de enviar para o Sentry. Defesa em profundidade — mesmo que
    `send_default_pii=False` esteja activo.
    """
    try:
        # Scrub form/query/headers do request
        req = event.get("request") or {}
        for key in ("data", "headers", "cookies", "query_string"):
            v = req.get(key)
            if isinstance(v, dict):
                for f in list(v):
                    if f in _SENTRY_SCRUB_FIELDS or f.lower() in _SENTRY_SCRUB_FIELDS:
                        v[f] = "[Filtered]"
        # Scrub extras top-level que possam ter sido injectados
        extras = event.get("extra") or {}
        for f in list(extras):
            if f in _SENTRY_SCRUB_FIELDS or f.lower() in _SENTRY_SCRUB_FIELDS:
                extras[f] = "[Filtered]"
    except Exception:
        # Nunca falhar o envio por causa do scrub — pior que dados a mais
        # é perder o evento por completo.
        pass
    return event


def configure_sentry() -> bool:
    """Inicializa Sentry se SENTRY_DSN estiver definido. No-op caso contrário.

    Returns:
        True se o Sentry foi activado, False se SENTRY_DSN está vazio.

    Defaults seguros:
      - send_default_pii=False (não envia IP, cookies, headers de auth)
      - traces_sample_rate=0.0 (só erros, sem performance overhead)
      - before_send scruba campos sensíveis adicionais
      - environment marcado consoante ENV
    """
    if not SENTRY_DSN:
        return False
    try:
        import sentry_sdk
        from sentry_sdk.integrations.flask import FlaskIntegration
        from sentry_sdk.integrations.logging import LoggingIntegration

        sentry_sdk.init(
            dsn=SENTRY_DSN,
            environment=ENV,
            release=SENTRY_RELEASE or None,
            traces_sample_rate=SENTRY_TRACES_SAMPLE_RATE,
            send_default_pii=False,
            integrations=[
                FlaskIntegration(),
                # ERROR e acima vão como events; INFO como breadcrumbs.
                LoggingIntegration(level=logging.INFO, event_level=logging.ERROR),
            ],
            before_send=_scrub_event,
        )
        return True
    except Exception as exc:  # noqa: BLE001 — Sentry nunca deve crashar a app
        logging.getLogger(__name__).warning(
            "Sentry init falhou (continuando sem error tracking): %s", exc
        )
        return False


# ── Startup info ─────────────────────────────────────────────────────────────
def print_startup_banner(db_path: str) -> None:
    """Imprime banner de arranque com informação de configuração."""
    sep = "=" * 60
    print(sep)
    print("⚓ Escola Naval — Sistema de Refeições")
    print(f"  Acede em:  http://localhost:{PORT}")
    print(f"  BD:        {db_path}")
    print(f"  ENV:       {ENV}")
    if not is_production:
        print("  ⚠️  MODO DESENVOLVIMENTO — contas de teste ativas")
    if not CRON_API_TOKEN:
        print("  ⚠️  CRON_API_TOKEN não definido — endpoints de cron desprotegidos")
    print(f"  Sentry:    {'activo' if SENTRY_DSN else 'desligado (sem SENTRY_DSN)'}")
    print(sep)
    print("  Variáveis de ambiente necessárias em produção:")
    print("    SECRET_KEY=<random 32+ chars>")
    print("    CRON_API_TOKEN=<random 32+ chars>")
    print("    ENV=production")
    print("    DB_PATH=/mnt/data/sistema.db  # volume persistente (Railway)")
    print("  Variáveis opcionais:")
    print("    SENTRY_DSN=<url do projecto>           # error tracking")
    print("    SENTRY_TRACES_SAMPLE_RATE=0.05         # opcional, perf tracing")
    print("    SENTRY_RELEASE=$GIT_SHA                # opcional, versionamento")
    print(sep)
