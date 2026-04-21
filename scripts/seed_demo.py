#!/usr/bin/env python3
"""
scripts/seed_demo.py — popular BD com dados realistas para testes UAT
=====================================================================

Cria:
- 200 alunos distribuídos por 5 anos (1º a 5º ano), 40 por ano
- 3 oficiais de dia, 2 admin, 2 cozinha, 1 cmd por ano (5)
- 8 semanas de refeições marcadas (passadas + futuras)
- 10 licenças activas ou futuras
- 3 detenções
- 20 ementas (últimas 4 semanas)

Uso:
    python scripts/seed_demo.py                  # dados padrão
    python scripts/seed_demo.py --wipe           # apaga BD antes
    python scripts/seed_demo.py --alunos 500     # mais alunos

AVISO: este script ESCREVE em `sistema.db`. Fazer backup antes se
quiseres preservar dados existentes:
    cp sistema.db sistema.db.pre-seed
"""

from __future__ import annotations

import argparse
import os
import random
import sys
from datetime import date, timedelta
from pathlib import Path

# Permite correr sem ter de mexer em PYTHONPATH:
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("ENV", "development")
os.environ.setdefault("SECRET_KEY", "seed-demo-not-for-production")

from core.bootstrap import ensure_extra_schema  # noqa: E402
from core.database import db, ensure_schema  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402


# ── Dados realistas (nomes portugueses, turmas, etc.) ────────────────────────

PRIMEIROS = [
    "João",
    "Miguel",
    "Pedro",
    "André",
    "Ricardo",
    "Tiago",
    "Bruno",
    "Diogo",
    "Rafael",
    "Rui",
    "Nuno",
    "Gonçalo",
    "Francisco",
    "Afonso",
    "Tomás",
    "Carlos",
    "Luís",
    "José",
    "António",
    "Manuel",
    "Maria",
    "Ana",
    "Catarina",
    "Sofia",
    "Inês",
    "Beatriz",
    "Margarida",
    "Mariana",
    "Leonor",
    "Joana",
    "Carolina",
    "Matilde",
    "Rita",
]
APELIDOS = [
    "Silva",
    "Santos",
    "Ferreira",
    "Pereira",
    "Oliveira",
    "Costa",
    "Rodrigues",
    "Martins",
    "Almeida",
    "Gomes",
    "Carvalho",
    "Lopes",
    "Ribeiro",
    "Marques",
    "Sousa",
    "Fernandes",
    "Mendes",
    "Nunes",
    "Cardoso",
    "Cruz",
    "Pinto",
    "Moreira",
    "Teixeira",
    "Correia",
]


def _nome_aleatorio() -> str:
    return f"{random.choice(PRIMEIROS)} {random.choice(APELIDOS)} {random.choice(APELIDOS)}"


def _password_dev(nii: str) -> str:
    """Password previsível para UAT: <nii>X9k (cumpre policy)."""
    return f"{nii}X9k"


# ── Seed functions ───────────────────────────────────────────────────────────


def wipe_db() -> None:
    """Apaga dados transitórios — mantém schema."""
    with db() as conn:
        conn.executescript("""
            DELETE FROM refeicoes_decisoes;
            DELETE FROM meal_log;
            DELETE FROM login_eventos;
            DELETE FROM admin_audit_log;
            DELETE FROM ausencias;
            DELETE FROM licencas;
            DELETE FROM detencoes;
            DELETE FROM menus_diarios;
            DELETE FROM utilizadores WHERE NII NOT IN ('admin');
        """)
        conn.commit()
    print("⚠  BD limpa (schema preservado, user 'admin' preservado).")


