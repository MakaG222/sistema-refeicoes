"""
config.py — Configuração centralizada do Sistema de Refeições
=============================================================
Importar em app.py com:  from config import Config, is_production
"""

import os
import secrets
import logging

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
SECRET_KEY = SECRET_KEY or "escola-naval-dev-insecure-key-change-me"

# ── Token de cron ────────────────────────────────────────────────────────────
# Gerar com: python -c "import secrets; print(secrets.token_urlsafe(32))"
CRON_API_TOKEN: str = os.environ.get("CRON_API_TOKEN", "")

# ── Negócio ──────────────────────────────────────────────────────────────────
DIAS_ANTECEDENCIA: int = int(os.environ.get("DIAS_ANTECEDENCIA", "15"))
"""Alunos podem marcar refeições até N dias à frente (inclui fins-de-semana)."""

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
    PREFERRED_URL_SCHEME = "https" if is_production else "http"

    # JSON
    JSON_SORT_KEYS = False

# ── Logging ──────────────────────────────────────────────────────────────────
def configure_logging(flask_app) -> None:
    """Configura o logger da app Flask para stdout com formato legível."""
    import sys
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s [app]: %(message)s")
    )
    flask_app.logger.addHandler(handler)
    flask_app.logger.setLevel(logging.INFO)

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
