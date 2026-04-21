"""core/rate_limit — singleton Flask-Limiter para uso por blueprints.

O `limiter` é criado sem `app` e mais tarde ligado via `limiter.init_app(app)`
em `app.py`. Permite aos blueprints importar `limiter` sem circular import.

Em testes, `TESTING=True` desactiva limites através de `limiter.enabled = False`
em `tests/conftest.py` — evita falsos-positivos em suites que fazem muitos
pedidos sequenciais.

Limites aplicados (defesa em profundidade — complementa o brute-force
por conta e o IP rate-limit já existente em `auth_db`):

  /auth/login              10/minute por IP
  /api/*-cron              30/minute por IP (cada cron externa é 1×/dia)

Backend `memory://` chega para single-worker. Multi-worker/Redis via env:
  RATE_LIMIT_STORAGE_URI=redis://redis:6379/0
"""

from __future__ import annotations

import os

from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

# Singleton global — inicializado em `app.py` via `limiter.init_app(app)`.
limiter = Limiter(
    key_func=get_remote_address,
    storage_uri=os.environ.get("RATE_LIMIT_STORAGE_URI", "memory://"),
    default_limits=[],
    headers_enabled=True,
    strategy="fixed-window",
)
