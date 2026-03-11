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
