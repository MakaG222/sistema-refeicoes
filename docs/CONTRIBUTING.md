# Contributing

Bem-vindo. Este projecto segue um fluxo simples — pull-request-based,
`main` sempre deployable, CI obrigatório.

## Setup

```bash
# 1. Clonar
git clone https://github.com/MakaG222/sistema-refeicoes.git
cd sistema-refeicoes

# 2. Venv (Python 3.11+)
python3.11 -m venv .venv
source .venv/bin/activate

# 3. Dependências
pip install -r requirements.txt -r requirements-dev.txt

# 4. Env mínimo
cp .env.example .env
# editar SECRET_KEY (obrigatório)

# 5. Arrancar dev server
flask --app app run --debug
# → http://127.0.0.1:5000
```

Login inicial (só em `ENV=development`): ver `core/bootstrap.py` — cria
`admin`/`admin123`, `aluno1`/`aluno1`, etc., em BD vazia.

## Branch naming

- **Features**: `feat/<scope>-<short-desc>` — ex: `feat/pwa-offline-sync`
- **Fixes**: `fix/<scope>-<short-desc>` — ex: `fix/login-csrf-race`
- **Docs**: `docs/<short-desc>` — ex: `docs/runbook-cron`
- **Performance**: `perf/<short-desc>`
- **Refactor**: `refactor/<short-desc>`
- **Test-only**: `test/<short-desc>`
- **Segurança**: `security/<short-desc>` — merge privado quando possível.

Agent-driven: `claude/<desc>` é aceite. Branches pessoais: `<user>/<desc>`.

## Commits — conventional commits

Formato:
```
<type>(<scope>): <subject>

<body — porquê, não o quê>

<footer com Co-Authored-By ou refs #NNN>
```

Tipos: `feat`, `fix`, `perf`, `refactor`, `docs`, `test`, `chore`,
`ci`, `security`.

Exemplos observados no histórico:
- `feat(ux): toast system, dark mode, empty states, a11y e atalhos`
- `feat(security): password reset, auto-unlock, user context logs, rate-limit e paginação`
- `feat(#15): QR check-in kiosk + corrige CI`
- `perf+infra: autofill N+1, refeicao_save race, Docker e pre-commit`

**Regras**:
- Subject imperativo, ≤72 chars, sem ponto final.
- Body explica a *razão* da mudança (o diff mostra o *o quê*).
- Se resolve um ticket/issue, referência no footer: `Closes #13`.
- Sempre incluir `Co-Authored-By:` se pair-programming ou AI-assisted.

## Pre-commit hook

Instalar uma vez:
```bash
ln -s ../../.git/hooks/pre-commit ./.git/hooks/pre-commit
chmod +x .git/hooks/pre-commit
```

O hook corre (ver `.git/hooks/pre-commit`):
- `ruff check .`
- `ruff format --check .`
- Se qualquer falhar → commit abortado.

Skip só com `--no-verify` e justificação no PR (raro — só merges bot).

## Correr CI localmente

```bash
# Reproduz exactamente o que corre em GitHub Actions:
source .venv/bin/activate

ruff check .                                    # lint
ruff format --check .                           # style
bandit -r core/ blueprints/ utils/ app.py -ll   # security
pip-audit -r requirements.txt                   # CVEs em deps
coverage run -m pytest -q                       # testes
coverage report --fail-under=90                 # cobertura ≥90%
```

Todos têm de passar.

## Mudanças que exigem migração de schema

Se a mudança altera tabelas (`ADD COLUMN`, `CREATE TABLE`, `CREATE INDEX`,
etc.):

1. Adicionar função `_migration_NNN` em `core/migrations.py` com número
   sequencial seguinte ao último (ex: `009_...`).
2. A função deve ser **idempotente**: verificar `PRAGMA table_info`
   ou `sqlite_master` antes de `ALTER`.
3. Adicionar tuple `("009_nome", _migration_NNN)` à lista `MIGRATIONS`
   no fim (ordem é importante).
4. **Nunca** mudar uma migração já mergida em main — cria uma nova em
   vez disso. A tabela `_migracoes` lembra-se.
5. Teste: a suite re-cria BD do zero em `conftest.py:app()` fixture —
   qualquer migração partida rebenta 800+ testes.

Exemplo aditivo (boa prática):
```python
def _migration_NNN(conn: sqlite3.Connection) -> None:
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(utilizadores)").fetchall()]
    if "nova_coluna" not in cols:
        conn.execute("ALTER TABLE utilizadores ADD COLUMN nova_coluna TEXT")
```

Exemplo destrutivo (evitar; se inevitável → ticket de aviso aos users):
```python
def _migration_NNN(conn: sqlite3.Connection) -> None:
    conn.execute("DROP TABLE IF EXISTS legacy_thing")
```

## Testes

- `tests/test_<modulo>.py` para cada domínio.
- Novos endpoints → testes de status codes + happy path + 1 edge case.
- Novas migrações → implícito nos testes (`conftest.py` recria schema).
- Fixtures: `client`, `app`, `csrf_token`, `create_aluno`, `create_system_user`,
  `login_as`, `get_csrf` (em `conftest.py`).
- **Não** tocar em `sistema.db` real — usar a BD temporária do fixture.

Correr só um ficheiro:
```bash
PYTHONPATH=. pytest tests/test_hardening.py -q -x
```

Correr só uma classe/test:
```bash
PYTHONPATH=. pytest tests/test_hardening.py::TestResetCode -q
PYTHONPATH=. pytest tests/test_hardening.py::TestResetCode::test_consume_reset_code_expirado_falha -q
```

## Style & estrutura

- **Python**: PEP8 via ruff (configurado em `pyproject.toml`). Line length 100.
- **Imports**: stdlib → third-party → local, via `ruff` `I` rule.
- **Typing**: type hints em funções públicas. `from __future__ import annotations`
  nos módulos que usam `X | None`.
- **Docstrings**: pt-PT, curtas, explicam *porquê* / *quando usar*.
- **SQL**: parametrizado (`?` placeholders). Nada de `f"...{var}..."` em queries.
  `# nosec B608` com justificação se SQL dinâmico for inevitável.
- **Flask**: blueprint-per-domain. `blueprints/X/routes.py` tem as rotas,
  `blueprints/X/__init__.py` define o `Blueprint`.
- **Templates**: CSP-safe (zero `<script>` inline, zero `style=""` inline,
  zero `onclick=""`). Use `static/js/<feature>.js` + `data-*` attrs.

## Pull-request checklist

Antes de abrir PR:

- [ ] CI local passa (`ruff`, `bandit`, `pip-audit`, `coverage ≥90%`)
- [ ] Commits seguem conventional-commits
- [ ] Nenhum `sistema.db` modificado commitado (usar `git checkout -- sistema.db`)
- [ ] Nenhuma credencial/secret em código ou testes
- [ ] Se há migração: testes continuam a correr de schema do zero
- [ ] Documentação actualizada se API/UX mudam (README / docs/ / CHANGELOG)
- [ ] Screenshot ou GIF se mudança visual
- [ ] Self-review do diff (olhar para ele como se fosse de outra pessoa)

## Arquitectura

Ver [ARCHITECTURE.md](ARCHITECTURE.md) para decisões de design e
organização do código.

## Reportar vulnerabilidades de segurança

**Não** abrir issue público. Contactar o maintainer directamente
(ver `README.md`). Corrigido → disclosure coordenada.
