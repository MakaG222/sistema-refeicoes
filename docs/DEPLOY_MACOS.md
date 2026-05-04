# Deploy de staging — guia macOS

Plano passo-a-passo para validar a v1.2.0 num macOS (Apple Silicon ou
Intel) antes de promover para produção real. Funciona em macOS 12
(Monterey) ou superior.

**Tempo total estimado:** 30 min de setup + ~1h de smoke testing.

---

## Caminho A — Docker local (validação rápida, ~30 min)

Bom para validar que o build/runtime funcionam fora do venv. **Não**
testa HTTPS, DNS, ou SSL real — para isso, segue o **Caminho B**.

### A.1 Pré-requisitos

```zsh
# Verificar Homebrew (caso não tenhas, segue https://brew.sh)
brew --version || /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# Instalar utilitários necessários
brew install --cask docker      # Docker Desktop (~700 MB)
brew install jq curl            # já tens curl, jq facilita ler JSON
```

Após instalar:
1. Abrir **Docker Desktop** (Applications) e esperar pelo ícone na status bar
2. Confirmar que está a correr:
   ```zsh
   docker --version              # → Docker version 24.x ou superior
   docker compose version        # → Docker Compose version v2.x
   docker info | grep "Server Version"  # confirma daemon vivo
   ```

> **Nota macOS Apple Silicon (M1/M2/M3):** O `python:3.11-slim` base é
> multi-arch — funciona nativo em arm64. Sem necessidade de
> `--platform=linux/amd64`.

> **Nota macOS Monterey+:** A porta 5000 está reservada pelo *AirPlay
> Receiver* por defeito. Não nos afecta (usamos 8080), mas se vires
> erros de "port already in use", verifica em **System Settings →
> General → AirDrop & Handoff → AirPlay Receiver**.

### A.2 Pre-flight checks

```zsh
cd ~/Desktop/P3/.claude/worktrees/naughty-blackburn-2d8084

# Activar venv se ainda não está activo
source .venv/bin/activate

# Validação local (lint + bandit + tests + docker build) — ~3 min
./scripts/preflight.sh
```

Se algum gate falhar, corrige antes de prosseguir. O preflight termina
com:
```
✓ PREFLIGHT OK (XXXs) — pronto para promover
```

### A.3 Configurar `.env` para staging

```zsh
# Copiar template
cp .env.example .env

# Gerar secrets seguros (one-liner copy-paste):
cat <<EOF >> .env

# === Staging secrets — gerados automaticamente ===
ENV=production
SECRET_KEY=$(python3 -c 'import secrets; print(secrets.token_hex(32))')
CRON_API_TOKEN=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')
EOF

# Verificar (sem expor secrets na shell history!)
grep -E "^(ENV|SECRET_KEY|CRON_API_TOKEN)=" .env | awk -F= '{print $1"=<"length($2)" chars>"}'
# Esperado:
#   ENV=<10 chars>
#   SECRET_KEY=<64 chars>
#   CRON_API_TOKEN=<43 chars>
```

> **⚠ NUNCA commitar `.env`.** Já está no `.gitignore`. Confirma:
> ```zsh
> git check-ignore -v .env  # → .gitignore:N:.env  .env
> ```

### A.4 Build + arrancar

```zsh
# Build da imagem (cache de pip — 1ª vez ~3 min, subsequentes ~30s)
docker compose build

# Arrancar em background
docker compose up -d

# Esperar pelo healthcheck (~15s)
until docker compose ps app | grep -q "healthy"; do
  echo "A esperar pelo arranque..."
  sleep 2
done
echo "✓ App está healthy"
```

Verificar manualmente:
```zsh
curl -s http://localhost:8080/health | jq
# Esperado: {"status": "ok", "db": "ok", ...}
```

### A.5 Smoke test automatizado

```zsh
./scripts/smoke_test.sh http://localhost:8080
```

Esperado: **10 passed, 2 warnings (HSTS+HTTPS redirect — esperado em
http local), 0 failed**.

### A.6 Smoke test manual (~10 min)

Abrir em Safari/Chrome:
```zsh
open http://localhost:8080
```

Seguir o checklist em `docs/DEPLOYMENT_CHECKLIST.md` § "Verificação
funcional". Mínimo viável:

- [ ] Login com `admin` / `admin123` (conta bootstrap dev)
- [ ] Forçado a mudar password → mudar para algo forte
- [ ] Criar 1 aluno via UI → fazer login com esse aluno
- [ ] Aluno marca refeição amanhã → confirmar persiste após F5
- [ ] Aluno descarrega `/aluno/refeicoes.ics` → abrir em **Calendar.app**
      → confirmar evento aparece no horário correcto
- [ ] Toggle dark mode → recarregar página → tema persiste
- [ ] `?` (com Shift) → overlay de shortcuts aparece

### A.7 Logs e debug

