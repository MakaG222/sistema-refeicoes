# Guia Operacional — Sistema de Refeições

Manual de operações para instalar, configurar, atualizar, fazer backup e restaurar o sistema.

---

## 1. Instalação (novo servidor)

### Pré-requisitos
- Python 3.11+
- pip

### Passos

```bash
# 1. Clonar o repositório
git clone https://github.com/MakaG222/sistema-refeicoes.git
cd sistema-refeicoes

# 2. Criar ambiente virtual
python -m venv venv
source venv/bin/activate   # Linux/macOS
# venv\Scripts\activate    # Windows

# 3. Instalar dependências
pip install -r requirements.txt

# 4. Configurar variáveis de ambiente
cp .env.example .env
# Editar .env com os valores reais (ver secção 3)

# 5. Arrancar (cria BD + schema + contas de sistema automaticamente)
python app.py
```

A BD SQLite é criada automaticamente no primeiro arranque. Não é necessário nenhum setup manual de base de dados.

### Produção (com Gunicorn)

```bash
export ENV=production
export SECRET_KEY=$(python -c "import secrets; print(secrets.token_hex(32))")
export CRON_API_TOKEN=$(python -c "import secrets; print(secrets.token_urlsafe(32))")

gunicorn -w 2 --threads 4 -b 0.0.0.0:$PORT --timeout 120 app:app
```

---

## 2. Arranque e verificação

### Verificar que a app está funcional

```bash
# Health check
curl http://localhost:8080/health

# Métricas
curl http://localhost:8080/health/metrics
```

### O que acontece no arranque

1. **Schema**: cria todas as tabelas se não existirem
2. **Migrações**: aplica migrações pendentes (versionadas)
3. **Backup diário**: cria backup automático (1x/dia)
4. **Limpeza**: remove backups com mais de 30 dias
5. **Contas dev**: seed de contas de teste (só em desenvolvimento)

### Verificar migrações pendentes

```bash
flask migrate
```

---

## 3. Variáveis de ambiente

| Variável | Obrigatória | Default | Descrição |
|----------|:-----------:|---------|-----------|
| `ENV` | Produção | `development` | `development` ou `production` |
| `SECRET_KEY` | Produção | auto-gerada | Chave de encriptação de sessões |
| `CRON_API_TOKEN` | Recomendada | vazio | Token Bearer para endpoints cron |
| `DB_PATH` | Não | `sistema.db` | Caminho para a BD SQLite |
| `DIAS_ANTECEDENCIA` | Não | `15` | Dias à frente para marcar refeições |
| `PORT` | Não | `8080` | Porta do servidor |
| `DEBUG` | Não | `false` | Modo debug (nunca em produção) |

### Em produção são obrigatórios:
```bash
ENV=production
SECRET_KEY=<token de 32+ caracteres>
```

---

## 4. Backup

### Backup automático
- Criado **automaticamente no arranque** da app (1x/dia)
- Ficheiros guardados em `backups/` com formato `sistema_YYYYMMDD.db`
- Retenção: 30 dias (configurável via `BACKUP_RETENCAO_DIAS`)

### Backup manual (CLI)

```bash
# Criar backup
flask backup

# Listar backups disponíveis
flask backup-list
```

### Backup manual (interface web)
- **Painel de Operações** → botão "Backup BD" (oficialdia/admin)
- **Admin** → "Download BD" (admin) — descarrega para o PC

### Backup via cron (automatizado)

```bash
curl -X POST http://localhost:8080/api/backup-cron \
  -H "Authorization: Bearer $CRON_API_TOKEN"
```

### Boas práticas
- Manter pelo menos 1 cópia **fora do servidor** (download regular)
- Verificar que `backups/` não enche o disco (limpeza automática a cada 30 dias)
- Em Railway/cloud: usar volume persistente para `DB_PATH`

---

## 5. Restauro

### Via CLI (recomendado)

```bash
# 1. Listar backups disponíveis
flask backup-list

# 2. Restaurar (com confirmação)
flask restore backups/sistema_20260320.db

# 3. Restaurar sem prompt (scripts)
flask restore backups/sistema_20260320.db --yes

# 4. Reiniciar a app após restauro
# O restauro NÃO reinicia automaticamente — é preciso reiniciar manualmente.
```

