"""Blueprint de operações (painel, listas, relatórios operacionais)."""

from flask import Blueprint

ops_bp = Blueprint("operations", __name__)

from blueprints.operations import routes  # noqa: E402, F401
