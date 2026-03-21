"""Funções de exportação CSV e XLSX."""

import csv
import os
from typing import List

from core.constants import EXPORT_DIR
from core.database import db
from core.meals import (
    _HEADERS_DISTRIBUICAO,
    _HEADERS_TOTAIS,
    _totais_para_csv_row,
    get_totais_dia,
)


def export_csv(rows: List[dict], headers: List[str], name: str) -> str:
    path = os.path.join(EXPORT_DIR, name + ".csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(headers)
        for r in rows:
            w.writerow([r.get(h, "") for h in headers])
    return path


def export_xlsx(rows: List[dict], headers: List[str], name: str) -> str:
    """Exporta para Excel (.xlsx). Requer openpyxl."""
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill
        from openpyxl.utils import get_column_letter
    except ImportError:
        print("openpyxl não instalado. Instala com: pip install openpyxl")
        return export_csv(rows, headers, name)

    path = os.path.join(EXPORT_DIR, name + ".xlsx")
    wb = Workbook()
    ws = wb.active
    ws.title = name[:31]

    header_fill = PatternFill("solid", fgColor="1F4E79")
    header_font = Font(bold=True, color="FFFFFF")
    for col, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=h)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center")

    for row_idx, row in enumerate(rows, 2):
        for col_idx, h in enumerate(headers, 1):
            ws.cell(row=row_idx, column=col_idx, value=row.get(h, ""))

    for col_idx, h in enumerate(headers, 1):
        max_len = (
            max(len(str(h)), *(len(str(row.get(h, ""))) for row in rows))
            if rows
            else len(str(h))
        )
        ws.column_dimensions[get_column_letter(col_idx)].width = min(max_len + 4, 50)

    wb.save(path)
    return path


def export_both(rows: List[dict], headers: List[str], name: str) -> tuple:
    """Exporta CSV e XLSX e devolve ambos os caminhos."""
    p1 = export_csv(rows, headers, name)
    p2 = export_xlsx(rows, headers, name)
    return p1, p2


def exportacoes_do_dia(d, ano=None):
    """Gera CSV + XLSX do dia d."""

    di = d.isoformat()
    tag = f"_ano{ano}" if ano else ""
    t = get_totais_dia(di, ano)

    row_sum = _totais_para_csv_row(di, t, {"ano": ano} if ano else {})
    hdrs = (["data", "ano"] + _HEADERS_TOTAIS[1:]) if ano else _HEADERS_TOTAIS
    export_both([row_sum], hdrs, f"totais{tag}_{di}")

    with db() as conn:
        if ano is None:
            det = [
                dict(r)
                for r in conn.execute(
                    """
                SELECT u.ano, u.NI, u.Nome_completo, r.data,
                       r.pequeno_almoco, r.lanche, r.almoco, r.jantar_tipo, r.jantar_sai_unidade
                FROM refeicoes r JOIN utilizadores u ON u.id=r.utilizador_id
                WHERE r.data=?
                ORDER BY u.ano, u.NI
            """,
                    (di,),
                )
            ]
        else:
            det = [
                dict(r)
                for r in conn.execute(
                    """
                SELECT u.NII, u.NI, u.Nome_completo, u.ano, r.data,
                       r.pequeno_almoco, r.lanche, r.almoco, r.jantar_tipo, r.jantar_sai_unidade
                FROM refeicoes r JOIN utilizadores u ON u.id=r.utilizador_id
                WHERE r.data=? AND u.ano=?
                ORDER BY u.NI
            """,
                    (di, ano),
                )
            ]
    hdrs_det = (
        [
            "NII",
            "NI",
            "Nome_completo",
            "ano",
            "data",
            "pequeno_almoco",
            "lanche",
            "almoco",
            "jantar_tipo",
            "jantar_sai_unidade",
        ]
        if ano
        else _HEADERS_DISTRIBUICAO
    )
    export_both(det, hdrs_det, f"distribuicao{tag}_{di}")

    with db() as conn:
        occ_rows = [
            dict(r)
            for r in conn.execute("SELECT * FROM v_ocupacao_dia WHERE data=?", (di,))
        ]
    export_both(
        occ_rows,
        ["data", "refeicao", "ocupacao", "capacidade"],
        f"ocupacao_vs_capacidade_{di}",
    )