### O que o restauro faz
1. **Valida** o ficheiro de backup (integridade SQLite + tabelas do sistema)
2. **Cria backup de segurança** do estado actual (`pre_restauro_YYYYMMDD_HHMMSS.db`)
3. **Substitui** a BD activa pelo backup
4. **Remove** ficheiros WAL/SHM orphans

### Restauro manual (emergência)

Se a app não arranca e o CLI não funciona:

```bash
# 1. Parar a app
# 2. Backup do estado actual (por segurança)
cp sistema.db backups/pre_restauro_manual_$(date +%Y%m%d_%H%M%S).db

# 3. Copiar o backup para substituir a BD
cp backups/sistema_20260320.db sistema.db

# 4. Limpar WAL (se existir)
rm -f sistema.db-wal sistema.db-shm

# 5. Reiniciar a app
python app.py
```

---

## 6. Atualização

### Procedimento seguro

```bash
# 1. Backup antes de atualizar
flask backup

# 2. Atualizar código
git pull origin main

# 3. Atualizar dependências
pip install -r requirements.txt

# 4. Correr migrações
flask migrate

# 5. Reiniciar a app
# (Gunicorn: kill + restart; systemd: systemctl restart)
```

### Rollback

Se a atualização correu mal:

```bash
# 1. Reverter código
git checkout <commit-anterior>

# 2. Restaurar BD (se migração alterou dados)
flask restore backups/sistema_<data_antes_update>.db --yes

# 3. Reiniciar
```

---

## 7. Configurar administradores

### Em desenvolvimento
As contas admin são criadas automaticamente no arranque:
- `admin` / `admin123` — Administrador
- `cmd1`–`cmd8` — Comandantes por ano
- `cozinha` / `cozinha123` — Cozinha
- `oficialdia` / `oficial123` — Oficial de dia

### Em produção
1. Arrancar a app (cria schema)
2. Aceder via interface web com conta admin
3. Menu **Admin → Utilizadores → Criar** novo utilizador com perfil adequado
4. Ou importar via **Admin → Importar CSV**

### Perfis disponíveis

| Perfil | Acesso |
|--------|--------|
| `admin` | Acesso total: utilizadores, auditoria, backups, calendário |
| `cmd` | Gestão do seu ano: alunos, ausências, detenções, presenças |
| `oficialdia` | Operações diárias: painel, excepções, licenças, presenças |
| `cozinha` | Painel de operações, menus, relatórios |
| `aluno` | Marcar refeições, ausências, perfil próprio |

---

## 8. Monitorização

### Logs

**Desenvolvimento:** texto legível em stdout
```
2026-03-22 10:15:30 INFO [app] [a1b2c3]: Login OK: NII=admin perfil=admin IP=127.0.0.1
```

**Produção:** JSON em stdout (parseável por ferramentas de log)
```json
{"ts": "2026-03-22T10:15:30", "level": "INFO", "logger": "app", "msg": "Login OK: NII=admin", "rid": "a1b2c3"}
```

### Endpoints de monitorização

| Endpoint | Descrição |
|----------|-----------|
| `GET /health` | Health check (JSON: status, bd, disco) |
| `GET /health/metrics` | Métricas: contagens, latência, erros |

### Request ID
Cada pedido recebe um ID único (header `X-Request-ID`), rastreável nos logs.

### Pedidos lentos
Pedidos com >500ms são logados com WARNING.

---

## 9. Troubleshooting

### A app não arranca

| Sintoma | Causa provável | Solução |
|---------|---------------|---------|
| `RuntimeError: SECRET_KEY` | ENV=production sem SECRET_KEY | Definir `SECRET_KEY` |
| `sqlite3.OperationalError` | BD corrompida ou permissões | Restaurar backup |
| `ModuleNotFoundError` | Dependências em falta | `pip install -r requirements.txt` |

### BD corrompida

```bash
# Verificar integridade
python -c "import sqlite3; c=sqlite3.connect('sistema.db'); print(c.execute('PRAGMA quick_check').fetchone())"

# Se falhar: restaurar último backup
flask restore backups/sistema_<data>.db --yes
```

### Sessões expiram demasiado rápido
- Default: 3 minutos de inactividade (`PERMANENT_SESSION_LIFETIME=180`)
- Ajustar em `config.py` se necessário

### Espaço em disco
- Backups ocupam ~espaço da BD × 30 dias
- Verificar `backups/` periodicamente
- Limpeza automática activa (30 dias retenção)
