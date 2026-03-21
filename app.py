"""
Sistema de Refeições — Interface Web (Flask)
============================================
Corre com:  python app.py
Acede em:   http://localhost:8080
"""

import os
import sys

from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix

sys.path.insert(0, os.path.dirname(__file__))
import config as cfg  # noqa: E402
from core.bootstrap import ensure_extra_schema, init_app_once, seed_dev_command  # noqa: E402
from core.constants import BASE_DADOS  # noqa: E402
from core.database import close_request_db  # noqa: E402
from core.middleware import register_middleware  # noqa: E402

# ── Re-exports de utils/ (backward-compat para testes que fazem `from app import X`) ──
from utils.constants import (  # noqa: E402, F401
    ANOS_LABELS,
    ANOS_OPCOES,
    NOMES_DIAS,
    ABREV_DIAS,
    _PERFIS_VALIDOS,
    _TIPOS_CALENDARIO,
    _REFEICAO_OPCOES,
    _RE_EMAIL,
    _RE_PHONE,
    _RE_ALNUM,
    _MAX_NOME,
    _MAX_TEXT,
    _MAX_DATE_RANGE,
)
from utils.validators import (  # noqa: E402, F401
    _val_email,
    _val_phone,
    _val_nii,
    _val_ni,
    _val_nome,
    _val_ano,
    _val_perfil,
    _val_tipo_calendario,
    _val_refeicao,
    _val_text,
    _val_int_id,
    _val_date_range,
    _val_cap,
)
from utils.auth import login_required, role_required, current_user  # noqa: E402, F401
from utils.helpers import (  # noqa: E402, F401
    render,
    esc,
    csrf_input,
    _parse_date,
    _parse_date_strict,
    _ano_label,
    _get_anos_disponiveis,
    _refeicao_set,
    _back_btn,
    _bar_html,
    _prazo_label,
    _audit,
    _client_ip,
)
from utils.passwords import (  # noqa: E402, F401
    generate_password_hash,
    _validate_password,
    _check_password,
    _migrate_password_hash,
    _alterar_password,
    _criar_utilizador,
    _reset_pw,
    _unblock_user,
    _eliminar_utilizador,
)
from utils.business import (  # noqa: E402, F401
    _registar_ausencia,
    _remover_ausencia,
    _editar_ausencia,
    _tem_ausencia_ativa,
    _tem_detencao_ativa,
    _auto_marcar_refeicoes_detido,
    _regras_licenca,
    _licencas_semana_usadas,
    _pode_marcar_licenca,
    _dia_editavel_aluno,
    _marcar_licenca_fds,
    _cancelar_licenca_fds,
    _get_ocupacao_dia,
    _alertas_painel,
)

# backward-compat alias
_ensure_extra_schema = ensure_extra_schema

# ═══════════════════════════════════════════════════════════════════════════
# APP
# ═══════════════════════════════════════════════════════════════════════════

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
app.config.from_object(cfg.Config)
cfg.configure_logging(app)

app.teardown_appcontext(close_request_db)


@app.context_processor
def inject_csrf():
    return dict(csrf_input=csrf_input)


app.jinja_env.globals["back_btn"] = _back_btn
app.jinja_env.globals["bar_html"] = _bar_html
app.jinja_env.globals["prazo_label"] = _prazo_label
app.jinja_env.globals["ano_label"] = _ano_label

# ── Registar Blueprints ──────────────────────────────────────────────────
from blueprints.api import api_bp  # noqa: E402
from blueprints.auth import auth_bp  # noqa: E402
from blueprints.aluno import aluno_bp  # noqa: E402
from blueprints.cmd import cmd_bp  # noqa: E402
from blueprints.operations import ops_bp  # noqa: E402
from blueprints.admin import admin_bp  # noqa: E402
from blueprints.reporting import report_bp  # noqa: E402

app.register_blueprint(api_bp)
app.register_blueprint(auth_bp)
app.register_blueprint(aluno_bp)
app.register_blueprint(cmd_bp)
app.register_blueprint(ops_bp)
app.register_blueprint(admin_bp)
app.register_blueprint(report_bp)

# ── Middleware (before/after request, error handlers, métricas) ──────────
register_middleware(app)

# ── CLI commands ─────────────────────────────────────────────────────────
app.cli.add_command(seed_dev_command)

# Expor constantes de config usadas localmente
CRON_API_TOKEN = cfg.CRON_API_TOKEN
DIAS_ANTECEDENCIA = cfg.DIAS_ANTECEDENCIA

# ── Bootstrap ────────────────────────────────────────────────────────────
init_app_once(app)


@app.before_request
def _bootstrap_before_request():
    """Garante schema da BD antes do primeiro pedido (Gunicorn). Auto-remove-se."""
    init_app_once(app)
    app.before_request_funcs.setdefault(None, [])
    try:
        app.before_request_funcs[None].remove(_bootstrap_before_request)
    except ValueError:
        pass


# ── Arranque directo ─────────────────────────────────────────────────────
if __name__ == "__main__":
    init_app_once(app)
    cfg.print_startup_banner(BASE_DADOS)
    app.run(debug=cfg.DEBUG, host="0.0.0.0", port=cfg.PORT)
