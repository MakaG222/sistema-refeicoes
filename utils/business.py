"""Lógica de negócio partilhada entre blueprints (ausências, licenças, detenções, etc.).

Funções de resultado seguem o padrão tuple[bool, str]:
    (True, "")           — sucesso
    (False, "mensagem")  — erro com motivo legível
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from flask import current_app

from core.database import db
from core.meals import (
    get_ocupacao_capacidade,
    refeicao_editavel,
    refeicao_get,
    refeicao_save,
)
import config as cfg
from utils.helpers import _refeicao_set


# ── Ausências ────────────────────────────────────────────────────────────


def _registar_ausencia(
    uid: int, de: str, ate: str, motivo: str, criado_por: str
) -> tuple[bool, str]:
    try:
        d_de = datetime.strptime(de, "%Y-%m-%d").date()
        d_ate = datetime.strptime(ate, "%Y-%m-%d").date()
    except ValueError:
        return False, "Data inválida."
    if de > ate:
        return False, "A data de início não pode ser posterior à data de fim."
    with db() as conn:
        conn.execute(
            """INSERT INTO ausencias (utilizador_id,ausente_de,ausente_ate,motivo,criado_por)
                        VALUES (?,?,?,?,?)""",
            (uid, de, ate, motivo or None, criado_por),
        )
        # Limpar refeições durante o período de ausência
        conn.execute(
            """UPDATE refeicoes SET pequeno_almoco=0, lanche=0, almoco=NULL,
               jantar_tipo=NULL, jantar_sai_unidade=0, almoco_estufa=0, jantar_estufa=0
               WHERE utilizador_id=? AND data>=? AND data<=?""",
            (uid, de, ate),
        )
        # Cancelar licenças pendentes durante o período (preservar as que já têm saída registada)
        conn.execute(
            "DELETE FROM licencas WHERE utilizador_id=? AND data>=? AND data<=? AND hora_saida IS NULL",
            (uid, de, ate),
        )
        conn.commit()
    return True, ""


def _remover_ausencia(aid: int) -> None:
    with db() as conn:
        conn.execute("DELETE FROM ausencias WHERE id=?", (aid,))
        conn.commit()


def _editar_ausencia(
    aid: int, uid: int, de: str, ate: str, motivo: str
) -> tuple[bool, str]:
    try:
        datetime.strptime(de, "%Y-%m-%d")
        datetime.strptime(ate, "%Y-%m-%d")
    except ValueError:
        return False, "Data inválida."
    if de > ate:
        return False, "A data de início não pode ser posterior à data de fim."
    with db() as conn:
        conn.execute(
            """UPDATE ausencias SET ausente_de=?,ausente_ate=?,motivo=?
                        WHERE id=? AND utilizador_id=?""",
            (de, ate, motivo or None, aid, uid),
        )
        conn.commit()
    return True, ""


def _tem_ausencia_ativa(uid: int, d: date | None = None) -> bool:
    """Verifica se utilizador tem ausência ativa na data (ou hoje)."""
    d_str = (d or date.today()).isoformat()
    with db() as conn:
        row = conn.execute(
            """SELECT 1 FROM ausencias WHERE utilizador_id=?
                              AND ausente_de<=? AND ausente_ate>=?""",
            (uid, d_str, d_str),
        ).fetchone()
    return bool(row)


# ── Detenções ────────────────────────────────────────────────────────────


def _tem_detencao_ativa(uid: int, d: date | None = None) -> bool:
    """Verifica se utilizador tem detenção ativa na data (ou hoje)."""
    try:
        d_str = (d or date.today()).isoformat()
        with db() as conn:
            row = conn.execute(
                """SELECT 1 FROM detencoes WHERE utilizador_id=?
                              AND detido_de<=? AND detido_ate>=? LIMIT 1""",
                (uid, d_str, d_str),
            ).fetchone()
        return bool(row)
    except Exception:
        return False


def _auto_marcar_refeicoes_detido(
    uid: int, d_de: date, d_ate: date, alterado_por: str = "sistema"
) -> None:
    """Auto-marca todas as refeições para dias de detenção se não estiverem marcadas."""
    try:
        d = d_de
        while d <= d_ate:
            with db() as conn:
                existe = conn.execute(
                    "SELECT almoco FROM refeicoes WHERE utilizador_id=? AND data=?",
                    (uid, d.isoformat()),
                ).fetchone()
            if not existe or not existe["almoco"]:
                _refeicao_set(
                    uid,
                    d,
                    pa=1,
                    lanche=1,
                    alm="Normal",
                    jan="Normal",
                    sai=0,
                    alterado_por=alterado_por,
                )
            d += timedelta(days=1)
    except Exception as exc:
        current_app.logger.warning(f"_auto_marcar_refeicoes_detido uid={uid}: {exc}")


# ── Licenças ─────────────────────────────────────────────────────────────


def _regras_licenca(ano: int, ni: str) -> dict:
    """Devolve regras de licença para um aluno com base no ano e NI.

    As regras são lidas de config.LICENCA_REGRAS_ANO (configurável).
    Exceções: NI com prefixo '7' usa sempre o default (acesso total).
    """
    excepcao_ni7 = str(ni).startswith("7")
    if excepcao_ni7 or ano >= 4 or ano not in cfg.LICENCA_REGRAS_ANO:
        base = cfg.LICENCA_REGRAS_ANO_DEFAULT
    else:
        base = cfg.LICENCA_REGRAS_ANO[ano]
    return {
        "max_dias_uteis": base["max_dias_uteis"],
        "dias_permitidos": list(base["dias_permitidos"]),
        "excepcao_ni7": excepcao_ni7,
    }


def _licencas_semana_usadas(uid: int, d: date) -> int:
    """Conta licenças de dias úteis (seg-qui) já usadas na semana ISO de 'd'."""
    seg = d - timedelta(days=d.weekday())  # segunda
    qui = seg + timedelta(days=3)  # quinta
    with db() as conn:
        row = conn.execute(
            """SELECT COUNT(*) c FROM licencas
            WHERE utilizador_id=? AND data>=? AND data<=?""",
            (uid, seg.isoformat(), qui.isoformat()),
        ).fetchone()
    return row["c"] or 0


def _pode_marcar_licenca(uid: int, d: date, ano: int, ni: str) -> tuple[bool, str]:
    """Verifica se o aluno pode marcar licença para o dia 'd'.

    Retorna (pode: bool, motivo: str).
    """
    regras = _regras_licenca(ano, ni)

    # Detido não pode sair
    if _tem_detencao_ativa(uid, d):
        return False, "Estás detido neste dia — não podes marcar licença."

    dia_semana = d.weekday()  # 0=seg ... 6=dom

    # Fim de semana (sex=4, sab=5, dom=6) — todos podem
    if dia_semana >= 4:
        return True, ""

    # Dia útil (seg-qui) — verificar se o dia é permitido
    if dia_semana not in regras["dias_permitidos"]:
        nomes = {0: "segunda", 1: "terça", 2: "quarta", 3: "quinta"}
        return False, f"O teu ano não tem licença à {nomes.get(dia_semana, '')}."

    # Verificar limite semanal de dias úteis
    usadas = _licencas_semana_usadas(uid, d)
    # Verificar se este dia já está contado (para não contar duas vezes ao editar)
    with db() as conn:
        ja_tem = conn.execute(
            "SELECT 1 FROM licencas WHERE utilizador_id=? AND data=?",
            (uid, d.isoformat()),
        ).fetchone()
    if not ja_tem and usadas >= regras["max_dias_uteis"]:
        return (
            False,
            f"Já esgotaste as tuas {regras['max_dias_uteis']} saídas desta semana (seg-qui). "
            "Precisas de aprovação do Comandante de Companhia ou Oficial de Dia.",
        )

    return True, ""


# ── Dia editável ─────────────────────────────────────────────────────────


def _dia_editavel_aluno(d: date) -> tuple[bool, str]:
    """Editável pelo aluno: futuro, dentro de DIAS_ANTECEDENCIA, prazo ok. Fins de semana permitidos."""
    hoje = date.today()
    if d < hoje:
        return False, "Data no passado."
    if (d - hoje).days > cfg.DIAS_ANTECEDENCIA:
        return (
            False,
            f"Só é possível marcar com {cfg.DIAS_ANTECEDENCIA} dias de antecedência.",
        )
    return refeicao_editavel(d)


# ── Licença FDS ──────────────────────────────────────────────────────────


def _fds_deadline_passed(sexta: date) -> bool:
    """Verifica se o prazo para marcar/cancelar licença FDS já passou (sexta 12h)."""
    agora = datetime.now()
    deadline = datetime(sexta.year, sexta.month, sexta.day, 12, 0, 0)
    return agora >= deadline


def _marcar_licenca_fds(uid: int, sexta: date, alterado_por: str) -> tuple[bool, str]:
    """
    Marca 'licença fim de semana' para um aluno:
    - Sexta: licença antes_jantar (retira jantar, marca sai_unidade)
    - Sábado e Domingo: apaga todas as refeições
    Retorna (sucesso: bool, mensagem: str)
    """
    if _fds_deadline_passed(sexta):
        return False, "Prazo expirado — licença FDS só pode ser marcada até sexta às 12h."
    try:
        # ── Sexta-feira: licença antes_jantar ──────────────────────────
        with db() as conn:
            conn.execute(
                """INSERT INTO licencas(utilizador_id, data, tipo)
                   VALUES(?,?,?)
                   ON CONFLICT(utilizador_id, data) DO UPDATE SET tipo=excluded.tipo""",
                (uid, sexta.isoformat(), "antes_jantar"),
            )
            conn.commit()

        # Refeições da sexta: guardar sem jantar, com sai_unidade
        r_sexta = refeicao_get(uid, sexta)
        r_sexta["jantar_tipo"] = None
        r_sexta["jantar_sai_unidade"] = 1
        refeicao_save(uid, sexta, r_sexta, alterado_por=alterado_por)

        # ── Sábado e Domingo: apagar todas as refeições ────────────────
        for delta in (1, 2):  # sábado=+1, domingo=+2
            d = sexta + timedelta(days=delta)
            r_vazio = {
                "pequeno_almoco": 0,
                "lanche": 0,
                "almoco": None,
                "jantar_tipo": None,
                "jantar_sai_unidade": 0,
            }
            refeicao_save(uid, d, r_vazio, alterado_por=alterado_por)

        return True, ""
    except Exception as exc:
        current_app.logger.warning(f"_marcar_licenca_fds uid={uid}: {exc}")
        return False, str(exc)


def _cancelar_licenca_fds(uid: int, sexta: date, alterado_por: str) -> tuple[bool, str]:
    """
    Cancela 'licença fim de semana':
    - Remove a licença da sexta
    - Repõe jantar normal na sexta, retira sai_unidade
    """
    if _fds_deadline_passed(sexta):
        return False, "Prazo expirado — licença FDS só pode ser cancelada até sexta às 12h."
    try:
        # Remover licença da sexta
        with db() as conn:
            conn.execute(
                "DELETE FROM licencas WHERE utilizador_id=? AND data=?",
                (uid, sexta.isoformat()),
            )
            conn.commit()

        # Repor sexta: jantar Normal, sem sai_unidade
        r_sexta = refeicao_get(uid, sexta)
        r_sexta["jantar_sai_unidade"] = 0
        if not r_sexta.get("jantar_tipo"):
            r_sexta["jantar_tipo"] = "Normal"
        refeicao_save(uid, sexta, r_sexta, alterado_por=alterado_por)

        return True, ""
    except Exception as exc:
        current_app.logger.warning(f"_cancelar_licenca_fds uid={uid}: {exc}")
        return False, str(exc)


# ── Ocupação ─────────────────────────────────────────────────────────────


def _get_ocupacao_dia(dt: date) -> dict[str, tuple[int, int]]:
    return get_ocupacao_capacidade(dt)


# ── Alertas painel ───────────────────────────────────────────────────────


def _alertas_painel(d_str: str, perfil: str) -> list[dict[str, str]]:
    """Gera alertas operacionais para o painel do dia (sem tabela extra)."""
    alertas: list = []
    if perfil not in ("oficialdia", "cmd", "admin"):
        return alertas

    hoje = date.today().isoformat()

    with db() as conn:
        # 1. Detenções que expiram hoje
        det_exp = conn.execute(
            """SELECT COUNT(*) c FROM detencoes d
               JOIN utilizadores u ON u.id=d.utilizador_id
               WHERE d.detido_ate=? AND u.perfil='aluno'""",
            (hoje,),
        ).fetchone()["c"]
        if det_exp:
            alertas.append(
                {
                    "icon": "⛔",
                    "msg": f"{det_exp} detenção(ões) expira(m) hoje.",
                    "cat": "warn",
                }
            )

        # 2. Licenças sem registo de saída
        lic_pend = conn.execute(
            """SELECT COUNT(*) c FROM licencas
               WHERE data=? AND hora_saida IS NULL""",
            (hoje,),
        ).fetchone()["c"]
        if lic_pend:
            alertas.append(
                {
                    "icon": "🚪",
                    "msg": f"{lic_pend} licença(s) sem registo de saída.",
                    "cat": "warn",
                }
            )

        # 3. Ausências registadas hoje
        novas_aus = conn.execute(
            """SELECT COUNT(*) c FROM ausencias
               WHERE date(criado_em)=?""",
            (hoje,),
        ).fetchone()["c"]
        if novas_aus:
            alertas.append(
                {
                    "icon": "🚫",
                    "msg": f"{novas_aus} ausência(s) registada(s) hoje.",
                    "cat": "info",
                }
            )

    return alertas
