# API Reference

Endpoints JSON expostos em `/health` e `/api/*`. Os de `/api/*` são cron
endpoints — autenticação por Bearer token. Todos respondem `Content-Type:
application/json`.

## Autenticação

Os endpoints `/api/*` exigem o header:

```
Authorization: Bearer <CRON_API_TOKEN>
```

O token vive em env var `CRON_API_TOKEN`. Fora de produção (`ENV=development`),
se o token não estiver configurado, `"dev"` é aceite como fallback (com
warning nos logs). **Em produção, a ausência de `CRON_API_TOKEN` rejeita
tudo com 403.**

Comparação constant-time (`secrets.compare_digest`) para evitar timing attacks.

## Rate limiting

Todos os endpoints cron estão limitados a **30 pedidos/minuto por IP** via
Flask-Limiter. Ultrapassar → `429 Too Many Requests` + JSON
`{"error": "rate limited", "retry_after": 60}`.

Rate-limit também aplicado a `POST /auth/login`: **10/min**. Contas
específicas mantêm o lockout por-conta (10 falhas → 15 min) além deste.

## Endpoints

### `GET /health`

Health check agregado. Público, sem autenticação.

**Resposta (200):**
```json
{
  "status": "ok",
  "ts": "2026-04-21T10:35:12",
  "checks": {
    "db": "ok",
    "db_size_mb": 12.34,
    "db_free_mb": 45678.9,
    "backup_age_hours": 4.2,
    "disk_free_mb": 45678.9
  },
  "elapsed_ms": 8
}
```

**Resposta (503):** mesma estrutura com `"status": "degraded"` e pelo menos
um check em `"error"`. Razões típicas: BD inacessível, último backup >48h,
<500MB livres em disco.

Uso típico: Railway/Docker healthcheck, uptime monitor externo.

### `POST /api/backup-cron`

Dispara um backup manual completo e purga backups antigos (política
rolada — diários últimos 7d, semanais últimas 4s, mensais últimos 12m).

**Resposta:**
```json
{
  "status": "ok",
  "ts": "...",
  "backup_file": "backups/sistema_20260421_103500.db.gz",
  "backups_deleted": 2
}
```

**Exemplo:**
```bash
curl -X POST \
  -H "Authorization: Bearer $CRON_API_TOKEN" \
  https://refeicoes.exemplo.pt/api/backup-cron
```

Schedule sugerido: **a cada 6h**.

### `POST /api/autopreencher-cron`

Corre o autopreenchimento semanal: para alunos que não marcaram até ao
deadline (ter 20:00 da semana anterior), marca automaticamente todas as
refeições como "presente" conforme o menu vigente.

**Resposta:**
```json
{
  "status": "ok",
  "ts": "...",
  "semana": "2026-W17",
  "alunos_autopreenchidos": 42,
  "refeicoes_criadas": 630
}
```

Schedule sugerido: **ter 20:05** (após o deadline de marcação).

### `POST /api/export-cron`

Gera e guarda o export mensal em CSV+PDF para consulta offline pelos
oficiais. Output em `exports/mensal_YYYY-MM.{csv,pdf}`.

**Resposta:**
```json
{
  "status": "ok",
  "ts": "...",
  "files": [
    "exports/mensal_2026-04.csv",
    "exports/mensal_2026-04.pdf"
  ]
}
```

Schedule sugerido: **1º dia do mês, 06:00**.

### `POST /api/unlock-expired`

Limpeza de dados de segurança expirados:
1. `DELETE FROM login_eventos WHERE sucesso=0 AND criado_em < now()-24h`
   — evita crescimento infinito da tabela de eventos.
2. `UPDATE utilizadores SET reset_code=NULL, reset_expires=NULL` para
   códigos de reset cujo TTL passou.
3. `UPDATE utilizadores SET locked_until=NULL` para bloqueios de conta
   vencidos (a verificação em runtime já falha se `locked_until < now()`,
   mas isto liberta a UI de os mostrar como "bloqueados").

**Resposta:**
```json
{
  "status": "ok",
  "ts": "...",
  "deleted_login_failures": 15,
  "expired_reset_codes": 2,
  "unlocked_users": 1
}
```

**Exemplo:**
```bash
curl -X POST \
  -H "Authorization: Bearer $CRON_API_TOKEN" \
  https://refeicoes.exemplo.pt/api/unlock-expired
```

Schedule sugerido: **diário, 03:00**.

### Manutenção da BD — `flask vacuum` (CLI, não HTTP)

VACUUM + `PRAGMA optimize` são operações de manutenção interna sem
necessidade de superfície HTTP. Disponíveis via Click CLI:

```bash
# Local ou Docker
flask vacuum
docker compose exec app flask vacuum
```

Schedule via cron do SO (mensal recomendado — ver
[RUNBOOK § Manutenção da BD](RUNBOOK.md#manutenção-da-bd-flask-vacuum)).

## Códigos de erro

| Status | Significado                                            |
|--------|--------------------------------------------------------|
| 200    | OK                                                     |
| 400    | Request mal formado (ex: body JSON inválido)           |
| 403    | Bearer token em falta ou inválido                      |
| 429    | Rate limit excedido (respeitar `Retry-After` header)   |
| 500    | Erro interno — ver `error.ts` nos logs                 |
| 503    | `/health` em estado degraded                           |

## Formato de resposta de erro

```json
{
  "status": "error",
  "error": "mensagem em texto",
  "ts": "2026-04-21T10:35:12"
}
```

## Cron wrapper exemplo (systemd timer / Railway scheduler)

```bash
#!/usr/bin/env bash
# /etc/refeicoes/cron.sh
set -euo pipefail

TOKEN="${CRON_API_TOKEN:?CRON_API_TOKEN não definido}"
BASE="${REFEICOES_URL:-https://refeicoes.exemplo.pt}"

case "${1:?usage: cron.sh {backup|autofill|export|unlock}}" in
  backup)    ep="/api/backup-cron" ;;
  autofill)  ep="/api/autopreencher-cron" ;;
  export)    ep="/api/export-cron" ;;
  unlock)    ep="/api/unlock-expired" ;;
esac

curl --fail-with-body -sS -X POST \
  -H "Authorization: Bearer $TOKEN" \
  "$BASE$ep" \
  | tee -a "/var/log/refeicoes/cron-${1}.log"
```

`crontab`:
```
5  20 * * 2  /etc/refeicoes/cron.sh autofill    # ter 20:05
0  */6 * * * /etc/refeicoes/cron.sh backup      # cada 6h
0  6 1 * *   /etc/refeicoes/cron.sh export      # 1º do mês
0  3 * * *   /etc/refeicoes/cron.sh unlock      # diário
```
