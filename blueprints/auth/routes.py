"""Rotas de autenticação: login, logout, dashboard."""

import secrets
from datetime import datetime

from flask import (
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

import config as cfg
from core.auth_db import (
    block_user,
    existe_admin,
    FALLBACK_ADMIN,
    recent_failures,
    recent_failures_by_ip,
    reg_login,
    user_by_nii,
)
from blueprints.auth import auth_bp
from utils.auth import current_user, login_required
from utils.helpers import _audit, _client_ip
from utils.passwords import _check_password, _migrate_password_hash


@auth_bp.route("/", methods=["GET", "POST"])
@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if "user" in session:
        return redirect(url_for("auth.dashboard"))
    error = None
    if request.method == "POST":
        nii = request.form.get("nii", "").strip()[:32]
        pw = request.form.get("pw", request.form.get("password", "")).strip()[:256]
        # Rate limiting por IP (proteção contra ataques distribuídos)
        ip = _client_ip()
        ip_falhas = recent_failures_by_ip(ip, 15)
        if ip_falhas >= 20:
            error = "Demasiadas tentativas falhadas deste endereço. Aguarda 15 minutos."
            current_app.logger.warning(
                "IP rate-limited: IP=%s falhas=%d", ip, ip_falhas
            )
            _audit(nii or "unknown", "ip_rate_limited", f"IP={ip} falhas={ip_falhas}")
        # Autenticação via BD (contas de sistema sincronizadas para a BD em desenvolvimento)
        perfis = {}
        u = None
        db_u = None
        if error:
            pass  # IP bloqueado — não tentar autenticação
        elif False and nii in perfis:
            error = "Login legado desativado."
        elif (
            not cfg.is_production
            and not existe_admin()
            and nii == FALLBACK_ADMIN["nii"]
            and pw == FALLBACK_ADMIN["pw"]
        ):
            u = {
                "id": 0,
                "nii": nii,
                "ni": "",
                "nome": FALLBACK_ADMIN["nome"],
                "ano": "",
                "perfil": "admin",
            }
            reg_login(nii, 1, ip=_client_ip())
            current_app.logger.warning(
                f"Login via FALLBACK_ADMIN: NII={nii} IP={_client_ip()}"
            )
        else:
            # Busca directa à BD por NII
            db_u = user_by_nii(nii)
            if db_u:
                locked = db_u.get("locked_until")
                if locked:
                    try:
                        lock_dt = datetime.fromisoformat(locked)
                        if lock_dt > datetime.now():
                            mins = max(
                                1,
                                int((lock_dt - datetime.now()).total_seconds() / 60),
                            )
                            error = f"Conta bloqueada por demasiadas tentativas falhadas. Tenta novamente em {mins} min."
                            current_app.logger.warning(
                                f"Login bloqueado: NII={nii} IP={_client_ip()}"
                            )
                            db_u = None
                    except ValueError:
                        pass
                if db_u:
                    ph = db_u.get("Palavra_chave", "") or ""
                    ok = _check_password(ph, pw)
                    if ok:
                        _perfil = (
                            db_u.get("perfil")
                            if hasattr(db_u, "get")
                            else db_u["perfil"]
                        ) or "aluno"
                        u = {
                            "id": db_u["id"],
                            "nii": db_u["NII"],
                            "ni": db_u["NI"],
                            "nome": db_u["Nome_completo"],
                            "ano": str(db_u["ano"] or ""),
                            "perfil": _perfil,
                        }
                        reg_login(nii, 1, ip=_client_ip())
                        current_app.logger.info(
                            f"Login OK: NII={nii} perfil={u['perfil']} IP={_client_ip()}"
                        )
                        # Migração transparente: se ainda é plain-text, converter para hash
                        if not ph.startswith(("pbkdf2:", "scrypt:", "argon2:")):
                            _migrate_password_hash(db_u["id"], pw)
                    else:
                        reg_login(nii, 0, ip=_client_ip())
                        falhas = recent_failures(nii, 10)
                        if falhas >= 5:
                            block_user(nii, 15)
                            error = "Conta bloqueada por 15 minutos após 5 tentativas falhadas."
                            current_app.logger.warning(
                                f"Conta bloqueada: NII={nii} IP={_client_ip()}"
                            )
                        else:
                            restam = max(0, 5 - falhas)
                            error = f"Password incorreta. ({restam} tentativa(s) restante(s) antes de bloqueio)"
            else:
                reg_login(nii, 0, ip=_client_ip())
                error = "NII não encontrado."
        if u:
            session["_csrf_token"] = secrets.token_urlsafe(32)  # Rodar CSRF token
            session["user"] = u
            session.permanent = True  # ativa timeout de inatividade
            _audit(nii, "login", f"perfil={u['perfil']} IP={_client_ip()}")
            # Forçar alteração de password se necessário
            if db_u and db_u.get("must_change_password"):
                session["must_change_password"] = True
                flash(
                    "Por segurança, deves alterar a tua password antes de continuar.",
                    "warn",
                )
                return redirect(url_for("aluno.aluno_password"))
            return redirect(url_for("auth.dashboard"))

    return render_template("auth/login.html", error=error)


@auth_bp.route("/logout", methods=["POST"])
def logout():
    # Verifica CSRF para logout via POST
    t = session.get("_csrf_token", "")
    ft = request.form.get("csrf_token", "")
    if not t or not secrets.compare_digest(t, ft):
        abort(403)
    session.clear()
    return redirect(url_for("auth.login"))


@auth_bp.route("/dashboard")
@login_required
def dashboard():
    p = current_user().get("perfil", "aluno")
    if p == "admin":
        return redirect(url_for("admin.admin_home"))
    if p in ("cozinha", "oficialdia", "cmd"):
        return redirect(url_for("operations.painel_dia"))
    return redirect(url_for("aluno.aluno_home"))