```zsh
# Stream de logs JSON
docker compose logs -f app | tail -200

# Filtrar por NII de utilizador
docker compose logs app | jq 'select(.user_nii == "12345")' 2>/dev/null

# Filtrar por request_id (debug de um request específico)
docker compose logs app | jq 'select(.request_id == "abc123")' 2>/dev/null

# Entrar no container
docker compose exec app sh
# > flask --help
# > flask vacuum
# > sqlite3 /data/sistema.db
```

### A.8 Teardown

```zsh
# Parar mas manter dados
docker compose stop

# Apagar tudo (⚠ apaga o volume — perdes a BD!)
docker compose down -v
```

---

## Caminho B — Railway (staging real com HTTPS, ~30 min)

Bom para validar HTTPS, HSTS, DNS, e ter um URL público para testers
externos. Free tier do Railway: 500h/mês + 1GB RAM (suficiente).

### B.1 Pré-requisitos

```zsh
# Railway CLI via Homebrew
brew install railway

# Login (abre browser para autenticação)
railway login

# Verificar
railway whoami
```

### B.2 Criar projeto

```zsh
cd ~/Desktop/P3/.claude/worktrees/naughty-blackburn-2d8084

# Criar projecto novo
railway init -n refeicoes-staging

# Linkar à directoria (caso ainda não esteja)
railway link
```

### B.3 Configurar volume persistente

Railway free tier tem volumes — necessário para a SQLite BD persistir
entre deploys.

Via UI Railway:
1. Project → Add Service → **Volume**
2. Mount path: `/data`
3. Size: 1 GB

### B.4 Definir env vars

```zsh
# Vars obrigatórias
railway variables \
  --set "ENV=production" \
  --set "SECRET_KEY=$(python3 -c 'import secrets; print(secrets.token_hex(32))')" \
  --set "CRON_API_TOKEN=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')" \
  --set "DB_PATH=/data/sistema.db" \
  --set "BACKUP_DIR=/data/backups"

# Verificar (sem expor secrets!)
railway variables | awk -F'=' '/^(ENV|SECRET_KEY|CRON_API_TOKEN|DB_PATH|BACKUP_DIR)=/ {print $1"=<set>"}'
```

### B.5 Guardar tokens fora do shell history

**Importante:** o `CRON_API_TOKEN` vais precisar para chamar `/api/*-cron`.
Guarda-o em local seguro **agora**:

```zsh
# Recuperar do Railway (uma única vez)
railway variables --kv | grep CRON_API_TOKEN
# Output: CRON_API_TOKEN=abc123xyz...

# Guardar em macOS Keychain (recomendado vs ficheiro de texto)
security add-generic-password \
  -a "$USER" \
  -s "refeicoes-staging-cron-token" \
  -w "<cola_o_token_aqui>"

# Recuperar depois com:
security find-generic-password -a "$USER" -s "refeicoes-staging-cron-token" -w
```

### B.6 Deploy

```zsh
# Push da branch actual para o Railway
railway up

# Acompanhar logs do deploy
railway logs --deployment
```

Após o build (~2-3 min), Railway dá-te um URL tipo
`refeicoes-staging-production.up.railway.app`. Capturar:

```zsh
STAGING_URL=$(railway domain | head -1 | awk '{print $1}')
echo "Staging URL: https://$STAGING_URL"
```

Adicionar domínio público (opcional):
```zsh
railway domain  # gera URL público
# Ou para domínio custom: Railway UI → Settings → Domains → Add
```

### B.7 Smoke test pós-deploy

```zsh
./scripts/smoke_test.sh "https://$STAGING_URL"
```

Esperado em HTTPS: **11 passed (HSTS check passa agora!), 0 warnings,
0 failed**.

Verificar HSTS manualmente:
```zsh
curl -sI "https://$STAGING_URL/health" | grep -i "strict-transport"
# Esperado: Strict-Transport-Security: max-age=31536000; includeSubDomains
```

### B.8 Activar Sentry (recomendado 1-2 semanas em staging)

1. Criar projecto em https://sentry.io → tipo **Python / Flask**
2. Copiar o DSN: Project → Settings → Client Keys (DSN)
3. Adicionar ao Railway:
   ```zsh
   railway variables --set "SENTRY_DSN=https://...@oXXX.ingest.sentry.io/XXX"
   railway redeploy
   ```
4. Disparar um erro de teste para confirmar que chega:
   ```zsh
   # Numa shell, fazer um request a um endpoint que sabemos que rebenta:
   curl -X POST -H "Authorization: Bearer wrong-token" "https://$STAGING_URL/api/backup-cron"
   # 403 esperado — não é erro mas testa o pipeline
   ```
5. Sentry dashboard → Issues → confirmar que aparece dentro de 30s

### B.9 Cron jobs (Railway scheduler)

Via UI Railway: Service → Settings → Cron Jobs

Adicionar 4 schedules (token via `${{shared.CRON_API_TOKEN}}`):

