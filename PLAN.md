# Plano de Desenvolvimento — Sistema de Refeições

## Estado actual (Março 2026)

| Métrica | Valor |
|---------|-------|
| Testes  | 583 (0 falhas) |
| Cobertura | **92%** (mínimo CI: 90%) |
| Lint    | ruff check + format — 0 avisos |
| Segurança | bandit -ll — 0 issues (zero skips globais) |
| CSP     | `script-src 'self'` — sem `unsafe-inline` |

## Fases concluídas

### Fase 0: Fundação
- [x] Registar helpers (`_back_btn`, `_bar_html`, `_prazo_label`, `_ano_label`) como Jinja2 globals
- [x] Modificar helpers para retornar `Markup()`
- [x] Testes baseline

### Fase 1: Converter templates (por blueprint)
- [x] Batch 1: reporting (2 rotas) — inclui fix calendario_publico
- [x] Batch 2: cmd (4 rotas)
- [x] Batch 3: aluno (6 rotas)
- [x] Batch 4: operations (7 rotas)
- [x] Batch 5: admin (8 rotas)

### Fase 2: Testes 70%+ → 92%
- [x] test_reporting_routes.py
- [x] test_cmd_routes.py
- [x] test_aluno_routes.py
- [x] test_operations_routes.py
- [x] test_admin_routes.py
- [x] test_utils.py
- [x] test_bootstrap.py
- [x] test_database.py
- [x] test_config.py
- [x] test_security.py
- [x] test_api_routes.py
- [x] test_auth_routes.py
- [x] test_companhias_core.py
- [x] test_admin_companhias.py

### Fase 3: Modularização (P1–P8)
- [x] P1: Extrair admin/routes.py → sub-módulos (users, audit, calendar, menus, backup, companhias)
- [x] P2: Extrair SQL de blueprints → core/ (meals, users, absences, detencoes, audit, calendar, etc.)
- [x] P3: Slim app.py — re-exports, sem lógica
- [x] P4: CLI commands (seed-dev, migrate)
- [x] P5-P8: testes 92%, zero SQL em rotas, middleware, constantes

### Fase 4: Features
- [x] Estufa (♨️) — hot-holding para almoço e jantar
- [x] Refeições predefinidas (core/autofill.py + API)
- [x] Gestão de companhias/turmas
- [x] Capacidade de refeições + alertas

### Fase 5: Dívida técnica & Segurança
- [x] SQL dinâmico: allowlist de colunas em get_user_by_nii_fields()
- [x] Bandit: zero skips globais — tudo suprimido inline com justificação
- [x] CSP: `script-src 'self'` (sem unsafe-inline), CSS/JS extraído para static/
- [x] Migrações versionadas (core/migrations.py) — separadas do bootstrap

## Limpeza final
- [x] Corrigir templates híbridos (perfil_aluno, editar_aluno)
- [x] ruff check + format
- [x] pytest --cov final = **92%** (583 testes, 0 falhas)
