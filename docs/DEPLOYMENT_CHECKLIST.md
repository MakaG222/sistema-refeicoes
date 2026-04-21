# Deployment Checklist

Lista obrigatória antes de abrir o sistema aos utilizadores reais. Percorre
**de cima para baixo** — cada linha um passo, cada caixa um ok manual.

## Pré-requisitos — Infra

- [ ] Servidor Linux ≥ Python 3.11 OU Docker ≥ 24 OU Railway/plataforma equivalente
- [ ] DNS: domínio público apontado para o servidor (ex: `refeicoes.exemplo.pt`)
- [ ] Certificado TLS válido (Let's Encrypt via Traefik/Caddy, ou plataforma gere)
- [ ] ≥ 2 GB RAM, ≥ 10 GB disco livre
- [ ] Acesso SSH / shell para correr comandos ad-hoc

## Variáveis de ambiente — todas obrigatórias em produção

Criar `.env` (ou equivalente na plataforma) com:

- [ ] `ENV=production` — activa flags de produção (HTTPS redirect, JSON logs, etc.)
- [ ] `SECRET_KEY=...` — **64 hex chars aleatórios**.
      Gerar com: `python -c "import secrets; print(secrets.token_hex(32))"`
- [ ] `CRON_API_TOKEN=...` — **32+ chars urlsafe**. Sem isto, `/api/*` retorna 403.
      Gerar com: `python -c "import secrets; print(secrets.token_urlsafe(32))"`
- [ ] `DB_PATH=/data/sistema.db` (ou caminho equivalente — volume persistente!)
- [ ] `BACKUP_DIR=/data/backups` (mesmo volume)
- [ ] `LOG_LEVEL=INFO` (ou `WARNING` para menos volume)

### Segurança — confirmar valores:

- [ ] `SESSION_COOKIE_SECURE=1` (forçar HTTPS em cookies)
- [ ] `SESSION_COOKIE_HTTPONLY=1`
- [ ] `SESSION_COOKIE_SAMESITE=Lax`
- [ ] `PERMANENT_SESSION_LIFETIME=3600` (1h inactividade → logout)

### Backups offsite (fortemente recomendado):

- [ ] `OFFSITE_BACKUP_ENABLED=1`
- [ ] Uma das duas configurações:
  - **Webhook:** `OFFSITE_BACKUP_WEBHOOK=https://webhook.site/...`
  - **S3:** `AWS_S3_BUCKET=...`, `AWS_ACCESS_KEY_ID=...`, `AWS_SECRET_ACCESS_KEY=...`,
    `AWS_REGION=eu-west-1`

### Notificações (opcional mas útil):

- [ ] `NOTIFICATION_WEBHOOK_URL=...` — recebe eventos críticos

## Arranque

- [ ] Build / pull da imagem: `docker compose pull && docker compose build`
- [ ] Verificar que o volume de dados existe e persiste reboot:
      `docker volume inspect refeicoes_data`
- [ ] Arrancar: `docker compose up -d`
- [ ] Esperar ≥ 30s e verificar:
      `curl -sf http://localhost:5000/health | jq '.status'` → `"ok"`

## Migrações

- [ ] Confirmar que `ensure_schema()` correu: procurar nos logs
      `Migrações aplicadas: ...` ou `Migração '008_add_reset_code' aplicada`
- [ ] Se migrando de v1.0.x, confirmar que coluna `reset_code` existe:
      ```bash
      sqlite3 /data/sistema.db "PRAGMA table_info(utilizadores);" | grep reset_code
      ```

## Primeira conta admin

- [ ] Criar um admin real (não usar o `admin`/`admin123` bootstrap!):
      ```bash
      docker compose exec app flask create-admin \
        --nii SEU_NII --ni SEU_NI --nome "Seu Nome" --pw 'PasswordForte!X9'
      ```
- [ ] Fazer login → confirmar redirect para mudança de password
- [ ] Alterar password imediatamente para uma que só o admin sabe
- [ ] **Apagar ou bloquear** contas bootstrap: `admin`, `aluno1`, etc.

## Verificação funcional (smoke test — ~10 min)

Seguir os passos em [RUNBOOK.md § Verificação pós-deploy](RUNBOOK.md#verificação-pós-deploy)
e confirmar cada:

- [ ] `GET /health` → 200 com `"status": "ok"`
- [ ] `GET /login` → 200 com token CSRF
- [ ] `curl -I -H "X-Forwarded-Proto: http" http://host/login` → 301 para HTTPS
- [ ] `POST /api/unlock-expired` sem token → 403
- [ ] `POST /api/unlock-expired` com token válido → 200 + JSON com contadores
- [ ] Login com utilizador normal → dashboard
- [ ] Tentar marcar refeição → guarda sem erros
- [ ] Admin gera reset code para teste → user faz login com código → força change-password
- [ ] Admin abre `/admin/auditoria?page=1` → paginação funciona
- [ ] Toggle dark mode → UI escura, persiste F5
- [ ] Pressionar `?` → overlay de shortcuts aparece

## Cron jobs (externos à app)

Ver [API.md § Cron wrapper](API.md#cron-wrapper-exemplo-systemd-timer--railway-scheduler).

- [ ] `/api/backup-cron` — cada 6h
- [ ] `/api/autopreencher-cron` — ter 20:05
- [ ] `/api/export-cron` — 1º do mês 06:00
- [ ] `/api/unlock-expired` — diário 03:00
- [ ] Confirmar que cada um já correu pelo menos 1× com sucesso
      (procurar nos logs: `"backup-cron: ..."`, `"unlock-expired: ..."`)

## Monitorização externa

- [ ] Uptime monitor a pingar `/health` a cada 5 min (UptimeRobot / Better Stack / etc.)
- [ ] Alerta configurado para notificar admins (Discord/email/SMS) quando `/health` != 200
- [ ] Alerta para expiração do certificado TLS

## Backup — testado, não assumido

**⚠ Um backup nunca testado ≈ sem backup.**

- [ ] Backup manual testado: `curl -X POST -H "Authorization: Bearer $TOK" .../api/backup-cron`
- [ ] Ficheiro criado em `BACKUP_DIR` e tem tamanho > 1 KB
- [ ] Backup offsite também chegou ao destino (S3/webhook) — verificar manualmente
- [ ] **Restore testado** numa máquina separada (ver [RUNBOOK.md § Restore](RUNBOOK.md#restore-de-backup))
- [ ] Documentado onde vive o CRON_API_TOKEN no cofre de segredos (1Password/Bitwarden/etc.)

## Acesso aos utilizadores

- [ ] Importar lista inicial de alunos via CSV em `/admin/importar-csv`
      (formato: `NII;NI;Nome;Ano`)
- [ ] Distribuir NIIs aos utilizadores (password inicial = NII, forçam change)
- [ ] Partilhar link da doc `/docs/USER_MANUAL.md` (ou cópia em PDF)
- [ ] Canal de suporte definido (email / Teams / etc.) e comunicado

## Rollback plan

Se algo correr mal nas primeiras horas:

- [ ] Saber qual era a **versão anterior** para reverter: `git tag -l --sort=-v:refname | head -3`
- [ ] Comando de rollback testado: `docker compose pull && docker compose up -d` com
      imagem do tag anterior
- [ ] Restore do backup pré-deploy testado (ver [RUNBOOK.md § Restore](RUNBOOK.md#restore-de-backup))
- [ ] Decidir antecipadamente **quem** pode tomar a decisão de rollback e **como** comunicar

## Pós-deploy — primeiras 24h

- [ ] Verificar logs a cada poucas horas: erros novos? Volumes suspeitos de 401/403?
- [ ] Verificar tamanho da BD e do `-wal`:
      `ls -lh /data/sistema.db*` — `-wal` nunca deve passar ~100MB
- [ ] Verificar métricas em `/admin/metrics` (se disponível) para ver qps/latency
- [ ] Recolher feedback dos primeiros utilizadores
- [ ] Criar issue no GitHub com os bugs/UX problems encontrados

---

**Quando todas as caixas acima estão marcadas:** estás pronto para abrir aos
100+ utilizadores em UAT / produção. Boa sorte. ⛵