def seed_users(n_alunos: int = 200) -> list[str]:
    """Cria n_alunos alunos + oficiais/admin/cozinha/cmd. Retorna lista de NIIs."""
    inseridos: list[str] = []
    alunos_por_ano = n_alunos // 5
    with db() as conn:
        # Sistema users
        system_users = [
            ("of1", "of1", "Oficial Dia Alpha", 0, "oficialdia"),
            ("of2", "of2", "Oficial Dia Bravo", 0, "oficialdia"),
            ("of3", "of3", "Oficial Dia Charlie", 0, "oficialdia"),
            ("cozinha1", "cz1", "Cozinha Chefe", 0, "cozinha"),
            ("cozinha2", "cz2", "Cozinha Aux", 0, "cozinha"),
            ("admin2", "ad2", "Admin Secundário", 0, "admin"),
        ]
        for ano in range(1, 6):
            system_users.append((f"cmd{ano}", f"cmd{ano}", f"CMD {ano}º Ano", 0, "cmd"))
        for nii, ni, nome, ano, perfil in system_users:
            pw_hash = generate_password_hash(_password_dev(nii))
            conn.execute(
                """INSERT OR IGNORE INTO utilizadores
                   (NII,NI,Nome_completo,Palavra_chave,ano,perfil,must_change_password)
                   VALUES (?,?,?,?,?,?,0)""",
                (nii, ni, nome, pw_hash, ano, perfil),
            )
            inseridos.append(nii)

        # Alunos
        for ano in range(1, 6):
            for i in range(alunos_por_ano):
                nii = f"a{ano}{i:03d}"
                ni = f"NI{ano}{i:04d}"
                nome = _nome_aleatorio()
                pw_hash = generate_password_hash(_password_dev(nii))
                conn.execute(
                    """INSERT OR IGNORE INTO utilizadores
                       (NII,NI,Nome_completo,Palavra_chave,ano,perfil,must_change_password)
                       VALUES (?,?,?,?,?,'aluno',0)""",
                    (nii, ni, nome, pw_hash, ano),
                )
                inseridos.append(nii)
        conn.commit()
    print(f"✓ {len(inseridos)} utilizadores criados (alunos + sistema).")
    return inseridos


