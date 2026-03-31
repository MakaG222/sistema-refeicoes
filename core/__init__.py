"""Core — camada de dados do Sistema de Refeições.

Re-exporta as funções públicas para acesso simplificado.
"""

from core.absences import (
    ausencias_batch,
    ausencias_batch_detalhadas,
    detencoes_batch,
    licencas_batch,
    utilizador_ausente,
)
from core.analytics import period_days, series_consumo_por_dia
from core.auth_db import (
    PERFIS_ADMIN,
    PERFIS_TESTE,
    block_user,
    existe_admin,
    recent_failures,
    recent_failures_by_ip,
    reg_login,
    user_by_ni,
    user_by_nii,
    user_id_by_nii,
    verify_password,
)
from core.autofill import autopreencher_refeicoes_semanais
from core.backup import do_backup, ensure_daily_backup, limpar_backups_antigos
from core.constants import BASE_DADOS, BACKUP_DIR, EXPORT_DIR, PRAZO_LIMITE_HORAS
from core.database import (
    close_request_db,
    db,
    ensure_schema,
    sqlite_quick_check,
    wal_checkpoint,
)
from core.exports import export_both, export_csv, export_xlsx, exportacoes_do_dia
from core.meals import (
    _HEADERS_DISTRIBUICAO,
    _HEADERS_TOTAIS,
    _totais_para_csv_row,
    dia_operacional,
    dia_tem_refeicoes,
    dias_operacionais_batch,
    get_menu_do_dia,
    get_ocupacao_capacidade,
    get_totais_dia,
    get_totais_periodo,
    refeicao_editavel,
    refeicao_exists,
    refeicao_get,
    refeicao_save,
    refeicoes_batch,
)

__all__ = [  # noqa: F822
    "db",
    "close_request_db",
    "wal_checkpoint",
    "ensure_schema",
    "sqlite_quick_check",
    "verify_password",
    "reg_login",
    "recent_failures",
    "recent_failures_by_ip",
    "block_user",
    "existe_admin",
    "user_by_nii",
    "user_by_ni",
    "user_id_by_nii",
    "PERFIS_ADMIN",
    "PERFIS_TESTE",
    "refeicao_get",
    "refeicao_save",
    "refeicao_exists",
    "refeicoes_batch",
    "refeicao_editavel",
    "get_totais_dia",
    "get_totais_periodo",
    "get_ocupacao_capacidade",
    "get_menu_do_dia",
    "dias_operacionais_batch",
    "dia_operacional",
    "dia_tem_refeicoes",
    "ausencias_batch",
    "ausencias_batch_detalhadas",
    "detencoes_batch",
    "licencas_batch",
    "utilizador_ausente",
    "ensure_daily_backup",
    "limpar_backups_antigos",
    "do_backup",
    "autopreencher_refeicoes_semanais",
    "export_csv",
    "export_xlsx",
    "export_both",
    "exportacoes_do_dia",
    "series_consumo_por_dia",
    "period_days",
    "BASE_DADOS",
    "BACKUP_DIR",
    "EXPORT_DIR",
    "PRAZO_LIMITE_HORAS",
    "_HEADERS_TOTAIS",
    "_HEADERS_DISTRIBUICAO",
    "_totais_para_csv_row",
]
