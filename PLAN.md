# Plano: Templates Jinja2 + Cobertura 70%

## Fase 0: Fundação
- [x] Registar helpers (_back_btn, _bar_html, _prazo_label, _ano_label) como Jinja2 globals
- [x] Modificar helpers para retornar Markup()
- [x] Verificar testes baseline

## Fase 1: Converter templates (por blueprint)
- [x] Batch 1: reporting (2 rotas) — inclui fix calendario_publico
- [x] Batch 2: cmd (4 rotas)
- [x] Batch 3: aluno (6 rotas)
- [x] Batch 4: operations (7 rotas)
- [x] Batch 5: admin (8 rotas)

## Fase 2: Testes para 70%+ cobertura
- [x] test_reporting_routes.py
- [x] test_cmd_routes.py
- [x] test_aluno_routes.py (estender)
- [x] test_operations_routes.py
- [x] test_admin_routes.py
- [x] test_utils.py

## Limpeza final
- [x] Corrigir templates híbridos (perfil_aluno, editar_aluno) — passar variáveis ao template
- [x] ruff check + format
- [x] pytest --cov final = **76%** ✅ (267 testes, 0 falhas)
