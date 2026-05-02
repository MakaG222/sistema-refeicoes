# Arquitectura — Sistema de Refeições

## Stack

```
┌──────────────────────────────────────────────────────────┐
│  Browser (Jinja2 HTML + CSP strict, CSP-safe JS only)    │
└────────────────────┬─────────────────────────────────────┘
                     │  HTTP/S (X-Forwarded-Proto)
                     ▼
┌──────────────────────────────────────────────────────────┐
│  Reverse proxy (Traefik / Nginx) → TLS terminator        │
└────────────────────┬─────────────────────────────────────┘
                     │  HTTP
                     ▼
┌──────────────────────────────────────────────────────────┐
│  Gunicorn (sync workers)                                 │
└────────────────────┬─────────────────────────────────────┘
                     │
                     ▼
┌──────────────────────────────────────────────────────────┐
│  Flask app (app.py)                                      │
│  ├─ Middleware: request_id, UserContextFilter,           │
│  │             CSP, HTTPS-redirect, rate-limit           │
│  ├─ Blueprints por domínio:                              │
│  │     auth/   admin/   aluno/   cmd/                    │
│  │     operations/  reporting/   api/                    │
│  └─ Extensões: Flask-Limiter (rate-limit)                │
└────────────────────┬─────────────────────────────────────┘
                     │ sqlite3 (WAL + NORMAL sync)
                     ▼
┌──────────────────────────────────────────────────────────┐
│  SQLite (sistema.db)                                     │
│  + backups rolados (daily) para disco local e offsite    │
└──────────────────────────────────────────────────────────┘
```

## Decisões arquitecturais

### Persistência — SQLite WAL, não Postgres

A app corre tipicamente num único nó com <1 000 utilizadores e picos de
~30 escritas/min em deadline de marcação. SQLite em WAL mode oferece:

- Reads não bloqueiam writes (snapshot isolation).
- Um único ficheiro → backup é `cp`.
- Zero operacional: não há servidor separado, não há migrações de rede.
- Checkpoint automático mantém o `-wal` pequeno.

Se chegarmos a múltiplas instâncias activas, a migração para Postgres é
isolável ao módulo `core/database.py` (a camada de query já é parametrizada
e SQL-standard em 95% das rotas).

### Blueprints por domínio

Cada blueprint concentra rotas, templates e lógica de um perfil:

| Blueprint      | Perfil     | Responsabilidade                              |
|----------------|------------|-----------------------------------------------|
| `auth/`        | todos      | Login, logout, password change, reset         |
| `aluno/`       | aluno      | Marcar refeições, histórico, QR pessoal       |
| `admin/`       | admin      | Users, menus, calendário, auditoria, backup   |
| `cmd/`         | cmd        | Gerir ano, detenções, editar refeições alunos |
| `operations/`  | oficialdia | Painel diário, kiosk, presenças, licenças     |
| `reporting/`   | cozinha    | Totais diários, exportar CSV/PDF              |
| `api/`         | cron/ops   | /health, cron endpoints (token Bearer)        |

Dependências cruzadas passam por `core/` — nenhum blueprint importa de outro.

### Segurança — defesa em profundidade

1. **CSP estrito**: `default-src 'self'; script-src 'self'; style-src 'self'`.
   Zero `unsafe-inline`, zero `unsafe-eval`. Toast/theme/shortcuts em
   ficheiros `.js` próprios, payloads em `<script type="application/json">`.
2. **CSRF**: token por sessão em todos os POSTs (`utils/helpers.csrf_input`).
3. **Sessões**: `HttpOnly`, `SameSite=Lax`, `Secure` em produção,
   `PERMANENT_SESSION_LIFETIME=60min` (timeout de inactividade).
4. **Brute-force por conta**: 10 falhas → lockout 15 min (`login_eventos`).
5. **Rate-limit HTTP**: Flask-Limiter — 10 req/min em `/auth/login`,
   30 req/min em `/api/*` cron (defesa adicional mesmo com token).
6. **HTTPS forçado em produção**: middleware 301 de http→https (respeita
   `X-Forwarded-Proto` vindo do proxy) + **HSTS** (`max-age=31536000;
   includeSubDomains`) só em produção. Sem `preload` por defeito (commitment
   permanente — opt-in só após validar 6+ meses estável).
7. **Headers de segurança**: `X-Frame-Options: SAMEORIGIN`,
   `X-Content-Type-Options: nosniff`, `Referrer-Policy:
   strict-origin-when-cross-origin`. Todos via `setdefault` em
   `core/middleware.py`.
8. **Password reset**: admin gera código `secrets.token_urlsafe(8)` válido
   24h, single-use, constant-time compare, força change-password no próximo
   login.
9. **Auditoria**: `admin_audit_log` regista todas as acções administrativas
   + `login_eventos` regista tentativas (sucesso/falha) para análise.
10. **Error tracking** (opt-in): Sentry com `send_default_pii=False` +
    `before_send` scrubber para passwords, NII, csrf, Authorization,
    cookies. Sem `SENTRY_DSN` é no-op completo. Ver `config._scrub_event`.

### Observabilidade

