# Changelog

Todas as alterações notáveis ao Sistema de Refeições são documentadas aqui.

Formato baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/),
versionamento segue [Semantic Versioning](https://semver.org/lang/pt-BR/).

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
