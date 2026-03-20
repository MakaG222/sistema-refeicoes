"""Blueprint de autenticação (login, logout, dashboard)."""

from flask import Blueprint

auth_bp = Blueprint("auth", __name__)

from blueprints.auth import routes  # noqa: E402, F401
