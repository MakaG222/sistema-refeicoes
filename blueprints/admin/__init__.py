"""Blueprint de administração."""

from flask import Blueprint

admin_bp = Blueprint("admin", __name__)

from blueprints.admin import audit, backup, calendar, companhias, menus, users  # noqa: E402, F401
