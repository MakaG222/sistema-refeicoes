"""Blueprint de API (health, cron endpoints)."""

from flask import Blueprint

api_bp = Blueprint("api", __name__)

from blueprints.api import routes  # noqa: E402, F401
