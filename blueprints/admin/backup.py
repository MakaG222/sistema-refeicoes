"""Rotas admin — download de backup da base de dados."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from flask import (
    flash,
    redirect,
    send_file,
    url_for,
)

from blueprints.admin import admin_bp
from core.constants import BASE_DADOS
from utils.auth import login_required, role_required


@admin_bp.route("/admin/backup-download")
@login_required
@role_required("admin")
def admin_backup_download():
    """Permite ao admin descarregar o ficheiro da base de dados."""
    db_path = Path(BASE_DADOS)
    if not db_path.exists():
        flash("Ficheiro da base de dados não encontrado.", "error")
        return redirect(url_for(".admin_home"))
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    nome = f"{db_path.stem}_{ts}.db"
    return send_file(
        db_path,
        as_attachment=True,
        download_name=nome,
        mimetype="application/x-sqlite3",
    )
