"""
Sistema de Refeições — Interface Web (Flask)
============================================
Corre com:  python app.py
Acede em:   http://localhost:8080
"""

import os
import sys
import secrets
import time
from werkzeug.middleware.proxy_fix import ProxyFix
from flask import (
    Flask,
    request,
    redirect,
    url_for,
    session,
    flash,
    g,
    abort,
)

sys.path.insert(0, os.path.dirname(__file__))
import sistema_refeicoes_v8_4 as sr  # noqa: E402
import config as cfg  # noqa: E402

# ── Re-exports de utils/ (backward-compat para testes que fazem `from app import X`) ──
from utils.constants import (  # noqa: E402, F401
    ANOS_LABELS,
    ANOS_OPCOES,
    NOMES_DIAS,
    ABREV_DIAS,
    _PERFIS_VALIDOS,
    _TIPOS_CALENDARIO,
    _REFEICAO_OPCOES,
    _RE_EMAIL,
    _RE_PHONE,
    _RE_ALNUM,
    _MAX_NOME,
    _MAX_TEXT,
    _MAX_DATE_RANGE,
)
from utils.validators import (  # noqa: E402, F401
    _val_email,
    _val_phone,
    _val_nii,
    _val_ni,
    _val_nome,
    _val_ano,
    _val_perfil,
    _val_tipo_calendario,
    _val_refeicao,
    _val_text,
    _val_int_id,
    _val_date_range,
    _val_cap,
)
from utils.auth import login_required, role_required, current_user  # noqa: E402, F401
from utils.helpers import (  # noqa: E402, F401
    render,
    esc,
    csrf_input,
    _parse_date,
    _parse_date_strict,
    _ano_label,
    _get_anos_disponiveis,
    _refeicao_set,
    _back_btn,
    _bar_html,
    _prazo_label,
    _audit,
    _client_ip,
)
from utils.passwords import (  # noqa: E402, F401
    generate_password_hash,
    _validate_password,
    _check_password,
    _migrate_password_hash,
    _alterar_password,
    _criar_utilizador,
    _reset_pw,
    _unblock_user,
    _eliminar_utilizador,
)
from utils.business import (  # noqa: E402, F401
    _registar_ausencia,
    _remover_ausencia,
    _editar_ausencia,
    _tem_ausencia_ativa,
    _tem_detencao_ativa,
    _auto_marcar_refeicoes_detido,
    _regras_licenca,
    _licencas_semana_usadas,
    _pode_marcar_licenca,
    _dia_editavel_aluno,
    _marcar_licenca_fds,
    _cancelar_licenca_fds,
    _get_ocupacao_dia,
    _alertas_painel,
)


# Garantir colunas extra e FTS no arranque
def _ensure_extra_schema():
    """Garante colunas extra e FTS5 nos utilizadores."""
    try:
        with sr.db() as conn:
            cols = [
                r["name"]
                for r in conn.execute("PRAGMA table_info(utilizadores)").fetchall()
            ]
            if "email" not in cols:
                conn.execute("ALTER TABLE utilizadores ADD COLUMN email TEXT")
            if "telemovel" not in cols:
                conn.execute("ALTER TABLE utilizadores ADD COLUMN telemovel TEXT")
            if "is_active" not in cols:
                conn.execute(
                    "ALTER TABLE utilizadores ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1"
                )
            if "turma_id" not in cols:
                conn.execute(
                    "ALTER TABLE utilizadores ADD COLUMN turma_id INTEGER REFERENCES turmas(id)"
                )

            # Colunas extra na tabela licencas (entradas/saídas)
            lic_cols = [
                r["name"]
                for r in conn.execute("PRAGMA table_info(licencas)").fetchall()
            ]
            if "hora_saida" not in lic_cols:
                conn.execute("ALTER TABLE licencas ADD COLUMN hora_saida TEXT")
            if "hora_entrada" not in lic_cols:
                conn.execute("ALTER TABLE licencas ADD COLUMN hora_entrada TEXT")

            # Verificar e reparar FTS5 se necessário (sem writable_schema)
            try:
                conn.execute("SELECT COUNT(*) FROM utilizadores_fts").fetchone()
            except Exception:
                print("[AVISO] FTS corrompida — a recriar...", flush=True)
                for trg in (
                    "utilizadores_ai_fts",
                    "utilizadores_ad_fts",
                    "utilizadores_au_fts",
                ):
                    try:
                        conn.execute(f"DROP TRIGGER IF EXISTS {trg}")
                    except Exception:
                        pass
                try:
                    conn.execute("DROP TABLE IF EXISTS utilizadores_fts")
                except Exception as e2:
                    print(f"[AVISO] DROP utilizadores_fts: {e2}", flush=True)

                conn.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS utilizadores_fts
