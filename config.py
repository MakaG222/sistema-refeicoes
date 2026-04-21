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
    print(sep)
    print("  Variáveis de ambiente necessárias em produção:")
    print("    SECRET_KEY=<random 32+ chars>")
    print("    CRON_API_TOKEN=<random 32+ chars>")
    print("    ENV=production")
    print("    DB_PATH=/mnt/data/sistema.db  # volume persistente (Railway)")
    print(sep)
