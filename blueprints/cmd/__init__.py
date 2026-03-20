"""Blueprint CMD (comandante de companhia)."""

from flask import Blueprint

cmd_bp = Blueprint("cmd", __name__)

from blueprints.cmd import routes  # noqa: E402, F401
