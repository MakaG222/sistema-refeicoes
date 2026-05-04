# Changelog

Todas as alterações notáveis ao Sistema de Refeições são documentadas aqui.

Formato baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/),
versionamento segue [Semantic Versioning](https://semver.org/lang/pt-BR/).

## [1.2.0] — 2026-05-04

Hardening pré-produção: pré-UAT bug fixes, error tracking opt-in, segurança
HTTPS, manutenção de BD, redesenho do check-in QR, e exportação iCalendar
para alunos. Sem breaking changes — upgrade directo desde 1.1.0.

### Added — Aluno

- **Exportação para iCalendar** (`/aluno/refeicoes.ics`): cada refeição
  marcada vira um VEVENT no horário correspondente. Importável em Google
  Calendar, Apple Calendar e Outlook. UIDs estáveis → re-import actualiza
  events em vez de duplicar. Botão "📅 Exportar para Calendar (30d)" no
  perfil. Janela configurável via `?days=N` (1–90). Sem dependência externa
  (gerador hand-rolled em `utils/ical.py`, RFC 5545).

### Added — Segurança

- **HSTS em produção** (`Strict-Transport-Security: max-age=31536000;
  includeSubDomains`). Sem `preload` por defeito (commitment permanente —
  opt-in só após validar 6+ meses estáveis em hstspreload.org). Em dev o
  header não é enviado.
- **Política de password reforçada** (`utils/passwords.py`): blacklist
  de 28 passwords comuns (password, qwerty123, admin123, marinha1,
  escolanaval1, …); rejeita `password == NII` e `password == NI`;
  `_alterar_password` e `_criar_utilizador` propagam NI ao validador.
- **QR rotativo URL-based para check-in**: oficial-dia mostra um QR
  que codifica uma URL `/checkin/<token>` com TTL curto (15s) e
  anti-replay (`UNIQUE` em `checkin_log`). Aluno scaneia com a câmara
  do telemóvel — sem necessidade de app dedicada. Substitui o fluxo
  anterior em que o aluno mostrava o QR estático ao kiosk.
- **Rollback de `checkin_log` em falha**: se `registar_*_presenca`
  levantar excepção depois do INSERT em `checkin_log`, o INSERT é
  revertido para manter o invariante "linha em log ↔ presença efectiva".
- **IP correcto via `_client_ip()`** em `/checkin/<token>`: usa
  `request.access_route[0]` em vez do header bruto `X-Forwarded-For`
  (evita gravar a chain inteira de proxies como IP do cliente).

### Added — Observability

- **Sentry error tracking opt-in via `SENTRY_DSN`**. RGPD-friendly por
  defeito (alunos são menores): `send_default_pii=False` + `before_send`
  hook (`config._scrub_event`) substitui campos sensíveis por `[Filtered]`
  — credenciais (password, csrf_token), identificadores (NII, NI,
  reset_code) e tokens (Authorization, Cookie). Sem `SENTRY_DSN` é no-op
  completo (zero overhead, zero conexões). Env vars adicionais opcionais:
  `SENTRY_TRACES_SAMPLE_RATE`, `SENTRY_RELEASE`.

### Added — Operations

- **`flask vacuum` CLI command** para manutenção mensal da BD:
  `wal_checkpoint(TRUNCATE)` → `VACUUM` → `PRAGMA optimize`. Reclama
  espaço de páginas livres que `wal_checkpoint` periódico não devolve
  ao SO. Output mostra tamanho before/after, bytes libertados e %.
  Schedule recomendado: 1× por mês via cron do SO em janela de baixo
  tráfego.
- **`scripts/seed_demo.py`**: popular a BD com dados realistas para
  ambientes de UAT/staging (200 alunos + oficiais/cmd/admin/cozinha,
  8 semanas de refeições, 4 semanas de ausências, dados de calendário).
- **`docs/DEPLOYMENT_CHECKLIST.md`**: lista de verificação obrigatória
  antes de promover para produção (env vars, segurança, backup,
  smoke tests, rollback plan).
- **Post-restore integrity tests** (`test_restore_backup_post_restore_integrity`):
  após `restore_backup()` bem sucedido, valida `PRAGMA quick_check`,
  `foreign_key_check`, `integrity_check`, contagens de tabelas, ≥1 admin
  presente, e índices preservados. Apanha o pior cenário (restore que
  diz "ok" mas deixou referencial integrity partida).

### Changed — UAT bug fixes

- **Cutoff específico para lanche** (até 10h do dia de prazo): novo
  `CUTOFF_LANCHE_HORA=10`; `refeicao_editavel(d, tipo='lanche')` desliza
  prazo de `d-2 00:00` para `d-2 10:00` (margem da manhã). Mantém 48h
  de antecedência.
- **Dark mode**: bug fix de consistência (toggle não persistia em
  algumas páginas).
- **Companhias** (gestão admin): feedback visual após acções (criar/
  editar/eliminar) — antes era silencioso.

### Removed — Bloat especulativo

Adicionado e removido no mesmo release após auditoria honesta — features
sem utilizador real ou infra para as suportar:

- **Prometheus `/metrics` endpoint**: removido. Sem Prometheus infra,
  e `/health/metrics` (JSON) já cobre o caso real (curl manual,
  dashboards simples).
- **Slow query log via `SLOW_QUERY_THRESHOLD_MS`**: removido. Defensivo
  contra um problema hipotético; quando precisar de diagnóstico
  ad-hoc, `set_trace_callback` numa branch temporária é trivial.
- **`POST /api/vacuum-cron`**: convertido para `flask vacuum` CLI
  (manutenção interna não precisa de superfície HTTP com token,
  rate-limit, error handler).

### Migrations

- Sem migrações de schema novas. `008_add_reset_code` (de v1.1.0) é a
  última.

### Dependencies

- Nova: `sentry-sdk[flask]>=2.0,<3.0` (totalmente opcional — só activa
  com `SENTRY_DSN` definido; sem o env var é zero overhead).

### Tests & Coverage

- 864 → **956 testes** (+92), coverage ~90%.
- Novos suites: `test_sentry.py` (12), `test_ical.py` (26),
  TestHSTSHeader (3), TestVacuumCli (3), TestDatabaseMaintenance (3),
  test_restore_backup_post_restore_integrity (1), checkin rollback
  regression test, e expansões em test_security/test_api_routes.

### Stats

```
43 files changed, 3563 insertions(+), 73 deletions(-)
11 commits desde v1.1.0
```

---

## [1.1.0] — 2026-04-21

Polish de produção: UX/a11y, hardening de segurança, observabilidade e
documentação. Sem breaking changes — upgrade directo desde 1.0.0.

### Added — UX & Accessibility

- **Toast system CSP-safe**: mensagens de feedback in-app com animações,
  dismiss por clique e auto-hide 5s. Reaproveita `<div aria-live="polite">`
  existente. Payload JSON via `<script type="application/json">` (sem
  inline JS).
- **Dark mode**: toggle sol/lua no nav, persiste em `localStorage`,
  respeita `prefers-color-scheme` em primeira visita. CSS custom
  properties via `[data-theme="dark"]` + `@media`.
- **Empty states**: macro `empty_state(icon, title, hint, cta_url, cta_text)`
  em `templates/_macros.html`, aplicada em histórico de aluno, lista de
  users, auditoria e painel CMD.
- **Skip-link** para acessibilidade: primeiro focusable em `base.html`,
  salta directamente para `#main`.
- **Keyboard shortcuts** globais: `?` abre overlay de ajuda, `Ctrl+S`
  submete formulário principal, `Ctrl+P` imprime, `←/→` navega dias no
  meal-editor. Indicadores `<kbd>` nos botões relevantes.
- **`aria-expanded` / `aria-pressed`** nos toggles de estufa e filtros
  admin. Conversão de `<div role="button">` para `<button>` onde possível.

### Added — Hardening & Performance

- **Password reset por admin** (`reset_code`): código single-use de 24h
  gerado com `secrets.token_urlsafe(8)`, comparação constant-time, força
  `must_change_password=1` no próximo login. Botão **🔐 Gerar código de
  reset** em `/admin/utilizadores?edit_user=<NII>`.
- **Cleanup automático** via `POST /api/unlock-expired` (token Bearer):
  apaga `login_eventos` failures >24h, limpa `reset_code` expirados,
  remove `locked_until` vencidos. Schedule recomendado: diário 03:00.
- **Rate-limit HTTP-layer**: Flask-Limiter em `POST /auth/login` (10/min)
  e em `/api/*` cron (30/min). Handler 429 retorna JSON para `/api/*`
  e template `errors/429.html` para UI.
- **Paginação consistente** em listagens admin: `/admin/utilizadores`,
  `/admin/auditoria`, `/admin/log` todos aceitam `?page=N`. Macro
  `pagination_nav(page, total_pages, qs)` preserva query string.

### Added — Observability

- **User context nos logs**: `UserContextFilter` injecta `user_nii` e
  `user_role` em todos os log records. `JsonFormatter` e `DevFormatter`
  emitem os novos campos. Útil para `grep` por NII em produção.

### Added — Documentation

- `docs/ARCHITECTURE.md` — diagrama de stack, decisões de design, fluxo
  de request.
- `docs/API.md` — referência dos 5 endpoints JSON (`/health` + 4 cron),
  exemplos `curl`, rate limits, cron wrapper.
- `docs/RUNBOOK.md` — procedimentos operacionais: restart, logs, backup
  & restore, rotação de tokens, erros comuns, verificação pós-deploy.
- `docs/USER_MANUAL.md` — guia por perfil (aluno, oficial-dia, cozinha,
  CMD, admin) + atalhos.
- `docs/CONTRIBUTING.md` — setup, branch naming, conventional commits,
  CI local, migrações de schema.

### Changed

- `core/audit.py` — nova `query_admin_audit_paged(...)` (3-tuple) em
  paralelo com `query_admin_audit` legacy (2-tuple). Sem breaking change.
- `templates/admin/auditoria.html`, `log.html`, `utilizadores.html`
  passam a usar o macro `pagination_nav`.
- `core/middleware.py` — handler 429 global para `/api/*` vs UI.
- `config.py` — `JsonFormatter` e `DevFormatter` emitem `user_nii` e
  `user_role`.

### Security

- **Single-use token** para reset password (previne reuse attacks).
- **Constant-time compare** em `consume_reset_code` (defesa contra
  timing attacks).
- **Rate-limit HTTP-layer** cobre gaps do rate-limit por-conta: IPs a
  varrer múltiplas contas são travados antes de chegar à lógica de
  lockout por NII.
- **Cleanup automático** de `login_eventos` — evita crescimento infinito
  da tabela e reduz superfície de retenção de dados pessoais.

### Migrations

- `008_add_reset_code` — adiciona `utilizadores.reset_code TEXT` e
  `utilizadores.reset_expires TEXT`. Idempotente.

### Dependencies

- Novo: `Flask-Limiter>=3.5,<4.0`.

### Tests & Coverage

- +31 testes novos (`test_upgrades.py` +12, `test_hardening.py` +19).
- Total: **864 testes** (era 830), coverage **90%**.

### Stats

```
21 files changed, 840 insertions(+), 35 deletions(-)   (PR B)
... (PR A também com impacto significativo em UX/a11y)
```

---

## [1.0.0] — 2026-03-22

Primeira versão estável para produção na Escola Naval.

### Funcionalidades

- **Gestão de refeições**: alunos marcam PA, lanche, almoço (Normal/Veg/Dieta), jantar (Normal/Veg/Dieta) até 15 dias à frente
- **Opção estufa**: almoço e jantar com opção de refeição em estufa
- **Licença fim-de-semana**: marcação/cancelamento com cancelamento automático de refeições
- **Ausências prolongadas**: registo de períodos de ausência com bloqueio automático de refeições
- **Detenções**: bloqueio de "sai unidade" e licenças para alunos detidos
- **Calendário operacional**: feriados, exercícios, fins-de-semana — com impacto nas refeições disponíveis
- **Painel de operações**: dashboard diário para cozinha, oficial de dia, e admin
- **Controlo de presenças**: marcação por NI com registo em tempo real
- **Gestão de excepções**: override de refeições individuais por dia
- **Menus diários**: gestão de menus e capacidade de refeições
- **Relatórios**: dashboard semanal, calendário mensal, exportação CSV/XLSX
- **Importação CSV**: carga em massa de utilizadores via ficheiro CSV
- **Gestão de companhias/turmas**: organização de alunos por ano e turma
- **Auditoria**: log de todas as acções administrativas e alterações de refeições

### Segurança

- Autenticação por NII + palavra-chave com hash (pbkdf2/scrypt)
- Rate limiting por IP (20 tentativas/15 min) e por conta (5 tentativas → bloqueio 15 min)
- CSRF tokens em todos os formulários
- Content-Security-Policy sem `unsafe-inline` (scripts e estilos externos)
- Headers de segurança: X-Content-Type-Options, X-Frame-Options, Referrer-Policy
- SQL parameterizado + allowlist de colunas dinâmicas
- Validação de inputs com mensagens específicas
- Sessões com HttpOnly, SameSite=Lax, Secure (produção)
- Bandit CI com zero skips globais

### Perfis de acesso

- **admin**: acesso total ao sistema
- **cmd** (comandante): gestão do seu ano — alunos, ausências, detenções
- **oficialdia**: operações diárias — painel, excepções, licenças, presenças
- **cozinha**: painel, menus, relatórios
- **aluno**: refeições, ausências, perfil próprio

### Infraestrutura

- Flask 3.x + SQLite (WAL mode) + Gunicorn
- Migrações versionadas com tabela de controlo (`_migracoes`)
- Backup automático diário com retenção de 30 dias
- Restauro via CLI (`flask restore`)
- Logging estruturado (JSON em produção)
- Request ID tracking (X-Request-ID)
- Métricas in-memory (latência, contagens, erros por rota)
- Health check endpoint (`/health`)
- CI: pytest (583 testes, 92% cobertura), ruff, bandit, pip-audit

### Documentação

- README.md: arquitetura, setup, deploy, segurança
- OPERATIONS.md: instalação, backup, restauro, atualização, troubleshooting
- PLAN.md: plano de desenvolvimento e estado do projecto
