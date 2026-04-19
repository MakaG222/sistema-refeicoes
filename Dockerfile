# syntax=docker/dockerfile:1.7
# ─────────────────────────────────────────────────────────────────────────────
# Sistema de Refeições — imagem de produção
# ─────────────────────────────────────────────────────────────────────────────
# Build:    docker build -t sistema-refeicoes .
# Run:      docker run -p 8080:8080 \
#               -e SECRET_KEY=$(python -c 'import secrets;print(secrets.token_hex(32))') \
#               -e CRON_API_TOKEN=$(python -c 'import secrets;print(secrets.token_urlsafe(32))') \
#               -e ENV=production \
#               -v sr-data:/data \
#               sistema-refeicoes
# ─────────────────────────────────────────────────────────────────────────────

FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8080 \
    DB_PATH=/data/sistema.db

WORKDIR /app

# Dependências do sistema mínimas (sqlite3 CLI para debug; tini para PID 1).
RUN apt-get update \
    && apt-get install -y --no-install-recommends sqlite3 tini \
    && rm -rf /var/lib/apt/lists/*

# Instalar deps Python primeiro para maximizar cache de layers.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt gunicorn>=21.0

# Copiar código.
COPY . .

# Utilizador não-root + volume persistente para a BD.
RUN useradd --system --uid 1000 --home /app sr \
    && mkdir -p /data \
    && chown -R sr:sr /app /data

USER sr
VOLUME ["/data"]
EXPOSE 8080

# Healthcheck bate no endpoint público /health.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request,sys; \
        sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/health',timeout=3).status==200 else 1)"

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:${PORT} --workers 2 --timeout 60 --access-logfile - --error-logfile - app:app"]
