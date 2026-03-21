"""Inicializar a base de dados do Sistema de Refeicoes.

Uso:
    python scripts/init_db.py              # Cria schema vazio
    python scripts/init_db.py --seed-dev   # Cria schema + contas de desenvolvimento

Este script cria o ficheiro sistema.db (ou o caminho em DB_PATH) com o schema
completo. Com --seed-dev, cria tambem as contas de sistema (admin, cmd1-8,
cozinha, oficialdia) e contas de teste (teste1-15).

Requisitos:
    pip install -r requirements.txt
"""

import argparse
import sys
from pathlib import Path

# Adicionar raiz do projeto ao path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from werkzeug.security import generate_password_hash

import core.constants
from core.database import ensure_schema, db


def seed_dev_accounts():
    """Cria contas de desenvolvimento (PERFIS_ADMIN + PERFIS_TESTE)."""
    from core.constants import PERFIS_ADMIN, PERFIS_TESTE

    perfis = {**PERFIS_ADMIN, **PERFIS_TESTE}
    if not perfis:
        print("Nenhum perfil de desenvolvimento definido.")
        return

    conn = db()
    try:
        for nii, p in perfis.items():
            row = conn.execute(
                "SELECT id FROM utilizadores WHERE NII=?", (nii,)
            ).fetchone()
            if row is not None:
                continue
            pw_hash = generate_password_hash(p.get("senha", ""))
            nome = p.get("nome", nii)
            perfil = p.get("perfil", "aluno")
            ano = str(p.get("ano", "") or "")
            conn.execute(
                """INSERT INTO utilizadores
                (NII, NI, Nome_completo, Palavra_chave, ano, perfil,
                 must_change_password, password_updated_at, is_active)
                VALUES (?, ?, ?, ?, ?, ?, 0, datetime('now','localtime'), 1)""",
                (nii, nii, nome, pw_hash, ano, perfil),
            )
        conn.commit()
        print(f"  {len(perfis)} contas de desenvolvimento criadas/verificadas.")
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="Inicializar base de dados.")
    parser.add_argument(
        "--seed-dev",
        action="store_true",
        help="Criar contas de desenvolvimento (admin, cmd, cozinha, teste1-15)",
    )
    args = parser.parse_args()

    db_path = core.constants.BASE_DADOS
    print(f"Base de dados: {db_path}")

    existed = Path(db_path).exists()
    ensure_schema()
    if existed:
        print("  Schema atualizado (tabelas em falta criadas).")
    else:
        print("  Base de dados criada com schema completo.")

    if args.seed_dev:
        seed_dev_accounts()

    print("Pronto.")


if __name__ == "__main__":
    main()
