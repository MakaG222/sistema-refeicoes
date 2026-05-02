# Runbook

Procedimentos operacionais comuns. Para arquitectura, ver
[ARCHITECTURE.md](ARCHITECTURE.md); para endpoints JSON, [API.md](API.md).

## Índice

1. [Restart](#restart)
2. [Logs](#logs)
3. [Sentry (error tracking)](#sentry-error-tracking)
4. [Backup e restore](#backup-e-restore)
5. [Rotação de tokens e segredos](#rotação-de-tokens-e-segredos)
6. [Cron jobs](#cron-jobs)
7. [Erros comuns](#erros-comuns)
8. [Verificação pós-deploy](#verificação-pós-deploy)

---

## Restart

### Docker Compose (produção recomendada)

```bash
docker compose restart app
# ou zero-downtime:
docker compose up -d --no-deps --build app
```

### Railway

```bash
railway up
# ou via UI: Deployments → Redeploy
```

### systemd (bare metal)

```bash
sudo systemctl restart refeicoes
sudo systemctl status refeicoes
```

### Dev local

```bash
source .venv/bin/activate
flask run --debug
```

---

## Logs

### Produção (JSON via stdout)

```bash
# Docker
docker compose logs -f app --tail=200

# Railway
railway logs

# systemd
journalctl -u refeicoes -f -n 200
```

Filtrar por utilizador:
```bash
docker compose logs app | jq 'select(.user_nii == "12345")'
```

Filtrar por request_id (debug de request específico):
```bash
docker compose logs app | jq 'select(.request_id == "abc-def-...")'
```

### Formato

Cada linha é um JSON com campos:
- `ts`, `level`, `logger`, `msg` — padrão
- `request_id` — UUID por request HTTP (gerado em `core/middleware.py`)
- `user_nii`, `user_role` — `null` para anónimo/cron, NII do utilizador autenticado caso contrário

---

## Sentry (error tracking)

**Opcional.** Sem `SENTRY_DSN` definido, a feature é completamente no-op
(zero overhead, zero conexões — `configure_sentry()` devolve `False`
imediatamente).

### Activar

1. Criar projecto em [sentry.io](https://sentry.io) → tipo **Flask** /
   **Python**.
2. Copiar o DSN: Project → Settings → Client Keys (DSN).
3. Definir env vars (Docker / Railway / systemd):

```bash
SENTRY_DSN=https://<key>@oXXXXX.ingest.sentry.io/<project>
SENTRY_RELEASE=<git-sha>          # opcional, mas recomendado
SENTRY_TRACES_SAMPLE_RATE=0.05    # opcional (0.0 default = só erros)
```

4. Restart. Verificar nos logs:

```bash
docker compose logs app | grep -i sentry
# → "Sentry activo — env=production release=abc123"
```

### Garantias de privacidade (RGPD)

Os alunos são **menores** — política de defesa em profundidade:

- `send_default_pii=False` — Sentry **não** envia IP, cookies, headers de auth.
- `before_send` hook (`config._scrub_event`) substitui por `[Filtered]`:
  - Credenciais: `password`, `pw`, `old_password`, `new_password`,
    `csrf_token`, `_csrf_token`
  - Identificadores: `nii`, `ni`, `Palavra_chave`, `reset_code`
  - Tokens: `Authorization`, `Cookie`, `Set-Cookie`
- O scrub é case-insensitive e nunca crasha o envio (try/except interno).

Para validar localmente:
```bash
source .venv/bin/activate
python -m pytest tests/test_sentry.py -v
```

### Verificar que está a funcionar

Disparar um erro de teste (em ambiente de staging, **não** em produção):
```python
# numa rota qualquer protegida, temporariamente:
raise RuntimeError("teste sentry")
```

Esperar 30s e verificar em sentry.io → Issues. Apagar o teste após confirmar.

### Desactivar

```bash
unset SENTRY_DSN  # ou comentar no .env
docker compose restart app
```

---

## Backup e restore

### Backup manual (sem cron)

```bash
curl -X POST \
  -H "Authorization: Bearer $CRON_API_TOKEN" \
  http://localhost:5000/api/backup-cron
```

Ou via CLI:
```bash
flask backup
```

Ficheiros ficam em `backups/sistema_YYYYMMDD_HHMMSS.db.gz`.

### Restore de backup

**⚠ CUIDADO:** isto sobrescreve `sistema.db`. Fazer downtime primeiro.

```bash
# 1. Parar app
docker compose stop app

# 2. Backup do estado actual por segurança
cp sistema.db sistema.db.pre-restore-$(date +%s)

# 3. Restaurar
flask restore backups/sistema_20260418_030000.db.gz

# 4. Arrancar
docker compose up -d app

# 5. Verificar
curl http://localhost:5000/health | jq
```

### Teste de restore (OBRIGATÓRIO antes de produção)

**Um backup nunca testado ≈ sem backup.** Correr este procedimento em
ambiente de staging ou máquina dev antes do deploy inicial, e depois
1× por trimestre.

```bash
# 1. Fazer backup do estado actual (guardar como sanidade)
cp sistema.db sistema.db.pre-test

# 2. Escolher um backup recente
ls -lt backups/*.db.gz | head -3

# 3. Restore
flask restore backups/sistema_AAAAMMDD_HHMMSS.db.gz

# 4. Arrancar app em porta dev
flask run --debug &
APP_PID=$!

# 5. Fumar:
#    a) GET /health → 200
curl -sf http://localhost:5000/health | jq '.status'
#    b) Login com um user conhecido do backup → dashboard carrega
#    c) Listar 5 refeições em /aluno/historico → aparecem
#    d) /admin/auditoria → log visível com entradas antigas

# 6. Parar app, restaurar estado original
kill $APP_PID
mv sistema.db.pre-test sistema.db
```

Se qualquer um dos passos 5a-5d falha: o backup está corrupto ou
incompleto. **Investigar antes de confiar neste backup em produção.**

### Backup offsite

Configurar vars em `.env`:
```
OFFSITE_BACKUP_ENABLED=1
OFFSITE_BACKUP_WEBHOOK=https://webhook.site/...
# ou S3:
AWS_S3_BUCKET=refeicoes-backups
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
```

Cada backup local dispara automaticamente o upload offsite. Ver
`core/backup.py:send_offsite()`.

---

## Rotação de tokens e segredos

### `CRON_API_TOKEN`

```bash
# 1. Gerar novo
python -c "import secrets; print(secrets.token_urlsafe(32))"

# 2. Actualizar env var (Docker / Railway / systemd)
# 3. Restart
# 4. Actualizar crontab / scheduler externo com o novo token
# 5. Testar:
curl -X POST -H "Authorization: Bearer $NEW_TOKEN" \
  http://localhost:5000/api/unlock-expired
```

### `SECRET_KEY`

**⚠ Rotação invalida TODAS as sessões — todos os users terão de re-login.**

```bash
python -c "import secrets; print(secrets.token_hex(32))"
# Actualizar .env / env var → restart
```

Só rodar em caso de comprometimento suspeito ou na rotação anual.

### Password admin perdida

Ver secção [Reset de password](#reset-de-password) abaixo.

---

## Cron jobs

Ver [API.md — Cron wrapper exemplo](API.md#cron-wrapper-exemplo-systemd-timer--railway-scheduler).

Resumo dos schedules recomendados:

| Endpoint                 | Frequência             | Propósito                |
|--------------------------|------------------------|--------------------------|
| `/api/autopreencher-cron`| ter 20:05              | Preencher faltosos       |
| `/api/backup-cron`       | cada 6h                | Backup rolado            |
| `/api/export-cron`       | 1º do mês 06:00        | Export CSV+PDF mensal    |
| `/api/unlock-expired`    | diário 03:00           | Cleanup login_eventos    |
| `/api/vacuum-cron`       | 1º do mês 03:30        | VACUUM + PRAGMA optimize |

Validar que os crons estão a correr:
```bash
grep '"cron' /var/log/refeicoes/cron-*.log | tail -20
```

### Slow query log (opt-in)

Sem instrumentação, a única pista de queries lentas é o "Slow request"
no log (>500ms total HTTP). Para identificar a *query* responsável,
activar:

```bash
SLOW_QUERY_THRESHOLD_MS=500 docker compose up -d app
```

Logs vão ficar com entradas:
```
[slow_query] 612.4ms | SELECT * FROM refeicoes WHERE utilizador_id=...
```

`SLOW_QUERY_THRESHOLD_MS=0` (default) desliga completamente — zero
overhead, a subclass `_TracingConnection` nem sequer é instalada.

Threshold recomendado:
- **dev**: 100 (vê tudo o que demora mais de 0.1s)
- **prod**: 500 (só queries verdadeiramente problemáticas)

Após identificar e optimizar (`EXPLAIN QUERY PLAN`, adicionar índice,
reescrever JOIN), voltar a desligar para evitar ruído nos logs.

### Métricas Prometheus (`GET /metrics`)

Endpoint padrão Prometheus em formato `text/plain` — sem auth (pattern
standard: scrape de dentro da rede privada). Para expor publicamente,
envolver com Basic Auth no proxy/ingress.

Exposição: counters, gauges + per-route (top 20 por contagem). Sem
dependência externa em `prometheus_client` — formato hand-rolled.

```bash
curl http://localhost:5000/metrics
# # HELP http_requests_total Total HTTP requests processed.
# # TYPE http_requests_total counter
# http_requests_total 12453
# # TYPE http_request_errors_total counter
# http_request_errors_total 3
# ...
# db_size_bytes 14523904
```

Configuração mínima do Prometheus scraper:
```yaml
scrape_configs:
  - job_name: 'refeicoes'
    scrape_interval: 30s
    static_configs:
      - targets: ['refeicoes:5000']
```

### Manutenção da BD (`/api/vacuum-cron`)

`PRAGMA wal_checkpoint(TRUNCATE)` corre a cada 5 min via middleware
(liberta o ficheiro `.db-wal`), mas **não** reclama o espaço de páginas
livres dentro do `.db` principal — esse acumula-se com DELETEs e UPDATEs.
Após meses, a BD pode estar 30-50% fragmentada.

`/api/vacuum-cron` faz:
1. `wal_checkpoint(TRUNCATE)` (liberta WAL)
2. `VACUUM` (rebuild completo, devolve espaço ao SO)
3. `PRAGMA optimize` (reanalisa estatísticas das tabelas para o query planner)

VACUUM adquire **lock exclusivo** — agendar em janela de baixo tráfego
(03:30 do 1º de cada mês). Pedidos concorrentes esperam até 8s
(`busy_timeout`) antes de falhar com `database is locked`.

```bash
curl -X POST -H "Authorization: Bearer $CRON_API_TOKEN" \
     http://localhost:5000/api/vacuum-cron | jq

# Response:
# {
#   "status": "ok",
#   "size_before_bytes": 12345678,
#   "size_after_bytes":   9876543,
#   "freed_bytes":        2469135,
#   "freed_pct":          20.0,
#   "optimize_ok":        true
# }
```

Se `freed_pct > 30%` regular → considera baixar a frequência da causa
(DELETEs em massa, exports). Se `freed_pct ≈ 0%` ao fim de 1 ano →
maintenance está a correr suficientemente bem, podes baixar para
trimestral.

---

## Erros comuns

### `sqlite3.OperationalError: database is locked`

Causas:
1. Backup em curso a ler o ficheiro enquanto outra escrita acontece
   (raro em WAL mode mas possível em `VACUUM`/`.backup`).
2. Transaction longa não fechada (bug).
3. Processo anterior crashou e deixou lock stale.

Diagnóstico:
```bash
# Ver processos a usar a BD
lsof sistema.db

# Ver WAL size (se gigante, checkpoint ficou bloqueado)
ls -lh sistema.db*
```

Resolução:
```bash
# Checkpoint manual
sqlite3 sistema.db "PRAGMA wal_checkpoint(TRUNCATE);"

# Se persistir — restart a app (liberta todos os file handles)
docker compose restart app
```

### `Session expired` — users a ser deslogados constantemente

Verificar:
1. `SECRET_KEY` não mudou entre deploys (sessões assinadas com a chave
   antiga ficam inválidas).
2. `PERMANENT_SESSION_LIFETIME` não está muito curto.
3. Cookies correctos: `Secure` em HTTPS, `SameSite=Lax`, `HttpOnly`.

```bash
# Inspeccionar session cookie no browser:
# DevTools → Application → Cookies → 'session' deve existir
```

### `401 Unauthorized` em `/api/*-cron`

1. Header `Authorization: Bearer <token>` presente?
2. Token bate com env var `CRON_API_TOKEN`?
   ```bash
   echo $CRON_API_TOKEN | head -c 10
   curl -v -H "Authorization: Bearer $CRON_API_TOKEN" http://.../api/...
   ```
3. Em prod, `CRON_API_TOKEN` está definido? (senão → 403 automático).

### `429 Too Many Requests`

Rate-limit activo. Ver header `Retry-After` na resposta.

Se é cron legítimo a bater no limite → baixar a frequência ou aumentar o
threshold em `blueprints/api/routes.py`:
```python
@limiter.limit("30 per minute")  # ajustar
```

Se é abuso → bom, está a funcionar. Ver logs:
```bash
docker compose logs app | jq 'select(.msg | contains("rate"))'
```

### CSRF token missing / expired

Causas:
1. Formulário submetido >60min depois de carregado (sessão expirou).
2. Cookie `session` não está a ser enviado (CORS, domínio, scheme http↔https).
3. Token em falta no HTML — verificar `{{ csrf_input() }}` em todos os `<form>`.

Teste manual: abrir DevTools → Network → ver se `session` cookie vai com
o POST.

### `templates/_macros.html: can't find 'empty_state'`

Template sem `{% import "_macros.html" as ui %}`. Adicionar no topo do
template, a seguir ao `{% extends %}`.

### Health check degraded

```bash
curl http://localhost:5000/health | jq
```

Consoante o check em erro:
- `db: error` — BD inacessível. Ver logs para exception.
- `backup_age_hours > 48` — cron de backup parou. Ver `cron-backup.log`.
- `disk_free_mb < 500` — disco cheio. Limpar `backups/` antigos e
  ver `DISK_FREE_THRESHOLD_MB` em `config.py`.

---

## Reset de password

### Admin perde password

Se há outro admin activo:
1. Outro admin vai a `/admin/utilizadores?edit_user=<NII>` → clica em
   **🔐 Gerar código de reset (24h)**.
2. Recebe código 8-char. Entrega ao admin bloqueado.
3. Bloqueado faz login com o código como password. É redireccionado para
   mudar password.

Se não há outro admin (bootstrap/desastre):
```bash
# 1. Parar app
docker compose stop app

# 2. Abrir SQLite
sqlite3 sistema.db

# 3. Gerar novo hash
python -c "from werkzeug.security import generate_password_hash; \
           print(generate_password_hash('NovaPassTemp123!', method='pbkdf2'))"

# 4. Na prompt SQLite:
UPDATE utilizadores
   SET Palavra_chave='<hash gerado>', must_change_password=1, locked_until=NULL
 WHERE perfil='admin';
.quit

# 5. Arrancar app e fazer login. Forçado a mudar password.
```

### User normal perde password

Admin vai a `/admin/utilizadores?edit_user=<NII>`:
- **Reset rápido**: botão "Reset password = NII" → password temporária igual ao NII.
- **Código único**: botão "🔐 Gerar código de reset (24h)" → melhor segurança, single-use, TTL 24h.

---

## Verificação pós-deploy

Smoke test (~5min):

```bash
# 1. Health
curl -f http://localhost:5000/health | jq '.status' | grep -q '"ok"'

# 2. Cron endpoints respondem com 403 sem token
curl -o /dev/null -w "%{http_code}\n" http://localhost:5000/api/unlock-expired
# → 403 ou 405 (GET vs POST — OK)

# 3. Login page carrega com CSRF
curl -s http://localhost:5000/login | grep -q 'csrf_token'

# 4. HTTPS redirect activo (em prod)
curl -I -H "X-Forwarded-Proto: http" http://localhost:5000/login | head -1
# → HTTP/1.1 301 ...

# 5. Logs JSON bem formados
docker compose logs app --tail=5 | tail -5 | jq -c .

# 6. Backup cron funciona
curl -X POST -H "Authorization: Bearer $CRON_API_TOKEN" \
     http://localhost:5000/api/backup-cron | jq '.status'
# → "ok"

# 7. BD responde rápido (<10ms SELECT)
time sqlite3 sistema.db "SELECT COUNT(*) FROM utilizadores;"
```

Se tudo passar → deploy validado. Anunciar aos utilizadores.
