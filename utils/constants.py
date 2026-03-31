"""Constantes partilhadas do sistema de refeições."""

import re

# ── Anos / turmas ────────────────────────────────────────────────────────
ANOS_LABELS = {
    1: "1\u00ba Ano",
    2: "2\u00ba Ano",
    3: "3\u00ba Ano",
    4: "4\u00ba Ano",
    5: "5\u00ba Ano",
    6: "6\u00ba Ano",
    7: "CFBO",
    8: "CFCO",
}
ANOS_OPCOES = [
    (1, "1\u00ba Ano"),
    (2, "2\u00ba Ano"),
    (3, "3\u00ba Ano"),
    (4, "4\u00ba Ano"),
    (5, "5\u00ba Ano"),
    (6, "6\u00ba Ano"),
    (7, "CFBO \u2014 Curso de Forma\u00e7\u00e3o B\u00e1sica de Oficiais"),
    (8, "CFCO \u2014 Curso de Forma\u00e7\u00e3o Complementar de Oficiais"),
]

NOMES_DIAS = [
    "Segunda",
    "Ter\u00e7a",
    "Quarta",
    "Quinta",
    "Sexta",
    "S\u00e1bado",
    "Domingo",
]
ABREV_DIAS = ["Seg", "Ter", "Qua", "Qui", "Sex", "S\u00e1b", "Dom"]

# ── Whitelists de perfis / tipos ─────────────────────────────────────────
_PERFIS_VALIDOS = {"admin", "cmd", "cozinha", "oficialdia", "aluno"}
_TIPOS_CALENDARIO = {"normal", "fim_semana", "feriado", "exercicio", "outro"}
_REFEICAO_OPCOES = {"Normal", "Vegetariano", "Dieta", ""}

# ── Regex de validacao ───────────────────────────────────────────────────
_RE_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_RE_PHONE = re.compile(r"^[\d\s\+\-\(\)]{7,20}$")
_RE_ALNUM = re.compile(r"^[A-Za-z0-9_]+$")

# ── Limites ──────────────────────────────────────────────────────────────
_MAX_NOME = 200
_MAX_TEXT = 500
_MAX_DATE_RANGE = 366

# ── Segurança / login ────────────────────────────────────────────────────
LOGIN_BLOCK_MINUTES: int = 15
"""Tempo de bloqueio de conta após falhas consecutivas."""
LOGIN_MAX_FAILURES: int = 5
"""Tentativas falhadas antes de bloquear conta."""
IP_RATE_LIMIT_WINDOW: int = 15
"""Janela (minutos) para contar falhas por IP."""
IP_RATE_LIMIT_MAX: int = 20
"""Máximo de falhas por IP antes de bloquear."""
MAX_PASSWORD_LEN: int = 256
"""Comprimento máximo de password aceite."""

# ── Mensagens padronizadas ────────────────────────────────────────────────
MSG_NAO_ENCONTRADO = "Registo não encontrado."
MSG_ID_INVALIDO = "Identificador inválido."
MSG_ERRO_INTERNO = "Erro interno. Tenta novamente."
MSG_SUCESSO_CRIAR = "Registo criado com sucesso."
MSG_SUCESSO_EDITAR = "Dados atualizados."
MSG_SUCESSO_ELIMINAR = "Registo eliminado."
MSG_NAO_AUTORIZADO = "Não autorizado."
