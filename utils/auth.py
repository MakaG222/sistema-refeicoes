"""Decoradores de autenticação e autorização."""

from __future__ import annotations

from functools import wraps
from typing import Any

from flask import flash, redirect, session, url_for


def current_user() -> dict[str, Any]:
    """Devolve o dicionário do utilizador autenticado (ou {} se não autenticado)."""
    return session.get("user", {})


def login_required(f):
    """Decorador que exige autenticação."""

    @wraps(f)
    def d(*a, **kw):
        if "user" not in session:
            return redirect(url_for("auth.login"))
        if session.get("must_change_password") and f.__name__ != "aluno_password":
            flash("Deves alterar a tua password antes de continuar.", "warn")
            return redirect(url_for("aluno.aluno_password"))
        return f(*a, **kw)

    return d


def role_required(*roles):
    """Decorador que exige um perfil específico."""

    def decorator(f):
        @wraps(f)
        def d(*a, **kw):
            if "user" not in session:
                return redirect(url_for("auth.login"))
            if session.get("must_change_password") and f.__name__ != "aluno_password":
                flash("Deves alterar a tua password antes de continuar.", "warn")
                return redirect(url_for("aluno.aluno_password"))
            if session["user"]["perfil"] not in roles:
                flash("Acesso n\u00e3o autorizado.", "error")
                return redirect(url_for("auth.dashboard"))
            return f(*a, **kw)

        return d

    return decorator