- **Logging JSON estruturado em produção** (`config.JsonFormatter`):
  `ts`, `level`, `logger`, `msg`, `request_id`, `user_nii`, `user_role`.
  `stdout` → `docker logs` / Railway logs.
- **Health check** `/health` agrega BD (`SELECT 1`), backup age, disco livre.
  Retorna JSON e status 200 (ok) / 503 (degraded).
- **Métricas in-memory** em `core/middleware.get_metrics()` — contador de
  requests, errors, tempo médio. Expostas em painel admin.

### Migrações

Framework versionado em `core/migrations.py`:

```python
MIGRATIONS = [
    ("001_initial_schema", _migration_001),
    ("002_add_login_eventos", _migration_002),
    ...
    ("008_add_reset_code", _add_reset_code),
]
```

Cada migração idempotente (verifica `PRAGMA table_info` antes de `ALTER`).
A tabela `_migracoes` regista o que já correu. `ensure_schema()` aplica
só as pendentes. Sem rollbacks automáticos — cada mudança é aditiva.

### Testing

- `pytest` + `coverage` — alvo ≥90% (actual 90%).
- `tests/conftest.py` usa BD temporária + desactiva rate-limit (reactivável
  em testes específicos).
- Testes por domínio: `test_admin_*`, `test_aluno_*`, `test_auth_*`,
  `test_api_*`, `test_hardening.py`, `test_upgrades.py`.

### CI

```
ruff check + ruff format --check
bandit -ll (só medium/high)
pip-audit
coverage run -m pytest && coverage --fail-under=90
```

Corre em pre-commit hook local e em GitHub Actions. Todos devem passar.

## Fluxo de request típico — marcar uma refeição

```
POST /aluno/editar/2026-04-22
  Content-Type: application/x-www-form-urlencoded
  Cookie: session=...; csrf_token=...
  Body: pa=1&al=1&jt=0&csrf_token=...

  │
  ▼
[middleware] request_id injectado em g
[middleware] UserContextFilter lê session["user"] p/ logs
[middleware] HTTPS redirect (skip se dev)
[limiter]   default limits (não aplica a esta rota)
  │
  ▼
[aluno.routes.editar]
  ├─ valida CSRF
  ├─ verifica login (@require_aluno)
  ├─ rate-limit por-sessão (30 ops/min)
  ├─ core.meals.salvar_refeicao(nii, data, pa, al, jt)
  │    └─ core.database.db() → UPSERT em refeicoes_decisoes
  │    └─ regista em meal_log (quem editou o quê)
  └─ flash "ok" + 302 redirect
  │
  ▼
[response] Set-Cookie rotativo, CSP headers, HSTS
```

## Estrutura de directorias

```
/
├── app.py                    Flask app factory + init Limiter
├── config.py                 Carrega ENV, valida, configura logging
├── requirements.txt          Dependências Python
├── requirements-dev.txt      Pytest, ruff, bandit, pip-audit
├── pyproject.toml            Metadata + ruff config
│
├── blueprints/               Rotas por domínio
│   ├── admin/
│   ├── aluno/
│   ├── api/
│   ├── auth/
│   ├── cmd/
│   ├── operations/
│   └── reporting/
│
├── core/                     Domínio + infra
│   ├── auth_db.py            Login + reset_code + lockout
│   ├── database.py           Context manager db() + WAL setup
│   ├── migrations.py         Schema versionado
│   ├── middleware.py         request_id, UserContext, métricas
│   ├── rate_limit.py         Limiter singleton
│   ├── meals.py              Lógica de decisões refeição
│   ├── audit.py              Regista e consulta admin_audit_log
│   ├── backup.py             Backups rolados + offsite
│   ├── notifications.py      Email/Webhook pluggable
│   └── ...
│
├── utils/                    Helpers sem estado
│   ├── auth.py               Decorators @require_*
│   ├── helpers.py            csrf_input, flash_toast
│   ├── passwords.py          Hash, verify, reset
│   └── validators.py
│
├── static/
│   ├── css/ (app, theme, toasts)
│   └── js/  (toasts, theme, shortcuts, meal-editor, ...)
│
├── templates/                Jinja2
│   ├── base.html             Layout + CSP scripts
│   ├── _macros.html          empty_state, pagination_nav
│   ├── _shortcuts_help.html  <dialog> com atalhos
│   ├── admin/ aluno/ auth/ cmd/ operations/ reporting/
│   └── errors/               400, 403, 404, 429, 500, 503
│
├── tests/                    pytest — 864 testes, 90% cov
├── docs/                     Este directório
│   ├── ARCHITECTURE.md       Este ficheiro
│   ├── API.md                Endpoints JSON
│   ├── RUNBOOK.md            Ops
│   ├── USER_MANUAL.md        Por perfil
│   └── CONTRIBUTING.md
└── sistema.db                SQLite + sistema.db-wal, sistema.db-shm
```

## Próximos passos (v1.2+)

Ver `CHANGELOG.md` secção "Unreleased / Roadmap":

- MFA/TOTP
- i18n
- OpenAPI/Swagger em `/api/*`
- PWA offline sync com IndexedDB
- Postgres opcional via `DATABASE_URL`