def seed_refeicoes(alunos: list[str], semanas: int = 8) -> int:
    """Marca refeições para alunos nas últimas `semanas//2` e próximas `semanas//2`."""
    start = date.today() - timedelta(days=7 * (semanas // 2))
    count = 0
    with db() as conn:
        for i in range(semanas * 7):
            d = (start + timedelta(days=i)).isoformat()
            # 85% dos alunos marcam
            for nii in random.sample(alunos, int(len(alunos) * 0.85)):
                # pa=85%, lanche=40%, almoço_n=70%, almoço_v=10%, janta_n=75%, janta_v=8%
                decisoes = {
                    "pa": 1 if random.random() < 0.85 else 0,
                    "lanche": 1 if random.random() < 0.40 else 0,
                    "almoco_normal": 1 if random.random() < 0.70 else 0,
                    "almoco_veg": 1 if random.random() < 0.10 else 0,
                    "janta_normal": 1 if random.random() < 0.75 else 0,
                    "janta_veg": 1 if random.random() < 0.08 else 0,
                }
                cols = ["NII", "data_refeicao", *decisoes.keys()]
                vals = [nii, d, *decisoes.values()]
                placeholders = ",".join(["?"] * len(cols))
                try:
                    conn.execute(
                        f"INSERT OR IGNORE INTO refeicoes_decisoes ({','.join(cols)})"  # nosec B608
                        f" VALUES ({placeholders})",
                        vals,
                    )
                    count += 1
                except Exception:
                    pass
        conn.commit()
    print(f"✓ {count} decisões de refeição inseridas ({semanas} semanas).")
    return count


def seed_menus(n_semanas: int = 4) -> int:
    """Cria menus diários para as últimas n_semanas + próximas n_semanas."""
    pratos_almoco = [
        "Bacalhau à Brás",
        "Bitoque com ovo",
        "Lasanha de carne",
        "Frango assado com batata",
        "Rojões à minhota",
        "Peixe grelhado",
        "Arroz de pato",
        "Salmão no forno",
        "Favada com carnes",
    ]
    pratos_janta = [
        "Sopa e omeleta",
        "Bifinhos de peru",
        "Massa à bolonhesa",
        "Carapaus grelhados",
        "Pizza caseira",
        "Hambúrguer com batata",
        "Creme de legumes e tarte",
        "Arroz chau-chau",
    ]
    start = date.today() - timedelta(days=7 * n_semanas)
    count = 0
    with db() as conn:
        for i in range(n_semanas * 2 * 7):
            d = (start + timedelta(days=i)).isoformat()
            conn.execute(
                "INSERT OR IGNORE INTO menus_diarios (data, almoco, janta)"
                " VALUES (?,?,?)",
                (d, random.choice(pratos_almoco), random.choice(pratos_janta)),
            )
            count += 1
        conn.commit()
    print(f"✓ {count} menus diários criados.")
    return count


def seed_licencas(alunos: list[str], n: int = 10) -> int:
    """Cria n licenças médicas em alunos aleatórios."""
    today = date.today()
    count = 0
    with db() as conn:
        for nii in random.sample(alunos, min(n, len(alunos))):
            ini = today + timedelta(days=random.randint(-7, 14))
            fim = ini + timedelta(days=random.randint(2, 21))
            try:
                conn.execute(
                    "INSERT INTO licencas (NII, data_inicio, data_fim, motivo)"
                    " VALUES (?,?,?,?)",
                    (nii, ini.isoformat(), fim.isoformat(), "Atestado médico"),
                )
                count += 1
            except Exception:
                pass
        conn.commit()
    print(f"✓ {count} licenças inseridas.")
    return count


def seed_detencoes(alunos: list[str], n: int = 3) -> int:
    """Cria n detenções em alunos aleatórios."""
    today = date.today()
    count = 0
    with db() as conn:
        for nii in random.sample(alunos, min(n, len(alunos))):
            ini = today + timedelta(days=random.randint(0, 7))
            fim = ini + timedelta(days=random.randint(2, 7))
            try:
                conn.execute(
                    "INSERT INTO detencoes (NII, data_inicio, data_fim, motivo)"
                    " VALUES (?,?,?,?)",
                    (nii, ini.isoformat(), fim.isoformat(), "Infracção disciplinar"),
                )
                count += 1
            except Exception:
                pass
        conn.commit()
    print(f"✓ {count} detenções inseridas.")
    return count


# ── CLI ─────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wipe", action="store_true", help="Apaga dados antes de seed")
    parser.add_argument("--alunos", type=int, default=200, help="N de alunos a criar")
    parser.add_argument("--semanas", type=int, default=8, help="Semanas de refeições")
    parser.add_argument(
        "--seed", type=int, default=42, help="Random seed (reproducível)"
    )
    args = parser.parse_args()

    random.seed(args.seed)

    ensure_schema()
    ensure_extra_schema()
    print(f"→ BD: {os.environ.get('DB_PATH', 'sistema.db')}")

    if args.wipe:
        wipe_db()

    alunos_niis = seed_users(n_alunos=args.alunos)
    # Só os NIIs que começam por 'a' (alunos) para refeições/licenças/detenções
    alunos_reais = [n for n in alunos_niis if n.startswith("a")]

    seed_menus()
    seed_refeicoes(alunos_reais, semanas=args.semanas)
    seed_licencas(alunos_reais)
    seed_detencoes(alunos_reais)

    print()
    print("✅ Seed completo.")
    print()
    print("Logins de teste (password = <NII>X9k):")
    print("  admin2   (admin)       pw: admin2X9k")
    print("  of1      (oficialdia)  pw: of1X9k")
    print("  cozinha1 (cozinha)     pw: cozinha1X9k")
    print("  cmd1     (cmd ano 1)   pw: cmd1X9k")
    print("  a1000    (aluno 1ºano) pw: a1000X9k")


if __name__ == "__main__":
    main()
