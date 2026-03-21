"""Constantes partilhadas — caminhos, limites e perfis de sistema."""

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Caminhos e diretórios
# ---------------------------------------------------------------------------
DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sistema.db"
)
BASE_DADOS = os.getenv("DB_PATH", DB_PATH)
BACKUP_DIR = "backups"
EXPORT_DIR = "exportacoes"
Path(BACKUP_DIR).mkdir(exist_ok=True)
Path(EXPORT_DIR).mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# Limites operacionais
# ---------------------------------------------------------------------------
PRAZO_LIMITE_HORAS = 48
BACKUP_RETENCAO_DIAS = 30

# ---------------------------------------------------------------------------
# Perfis de sistema (contas admin/cmd/cozinha/oficialdia)
# ---------------------------------------------------------------------------
PERFIS_ADMIN = {
    "admin": {
        "senha": "admin123",
        "nome": "Administrador Geral",
        "perfil": "admin",
        "ano": "",
    },
    "cmd1": {
        "senha": "cmd1123",
        "nome": "Comandante 1\u00ba Ano",
        "perfil": "cmd",
        "ano": "1",
    },
    "cmd2": {
        "senha": "cmd2123",
        "nome": "Comandante 2\u00ba Ano",
        "perfil": "cmd",
        "ano": "2",
    },
    "cmd3": {
        "senha": "cmd3123",
        "nome": "Comandante 3\u00ba Ano",
        "perfil": "cmd",
        "ano": "3",
    },
    "cmd4": {
        "senha": "cmd4123",
        "nome": "Comandante 4\u00ba Ano",
        "perfil": "cmd",
        "ano": "4",
    },
    "cmd5": {
        "senha": "cmd5123",
        "nome": "Comandante 5\u00ba Ano",
        "perfil": "cmd",
        "ano": "5",
    },
    "cmd6": {
        "senha": "cmd6123",
        "nome": "Comandante 6\u00ba Ano",
        "perfil": "cmd",
        "ano": "6",
    },
    "cmd7": {
        "senha": "cmd7123",
        "nome": "Comandante CFBO",
        "perfil": "cmd",
        "ano": "7",
    },
    "cmd8": {
        "senha": "cmd8123",
        "nome": "Comandante CFCO",
        "perfil": "cmd",
        "ano": "8",
    },
    "cozinha": {
        "senha": "cozinha123",
        "nome": "Respons\u00e1vel da Cozinha",
        "perfil": "cozinha",
        "ano": "",
    },
    "oficialdia": {
        "senha": "oficial123",
        "nome": "Oficial de Dia",
        "perfil": "oficialdia",
        "ano": "",
    },
}

_ENV = os.getenv("ENV", "development").lower()

if _ENV != "production":
    PERFIS_TESTE = {
        f"teste{i}": {
            "senha": f"teste{i}",
            "nome": f"Utilizador Teste {i}",
            "perfil": "aluno",
            "ano": "1",
        }
        for i in range(1, 16)
    }
else:
    PERFIS_TESTE = {}
