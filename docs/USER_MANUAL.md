# Manual de Utilização — por perfil

## Índice

- [Aluno](#aluno)
- [Oficial-dia](#oficial-dia)
- [Cozinha](#cozinha)
- [CMD](#cmd)
- [Administrador](#administrador)
- [Atalhos de teclado (todos os perfis)](#atalhos-de-teclado-todos-os-perfis)

---

## Aluno

### Login

1. Abrir a aplicação. Campo "NII" + "Password".
2. No primeiro login (ou após reset), és forçado a mudar a password.

### Marcar refeições da semana

1. Menu → **📅 Editar refeições**.
2. Tabela com os 7 dias: linhas = dias, colunas = Pequeno-almoço / Almoço
   / Janta.
3. Clica nos emojis para alternar **✅ presente** / **❌ ausente** /
   **🏥 licença**.
4. O contador de SLA no topo diz-te quanto falta até ao deadline
   (tipicamente ter 20:00 da semana anterior).
5. **Não há botão guardar** — cada clique guarda imediatamente.

### Histórico

Menu → **📜 Histórico**. Lista das últimas refeições marcadas com filtros
por mês.

### Exportar para Calendar (.ics)

No perfil, botão **📅 Exportar para Calendar (30d)**. Descarrega um
ficheiro `.ics` com as refeições marcadas para os próximos 30 dias.

**Como importar:**
- **Google Calendar**: Settings → Import & Export → seleccionar o `.ics`
- **Apple Calendar**: File → Import → escolher o `.ics`
- **Outlook**: File → Open → Import → iCalendar (.ics)

Cada refeição vira um evento no horário correspondente:
- Pequeno-Almoço: 07:00–09:30
- Almoço: 12:00–14:00 (com variante: Normal/Vegetariano/Dieta + ♨ se estufa)
- Lanche: 16:00–17:30
- Jantar: 19:00–21:00 (idem variantes; "sai da unidade" se aplicável)

**Janela personalizada:** adicionar `?days=N` ao URL (1-90). Ex:
`/aluno/refeicoes.ics?days=14` para 2 semanas.

**Re-importar actualiza** os mesmos events (UIDs estáveis) — não duplica.
Para subscrição contínua (calendar app a refrescar automaticamente), v2.

### QR pessoal

No perfil, botão **📱 Mostrar QR**. Usado no kiosk de check-in no refeitório
para marcar presença real. Ninguém te vê o QR além de quem o scaneia.

### Licenças

Se vais ter licença/dispensa médica prolongada, o oficial-dia ou admin
marca-te as datas. Durante o período, as refeições ficam "licença"
automaticamente.

### Dietas permanentes

Se tens dieta permanente (ex: vegetariana, sem lactose), o admin configura-te
o perfil. Todas as refeições futuras são marcadas no tipo correcto sem
intervenção tua.

---

## Oficial-dia

### Painel diário

`/operations/painel` — visão do dia com:

- **Contagem prevista** (quem marcou o quê, por turma/ano).
- **SLA**: quem ainda não marcou e está próximo do deadline.
- **Licenças activas**.
- **Check-in**: link para kiosk.

### Kiosk de check-in

`/operations/checkin` — página fullscreen optimizada para tablet/leitor
de QR no refeitório.

1. Aluno apresenta QR no perfil.
2. Câmara lê → POST para marcar presença real.
3. Feedback instantâneo: verde (OK) / vermelho (aluno não marcou / sem refeição).

### Presenças e licenças

`/operations/presencas` — registar manualmente quando aluno não tem QR ou
houve falha. Tabela editável dia-a-dia.

`/operations/licencas` — criar/editar períodos de licença médica.

### Forecast semanal

`/operations/forecast` — projecção de refeições para a próxima semana,
baseada nas marcações já feitas + histórico. Ajuda a cozinha a planear
compras.

---

## Cozinha

### Painel

`/reporting/painel` — só totais agregados por refeição e dieta. Nada de
nomes de alunos (privacy-friendly).

### Menus

`/reporting/menus` — consulta dos menus do mês, PDF da ementa.

### Export mensal

`/reporting/exportar/mensal` — download CSV com contagens diárias por
dieta. Equivalente a `/api/export-cron` mas manual.

---

## CMD

### Gerir ano

`/cmd/alunos` — lista de alunos do ano. Editar refeições, registar
detenções.

### Detenções

`/cmd/detencoes` — criar detenção para um aluno. Marca automaticamente as
refeições do período como "ausente" e impede o aluno de as editar.

### Editar refeições do aluno

Modo admin-by-proxy: abre o editor de refeições do aluno (todas as datas,
mesmo já no passado) — útil para corrigir após o deadline.

---

## Administrador

### Utilizadores

`/admin/utilizadores` — CRUD completo.

- **Filtros**: por nome (FTS), por ano, por perfil.
- **Paginação**: 50 por página.
- **Importar CSV**: menu "📥 Importar CSV" — aceita `NII;NI;Nome;Ano`,
  preview antes de commit.
- **Editar contactos**: botão ✉️ — só email e telemóvel.
- **Editar user completo**: botão ✏️ Editar — nome, NI, ano, perfil,
  password.
- **Reset password**:
  - Rápido: botão "Reset pw = NII" — password temporária = NII (deprecado
    por segurança, ainda disponível).
  - **Recomendado**: botão **🔐 Gerar código de reset (24h)** — código
    single-use, TTL 24h, mostrado 1×. O aluno faz login com o código
    como password e é redirecccionado para mudar.
- **Desbloquear conta**: botão 🔓 (só visível quando bloqueado).
- **Eliminar**: botão 🗑 (confirmação obrigatória).

### Menus

`/admin/menus` — editor de ementas por semana. Suporta ementas especiais
(dietas), copy-paste entre semanas.

### Calendário

`/admin/calendario` — dias especiais (feriados, licenças colectivas,
autopreencher manual).

### Auditoria

`/admin/auditoria` — log de todas as acções administrativas. Filtros por
actor (NII) e action. Paginação 50/página ou `?limite=N` (legacy).
Export CSV disponível.

### Log de alterações

`/admin/log` — log granular de alterações a refeições dos alunos (quem
mudou o quê, quando, valor antes/depois). Útil para disputas.

### Backup

`/admin/backup` — botão "Criar backup agora". Lista backups recentes.
Download directo. Restore só via CLI (ver [RUNBOOK](RUNBOOK.md#restore-de-backup)).

### Companhias

`/admin/companhias` — gerir estrutura organizacional (turmas, companhias,
sub-grupos). Afecta filtros e relatórios.

### Dietas permanentes

`/admin/dietas` — atribuir dieta permanente a aluno (ex: vegetariano). O
sistema aplica automaticamente em todas as marcações futuras.

### Notificações

Se configurado (`NOTIFICATION_WEBHOOK_URL`), envia webhook para eventos
importantes (login falha, backup fail, rate-limit atingido). Ver
`core/notifications.py`.

---

## Atalhos de teclado (todos os perfis)

Pressionar `?` em qualquer página → overlay com todos os atalhos.

| Atalho        | Efeito                                            |
|---------------|---------------------------------------------------|
| `?`           | Mostrar / esconder overlay de atalhos             |
| `Ctrl+S`      | Submeter formulário principal (se marcado)       |
| `Ctrl+P`      | Imprimir (em páginas marcadas `data-printable`)  |
| `←` / `→`     | Navegar entre dias no editor de refeições         |
| `Escape`      | Fechar modais / toasts                            |
| `Tab` (1ª vez)| Mostrar skip-link → saltar para conteúdo          |

### Dark mode

Botão no topo direito (🌙 / ☀). Persiste em `localStorage`. Primeira
visita: respeita `prefers-color-scheme` do sistema.

### Toasts de feedback

Mensagens de sucesso/erro aparecem no canto inferior-direito, fecham
automaticamente após 5s ou clique no ✕.