| Cron expr   | Comando                                                                                        |
|-------------|------------------------------------------------------------------------------------------------|
| `0 */6 * * *`   | `curl -X POST -H "Authorization: Bearer $CRON_API_TOKEN" $RAILWAY_URL/api/backup-cron`     |
| `5 20 * * 2`    | `curl -X POST -H "Authorization: Bearer $CRON_API_TOKEN" $RAILWAY_URL/api/autopreencher-cron` |
| `0 6 1 * *`     | `curl -X POST -H "Authorization: Bearer $CRON_API_TOKEN" $RAILWAY_URL/api/export-cron`       |
| `0 3 * * *`     | `curl -X POST -H "Authorization: Bearer $CRON_API_TOKEN" $RAILWAY_URL/api/unlock-expired`    |

VACUUM mensal (CLI, não cron HTTP):
```zsh
# Manual, 1× por mês:
railway run flask vacuum
```

---

## Pós-deploy — primeiras 24-48h

### Monitorização contínua

```zsh
# Tail dos logs em janela dedicada (open new Terminal tab: Cmd+T)
railway logs --tail

# Health check repetido a cada 30s (poll manual em outra janela)
while true; do
  curl -s "https://$STAGING_URL/health" | jq -c '{ts, status, db, last_backup_hours}'
  sleep 30
done
```

### Uptime monitoring externo (recomendado)

Free tier suficiente:
- **UptimeRobot** (https://uptimerobot.com) — 50 monitores grátis,
  ping cada 5 min
- **Better Stack** (https://betterstack.com) — 10 monitores grátis,
  alerta por email/Slack

URL a monitorizar: `https://$STAGING_URL/health`
Critério de "down": HTTP != 200 OR `status != "ok"` no body.

---

## Rollback rápido

Se algo correr mal nas primeiras horas:

```zsh
# Ver últimas 5 deployments
railway deployments

# Reverter para a anterior (via UI: Deployments → ⋯ → Restore)
# Ou via CLI:
railway redeploy --deployment <id-da-anterior>
```

Para Docker local:
```zsh
git checkout v1.1.0  # tag anterior estável
docker compose up -d --build
```

---

## Quando promover para produção real

Critérios mínimos antes de abrir aos 100+ utilizadores reais:

- [ ] **1 semana em staging sem incidentes** (Sentry: 0 errors novos)
- [ ] **Smoke test manual feito 2-3×** com utilizadores diferentes (admin,
      aluno, oficial-dia)
- [ ] **Restore de backup testado** numa máquina separada (ver
      [RUNBOOK.md § Teste de restore](RUNBOOK.md#teste-de-restore-obrigatório-antes-de-produção))
- [ ] **Cron jobs verificados** — pelo menos 1 ciclo completo de cada
      (backup, unlock-expired, autopreencher) correu OK
- [ ] **Plano de rollback documentado** — quem pode autorizar, qual
      versão estável anterior, comando exacto
- [ ] **Canal de suporte definido** (email/Teams) e comunicado aos users

---

## Troubleshooting macOS específico

### "docker: command not found" após instalar Docker Desktop
```zsh
# Adicionar ao PATH (zsh)
echo 'export PATH="/Applications/Docker.app/Contents/Resources/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc
```

### "Cannot connect to the Docker daemon"
Docker Desktop não está a correr. Abrir manualmente:
```zsh
open -a Docker
# Esperar pelo ícone na status bar a deixar de animar
```

### "Address already in use" na porta 8080
```zsh
# Ver o que está a usar a porta
lsof -ti:8080

# Matar (cuidado se estás a usar para outra coisa)
lsof -ti:8080 | xargs kill -9
```

### Build do Docker muito lento na primeira vez
Normal — está a fazer pull do `python:3.11-slim` (~50 MB) e build dos
deps. Builds subsequentes (com cache) demoram <30s. Para acelerar:
```zsh
docker buildx build --cache-from sistema-refeicoes:local -t sistema-refeicoes:local .
```

### Apple Silicon — performance issues
Em M1/M2/M3, deves ver "linux/arm64" no `docker info`. Se vires
"linux/amd64", está em emulação Rosetta (lento). Forçar arm64:
```zsh
docker buildx build --platform linux/arm64 -t sistema-refeicoes:local .
```

### Logs JSON difíceis de ler
```zsh
# Pipe sempre por jq
docker compose logs --tail=50 app | jq -R 'fromjson? // .'

# Filtrar só warnings/errors
docker compose logs app | jq -c 'select(.level == "WARNING" or .level == "ERROR")'
```

### Calendar.app não importa o `.ics`
- Verificar que o ficheiro abre num text editor — deve começar com
  `BEGIN:VCALENDAR\r\n` (com CRLF)
- Tentar via drag-and-drop directo para Calendar em vez de duplo-click
- Se persistir: `open -a Calendar /caminho/para/refeicoes.ics`
