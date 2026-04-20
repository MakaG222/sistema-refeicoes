"""Rotas admin — menus diários e capacidade de refeições."""

from __future__ import annotations

from datetime import date

from flask import (
    flash,
    redirect,
    render_template,
    request,
    url_for,
)

from blueprints.admin import admin_bp
from core.menus import get_capacities, get_menu, save_capacity, save_menu
from utils.auth import current_user, role_required
from utils.helpers import _audit, _parse_date
from utils.validators import _val_cap


@admin_bp.route("/admin/menus", methods=["GET", "POST"])
@role_required("cozinha", "admin", "oficialdia")
def admin_menus():
    d_str = request.args.get("d", date.today().isoformat())
    dt = _parse_date(d_str)

    if request.method == "POST":
        d_save = request.form.get("data", dt.isoformat())
        campos = [
            "pequeno_almoco",
            "lanche",
            "almoco_normal",
            "almoco_veg",
            "almoco_dieta",
            "jantar_normal",
            "jantar_veg",
            "jantar_dieta",
        ]
        vals = [request.form.get(c, "").strip() or None for c in campos]
        save_menu(d_save, vals)

        for ref in ["Pequeno Almoço", "Lanche", "Almoço", "Jantar"]:
            cap_key = "cap_" + ref.lower().replace(" ", "_").replace("ç", "c").replace(
                "ã", "a"
            )
            cap_val = request.form.get(cap_key, "").strip()
            if cap_val:
                try:
                    cap_int = _val_cap(cap_val)
                    if cap_int is None:
                        continue
                    save_capacity(d_save, ref, cap_int)
                except ValueError:
                    pass

        u = current_user()
        _audit(
            u.get("nii", "?"), "menu_save", f"data={d_save} por {u.get('nome', '?')}"
        )
        flash("Menu e capacidades guardados.", "ok")
        return redirect(url_for(".admin_menus", d=d_save))

    menu = get_menu(dt.isoformat())
    caps = get_capacities(dt.isoformat())

    back_url = (
        url_for("operations.painel_dia")
        if current_user().get("perfil") in ("cozinha", "oficialdia")
        else url_for(".admin_home")
    )

    return render_template(
        "admin/menus.html",
        dt=dt,
        menu=menu,
        caps=caps,
        back_url=back_url,
    )