USING fts5(Nome_completo, content='utilizadores', content_rowid='id')""")
                conn.execute(
                    "INSERT OR IGNORE INTO utilizadores_fts(rowid, Nome_completo) SELECT id, Nome_completo FROM utilizadores"
                )
                conn.execute("""CREATE TRIGGER IF NOT EXISTS utilizadores_ai_fts
AFTER INSERT ON utilizadores BEGIN
  INSERT INTO utilizadores_fts(rowid, Nome_completo) VALUES (NEW.id, NEW.Nome_completo);
END""")
                conn.execute("""CREATE TRIGGER IF NOT EXISTS utilizadores_ad_fts
AFTER DELETE ON utilizadores BEGIN
  INSERT INTO utilizadores_fts(utilizadores_fts, rowid) VALUES('delete', OLD.id);
END""")
                conn.execute("""CREATE TRIGGER IF NOT EXISTS utilizadores_au_fts
AFTER UPDATE OF Nome_completo ON utilizadores BEGIN
  INSERT INTO utilizadores_fts(utilizadores_fts, rowid) VALUES('delete', OLD.id);
  INSERT INTO utilizadores_fts(rowid, Nome_completo) VALUES (NEW.id, NEW.Nome_completo);
END""")
                print("[INFO] FTS recriada com sucesso.", flush=True)

            # === Migrações pontuais (tabela de controlo) ===
            conn.execute(
                "CREATE TABLE IF NOT EXISTS _migracoes (nome TEXT PRIMARY KEY, aplicada_em TEXT)"
            )
            done = {
                r["nome"]
                for r in conn.execute("SELECT nome FROM _migracoes").fetchall()
            }

            # 1) Corrigir NI da aluna Reis 4º ano: 382 → 482
            if "reis_ni_382_482" not in done:
                reis = conn.execute(
                    "SELECT id FROM utilizadores WHERE NI='382' AND ano='4'"
                ).fetchone()
                if reis:
                    conn.execute(
                        "UPDATE utilizadores SET NI='482' WHERE id=?",
                        (reis["id"],),
                    )
                    print(
                        "[MIGRAÇÃO] NI da aluna Reis corrigido: 382→482",
                        flush=True,
                    )
                conn.execute(
                    "INSERT INTO _migracoes VALUES('reis_ni_382_482', datetime('now','localtime'))"
                )

            # 2) Corrigir NII da aluna Rafaela Fernandes: 20223 → 21223
            if "rafaela_nii_20223_21223" not in done:
                try:
                    cur = conn.execute(
                        "UPDATE utilizadores SET NII='21223' WHERE NII='20223'"
                    )
                    if cur.rowcount:
                        print(
                            f"[MIGRAÇÃO] NII Rafaela Fernandes corrigido: 20223→21223 (linhas={cur.rowcount})",
                            flush=True,
                        )
                except Exception as exc:
                    print(
                        f"[AVISO] Migração Rafaela NII 20223→21223 falhou: {exc}",
                        flush=True,
                    )
                conn.execute(
                    "INSERT INTO _migracoes VALUES('rafaela_nii_20223_21223', datetime('now','localtime'))"
                )

            # 3) Reset credenciais alunos: password→hash(NII), must_change=1
            #    Login = NII (ex: 24123), pw inicial = NII
            if "reset_creds_nii_v2" not in done:
                alunos_reset = conn.execute(
                    "SELECT id, NII FROM utilizadores WHERE perfil='aluno'"
                ).fetchall()
                for al in alunos_reset:
                    al = dict(al)
                    nii = al["NII"]
                    if not nii:
                        continue
                    pw_hash = generate_password_hash(nii)
                    conn.execute(
                        "UPDATE utilizadores SET Palavra_chave=?, must_change_password=1 WHERE id=?",
                        (pw_hash, al["id"]),
                    )
                conn.execute(
                    "INSERT INTO _migracoes VALUES('reset_creds_nii_v2', datetime('now','localtime'))"
                )
                print(
                    "[MIGRAÇÃO] Credenciais alunos resetadas: pw=hash(NII), must_change=1",
                    flush=True,
                )

            _bootstrap_dev_system_accounts(conn)
            conn.commit()
    except Exception as e:
        print(f"[ERRO] _ensure_extra_schema: {e}", flush=True)


# ═══════════════════════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════════════════════

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
app.config.from_object(cfg.Config)
cfg.configure_logging(app)

# ── Teardown: fechar conexão SQLite no fim de cada request ────────────────
app.teardown_appcontext(sr.close_request_db)


# ── Context processor: disponibiliza csrf_input() nos templates ──────────
@app.context_processor
def inject_csrf():
    return dict(csrf_input=csrf_input)


# ── Registar Blueprints ──────────────────────────────────────────────────
from blueprints.api import api_bp  # noqa: E402
from blueprints.auth import auth_bp  # noqa: E402
from blueprints.aluno import aluno_bp  # noqa: E402
from blueprints.cmd import cmd_bp  # noqa: E402
from blueprints.operations import ops_bp  # noqa: E402
from blueprints.admin import admin_bp  # noqa: E402
from blueprints.reporting import report_bp  # noqa: E402

app.register_blueprint(api_bp)
app.register_blueprint(auth_bp)
app.register_blueprint(aluno_bp)
app.register_blueprint(cmd_bp)
app.register_blueprint(ops_bp)
app.register_blueprint(admin_bp)
app.register_blueprint(report_bp)


# Expor constantes de config usadas localmente
_is_production = cfg.is_production
CRON_API_TOKEN = cfg.CRON_API_TOKEN
DIAS_ANTECEDENCIA = cfg.DIAS_ANTECEDENCIA

_APP_BOOTSTRAPPED = False


def _init_app_once() -> None:
    """Inicialização segura para app.py importado via gunicorn ou execução direta."""
    global _APP_BOOTSTRAPPED
    if _APP_BOOTSTRAPPED:
        return
    sr.ensure_schema()
    _ensure_extra_schema()
    try:
        sr.ensure_daily_backup()
    except Exception as exc:
        app.logger.warning("Backup no bootstrap falhou: %s", exc)
    _APP_BOOTSTRAPPED = True


@app.before_request
def _bootstrap_before_request():
    """Garante que o schema da BD é criado antes do primeiro pedido (Gunicorn).
    Auto-remove-se após a primeira execução para não correr em cada request."""
    _init_app_once()
    # Remover este handler — já não é necessário
    app.before_request_funcs.setdefault(None, [])
    try:
        app.before_request_funcs[None].remove(_bootstrap_before_request)
    except ValueError:
        pass


def _bootstrap_dev_system_accounts(conn=None) -> None:
    """Sincroniza PERFIS_ADMIN/PERFIS_TESTE para a BD em desenvolvimento (passwords hashed)."""
    if _is_production:
        return
    perfis = {**getattr(sr, "PERFIS_ADMIN", {}), **getattr(sr, "PERFIS_TESTE", {})}
    if not perfis:
        return
    owns_conn = conn is None
    try:
        if owns_conn:
            conn = sr.db()
        cols = {
            r["name"]
            for r in conn.execute("PRAGMA table_info(utilizadores)").fetchall()
        }
        if not cols:
            return
        for nii, p in perfis.items():
            row = conn.execute(
                "SELECT id, Palavra_chave FROM utilizadores WHERE NII=?", (nii,)
            ).fetchone()
            pw_hash = generate_password_hash(p.get("senha", ""))
            nome = p.get("nome", nii)
            perfil = p.get("perfil", "aluno")
            ano = str(p.get("ano", "") or "")
            if row is None:
                conn.execute(
                    """INSERT INTO utilizadores
                    (NII,NI,Nome_completo,Palavra_chave,ano,perfil,must_change_password,password_updated_at,is_active)
                    VALUES (?,?,?,?,?,?,0,datetime('now','localtime'),1)""",
                    (nii, nii, nome, pw_hash, ano, perfil),
                )
            else:
                stored = row["Palavra_chave"] or ""
                # Sempre sincroniza perfil, nome e ano — garante que perfis de sistema
                # nao ficam "presos" como aluno se o campo estiver errado na BD.
                conn.execute(
                    "UPDATE utilizadores SET perfil=?, Nome_completo=?, ano=?, must_change_password=CASE WHEN ? != 'aluno' THEN 0 ELSE must_change_password END WHERE id=?",
                    (perfil, nome, ano, perfil, row["id"]),
                )
                # Migra password de plain-text para hash se ainda nao foi migrada
                if stored == p.get("senha", ""):
                    conn.execute(
                        "UPDATE utilizadores SET Palavra_chave=?, password_updated_at=datetime('now','localtime') WHERE id=?",
                        (pw_hash, row["id"]),
                    )
        if owns_conn:
            conn.commit()
    except Exception as exc:
        app.logger.warning(f"_bootstrap_dev_system_accounts falhou: {exc}")
    finally:
        if owns_conn and conn is not None:
            try:
                conn.close()
            except Exception:
                pass


_init_app_once()

_WAL_CHECKPOINT_INTERVAL = 300  # checkpoint WAL a cada 5 min
_last_wal_checkpoint = 0.0


@app.before_request
def before():
    global _last_wal_checkpoint
    g._t0 = time.perf_counter()

    # Refrescar sessão permanente em cada request (reset timeout inatividade)
    if "user" in session:
        session.modified = True

    # WAL checkpoint periódico
    now = time.time()
    if now - _last_wal_checkpoint > _WAL_CHECKPOINT_INTERVAL:
        _last_wal_checkpoint = now
        sr.wal_checkpoint()

    if request.method == "POST":
        # Blueprint "api" usa Bearer token — sem CSRF
        if request.blueprint == "api":
            return
        t = session.get("_csrf_token", "")
        ft = request.form.get("csrf_token", "")
        if not t or not ft or not secrets.compare_digest(t, ft):
            # Sessão expirada sem CSRF — redirecionar para login
            if "user" not in session and request.endpoint not in {None}:
                flash(
                    "A sessão expirou. Inicia sessão novamente e repete a operação.",
                    "warn",
                )
                return redirect(url_for("auth.login"))
            # CSRF inválido (inclui login POST) — rejeitar sempre
            abort(400)


@app.after_request
def after(r):
    r.headers.setdefault("X-Content-Type-Options", "nosniff")
    r.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    r.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    r.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'",
    )
    # Logging de performance — avisar para requests lentos
    t0 = getattr(g, "_t0", None)
    if t0 is not None:
        dt_ms = (time.perf_counter() - t0) * 1000
        if dt_ms > 500:
            app.logger.warning(
                "Slow request: %s %s %.0fms", request.method, request.path, dt_ms
            )
    return r


@app.errorhandler(400)
def err400(e):
    from flask import render_template

    return render_template("errors/400.html", content=""), 400


@app.errorhandler(404)
def err404(e):
    from flask import render_template

    return render_template("errors/404.html", content=""), 404


@app.errorhandler(500)
def err500(e):
    from flask import render_template

    app.logger.exception("Erro 500")
    return render_template("errors/500.html", content=""), 500


# ── Arranque directo ─────────────────────────────────────────────────────
if __name__ == "__main__":
    _init_app_once()
    cfg.print_startup_banner(sr.BASE_DADOS)
    app.run(debug=cfg.DEBUG, host="0.0.0.0", port=cfg.PORT)
