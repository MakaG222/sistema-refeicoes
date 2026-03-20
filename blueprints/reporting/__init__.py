"""Blueprint de relatórios e exportações."""

from flask import Blueprint

report_bp = Blueprint("reporting", __name__)

from blueprints.reporting import routes  # noqa: E402, F401
