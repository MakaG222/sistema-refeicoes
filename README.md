# Sistema de Refeições — Escola Naval

Sistema web de gestão de refeições para a Escola Naval Portuguesa.
Permite aos cadetes marcar refeições, aos oficiais de dia gerir presenças e licenças, e à administração controlar utilizadores, turmas e relatórios.

## Arquitectura

```
app.py                  Flask entry point (slim — re-exports, blueprints, middleware)
config.py               Configuração centralizada (env vars, logging, Flask config)

core/                   Camada de serviço (zero dependência de Flask request)
  schema.py             DDL — fonte de verdade para a estrutura da BD
  database.py           Conexões SQLite, schema init, WAL checkpoint
  bootstrap.py          Arranque: schema + migrações + backup
  migrations.py         Migrações versionadas (ALTER TABLE, data fixes, FTS repair)
  meals.py              CRUD e queries de refeições (totais, período, UPSERT + audit log)
  users.py              CRUD de utilizadores (allowlist SQL, perfis, contactos)
  auth_db.py            Autenticação (verify password, login eventos, brute-force)
  autofill.py           Auto-preenchimento de refeições predefinidas (cron)
  operations.py         Lógica operacional (presenças, licenças, detenções)
  absences.py           CRUD de ausências
  detencoes.py          CRUD de detenções
  audit.py              Log de auditoria admin
  calendar.py           Calendário operacional
  menus.py              Ementas diárias
  companhias.py         Gestão de turmas/companhias e promoções
  backup.py             Backup diário, restauro, validação, limpeza automática
  middleware.py         Before/after request, CSP, métricas, error handlers
  constants.py          Constantes globais (paths, limites, perfis dev)

blueprints/             Rotas HTTP organizadas por domínio
  auth/                 Login, logout, change password
  aluno/                Home do aluno, marcar refeições, perfil, ausências, histórico
  cmd/                  Oficial de dia — gerir alunos, editar refeições, detenções
  operations/           Painel operacional, presenças, licenças, relatório semanal
  admin/                Gestão de utilizadores, calendário, menus, auditoria, backup, companhias
  reporting/            Dashboard semanal, exportação CSV/XLSX
  api/                  Endpoints JSON (autofill, métricas, health)

utils/                  Helpers partilhados
  helpers.py            Rendering, CSRF, datas, _refeicao_set, audit, Markup helpers
  validators.py         Validação de inputs (email, NII, NI, datas, etc.)
  constants.py          Constantes de UI (anos, dias, perfis, regex)
  passwords.py          Hash/check passwords, criar/eliminar utilizadores
  business.py           Regras de negócio (ausências, detenções, licenças, ocupação)
  auth.py               Decoradores de auth (login_required, role_required)

templates/              Jinja2 templates (extends base.html)
static/                 CSS e JS externos (app.css, meal-editor.css, app.js, etc.)
tests/                  717 testes pytest (cobertura 92%)
```

## Perfis de utilizador

| Perfil | Descrição | Acesso |
|--------|-----------|--------|
| `aluno` | Cadete | Marcar refeições, ver perfil, gerir ausências |
| `oficialdia` | Oficial de dia | Painel operacional, presenças, licenças, detenções |
| `cozinha` | Cozinha | Painel operacional (só totais, sem dados pessoais) |
| `admin` | Administrador | Tudo: utilizadores, calendário, menus, auditoria, backup, companhias |

## Setup local

> **Requisito:** Python **3.11+** (as passwords usam `scrypt`, que não é suportado no Python 3.9 do macOS).
> No macOS com Homebrew: `brew install python@3.11`

```bash
# 1. Clonar e instalar
git clone https://github.com/MakaG222/sistema-refeicoes.git
cd sistema-refeicoes

# 2. Criar venv com Python 3.11+
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 3. (Opcional) Instalar ferramentas de desenvolvimento
pip install -r requirements-dev.txt

# 4. Arrancar
python app.py
# → http://localhost:8080
```

Se a porta 8080 estiver ocupada:
```bash
lsof -ti:8080 | xargs kill -9    # libertar a porta
python app.py
```

A base de dados SQLite é criada automaticamente no primeiro arranque.
Em desenvolvimento, contas de teste são criadas via `PERFIS_ADMIN` / `PERFIS_TESTE` em `core/constants.py`.

**Contas de teste (desenvolvimento):**

| Login | Password | Perfil |
|-------|----------|--------|
| `admin` | `admin123` | Administrador |
| `cmd1`–`cmd8` | `cmd{N}123` | Comandante de ano |
| `cozinha` | `cozinha123` | Cozinha |
| `oficialdia` | `oficial123` | Oficial de dia |
| `teste1`–`teste15` | `teste{N}` | Aluno |

### Comandos CLI (Flask)

```bash
export FLASK_APP=app.py

# Seed de contas de desenvolvimento
flask seed-dev

# Aplicar migrações pendentes
flask migrate

# Backup manual
flask backup

# Listar backups disponíveis
flask backup-list

# Restaurar BD a partir de backup
flask restore backups/sistema_20260322.db
```

### Documentação operacional

Ver [OPERATIONS.md](OPERATIONS.md) para procedimentos detalhados de:
instalação, backup/restauro, atualização, configuração de admins e troubleshooting.

## Testes e qualidade

```bash
# Correr testes com cobertura
coverage run -m pytest tests/ -v --tb=short
coverage report --fail-under=90

# Lint
ruff check . && ruff format --check .

# Segurança
bandit -r core/ blueprints/ utils/ app.py -ll
pip-audit
```

## Deploy (produção)

### Variáveis de ambiente obrigatórias

```
ENV=production
SECRET_KEY=<random 32+ chars>
CRON_API_TOKEN=<random 32+ chars>
DB_PATH=/mnt/data/sistema.db   # volume persistente
```

### Railway / Docker

```bash
gunicorn app:app --workers 2 --bind 0.0.0.0:$PORT
```

A sessão expira após 10 minutos de inactividade.
Cookies são `HttpOnly`, `SameSite=Lax`, e `Secure` em produção.

## Segurança

- **CSP**: `script-src 'self'; style-src 'self'` — zero inline JavaScript e zero inline CSS
- **CSRF**: Token por sessão, validado em todos os POST (excepto API)
- **Brute-force**: Bloqueio após tentativas falhadas (por NII e por IP)
- **SQL**: Queries parametrizadas; colunas dinâmicas validadas via allowlist
- **Bandit**: Scan sem skips globais — todas as supressões são locais e justificadas
- **Audit log**: Todas as acções admin são registadas com actor, acção e detalhe

## Fluxo de dados (refeições)

```
Aluno marca refeição (POST /aluno/editar/<data>)
  → _refeicao_set() [utils/helpers.py]
    → refeicao_save() [core/meals.py]
      → UPSERT refeicoes + audit log (refeicoes_log)
      → Triggers validam valores (0/1, Normal/Veg/Dieta)
      → Triggers verificam capacidade → capacidade_excessos

Painel operacional (GET /ops/painel)
  → get_totais_dia() [core/meals.py]
    → JOIN utilizadores (is_active=1, sem ausência)
    → Retorna totais por tipo (PA, lanche, almoço N/V/D, jantar N/V/D, estufa)

Exportação (GET /reporting/exportar)
  → get_totais_periodo() → CSV/XLSX com todas as colunas
```

## Licença

Projecto interno da Escola Naval Portuguesa.
