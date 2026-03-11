"""Blueprint do aluno: home, editar refeicoes, ausencias, historico, password, perfil."""

from flask import Blueprint

aluno_bp = Blueprint("aluno", __name__)

from blueprints.aluno import routes  # noqa: E402, F401
