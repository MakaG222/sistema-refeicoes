"""
Sistema de Refeições — Interface Web (Flask)
============================================
Corre com:  python app.py
Acede em:   http://localhost:8080

Credenciais:
  admin / admin123
  cozinha / cozinha123
  oficialdia / oficial123
  cmd1..4 / cmd1..4 123
  teste1 / teste1  (aluno)
"""

import os, sys, secrets, logging, time, threading, smtplib, json
from typing import Optional
from logging.handlers import RotatingFileHandler
from pathlib import Path
from datetime import date, datetime, timedelta
from functools import wraps
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from markupsafe import Markup, escape
from flask import (
    Flask, Response, render_template_string, request,
    redirect, url_for, session, flash, send_file, g, abort,
)

sys.path.insert(0, os.path.dirname(__file__))
import sistema_refeicoes_v8_4 as sr

# Garantir schema de notificações logo no arranque (mesmo sem __main__)
def _ensure_notif_schema():
    """Garante que a tabela de notificações, colunas extra e FTS estão correctos."""
    try:
        with sr.db() as conn:
            # Tabela de notificações enviadas
            conn.execute("""CREATE TABLE IF NOT EXISTS notificacoes_enviadas (
                id INTEGER PRIMARY KEY,
                utilizador_id INTEGER NOT NULL,
                data TEXT NOT NULL,
                tipo TEXT NOT NULL DEFAULT 'prazo',
                enviado_em TEXT,
                UNIQUE(utilizador_id, data, tipo)
            )""")
            # Colunas extra em utilizadores
            cols = [r['name'] for r in conn.execute("PRAGMA table_info(utilizadores)").fetchall()]
            if 'email' not in cols:
                conn.execute("ALTER TABLE utilizadores ADD COLUMN email TEXT")
            if 'telemovel' not in cols:
                conn.execute("ALTER TABLE utilizadores ADD COLUMN telemovel TEXT")
            if 'is_active' not in cols:
                conn.execute("ALTER TABLE utilizadores ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1")

            # Verificar e reparar FTS5 se necessário
            try:
                conn.execute("SELECT COUNT(*) FROM utilizadores_fts").fetchone()
            except Exception:
                # FTS está corrompida — recriar
                app.logger.warning("[AVISO] FTS corrompida — a recriar...")
                try:
                    conn.execute("PRAGMA writable_schema = ON")
                    conn.execute("DELETE FROM sqlite_master WHERE name='utilizadores_fts' AND type='table'")
                    conn.execute("DELETE FROM sqlite_master WHERE name IN ('utilizadores_ai_fts','utilizadores_ad_fts','utilizadores_au_fts') AND type='trigger'")
                    conn.execute("PRAGMA writable_schema = OFF")
                    conn.commit()
                except Exception as e2:
                    app.logger.warning(f"[AVISO] writable_schema: {e2}")

                conn.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS utilizadores_fts
USING fts5(Nome_completo, content='utilizadores', content_rowid='id')""")
                conn.execute("INSERT OR IGNORE INTO utilizadores_fts(rowid, Nome_completo) SELECT id, Nome_completo FROM utilizadores")
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
                app.logger.info("[INFO] FTS recriada com sucesso.")

            conn.commit()
    except Exception as e:
        # Usa print porque app.logger pode não estar inicializado ainda
        print(f"[ERRO] _ensure_notif_schema: {e}", flush=True)

_ensure_notif_schema()

# ═══════════════════════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════════════════════

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "escola-naval-secret-2024")
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")

DIAS_ANTECEDENCIA = 15   # alunos podem marcar até N dias à frente (inclui fins de semana)

# ─── Configuração de Notificações (Email/SMS) ─────────────────────────────
# Edita estas variáveis ou usa variáveis de ambiente para activar notificações.
SMTP_HOST     = os.environ.get('SMTP_HOST', '')          # ex: 'smtp.gmail.com'
SMTP_PORT     = int(os.environ.get('SMTP_PORT', '587'))
SMTP_USER     = os.environ.get('SMTP_USER', '')          # ex: 'refeicoes@escolanaval.pt'
SMTP_PASSWORD = os.environ.get('SMTP_PASSWORD', '')
SMTP_FROM     = os.environ.get('SMTP_FROM', SMTP_USER)
# Twilio SMS (opcional — deixar vazio para desactivar)
TWILIO_SID    = os.environ.get('TWILIO_SID', '')
TWILIO_TOKEN  = os.environ.get('TWILIO_TOKEN', '')
TWILIO_FROM   = os.environ.get('TWILIO_FROM', '')        # ex: '+351XXXXXXXXX'

NOTIF_HORAS_AVISO = 24   # avisar X horas antes do prazo expirar
NOTIF_INTERVALO_SCHEDULER = 3600  # verificar a cada 60 min (em segundos)

log_dir = Path(__file__).resolve().parent / "logs"
log_dir.mkdir(exist_ok=True)
fmt_log = logging.Formatter("%(asctime)s %(levelname)s: %(message)s")
fh = RotatingFileHandler(log_dir / "app.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
fh.setFormatter(fmt_log)
app.logger.addHandler(fh)
app.logger.setLevel(logging.INFO)

# ─── Scheduler automático de avisos ───────────────────────────────────────
# Corre em background, sem dependências extra. Verifica e envia avisos de
# prazo a cada NOTIF_INTERVALO_SCHEDULER segundos.
# Só arranca se pelo menos um canal (SMTP ou Twilio) estiver configurado.
_scheduler_timer: Optional[threading.Timer] = None

def _scheduler_loop():
    """Loop periódico de envio de avisos. Reagenda-se automaticamente."""
    global _scheduler_timer
    try:
        # Só executa se houver pelo menos um canal de notificação ativo
        if (SMTP_HOST and SMTP_USER and SMTP_PASSWORD) or (TWILIO_SID and TWILIO_TOKEN and TWILIO_FROM):
            _verificar_e_enviar_avisos()
    except Exception as e:
        try:
            app.logger.error(f"Scheduler de avisos falhou: {e}")
        except Exception:
            pass
    finally:
        # Reagenda independentemente de erros
        _scheduler_timer = threading.Timer(NOTIF_INTERVALO_SCHEDULER, _scheduler_loop)
        _scheduler_timer.daemon = True
        _scheduler_timer.start()

def _iniciar_scheduler():
    """Inicia o scheduler de avisos em background (chamado no arranque)."""
    global _scheduler_timer
    if _scheduler_timer is not None:
        return  # já está a correr
    # Primeiro disparo com 30s de delay para dar tempo ao Flask de arrancar
    _scheduler_timer = threading.Timer(30, _scheduler_loop)
    _scheduler_timer.daemon = True
    _scheduler_timer.start()
    try:
        app.logger.info(
            f"📬 Scheduler de avisos iniciado "
            f"(intervalo: {NOTIF_INTERVALO_SCHEDULER//60}min, "
            f"aviso: {NOTIF_HORAS_AVISO}h antes do prazo)"
        )
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════════════════
# HELPERS DE NEGÓCIO
# ═══════════════════════════════════════════════════════════════════════════

def _refeicao_set(uid, dt, pa, lanche, alm, jan, sai, alterado_por="sistema"):
    r = {'pequeno_almoco': pa, 'lanche': lanche,
         'almoco': alm or None, 'jantar_tipo': jan or None,
         'jantar_sai_unidade': sai}
    return sr.refeicao_save(uid, dt, r, alterado_por=alterado_por)

def _audit(actor: str, action: str, detail: str = '') -> None:
    """Regista uma entrada de auditoria na tabela admin_audit_log."""
    try:
        with sr.db() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS admin_audit_log (
                id INTEGER PRIMARY KEY,
                ts TEXT DEFAULT (datetime('now','localtime')),
                actor TEXT NOT NULL,
                action TEXT NOT NULL,
                detail TEXT
            )""")
            conn.execute("INSERT INTO admin_audit_log(actor,action,detail) VALUES(?,?,?)",
                         (actor, action, detail))
            conn.commit()
    except Exception as exc:
        app.logger.warning(f"_audit falhou [{action}]: {exc}")

def _alterar_password(nii, old, new):
    uid = sr.user_id_by_nii(nii)
    if not uid:
        return False, "Conta de sistema — não é possível alterar a password."
    with sr.db() as conn:
        row = conn.execute("SELECT Palavra_chave FROM utilizadores WHERE id=?", (uid,)).fetchone()
    if not row:
        return False, "Utilizador não encontrado."
    ph = row['Palavra_chave']
    ok = (old == (ph or ''))
    if not ok:
        return False, "Password atual incorreta."
    if len(new) < 6:
        return False, "A nova password deve ter pelo menos 6 caracteres."
    with sr.db() as conn:
        conn.execute("""UPDATE utilizadores SET Palavra_chave=?, must_change_password=0,
                        password_updated_at=datetime('now','localtime') WHERE id=?""", (new, uid))
        conn.commit()
    return True, ""

def _criar_utilizador(nii, ni, nome, ano, perfil, pw):
    try:
        if not all([nii, ni, nome, ano, perfil, pw]):
            return False, "Todos os campos são obrigatórios."
        if len(pw) < 4:
            return False, "Password deve ter pelo menos 4 caracteres."
        with sr.db() as conn:
            conn.execute("""INSERT INTO utilizadores
              (NII,NI,Nome_completo,Palavra_chave,ano,perfil,must_change_password,password_updated_at)
              VALUES (?,?,?,?,?,?,1,datetime('now','localtime'))""",
              (nii, ni, nome, pw, ano, perfil))
            conn.commit()
        _audit("sistema", "criar_utilizador", f"NII={nii} perfil={perfil} ano={ano}")
        return True, ""
    except Exception as e:
        app.logger.error(f"_criar_utilizador({nii}): {e}")
        return False, str(e)

def _reset_pw(nii, nova_pw=None):
    import random, string
    if not nova_pw:
        nova_pw = ''.join(random.choices(string.ascii_letters + string.digits, k=10))
    nh = nova_pw
    with sr.db() as conn:
        cur = conn.execute("""UPDATE utilizadores SET Palavra_chave=?, must_change_password=1,
                              password_updated_at=datetime('now','localtime') WHERE NII=?""", (nh, nii))
        conn.commit()
    if cur.rowcount:
        _audit("sistema", "reset_password", f"NII={nii}")
        return True, nova_pw
    return False, "NII não encontrado."

def _unblock_user(nii):
    with sr.db() as conn:
        conn.execute("UPDATE utilizadores SET locked_until=NULL WHERE NII=?", (nii,))
        conn.commit()

def _eliminar_utilizador(nii):
    with sr.db() as conn:
        cur = conn.execute("DELETE FROM utilizadores WHERE NII=?", (nii,))
        conn.commit()
    return cur.rowcount > 0

def _registar_ausencia(uid, de, ate, motivo, criado_por):
    try:
        datetime.strptime(de, "%Y-%m-%d")
        datetime.strptime(ate, "%Y-%m-%d")
    except ValueError:
        return False, "Data inválida."
    if de > ate:
        return False, "A data de início não pode ser posterior à data de fim."
    with sr.db() as conn:
        conn.execute("""INSERT INTO ausencias (utilizador_id,ausente_de,ausente_ate,motivo,criado_por)
                        VALUES (?,?,?,?,?)""", (uid, de, ate, motivo or None, criado_por))
        conn.commit()
    return True, ""

def _remover_ausencia(aid):
    with sr.db() as conn:
        conn.execute("DELETE FROM ausencias WHERE id=?", (aid,))
        conn.commit()

def _get_ocupacao_dia(dt):
    return sr.get_ocupacao_capacidade(dt)

ANOS_LABELS = {1:'1º Ano', 2:'2º Ano', 3:'3º Ano', 4:'4º Ano', 5:'5º Ano', 6:'6º Ano',
               7:'CFBO', 8:'CFCO'}
ANOS_OPCOES = [(1,'1º Ano'),(2,'2º Ano'),(3,'3º Ano'),(4,'4º Ano'),(5,'5º Ano'),(6,'6º Ano'),
               (7,'CFBO — Curso de Formação Básica de Oficiais'),
               (8,'CFCO — Curso de Formação Complementar de Oficiais')]

def _ano_label(ano):
    return ANOS_LABELS.get(int(ano) if ano else 0, f'{ano}º Ano')

def _get_anos_disponiveis():
    with sr.db() as conn:
        rows = conn.execute(
            "SELECT DISTINCT ano FROM utilizadores WHERE ano > 0 ORDER BY ano"
        ).fetchall()
    return [r['ano'] for r in rows]

def _editar_ausencia(aid, uid, de, ate, motivo):
    try:
        datetime.strptime(de, "%Y-%m-%d")
        datetime.strptime(ate, "%Y-%m-%d")
    except ValueError:
        return False, "Data inválida."
    if de > ate:
        return False, "A data de início não pode ser posterior à data de fim."
    with sr.db() as conn:
        conn.execute("""UPDATE ausencias SET ausente_de=?,ausente_ate=?,motivo=?
                        WHERE id=? AND utilizador_id=?""", (de, ate, motivo or None, aid, uid))
        conn.commit()
    return True, ""

def _tem_ausencia_ativa(uid, d=None):
    """Verifica se utilizador tem ausência ativa na data (ou hoje)."""
    d_str = (d or date.today()).isoformat()
    with sr.db() as conn:
        row = conn.execute("""SELECT 1 FROM ausencias WHERE utilizador_id=?
                              AND ausente_de<=? AND ausente_ate>=?""",
                           (uid, d_str, d_str)).fetchone()
    return bool(row)

def _dia_editavel_aluno(d):
    """Editável pelo aluno: futuro, dentro de DIAS_ANTECEDENCIA, prazo ok. Fins de semana permitidos."""
    hoje = date.today()
    if d < hoje:
        return False, "Data no passado."
    if (d - hoje).days > DIAS_ANTECEDENCIA:
        return False, f"Só é possível marcar com {DIAS_ANTECEDENCIA} dias de antecedência."
    return sr.refeicao_editavel(d)

# ─── Notificações (Email / SMS) ───────────────────────────────────────────

def _send_email(to_addr: str, subject: str, body_html: str, body_text: str = '') -> bool:
    """Envia email via SMTP. Retorna True se enviou com sucesso."""
    if not SMTP_HOST or not SMTP_USER or not SMTP_PASSWORD or not to_addr:
        return False
    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From']    = SMTP_FROM or SMTP_USER
        msg['To']      = to_addr
        if body_text:
            msg.attach(MIMEText(body_text, 'plain', 'utf-8'))
        msg.attach(MIMEText(body_html, 'html', 'utf-8'))
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=8) as s:
            s.starttls()
            s.login(SMTP_USER, SMTP_PASSWORD)
            s.sendmail(msg['From'], [to_addr], msg.as_string())
        return True
    except Exception as e:
        app.logger.warning(f"Email falhou para {to_addr}: {e}")
        return False

def _send_sms(to_number: str, body: str) -> bool:
    """Envia SMS via Twilio. Retorna True se enviou."""
    if not TWILIO_SID or not TWILIO_TOKEN or not TWILIO_FROM or not to_number:
        return False
    try:
        import urllib.request, urllib.parse, base64
        data = urllib.parse.urlencode({'From': TWILIO_FROM, 'To': to_number, 'Body': body}).encode()
        url  = f'https://api.twilio.com/2010-04-01/Accounts/{TWILIO_SID}/Messages.json'
        creds = base64.b64encode(f'{TWILIO_SID}:{TWILIO_TOKEN}'.encode()).decode()
        req  = urllib.request.Request(url, data=data, headers={'Authorization': f'Basic {creds}'})
        urllib.request.urlopen(req, timeout=8)
        return True
    except Exception as e:
        app.logger.warning(f"SMS falhou para {to_number}: {e}")
        return False

def _notif_prazo_aluno(uid: int, nome: str, email: str, telef: str, d: date):
    """Envia notificação de prazo a um aluno (email e/ou SMS)."""
    prazo_dt = datetime(d.year, d.month, d.day) - timedelta(hours=sr.PRAZO_LIMITE_HORAS or 48)
    prazo_str = prazo_dt.strftime('%d/%m/%Y às %H:%M')
    d_str = d.strftime('%d/%m/%Y')

    html = f"""
    <div style="font-family:sans-serif;max-width:520px;margin:0 auto;border:1px solid #dde3ea;border-radius:12px;overflow:hidden">
      <div style="background:#0a2d4e;padding:1.2rem 1.5rem;color:#fff;display:flex;align-items:center;gap:.7rem">
        <span style="font-size:1.4rem">⚓</span>
        <span style="font-weight:800;font-size:1.1rem">Escola Naval — Refeições</span>
      </div>
      <div style="padding:1.5rem">
        <p>Olá <strong>{nome}</strong>,</p>
        <p style="margin-top:.8rem">O prazo para alterar as tuas refeições de <strong>{d_str}</strong> expira em:</p>
        <div style="background:#fef9e7;border:1.5px solid #f9e79f;border-radius:10px;padding:.9rem 1.1rem;margin:1rem 0;font-size:1.1rem;font-weight:800;color:#9a7d0a;text-align:center">
          ⏰ {prazo_str}
        </div>
        <p>Se ainda queres alterar as tuas refeições, <a href="http://localhost:8080/aluno" style="color:#0a2d4e;font-weight:700">acede ao sistema</a> antes do prazo.</p>
        <p style="margin-top:1rem;font-size:.83rem;color:#6c757d">Após o prazo, apenas o Oficial de Dia pode fazer alterações.</p>
      </div>
    </div>"""
    text = f"[Escola Naval] Prazo para alterar refeições de {d_str} expira em {prazo_str}. Acede ao sistema antes desta hora."

    def _async():
        if email:
            _send_email(email, f"[Escola Naval] Prazo de refeições a expirar — {d_str}", html, text)
        if telef:
            _send_sms(telef, text)
    threading.Thread(target=_async, daemon=True).start()

def _verificar_e_enviar_avisos():
    """Verifica alunos com prazo a expirar nas próximas NOTIF_HORAS_AVISO horas e envia aviso."""
    if not sr.PRAZO_LIMITE_HORAS:
        return
    agora = datetime.now()
    hoje  = agora.date()
    enviados = 0
    for i in range(1, DIAS_ANTECEDENCIA + 1):
        d = hoje + timedelta(days=i)
        if sr.dia_operacional(d) in ('feriado','exercicio'):
            continue
        prazo_dt = datetime(d.year, d.month, d.day) - timedelta(hours=sr.PRAZO_LIMITE_HORAS)
        janela_inicio = prazo_dt - timedelta(hours=NOTIF_HORAS_AVISO)
        if not (janela_inicio <= agora < prazo_dt):
            continue
        # Notificar alunos que têm refeições marcadas nesse dia
        with sr.db() as conn:
            rows = conn.execute("""
                SELECT u.id, u.Nome_completo, u.email, u.telemovel
                FROM utilizadores u
                JOIN refeicoes r ON r.utilizador_id=u.id AND r.data=?
                WHERE u.perfil='aluno'
                  AND (u.email IS NOT NULL OR u.telemovel IS NOT NULL)
                  AND NOT EXISTS (
                      SELECT 1 FROM notificacoes_enviadas n
                      WHERE n.utilizador_id=u.id AND n.data=? AND n.tipo='prazo'
                  )
            """, (d.isoformat(), d.isoformat())).fetchall()
        for row in rows:
            _notif_prazo_aluno(row['id'], row['Nome_completo'],
                               row['email'] or '', row['telemovel'] or '', d)
            try:
                with sr.db() as conn:
                    conn.execute("""INSERT OR IGNORE INTO notificacoes_enviadas
                                   (utilizador_id, data, tipo, enviado_em)
                                   VALUES (?,?,?,datetime('now','localtime'))""",
                                 (row['id'], d.isoformat(), 'prazo'))
                    conn.commit()
            except Exception:
                pass
            enviados += 1
    if enviados:
        app.logger.info(f"Avisos de prazo enviados: {enviados}")

# ═══════════════════════════════════════════════════════════════════════════
# TEMPLATE BASE
# ═══════════════════════════════════════════════════════════════════════════

BASE = """
<!doctype html>
<html lang="pt">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Escola Naval — Refeições</title>
  <style>
    :root{
      --bg:#f0f4f8;--card:#fff;--primary:#0a2d4e;--primary-light:#1a5276;
      --gold:#c9a227;--danger:#c0392b;--ok:#1e8449;--warn:#d68910;--muted:#6c757d;
      --text:#1a2533;--border:#dde3ea;--shadow:0 2px 10px rgba(0,0,0,.08);
      --radius:12px;
    }
    *{box-sizing:border-box;margin:0;padding:0}
    body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif;
         background:var(--bg);color:var(--text);line-height:1.5}
    a{text-decoration:none;color:inherit}

    nav{background:var(--primary);color:#fff;padding:.85rem 1.5rem;
        display:flex;align-items:center;justify-content:space-between;
        box-shadow:0 2px 8px rgba(0,0,0,.2);position:sticky;top:0;z-index:100}
    .nav-brand{font-weight:800;font-size:1.05rem}
    .nav-right{display:flex;align-items:center;gap:.75rem}
    .nav-user{opacity:.8;font-size:.82rem}
    .nav-link{color:#fff;font-weight:600;font-size:.85rem;
              background:rgba(255,255,255,.15);padding:.38rem .75rem;
              border-radius:8px;transition:.15s}
    .nav-link:hover{background:rgba(255,255,255,.25)}

    .container{max-width:1120px;margin:0 auto;padding:1.5rem 1.2rem}
    .page-header{display:flex;align-items:center;gap:.75rem;margin-bottom:1.4rem;flex-wrap:wrap}
    .page-title{font-size:1.4rem;font-weight:800;color:var(--primary);flex:1}
    .back-btn{display:inline-flex;align-items:center;gap:.3rem;padding:.42rem .85rem;
              border-radius:9px;font-weight:600;font-size:.85rem;border:1.5px solid var(--border);
              background:#fff;color:var(--text);cursor:pointer;transition:.15s;white-space:nowrap}
    .back-btn:hover{border-color:var(--primary-light);color:var(--primary);
                    box-shadow:0 2px 8px rgba(0,0,0,.08)}

    .card{background:var(--card);border-radius:var(--radius);
          box-shadow:var(--shadow);padding:1.4rem;margin-bottom:1.3rem}
    .card-title{font-size:.93rem;font-weight:700;color:var(--primary);
                margin-bottom:.9rem;padding-bottom:.5rem;
                border-bottom:2px solid var(--border)}

    .grid{display:grid;gap:1rem}
    .grid-2{grid-template-columns:repeat(auto-fit,minmax(280px,1fr))}
    .grid-3{grid-template-columns:repeat(auto-fit,minmax(210px,1fr))}
    .grid-4{grid-template-columns:repeat(auto-fit,minmax(170px,1fr))}
    .grid-6{grid-template-columns:repeat(auto-fit,minmax(110px,1fr))}

    .alert{padding:.82rem 1.05rem;border-radius:10px;margin:.7rem 0;font-size:.87rem;border:1px solid}
    .alert-ok{background:#eafaf1;border-color:#a9dfbf;color:#1e8449}
    .alert-error{background:#fdecea;border-color:#f1948a;color:#922b21}
    .alert-warn{background:#fef9e7;border-color:#f9e79f;color:#9a7d0a}

    .btn{display:inline-flex;align-items:center;gap:.32rem;padding:.55rem 1rem;
         border-radius:9px;font-weight:600;font-size:.87rem;border:none;
         cursor:pointer;transition:.15s;white-space:nowrap}
    .btn:hover{filter:brightness(1.08)}
    .btn-primary{background:var(--primary);color:#fff}
    .btn-ok{background:var(--ok);color:#fff}
    .btn-danger{background:var(--danger);color:#fff}
    .btn-warn{background:var(--warn);color:#fff}
    .btn-ghost{background:#fff;border:1.5px solid var(--border);color:var(--text)}
    .btn-ghost:hover{border-color:var(--primary-light);color:var(--primary)}
    .btn-sm{padding:.3rem .62rem;font-size:.78rem;border-radius:7px}
    .btn-gold{background:var(--gold);color:#fff}

    .form-group{margin-bottom:.85rem}
    label{font-weight:600;font-size:.83rem;display:block;margin-bottom:.28rem;color:#4a5568}
    input,select,textarea{width:100%;padding:.56rem .72rem;border:1.5px solid var(--border);
      border-radius:9px;font-size:.87rem;color:var(--text);background:#fff;transition:.15s}
    input:focus,select:focus{outline:none;border-color:var(--primary-light);
      box-shadow:0 0 0 3px rgba(36,113,163,.12)}
    input[type="checkbox"]{width:1rem;height:1rem;cursor:pointer;accent-color:var(--primary)}

    .table-wrap{overflow-x:auto;border-radius:10px;border:1px solid var(--border)}
    table{width:100%;border-collapse:collapse;font-size:.84rem}
    th{background:var(--primary);color:#fff;padding:.58rem .9rem;text-align:left;font-weight:600}
    td{padding:.5rem .9rem;border-bottom:1px solid var(--border)}
    tr:last-child td{border-bottom:none}
    tr:hover td{background:#f5f8fc}

    .badge{display:inline-block;padding:.18rem .52rem;border-radius:999px;font-size:.72rem;font-weight:700}
    .badge-ok{background:#d5f5e3;color:#1e8449}
    .badge-no{background:#fdecea;color:#922b21}
    .badge-warn{background:#fef9e7;color:#9a7d0a}
    .badge-info{background:#ebf5fb;color:#1a4a6e}
    .badge-gold{background:#fef3cd;color:#856404}
    .badge-muted{background:#ecf0f1;color:#6c757d}

    .stat-box{padding:1.05rem;border:1.5px solid var(--border);border-radius:12px;
              text-align:center;background:#fff;transition:.15s}
    .stat-box:hover{border-color:var(--primary-light);box-shadow:0 4px 14px rgba(0,0,0,.09)}
    .stat-num{font-size:1.85rem;font-weight:800;color:var(--primary);line-height:1.1}
    .stat-lbl{font-size:.77rem;color:var(--muted);margin-top:.22rem}

    .week-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(135px,1fr));gap:.65rem}
    .week-card{border:1.5px solid var(--border);border-radius:12px;padding:.82rem;
               background:#fff;transition:.15s}
    .week-card:hover{border-color:var(--primary-light);box-shadow:0 4px 12px rgba(0,0,0,.09)}
    .week-card.day-off{background:#f9fafb}
    .week-card.weekend-active{border-color:var(--gold);background:#fffdf5}
    .week-dow{font-weight:800;font-size:.88rem;color:var(--primary)}
    .week-dow.weekend{color:var(--gold)}
    .week-date{font-size:.73rem;color:var(--muted);margin-bottom:.42rem}
    .week-meals{display:flex;flex-wrap:wrap;gap:.24rem;margin-bottom:.38rem}

    .ausente-banner{background:#fff9e6;border:1.5px solid var(--gold);border-radius:10px;
                    padding:.65rem 1rem;font-size:.84rem;color:#856404;margin-bottom:.8rem}

    .occ-bar{height:7px;border-radius:999px;overflow:hidden;background:#e9ecef;margin:.2rem 0}
    .occ-bar>span{display:block;height:100%;border-radius:999px}
    .occ-label{font-size:.73rem;color:var(--muted)}

    .meal-chip{font-size:.67rem;font-weight:700;padding:.15rem .42rem;border-radius:999px;white-space:nowrap}
    .chip-ok{background:#d5f5e3;color:#1e8449}
    .chip-no{background:#fdecea;color:#922b21}
    .chip-type{background:#ebf5fb;color:#1a4a6e}

    .prazo-warn{font-size:.7rem;color:#9a7d0a;font-weight:600}
    .prazo-lock{font-size:.7rem;color:#922b21;font-weight:600}

    .login-wrap{display:flex;justify-content:center;align-items:center;
                min-height:100vh;
                background:linear-gradient(160deg,#0a2d4e 0%,#1a5276 60%,#0a2d4e 100%)}
    .login-box{background:#fff;border-radius:20px;padding:2.6rem 2.4rem;width:100%;
               max-width:420px;box-shadow:0 24px 70px rgba(0,0,0,.32)}
    .login-header{display:flex;align-items:center;gap:1rem;margin-bottom:1.6rem;justify-content:center}
    .login-title{font-size:1.45rem;font-weight:900;color:var(--primary);line-height:1.1}
    .login-subtitle{font-size:.8rem;color:var(--muted);margin-top:.15rem;letter-spacing:.4px;text-transform:uppercase}

    .year-tabs{display:flex;gap:.4rem;flex-wrap:wrap;margin-bottom:1.1rem}
    .year-tab{padding:.42rem .95rem;border-radius:9px;font-weight:700;font-size:.84rem;
              border:1.5px solid var(--border);background:#fff;color:var(--text);transition:.15s}
    .year-tab:hover{border-color:var(--primary-light);color:var(--primary)}
    .year-tab.active{background:var(--primary);color:#fff;border-color:var(--primary)}

    .action-card{border:1.5px solid var(--border);border-radius:12px;padding:.95rem;
                 background:#fff;text-align:center;transition:.15s;cursor:pointer;
                 display:flex;flex-direction:column;align-items:center;gap:.35rem}
    .action-card:hover{border-color:var(--primary-light);box-shadow:0 4px 14px rgba(0,0,0,.1);
                       transform:translateY(-1px)}
    .action-card .icon{font-size:1.55rem}
    .action-card .label{font-weight:700;font-size:.84rem}
    .action-card .desc{font-size:.74rem;color:var(--muted)}

    .gap-btn{display:flex;gap:.5rem;flex-wrap:wrap;align-items:center}
    .text-muted{color:var(--muted)}
    .small{font-size:.79rem}
    .right{text-align:right}
    .center{text-align:center}
    .flex{display:flex;gap:.5rem;align-items:center;flex-wrap:wrap}
    .flex-between{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:.5rem}
    hr{border:none;border-top:1.5px solid var(--border);margin:.85rem 0}

    @media(max-width:640px){
      .grid-2,.grid-3,.grid-4{grid-template-columns:1fr}
      .week-grid{grid-template-columns:1fr 1fr}
    }
  </style>
</head>
<body>
{% autoescape true %}
{% if session.user %}
<nav>
  <span class="nav-brand" style="display:flex;align-items:center;gap:.6rem"><img src="data:image/png;base64,/9j/4AAQSkZJRgABAQAASABIAAD/4QBMRXhpZgAATU0AKgAAAAgAAYdpAAQAAAABAAAAGgAAAAAAA6ABAAMAAAABAAEAAKACAAQAAAABAAACaaADAAQAAAABAAACrQAAAAD/7QA4UGhvdG9zaG9wIDMuMAA4QklNBAQAAAAAAAA4QklNBCUAAAAAABDUHYzZjwCyBOmACZjs+EJ+/8AAEQgCrQJpAwEiAAIRAQMRAf/EAB8AAAEFAQEBAQEBAAAAAAAAAAABAgMEBQYHCAkKC//EALUQAAIBAwMCBAMFBQQEAAABfQECAwAEEQUSITFBBhNRYQcicRQygZGhCCNCscEVUtHwJDNicoIJChYXGBkaJSYnKCkqNDU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6g4SFhoeIiYqSk5SVlpeYmZqio6Slpqeoqaqys7S1tre4ubrCw8TFxsfIycrS09TV1tfY2drh4uPk5ebn6Onq8fLz9PX29/j5+v/EAB8BAAMBAQEBAQEBAQEAAAAAAAABAgMEBQYHCAkKC//EALURAAIBAgQEAwQHBQQEAAECdwABAgMRBAUhMQYSQVEHYXETIjKBCBRCkaGxwQkjM1LwFWJy0QoWJDThJfEXGBkaJicoKSo1Njc4OTpDREVGR0hJSlNUVVZXWFlaY2RlZmdoaWpzdHV2d3h5eoKDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uLj5OXm5+jp6vLz9PX29/j5+v/bAEMAAQEBAQEBAgEBAgMCAgIDBAMDAwMEBgQEBAQEBgcGBgYGBgYHBwcHBwcHBwgICAgICAkJCQkJCwsLCwsLCwsLC//bAEMBAgICAwMDBQMDBQsIBggLCwsLCwsLCwsLCwsLCwsLCwsLCwsLCwsLCwsLCwsLCwsLCwsLCwsLCwsLCwsLCwsLC//dAAQAJ//aAAwDAQACEQMRAD8A/rJ/bT+O/wAZvhT8RtM0r4d63/ZthNpqzyxfZoJt0plkBbdKjMPlUDA4r4xl/bZ/afeULb+JuM8/6Faf/Ga9g/4KOWEt/wDGbRIkbC/2MmeP+m8tfnndXMmjyixtPnkY4Hb275FfzfxhnuZUM3xNOjiakYqWiU5JLRbJM/ZeHsqwVbLqMqlGDlbVuKvv1dj6+/4bV/aXQKJPEfOM/wDHna//ABmm/wDDa/7S3T/hI/8AyTtf/jNfJ0kN7CFfWrTc7ruU+YB8p/3feo/Msm4Fl/5ENfMviDPWrxxVX/wOf+Z1VMly5u3soL/t2P8AkfWh/bW/aWHH/CR/+Sdr/wDGaT/htf8AaX/6GP8A8k7X/wCM18lh7Hp9i/8AIhpd1j/z4/8AkQ10U8/z5xV8TV/8Dl/mR/ZmXw932MH/ANux/wAj6z/4bX/aX/6GP/yTtf8A4zR/w2v+0v8A9DH/AOSdr/8AGa+TN1j/AM+P/kQ0brH/AJ8f/Ihq/wC3s9/6CKn/AIHL/MX1DL/+fEP/AAGP+R9Z/wDDa/7S/wD0Mf8A5J2v/wAZo/4bX/aX/wChj/8AJO1/+M18mbrH/nx/8iGjdY/8+P8A5ENH9vZ7/wBBFT/wOX+YfUMv/wCfEP8AwGP+R9Z/8Nr/ALS//Qx/+Sdr/wDGaP8Ahtf9pf8A6GP/AMk7X/4zXyZmy/58v/Iho3WP/Pj/AORDSefZ7/0E1P8AwOX+YfUMv/58Q/8AAY/5H1n/AMNr/tL/APQx/wDkna//ABmj/htf9pf/AKGP/wAk7X/4zXyZusf+fH/yIaN1j/z4/wDkQ0f29nv/AEE1P/A5f5h9Qy//AJ8Q/wDAYn1n/wANr/tL/wDQx/8Akna//GaP+G1/2l/+hj/8k7X/AOM18mbrH/nx/wDIho3WP/Pj/wCRDR/b2e/9BNT/AMDl/mH1DL/+fEP/AAGJ9Z/8Nr/tL/8AQx/+Sdr/APGaP+G1/wBpf/oY/wDyTtf/AIzXyZusf+fH/wAiGjdY/wDPj/5ENH9vZ7/0E1P/AAOX+YfUMv8A+fEP/AYn1n/w2v8AtL/9DH/5J2v/AMZo/wCG1/2l/wDoY/8AyTtf/jNfJm6x/wCfH/yIaN1j/wA+P/kQ0f29nv8A0E1P/A5f5h9Qy/8A58Q/8Bj/AJH1n/w2v+0v/wBDH/5J2v8A8Zo/4bX/AGl/+hj/APJO1/8AjNfJm6x/58f/ACIaN1j/AM+P/kQ0f29nv/QTU/8AA5f5j+oZf/z4h/4DH/I+s/8Ahtf9pf8A6GP/AMk7X/4zR/w2v+0v/wBDH/5J2v8A8Zr5M3WP/Pj/AORDRusf+fH/AMiGj+3s9/6Can/gcv8AMPqGX/8APiH/AIDH/I+s/wDhtf8AaX/6GP8A8k7X/wCM0f8ADa/7S/8A0Mf/AJJ2v/xmvkzdY/8APj/5ENG6x/58f/Iho/t7Pf8AoJqf+By/zD6hl/8Az4h/4DH/ACPrP/htf9pf/oY//JO1/wDjNH/Da/7S/wD0Mf8A5J2v/wAZr5M3WP8Az4/+RDRusf8Anx/8iGn/AG9nv/QTU/8AA5f5h9Qy/wD58Q/8Bj/kfWf/AA2v+0v/ANDH/wCSdr/8Zo/4bX/aX/6GP/yTtf8A4zXyZusf+fH/AMiGjdY/8+P/AJENH9vZ7/0EVP8AwOX+YfUMv/58Q/8AAY/5H1n/AMNr/tL/APQx/wDkna//ABmj/htf9pf/AKGP/wAk7X/4zXyZusf+fH/yIaN1j/z4/wDkQ0f29nv/AEEVP/A5f5h9Qy//AJ8Q/wDAY/5H1n/w2v8AtL/9DH/5J2v/AMZo/wCG1/2l/wDoY/8AyTtf/jNfJm6x/wCfH/yIaN1j/wA+P/kQ0f29nv8A0EVP/A5f5h9Qy/8A58Q/8Bj/AJH1n/w2v+0v/wBDH/5J2v8A8Zo/4bX/AGl/+hj/APJO1/8AjNfJm6x/58f/ACIaN1j/AM+P/kQ0f29nv/QRU/8AA5f5h9Qy/wD58Q/8Bj/kfWf/AA2v+0v/ANDH/wCSdr/8Zo/4bX/aX/6GP/yTtf8A4zXyZusf+fH/AMiGjdY/8+P/AJENH9vZ7/0EVP8AwOX+YfUMv/58Q/8AAY/5H1n/AMNr/tL/APQx/wDkna//ABmj/htf9pf/AKGP/wAk7X/4zXyZusf+fH/yIaN1j/z4/wDkQ0f29nv/AEEVP/A5f5h9Qy//AJ8Q/wDAY/5H1n/w2v8AtL/9DH/5J2v/AMZo/wCG1/2l/wDoY/8AyTtf/jNfJm6x/wCfH/yIaN1j/wA+P/kQ0f29nv8A0EVP/A5f5h9Qy/8A58Q/8Bj/AJH1n/w2v+0v/wBDH/5J2v8A8Zo/4bX/AGl/+hj/APJO1/8AjNfJm6x/58f/ACIaN1j/AM+P/kQ0v7ez3/oJqf8Agcv8w/s/L/8AnxD/AMBj/kfWf/Da/wC0v/0Mf/kna/8Axmj/AIbX/aX/AOhj/wDJO1/+M18mbrH/AJ8f/Iho3WP/AD4/+RDR/b2e/wDQTU/8Dl/mH1DL/wDnxD/wGP8AkfWf/Da/7S//AEMf/kna/wDxmj/htf8AaX/6GP8A8k7X/wCM18mbrH/nx/8AIho3WP8Az4/+RDR/b2e/9BNT/wADl/mH1DL/APnxD/wGP+R9Z/8ADa/7S/8A0Mf/AJJ2v/xmj/htf9pf/oY//JO1/wDjNfJm6x/58f8AyIaN1j/z4/8AkQ0f29nv/QTU/wDA5f5h9Qy//nxD/wABj/kfWf8Aw2v+0v8A9DH/AOSdr/8AGaP+G1/2l/8AoY//ACTtf/jNfJm6x/58f/Iho3WP/Pj/AORDR/b2e/8AQTU/8Dl/mH1DL/8AnxD/AMBj/kfWf/Da/wC0v/0Mf/kna/8Axmj/AIbX/aX/AOhj/wDJO1/+M18mbrH/AJ8f/Iho3WP/AD4/+RDR/b2e/wDQTU/8Dl/mH1DL/wDnxD/wGP8AkfWf/Da/7S//AEMf/kna/wDxmj/htf8AaX/6GP8A8k7X/wCM18mbrH/nx/8AIho3WP8Az4/+RDR/b2e/9BNT/wADl/mH1DL/APnxD/wGP+R9Z/8ADa/7S/8A0Mf/AJJ2v/xmj/htf9pf/oY//JO1/wDjNfJm6x/58f8AyIaN1j/z4/8AkQ0f29nv/QTU/wDA5f5h9Qy//nxD/wABj/kfWf8Aw2v+0v8A9DH/AOSdr/8AGaP+G1/2l/8AoY//ACTtf/jNfJm6x/58f/Iho3WP/Pj/AORDT/t7Pf8AoJqf+By/zD6hl/8Az4h/4Cv8j6z/AOG1/wBpf/oY/wDyTtf/AIzR/wANr/tL/wDQx/8Akna//Ga+TN1j/wA+P/kQ0brH/nx/8iGj+3s9/wCgip/4HL/MPqGX/wDPiH/gMf8AI+s/+G1/2l/+hj/8k7X/AOM0f8Nr/tL/APQx/wDkna//ABmvkzdY/wDPj/5ENG6x/wCfH/yIaP7ez3/oIqf+By/zD6hl/wDz4h/4DH/I+s/+G1/2l/8AoY//ACTtf/jNH/Da/wC0v/0Mf/kna/8AxmvkzdY/8+P/AJENG6x/58f/ACIaP7ez3/oIqf8Agcv8w+oZf/z4h/4DH/I+s/8Ahtf9pf8A6GP/AMk7X/4zR/w2v+0v/wBDH/5J2v8A8Zr5M3WP/Pj/AORDRusf+fH/AMiGj+3s9/6CKn/gcv8AMPqGX/8APiH/AIDH/I+s/wDhtf8AaX/6GP8A8k7X/wCM0f8ADa/7S/8A0Mf/AJJ2v/xmvkzdY/8APj/5ENG6x/58f/Iho/t7Pf8AoIqf+By/zD6hl/8Az4h/4DH/ACPrP/htf9pf/oY//JO1/wDjNH/Da/7S/wD0Mf8A5J2v/wAZr5M3WP8Az4/+RDRusf8Anx/8iGj+3s9/6CKn/gcv8w+oZf8A8+If+Ax/yPrP/htf9pf/AKGP/wAk7X/4zSH9tj9pYdfEf/kna/8AxmvkwtZdBZf+RDTB9kzn7H/5ENXHPs82eJq/+By/zOmjlGWzi5SowX/bsT62H7bH7Sx6eJP/ACTtf/jNV5f23f2lpB/o/iXB/wCvO1/rDXyoGtR/y5f+RDU09g2ncvWVbiPOafxYqov+35f5jWTZdL4aMH/27H/I/RT9mn9qr48+PvjZoXg/xjrn2uwvZJVmi+y28e4LE7D5kiVhyAeCK/Ymv59v2N4vO/aO8MXC9Fmm/wDRElf0E1+z+GePxGLy2rUxNWU5Ko1eTbduWOl3fQ/NeMMLSoYyEKMFFcq2SXV9j//Q/pg/4KMgL8ZdFlXhhoyDP/beWvz5smK36agvEyXCqG9s56dOtfoN/wAFHOPjBop/6g6f+j5a/PiyOJB/18g1/MfGUU89xF/5v0R+0cMyby2ml2/zP39+DuieHtO+Dmk+KdU/dRf2XFd3L/MeREGZsA56DoB+FcFqP7XP7I2lFo77xAEaKXyX/wBEvDhh1HERql42tra+/wCCfuuwTPs2eCbyTqAcCwfn6V/M/wD8E9/+CPnwd/bL+HHiX4jeOPEWrWgXXZotum3cEYGYYpORJaTf89D/ABen4/u/DHDuXzwNOc4R1SvdH45nGY4mOLlGLe7/ADP6W5/2yP2OIGAfxOAGGQPsV6eP+/NQf8Nn/sZ/9DQP/AK+/wDjNfl23/Bt7+yjJDEtr4s8WMAoyft1mRn2xp9Q/wDENx+y1/0Nfizrn/j9tP8A5X19NT4WyLl99K/kedUzDGc2lz9Sv+Gz/wBjP/oaB/4BX3/xmj/hs/8AYz/6Ggf+AV9/8Zr8tf8AiG4/Za/6GvxZ1z/x+2n/AMr6P+Ibj9lr/oa/FnXP/H7af/K+r/1WyDsR/aGN8z9Sv+Gz/wBjP/oaB/4BX3/xmj/hs/8AYz/6Ggf+AV9/8Zr8tf8AiG4/Za/6GvxZ1z/x+2n/AMr6P+Ibj9lr/oa/FnXP/H7af/K+j/VbIOwf2hjfM/Ur/hs/9jP/AKGgf+AV9/8AGaP+Gz/2M/8AoaB/4BX3/wAZr8tf+Ibj9lrGP+Er8Wc/9Ptp/wDK+g/8G3H7LRz/AMVX4s5/6fbT/wCV9H+q2Qdg/tDG+Z+pX/DZ/wCxn/0NA/8AAK+/+M0f8Nn/ALGf/Q0D/wAAr7/4zX5an/g24/ZaOf8Aiq/FnP8A0+2n/wAr6D/wbcfstHP/ABVfizn/AKfbT/5X0f6rZB2D+0Mb5n6lf8Nn/sZ/9DQP/AK+/wDjNH/DZ/7Gf/Q0D/wCvv8A4zX5an/g24/ZaOf+Kr8Wc/8AT7af/K+g/wDBtx+y0c/8VX4s5/6fbT/5X0f6rZB2D+0Mb5n6lf8ADZ/7Gf8A0NA/8Ar7/wCM0f8ADZ/7Gf8A0NA/8Ar7/wCM1+Wp/wCDbj9lo5/4qvxZz/0+2n/yvoP/AAbcfstHP/FV+LOf+n20/wDlfR/qtkHYP7QxvmfqV/w2f+xn/wBDQP8AwCvv/jNH/DZ/7Gf/AENA/wDAK+/+M1+Wp/4NuP2Wjn/iq/FnP/T7af8AyvoP/Btx+y0c/wDFV+LOf+n20/8AlfR/qtkHYP7QxvmfqV/w2f8AsZ/9DQP/AACvv/jNH/DZ/wCxn/0NA/8AAK+/+M1+Wp/4NuP2Wjn/AIqvxZz/ANPtp/8AK+g/8G3H7LRz/wAVX4s5/wCn20/+V9H+q2Qdg/tDG+Z+pX/DZ/7Gf/Q0D/wCvv8A4zR/w2f+xn/0NA/8Ar7/AOM1+Wp/4NuP2Wjn/iq/FnP/AE+2n/yvoP8AwbcfstHP/FV+LOf+n20/+V9H+q2Qdg/tDG+Z+pX/AA2f+xn/ANDQP/AK+/8AjNH/AA2f+xn/ANDQP/AK+/8AjNflqf8Ag24/ZaOf+Kr8Wc/9Ptp/8r6D/wAG3H7LRz/xVfizn/p9tP8A5X0f6rZB2D+0Mb5n6lf8Nn/sZ/8AQ0D/AMAr7/4zR/w2f+xn/wBDQP8AwCvv/jNflr/xDcfstZJ/4SvxZz/0+2n/AMr6P+Ibj9lr/oa/FnTH/H7af/K+j/VbIOwf2hjfM/Ur/hs/9jP/AKGgf+AV9/8AGaP+Gz/2M/8AoaB/4BX3/wAZr8tf+Ibj9lr/AKGvxZ0x/wAftp/8r6P+Ibj9lr/oa/FnTH/H7af/ACvo/wBVsg7B/aGN8z9Sv+Gz/wBjP/oaB/4BX3/xmj/hs/8AYz/6Ggf+AV9/8Zr8tf8AiG4/Za/6GvxZ0x/x+2n/AMr6P+Ibj9lr/oa/FnTH/H7af/K+j/VbIOwf2hjfM/Ur/hs/9jP/AKGgf+AV9/8AGaP+Gz/2M/8AoaB/4BX3/wAZr8tf+Ibj9lr/AKGvxZ0x/wAftp/8r6P+Ibj9lr/oa/FnTH/H7af/ACvo/wBVsg7B/aGN8z9Sv+Gz/wBjP/oaB/4BX3/xmj/hs/8AYz/6Ggf+AV9/8Zr8tf8AiG4/Za/6GvxZ0x/x+2n/AMr6P+Ibj9lr/oa/FnTH/H7af/K+j/VbIOwf2hjfM/Ur/hs/9jP/AKGgf+AV9/8AGaP+Gz/2M/8AoaB/4BX3/wAZr8tf+Ibj9lr/AKGvxZ0x/wAftp/8r6P+Ibj9lr/oa/FnTH/H7af/ACvo/wBVsg7B/aGN8z9Sv+Gz/wBjP/oaB/4BX3/xmj/hs/8AYz/6Ggf+AV9/8Zr8tf8AiG4/Za/6GvxZ0x/x+2n/AMr6P+Ibj9lr/oa/FnTH/H7af/K+j/VbIOwf2hjfM/Ur/hs/9jP/AKGgf+AV9/8AGaP+Gz/2M/8AoaB/4BX3/wAZr8tf+Ibj9lr/AKGvxZ0x/wAftp/8r6P+Ibj9lr/oa/FnTH/H7af/ACvo/wBVsg7B/aGN8z9Sv+Gz/wBjP/oaB/4BX3/xmj/hs/8AYz/6Ggf+AV9/8Zr8tv8AiG4/Za4/4qvxZx/0+2n/AMr6Qf8ABtx+y0Mf8VX4s4/6fbT/AOV9H+q2Qdg/tDG+Z+pX/DZ/7Gf/AENA/wDAK+/+M0f8Nn/sZ/8AQ0D/AMAr7/4zX5aj/g24/ZaGP+Kr8Wcf9Ptp/wDK+gf8G3H7LQx/xVfizj/p9tP/AJX0f6rZB2D+0Mb5n6lf8Nn/ALGf/Q0D/wAAr7/4zR/w2f8AsZ/9DQP/AACvv/jNflqP+Dbj9loY/wCKr8Wcf9Ptp/8AK+gf8G3H7LQx/wAVX4s4/wCn20/+V9H+q2Qdg/tDG+Z+pX/DZ/7Gf/Q0D/wCvv8A4zR/w2f+xn/0NA/8Ar7/AOM1+Wo/4NuP2Whj/iq/FnH/AE+2n/yvoH/Btx+y0Mf8VX4s4/6fbT/5X0f6rZB2D+0Mb5n6lf8ADZ/7Gf8A0NA/8Ar7/wCM0f8ADZ/7Gf8A0NA/8Ar7/wCM1+Wo/wCDbj9loY/4qvxZx/0+2n/yvoH/AAbcfstDH/FV+LOP+n20/wDlfR/qtkHYP7QxvmfqV/w2f+xn/wBDQP8AwCvv/jNH/DZ/7Gf/AENA/wDAK+/+M1+Wo/4NuP2Whj/iq/FnH/T7af8AyvoH/Btx+y0Mf8VX4s4/6fbT/wCV9H+q2Qdg/tDG+Z+pX/DZ/wCxn/0NA/8AAK+/+M0f8Nn/ALGf/Q0D/wAAr7/4zX5aj/g24/ZaGP8Aiq/FnH/T7af/ACvoH/Btx+y0Mf8AFV+LOP8Ap9tP/lfR/qtkHYP7QxvmfqV/w2f+xn/0NA/8Ar7/AOM0f8Nn/sZ/9DQP/AK+/wDjNflqP+Dbj9loY/4qvxZx/wBPtp/8r6B/wbcfstDH/FV+LOP+n20/+V9H+q2Qdg/tDG+Z+pX/AA2f+xn/ANDQP/AK+/8AjNH/AA2f+xn/ANDQP/AK+/8AjNflqP8Ag23/AGWgMf8ACV+LP/A20/8AlfR/xDcfstf9DX4s65/4/bT/AOV9H+q2Qdg/tDG+Z+pX/DZ/7Gf/AENA/wDAK+/+M0f8Nn/sZ/8AQ0D/AMAr7/4zX5a/8Q3H7LX/AENfizrn/j9tP/lfR/xDcfstf9DX4s65/wCP20/+V9H+q2Qdg/tDG+Z+pX/DZ/7Gf/Q0D/wCvv8A4zR/w2f+xn/0NA/8Ar7/AOM1+Wv/ABDcfstf9DX4s65/4/bT/wCV9H/ENx+y1/0Nfizrn/j9tP8A5X0f6rZB2D+0Mb5n6lf8Nn/sZ/8AQ0D/AMAr7/4zR/w2f+xn/wBDQP8AwCvv/jNflr/xDcfstf8AQ1+LOuf+P20/+V9H/ENx+y1/0Nfizrn/AI/bT/5X0f6rZB2D+0Mb5n6lf8Nn/sZ/9DQP/AK+/wDjNH/DZ/7Gf/Q0D/wCvv8A4zX5a/8AENx+y1/0Nfizrn/j9tP/AJX0f8Q3H7LX/Q1+LOuf+P20/wDlfR/qtkHYP7QxvmfqV/w2f+xn/wBDQP8AwCvv/jNH/DZ/7Gf/AENA/wDAK+/+M1+Wv/ENx+y1/wBDX4s65/4/bT/5X0f8Q3H7LX/Q1+LOuf8Aj9tP/lfR/qtkHYP7QxvmfqV/w2f+xn/0NA/8Ar7/AOM0f8Nn/sZ/9DQP/AK+/wDjNflr/wAQ3H7LX/Q1+LOuf+P20/8AlfR/xDcfstf9DX4s65/4/bT/AOV9H+q2Qdg/tDG+Z+psf7Zv7GjyKi+KBliB/wAeV93/AO2NaFz+2J+x/Yw+Y+vhsttH+i3oyT0H+pr8oH/4Ntv2WXjZG8V+LMMCD/ptp3/7h9fF37eX/BEr9n39l79my7+Ing7xH4gu57G5DBL27tnjOyGV+iWcZ/5Zj+Id6S4ayiEl7CKfqr/mKpmmMjBttn9UvgLxH8Ofiv4Th8Z+Bz9qsJ2dUfEqZKMVPEgVuoPavwX+LcEdn8QLzSbcbYEIITr1UE89etfQf/BA0QR/sJ6O9oS0fn32C3P/AC+XHpXgHxmOPinfse5T/wBAFfi/ipluFw0YzpRSeuyP0Xw8xdevUlzvoel/sdSyQftK+GrSE7YzLNkdf+WEhr+gmv58f2QOf2nvDB/6azf+k8lf0HV6PhG75TVf/Tx/+kwHx2rY6C/uL82f/9H+mD/go5/yWHRf+wOn/o+Wvz2s/wDWD/r5FfoT/wAFHP8AksOi/wDYHT/0fLX57Wf+sH/XyK/mTi7XP8Qn/N+iP2bhv3cqjJb2P248X26H9gLxDKep8B36/gbB6+Df+CCsSH9mLxfE33f+EnmX8PsltX3z4u/5R++IP+xEvv8A0gevgr/ggr/ybL4v/wCxpm/9JLWv6S4ZpRWSc6WqsfiWZVJPM1FvT3vzP3KiVIkEcAwAMU8O27ac4pI+lK7qnU1UJ3iqk5HRPyFdmHQ8U1Xc96aJo36VHJP5QyopyxlJR5r6FcysWdzZxUMlxIh2qM/rVeO8MmOBVkkOOK51i6WIXLTnZindK6Q6OSSTnpRLI6DIpodY+Cf1qvLOGGFrLFYuFGk4qpeQ6d5boktrpps7+MVb3r6msmM7AT61EsszPWOFzKEKEPau8mZyUuZpG1lqCXHSmggcE1GZ1D7WPFetVxEKSTm7XLjzPoShmI5pSWA61E8mwblGTSB2PzMKzq4iKfKn8xtPccWn6AU1ZZicEGkWcseuPrUxO0ZqfZym041NCVPm0sLlqjaSQHCgmk8zj3qQHvVVU5y9nGVmikrbjBJJjpURnlBwRipwwPANBQP1rKeHqNXhVY9F0ITO4pwmY08IH7YprRkdOlcVWWJT5k9BrlY8SZ70hkPY1DQOelR9cqNctx8qJ0Zmp4JNQxkq2TVnqNy16OFrOULPciSsyPJqB5pFPy1ayvcUm2MjNXXjUlFeydmKOj1K6SyMuW4p4kJPUfnSyKgXFMjjTqM5ropJqCUnqKUpX0Whzvi/xI3hTRJNa+zT3axZZo7dPMkKgEnAyPT161+ZnxE/4K+fs0/CvxHN4a8f2GuaRNAzI0l5Fa28WVYqcGS6U4yD26A1+p2oQm5tWgVUfcCGWTkFT1GK/Pb9p/8A4J1/s6/tQ6PcQ+M9ItrO+kVgs9vb20chch8EtJbyN1cnPXNebjKtaMr0padrfkz6rhh5LKuo5ypcneL1XyPnC8/4LrfsTWm1lvrueNmC+ZFJYMgJ9T9t/H6Vdj/4Lf8A7GEkf2l9a2xnkL9osfMH1H2yv54P22f+CKvxX+AS3Xir4TWk2u6GkrH7PFHNcyhB5jZCQ2aJkIgGc9T6V+Kev+F7rw1qDaN4j0q6027QlWiuoDCQQcEbWAPUEdO1cdStWqU+aNW3lZXP7E4O8BeA+I8GsVl+Mk76W5ldP0P7xH/4Lm/sQpjGrzMWYKAJ7DPPt9trSP8AwW3/AGM4oBdzarIIz0Hn2O7/ANLMV/AXNp+lLjES+YDnIUcVp2ekatrEyadoFjc6pO5AWG3iaY5PA+VQT1IH41xRniVJXru3ofU1vom8N0k5zxMlFb6n97Df8Fxf2Lwvmi8vBHjO8yWO38/ttbXhT/gtL+yd421aLRfBtvrOtXEriMCwSzuQGYgYOy8Yjkjt3r+Vf9jf/gk5+0j+0z4ks5PFWmXWgeHWiWdnuobq1Y/NGdmXtZIzlHPHfHoK/rG/ZJ/4JZfs3/syaVBPbaRa6jrCKrSST29rMfMATJBFtG/3kzk812yxNe3LCpd97aH8/eI3BXAXDqlhcPiZ1cQukWml6n6JfDj4n2XxJ02HVbDTdQsIpoo5gL2ERHEgJA4ZhkY5r0tiw71z+i6f/ZsSW9tBDbwKoUJGuzCjoABwMV0DLvFerg5T9mvaO79LH821nBTfsthgclsbv1pxZgOKhWAI27Jq0VHUGuxtGUJyfxKw3LYzmky1OyM7e1MqYyuVzFKW8dJNij/P51djcsAW71lzoRNmtCI5WvBy2tWliasKktFf8zSSVk0PWRiSOaHkKqTmgkLzUNwCUwK9OrKpSw85Xu9SUtRyysy7s09XYmqduGLYqy7CNeKzwWN5sOqlXRrcprohzzbOOc0qSM65qkTuPHNXI12pg0sJi5Vqjf2QasOV2NJJKY8ClAA6VDMrMOBXRjZTVJunuTa7JIZvNz1zUhZhnmqEZMR4q2rBxXNgcU6keWb94GrbERuJBUnmt3NRvGKeI+dzVzU6mJlUcLlOxOCT3pefWoS2BxVYzNuIrrr4xYe0JasSjcv5HbNNyajRzjmnvIqD3NdEaqVP2kzNxd7Ik+maXH1pkcoaneYc+3+fatKdRTipR2E3bQPzow3rQzAYNSVMp62QyPDetGG9akopc7C5CwIHzdK/Kj/gsZbQf8MTa58oI8x3wR3+y3FfqzL9yvyt/wCCxn/Jkut/WT/0luK2wU3LEKD2M6yThqeIf8EDo4X/AOCe3h/aoQm41HlRg/8AH7PXzd8Zv+SoX31X/wBAFfSX/BAv/lHt4d/6+dR/9Lp6+a/jNn/haF99U/8AQRX5f4y04+0pxS0/4Y+98OtKlS3b9WemfsgAj9p3wwf+ms3/AKIkr+gyv59P2Qf+TnPC/wD11m/9ESV/QXR4UxUcrrJf8/H/AOkwMeOW3joX/lX5s//S/pg/4KOf8lh0X/sDp/6Plr89rP8A1g/6+RX6E/8ABRz/AJLDov8A2B0/9Hy1+e1n/rB/18iv5l4t/wCSgxH+L9Efs/Dv/Ipj/hP2/wDF3/KP3xB/2Il9/wCkD18Ff8EFf+TZfF//AGNM3/pJa196+Lv+UfviD/sRL7/0gevgr/ggr/ybL4v/AOxpm/8ASS1r+leGv+RC/kfh2Y/8jWP/AG9+Z+5MZYtgUTQl+QaSKUBip71PXDhKNOrTabud0r7nj/xZ+Lfgb4HeE5fGvxAv4bCwhJ3yTSxxAAKznmR0Xoh718Ov/wAFbv2HplJj8ZaeRnBP9oWGB/5NVyn/AAWbIP7FmubURmJlAZhnH+iXXQ1/nq+Fra/1HWbTw2MA6jfRQBucfvWC9efX0NfofDfClHGYSc72UT53F4xqUlfVH+itZ/8ABWn9hpixj8a6ZtQkHdqOn9R/29V1Hhv/AIKmfsa+M/FumeCPCvi3TrrUNUuYbaJEv7F8tM4jUYS5LZ3EdAa/nd+Ff/BvdrfxZ+F3h/4hWfiFbM6vp9vdtH9rMfMyBzwLBvX1P1r6g+BH/Bvre/Bf4veGfije+Ixc/wBi6jZ3jR/bC+4W8ySkYNimc7MfeH1FeJjsowFKHJTmubrZBgMdXb5pr3T+oOOaIxtcXLoYWOUkQ/LsPTJPGfpxXyt8Vf25v2Tvg0GHi/xvoolTrCmpWfmD7vVXmQ/xCvxu/wCCtn/BTfxz+zB4vj+DnwpubUWx0mN7mdXk8+G4S6kjZd0NzGFO2MAgrnk+1fz7fsi/sV/tdf8ABRnxlceJbi+nl0h2G+61GS8aEDEi8OYbhPvRY6+g69N8DwnhHS+sVWkjtrZjJq1Lc/tW8I/8FOf2J/GMixWHjzQ4GPRZ9UsFY9egFy3pX274Z8S+E/GGnprPhLULfUbZ84ltpUmQ4JHVCR1B71/G98TP+DeD4s+A/Dlxr3gzxO91qkQUx29jezPgllB+RLBW+6SeDXq//BHX4if8FMvA/wAZG+C3xl8PaoPDkDAG61O01XaAyXEvDzsI/vbR930HXFY43K8tqQUsO1KUQw2NkvcqfEf11chgp79PwrB1fVLHSbee81KeOzhtwpee4YRwjcQB8xIHU4+tT3cs/wA63bGKNgMSKcbPXk9M9K/jx/4LIf8ABYLxlqetH4I/svX5fToMrfXljK5lfctvKuJLS7Kna4cHcnAyBzk1wYXJv7UqKk1omb4jHunFySP6K/HH/BT39ifwFeppuo+OtGunYkbrXU7B1BABOSblfXFev/B/9sz9m747n7P8M/FmlahcHpDHf2ssn8X8MUznopP4V/FP+yP/AMEfv2oP2u7K58Q61DL4esYAjxTa8t3aPOZC4by2a0lV9pTnB4BHrXqfx6/4Jc/t/wD/AAT5mtPiZ+zprk+qffLwabc6jO3AVBlba2g7zMRlux98/QVcnwVLmoqfvI8vC5pWrrmUfd7n9xplZJSuGYDHIHBz6VynxA+K3w/+FOhy+J/iXrVhomnxAEzX1zHbJ1C/eldV6sB1718ZfsLfEX49+IPgPB4q/aYt10y+QNu85LiF+JpFG77UxboFxz3+lfzK/t2ftZfFb/gpD+1PD+yV8HLi/i0ANslksHmCHdbxz/MYZbiP78DYzH1z3yR87k+VR5pNPR9+ljqljKrX7uOp/TPo3/BVH9iTxDrsWgaP440ieSclUdNSsGTKgk5K3J9D2r760zW7DxFaR6l4du7e8tZBkSQyCQH6FSR1r+Xv4rf8G/Pw88J/s9xyfCLWtWt/GNgrOJ/tESF2mlTq0NiJjhCw7Y+ma5j/AIIqf8FGvEmm+M7j9kn9o3VZzrUDhLebUZ23Hd9pnPzXVwH4QKOIvTtg16FXK4VKM6lOX+ZphMa5L96rS7H9VP2iZDlyAe+OlfIXxj/b9/Z2+AnjnUPAnxN1y1sLqySFijXNtE371FcZEs6How/hr6+VIEYBQ0inkE8jn3r+Ef8A4L56hJH+3Nr8Nv8AaETybDd5fCn/AEK39K8fhLI61evKlVl1/L/hx5hmEaUbn9b/AMF/+Cjv7Mfx78b6f8P/AIb65Be6hfs6qi3NpIfkRn6RXEjdEP8ACa+77CWa4tw842k54H1r/PY/4IfXHm/8FCvAdmss6RyXF3uR2wGxZXR6Z5xX+hOvLiGPIC9cV6+bYGOGrSop3sbZfXVfDQqrr/mVCbgSlX27PbOasxEEgVWlLiUg9KsW3LCvz+jK+I5fM9HpcszK3l/JjcK4fUviN4U0PxNa+DdWvIYdSu4hNHC0iKWUtsGFZgxy3HANdvK20596/mm/b1/ahvvB3/BVX4YeENO1FobCeDT7C4jWYqnmyaqyHIEgXO31Gcdq+toYRtuUNzy8fi1Qpqctj+lpsnBXHNNdtpCfxHn8Ky4r0XFtDPasHRypDKcgqfcVLqEjRl54+qxNj6is4T/eTg+h2UpKrGLXUPt8MkrKGUqjbDg8hqvq4LBVIxX5d/Cn9r/TLr9rzXvgD4ruY4HUXdxbiZwu5kuEhULvl5yScAJ9PSv01iciXMeCpGc9q8Wjm3tZO2ydj2c2yupgakadVbxTXozQlhEhBDEY9OtRyQRkDegYjuwzTbWV33q2OGOKt4J4zXrUuWpHmtozx7W1sYWqaHpWu2jafrNvHcQOpUxyoHQ5BHRgR0JHSv5wP+C237EnwM0T4GTfGHwjo0Gm64b5Yt9pb28KtuiuZTkpCHJLAfxdvWv6Wiuflr8bf+C3c8Nr+yCzOQf+JnFn/wAB7mvOzOkowVSK2P0zwoznGYPibBLDVHFOauk9GvNH4v8AwH/4I/8AwO8f/sYxfHDxRqGupq8/hs6ufKltxGJ/sgm2jfaM+zd235x3zzXa/wDBFn9i74PeP9e8TeIfGdq19ceH9WuLO3EyQSqyQC3dS++FiWyTnBA9hX7CfssrbXv/AAS6sHRRz4FzkAf9A4V8kf8ABDuNIpfiKBgY1+//APRdtXh06jqVOR7H7PnHiTnuKyvPqNXEy9yaUdfhV2rL1P3/APDvgrw54Z0mHSvDdpFYQwhQFtkWIYUAYIUAdAK6iOKNG3CNdx6tjn86W0ZTEKsn6V9Zh6MFCOmp/J8sTOpedR3bIRGVO7J/HpUjSRxAGQ4NQvJIGwB0qjOyuwExxioxMnSjddTOnKMpWsWLi/gt4TNM4VV7sQBzVnzVIXb/ABdK/Lz/AIKJ/tj+Hv2d/hFqtnpt5GNelSP7NEJF8wkSQltqiaN/uMScdvav0M8BazJrfhfT9SuziWaINg9eR7kmsKWYKU3T7W+++p7WLyatRwdLF1FaM27eaSWv4ncFkQF5CAvr0r84/HP/AAVT/Y1+H/jKfwL4k8VWdvf2+3eGvrFQNyhhw1yrdD6V+htnI88OLleDngj396/zR/8AgoRdaVY/tda9thTLGD7qjA/cJX0vD2WSx9RQj11+8+WzfE/VsO6nVH+gt8CP2yf2f/2lXlt/hV4j0+/uI8bYkvLaWRs7uiwyyE/cJ+lfTsF3HKu6Js49DX+c1/wTA/al179lr9pvw/4h1O5RfD5llN2jOwXaIJwvBkij++4+8f1r/RD8HeItA8YaNa+I/DE8NzaXS7vMhZXQ444KEjg8HmuXibhrEYLERcVZ/mRlmYqrTTZ2gwwGc814x8cPj38P/wBnzwdN42+IlyLWygAJZnjTqyr1ldB1cd69mST984K4RcbeOuetfh1/wXnvxD+xlqE0TvExQ/Mp2ni4tanB0nWqRovd6HoVa0YK7Puz9nz/AIKA/s4/tJ+KX8JfC/VUurxduFE9q+Swc8CKeQ9EPb/632dO5zzX8O3/AAbufZ2/aUvVnnlmli8kqXYMBuju/wAa/uNuIwTn1rLivJ5YRqkuyf3nPgcWqt2ujEtlXO9ulZ1v4j0e616fw3b3MT3loEaeIOpdBIMrlc5GR0yBntWuqrHD8/A7mv5+P2b/ANtH/hM/+CrXj3wNeaoo0vWIdIh06Bp/kL29lK02xTMVzlcnYCfXFc+WYJwoWS13Z6EIupzPsfsz8dv2jvhj+zl4Zbxd8T7n7HYoMtIXijUcqvWWSNerjvXyDo3/AAVv/Yn1+5az07xTabxjG6+sMHP0uj6Vof8ABVP4PaZ8Yv2N/FVg1tJcXsEMBt1gQPIS1zBuwNjt0Xt2r/OZ1K6ufDolm0Y3aXNu7BlXIPXA4XB6Zr6XIuH6+OVWaekdjwMVnVOhjIYaS3V/xP8AVk0vXdK8R2a6nodzDcW5wC0bhxkjOMqSOhrdtzxX4+/8Eg/2rdI/aL+Eer2od3u7G/KYcqfljggz/wAtZD1b2r9g4BwTXyeKwcsPmKj957kKinG8SSaRIgZJCAqrkk8AYr4euv8Agof+zNZ/F5/ghNrKNr6X/wDZ3krcWufP8wRY2/aPM++cY2Z9s8V9TfFPW18NfDbxB4ikbYthpl3cFs4wIombOcjGMeo+tfwz/scaLdftGf8ABWbW/ESXFxdpa+I59YUK5kTyodQhfPSTj5vXHvXtYXL5VcPVrw+yzhrYtUq0YS2Z/eRvnfEo2CF14z97centiuevPEej6ZqMei6rd28V5cAGGIyKrMCdoO0kE88cCtrLsrRvwqPlAPQdK/nl/bU/a/vfCH/BTH4d/Dix1TyNPkisra5jE5RfMOpNG24CULnb6rmvJWXvFtzSu1/TNKmMjGSR/RGwuFRANu/jd1x74pboOGG3pt/Wo7W4h1CKLUrSQPDKgZWByCDyCCODUs5YuGUZXGKnMKN6DjE61UWhxvjbx54e+GfhC78Z+L7hLWxsY3mnld1RVSNC7El2VQAqk5JAr4FX/grf+xA159jXxnpxY8j/AImFhj0/5+q67/gpnLLF+xp42eByp/sm/wChxkfY5+K/zfbCCe4kF6GCsvqce9fUcK8PSxeHa/lR85mebKlXUD/Uu+E3xo8B/HLwtF4u+G2pW2oWjsvzxzRyjDKG6xO4zhh3r2McDFfjH/wQ/SQfsb2M91GhZnt8OB8xBtYO5r9na8TF4d0qsove57dKqpwUohRRRXKaEcv3K/K3/gsZ/wAmS639ZP8A0luK/VKX7lflb/wWM/5Ml1v6yf8ApLcVvgP96RNX4DxH/ggX/wAo9vDv/XzqP/pdPXzZ8Zv+Sn331T/0EV9J/wDBAv8A5R7eHf8Ar51H/wBLp6+a/jNn/haF99U/9BFfmnjL/GpfP8kfe+Hf8Sr6L82em/sg/wDJznhj/rrN/wCiJK/oLr+fT9kH/k5zwv8A9dZv/RElf0F0vCv/AJFlb/r4/wD0mJz8cf79D/CvzZ//0/6YP+Cjn/JYdF/7A6f+j5a/Paz/ANYP+vkV+hP/AAUc/wCSw6L/ANgdP/R8tfntZ/6wf9fIr+ZeLf8AkoMR/i/RH7Pw7/yKY/4T9v8Axd/yj98Qf9iJff8ApA9fBX/BBX/k2Xxf/wBjTN/6SWtfevi7/lH74g/7ES+/9IHr4K/4IK/8my+L/wDsaZv/AEkta/pXhr/kQv5H4dmP/I1j/wBvfmfuNDAHctnvU8w2jgmi27/WluOlcFKlGOHlKK1Z3X1sfkn/AMFo9kH7EOrzscB5XX87S5r/AD/PAUqyeNtD3qqiPVLUqyjDfLIuOa/v2/4Lb7z+wxfhO94Qfp9kuq/gD8ENDaePtCkuDi3i1C1kkY8ABZFzknjp61+u8ENvB1V5fofCcSWhUvHqf6aX7Gs0sv7K3w+kGcvoGnZLe8CV9Lv9maM5kGVOPmI+9X5+fsi/tL/s66d+zd4F03VfG+hadLb6HYxGC61K1hkysKD7pkz14r6SsP2k/wBnHVNSj0fSvGmgXl3czLHHDFqVrJIzudowokyck4GBnNfnePwteWKk+T3Uz6LLq9JYSClLVo/h5/4Ls+G9Q0v9tG70fU7hxDrFtPdr5bnhGvLjA5AA+76EV/RR/wAEBx4ZX9jezmsvLTVC9wLxE2DCi8ufL4HzDK/3vwr4I/4OAf2SfEfjrx3a/tA+E7MvZaP4egimlhjYqZJL2XkskTDJEo6uD/XG/wCDdP8AaE03TtU8TfBzxXeCKe9W1XToZJAu5la7ll2q8gJwME7FPv619lUcMRlVqMLONrnn+2lQxMVy6M/rXSzhllZpMgn+IdT+lVNP8NeH9NvHvbOxhhnbG50iVWOOBkgZ71piYfaRGBhR3/CnCRjcgA8HrX5zDEUYvloaNtp+p9RKlGUlJrU+Cv8Agov+0jJ+zL+y/wCIPiJZ3MMOrRxxGyimfash+0Qo/AkjdsK+flbjvxX8LX7DHwGf9rL9s3TPBPiRZ30m+mlN08Ay5H2aaRclklT7yDG4Hj3r+gv/AIOX/iPeaf8ADjwL4H0e5aMX8upLcIjkBgn2N1yFYZx2yD7V8Zf8G6fgmLxR+0B4i8V3EIkOiJZOrlclfPju0PO046eor9UyLC/V8hrYqS99W1Pka9SpPHypN+5tY/s28JeFNH8K6DBoWiwpZwQIFWOBVjUeuAoAGep461r6houj6xbGx1y1ivIv7s6LIOeejA+gqYbvto2HKe3TpVosJGiZejZz+Ffl88Y5TnKD1ul+n6n01ChGFNQtofkd/wAFdP2nJ/2V/wBlHUZ/CjW1pqmoqFso3PlqTFcW5fASSNvuuc7c+/HX8Lf+Ddf4UWPxE+PXjj4o+Io2uLrRV06W3mcB8tcLeRvuZlYkADAww9816D/wcm/FA6/4q8G/CbT7koNLkvWuo1fAIuIrORNwDH0yMqPbNfW//BuP8Lp/C/wU174izxbV18QoshXG77JcXaHkqM4z/eP4V9xSpU6eVusl7z0/E+Wy3FVnmlSnze7tb5H9JF1aW042Sjdu42nBHHtX+fR/wU08L6p+yN/wUM1Dxv4Ig/sqS0FvJA8SmAOZLKNWyYxFnHmHoR1/P/QbeMiRXY9K/jw/4OPfhXaWnjbQ/iCYwkmsGZN+AC32eK0Trt5xn+8fwryuG5urjPYz+G7VvK1z2czgoR9pHc/qe/Zy+I1n8X/hBpHjizmWaO6jZd6MGBMbFDyGfuPWv4lP+C+TS237dPiGMMSDBYdT/wBOVvX78f8ABAf48aj8TP2XT4A1uYy3nhsu8m9izYurm5Zc7nY9FHZfxr8Bf+C/WT+3b4iH/TCw/wDSK2r6nhmlGnnzpNe7roeJmzU8DGT3PK/+CIuW/wCCh3w+mJOY7i8I982V11r/AENA6xsHHVv6V/nmf8ERAR/wUH8BA/8APxd/+kV1X+hbL0j/ABryPEBRoYuo6StovzO7hlv6soPZEYTMxXJP1qdSIzgeuKYn/HwalKZlxX5lSoL4473PqFLe5FqN7bWdhNfXreXFDG0jtkDCqCScngYHfNf57H/BRn47X+v/APBSC98d6RcRyp4O8RsLVw5ZStlfvKm8q549dpXjpjrX92f7U/jGDwF+zv428TSyiFrTQdSkjYtt+dLaRhg5Xnjsc1/mS/EvxXqXi/4heJ/GN47SS6rqNzdI5JJPmsWByST1Pqa/VeEsu9vOTqLSx8tnmKppQhLvt5H+l3+yB8UY/jD+zL4L+ILyRS3WoaPZT3XlHcizSwo7AfM5A56Fia+lJwJIjkf8syfavwn/AOCAPxmT4j/sfv4V1C6Et5oN3HYhGfcwjgtLYdC7EDLegHsK/duf5VYf9MjXymaYV4fFVOnQ9/BVISpRdLY/h0/4KLfHjWP2e/8AgpvD470CaSBrWZmlCMyxtGuou7btrpkHZzk4xX9YP7G/7Tnhv9pv4N6T400O5jluXtoftKRurFZGiR2ACySEffHU59a/i1/4LVKkn7cupLIDtMdyDjr/AMfk9fVf/BEv9uCP4S/ENvg94muli0u6JEbXL7VXc1tCMF5kUYAPRf8ACvgMPDkj7WC3bv8Aef3Nx14YUMy4CwObYKl/tMKcW7dVY/tftnG7bGcKeTu67q0MlOQwP415bp3xX+F19Gs1vr+nMJRvwLqEnn6NWm/xH+HqruXXdPx73Uf/AMVX19OcYUdWfxNPA46L5Z0X9zO4lunSNnwOM9Otfin/AMFxJFn/AGNlkPmbpNZgUgf3Tb3VfrPL8UPh2B8+vacq9/8ASohx/wB9V+OH/Ba/x/4G1v8AZFj0/QdYsLmc6vAVWO4jcn/R7kdFYnqRXzeKxFSpPyP0Lwxy/Ex4ny6TpS0qLoz2X9liNLX/AIJZactuQuPAwBMnHH9ne1fHv/BDwXct18RpI5YJIz4gvlO1iTkpbV9W/sr30h/4JbWkVzGZGPgry8IM8nThzz2r4p/4IoeJ/C3g27+IMGv31vYtN4hvWCzypEeUtx0Yj0Nc0W1O63a/U++r4epPL+JFGN26va/2mf0qWmYYwo5PvV8MxIzivMl+KPw9JAGuafyMj/SYun/fVXo/if8AD/p/bmn/APgTH/8AFV7GX4qWkJPQ/BnluISsqT+5neybxymK878beKdG8HeH7rxf4iuUt7G2ClnZ1QLlgvJYhRyR3q1N8Tfh+ISX1ywVf7wuYx+u+v5+v+C0X7delfDz4XS/Cr4eaqlzJqAImeynDuNr28o5inX1PVT3rszKq1CLir9vXofScF8GY3Os3o4GnCSUmr6PSN9Wfg7+3b+1v4k/ab/a6jjju4pPD1i+LdYJGKylrZA4cebJG2GTIxjB681/e/8ADm1ifwppc4+Xy7dMKOF5GOlf5g3gQxN8RdOxK8xZ5GLu25juRjyfav8AUA+Gh/4ovTv+vdP5Vw5dCLxDjJfZT+d9z+hfpOZBhMmw+T4PBQ5VGm0/lyrU7xdhyAMV/mf/APBQyyjj/au8UyWaLMIfsxBkG4/NCnpX+l/naSa/zR/+Cgc/2f8Aau8XIoLBhaf+iUr9T8Pqkv7RfbX8Nj+IeK5S+pJLuj4itXDSzXH2maKCIKQ8T7W54OD061/bt/wQr/bRT4tfB6L4N+PtTiOu6cX8lJJ/38glmuHAAkmZ2wijoowPUV/Jr8Sf2VPGngv9n3wZ8aLazlk0fW5tQS6lSNyii2kWNcsIgg+Y4+Zj+Bq7+xZ+09rv7MP7Uui/E/S7iSHS4nbzowzLGwFvKgyBJGp+Z+7dfev0DP8ACxzDA1p2vODevofM5NiqkJU4X0f5f8Hc/wBMx0n3RIrj5CfMGecHp/k1+IP/AAXyVR+xFq0u0ZWPjj/p5ta/Yb4WfEPw18VvBdp438LXMd1b3ifejdHGVJU8ozDqD3Nfj3/wXxGP2HNWDf8APP8A9urWvxPKKNWOZ04X3a/P/I+9zGn7TCTcN0j8Rv8Ag3ct4of2m9ahRFPy2hBI5GYrvpX9vLySuoZgMV/Ed/wbv/8AJ0Wtf7ln/wCiruv7b5HZLYsRk+1et4hqo8bLlekY6/p+plkEI/V4vq9/U5H4g+LrDwb4OvPEeqyCG3tlUu7EKACwUckgDk+tf5zXwF/aE1rwb+3b4d+OYvmE1hfXrENIwhlBt5YR5n7wFgAePnGD+Vf27f8ABWL4oW3wu/Yb8Z6hFci21CSC3+zgOEckXdvux8yt91u1f50bS3Njc/bLaQrdWrtISCQR5p9uehr6DgfK/rGW1Z1VeSW/yPNzXF4inibUZNRP9U270jSviD4MFtqQWe1voYywGGU4w3cMOo96/wAyn9o74Yaz8IPjr4k8G6tC8RjeNo0kVlJ8xQ/QqnZuwr/Qp/4J3fHPT/jz+zBoXiuC5FxNiaOTDhz+7meMfxuf4e5r+VH/AIODPgifhr+0fp/xK0a0EFlrmUyibVJtra2U/dRR1b+8fwq+CsXVw+YvDN+6+hy51haU/Z4y3vJWv+P6Hdf8G3fxL1Tw18e9c+F13e79LvrC8vh9okJf7TvtIlVcsFxt7bS2e9f2s71AHl8nOD7V/ml/8E0PjfrPwJ/a08IXvmvbx6rqtjYzMWZB5NxdQBsnegxhec5Hsa/0m9N1O11ayttX0xxJBdxrKjKQQwfkEEZB/OvK48wccLj/AGkY6M9nI8T7am7PY+Mv+Ck3xbX4PfsfeM9ZjeFJNS0y+01DKcYa4tJ8FfnT5srxyfoa/mw/4N1PAE3iH9o/xF8WtVg83/iX31uZWXcpkZ7STO4qee/3s1+k3/Bw38TP+EZ/ZTtfCNtP5dze38D7Q2CytDdpjAYEgn2NWf8Ag34+Cv8Awgf7Lf8AwsLVYBHda3Mk8bsuGMc9tbt1KKcZHZiPrWWHboZTVle3M0c2I/e42K6I/fjUWW3tmuchFQEsTwAo5Jr/ADsf+Cj3xi1++/4KTeIPGsF62/wl4knitfLkbZ5NrfPKu7D+vXBUY9K/vx/aT8bWvw6+A3jDxrcziA2Gi380ZLBf3kdvI64yV5+XjBBr/Mj+NvjiX4k/F7xX4+vJC8mtXl3Ork5JedywOSzHqfU/jXo8G5csVUnppZnn8RurGrT9k7H+kx+xP8Th8Wf2VvAXjeS4jnl1DQ7CW4ZH3YmkhRiM7nPfoSTX1S0mxvJ7Yr8Hv+CBPxes/HH7IcPgHVLwSahoM8NskRkBcR29rbj7pdmABP8AdA+lfu5L/wAfH4f1r4bPKVbC1KsZae8j6TAT56cG9XY+C/8AgpxAj/saeMZFJGNNveB0P+iT1/m5y3MnmSJH8gBI+Xiv9JH/AIKZ/wDJl/jH/sG3v/pHPX+bM/8Arpfqa/UPD6o/Y1/RHx3EtOKxUWj/AEDv+CHhab9iXSWk5I+zD/yUgr9kq/G3/ghv/wAmR6T9bb/0kgr9kq/Ps6f+11PVn2WDilRjYKKKK8o6iKbhCa/Kv/gsWxP7EeuH3k/9Jbiv1Um/1Zr8qv8AgsV/yZFrn1k/9Jbit8B/vSJqr92zxb/ggV/yj28PD/p51H/0tnr5t+Mq5+KN8Pdf/QRX0l/wQK/5R7+Hv+vjUf8A0tuK+bfjID/wtS/PbKf+gCvzTxn/AI1L5/kj7Xw9k+efp/mej/shE/8ADTvhgf8ATab/ANESV/QdX8+H7IX/ACc94Y/67Tf+iJK/oPpeFf8AyLK3/Xx/+kxK46/32n/gX/pUj//U/pg/4KOf8lh0X/sDp/6Plr89rP8A1g/6+RX6E/8ABRz/AJLDov8A2B0/9Hy1+e1n/rB/18iv5l4t/wCSgxH+L9Efs/Dv/Ipj/hP2/wDF3/KP3xB/2Il9/wCkD18Ff8EFf+TZfF//AGNM3/pJa196+Lv+UfviD/sRL7/0gevgr/ggr/ybL4v/AOxpm/8ASS1r+leGv+RC/kfh2Y/8jWP/AG9+Z+5tt3+tLcdKS27/AFpbjpXLH/dmdy3PyK/4LaLdN+wrqZtApYXLE7s4wLS69K/z5YJvKAikO0mLzN68MH9j/k1/oT/8Fobaa4/Yi1URvtBkcYzjJ+yXNf569qFWYQzqWZJApwM/KOvWv1jgb/dKvp+h8JxQvfR9CW2iftWS6PpOo6Ja+KL3T2topbV7OO8lj8rqoyo29OcLxivqb9jHQ/2p7n9rHwNqXiDT/FcVoNb00yrcRXaw+X9riLbgwxjGc54xX9mv/BPP4GfBbUP2Ivhfq2q+F9Lvp7zwvpdxLJcWUEshZraMnkpk59z1r7MsPgn8EbDUrfUNH8I6VaXNvtmjlisII3RkOQQVTIIIB4rxMVxNTpOeEcFdvS56+WZbCrSpVH0Rxn7QHwW0v4+fs+ap8MtUt9j6nb28ZcoBKvlSxy8Eo+OV/umv8874H/Fr4ifsY/tW6X4jSE2Gt+G7i4aS1uVlitZVuIJETzE3Qu2Ek3DJHzcjIr/Svv7+8tNOmuLeLfIhGxdpORkdhz+Vfxkf8HAn7EVx4I8bad+0P8KNJ2Q6oZBfG0gIjjEEVrChbyYQFyzN95zk9MdKnhfOKPtauCqfb/4J2ZpF01GUY7H9h3w38beHfib4Ss/Gnhq7hvLO8T5JbeRZFLJ8rjchZeGBHB47812ULb1eWLBZegNfzA/8ECP2421/w1qX7OvxP1ZTc6QImsDcz/M5uZLqaQDzZiTtAH3UGO+etf082zmGclQCr4xivj88ytZfmMabX2nf57fmdmExbr01O1j+NL/g5B1ya7+K/hzwyz5j043DqM8/voLRjnnH6Cvb/wDg2X0Gzjl+JOsHcZpYdL64wNr3g44z096+Uf8Ag4lu5Jf2oRavnbGseP8AgVra5r7K/wCDZwMNL+IpP/PHTP8A0ZeV+rTqKHCTtu3+p4k4p4/5r8j+qODMQzyQPzq0qiDy1HTJ69arSny4WYe1WZvnQN3H9a/EMrjeTi99/wAf+AfSV/dp3R/n4/8ABcX4gal4t/bo8UWEkimHT4tPMQQngyWdvu/iI7dsV/Vb/wAET/DOn6B+wH4US1Ugyy6juZgNxxezkcgD1r+NX/gqDqM+uftteMb2clvMSwHJz921iHv6V/bP/wAEjrRbH9gvwjGoxiTUP1vJq/UM8vh8opPvr91z5HKqX+3zZ+lM2WZW/udB61/L/wD8HKmkw3nwm8A60xZZrabUyAvAOTZjnjJ46civ6e0ff83rX81v/ByHah/gZ4OI52Sah+r2dfLcK1nLNItbP/I9jO3y4Tm7f5nzb/wbWfEC9m8X/Erwzd+WsclvpPlquRzm7Y4Bb+Qr4F/4L8M5/bw8SBRkLBp+Pxsrave/+DdS/ubT9oDxNZwkhZ0sQwGedsd2RXhv/Be3Z/w3r4ijbq0Gn9f+vK3r9AoxdHiL5/ofM4yd8FC/c8r/AOCJWxf+Ch3gKNO091nPvZXVf6E0/Cx596/z1f8Agicrx/8ABSHwVE3AE1xj/wAAbqv9Ce5PzIPrXyniRV/2iq/T8z38gjaFiSEEz/hVpjIsvy4wTzmoLX/j4P0qd5FVucfexXx2WQUqSk+59At2fk1/wWi+KX/CsP2KtbmimWN9VaXTmBbaSlzaXIOPmXnj3Hsa/gZ1bwL4n074cWvxNvLOUaTI8cEc5jfa0roXUFioQkqM8Nmv6lv+DkP45SWtl4d+AtlKS+oC2vjGrdctdwZwH/D7h+vavGPjx+yZY2v/AARP8F69p+lqNRaXStZmlWAeZ5KaZKz5YRbsZGTk49TX6jkOYxwmGpvrKVj4vP8ALp1b1lsjyn/g3W/aLXwf8f8AVfgrezpHp+t2dzqQ818N9rkktIVRMyBduBwNhbPQ9q/tqvCVhHdyuCPY1/mbfsFfESP4WftbfD/xNpkv2YXOs6ZZ3BRtn7mW7hLhsMvGF5yceor/AEtdM1ez1/RrXX7J1eC7hV4yCCCHGR0JH615XiBQUK6nH7STPR4Xm5YNN92fwGf8FqreWD9uLVGmRjmK6KhRzj7ZPzX5sfCTw94h8VfE/Q/C/gvU5tI1LUbm3gFxHM1vKollVPldAWBBII4PIr9Mv+C4d3O37dt2lsQgSzuAxPAyL2f0r82fgtdeL7b4t+H9Y+H2lz6tq1rdW0kcNvA9wxKTIw+WP5/vYHBHX1r8fwjcYOz77+p/svwPNz4EwnLZS9ivi228z+mPwN/wR/8A21Na0Sy1PRPjTr0AuYEl/wBI8R3yBQwB2jbaHH0rtL3/AIIzft7SwGL/AIXpqy57p4m1AEf+SlYXhn9tX/grNpdlaWPhv4U3LWkFuqI1zoes4IXGDlJgCcV0R/bv/wCCw3m+X/wqaPGf+gFrf/x6uiU3y35nc/jrMMs4wrYydWGNw2jf24dzlf8Ahy3+3raJJj47a1P5iMmJfE+oMBnuP9D6jsaz/F//AARM/az8QeEdO8MeL/ibca9ax3sFzML3Wry6fKAqQN9mRjBPUdT716Jeft1/8FgLC1W5T4RxyliAV/sHWmxnv/rq8v8Aip/wU0/4KbfBrwhbeOPin4AtNKsLi+igLS6XqsAVZFZuTNcIowFbv2+tc0Kjm1eR0YCHHMsRT9hi8M530tKF7/I/ez4S/s26z8Pv2ULf4BNdQeemiDSxMXbGPs3k7i3lqc55zs/DtX4Ual/wQ6/am07xLrmpfDr4oz6Ja6rqE19iy1u7tmBkPT93ZYxgD16da/bz4M/tIeLviJ+xrbfHm7hsjqD6ANTKIr7P+PXzjwZGbr/t/j3r8SNE/wCCnX/BQL4qeLNZ0v4K+C7bVbbTLuaHemnalOCIiO8Fy46MvYfyrOm7Tvd7Hw/B9Hiv65mEsFWpxlGb9o5tct7+eh0cf/BGr9ue60yOFvjhrKTxBU3J4lvxuUDqT9kyTmoYv+CMX7eMEm9fjrrTezeJ9QP/ALZ11Ef7c/8AwV+jQRJ8JYWUDG8aFrXJ+vnVkzft3f8ABYAXOw/CeMDPX+wtax/6Oq6dRqdub8T6Z4bjKpKUfreG/wDAqY3/AIc+ft62pErfG3U5gP4JPEmoMp+o+yV/PX+3d8Dfi5+zn8arz4efFfxRf+JliWEwz317NeOS8UcjfNMkfA3ADC9AAelf0YR/ts/8Fb76MQ3Pwr2qw5MWiazv/DM1fz6f8FDvF3xy8f8Axdh8QftD6HdeH7y54jFzbXFpv2RRKcfaSxOAF6Hv9K7YYiLmowWrvu/I/VPA2vnMc/dHMatGUHF6R5b30s9Om58gfDVYR460sNtyzSdOvCNX+n98NTjwhp6f9O8f8q/zCvhV9mXxvYosbS/M+2TAYL8jd+3pX+nj8OTjwnYe9vH/AOgiry6q1iG3/L/meB9MbXGZbG32Z/nE75Dyx61/mkf8FCcRftb+KrbqJPsuSeo/cp0r/Szj+41f5pf/AAUMz/w2B4nz/wBOv/ohK/U/Dtt15VPKX5n+fHFL/cxh5o/qA/Ze/Za0T9rP/gjxpXw8voM30j3wtZo1XzUP9pFmwxilYZWPHyr0r+NH4jfDnxp8P/FOpfDPx1af2fq1kw8tVjeJiHw44kVX5TB4UdfSv9A//gjGRH/wT58JOBu/f6lxj/p8mr8Kv+C+H7DGq+FPidH+1F8P7DZp9wALpLaIhUEMNvApIjhCjLMfvSc/XivfyfiB0sZXwk/hlJ/izkxmXKjhqeJXRI+qf+CBf7clh4l8Nf8ADPXxD1Tfqucaek04LuS9zK4xLMWOEUfdT68c19ef8F6Q0/7GGpWc3CyJwR7XFqa/it/Zp+Ous/s0fGbw98aPD80kM2mSzuyKzKG3xPEMhXjJ++f4xX9aX/BVb446B+0B/wAEx0+KPhq4juFuY5AfLdXIKXltGc7Xk7qf4q5sblbwOZ0a/wBltM78FmaqYCrN9Ez8x/8Ag3euIpv2ltTlt+ZGFuJAegCx3eMY5+ua/uCxGQYW5Br+IL/g3Ys4rf8Aab1cofvLa/8Aoq7r+3WL/Xs5PXFfOcZYpSzDlf2tPzOrh2sqmCVRH8yP/ByN8WJvCfwp8K+BtFuQ0mvSXsd1Ez5AWA2kiYVWH6qfbFfyWfEj4R+OfhhPDqfi+1ltn1oAKkiSImIQp+UOi9mGetfvF/wXj8Vt8Xv2ttK+EulzNM+ikuIlbdj7TaW7/dBf+7/dH416H/wX2/Z80b4f/C/4Z+NvCdkkKIt59pMUar0jtFGdka9yepr7ThrHwwFKNCW9Q5c0aVps+tP+Dcj4tX2qfC7XfhRq1wjjRxFJAGcmUm5nunbILn04wo49a9K/4OFvgdN8Qv2c7H4iaXA80/hgzygxruB+0yWsfz4Rjjjj5l/HpX4W/wDBDH9ouf4W/tq2ngfWLn7NpviMpG5dyif6PbXMn8Uir94js1f2oftifCO2+NP7NviH4exwLcvqEMIjygc8TxyHHyv2Xsprxs3p/wBm5vCrHROzOTDSlisuqp7pv/M/zQ/C2tTad8RNE8S2ZVG0u5t7lWXgCSCUOMkEccc8g1/pZfsTeO/+Fk/sneAPFscyXFzP4fsHnKtuAmaBGI+8x79Cc1/mYXGj6p4X1G/0zUkeOYTSlAwIOAcDGQD1HYV/cJ/wb/ftBx/EX9le78Eavc77rw5di0VHfLLDbWtuOjSMQAW9FHsK9rjfDLF4SljbepycKV/ZVJ0eh+fH/BxX44v/ABR8Y/A/wd0+aOWO5tLOeSOJizi5+03UW3AJGOehXd/Kv6Jf+Cfnw9s/h7+xT8NNGWNoJ20DTJLhSAp80W6A5G1T27jNfyI/tcaz4q/ad/4LJ2ngSEPeWWg+K47Qgb5FFtBquD3kUAB/7oWv7mfBnh+z8LeC9M8KWahIdNgit0UAAARqABgAD9BXwfENX2eV0qK0un82fR5ZDnxFab7n5p/8FmPiVH8Pf2IdYInEMurO+nKN+0n7TaXIwPmUnJHv9DX8EGu/Azxjpvwqs/i1qUE0elm9ghWcK4VpXQyKpYoFOQOm7Jr+rT/g4y+MdsvgHw98FtMuh9rlurTUGhVxuKp9qiPyh89ePu/j2r5s/aK/ZrlT/giV4O1bTrFF1C5n0rXZZFixJ5S6bKWyRHuxkc5OPVq+o4OzCOXYaFR6uVkefxFJxSmeRf8ABvd8bb/wv+1LqHw11O5SLStU0q6ulV3Kn7U8trGqqC4Xp22lvSv7a28xtzgfNghfSv8AMy/YM+LN98HP2qvAniZZmgjn1HT7e4YMVHkyXUJfJ3JxhecnHqK/0tfDHiGw8V+GdO8UaU4ltr+3jniZSCGVxkEEEg/gTXjeJGHhGvGrH7Vmb8OYj2nMux8Sf8FNXmT9ivxeWA3f2begj/tznr/Nuf8A10v1Nf6TX/BS7Mv7GPjQzDGNNviP/AOev82V/wDXS/U19J4e/wAGv6I8jif/AHmB/oHf8EN/+TI9J+tt/wCkkFfslX42/wDBDf8A5Mj0n623/pJBX7JV8FnP+91PVn2GE/gxCiiivKOgim/1Zr8qv+CxX/JkWufWT/0luK/VWb/Vmvyq/wCCxX/JkWufWT/0luK3wH+9IVX+Gzxb/ggV/wAo9/D3/XxqP/pbcV83/GX/AJKjff7y/wDoAr6Q/wCCBX/KPfw9/wBfGo/+ltxXzb8ZAf8Ahal+e2U/9AFfmfjR/Gp/P9D7Lw9+Ofp/mejfshf8nPeGP+u03/oiSv6D6/nw/ZC/5Oe8Mf8AXab/ANESV/QfR4V/8iyt/wBfH/6TE146/wB9p/4F/wClSP/V/pg/4KOf8lh0X/sDp/6Plr89rP8A1g/6+RX6E/8ABRz/AJLDov8A2B0/9Hy1+e1n/rB/18iv5l4t/wCSgxH+L9Efs/Dv/Ipj/hP2/wDF3/KP3xB/2Il9/wCkD18Ff8EFf+TZfF//AGNM3/pJa196+Lv+UfviD/sRL7/0gevgr/ggr/ybL4v/AOxpm/8ASS1r+leGv+RC/kfh2Y/8jWP/AG9+Z+5kTFTgU2V88e9Cdajk6/jXLFf7Kzrm7SR+TX/BaoMf2HNUKsybLlm+U4zi0uuD7V/nw6bI0d9FPgOZpVU7uR8x5r/Qg/4LU/8AJjer/wDXd/8A0juq/wA92x/19p/12j/nX6xwM7YSr6fofE8QJSxMIy2Z/pb/APBO2JP+GG/hSBkD/hFNJAA/69Y6+zY7aNW3qTnG3mvjj/gnYf8AjBz4U/8AYraT/wCk0dfaNfnmYQpyxU5TWqbPr8DFQoxUdDNu7Ykh43beowFJ4P1Hevnz9o/9nXwT+0b8GtU+E3jOJpoLxUCzqsbTx4lSU+W0kcgXOwA/Kcjj3r6OkwXD9xxmooRJHa7sbmHYdeteXg6kY4x1oL3u/o1/XyNqseaNpH+ZtbH4xfsD/tV32o6U0treaM6Okd35yRziaBgBIB5BfasuRjGD+v8Aot/s7/GXwv8AHX4b6f448N3VvcpMhEghdH2lWKfwu+OVPU1/M7/wcP8A7Hn2Wz0j9oj4baaFWUz/ANryW8OERY1tIISxii4ySR879enpV/8A4N2/2tpJNM1D4FfELUc3r+WLRLibnJe7mbAkl3fdA+6n+Nfo+a4SGOyqlj0r1F8T9HoeJSc4Y+UU/c6L5Hyr/wAHF+myWv7R1prAB23vyjP/AEztbQccf419f/8ABtDIgX4mWScrFBpJGevzSXmc14R/wckaHeW/xM8M6zJHiC5a5EbYODsgtAecYPPoa9M/4NmNVD678VICeFt9G4z/ALV5713V68ZcMwil1/JnkSqzjnCT+F/5H9abxeessXbAxjrVlmQxmNMFhSqpTe3rinJHGCZE79xX5lClCFW8V0/zPtp2cWmf5t//AAUx0+fS/wBtHxJYXAwxW0Jz15tYiOuPWv7X/wDgkxfz337A3hG7VVDGTURgdOLyYV/IX/wWe8Aat4P/AG9/EN7eRNHbXcdj5RKsAStlBuxlQO/av6vP+CJ3iGLxD+wB4URGBMc2o55/6fbj3PpX3XEK9vkdJdVf8v8AM+XyuLhipN/1qfqqZDHtVRX85n/Bxxs/4UX4MV+PMl1Ee/DWlf0bOOC/av5mP+DlXxBDZ/DP4faMrYaWbVOM+n2M+v8ASviOBaU3mlOctk/6/I9fN1F4acGfCP8AwbtWRl/aI8VzW2WW3jsDnqPmjux2FeFf8F84v+NgWvKeMw2H/pDbV9u/8G0Xglrjxd8SfFNypPlwaVsOP9q8U9R/I18Tf8F+22/8FANaY8Zhsf8A0htq/VKco1eJ/K/6HydWH/Caubc8v/4Ir/P/AMFGPAs7cO891nHTixua/wBB2ZAxj9Tmv8+X/gi0Av8AwUY8CIP+e91/6Q3Nf6Dsn3ovx/lXx3H8E8TVUvL8z2eGpudFSZZjiETlh6VRm+bOcjD7/wAq0WYBmHtXBfETxDD4Q8Ba74vuHCppen3V0xJwB5MbOe49PUV8lGmo1adOltfU9+pJqLfU/iy/4K5a0/7Qv/BUrwX8OyBNBbXFpoUqwfNtU6pKhdhmQBgH6lePSv6dfjR8CtKtf+CdepfBi1jWaPQ/B1xbQeYAzZt7CSNTwmN30UfhX8VPjEfGL9rj/gorr2ofBe7aPWW1y4vLK4Ek4EUYvQUcPB5rqFZ1OV4HUHOK/XPV/wBij/gtn4ytbrw3L8UfK0+SGSKWN9b11RJCQVZMeSVJKnGCMGv0jMMDT9hQVOSVrX9TwZ1pTpzhI/mLs5rv4bfFuM2oaO68OaokgEmR+8tZQR02nblf9k/Sv9IT9gT4v2Xxr/ZI8F+KLW4juLpNFsvtnlOHCXP2eNmX77kY3dGO71r/ADvf2m/g78SPgd8Z9U8B/EOIvqdos6XN3tlImnSV0ZhJKiM24qTkqCa/q8/4NvfjNJ4h+AXiT4V69dGTULTVJZreJ33MtpFbWsYwrOWChj2XbXscX5LCWVU68nzSta/kcGSRqUasKSl7rex+Nn/BbtXT9tC+WSJd0lpcz7tvz/8AH3ccZ9Pavu//AIIc/sMWXjmaD4+/EGxeKK3KmzKRARuALadC3mQsCTzkq/Tp615B/wAFHv2fPGv7RX/BUvSvBmhWss1k20XRRJGHkf2k6yZ2xyDG1+dwx68V/Wb+yx8DPDnwA+Eej/DfQbcW6WtnCr7UVBvjjRP4UTn5f7ua/AKWGpyh7OLvZu/37H+gfH/iUst4Cy/J8HO1apTV7PaNtT6HsdGsLeJYIYoxGgwqhRtwPbGKtnStOznyUz/uirFpkKyspG07QSOoqzXvYfB0eT3on8XqtN6tmY+l2LptMY546Cvxr/4LhWdlZfsbiMW8T7NUhILKCeLe646V+0kh24z61+LX/BdWXZ+xszk9NUi/9JrquTMcNQp07wjZn3PhspT4my+F96iOu/ZZjtpv+CV9g0USQl/AWCYwFPOnda+Tv+CINja3cfjz7SgleDXbyJWYBm2iO26k19XfslOJP+CVWmN6+Ax/6bhXyn/wQ1m/efERPTxDff8Aou2rwqDjKtGLX9XPusVTlHAcQNPap+rP6Fo9Js0TYFGOvQf4Up0jTj1hQ/VR/hWgvKg0/tX1lPB4eytBH4TCpJPcoDS7PbtijVD6gAH+VfiD/wAFhv2ItC+O3wTk+IOh6THP4i0MF7fy4FbcZpLeM7tsLyHCKcYYflX7mFN/y5Iz3HWuV8Q6TZazp82l6tEk9tIAGWRQynBB5BBHUV5eaYSGnIrH0/CnEmKyXNKOYYeTUoNPR7rqvmj/AC6/hVFe2njyx03UI5LW5glmEsLAocbWxlTz781/p8/D1ceFdOA/594//Qa/h9/4KF/sVax+zd+1paeJfDWnMNC1l23yCFvLi8q2j+6UhjRcu+Dyc/Wv7jPAabPC2n/9e8f/AKDXLQcG7pWdkn97P6G+kfxZhM+w+T43DSvenK//AJKdqqAQsa/zSP8Agot+5/a28TTLyT9m6/8AXBK/0umysRWv80b/AIKNY/4ay8TfS2/9EJX694e0orFyglpys/jLP4xdKMpd0f2qf8EUrr7V/wAE+PCck+1AJ9S56D/j8m9TX1/+1D+z3of7RvwM1f4S+KP3r3qoqSfK0i4mST5S8cmOEA+70r4z/wCCJcSz/wDBPPwpE2QDPqX/AKWz1+r3ySyLejcCucj17V8rnNdYbNKlRO1pNL7/AND0MLTWIwShNXTR/lxftC/Arxv8CfizqPw78eWvkG1ZTCpSRSwkQP0kSPPDDotfROhftsa5/wAMd3H7LPiRhNZfP5TylmnHmXX2g8tNtHIHSPp+dfvp/wAF+P2E9RvdM/4aw8C2u5dKG++hhQklSLa2jyscHqSfmkHt6V/IzqDabqNzHceWYSmd4IC9sV+w8NVKWaYZPE+9KO3yPg8fh6mHqujC6pve35fM/oS/4N15Zz+1JrSNjaq2nXtmK7r+2+5uYrS2e7ncJGvJYnAHOOvSv4lP+DePC/tYa7s4RkssfhDd1/Xb+1t8Qrb4U/s3+JPHk8ogXT44WLlguN88addy/wB7+8K/NOLsBGedUop2S/4Y+wymUI4B+zVkfxT+ENUu/wBtb/gq7p2peJpQsurXDW80Vsx8lVtbCRUKh/OIJEYJznJ6Yr+lH/gs7+zjY/Fr9h/V7yJ3+3+GreJrNQRhjPcWyNkeWzH5V427ffNfyR/sVfAv9pj43fHTXPG37N1zJaa7YyRyWd8Huo0iaRZVYmW1R3XKhl+UjOcdM1+tPin9jT/gtL4l0a+8PfEXxtLq2k6gqLJbjUtcuBiMhh8ksJT7wB5B6e1fR5+6NLHYVQaSsr/ec9Sk6uEnd3Z/Oh8HvG2ofDP4t6R480lhb3ukSSndkoPnjaPkgq3QnuK/1DvAWt6b4y8FadrVjKtxDcQJ8ykOpKjB5BI6j1r/ACste0TXPC+o3ugaup/tBXKuQGyDnPcBuntX+gh/wRs/aIl+O37G+kS3c/n6lpzXIm3NubBuZlXOZHbonfFdPiDRg4UcVDta5x8OtQqSw72sfyW/8FV/2Vbj9m345aXBpcE6aff6UbtnmUjM73EwC5EUa4ITp196+kv+CDvx0u/hl8R/H/g++uY4LKfw7rGtfvXK4uEFsgVcuo24HTG73r9U/wDg4w+Dw1L4LaD8XNMtVMllqFnYSOiciPbdytkhDx65YD2r+Q74a/FvxP8ABzxbqPiLwvLJD/aWlXOnlo2dcrcEZGUZOu0cZP0rtyiSzHKlQqO9jyc2i8FiXKirNn76/wDBLfw3P8ef+Csvj74k6zAk9rBc6reRyou5fMS9t5FO5g4/izw2ffvX9pKxpud8nbktx61/LP8A8G5fwy1W40rxV8bdbgJmvby5t/OdDkiaO1k+8y5Oev3/AM+tf1AeKtas/C3hHVfEl84SDTrWe5kYkABIkLk5JA4A7kCvzXie1bG0aEV7sT7DKE44d1HvJfifxDf8FeNYvfjz/wAFO/DHw00y58yD7TBojIrkhZn1KWPBALgEB/7ufav6c/iB+zRBd/8ABPWT4D6moN1ofhCW0iU4/wBbb2UkSnBjz1PZAa/jX+JNj8T/ANq7/go94vm+CFwzayfE13eaTdK8pEBW8Bhk3wea6KjurFk6dQc4r9aNS/ZP/wCC39zay6bqXxCa4V7Z7ebZq2vOrqww2Mxc59697N3SoUsPTpq1t/U8yEHiXVjW1S2P5mvFWhav8MfiNe6LdwzQ6l4c1JoY3VSqYtn4bJCtncOoA/A1/om/8Ez/AInv8UP2K/AOrTTLPdWmi6fb3DBtxEgt42bJ3Mc898Gv8+b9or4ffFH4WfFvW/BHxdufO1+O7nNzKzysXdXZHO6ZVkOWB6jJ781/Vd/wbxftDnxN8NdV+B2qXG+5sJZJ4EdskQwQ20YwDITjJ7IB/Kva41w1PFZTSrRXvWtc8zIZSoYlw6Nn64f8FKXeb9jPxsLr5cadf7ccZH2OfHWv82ucYnkx6mv9JP8A4KaQyH9jfxeB/Dpd7nH/AF6T1/m2XBxPL9TR4fwlHCzTfvW1O/iSMHKEran+gP8A8EN3P/DEmkg/9O3/AKSQV+zFfjH/AMENwf8AhibSf+3b/wBJIK/Zyvg84/3mo/M97AO9CIUUUV5R2kU5Aj5r8rP+CxK5/Yj1v6yf+ktxX6o3H+rr8sP+Cw//ACZHrf1k/wDSW4qsFJ/W/kvzJrfwzxL/AIIGAj/gnr4fPpcaj/6Wz183fGP5vijfH3X/ANBFfSf/AAQO/wCUeugf9fGo/wDpbPXzX8Yc/wDC0L76r/6CK/MfGmT9vS9P8j7rw8iuap6fqz0f9kHn9p7wx/11m/8ASeSv6Dq/nx/ZBB/4ae8Mf9dZv/SeSv6Dq08KXfLK3/Xx/wDpMDLjh/7dD/Avzkf/1v6YP+Cjn/JYdF/7A6f+j5a/Paz/ANYP+vkV+hP/AAUc/wCSw6L/ANgdP/R8tfntZ/6wf9fIr+ZeLf8AkoMR/i/RH7Pw7/yKY/4T9v8Axd/yj98Qf9iJff8ApA9fBX/BBX/k2Xxf/wBjTN/6SWtfevi7/lH74g/7ES+/9IHr4K/4IK/8my+L/wDsaZv/AEkta/pXhr/kQv5H4dmP/I1j/wBvfmfuUnWoZv61MnWo36/jXNB/7Kzqqr3kfkv/AMFrLiGH9hzV1kzkzPjHr9juq/z4tNKlrZ5jt/fJjt3r/Qh/4LUvaxfsN6xLcpvxM+OAefsd161/nt6bAdZubURHYomjHp3/ABr9W4H/AN0q+n6Hxee/71C5/pe/8E6JA/7DfwrORgeFtJ/9JY6+0o2WZS0ZBxxXxd/wTzsWsP2IPhXbREN/xS2lZ3c/8u0ftX2hEGjTaoUE+nTNfnmYUVLEzl5s+swkv3UUQTHY/lMjktzuUcCl2XMVw0qlTEccc54/SnRm9LEXGzGeNuelKd+z5Tke9cE8LGMXNPVa/mdm58uftW/s8eHv2kv2ftf+CnidpfsmqJComhK/aU2TxzHYzxyAZKAH5Dx+df58n7KPxD8WfszftiW2vRTG0lsZpF8pmeOJh9nlQbl3Rk8PnqOa/wBLNFLzAE8Dt61/mOftb3Ueh/tQ32o6KvkqZRgAbf8Alio/hx6mv0Dw6rvF4DF4ar8KWh8/nE1Qr05r7V/wP6a/+DjPwFJ4z+DPw8+IWlKCun/b5LmbHyDzvsaLuYKe+QMsOa+Rf+DbvxjbaJ8VvGfhrcqtrsenphyN/wDo4vH+XkfjwePSv34/ar/Z8039rb9ga98BakyRX89rALe4fC+URcwu2HKSsuVjxwOen0/ju/4JM/tCxfAD9rODxLexSy2EUzpNDbruLbILlBhTJGp+Zs8mtsI44zKKmBhq4u/yvf8AI83O0qeLoTjvfX8j/RDju7hrPfdALIOoTPTPHWr9qR5KiLdt5+91qpB5dxaR3ajiVFbn3GauxHzEyvyr2xxX5nhYTWJqKb2sfXzl7qP4/f8Ag4/+FjaD4u8G/FeKAr/a73qXMgXCgW8VpGnO0euOWPtivvP/AIN6PiFd6x+y7N8OLpozJ4eZ5MKTuxd3V0/zZY+nHyj8a96/4Ldfs6j48fsiajfs0Ed5oCB7SSQ42m4ubVX58tyMhf4ce+a/Ib/g2u+LV1a+OfiD4F1jzJPtcempDtyUQxm8Zs7m4z7DmvvoSdTKXFdP8zy6tNU5xkup/YZLBgCEcqevqK/j/wD+DlP4i6Rd+IfBvg2aQmXSZL12SMruAnitGG4bs8444H41/YWwG4t61/np/wDBZD4oXvxu/b11bwh8wgCWqwibOFP2OEt/E4GdnYVy8IYRLFuceiIzmbUYx7n9Dn/Bvl+z3r/wv/Z1v/ij4miaC68V4jMZVlVRZXNyq4DxqRkMCfmb8K/Df/gvxCkv7eWuTEnKQ2OPxsrYV/aL+yt4EtPhr8BNB8JWKokcCSNiMALmRy56Kvr6V/F9/wAF8Pm/bs8Qe0Nh/wCkVtXo8NYv23EMr7pnk59D2WChFdTyP/gii7T/APBRLwHcP1ae7HHtZXVf6EpAZ419Af5V/ntf8ETFA/4KEeAT/wBPF3/6RXVf6Esf/Hwn0/pXJ4hwSx3L3a/M24Vf+zj7pzHvdeoGR+VfEf8AwUF+IEfw5/Y88c6xcSCI3+i6jaqzHbiSaznwAdy88cck19u3A3SbfUivwY/4OBfivP4J/Y/Xwjp3mJNqV/EpdeF8uS3u0IJDg9umCK+ayOg6+ZKHS59XGnz2ifkJ/wAG/PwkvPG37Vmr/GPVoDcWUGlXdsJWQuouDNaSg7irLvxzndn+df2mPbW8ZWONcHcFJAGTX89P/BuT4Cs9F/ZZ1fxRcpG93dasSJAAWCSWlqcZKg9R0zX9ETR5uc9hzXbxViK1PEONJ6LQ8nCUYS5r92fw7/8ABwt8Jm8B/tWab4r0OOX7JrGjm4nLL8hupru6JxtUDOBxklq80/4IO/tHWPwp/bFg8I6tOsNrrumPYhHYKpurq4tY1wDIo3YHHBb2NftB/wAHF3wfsNZ/Z+0f4swiMXljqdpaFmwGMQju5SAdhPJ7bse1fyVfsueJI/Bv7RvgLxnpgeKWDxJpTuY/lZo1uo3ZeCpOcdM4r9Cy2MswyWVOUtkfLZpUeHzOkoeR/oT+C/2TtEi/af1f9oTXIfOu2S5s7ZXVWjEUk6zK+GizvDDhg+PbvX3Q8CBVVRgqeo9u30rkfhr4j/4TLwHo3i2MFV1GyhuMMMMDIobnk+vqa7jvzX5FQy+nhuemtbts/R8wzTEY7klWleySXkkNLEJ+tR28rOrM45BwKWVDIu0HH0qvuAbyRxxWeKqum0cySSsMupZY4y7LuxyAnJzX4xf8FyFW7/YsjL8SSaxACh64NtdZ461+wep3MmmxtcozMEBZgxz8o5OOnNfzhf8ABcD9rHwvc/BcfDXTLO6+3w6jHLumjXyTsjuU6iXd1P8Ad6V4ONqzluz9R8JcnxeM4nwLwsL8s02+y8z7q/ZZFvZf8EstLtbdXdv+EFVCoAJB/s4DoO1fIn/BDsXNpe/ESG6A+fxDfMNucgFLbrnvXxN8Ev8AgrNofgn9i6z+DN7o076k+grpvmxW6mABrQQ53G6Dfe5zs6dqZ/wR5/bf8GfDzxf4p8KeKLC+nudc16eSKS3iRghn+zxgEvMpABBJwDXi0XNVOZ6Jb/efruZ+H2e0cuz2UqDftKl1rurt3P7C7aUsDG3arK4J5rj/AA54gttX0u31m3VxHdosihgNwDgEZ5Nda7hUDgds19zgavNS13P5Qr0ZUm1PdCyLKGUpjb39azJ7IG2NlI74bq2fm6561oxziRdveiUb+TTq041tbkubspI+Dv26f2XNA/aG+D97Zx227WbRQbOWNFL5eSLflvKd/uLj5ce/FfYfhG1vLDw5Z2eoBRPHGEIXOPlGO+D0rqPKRoyHAYehFVAcESN0HSvIq4Z0q177r+mehUzOrWwkMPN3jFu3le2npoX5WCsIsHnvX+aZ/wAFD41uP2vfFkLkgRC0xj3gSv8AStW6juDkD6Zr/NW/4KC8/tieMR/s2f8A6ISv1zw/aeOutuU+O4ofLho+qP7O/wDghtO1z/wTu8ITPjJuNTHtxeziv1mmjKSblyB6DpX5Hf8ABDRtn/BObwgf+nnVf/S6ev17VROmTXxXFOElXxNVR35n+Z6OT1UqFOL7HlPxm+D/AIW+Ovwv1T4YeMw82nasiJMAEY4jkWQY3o69VHVTX+bd+2t+zX4n/Zg+P+s/DnxZZta2Ft5LiRI3QsssSSDYXjjUkbxn5RX+nTECgCV/N/8A8F+f2ONB8efCOX9ozSlt7e90BS96WCo9wsr2tvGOIWZ9o7M4x29K9/hPOKmAapt6NK/qc+cYNTXOtz8vv+DdnyP+Gn9btL0sL23WzY7P9WQ8V3jrz06+9fvb/wAFxfijH4D/AGHvEPhKOdIptfiSNCW2yfuLq2c7PmU9DzgNx6V+EP8Awbzrb2/7RFwlwu7UHMQnlABDKI7rYNx+Y4Hr07V9Df8AByR8X9TOr+EPhTZtIkMMl4Z+SFcSRWki9HwcH1WvaxuGWMzmM7/Zv/X4HDhaTo4Rw7s95/4NyPAcVh8K/EPjW6tQ0l8IVjmdPmzFPdKcMVHrjgmv6bEtGS2MYZpD6uc1+Vn/AASD+Dtj8I/2Q9Fs4djT3LXLSOmDkfaZWHIRDxu71+qzzOtysI6N1/KvlM4rWxMnJ31sj0svopwcT/Nu/wCCjHwLuP2f/wBsfxL4URZzpyC1lt3uQd7NNbpI+TsjUgF+MDjvX7ff8G3Hxps9O1Dxd8FdXuUQCOzOnozgSO0j3csnDPzj/ZTp19a8e/4OP/hpb+H/AIteGPiBpqxRtrL3CS7eGIt4LVRnCj17sfwr82f+CUHxcv8A4Tft0eCNTtnl+x3k9yt1FGT84jtJ9uVDqDgtnkn2r9Cq0PrmRpvdK58tXl9TzOMo7OyP7av+ClHwA0f4+fsdeMPDOqNOW0qwvdXthDt3tc2tpP5YOUkypLcgAN6EV/m8S6Jf22qyeHdUj2zWE/kspBDeZGcYIIz16jANf6pniayg8W+B9T0C5XMWpWE8LBum2aMqQc5HQ+hr/OH/AGl/hMfCH/BQjW/hhbNGLa98ZPAgU/Ksb3hiAOFAH02kfyrxuC8b7KVWn0szr4lwzrunJH9p/wDwRw+FNl8MP2KPD95BE0cviC3tNTl3qBh5rWEHb8qnb8vGcn3r6B/4KE/EtPhV+yF451gSpFJfaNqNnG0jbf3k1pNt2ncp3ZHGCT7V6V+yx4at/An7OvgfwZbhcWWi2URK4wdkSr6L6egr8kv+Dhn4nah4N/Y9sPD2lM8cuo61aRyMpIBilgu0ZSQwPOOhBFfG0JLFZzKO9v8AM9mtP2GXproj8hv+DevwFL40/a11v4l63AbtI9NvQ0kq+YouPOtZAQWU4buDuzX9uKRRKCABuPWv5w/+DdL4QaXoHwB134kuEa8u9UeNWGCwjltrV8Z2AjkdNxFf0Y+cVdnHrVcRZh7LEKMvQrK6fPS511P4dv8Agv78Crn4eftRW3j7TbLFjrdi13NO0Z5uprq4bbuCKuCq5wWLV5V/wQ6+Mkvwv/bPstPMyR2+taa1qyythfOubm2XCjeozgcdT7Gv3M/4OJfhlZeIf2W9K+IESxre2er2cBdgA3lCK7kKg7SeT23Yr+Pr9mb4iap8L/2hPCHjTTnZTaa3p5cITloluI3K8Muc7emcV+n5PR+v5DUv9k8avSVLHxSP9C7/AIKYXYi/Yz8ZuMHdpl8PztJ6/wA2O4Ia4kHua/0Of22vGp+I/wDwTfv/ABwoaP8AtbQZbrDcEebYzNgjLev941/nhyf8fMn1NYcA09MRF9EY53U5qsUz/QM/4IbYP7Euk/8Abr/6SQV+zFfjR/wQ2AH7Emk/9u3/AKSQV+y9fm+b/wC81PVn1WB/gRCiiivLOsguP9XX5Yf8Fh/+TI9b+sn/AKS3Ffqfcf6uvyw/4LD/APJket/WT/0luKeC/wB7+S/Mmt/DPFP+CB3/ACj10D/r41H/ANLZ6+bPjD/yU+++q/8AoIr6T/4IHf8AKPXQP+vjUf8A0tnr5r+MOf8AhaF99V/9BFfmHjV/Ho+n+R954efFV9P1Z6T+yD/yc74X/wCus3/pPJX9Blfz4/sg/wDJz/hj/rrN/wCk8lf0HVr4Tq2V1v8Ar4//AEiBz8br/bof4V+cj//X/pg/4KOf8lh0X/sDp/6Plr89rP8A1g/6+RX6E/8ABRz/AJLDov8A2B0/9Hy1+e1n/rB/18iv5l4t/wCSgxH+L9Efs/Dv/Ipj/hP2/wDF3/KP3xB/2Il9/wCkD18Ff8EFf+TZfF//AGNM3/pJa196+Lv+UfviD/sRL7/0gevgr/ggr/ybL4v/AOxpm/8ASS1r+leGv+RC/kfh2Y/8jWP/AG9+Z+5CNh8VKyp1x701EB+bvU0jADYawwrtT9/Y7Ki7bn48/wDBbqaF/wBhjVopeFNywz6Zs7qv894XwstNisNJO6UspD4xjjHRq/08f2wf2Z7T9q74R3Xwjv8AVv7JhunLmXyDPyYpIvuiSI9JM/eHSvwgX/g2j8Dx3AuE+JHOckf2PJ/8sa/RuE+IcFgVKnXjeL3t/wAA+VzHBzq1FNxvY/Nr4Df8F2fjT8C/g14X+E3h/wAM74vDmnWmlGX7ZAPPNtGsfm7WsnKbgo+Xccepr1S2/wCDir9ogXZvbjw9uWObZ5H2u1G4A5+99g49K+3H/wCDafwBJMJ5/iJvO8HP9kyj8P8AkI01/wDg2a+Gv2j7X/wsPnzPMx/ZMvbn/oI1vmOMyCtVlUhTav5yNMFTzDm1laPoj7R179tT9oz4nfsKt8YvBPhzytU1fTNPvopftdq32drhoXZNskKq+FYjcVHXoK/G/wDYQ/4L4+LPhBfXHw+/a9t/trM5C3u5IvL5lf8A1dlYvnhkX73v61/U9+zF+zPpf7N3wX0/4KwXX9s2dlDDF5+w2+RDGkY+UySH+AH7x6/jXyD+0h/wRz/ZN/aLu3v9U077Fcnkv515J2QdBdxjolfNOtl7m+al7vTXW39XPal9cUXBTX3HyD8fv+DgP9lLRPBEi/C+X+2r+VeRi7t/LwyY/wBbYlWyCfpj3r+XX9kr9nf4j/tv/GgS6XD5dq0rs1zuiPDJLj5DJCeDHj9a/qC8O/8ABuV+yhpetJfzXP2mJfvJtvE7Edf7QJ71+0XwB/Zc+D37NugjRPhjpv2RF6/vppM/Mzf8tZJCOXPevVhnGCy+hOOWx5HJa9TyquX4jEVYrEu8Y7dD2LwzoMWmeFbTRjF5XkRqu3du5+uT1+tfw+/8FgP+Cc3jH4B/FG4+MvwgtPL8MzbWaHeh2ERwRk7prh5Dukdj93j6V/dK9zghm+UD7w61yfjPwd4a+IegXHh7xRbfadOuAocb2TdtYN1Qqw5A714ORZ/HC15Om9HukdeY5Wq3LUXxR2Z/Jd+wv/wcH6Z4V+HMfw++P2m/2nfaYCG1PzjBvDySMB5NvYkDau1fvHOM+td3+2n/AMHBugar4KXwv+y7Fu1e7BBn3N8u1omHy3diEPAcdff0r7E+N/8Awb6/s1/FXWm1jwnqH/COrKcyJ5V1d7sBQOXvlxggngd/avZf2a/+CI37Mn7P8iahN/xOLmMkiT/S7frvHT7Y46P+lelmlXKavNOlTfPLfVq5nhY42U/389PQ9x/4J/fEj4w/tEfs4oP2hdG8iWUMRP8AaIG+0/v5ONtssezYFX/e/Ov5SNV8M+O/+CTv7f8AY+N/EVv9t8OCZ5JH3xx+YGtDgYU3Ui7XuAOBzj06f3maJplj4fsE0fTYvLghHyLuJ68nk5NfNH7WH7Hvwo/bA8ByeCPiPb70Iwr75hsy0bHiKWLOfLHevJwGZKjB0E7w7en9b/edeOw1SouaL1Pjz4qf8Fhv2TPAfwbk+KGka3/ad46L9lsPs15D5rLIiOPNa0YLtDFslecYHWv5+v8AgkD+yD4h/a0/aLl/ah+J64sLKYyLDlf3odLm3xuiliZdu1T/AKs5/M1+n/hP/g3J/Zr0nxDBqGsX/wBqtrVmaOLy7pPvgg/MNQJ6kdc1+9nwo+Engf4JeEYPBvgW1+y2duDhN8j/AHmLHmRnPUk9a9F5rh8HCTwt4t9W/wAv6uebRweJryTxUrpeR6FDZiFFgU/u0UBV/u4Hr3r+DX/gvrJHB+3prkMh2+ZDY89cYsrc1/eck+6YgjrjJ9K/FD9t7/gjV4R/bQ+Nl18WNa8R/wBkyXCwqR9kef8A1cMcX8N3D/zz/u9/xPFw1mlDDYxYtfP+v6+8683y94ij7Ptsfx0/sMftO+GP2Of2mNA+M2u2/wDai6HJLItpvaDz/Ot5YiPMWOXbt8zP3TnGPp/T7bf8HHPwYNut3N4O2sP4f7QnPt1/s+uUX/g2p+FRu5Lq/wDHfnbgNo/syZduBg9NQ5zVqX/g2u+EVxlR43wP+wbP/wDLCvrs9zbKMwrqtUg2/n/keDgMFjMNFU6crLrsfY37Iv8AwWe+GH7XvxcT4SeHfDn2C5ktmuBL9sll4EkceNrWkQ6yA/e7V+N3/Bxr8RW8SfHPwp8OLO42pb6bbytFszukjubpPvFRjOcfexX6/fsY/wDBFH4ffsa/GAfF7wx4p/tG5+yPZ+V9ilh+V5I5Cdz3kw6xjjb36+vW/tPf8EgvAv7UH7QNp8cvFviLypLOIIsH2SRuVnacfMl1GOrEfcP9K+chisJhsT7fCQt+f4n02FWKUvemelf8EhvAaeCf2LPCT42tqmn2V6w67TJawgjO456e30r9OZXCy5XmuL+FfgTTvhh8PtG+HGlyebBo1nDZxtgrlYUCA4LMR07sfrXYXPyucV8rxHj6tRurf4nqddDDqF77nwb/AMFNvhhb/Fb9jPxjpc7Y/s3Tr7U0GDy9vZz4H3l/vdefoa/zg9FuDoOvnUnXypLOfyAM7trqQQ/fOMfd5zX+qP408M23jvwPq/gm+k8qLWLKeyd8FsLcRtGTgEHgN6j6iv57PGn/AAbmfCTxd4l1DXv+Ev8AJF7JJNs+wTNtZyT1/tAZxn0FfX8M8RU6GGcGrpo8bH5bGpV9u17y2P0Y/wCCTXx3T41fsbeFUEf7zw7Y2WkySZ/1zQ20JL42Jtzu+6N2PWv0yeQL2xXwT+wJ+xTbfsQfDS9+Gem6x/attNdmdG+zmDgRRxjgzTHpH/e7196ruI4rw8ZXhVnOpRVkejgqdX2S9o9SCSZlXfGOc818sftDftd/Bz9mzQZtf+I+ofZfLRmVPKnfcQHIGYopMZ2EZxX0D4v0O48QaNPpdu/ltMrJuxnG4EZxkdM+tfj78Zf+CM/wj+PPiibxP8U9T/tDzXaQQ+TPFglmb70N2mfvHt3r5LGVsRKpyqN13/4Gn5n2/DOFyapXUs5rONNdIp3fzs7fcfjL+2//AMFsvHvxZuLzwd8CYf7M0fLq+p7o5/MjzIpi8m4s0dd6MrbwcjGOtfz7+L/Fd54z8UzeK9XPmXk7M8knA3MzFicAADJPQCv7R4/+CAf7LsbfY7X9zY/eEX+lt846Hcb7d04xUD/8G9P7I8zmVhkk5PN71/8AA6sIRt9l373X+Z/ZfBHjD4acMUYwwNOUZW3UJNv1bP4lpFglvReSjc46HJHfNTLNFaXMWqWK+VfW863EM+c7GQ5X5TwcHnmv7Y/+IeP9krO4D9bz/wCT6Rv+DeT9kf8AiX9b3/5Oq1TqK7mnb5f5n31b6UfAtSDhJzae/wC7Z+Lf7FX/AAV9+KPwMj0/Q/iEv/CRaVAkdsVzFaeSB5a78x2rs21VPy989eK/q4/ZU/4KC/Af9pzQbVfCeo7tSdE8y18q4+RiEyN7wRKcFwMjrX54Tf8ABvz+ygLU2wP7tl2Ef6Z908Ef8f1dR8Nf+CHfwQ+EniC31/4aXX9my27KwOy4mztZW/5a3jj+Edu1VSU6a56MPlp/mfzR4i5x4b5854nAOdKq9bqLSb81Zr7rPzP3dtZjK21Ytq9juz+lXScgCvJPhj8P7v4f6bb6bd3n2zyoY4g3liP/AFYxnhm616w0ak7s17+DlKUbzVn20/Sx/M2MpxhNxovmj32JMA/L1qlLafIQX/SrO4AcdqXO8ZNOvRhUlZ/FYwin1Rnu8RIcjBT+tf5r/wDwUOMMP7ZfjiFG3eSlgc4xu3QJ+WK/0opYNzbc4z3r+ef9of8A4N/vAX7Qfxh1n4p6n4y+wtqiwDy/7Pklx5Map1F9H/d/uivouCs3jgqkqmIequv8jys3wjxKUH8J9Lf8EM5y/wDwTt8I2rDaRc6p79b6ev2IgO1cV8lfsafstaX+x98E9M+C+maj/aMNi87pL5Ji3GaV5T8pllIxvx98/h0r63UZwK8LOsfKeKlKk922zfLMK6dJKe6/IfGpLCuG+I3w80P4qeDdQ8AeLYfP0+/VFlG5l3BHVx9xlYYKjoRXfJGrLhuRUTSzShJLf7vOf8ms8PUlpKT1O+aUtGfyf/8ABML9m3Vv2Xv+Cj3i7wVrk/2rzBYNbzbQm4NbXEhG1ZJMbQ4GSeetfn5/wWn8UWnxo/byvvDOiyZZEtgi4PyH7HCScsEznaepr+2HVvgn4WvPipa/F0Lt1KA5Zsud2I/KHG/aOP8AZ/xr8gvFX/BEnwl42/alu/2j9W8VbZZfKK2/2Fz9yDyT8wvFHTn7n+Nevgs1xNObq1Xeeyem3/BOTF0aU3GCWn6n7B/BrwxZeCPhvo3hqE4xACo55J+Y929fWvWZofmB7mqNj9nitY7a2bebZFXoR0GO/wBK07hdwVx1FebjuXExblrd3+Zrh4KGkT8Av+C/vwfHjj9mJfiU37seDRJMR1837ZLaxY++Nu3H91s+3Wv4g/AHiiXwt4msPEmkT75IXkKttxsJUr/EDnOfSv8AUQ/aD+D2jftAfB/WvhDrlx9lttZjiSSXYz7RHKko+VXQnlAOGFfz2at/wbW+AruSa40/4heQZDlV/smRsfidQr7XhbiClhsJUw+M1ve3p2PJzPKqVatGo4n74/s9fGfRPjB4Ai1rRotq2xjtX+Zj8wjRifmRP73p+Nfzq/tsfsUTaj/wVe8C+MLG9321/dWWtTr5eNrHVCxTmYE8fxAD6V+637HP7JN7+yV4ZvPDl14l/tuC8uDMB9jFthmSNB/y1lP8Hr3r6H1b4W+GvEXxAtfH2pR7rmyhEMZywxtk8wdGA6+q181VzWlhsRJ4V8rf9f1c7qOEjOyrq6R6Hp+kW1hbw20J+SCMRqOei/ia/lA/4ON/HlnrGu+GfhZbN+9W3t70DB5aOW6jxkqB367vwr+s2WURI5zjqc+lfit+2J/wSD0b9sP47af8adc8W/Y1s0VVt/sDSZVZ3mxvW7i/vkfcP9Kwy7FUYYiVeGs+v9dSsXQhOKpSXunt3/BIn4ZN8Nf2IPB8lxJmTX9NsdRZcf6tpbWIbchmzjHXj6V+ncdosZDFt36V5v8AB3wFY/CL4Y6D8L7S5+0xaFZQWEcmwpuWBAgOCzEZx3Y/WvUmULziscdCliqzqVFdm2GpxpxUIaI/PD/gp18NE+Kn7H3jTQpzt/s3TL/Vo265e2tJ8LjcvXPUkj2Nf5wDzx6cDa253XSnY46eU/dvRtp7d6/1Z/Fuh2nivwxqfhm7bbFqFpPau2CcLMhQnAIJwD6iv51PiB/wbveAfHHj/WPHUHjv7EmszTymL+zJJNnnsWxu+3jOM46D8K+t4W4kWBo4jD15+5LZWPHzXAVauKpVaWlty14F+Llj8R/+CKcGn4/e+HfDK6RJJz++li0pjvxsXbnd0G4D1r+K+ZPKvCLo7FY8nrjJ9BX+hL8Ev+CZGnfBX9mHxH+y1F40+12fiFbiJbj+zinlie1FqDs+0uTgDP8ArBnpx1r86Yv+DaHwOny3PxJ8z/uDyD+Wo16PDfE+X4T20k9ZP1/K5zY7Jq0/eb1PmD/gnR/wWU+E37KXwJs/hH4j0b7YbURFrr7RNH/qoY4z8i2kv9wn71foHon/AAcTfAfX9atdG0/w3va7vorJD9suB/rWChubAevT9a8Su/8Ag2S+GV2Nz/EPBHT/AIlM3/yxrV8Jf8G2vw+8La1Y6vb/ABC85rG8hugv9lSrkxMGxn+0D1x1wfpXJmVbJK8XUpxfO99Xv89DTDU8ZRUYOd16I/pL+HPje1+IvgLSPHdlH5UWr2cV2iZLbVlUMBkhSevXA+ldeJiW21578MvBSfDn4faL8PLa6+0Lo1pDaCTZt3rCoXOCWxnH94/WvQE4b+lfAYnmVWPJsfQ0nePvbkjHeNpr8sf+CxWV/Yk1se8n/pLcV+qBIzwK/K//AILGf8mS639ZP/SW4rvwsI/WYuxNV3geJf8ABA0lv+Ce3h4etxqP/pbPXzb8ZTt+J99j1T/0EV9I/wDBAv8A5R7eHf8Ar51H/wBLp6+a/jNn/haF9n1T/wBBFflvjPCLrUr/ANaI+98O/jq+n6s9L/ZBH/GTvhg/9NZv/SeSv6Da/n0/ZB/5Oc8L/wDXWb/0RJX9BdHhUrZZW/6+P/0mBhxx/v0P8K/Nn//Q/pg/4KOf8lh0X/sDp/6Plr89rP8A1g/6+RX6E/8ABRz/AJLDov8A2B0/9Hy1+e1n/rB/18iv5l4t/wCSgxH+L9Efs/Dv/Ipj/hP2/wDF3/KP3xB/2Il9/wCkD18Ff8EFf+TZfF//AGNM3/pJa196+Lv+UfviD/sRL7/0gevgr/ggr/ybL4v/AOxpm/8ASS1r+leGv+RC/kfh2Y/8jWP/AG9+Z+5tu2cjPep3XIqgj7W/GrZbdg1zYScKkPZvodrTvcpSWwL+aBzjFVpY0fh+cdq1TUUigr83NYYnLIcrcHYtW6la0KxAiLgHk/WrRWR5AytgenrSQhQDtqZ+B+FdGFpSjTiubQmbUdUVVt5Vm3+bhScldv8AWnJEq3TunzF8bu2MdK4P4mePtN+Gvge/8b6y+y2sAhc4J++6oPuqx6sOxrH+Dvxf8K/GrwXF428HS+dBOXX7rrzG7Ifvoh6qe1dX1eKfnv1MPrDfQ9RuVgnfyG529R9ajgs4YgVh+X9al8tN5mAwzdfwqzgAcVwPDupXl7Rm3M+VWITbowIaoZbZWUxMco3arlIwyprSrgaSi3FWYlKV9SlHttkEEQwo6U8/apm3JJgemBTvLDc5xU0ZEbYNcFJSdozm+X1K1TukVRbS4IL/AE4qUwMYwHOTVzcn3hzSFs16CwdKUdBOUmZLoSnlyfN+lLHD8m2L5f1q7Ku5c+lPiTA5ri/szmre9LQSqNK1iKO3ULiQ59alggNvCEY78Z56dadQXO31r0o4aFKN6a1SC8nuUZVt9hKr97ryaWFFgGFGB3qZcO3HapdgxxWeH56idSctehlUjaWiIURzL56tgHjGKnMUOS6jk9aiiX9KWWQRj3raNSEaftJS0GnJaEyKB07c1SmQu5Iq0rfJu9RUNcOZOFSCjvfU1pt7kS25ZRzjDA/lSzWdtPcrcSrl1xg5Prmrauu3axxSkpjg5rfB0qMKSiOUlfUrGNzKXB46YqYsR1FJvUA7TzQHPcVtThCDcYLz3IdVEcomlAEL+WQQScZyPSnC3TO8cORjPvUnmj+7+v8A9anBt3IGKuWHhJ3a1F7VdCvHbXKNl5NwPbAHFTYIfy+nfNOOW71wHjvxnp3gTR5da1E5VATjnsCewb09K5cVXpYGk6ktiqVOVWShFXbO8KP1T5jnHpUixv8AxV+Xmrf8FAdIsPFZ0fyP3OSN28/3sdPIz+tfc3wu+J+lfEnw7Hrelt95RkYPUqD3VfX0rwcFxjhMTV9lFano4rIsVh4e0qU2kesT2qSgeZyAcj6jvRHC+D5rb19MYqOASFd7+tWlDdRX00aVOaVRLc8tSlfUZjACx/KB260jLuqZl43VQldlPvWdar7JaGq1JimBmpUQ7PeqMMnzf5/wq7LLsQE96zozvfETfSwTuiG4X5cVE8hdkb+7n8alRhcHaKY0RHI6CuCt7eNR1sP8LCLTWoyNUM/mY5NaSoB1rMt2HnA1fBI6V05dFzjKdRa3FKSexPVVoSjxmJ9iqTlcZzn37VaBzzVR3Oa6MRNU0KKuMk3qqgNwM5461RVJD8rtuHpjFXlGTTwqnpXHGNev70HZeo5QjfUZb7YVEaDAqQhjIXzwe1Ox3orvpUJxS53dIe2w1xlSKgeZXZXZfu9OfWrQGaqEqsu3FTi5zjaUJabCuloyVg0rZPSofszICyN1bPTtVw01ztjzWeIwdPkdSW/cal2KLQln354xjFQSC4ChYpNuHB6A8Dt/9etKJ+DimyDd83WuKFCSpe2oS1BxTfvEYV3QiM7CTnPWrCsVjCOckd6YnyjB702YA9eK7VzUqXtpfEFlsiRVUHeOtUdxnLxyt5hV8qMYxjoPep2LNAyL1wQKoyxSWtkZ4Dh0G5vfAyaKsKlZQdJaPfUrZNssQCBy4DZbJ3DHT1FOENuD8q5I96+bPAn7VPws8cfFXUvg7p91t1vTjMJotk3/ACxdY2+YxKn3mA4c19LGBIn8ztSxOW1qPKqcVruRGo5OzQ4TGNcHgenWoWmkdvkbb+FTNBHON0Z6VF5O35q8zE/XaUuVaR8maWi9ydJCcA9amPtWcW21cgOV+vNejhMS5e7Pc5mtdBcsBzX5bf8ABYz/AJMl1v6yf+ktxX6mORjHSvyz/wCCxn/Jkut/WT/0luK9XB831qNyakbQPEf+CBf/ACj28O/9fOo/+l09fNnxm/5KfffVP/QRX0n/AMEC/wDlHt4d/wCvnUf/AEunr5r+M2f+FoX2fVP/AEEV+YeMv8al8/yR9/4d/wASr6L82em/sg/8nOeGP+us3/oiSv6C6/n0/ZB/5Oc8L/8AXWb/ANESV/QXS8K/+RZW/wCvj/8ASYnPxx/v0P8ACvzZ/9H+mH/go4P+Lv6Kf+oOn/o+Wvz2sv8AWgf9PK1+hX/BRz/kr2i/9ghP/R0tfnrY/wCtH/XytfzJxb/yUGI/xfoj9n4bV8tgv7r/AFP3A8YDH/BP7xB/2Il9/wCkD18D/wDBBQ4/Zn8YA/8AQ0zf+kltX3z4y/5R/a//ANiJff8ApA9fAv8AwQV/5No8X/8AY0Tf+klrX9LcN/8AIhfyPw/Ml/woKXXU/cpIg2WJqwCAuDUUZ/hqSscJRgqfPBanVNu4wOTnaM4qKZ32E7cADJ5r5c/bU8ceLPhn+zp4h+IHg1d15o9pc3uMoPlt7eWT+MMOqj+E/Q9K/Mb/AIJm/wDBXXw3+1LOvwy8f/6HrVqnkfxSb3TyUx+7tY0GWkP8WB9K6Y4WrXpSi103FUrU6dnJn7r2+ZV3DpjNWpSUi3KM+vtVazDNlt+9T93jGB2FcT8XPHGm/Df4d6x4z1STy49PsricHBPMUbP2Df3fQ1lhMK6UYwerFVqKUHKJ/Ob/AMF4P29Lv4YaZefspeH7TzpfE+i212115gXynivmyuxoG3cQdfMUc9OOeD/4N0f2lhqnh7WPgXrkv72z8prWbH/HyZ5LuZxtWMBNgHVn+bt6V/OH+3V8f5/jf+0/4v8AixrEvmQWGqXtva/Lj9wZ5GX7qIf+WndSfX2539jv45a9+zH8bdA+Inh19sWgyTyyDCncLmGRB95JCMGQ9FP4V+tvhWjUyZ4inH96lff+vM+GpZtWWK5JS0vr/X3H+oOwCbncbSfxqCO4dyc8CuX8IeINO8aeFbbxJpsnmQ3cakHBHI4PUA9fatOeJrll8tsKM7uK/DMdPEUsSowjq+nfX+mfe06kOTm6GynmuwAXjuc9Kh+0TlPMAx7V+EH/AAUt/wCCyHhD9lZB8O/h3B/aevXOQX3vD5O0QyA4ltJEbcrkfe4x61+nH7GOveOPG/wH0vxP8Qnzc33nMUxGNoWZwOYgAcjHYV7eKwOIcIz5mk/QjD4mnWi5Ra0PrRoA447Ui27LRCJBO+77vGKuVxyw1KeysUpsqGBjyDipNjY55qeitaNNUvgByZCIzsK04p6VJRWvM73FfW5WMRpptyc+9W6KU3zq0h8zKkduUNTbDUtFTTioR5FsDk2VkhKmm3EBlxjtVuisZYaDpuk9g5ne5S2NFHg1GPn4AzU11IEXmo7eYGuTkpe1VK43J2uBtuMk4BphskJyG/SrxelDZBr0PqVHrH8yNyh9m8pdyHNOjklU4C/rSyyJ/wAtT34oLrtykm3j0rnUaEXzQqcvldfqOLd7W0CS5EfMvFTRsJBla52+1yy0sGW+n+Tp909fwBrz/Xfjh8PPD6FtTvNoXr+7kPTPoh9KUs4wdL+LWX9elzshg6s/4cG/Q9kCgZGc5r88v28o9ek8EmDR1wo+YtlegWXs1eg+If20PgrpCb7W88+VTynlzrwM85MJFeLeK/23fgr4v0+bRL1fM3hlxmcdQR2hHr618bxRnuBxeFlRo1U5d/6R7mU5Xj6VaNZUnofkRGNNGgXEmqy77tI2VU2kfPt4ORx1r9Mv+CazeKTpd4uqRYtS7+WdydNsOOnP518seI774MX3iT+3Irf935m4jfP/AHs+n9K+xPAf7afwR+HHhiHQ7VPLaNVG3M5yQoHUwt6etfjvDNapHMOavK0U7n2ucPH18L7FRcr+h+rHlblBDYXGMY70GPYM56V8K+FP26Pg9rkA+23H2ZG5ztmfnj0hFe1+H/2jvhX4hwul6hvz/wBMph6esY9a/oHC8WZa0oOokfnFXKMZSdp039x70t0hOwd6bIXXkDFcrYeNdA1Rlisp9zMMj5W+vcCusicuOJOvtXozxWDxsbUayv5HDUpSp/FG3qVoZcy7H796vTRLIgQGoZLdFkEjD5uxqaPOOua0wuFVODw9WXNd3OV1JPoJDbiIf1qfoCDSZ7UjYQZY4rt5Y04WjokEUU4YVEu/PStDy/8AP+TVFJRvwDxWiOlcWCrRcWou9mU4KOwoGOKgaLPSp6K2qQVRWkCdissJBJ9aTyWq1RVUUqceWOw+ZkAjI7U7YalorR1GxXZEIyCfeqUlozPuFaVFYzgpx5XsS0m7kSxnHNRTQM64WrVFVVXtIOEtmOOmqKMcJiBZ6kUL95TmppfuVneftfYPWuWlOOGkqSfu/qXKTtctSKApZeT6VVbzCg3LtOORnNW2YgYHWvLvi/8AESx+E/wz1zxzqkm1dNsrm6Xgn5ooncDhW/u+hrurYaeI/cxvr2JlVUIc0j0IzEQMpXnBwc0+2JaLD8jbya/Cf/gmh/wUt8Yftm/tD+MvAN9B/wASvSJb/wAh9ycrA8IXgW8LdJO5Nfrn8ePi74a+BXwp1n4g+Krnyra1gneM7GOXWJ3VfkRzzsPJXFKlhK8atOhG6S0a8/67HP8AWmoSlM/j9/4Kr/F8/sx/8FFdI+J/gsfZGgvI7u9X/Wfagl/JJJHl0k8vzBGBuVfl6gev9bv7J/xusP2gP2fvDXxd0yPYddsLW8kt8k+Q08SyGPeUTft3feCjPoK/zrP24Pj+n7Tf7SGv+NtVbNhNd3K2Z/2Xmd4/upG38X8Q+tf1Df8ABEb463HgX/gnL8QPHepjzk8FX13HCOF/d2WmwyjojendWP16V91xHkDw+BoV4q0mt/8AgHhZfm1SrieSb0Z/SpHNOr4ePaSemc8etaoXclfLH7KP7QuhftQfBfRPixoI+e7gtzOvzfK8kSyMMtHFn7w5C4r6qUAAAV8CqdaE5+0d09j6eck20UJLVmbIqaKEoDmrVFRGjFS51uQtCnIhUEmvy0/4LFZb9iTWz7yf+ktxX6oXH+rr8sP+Cw//ACZHrf1k/wDSW4rqwlWTxSXZfqTXf7s8S/4IGjH/AAT18P8Atcaj/wCl09fNvxlG74n32PVP/QRX0p/wQO/5R66B/wBfGo/+ls9fNfxhz/wtC+9Mr/6CK/LvGibVelbt/kfeeHnxVX5fqz0j9kE/8ZO+GB/01m/9J5K/oNr+fH9kH/k5/wAMf9dZv/SeSv6Dq18KXfK63/Xx/wDpMDDjj/fof4F+cj//0v6Yv+Cjn/JXtF/7BCf+jpa/PWx/1o/6+Vr9Cv8Ago5/yV7Rf+wQn/o6Wvz1sf8AWj/r5Wv5k4t/5KDEf4v0R+0cNf8AIuh/hf6n7g+Mv+Uf2v8A/YiX3/pA9fAv/BBX/k2jxf8A9jRN/wCklrX314y/5R/a/wD9iJff+kD18C/8EFf+TaPF/wD2NE3/AKSWtf0tw3/yIX8vyPw/Mv8Af16s/chWxIMVaDAvVQL+8H50srEZNeTTxEqUdTulG7POvjXoj+JvhF4q8OL8w1HSL61I/wCu0Lp6j19R9a/zOPjsNe+D37TfiSTwc/8AY954Y8S3EO7C3HnfZJyc4feF3FRx82Md6/1AtQBn0uaJRnfGy/mDX+af/wAFFNJuNL/bs+KUl7xb/wDCSaswHHX7VJ6EnpX6pwVCnWc4zXRM+V4ijOMU4n9jv/BHP9vmL9q/4G2/hbxTH9n13w6kdgxzv89LeCDMvyQRIm55D8uSR6kVwf8AwXS/aePwx/Zsn+GHh20+16hrzfZmk8zy/JS5guoS2GjZW2kA43An1HWv5Mv+Ccvxv1/4AftX+FvFkQ26bqupWdlKMqcx3F1AWP3Hb7qdAAfcV/orQeHvBPxY0rTfHd7bfaFuLWOSA73TCP8AOvQr69xXm5rhKeCx6qrVX2/4B15NX/2eKm7tI/yzNSH2WyS1vrfztsai4k37cyDqcD1PpxVXzY5ppLTTH85rYKZRjbvD/dHPTHt1r+m7/g4W+H/hrw58UdOudEtfIL+G4JGG9my7X0wJ+ZjUn/BuV4N8M+N/EXxA/t/T/NlsYtMKyeay48xrsdFIHQD1r9Kw2eRp4KWNXw22sr/mfG1stqvH+2j326ev/Dfoj9HP+CA/7U+p/GL9n+9+E/iJN954SxI91kDzRfXFy6jYsSBdoUD7zZ68V7x/wVt/b+sP2N/g3Lpvhi3+2+INTBEEW8x+WYpLdjy8E0bbkkPUjH16fqJo3hnwt8Pbaa+sbT7FCuC53vJnnHcsep/Wv88L/go9+1xqH7XP7Tms/ERbXy9L0ryVjTzAeTBHC3PlRN96Pup/rX5bkXs82zZuvFRTb6dPn39Oh9bmLlSwvK9z5A0698SfEf4lx63rd751xfuxMvlqudqHjau0cAY6DNf6f3w88NW2g+CNI0qR/OEEIIOCud4z6n196/zKf2XtAuNU+Lmh6BJyjSTFOneKRj39vWv9P3TLcxaRZp6QoP8Ax0V6niO1gJKjh17q/wCG/U4eFadS0+Z6dDbtBK0jTO2VbG1cfdx1575rQqlanjZ6Vdr4Cl8Cf9X6n1aCiiitBhRRRQAUUUUAFFFFABUatIXKsuAOhz1qSqUzXL8W/HPXj+tDaST1+Q0hl9FJKuEGaqW0RV8N1HamatrFjoNibzWptkY6naTjjP8ACD6V8S/F79ujwR4Clm0jRk+13C7lVsyR8/MBw0LDqB3rx8wxGX4V/WMRO0u19fuR2YXLcTiX+5i2vwPuO4vfIYmRcIoyWz6e1eG+PP2j/hf4CEketaj5c6A4j8qU5IzxlY2HUV+NXxH/AGpPiV8VLyS0mf7Lp5YyLxE/IJwOIlbo1fPWqx6lqshmup/PzyRtC/yxX5rm/iNWvKnhNv67H1WB4X1TxD+4/U3xt/wUHtbcPB4d0bdFuIS6+0Yz1wNjQZ54NfHfxB/at+MfjiV10e7+yxMT8nlwPwc92iU9DXzromgazfXX2Hw3p3m3TDn96q/KcA/eOOuK+iPBP7HPxi8eMt3cJ9jjbBPMEnBwe0q+tfEPE5tmNbnk36f8BH1lHLMowtPmqWT8zxC91vxxrSbvE15kMdwby4+W7DCAeprmhGLq9FhcvudugxjPOO1fpr4Y/wCCd7ohbxHqG75DgeVjD8c/LcfWqPxS+APwk+DPgqaPVLvdqkiN5LbJh8xRgvAd1+8vf+VVmGTZhSpOpUuvwNKGb5c6ipUXd+SPzfk05NDvtskXkOykB927OT0xk4zVoKY1+0ufLyPvdf0qgdOuZZ2SV/tFxc3AjhGAmFfgd8dfWvpPW/2cPFHhr4a23jnXDtt5FRwPk6Mhf+GQnoP7tfPYTD4x809XE9r6/h6UlSbV2fN91daoFWRLv9zkf8sx/wDr6VMLe31FQ6ybm4P3cZ/lV+9vdLjhSAD93Im3v948fWvoPQf2WvEGsfDRfHeifMHUSgfKPlMe/wDilH8qWF9vOcowTubVcXRormqaXPmm6iadRpU0fmoDvxnb04zSDTtPtF3RSeS/93Bb9c1o6pp2paTqn9mavZ+a8SEsPMC/dOCflJ/nX1R+z/4D+E3xFk/szWW+yXZG0D99Jz8o/hZR1b1rWjha8qnspStJnJWxuG9m6zWi7anyhYXuvWN0l7ZvtWP+PCnAIwODnrXqvhn45fGbwxcCfR7/AHxr/D5UA9e7IfWvujxP/wAE9jqsQl8PeIdkTkt5X2TOVOMDc1wDxXzb41/YW+MHhNTd6Pd/a4x/0zhj9P70xPevflw/m1CHtYXt3/4KPGhmGU4mXLKS9H/wT2jwl/wUE8T6P5cfjLRvPjXO+X7Qi464+VICfTpX2b8Ov2ufhp46VVFx9llb+DbK/r38pR2r8N9d8OeLvC8L2vibTMqv3m85PbshPqK5WyttFWX7XCv2dx7s3tV4HifN8DK1Rt/j/wAEyxnDOXV1z0Vb0P6kNP1uy1SAXFi3mKenBH8wK0Zv32O1fzpeAf2hfH/w41i21DTp/tFtbEkx7Y13ZBHUxsRyfSv0i+Ef7d/hPxEUsPFX+jztn++/94/wQAdAK/S8l49oYy1LGvlv9x8NmHDdek37Jcy/E++44J0uB8uV9c1vjgYrlNB8TaT4lt0vdKn3xnPG1h/MD0rchgmgmLFsqfavvcJHDxjzYZ80ZbtO9j52VOcG41Ny20sauIyeT0FSVAYoZJRKRll6Gp66tbskKKKKBBRRRQAUUUUAFFFFAEFySsRIrLgVZWMmfunmtaZd0ZBrPt7OOCCQLz5rEn8RXL9XlPExnJXjb8QlqrIqazrem6Bps2tau/k21ujSO+C2FUFicAE9Aegr+KT/AILHf8FSNW+OfjfVf2aPg9a+RY6bey2M+oeYrea0TzwkeVNbIy7lcHiQ46Z71+i3/Bej9ulPhf8ADaH9njwNNjVtTdPNbb92N1ubdxiSFlPOOQ4Pp61+EX/BJD9k+w/aG/aitdR1o79P0a3OtanJyN09pPbvIuFljYZVjymQOyk1+lZDlsadN42tstjwMfio1r4eO6P6Kf8Agkd8GvCH7Ff7Gkf7QfxTm+xyaxYLqLS7Xk3xzWkUp+WF5sZ8o/wA+3avwa/4Kv8A/BS7xR+1z8QJvh74BTyfCWnyMqTZRvPEUkyq22S3ilTdHJ0ycfWuv/4LD/t8618X/HV/+yZ8Jl+w+CvBNy+nXS5WTzJbB54SczQJMMxOPuyMPcnmvwjs9KhupY4bWTyrC3ALHG7IX6/N0r6TJcg9pV+uVY6y1ttZf1ueFmOZqEFRctVuaJ/s8BUuVyEGFXJ+8OhyP5V/ZN/wTC+GEvw//wCCS/xRvNTi86HxHpGqaui525WbSUAGQzHt14+lfzX/ALAv7KXiX9sD4+af4U8KW/m6PY3cZvJd6riGKaESHDyxP9yTPykn056f2ift33Glfshf8E3rvwL4cO2GDR38Oxnk5DWM6D73mn+AdT/wKlxVmtOvOjgobJr8zuwuWyp0I4p72PwQ/wCCDf7TmsfD79qDVvg94iuP+JBq8dzLax7F/c3EktrDHFlY2kbCg/MWCnv61/bUGIjJQbiM8dOa/wAwb9j3xpP4P/ay+HdxcPt87xDpV9KcZ+X7ZFuHQ+nUflX+mz4d8Q2fiHw9YeIbI/ur2OORevRxkdQD+gr5DjbA08JioqOzVz1snxTr+0v0ZvLMRGHuF8sk4xnP8qnqCR4/NET/AO8Knr4vyueyyC4/1dflh/wWH/5Mj1v6yf8ApLcV+p9x/q6/LD/gsP8A8mR639ZP/SW4p4L/AHv5L8yK38M8U/4IHf8AKPXQP+vjUf8A0tnr5s+MP/JT776r/wCgivpP/ggd/wAo9dA/6+NR/wDS2evmv4w5/wCFoX3plf8A0EV+YeNX8ej6f5H3nh58VX0/VnpP7IX/ACc54X/66zf+k8lf0GV/Pj+yD/yc/wCGP+us3/pPJX9B1a+E6tldf/r4/wD0iBz8br/bYf4V+bP/0/6Yf+Cjh/4u/oo/6g6f+j5a/Pay/wBaP+vla/Qj/go5/wAlh0X/ALA6f+j5a/Paz/1g/wCvkV/MnFv/ACUGI/xfoj9n4c/5FUJf3T9wfGBz/wAE/vEH/YiX3/pA9fA3/BBQE/sz+MCf+hpm/wDSS2r738Xf8o/fEH/YiX3/AKQPXwV/wQV/5Nl8X/8AY0zf+klrX9LcN/8AIhfyPw7MX/wpxj01/M/chMHLY6Uxl80GnRnAYU6P7jfU15UqcJQS8md6fUqSEJA8S9Sp59K/zd/+CnAktv28viHbXbedE3iXUZSMbfl+1y5Xjn8a/wBIS4+SOWVugRjX+bV/wUz1KLVf25/ia8R5h8Raon5XUvsK/QuApVGpvqkfKcSV5xcIxejP7Ef+Cb37Lv7PPxE/ZJ8AeM9U8Of6amlabLv+2XP+tFvE+7AkUdT0xiv1/wBK0HSNE0+DSdMj8uG3RUjXJOAowBkkk/ia/OX/AIJJLNb/ALB/gR5P49H04j6G0hr9J7aXccE18tm2YR+vSp1tXJ6/oe1l+EpQopwj0P5G/wDg43kkg+I2mmNtoHhi24xn/l/mql/wbKJMPGPxUeR93mW+jcYxjBvan/4OPlJ+IWngf9Cxbf8ApfNTf+DZYY8W/E/PeDR/53lfoUaE3kUql9FY+Zw1WtLM5Qa9xM/rW1+0i1bSZdJueUlAB/Ag9sV+VP7c37KP7Nfw1/ZT8YePNP8AC3mSW0VszJ9tuhvLXMS9TK2PvZ6V+tXl+bIQegr4Q/4Kfwyy/sK+PbeDr5Fn/wClkFfnOR1sQ8xVaMrQukvv/wCHufWY3B068HGSP4F/2HIpIP2pvC+j3EvmJLcXmG27do8iY9B19Otf6Y9nPt0yBx2jX+Qr/MW/Zd1BtC/aN8N6gx+aCe6P5wyD0PrX+nNp/wC90C0PrEn8hX3HifTlCMHF68v+R5ORc0Kk6T2OgggEcjSA5zjj6VaqKGQSRhhUtfC8iikonuBRRRSAqXFx5NRJdMetJdqS2aYi44WvMr1qntOSD1KjF3u9iY3RBxT0nLnFRiEY5qxGqjgCumlh8RvORT5QMjbNyjn0pgudq7rgbP1pxHyE5xXAeM/HeheCNIOoeIZ9iL32sc8gfwq3qKeOx9HB0XOs1sVRpSqtRgrtncXF4sKmQHgcn6V8t/Gf9qvwh8J7KRc/a7sA4i+ePnDfxeUw6rXwB8XP23b7xRNceFfCL7ELKd+AfkK4bh4R3PrXwN4omttQ1r+2NTm+0XMz527SnLEntgdfavyLOePa8pOGDly/10Pu8l4RU5KeNXu9j3X4tfHzxl8b9XmmJ8qyLGQW3yHbyxDb9iE4DYxXhFnhdREFt++uGbGz7vJPqeOte/8Awp+AfjX4qXMcenxfZrZlDF90b/L8vGC6no1fqD8Fv2QvBnw6jj1TVV+03oAYnLphvlPQSsOor5GhgMwzWr7Wq20+ux9RmWZ5fllP2GFsvJan5r/Dr9nv4n/Ee9SKOX+zLd03GTbFNlcjjHmKeQevtX398Nf2C/Beh+Tq/ieb7fdgKxO2SLng/wAM5HUHtX3nY2yW0Sw28eI1GAc+nb1rQb7vIr9DyfgvA4dKrXhzP5n57jOI8VVbUJcqOQ8P+BPD3hWzS10uHYEAUfMx6ADux9K6DbJHxu2egxmpmuITuhQ4cA4+teCfHv4xaR8KPBdzfanN/pkkTiFdp5dkfachGXqvfivfx9bCYTDvEU0kl2/rc8ulTrYmqo3bkzjf2kP2i9C+E/hmazSP7Xqd1m3jTc0ezzFcCTPlsp2sPunr9K/EDVpte+KfjCca639o3t7M32dMLF5QdvlGV2q2GbvjOan8beKdV+J2uHxHqvz317di3gXgYWViQcgKv3j3Ar9Xv2TP2aLXwro9t4w8TR7tQmVJI+SMBgjD7shX7w7ivyx47G5zilST90+/jRwGVYW81er+o79lP9k628HaVF4o8Zfv7t4QVh5XYGCN95JWBwQe3Nd9+2hEunfBHyrbiHzljCexil7nnpX2lbo6W7CQYOD+VfGX7b3/ACREf9fSf+ipa++zDI6OW5TKNNWla7PksJjquJzGnOo+qPwxdIHRm2YVISgXP8XZs/0r92P2MbF5fgfYx3b+arRRcYxhTCnHH86/CbnypPTaa/er9i7/AJIjYf8AXKL/ANFJXw/AdGFbMGpo+x41m4YWEYmJ+0Z+y3ofxP0JrrSZPsl1B/pG3a0nm7A52ZMihdxON3avxf8AGXhCb4feJXs76x/si9tJT5TeZ5/nbGODgFlXcR36Yr+ma5E3lKYBuIUcdOK+U/2if2fdG+Mvh6W4aPy9Tt4yYW3MfmUOVGN6L95upr6Pi3hWKqvG4WLUl2PmsgziMJqli3em/wAD4w/Zr/a+nsdVs/BPjmHyraaJIfO3Bs+SjEnbHFnkgd6/XTTNUs9SsVurcfu26df6iv5nPGvw21bwTrzeFPGA2TSzSQWz8HmH73CMw6Y6n6V+hP7Hn7Uk1kf+EB+IsvlsvED7c9fMc8RR+mOrV5vDHF+Jo1PqmLl7r01X9afqe3xLkWGqU1isvXqk9/NH6j6t4b07XLZ7TWF3wSY3JkjOMEcqQeor5I+JX7GPgzx+GfSbv+z2/wBx5fT1mX0r7TtWW5Rb4N5gxlBjFPt7eQSFyu0H3zX6fi8mwWJpxj7K6lrfr9+x8Lg8yxWGTUKjX5fcfg18UP2PviR8LZG1Hw6n9qJHyGzFD1wOjSt/er5S1nTk0q53eLIfsc/+95mP++MjpX9SNzbeZheq96+a/iv+zN4C+JsDtqVvtmPRt8n+z2EijtX5hn3AMqTdTCxZ9Ll/FE3JLEPQ/Fj4c/GLxv8AD3VrfxJoVx9qs7QktDtjTfuBUfMyMRgtnpX6zfBD9s3wr8Qoo9M1lPsN1zxl5f7x/hhUdBX5z/Ff9kvxv8MzP4g0YbtLtsGQfuxkNgD70rN95uwr5X1C80W3uVTS/wB1fr/vN1+vy9M18xlWd5jk1T2Mpu3b/M+mxmAwOZUvaL4+jR/UpZTW0kIuYPmVuc8itATB/uc1+IPwJ/bB8SfD4W+i+ODvtCW3N8owPmPSOJj1I71+v3gL4gaH4/0ZNU0WTKt/stxyR/Eq+lftPD3FmDx0FSfuzPzfMsnxGEneS907iW5aMHIqst+7VOscjJtlO4/lSR2qjk19DUw2IXwzPI57y0Q8XRNTrLke9R+WoGBTmXYA1KjTxEHeo7o1k1bQcJeetT1nxtl9p4rQrtcovWJjBu2oUUUUixrnArkfGOsSeHPCureJCN62FjcXG3pnykLYzg+noa6yV/LQtXhP7StxMv7Ofjuez/1i+HtT2/7wtpMdfetKMoTqxoN+9dMbdotn+d5/wUX+KGofFT9sbxN4vv8A95ELq8ghh4GzNzI6/MFXOM9xzX7c+C7a1/Ya/wCCPtz8SvDv+ka58S4FZpOV8hNV0xgRhvOR9rRg/dTP+z3/AJrvF093qXxd1L+1+Zf7dbf0+75nP3cCv6Fv+CrN9qNh/wAEw/gfpmlcWj6Z4f3dO9tcDvz0r9rzinCFDCYKmrXtc+JpzX1qc/trqfzPXV7qlzh/PzNqK/bLpto/eSvw3HRc/wCzgD0r2P8AZ/8Agjr/AO0T8UdD+EXhNPNlvru3juuVHkxPKkUjfO8YbbvHAYE9vWvG7YNMhiH3/uJ/T/Jr+t7/AIN1P2UtHsNI1r9obUkzfi4lsAct92SO1m7S7eo/ufj2r389xEMryxyXxbI8zAZZLF4yVaq9E9fM/bX9h39jvwD+xF8F7Pw9pcX72KyS41C+zJzIkSLJ+6Ms2MiPPyHHYe/80H/Bc7/goho3xt8Rn9m/4VW3m2GjXge/v97DE1s9xE6+VNbo33XDZVyOwyea+0P+C6f/AAUf1jwVpMn7LHwuk8vVL2b/AE6bCnZan7TbTLtltyp7HKyBvT1r+Q6Gx1jVdZg07RR52qXl4kf8K+dvbB+9hV3MR6Yr87yTIZ4xrM8XK8U7/d/Xoe3n2NrxjHB4KSi/S/6nvH7Ifgi6+In7RvgTw5pc/nu/ifSle42hfKiNzECNhZd2M54OT0r+/b9rP4yN+xt8AvCurXi/abe21jTtHznZvVkkPmcJKRxH939a/JT/AII2f8Eob/4Ua/YftXfEZvKn1TTQ8VhgHbJOYJ1fzYrpgdpQrgxgHrx0r5x/4OE/2ybXxLrWlfsuaG2BYXsF/OcHiW2kuYD96Edm7SEex61ycQqGf4/9yvdgrfIvKKFfL6U5VpXcndf1/Xof1m/CnxxafEn4baF47s1/c6tYwXicnpKgYdVU9/7o+leiJKW5r8cP+CJXxRn+JX7HFlY3T7z4fe300cY4htYT2VfX3+tfsYg+XNfDYqg6OKVK2iufS4St7WnzsZcS9Fx1r8tv+CxJx+xHrf1k/wDSW4r9Rpxkg1+XH/BYn/kyPW/rJ/6S3FYZcpfX5KW2ljSvb2Z4p/wQNJb/AIJ7eHh63Go/+ls9fNvxlO34n32PVP8A0EV9I/8ABAv/AJR7eHf+vnUf/S6evmv4zZ/4WhfZ9U/9BFfnXjPCLrUr/wBaI+78O/jq+n6s9L/ZBH/GTvhg/wDTWb/0nkr+g2v59P2Qf+TnPC//AF1m/wDRElf0F0eFStllb/r4/wD0mBhxx/v0P8K/Nn//1P6YP+Cjn/JYdF/7A6f+j5a/Paz/ANYP+vkV+hP/AAUc/wCSw6L/ANgdP/R8tfntZ/6wf9fIr+ZeLf8AkoMR/i/RH7Pw7/yKY/4T9v8Axd/yj98Qf9iJff8ApA9fBX/BBX/k2Xxf/wBjTN/6SWtfevi7/lH74g/7ES+/9IHr4K/4IK/8my+L/wDsaZv/AEkta/pXhr/kQv5H4dmP/I1j/wBvfmfuIOhp8OSCKiHU1Ytlzk15NL35KKO/ozm/Fup22j+GtRvrg4+z2k0x6/dRCT2PpX+Zv+2bqVt4p/be+I1zp7+Y+p+LdQiRMEY866fByQOmfb8K/wBGf9rXxjb/AA+/Z08beKbqfyVg0HUinylv3i20rDoG9O4xX+avc/E1I/2j1+KuoWP9opJ4hTUXHmeTuX7QJD0Xjgf3e/Sv1zw9wvN7SPZHyufR5pQ0P9Eb/gnJ4UvvCv7EXw30m6TEg8PaYTyO1tGOxPp619qwRTBwwXp71/K14J/4ONfhx8NfCGmeBpfAXnf2XaxWwb+1JF/1Shen9nt6ep+tdUf+Dm74aD5W+HmP+4tL/wDK6vjs54PxeJx8sQ4vfpax7GHx9OEY0nvY8g/4OM5Rc/F3RtIk/difwtbkv1xi+nPT8PWsn/g2O1O2vPFfxNaM8tDpIHXs17X5wf8ABSn/AIKK6L+3v490/wAeeH9F/sOHTtJj0x7f7S1zvZJ5Jt25reAjiQDGD0684qX/AIJa/wDBSnwp+wLqfiGbVvDH9qDV1tlz9teD/Umc9ref/nr7dPy/So5ZiIcPShJa6aHiUsywscXNn+gkk8UDSbzymMj618t/tneHrbx7+zJ4r8GpP5T3cVsC2wtjbcRP0yvp61+FN1/wcseC5mK6d8MTcgfeP9tOmPz06sTxT/wcd+Cte0q50DUPhr9l80Lh/wC2Xk6EN0Gnj09a/M8FkGLw9WnGzSv+t979D062d0FCUovY/lb8Fa/H4c+IkHiuVPmtJph5GevDJ97B9c9Pav8AUZ8KXkd94ZspFGFMEZ/MCv8AK68Z6pYeIPGU/iTS7X7HDcOW2bzJ2x1IB6+1f6Y37G3j23+KX7OPh/xfA25blZkzjH+plZP7q/3fSvpvEKnW9lTVRa2/yPB4ZxrrYibfU+q7NBHCFB3D1q3Ve3QRJ5dWK+FlskfZIKKKKkZRuiQaSAdTUs8ZYg09I8KBXLQpv6xKbNL6DO+TTJJTGCUG4jtRKxRlr58+Nvxr0T4SaM2rXx3SHovzDoVHUI4/irnzzO6WAouc3qdGGws8RNU6au2bHxi+M/h34W6E+o6i26XGY48MNxyoPIRgOGr8LPjD8dvEnx1mkuLo/Y7ZD8sfyyZ+6OoRD/Dmsj4lfE2b4reJrjxf4gkxZxbSi49gh5VVPUDtVb4c/CXXPjprUdv4LTZaqSGbKnsezuh6qa/BM4zvF5vi3Gm3yvZH6blGU4bL6Xtq/wAXW/Q4Pw3Ct7PFpOh6d9quZm8s/vdm1XOC3zYBxkcZ5r9Of2c/2MNNBt/Gvii58xn2yiDYRtJ2Njck3OOR0r61+Dv7LnhL4bxwak0W68EbIzbnH3mDdPNYdhX1NBYwW0Qii4AGMe1fS5B4e4hyVbFKy7M8nPOLlUi6OE0Xc5/RtA07QrJLPTY9kaAKBkngDHck1uosZIZhyKsiJMbFpwjUDAr9TweTU6LiqVuVH5/Urzldy3EBYv14x0qOQEZwck9qe37tc1g395b6ZFLf3D4VFZmOOw5PTNb5tjaVCHLP/hiaFOUmcf8AEbxlpPgTwze6/fv5ckUEhjGCdzhWYDgNjJHUjFfz8fFr4ka98Y/FWoa7rj4FrNIttb4X51Viy/OqpjJbHIPqa93/AGzfjVN8QvHQ8D6bLizsH+0scdTDJIuOUUjIP941w/7N/wAIbn4r/ES01KNcWlm6M5yOdjox/jQ9G7Zr8PzvMp47ErDUfgP1bIsvhgcHLGVt3+B9RfsQfAqbX7lPiR4wtPKjSLyobffu+Y+XIr70cdORgrX602tpHbQrBF8ojGF74x0rI8O6baeHNPg0azXEcMYUHJ7ADuT6etdFkMDt5Jr9R4WyPCYLD8zs5s/O83zGeLxDmthIzIIZFkfecHnGK+Lv23v+SIj/AK+k/wDRUtfaXk+Wjk/3TXxb+29/yREf9fSf+ipanilzeW1PaLWzNcl/3yl6o/DH/lg/+6a/en9i7/kiNh/1yi/9FJX4K8+U/ptNfvV+xd/yRGw/65Rf+ikr808PP+Rgfd8c/wC7wPrVGAZM/wBypHihkYSEc4xn2piKDtz/AHKn4xjFfvEabm5RqK8D8vbdlbc+Vf2jvgFpPxU8F3VtbjydSU7rafltjO6FjtLqpyoxz07V+D3izR9U0TXJdN1dfKuLPGyTIbO8eikjp9a/qDunEcBbr7V+Y37aH7PaaxpEnjTQV/fIMuufeNRy0gH5CvyXjTh6hRl9awq82fa8NZ26d8NXej2Nf9iP9pCfxrpC+E/EvyXceRG3Xd80h6JGAMADqa/SlXL/AE9a/lu8NeKLr4fXieI7H5LmxJIXg53ZXqQw6H0Nf0M/BP4mWXxA8E22oQSZlIO8YPGGI/uqO1dvA3FMPYyw+Keqen+RzcR5O6UvbwWkj3VnKkADOaa6RuCxGTUEd0rfu36jqalwT+Nfp0a9HE0ny6o+MalCRi6rplrqtq9ldr5kUmAy5IzjnqCDXwX8ef2M/Dviq1l1vwd/ol6oBC/NJn7o6vMFHAPav0MiT5uamMCAEEda+QzHhLD5jScqi9/oeng8zrYaopU3ofzHeJfDuueBr59A8e2m/PAPmKPf/lmW9R3rsvhL8S/Hfwr1xdb0eX7Vp4OWhxGmBhh95lZurelfuD8XvgP4Y+KemzRarFidwNsm5+MFewdQeBX4bfE74U+P/gb4qm06/j8zS5MYbMY42g9A7t1avxzM8gx2VVnOCaXT+uh+j4LOMNmNL2NRWl2/yP3G+C/x30D4r6THd6efLmHDR/Me7DqUUdq9/WeNiFB5NfzL+CvG2s/CrW4vEPhGTcikllwvoR1dW7se1ft98Avjp4f+Mfh2No3xeJneuG7sw67EHRa+44S43quP1fFu8r2V/wAvI+Xzvh54V+0h8P5H1Wju3+sXb+OakkIKcVUs53kj8uYfMvf1qy/3a/XIVo1qXtI7NHyNTR2KMefMxWuOgrKjH7wGtUdBXnZe7qXqVJWsLRRRXoEkFyu+IrXmPj7SJ/EPw78Q+G2Xb9ssLu3U5znzImXPUevTIr1NjgVnOu6Vpj8qKCD3rFUnGvDER3WgpxcoNH+Y3+134I1D4c/tf+L/AApImRb397KWyOFjuGXONzenTNf0O/tReDB8ev8AgjB4C1nQpd0vhfStMeX5cbhaadM5+8UAzu7Bvxrc/wCC5P8AwTM8UeO7xv2o/g8nnTRgtqMWUXdGWuLiU7p7hQOMD5I8+npXz7/wRZ/bV0zSIta/Yi+PD/2dY67HcWtmMGbPnJBZpH/o8JYZy3zNKMdz0Nfq2Izn6xhKVan8cD5evlj+sxa6n8wumzXs9mup28eNjiA8jhuufw9K/wBAL/glPcjwH/wTkg8UafHi4j0pNQkOf9ZIlhG2edwXJXoBgelfzaf8FUP+CZvxD/ZY+JWqfErwMP7S8Ja3PNeL/q4fI8+SZlX95cSyvtjj67RnPTNf0g/8Eabiy+JX7Bdp4PvPl22kVi/U8GyhU9Nv971/GnxLnFHM8upRctVuFGjVpYx04LRn8Wn7YXxG1X4j/tT+P/GWot513d6/fxCLhdkMszMVyFUHk9cZr9jv+CBX7FcXxN+KV/8AtDfEJPtOn6BLJZ2VnnZ5UsTWtxFJ5kcys23JG1kIPc9q+G/+Cq37LHjD9lz9pvX/ABFp9n5mk6zfXNys3mINqzTy87TLI5wqZxgV/Rr/AMER9bfR/wDgnj4s8W6K/wBrmS5muBx5e110+Fh94EHkDtj2ruzLM6NHJ6OHwsruSsclTLpyzNNbHLf8Fev+Csdl8CvDd98B/hFJs1xZmt7m6wT9nQCeF12TWro+0hWyHyegPU1/Gh4k8ZeIfH3im88f+M73+2LzUZn2TeWtvkzNvHyoFHUk/dHWvcP2wvF8fif9qLx94v8AFL5muNZv96Yx9+ZmIygA6k9BXsH/AAT+/Yr8aftm/HHRLTQLfyfD+mzwTzvvRv3cM0RbhpoZPuSdsn8a9DLaOGyTK/a1HepUVzbFYmdeq6DXw6H9eP8AwQc8D3ngz9jxbi9TYdXuYb4cg4ElpAMcMfT2+lft/nB46V5B8HPh74c+Dfw80X4W6LwmlWcMI+9z5KhM/Mz+nTca9aVsrur8ex2KjXxLqLq2fT4bDujRURzrkfrX5Zf8FjP+TJdb+sn/AKS3FfqeTmvyw/4LGf8AJkut/WT/ANJbilhYpYmLNazvA8R/4IF/8o9vDv8A186j/wCl09fNnxm/5KfffVP/AEEV9J/8EC/+Ue3h3/r51H/0unr5r+M2f+FoX2fVP/QRX5d4y/xqXz/JH3vh3/Eq+i/Nnpv7IP8Ayc54Y/66zf8AoiSv6C6/n0/ZB/5Oc8L/APXWb/0RJX9BdLwr/wCRZW/6+P8A9Jic/HH+/Q/wr82f/9X+mD/go5/yWHRf+wOn/o+Wvz2s/wDWD/r5FfoT/wAFHP8AksOi/wDYHT/0fLX57Wf+sH/XyK/mXi3/AJKDEf4v0R+z8O/8imP+E/b/AMXf8o/fEH/YiX3/AKQPXwV/wQV/5Nl8X/8AY0zf+klrX3r4u/5R++IP+xEvv/SB6+Cv+CCv/Jsvi/8A7Gmb/wBJLWv6V4a/5EL+R+HZj/yNY/8Ab35n7jogJOasRgRrxTE6ZqTissLRioKdtTtndbHyP+238Fte/aG/Z+1z4V6BqL6VPqFvOn2iO1+1nEkEsW0R7kycuD97PGO9fyA/EX/giV4k+FnhK88a+PviRJpltYh4wb7QvsiSFEZwweS7AGQp456E9q/uxmnhgA3EZJxgmvx3/wCC1/wh+IHxX/Y+1GL4f3NxDJaO1xKLV5Fdoo7W63D92jkg7h1wK+r4YzmeBxL960XueXmOE9rTTtqtj+Yb4Cf8Ei7L9oK2urvwR8S4rowlywstLF7udQpx+7uxgneOPp617Fb/APBA/wCLDPcibxJqkYhLBC3hub95t6Y/0nv2xmvzL/ZI/ar+NH7K/wAXPDw8Ka3qdvaW+p2mnalZT3M6LLN50fmExpLEGyqBcvyehFf6NnwL8dt8V/g54a+IF5E0Ump2FrdsCu0bpY1c4yzHHP8AeP1r6HiTiHMcLFVMLU5YP02+48jK8PCtVk66u0f52v7Xv7EXjX9kPWY/DniS/u5ZbizW9Q3WmvYsUeVouFd3yMqfm6du1Rfsc/sQ+Lv20Nf1bRvCtzefZ9BWB5bqy0579G+0iTG7y3QJgxkcscn0xX7nf8HE+T8UtKuGWJmTwxbhEIy5H26foK/ns/Z2/al+LX7Ktj4k0v4Ga3f6UNfS1WeWO5mgZPs7Oy4a3kixkuw5B69ua+yyvH4nHZI3GSc9Dyczw+Ap1WqcPeb6f8Ofqxqf/BBb4mT2lxc6Z4g1RygXZs8OzPuJPOP9JPTv1rW1H/ggT8WJGvtLk8XalJHp6RNFIPDUoE5l5YL/AKT/AAdDgn8K9t/4Jh/8FB/2j/g58F/ib8Vv2i9a1bxJpGmQ6Y+kTa5cXV2JnluJo5xA1zOiMVLIH8tgQAN3QV6B+wP/AMFwvjp+0R+0fovwh8WaHbz22oTTrKtrbXDTKqwzSrw95IBnYOqnj86+PxFTN3OVSVS8Vtov8j1aWV0vZwXLp1PljR/+Dff4ia3ZQpceKNS0t7ssH8zw5M2wIeMhrpeuPbrX9SH/AATv+AniX9mH9l7RPg94m1CXU5tIku2NxNafYmk+0XEso/dFn24DgfeOevevwE+K3/BbH48eGP2yrrwtp9lbw+G38pY7a6juFeIrahmLILwRrufkYHPXrX9aem3N1qPh+3vbkKHljVyFzj5sHjNfJcT4nMsTRUsbNu2y0/RHdlGAw9GvJ0VodDCHDZdsg9OOlWqgjZSdo7VPXhzVrHt3T2CiiioAzrybYwFV0vhjD4z6Zq/PbRTOsjk/L2HfNctq9xp+jI2oapOkCLjl2CqOg749a8PH1cThXLEc3uGiipWSWpzfxJ+IWneBfDM+tahJEDGAUjklWIvllBwSD0znoa/n0+InxE1r4t+KJ/EevSDT7GIjEL7Sp4Cn95tQ9VB/Svav2nPjzrvxa8YyeHPD900On6dg5jdlWTzVQ/wu6nDL6DFfM+h+HvE/xR1SLwJ4b02ZlYkPLHC5HIL/AHlDf3T/AA1+HZ/neIzTEeyjLQ/SeHssjhqTr1Fr+R2Hwz+H3/C3PEq+HtMhkFm5w0kKNOq/Kx5KkdSvrX70fB74ReHvhB4cj0XRVycfM/zDOSW6Mz4xu9a4P9nf4AeFPhH4bhEdmn20g+Y5jTcfmYjnYh6N3r6pjeGVAdoHswxX6DwXwtTw8I16zXP2Z83n+dTxNV04v3F+I0gSAK7A85qYFMbCRj61UMqBzygH1qrcazpNkpa7niQf7TqP5mv1CriaMItzmrep8yqUm/dRYgi23DMJARg8VM06K20kfnXE33xI8Aaam/UdWsrZOm5540GfTJYVx198dPg9ZktL4j0wgf8AT5B/8XXzeIzihhqKdKqt31/zOqOEqz1cH9x7FdzqsI8obyxAIB6A96+C/wBs74vT+CfDH/CMWESvJfpt8zzFBTzBIn3SrZxjPUV7Lq37T3wgt7GZ9M1mzuZoo2YJDcQux2g9hLnFfj5+0H8Ybj4s+MpDG2YIXOwZOMK7EfxuP4u1fnXHfE9CVFKjO7e9j6bhzJqk68ZVYe6jwaysLvVddstDdWmn1K8i33Kr91JWCkbBx3znIr+gL9nn4Q6V8MfA1pDaTebJcxJIzlWTlkUEYLt6V+OnwG8S/D/wv8QYtV+IMBntIIMoFSNyJVdCpxIQOgPfNfotqP7evwb0dIrCwtb91TAUIkBAA47TivC4UzLLIU/b4hrn6Hv8UU8bNxwmHT9nv5H3qbdtuGbcfpViBNnWvzsvf+CgngggDTbC+DessUe39J+tczef8FD9GgB2WMhx/wBMh/8AJFfcri3KoyTU9j42PD+N/kP1BnfZCxQbiRjAr4q/bd8w/BTy1UkrcKx/CKWvnV/+Cjlo25bewfdg/eiGP0uK8n+Of7W//C1fAkei28OwuyswC452uD0lb+96V5/EfGuCxWFnSp7tHo5TkGLhioScdEz4VuAYbcYG5pI923uM1+837FsgHwS08SDb+6i6/wDXJK/Bu7W4KrcwqWZYTlcE9Pavu74FftnaR8MvAMPhzVrV2eEInyoDwqKveZfT0r4zgvNKWExTr1Nj7nifKa2KwaVNXkj9rzLEqqFIzjgZqCSYsNoFfmXH/wAFFvAUZRrmwvHyo4iijLD87jpW7af8FEvhlNgNp2pj/tjF/wDJFfp0+OsJWi4qfKtj80XD+MW0D9DJGdVLKpPtWbfWS3dubK8UzRycMBkdOe1fD6ft+fCm4xFLa6jArdXZIVA/Hz+K6O4/bl+CluiSNPcd/wCKD/4/XmzzzLKkXCVVWYf2LjYtP2bufmj+178MJ/hh8S7W5srGe7sbpm3sI3RE2xoeXy3Ut7V1H7IXxKu/AXj2PQLy4SW3uu7skQXCyN3BzyfWvpv9ob42/BP4u/Dm9i0lml1Tav2ct5DEHem77ru33VPSvyxRr3Rx/wAJDaztFPD02sVPPy9sHofWvzXN/q9HFxlgZe7vofd4OFXEZfKGLi+Zaan9QulvBfwiaJ1dSM5U5H6VsjantXxl+zr+0H4H1fwJaRatqtvFdfOHNxPGvRmxndIT0FfTVr458J6kQtjqdncf9c50f+TGv23Is5wMcFCbmud7n5pjcFWhUcXF2OxkaLBywHvTomjYEIwOfeqf2u0miygU56HAIpFITldo+lfQvH4VyVSDTdjjcWlaxckUhPvYP96vMfH/AID0b4haFNouqjPmAYcbuOQeisvp613zzNszgt7dah+0BRxEw+i152ZVaOLj7OpG6NaE3SkpQdmfz1/FP4U3/wAE/GEmhlJr+zujzcGFoUiAUNySXByWxyRXl/w68Wa54B8Xr4j8OMREhyyhQwPykfeZWxyT2r+gT4tfBjwt8UPDlzY31vGlxOFCy7EDLhlP3ijEcDFfg58Q/h94r+D3imfQNZtX+xS42ylH28AMfmZUXqwHSvxPiLJ5YLEqpRjaLP0jLszjmNFwqfEvyP3c+CHxesvij4bTVoViSSMYZEmWU9WXsFx930r39P3qZ6Zr+ef4BfGvW/gh4jggmuGn0u4JMmXZlXAcj+NF+83ev3k8FeMNN8aaFFq2kyq6yA/dYHGCR2LelfpfBefKvR+r1ZXl+Z8hneUyw0+dR91nTK22YLWsOgqgIA7rIDzzWgOOK+0wtCVLm5tmz59yuFFFFdQiC5JERIGarwSNLDh1xkYxVuX/AFZA6kYGfWqiyeVEFlxu/wBmpT5anPKVolp+6VriwS7he1uTugdChTGOCMdRz0r8vv2gf2OP+CcPjbxU/ij40/2RY6/bOZYrm81ye0aKRGZwxiF3Gh2uScEY4weK+6/jjd+Jrb4QeK7rwa041dNGv2sfJL7/ALQIHMe3Z8+7fjG35s9Oa/z1PjJqn/BQLx58bta0XxtD45v5vNuAsUS6jKv+sIwBJuOMk9u9fT5VgsTiZclGVl1Pn8dmSp1FKC95H9w+l/Gj9hzwn8L4vhPq3xN8IXelQW4sxE2u2sR8oR+Xtz9oL/dyM7s++aT4cftJfsG/CTRn0D4ffEPwfp9q8vmFP+EitpecBesk7noo71/Bna/sp/tx65dMZ/AvjMxO/wAkk+l3+3noSfKI/Gui/wCGG/22f+hV17/wBv8A/wCMV9VDhOjNXlXSfW//AADmo5pUlN1Huf3MfF/49f8ABPz46eHZvC/xJ+IPg+9sriNoZF/4SO3iyjKykZiuEPRj3FeOfszL/wAE6v2Y/BnifwF8OPi34Mj0nxJdXNwlqNetmMC3ESRBN8l5K77VUDOVJ+tfxkN+w5+2tg+Z4U17bjnFjfdP+/FYMv7HH7b2n3IGn+A/Ek8a87l0u/Y8ehEIqp8I0nGK+sqy2NIYtxqOqtz+lXwX/wAEgP2IvHn7Q+tfHK++LGg+KrXWtVm1JNNtJgvlmaVZQnnQaiS2B8udgznOOlf0DfBv4GfCr4IeHIdK+E9gILVkUBkmlnBG1RuzI8nZR3r/ADrYrf8Aby+D2pW1pp+meO9Hn8xAiQwahbx7s4AIAU4yvOOwr+8n/gmrrPxS1j9krwtqXxfe8bW59PtHlN6ZTLua3jLZ847/AL2c57+9ePxRh8RTUYSxHOullaxvgYYZ1J15r35fifdMcW+7PmKd20/N6/hWtGAF2ioYYiG8xzkkVc2BcnjpXwmGpKkmmtbn0E5qS0G1+WP/AAWM/wCTJtb+sn/pLcV+nsrzGT0FfmB/wWK/5Mj1v6yf+ktxW+XYqNXFKKVrHPUTUNTxT/ggXz/wT28O/wDXxqP/AKWz181/Gb/kp999U/8AQRX0n/wQL/5R7eHf+vnUf/S6evmv4zZ/4WhffVP/AEEV+beM38Wl8/yR994d/wASr6L82em/sg/8nOeGP+us3/oiSv6C6/n0/ZB/5Oc8L/8AXWb/ANESV/QXS8K/+RZW/wCvj/8ASYnPxx/v0P8ACvzZ/9b+mD/go5/yWHRf+wOn/o+Wvz2s/wDWD/r5FfoT/wAFHP8AksOi/wDYHT/0fLX57Wf+sH/XyK/mXi3/AJKDEf4v0R+z8O/8imP+E/b/AMXf8o/fEH/YiX3/AKQPXwV/wQV/5Nl8X/8AY0zf+klrX3r4u/5R++IP+xEvv/SB6+Cv+CCv/Jsvi/8A7Gmb/wBJLWv6V4a/5EL+R+HZj/yNY/8Ab35n7mJ0p9Uw5DfjVxZYyMGuTBYuDjyN6o7p9ytLGkkgDKrcdxk1zmr6No/i/Rr7w94mtoLqwlWSCWKVFdCrKVOVcFfukjkV0lyzpskhXflgpwMkA1+Mf/BW/wD4KOWP7GPw7bwv4Hkhl8T6tH8iIQzokyXCbsR3EMoZXjHODj616uFw0q9ZRp7sVSaUHc/FL/gur+zp+xl8DdPs/E3wdurbTfHB1KG/Nhpz2MKYBuWMkkUEaTZEqKpbPTAznFftr/wRQ/aUvPjl+wzp+qeLriOE+Fo4NKmnkcqi/ZrOB2ZneR8AbiSTt47Cv42vAXgD9oD/AIKb/tG2ekavdahqWpaldJPcTu9xNBa2MlwokQsy3LR7DNnByoByff8Af39sLxf4O/4JMfsQ2/7Lfwq1JovEniN4vt1xBMinfcWktpIQ0JtZM7oVILRk+vOAPsc0wfNhKeCk7y6+R8lhcZLD16rcfdkz5y/4L+fG34VeNPjlpS+BdetNbuNP8OxWkosbqG5hWaO9nLJII3JDAHODggYJFflF+wl+xv8AEX9sn48HwP4eht4dKstjalI6yLhZYZWi2lYpl+9HzvA9ua+ZfGPgr4gxwp4/8d/2jdf2pEt+s995jmWOc8OrSD5lJOQdxBPc1/QX/wAG9fjjwr8PH+K3xL+IlxbWMVvb6N5Ulw6REfPdxnaZWUfxDOGr6qlgamS5PalLmlNLboePXxdL65GrJXTep9Cf8Fiv2dvBvwR/Zo+H37LvwRhNpqesSamsa26pH9oaOS2uW+0CCNGcKCxTEZx+Zo8J/st/C3/gj78Fv+GoPiDY2usfEO7jVrJ44orixhMcnktkyR2lwu6G5A+WQ5Yf3eD8ifBX9on4rf8ABSL/AIKW+E7vXI59T8L+Fri6ISATTQRi5sZEJYNJcRLueEY+7kjuen6vf8HCGqeG9I/ZS0/RNV2rLdrMsC/KD+7msy2ASD09Aa+ThmVa1PLd9fe+etv66H1mKr/u5zp7WP5B/iv8YLz9of4y3Hj6CGCy1XUCoji0xTHF+7jCHaA8j/dXJwTzntX+m94EWV/B2nibcW8iPO/r0FfwJ/8ABIL9he4/ah/aIXUL21A0TwiVlunKHZIL2G4VckwSI2HTuVx7mv8AQKtRFZ6WiWgG2FQq46ccdsVhxhjI1KipNWstSMhpSjQdSXVlq0ZmdtwxWhVG22ly6ng1er42c1J3ierSi1GzCo5ZREMkFvpTyQCB61DGxaRlbtipeqSW5qkY+t61aaNaNf3jbI4+uSB1wO5HrX4f/tJftR+OPGHiqTQPC9w9vpi4DEPImflU/wAEpX7wPavrP9t748yeHLC08LeFrj9/d+YJNj8jb5bDOxwfXqDX5IWs8s9k8OoHdcufvHk9c9Tz0r8I454pr/XJ4GD9xfiffcLZH7W1ecdDO1M3tv5jaYf39xjDnOCV65I5OK+mfgP+0B4f+C1s0uraHPdameksNsrp1b+IyI33WxXiOh6T4m1xotP8OaXJqDwEmVkgeURhs4yVBxnB616jefsz/HhVGtrYhrc/weVcE+nTy8dfevhsKqlKX1iK1Wp9jm/sY0vYN7n0vqP/AAUM8YSoU0Cxs0kftcROAuP92fjPNeZa9+3F8eby0MdstgjnvB9oHp6TV88eJfhZ8SvDxhvbvQLgRTFg7pay4XbjuVAGSa4a+sSmY7K6EcvdHfBH4Dmt6+f5nOXPGTseXhMswSpXlBOR9EyftU/G/UbII+rsk2csIricHH/f0muK1T48/FrU8pqOuagoPXbczAfq5rx+20W7VhI5nZureTknH5dK9y+HFx8JRdpa+N47wcgEssPsP+Wn41zLNcbVdpzf3m7jQp6wpL7jzfUvGviPUUB1HXbucMQdk9yzLk98FutZJubu7Ijju4pnbohkLNz7Zr9SfCHw1/Y98TpEGnhhKgSf6Q1koJGOPunrnpX0ZZfAn9nbSNHfxLpejaZeRQxF/NFvbScKN2cqgHbPWvepZHiMVh/be1WnmebVz2FOXI6TXyPwguIdT0yRGYpA8jBWC5VyrdfTitOxsptY1IWOiWs81wBlsJuJIODjbknk16L+0Trng3WfiRdr4PgFqlkkn7uNURMpIx4CE89B2r69/YQ+ES+KY5PHWvWoaMNhBJHkHiNx95T7968bLcmnjq/sG7s9KWfrDU/aOFj4qf4b/EG8BWx8Pao8qnnbaSnI9RhScVeT4L/Fm6iX7N4X1ISdjLZTY/MJmv6JLLwv4dt5Gkt7CCIr8uREo/pWuNN05Puwxj/gIr9BoeHVHl9+dmfO4njSdWXN7M/nfsfgV8eHTy38Nzn62dwf/ZKtt+z58b3/ANZ4XlP/AG5T/wDxFf0Lm1gUf6OiBvp2pPIl/up+Vbf8Q6wv/PxnL/rbW6QR/PRF+zf8YpmZZvDM8Y2nBWznHP8A37rG1v4H/EXwNox1/wAS6XPHaiQJtEEobJBPR0UYwD3r+jNLZicSBAK+Pv23LeK3+DkskcghIuByDt/5Zy15md8CwwmCliac7pdzowXFNadeMOVK5+IB1IytI1nayjYrKVdOT+Ar0zwn8DPiB4u0P/hJtM0SS4gPzBRbSMxyobgBCOh9a8Zs4W+xS30V87SG4wQJc/KRmv3m/Y8EE/wgsgzKzbIsknP/ACySvjuDstljMTLDydkfRZnntfDYf2iVz8jpP2evjJdSia28KXCRhcD/AEGcN+kdVZ/gV8bLP7vhW8bH92xnP/slf0ZLZ7FBXywtNNnE3XyjX6U/DCm3eVVo+U/1tq3+BH83j/CH4yzn7LeeFNQjibhmNjOAMe5TFVLj4OeO4IyLrw5qL47LZyH+aV/SU2lWUg2zRwsp6jaDUB8MeHpfvWcB+sa/4VP/ABCyD+GsWuLpdYH81tn4K8daZOB/wjuqog7m0kA/kK5rUNH1vTJH1DxHp15b2HHyvCyMccdGAXqRX9Mt74I8JyQsJdPtmB6jyk/+JrwP4xfAXwh8QvBk9lp1jDDMANvlxIp+8vojHoK8XNfD2thk/ZSvpc9DA8aU1NU6sNGfgNpN3caRHLPZ6hJbwy48tWmKbSOuQMAde1dvB8Q/iHol5HbabrF783R7e4k2DjPJDD1rhdW0q10zV7zRtUDKbIg7DgE7/YgZ/IV+nv7M/wADfhV8VfAX2q6/4++R8vk7xh2HdGI4FfGYDLcbXxP1aD1PqcyxuDhh/bThp6HxVpX7RXxrsrvfF4h1NvI5Kz3c5iO71Hmf5Neq6N+2J8cdO+YzxXfs7XD/APtWvtrUv+Ce/wAOr2zeFL++id+pSWJeh/64VwV//wAE8LO0jJ0DV7jd2+0XAx/47b/Wvq62SZ5hvdhex8jDNMsqX54r7jyHTv8AgoP8XbO4RNU0e1MI++wt5ifzNxivUNC/4KJQxyA+KtLlVD3gg+v9+f6V454v/YW+MFhbyz2OpWV1EoH7qKaZ5m6fdUQYPr9K8i1L9mj406ZbmFtGluQO5t53/wDadcU8bndD4pM6YYTK6/wpfkfqN4a/bX+DHiWMB7w2ZP8Az8yQR46/9Nj6VyXxjufgn8b/AAvKmn6rpZu1HySPPBnJZe43notfkRrPwz8WaBG//CT6XPYWwx5kqwPEVHb5nUAZJA59a5SAXXh7K6bqF5sbsZTj9MetU8/rVqEqGLd5M68Nw5Sp1VUoSaLmr6TP4e1C40HVbqC9TI2PbP5oHfqQP5V93/sgftIt4Q1ZPB3jCWaSKU4jKtlVwJHOd8gA7dBX5/JFHuMs8jSTP03HJ/x6Va09dUacanpLmKaHoVJU85Hbn9a8fL81ngsUql7anu5vgqdfDunJbn9TFpe213HHPakOknIK8gflVxZVdN68/SvzG/ZF/anXxRc2ngHxRP8A6bMWWMu3pvb+OUt0A6LX6WMy2SrsyQ2etf0RkefU8yw3tobLR+p+MY7Ayw1V05fI0cgdaKayhwOcd6dX0ElZnCRyoZEKjg1zWr6pZaJo13ql3Iv+iRvIxYj+BSe5Hp7V00jFUJFcV4v8Ox+JPC+oaVDlXu4JY+eAS6Eeh9fSs6dOlOtFVAk58j5T+W39tT/gvt4++F3xI1j4afBjRdHvTpN5NYzvqVvJICYndGIMF8uVwF6qO/HSvxr8Y/8ABV/9oDxd4mu/GT6H4es7+5LsJNOtrmMguxbr9qLdTnr6V9LftR/8Eh/2rdX/AGlfF/jLSLGyh0HVtYu3S9v47xbeKOaZmEryLa7FRV+ZmyQACelZ/hz/AIJZfA7wNbG6/aL+M3hC1EYy0eheIrdHOAMgC5tl54YD8Pev1vJng8PTXsXds/O83oYyNXmWqPgfxL/wUP8A2wPFORdeNdd0hQ25V07Ub2CMdcAgznjn8hXFN+2v+17/AAfE/wATv/u61eH/ANrV9tfEH4df8EtPhvcNZaZ4h8Y+J5YsgNY3Wk3sDMCR1CqSpx+R968Xn+In/BP3TQVg8OeKyfVrPTj/ACYV9Nh50qavGnzXOT2s5K01ZnhR/bZ/bCX5j8SPFRA9dXvcf+julbui/t+fthaZMskHj7xFcYx8r6pesp6dR549K7Wb4mfsP3cwEOheJ0TIyDa2A4/76r0fwve/8ExvE0q2HiaHx9pk0mFElmmlQxKTgZLPkgAk/gK6liYS2w7B1LLU53SP+Ckv7TSXttf+ILfS9bNrIkmdWW6uWbYQed1xznv06mv0g+BH/Bwd8dvD/iHTvDHxA8L6PBoFv5aMdLsrhWEasqkJ5l+I/uZxxjp2rwew/wCCfv7D3xY0/wC2fCr40W+j3E6/6PaeJfEWn20rSMPkQxwwMSSWVSBzkECvPdT/AOCNX7VVz4gtbb4baroviXTMKXm0ue7vDt3c/NFabfu4PPqOxryM3WBr4abrQ5JROvBxc5XjI/uc/Zu+O/hf9pP4V6R8VPCbSx2uo28M/lybA6mVFkAYI8gGAwyNxr6MU5XJr4X/AGA/gXqX7Pf7MfhXwFr4kXULewtFuVORtlSBEYYZI2ABXuua+5VPHy81+L1HTU2os+5wfNye8QSyR5wBzX5a/wDBYrH/AAxJrePWT/0luK/VBkR+o5Fflh/wWL/5Ml1v6yf+ktxWWX05xxd5dTpq/AeI/wDBAv8A5R7eHf8Ar51H/wBLp6+bPjN/yU+++qf+givpP/ggX/yj28O/9fOo/wDpdPXzX8Zs/wDC0L76p/6CK/NvGX+NS+f5I+98O/4lX0X5s9N/ZB/5Oc8Mf9dZv/RElf0F1/Pp+yD/AMnOeF/+us3/AKIkr+gul4V/8iyt/wBfH/6TE5+OP9+h/hX5s//X/pg/4KOf8lh0X/sDp/6Plr89rP8A1g/6+RX6E/8ABRz/AJLDov8A2B0/9Hy1+e1n/rB/18iv5l4t/wCSgxH+L9Efs/Dv/Ipj/hP2/wDF3/KP3xB/2Il9/wCkD18Ff8EFf+TZfF//AGNM3/pJa196+Lv+UfviD/sRL7/0gevgr/ggr/ybL4v/AOxpm/8ASS1r+leGv+RC/kfh2Y/8jWP/AG9+Z+4Do27IpURnb2oeVRkZFWLb/Vk+9eHh8PSnWio/M75u6sKW8tdict6V/D3/AMHEmmXOm/tXeHrmed5YbrTIz5e4sil7y66jgDA/Sv7RPil470T4X+CNW+IGvTpDBpllPOd7KoPkxtJj5mUEkKf4hX+eB/wUm/a+l/bB/aY1XWIRmw0dbi3tnH3dkVxK64Pmyr0k7YHpX6nwTgpTxnNGOkT5niDGyoU7U9z97P8Aggv4E+Hfwv8A2UfiV+01rtrbXt5od1qMr3ESRSXUdrBZW1y0SMyqRymQvmBd3PvX52n4efFH/grv+342vSWt4/w10vUGlHnJKZljhvQ4U/LdW4/cTnjIGf8AZ6/W3/BHzXNR0z/glZ8fJLlne3ca+o5J5Ojw47gdK/I39jT/AIKM/GL9jzXb+w+G1lbX8+rSSCK3mjnlkJm8tRhIriIk5jA4zya9fEYerPE4uvTfXQMBiYVKFKjU3kj9CP8Agun8MvCPwHufCvwq+H9ja2lnpng6xt4z5SRyN5N3LGGfy1QFiqjJCivyn/Yy8FftF/HfxHrHwb+BxvLWDWEt0uXhNzHEBEJJV3G3Vx1RsZU8/ia9A/bz+PH7Qv7UfiS08efH7R9Q0W7k02OO1gFvc28bWpmeRWVbl5CRuZgCDt49cmvrj/giZ+2D+z7+ytqnijUPi7FKLtktPKmdbbzBg3OcNNLEejqOO34V30qOLWVOdGV5W9fuPko4Gq80nRnZ0r/P+tl95++Hwc/Z0/Zd/wCCN3wS1L4q+KJbefXbxIjK9w1o08hjmKj7MXjtHYqtz+8yxwPbr/J9+3X/AMFC/jn+3Hr9hpHjCHzNGspJjb22nrcGXEix7vkeeZOsQPA9a+oP+Csf/BTPRP25fEGl+B/h7NdWGlaU0xcSssSP5yQHny7iZThoj2HX1rmv+CLPwy+GnxN/a4g8D/Eexs/EFuhXaskUV3GN1vcsciVWHVR26iuGnlDwVFZjiH+9d7nruvXjiHhYfwla3f7/AFP6J/8Aggz+zN4o+Bv7P194u8X2sttqXiMKr+ejoxFtcXIX78aN91x1Le2BX7uWdpcW0CQsVZQTuznv0xWf4d0XT/DVkNF0a0t7LT4APJht0EarnlvlUBRyc8CugEufkr85zHGRxOK9pU+J/wBfofa0Vy0lCK0G20QiXbngUNdBTjFPqCWIN8w6ivKxdKdKmvYdDWDvrIlaYbRLgnHYda88+InxB0n4e+HLnxNrBdIowPlGA5+YLwGZR39a7syLBGWbtX5L/t6fGea9Nv4F8Pz/AH9/m7W9onGdr+x6rXzvEmd/U8A5wl+8PTyfA/WsVGnL4evofBXxJ8Z6n8QfGV/4puZme0m8sWccjEuhRQsm4EsBnHG08965KRrWSzH76OK455dgq9fz6U60S0tLtrG9bIUAqcjkkZPWsy9n0uz1NDqEEjQZOcKCOnvxX87TxVTMKzrVt2fuWFoU8JTVKjsj6c+Fx1EeBfEEvge6I1WKO3LeQ5y2ZDjHl/McDOa7lPGvxm8KfC5rjx/rV/p9w+7Ybm5niBxJ/wBNCD0Ipmg+E/BPjPwfKPhBriaJqkIBmVrmO287c3ygiEMzbQGPPTP1rx7xfo/xxnvLXw78YLa61Swy+17NLidSMA8mYbeu3t2+les6NaFG1PY+fxMY1qjk7Xvs99j0X4ffGn4vTWi2OvaRd67pzE+bO8E10FGSRsZn2jJwDn0rI+Kngv4U674Ubxz4PkuNPuxndbXBhhfIZU4RAT6nr0rtfE/iz4p+AbPTfD/w88MyzaffeYJ3ks5maMJhl5iKquWJHI5+teQ/GXSmtPEMWm2k+xJAC8aNhQdqnoB61yOpONJQmtSsPRUmpWt6fqjwy0vtWhf7EGIdRzIpYAg9s1dawtLk+bdyTb/VSOv4ipdPRv7Dja6XE+9g3HOMnHXmlB5FeLWrSg24npKy2QyFNUgbbp17NFt5G6Qr09Md/Su60b4w/FK2sH8Kw65ew2zKUIluZVQjG3H38dPauEuElkZfIOGDA8d6jtLi0ubme21f9y0avg8L092rbC4/FTi430HOlCUedxVy81gv9pG5v9twXffNOPnZlJ+Ybj1J64PWv05+Bn7WnwV+Hnw/i0C1guoJbXakqlIFLOiKCQBKpPTqRX59fD34S+PviFDLP4ekgisYJd7XF2ZFjKKASN6oy5IOcV2Hif8AZ28T2tk174evNLu5l/eSRwyNJyASeFjz6da9TLMVicsqfWcM/e89TxcVh6GL/dVtvI/WrTf2zvgrdxQCa/S2eaMS7J5YEbB9R51dZZ/tT/Be8IA1uwX/AHrmAf8AtWv57r/Sr2HUBNr8MYltV+zGOBTuyDnOGGcUwLD0t4LlfouK+lh4i5o/4rV/Q82twdhub3G7H9HVn+0B8IL19lvr+mZxn/j6h6fhJWqnxt+FDHafEekg+hvIR/7PX83UCakr5t5pbQ4+/KxjBHpn1PpV/wDtzUrQbJDNOQMeYmWH1zmu+l4h4+2qT+Rg+C6T2mz+kJPiz8M7kAR6/pjY5G26iP8A7NXyp+2R4u8I658JmtLTUrKZmmVwBMjAjy5B2J9a/Ga11nV5pJW/tae2xEzBVnKMCOnGao6r4g13UtIhs9X1O5mhDqB5szMMY/2jisM041xGLoSw9WKsx4XhL2WIjOE72LVjpVxHC0VqLPbJJ5gLZxtP0FftV+yd4v8ACWkfDC3sdS1LToJo1QMomRcYjQHgkGvxWMlpDHDHbSZXyhk5H9Kkttfv7FGgsdXubaNuojnKD9DXy+TZx/Zlf29NXZ9HmuTVMTh/ZSlY/pMh+JngGSPb/bWnMF44uYz/AOzUP8Svh5H11nTx/wBvEX/xVfzfp4o1SCL/AEfXrxsnJ/0on+RpjeKNfl4Gt3hH/Xy3+NfZrxLqTXvUz5WnwZGSv7Q/o5b4pfDhOZNd09V9TcxDH/j9UpPjB8KoeW8S6cP+3yH/AOLr+c19c1wDdLrN0y9w9w238eaqPqV/cjA1Nj/22P8AjSfiNP8A59mq4Lh1qn9FVx8c/g/GhaTxNpuB63sGP/Q652T9of4M2aMsWv2Eo9EuoG/9qV/PnDb3KzCW6vHnQdYxIWLfgetXV/tOIl9O0+4nHp5Rb+Vc2I48xlaPLRjY5anDGDo14qrN3PVv2j5/htc+PZdf8G3EMyTbd0MbxM74RRwqdecnrXU/sy/tAal8ENWe68QxNLpUmMRwqWk4354Z0Xqwr55PhjxZqVyt7/YEybD/AKw2rjGeOuKoX97/AGlobiNSksXbGDy3518ssxxeBrLE01abdz7edHCTwiw8tVax+s1//wAFGtAPmf2dot6UIHllrZevfJFx+WK43Uf+Ciuo7Nun6SAf+m0Bx+k9fCPh34R/Fjxbo8F74b06a5hO7PlQzOx5I42oR1BrvtM/ZO+O+rcDTXgP/TzDcL/7Sr6Gpn+fYxKfNv5HyksryehJqrv6nruqft8/FW/fybOx0uIN/Ekcyyj6Yn/yK811H9rr456pKVnuUhi/6YvcKf1lIrtNE/YJ+N2oXca38+mW0JzuffOkg4PQmAgV7Jo//BOvVi3/ABO9YyO/kXBP/oUFccsvzfE/Fc7KWMyOhsl+Z8C+LPH3iTxVG8mrapfz56wyzs0LdPvKWOemfrXE6hdteosdvEg2/wCz/hX6+aT/AME6vh3FMkmqanqcgHULNEQfzt69X0T9if4M6E4byprojtcrA/8A7RHrSp8E5hOXtmvvNa/FmBhpS/I/DmHT5b+9gWxtZ5pcnCRJuZuOwHJrVufD3ivw4pkvbG4son73ETxjj3IA71/Qfo3wD+EukzJcWGh6cssedr/ZoQ4z6EIDXmv7SPwQ0Pxn8OLuPRrKGG6jVfLMUaq2S6Z+6hPQdq9LGcBYh4R16j1Rw0uLaNWtGEo6PqfhL4e8UTeB9aj8W6NI0d9akmMqcAlgVONpVuh7Gv6PvhX42h8aeBrTXJcyuwbIHzHhiPVvT1r+bnWNFtIbmOwmJSa2Zt6nAJ3dMjGa/WH9g74vRavpcnhDVJgJIceWrN/eaRjwXJ6DsK5+AswxOAxbwdSXuSf/AAxhxNhaOIpLEUd0fpvPLJGysoG3pz1qcSZqrLIsrhR06/jUyrgZ9a/oClVU6zUXeJ+d201Enn8vjA6Z5qlue6TywSh+9lOBUl0vmHafTFR2kTAOo/hyv5VMatT2/Kl7tzKWuh+L/wDwXF+Jnxh+Gf7I2fhGbqOe+1CK3vZ7bzwyWcsFyJTuhZSMYB+bK+tfxReHfgf+1D8fStx4V8P+MPFUTuE86K0u76PLYOSyI4xhgfofev8ATD+Jvwx8D/Fbwu3hX4gaZa6tZsd5t7uGOeNjtZfmWRWBBDEHjoa/jB/4KE/tYfEz9lH9phvgX+zRHo/gnT4bsrtsBLpoKpcSQ9LWWJfuqv8AB/CPQCv0jhXHuHtIKzdtD5/NE4K0T4t8M/8ABKL49WV7YW/xL13w58PlvoEudnii6uNJm8tiBgCW1Hz9QByMqfSvr/xp/wAEfvBvwV+E0Pxl+LnxH0K60S52eTNpurxv5nmRmRdjTWqI2VRsYY54x3r5b/4KI/GX4ueNLr4cy+NvEepahe3vhmyvJHs7uaWMlpJckmRycZJyTnjvX3z+3hqrXX/BIr4SJEt04MOhfaZcZ3H7FPuJbPOR1z+NezUx2Y8sJ02ld66HJgsHQdJ1Jatnz78Cf+CYH7P/AO1Lez2PwW+Iliby1tJL14LvV7bKRxbdzSLBbSkKC6hj0Hr0rzDx9/wSR1dvElz4T8AfFDwdquq207W5sbHWzPcGVSV2iKO1353YGMZyQK9B/wCCAMmkR/ta+LUUzTWTeDNXDJJtaMZktecfdGBXgWmeJ/Euh/8ABVu7k8L6s0FnL8SY7TyI52VfJk1FMjahAxjjHT2rsniswjialK+iX6XKpU6LhzNangPi/wDYa/ap+EN/fQ6p4S8Q3SaVK6TX+nWF2/k+VndKkphTbsC7g5xjg1+un/BB344ftKt+0rffCvXptd1DQI7O4ZZNca6lICS20YwWfy87SSPl9e1fPnx5/bk/aA8Of8FDPF/wm1HVJpfDa+NLnT/sUk9y1vNbG88sxtGZhGyMmVK7dpHGMV/Yh+yp8FPhPoHgnRfir4W8JaLo+oarpsUzyWFhDbyETKjkZRA3JA/iPQV5GdZhU+pRVd6yR35fCm+dcvXQ+tLqxeW6SdJJUCrjaDhSc9cVaQXAGQenpTJmMpjkIkBOOF/qP51fC7FPvX47LCKvipSUmke/StGNipulDZLHmvy//wCCxX/Jkmt59ZP/AEluK/UcqD1r8uf+CxfH7Emtj3k/9JbivXyzCujil7zafcjEfAeJf8EC+f8Agnt4d/6+dR/9LZ6+bPjN/wAlPvvqn/oIr6T/AOCBf/KPbw7/ANfOo/8ApdPXzX8Zs/8AC0L76p/6CK/OPGb+LS+f5I+78O/4lX0X5s9N/ZB/5Oc8Mf8AXWb/ANESV/QXX8+n7IP/ACc54X/66zf+iJK/oLpeFf8AyLK3/Xx/+kxOfjj/AH6H+Ffmz//Q/pg/4KOf8lh0X/sDp/6Plr89rP8A1g/6+RX6E/8ABRz/AJLDov8A2B0/9Hy1+e1n/rB/18iv5l4t/wCSgxH+L9Efs/Dv/Ipj/hP2/wDF3/KP3xB/2Il9/wCkD18Ff8EFf+TZfF//AGNM3/pJa196+Lv+UfviD/sRL7/0gevgr/ggr/ybL4v/AOxpm/8ASS1r+leGv+RC/kfh2Y/8jWP/AG9+Z+2NxnzuK17YnyKgNtvy+enNWIciIqO1eRl9KUK15dTrW5+dv/BVDQ/G+t/sZeLI/AKyPepZXjOkQkLNELS43ACMFiSSMdq/zh2sNcsL2XT7i0mOp3EhhmjKN5vmvwUI+9uzxgjOa/1ZPGGiWPijwxqPhvVYUnt7u1midZFDAiRCpGCCOhPav88n9tT9nfX/ANmT/gosZ/FNusPhbU/EY1qIMrLCLNr9gF+eOOILsQ8DK474r9h4Gzajh3Vpz3ex87nuEc7S6H7p/scfB7xV8Bv+CNXj271OzMV54n0y/wBUWJo3V1iudHAy6lEIIKc/eHua/C//AIJV/CT4DfFr9p+LxD8d/EVhoVn4etzeuNTu7e1tne0nt32/6QrA5BbjIJAPI5r+o79or43/ALO3ij/gmhc6T4Z8Yabo7P4WkMENtqFtbySMdPlCwlVc5DZAKAc4xX8Sfwp8B/Eb4x66nhv4N6Nqs+qSyixMmnW8rRTo5VS7tArswYsuWxgjHHSurLadX2OLrTdk31PIxmFmlh/Y7pH6of8ABYT9p74C/Fr41Q+HfgJH/wAS/wANaWNEW4sRb/Y53tbqU+ZG9vI6ujIRtbCkjHGK+Uf2DP8Agnd8T/28vFXiLRfhfrNlpcmkJaNdHUbiWBWE4l2bfKt5yceUc5x1GK2v2tP2EPFH7JOh+HU8eSyf2zreg2usXEMjPmGSeRo3jZZIInUqyn5Tkjua/XT/AINlkS68efE64bllh0jH53orpr1JYfKFVoz0seRk9avUzWdOrG2v36HNp/wbW/GKS+mmuPEuhokoUbor2YSAqPU6f/kV+tP/AATk/wCCN/gv9h3xvN8R7rU5dU1d9mxzNHOqlVmTqbSFx8sv97t6V+27DJ+UDFXYcHGcCvzWvxRVxf7mc9D7yjlag+ZjY4FR2ZmY7scHoMelThFGSvWgDNSqpXrXmunBy57anprRWIsYGaSkmkCYAoBBGRVRrQk3C+qLSsjH1GeOIb5CFQdSTgV/OD8d/FK+Lfiff3umeYyR+WE384+RQehPpX7XftT/ABSh+GHwwvdUR9tzKF8nnBJWSMNj5lPRuxr8H9PZZ5p9Xv8A5muSMZ5+7n1r+fuOse51/YR/q59/wpgXyvENeSMUQ7QkF/IJbqHJZ4zuQhunJ54FTXGoG4URXVsrr67Mn9TXcXfwg8ZaL4D0vxdNBLI9+9wH+RzgROFGfkHr6muIsrtCxt7pdr/7Qx/Ovzh4etQWiPtXieZ6ENm0+nB7nRLu5sZxjb9nfylb/f28nA6V9N6b+1V8QLLToNPv9O0vUTDu+e8iklbn3MtfMKq6zuo5XjFO3seorWhnNWL5WU6UK2k1c+w9X/bA8W6kIoYdL06KPneFhdSOmNv70/jmvl3Wtf1bxBK2qajITeMeDlig7dyT09658M5wAKd+9rapi3UfMwjhoUvdgrFuW4luL5njwsJUYXvnj8KsKR3FUYNwf5vSrdcU4cxXLce4lCNcQYJgUykd2C84GOpPYV6n8Pfg8/jnQLnx54nv7HRLBmaNf7Ql+zOSyhx99GXGCe/Y15O7zRRtPCN3lguR1yF5Ir6o8I6f4S+N/wALo/Beu6l/YYjZXykyWxO2Pb/Fvz949u3tXTg5Rg+VixEpQo6M9E8Y6S3wr/Zrj0/QNThlF/eRSJeaZNuXyXgZcGRdvXAPcdDXz/8As33Pie68YXFzLdahfaeiSedJM7yxDBQnn7uNvr2r13V/B0mo+FdO+B3hO9bUrDTXhlnujJ5wEMAMTfOg2/dOeUA7njiovFnjnwH8EPCL+BfAvl3d7eIY55Y/LdlZ1MbZaNkIwVB5WvWr16bjZHj4a+sVrKTPmn4pTW7fE/UTpDI9u0krZQ5UHzDx8vGcVyYuL9D+72fjmqsKSxTzPdSGaW6czlmO4jd2yeatV4c8VCMrWPflhJTs0yC5v7/MYuo0lQOvygFvzBNfQvgf4u/DPwtBFZeN/DkVzG+1S8dnE7c4HWRx6HtXz7LHcSKPs43MDnuen0qtdq+oxiC8Taw6HGOn1rqo4yGjsZyy2UlZyP1f8B+Cv2TvitaJqFrbxadJKoJWRLOE8gHGAG/vdK5j9qD9nb4V+EvhZ/b/AIYtnk2SKqG3SEk/u3YH5IxxwOc1+Z+j614m8M3tumlXdxh5URUikfqSMcAj0r2jVf2ivHWs+ErnwPrUpZbW72YlaQttRSuPmkPqe1elWzOlLDuk6av3Pm8Tl2KoYpOnVbj2Z4fpVvaywCRt6Kg8spJgSfl/nmv1G/Zy/ZU+GHj/AMBR69r0EjyTheqxE/Oin+KJvX1r8stV1CUNDJDAU82VfnC4U7vfNfvN+yEtzH8ILDleUi9c/wCqSu3hXLMPicWvb6rscefY3F0qN1OxjQfsJfBW3iKLBIctkZSDp/35q1F+w/8ABeL/AJdn/wC+IP8A4zX2QJAqqr8sQDxTvMX/AD/+qv26pwtk0bXppHxKzfG9KjPkBP2KPgsjbmszIP7rxwFT+Hk1pQfscfBKDldJgOPW3g/+NV9W+Yv+f/1UeYv+f/1VH+rWS/yITzbGv/l4/vPmq1/ZV+DVjOtymiWrlezW0BH/AKKr0DTvgv8ACy1QCPw5pgH/AF6RZ/8AQK9ULBhgU5GGNpq8Nw7lkK6nCmrfqYVcZXnrOTbPMdS+F3gefTZbOHR7KIMB/q7eNT19lr+fv4g+C/8AhCvHOo+B7pQtzNs8pwMRDIDnJKg9D2HWv6UJxhGFfiv+3d4Jfw/43g8ZWMe1XzlguPupGvUKPX1r47xOwdOjClWo07d7fge3kOLqOo6be57/APsBeObLXvDF14amRVudOwWJAG7zXkI2nJJwBzwK/RwRg4r8Iv2L/F03g/4u2OmSS7LbVGZZPmwMRRyMM8gdT3zX7ql3EsQXo+f5VfBeYRxOC+D4XYjiLDOnirvqkyaOGPzACST6HpWkkKLgqo/KqqqS+eMirgLAc1+kYCMduQ+ckn3AKo5OKq3JTPTmrLEtVWbGDurozFcuHfKEF3KG1sHyuG7GlvFWaLyZFVkPUEZH5Vat1R39qfJGJITjg+teNTwdfE4eST0ZceVS0PwU/a9+FMfw18dtqtsgEOoYEflD5VMaJndhVAzu4xmvG/hH4x1H4d/E6w1CykkjtpS29UJDcRtjgFR1Pc1+sv7cHwx/4Sj4X3Wt2cPmXdmoMZVct87xKcYUnoOxFfixe3jQ+JJpEGw26pt7clcHFfiGdYOrleYck9Huj9OyOnHFYDv0Z/Th4b1m11yzS5tHDgYBwQecA9ifWuwzgZNfnV+wx8Vrvxrot/p+rylp47ligZjnYqR/3mY9TX6IMflJr9y4WxKxGC9t1PzvNcM8NXdNkZ+d8miBWi8xnwdxJGKapz8woaQEcGvVpV4xpyn1uec17yK82JJ/N9FIxX+ft/wWntV039vi8nihilneWaYGZdyhfts3HY9a/wBA2NQ7H6Gv4Fv+C5tl9i/boe7I+9DKfzvJzX0/BvPPESb8zxc+jak59kfMv7dKTz2vwk1MN9nkk8A2BKwHYpJklPT0/Gv1I/bQuWH/AARm+Et1GAYsaDBPu+8WNhNuI7Yx61+Xn7dbbfDHwZn/AOengLTf1eWv06/bNGf+CKHwtx/z86D1/wCwfNX6pmSVGlQ5V2/M+Ty/Fzq4d8vQ+fv+CBNla237VXjC6twTa/8ACF6xlXxnPmW3bpjFfOmhR6dL/wAFU9S8qEbh4886IFRgONQTaT9PbmvpP/ggdx+0Z4yY/wDQlawf/HravnDwhCJP+CpV9P8A9Tr/AO5COsauJl/aNVW3S/JipTq+xdzjv2iBc65/wU41mDVki/eePzCzRA53tf4zzn1r/Qi+B+mtpvwQ8KaZbMQLfTLWPJPOFjA7AV/n5/FiD+0f+Cq+oWPXf8TUXH11FR71/oe+ALH+z/h9o1iBgxWkKfkor4bjGtJ0qK/uv8z6HhtSlGpzdzpY1cyLknHStBh1FVbcAsT3Bq31P1r4HKk1GUpdT6qWhEI/X/P61+Wv/BYz/kyXW/rJ/wCktxX6kSyBTt71+W3/AAWKOf2JNbPvJ/6S3FengsRCeLUYvYyr/AeJf8EC/wDlHt4d/wCvnUf/AEunr5s+M3/JT776p/6CK+k/+CBf/KPbw7/186j/AOl09fNfxmz/AMLQvvqn/oIr8z8Zf41L5/kj7zw7/iVfRfmz039kH/k5zwx/11m/9ESV/QXX8+n7IP8Ayc54X/66zf8AoiSv6C6XhX/yLK3/AF8f/pMTn44/36H+Ffmz/9H+mD/go5/yWHRf+wOn/o+Wvz2s/wDWD/r5FfoT/wAFHP8AksOi/wDYHT/0fLX57Wf+sH/XyK/mXi3/AJKDEf4v0R+z8O/8imP+E/b/AMXf8o/fEH/YiX3/AKQPXwV/wQV/5Nl8X/8AY0zf+klrX3r4u/5R++IP+xEvv/SB6+Cv+CCv/Jsvi/8A7Gmb/wBJLWv6V4a/5EL+R+HZj/yNY/8Ab35n7kjAUkmo4ZACRzycU/blSaqKcNtPrXlwqS5ot9Njrqu1rFe+E7uqRcDI3Zzgr3r8Ev8AgvB+y74L8efs/wAXxgiiWDV9NkS2EyLGreQsV1MQG8tnzuOfvAe3ev34l+Y+X3Ir8n/+C0Et5F+xNqsNlE0rRs8jYUthVtLnJ4r0eHq01mDm3pdHNmjbwztufwN6f4w+Juv2ln8MLfVNR1K0vb2LRLex86WYqs37pXWMEgYBwCFOM4welf2z/wDBMT9nz9lr9i34IeG7X4raj4XsfF+uw2l8jazNaRalG00UK+WvnJDKMSxn5efnzzmv4+f2I/7Cvv2ufhwuuLGYrjxFpAdZQuws95DnIbIzjNftl/wXb+Evxm0f46eHvH/wpTWBpOm6PFPGdME/2dGiuLmRf9SgUYUDow4x2r9azz2k1DB0H7s9T5vLXUdqlbdbHZf8HEWu2msfFLQ9V8PSpe2beFbYia2YSRkG+nIIZSQcgjvS/wDBsYFj8YfFDzZY0MUGkEhmwTuN7jFfz8/GH9pX4y/GjwlZ6D4yvpbm6sbCDTwbuWd5EWF92075HIIOcjA57V/QR/wbSPp58efE20EZaXyNI3nAKj/j8I96rMcrrYbJZQUtkeFluNqVc+dNrq/uSP6/oMi2EhXPXjHPWkeYInmYK+xGDX4zftPf8FRNd+Av7Wunfs7JoEn2a7JC3E1q2G/0Vbg7X+0oDgtg/Jx+tfsrFML2whupAA0ihsfXFfjmOwsoYW9Nf8Gx+nt2drgtw8hxWijOB83JrIhKv88ZBHtWySNua4cobmnUm3oTLR2ZBMjSkEU4jZEfUU2KTqCcZptxIotmbpXU40+Sriob2f5Bd8yiflH/AMFBvF2l6i2l+E0Lk2Rma6UYIYSiJkwM84x3A9q/OPwxo1/rmqad4XtsNPM0mCMlRwW5wCeg9K9i/aA8XXXjL4qavNcOXhUQhOSRxGoPUsO1dz+xZ4Ij8a/EhNUu03xWZB5GQNySDupHb1Ffzi5zzHN5Kp1Z+y4GnDBZP7Tqlf5n66w/Bzw9qPgu28O3lrCVhUlAUXguQxxlO+PSvxj/AGjv2e/FHwu1uTVEgWW1bBAtldiMBRz+7UdWr+gtEMYwDwoHFeb+P/AXh7x9pb2WrW0cu4YBZFYjkeqt6V+u59wfhng06MffS+8/O8qz2rRrP2jvBvU/mptttzHvjIDd1P3h9RSsjoOVJ+gr6G+P37PXiX4Ta0NQ06GSS2vCQoRWIGwLnpGoHLV4DpuoxS5gul2v3DDB/Wv59x2AlQrPmVj9VpYmnUpKeHd7kES7+h6VP5b+3+fxpVTZO+OhxipazXK1dG9JSlG9Tchxs+Z6XzVptxny+PWqWW9KpaGnIjViuNsU6xruLROo4yOR39qz411O005boXUsKKAGFo5Vs4zU9pcLB5plH3o2UfU0mmyYspra66SZ259xgdayWtUznTm9FsfdnwUt55PghqM/gydJ9evGdC1026RbeSAbuU/eA7seoz718Va74V1jwxqN2fFDtNeyyuxeQsyDOehcBuoNdF4MtvijpNrJL4Nu544zkssUko+TAzwmO2Paud8SarJd3Jj8aXVwJ88mR+pyf75z1zXoVaMpQtA86nRVGcpN7mNAjEiVnDnHUHIq3TY2sfs4Wwben97IJ/MUqkHpXnexlCPvrU9SnepDni9CvdXGoWyCTTlDPuAIwT8vfpTpbz7VGsYUmc4DBBkr657jHerK3D20izou4KQXGM/L3qKXyE1VZNGBlm1MiJEHzbWmOBgLyMHHrW+FhUnKyRy1a8odT0j4M+CNW8VfEbTdPtYftUMUsU9w5UvFHEki7yxCkKQDk54A619b+Ov2W/DmvQeIte8IXMFzLbTXMrrbOrjcgLbQEjJ3dMDNc94esrj4B/B82q7f+Eo8WSLYwE/62JL6LYGH3JV2yLnI3AHsTXexa/dfAD4faP8ADa0uX1fxZ4nubaa73ObhlFyvkyE4KSgB1Gdyt15yeK+xwmXUqlFwrLU+ZxePrTrXpvyX6s/PfxJ4a8deGbGOLxPpE1vZMoa3meCRCZOdmWcBexPHPpX0V8D/ANqjxz8KLK2sdbCz6ZGyZWPzGcKAo6NKq/dU17z+1J4H8c3vgHSvtqw7IIITIkYk3CRVfqCP581+dNok0ttPpmoLtZCwyeOgx3/wrwaknlmJvhnZopU44ynepqmf0DfCv9pf4b/FCwhuLOY2czoMrdNFHliFOABIx/i4FfQVvd21wA0ZBUjIYYwR7Gv5dNN8T+JfCfltod3OpjlUqIpGGCOmdpHHHNfdXwe/bo8VeF/s2j/ECL7XbnYnmIru4HyjkyTAdAT0r9Lyjj2pUjH69a/kjwsbwlVs6mG1R+1oELfcO76Ux2ROqmvEPAPxy8B+PLWJtDv4FnmjR1geWMSHcCcbVdjkAcjtXr8Us1zgllx9a/QMJnGExcUqNuZnyNfD1aLaqKzLyyRntTgUJ3DpUaxAD5utNYhRjpXsyUaVH96tehyKUpS02LD7WByRXwp+3P4Ek8R/CO81mzEYewUFi33v3kkSjGFP8xX3AGZzgV5D8ctBbxF8NdU0bG7z0jGCM9JEPofSvl+LLYrL5KUNrnqZbUlSxNOUX1R/PJ8P9fu9A8SaPrO8o0Mk3IJHVWHXI9fWv6SvC2vWviDw9aa7b7im0+hY447E/wA6/ma1Gwng8q3tuHSSQD16+1fvt+yP4mHif4R2DzNvZTKGyc9JHHqfSvzjw+xko4yWEfwyf43PreMMPJwhXe9rH1DEcXDHHDAVdJGSPWq+5A4TvUzHD5r9+UIx92J+eRbe4x3RTxyapzQTPytOf/XVojoK8yrTWI5oz2TNE7GTb200TgntWi0f7vaOtTUV0YWkqEOSOwr63OU8UaHZa/pEmm6jF5sMmAy7Q2cEHoQR29K/m0+Jvhefwn8RL/wjfJsubUo0j4IiZZVDKFJAJwDzkdelf04y4xzX4m/t7+BINB8bJ4nsIwralhSwXGfJjjHUKPX1NfmHifllOeHhjkvfWh9nwjmEqdSdDpLU4P8AZK+IEPgf4t2tu0hWzvEEJRTgmWSSMA43KMYHua/eSKRbqBZYujjI/Gv5ffDN1f6D440bVIWKiG7t3bBI+VZAT6elf0o/DfW08QeCtM1WNtwmt4nznP3lB9T/ADp+GeZznh54eow4twy541vkdgBtjYfgapBsPtrQYfe92NUCoE4Ir7DH80XGMdrnyMUty3GvlksfTNfwbf8ABfGNLT9tW2hYHfcWTzAjoFN5cDn3r+8/bl8exr+EX/g4EtSv7bmluAedHY/+TtxX6RwbFxxfIlufOcS1HHCyfkfG37eKE+CvgmV4x4A0tj9A0tfpr+2JKtx/wRR+Fu3tdaD1/wCwfNX5n/t3nHgn4LA/9E90z/0KWv0o/a4/5Qo/C/8A6+tB/wDTfNX6ZmLbpwT6NfmfJZDaGDlKJ4n/AMEFlMf7RHjTOOfBOsn/AMetq+fPh9bPd/8ABT3UVjIzH4waUk+i38efxr6B/wCCEbbP2g/Gr+ngbWj/AOPW1eDfCB/P/wCCm+rzdv8AhKJD/wCT0dcKquWNrTe+i/A1oYiTw7b7mLr9rJqv/BXqa1XaC3xQhb5umP7TT2Nf6JWk2pttJt7bj93Go46cCv8APMtYvtX/AAWEuGAzt+JUZ/LU46/0QLPm0Qf7Ir5fjGCSow6cp9HkE251V5ogh/dM+7oWNXAQRhOvvVN1OWHvUsLjdX5hh8RKE/Zva59PKN1cjkt3LbiQa/Ln/gsUMfsSa2PeT/0luK/VButflh/wWM/5Ml1v6yf+ktxXsZdhYUsUpQ6mFZ3geI/8EC+f+Ce3h3/r51H/ANLZ6+bPjN/yU+++qf8AoIr6T/4IF/8AKPbw7/186j/6XT181/GbP/C0L76p/wCgivzbxm/i0vn+SPvfDv8AiVfRfmz039kH/k5zwx/11m/9ESV/QXX8+n7IP/Jznhf/AK6zf+iJK/oLpeFf/Isrf9fH/wCkxOfjj/fof4V+bP/S/pg/4KOf8lh0X/sDp/6Plr89rP8A1g/6+RX6E/8ABRz/AJLDov8A2B0/9Hy1+e1n/rB/18iv5l4t/wCSgxH+L9Efs/Dv/Ipj/hP2/wDF3/KP3xB/2Il9/wCkD18Ff8EFf+TZfF//AGNM3/pJa196+Lv+UfviD/sRL7/0gevgr/ggr/ybL4v/AOxpm/8ASS1r+leGv+RC/kfh2Y/8jWP/AG9+Z+50ePLPqaotHiTJ9aux88e9QyJ8+fevPrJKjBnW1djJoztLJ97acE+tfOv7VPwdg+PX7Pvib4byxRS3l7pd5FbmUZUTyW8kaE/JIcbn7KT6V9Hybtv+zj9aqpwxfG4EYIHNTh6ioYpNdR1Yc9NxP8zf4sfAv4mfsR/tOaUPiHYXFqmgazBqEVzbRSpFIlpckhYnlSHc7eUSgGAfUdv7cf2SP2gf2dP+Cg37OUSzWlrd6lBp40u5g1qO2e8En2dCzhTJOwUGXGTyGzkeup/wUt/4J8+Bv2wvhHdS6dpsNv4j03deW08cMaSyvBHMyxlhBLIwaSQZUYJPQ56/yGfsg/F74o/8E8/235vAXxDv7uw0oXrwXtvLLLFCqfa4o5H2StAowsRGWGAOvHT9So8uPoKcZ2qRPkcS3hudz+FHyj+298O4fhN+1r8RfCWkwNbWGn+IdRiijC7E2JcOi+WAqrswBjAAr9pf+DenxZcfDHw58aPiR/o7y2NtobEXGTnfNdRjup/i/vCvx4/b7+MGjfGb9pfx5488O7H0641m+a1lTaRLC9w7q+5XdWyG6qcHtX6tf8EQvAmufEP4c/GfwroLMs17a6GNqFgfknuW/hVj2PavqMzw9V5Pz1Zaytc8fLvZfXViKb1d/wAj45/as/4KM+If2lv2stG+LMukw29lpMswEkMDJIN1ssJ8w/aJBjKfL8w/oP7aF/bW+BvhHx/4f+C/jLWLaDXtTj+QfaLdY02wCb598wcZQjHyHJ9ua/z8P2WP2RPjD+0f+0BD8E/CkMiq08w1C4dZgIVMcssRZ0ik27ihA3Jz0HrX6gf8Fi20f4fftUaJ8TPhd4saXUEjEZhtb9W8sxWVvD92Lay5Bb+Ln8xXh51ktBqGEoayjG/32Pby/HuM/aVnoz+5HT4RFH5IBIXkN/Cwbng96uSSEcDNfn1/wTc/bW0r9tv4DWXxH0+CSBlDxvvUKCY5pYeMTTZ/1XPzfl0H6DMuWIFfleMwqwcHRirN6H0lOr7Z88dhIF3P82K47x54ktfDHha71+43GKILwuN3LBe5A7+td3Gio27NfLP7U/iFfD/wjvnZseYq/pLH7j1rwc2lLB5dOMt7M9PAUvb4iEV1aPwv1zUTcareXlySXZgSf5d/Sv0h/wCCePhifS/Dt34jvUBFxt2YB3DY8oOcgevrX5c6vdbmnnPQ4zX73/sn+C4fDnwys4igBk8wnj/bc+g9a/GOEMNKvmd0up+m8R4j2OW+yvvZH1VD5qxhZiCe5FOaJSuIgAfehmC9akjdTxX9GNQcfZyPyVXtc8w+Ifw90PxvposdWt0lZc7C6K23JGeqnHSvw6/aF/Z28X/CrVn1lIFuLRuQLRXcjAUc/u1HVvWv6DrvGzIUsfbmvOvF/gfRPGmmNYazbRyhv76Kccg/xK3pX5ZxdwxGu5Sor3j6XI86nhJrm1j2P5s7W5gvIRIWETDqsh2sPqKVprcNtEqMfZga+q/jx+y14k+Ger/2zo9rJfWl8SNsKNII/LC9QsShclvfNfKKnTIZTbXNs8Uo7MgX+fNfiuIwcsNUdGejR+t4XF08TTVWm7pkk6Hyg+RjNUqgI1BZj5ikQ9s56/yp+W9KxszclBjBzKCR7Ujv5jgpwAajJb0oUMBwKpRt7x0RqxULPc9I8HfFDxL4H1CK502C0uYOEnjulZwYcjdtAYDdgcZ49a+5vDth+zr+0f4e+wQ2EGma35eGeaK2hUy7ex/ePjc/149a/M6WUKF3ZwSAfpU8eq+IvC15Bq/hC9lidWViscjL0OeiY9B3r1sBjqafKz5zMsN7bWm7M+x/Hf7Gfj7wNpralozW+o2zP8qWZkmcKQTkhYQOAP1r5W1jSdU8P3jWWsW0tu65BEiFOhx/EB6V98/Bn9tCDR9Ci0/4m2810ixhSVTexYKo582YD1q98Q/2gv2UPGFrK17oVz9qkBw4tbPdk57mQnqa9PF0cLUg6kJWfZnnYTH4zDv2FWndd0fnGJ9zhYipUcy98x/xAe+OmeK6bw7eeG9H1218WGKV7WydJPLZVLbo2DjA4HQeoqHxVP4Uu9VmvvCVvdQWzhgvnoirgkkfc4zjFc0GxpL2p/iz/LFfOQxSpy90+hpQVZe8faWgeNfDXxU8XH4leM7lbTR/D9g89tBK6o7z2zCWMIrlkLkEhQGBzwMda6HwPt1TVdQ/aE8TK+o/ankttDtsebNF52JrcsjZ2hWyCY3OCflB6183fBTxZ4P8O6hZaR42sf7QstQuYrTyzEkqq8xVQ7LIQAoGcnnHpX118fv2hfh18K7Cy+HngXQY7ido0khe3tYmhiI3RqSY5FKlSAeF4Fe5hMc5tts8HMMJ7PEKlSi2n+XUzfix468YaR8Mj/wsG6jfWtTuVntrZHc+XZyRsMOkjB1YPkHGVH1r4CMs8081zc4HmFj8nv8AWtnxH4z8U+PtXOu+MLkvcRqYooldyiR5LAbXLEEEnocVjkEDnpXi5nU5q1z0MHg+RWaKzIIYwbdQ0hb5t/Tb3xjv6VZAs4lEkCGWXriYAqD7d6i6sadgZzWc47WZ70KsadP2bN3QfFPizwtqcXiLwxeTQ6hbZMMayOsBLAqQwQhsAE7cHrX6CfBb9ug2jx6T8So5vNOcug+X+I9ZZx2x2r825vtJiIs22ydiSR/LmodQt9IurYJcXwhuR3WVVP689K9PLczr4SanFnz+Y5RRxes0f0y+E/Heg+MdOj1HRplkRs4G5SepHO1j6V1Jy5GK/nG+G/7R/wARvg8Uvo7yXULODJaISSyls5H3fMQHls/hX6x/DD9sv4e+LNDjutfuVsJznKyvHF3I6NMT0Ffq2Vcb069NRxTs0fneZ8O1cNVtSV0z7fjRVH9aydcsodR097UlTvx16cEH3rgNB+M3w51wj7DrFm+e32iI+vo5rp08X+GNQiD2V5bMD02yIf5Gvs62a4DE4KSUlZrY8X6tXp1E3Fpo/nK8cWL+FPH934Y1BC09iwd2QZjIlXcME4J4POQPxr9Sv+CfGqyy/D2bSJ5VaW3JJAbOA8spHfPSvz9/axgOl/GTUr22WDyJVh3Mg5+WJO446mvrT/gn5fNa32o2DEgSLFgH6ymvxLJsTTo5zFU9rn6PndCdbLFVnvZM/WGOe2kUTBl59xVoEPyCKycQQQ/JGWHoBk1btrlHAUQup91xX9A4TFOpNKXY/LXCydiR0/egGrw44qoTlxnrVuupQUZOxkncKKKKYxrBT96vzd/b+8Om+8Jab4jjQNHYtM0gxliHMSjHHr6kV+kEnavnD9pHwjb+K/hhe20q7iirjj1kT2PpXy3GWDeIyycbbHqZNX9njKb8z+etLuV71Scg4+XPGDniv33/AGTPF9r4i+D+mW0O8zafBFbzF8YLpGmSOSSOe+DX4GXSRm9KwdUJHHsfav10/wCCf/iE3nhe+0mRuY52H5JEPWvyXgbFujmKorroff8AFOFTwnP2P0hUhlAwcnmqzIfOGfWrwAGFHaoJVxLX7jjcNdxb6NH5lcsZVQXYgADqa/g7/wCC7nibS/GP7ZqXeiB5F0ixk0+4wAT5sd3cEkbSeMHvg+1f3eXcZmtZYgcbkYZ9Miv85r/goz4q1S2/bw+JVtrpM9jZa3qcCNJlgAtzJjljt6Z9K/SODqaeOR8zxMr4Vop/t3Wssvw++Ct8vCnwDpcfPUMWlPNfo7+1k/n/APBE34ayKCBBqOhQNnuw0+Y5HtX5tftYePvDHxa+CXw7Pgt0luPD+h6fZTqhRiogWRmGI2cjG4dce4r9Kf2o7mOb/gh58P1ZcP8A2zoY6c5/s2Wvv82XLTj/AIl+Z8xlCtgWjxD/AIIYQPD8cPHd4CGC+BdbG1eWJzbngV4L+z/FJqn/AAUg1qa3BLx+I5pGjP8ArAFvIyRgd698/wCCFcd1B8Y/H1x95h4H1wqpyef9HxxXyJ4c8bSfAD9tTxR8YddxhtbunEbdOblZOjFP7n97/wCt5dFN4uvp2/Iyw3+7P1Ot1nxBafDr/gqDr/xG8TwypZaX48kvpIwoEzRwX6yEIHKgsQpABYAnvX+gv8J/H+j/ABT+GuhfEnw/HNFYa7YwX9ulwFWVY50DqHCsyhgDzhiPc1/mZ/FH4xal8Zv2o9W+IMURgttU8RPOuFKhlln3j+Nwcg+pr/Rs/Yrz/wAMi/DX/sW9O/8ARK189xl8VG3Y+p4fi71m+6/I+lpRgk+vNQIwBq3P90fSqNfkeO/d1tD6qL0NFmDJnvX5Yf8ABYz/AJMl1v6yf+ktxX6iB8g4r8uv+CxRz+xJrZ95P/SW4r3MoxHtcRG5z11aB4l/wQL/AOUe3h3/AK+dR/8AS6evmz4zf8lPvvqn/oIr6T/4IF/8o9vDv/XzqP8A6XT181/GbP8AwtC++qf+givzvxl/jUvn+SPvPDv+JV9F+bPTf2Qf+TnPDH/XWb/0RJX9Bdfz6fsg/wDJznhf/rrN/wCiJK/oLpeFf/Isrf8AXx/+kxOfjj/fof4V+bP/0/6YP+Cjn/JYdF/7A6f+j5a/Paz/ANYP+vkV+hP/AAUc/wCSw6L/ANgdP/R8tfntZ/6wf9fIr+ZeLf8AkoMR/i/RH7Pw7/yKY/4T9v8Axd/yj98Qf9iJff8ApA9fBX/BBX/k2Xxf/wBjTN/6SWtfevi7/lH74g/7ES+/9IHr4K/4IK/8my+L/wDsaZv/AEkta/pXhr/kQv5H4dmP/I1j/wBvfmfuSrqvBNRyTAMDUDff56Zp7gHH0rxKmInKCj2PQkklcf50juVAHlhc++7+VNgBWTYM5K7h6D2qBWgjk2yOylxtwPfv9ar6rq2l+GtPbUdTmKRZwHYFjnBOOAT2rpw1NVmqk90S3yxNaWZEGyRS2R29fSv5If8Ag4M/Yz8G6BBZftO+F5rSyvtRuIrC6twyR3cslw11O8iokIZskABjITu4IPWv6qtW8Y6NpnhW58bzzgWVrZyXTOVYqFRS5baBuPA7DNfw9f8ABXr9s7xD+2R8ZNP+C/w01BL6HTdWhhS3tRcWm4wzzxgt9oZYy2JQCQB19Aa+74Zw9X637S/uLc8TNqOHxNBwqH4UQWTF/wCybuUs0h4EjZYAdmz/ABcc8V/T9/wbW3V9/wAJx8SJUhdrKeHSgQVJxsN51/h61+If7TP7Ifjb9m2+8O6l4ysrmzuNc0aDWSJrmCcYuHdMr5JOFypABO71r6+/4JCftyaR+yL4u8Qpr95a2dpqy2qsbi3uZ8+T9oPAgPrIOo/rX6pnuFeYZZfBvRb9fyPz/CKhg6ntqV7Ret/M/Tr/AII/3mk+Gf29vid4TutJmt9QuBpX2Zri3CBSILp2Kk4YZU84FfgL+3laeJdL/aN8TDxFdXk9xpf2d4oLl3Zh50UecKwz0weMcV+/X/BFHxVe/tB/tqfE741PFE0NhHpDRyW6mNPnhuoTlZSZP4e2Ofavgf8A4OE/hho3w9/ajOveDLcQXXiBY1KKERG+zWtr02BSPvEncfpXxeW4v2OZ1I1tdFb5L/gn1WMjh54OlOl1/pn7s/8ABvbp1ppH7HNppFpb+R5DzsTsClvMvLpu2M4r99wh80sa/IP/AIIq/CLxD8LP2OdHXxTbyW97dtcl1eSOXhbqcrgoT2YdSa/YEnNfF5+o18bKfRO/4HvZbD2eHSQxlyCPWvz/AP2/L2Wx+D8jRtjg8A9f3kVfoJ8oQmvy8/4KI65jwnb6Hvx5u/K89jE30r8548xEKeXt9XofW8M0efHU153PygudMub61NpAR5lzwnXjbyc96/pE+EEDab4JsIJRs2h8g8dSfpX850N2bRIrxOsOevvxX1H4q/bN+JVrpFnpfha4hBXeCV89D2I58xR61+OcK5rDB4l1X/Wp9zxLgKuIUYR2P3SvdX063laGeVY9mCS7BRzXH6h8T/h7o6l9Q1ywh29nuYlP6sK/BPXv2jPjB4iupF1fWLq0wFx9nuJl3ZAzn943TFea3Hi/U7pzJq2r3lznqJpXcfrmvrMy4/qe1k6MfQ8HDcIVJRTlKyP3Q8QftmfBLw4/kvqKXjeltNBJjp/02HrXh3iT/goV8NY8x6RpurSt6pDEy9v7txX5GjU/DNzue6Eav/CRGct65OKtWmna/q83leH7ZZVPTBCH9WHvXzuK48zSastvQ9qhwjg4/wAWX4n334r/AG44fFGlyWMejSvn7puLfOOQf+ex9K+ANYvb7WdYfUpreKIMc4VCvbHvXpmkfAD43+IPn0XS3lC/e/0mFcZ6fekHpXrGi/se/HG+A+3aNKM9cXdt/wDHDXz9XD47MJ/WZxbb8mexQqYDLoulCSt6o+V55LmVRHIYwvtnOaqtbyqMkcevavsvxR+x/wDELRNEfUrrTZ08vJJ+0256Ans2e1fGd7LqGk6/J4e1NQhjYoQTuPB29QSKwq5XWo/xYteqsdNHNqFbSlJP5kR2dnUn0B5pR061Zv8ATYLNhcRZy3HOMc1n+Y3+f/1VxVJacq2O9QUtWStCLgbDUkFotudykn2PIptu5MmPar1csaMYu6GqcUVLpruUqrbBGCDxnNWJJrNoQgt4yw7sg/nTJziOqHmN/n/9VVOtO/LfQxrQjc7bRNG1Txdt0HQbdprgYkKRoW+UYB4UE9SO1ekWX7NfxhvsGLSLhU67mt5wMf8AfuvENM8W+IPB14mteHZmt51IDMjsh2ZyRlSpxwOM1+lHwe/bBsf+EDktvFGoRfao4ioLRXDtkIP4st3z3rsweEpSlebseNjMTiaavQSZ+eXi3wf4l8Da0NL1iIwTRncDhlKMrEZUsFIYEcGsPVtR1S5VTDL9slOFea9JkkVT12NnI9R711/xV8aR+OfH11r0N9LcqxkAQlxGAXLZCvz3rgvMb/P/AOqtKzVKfuHp4SLqwVSr8RbtILa3j2xtJJI3zO0pBOe4B64+tW8mqFu5MmPar1cdR87vI7oxS2IZZo4AGf8AiIX86khYS3C2w4L4wx4Xk4602aewt4Hkv8Y2kJkZ+ft2Ndv8JvB8nxBuHs5A/mKSYvKZVPG3HLZ7n2rSEbvmOHFzrJ3jsLZfDPx5rFzFa6fpl20M2f8AS1hkMEYxkM0gUgBuinua+kNY+F37Ofw71e38G/E+S7OqzDP2iA232dflD8vKqsPlIHTrxWt4O8deOfgl4qs9O+IcMUPhNi0N3PcE3DJHCpEZCRu2SXKg/uz9B1rxjXPDXiL9pH4w3F9oqPe2MXl7HjcR4Bjx0mOeqelevRw3PFJnj1MRiJNqUrQSvdf5mb8afhDYfDO907UNHnivtP1FpBFhhKv7tVzu2oq9W4xmvEbi0e6GzzHt19IDsr7C/aP1zww+iaH4PhmC3+ltOWiVCP8AW7Dy2Np4HY18lV52Owqo1Eos9XLG6lLnqbjtMudf0qQLp2tX0C/3vtDLj8Ritux+KHxD0JIra08S30jKWJH2yQ5zz2YVzdzNFbwNNPwi9cjNdRo2n+EotXguNSxsbPVNw6HttNOOPqxhyX0OyWHpuXO46nK+KLu98YavJqus6rLJNKFCxTzklioAPDZJ4Ga9W+Hvxa8bfCrXn1XwyYmicKNp8wtwCOiOo6saqfG7w/4It7mz1LwoxEibiAqCMZKqP7i+/esL4b+BPEXjjxTFo9lHJLu/hWRV/hJ/iOO1YYOrKlWVWn8RONlTrUeWqrRsfanhL9v/AMf2EO3xLp0UoHXyopGbqf78/wBK9d0T/go34Y8wJremXUY7lIFHr/euK+OvE37JHxe065ku9M0+5dUCkK13b7TnHUbxXkms/Bb4raIpm1TSQqju00TfykPrX3VLiDO6CU4St8j5KWW5NN2f4NH6/wCj/t3/AAZ1eeOHbdxO2eZBAoH/AJHNe4+Hf2iPhX4lx9j1e1iJ/hluIVPfsJD6V/OW0V6J/wCybuFbZn4LxYEi454YE1LDf2WhygQ6zexyj0kb+grSj4i5lTfLX1foctXhPCVNcPKy+8/p7tvFmhXoDWd5BMD3SRW/ka2I76KQZQg+4ORX808HxI+MmmWpv/DWt6jIsf3Q15Iq88cjevrXqPgn9rr45+HXC65diRB186WeT19Jj6172F8RnLWpE4KnBlb/AJdyTP6CvNWY7M1wfxA0ybU/Cl7p8LpmQIFyemGB54Nfn34J/bu0e4uIbbxFfWsW7IJW3uWPf3avqTw7+0V8MvFuns0eoo7HsLeYDr/tJ7V7lTi7C4zBzpSer8/I8WrkmKwlZTlHVH4C2ttc2F/cm/G/DvjaCe/vX6S/8E9tRRNX1LT23AySSzKOwX90OfevgTx5q2lmdrjRNhTziGIQr1Jz2FfZ37AOoxv48uEDcPbyEDnu0VflXD9aMM1jUj3Pv84jOeWyc+x+zJZvOEi8qOCB61JIQzhj2qnYMcyiQ/xHH0q1KOM1/RrqxqwU4ddWfk09BZhvt32nqCK/zdf+CpekeMNO/bq+I2ma3pd1Z2d9r+pXMVzNC8cUsTXMgBR2GG3DJGMg+tf6RCMAmG6Yr+QX/g4Y8P8Ag+3+NXhfTdOsre3utS02G5llihVJZHe4uVJLhecnrnmvueB8YnjYuXXQ8DiCEvqzaP5ftH8VXHhZJrArO9lkqQwJBTgYTkKSQOBX7kftBfHz4Y/Er/glJ4O+EfgqZv7Y06/0q+mtnaLzPLt7GRHGyN2fdlgACoGeCRX4a+KtM1TT9ZfSLyEJbxyZQ5BYhSQM4P8ASvUvhPFqml3sup69PM2lKGMccj+ZDxtI/djOOAe3Tiv17MsPCtKNOC91fofnmDx1SnRkptf5n3H/AME8/wBpHw7+yLrnib4l+PbW8NtrfhnUdHtoLeNTOtzeLEY2dZJIgIxsIYhiw7A1+fHxd+JOofEXxhrfim63pb3uoTXEWcj5XJYZyzDv2Jqj488aXmv+KJrO0Kpp+5gqxhkXG44+Unjg+lW/Bfw91v4o+I4/BPhy3a4Ih89gjpG21WCk5chf4h71jLLqMOar9qW4sNiKkVbpudJ8Avhv45+Knxh8LfD3wrbebcald2c6TBJGiRXmSMGRkViMFgT8p4r/AEwv2Y/COq+APgB4Q+H2u7ft2g6TaWFw0eTG0sESqxQsFYrkcZUH2r+FP/gm5Jb/AA9/be8LeANRtoTc209tassyCR1K3kCH5l+XII6jjNf6DWxYwsFsoUdTtGK/L+Na/wC/pxi9Ej73h1P2dSTW7JHG/wCUcmq/2aSriokXOTz61LX5/XwcKr5p7n0nNYzjC6jmvy5/4LFDH7Emtj3k/wDSW4r9U5fuV+Vv/BYz/kyXW/rJ/wCktxXVlOHjSxKUTOu7wPEf+CBfP/BPbw7/ANfOo/8ApbPXzZ8Zv+Sn331T/wBBFfSf/BAv/lHt4d/6+dR/9Lp6+a/jMc/FG+Hun/oIr878Zv4tL5/kj7zw7/iVfRfmz039kH/k5zwx/wBdZv8A0RJX9Bdfz5/sg/8AJznhf/rrN/6Ikr+gyl4V/wDIsrf9fH/6TE5+OP8Afof4V+bP/9T+mD/go5/yWHRf+wOn/o+Wvz2s/wDWD/r5FfoT/wAFHP8AksOi/wDYHT/0fLX57Wf+sH/XyK/mXi3/AJKDEf4v0R+z8O/8imP+E/b/AMXf8o/fEH/YiX3/AKQPXwV/wQV/5Nl8X/8AY0zf+klrX3r4u/5R++IP+xEvv/SB6+Cv+CCv/Jsvi/8A7Gmb/wBJLWv6V4a/5EL+R+HZj/yNY/8Ab35n7hYy341IUyPpUY+/WiuFSvHwVJVHJPod89ikI2ZgiNtI+bpnIr8g/wDgtf8AD7xr43/Y41C58F3nkT6dcG5kHlo2IobW6LH52Udx0ya/X92wzzIMuqHHNfhH/wAHAvjvWvCn7D0A0uXyZtQ1m3tpFwrZSa1uwwyVI7dsGvoMjwnta0aS6s5cZWdOm5I/jRj/AGnvjlpXw+l+EmoeJfK0Lym3t9jgbdciPyhHxHvGU/i3bR9a/bL/AIILfsG6z4/+I8v7Uvj7T/8AiU2SPDbXPmr89wGtbmM7I51YZGTgxlR39K/KX/gnJ8ErT9or9qjwp4O1aPzFs57S7nXJG6KK6gV+Q8eOHPQ59BX+jV4K8HeG/hD4Lg8P6FD9l0vS7UFm3M+BCgB4Ys33V9T+dfcZ/i/7PUcLQWrWp4WEwrnepJ6M/lX/AODiKxgh8d6bsOXHhi22D/Z+3Tf55r+UHXLmGze3S5XYDu3Hr2HpX7o/8Fwf2tLf48ftS3OieBZPOsPDlo+iTtjbmW2vJ2PEkSN0YdCw9zWr/wAENP2SPhR+1f468XL8V7b7WmjJYskW+aPPn/agfmhli/uA85/CvpMBmNTKsklOavz2t/XzPJzDL6avCnq5M6T/AIIm/wDBQ34T/sj654s8K/GWb+zLbVY7EQS7Zpt3lG5dvlgglIxvXqR1+tepfs0PqH/BWn/goVq/xW8dQ+Xo3hX7MyR7geLmzlgzmP7K/JgU/db8Op3/APgrx/wSb8Kfs++HD+0R8Dz9itrIZvYvnl4Jt4E+a4unPV2Pyp9exrC/4N+f2hPAXw48a+JNC8Yz+Vf+J1tI4jtkbm0+1sfuRsvQjqV/GvKxeCpYjByzOi7zlvbyFPB+ww9LCt7b/M/s50OxtNHsI9PtfljjUKo5PT65Nb4AOMHOa54HzGJTpgHPsa2bXcBlq/I6eY1KmMnRnHRXsz7WjSUaUbEE4ZeR0FfjD/wUV1Ge38Yaasxxb5k3H0/dxfj1r9p5QX49a/KX/god4TivbCy1fGSpk/8AaQ9f6V+feIGFqQwvNurn1vCkksfA/L5o91lJ5/EeBk/j7c1mwX8Ntfrp6SYb6e2fStZrWbUbeS2tuHAG0fz64r6Tvv2dvjorW+o6dab4HB53246ADvJnrX4VQw9ao2qZ+q47FUYNc7SOZ+GngH4beLrx5te8UfYS2Nq/YpZd2N2eVYYxiv0F8E/shfBG9C3Juv7T/wCATw56/wDTX/OK/LS68E+PNBZ4dZtvLZOp3xnr/uk+tcDd63awXgjbUvIlHbyS3b6Yr3cvxUMKvZ4mjztddf6/A8nGYaviFzYeu4rsrf5fqf0RaR+zX8G9OC/ZtH8sjv8AaJzn85K9g0jwR4Z0aMR6bbbAO29j/NjX8+fg/wCOvxQ8Cwn/AIRfX9izcOPssRyFzj76N6mvoPw1+2/8VdMUDU0/tLHvDD6+kJr7XLeLcno29rhF9yPkcZw5mk/hquXzZ+0721jbDONmfqavJFHt3KOlflVpP/BQ65tz5fiPw95Ib7jfaw2cZzwsH0r3jwx+3J8MtZI/taX7F6jEsnr6QivucBxfktSHOoqN/Jf8N9x4eIyDMKatODbPta5gSSJlYZDDBHsa/Mb9tX4Q+C9M8PSePD+4nkk8sH942XYSOP4yByPTFfW1t+0z8GddlW107V/NkJB2+ROvt1MY9a+cf2zfGXhjxF8JUt9Kn85/tcZX5XX/AJZyeoHrXzXGOZ4DE0Jewav5WOnI8NiqWKgpRaTfZn42wARKZp35z8ox/D61YS4hcFkOce1dL4VstW1/XodDdcJJIsYPB5LBR3Hr616j8UvgD418Cpa6/a/PBIEY/wCrHXce7seg9K/GJJ3dz9d9rBOMW9XseF2l7azXJt42y4BOMHtWpVu4vr2VUtbqLZhQc7geR9KqUrM0GSKWXC8mq5glHJFXB1qR/u1jNWlc5azfNYisru40+bzYx94bT0+6evrUV9qExuV+zJvRvvHOO/vUrLxlulMDKOlb06/KEcOpCSypKBFAmBjJOe9V/Lk9KsqVJ460+qnPn1R0Rp8i5SCJGVssMVPRRUWZRJFYWd+4F4Nwh/eqOeWXp0qle3l3cXcd5p95/Z01qwKDyxNu2HIHIwMn+VWobi2j1Czs7l9gvbiO1U4J+aU4HT/631r648R/spfEDw34Ni8Z+GpvPjmiExXbGuFKljy8pPQDtWsMNiJe/Ti2vQ562Y0KLUKrSb2OE8HftL659jTwj8SNP/tfRHRIpx5q2+5YxlT+6j3jLBTwf0r2eLwvqEmsR/Eb9k/UvtN1HzNpnkhe2xf314cf324Xtj0r4aubye21H7H4g+W4VmUL1yw+9yoxV7wl8RvFPgvVGbR2xG+M8J2B/vKfWuiONr0dWv69TzMRQhUu6Ol919l+q/ysz334/wDjXwn4q1WGSbT9viAcXTea58r5VCHGBG2VH8PTvXzpWpqFw1zczahfczXOOf8Ad+nHSsusK2KliGpvc7sDS9nT5SKfPlHCeZ/s5xn8amvBa61bCG+h8rb0+Yt/LHpSUVjZnaVLiy06GEXMj8xdOD34r6T/AGUNYv8AS/j5ZQ3jeVbyHg4DZxDIewJr50kltYl33hxH36/05pyajrHhG7j8TD92wyUb5T229OfXuK3wdZ0a8akldI48wwDxFCSUrbo/p4iVtoMb8t0GKW4hSVTHONx7ivxp8E/8FA9W8OaTFo2oWn9oXsmREu9YuhJPIgI6epqDxD+3p8V7gmXSdN+yn186F/T1gr9jp8d5e8J7F0vePymXCuO9to9PU/VjXPh34J8QRNpusWnmLPwRvkGduD1Vh6V5Rrf7JfwL1KIm60nB9ftFx7ektflnqv7X/wAbvE1nJYz3mwyYG3y4D0OevlD0r568Q+Lr/V7j7R4t1nYx6r9nB/VAPavlpZ1ls+ZVMMm3tov0Pfw3DuYU0rVuX0v/AMA/Q34j/st/s+6KZBHrn9ly8bR9muZsdP8ApqR3/Wvizxv4T0LwnMU8L6l/aq/9cWgz0/vk+p/KvJk1rRGcNYXXnkd9jL/MV1ukeCfEvjGULo9n9pJ7eYqf+hMPSvmcdTVeX7iny/f/AJn0eFwWJpR/eV7+tjJgl0uWIyXlpmYdB5h5/EcdKXRLJo9fjt4IfIZ+i7t3YnrXv/h/9kX4weIZkszov2WOX70/2iF9mMn7vmjOcYr6Q8N/8E3tNGqxa74jvv30fbyz6Efw3GP0rHDZNj+VtRlb0MMZmGGotKtUTfrc/MLwzDPDpNzaFNzSzkHnGARjNfRP7N/i2TwT8YtIs7Q/JcGK3b/feZBjkN6fSvN9Y8KP8NL5tN0c+ak7kv8Aw4BJU/eLdhXvH7JvgtPGHxYgunXP2KdZuv8AzzkjPqPWpySnP66vU784q05YBtbH7yWsjyxRuwwTgkelbMv3KqwQYAA7fyq1KflxX9KZVSlTpS9p12PxSs77FVQGOxuh4NfyOf8AByXpl9b+LPBviC2g2NFBaW8T7gcnzrplGCeMnuRX9cyAL+8boBmv5SP+DkvTrm5Twbq1mMrE9ix6dRJdnuf6V9fwfCo68Yx3bOHM6kI4eTmfn3+0z8IvDT/sL/DX4lalD9mnks9JW7n3M/71oJHYbQ2OeuVXHpX42+O9dtrqOLRdMuP9GGBGdh+YcgdRkZHqa+gfi/8AtY+PPHfwh0T4KRJ/oelC2JGY+kCNH/zyU9G/vH8a+V/Cejf2rdnRHbLapdi3zj7nnYX1GcZ9RX77hF7JuNTr3PyHEJT/AHtPZdC3p+katrKRaVHb4iTBeTeOEHBOMg9D9a/WH/gmxpb+Ef2hdui2v26I+G7hpG3+Xt/ew5OGJJwB2r82viFpsfwts0+GaS+ZIGEvm7cfdzHjHzenXdX0N+xT+0zY/AH4oza3rn7xbnR5rMHlcGR48fdjf+76fjW+MleN4sywClzcz+G9/wDgn3D+xjPHq3/BXbWNRig3ldbuFI3Y2f8AEwhPtnH61/enHGFJkHJNfwJf8E47pvGP/BTGb4jL8sGsX8l1H34mvoHHofzUfSv78U+6K/D+Kaco4lOe5+q5PUhKl7g0hn4YYqSiivl/U9cjl+5X5W/8FjP+TJdb+sn/AKS3FfqlL9yvyt/4LGf8mS639ZP/AEluK3wH+9Imr8B4j/wQL/5R7eHf+vnUf/S6evmz4zf8lPvvqn/oIr6T/wCCBf8Ayj28O/8AXzqP/pdPXzX8Zjn4oXw90/8AQRX5p4y/xqXz/JH3vh3/ABKvovzZ6b+yD/yc54Y/66zf+iJK/oLr+fT9kH/k5zwv/wBdZv8A0RJX9BdLwr/5Flb/AK+P/wBJic/HH+/Q/wAK/Nn/1f6YP+Cjn/JYdF/7A6f+j5a/Paz/ANYP+vkV+hP/AAUc/wCSw6L/ANgdP/R8tfntZ/6wf9fIr+ZeLf8AkoMR/i/RH7Pw7/yKY/4T9v8Axd/yj98Qf9iJff8ApA9fBX/BBX/k2Xxf/wBjTN/6SWtfevi7/lH74g/7ES+/9IHr4K/4IK/8my+L/wDsaZv/AEkta/pXhr/kQv5H4dmP/I1j/wBvfmfuGqln49avDlQtQRHgj3qYcHNc+Aw8YwcluzqlN3sU2hSG4bUHP3EIx7da/hH/AOC5v7aesfHb416h8BtBm83TvCmsPaTLtVdtxZzXEZXLQox+VxyHYe5r+7mRZXv+f9WYiD9Sa/g8/wCC7n7JWq/B39pq6+MGnpjT/E80moSNkf6+6ubl8YMrt91OyqPYV9ZwnGnHHRbVtTgzWbVB2PrT/g298J+F9U+IXiXxPrEO3WbKO8tYxuc/uF+xvn5SE+92IJ98V+//APwU8+Pd3+zp+x74r8W6bzNqFreWEZ4+/PaXBHVJB1TuAPevyt/4N1P2f10P4V618dLyX9/fXctuse3rHNBaS5yHI6jGNgP8q+K/+Cwv7UPxG+Pn7Znhz9ivQn8vRX1u0t7k4iPS9mtGPzRo/wByTtL+vI9bNaP13O5xWqT/AAPKoVakcCqkUfzja9qF74livPG92+yfV5De3HAP72Yhm6ADqewA9hX9Gn/BuH458GeF/HfxGm1+48qK/h0pbZ9kjbzF9s38KpIxnvjPavn/AP4Kl/s3eEf2VNA8MeBvC/zzT+FLG6mPzjdN9oeNj88koHCdjj2r8M4NRmtLqPT/AA5J5N9cZBXG7O3kcsNvTNfouMw+GzHKPY1NKcba+h4kVOeIjKOrvc/q5/4KGfHPw7/wVD/ai8IfsSfCDXvs2kPLdrdz/ZWfzt1rHdKNsyWzrte3YfLLz9OD+ZX/AAUk/wCCc0P/AATK1zwX49+HWs/bJrt737Ov2cx72jjhV+Zbi5Ax5x6qPb24z/gjn4S+Img/tzeAZtC0Py2We9N5cfaYjuBs7nZ8rE4xnHy/jX7Df8HJfxC8NHwx8OvC6t5moQy6mWXDDbvWzYc7dpyPeviadSnRxMcBg5c1F3vff+u3/DHpY3mkvrafv6adO2x+6P8AwTx/aks/2tf2adF+Jm/N1J50cy4PHkzSRDny4hz5fZf8a+6d421/PV/wbwvcr+y1NZzH5Y2YqOON13dE1/Ql5W8Y9K+FzalCjXqKitnofQ5biJV8PGpNWbEZWPvX52f8FAba7/4V9HeouUj35ORxlohX6MAZOK+Mf2zdEfXPhDc7BnYB+skfuK+G43h7TL5Ra8/yPpskrunjacl3PwugvTpEC3l0dipnc3XGeBwM561/SH8Obj+0fDNhcxS+Yjq2TtxnGa/mv1+MPBJbDvgH8xX9Gf7O+pwar8ONPuU5xvH/AI8w9B6V+S8EUoVMc6cv61PtOMqbVGnVX9aHqd74R0bVQ326DeDj+Jhn8iK8L8Tfsq/CXxZvOo6Z5btj5vOmPp2Eo9K+oAckj0pa/ba+QZfU/iUYt+h+fUcwxFJ3pza+bPzO1f8A4J0+AZpmuNKvvsxb+Hy5H/ncV434o/4J8+J7IH/hGLn7T6fIqen9+f61+ytFeFieB8tqu/Lb0PYw/FmY0dp39Ufzx+JP2Pvjl4ZaM6joP9oxyZ2f6TbxbcYz0lOc5/SvKdZ+DvjTRPn1LRvsmP8Ap4jk/k5r+mqWFHOWNY9xpiTnLru/GvlMf4b/ALxyw85W6f0j1KfGuIk71YRf4f5n8t15ops8PeTeVhhj5d3zduhqnaaxJPayWd/d4ggug5Pl/wAKjk8DPSv6f9R8FaFq9uLXU7bfGGDAb2HzDp0I9a+PP2nPhF4L0j4b6prNja7HRJXPzuekbnu5/lXz2bcGYrB0HW1du524Xil1qqhyJXPxbSYC6j1PSr3EcMi3EbeX1CHI4P8AWvXtV+NXjnxNo0GhXr7ogirGcRjIwQOiA9+5rx2CXRpbKIXc32eOSRYN21n+92wK/S3wF+wz4R+IXgLSPES6rtaeCGQHyHP3lB/57L6+lfEZXl2MxtV0+XbsfXY/MMLhaUalbf0PzZlu7oXTC9bhSVPA+9+FT+Ym3fniv0k1P/gnLb20c/8AYes4mZyQfs56Ht81xj0ryi6/YF+JmmTSTJcfa0XJA2RR5H/f8121uH8fCTXs3ZeRzYTiPA1F780j4uS4hkbYrc/SpLueOzQNdHYp6d/5V23j/wCG/jv4W6kx1HTdsRJiMnnRnqT2DMe1cTBNpsn76c5lPVeev16V41fD1KcuWqtT1KUqdePtabuilcz/AGu1Elid4yBnp/Os0i/xjb+orZnuo5G2Bdo6gZqv5gU9M5rndI19vKnsiCy+0LJuuBhcfrWp5kfrVHzQeCKb5n+f8itacbKxcazqLmkrGh5kfrR5kfrWf5n+f8ijzP8AP+RVjPZvgzr3gbw/45t9W8eR+bZQgMvzSLtkV1IP7sEnAB4xiv1Dl/bb+COm2UOn6ZL50SoqEYnXaOmOYcnH1r8VvM/z/kUeZ/n/ACK9zAZ9WwlJ0qcVr1aPEzDIqGMqKpVb+TP0R+NPxq/Z28d6HM8Q8m6LFxNm5baWZSTt2KDnGK/Pia4ngill05PMj/hOQO/vUcEi+aN4yPTNdZoeja34mnFlpK8noPl9z3I9K4cXj5V3zTil6HVhcup4aNoybXm7nJSana3FvHd3j7HizkYJ68dhV+M+dF50fK+telaj8BvjzpxGoGx83Tk5k/eW44PA/j3dT2rzm/upIJv7J1aLyJBwRu3e/bj9axjhpOlzxR1qrFr3GmQwOtw/lw8se3Sren2d1qt21jp6+ZKuMrkDr7njtVSJ7/T5FskGbeT7zccd+nXrUll4Y1Lx/fx+C7KT7PcSk/PgP23dCVHQetceG551OWeiJ9q+p3fwn+Hb/GPxjH4Lsx5iE4nOcbBtZh1ZM5K9jX3j4g8BfBj4t3Y8DeDb/wC0zaWB9oXyp02eaAV5dkByVPQmvM/FF3J4a0bT/wBln4cRZ1XxHvje83f6rysXIPly5VsjK/6xcdfausTUdc+FKwfAj4VL9ovr3IuLv5U2H/XD93MGBzlhw49fQV9bHD0KdNU2rp63637L+v8Ag/NZhiq8665Jcq6K/TrJ/ov6XwprHgC+8EeJ7vStVs/9GkCC2k8wfMQMvwGJGM9/wqh4UsNK1LxAun6td/2fG3fyzL2J/hwa+tf2pfiF4nt9MsfhfHP5yQ72vJNiLxIEkTjb68fK31r4tnurm9hFtCu1h/FkH9K+brUo0q/uM9XDU5zpKpJ2f6dz9N/DP7DPh/xZYRapH4j82B85X7Iy5xx184EcivdvDP7CXwfsow17F9uZf4t00fr2E9fBn7P/AO1Rqfww1az8P60fNs3LbzgDHDMPuxM3UjvX7T+GPGuleNNHj1vQpNyvnHBHQ4/iA9PSv1/hLB5Ri8PfFQXOumup8TnmJzPD1NKr5Oj0X3nF+FvgP8OPBsqXWgad5U0OSp82VuTkfxOR0Jr1u3021iXO3b7dadE321kmHyeXnI65zxWgyHbX6VhcnwNOPNQpq3Q+Rr4qtUf72Tb82VGtIWH7ptp9etYeuS/2faPNKcpgc/iK3olGT7V5r8XdTXRvBN5qDdIlU/m6j39a4c2jSjgKlbkSa7BhU514U3sz+dDWLoal4glu559kaM6/dzk7sgdBX3J/wTzgeTx5q97tzEvnxhv9rMRAx1r8/LmTf5s//Twa/UP/AIJ46AyW2pavjIa4c/msR9f6V+G8NR9rm8VbS5+pZ7TjSy+UUz9WIi5Cq/ULyKVkJzzSdZc+1S1/SXKpvkeyPyWRGvyIc9RzX8hX/BxT8etP/wCEu0P4S6aN99HaxXrpyMRxy3UbNkpt69g2fbvX9ecpxz7V/D7/AMHGfhqTRP2u/Dmt2HzyX3hsAr0x5t5c9yT6egr6fgqooY9K3ws+c4lv9W5e5+BWlHVdbm+1aJH50gP7xchcDvy2P0r9lfiV+yl8N/hZ/wAEubL9orR7jz/EGr65ZLcfLKvkSXNi8rJ80rRvtZR8wQA9sDivk/W/2ZtT+Bf7JGnftF3p3t4int7dV4G37bbtL1Ej5xs/uL+Ffov8UNRk1z/gh1pbS9R4r0wf+UuT6V+oZ/mtWFWmoaao+OyjLqUoTlI/Pn9in9mE/te+FfFVmn+kavo2kX+qRj7m77LEhA/1kSDLP1OfoR0+FfFvgm68IeJrjw54jT7Pf2czQmPIfDIxGMqxXqOuTX7Tf8EYBLZWHxXa24dfAniFlPuIIcda/Ohfh/rPx08Y67ougL5uo6VbXWozHKj5bY/N95kXqw6E/Q1rQzKpUVWM0vdcUvmY1sFCjO9NtJ3PuT/gjx4y8Bp+2RoWk+N737E0dtHb2w8uSTfdC6txGn7scbjnk/KO9f6Acf3BX+bT/wAE6vDt/cftqeCtFb5Lyw1nT5Juh4ivIN3fHX0Jr/SWj+4K+A4+hGOLhy9j63hZv2U15j6KKK+CPqSOX7lflZ/wWLOf2Jdbx6yf+ktxX6oXH+rr8sP+Cw//ACZHrf1k/wDSW4rTAztil6fqTW/hniX/AAQLx/w718PEn/l41H/0unr5s+Mo3fFC+x6r/wCgivpP/ggd/wAo8/Dv/XxqX/pdPXzb8Yz/AMXSvh7p/wCgCvzHxoqNV6Xp/kfbeHlR81R+X+Z6T+yE2P2nPDA/6azf+iJK/oMr+e/9kI4/af8AC4/6bTf+k8lf0IVfhS75XW/6+P8A9JgPjlWxtP8AwL/0qR//1v6YP+Cjn/JYdF/7A6f+j5a/Paz/ANYP+vkV+hP/AAUc/wCSw6L/ANgdP/R8tfntZ/6wf9fIr+ZeLf8AkoMR/i/RH7Pw7/yKY/4T9v8Axd/yj98Qf9iJff8ApA9fBX/BBX/k2Xxf/wBjTN/6SWtfevi7/lH74g/7ES+/9IHr4K/4IK/8my+L/wDsaZv/AEkta/pXhr/kQv5H4dmP/I1j/wBvfmfuTGME1NVVSQePWrDkA/jXNl1S8WjrraaiSywjakpwcjFfh/8A8F6fhVqfxE/Ywk1TRLH7RdaTqkd1I3mBdtrb2t2zt8zKDgnoMt6A1+4KzRh9nfGfwrxb496LYX/wX8XWWsj7RDc6ZffLynDQOMZU56d69fAYt0q6nF7Pp/w5y4qn7Sk0fiF/wb3fFLQPF37KWsfD83WNTsNVZUh2N/qorS1Tdu2hfvHGNxNfzgf8FSPCHxU+F3/BQLxd4q8RD+zZ7nV72/0l/wBzN5lsb2VonwpYLllPyuN3HIq58E/2pG/Ym/bV8W/ETQ7b7RpljqV7by22/ZwtzG5G9opm+7FjIU+tH/BTr/goj4d/4KAeJ9K1fw/ov9lTWOnpGzfaGn+48zH71vAP+Wo7Hp+X6Vw1gqksyjiWvcnqfO4zFwo4b6vHe9n5Hwj8Rvi349+Kl5DeeONW/tG+tYFtoV8iOHEasW25jRV4JJyck1+vH/BDb9j3wV+0v8btU8Z+Mp/3vhsW7vb7ZP8Al5juox86SxjogPQ+nFfhpYyLA1pppO+Zohk9OQDn27V/UR/wbNhz47+JaueY4dJz+Jva+l46l9WwEoYf7W5xYKUYYqny9T+qHxrrngb4EeBLjxZrUX2PSNFVWaXMkm0SuqdFDufmYDoa/wA4z9sP45+M/wBpL48XM3i8/wCnI4+xQfJ837pA3zIkaj5EB+b6da/rp/4Lwfthf8KN+B+n/CDTRvufG5niY9Nv2GS1mHWJwchv7y/j0r8Zv+CAP7Fnh740/EHxH8VPiYn2waWLZ4ly0f8ArftcZ5imT0HVTXwWSYZ4bBzx9Ven9fl+p61dxdTk6M/of/4I0fs/eLvgH+xvo1j8QrL+ztevGujc2/mJLtQXc7RnfHJIhyjg8dM4PNfrpExC1QstOtNOs47KzXZGgwBknH51ejzjFfn+JlKWMVTpK/6s9vB01Cl7PsToOc14j8ZtAuvEPw21DSLSPzLhgmEyB/y0U9SQOg9a9sCseVOMVlXtutxbSLIM78Z/D8q8/OsJ7fCVI+TOzD1uSrCS7n8v2oWktxqk1rEMszYUfTrX7NfsMeKzqfw/k0Bnzd6fzKmPuiWSQrzjByPQmvyf+J2mHw18TL7S8bfIKEd/voD6n19a+1v+Cd3i5E1nV7C4ODeCBRx/c809h/hX89cNVnRzZJ6a2P1/iKj7bKFUXRJn7EROsg8xOQalqOJVRAqdKkr+lG20rn44woooqRDHCn71ViyIcd6hu2Kvgd6qb2Jrz8TmvsZciiawjc0gcDpXhv7QukPrHwk16EJ9yyuZDz2WF+eor2+IkDAri/iFYtqvg7V9N73FlcRf99ow9vWuLPsSquXz03R04SThWjJdGfzLWEY0gTXEfzBJSfoBznv6V/QX+y5qo1v4M6DdQy522tvn5f8Apmpx0HrX4O+PtHl8P+KdV0CQYEUky/8AfJK+p9PWv2R/Yf1tbz4S2Wn/APPssSf98Rxj0/qa/HODcwjDNfZzW599xPT9phI1F0PuKe2WcYJx71VW0KHaG3fpWiSxjyemKgAJPFfv0sDQm3UcNWfnMJNK1ziPGHhPSfE+mvpesDfFICuORyQR/CQe/rX5ifHP9iSWxmn8X/D2D7XL80jRb9mPvMfmkmx2Hav1w8tZDiTnnP41HcCAqY3GBjHWvhs04Po47mr2s/xPXy7O8Rg3y0paduh/L34gs7rw/qr6d4jj+y38ZKmLIfABIPzLlevvVESArlehr+hn4o/AbwL8T9Jex1K3xIzeYJN0n3sNjgOv96vyf+Mf7J/xH+GWozat4Mi+1WGWP3okwuWP8crN0Udq/J854YxGHb5Vdd0fomU8R4bE+7VdpeZ8jAFulCjc2wdap3ivFqxbUF8m/wA+W6Z3cZ55Hy9a0dR0BZrVbjfzgE8f/Xr5KVCdN8s9z6jmpy1pvQHgljXe4wKhpsENnb2IEZzJkA9emKb5n+f8ipsxEyqWOF5p3lyelJbvmTHtV2izArR+bE4kVckds1s6drGqaDcjUdIkxKP4cD6dSCO9Z9FFmB9qfD39t7xn4bWK28R6b9qt485HnRpnOf7sJPUivetf074GftR6Q0ug3WNXwP3Wy4+U5A+83lIeENflgc4+XrWPBezR3obSrr7POP8AY39vfivcwOZKlQlRqarz/R/0jxsRldFT9rQ9yfdbP1Wz/PzPSPHnwv8AEHwy1v8A4R3xIPJWc4QfK2cAN/Czeo71yyalqnlHVdv2e+i+4Mh89vTb09RXoS/HPxN/YkngPXYPPjvAFM+5F27Tv+6qZPYda4S9uIpxmDrXlzqx57o6Yc9vf3/M92+F/wAZvE3hjQbrS/D1p9t8RXwVYW8xI9pRiTwyGM5QnqR+deuXuqN+y54BPhbR1/tfxNq+TMf9R5e1xIv3vNjOVcjgjp618e/DPxA3gX4had4vvF3w2TOzLnGdyMvUAnv6Gvqf48fDzTvHeh/8Ls0+6/exjiPYexWLqWUf+Of416UMR+5ckzy8Vh4vEx5tn+L6L0PjG40K3jvbm4lXzWvdvmz5K7NvI+XPPpxj1q+L2fyPsGzEf9/I+vTFVpEl1u2tdWn+RoGfK9c5+XrxThkDjtXie19pK9z6eNL3CGa1tLW2eJD5ofGW5XH4V9Gfs7/H3VPg7q6WuoTb7CU/N8oGMBz2R26tXz0yCQbG6GoZ5YGUR4+YdK78NmdWjVUoO1jzsThqNSk6VRXuf0teCPHOi+NdHi1vQpvNhcHnaw6Ej+IA9Qa9ASVWGa/nz+C37Svif4Z+IrK3vTu0xS3mj5BkbWx0jZvvHtX7geBvG2leOdFi1vR3yj57HsSP4gvp6V+1cMcYKtBUp/F/Wx+UZxkdTCS5re50Z6GsiSgtCd36V8o/tf8AizTPDnwj1S3vJvLnu0QQLtJ3FJIy3QEDAPevrCKUSx7h2r8uP+ChviSNdIsNBjPzsZc/+Qj6f1r0+N8b7LKHNP4jnyOh7TGwXbX7tT8pJIJ4tPljkXDhjKRn+ADk1+2H7Bvh6+0v4UDVLyPYl86zRHIO5HijweDx074NfjLqMXnXlraW45uXSD/vs4r+hP8AZo0BvD/wc0XTnGGSzgH5RqPU1+f+HmCdfFe27H1/FWJaw6h3PeghXDH0xUz9arRy7m8s9R/SrL9a/d6DUm2j84kVCf3m2v5Kv+Dg74da94d+MGgftA3Ftv0WDQl0vzt6jF289zMqbdxf7oznZt9+1f1olv3+33r+c7/g4/sv+MWdKvT31mzj/OK7r1eEa7jjpuPRnkZ7Q9ph7M/HL9orXNX13/glD4V1vWY9sM+taUIhlTiNrGQg/KAfXqM16P8AESSyH/BDzSxZtuB8WaYvQj5v7Lf1rz346w+f/wAEb/B7DkQ61pCH8NPkrp/GzY/4IeaX7eMdL/8ATW9fo+NqOtUi3/NE+Zp0/q8FFdbnP/8ABGxS2h/F2QfeTwJ4iUn0It4a8c/4JnXqaF+0j8VNWtl+0tH4G8RSuM7NpUxE9c5xj0r17/gjY+zw38Z5PTwP4lP/AJLQ14t/wTXXzvjH8Ybz/qQfExz/AMBiNdq92WJuvtQOOque3ozuP+Cc+gp8Tf8AgoLpmt+C/wDSb+K6S9vIfueXCl7A0jbnKq23I4UZPYV/fzH9wV/CP/wQb07+0v2/9VuMf6vSr1vyubU+tf3cR/cFfHcczUsXB9bH0XDdL2dGS8x9FFFfDn0hBcf6uvyw/wCCw/8AyZHrf1k/9Jbiv1PuP9XX5Yf8Fh/+TI9b+sn/AKS3FPBf738l+ZNb+GzxL/ggd/yjz8O/9fGpf+l09fN3xj/5KhffVP8A0AV9I/8ABA7/AJR5+Hf+vjUv/S6evm34xn/i6V8PdP8A0AV+Y+NX8ej6f5H2Xh78U/67no37IX/Jz3hj/rtN/wCiJK/oPr+e/wDZCOP2n/C4/wCm03/pPJX9CFaeE/8AyK6//X1/+kwNeOv99p/4F/6VI//X/pg/4KOf8lh0X/sDp/6Plr89rP8A1g/6+RX6E/8ABRz/AJLDov8A2B0/9Hy1+e1n/rB/18iv5l4t/wCSgxH+L9Efs/Dv/Ipj/hP2/wDF3/KP3xB/2Il9/wCkD18Ff8EFf+TZfF//AGNM3/pJa196+Lv+UfviD/sRL7/0gevgr/ggr/ybL4v/AOxpm/8ASS1r+leGv+RC/kfh2Y/8jWP/AG9+Z+4iqS24djUxIY8GnJ92q7LtfPvXBT/2eCnHW+51T973WPNsolE/8WNv4Vn3ulwXttPYzr5kN0jRSrnb8rjDc5z09K2eXX8KiKqoya9CnCmn7VFqKtY/lC/4KX/8EL9Q8Ra14q/aP+Amq7bm6W91G60vyAcu3nTs/n3F6AP4VwEx3A6iv5Mtc0PVNL1O78MavceXqGiSPY3ybAdksJxIMqSpwe6kj0Nf6vl5Ha6hby6XejdBcRtG68jIcYIyMHoa/hX/AOCzf7Lmi/CP9tPRbrRoPs+l+LNQgurg7mfm8vZw33pGb7q9tv4Gv0jg3iivzrDRSlFJ2fU+Mz/KqdKDxF3dtbn57+O/2crb4afCvwv461b91Nreg2erWvU+ZDc8BuJGC554IB9hX6X/APBDD9qL4P8A7LemfEfx58YNW/spdQi0xbb9xNP5hhkuVb/UxS4xvXqoznjvjE/4Kj6t4O8N/Dj4W+HvCkvm/wBmfD3SLULtdfmjmkGMuD29zX4Ii21nwtpKalpj75bst+5wo+4f7xyO+a+xlBYvB1HjE1H/AIPn3/I+ZjUlTqReHd5Pa+vR9tj7c/bp/bK8R/tZ/FS/8d64/m6Jp2w2E+FXl0jjk+VYon6oB8wPtX9MP/BuN8OfEfhv4JeIPFeqxbYtVEAhbcpz5Nxdg8Bie46gV+TX/BNb/gjz44/aU8Tw/Er4rp9g0ax+by8xy+Z5izRnmG7jdcMqn7pz9M1/b98OPAXhP4VeH7TwP4Qtvs9rACFTe74ySx5dmPJJ718RxBmtGOD/ALMwz91dev3n3mXYRVcPTrVvjuehQoDAApzSgHfTXf5jGOgpycpzX5rCrCdRUl9k9yMLajJJZISCi7gevOMVSuDKYgNvB+9z0x0qzMvyAVIIhLEQO9c2JrVZ1KtCO1v0F7OOjPxG/ba8CP4c+JsHiZY9tpqhKo+c7jDFGDxuJGCe4FeK/s4+LrXwT8RNO1CSTy7WJ5DcPgnaCj7eMMTkntX6Oft6+BZtd+G0er2YzLp29l/7aPEvdgO3vX432d++lXg8v+Pr+Ar+dcfhpZfmsprvc/XMqx31zLVh6nax/UtY3Sz2qSL3AP51N9qizivBP2dPHkPxA+G1jrxb538xWGOmx2Ufwr6ele8OsTNuHWv6ByrMVjMJTrwktT8rxeHlRrSpvoywJARmnBxVeOTPFPNe3GCZzWZDPH5h4qKO2Y4z2q2BmnjC59q5a+BoTfPIIykmQyROFHlDJyM/Sql/CHjKuODwfpVxpTnAqhNKwmVTXFj6lCVF0raGtNPmufz+ftZaZH4f+M+pLeDyvtks0kXO7KvK+OmcdO9fYH/BPjxbpktrf+Gnl/0hHdlj2n7qrEuc4x1968//AG+PCUdj8QLHxZMuYpIFjJz/ABNLI3rnt6V45+yN4jOgfF5Ps5/dXR2/99yR+oJ7V+DTVDLs9vHXU/TPZxxeT88371vyP3zSRZFG3sMH608cdKzrCbzIww6MM/nWljjNf0Rl+KjXoRqI/MZK0nEYEDNkmpW3bMKMkVA6t1X1o/eUq+J9k+RRYKHmSeXuQZXBPXmqklgsqtHL8ytkY6cGp/3lH7yvPqzpVPjpFK62Z8gfGf8AY+8E/E8S6jCnkXpJcPmRsn5jjHnKvJPWvyb+NP7Ovjn4KXZ1LVbf/iVmXy0l3R8klsDaJHbkKTzX9ELebgY9QTXxn+23Y2mofCV5bkcx3Kkde0cvpivz7i/hnL5YeWNpKUanbofWcP57i6VWOGunFvqfh7c38EtmsmzYpYKGznJPbFV2ikVBIw4PSqsR1SMTNpq7g0hjxkDGe/NepXPww8f6b4Kj8Yrb+dE21vvxrwULf3s9vSvxOOHxPO7pWP0v6zytKo0rnm7OtnGLi4O1G4B68n6VLHcwTDMbZ/CrNi8kqC9Rdl2VwyZzgHk89OtUboa0ZTJIPl/4DVSTTs0dM2217NqxOJoy2zPNSk45qCG7JHkyDDHj8qlJ2jms3VgviNqdGb3IrhDNCY0OC3Q1BaW0BO26gwf7+/8AoKtKxJPbPSpKynTp1mpRbJrUEmrkUqyRn7PHJmFvvHGMf160v7uM5Rs49qJuIyaz/M/z/kVpGkkrXMvZI1TqMsXKx7wO2cZqGzutTsdKS3S83rli0HlgYGcj5v1qh5n+f8ijzP8AP+RW6m1T9n0J9jG9zVuZ11e9e7uD5PkhfKT724kYbkYxj3qJWAHJqpC+ZAKv1hTpKGiOtVWlykcr+XGXz0qG5tUWPzkPzGrqK7sFj+92pdK8PeKvEXiD7DaDI4/udwT3I9Kfsm1pscNWlBzU5sojUozZPb3o2hse/Q+wr2/9n/8AaI1v4H+JorLV5f8AiXXJ9F/hDH+GN26sK8hRtX0DXntNRbyfKxl8K2Mj0Gc19JeLP2dF1nwm/iD4PX32u4VQZR5WzGWAH+vkA/vdB/SvUyxVMPNThuZY5YatT9nW2f8AW5+3HhXxponinTIdX0mXejDOdrD27gfyr8YP2vPHeleNfjOLXT5/OtoNoB2leTEmeqqeori/gL+0d4q8A2SeHIB5sLMy3L/IuzBYjgxsTknsa+er/V7vXPEN5rl197KkdPp2A/lXt8Q8U4rG0IYGcUlu/XY+VwORxweLlJSvHp/wTvvhHoF74y+J+kaPZR+c8d1BOy5C4jSVQTkkevTOa/o88LaedI0S309BjykVSPoAPU1+OH/BP7wO9/40uvE1+v8AqI5I1Oe4MTDo39K/aqz/ANT+NfofhphFSpylbc+f4sxClWVKL0QMpE4ftipTlvmpsgxmkVsp+NfqkOWMnFddT5J6q5UZD5wev59P+Dja0aX9jfSbiMZA8SWGee3k3df0Izfcr8Dv+DiK3Mv7EWnSf3fEdj/6T3ddORx9lmDjHrqcmYLmo6n4bfFq8trz/gjVo8kLblg8T6ZAxweGGmyHFdD4wRrr/gh7pqw87fGGmZ/DS3rh/Gkfn/8ABF+LH8PjTT/00x69B1n/AJQf2P8A2OGm/wDprev0eT5Hp0nE+TxHv6Pomcf/AMEebeaPwL8bb5hiNfA3iZd3+19lhPTrXi3/AATFuIF8d/GC5nbr8P8AxLg4PPyRV73/AMEhDt+EPxyb08GeJv8A0kir55/4JjReb4h+Ls/X/i33iT/0XFXoQqOdTERfWUTno006lKD6xbPrH/g3oRZ/25PEE7dDo2oEH/t4tK/uOT7tfw/f8G61uZf2ztfkH8Oi6h/6PtK/uBT7tfE8Yv8A25x7H0WSq1OXqOooor5I9oimGUxX5W/8FiTt/Yk1vPrJ/wCktxX6py/cr8rf+Cxn/Jkut/WT/wBJbitcDFPFL+upNb+GeJf8EDAW/wCCenh0elxqP/pdPXzb8ZDj4oX2fVP/AEEV9I/8EC/+Ue3h3/r51H/0unr5r+Mxz8UL4e6f+givzPxnpp1qX9dEfc+HlNc9ReX6s9K/ZCH/ABk74YP/AE2m/wDRElf0HV/Pp+yD/wAnOeF/+us3/oiSv6C6PCpWyyt/18f/AKTAjjh3xsP8C/OR/9D+mD/go5/yWHRf+wOn/o+Wvz2s/wDWD/r5FfoT/wAFHP8AksOi/wDYHT/0fLX57Wf+sH/XyK/mXi3/AJKDEf4v0R+z8O/8imP+E/b/AMXf8o/fEH/YiX3/AKQPXwV/wQV/5Nl8X/8AY0zf+klrX3r4u/5R++IP+xEvv/SB6+Cv+CCv/Jsvi/8A7Gmb/wBJLWv6V4a/5EL+R+HZj/yNY/8Ab35n7nRbdpzSTKMBsUidKcelY+y58Ml5HalZ3KRnEblSeev4VKrLOpYcgdarXBeMqVGcsAamuJrezt2v7g7IoULueTgKMk8V5eAoVKs3Bv3excpWER0wzxncRwB71/LH/wAHImpaAfCPhq0jfbryz2ckceGP7kG8Gc42ff7Hn8K+2v2uv+C4vwE/Zo8cap4Ct7X+1b3T4Z0Z/MuINtxEzptwbKQHlOuSOa/jZ/bW/a38SftqfGe5+LmsxfZLAzutpFuWTCNNJKgysULdJD95c+voP1fgvJ6lHFxnKNkk9z5LifExlQdG+t0eCXfiHxFdwQ3GvTec8Eaxqm1Vwo7ZUds9a/ZL/ghx+xj4X/af+OGp+MPiK27/AIRsQSSWuHGBcx3UY+eKaPrsB4DenFfkjqHgTX9L8KLrurJsjuokuIeVOYpCMH5WP6jNf0j/APBs5N9p8efE2Uf88NIH63lfccTV0suqKDPlskjbHU0+v+R/XXounWOj2qaPpqeTBAAFGS2c/U5/WtaSY5IUcetVoYSDu9asGPPFfz/iKmKmpNLq/U/VOWK2KmSxq/Eh2U1YMfNUoJTg9a58vwtSnPnqBOStoNKZXNIFdW+UU8sTxVWS4IPJrvq1KdKXtJdTO7ZzHjPw7Z+JdIl0m+GUlAHfsQexHp61/NN4i0O70PxfL4e1aPybqFyWTIbAYbl5Ukcj3r+nzzkcfMa/HH9v/wCGMPhXULbx1o0WDcb9/wA39xYlH3mPr6V+Ucf5WqlP63S/rsfU8M41qt7Fvc6b/gnz498rUta8DXku1UWD7IuPvFjK78heMf7R+lfq+krdjX8zXhfxbe+E/Etj4q0mTyxaljLwDncpUdQfU9Aa/os+HHi7TvGfhe21iBt4kBzwR90kdwPT0ry+BMY5YeFGpOzV+ptxPgalPESqpaM9HSRyTjrTncqPSqIlUTNDaJvK4LDOMZ6dadLdkJ+9j2/jmv1+vOnTo3VSz9dPyPkaXPKWqLkch2/MaeWU8GufbUEzujk2Y68Z/pXP6l8QvDWiqf7UvNn/AGzY/wAlPrXBS4gwlKmoV56/L/M1eFrSl7iO9bByR1qu6F1BxyK8H1T9pD4W6ZGXk1Hocf6mb/43XnOs/tlfCzTVJW48wj/YlH/tE14+L4ly3Vc6O6lleLltTf3Fb9tbwVb+J/hNLduv720fzc5PCxxyn1A71+MHw21658KeIbLWk4ghvIlZ+OAGUnjBPQelfpn8RP22/h74k8L6loVmu95beZRzIOqMveEevrX5QaxNLrlvJe6cNoNz5oXr798V+VZ/jsFUx/t6fZH3WTYSvHByo1Yta6H9Kfww8T6f4o8F6drVlJ5iy28bFsEclQehA9fSvRmuoAOuB9DX4EeBP2y/Gnw78M2ngzSI/wB9BGnzZj+6ihejQsO3rXRXf7dPxuu1K2snl5/2bc/zhr6nL/ESjhqSpqF/u/zR85iOG8R7Rtbf15H7oi8gY4U5/ClN1EoyTX4CXn7XXx71EFbjUPKTOc+Tbt/KIVhzftL/ABtnBC6v1/6d4P8A43VVfFRqVlS/r8Tpw/B9etHn50j+hRb63dtinn6VJ9pjr+cfUfj18ZLyLZfal56Zzt8mBefXISuel+L3xNl58/P/AAGL/wCJrL/iK0v+fJ0f6jVf+fqP6VWv4FB+bk8DjvXxt+2Sy3fwkuLVf9b5u8D2Ecn0FfjbH8R/iRfSATXvkBPnB8uJuR/wGqWu+OPGHieD+zdXvvMRfl/1SDjkfwqPWvJz3xEWNwjoulZ9z08p4OdPEwlKqtHfb/gnMaXdanbadcWkYxM05CdDnIwPbrX7m/sneZf/AAYs7DWU3S+VGrpnHHlIDyvFfhlZWV3Yh109t0m0noB/Ou08O+NvijpGnSw6ZfeQozgeVE3YeoNfM5JmaoTdarG6PoM94clXSjCpY/ZD4tfsjeBPiWnn27fYLkfNuxJLn7xxjzVHU1+cfxW/Yo+IfhCaS78Oj7Zaplt37uPgbj0aYnoK8is/ij8YY7QynWvJYOCW+zQtnjpjbW3H+0V8WLOH7LPq/n/9u8K/+069HH5ll+JftKVLlf8AXQ8zD5fmGEag66kvNfqeRzeH7/QZza6uuycEqBkH5l69CRUqxsegzXW6z8UvG/iy2fTtRfzY5/vDEa55z2UHt7VxUljrEh/dQdP9pa+Vxapy6Hv0fbW1kTmCZFLv0FV2miT7xqsNE13cGkTYo6nKnH61I9rqkX3Dn8q4aULLQ7UpfaYxru3kHlo2Se2KZTDcaxnZPH+67nK//rpPM/z/AJFaWY2SUVH5n+f8ijzP8/5FFmInjIDgmrPmR+tZ/mf5/wAijzP8/wCRRZgasOlza/KNIs32yzcKcZ6c9yB29a+0/wBnn9mzxx4H8aHxD4/T7Np4CkSZjfPyuOkcjN1I7V8L/wBpT6b/AKdaj95HyPx47it3UfEfxLh8Qwa819m2fjy/Ki/hXHXGevtXdhpKMfePMx9GrU0pytofdfxj+HnwS8FeK57nxrqG6S6CeTa+VOPN2quRvRmC4BB5xnpXq3wG+Lega3oGr3ugeH/7L0iFYglz9rafzDuYH5GQOuGH459K+JvjL8R4PiZqml37jBg3A8/7Cr/dX0rxfQfHXiDwp4Ru9B004SfAx8vZy3dT6+ta/Xowd4o8p4CpUpKNV3en4eg3xFrN34gllnsLb7CY3YmLeJfNBOOpA24xn9KrRW5/tJRt/cycZz6D8+tQ6fBNZ29v9p+9KXz+H0r1f4JeBdR8cfFy00kLutgfm5A6xsfVT1FYUJRxOKjGJ04tOlQdSXQ/Z/8AZ38DaZ4T8My/YY8NLOGJyehRPUn0r6ljGyMVy/h3SLfR7QWsI44PfqAB6mujY4Ff0twzlqwmEUn2PyHG4p1arkxHkJOKWP7h+tVC3zZq3Hyma9XC1XOq2zB7DJs7OK/Cn/g4KtJr79he3aBd3keIbRn5xgLbXea/deX7v41+Jf8AwXotw/7CU5/6jUJ/8lbqvZytf8KMPkcmN/gn88+uXEFx/wAEWnlQ5VfGliuff+y3rutRYXf/AAQ/tfs/zeV4w07d2xjSnzXnckXnf8ES7nH8PjiyP5aU1d/b/wDKD8/9jhY/+mpq/Q628v8AHH9D5KfX0Zjf8EiI3PwW+Olwo+U+DPEyg+5s4q8M/wCCXCbbn4v3LDAXwD4kQn/a8mI4r3z/AIJC8fAT45H/AKlHxL/6RRV4P/wTKPkaX8Ybg/xeCPEf6wRV3UFetX/xRIofxqC/uM+v/wDg3MQTftd+JL1eUGkahHn/AGvNtDjFf21J92v4sP8Ag3AsiP2ivEl/jhrC+H5vaGv7T0+7XxXGC/4UJnvZL/Dl6jqKKK+VPZI5fuV+Vv8AwWM/5Ml1v6yf+ktxX6pS/cr8rf8AgsZ/yZLrf1k/9Jbit8B/vSJq/AeI/wDBAv8A5R7eHf8Ar51H/wBLp6+bPjN/yU+++qf+givpP/ggX/yj28O/9fOo/wDpdPXzX8Zjn4oXw90/9BFfmnjL/GpfP8kfe+Hf8Sr6L82em/sg/wDJznhj/rrN/wCiJK/oLr+fT9kH/k5zwv8A9dZv/RElf0F0vCv/AJFlb/r4/wD0mJz8cf79D/CvzZ//0f6YP+Cjn/JYdF/7A6f+j5a/Paz/ANYP+vkV+hP/AAUc/wCSw6L/ANgdP/R8tfntZ/6wf9fIr+ZeLf8AkoMR/i/RH7Pw7/yKY/4T9v8Axd/yj98Qf9iJff8ApA9fBX/BBX/k2Xxf/wBjTN/6SWtfevi7/lH74g/7ES+/9IHr4K/4IK/8my+L/wDsaZv/AEkta/pXhr/kQv5H4dmP/I1j/wBvfmfuWmcVKATUC9/pTo+WrhoYqSSgkd8kI9zGmUXlsdK/N7/gqR8XvEvws/Y/8SeIfBv/AB+NDc2z/d+UNaXBP30YHBUdq/Re6hdmQr/eH5V+TH/BZb4p+D/h1+x/rWm+IZP32piW1iTD8tNa3KqcqrDqO+Pwr08kdeeMVOpFJXRyY6u8PT9rTV2j/Pl1O41nWNR1DxZrN/mXWbt7+ceUv+sm5YfLgfkAPava/wBnP4T6r+0v8ePCnwqtm8yFr2yOMAbk+0Rx92jI4f8AvV4/o2l6hq+s2ejabH5sut3kVjbrkD/j5YKvJI7nuR9RX9vH/BIf/glv4c/Z08Dad8avG6ed4g1O2juo0yy+WsyQSYzHcyRnDoeqDPoBX7Xm+b0stoRjSS57H55h6FTMcXUqYhbvoflH/wAFkPgpofwH0jwT8PbKLyJNM8E6dBKNzNmSK5kjJ5d/7vZiK9a/4Njtw8a/FCF+HWHSCR9Te4rR/wCDjWGaf4m6TdJwo8LW2R/2/T1T/wCDanC+MfiXPD1lg0kf98m8r5r21XE5TWq1nqenhcPyZnClbRbfcf2CI6rEFJ5Gc00XMPds/hWLrV/pumQtf3knlRW3MnBP3sAdOfyptpPDqECX+nt5kLZwcY6cd+a/JcbicbQhzqMX9+3fofbU6kXN0zooZUkUSRnIqKaVU+XqaWCPYpweO1VJULvtraeIboKT+JiqL3rIuQNvXntVO4tpm+dRxV2JPLXHrUoGTipqYNYijGNXR+RS90yordm4kFeJ/tAeDNM8Z/D+5s9ZX5lA2cnu6Z+6R6V9DP8AdrGnt4LqN7G5G9DjI6Z79q8rNMvhPDfVXqnpf8jfCVXRqqtDdM/lp1XTVivp9Bkba6n51xnAPI5z/Wvs79nz9qy8+Dmhy6J4htPMQY8seYFzlnJ+7E/94dTWd+1N8E734e/EKbXbVP8AR77bt5H/ACzRM9XY9W9BXy6bu11AGJ/vD6/4V/N2awr5bj5xoSaSP2LB1aWa4WP1mK1PubxD/wAFCPiRqWxdD0n+y0BPzefFP5nTs0Hy4/XNeWa9+1h8dNZjMjXXlJ/uW7en/TIV8vK9uWMdxwq9OvevTfAUHwnmvVXxhNsQ9flmPY/3PwrpedY/EQtUm7erOiPD+Bwn7yEL/K5nap8V/ih4uO3UtR84QcgeTEuN30UelcJfazcXj7dWm3n/AHcfyFfpx4O8MfsUX6+Wy/aZB15vkx19+a+uvBHwt+ArRiXwpZYB/wCmlwfX++31r08DkdbGRVSNVa93r+R42L4ip0JNKg//AAGx/P2ttoYPmldpPG7LH9K6LTfDt1qeF0q1+0Z6fOEz+ZFf0m2nhDw3ppDpDtXoPmY/1NdVZ2emoMW4zj6/1r6HDeHuIxDs66T9bnnS44lFe7QR/OJovwQ+KXiK8jstF0DczsOftUQ+UkD+Jx61N8UPhj8QPg99jPinTvsccxQD97HJksW/uM5/hNf0irHHHlkT9a+Rv2t/h5p3jv4dXV5PFuuLNGkQ7iMeWkhH8Sjqe9VnPh5hsDhHWqVnKp26WM6HGVevXSdNKLPxS0XRJ/HXiaz8M2s32C6ukQxPt83eWYKFwSoGSepPFfW+j/sF/FC52/btU8oMAf8AURNxx6T18WaTcar4e1q21vVf3cOl3se08H/VMGHTJ7ehr+gr4DfELSPiL8PdN1iwbeRDEH4YfNsVu6r6+lfN8O5BhcVVcK0n/XyO/Os5r0YRnRSt1uj8/Yv+CdviG9AgvvEG0D5sfZFPP4XFblp/wTfnix5niL/yU/8Auiv1dFzGX8onnGaV2z9w4r9XocDZTShytuR8h/rRjn8MkvkflxJ/wTltpohFP4h4zn/j0P8A8kUsX/BN/R1wG8Qf+Srf/JFfp+hl8zLnjpU+/wB62jwfk/8AK/6+YnxJmHSr+C/yPzDb/gnJ4aYKbrWvORSCV+zuvA75FxXj37Q/7HvhL4YeBT4k0274BCA7H5cq7Acyt/d9MV+zrfPC/wDumvin9t44+COP+npP/RUtfPcT8KZdQwcqtKOyudOWZ1jamMhzVN3Y/CrRbN4TIt3P5MYB3Sbd3ycZ4HP9a/RT4L/sUeFvif4Jh8TT6tmO7RWU+Q/8aKw/5bL6+gr89uttOv8A0yf+Vfu9+xmxT4IaMo4/c2//AKKSvg+D8Jg8finQqXsfbcR4rEYfCqtTqPmuj59tf+Cb/hixDJb61tyev2dz/O4psv8AwTr0xG32+v8AP/Xqf63FfqO6Bsnuag8lq/TK3AOBUmkn+P8Amfnz4ix0nzSqa+i/yPy4vP8Agn3PPaPaR6/gNgZ+y+h/671xd3/wTk15ATZ+Id3/AG6L/W4r9e/JajyWrJcA4NbcxrDibGx2n+B+LV3/AME7PiKVYRar9pH/ADz8iNN34/aOPWuZvf2BPjHpybrO28wf78A/nOa/dCJCmc0y4ZIoiTxW8vDjLZ0XUnOUWl0aS+eh1U+MswjJL3WvNH81/wATvhT42+D81va+PYfsbXJYRLujk3bQCeY2fGAw61581tMjBGHLdOa+lf2vvFcHjn4qCzvx5lnYkEr9378adwFPUe9b/wCxX8AtM8ZeJpfEPiHTd1km3H74+ki/wuD1A7V+NrKp1MdLD0vhvZX9T9Fp5lGng1iMVva7t+h8jQKbm4NrB80g7dKjmdYJfJl4b0r9w/HP7Fnwc8Y2NxDY2f2O4mCgS+ZPJtwR2MwB4FfG/iX/AIJ8eLNEnMvgxvtY7DCJ6f35z6mu7E8LY2m9I3Xlr/X3HnYfirA1H7za9dD4HCkkKOpprkIcPxXpnjn9n34p+F7v/ic6V5AXrN58LbeB/Cshz1xXnD6Tc6P8t/1H0/oTXg18POjpPc+hw2Mw1ZXiwEUpQyIu4D3xVfTtNkkntJ9SuNojZy6bc4B6ciqzSTM3mr9wUfaWIzjiuSE5SlZbHZKNK9ollF1CURJ5eGQt3HertnORA6yLyO2feqdi73N0kC9Wq5YPHJHLOfuxYzWGIo80uWDOequRXY1tWjuNDuNSujtFsAc9fvNjtX7G/sU/CnUPDXhIeKvEdr5NxPnYd4bhWkH8LEdCOor81P2cPhjc/GX4h2Pkp/xLY3fz+R02OB/EjfeXtX9EGgaDb6DocWi2vCRjjr3Oe5P86/VeAeDqddvFVG7x27XPz3i3OZRSwlO1nqzTSNwvIxTnRtu0U2784Kgi/vDP0q5X7rooOlbQ/OOXXmMsxPu3EVcjUBMZqSX7lRp2rnwtBU6tkzTdDZUIXPpX4w/8F3Ld5v2ErnyxkjVI264/5dbqv2l6qw+tfj1/wXCtftH7DF7GP4b9W/K1ua9rLY2xsJ+i/E48Y/3DfY/mv0do7r/gidqaRcmPxxaqfYjSmrs7NhL/AMERJIo+TH4xsg3sRpTVxnw+j+1f8EX/ABLEOfK8dxH/AL50o11mjf8AKFG9/wCxytP/AE1NX6LWh+8lH/p5H9D4qtWatbqmQ/8ABItGh+AHxxlk4U+EfEqg+5soq8E/4Jvyxnwn8XZ4zkJ4M8Qo3+99njr6C/4JM8fs5fG4/wDUq+I//SGKvn3/AIJvQCP4dfGO5I4bwn4hGfrbR16lOioV8RbpOJvNKm6E1vyn6C/8G3UUd18V/EN5AdwS1vI2Po2bQ1/ZMv3a/jq/4NpbZk8a+J7jsyXn6i0r+xevzriuo55hUb7n0eVUlCjddQooor5o9Mjl+5X5W/8ABYz/AJMl1v6yf+ktxX6pS/cr8rf+Cxn/ACZLrf1k/wDSW4rfAf70iavwHiP/AAQL/wCUe3h3/r51H/0unr5s+M3/ACU+++qf+givpP8A4IF/8o9vDv8A186j/wCl09fNfxmOfihfD3T/ANBFfmnjN/FpfP8AJH3vh3/Eq+i/Nnpv7IP/ACc54Y/66zf+iJK/oLr+fT9kH/k5zwv/ANdZv/RElf0F0vCv/kWVv+vj/wDSYnPxx/v0P8K/Nn//0v6YP+Cjn/JYdF/7A6f+j5a/Paz/ANYP+vkV+hP/AAUc/wCSw6L/ANgdP/R8tfntZ/6wf9fIr+ZeLf8AkoMR/i/RH7Pw7/yKY/4T9v8Axd/yj98Qf9iJff8ApA9fBX/BBX/k2Xxf/wBjTN/6SWtfevi7/lH74g/7ES+/9IHr4K/4IK/8my+L/wDsaZv/AEkta/pXhr/kQv5H4dmP/I1j/wBvfmfuSoJ6VJEcEr3pisRxThhXzXHSpcvJNdTtm9BspZSoXn5hnHpX86n/AAcP/B3xh8Q/2e9N1nwrpk+oLY39s85gillMcccV2zs2xWAUAjJOAO+K/owcZBI6kda8z+Jnwz0T4r+BNU8C+LYY7i2v4ZoQHVXx5sbJn51YZAY9jXvYGuqVaE30ZjWgp02j/OH/AOCfVjpF/wDte+ANP10qIrDWtMhId9m2ZLyDC5yDu9jz7V/pN2Sx2dmk1n8yRW2UjT5nOBxgd/av4dP2ov2JIv2JP+CmHw1Hhm4VdM1/xFpN+sAfCAz6mVGVSGFc7YwO/wBfT78/4KgftYft8fs+ftD+HPE/wzgvk8EQ6fC901ouoi3KrczMxcxSxw58lBnJ6deK+v4mjLERo16b0kjwsOqNGrZdTzz/AIOJtRkn+Iui4jkXzvCtq7Ky4Kk303Deh9qj/wCDZowx+J/iSJnVHSHSsqxwwy15jIr5U/4Ks/tEeH/2nfAfgf4jaZqEUuoS+CdMW/RZVZku2uHkkVgJJGBG/kM24d6+n/8Ag2zhW68WfE64gI3yQaQCf903lehVw86eRqLVtNTgxlZRzOk493+R88/8Fyf2qvi3qn7Yeu/A/wCHWrvZ2+mxWTPDLbQMrefaW8o2tseQ8gk5xj6V/WP+xJ4Z8aeEf2YtA8O+OlMWqgTsyOhjbDzOw+VlU/dIPSv4w/8AgsE11P8A8FS/EGgaVpkk2p3kWnhfLhLFtmmwtxt+Y/L7Gv7vPhPqNxrPgTS9S1KExTiEDa67WGPl6HJ6Cvls9w1KOCpcttU0/mj2sI3Ot7S2h3MCPHI4PTjFWogPvCqFm07W++fhjnj8fersDZO2vzrCazjHoe3NpPUsRkN1qwAAOKq4KAmnJIe9e1WxChPkZDjfUs1m3NqzSiePqPzrRByM01nx0pTpqrHlZKdjyz4leBdJ8b6QbC/gEkgH7vc7Lg5Un7p9vev55fi34H1jwB43ktdVspbNWI2+YjqD8oPBcDPWv6Ybld2JQPmXpXw7+1n8AIvib4ck1zTY99/CPlwuScmNeMIzdB61+ZeIHC0amHeMoL3up9Xw9mzoSVKT91/gz8ToYYpJmEw+9jaCcE/T1rq5PCWoWlmdQ1Lw7qbWx/5bC3kEfp97IHXiuGuNP1ix8/Tr0NFfWeNofcpO/wBMjd0+lfoT+yx8aNB1nSx4K+K9rFIDwouEUqcl2/5bOfRe1fjWUNzq/V6uh+jV8xkqPNGN/L/I+EZtN8MTwC4srCa329WlLAH6fMelZcWsajA/k6PMFHsA39DX9Bv/AAz78F/E1inl6HZRRclTHbQKDn/gBHavPtS/YU+Cd9ua3Etozd4PIT/2jX3q4KzbkU8NrHpqfPQ4py9e7Wg0/Q/FjT/GfxW0SUXuh6pHby/dJaNGO3qRhoz6V6joH7S/x20AAy61vx/zztYG9PWIelffOtf8E4fBdwzS6XrmqKxI4a5jC4x7W9ecaz/wTt1exjLaLqc0xA482Yt/6Db15OJyPiKh8MZfK5r/AG5lNR6xX3I8u8M/tvfFGG8ji1u9u54RjKx2dvnqOfujtmvddP8A25fDutRXHh/WLbVVeWJ4/NktoEiJYbfvCQeuenSvnnxJ+xR8YNJi8yzHmDdj9z5xkxz6QjivMNR/Zh+NunqXOnahKB/dhnb/ANpiuWNTN/ZujiObm87mHs8sxFTmhZL5I4f4h3i654x1C7tsz2U5lcInLZZjgkjtj3r7C/Y2+NXhj4dWs+jeKJzYW+W2GYxxoSBGowzsCeh/Kvj+7+Efxh0RXuZtIv4kRSZHkt51XaOTzsHp3rgL+O71fZp8TFHgYeYpJByvXjn17ivPpYqvgaik1ax7NfKqWLpezjLQ/oP0z9pb4P6oWji1+ytyO811AoIHcfvDXU23xw+FU0fmDxPpRUd/tkGP/Q6/nXk0fVZgv2KN9qR7Syg9R7gVJaTX9lZPZXjyRk5HzEjnGO9fSUeOMQo7X+88X/UhP7Z/RrH8YPhfNF50XiXSmTONwvISM/XdVuH4o/D25/499f06T/duoj/Jq/myi1fVrWBrFbuUR7tw2yEc9PWrlr4n8SW/Frqc6fWZh/I1v/rziOsPxZL4H/6eH9K1v428K3QZLfVLWQlT92ZD/I18lftqanp158GPItJ45XNyjbUYMceXLzgV+O6+O/H5hkMPiW4svLUtv+2SR5x2zu71Q1j4neMPEWhQaVrHiOW7QOg+a8eTPBH8TH1rkzTjOpiMJLDyhuvMeD4RlRxMZe02dzDaKRLWYOpXfGwGRjORX7kfsealp8HwV0iCWeNJEhgDKWAIIiTqK/EO9lkgsPKhjku2FuW/dDzGGB1/D1rV8PfFHxnovhVYdP1rUtNAYAItw8OPl6YDD/Ir5rhDMY4PESrxjc+ozjJJYvDKmp2P6XW8RaTEf3t3Ev1df8aryeMfDMX+t1C2X6yoP61/N7L8V/irfQRBfFGqEbQd322bJ/HcapP49+I7j/SvEmqPz/z+Sn+bV+gy8Qqsm24fmfHU+CJSV/aI/pDk+IPgqIFpdXslA7meMf8As1Yv/C5vhPvMX/CT6VuHUfbYc/8AodfzpHxr4vHXWtSkPpJcuVP1GawrPULy1ke5vLudnb1kJPH1qHx/V6Q/M2jwL3qH9Hl38a/hKIWdPFGkkr1xewcf+P18/wDxk/aH8Caf4OuDoniCynu8DbFBPBLIfmXou7njJ+lfiLp99ctv8x5yrf3ydv481Uh03ULrUDrF/LthTpuYjtjuMV87nPHWIqe7FWurdTfD8J08PUUpyvbUdr+qr4pvZ7jUleUTn94CNpwOmduMdK/Sv9nP9pv4WfDLwGuj6hBeJImflQRnq7H+OUHvX5weHdN1vW47yfRNLuL04QKIYGkY8kcbQc11dn8M/ijqYxF4b1OP/tzmH/spr56hj8XRmsTCLufQY/C4avR9lVlZetj9OdZ/b9+HNoVttNtNW8yTI3rBAyrjB5PmnH5V89+Jv28/GlzemPwvdX1vH28yztiOg7nd7183237PfxlmImt9Dv2bsr205B/AJXaaR+yl8c9XbB0kWue80Fwn/tI17P8Aa+Z4hLkUvldHgwy3LKL99p+tmXPEH7T3xO8XQNp2t6k9xbzcOgt4FJAwRyqA9QO9fPevar/bUpZCTjrkAenpX2Non7B3xsurqJri502FMnOXnUjj3gr2Lwz/AME+L60Vj4jvYnLf8+8hPr/eg+lck+Hsxxj5nBnfSzjLMKrRkvkj8w4LmxjsZEeVABjJ3DA5781FbxJeKXs2WUDqUO7+VfsLoX7Anws05Wl1eS7l3feRzC0Z69Q0A/8A118eftGWfwe8FTf8Ij8NYYBdrw7QrB3CsMmLaehPaubE5JVy6mo1lqzow+eUcViEqF3bfsfHPnm1zcWzB3j7J8zc8dKiuILzzF0rTo3llucjZGpdvl56dfyqS28P3YY2unuZ7qXoqEseOegGelfr1+zl+yboSabB4o8a24a65K7kUkcsvPmRZ6Y7118P5JVxldKCudWe5vSoYe8mes/scfCaT4a+A0W8tPss9xkkEyZ4dyMh8Y+9X21WfY2UNlCtugGF4FaFf0ZkuXfUcOqR+K4zEOvVdR9SOOXzP4Sv1GKkoor1bu2pykcv3KjTtUkv3KjTtWdP+OvQ0WxL/C341+S3/BaGLzv2IdVZRuCXRckdgLW5yT7V+tBGVYfWvyu/4LCWxn/Ye8SRjnas5/K0ua9XAO2Jh6r8zjxavRkfzD/BySKT/gjf41JYbP8AhORtOeD/AMSrjFdBoREv/BFG+8r5tvjK0zjnGNKbrXH/AALj+0/8EZPGcK8+V42B/wC+dJrq/Av/AChP1r/scYP/AE1Gv0itrVm/+nkf0Phq6+H0ZP8A8Emlc/s2/G5wDtPhXxGAe2fsMVeIf8E7Vjj+DfxeuTgL/wAIxr8Rbt5htEwv+97da96/4JLHH7Lnxp/7FvxD/wCkMVeHf8E8rUn9m/4yXOMhbTWx/wCSaV6dSTjVxLX88TsxMbxoPtE/Rv8A4Nq7WX+1fEt4I2KJ9qjZsHAfZaHaT0B9q/r2r+Ur/g2q0118H+MtSx8ratcgH6w2h9K/q1r814oVswq+p9Jlb/2eIUUUV84eiRy/cr8rf+Cxn/Jkut/WT/0luK/VKX7lflb/AMFjP+TJdb+sn/pLcVvgP96RNX4DxH/ggX/yj28O/wDXzqP/AKXT182fGb/kp999U/8AQRX0n/wQL/5R7eHf+vnUf/S6evmv4zHPxQvh7p/6CK/NPGX+NS+f5I+98O/4lX0X5s9N/ZB/5Oc8Mf8AXWb/ANESV/QXX8+n7IP/ACc54Y/66zf+iJK/oLpeFf8AyLK3/Xx/+kxOfjj/AH6H+Ffmz//T/pg/4KOf8lh0X/sDp/6Plr89rP8A1g/6+RX6E/8ABRz/AJLDov8A2B0/9Hy1+e1n/rB/18iv5l4t/wCSgxH+L9Efs/Dv/Ipj/hP2/wDF3/KP3xB/2Il9/wCkD18Ff8EFf+TZfF//AGNM3/pJa196+Lv+UfviD/sRL7/0gevgr/ggr/ybL4v/AOxpm/8ASS1r+leGv+RC/kfh2Y/8jWP/AG9+Z+5WORikkVi4Ip6dKmQAnmoUFOlE7JPoVZ2Zo/LjyD1zVPzpYgVbJKqW59quXDbCmO7gVNLAsm7I6oV/OuJ0J1qvOp2sUmkrM/iW/wCC/wB8dbmX9srwOvhC4SPUvC1tZ3yyo5VVksr65YIzI+4NuwdvynHcV9Gfsd/8FUvhb+2zoNz+yd+0voECavcaXLpttql1axCFpHVLZGWe6uZmLl5WYER5xyBnIPnX/BdH9gT42658XX/aC+GekPqWkQafI915UE8zLIJrmdj+6gKABMfefPPpzX82ngTXPEXhDxNZePvD93/Zet6HdxPNaySNA5a3ZZGGxSH+8AMEj04NfsuS4anjcrp05SvOB+e5xTr0sbeHws+3P+CgH7IHxO/ZU+JWreEdS1KS70HUt+oaU6zSyRJp7zvHCoJiiQD5OAgKc8GvQv8Agmn+3lrH7AF/4p8TQaLc6zb3kdmLgQWzXAURmYLuxPABky8ZJ6fng/tmftx3f7Wfgnw5deIrdYL7QvDtno87BSvmSQSmRmy00rHJfqcH2r7z/wCCDvwE+FH7SHij4k+GvivpEGqW1vBpOxLi3hmX5zdE8TJIP4F6DtX0WI5aeUyhjVbTpqTzyli4SSu0zqv2avjX8DP29/8AgqifjN8T7Wz0iGEW+xNTS3gWX/iXSQkASvOG2+WD9/jI+lf18+M/ip8IPhNYWj+PPEWkeGLOUMIZNRu4LKJ9uM7TIyg43DOPUetfwq/8FLvg/wCHvgh+3u/w2/ZRg1O31K3WEwxeH0VGBksYpDuWzRGHDPjC+vvXzp+1j4q/4KC6l4a0xv2mn8ZQ6faeb5P286kq4bywf+Po7f7nT29q+Ix+VYfMqNOdOraFrW7W7H0NLFTw8Ypx1Z/o0+HPEXh3xho0WveE9QttTsZ93l3FpKs0T7SQcOhKnBBBwetbEULocnivy9/4I6zarqX7DnhbXtTvLq6NzJfqBdSF2XZdzjv0/Ov1NdWJxXwdfLKWHruEHflZ7dKftoqT0EkLEYFRBWPFToylsdanwK82rQjVqOonodClyqw1AR1phVhU1FdlN8qsZsozytDjC7s+1V5LWOaLy5V3KeoIyKuTgFuacnYmslTVSc+fWPY1T5VdH5Ifta/suzWd1F478G20jHLG4SBMjACIuQkXuep+lfnLHc6rcMNU0idLa4tycoHKNz8vReema/pz1fTbXVLN7O+RZInxuVgCDgg9Dkda/HT9p39lO+8G6hL438AQE2XBkgiU+iKPljiA6knk1+G8ZcNvBYh4rDLTsfecO50qzjQxDt5n0F+yj+1bpniWyl8J+OJYre5twvkl2CNIWLk48yUlsADoOK/ROO5tJ4w8eDnp0r+X6z1SfTL6PxDozm3urck+XnYwyNvRSD0J71+pH7L37XB8Sv8A2D8QJ1t5uiPK2wfxnrJKT0A7Vvwx4h4iHLg8QrJaGfEfDajUlicPqmfqCNyjcxX8KbJK6jODj2rIspEu5BfQTCWFx8oRtwz68cVrpMjHy2BH1r9wwmLjXpqcT4K7i7SiVyDcDoQffpThaQyR7ZlB/DNTY2vwTipRIrHniuXkw86zlUguYuTlvF6HH6/4U0LU9Omsrq2SRZ1aM7kU8OCO4PrX4RftNfBi/wDhX8RrnVNKR0sbx3cYBCLvd/RFUcL6mv6Cp9hjOOfrXzN+0L8JNN+JPgi8Rog13FDI8Z2gnKo+B91j1btX57xzktOrHnoQ1XY+m4ezieGrLmfus/K39lvxz4JtvHCaB41sILuC4TarXMUckZkZkUYMjYz1xxmv1jv/AIDfBrxHaRX0Xh/TE8wBgRawDIPP9w+tfgDf6drvgTxJPpMyvDf2Nw00WQynERwMcK33h2Ar9jP2QvjqfHXhWLQPFk8a6jbhVVZGwzBVQdHdmJLE9q+D4cq0ac/qmLpLV7n03EcK0orHYebXdI9Yn/ZQ+Dl2gjuNHs4x1zFbwA5+pi6ViXf7GPwXuBiOyVD7RwD/ANo19SJOWby2HPb6VoRR78E1+ox4fy+qk6FJNHwcM6xl7SqNfM+K7j9hL4PXiPHcxSeWwOdqwf1gr5i/aV/ZE+FHwy+Hn/CQeHY5EaKRVBIhGWCO38MS/wB0d6/X4rtgb6Gvij9t4D/hSI/6+k/9FS15HE3C+CoYKVSELStc9XLM1xdXEwhKo7N2PxBs70QR5hLoPLMTnoQh61+n37P/AOyd8Kfib8N7XWdeFy7ziN9wEJBLRqeC0TevrX5Yk/6LcD/pk/8AKv3s/Yu/5IZo/wD17wf+ikr4HgfLqFfGOnOOh9XxNVxGCoRdOq7s5EfsG/B2J1WNrxY0GMfuO3/bCr8X7EXwYgPzLcv9RAf/AGjX2rMwAHHJFVfJaQZ6V+p4rhLAwbVKmmz4ZZ1jLa1WfIUP7HnwTtJRM9q7Bf8AnpHAV/H91Wz/AMMlfBISh30yFj2BggIP/kKvqB7NXGHAIPUGvPPHHjjSPAvhmfxHrkscKwAEB2Vc5YLxuYeo7152KyPB4Wm516SQ6WaY2rUUIVG7n58ftV+F/gT4C8HXXhrQLO1t9bmVRAIo7dHBDIx+6Ff7hPQfpX5oReHfEHi6SLQvDwkuHcsGSDczDv0UH0Paup+InxI1X4keK7nxlrbl/LI8hck5wAhwGZuwHQ199/sUfs/39i7eNvEsBG/Bj3qcnBkU/fjHYjoa/Mq2DjmOMSw9O0bn6HUf9nYJOtU5pPXXv2PrT9n74A+E/APg62S906NrttxdpIUL/eYjrGp6Gvo1PC+gwjNvZxJ9I1H9K2bRChKHgADFXgB3NfvOV5FgVgqdOVJNpas/MMXjq1Wo5Sk7sx007T4FyEEeP4sAY/Gr0EFsy5Rtw+oNS3EKSxmM8g1BBAYeFIxXasLQp1FTp0Vydzm5m1dy1LPkxDjj+tUplhgbzS/y/Xikur60sUN1eSpGi9SxAA+pJr85v2mv2yNM8KRy+FfBLia8YAB4yGA+43BjlB6E9q87iDM8HgaHMrc3RI7suy6vi6nJTXzLX7WX7WVr4O02Xwh4Kk828mAH2mAhlixsb5nSUFcgkdPavyUv7meGSXWtZL3WoXONgf52O3jjd83T3qhNfa7cSXOseM5/tMk2CI9zOTjjo5z0x3r7V/Ze/ZZ8R+Ntai8X/ESJjp6HMUcqtzkOp4liK9QOhr8Hx+OxOc41NryP0yjhaGU4RqT1ep2n7Hn7J+p323x14/inVskxxXCkd5F+7JF6YPDV+uw0hYrBbKzPkqBgbflH6CmaRptvp9vFbWKqkUfRVAA/IDFb/wAucZr9w4UyCngsNzSV5SPzbM8zq4mrzPZbIRUxt5yVqaqgdfMxmrY55r6hVFJu3Q8lruFFFFMRHL9yo06A9qfN/qzVVGby+vesHU5Kt/I0WxaDg5HvX5of8FY7R7r9irxWpUkJb3T8DsLS45r9J4uh+tfAH/BUiLzf2K/GoH8Om3x/KzuK9HK67lOnN91+ZyYv+FNH8nv7MsL3f/BHv4iQBSSnjCWQADJ2jSRzj0963PAnz/8ABFHWkXkjxjBkd/8AkFGq/wCx2/2z/gk58S4ByYtfuj/3zpQq18KR9r/4IveJsDPleL0z+GlGv1Cr9qp/fifHRo+1pwn5M0/+CS6Mv7K/xqnIxGPDniAFv4c/YIuM+teWf8E7EgH7JnxpvHZVH2fW1BJwCTYoQM+tevf8EoiI/wBir43Snvo2vr/5To68Z/YBg8v9iD4wXOOGbV//AE3pXa6jk8TN/wA6N5xUp0ab/lf5n7F/8G2enKvwI8V6iqddblXdj1trU4zX9Ntfze/8G29uyfsueJpz0bxA5/O0ta/pCr864nqc+OqM+ky+HJRUUFFFFfOnaRy/cr8rf+Cxf/Jkut/WT/0luK/U+4JEfFflh/wWH/5Mj1v6yf8ApLcVpgZ2xS9P1IrfAeI/8EDMj/gnt4eJ7XOo/wDpdPXzb8Zh/wAXPvseqf8AoIr6V/4IHf8AKPXQP+vjUf8A0tnr5r+MR/4uhfD3X/0EV+Y+NFRqvS9P8j73w8+Oq/L9Wek/sgkf8NOeGP8ArrN/6Ikr+gyv58f2Qf8Ak5/wx/11m/8ASeSv6Dqvwpd8srf9fH/6TAw44/36H+Ffmz//1P6YP+Cjn/JYdF/7A6f+j5a/Paz/ANYP+vkV+hP/AAUc/wCSw6L/ANgdP/R8tfntZ/6wf9fIr+ZeLf8AkoMR/i/RH7Pw7/yKY/4T9v8Axd/yj98Qf9iJff8ApA9fBX/BBX/k2Xxf/wBjTN/6SWtfevi7/lH74g/7ES+/9IHr4K/4IK/8my+L/wDsaZv/AEkta/pXhr/kQv5H4dmP/I1j/wBvfmfuYn3anHyrk96hgPytn1oeTPArlVdQorudlrsdsVyN/Y5/KlklRSFJAz79arpFK8m/d8mMYz3qvqd1b6dYT6hcgFbaJpD64QE+3pW+Es4XaG1rY+If2+P2sfgZ+y98HLrXPjQLfULW5DQtpjC3lnljkilJYQzyxBlIRlznBJx61/A9+2z8Tf2UfjH46PxF/Z20XUtBTUpPNmgW2tLWMNLJI5+W2Z+xUcseB9K+wf8AgtP+17q37RX7VN/4V0q4kOheF5JtCktA52yXNvc3HzeWJZEJKPjJCt22gV3f/BMr/gktrv7SV9F8TPipbzaN4Nt0EwMqtbh0XyZQR51rJCQY2bndg49M1+r5Pg6OAy9Y2dTWXQ+QxrlUxrg/sn4c6rcRf2HLpdqsjySgEZGW4I9Oe1f0Qf8ABBD9pj4RfAv4ieOdH+I+p2GjSanDpq28t5NDb+aYhdMwVpZU3bQwzgHGa+TP+CtHwf8A2Wfgh8d9N8B/s030Oox2ukKl6bWS0lWO+S4mSRJPsqIFcIq5Vhv5GeK/IqPUb06dNdWssmmalbYKzoxhI3HH3h833ePxr6DEzqZhgoxcfdkj5PDZlJ5jKMY6RbX9eR/U34C8Lav8R/8AgtvqXiqyt7TVLALYlvtatMgB0l1GzAZeo557VJ/wXr/aQ0bxH4j0L9nf4X2dlrt9mYTrpka3Vwm6O1mXcIZCw4VsZToD6E1+UP7Jn7d2tfAn9sTR/jt8TLme50iw/wCP+SJ2czqbSSGMMZZkR9rOuNzjHbniv3e/4JWfsteEf2kfH/iT9t34zWEOuWerfZxo8d7ElyIzbefaTcTxSAZ2r/q5TnHOOBXyOd5XUwE7xXuR2+fQ+tweOWKqtTWp+wH/AATA+HXiT4WfsW+FPBni6xudNv7Z755Le8ieGZRLdSuuUdVYZDAjI5Fffk0xjANJbII4FRVCKBgKBjAFVrzJwK/PMdXahKp1Ppox5VZBYuXbJ9q16zbOJl56CtKuDCRapq5otgooorpGVJ854qRfuflTnUdaauAaulCzcu5TeliCRdzDcTgdvWsXU9FsNas3tdRgWWF+CkihgcHuCCO1dDtDf/XoIHfmscdgKOKhy1ERTlODvFn4zftO/sdXmgah/wAJr4Agmkhl/wBZbwKSF2hFHyRxADJJPJr8+0j1i7uM6c0mm3kB+7loXOeOg56fzr+pHUNPs9RtjbXaho2+8CAc/nX5sftIfscQ6zA2vfDJFtrrqRGNmfuD/llESeM96/GOKOB5YWbxmG1R97k/E3uLD4j7z58+C/7aXirwdf2uh+NEmntlBjMjB2wXcHJLzAYAzzjiv1o8A/E3wr8QtJh1LQ7uCd3QMyJIjOpIB5Cs2MZr+c3xZovifwrGNN1+ylSVJVVpWicA7skfMwB6V1Pgr4t/Eb4Z3MF34Ru28nKmSMSS4xkZGEZR0UDmvDyLivG4KqoVL8p6OPyCjjY89Cykf0oJsYfeGfTvUU2IhuY4H5V8LfAv9rrwv4wsINK8V3MdnqPlLuMjpGC2FBALylskk9q+ytL1KHU4BdwypcQOMqVbf16e1frVDiDC4uKlB+8fDYzKq2Fk4VFY1jKz42kYz19aHgE7bGwUZSCD71IiRyDCArjt0qZUKGvSdNVIqT1R5sW4ux+Xn7Zn7OGVb4meFrVpJo2/fJAmSI/3kjMQkeccDJLY9a/OH4Z+P9e8GeOk8U2MkiQWUoWaNSwA2OrHcAwHQYOSK/pM13TrXWdMn0u/QPDcRtE4IBBVwQeox0Nfh3+1h+zxrHgDWptW8EWrppd27ST+WjBRvZ858uNV+6B1P6V+U8X5aqVb21BWX5H32Q5oqlP6tXP1k+CnxY0T4s+FIPEOmyI0m1RIoZSwYqrHgM579zXukOSQa/n5/Zo/aAuPg74qtNLun/4lV0yW75J2rLIyLk5dFGFX61+7/hjxLp3iTSbbWNKmSaOdFb5GDcMAexIzz619PwPxDFw9jXfvHzef5TKjV54L3Xsdm5Jib/dNfE/7b3/JER/19J/6Klr7TbeVZv4dp4r4s/be/wCSIj/r6T/0VLX0XGTvgKj8jHI/98peqPwyOPss/qY3H6V+9v7F4I+BukKeot4Mj/tklfgexHkuPY1++v7Gf/JF9M/64Q/+ikr8r8PFfMbH3vHP+7wPrFwsg4PT0pW4XA9KrxHCyY4O41kalrUGj2U15qckcccYJ3E4GACepI54r91xOLoYaDr1ND8vVOU/dRZ1TVrDSrJ7zUJkghTG6SRgqryBySQBzX4gftV/tGT/ABX8RTfDrw1OVsYsb54mIjO5UflkkZeGXH3etem/tT/tTXWp2l38P/CN0FN1PLF5ocgKInVh8ySHrg/w18kfCD4Tav8AGnxAsXh2F7dVP7+XayK3DY+ZUfPKnrX4XxNxJVzbEKhh/hP0Lh3KIYWLxOJ6bX6Hafs0fAC8+Kvim1S9jkbS7RmMsgUmNt6vjkoynDL37+9fvJo3h6x8O6THpGmosSRjC7QFHr2A/lXAfCz4ceG/hhoCaLocCRNj5yiKpOSW52qvqeor1XzGlb6V9pw3k8MBh+SqrzlqfPZ9mssXW934Ft/mPaXYuanWUuuaqSAbSXHFQvdGAFnKqg7nivqpY5UFaTsj56NNydzWRkI+8Ca5TxN4w8N+ErI3viG/t7KP+/PKsa9h1YgdxXhnxT/aU+H/AMONGubr7ZDPdwBSsEckbMxJX+HzFJ4Oa/IP4v8A7Sfi/wCOF09lIWs7BOgO+Mc7f+mjr1Wvlc744o4em6FDWTPpMr4ar4qXtJK0F1Por9pD9rXWNbkm8FfD+cyRXeF+2WrMY027G5kjlIGcEdDzxXwDDcNrlw76i5u9Q4xk+Y5/PLdKZotlqeoTf2P4Zilupn6CNTJnqf4OfWv0y/Zu/Yxjinj8W+OEy7c+WwyeN69JIfp3r8ypwxma4i2rufocsZgcrw/JG1/xZ43+zl+yP4h+IGoQeJfH0M8FpESfKnVlDg71+7JEwOMA9a/Z3RvDlj4e06LS9MiEcUYwAqgY/IAVqaVpFjpFpHa2MapGo4CgAfoBWvxsr9k4c4Ro4KmpVVeTPzHN87q42reWy2RX8vaV8vAUde1SE4HFBI6VFI4xhfzr7OrUhTps8jcgjJMua1B0FZiEeYK0x0FebgXeMn5kzFoooruIIpv9Wapp/q/xq5N/qzVNP9X+Ncdb4/kzRbEsHf618Kf8FMofO/Yw8dDGdukagfys56+64O/1r4y/4KI2hvf2OfiBGBnboOptx7Wc9ehlC0pev6nJjP4U/Rn8in7C0sU//BLr4s2JYGRNZv5Nmfm2rpajdj0HrW18BCk3/BF7xoXI48Xnbnv/AMSnjFcf+wOvmf8ABP34w246pJqx/LTUrpPgD/yhf8Wf9jkv/ppr9Vq60pL+9A+Yw38GD8mdB/wS0Btv2EfjZdyDav8AZuuxljwNx02PjPrXm37B0Jj/AOCfXxbvwP3Zl1RN/wDDuOnIcZ9favT/APgmpz/wTu+N4/2Na/8ATYlcH+w5F5P/AASv+LN6eg1jUEz9dKU1005X+tRfSaM3/HoPyZ+0n/BuJA8f7IuuzBT82vdcetnbV/RRX8/H/BulbmH9jHU5CMebrKv+dnbV/QPX5zxC746r6n0+CX7pBRRRXhnWQXH+rr8r/wDgsQyj9iPW8nHMn/pLcV+qFx/q6/KP/gsf/wAmRa5/vSf+ktxRg3/tiXl+pNb+GeQf8EDWVv8AgnroG05/0jUf/S6evm34w/8AJT776r/6CK+iP+CAv/KPTQP+vjUf/S6evnb4wn/i6F8Pdf8A0EV+Y+NX8ej6f5H3nh58VX0/VnpH7IIH/DTvhc/9NZv/AEnkr+g2v58f2QT/AMZPeFx/01m/9J5K/oOrXwnv/Zde/wDz9f8A6RA5+N/9+h/hX5yP/9X+mD/go5/yWHRf+wOn/o+Wvz2s/wDWD/r5FfoT/wAFHP8AksOi/wDYHT/0fLX57Wf+sH/XyK/mXi3/AJKDEf4v0R+z8O/8imP+E/b/AMXf8o/fEH/YiX3/AKQPXwV/wQV/5Nl8X/8AY0zf+klrX3r4u/5R++IP+xEvv/SB6+Cv+CCv/Jsvi/8A7Gmb/wBJLWv6V4a/5EL+R+HZj/yNY/8Ab35n7lRghTUeRUq/cNVQhd8CvFrwfLBR6nenYmjbbLvZgEC8gmsfXRaXWn3UF1IqwS28iMxICgMCCcnjpWrLDEh+Zsk8bc8flX8mn/BVf/gsf8bPgh8Zta/Z1+GOjGCO3guoWu/s86t8ks0G5ZIrtOwBB2ds+1fQ5Nga9WcaLW55uZY6WGpurCN2fkX+0z8G7G//AOCn+teBfDdlNr8GreMpb2SOyjF1hJNQMbblRMYwRnIPXrX9Ln7eX7Rnhv8AYU/YC0TwX4JNvoWv6hplvZrYR7bW82y2k0e/yo5IZNweMLnB+YYxnp8Pf8EDfh94T+PGreK/2j/iybPWfFr6rdPH9q2XFzCrLa3G5fOV5VCyMcEPwT681+eP/Bcb4kfEv4r/ALYNz4Xsra41TTvDsM1nHBZJLOVNtd3AVioZlBAbBIAxkcCvuFQU8THAYl8saZ4lLFVK1P61Omk5bn4ta94j1Txl4kvvE+s31zJqms3El5I99KSweUlm5YlsZz1JOepr9tP+COH7G3wY/bf07xx4J+LFsftthHYGO5hSDI82S4J2vNDMfuxKDgCvz1+N/wCyVrPwT+H/AIU8deIVlFx4q8PWXiBI7kN5kSXjFdmGiQrjB4y2P7xr9uP+DZCSOfxR8SHnglEixaVj5ePvXvrX6Pm1WnRyKVTCva1meNlWWKeYOT2/4c/Lb9uH/gmJ8e/2NvFlza6ppd34h8J6ltC3kUFxdwwiJY2O+Q20US5eQKOuSMda++/+CUf/AAVz8KfsxeG9M+AnxWtzJ4fmeYW11CiGNDvnnky811HGPmdRwnXg84Nf2B/GLwH8MPit4Il0f4p2lrLpjDD/ANoJEUX51P8Ay2VlGWVe3pX+fz/wUe/YL8V/sXfFxtFktHk8M3p3add2yOYYz5UTy4k8iGNctIF+QHng89fh8szd5rhZYHF7vVS69/wPXxWBjgqk8RSd5dj/AETvC/iPQvF2kReJfDl4l5Z3S5R4pFkj44OCpI68HB61tzpucECv59f+DfH9oPxP8Wv2eNS8A+M9WudW1Dw1td5bid5ztu7m6ZQGeRm4VQOi9O9f0J7ema/OM0wHJOWHb6/8E+jweI9rRjVtqOQj7tS1Agw+anrCaSdkdDCiiioEIRmoyh5xUtFaKegFNy3O0Ugd+mKuYWjC1i1LmvzGnOuxmXUkvlhFHXuKrx20jJt6j/araKKeoBpQoHQYrkqYT2kn7SV12IulLmW585fFP9nrwT8RtEksZ7GCKZmDiVI41fIUgfMY2PevyL+M37KHj34a6rNqegRXN/ZhmO2FZJQFyx6LEo6D1r9/ZFO35AKzNU0jSNVtGstSjjdHBBDBT1GO496+XzngvC4xN0nyM+gy3iGvhNFqj+X1JpY78w75dP1CHnZnypAVPcfeAB619LfCP9q/4p/Da8jsdYm/tSwQhdu6adgoKjoZVXoD+dfoL8bv2KvBPjm3l1DwiiWGpFzIZoxHEWX5iVLJCWIJI49q/Lb4ifAr4v8Awr1aS3g0mfULVGP71IJpRgEjO4Io6LmvyHMskx2UYq1JuUV1PtcPmWEzKj+/0l2P2D+Ef7Xnw3+IN0mm6jcJpdz5W8/a3igXI2jaMysc5PA9q+qrDW9H1eET6bcxXEbch4nVwfxBIr+XeW8SC5EmozT6XeKeit5DbgenPOM/yr2rwB8efjX4KmjbSNVkvLJCMJLPPIMDHZXA6Cvq8o8Q6tGKo4qmrHkYrhOFSV8PM/okuSpGxOc+n86878f+DNL8b+Grvw9qESv50ToGYA4LKVByQ2MZ9K/O/wAIf8FDbbTGhs/Hdi2TtQvBFzuOByZLgcda+xPCX7UHwk8Z2kdxb6lb2zSAfJPNCjZOO3mH1r6SvmmXZjSc4ytfSzPEq5LjcLK7g9OqPxq+N3wH1L4ZeIZ9Plgm+wtcGWG6KnyxLuYIA+xVBwuQBzjkV7r+yt+0b4j8Ba5D4S8ZTPJpjlUSeVmMaAlEHzPIqjABPTpX6YfEfw18OPi94On0W7ns5PPBMEoaIkSlGCEEh+RuzkDPpX4nfFn4E+PvgtrdzFeXhvrK4lZrdhJJKEViQvJRFGAvbpX5visLPL8Qq9Cd10PqsHjIY3DvDV4Wl5/mf0QaPr+keJdOXU9DuormCSPKtE6upyMjlSR0PrXyR+28rH4JiMAlhdIcDrjypa+GP2cP2otX+GS23hnxbLvs5SiIzEnaTsUDLyKBwD2r67/a18W6V4y+Ayajot3G0kxSUBJATgxSHHylvWvua/E1LMsqnGv7tRKx85Tyqpg8fDl1jfc/FmXEatFJ8rspIB4JHrX76fsasE+C+mb+P3EPX/rklfguXtJ7CCK6GLtgsW5sZGfc89a/Zr9n34heGPhV8CbS68TX0eI4YyFMqZ4hXjDsv908V8NwZmNPC5g5TeiPpOKFVxVCKUdbn2/q+r2WiafPqOozJBCm5y8jBVwAT1JA6CvyT/ah/a3utQluvCPg6TzYUZ43ntyWXjepyyS46EHpXI/tC/tf6j8SWHhPwbLLBZFwC0JKll+ZcZSVgQQw4xXjfwn+Anjz4i+J4lt7X/QJnDTSTpJggsu75hGy/dPeva4ozmtmGL+r4d/u3+J5+R5VSw9P61it10PL/BHwu8WfGbxLDpdn57SXbFzcLvIjLAtkuFfbuxgHHNfvT8Gfgr4c+GHhqOy0S3jS5wd8uxQx5J5ZUUnqaqfDb4YfDT4QafDHixtrkRRxtI3lIS0YIODtU967DX/jJ8LfDsBkv9f0+ID+FbqFT27Fx617PD2Q4LAReJxdRNroc+dZ1WxzVHDwaj5dT1BxaRgREDc3fjNL59pAhklYKo6kkCvzz8f/ALevw/0PzLTwrG9/dDHlsVjljzxnJScHpnp3r4n+I/7YnxH8Y7otMurjTVPa2eWEdvSU+n6135rxrhacuajFNrRI4sNw1iaq5p+6vM/XL4iftAfDnwFpdzfXeqWt1PAARZQTRPcyZIGEjMiliAcn25r8yfjJ+25408ZBtI+H6tpitxumEkL/AMJ6xzH0PbvXxXd6r4h1eY6hqd+Lqfszys83pwSSen6V1fhL4f8AxB+IF6tjoGjTsW/5bG3kPY/xKremK/O8x4ozHMJOnCnZPse/g8kweGalVnexxmo3+u3Mjal4wv3vbk9AZWkX05389Md69Y+GPwE+IXxN1GOCy027t7KQnMwhkROh/iCMvUY+tfdPwl/YSihu7bWPiJILhEyWhJ3KchgMrLB9D1r9K/DfhTw14K0tdP0G3SKJegRFHfP8IA716XD/AAHWxsXXxdRxNsy4rp4WH1bBpSv1Pm34QfstfD74Y2sU32KO6vUyTJLHG5yc9/KVuhxX1ZZW62yhYUVEH8KjFWbVBIvnOuM9sVdAA6Cv1rJuH6OCinD5H51isVUrScpvUjIyoKjHtULtIExirdGAetfQ1eaSsnY5k9bmURL70MrjAxWphaNqntXC8E29ZFuZjxLKX56VsDgYo2qO1LXVSpKCsjJIKKKK1GMkUshAqosbKm0+tW3JA4qKpdBTvJjUuhFEQpw3BJzXyz+3FbG6/ZI+IyFcgeHNWPTsLOavqCb/AF6fhXz9+2Jb/af2UviPAP4vDGrj87OWunKnyVYw/lZhilzU5ryP4w/+Cf8Asb9h742WhxuB1oqvc405MYFbX7Px8z/gjD4vSP5mj8ZAOByVxpPOfTFYf7AsXk/sq/GvTTnMcett/wB82KCtj9mj/lDh8Rf+x1l/9NIr9Qqu1CpLtOJ8hKs6ap012Z2H/BNSGQf8E6fjdclT5RGtKHx8uTpacZ6ZrjP2Mo/I/wCCQnxZnf5S/iO9jXPGSdIXAH1r03/gm1x/wS3+NP8A18av/wCmlK89/ZQhaL/gjt8R5z0bxbMfz0kUVajjTxM11nE6IQvUoS8mfub/AMG99qbX9iT51Ks19CzAjBz9jtq/eUc81+HX/BBWMxfsWIx433kJH42lvX7iDoK/PM5q8+MqPzPo8E70kxaKKK8s6iC4/wBXX5S/8FjlY/sRa5gE/NJ/6S3Ffq1cf6uvyw/4LD/8mR639ZP/AEluKMGv9sv5fqTW/hnhX/BAZWX/AIJ6aBuGP9I1H/0unr52+MP/ACU+++q/+givpP8A4IHf8o9dA/6+NR/9LZ6+a/jCf+LoXw91/wDQRX5j41fx6Pp/kfeeHnxVfT9Wej/sg/8AJz3hj/rrN/6TyV/QdX8+P7IP/Jz/AIY/66zf+k8lf0HVt4Uf8iut/wBfH/6TAw44/wB+h/gX5yP/1v6YP+Cjn/JYdF/7A6f+j5a/Paz/ANYP+vkV+hP/AAUc/wCSw6L/ANgdP/R8tfntZ/6wf9fIr+ZeLf8AkoMR/i/RH7Pw7/yKY/4T9v8Axd/yj98Qf9iJff8ApA9fBX/BBX/k2Xxf/wBjTN/6SWtfevi7/lH74g/7ES+/9IHr4K/4IK/8my+L/wDsaZv/AEkta/pXhr/kQv5H4dmP/I1j/wBvfmfuYmdhqCI4kNWIuI2zVPJDZFePVk6ap1Gd2+hNIAZCAvzbeCRxX8Sv/BcP9j34zeFfjxeftBW1n9t0PUxKJJoo55FiWee5mIZhEEUqi5OXOBz05r+2AzSRzh2I2HjHvXxZ/wAFBfDngbxV+yZ46PxCiiFvZ6PqU8BlWP8A1sdpOUx5gIzycYwfSvreGc8p08bCcVqmcOZw/cPQ/nE/4N0/BXju18aeMfijYy3k3h230/UbP7NG0jQPekWkilUA8suUGF+bdjjGKyP2cvHXgmH/AIK+/E3TP2k9Hi1DSNQuda+zQ6vbpKkHmahEqFlum2IqDfkjOMnHevWv+CCX7XHwE+DXgnxj8I/Hmuabob32u3OpWs99c29sPI8m1iVQ0kqdSpICqRwee1flh/wU3+IkXij9uvWbz9njUGlu9XluoY77SJd3nPcXkuNstsxLbiUIxnPB9K+3WFljs0ryatGetzgwuJo/VVTe59yf8F5/FHwl1bxjoOn/AAo1PS59P03wta2kVvpc0LQweXeTYiCwkqm1cfKMYGOMVD/wbx/FzwH8JvEfxHm+IXiXQPDiXUOlC2bVr2Oz84obveEMrpu27hnHTIz1r8Pvj78GP2i/AGmWh+Os1/b3Wo2EV5C1+1ysslvI5CuDcIpYFgeRkZzzms34EfBf4/8Axaubq2+DOiapqstqEMgsLa5nI37sZ8hGP8LdcdD719BQw/Plk8BOXuK2vzPiYZtWp5sqUFpd/d/Vj9RP29f+CwX7UPxb1y4+Dnw01OKytoWIgudDnu42udwjkI3xXTiTZtPReMmv3N/b38GXPij/AIJNX/jL41aK2p+LdNt1eK5uLYzTwebqECZLzgypujAHBGRx0r+YXSP+Cb/7b3h3wlN47tvh7rbapp/zRrd6TflyZH2HYBbhvunnBHFful/wVW/4KF6n4R/ZZ0X9nTxDpjQ+IfEUcsWsW7wlfIW2kt54DsacSLuXn94hz/Dgc183i8JSp4jD08HK9t7fqfVYTEyrKpVrLQ2P+DaKKLTfA/jrS5Ih9riSxMs4XiQNPeFRu6ttHHPSv6oj90V/LJ/wbWXCXXhf4gRMR5yR6eXXPIDTXmOOo49a/qcI4Ar5fiFxePny+X5I9HKL+yu9rv8AMjRj5hHpirNV0X94T61Yr5tKSb5u7PWluFFFFMkKKKKACiiigAooooAhmLhRs65qOeJSoZgWPoBVqipcU73HcpPBHJENoKfofxrI1Pw9our27W+o2sU4YEESIrDn6g1vTOEHNRI4ccVjVpYWtejVSbZpCUlqmfCHxf8A2Kfh/wCO2e/0qCK0u2k3ZCRxr/Ee0LHqa+A/iT+yN8T/AAK7v4fiuLy2XPFqssnAz/diUdBX7yzW6S8SHHPaq81haSxmKVfMU8EEA18Fm/hxRxMnKk7H0eX8SYjDWvqvM/mP1DQNd0ndDrOiXaTJnLXlswTj0LAHdnpXPQ6mhuPK8+TT5F6eW3lDI/z+lf0j+I/g/wDDzxLG0WqaZbvuOSWhjJzz/eQ+tfMvj39hX4ZeKMzaVF9kkPdFij9fSA+tfEYrgrHYS8KTuvI+mp8YUpv342PyHsPiN8WNGxFo+u3UsKj92DdTMAw6H5WA/Kum1P4weNfEmiHRfHkjXs+MRzZkkC8ED5pGJGCSelfZmuf8E9fEWlw+f4Q1OOSXdjy7uZjHs56BIAc9MV434l/Y6+OGkIzxwWt3jtAtxJ6/9Mq+Vx+S5lF2cWenQzTA1mnzJM+PHWR7U215LveOTzkcNnG3oMnpz2FdEvxN8W3+mJ4ZmvXltowAIzI7DCjb03Y6H0rr9X+B3x10Vy9z4WvJ4920+TY3DfzQVXi+GfxHtYvO/wCEN1FHxyx06UfrtrzKeDx0G3UTsenHEYRuyab+R5tfIt2yRRrslGCGxjke/Wt+61rxjPoyaFql5I1muAF8xyMAY6E46Z7Vdl8HeP5r5IF0K9SYuPlNrID19MZ61q6n8OvjcbqG2g8K6jNE20bhY3DDk+oXHSsMLTqyqNQ3NHWoL4mjzaMy2U8K6TEoKsuXlX5cA+or6A0v4+/FrwppyaP4UubWEFQC6PMpHGOqOPQdqwofgD8dru7Q2/hq+EbKMh7O4ABz/wBc8V674W/Y4+Muv7ftdslhux/rknixnHrEfX9K9LBYPM1PmjF/ic1TG5a1y1Gjx7Wviv8AFjXQLfWNb1CWWUkgwXMzKp6n7zEgelea3+pR3Eo/4STVNQmf0lm3D8mr9D9L/wCCfHjZrmKTV9Zhjh6v9nuHEg47boMdf0r3bwh+wJ8P9NlW58QO+oMO05jl9f70Ar6qnkOaYlcrT1PMrZ7ldFXp2fofkPZ2E2qp9i0TS5bvzOAbeDfcHHPy7c+nPtXtPgP9lb4v+NbhZIdPks7c9TfRTRt3/wCmTDqK/bXw/wDBP4W+F9i6VoFjC6dJltYlYZz/ABBBjrXq1hpllaosdqioo7KAP5CvSy3w+rupbES3Pnsbxfz6UI2R+d3w0/YF8J6Pf22teLZ5LiaIsTCrI8TZBHKvACcZB69a+5vC/gDwf4RgEGg6db2uz+JYUQnOe6qPU13wt1DiQ546AVVuAa/ScHw1hcvp8/Ldo+SxeYVq796QxSgbEoB9l6VYMsX3fJYj/d4rMgDeaCa6AdBXs4HEe2jJxjaxx2a3IEkJIVUKj3GKsUUV3q/ViYUUUUxBRRRQAUUUUAFFFFADH6VFUr9KirWGzDqipN/r0/CvFv2pIftP7Nnj+ADdv8N6quOvJtZa9pm/16fhXlv7QkDT/ATxpCOd+haiPzt5KeBdq7f95GVf4J+h/FT+wtbyRfBH492LIQIrXxCSCOhWzT8qd+zGDN/wRx+IwiG4jxpKTjnj+yRXRfsXRmL4e/tHaZjlLXxOcd+LRB/nisD9kpwv/BHz4lxf9ThP/wCmkV+oV/8Adaj/AL8T4mv8UPQ9F/4Ju/8AKLL41Sfw/adXXPbP9kpx9a4/9mQxQf8ABGDx9IzBd3iyQcnGSdIHFdn/AME5T5H/AASZ+NZbvqWq/wDpoSvOfgYpsv8Agir4vc8ef4zGM/7Wk1nif4Nf/HA9Gm9aHoz9/f8AghIm39iWxIH3p7Y/+SkFftsOgr8X/wDghrZtafsS6WGGN5tm/wDJSCv2gHQV+d5mrYuoz38F/CQtFFFcJ1kFx/q6/LD/AILD/wDJket/WT/0luK/U+4/1dflh/wWH/5Mj1v6yf8ApLcU8F/vfyX5k1v4Z4p/wQO/5R66B/18aj/6Wz182fGH/kp999V/9BFfSf8AwQO/5R66B/18aj/6Wz181/GE/wDF0L4e6/8AoIr8w8av49H0/wAj7zw8+Kr6fqz0j9kED/hp3wuf+ms3/pPJX9Btfz4/sgn/AIye8Lj/AKazf+k8lf0HVr4T3/suvf8A5+v/ANIgc/G/+/Q/wr85H//X/pg/4KOf8lh0X/sDp/6Plr89rP8A1g/6+RX6E/8ABRz/AJLDov8A2B0/9Hy1+e1n/rB/18iv5l4t/wCSgxH+L9Efs/Dv/Ipj/hP2/wDF3/KP3xB/2Il9/wCkD18Ff8EFf+TZfF//AGNM3/pJa196+Lv+UfviD/sRL7/0gevgr/ggr/ybL4v/AOxpm/8ASS1r+leGv+RC/kfh2Y/8jWP/AG9+Z+50RGCD3qs0ZDHPT2qRFyd2amwK43h/bUUn0O7Z3MqaIyY4OFO7jqcV4f8AtM/CDTPj78F9Z+GmrXD2UN/aTxPJvEe0SQyRncSjgLhzn5T0r3qVmG4R/ewSPrXzD+17ovxW8S/s9eILL4OzPa+IfsVz5TI0yMzfZ5cBTADISXK4A/niryWnGNZtbk1oe0Xs3omf5zv7Svwon/Z2/aC8WfDjw7qJlg0nUbyyhmtZtzlYZWRWDKsY2/KDkKPpXrH7Aup+ANC/ay8Ka/8AHvUorjSTPaSGe6mjeOCT7VCw85rghECqGL85A56Zrwr48+Efjz4O+K3iP/hfNjfRarJfXPm3NxFcAsS7bmD3ADHLBjk/zzXzvBJDptpc6t5l7eHzG2pbnzDnGQMcfzr+jsDhaGKwMVTlZ23W5+XZ3Wr4KtUhTV9Uf0i/8Fzfir8Bvi34lsNR+EXiDR9X0/SfDttYCbTLu3uIYmjvJDs3Qs6qQrA7eOCOMGvXv+Da2yto/FfjyHVWFzJHFppjDkORuN5nr7elfzd3XgXxrY/D6PWPEdjfWWlaxbxX4F9FJGWilxtYbl2lSQMHJGehr+k3/g20uraT4p/FBFj3LFb6N5RAB5IvM4/+tXiZxg1hciqU6Uve2ufRZVOjOrCpKK5nr+B/W7qp0yC2L3LWyWy/64TkAKCeOvA59a/zW/25/j34t/aj/am1vxzrM9uIrwwxQ20bPiD7PAkROxnl2b/LB4PzdeOlf2If8Fq/2v8AV/2bPgdp/gvwLcfZPEvjMzxWkkbtHIpspLaRuY5Y5BlHP3Q34Dmv4hvg18F/iB+0/wDElfAHw4SSXX/ELFY50EjGFoEeQktEkki7lRgMKc+w5r5zg7CypUHjMVs/0KzmrJ1JYWlHTv6n9Uf/AAbU+EdS0zwj8QPGc7F7fV4tOSEgkoTbzXitjgDr1wT+Ff1IbsHGa+D/APgn1+yJon7HnwI0/wCHtiqi7HmG5kUL826aSQciKEnHmHqtfcbSF3yOlfA8R46FPEylSd7s9/K6bjh405dDQQNuyelTVDFkjOTU1cnPzJOx2vcKKKKQgooooAKKKKACiiigAooooAoXoYgbaqRGVSDitkgHqM0mxfQV51TAc1b2vMXzaWIRiRQWByKrSzMnyoh+uK0QAOlIVU9RXZWjUnDljOwk7GMjSux3rx15FT+YSAu0j8K0di+gpdq+lcVLBThGzncblcyTEx+ePk/7XSnfZWcfPgfStTao7UYWtVgKP2lcXO+jMR9MtCMTL5gJ6EAj618wftN/EXSvhT4Olu7W2SS4kBCBUBOSr46Mp6rX1u4AAwK+Nv2w/hhqfjrwDNc6KjSXNsC4VASTsSQ9FVj1NeFxNl9OGAnOjTVz0crqXxMVUlofkDN8ZviDeaxJ4vWQwqmZBAxkVtoO/O3f07da/VT9kH4y23xi8N+VqUMZubbCsSoySqpnku56tX41PpHj7zH8PTaPc/aMm3LfZ5Pun5TzjPX2r9ef2Ifg3qXgDwv/AGtqKNBJdYYqwKn5kj7FF9PWvxvhjB1I5im4aNn3efRoRwl1LXoff0VlHGNoAA7bRVg20ZXkVPGOmfSpq/oulh6NOCjGCPzLmle7Zmm3ij+fBOO1IxYr8qlfoK0sCjC1lWw6lb2ful891qYj+YRsIJBqWLzEHyitXYvoKNi+grjeAm5qbqaijJJWSKSLOZQ5JwO2anaPf1qfAHSlr0KdPljyy1JbuV0t0HzYqwOOKKKqMIR+BWC4UUUVQgooooAKKKKACiiigAooooAY/Sohg1JL9yqhJCHFYVcS6b5UilG+o2RS0iuvIBrgPjSqzfB3xVDwd2j3y4+sD16FCflrz74oxm4+GniSDrv0u8H5xNWmFr8s6crfG7kVYXjI/jL/AGQIfLm/aa0sDBWw8Wvt77Vt0GcenviuE/ZODzf8EjfiZFbAyFPF9w7BeSFGlDJOOw9a9O/ZOjaH4lftQ6ZjlNB8ZH8okFeZfsVSFP8AglJ8XoicH/hIb8Y/7hS1+rwl7TCzT/miz4rH01Trxh5HqP8AwT+AX/gkl8ZZ4vuPqepgMOhJ0heM1wHw6Q2X/BE7Vyw2+Z4zt89uulHOa9B/YCQwf8Ecvixnjdrd9+ujrXD6IDb/APBErUT03+Mbb9dKasK0+ZVof34nXFcsqUeyZ/Rd/wAEYLX7L+xL4dcrtEsFo6nGAwNrDyPWv1zX7or8sP8AgkHbG2/YT8DMwx5mlaew/G1ir9TY/uCvz/NJf7bUifR4RWpIfRRRXEdBDOCUwK/K/wD4LEHH7Eet59ZP/SW4r9UpfuV+Vv8AwWM/5Ml1v6yf+ktxWuBjfFL0/Uit8B4n/wAEDiP+Hevh8Dvcaj/6Wz182fGPC/FC+z6r/wCgivpH/ggX/wAo9vDv/XzqP/pdPXzZ8Zj/AMXQvh7p/wCgivzLxnpp1qT/AK2R974efHVXl+rPSP2QiF/ad8Ms3AEs3P8A27yV+9v/AAk+m+pr+fv9mN2j/aC8PshwfMl/WF6/aavG8Pcxnhsvqwgt6jf/AJLH/IjjSCeNhf8AlX5s/9D+mD/go5/yWHRf+wOn/o+Wvz2s/wDWD/r5FfoT/wAFHP8AksOi/wDYHT/0fLX57Wf+sH/XyK/mXi3/AJKDEf4v0R+z8O/8imP+E/b/AMXf8o/fEH/YiX3/AKQPXwV/wQV/5Nl8X/8AY0zf+klrX3r4u/5R++IP+xEvv/SB6+Cv+CCv/Jsvi/8A7Gmb/wBJLWv6V4a/5EL+R+HZj/yNY/8Ab35n7mrjHFPAyajTpUhkC8Ac0qdRRpJ9DsvrYpzoZLlY13KFIbcOBwelPP75nIQqqkqwYYDe49c1aH940jNnmssLBxvPo9RvU+Iv2lv2Gf2eP2hfCOr2HiTwpo76tewzyJfyWNsbhZHRwD5rQuwG59x6nPPWv8+f9sP4A65+yn8e/Enw4vYIo7WHULp7NdrASQJM8aMgMcQKnZwVXHpX+nJKGkcgLu3Dafoa/iZ/4ON/Cfh7SP2l/Cuo6OIRNeabbpMI4RGQ8l3d7st/F0GfWv0PgfOakq0qKbtZnz+fYClKg5y3uj4m/aX8Yaf4v/Zl8DaHBax283/CHaVEzqgQ7kcEnOScn6V+gn/Bvb8U/Bnwo1D4peK/G95b6baQQaPsubmRIUky92p2vIyg7SwBweM18D/tIeBn8N/AHwPM5Ba58JaZOhCgHDPjsTX54+Evi/4p+GnhXWfh74fe5eXW1hUCG5a3H7lzJyACD1PUivvMxwnPgJ+1eml/vPgqGKrRxdKNLXX8Lf8ADH6E/wDBUj9vnVf26vjtpNl4ThlS28NvMdNaNSFma4ghWXyyk8wbHlHds24757fuh/wQb/4J9XXw08K3/wC0H8T9Gmsda1LyxYRahbmKe1MElzC7BZYFePzEYHKudwxnjivk/wD4JYf8EcfHN/cP8Sfjjb2iwRhZLFb20tb/ACXM6SbSLhyn8OTtGfwr+wXw9oFh4V0CPRtOjjijhGAsSCNeueg4Ffn+e5vGhQ+pUH7sT73CYedTFSqTWmhveWGTEeRnsajWABsGpIS3lgnrUtfCvD06j9pPW57k04/CEBPIqxVCFiZOOlX6xpO8RhRRRWgBRRRQAUUUUAFFFFABRRRQAUUUUAFFFFABRRRQAUUUUARyPsA+UtkgcDNZt+iTubWWIyRupDfLleeCD2rTckDijICfWs6lNVU6Utik7ao8yb4ZeAWuWv20W0MpbJb7NHuz1znbXYWmlwQW4trCMW8ajACjbjHoBWxT19K5aeSYaD5ox1LliaktJMSGPykCZLEDqamqMH5gBUldylfQzYUUUUCCiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAjl+5VM/cNXJfuVTP3DXn4v4vkax2HQnC5NcX46US+CtajPIfT7kfnG1div+pb6GuX8Ux+b4T1KIfxWcw/NDV0X7+HRNX4Wz+Nr9l9Nv7RH7Vdn/Cvh7xsMe4VOK8W/Y0Zn/wCCXvxdtY/mYeI78lRyQP7LXJxXuv7MUJ/4as/axsP4hovjfj6BBXiP7FsRg/4Jz/GaI8Ea9qY/8pi1+w4ON6FRPvH8j4rNP96TPXv2FykX/BHL4ptkY/t28U+x/sheK4h4mh/4IjTEAjf4vs2+oOlNzXV/sSjZ/wAEZ/iqemfEt3/6Z1rI1mM2v/BEO1Zz/rfE+n4/4Fpb1ySd3W/xxOj7dN+p/Sn/AMEpYvJ/YN+GxQff0PTGOPe1ir9L4/uCvzn/AOCWkBt/2D/hlnjd4e0s/wDktHX6MR/cFfB5trmFZ+h9Hhv4aH0UUVxG5HL9yvyt/wCCxn/Jkut/WT/0luK/VKX7lflb/wAFjP8AkyXW/rJ/6S3Fb4D/AHpE1fgPEf8AggX/AMo9vDv/AF86j/6XT182fGb/AJKhffVP/QRX0n/wQL/5R7eHf+vnUf8A0unr5s+Mx/4uhfD3T/0EV+aeMv8AGpfP8kfe+Hf8Sr6L82df+zN/ycB4f/66S/8Aol6/aivxX/Zm/wCTgPD/AP10l/8ARL1+1FfJcE/7nU/xv8oi4z/32H+Ffmz/0f6YP+Cjn/JYdF/7A6f+j5a/Paz/ANYP+vkV+hP/AAUc/wCSw6L/ANgdP/R8tfntZ/6wf9fIr+ZeLf8AkoMR/i/RH7Pw7/yKY/4T9v8Axd/yj98Qf9iJff8ApA9fBX/BBX/k2Xxf/wBjTN/6SWtfevi7/lH74g/7ES+/9IHr4K/4IK/8my+L/wDsaZv/AEkta/pXhr/kQv5H4dmP/I1j/wBvfmfuH5jJkL61BvcycVehVWzn1q0I0HQV5Lw9SrFWlodmiZAm7YA1Kehp05iVAkvRjt/OqkzRxyfZ3GIljLH6D9eleopqEFH5DTOV8cQaheeD9Y03RW231xYXCwHAOJGRgp+b5fvY68etf5sn7fMPxb0n9q7xvpXxcm8+e21LUFszthXEaXEgT/U5HXd9459e1f1df8FCv+C2/hn9jf40p8M/DXhX/hI5I7Nlmn+3NaeVIs0sZXa9nNux5YOQ2Ocdq/ku/ab+O95+2R+0Wvi2x0z+ybrxjfCMx+cJ9ov7hieSkIODJjovTtX3/AmE5HLFuPupP5nyvEFb2v8AsvNbr9x57rPx88S+PfDOi/DLWm3S2unQWNtwgxFb/MPuoo7Hq2fc1+wH/BAP9m/wb+0D8W/E+rfE63+0v4fWzaNd7p/x8LdKeYpI+yDrn8K+Rv22f2Prf9lH4eeEPDyXP2qbXfCthq852FNkksrRlf8AWyg42dQQPav1R/4NpIzF46+IA/6ZaX/O8r7vP8Yq2RTq0vL8zxModNYuK5dn+h/X/A0Wnotki7I1AVOc9KvTSxou89e1SFk5x2pkblwSa/B61RP3OrP0R26KxVa5J6HAqUSvtNZlyY/Nj3DLnO3+teJ/Fz4/fC74E2trb+Pb77K1+XFvF5cr+YY9pb5o43xgMDzjPavJwMcTWlNudoxIrVVBqNrtn0BAUU9ateZH61zumN9qQXcD74ZACoxjH9eas+Wd1YPMasYRl7Pe6NVTWyZuAg0BgeKypSMKJE3Y75xTFund9o4r0frlFOMW9X0sZO6NgkDrSF1HU1y+v6/p/h6xfWdVuPs9rbAGVthbhiAOgJ6nsK/IH4m/8Fw/2JPh94xbwnqHiMB4zhz9kv8AuoYdLNvX1r0lgsRUT9hC7Rn7eCu5PY/aTzYxxmnb19a/Lr4Sf8FWf2Lfjz8QdI+G/wAM/EP9palqzSLs+yX0Ozy42kHMtqinIQ/xDpX6Vxpc/a2A/wBUQCDx6fnWEMPiITlDExUWu2prRqUq0XKnK5t7h1oyM4qup3IDSYJcsaJQlpZeoNpOzLJIAyaTeuM1WkTehWmQjERiNQ1P2qhb3e4c0bblzcMZpgmjJwDVN1aKIpGu7ceecYBrwfXvj38KvCnjy2+HGsajs1a727IPKmP3n8sfMqFPvccsPyqMR7ZTjGjG/e4lON7Nn0QGBpN61kwuplW4ST90y7QuO56HPWlFrt3JD8jM+9u+TWsrRqRjLZmvIr7msSAM0m5c47mqMBwWjJyQaYz5m2j6UsRKNK3M93Yi29jR3igOpOAeaqMhKnnqKoIWjnQMu5cDnOOaitJwlGEVe5EXfdGtI9MB4zVJ7W3hka4iXDMSSc+tOU7huxWPtJQqa/F21enqarey2Jy5zUgORmqBEO7mplHHyjjGa6ZVqqXvU7L1v+hco6FqPcX9hU5YDrWbE4LYb14qO8aGEmS6OIypU9+v0rGMoxpxlF3TfXQzjeTszT81M4zzT8jGawFsl+zhtPbYJACH64H0NeK6v+0l8INJ+Lem/Bi7v86/fM4jg8qbgrF5v3hGY+U55YfnxVYeOIqc14Wt5hPlTtc+idwxmgMD0rMiVbhUNwPmTJH405Zwtw1uOenNVFVHKN1o/wDIGrIvmRB1NKHVuFOa5K8v7OS/TSp5P3kuQFwecDPX/wCvUN5qljoFg+va+fs0FtyzcvgE7ei5PUjtWNR4lYhUlT9zuYwrU3G7Z2RkQdTTgwbpXyh4J/a4/Z0+KPiJfDHg3xD9q1CZioj+yXKZKgnq8SrwFPevp5LcNGjHh1zg+ma25aqrum4+7be5vOKikzRJA60m9a8i8CfEDw/8SdGi8WeEj9oiunkjc/MufJYp/GqngqegH4188ftLft8/s4fssxvbfE3W/IvlAK232e6bOdh+/FBKvRwa3p0Z1Y3pK7/D+v10IlKEVeTPuQOp6GnA55r8RfAX/Bd39gvxmJoPEPiP+x/Lx8v2TULjOSe62Q9B+dfp78GPj18Lv2hdAj8YfDfUP7QsVzsfypYv4mQ8SpG3VT2rpr5biaUFOpC17bnPQxVOrfk2R77SFgOKz5ZkikL45b+lRJLDekPIuMdOa86delCapyl7zOiDUnY1gc803etZep31tpVm17eSeVEn3jgt7Dpk1LE/nQB4m3KenGK39m+a3Qate1y/vXPWnZ4zWfHF3zUwmUHb2pKnK7T2CbjHdksjArxVQ/cNT7c/NVa4ORgVw42mo+82WnZaCr/qW+hrH1WMvpN1GejW8g/NTVyFnDY96sakd2mz/wDXJv5Gpy5qtOm07cn46kzd4s/jY/ZmTH7fP7WWndv7E8c/+hoK8Q/ZXj+y/wDBP/40W44/4qHUx/5Tlr6M/Zriz/wUr/akiHVtB8bD85o6+d/2dgV/Y4+O1ljlda1n9LACv1jL8TeFSNt2j43H01Urqd9j0D9jRAn/AARm+KWzv4ju/wD0ziqPxHj+zf8ABD7w2yD/AF3ifSM/8C0ySpv2UPl/4IpfEGM9vEE6/wDlHFM+J0Zi/wCCIPhCNh/rPFGigfjpslc9aryVK8bfbizdvWk/U/p4/wCCbFv5H7CfwpAGM+GtKP520dffCAhQDXxF/wAE44zB+w18K4z/ANCxpf8A6TpX28Ogr4bHRviJ1e59Hhl+6iLRRRXKbEcv3K/K3/gsZ/yZLrf1k/8ASW4r9UpfuV+Vv/BYz/kyXW/rJ/6S3Fb4D/ekTV+A8R/4IF/8o9vDv/XzqP8A6XT182fGYf8AF0L4+6f+givpT/ggXx/wT28O/wDXxqP/AKWz181/GY/8XQvh7p/6CK/NPGb+LS+f5I+98O/4lX0X5s6/9mb/AJOA8P8A/XSX/wBEvX7UV+K/7M3/ACcB4f8A+ukv/ol6/aivkuCf9zqf43+URcZ/77D/AAr82f/S/pg/4KOf8lh0X/sDp/6Plr89rP8A1g/6+RX6E/8ABRz/AJLDov8A2B0/9Hy1+e1n/rB/18iv5l4t/wCSgxH+L9Efs/Dv/Ipj/hP2/wDF3/KP3xB/2Il9/wCkD18Ff8EFf+TZfF//AGNM3/pJa196+Lv+UfviD/sRL7/0gevgr/ggr/ybL4v/AOxpm/8ASS1r+leGv+RC/kfh2Y/8jWP/AG9+Z+5lv1P1q3VS36n61brmw/wI7JFe4EWwNN0UhvyqpMsUj/aXOYnjKn6H9eladFaSjcSZ/Hh/wcA/sQXfhzWbL9qjwRH/AMS+7eOzulyP+Pmd7q4Y5kmLfdA4WPb7g8V+bn/BGT9nfUvj1+19aanrku200GP7aq7Qfmtbm2YDKyIejHsfoa/qj/4Le6F4r8S/sWajY+Cxm9s7k3xPycRw2t1n75C9SPU+1fkF/wAG2/iTTV1LxVo/iS9zrcst3vi8o91s1J3KNn3uOv6V+jZXmk6OTulTjqu2h8xmGVxq4pVZNa/eZf8AwcOWcOmfFXQNPiGUi8KWyDtgLezjHetH/g2q2t47+IDL08rTP/byrP8AwcTtE/xV0aFTkxeF7dPyvp6y/wDg2rh8v4h+P2x/yy0z/wBvK7YYirLh6rGa00/MMHhqMMS2t7n9hmwq8hPfFNtzyR61P5nmFox7VThbbIa/IMVP2daEuh9Slc8E/ah8efEj4dfB/U/EHwl0r+1tdjVPs0PnxQZJljDfNMrJ9wseR29a/gY8Q/HX44ftNfty+HdS/aJj8vxJbTXAjsswHraFf9ZbJHH/AKtFPP065r/Ri1CJJ7cwSruRuvOPev4Af+Cp3ju58Cf8FI/EnjrSLP7eNOjsGhHmCLJewiRuqt69welfoHCeXQryrX6K/l1PNxVZRqxuf34aG0kmi2pnTypBEmUzuxwMc1rxAtnivw2+Bf8AwU41bw/+ztqXxj/aV8Pf2BBpUUJ05fta3X2svMYZBm1tyY9gKH51O7PHQmvMPgN/wcG/s4ePvGMnhj4o2v8AwiisQIpPMub7d8rk8Q2C46Dqe/tXztTBYn2icI+5d/1/XzNljYK0Wf0K3CSuoCNtHfjNZkQaCGWeEea/HH3c1zfgvxx4O+IvhyPxX4KuvtVlcg7JdjpnaxU/LIqkcg9q6yytk89rxXyGxxj04rx62GrRxUKrhdLfXb8jbmjNH8OX/BXT/gox+0J8QfiHP8IIYv8AhHtL0PDE7ra788XCQv8A8+6su1l/vHOe2K7/APZy/wCCF1h+0l+zhB8XNF8U+Vq+o+YIh9iLZMU7Rn717Gn3VPVRUP8Awcbadpcf7QmjakJvIkPmbvlLbsW1qB7DFf0V/wDBKTV7f/hgvwpquqPujWS/3Pg9Ptco6Af0r9Jhj5YXLaVfDJKUnZ7dDxKtO+OnG/uWWh+dn/BLf/gjBqP7LPxEu/i38c9U/ti7tih0pfIFv5TbZ45f+Pe7lDZWRfvrxjjnJr+jeOSdbokH91wAv0H51/Oh43/4OJ/g14K+LU3gv/hH/tumIwR7r7XNH5OEznZ9gZm3NxweOvSv2q/Z8/ac+CP7TegQ+LvhZqH9odSR5U8Wzll/5bRxZ+4e3aviM3wuZYmSxPNZXvt/l+Vl5nt4WjQpRfItz6bBRBg0xjujOOtZYu5RE+f3k0XOzp19+nSv5tvGv/BbcfAT9te5+D3xns/7P0K88pUuPM83bttfNJ2wWbucsyj7w6/Wt8Nga2KlKjTXvW0+f4FwdNxcm9j+kqCRvNA7mtII6S5xwetfhh+1F/wXJ/ZE+FvgTU7z4Zav/wAJF4g8lo4bbyLy0w0kLMrb5bJo+GCjB65z0zXwr+wD/wAF7fFHx6+Nlv8ADD4r6V9gs72dYbZ/PSXHmSwxpxDYof426sOnPrU4bhXH0KDqVr2TvqunpuRSr0qt3FrQ/a7/AIKO/G/45/Bv4B6n4j/Z803+0tYtUkluU863h8uzSGZpJM3KOh2lV+UDcc8d6/kU/wCCe/xY8efHn/govp3xK8fp5mry3CtNFmMbWa+gkbmNUQ4Zj0Wv7oPirdR6j8H/ABHf6Y2+K40W8eM4xuLQuR1wRn3Ff56/hj49eMf2Y/28PE/jbwPpf2zWJ9Yu4lTzkjyZLpW6yJIg+ZB2/Svpclo0p4as1H37W/rT9Tw8+xTi6UaC1uf37/tFftG+Cv2avhdefFD4mN9is7dXERxJJvmEbyKn7qOQjdsPJXA71+WH7Af/AAVisP24f2itf+G9va/Z9K0+C8kgbeX3iGWJV4+ywsMiXux9/b7I+A/ijXf2r/2TVvf2jNM/syfxBbLbpH5yzf8AH1bDBzbCIdXYY+Xp24r+Wn9v/wDYM+If/BPD486b+0j8Bx9t0izmj1m4P7uLYYriScr/AKRPO5wkK8hD9M8Hy8uwNGuqlHE79L/kenjKzUYRj8R/ctavBHH5VouEA657/jRGGabg1+Vv/BMP/gpF4G/bk+GNjE8vkeJba1Rry3xI2SkcRkbf9nhj+/JjC/hxX27+0x8bvCP7P/wc1/4jeJrj7MtlY3L2/wAjvumSGSRF+RJMZ2HkqQO9fPY/JK88RGm3ontY3hiY06d57n0HOE2jf2P618v/ALYHxr1f4AfALXfihocPnT6Zb3EoG5V/1UEsv8SOOqD+E1+af/BJr/go1qH7YHiLxd4U8TnZcW+o3d1Yjg5s4xAFPyW8Q6yH7x3f0+t/+CqOsS6X+xh4tuIRljZXg/O0uPavTpZbOGMhGppsrE1MTBwvF3P5srb/AIOP/j1JLcW9joP2gwStGP8ASrdOF786fVv/AIiOP2kO/hfH/b7a/wDyvr8jP2Of2OviD+278YYPAXgpvsjhfttzPiOTESSxq/yvNDn/AFgPDZ9B6fpF+2Z/wQz+In7OHwsufipoWtf21Dao0t1F9mjttm2OSR/ma9kJwE7Lznjpiv1CWXZFQlGhiV+8aT+8+br4/EqLqUVoeon/AIONv2jicnwx/wCTlr/8gUp/4ON/2kO3hjP/AG+2v/yvr+cuzgRbpbic+WbaLynXryDk819/f8E+/wBgHx1+3R42v9I0Kf8As7TIXkMtzsjmwFMWfkaeFvuy54Pb16dFbJ8kowVSqvdPLo59mFSfJb8D9L5/+Dj39pCKPcfC2e3/AB+2vH/lPr+iT/glr+2P4y/bZ+A2ofEzxrp/2W4t9R+zqvmo+R9nhl6xwwjrIf4a/ke/4KI/8EiPiN+xJ4ZtviLa6p/bGiuUtWk8iO3xK4lccG6mf7sefu45656/0N/8G7svn/sZas5+/FrWCPcWVrXzfEGFyhYKGIy/u0fQYPMpuusNUXvH2t/wUK+KX7U3w/8AhJNdfAPSfKu2LA3Pn2jbFWSIKdlwrA5BYe35V/J1/wAEuvH3i34mf8FSPBfij4w3Pmau11dBhsQfdsLhf+WCqn3QO361/fDDeC4R94xtxX+cZ8Nv2jbf9kn9vA/GaDSftMugXLy/6/Znz7eSLvHKB/rP7p/DrXFwnShjaOIhHfldn56+XkdmYwlSfPc/0eVuYZrQSDgGpZ1Zh+6O1j0OM1/nlfFf/gtJ+2X478dxePbPVPslrpTF4rLyLF93moIz+8NopHTdyp9K/sS/4Jyftiv+1R+zNYfEXxGv+np5on567Z5Il+7FEvROy/4142Kymvh4+0mtL2Jw+aQlU9lNa2ufll8ZP2RP+Cjmvft9aN8RvBWo+VoFnIzF/J0w7Q1ns6SSiQ5b/Z/Sv1N/b88DeM/i78I7f4IeGr/y77V0Md23lIf9W0Mq8MyL/CfuuP6V+H/x9/4LnePfhh+2jbeHWsvM8K6K+buDzIxvE1qNvzfYmlGJDn5SfTpX9UvhfxNB4/8ACNprdiPJiu037fvY/EhT+grjzN1KEaU4fE0vz+W9/wDgF0q1Krzr1P55PE3/AAQG8GeGfh2NU+E2s/ZPFcYLxz+RJJ8zuM/LNfGL7hYc/wA8V8a+Lf8AgpH/AMFF/wBgjwnqHwZ+MOi/2lqViqfY7v7RpkOBIwlP7uG3nX7kij5mPr1yK/SH/gqR/wAFXNR/ZS8faD8Mfh/PsmvGmF3NtB2KsUMifLJayg53kfKw96/aT4Y+MPBvxX+H2n/EDw/cC90+6jysux48sp2v8rBW4YEdPpXTObilWxEd1v5HHWlXrVYxoy91L5H8/lx+0v8AtCfsQ/8ABM7RtZ0nRPJ1q4lv55r77TbN9mEl8rKfKaOZH3LKVwAMdT7fgH+yt8E/iT/wVS/a5Zvi9c/2eJGQNdbIpf8AlhIB8kL23/PFR+P5/wBpf/BTiXTx+w14/iul8zZb2fmRZIyDdwY+YdPwr+Kr/gmf+0d8Of2Mvj9B8YPHa77ZpWxHmUY2Rzx9Y45j1kH8NfTZHWoTy6rWowXMtn6Lz/IyrNU70qj1Z+mfxF/4Nwfie3xAXQvCviX7b4Yu8C4u/scUflBUDD92+oeY2ZPl4Pv0r+qv9mv4B6L+zV8IdN+E+k3Xni1EmJtjLuLu0h+UvJjG7+9XJxftk/CWH9me5/ajvp/I8PWib5n2zNtHni3HAi8w/Oe0f6c1+Q3w5/4OEvgN4o+JMPgrxHov9maZO7Kmo/abibojMf3K2AbqAOvfNfO5rj80xeE9rGPNGLtt/kethKdPDx9n0ep/RhshmcKx3Mv4daogwadbGW4OFHU/j7Zryf4VfGv4Y/F7SZPE3w11L+0bVlUs/kyxYwSvSVEPUEdK9X3Jdxeep8z0HSvnK9GuoRqumvaL+vn0/Q6aNei5tJn4cf8ABYX9m39sn49eH7KT9mu88qzt/MNzH5dk2Q3kBebqRD95W6fyxX6O/sVfD/4t/DL4M6d4a+Ls/n6jEH3HbCmMyOw/1LMvQjvX5cf8Fef+CrOv/seWdj8M/B2j51fXTIlvdfaF+QwiCU/I9rKh+VyOWHr14r6C/Yy/4KK3/wC0z+yLqXx102zxqlii5i8wfMRctB9428ajhSfuH+te1iMqxH1WOL1Svb1/z/QVXFU/aqL7H7BzHGPfis+Zih6V/K/+xX/wX1bx78Tbj4YftRWX9k+dK7Wl15nn4G+ONBstLFc8lj8zdue1ep/t0f8ABfb4YfCnZ4C/Z0f+3dfiulilkxLbYUGRGG25sWjPzBDkN344zW7yPHOEZQg9fQ4a2Mp8spbpH9JaXDMNo4HSrSxkrnNfg5/wSj/4Kq3f7aviTVvhX4+0n+ztctIp795PPEu6ODyUbiO2hQZaQ/xZ46EdKv7d3/BWzS/2cP2nPCPwf8DyfbLWe4s7fVjgx+SGunhmH7y1kLYVRyjc9uea8KeR4mNaUcQm326r9DenmNL2MZXtc/eoQbTmmXIJsJ/91h+lcT8OfHmk/Efwhpnjrw9JusdVtY7qIYI4lUMp+ZVboe4Fd9cnzLcqe4oo4CNCpeJvDEKpDQ/j+/ZijD/8FR/2nIezaL4zH53EdfOPwAAT9nD4/WXZdb1z9LIV9f8A7PNoo/4K+ftBRp9+Tw54vA/G7jr5U+DFjqEXwp/aTg9NU8R+n/PoK+/ytpxb80fM4mFptmv+y22z/gjF8Q4f+pmmH/lIFXvjGgj/AOCKfw/jz9/xToP66dJVT9leG7t/+CK/j5WPJ8Qy+nT+yBXQfG+Uwf8ABGD4XJ/z08ReHQPxsJKnFfx6/wDiX5Cf/Lr0Z/UT+wHEIP2K/hbGP+hY0z/0nSvsUdBXyJ+wgNv7HHwzU9R4b03/ANEJX12Ogr4rE7s+nw/8KItFFFcpqRy/cr8rf+Cxn/Jkut/WT/0luK/VKX7lflb/AMFjP+TJdb+sn/pLcVvgP96RNX4DxH/ggX/yj28O/wDXzqP/AKXT182fGYf8XQvj7p/6CK+k/wDggX/yj28O/wDXzqP/AKXT182fGY/8XQvh7p/6CK/NPGX+NS+f5I+98O/4lX0X5s6/9mb/AJOA8P8A/XSX/wBEvX7UV+K/7M3/ACcB4f8A+ukv/ol6/aivkuCf9zqf43+URcZ/77D/AAr82f/T/pg/4KOf8lh0X/sDp/6Plr89rP8A1g/6+RX6E/8ABRz/AJLDov8A2B0/9Hy1+e1n/rB/18iv5l4t/wCSgxH+L9Efs/Dv/Ipj/hP2/wDF3/KP3xB/2Il9/wCkD18Ff8EFf+TZfF//AGNM3/pJa196+Lv+UfviD/sRL7/0gevgr/ggr/ybL4v/AOxpm/8ASS1r+leGv+RC/kfh2Y/8jWP/AG9+Z+5lv1P1q3VS36n61brmw/wI7JBRRRWxJ+J//BYn9vWy/ZS+Gc/w7XS/t114ksHTzPPMe2O6S4i6eRKDgpn7yn6da/iY+E37SvxA+BvxrX9oP4aWmyX7YLu5j3xnKeak7jMsbjnYBkJn0Hav9Fb9tf8AZx0v9qD4J6n8L5/lurqKXyH5OHaGWNeBJEOsndsfzr+Pv41/8EH/ANqf4UeHtS8QeHbj+0bGIyS+VstIsIqs2NzXrN0XHTvX6Vwbj8BTpzo4u13sfKZvgcXKt7ai7rsfNf8AwUL/AOCgX/Dc1noXxEfT/wCz7zT/AA/a6ddr5vm751meV2z5EIGfM6KpHoe1fqX/AMG0Ugm8ZfEKUjGItL/neV/NT4w8C+Ofh9q0/gnx1p32KSNmDDzY5MhCRn92zY5Xpmv6Ov8Ag2n1/wAN6J4w+IWi6xJsubuLTBbrhjuKG8ZuVBA49TX0vEE6FPJfYYOF4vqfN5NOv9fcastb7fI/skVAJC46Gswn94cVLbLcCR/MHyYG08c0qKTwvavwnFYeNdwi3y7n6aqsrXsfJ/7Z3hD47+O/gzeeHPgDqP8AZ2sXCqPM8m3m6SxMOLghPuhu/f6V/BB8bPhZ8Qvgh+2pD4Z/aA1H+0dSuivmjyY4d2LQOv8Ax7s6jCsvQ/1r/SYZD5dfwB/8FHxj/grNrQP9yx/9NsdfoPB03TVaCfT9DwM3UuaEtrn9Cv8AwWq/Z/8AFfxi/YgkHhqb7Hp+h20bzx7Uk+0C4ntAoy0iMm1lzxnPtXmtx/wQX/ZJ8ffDq1tdBT+wdXkiUvdZvbrkkH7jXqp0BH4+1f0E2iWF5odlpGojdHcwRgJz821Qeo6fnXw/+2B+3R8PP2RdIh0i7X+1fFOo7l03SsyQfaGj8tnHniGWJNscm/5yM4wOa+foZriGnTjK0Yt+bvf5m9fBOLh72jP49f29P+CfnxM/4JjQ6JqHh/xB/bVl4ke5RH+yxW+PsoiJ4ae5PWbH8PTv2/pc/wCCGnj7X/iP+xRovifXzunklvVJ+UfcvLhf4VUdB6V+C/8AwVa+Ef7SXjP4a6B+0T+0hc+Rea690INJ2WrfYltmghz59q4WXzU2NgoNvTk5Nfuf/wAEC51uf2HtIjQ52y3n/pZc19ljv3mTqvNpt9vVevz/AEOWeNnHFrA/j8j8ef8Ag41vvO+MGjW95b+bjzdh3bcZgtM9Ov41+9v/AATT8MSeOP8AgnD4c8P2x+zG4e+A/jxtvpG9R6etfgf/AMHHO+T9oXw/AeFzNj/wGtK/ou/4JTyuP2KfC8adN97g/wDb1LXk41cuT0JLXVu336GWF54ZhOM5XR+On/BO7/gkH+zF8bP2fp/GHxSh/tPVp7q7WObddw+Rsu5kJ2xXSK+5VA5HGPWvmP8Ab2/4Ikv+y38Lda+Pnwv8T/b4dMSJza/Y/K2B5IoR88t5ITnzCfuHp+Nf1h/Fv4sfCn9mTwnqXxE8Y3P2O2iWMzfJNJu+ZUH3FkIwXHRa/nO/bL8U/tNf8FBPhZrXxA062/sv4caKsbJFvtZ/N82SOJvmIguFxLEDyp64HHNebhcxxFRwVd6XWn9I9TH0HKnOtRfLY8q/4IDfGr4s+Nfjbr/h3xBq/wDaNnGloBB9nhi8nKXRPzKgLbiB34xXuP8AwcuaRZW/gPwFqWnr5GoTzakDNy33RZgfKTt6cV8S/wDBuctqv7SPiJLFdkK/ZVAyTyEvM8nnrX6Ef8HLQA+Efw8H/TbVP52dfQ16UcPnqjh9L9v8J5tGrL6j7STu1/mfCf8AwTu/4In+Dv2mPgZ4e+N3j7xLua8lunMf2OQfNa3LRrzHeR9k/uD8a+O/2g/2cfD37M//AAUY8J+E9B1HzbOz1ezjA8ll/wBXqBX+KSQ9E9TX9VX/AARDAP8AwTw8GZ/566t/6XTV/Ol/wU0H/G2DwoP+phs//Tq9a4fMsRXxdWhUleKvp6GdKg8Oo4hyvd7H9DH7c3w//ah+Kf7J2kad+zlefYkTSYrm7k8u0k3QLBN5iYuWXGQy8ryOw61/Kf8A8E9vC3iXwx/wUX07Q/iXN9o1JNUjSYbUX96L6BT/AKoleoPQ4r+8NmZf2XpivUeG5T/5LGv4ev2TtVudO/4K23YM2BP4idT8o/i1GH615uRVIqni4Ja2ep3Y2EITp1Wr3P2H/wCDgX4w/tAfC+Lwjp/w8m+zeGbV7K/k+W2fN3C91j/WI0n+rUdPl9s19xf8E1/i3P8At6fsE3+m/EaPzpIbR9AmbIXdvsY9x/dJBj/Wngf99eny1/wcUvFF+ynpdukuS+vWbldv/TG7HWvWv+DfQ/8AGE+o/wDYXX/0itq4nhlTwEcSn73NudFKup4j2LWyP5n/AAJZa1+yD/wVlj8A/CifybdPG8WnXSbVb/QTqUccozMZT91B907vQ1+sX/Bx3rfjbUNH8Ky6ZLs8O3EFo0i7Yzmdmu8ckCQfJ6cfjX5n/Gy48j/gs/eE87vici/nqor9DP8Ag46Wwuj4Esr6Lev2ewfO4jpJdjtX1dL2csww05xvdL8jxcyxU61KVtLOx+CH7OvxJ+JX7K/xQ8KfGrS5/s9jBLZzTDbE++zWVJXHKysMrH1Cbh29D/Yz8aP2vvA37Y3/AATP8VfEjwid7jTLqGVcSDE7adJIeXih/vjouK/B69/ao8J6t/wT28M/sleCLfOs67rWm2Ei7n/1NxaG1Y5ki2feYdJAfQ96/U/wz+x5Z/scf8Em/FGnsuy91uyudRfknDS6Wykf62UdY+2PpXLnvs62bQtHk1t3+Zhg4VYUpSlK+lz8g/8AgjH+3d8Mf2S/ja+hfGx/sdvrsT2cM+JZMPcyW6KNsEEp/gY8kD1I4r+gn/god/wVU/Zd8D/ATV/CHgzV/wC1dY8QWE9rBb+RdwcXUM0atvktWThgBgkdew5r+Fma5vLyIX80nJTyduB3561No3hXU/GviDS/AXh1N1/qFxDEhyPvSMIx94hepH8Qr6nEcKUK9dZhWqWsl07HnLOKk6f1elDd6kHirU7fW/EFzcal+6vdRZ7mNPvf6wnHIAXr64r+iL/ghb+3h8LvgVeah8GfjPcf2a15NIbebbLNu3C2hVdsED4yVY5LDGOexrlPDH/Bv/8AtA+IPg5a/EQ6r5d9JZLdpbeRbn5fK343/bgvXj7v4V+H3j74deKPhP8AETxD4G8ZnOq6Bc3No33B/wAe7FT9xmX7wPc/U0m8szW+ChK1tLilhsbg4LES1XY/rh/4LW/t8fs463+z7B8EvBepf2hql7dRXoTybqLbH5VzCTmSAKcMR/GDz0717j/wbyR+T+yPrdxH9yfXS4/4FZ2tfw0vBc/ZkvJJMx3REpXA+83vX9zf/Bu9/wAmda3/ANh3/wBsravleIckhleCWHi763v6nq5PioYrHOvKNpJH723Efl28kiR72bB25xnn9K/z0P2b/gT8Kf2o/wDgpFp/wz8eWv2a3167mjeLzJnz5FpNJ96N4j1jB+8P8f8AQ3uP+Pc/Sv4JP+CdQB/4K7eE8/8AP9ef+kFzXi8OxmqeInTdrRbPVzyUnKCT3P1W/wCCw/7Cv7KHwK/ZUufGPw/8LfYdWhjAjuvt15L5ZE1uhOySZ0OVYjkcZr3b/g3eglb9kVWlGUd5sH6XlzXon/BfQY/YmvMf3f8A25ta4P8A4N3iR+x9H/vz/wDpZc1dXG1J5NyVHf8Aeb9djSnTpQxKXLryfqfgD+1zomjaR/wVYvNJW385JPs37ncy7v8AQA33snHr1r+8K/QW/wAP18tvJ2W8OGxuxnb2r+Fj9rT/AJS23Hsbf/03Cv7vrg/8UYv/AF7w/wAlrmzWl7uHpy2cUPC0ISdWUVbc/wAvzxXp3xF8ffFnW4ZR/bOt3s+yyhzFb7jHnd83yoPkH8WOnrX75/8ABEz/AIKcW/wO1C3/AGTvjy32IXjuumLjzMPm4uZube2fPBX78n07ivzT/wCCfPxE8KfCj/gor4e+IHjiTy9K07Ub1rhsO2A9tcIOI1ZvvMOimvvT4efDAf8ABVf/AIKV33xbhsfL8O6X9mHmeZnhrJ4Om62k+9D/AHT+XJ+0zRUP7PhSlS0UF71+t9rfK97nkZXVmq8op6J2P1C/4LSf8FEde+GPhGb9mXwh4e+13viONBLd/a1j8lU+z3KHy3gYNuBI4kGOp9K/nj/4JJ/Bb4efHP8AbD0fwP8AFuLz2mmkPkbpUzm3uH+9C6f3Qetf2l/8FGPD+k6d+w94vs4oubO1tFjOTx/pMAPf+ea/kd/4IWAf8PAtIH/TeT/0luq5cgxFGOS4vlp+8r6/I6cdT9piVB+X5n9Vf/BTT4Hah4t/YG8XfCb4WTf2XKtvbrAdonxm7gc/61wOgPVu9fmd+yv/AMEXP2YvjF+ynoWseKbP7P4paW8Dal5l2+CLhlH7lLtIvuLt/HPWv6bZ4El3owyDivjD9o39p34Q/sY/Dj/hJvESbTIXNva5mPmlXQN86xzbceYDyPavzjB5jmFJzwqnelK8vTyPbxdCMuSSe2lj+PD/AIKA/wDBML4o/wDBO3w9Y+PvBfjz+0oLt5tkn9mRQ+X5ZiHSS5uC2fNI6Dp+X7Tf8G+fxY8e/Ff4Oa03i3Wf7QktBHgfZ44sbp7kfwKvZRXwP/wUo+G/7S/7Sn7MOoftafHC8+xaVoIMulab5drJjzp4baT99btG3O1W+eM46Dua+sf+DbfnwX407DyrL/0ddV9hiq/1jKY1XvHy3/M8SFdQxiwlt+pwX/By0txp9l8Lr2CLdLLcasAcgfdjtPqK++f+CCfh7w1af8E9PDd9Zx4ub+41JZuW+by764x1OB+GK+D/APg5uP8AxIPhgP8Ap41b/wBAtK/Sf/ghHlv+CdXhEntc6p/6XT1njJV3kVKaloppWsaUoc2OnB7WZ+D3/Bwp8OvCnhr9orwX4T8L2f2OS90OCdpfMeTG66uUJwzHPY9RXd/C/wD4I5/ATRf2NbP9pn44eI/sy3WiLqsT/ZLl+WtfPHFvdnurdY/w6Crn/BxdGkv7V/w+il+42g2ob6G9us16n4a8Hal/wUb8DfCb9lnQ4Nvhnwbpekyam+4fM2nHypBhmt5BmOb+CRvbJ5Hf/aWIo5fRqqWi+b/HucuEpxrVamC2T6nwx/wSb/Z/07U/jP8AFX41eCp/t3h3RvD2vQ2V5tMWWjEE0TeXJIJPu4OGQ+h9K/KP4saP43+Ifxr8Y6x4Xb7XcR317NM+I0xhyzHDlR3HSv8ARk1T4ReDfgB+xxrvwz8Gw+Xp+ieF7y2jG5zuEFoyA/OzsOFHVj+NfxXf8E9vjl4F+B37enj74qeOj5Nvbadq6xn942WFxDIB+7Rz/AeqmuzKcwljvbV6lPa1l/wQzPKkoU4qtblVtVufpB/wRJ/4KYW+nLZ/spfFyTybi2MdrZSY3ZeMW9vGmIbfHLE/M0mPU96/rbiuFmiG3vX8Xf7EHwV8E/8ABRr/AIKNa5+0NrK/8SnRNUudQszmT5nt7yG5j+69uwyJD95CPUdh/Xd8Ytam+GvwX8TeMvDa+VceH9Evrq2Gd2DawPIo+YMOqjqD75r5LO+V4m0YpHqYHDewpfxLn82HwE0Gd/8AgsT8c2h4M/h7xXj6tex4718ufC/wl4hi8A/tJwjj/ia+JP7v/PqPevgX4kf8FSP21vEvxL8R/EPw74jFn5d5c6bs+x2EmInYuRlrYZ/LPvXmJ/4Kc/ty9/FY/wDADT//AJGr7DL8nqU6MXB817M8utiadSclJ2sfqT+znomrWv8AwRi8aW0jYY+IncjA+7/ZPPeux/aQ0xYP+COXwbhHSXxF4ZJ/GxlHrX5Ayf8ABTT9uWcGFvFgCv8AKf8AQNP6H/t3rWm/4Kf/ALduoaXG0njIC205RZhf7O0/lVGf+fbPT6/WuvFcPYhRq4i3xO/pY8yvmMYVIRSvb9T++f8AYpg8j9k34bwjonhzTh+UK19WDoK/EX/giD+1T8QP2pf2atQ1b4lX/wBvvdE1Macr+VHF8sdtA/SKKNern1+tft3X5TjIShNxkrH2+Dq89GOgUUUVxnSRy/cr8rf+Cxn/ACZLrf1k/wDSW4r9UpfuV+Vv/BYz/kyXW/rJ/wCktxW+A/3pE1fgPEf+CBf/ACj28O/9fOo/+l09fNnxmH/F0L4+6f8AoIr6U/4IF8f8E9vDv/XxqP8A6Wz181/GY/8AF0L4e6f+givzTxm/i0vn+SPvfDv+JV9F+bOv/Zm/5OA8P/8AXSX/ANEvX7UV+K/7M3/JwHh//rpL/wCiXr9qK+S4J/3Op/jf5RFxn/vsP8K/Nn//1P6YP+Cjn/JYdF/7A6f+j5a/Paz/ANYP+vkV+hP/AAUc/wCSw6L/ANgdP/R8tfntZ/6wf9fIr+ZeLf8AkoMR/i/RH7Pw7/yKY/4T9v8Axd/yj98Qf9iJff8ApA9fBX/BBX/k2Xxf/wBjTN/6SWtfevi7/lH74g/7ES+/9IHr4K/4IK/8my+L/wDsaZv/AEkta/pXhr/kQv5H4dmP/I1j/wBvfmfuZb9T9at1Ut+p+tW65sP8COyQUVG+yQmFu4psMEcC7Yxit0m3psKxRmuCs20cc4zSSx3KkyK3mD+5gD9a8l+Pvxh8N/AL4Zax8VfFB3WulWs8/l/MN7wxPLtyiSEZCEZ2kCv5Yfi1/wAHMWoSxahonw68FeRMbl7S0uf7SDbidyo2yXTsDnBwx+tduUZTjK/tGtVfQ5sVmNKhaL3Gf8HE2n2V/wDGTR7V4fKupPDFvs+Ytx9unPsPXrX84fwZ+N3jP4A+LbP4k+FpfLW0ZzNwhyCrRr99H7uei19KftX6/wDtM/HHxPD8cP2hv3R160XUNPT/AEU4sLqRnjGbYJ/EzfeQP6gDFfZv/BGH9kf4R/tr+Cfido2uwZ1LS4dLMLbpuDLNcZ4WWFfux9ya/YcFSp4fKnSxmi0X4r/M/P8ADVFLMZ4mK0TvY/ss/Yq/ab0X9qv4HaX8T9GG0TiRWHzHmOV4v4o4u6H+H/GvrK3HLZ6Cv4bf+CMX7Vl3+xX+0rqn7OHxOb7PpeuSJHLJgN5KwR3VwpxFHMzbi6jhxj9K/t+03VrLWkS8tfniIBjfkZz14IB/OvyvPcqVHGXp/Ctvmfd4PH069O/c1PNZpNpr+Aj/AIKS8f8ABWbW1/2LH/02x1/fVaRqkzb23zD75xjjt7V/At/wUn/5Sz62f9ix/wDTbHXqcFKXPXU/60ZyZ/aNOLR/efb372PhuyuG+SLyI/Ml6+XwMfL1OTxxX8P3/BIvxn4r8ff8FPNL1X4if8T15bu4Wz1D5Lby9lndK/7mMLu4AX5h2yK/t7iP/FJWwP8Azwj/AJCv4W/+CKpz/wAFI9FP/T9d/wDpLd1ll2EjUw2JqPeKDEV06tGB+wn/AAcUCW2+AXhWWRt07yX4dsY4Elnjjp0r2v8A4N9ovsn7CulXMp4M15/6W3NeNf8ABySuP2fvC2f799/6Ms69v/4N+hj9iLTM/wDPW7/9LLmuypUlPJYpdG/zOKdC2Zqf9bH5Nf8ABx+iyfG/wuYxls3Gf/Ae0r+hn/gk5Kv/AAwp4SlfgmS//S7lr+fD/g5Gwfiv4X/3rn/0RaV/QV/wSIA/4YG8Hkf89dR/9K5q5sZzLK8PzdWzTC03LHVpvsvyP5aP+C4/xO8W+KP24dU+Hl5eYg0iO0e0t/LT94bizt2cbwoK4xn5mPtiv6lv293uNG/4J2eKxoifZZbbT9PPl5D8vdW/dsjuTX8hn/Ba993/AAUe8XZPSDTP/SGCv7Av29ef+Ccvio/9Q/T/AP0qt61xuGjQo4eUerv+J10Knt6E49rn82//AAb0h5/2n/EmpMuwTfZABnP3Y7sH/OK+8v8Ag5a/5JJ8PP8Artqn87Ovgr/g3nGP2o/Ew/69P/Rd3X3r/wAHLX/JJPh5/wBdtU/nZ16eLTWew5t7f+2nj0E1lk0+/wCp99f8EQv+UePgz/rrq3/pdNX86P8AwU0/5SweFP8AsYbP/wBOr1/Rh/wQ/wD+Uefgv/rtq3/pdNX83H/BTH/lK9oP/YyW3/p0eubAP/hSrfP8zrxX+7U/U/s+lgjuP2YJIpTgHw5Jz/27Gv4Pfg5451r4d/8ABSDxb4q0HT/t7aPq17OB5qxZEF5G/wDEG/ujsa/u/sP+TWP+5Zf/ANJjX8Of7Lf/AClpvf8AsaX/APTjFXLkL1xfozbM/go/I+kv+CkX7fPxD/a9/Zf1Hw/4l8O/2ZZaL4ujgW4+1xTZeCCbC7UgibkSE5yRx37fsz/wb6f8mUaj/wBhhf8A0itq8n/4OOVA/ZL0LAx/xP7D/wBE3le0/wDBv1/yZhe/9hdP/SO2q6//ACKIv+8GGf8AwoN/3UfzgfGuEzf8ForoD+H4nxt+WrCv0p/4OLby/hXwVFZRb2e1ssHcByZLv1r84v2hiR/wWnkH/VTI/wD07Cv0e/4OMOdO8F/9e1l/6Hd16s6ns8Thp+S/I8SVpRqx/vP8z46/4I0/sx+K/Hf7Uml+OvGtru0qxsBdQ/OgxPFcW0iH5JFboT1Uj1r+oP8A4Kp2G/8AYm8URr8kUVldEjr8q2dx756V5F/wRGVV/Yl0ot13Ww/8lIK9n/4KxaeNQ/Yo8VxqceXaXb/lZ3HvXh1Ma6uZxk/5j3auGUcLJ/3T/N6ltrGWcz2kufLbfjaf4frXu37NfxRsPhX+0J4V+IfiaPNhZ39m7nJ+6lxHIT8is3RT/Cax/gX8FPiX8ePGC/Dr4X2X26/u7oR7PMijwjskZOZXjU4LjjcDX2P+03/wS7/am/ZT8Dw+NfHumbLGVVZ2860O3crt0juZWOBGe3/1/wBZrZthvYLBzfvs+DwdCvZ14apM/t58Eft//s6XP7O1v8XP7V22VtpomdfIuTgrB5pXPkZPHcKa/go/bd+Lvhb4s/tc+MfjB4GbzNK169v2U4cZ+1XDyA/vFVvukfwj8K+Qh59lZxS3EmI3ZSwx+fSvpP8AZy/ZY+NP7XHjBPDHwdsftUMbYkbzIEwFZATieWI9JF6GvDweRUsrqrGzlo9TvnnGIxq+rcp80myt4JPOD5dm4GDwp96/uk/4N3v+TOtb/wCw7/7ZW1fyk/taf8E+/wBqL9kOK11T4h6X9n0SZkTz/OtHzKxfA2xTyvysZOen41/Vh/wbsRW4/Y9114efM18s31Nna1w8a5hRxmGVWhtsd3D2HqUcVOFTex+/dx/x7n6V/BL/AME6f+Uu3hP/AK/r3/0gua/vZnRUgYKMV/BN/wAE6f8AlLt4T/6/r3/0gua+W4a/g4r/AAP8me5nPx0/X9T+g/8A4L6/8mTXv+7/AO3NrXBf8G7/APyZ9H/vz/8ApZc13P8AwX6/5Mivv9z/ANubSuE/4N4/+TT2+sn/AKV3Nccv+RSv+vn6Gsv98iv7n6n4P/taf8pbrj62/wD6bhX93tx/yJq/9e8P8lr+D39qv/lK6f8Aeh/9N4r+8K4/5E1f+veH+S105v8AFhv8KNsv2q/M/wAvj/hAvEPxO+KeqeBvCkH2nUL+5kWGLcqbihZz8zsqjhSeSK/tF/4J5pcfsg/8Ez/+FsWPhvbqlq1x9oj+2A+Z/p7xr8x81VwJM8LX833/AASwAP8AwVL8IA/9BLUP/SS6r/QphRUt844xXXxNi5RpU6K25Ty8lpp1aja+3/kfgbqH7aOpftof8E0fHvjjUNH/ALNkhjhVj9oE2duoiMdIYf8Ann6f41+Cf/BC0/8AGwPSP+u8n/pLd1/YR/wUeKt+xH46K8fuLX/0rhr+PD/ghic/8FCtIH/Tw/8A6S3VbcM65LjLu+j1+R3Y6C+uKS00/U/vumnjhDzSnCrjJ61/Cvonj7x38T/+CummRfHCb7bcWlw32DT9sce/dp7hv3sAUD5VVvm9Mdc1/dPIfldfpX8B/wAI2z/wWL0Js5/0qXH/AIL5a+fyXDwr+1bWqizvxVVKKj1bR/Rl/wAFyNOOofsA67qF5H5D21vFsjzuxuurUdQfSvkL/g2058E+NCO8Vl/6Ouq+0/8AgueMfsAeIc97eL/0qta+L/8Ag21UjwP42J/55WX/AKOuq2hKTy2p2Uv8jw/qzeZwqeRw/wDwc4n/AIkPwwH/AE8at/6BaV+k/wDwQh4/4J0+Ev8Ar51T/wBLp6/Nb/g5x/5Afww/6+NW/wDQLOv0o/4IQEv/AME6/COf+fjVP/S6evXxKtw7T85/ob0F/wAKM/Rn4uf8HHKGT9qTwGi9W8PWwH/gZdV/RN/wTP8Ahf4N8GfsffD3XtDtPLvdU8PafPdyeY53ySwR7zhmKjOBwoA9K/nb/wCDjM5/ao8An/qAW3/pZdV/TJ/wT0IX9ir4X7v+hZ0z/wBJ0rgzCfLltFHDl7/4UqiPYP2g7Ff+FD+NreBcJLoOpHOf4jbyD1r/ADXNZ8KeNfiB8bfFvhvwRbfaJre+vIZBvRMqr4P+sKjuOhr/AEr/AI/MH+CHjRR0/sHUv/Sd6/iu/wCCTUa/8PQtf3dxej/ydt634XzJUsLibanVnODnXrQjFn646X+0P4q/4Jm/8E1/hn4t03wf9pu9WXRrW8X7eiZkuLQ73yY7gf8ALHooA9CO/wCx3wg8aP8AtV/sr6brviCx/s6Hxr4dVpl83zti39vhhlRETgOegX8K+pZreN7Z7ZhlXyp/HiseAW1riwjfiIYAweAK+EznNqixCqRXqexh8CoUrSZ/Hj+3r/wRL+A37OnhPU/jzqnjn7BZ32qmf7P/AGZcS/NIskoTeLyQ9ExnYK/F4eEP2Us5/wCEs/8AJG8/xr+yz/gu+dn7CbyoN4OsQjGcZH2W6r+BDdEGOLHp/wBNa/a+Can1rCN1JbHyGc05UK0eTW/mfYLeDv2UDlpPFoCdWP2G86d+9fVX7GH7AH7Lf7ZPxduPhD4P8afv5NNm1Uj+zrvqjxx/xzwj/loP4vw9PyaiaEzIps8ZYDPmZxX7q/8ABva9lL+3fqcM1vu2+Hr5Q28jB+02navZz/FOjhJKEjhwmJft0nC/zP6uP+Cen7C/hj9gf4PT/DLwvqP9oJqF4t9LL5LxfvDDHERteaftGDwwHt3P6GqcqDWKkaW7KkYwCAa2I/uCv5/qY11684Pofpd7wTsPooooII5fuV+Vv/BYz/kyXW/rJ/6S3FfqlL9yvyt/4LGf8mS639ZP/SW4rfAf70iavwHiP/BAv/lHt4d/6+dR/wDS6evmz4zD/i6F8fdP/QRX0n/wQL/5R7eHf+vnUf8A0unr5s+Mx/4uhfD3T/0EV+aeMv8AGpfP8kfe+Hf8Sr6L82dl+zFG0v7Qnh6NOS0soH1ML1+43/CL6x/cH51+KP7Iaq37TfhhXGR5s3X/AK4SV/QbXmeG2W08Rl9ac2/4jWn+GJlxtO2Ngv7q/Nn/1f6YP+Cjn/JYdF/7A6f+j5a/Paz/ANYP+vkV+hP/AAUc/wCSw6L/ANgdP/R8tfntZ/6wf9fIr+ZeLf8AkoMR/i/RH7Pw7/yKY/4T9v8Axd/yj98Qf9iJff8ApA9fBX/BBX/k2Xxf/wBjTN/6SWtfevi7/lH74g/7ES+/9IHr4K/4IK/8my+L/wDsaZv/AEkta/pXhr/kQv5H4dmP/I1j/wBvfmfuZb9T9at1Ut+p+tW65sP8COyRH5Y83ze+MVRWM2rTXkp4G449utWmlYXAhHdc1R8yS6nmspPuMrD8+P8APNXKUdO99PUFc/kS/wCDgf8Abd+IFh45039mfwh+60a805b66k/dn955t1bsMPDv+5j7smPbPNfzU/CjXE8P+P8AQ/Evi698zS7TUbXMflYyySK3VAW+6COlf2L/APBTb/gjf4q/a0+K6/GDwPrf9nzWVg8awfZkm8yVZZpgd0l3EBkuB90gYz7V/IL8VPgZ8QPgx8b774F/ESfzdVTU3t4DtjXdtmMK/wCqd1HzA9X/AE5r9h4VzHCwylQnZVep8Ti8PUWMqVnHmj/Xr+h+0P8AwV71rw14s+Hfwt8RaafKju/h1pEtuuGb929xIRyQOx7jNfRf/BtTpyweP/ie1gmy3EGjmQ5znP2zHU56+lfCn/BQnw3L8Nfgl8KvDHiu523r/D3R3jXZnCCVl6oWXqp6mvuj/g2tmCePfiAs8m4tFpm3jH/P56V6Gf0qVDh+NSnNSv8AqzLLaHNiPay0Texy/wDwXk/4J+L4U8Up+034C/caNqWRqz/e8pbeO2giOJJy7bnY/cjGO+RzXOf8Egf+Csuv/DHV9O+AHxql83w/dtItjeYVfLKi4nk/dwWrSHLFV+Z/ccZFf2VeL9B0jxRpb+HdaXcl0MKORnaQx+6R6DvX8gH/AAVz/wCCTnh74HabL+0R+zva/YtOt/nvrfzGk8vPkQod9zdOx3O7H5U46HjBr4LK8bDEUY4TEWcle7/Hby6dfzPRx2DnQxCqUn7r6dj+yGxmjubVLi3H7t1DKfY1/AX/AMFJCT/wVl1sn+5Y/wDptjr+i3/gh9+1b4+/aJ/Z2m0H4gy7r7w9yzYjG4T3FwF4jijUYVB3P+P83X/BQXxZH4x/4KhapJaXX26zjW1Gdnlf8w9AeoB6ivS4Xw8Y4mvBy2X37nRnblyxpW/qx/e/G/8AxS1oo7wR/wAhX8L3/BFM/wDGyLRR/wBPt3/6S3df25XPiKxg+F8Wui6+yWyQR5n2GTZyq/dxk88dK/iP/wCCNd3bJ/wUf0K409fsVql7dl2yZPtObS7wMHlNh/PNebkrqOhj6Tjsv60HONN4ilOc7O2x+x//AAckgt+zz4W3cEPff+jLOvcf+Dfzj9iDTP8Arrd/+llzXz//AMHG/iHTNK+BfhDwzMu5NSfUBsyf+Wb2b9cH+Yr2f/ggBrWhzfsZ2nhjR28o2clwxXDNjzby5bqw/qa3o05RyOV+/wD7cjuToTxTalqj8tv+DkPH/C2fDOf71x/6ItK/oL/4JFf8mC+D/wDrrqP/AKWTV/M5/wAHEPxc8D+PPjXpvhDwPe51Hw7vfUB5cnzLdQWpi/1iqowFP3S3viv6Jf8AgkN8UPC/i79jbQNO0mb/AEq2a785Nr8brmbbyVA5wela5pByyfCJLv8AmcGExlL2taSe/wCh/KD/AMFrxj/gpB4wH/TDS/8A0hgr+w39vYf8a5/FR/6h2nf+lVvX8af/AAWI17Tta/4KJ+K3uLbyZZIdOC3u8t5W2yh58sABsgY/Wv7CP+CgXibR7D/gnj4mvL+fbHLp9gPusd225tx2GRijNoTthqdtv+AefkWOhH2qb7v8Wfzq/wDBvQB/w1F4lP8A16f+i7uvvT/g5a5+Enw9I7Tap/Ozr4E/4N5rmB/2lvF19bf8SmK5FkPsP+v83Yl3/wAtTyuPvfjivsz/AIOS/EOi3Xhj4c+HpLrzp4p9ULrsZfJDLaEHOMNuHvxXbm03/bkalui/9JOzB+xrYWpS57W6/ifpX/wQ/wD+Uefgv/rtq3/pdNX83P8AwUxBH/BV3Qf+xktv/To9f0Vf8ETrvS9K/YN8I6BaX32+SBtVkNx5Ri3A3spxsOcYzjrzjNfzlf8ABR/xFpWr/wDBVfS30Jd93a6vGkjZIwF1Ny3DADqR0rgwMZrMaja3uat0KuD5ueyif2i2H/Jq+f8AqWX/APSY1/Dn+y3x/wAFab3/ALGlv/TjDX9rll4jhg/Y4F7NP5Ljwu48zbuw/wBlJzjHOOvpX8S/7KupQ3v/AAVLk1LVr77ZI/iXEUnleXgHUIiOAMdeeajIcPNvFJdmRmE3UhTdNXS1P3f/AODjlg37Jmggf9B+w/8ARN5XtH/Bv0Qf2Mb0f9RZP/SO2rpP+C2v7PWsfHb9ii9uPD6773w/cf2xK+VG62s7W6Z1wzooyWHIyw7A1+Kf/BOb/gp18P8A9nD9h/xb8OPEkvleKokuodLixIdhFjHHFyttJEcSr/G3PfinRo1MVlro01dxd/62IVSdKt9Z5bq1rHyt+0MM/wDBaWRh/wBFMj/9Owr9Hv8Ag4tX/QfBef8An2sv/Q7uvPv+CTH7GWrftnfHm/8A21/jZHiOx1V7i3XI+e9WW3vI5MwTQ4++3ymIr69hXbf8HIQ8Hy+IPCGleILjM8EVm8Eex/4JbsLyvHX1rXnVfFUqTdnFL8EKGX/upVG9ZO9ux+uX/BEYKf2JtLLdA1sf/JSCvef+Cm/hfV/GP7GfjWz0psNBp99MR8v3Es589SPWvnD/AIIo30cn7Gmnpdzb0E1ukQ24x/okGBx1+pr9YvFnh208WeHNW8E3/wDqdWsp7aQ8/dnQxnoQeh7EV8djKk8LmVNtXjzM9x/v8NKNraWP4ov+CAHxe+F3wz/aQ1Pwv47u/sF/ewXFhaybJZd9zLLaJGmI0ZRlgeSQoxycV/TB/wAFMviz8M/hV+yT4ut/jBqHnHVLG8gsB5Uq4lmtZ1iH7lG7hvvYHrX8bX/BTj9jHXP2PP2jtY1OC33aDrV3PqFvLvUYklnlCfL50sn3Y884HqM1+cup+KNW8QWqzape+dBCOI/LC4A56gA96/TqWQRzSrDH061mre76fP8AQ+UoV1l9CeHa5rtu/qc9OEv9VuEX5rO+dp4T0xG5wPfp64Nf1b/8G6XxU+EuhW+v/DWKfy9aa5nb7sx4CWkePulPvf7X6V/KKNRtr5f9Gu/JROGTyy2QOoyRx6VZstbj0Ob+0/DA8m4U/wCtzu5HP3XBHUA19fnOU1cdhFh2+VLrueNlmNdHEubpb/11/S5/dT/wX0+K3gXQf2Sovh9qdxv1e+1CGWKHZIPkaC6j37gpThuMEg/hTv8Ag328Jy+Ef2Mr6e6P/IS1hLlPpJZ2wHQn09vpX8kP7E37MvxM/bl/aJ0Twvb3P2mTTpYdUuPliTFvbzxbzzJCOPM7En0Hp/on/A/4Y6f8FvhVongLT12tY2kEEnJO5o4whPLPj7vQE1+UcRQo5VhHhJVOZp3vb8D6rA81bGSxEVo1b0PZZonjjkdjkN0r+Cn/AIJ1kL/wV28J5/5/rz/0gua/vZiIKbQdrYGK/gD8RalL/wAE9P8Agp1Y+M2g+xafpE7zTtu8zcLixYDjE7DDTdgevbtz8J4lVqdWMPtxaO7NsO5JT/lP6Lv+C/LBv2Ir/H9z/wBubWuF/wCDeM/8YnuPeT/0rua/LT/got+2545/4KNfEbRv2Uf2W186C53/AGs5jXzd0MVwoxdw25Xa0L/dk5/IH+nH9gX9j/w/+xp8D7TwFph/0hlZp2+bq8sknQyzDrIeh/wqMwhUw2Gjg6i15ub8LGODk8RV9va1lb1P49P2rDj/AIKuE/7UP/pvFf3hT8+DFb/p3h/ktfwZftIeING8S/8ABUI3cWpfZ9MsXjMz+Sz432OBxgN94ds/lX90+seJtI8LfDf+39au/ItIoIiZNhbAJUdFBPUjtXTm+scPUXZfhYWDrzjUq0nHufwS/wDBK0f8bTvB/wD2EtQ/9I7qv9CiRgtnlfSv88f/AIJjy6TpX/BVbwE0x+zwy6nqRDcvu/0K5PTkjrX+he0IktEFgcAjj/Jrh4w9u6dKVON7wFw7Bt1ZTVvePi//AIKMf8mR+Oj/ANO9r/6Vw1/Hj/wQxGP+ChOkMe9w/wD6S3Vf1xf8FSfGvhLwR+w743u/Fs2yMQW2Btc5/wBLg/uBiOor+PL/AIImeINHh/b10LXWk+zWYuJSHwz5zbXQ6Yz19q9nhWnVhk+Ipct21+nTudOYykqykldf8E/0EWz+8A74r+BX4Rcf8Fi9Cz/z9S/+m6Wv70l1ew/sxtYin/cqOX2njnHTGa/z7/gX4m1LWf8Agrdo/iwX+Ira7l+fyh3sJU6YB/SuHhLA1ZyrwkrJKS9Sce6a5Kk5W20P6iv+C6GT/wAE/wDxAB/z7xf+lVrXxh/wbb8eB/Gw/wCmVl/6Ouq+pP8AguZ4306x/YYv9AS723+pwqsA8sneY7i1Zv4do49SK+L/APg2svru7+HfjK2tD9jZEtc9JM5nu/Wro3WUVocv2r3+4csVRhiIK920Zn/Bzh/yBPhj/wBfGrf+gWlfpV/wQg+X/gnZ4RJ/5+NU/wDS6evy6/4OVfF9zc2/w+0zRzmXT5tRaZOP3gljtNvLDAxyeM5r9N/+CFratZ/sA+FtM1R8PDcaixTA4D3s56iuzEucsjpU+XRPc4KFeH9oTlF90fjT/wAHGisP2pfAX/YAtv8A0suq/pi/4J9Z/wCGJ/hew7eGtL/9J0r+Wr/g4S8c6Z45/au8EJ4cfzPsnhyF3OCMFLy5z95V9a/p7/4J5Xov/wBiD4WtYXOXPh3SxMNnRvs6ZXn+YrzM1o1JZVBpd0i8vwsfr0qvMfQfx3cH4I+Nf+wDqX/pO9fxc/8ABJhs/wDBUXXwen+m/wDpbb1/Zh+0JJHZfBDxrMZPLWPQ9SdzjPC28mR/+qv4tv8Agkjqmhaj/wAFK/Ec91F8klzeGKTc3JN5bY4AH61xcHqf1Cu6sNdTsxlflxUVFXsj+8MhmVghw3OD6Gs2+urizs3kx5rxoW7LuIH6Zq28721ojQruHA64xSRN5uVk/jU5H1rxKmLp06qpyWrPTlTlUhdOx/Id/wAFPf8AgrFo/wAQtS8U/shax4I+1x6HrNxFPP8A2kU+S1823Ztq2qno2cCUn0z1r8CD4l/ZtH3fCJ5/6f7r/Cv7u/8AgqJ4e1jS/wBlXxD4t8HHy7/TIbi8WTCttaG2ncNhztOCAcYOfSv4QpP2vv2l/IOpyeIhPI/Lj7Har15P/LKv1nhGrTqUZU6dO9t2fE5661Fws7v7hsvij9nFIj5PhIq2Plb7fc8HscY7V9n/ALE37fPw6/Yg+JbePfD/AIT+0Pe2EkRP26RP9a8bfxQTf88x2FfEtt+2b8eoQbqDWgJj99fs1vyD15MWK/oW/wCCDHif4m/HD4ja7q/xQj+1aasF0qPmJMt/oxAxEEbox616vEGKoUcO4ypX+Z4+WV61bEJzVvxP6RP2Tfj7B+1R8FtA+MQ0r7Amp2dvcBfP83BmjSTGdkX97+6K+sEGFAFYWmW2m2drHpGlrsitwFC5Jxt4xk81vAYGK/EaipurKUIWufqSb5UmxaKKKkRHL9yvyt/4LGf8mS639ZP/AEluK/VGb/Vmvyq/4LFf8mR659ZP/SW4rfAf70iaq/dtni3/AAQLBP8AwT28Pf8AXzqP/pdPXzX8Zh/xdC+Pun/oIr6U/wCCBX/KPfw7/wBfGo/+ls9fN3xlI/4WnfDvlP8A0EV+aeM+lal8/wAkfceHlS06np+rO1/Zau5LH9orw7dw43JJNjPvBIK/cX/hLtZ/vL/3zX4Xfszf8nBaB/10l/8ARL1+1dfMcC4mrTwVRU5NLnf/AKTE140gvrkH/dX5s//W/pg/4KOf8lh0X/sDp/6Plr89rP8A1g/6+RX6E/8ABRz/AJLDov8A2B0/9Hy1+e1n/rB/18iv5l4t/wCSgxH+L9Efs/Dv/Ipj/hP2/wDF3/KP3xB/2Il9/wCkD18Ff8EFf+TZfF//AGNM3/pJa196+Lv+UfviD/sRL7/0gevgr/ggr/ybL4v/AOxpm/8ASS1r+leGv+RC/kfh2Y/8jWP/AG9+Z+5lv1P1q3VS36n61brmw/wI7JELIqy/aG7LimLcpNC8kJztzVmittVsK5lJMHKwyn52w/4V8m+OP2KP2dPiV8Wbb4zeJ9G8/WrArtl+0XK4kSUzA7UmVPvnONhH4cV9fSR72znpzTHGPmFOhVrQi3LpsXyxaaXU/ml/4LM/sN/tS/tIfEHSdS/Z88Mf2jplh4fgsPM+2WkW2SO6lfGLmaNj8jKc8j3zVj/gh9+x7+05+y9418cw/HfRv7ON3Fpwj/0i0l+79pJ/495ZP769cdfrX9Ku3fHk1TmQKML1Nerjs9q/UFRk/dWtjyv7Pj7VNCmS7ETTKu88bUyB9ea8a+Lvwl0n40/DPWfhdrfGn6okSn73/LORZP4XRvvKP4hXskO4nBq2qhW3V4dKpUmqWIWi6r+v62PRqUItJPofmZ/wTm/YH0/9i/4P3PgvUX+13+rSP9sfBj+RJ5pIuBPMvSTHykH1r8ev+CiX/BB7xB491KT4jfsr3GzWLw5nj2KcbBEi83d6qdA/RR/Kv6tnlEYznmqscpLEt0r0KWdLD1vdnq+v+ZniqXtWpPofkx+yj/wT98R+APgNc/D34/an/bt1qaKksHkra7RFM0g+a3nYHIK9GHT3Nfi98e/+Dfj4leE/i9pfir9mOfz7fzJnc7Yl+zZiUZ/0q+Jk3kuOnH5V/YfKrSAbe3SiN2b5M8nvXPhs2qYWtWjG657fPuY1cDTqTjVe6Pw2+Pf/AARq8GfGv4JaH4S8Sat5/iLQ/Pb7b5Ei+abiRDjy1u0jXYi7epz14NfGH/BOH/gj3+0h+zp8TLi+8f6t5WgKylU8i2O4bZs8x3ckg+Z1/wD1Zr+pcq8Iye9PZXmUbelW85rPDvC2foEcvpqq6vc/ET9u/wD4I3/Bn9qHSo/EPhCH+zNetwd8m6ebz8+Uqja93GibVQ9uc+tfG3/BNX/gil41+CXi678QfHeczafEV8i22ovmZ84N89veOwxuU8j29a/p7UurEA4q7sbdxW+FzurXoeyenL0M6WU4enzWW5/MF/wUV/4IOaN8abm78bfs3Q/YtUvVRbm23NL54iESIN91eoqbQrngDPQ9q+kP2Z/+CRl7P+zhJ8PP2kvEf9tXN6CixfZPs32by52f71rdYfcAn8Qxj3Nfu4Qxlz3rRCHcAawwmd18VJ3uuX0f/DEUMlw1C7itz+M7wt/wQU/an+Gn7U1l4i8C695XhqzdmXVfsto2A9uwI8iS+aTh2KdOevSv1v8A2z/+CNPwn/al8K2Vve6j9l1nT1b9/wCVM/ml/KH3RdxouFT3zmv3BjY9TTwM8V69PPsTOSrqWvyLo5bSpRlGPU/kz/YT/wCCL37VPwb+INhbfEDXv7J8HabvmjtPstpPl/PSQr5kd483zjecnIXP0r0b9v8A/wCCFE/xX8Uy/GD4H6z5OtTbtyfZ93zu8spObi9RPvFeNv6Zr+oaSMuu0DFNA2jb6VVLPMXCu6t+nkEcvpxpOmj8Y/8Agnp/wT5+JnwU+AF74C+PWsf2tNrFm9m8H2eKDy4p7eONvmt53DbSG6EE569DX5W/tY/8EEfiDp/xk0vxt+zBdeRa3mrwXN3Lsjfyg87s5xd32W2qEPygZ7c5r+u5ieKzN8i3Z9M1wVOIK2Bmqqb992fzOunhlGKjE+Rv2X/2c9e+EPwLj+F3xM1X+23ngWKY+Qtty0KxMP3Uj+h6N3r8Bf23/wDggjqnj79pLTPif8Fx5ei6nqkN5qyZB8vzbl3m5nvlc4jI+4oz2Ga/rCk+bK5xVe3ciTYelKnxBXwmJXaf9amnsre8jwr9n74SeGvgP8H/AA38LNKX/kEafbQZ+b5jBGse7lnx93puNfmV/wAFNf8AglD4Z/bgQ+ONHuPL8Q2luy242OcupmkX711DGPnkHUfXiv2xli3P7VOEGziuTD4jErGTxE9F0JqU1yWR/Nn/AMEm/wDglt8fv2X/ABfqmt/GDVcWUU0sVvaeRb88wFX8yG5kPRGGCPc+/wDRVJbQgJbQN88OF6dlrallEa7V61SjXe+G715ed5gsTXSjrLqzWlTahZngvx8/Z/8Ahn+0X4Fn8D/FSz+0W0+UQ+ZKm2RkdFb9zJGTje3GcV/K5+1B/wAG83xT8K+Nr3xh+yvF/akV1JJILfMMO0O7tjfd35zgBBnA6/Wv7MggC7VNBLKuG7V9Nl+cYzCNKEnY82pl9GabktT/ADy9S/4Itf8ABR1teC3Xgwi4f5dv9oaZ0LdeLzHXtX1r8C/+Dd79o3xvrlneftBN/Y1lHJHI8OLa4yoZSRutr8HoWH4fSv7ed3OalR+9fRYvjXH1afI5WOenltC+x8cfsmfsafDH9kfwPZ+Ffh7Y7Z4LdIZbnzZT5hVEUnZLLKBnYDgHivrQ2+w5kbLt7etaWGBLMeDUZlAODXwuPq08Y+bFt3+f3nq4ejCjHlgtCv8AJCP3/APFfmH/AMFHP+Canw3/AG9fh/cadqjfZNXZQIbjEr87oc/ILiBPuxY5P69f1BlO8DbUCeYr59KywWMqYSoo4ZXj3W5pVpKpGzP54P8Agkx/wSW8V/sc/E/XPiN8TE8y9Rbcae2UXlROj8RXUw+7IPvL9Oa/oVmtoL0TabdNuLBdwxj3HStpTn79VJW3PuXiu/M80qS5alTV/oYYXDRpXSP5Xf8Agoh/wQU1z4l+KD8VP2bJfN1qQlpLbao34WNB891fKgwA54X29K/V39i79iLxH8EfgJe+DfjZrX/CTXF8iK8X2dbPytkzuBmCZw2Qy9COnvX6mq/yEt1rGlmbzNxNc+Y597OhBP3rbeXr/kaKl+8cu5/Hh+0B/wAG9Xxe8O/Ge31T9m3V/P03V5GNxdfZ4V+wbIwQdl1flpvMYsuBjb16Yr+oL9l34H678DfhfY+GPGGp/wBp3sIYNJ5Kw9XYjhHcdGx1r6ft5jIvPWmXA3dK2zHPamJwsK0NeVWX/B/qw8OnScoLRN3PDvj/APs6/DH9pf4fXnw9+Jll9s0++VVlTzJY8hXRxzFJG3VB0Ir+U342f8G/3x0ufj7bah8F7v7D4ViORL5cEu3MIB4nvvNPz5H/ANav7HYFZGFX2jBwa2yPiDFLDuN7dzHE0W5Jpnxh8Bf2SvD3wY+CD/CHW77+0obpAJpPLaHOJTL0WVyME9m/wr+er9r3/g378aXnxCj8d/sx6h5czszMPKQ7fkRet1fYOfn7f0r+t8f6zaDVnAxnNb5fm9ai6jpO13YyxGDjXUefdH4Yaj/wSJ0b4v8A7N9v8M/jtqvmeISrrFdeQw8k+aHPyW90sbZRQOW469a/OP8AY9/4IiftLfBn446lqep+J/7O8PxeTs/0K1l80bJAel48i4Zh1659K/rpxzmmSAyD5eBWFXMa6pSoJ/F001Mp5VRlVjUfQ/G7/goZ/wAEsfDH7ZXh/Tr2LUfs+veH1Y2jeS77mmESNx9phQfJH/Fn2wevw3/wTI/4JGftDfs8+Jrq7+MPiTOjwsDBafY7Yb8+du+eC6kccup5+nrX9OqIVBU8jtTZAiqBjArWebVlgvYz2XQzjlMI1nUh1P54f29/+CKngv8AaKsx45+Fd/8AZNd0+za1hi8qSTzCPNkHzTXkaDLso5Bx9M11f/BJ7/gmz8cf2Ukm1b4z6t5oG4W9r5EC7VxCV+eC4lzgow5FfvsMMMt2pJT8lXVzas8F7JfDudFDBezqOaZ4x8cfhTpnxx+HOp/DjV5fs9vqUEtvI+0t8ssbxnhWQ9HPRhX8q/jH/ggL8cvBH7Qtt4p+A+ueXozXSyyS/ZoG2p5+4/LcXxc4QKeBn8a/sHiiI+apU8vOB+VcWWZvUp0nGpo5d+o6mESq+0irnhP7Nnwj1b4MfCrTPCHiLUP7RvYIIlnm8oQ5kWNVb5Vdx1XPBxXtzJi7BXoV4/OtEjGKRSQeKxxGHjVSv0O1TZwfi7wVpPjnwjrHgrxXH51hrEM9pKmSuYrhDGwyhVhlWPIIPuK/ju/bf/4IM/HeL4sXXjD9lrSPP0SR5HWLz7ddoMjsBuu77ecJt5x+ua/tOk4UviqP2rBIxXqZfntTLF7OlKyZw18JCq7zR/BF8Kf+CEX7avjjxhbx+PNE/s7TXuEjupvtFjLsiLLvbbHehjhSTheeOOa/sS/Yr/ZR8Kfsb/Byy+F+gv5zwiJZJcOu91iSMna0suM7BwGx/M/Z6ThugxT2LONueKvMc+xGKg9bmGGy2hTnzJGeLhmO0DHz5NbUZ3IDVAWy53d60FAVQBXzWEWI5pSr9dj1puNrRHUUUV3GZFN/qzX5Vf8ABYr/AJMi1z6yf+ktxX6qzf6s1+VX/BYr/kyLXPrJ/wCktxW+A/3pCq/w2eLf8ECv+Ue/h7/r41H/ANLbivm74ygf8LSvj7p/6AK+kf8AggV/yj38Pf8AXxqP/pbcV83fGUj/AIWlfD3T/wBAFfmfjP8Axqfz/Q+y8Pfjn6f5ncfspWa6h+0l4bs2baHllBI/64SV+9H/AAhFh/z2k/T/AAr8JP2Qv+TnvDH/AF2m/wDRElf0H1y+GODo1curSqRu/aP/ANJibccyaxtO38i/9Kkf/9f+mD/go5/yWHRf+wOn/o+Wvz2s/wDWD/r5FfoT/wAFHP8AksOi/wDYHT/0fLX59WYwwz/z9Cv5c4wr8vEWIjb7X6I/YOGqyll0aXkft34u5/4J++IMf9CLff8ApA9fBX/BBT/k2Xxf/wBjTN/6SW1fd/jIf8a+/EAO/nwNe/c6/wDHg9fBv/BBeNIP2ZfFom8yMt4pmIEnBx9ktq/p3h68ckjC26TPxzMaMVj3Vctm1+J+50BAzn1q1vX1FZgjAJZXyGOeTTtp/vj868+m8RFcvsmauUO5o719RRvX1FZ20/3x+dG0/wB8fnV+0xH/AD6Yrw7l5uuVqu55qL5hwHH50mD/AHx+db/WK1rexY1KC6lnzAE4xTAoYZPNVzHn+MfnTgCOjj865060m/aUW0JuN7qRZSNY/b60jnauarsGbGZP1pCpYYLj86c6mI5JU4Uml0K549ZFAzM7EHmtKGJQuTzmoBbp1DCpQD2cfnXDgsNXpz5qtNsmUov7RZjznBFVoGHmmlG4dHHPvUaxAHIcfnXZifbznTlCk/dCDgk05Fm4PyjdToHJTAqsyF8BnH50IpX7rj86lfWFiHV9k7WHzQtbmHzKFclanWQHmqhj3HJcfnShcdGHPvVUJYinKUvZPUmTi7WkP2Dzc54qcyAGqoTnO8c+9G3P8Q/OnQlWp81qO4ScXa0iaDuWpJzKrHaQKiVSBgOPzoZS3Vx+dRJV3RVNU2mUpQvuIGnP8VSR+aT8x796jEeOjj86UIQc7x+dclPD4qLT5ZfeU5w7lxyOp4xUIgVpBKahKkjBcfnThuAx5g/OvQqOpUaU6LaW3qTzRtpIkd08winRxIGD1VMWTnePzp4DAY3j86hSxEpc1Sje2w3ONtJFhpFDkU0y9lNVjHk5Lj86PKH98fnROrjJJp09B88P5hxUueSKekYRskimbMdGH50bc9WH51zww84y5/Yu4e0j3LJkUc5p4dXHXNUjGCMbh+dKEx0YfnXcsRib60tCXKm1uWdi08FUFVcN/fH50hBPVx+davEVbaUmQlTXUmeRW+UHmq0ykAMDSiMA5DD86Gj3dXH51w4mNarHWk+Y054dxbfk461Y2jdntVZY9nKuPzpxDd3H51phXWpQadJ3FzxX2gN0ok2ijIZ8iovIXOdwqTZj+IfnXPGOKlFxq0763E5R5rqRaCADaOazpbTMnFWfmH8Y/Okw398fnW86SqRUalB6ClJPaRIkYhQnvUaOr43UMGIwzj86j8kDo4/OsqlOskoUqTUexXNC93InUIBk1L5q4xVbZjjcPzppiH98fnWjliYw5adKxXPB7sWGQNLirLSYcLVURBTuDDP1pSnzbi4/Os6X1uNNRdN3vf5CUoLqXM4601WBXIqA5IwXH50igp0cfnXa61Z1FL2TtYOeHctEgdailljB28GoiCTkuPzqMwr3YfnWGIq4qd4xpOwc8P5i2kiPwOKczxqOcVTEQU8MPzpxQHqw/OnCtilHllSJco9JFs4Knb+lVIh8/J70uGxgOB+NNEeDnePzrDEwr1ZwkqTViozilrIvZJpeFXc1U/m/vj86QhiMFx+dd0q9a3u0ncjmj3FkuNzbB06Uht065qPyV/vD86ftP98fnXm0qeK5pSq079ipOm+pIkSL3FTZUelVPLP98fnQEI/jH510U6mKhoqWhLVPuWgfm5IqyHX1FZu3/aH50u0/3x+dbSr4l/8ALoUeRfaNHevqKN6+orO2n++Pzo2n++PzqPaYj/n0yrw7lyZlKEAivyt/4LEgn9iPXMesn/pLcV+opwvzOwIHPBr8uP8AgsTf21p+xNq8soLB5mUKACTm1uO2a7cA6qq+1nC3kTUlBwaueL/8EC+P+Ce/h7P/AD8aj/6Wz183fGRf+Lp37e6f+givo3/ggxeib9g3Ro44JYlWe+wGXaObyfpXzn8Y/wDkqF8D1yn/AKCK/LvGLEucqdTltv8A5fofZ8AWVSaT6HZ/ssB2/aN8OrGGZjLKAF4J/cSV+832e7/59J/++/8A69fhR+yRKkH7TXhiWQ4Hmzf+iJK/oP3rWPhbRjXy2tNu37x6afyx7j4zr82NhptH9Wf/0P6Yv+CjWP8AhcGi/wDYHT/0fLX572n3h/19Cv0B/wCCksr/APC2dDhjHzHSoyWHXHnTcZ9K+B77ULa0ewkt41PlSRPKAB820859c981/LnFkP8AjIsTN/zfoj9h4doN5dTt1R+58/hvV/G/7FVx4L8OFPt+qeE5bWFWLYLzWbIvCgt95h0BP1r+en4IfAr/AIK2/sr6PrXgv4TWWjTWGpXst6huI9ZbBdVjH+pSJc4Qdj9a/Wzwd+3X4e8G+FdM0FNPkma1tYoj5UKso2qBt/14x06YFdJ/w8Z0H/oDz/8AgOv/AMkV+2ZH4h4ChgY4epKzSXQ/MM34QxtfEuaT3Z+dcPif/guWtpDGdN8LllRQcRa/n8eetO/4Sj/guX/0C/DH/frX/wDGv0WT/go3oacDSZ//AAHX/wCSKf8A8PHdF/6BM/8A4Dr/APJFdsfEvKILldVX/wAJEeF8ZBKLgfnN/wAJR/wXL/6Bfhj/AL9a/wD40f8ACUf8Fy/+gX4Y/wC/Wv8A+Nfoz/w8d0X/AKBM/wD4Dr/8kUf8PHdF/wCgTP8A+A6//JFV/wAROyf/AJ+r/wABK/1axn8h+c3/AAlH/Bcv/oF+GP8Av1r/APjR/wAJR/wXL/6Bfhj/AL9a/wD41+jP/Dx3Rf8AoEz/APgOv/yRR/w8d0X/AKBM/wD4Dr/8kUf8ROyf/n6v/AQ/1axn8h+c3/CUf8Fy/wDoF+GP+/Wv/wCNH/CUf8Fy/wDoF+GP+/Wv/wCNfoz/AMPHdF/6BM//AIDr/wDJFH/Dx3Rf+gTP/wCA6/8AyRR/xE7KP+fq/wDAQ/1axn8h+c3/AAlH/Bcv/oF+GP8Av1r/APjR/wAJR/wXL/6Bfhj/AL9a/wD41+jP/Dx3Rf8AoEz/APgOv/yRR/w8d0X/AKBM/wD4Dr/8kUf8ROyj/n6v/AQ/1axn8h+c3/CUf8Fy/wDoF+GP+/Wv/wCNH/CUf8Fy/wDoF+GP+/Wv/wCNfoz/AMPHdF/6BM//AIDr/wDJFH/Dx3Rf+gTP/wCA6/8AyRR/xE7KP+fq/wDAQ/1axn8h+c3/AAlH/Bcv/oF+GP8Av1r/APjR/wAJR/wXL/6Bfhj/AL9a/wD41+jP/Dx3Rf8AoEz/APgOv/yRR/w8d0X/AKBM/wD4Dr/8kUf8ROyj/n6v/AQ/1axn8h+c3/CUf8Fy/wDoF+GP+/Wv/wCNH/CUf8Fy/wDoF+GP+/Wv/wCNfoz/AMPHdF/6BM//AIDr/wDJFH/Dx3Rf+gTP/wCA6/8AyRR/xE7KP+fq/wDAQ/1axn8h+c3/AAlH/Bcv/oF+GP8Av1r/APjR/wAJR/wXL/6Bfhj/AL9a/wD41+jP/Dx3Rf8AoEz/APgOv/yRR/w8d0X/AKBM/wD4Dr/8kUf8ROyj/n6v/AQ/1axn8h+c3/CUf8Fy/wDoF+GP+/Wv/wCNH/CUf8Fy/wDoF+GP+/Wv/wCNfoz/AMPHdF/6BM//AIDr/wDJFH/Dx3Rf+gTP/wCA6/8AyRR/xE7KP+fq/wDAQ/1axn8h+c3/AAlH/Bcv/oF+GP8Av1r/APjR/wAJR/wXL/6Bfhj/AL9a/wD41+jP/Dx3Rf8AoEz/APgOv/yRR/w8d0X/AKBM/wD4Dr/8kUf8ROyj/n6v/AQ/1axn8h+c3/CUf8Fy/wDoF+GP+/Wv/wCNH/CUf8Fy/wDoF+GP+/Wv/wCNfoz/AMPHdF/6BM//AIDr/wDJFH/Dx3Rf+gTP/wCA6/8AyRR/xE7J/wDn6v8AwEP9WsZ/IfnN/wAJR/wXL/6Bfhj/AL9a/wD40f8ACUf8Fy/+gX4Y/wC/Wv8A+Nfoz/w8d0X/AKBM/wD4Dr/8kUf8PHdF/wCgTP8A+A6//JFH/ETsn/5+r/wEP9WsZ/IfnN/wlH/Bcv8A6Bfhj/v1r/8AjR/wlH/Bcv8A6Bfhj/v1r/8AjX6M/wDDx3Rf+gTP/wCA6/8AyRR/w8d0X/oEz/8AgOv/AMkUf8ROyf8A5+r/AMBD/VrGfyH5zf8ACUf8Fy/+gX4Y/wC/Wv8A+NH/AAlH/Bcv/oF+GP8Av1r/APjX6M/8PHdF/wCgTP8A+A6//JFH/Dx3Rf8AoEz/APgOv/yRR/xE7J/+fq/8BD/VrGfyH5zf8JR/wXL/AOgX4Y/79a//AI0f8JR/wXL/AOgX4Y/79a//AI1+i7f8FHtHA+XSJ/8AwHH/AMkUz/h5BpX/AECJv/Acf/JFH/ETsn/5+r/wEP8AVrGfyH51/wDCUf8ABcv/AKBfhj/v1r/+NH/CUf8ABcv/AKBfhj/v1r/+Nfosv/BR/SCfm0if/wABx/8AJFP/AOHjui/9Amf/AMB1/wDkij/iJ2T/APP1f+Ah/q1jP5D85v8AhKP+C5f/AEC/DH/frX/8aP8AhKP+C5f/AEC/DH/frX/8a/Rn/h47ov8A0CZ//Adf/kij/h47ov8A0CZ//Adf/kij/iJ2T/8AP1f+Ah/q1jP5D85v+Eo/4Ll/9Avwx/361/8Axo/4Sj/guX/0C/DH/frX/wDGv0Z/4eO6L/0CZ/8AwHX/AOSKP+Hjui/9Amf/AMB1/wDkij/iJ2T/APP1f+Ah/q1jP5D85f8AhKP+C5f/AEC/DH/frX/8aX/hKP8AguX/ANAvwx/361//ABr9Gf8Ah47ov/QJn/8AAdf/AJIo/wCHjui/9Amf/wAB1/8Akij/AIidlH/P1f8AgIf6tYz+Q/Ob/hKP+C5f/QL8Mf8AfrX/APGj/hKP+C5f/QL8Mf8AfrX/APGv0Z/4eO6L/wBAmf8A8B1/+SKP+Hjui/8AQJn/APAdf/kij/iJ2Uf8/V/4CH+rWM/kPzm/4Sj/AILl/wDQL8Mf9+tf/wAaP+Eo/wCC5f8A0C/DH/frX/8AGv0Z/wCHjui/9Amf/wAB1/8Akij/AIeO6L/0CZ//AAHX/wCSKP8AiJ2Uf8/V/wCAh/q1jP5D85v+Eo/4Ll/9Avwx/wB+tf8A8aP+Eo/4Ll/9Avwx/wB+tf8A8a/Rn/h47ov/AECZ/wDwHX/5Io/4eO6L/wBAmf8A8B1/+SKP+InZR/z9X/gIf6tYz+Q/Ob/hKP8AguX/ANAvwx/361//ABo/4Sj/AILl/wDQL8Mf9+tf/wAa/Rn/AIeO6L/0CZ//AAHX/wCSKP8Ah47ov/QJn/8AAdf/AJIo/wCInZR/z9X/AICH+rWM/kPzm/4Sj/guX/0C/DH/AH61/wDxo/4Sj/guX/0C/DH/AH61/wDxr9Gf+Hjui/8AQJn/APAdf/kij/h47ov/AECZ/wDwHX/5Io/4idlH/P1f+Ah/q1jP5D85v+Eo/wCC5f8A0C/DH/frX/8AGj/hKP8AguX/ANAvwx/361//ABr9Gf8Ah47ov/QJn/8AAdf/AJIo/wCHjui/9Amf/wAB1/8Akij/AIidlH/P1f8AgIf6tYz+Q/Ob/hKP+C5f/QL8Mf8AfrX/APGj/hKP+C5f/QL8Mf8AfrX/APGv0Z/4eO6L/wBAmf8A8B1/+SKP+Hjui/8AQJn/APAdf/kij/iJ2Uf8/V/4CH+rWM/kPzm/4Sn/AILmf9Avwx/361//ABo/4Sj/AILl/wDQL8Mf9+tf/wAa/Rn/AIePaJ/0CZ//AAHX/wCSKP8Ah47ov/QJn/8AAdf/AJIo/wCInZP/AM/V/wCAh/q1jP5D85v+Eo/4Ll/9Avwx/wB+tf8A8aP+Eo/4Ll/9Avwx/wB+tf8A8a/Rn/h47ov/AECZ/wDwHX/5Io/4eO6L/wBAmf8A8B1/+SKP+InZP/z9X/gIf6tYz+Q/Ob/hKP8AguX/ANAvwx/361//ABo/4Sj/AILl/wDQL8Mf9+tf/wAa/Rn/AIeO6L/0CZ//AAHX/wCSKP8Ah47ov/QJn/8AAdf/AJIo/wCInZP/AM/V/wCAh/q1jP5D85v+Eo/4Ll/9Avwx/wB+tf8A8aP+Eo/4Ll/9Avwx/wB+tf8A8a/Rn/h47ov/AECZ/wDwHX/5Io/4eO6L/wBAmf8A8B1/+SKP+InZP/z9X/gIf6tYz+Q/Ob/hKP8AguX/ANAvwx/361//ABo/4Sj/AILl/wDQL8Mf9+tf/wAa/Rk/8FHtFAONJn/8Bx/8kVF/w8g0r/oETf8AgOP/AJIo/wCInZP/AM/V/wCAh/q1jP5D86/+Eo/4Ll/9Avwx/wB+tf8A8aP+Eo/4Ll/9Avwx/wB+tf8A8a/RQf8ABSDSc86RP/4Dj/5IqX/h47ov/QJn/wDAdf8A5Io/4idk/wDz9X/gIf6tYz+Q/Ob/AISj/guX/wBAvwx/361//Gj/AISj/guX/wBAvwx/361//Gv0Z/4eO6L/ANAmf/wHX/5Io/4eO6L/ANAmf/wHX/5Io/4idk//AD9X/gIf6tYz+Q/OU+Kv+C5aDedK8MHHOPJ1/wDxrxz9oj4Yf8FjP2ovhj/wr3x7pvh22sRMkkgtotbQsFR1PEiyKeHPVa/Xxv8Ago5orDH9kz/9+F/+SKhH/BRfQgpUaTcYPUeQv/yRSXiZlTfuzv8AItcI42rF2gL/AMEoP2e/ih+zZ+yZpfw7+KaW0N/bzXbFYBKuPMuZZBkSxxt91h/DX57fGYj/AIWnfMPVf/QBX6BH/goloWzy10q5VfQQKB/6Pr8zvGutDxh4ye/gOwyHJPQkbe/LdMV+XeIPFOGzOMY0dbXPuuCuHK+CqSlXVlY9s/ZMiS4/aW8MxSg7TNLnBwf9RJX9AH2yP/nkf++h/jX4AfsiMumftQeHEnbeGllwSc4P2eTpX9A/25v+eEn5L/jX0HhRFLK62uvtH0v9mJ5nHsFHHwUf5F+cj//R/pr/AOCjghHxP0iRvv8A9koB/wB/pa/Oe3t1izNfco3TPv8AWv6cvEvw4+HnjO7S/wDGGg6dqs8SeWkl5axzuqAk7QXUkDJJx71zr/An4IyJsk8HaGy+h0+Aj/0CvynOfDzEY3H1sZGtFKbvazutEfd5XxdSwmFp0HSbcVa90fzcoUh5sPJZG5Pmdfwx2pfNu/vbbX8jX9Ia/Ab4HKNq+DdDA/7B8H/xFL/woj4If9Cbof8A4L4P/jdeC/CbGufN9ajb0Z6H+vVDf2L+9H83fnXI5KW34A0faLn+5bfka/pE/wCFEfA//oTdD/8ABfB/8bo/4UR8D/8AoTdD/wDBfB/8bpy8JsU3f6xD7maLjvCdcO/vR/N39ouf7lt+Ro+0XP8ActvyNf0if8KI+B//AEJuh/8Agvg/+N0f8KI+B/8A0Juh/wDgvg/+N1P/ABCTFf8AQRD/AMBYf69YP/oHf3o/m7+0XP8ActvyNH2i5/uW35Gv6RP+FEfA/wD6E3Q//BfB/wDG6P8AhRHwP/6E3Q//AAXwf/G6P+ISYr/oIh/4Cw/16wf/AEDv70fzd/abn+5bfkaPtFz/AHLb8jX9In/CiPgf/wBCbof/AIL4P/jdH/CiPgf/ANCbof8A4L4P/jdH/EJMV/0EQ/8AAWH+vWD/AOgd/ej+bv7Rc/3Lb8jR9ouf7lt+Rr+kT/hRHwP/AOhN0P8A8F8H/wAbo/4UR8D/APoTdD/8F8H/AMbo/wCISYr/AKCIf+AsP9esH/0Dv70fzd/aLn+5bfkaPtFz/ctvyNf0if8ACiPgf/0Juh/+C+D/AON0f8KI+B//AEJuh/8Agvg/+N0f8QkxX/QRD/wFh/r1g/8AoHf3o/m7+0XP9y2/I0faLn+5bfka/pE/4UR8D/8AoTdD/wDBfB/8bo/4UR8D/wDoTdD/APBfB/8AG6P+ISYr/oIh/wCAsP8AXrB/9A7+9H83f2i5/uW35Gj7Rc/3Lb8jX9In/CiPgf8A9Cbof/gvg/8AjdH/AAoj4H/9Cbof/gvg/wDjdH/EJMV/0EQ/8BYf69YP/oHf3o/m7+0XP9y2/I0faLn+5bfka/pE/wCFEfA//oTdD/8ABfB/8bo/4UR8D/8AoTdD/wDBfB/8bo/4hJiv+giH/gLD/XrB/wDQO/vR/N39ouf7lt+Ro+0XP9y2/I1/SJ/woj4H/wDQm6H/AOC+D/43R/woj4H/APQm6H/4L4P/AI3R/wAQkxX/AEEQ/wDAWH+vWD/6B396P5u/tFz/AHLb8jR9ouf7lt+Rr+kT/hRHwP8A+hN0P/wXwf8Axuj/AIUR8D/+hN0P/wAF8H/xuj/iEmK/6CIf+AsP9esH/wBA7+9H83f2i5/uW35Gj7Rc/wBy2/I1/SJ/woj4H/8AQm6H/wCC+D/43R/woj4H/wDQm6H/AOC+D/43R/xCTFf9BEP/AAFh/r1g/wDoHf3o/m7+0XP9y2/I0faLn+5bfka/pE/4UR8D/wDoTdD/APBfB/8AG6P+FEfA/wD6E3Q//BfB/wDG6P8AiEmK/wCgiH/gLD/XrB/9A7+9H83f2i5/uW35Gj7Rc/3Lb8jX9In/AAoj4H/9Cbof/gvg/wDjdH/CiPgf/wBCbof/AIL4P/jdH/EJMV/0EQ/8BYf69YP/AKB396P5u/tFz/ctvyNH2i5/uW35Gv6RP+FEfA//AKE3Q/8AwXwf/G6P+FEfA/8A6E3Q/wDwXwf/ABuj/iEmK/6CIf8AgLD/AF6wf/QO/vR/N9HNcs2Nlt+Rqffc/wDPO2/I1/R2PgT8EB08HaGP+4fB/wDEUv8Awor4Jf8AQn6J/wCC+D/4ij/iEmK/6CIf+AsP9esH/wBA7+9H838ktyq58u26+hqH7Rc/3Lb8jX9Ip+BPwRPXwdoZ/wC4fB/8RSf8KI+B/wD0Juh/+C+D/wCN0f8AEJMV/wBBEP8AwFh/r1g/+gd/ej+bv7Rc/wBy2/I0faLn+5bfka/pE/4UR8D/APoTdD/8F8H/AMbo/wCFEfA//oTdD/8ABfB/8bo/4hJiv+giH/gLD/XrB/8AQO/vR/N39ouf7lt+Ro+0XP8ActvyNf0if8KI+B//AEJuh/8Agvg/+N0f8KI+B/8A0Juh/wDgvg/+N0f8QkxX/QRD/wABYf69YP8A6B396P5uvtNz/ctvbg0v2i57Jbfka/pE/wCFEfA//oTdD/8ABfB/8bo/4UR8D/8AoTdD/wDBfB/8bo/4hJiv+giH/gLD/XrB/wDQO/vR/N39oueyW35Gj7Rc9ktvyNf0if8ACiPgf/0Juh/+C+D/AON0f8KI+B//AEJuh/8Agvg/+N0f8QkxX/QRD/wFh/r1g/8AoHf3o/m7+0XPZLb8jR9oueyW35Gv6RP+FEfA/wD6E3Q//BfB/wDG6P8AhRHwP/6E3Q//AAXwf/G6P+ISYr/oIh/4Cw/16wf/AEDv70fzd/aLnslt+Ro+0XPZLb8jX9In/CiPgf8A9Cbof/gvg/8AjdH/AAoj4H/9Cbof/gvg/wDjdH/EJMV/0EQ/8BYf69YP/oHf3o/m7+0XPZLb8jR9oueyW35Gv6RP+FEfA/8A6E3Q/wDwXwf/ABuj/hRHwP8A+hN0P/wXwf8Axuj/AIhJiv8AoIh/4Cw/16wf/QO/vR/N39oueyW35Gj7Rc9ktvyNf0if8KI+B/8A0Juh/wDgvg/+N0f8KI+B/wD0Juh/+C+D/wCN0f8AEJMV/wBBEP8AwFh/r1g/+gd/ej+bv7Rc9ktvyNH2i57Jbfka/pE/4UR8D/8AoTdD/wDBfB/8bo/4UR8D/wDoTdD/APBfB/8AG6P+ISYr/oIh/wCAsP8AXrB/9A7+9H83f2i57JbfkaPtFz2S2/I1/SJ/woj4H/8AQm6H/wCC+D/43R/woj4H/wDQm6H/AOC+D/43R/xCTFf9BEP/AAFh/r1g/wDoHf3o/m7+0XP9y2/I0faLn+5bfka/pE/4UR8D/wDoTdD/APBfB/8AG6P+FEfA/wD6E3Q//BfB/wDG6P8AiEmK/wCgiH/gLD/XrB/9A7+9H83f2i5/uW35Gj7Rc/3Lb8jX9In/AAoj4H/9Cbof/gvg/wDjdH/CiPgf/wBCbof/AIL4P/jdH/EJMV/0EQ/8BYf69YP/AKB396P5u/tFz/ctvyNH2i5/uW35Gv6RP+FEfA//AKE3Q/8AwXwf/G6P+FEfA/8A6E3Q/wDwXwf/ABuj/iEmK/6CIf8AgLD/AF6wf/QO/vR/N39ouf7lt+Ro+0XP9y2/I1/SJ/woj4H/APQm6H/4L4P/AI3XC+L/AIN/Bq1aO2tfCGix55JWwgB/9ArnxXhbiaFN1JYiOnkyo8c4Nu31d/ej+fUXFySBstufY1a33P8AzztvyNfuV/wp/wCEv/Qr6R/4BQ//ABFO/wCFQ/Cf/oWNJ/8AAKH/AOIryP8AUOv/AM/Y/cy/9dcJ/wA+H96PwxaS5AJMdt09DVf7Rc/3Lb8jX9Cfhr4G/BrUdPZbvwpo7c8H7DDu/PbUmsfB74M2VxBHb+DtD27irA6fAc4/4BXpLwxxHsVWlXik/JkrjfB3t7B/ej+ej7Rc/wBy2/I0faLn+5bfka/ee8+Enwgur848JaNGmcbUsYQMf98Vqt8Kvg3BC8C+DdEP7oEE2EBYE987K5Y+HtVuX7+Nl/dZX+uuE/6B396PwCNxdHgJbc+xpfNuxxttfyNfuu3wh+Ez9fC2kfhYwj/2Smf8Ke+Ev/Qr6R/4BQ//ABFcsuA8Rf3a0fuYpcb4f7FFr5o/CwS3Z/htfyNZwt3e4+02h59vy7V/Q1onwG+DQmWW48K6RLlNwDWMOM/98c10918EPgnGsUCeDtFQTHBKWECkfTCV61Dwvxk6ftJ4iK+TMnx1TTsqT+9H4r/sfW8x/aZ8LXFx97zpv/RElf0I14kfhZ8MfAl1H4h8NeHtOtb+LIinjtYkeMkYJVlQEEgkZB6Gpv8AhINY/wCe7fnX3/DGCfD+Gng8RLmlKXN7uyukuvofJZ9mazKvGtFWSVtfVv8AU//Z" style="height:36px;width:auto;border-radius:4px" alt="EN"> <span style="color:#fff">Escola Naval</span></span>
  <div class="nav-right">
    <span class="nav-user">{{ session.user.nome }} · <strong>{{ session.user.perfil }}</strong></span>
    {% if session.user.perfil == 'aluno' %}
    <a class="nav-link" href="{{ url_for('aluno_perfil') }}">👤 Perfil</a>
    {% endif %}
    <a class="nav-link" href="{{ url_for('logout') }}">Sair</a>
  </div>
</nav>
{% endif %}
{% with msgs = get_flashed_messages(with_categories=true) %}
  {% if msgs %}
    <div class="container" style="margin-top:.8rem;margin-bottom:0">
    {% for cat,msg in msgs %}
      <div class="alert alert-{{ cat }}">{{ msg }}</div>
    {% endfor %}
    </div>
  {% endif %}
{% endwith %}
{{ content|safe }}
{% endautoescape %}
</body></html>
"""

def render(content, status=200):
    html = render_template_string(BASE, content=content)
    return Response(html, status=status, mimetype="text/html")

def esc(v):
    return str(escape(str(v))) if v is not None else ""

def csrf_input():
    t = session.get("_csrf_token") or secrets.token_urlsafe(32)
    session["_csrf_token"] = t
    return Markup(f'<input type="hidden" name="csrf_token" value="{t}">')

def _parse_date(s, default=None):
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except Exception:
        return default or date.today()

def _bar_html(val, cap):
    if cap is None or cap <= 0:
        return f'<div class="occ-label">{val} (sem limite)</div>'
    pct = min(100, int(round(100 * val / cap)))
    color = "#1e8449" if pct < 80 else ("#d68910" if pct < 95 else "#c0392b")
    return (f'<div class="occ-bar"><span style="width:{pct}%;background:{color}"></span></div>'
            f'<div class="occ-label">{val} / {cap} ({pct}%)</div>')

def _prazo_label(d):
    ok, _ = sr.refeicao_editavel(d)
    if ok:
        return ""
    if sr.PRAZO_LIMITE_HORAS is not None:
        prazo_dt = datetime(d.year, d.month, d.day) - timedelta(hours=sr.PRAZO_LIMITE_HORAS)
        h = (prazo_dt - datetime.now()).total_seconds() / 3600
        if h <= 0:
            return '<span class="prazo-lock">🔒 Prazo expirado</span>'
        if h <= 24:
            return f'<span class="prazo-warn">⚠️ Prazo em {int(h)}h</span>'
    return '<span class="prazo-lock">🔒 Prazo expirado</span>'

NOMES_DIAS = ['Segunda','Terça','Quarta','Quinta','Sexta','Sábado','Domingo']
ABREV_DIAS = ['Seg','Ter','Qua','Qui','Sex','Sáb','Dom']

def _back_btn(href, label="Voltar"):
    return f'<a class="back-btn" href="{href}">← {label}</a>'

# ═══════════════════════════════════════════════════════════════════════════
# AUTH / DECORADORES
# ═══════════════════════════════════════════════════════════════════════════

def login_required(f):
    @wraps(f)
    def d(*a, **kw):
        if 'user' not in session:
            return redirect(url_for('login'))
        if session.get('must_change_password') and f.__name__ != 'aluno_password':
            flash("Deves alterar a tua password antes de continuar.", "warn")
            return redirect(url_for('aluno_password'))
        return f(*a, **kw)
    return d

def role_required(*roles):
    def decorator(f):
        @wraps(f)
        def d(*a, **kw):
            if 'user' not in session:
                return redirect(url_for('login'))
            if session['user']['perfil'] not in roles:
                flash('Acesso não autorizado.', 'error')
                return redirect(url_for('dashboard'))
            return f(*a, **kw)
        return d
    return decorator

def current_user():
    return session.get('user', {})

@app.before_request
def before():
    g._t0 = time.perf_counter()
    if request.method == "POST":
        t = session.get("_csrf_token", "")
        ft = request.form.get("csrf_token", "")
        if not t or not ft or not secrets.compare_digest(t, ft):
            abort(400)

@app.after_request
def after(r):
    r.headers.setdefault("X-Content-Type-Options", "nosniff")
    r.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    return r

@app.errorhandler(400)
def err400(e):
    return render("<div class='container'><div class='page-header'><div class='page-title'>⚠️ Pedido inválido</div></div>"
                  "<div class='card'><p>Sessão expirada ou erro de validação.</p><br>"
                  "<a class='btn btn-primary' href='/'>Início</a></div></div>", 400)

@app.errorhandler(404)
def err404(e):
    return render("<div class='container'><div class='page-header'><div class='page-title'>🔎 Não encontrado</div></div>"
                  "<div class='card'><p>Página não encontrada.</p><br>"
                  "<a class='btn btn-primary' href='/'>Início</a></div></div>", 404)

@app.errorhandler(500)
def err500(e):
    app.logger.exception("Erro 500")
    return render("<div class='container'><div class='page-header'><div class='page-title'>💥 Erro interno</div></div>"
                  "<div class='card'><p>Erro inesperado. Consulta <code>logs/app.log</code>.</p><br>"
                  "<a class='btn btn-primary' href='/'>Início</a></div></div>", 500)

# ═══════════════════════════════════════════════════════════════════════════
# AUTH
# ═══════════════════════════════════════════════════════════════════════════

@app.route('/', methods=['GET', 'POST'])
def login():
    if 'user' in session:
        return redirect(url_for('dashboard'))
    error = None
    if request.method == 'POST':
        nii = request.form.get('nii','').strip()[:32]
        pw  = request.form.get('pw','').strip()[:256]
        perfis = {**sr.PERFIS_ADMIN, **sr.PERFIS_TESTE}
        u = None
        if nii in perfis:
            if pw == perfis[nii]['senha']:
                p = perfis[nii]
                u = {'id': 0, 'nii': nii, 'ni': '', 'nome': p.get('nome',''),
                     'ano': str(p.get('ano','')), 'perfil': p.get('perfil','aluno')}
            else:
                error = 'Password incorreta.'
        elif not sr.existe_admin() and nii == sr.FALLBACK_ADMIN['nii'] and pw == sr.FALLBACK_ADMIN['pw']:
            u = {'id': 0, 'nii': nii, 'ni': '', 'nome': sr.FALLBACK_ADMIN['nome'], 'ano': '', 'perfil': 'admin'}
        else:
            db_u = sr.user_by_nii(nii)
            if db_u:
                locked = db_u.get('locked_until')
                if locked:
                    try:
                        lock_dt = datetime.fromisoformat(locked)
                        if lock_dt > datetime.now():
                            mins = max(1, int((lock_dt - datetime.now()).total_seconds() / 60))
                            error = f'Conta bloqueada por demasiadas tentativas falhadas. Tenta novamente em {mins} min.'
                            app.logger.warning(f"Login bloqueado: NII={nii} IP={request.remote_addr}")
                            db_u = None
                    except ValueError:
                        pass
                if db_u:
                    ph = db_u.get('Palavra_chave','')
                    ok = (pw == (ph or ''))
                    if ok:
                        u = {'id': db_u['id'], 'nii': db_u['NII'], 'ni': db_u['NI'],
                             'nome': db_u['Nome_completo'], 'ano': str(db_u['ano']),
                             'perfil': db_u['perfil'] or 'aluno'}
                        sr.reg_login(nii, 1)
                        app.logger.info(f"Login OK: NII={nii} perfil={u['perfil']} IP={request.remote_addr}")
                    else:
                        sr.reg_login(nii, 0)
                        falhas = sr.recent_failures(nii, 10)
                        if falhas >= 5:
                            sr.block_user(nii, 15)
                            error = 'Conta bloqueada por 15 minutos após 5 tentativas falhadas.'
                            app.logger.warning(f"Conta bloqueada: NII={nii} IP={request.remote_addr}")
                        else:
                            restam = max(0, 5 - falhas)
                            error = f'Password incorreta. ({restam} tentativa(s) restante(s) antes de bloqueio)'
            else:
                error = 'NII não encontrado.'
        if u:
            session['user'] = u
            # Registo de auditoria só para perfis de sistema (perfis_admin já não passam por reg_login)
            if nii not in {**sr.PERFIS_ADMIN, **sr.PERFIS_TESTE}:
                _audit(nii, "login", f"perfil={u['perfil']} IP={request.remote_addr}")
            # Forçar alteração de password se necessário
            if db_u and db_u.get('must_change_password'):
                session['must_change_password'] = True
                flash("Por segurança, deves alterar a tua password antes de continuar.", "warn")
                return redirect(url_for('aluno_password'))
            return redirect(url_for('dashboard'))

    ANCORA = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 120" width="54" height="65">
      <circle cx="50" cy="18" r="9" fill="none" stroke="#c9a227" stroke-width="3.5"/>
      <circle cx="50" cy="18" r="4" fill="#c9a227"/>
      <line x1="50" y1="27" x2="50" y2="95" stroke="#c9a227" stroke-width="4"/>
      <line x1="20" y1="48" x2="80" y2="48" stroke="#c9a227" stroke-width="4"/>
      <line x1="20" y1="48" x2="20" y2="65" stroke="#c9a227" stroke-width="3.5" stroke-linecap="round"/>
      <line x1="80" y1="48" x2="80" y2="65" stroke="#c9a227" stroke-width="3.5" stroke-linecap="round"/>
      <path d="M20,88 Q10,100 22,104 Q36,108 50,95 Q64,108 78,104 Q90,100 80,88" fill="none" stroke="#c9a227" stroke-width="3.5"/>
    </svg>"""
    content = f"""
    <div class="login-wrap">
      <div class="login-box">
        <div class="login-header" style="flex-direction:column;text-align:center">
          <img src="data:image/png;base64,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" style="height:100px;width:auto;margin-bottom:.5rem" alt="Escola Naval">
          <div class="login-title">Escola Naval</div>
        </div>
        {'<div class="alert alert-error">'+esc(error)+'</div>' if error else ''}
        <form method="post">
          {csrf_input()}
          <div class="form-group">
            <label>NII</label>
            <input type="text" name="nii" autofocus autocomplete="username" required placeholder="O teu NII">
          </div>
          <div class="form-group">
            <label>Password</label>
            <input type="password" name="pw" autocomplete="current-password" required placeholder="••••••••">
          </div>
          <button type="submit" class="btn btn-primary" style="width:100%;justify-content:center;padding:.72rem;font-size:.95rem;margin-top:.2rem">
            Entrar
          </button>
        </form>
      </div>
    </div>"""
    return render(content)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/dashboard')
@login_required
def dashboard():
    p = current_user().get('perfil','aluno')
    if p == 'admin': return redirect(url_for('admin_home'))
    if p in ('cozinha','oficialdia','cmd'): return redirect(url_for('painel_dia'))
    return redirect(url_for('aluno_home'))

# ═══════════════════════════════════════════════════════════════════════════
# ALUNO
# ═══════════════════════════════════════════════════════════════════════════

@app.route('/aluno')
@login_required
def aluno_home():
    u = current_user()
    uid = sr.user_id_by_nii(u['nii'])
    hoje = date.today()
    menu = sr.get_menu_do_dia(hoje)

    # Banner ausência ativa hoje
    ausente_hoje = uid and _tem_ausencia_ativa(uid, hoje)
    ausente_html = ''
    if ausente_hoje:
        ausente_html = '<div class="ausente-banner">⚓ Tens uma <strong>ausência registada</strong> para hoje. As tuas refeições não serão contabilizadas.</div>'

    menu_html = ''
    if menu:
        def mv(k): return esc(menu.get(k) or '—')
        menu_html = f"""
        <div class="card">
          <div class="card-title">🍽️ Ementa de hoje — {hoje.strftime('%d/%m/%Y')}</div>
          <div class="grid grid-4">
            <div><strong>Peq. Almoço</strong><br><span class="text-muted">{mv('pequeno_almoco')}</span></div>
            <div><strong>Lanche</strong><br><span class="text-muted">{mv('lanche')}</span></div>
            <div><strong>Almoço</strong><br>N: {mv('almoco_normal')}<br>V: {mv('almoco_veg')}<br>D: {mv('almoco_dieta')}</div>
            <div><strong>Jantar</strong><br>N: {mv('jantar_normal')}<br>V: {mv('jantar_veg')}<br>D: {mv('jantar_dieta')}</div>
          </div>
        </div>"""

    def chip(val, label, tp=None):
        if val:
            return f'<span class="meal-chip chip-{"type" if tp else "ok"}">{tp or label} ✓</span>'
        return f'<span class="meal-chip chip-no">{label} ✗</span>'

    dias_html = ''
    for i in range(DIAS_ANTECEDENCIA + 1):
        d = hoje + timedelta(days=i)
        tipo = sr.dia_operacional(d)
        r = sr.refeicao_get(uid, d) if uid else {}
        ok_edit, _ = _dia_editavel_aluno(d)
        prazo = _prazo_label(d)
        ausente_d = uid and _tem_ausencia_ativa(uid, d)
        is_weekend = d.weekday() >= 5
        is_off = tipo in ('feriado', 'exercicio')

        if is_off:
            ic = {'feriado': '🔴', 'exercicio': '🟡'}.get(tipo, '⚪')
            lb = {'feriado': 'Feriado', 'exercicio': 'Exercício'}.get(tipo, tipo)
            dias_html += f"""
            <div class="week-card day-off">
              <div class="week-dow">{ABREV_DIAS[d.weekday()]}</div>
              <div class="week-date">{d.strftime('%d/%m')}</div>
              <span class="text-muted small">{ic} {lb}</span>
            </div>"""
            continue

        aus_chip = '<span class="meal-chip chip-type" style="background:#fef3cd;color:#856404;margin-bottom:.3rem;display:block">⚓ Ausente</span>' if ausente_d else ''
        alm_t = r.get('almoco'); jan_t = r.get('jantar_tipo')
        meals = f"""<div class="week-meals">
            {chip(r.get('pequeno_almoco'),'PA')}
            {chip(r.get('lanche'),'Lan')}
            {chip(alm_t,'Alm', alm_t[:3] if alm_t else None)}
            {chip(jan_t,'Jan', jan_t[:3] if jan_t else None)}
          </div>{prazo}"""
        btn = (f'<a class="btn btn-primary btn-sm" style="margin-top:.38rem" href="{url_for("aluno_editar", d=d.isoformat())}">✏️ Editar</a>'
               if ok_edit and not ausente_d else '')

        card_cls = 'weekend-active' if is_weekend else ''
        dow_cls = 'weekend' if is_weekend else ''
        wk_icon = ''

        dias_html += f"""
        <div class="week-card {card_cls}">
          <div class="week-dow {dow_cls}">{ABREV_DIAS[d.weekday()]}</div>
          <div class="week-date">{d.strftime('%d/%m/%Y')}</div>
          {aus_chip}{meals}{btn}
        </div>"""

    stats_html = ''
    if uid:
        d0 = (hoje - timedelta(days=30)).isoformat()
        with sr.db() as conn:
            rows = conn.execute("SELECT pequeno_almoco,lanche,almoco,jantar_tipo FROM refeicoes WHERE utilizador_id=? AND data>=?", (uid, d0)).fetchall()
        if rows:
            stats_html = f"""
            <div class="card">
              <div class="card-title">📊 Últimos 30 dias</div>
              <div class="grid grid-4">
                <div class="stat-box"><div class="stat-num">{sum(1 for r in rows if r['pequeno_almoco'])}</div><div class="stat-lbl">Pequenos Almoços</div></div>
                <div class="stat-box"><div class="stat-num">{sum(1 for r in rows if r['lanche'])}</div><div class="stat-lbl">Lanches</div></div>
                <div class="stat-box"><div class="stat-num">{sum(1 for r in rows if r['almoco'])}</div><div class="stat-lbl">Almoços</div></div>
                <div class="stat-box"><div class="stat-num">{sum(1 for r in rows if r['jantar_tipo'])}</div><div class="stat-lbl">Jantares</div></div>
              </div>
            </div>"""

    content = f"""
    <div class="container">
      <div class="page-header"><div class="page-title">Olá, {esc(u['nome'])} 👋</div></div>
      {ausente_html}{menu_html}
      <div class="card">
        <div class="card-title">📆 Próximos {DIAS_ANTECEDENCIA} dias

        </div>
        <div class="week-grid">{dias_html}</div>
      </div>
      {stats_html}
      <div class="gap-btn">
        <a class="btn btn-ghost" href="{url_for('aluno_historico')}">🕘 Histórico (30 dias)</a>
        <a class="btn btn-gold" href="{url_for('aluno_ausencias')}">🚫 Gerir ausências</a>
        <a class="btn btn-ghost" href="{url_for('aluno_password')}">🔑 Alterar password</a>
        <a class="btn btn-ghost" href="{url_for('calendario_publico')}">📅 Calendário</a>
        <a class="btn btn-primary" href="{url_for('aluno_perfil')}">👤 O meu perfil</a>
      </div>
    </div>"""
    return render(content)


@app.route('/aluno/editar/<d>', methods=['GET','POST'])
@login_required
def aluno_editar(d):
    u = current_user()
    uid = sr.user_id_by_nii(u['nii'])
    dt = _parse_date(d)

    if not uid:
        flash("Conta de sistema — não é possível editar refeições.", "error")
        return redirect(url_for('aluno_home'))

    # Bloquear edição se tem ausência ativa
    if _tem_ausencia_ativa(uid, dt):
        flash("Tens uma ausência registada para este dia. Remove a ausência primeiro.", "warn")
        return redirect(url_for('aluno_home'))

    ok_edit, msg = _dia_editavel_aluno(dt)
    if not ok_edit:
        flash(f"Não é possível editar: {msg}", "warn")
        return redirect(url_for('aluno_home'))

    r = sr.refeicao_get(uid, dt)
    occ = _get_ocupacao_dia(dt)
    is_weekend = dt.weekday() >= 5

    if request.method == 'POST':
        pa = 1 if request.form.get('pa') else 0
        lanche = 1 if request.form.get('lanche') else 0
        alm = request.form.get('almoco') or ''
        jan = request.form.get('jantar') or ''
        sai = 1 if request.form.get('sai') else 0
        if _refeicao_set(uid, dt, pa, lanche, alm, jan, sai, alterado_por=u['nii']):
            flash("Refeições atualizadas!", "ok")
        else:
            flash("Erro ao guardar.", "error")
        return redirect(url_for('aluno_home'))

    def occ_row(nome):
        val, cap = occ.get(nome, (0,-1))
        return f'<div style="margin-bottom:.65rem"><strong style="font-size:.84rem">{nome}</strong>{_bar_html(val,cap)}</div>'

    def tipos_opt(sel):
        return ''.join(f'<option value="{t}" {"selected" if sel==t else ""}>{t}</option>' for t in ['Normal','Vegetariano','Dieta'])

    def chk_label(name, checked, icon, label, color=''):
        s = 'background:#eafaf1;border-color:#a9dfbf' if checked and not color else (color if color and checked else '')
        return f'<label style="display:flex;align-items:center;gap:.6rem;cursor:pointer;padding:.6rem;border:1.5px solid var(--border);border-radius:9px;{s}"><input type="checkbox" name="{name}" {"checked" if checked else ""}> {icon} {label}</label>'

    wknd_badge = ''
    wknd_note = '<div class="alert alert-info" style="margin-bottom:.8rem">Fim de semana — refeições opcionais conforme disponibilidade.</div>' if is_weekend else ''

    content = f"""
    <div class="container">
      <div class="page-header">
        {_back_btn(url_for('aluno_home'))}
        <div class="page-title">🍽️ {NOMES_DIAS[dt.weekday()]}, {dt.strftime('%d/%m/%Y')}{wknd_badge}</div>
      </div>
      {wknd_note}
      <div class="card">
        <div class="card-title">📊 Ocupação atual</div>
        {occ_row('Pequeno Almoço')}{occ_row('Lanche')}{occ_row('Almoço')}{occ_row('Jantar')}
      </div>
      <div class="card">
        <div class="card-title">✏️ A tua seleção</div>
        <form method="post">
          {csrf_input()}
          <div class="grid grid-2">
            {chk_label('pa', r.get('pequeno_almoco'), '☕', 'Pequeno Almoço')}
            {chk_label('lanche', r.get('lanche'), '🥐', 'Lanche')}
            <div class="form-group" style="margin:0">
              <label>🍽️ Almoço</label>
              <select name="almoco"><option value="">— Sem almoço —</option>{tipos_opt(r.get('almoco'))}</select>
            </div>
            <div class="form-group" style="margin:0">
              <label>🌙 Jantar</label>
              <select name="jantar"><option value="">— Sem jantar —</option>{tipos_opt(r.get('jantar_tipo'))}</select>
            </div>
          </div>
          <div style="margin-top:.8rem">
            {chk_label('sai', r.get('jantar_sai_unidade'), '🚪', 'Sai da unidade após o jantar', 'background:#fef9e7;border-color:#f9e79f')}
          </div>
          <hr>
          <div class="gap-btn">
            <button class="btn btn-ok">💾 Guardar</button>
            <a class="btn btn-ghost" href="{url_for('aluno_home')}">Cancelar</a>
          </div>
        </form>
      </div>
    </div>"""
    return render(content)


# ─── Aluno: Gerir ausências próprias ─────────────────────────────────────

@app.route('/aluno/ausencias', methods=['GET','POST'])
@login_required
def aluno_ausencias():
    u = current_user()
    uid = sr.user_id_by_nii(u['nii'])
    if not uid:
        flash("Conta de sistema — funcionalidade não disponível.", "error")
        return redirect(url_for('aluno_home'))

    if request.method == 'POST':
        acao = request.form.get('acao', '')
        if acao == 'criar':
            de = request.form.get('de', '')
            ate = request.form.get('ate', '')
            motivo = request.form.get('motivo', '').strip()
            ok, err = _registar_ausencia(uid, de, ate, motivo, u['nii'])
            flash("Ausência registada com sucesso!" if ok else (err or "Erro."), "ok" if ok else "error")
        elif acao == 'editar':
            aid = request.form.get('id', '')
            de = request.form.get('de', '')
            ate = request.form.get('ate', '')
            motivo = request.form.get('motivo', '').strip()
            ok, err = _editar_ausencia(aid, uid, de, ate, motivo)
            flash("Ausência atualizada!" if ok else (err or "Erro."), "ok" if ok else "error")
        elif acao == 'remover':
            aid = request.form.get('id', '')
            with sr.db() as conn:
                conn.execute("DELETE FROM ausencias WHERE id=? AND utilizador_id=?", (aid, uid))
                conn.commit()
            flash("Ausência removida.", "ok")
        return redirect(url_for('aluno_ausencias'))

    with sr.db() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT id,ausente_de,ausente_ate,motivo FROM ausencias WHERE utilizador_id=? ORDER BY ausente_de DESC",
            (uid,)).fetchall()]

    hoje = date.today().isoformat()
    edit_id = request.args.get('edit', '')
    edit_row = next((r for r in rows if str(r['id']) == edit_id), None)

    if edit_row:
        form_title = "✏️ Editar ausência"
        form_action = "editar"
        form_de = edit_row['ausente_de']
        form_ate = edit_row['ausente_ate']
        form_motivo = edit_row['motivo'] or ''
        form_id_inp = f'<input type="hidden" name="id" value="{edit_row["id"]}">'
        cancel_btn = f'<a class="btn btn-ghost" href="{url_for("aluno_ausencias")}">Cancelar</a>'
    else:
        form_title = "➕ Nova ausência"
        form_action = "criar"
        form_de = form_ate = form_motivo = ''
        form_id_inp = ''
        cancel_btn = ''

    rows_html = ''
    for r in rows:
        is_atual = r['ausente_de'] <= hoje <= r['ausente_ate']
        is_futura = r['ausente_de'] > hoje
        estado = ('<span class="badge badge-warn">Atual</span>' if is_atual else
                  ('<span class="badge badge-info">Futura</span>' if is_futura else
                   '<span class="badge badge-muted">Passada</span>'))
        pode = is_atual or is_futura
        edit_btn = f'<a class="btn btn-ghost btn-sm" href="{url_for("aluno_ausencias")}?edit={r["id"]}">✏️</a>' if pode else ''
        rem_form = (f'<form method="post" style="display:inline">{csrf_input()}'
                    f'<input type="hidden" name="acao" value="remover">'
                    f'<input type="hidden" name="id" value="{r["id"]}">'
                    f'<button class="btn btn-danger btn-sm" onclick="return confirm(\'Remover ausência?\')">🗑</button></form>') if pode else ''
        rows_html += f"""<tr>
          <td>{r['ausente_de']}</td><td>{r['ausente_ate']}</td>
          <td>{esc(r['motivo'] or '—')}</td><td>{estado}</td>
          <td><div class="gap-btn">{edit_btn}{rem_form}</div></td>
        </tr>"""

    content = f"""
    <div class="container">
      <div class="page-header">
        {_back_btn(url_for('aluno_home'))}
        <div class="page-title">🚫 As minhas ausências</div>
      </div>
      <div class="alert alert-info">
        📌 Com uma ausência ativa as tuas refeições não são contabilizadas e não podes editar refeições para esse período.
      </div>
      <div class="card" style="max-width:520px">
        <div class="card-title">{form_title}</div>
        <form method="post">
          {csrf_input()}
          <input type="hidden" name="acao" value="{form_action}">
          {form_id_inp}
          <div class="grid grid-2">
            <div class="form-group">
              <label>De</label>
              <input type="date" name="de" value="{form_de}" required min="{date.today().isoformat()}">
            </div>
            <div class="form-group">
              <label>Até</label>
              <input type="date" name="ate" value="{form_ate}" required min="{date.today().isoformat()}">
            </div>
          </div>
          <div class="form-group">
            <label>Motivo (opcional)</label>
            <input type="text" name="motivo" value="{esc(form_motivo)}" placeholder="Ex: deslocação, exercício, visita...">
          </div>
          <div class="gap-btn">
            <button class="btn btn-ok">{'Atualizar' if edit_row else 'Registar ausência'}</button>
            {cancel_btn}
          </div>
        </form>
      </div>
      <div class="card">
        <div class="card-title">Histórico de ausências</div>
        <div class="table-wrap">
          <table>
            <thead><tr><th>De</th><th>Até</th><th>Motivo</th><th>Estado</th><th>Ações</th></tr></thead>
            <tbody>{rows_html or '<tr><td colspan="5" class="text-muted" style="padding:1.5rem;text-align:center">Sem ausências registadas.</td></tr>'}</tbody>
          </table>
        </div>
      </div>
    </div>"""
    return render(content)


@app.route('/aluno/historico')
@login_required
def aluno_historico():
    u = current_user()
    uid = sr.user_id_by_nii(u['nii'])
    hoje = date.today()
    rows = []
    if uid:
        with sr.db() as conn:
            rows = conn.execute("""SELECT data,pequeno_almoco,lanche,almoco,jantar_tipo,jantar_sai_unidade
              FROM refeicoes WHERE utilizador_id=? AND data>=? ORDER BY data DESC""",
              (uid, (hoje-timedelta(days=30)).isoformat())).fetchall()

    def yn(v): return '✅' if v else '❌'
    rows_html = ''.join(f"<tr><td>{r['data']}</td><td>{yn(r['pequeno_almoco'])}</td><td>{yn(r['lanche'])}</td>"
                        f"<td>{r['almoco'] or '—'}</td><td>{r['jantar_tipo'] or '—'}</td>"
                        f"<td>{'✅' if r['jantar_sai_unidade'] else '—'}</td></tr>" for r in rows)

    content = f"""
    <div class="container">
      <div class="page-header">{_back_btn(url_for('aluno_home'))}<div class="page-title">🕘 Histórico — 30 dias</div></div>
      <div class="card">
        <div class="table-wrap">
          <table>
            <thead><tr><th>Data</th><th>PA</th><th>Lanche</th><th>Almoço</th><th>Jantar</th><th>Sai</th></tr></thead>
            <tbody>{rows_html or '<tr><td colspan="6" class="text-muted center" style="padding:1.5rem">Sem registos.</td></tr>'}</tbody>
          </table>
        </div>
      </div>
    </div>"""
    return render(content)


@app.route('/aluno/password', methods=['GET','POST'])
@login_required
def aluno_password():
    u = current_user()
    if request.method == 'POST':
        old = request.form.get('old','')
        new = request.form.get('new','')
        conf = request.form.get('conf','')
        if new != conf:
            flash("As passwords não coincidem.", "error")
        else:
            ok, err = _alterar_password(u['nii'], old, new)
            flash("Password alterada!" if ok else (err or "Erro."), "ok" if ok else "error")
            if ok:
                session.pop('must_change_password', None)
                return redirect(url_for('aluno_home'))

    content = f"""
    <div class="container">
      <div class="page-header">{_back_btn(url_for('aluno_home'))}<div class="page-title">🔑 Alterar password</div></div>
      <div class="card" style="max-width:440px">
        <form method="post">
          {csrf_input()}
          <div class="form-group"><label>Password atual</label><input type="password" name="old" required></div>
          <div class="form-group"><label>Nova password</label><input type="password" name="new" required></div>
          <div class="form-group"><label>Confirmar nova password</label><input type="password" name="conf" required></div>
          <div class="gap-btn"><button class="btn btn-ok">Guardar</button><a class="btn btn-ghost" href="{url_for('aluno_home')}">Cancelar</a></div>
        </form>
      </div>
    </div>"""
    return render(content)

# ─── Aluno: Perfil próprio ────────────────────────────────────────────────

@app.route('/aluno/perfil', methods=['GET','POST'])
@login_required
def aluno_perfil():
    u = current_user()
    uid = sr.user_id_by_nii(u['nii'])
    if not uid:
        flash("Conta de sistema — funcionalidade não disponível.", "error")
        return redirect(url_for('aluno_home'))

    with sr.db() as conn:
        row = dict(conn.execute(
            "SELECT NII, NI, Nome_completo, ano, email, telemovel FROM utilizadores WHERE id=?",
            (uid,)).fetchone())

    if request.method == 'POST':
        email_n  = request.form.get('email','').strip()
        telef_n  = request.form.get('telemovel','').strip()
        try:
            with sr.db() as conn:
                conn.execute("UPDATE utilizadores SET email=?, telemovel=? WHERE id=?",
                             (email_n or None, telef_n or None, uid))
                conn.commit()
            flash("Perfil atualizado com sucesso!", "ok")
            return redirect(url_for('aluno_perfil'))
        except Exception as ex:
            flash(f"Erro: {ex}", "error")

    hoje = date.today()
    with sr.db() as conn:
        total_ref = conn.execute(
            "SELECT COUNT(*) c FROM refeicoes WHERE utilizador_id=?", (uid,)).fetchone()['c']
        ausencias_ativas = conn.execute(
            """SELECT COUNT(*) c FROM ausencias WHERE utilizador_id=?
               AND ausente_de<=? AND ausente_ate>=?""",
            (uid, hoje.isoformat(), hoje.isoformat())).fetchone()['c']

    content = f"""
    <div class="container">
      <div class="page-header">
        {_back_btn(url_for('aluno_home'))}
        <div class="page-title">👤 O meu perfil</div>
      </div>
      <div class="grid grid-2">
        <div class="card">
          <div class="card-title">ℹ️ Informação pessoal</div>
          <div style="display:flex;flex-direction:column;gap:.6rem;font-size:.9rem">
            <div><span class="text-muted">Nome completo:</span><br><strong>{esc(row['Nome_completo'])}</strong></div>
            <div><span class="text-muted">NII:</span><br><strong>{esc(row['NII'])}</strong></div>
            <div><span class="text-muted">NI:</span><br><strong>{esc(row['NI'] or '—')}</strong></div>
            <div><span class="text-muted">Ano:</span><br><strong>{row['ano']}º Ano</strong></div>
          </div>
          <hr style="margin:1rem 0">
          <div class="grid grid-2">
            <div class="stat-box"><div class="stat-num">{total_ref}</div><div class="stat-lbl">Refeições registadas</div></div>
            <div class="stat-box"><div class="stat-num" style="color:{'var(--warn)' if ausencias_ativas else 'var(--ok)'}">{ausencias_ativas}</div><div class="stat-lbl">Ausências ativas</div></div>
          </div>
        </div>
        <div class="card">
          <div class="card-title">✉️ Contactos <span class="text-muted small">(para notificações)</span></div>
          <form method="post">
            {csrf_input()}
            <div class="form-group">
              <label>📧 Email</label>
              <input type="email" name="email" value="{esc(row.get('email') or '')}" placeholder="o-teu-email@exemplo.pt">
            </div>
            <div class="form-group">
              <label>📱 Telemóvel</label>
              <input type="tel" name="telemovel" value="{esc(row.get('telemovel') or '')}" placeholder="+351XXXXXXXXX">
            </div>
            <div class="alert alert-info" style="margin-bottom:.8rem;font-size:.81rem">
              📌 O email e telemóvel são usados para receberes avisos quando o prazo de edição de refeições se aproxima.
            </div>
            <div class="gap-btn">
              <button class="btn btn-ok">💾 Guardar contactos</button>
              <a class="btn btn-ghost" href="{url_for('aluno_home')}">Cancelar</a>
            </div>
          </form>
        </div>
      </div>
      <div class="card">
        <div class="card-title">⚡ Ações rápidas</div>
        <div class="gap-btn">
          <a class="btn btn-ghost" href="{url_for('aluno_ausencias')}">🚫 Gerir ausências</a>
          <a class="btn btn-ghost" href="{url_for('aluno_historico')}">🕘 Histórico de refeições</a>
          <a class="btn btn-ghost" href="{url_for('aluno_password')}">🔑 Alterar password</a>
        </div>
      </div>
    </div>"""
    return render(content)

# ═══════════════════════════════════════════════════════════════════════════
# PAINEL OPERACIONAL
# ═══════════════════════════════════════════════════════════════════════════

@app.route('/painel', methods=['GET','POST'])
@role_required('cozinha','oficialdia','cmd','admin')
def painel_dia():
    u = current_user()
    perfil = u.get('perfil')
    d_str = request.args.get('d', date.today().isoformat())
    dt = _parse_date(d_str)

    if request.method == 'POST':
        acao = request.form.get('acao','')
        if acao == 'backup':
            try:
                sr.ensure_daily_backup()
                flash("Backup criado.", "ok")
            except Exception as e:
                flash(f"Falha: {e}", "error")
        return redirect(url_for('painel_dia', d=dt.isoformat()))

    ano_int = int(u['ano']) if perfil == 'cmd' and u.get('ano') else None
    t = sr.get_totais_dia(dt.isoformat(), ano_int)
    occ = _get_ocupacao_dia(dt)

    def occ_card(nome, icon):
        val, cap = occ.get(nome, (0,-1))
        bar = _bar_html(val, cap) if cap > 0 else ''
        return f'<div class="stat-box"><div class="stat-num">{val}</div><div class="stat-lbl">{icon} {nome}</div>{bar}</div>'

    detail = f"""
    <div class="grid grid-3" style="margin-top:.9rem">
      <div class="stat-box"><div class="stat-num">{t['alm_norm']}</div><div class="stat-lbl">Almoço Normal</div></div>
      <div class="stat-box"><div class="stat-num">{t['alm_veg']}</div><div class="stat-lbl">Almoço Vegetariano</div></div>
      <div class="stat-box"><div class="stat-num">{t['alm_dieta']}</div><div class="stat-lbl">Almoço Dieta</div></div>
      <div class="stat-box"><div class="stat-num">{t['jan_norm']}</div><div class="stat-lbl">Jantar Normal</div></div>
      <div class="stat-box"><div class="stat-num">{t['jan_veg']}</div><div class="stat-lbl">Jantar Vegetariano</div></div>
      <div class="stat-box"><div class="stat-num">{t['jan_dieta']}</div><div class="stat-lbl">Jantar Dieta</div></div>
    </div>"""

    prev_d = (dt - timedelta(days=1)).isoformat()
    next_d = (dt + timedelta(days=1)).isoformat()
    nav_data = f"""
    <div class="flex-between" style="margin-bottom:1.1rem">
      <div class="flex">
        <a class="btn btn-ghost btn-sm" href="{url_for('painel_dia',d=prev_d)}">← Anterior</a>
        <strong>{NOMES_DIAS[dt.weekday()]}, {dt.strftime('%d/%m/%Y')}</strong>
        <a class="btn btn-ghost btn-sm" href="{url_for('painel_dia',d=next_d)}">Próximo →</a>
      </div>
      <form method="get" style="display:flex;gap:.3rem">
        <input type="date" name="d" value="{d_str}" style="width:auto">
        <button class="btn btn-primary btn-sm">Ir</button>
      </form>
    </div>"""

    # Ações rápidas por perfil
    acoes = []
    if perfil in ('cozinha','oficialdia','admin'):
        acoes.append(f'<a class="btn btn-ghost" href="{url_for("dashboard_semanal")}">📊 Dashboard</a>')
        acoes.append(f'<a class="btn btn-ghost" href="{url_for("admin_menus")}">🍽️ Menus &amp; Capacidade</a>')
        acoes.append(f'<a class="btn btn-ghost" href="{url_for("calendario_publico")}">📅 Calendário</a>')
        acoes.append(f'<a class="btn btn-ghost" href="{url_for("relatorio_semanal")}">📈 Relatório Semanal</a>')

    if perfil in ('oficialdia','admin'):
        anos = _get_anos_disponiveis()
        for ano in anos:
            acoes.append(f'<a class="btn btn-ghost" href="{url_for("lista_alunos_ano",ano=ano,d=d_str)}">👥 {ano}º Ano</a>')
        acoes.append(f'<a class="btn btn-primary" href="{url_for("controlo_presencas",d=dt.isoformat())}">🎯 Controlo Presenças</a>')
        acoes.append(f'<a class="btn btn-warn" href="{url_for("excecoes_dia",d=dt.isoformat())}">📝 Exceções</a>')
        acoes.append(f'<a class="btn btn-ghost" href="{url_for("ausencias")}">🚫 Ausências</a>')

    if perfil == 'cmd' and u.get('ano'):
        acoes.append(f'<a class="btn btn-ghost" href="{url_for("lista_alunos_ano",ano=u["ano"],d=d_str)}">👥 Lista do {u["ano"]}º Ano</a>')
        acoes.append(f'<a class="btn btn-ghost" href="{url_for("imprimir_ano",ano=u["ano"],d=d_str)}" target="_blank">🖨 Imprimir mapa</a>')
        acoes.append(f'<a class="btn btn-gold" href="{url_for("ausencias_cmd")}">🚫 Ausências do {u["ano"]}º Ano</a>')
        acoes.append(f'<a class="btn btn-ghost" href="{url_for("calendario_publico")}">📅 Calendário</a>')

    backup_btn = ''
    if perfil in ('oficialdia','admin'):
        backup_btn = f'<form method="post" style="display:inline">{csrf_input()}<input type="hidden" name="acao" value="backup"><button class="btn btn-ghost">💾 Backup BD</button></form>'

    back = _back_btn(url_for('admin_home')) if perfil == 'admin' else ''
    label_ano = f' — {ano_int}º Ano' if ano_int else ''

    content = f"""
    <div class="container">
      <div class="page-header">
        {back}
        <div class="page-title">📋 Painel Operacional{label_ano}</div>
        {backup_btn}
      </div>
      {nav_data}
      <div class="card">
        <div class="card-title">Ocupação geral</div>
        <div class="grid grid-4">
          {occ_card('Pequeno Almoço','☕')}
          {occ_card('Lanche','🥐')}
          {occ_card('Almoço','🍽️')}
          {occ_card('Jantar','🌙')}
        </div>
        {detail}
        {'<div style="margin-top:.65rem;font-size:.81rem;color:var(--muted)">🚪 Saem após jantar: <strong>'+str(t['jan_sai'])+'</strong></div>' if perfil != 'cozinha' else ''}
      </div>
      <div class="card">
        <div class="card-title">⬇ Exportar</div>
        <div class="gap-btn">
          <a class="btn btn-primary" href="{url_for('exportar_dia',d=dt.isoformat(),fmt='csv')}">CSV</a>
          <a class="btn btn-primary" href="{url_for('exportar_dia',d=dt.isoformat(),fmt='xlsx')}">Excel</a>
        </div>
      </div>
      {'<div class="card"><div class="card-title">⚡ Ações rápidas</div><div class="gap-btn">'+chr(10).join(acoes)+'</div></div>' if acoes else ''}
    </div>"""
    return render(content)

# ═══════════════════════════════════════════════════════════════════════════
# LISTA DE ALUNOS POR ANO (Oficial de Dia / CMD / Admin)
# ═══════════════════════════════════════════════════════════════════════════

@app.route('/alunos/<int:ano>', methods=['GET','POST'])
@role_required('oficialdia','cmd','admin')
def lista_alunos_ano(ano):
    u = current_user()
    perfil = u.get('perfil')

    # CMD só pode ver o seu ano
    if perfil == 'cmd' and str(ano) != str(u.get('ano','')):
        flash("Acesso restrito ao teu ano.", "error")
        return redirect(url_for('painel_dia'))

    d_str = request.args.get('d', date.today().isoformat())
    dt = _parse_date(d_str)

    # POST: marcar/desmarcar ausência via lista
    if request.method == 'POST':
        acao = request.form.get('acao', '')
        uid_t = request.form.get('uid', '')
        if acao == 'marcar_ausente' and uid_t:
            _registar_ausencia(int(uid_t), dt.isoformat(), dt.isoformat(),
                               f'Marcado por {u["nome"]} ({perfil})', u['nii'])
        elif acao == 'marcar_presente' and uid_t:
            with sr.db() as conn:
                conn.execute("""DELETE FROM ausencias WHERE utilizador_id=?
                                AND ausente_de=? AND ausente_ate=?""",
                             (uid_t, dt.isoformat(), dt.isoformat()))
                conn.commit()
        return redirect(url_for('lista_alunos_ano', ano=ano, d=d_str))

    with sr.db() as conn:
        alunos = [dict(r) for r in conn.execute("""
            SELECT u.id, u.NII, u.NI, u.Nome_completo,
                   r.pequeno_almoco, r.lanche, r.almoco, r.jantar_tipo, r.jantar_sai_unidade,
                   EXISTS(SELECT 1 FROM ausencias a WHERE a.utilizador_id=u.id
                          AND a.ausente_de <= ? AND a.ausente_ate >= ?) AS ausente
            FROM utilizadores u
            LEFT JOIN refeicoes r ON r.utilizador_id=u.id AND r.data=?
            WHERE u.ano=?
            ORDER BY u.NI
        """, (dt.isoformat(), dt.isoformat(), dt.isoformat(), ano)).fetchall()]

    t = sr.get_totais_dia(dt.isoformat(), ano)
    total_alunos = len(alunos)
    com_ref = sum(1 for a in alunos if any([a['almoco'], a['jantar_tipo'], a['pequeno_almoco'], a['lanche']]))
    ausentes = sum(1 for a in alunos if a['ausente'])

    prev_d = (dt - timedelta(days=1)).isoformat()
    next_d = (dt + timedelta(days=1)).isoformat()
    is_weekend = dt.weekday() >= 5

    # Tabs de ano
    anos = _get_anos_disponiveis()
    tabs = ''
    if perfil in ('oficialdia','admin'):
        tabs = '<div class="year-tabs">' + ''.join(
            f'<a class="year-tab {"active" if a==ano else ""}" href="{url_for("lista_alunos_ano",ano=a,d=d_str)}">{_ano_label(a)}</a>'
            for a in anos) + '</div>'

    def chip_ref(val, label, tp=None):
        if val:
            return f'<span class="meal-chip chip-{"type" if tp else "ok"}">{tp or label} ✓</span>'
        return f'<span class="meal-chip chip-no">{label} ✗</span>'

    # Validação de prazo para esta data
    ok_prazo, _ = sr.refeicao_editavel(dt)
    prazo_badge = ('<span class="badge badge-ok" style="font-size:.65rem">✏️ Aluno pode editar</span>'
                   if ok_prazo else
                   '<span class="badge badge-warn" style="font-size:.65rem">🔒 Só via exceção</span>')

    rows_html = ''
    for a in alunos:
        sem = not any([a['pequeno_almoco'], a['lanche'], a['almoco'], a['jantar_tipo']])
        row_bg = 'background:#fdecea' if a['ausente'] else ('background:#fff3cd' if sem else 'background:#d5f5e3')
        ausente_b = '<span class="badge badge-warn" style="font-size:.65rem">Ausente</span>' if a['ausente'] else ''
        sai_b = '<span class="badge badge-muted" style="font-size:.65rem">🚪</span>' if a['jantar_sai_unidade'] else ''
        exc_btn = ''  # Edição de refeições disponível no módulo de Exceções/Controlo de Presenças

        # Botão perfil do aluno — OD só pode VER, cmd e admin podem EDITAR
        edit_aluno_btn = ''
        if perfil == 'oficialdia':
            edit_aluno_btn = f'<a class="btn btn-ghost btn-sm" href="{url_for("ver_perfil_aluno",nii=a["NII"],ano=ano,d=d_str)}" title="Ver perfil do aluno">👁</a>'
        elif perfil in ('admin','cmd'):
            edit_aluno_btn = f'<a class="btn btn-ghost btn-sm" href="{url_for("cmd_editar_aluno",nii=a["NII"],ano=ano,d=d_str)}" title="Editar dados do aluno">👤</a>'

        # Botão de presença/ausência — removido da lista (usar módulo Controlo de Presenças)
        presenca_btn = ''

        rows_html += f"""
        <tr style="{row_bg}">
          <td class="small text-muted">{esc(a['NI'])}</td>
          <td><strong>{esc(a['Nome_completo'])}</strong> {ausente_b}</td>
          <td>{chip_ref(a['pequeno_almoco'],'PA')}</td>
          <td>{chip_ref(a['lanche'],'Lan')}</td>
          <td>{chip_ref(a['almoco'],'Almoço', a['almoco'][:3] if a['almoco'] else None)}</td>
          <td>{chip_ref(a['jantar_tipo'],'Jantar', a['jantar_tipo'][:3] if a['jantar_tipo'] else None)} {sai_b}</td>
          <td><div class="gap-btn">{presenca_btn}{exc_btn}{edit_aluno_btn}</div></td>
        </tr>"""

    wknd_badge = ''
    prazo_info_banner = ''
    if ok_prazo:
        prazo_info_banner = '<div class="alert alert-ok" style="margin-bottom:.7rem">✅ Os alunos ainda podem editar as próprias refeições (prazo não expirou).</div>'
    else:
        prazo_info_banner = '<div class="alert alert-info" style="margin-bottom:.7rem">🔒 Prazo expirado — os alunos já não podem alterar. Usa o botão <strong>✏️</strong> para fazer exceções.</div>'

    imprimir_btn = f'<a class="btn btn-ghost" href="{url_for("imprimir_ano",ano=ano,d=d_str)}" target="_blank">🖨 Imprimir mapa</a>'

    content = f"""
    <div class="container">
      <div class="page-header">
        {_back_btn(url_for('painel_dia',d=d_str),'Painel')}
        <div class="page-title">👥 {_ano_label(ano)} — {NOMES_DIAS[dt.weekday()]}, {dt.strftime('%d/%m/%Y')}{wknd_badge}</div>
        {imprimir_btn}
      </div>
      {tabs}
      {prazo_info_banner}
      <div class="grid grid-4" style="margin-bottom:1.1rem">
        <div class="stat-box"><div class="stat-num">{total_alunos}</div><div class="stat-lbl">Total alunos</div></div>
        <div class="stat-box"><div class="stat-num" style="color:var(--ok)">{com_ref}</div><div class="stat-lbl">Com refeições</div></div>
        <div class="stat-box"><div class="stat-num" style="color:var(--danger)">{total_alunos-com_ref}</div><div class="stat-lbl">Sem refeições</div></div>
        <div class="stat-box"><div class="stat-num" style="color:var(--warn)">{ausentes}</div><div class="stat-lbl">Ausentes</div></div>
      </div>

      <div class="card" style="padding:.9rem 1.2rem;margin-bottom:.8rem">
        <div class="flex-between">
          <div class="flex">
            <a class="btn btn-ghost btn-sm" href="{url_for('lista_alunos_ano',ano=ano,d=prev_d)}">← Anterior</a>
            <strong>{dt.strftime('%d/%m/%Y')}</strong>
            <a class="btn btn-ghost btn-sm" href="{url_for('lista_alunos_ano',ano=ano,d=next_d)}">Próximo →</a>
          </div>
          <form method="get" style="display:flex;gap:.3rem">
            <input type="date" name="d" value="{d_str}" style="width:auto">
            <button class="btn btn-primary btn-sm">Ir</button>
          </form>
        </div>
      </div>

      <div class="card">
        <div class="card-title">Lista de presenças
          {'<span class="badge badge-info" style="margin-left:.5rem;font-weight:400;font-size:.7rem">Usa o módulo <a href="'+url_for("controlo_presencas",d=d_str)+'">Controlo de Presenças</a> para marcar entradas/saídas</span>' if perfil in ('oficialdia','admin') else ''}
        </div>
        <div class="table-wrap">
          <table>
            <thead>
              <tr><th>NI</th><th>Nome</th><th>PA</th><th>Lanche</th><th>Almoço</th><th>Jantar</th><th>Presença / Exc.</th></tr>
            </thead>
            <tbody>{rows_html or '<tr><td colspan="7" class="text-muted center" style="padding:1.5rem">Sem dados.</td></tr>'}</tbody>
          </table>
        </div>
        <div style="margin-top:.7rem;font-size:.78rem;color:var(--muted);display:flex;gap:.8rem;flex-wrap:wrap">
          <span style="display:inline-flex;align-items:center;gap:.3rem"><span style="width:.75rem;height:.75rem;background:#d5f5e3;border:1px solid #a9dfbf;border-radius:3px;display:inline-block"></span>Presente com refeições</span>
          <span style="display:inline-flex;align-items:center;gap:.3rem"><span style="width:.75rem;height:.75rem;background:#fff3cd;border:1px solid #ffc107;border-radius:3px;display:inline-block"></span>Sem refeições marcadas</span>
          <span style="display:inline-flex;align-items:center;gap:.3rem"><span style="width:.75rem;height:.75rem;background:#fdecea;border:1px solid #f1948a;border-radius:3px;display:inline-block"></span>Ausente</span>
        </div>
      </div>

      <div class="card">
        <div class="card-title">📊 Totais do {ano}º Ano</div>
        <div class="grid grid-4">
          <div class="stat-box"><div class="stat-num">{t['pa']}</div><div class="stat-lbl">Pequenos Almoços</div></div>
          <div class="stat-box"><div class="stat-num">{t['lan']}</div><div class="stat-lbl">Lanches</div></div>
          <div class="stat-box"><div class="stat-num">{t['alm_norm']+t['alm_veg']+t['alm_dieta']}</div><div class="stat-lbl">Almoços</div></div>
          <div class="stat-box"><div class="stat-num">{t['jan_norm']+t['jan_veg']+t['jan_dieta']}</div><div class="stat-lbl">Jantares</div></div>
        </div>
        <div class="gap-btn" style="margin-top:.8rem">
          <a class="btn btn-primary" href="{url_for('exportar_dia',d=d_str,fmt='csv')}">⬇ CSV</a>
          <a class="btn btn-primary" href="{url_for('exportar_dia',d=d_str,fmt='xlsx')}">⬇ Excel</a>
          <a class="btn btn-ghost" href="{url_for('imprimir_ano',ano=ano,d=d_str)}" target="_blank">🖨 Imprimir</a>
        </div>
      </div>
    </div>"""
    return render(content)

# ═══════════════════════════════════════════════════════════════════════════
# RELATÓRIO SEMANAL (cozinha + oficialdia + admin)
# ═══════════════════════════════════════════════════════════════════════════

@app.route('/relatorio')
@role_required('cozinha','oficialdia','admin')
def relatorio_semanal():
    u = current_user()
    perfil = u.get('perfil')
    hoje = date.today()
    segunda = hoje - timedelta(days=hoje.weekday())
    d0_str = request.args.get('d0', segunda.isoformat())
    d0 = _parse_date(d0_str)
    d1 = d0 + timedelta(days=6)
    ICONE = {'normal':'','fim_semana':'🌊','feriado':'🔴','exercicio':'🟡','outro':'⚪'}

    res = []
    for i in range(7):
        di = d0 + timedelta(days=i)
        t = sr.get_totais_dia(di.isoformat())
        tipo = sr.dia_operacional(di)
        res.append({'data': di, 't': t, 'tipo': tipo})

    totais = {k: 0 for k in ['pa','lan','alm_norm','alm_veg','alm_dieta','jan_norm','jan_veg','jan_dieta','jan_sai']}
    rows_html = ''
    for r in res:
        is_off = r['tipo'] in ('feriado','exercicio')
        is_wknd = r['data'].weekday() >= 5
        st = 'color:var(--muted);background:#f9fafb' if is_off else ('background:#fffdf5' if is_wknd else '')
        ic = ICONE.get(r['tipo'],'')
        t = r['t']
        sai_td = '' if perfil == 'cozinha' else f'<td class="center">{t["jan_sai"]}</td>'
        rows_html += f"""
        <tr style="{st}">
          <td><strong>{ABREV_DIAS[r['data'].weekday()]}</strong> {r['data'].strftime('%d/%m')} {ic}</td>
          <td class="center">{t['pa']}</td><td class="center">{t['lan']}</td>
          <td class="center">{t['alm_norm']}</td><td class="center">{t['alm_veg']}</td><td class="center">{t['alm_dieta']}</td>
          <td class="center">{t['jan_norm']}</td><td class="center">{t['jan_veg']}</td><td class="center">{t['jan_dieta']}</td>
          {sai_td}
        </tr>"""
        for k in totais: totais[k] += t[k]

    sai_th = '' if perfil == 'cozinha' else '<th>Sai</th>'
    sai_total = '' if perfil == 'cozinha' else f'<td class="center">{totais["jan_sai"]}</td>'
    rows_html += f"""
    <tr style="font-weight:800;background:#f0f4f8;border-top:2px solid var(--border)">
      <td>TOTAL</td>
      <td class="center">{totais['pa']}</td><td class="center">{totais['lan']}</td>
      <td class="center">{totais['alm_norm']}</td><td class="center">{totais['alm_veg']}</td><td class="center">{totais['alm_dieta']}</td>
      <td class="center">{totais['jan_norm']}</td><td class="center">{totais['jan_veg']}</td><td class="center">{totais['jan_dieta']}</td>
      {sai_total}
    </tr>"""

    prev_w = (d0 - timedelta(days=7)).isoformat()
    next_w = (d0 + timedelta(days=7)).isoformat()
    back_url = url_for('admin_home') if perfil == 'admin' else url_for('painel_dia')

    content = f"""
    <div class="container">
      <div class="page-header">
        {_back_btn(back_url)}
        <div class="page-title">📊 Relatório Semanal</div>
      </div>
      <div class="card" style="padding:.9rem 1.2rem;margin-bottom:.8rem">
        <div class="flex-between">
          <div class="flex">
            <a class="btn btn-ghost btn-sm" href="{url_for('relatorio_semanal',d0=prev_w)}">← Semana anterior</a>
            <strong>{d0.strftime('%d/%m/%Y')} — {d1.strftime('%d/%m/%Y')}</strong>
            <a class="btn btn-ghost btn-sm" href="{url_for('relatorio_semanal',d0=next_w)}">Semana seguinte →</a>
          </div>
          <form method="get" style="display:flex;gap:.3rem">
            <input type="date" name="d0" value="{d0_str}" style="width:auto">
            <button class="btn btn-primary btn-sm">Ir</button>
          </form>
        </div>
      </div>
      <div class="card">
        <div class="table-wrap">
          <table>
            <thead><tr><th>Dia</th><th>PA</th><th>Lanche</th><th>Alm N</th><th>Alm V</th><th>Alm D</th><th>Jan N</th><th>Jan V</th><th>Jan D</th>{sai_th}</tr></thead>
            <tbody>{rows_html}</tbody>
          </table>
        </div>
        <div class="gap-btn" style="margin-top:.9rem">
          <a class="btn btn-primary" href="{url_for('exportar_relatorio',d0=d0_str,fmt='csv')}">⬇ CSV</a>
          <a class="btn btn-primary" href="{url_for('exportar_relatorio',d0=d0_str,fmt='xlsx')}">⬇ Excel</a>
        </div>
      </div>
      <div class="grid grid-4">
        <div class="stat-box"><div class="stat-num">{totais['pa']}</div><div class="stat-lbl">Total PA</div></div>
        <div class="stat-box"><div class="stat-num">{totais['lan']}</div><div class="stat-lbl">Total Lanches</div></div>
        <div class="stat-box"><div class="stat-num">{totais['alm_norm']+totais['alm_veg']+totais['alm_dieta']}</div><div class="stat-lbl">Total Almoços</div></div>
        <div class="stat-box"><div class="stat-num">{totais['jan_norm']+totais['jan_veg']+totais['jan_dieta']}</div><div class="stat-lbl">Total Jantares</div></div>
      </div>
    </div>"""
    return render(content)

# ═══════════════════════════════════════════════════════════════════════════
# EXCEÇÕES
# ═══════════════════════════════════════════════════════════════════════════

@app.route('/excecoes/<d>', methods=['GET','POST'])
@role_required('oficialdia','admin')
def excecoes_dia(d):
    u = current_user()
    dt = _parse_date(d)

    if request.method == 'POST':
        nii = request.form.get('nii','').strip()
        db_u = sr.user_by_nii(nii)
        if not db_u:
            flash("Utilizador não encontrado.", "error")
            return redirect(url_for('excecoes_dia', d=dt.isoformat()))
        pa = 1 if request.form.get('pa') else 0
        lanche = 1 if request.form.get('lanche') else 0
        alm = request.form.get('almoco') or ''
        jan = request.form.get('jantar') or ''
        sai = 1 if request.form.get('sai') else 0
        if _refeicao_set(db_u['id'], dt, pa, lanche, alm, jan, sai, alterado_por=u['nii']):
            flash(f'Exceção guardada para {db_u["Nome_completo"]}.', 'ok')
        else:
            flash("Erro ao guardar.", "error")
        return redirect(url_for('excecoes_dia', d=dt.isoformat(), nii=request.form.get('nii','')))

    nii_q = request.args.get('nii','').strip()
    u_info = sr.user_by_nii(nii_q) if nii_q else None
    r = sr.refeicao_get(u_info['id'], dt) if u_info and u_info.get('id') else {}

    def tipos_opt(sel):
        return ''.join(f'<option value="{t}" {"selected" if sel==t else ""}>{t}</option>' for t in ['Normal','Vegetariano','Dieta'])

    def chk_label(name, checked, icon, label):
        s = 'background:#eafaf1;border-color:#a9dfbf' if checked else ''
        return f'<label style="display:flex;align-items:center;gap:.6rem;cursor:pointer;padding:.6rem;border:1.5px solid var(--border);border-radius:9px;{s}"><input type="checkbox" name="{name}" {"checked" if checked else ""}> {icon} {label}</label>'

    form_html = ''
    if u_info:
        uid_info = u_info.get('id')
        # Ausência ativa
        ausente_hoje = uid_info and _tem_ausencia_ativa(uid_info, dt)
        # Prazo — pode o aluno ainda alterar por si?
        ok_prazo, _ = sr.refeicao_editavel(dt)
        # Histórico recente de ausências
        aus_hist = []
        if uid_info:
            with sr.db() as conn:
                aus_hist = [dict(r) for r in conn.execute("""
                    SELECT ausente_de, ausente_ate, motivo FROM ausencias
                    WHERE utilizador_id=? ORDER BY ausente_de DESC LIMIT 5
                """, (uid_info,)).fetchall()]

        ausente_alert = ''
        if ausente_hoje:
            ausente_alert = '<div class="alert alert-warn">⚠️ <strong>Utilizador com ausência activa hoje</strong> — esta exceção pode não ter efeito prático.</div>'

        prazo_info = ''
        if ok_prazo:
            prazo_info = '<div class="alert alert-ok" style="margin-bottom:.6rem">✅ O aluno ainda pode alterar refeições por si próprio (prazo não expirou). Esta exceção só é necessária se o aluno não conseguir aceder ao sistema.</div>'
        else:
            prazo_info = '<div class="alert alert-info" style="margin-bottom:.6rem">🔒 Prazo expirado — o aluno já não pode alterar. Esta exceção é necessária para efetuar qualquer alteração.</div>'

        aus_hist_html = ''
        if aus_hist:
            aus_hist_html = '<div style="margin-top:.75rem"><div class="card-title" style="font-size:.8rem;margin-bottom:.4rem">📋 Ausências recentes</div>'
            for ah in aus_hist:
                aus_hist_html += f'<div style="font-size:.78rem;padding:.22rem 0;border-bottom:1px solid var(--border);color:var(--text)">{ah["ausente_de"]} → {ah["ausente_ate"]} <span class="text-muted">{esc(ah["motivo"] or "—")}</span></div>'
            aus_hist_html += '</div>'

        form_html = f"""
        <div class="card">
          <div class="card-title">✏️ {esc(u_info.get('Nome_completo',''))} — NI {esc(u_info.get('NI',''))} | {esc(u_info.get('ano',''))}º Ano</div>
          {ausente_alert}{prazo_info}
          <form method="post">
            {csrf_input()}
            <input type="hidden" name="nii" value="{esc(nii_q)}">
            <div class="grid grid-2">
              {chk_label('pa', r.get('pequeno_almoco'),'☕','Pequeno Almoço')}
              {chk_label('lanche', r.get('lanche'),'🥐','Lanche')}
              <div class="form-group" style="margin:0">
                <label>🍽️ Almoço</label>
                <select name="almoco"><option value="">— Sem almoço —</option>{tipos_opt(r.get('almoco'))}</select>
              </div>
              <div class="form-group" style="margin:0">
                <label>🌙 Jantar</label>
                <select name="jantar"><option value="">— Sem jantar —</option>{tipos_opt(r.get('jantar_tipo'))}</select>
              </div>
            </div>
            <div style="margin-top:.8rem">
              {chk_label('sai', r.get('jantar_sai_unidade'),'🚪','Sai da unidade após jantar')}
            </div>
            <hr>
            <button class="btn btn-ok">💾 Guardar exceção</button>
          </form>
          {aus_hist_html}
        </div>"""

    content = f"""
    <div class="container">
      <div class="page-header">
        {_back_btn(url_for('painel_dia',d=dt.isoformat()),'Painel')}
        <div class="page-title">📝 Exceções — {NOMES_DIAS[dt.weekday()]}, {dt.strftime('%d/%m/%Y')}</div>
      </div>
      <div class="card">
        <div class="card-title">Pesquisar utilizador</div>
        <form method="get" style="display:flex;gap:.5rem">
          <input type="hidden" name="d" value="{d}">
          <input type="text" name="nii" placeholder="NII do utilizador" value="{esc(nii_q)}" style="flex:1">
          <button class="btn btn-primary">Pesquisar</button>
        </form>
      </div>
      {form_html or '<div class="card"><div class="text-muted">Introduz um NII para editar exceções.</div></div>'}
    </div>"""
    return render(content)

# ═══════════════════════════════════════════════════════════════════════════
# AUSÊNCIAS
# ═══════════════════════════════════════════════════════════════════════════

@app.route('/ausencias', methods=['GET','POST'])
@role_required('oficialdia','admin')
def ausencias():
    u = current_user()
    if request.method == 'POST':
        acao = request.form.get('acao','')
        if acao == 'remover':
            _remover_ausencia(request.form.get('id'))
            flash("Ausência removida.", "ok")
            return redirect(url_for('ausencias'))
        nii = request.form.get('nii','').strip()
        db_u = sr.user_by_nii(nii)
        if not db_u:
            flash("Utilizador não encontrado.", "error")
        else:
            ok, err = _registar_ausencia(db_u['id'], request.form.get('de',''), request.form.get('ate',''),
                                         request.form.get('motivo',''), u['nii'])
            flash(f'Ausência registada para {db_u["Nome_completo"]}.' if ok else (err or "Falha."),
                  "ok" if ok else "error")
        return redirect(url_for('ausencias'))

    with sr.db() as conn:
        rows = [dict(r) for r in conn.execute("""
            SELECT a.id, u.NII, u.Nome_completo, u.NI, u.ano,
                   a.ausente_de, a.ausente_ate, a.motivo
            FROM ausencias a JOIN utilizadores u ON u.id=a.utilizador_id
            ORDER BY a.ausente_de DESC""").fetchall()]

    hoje = date.today().isoformat()
    rows_html = ''.join(f"""
      <tr>
        <td><strong>{esc(r['Nome_completo'])}</strong><br><span class="text-muted small">{esc(r['NII'])} · {r['ano']}º ano</span></td>
        <td>{r['ausente_de']}</td><td>{r['ausente_ate']}</td>
        <td>{esc(r['motivo'] or '—')}</td>
        <td>{'<span class="badge badge-warn">Atual</span>' if r['ausente_de'] <= hoje <= r['ausente_ate'] else '<span class="badge badge-muted">Inativa</span>'}</td>
        <td><form method="post" style="display:inline">{csrf_input()}<input type="hidden" name="acao" value="remover"><input type="hidden" name="id" value="{r['id']}"><button class="btn btn-danger btn-sm">🗑</button></form></td>
      </tr>""" for r in rows)

    content = f"""
    <div class="container">
      <div class="page-header">{_back_btn(url_for('painel_dia'))}<div class="page-title">🚫 Ausências</div></div>
      <div class="card">
        <div class="card-title">Registar ausência</div>
        <form method="post">
          {csrf_input()}
          <div class="grid grid-2">
            <div class="form-group"><label>NII do utilizador</label><input type="text" name="nii" required placeholder="NII"></div>
            <div class="form-group"><label>Motivo (opcional)</label><input type="text" name="motivo" placeholder="Ex: deslocação, prova..."></div>
            <div class="form-group"><label>De</label><input type="date" name="de" required></div>
            <div class="form-group"><label>Até</label><input type="date" name="ate" required></div>
          </div>
          <button class="btn btn-ok">Registar</button>
        </form>
      </div>
      <div class="card">
        <div class="card-title">Lista de ausências</div>
        <div class="table-wrap">
          <table>
            <thead><tr><th>Utilizador</th><th>De</th><th>Até</th><th>Motivo</th><th>Estado</th><th></th></tr></thead>
            <tbody>{rows_html or '<tr><td colspan="6" class="text-muted center" style="padding:1.5rem">Sem ausências.</td></tr>'}</tbody>
          </table>
        </div>
      </div>
    </div>"""
    return render(content)

# ═══════════════════════════════════════════════════════════════════════════
# CMD — Editar dados de aluno do seu ano
# ═══════════════════════════════════════════════════════════════════════════

@app.route('/cmd/editar-aluno/<nii>', methods=['GET','POST'])
@role_required('cmd','oficialdia','admin')
def cmd_editar_aluno(nii):
    u = current_user()
    perfil = u.get('perfil')
    ano_cmd = int(u.get('ano', 0)) if u.get('ano') else 0
    ano_ret = request.args.get('ano', str(ano_cmd) if ano_cmd else '1')
    d_ret   = request.args.get('d', date.today().isoformat())

    # Buscar o aluno
    with sr.db() as conn:
        aluno = dict(conn.execute(
            "SELECT id,NII,NI,Nome_completo,ano,email,telemovel FROM utilizadores WHERE NII=?",
            (nii,)).fetchone() or {})

    if not aluno:
        flash("Aluno não encontrado.", "error")
        back_ano = aluno.get('ano', ano_cmd or 1) if aluno else (ano_cmd or 1)
        return redirect(url_for('lista_alunos_ano', ano=back_ano, d=d_ret))

    # CMD só pode editar alunos do seu ano
    if perfil == 'cmd' and int(aluno.get('ano', 0)) != ano_cmd:
        flash("Só podes editar alunos do teu ano.", "error")
        return redirect(url_for('lista_alunos_ano', ano=ano_cmd, d=d_ret))

    if request.method == 'POST':
        nome_n  = request.form.get('nome','').strip()
        ni_n    = request.form.get('ni','').strip()
        email_n = request.form.get('email','').strip()
        telef_n = request.form.get('telemovel','').strip()
        if not nome_n:
            flash("O nome não pode estar vazio.", "error")
        else:
            try:
                with sr.db() as conn:
                    conn.execute(
                        "UPDATE utilizadores SET Nome_completo=?,NI=?,email=?,telemovel=? WHERE NII=?",
                        (nome_n, ni_n or None, email_n or None, telef_n or None, nii))
                    conn.commit()
                flash(f"Dados de {nome_n} actualizados.", "ok")
                return redirect(url_for('lista_alunos_ano', ano=ano_ret or aluno.get('ano', 1), d=d_ret))
            except Exception as ex:
                flash(f"Erro: {ex}", "error")

    back_url = url_for('lista_alunos_ano', ano=ano_ret, d=d_ret)
    content = f"""
    <div class="container">
      <div class="page-header">
        {_back_btn(back_url, f'{ano_ret}º Ano')}
        <div class="page-title">👤 Editar aluno — {esc(aluno.get('Nome_completo',''))}</div>
      </div>
      <div class="card" style="max-width:560px">
        <div class="card-title">ℹ️ Dados do aluno
          <span class="badge badge-info" style="margin-left:.4rem">{aluno['ano']}º Ano</span>
        </div>
        <form method="post">
          {csrf_input()}
          <div class="grid grid-2">
            <div class="form-group">
              <label>Nome completo</label>
              <input type="text" name="nome" value="{esc(aluno.get('Nome_completo',''))}" required>
            </div>
            <div class="form-group">
              <label>NI <span class="text-muted small">(número interno)</span></label>
              <input type="text" name="ni" value="{esc(aluno.get('NI') or '')}">
            </div>
            <div class="form-group">
              <label>📧 Email</label>
              <input type="email" name="email" value="{esc(aluno.get('email') or '')}" placeholder="email@exemplo.pt">
            </div>
            <div class="form-group">
              <label>📱 Telemóvel</label>
              <input type="tel" name="telemovel" value="{esc(aluno.get('telemovel') or '')}" placeholder="+351XXXXXXXXX">
            </div>
          </div>
          <div class="alert alert-info" style="font-size:.81rem;margin-bottom:.8rem">
            📌 NII: <strong>{esc(aluno['NII'])}</strong> — Este campo não pode ser alterado aqui.
            Para alterar o NII contacta o administrador.
          </div>
          <div class="gap-btn">
            <button class="btn btn-ok">💾 Guardar alterações</button>
            <a class="btn btn-ghost" href="{back_url}">Cancelar</a>
          </div>
        </form>
      </div>
    </div>"""
    return render(content)

# ═══════════════════════════════════════════════════════════════════════════
# VER PERFIL DE ALUNO — Oficial de Dia (apenas leitura)
# ═══════════════════════════════════════════════════════════════════════════

@app.route('/alunos/perfil/<nii>')
@role_required('oficialdia','admin','cmd')
def ver_perfil_aluno(nii):
    u = current_user()
    perfil = u.get('perfil')
    ano_ret = request.args.get('ano', '')
    d_ret   = request.args.get('d', date.today().isoformat())

    with sr.db() as conn:
        aluno = conn.execute(
            "SELECT id,NII,NI,Nome_completo,ano,email,telemovel FROM utilizadores WHERE NII=?",
            (nii,)).fetchone()

    if not aluno:
        flash("Aluno não encontrado.", "error")
        return redirect(url_for('painel_dia'))
    aluno = dict(aluno)

    # CMD só pode ver alunos do seu ano
    if perfil == 'cmd' and str(aluno.get('ano', 0)) != str(u.get('ano', '')):
        flash("Acesso restrito ao teu ano.", "error")
        return redirect(url_for('painel_dia'))

    # Admin é redirecionado para edição
    if perfil == 'admin':
        return redirect(url_for('cmd_editar_aluno', nii=nii, ano=ano_ret or aluno['ano'], d=d_ret))

    hoje = date.today()
    uid = aluno['id']
    with sr.db() as conn:
        total_ref = conn.execute(
            "SELECT COUNT(*) c FROM refeicoes WHERE utilizador_id=?", (uid,)).fetchone()['c']
        ausencias_ativas = conn.execute(
            """SELECT COUNT(*) c FROM ausencias WHERE utilizador_id=?
               AND ausente_de<=? AND ausente_ate>=?""",
            (uid, hoje.isoformat(), hoje.isoformat())).fetchone()['c']
        aus_recentes = [dict(r) for r in conn.execute(
            """SELECT ausente_de, ausente_ate, motivo FROM ausencias
               WHERE utilizador_id=? ORDER BY ausente_de DESC LIMIT 5""",
            (uid,)).fetchall()]
        # Refeições de hoje
        ref_hoje = conn.execute(
            "SELECT * FROM refeicoes WHERE utilizador_id=? AND data=?",
            (uid, hoje.isoformat())).fetchone()

    ref_hoje = dict(ref_hoje) if ref_hoje else {}

    def yn(v, t=None): return f'<span class="badge badge-ok">{t or "✅"}</span>' if v else '<span class="badge badge-muted">—</span>'

    aus_html = ''
    for a in aus_recentes:
        aus_html += f'<div style="font-size:.82rem;padding:.25rem 0;border-bottom:1px solid var(--border)">{a["ausente_de"]} → {a["ausente_ate"]} <span class="text-muted small">{esc(a["motivo"] or "—")}</span></div>'

    back_url = url_for('lista_alunos_ano', ano=ano_ret or aluno['ano'], d=d_ret)
    content = f"""
    <div class="container">
      <div class="page-header">
        {_back_btn(back_url, f'{ano_ret or aluno["ano"]}º Ano')}
        <div class="page-title">👁 Perfil — {esc(aluno.get('Nome_completo',''))}</div>
        <span class="badge badge-info">Só leitura</span>
      </div>
      <div class="grid grid-2">
        <div class="card">
          <div class="card-title">ℹ️ Informação pessoal</div>
          <div style="display:flex;flex-direction:column;gap:.7rem;font-size:.9rem">
            <div><span class="text-muted">Nome completo:</span><br><strong>{esc(aluno['Nome_completo'])}</strong></div>
            <div><span class="text-muted">NII:</span><br><strong>{esc(aluno['NII'])}</strong></div>
            <div><span class="text-muted">NI:</span><br><strong>{esc(aluno.get('NI') or '—')}</strong></div>
            <div><span class="text-muted">Ano:</span><br><strong>{aluno['ano']}º Ano</strong></div>
            <div><span class="text-muted">📧 Email:</span><br><strong>{esc(aluno.get('email') or '—')}</strong></div>
            <div><span class="text-muted">📱 Telemóvel:</span><br><strong>{esc(aluno.get('telemovel') or '—')}</strong></div>
          </div>
          <hr style="margin:1rem 0">
          <div class="grid grid-2">
            <div class="stat-box"><div class="stat-num">{total_ref}</div><div class="stat-lbl">Refeições registadas</div></div>
            <div class="stat-box"><div class="stat-num" style="color:{'var(--warn)' if ausencias_ativas else 'var(--ok)'}">{ausencias_ativas}</div><div class="stat-lbl">Ausências ativas</div></div>
          </div>
        </div>
        <div class="card">
          <div class="card-title">🍽️ Refeições de hoje — {hoje.strftime('%d/%m/%Y')}</div>
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:.6rem;margin-bottom:.8rem">
            <div style="padding:.6rem;background:#f8f9fa;border-radius:8px;text-align:center">
              <div class="text-muted small">☕ Pequeno Almoço</div>
              <div style="margin-top:.3rem">{yn(ref_hoje.get('pequeno_almoco'))}</div>
            </div>
            <div style="padding:.6rem;background:#f8f9fa;border-radius:8px;text-align:center">
              <div class="text-muted small">🥐 Lanche</div>
              <div style="margin-top:.3rem">{yn(ref_hoje.get('lanche'))}</div>
            </div>
            <div style="padding:.6rem;background:#f8f9fa;border-radius:8px;text-align:center">
              <div class="text-muted small">🍽️ Almoço</div>
              <div style="margin-top:.3rem"><strong>{ref_hoje.get('almoco') or '—'}</strong></div>
            </div>
            <div style="padding:.6rem;background:#f8f9fa;border-radius:8px;text-align:center">
              <div class="text-muted small">🌙 Jantar</div>
              <div style="margin-top:.3rem"><strong>{ref_hoje.get('jantar_tipo') or '—'}</strong></div>
            </div>
          </div>
          {'<div class="alert alert-warn" style="font-size:.82rem">⚠️ Aluno com ausência ativa hoje</div>' if ausencias_ativas else ''}
          <div class="card-title" style="margin-top:.8rem">📋 Ausências recentes</div>
          {aus_html or '<div class="text-muted small">Sem ausências registadas.</div>'}
        </div>
      </div>
      <div class="alert alert-info" style="font-size:.82rem">
        🔒 Estás no modo de visualização. Para editar dados do aluno, contacta o Comandante de Companhia ou o Administrador.
      </div>
    </div>"""
    return render(content)


# ═══════════════════════════════════════════════════════════════════════════
# AUSÊNCIAS — CMD (acesso restrito ao seu ano)
# ═══════════════════════════════════════════════════════════════════════════

@app.route('/cmd/ausencias', methods=['GET','POST'])
@role_required('cmd','admin')
def ausencias_cmd():
    u = current_user()
    perfil = u.get('perfil')
    ano_cmd = int(u.get('ano', 0)) if perfil == 'cmd' else 0

    if request.method == 'POST':
        acao = request.form.get('acao','')
        if acao == 'remover':
            # Validar que a ausência pertence ao ano do cmd
            with sr.db() as conn:
                aus = conn.execute("""SELECT a.id FROM ausencias a
                    JOIN utilizadores u ON u.id=a.utilizador_id
                    WHERE a.id=? AND (u.ano=? OR ?=0)""",
                    (request.form.get('id'), ano_cmd, perfil=='admin')).fetchone()
            if aus:
                _remover_ausencia(request.form.get('id'))
                flash("Ausência removida.", "ok")
            else:
                flash("Não autorizado.", "error")
            return redirect(url_for('ausencias_cmd'))
        nii = request.form.get('nii','').strip()
        db_u = sr.user_by_nii(nii)
        if not db_u:
            flash("Utilizador não encontrado.", "error")
        elif perfil == 'cmd' and int(db_u.get('ano',0)) != ano_cmd:
            flash(f"Só podes registar ausências para alunos do {ano_cmd}º ano.", "error")
        else:
            ok, err = _registar_ausencia(db_u['id'], request.form.get('de',''), request.form.get('ate',''),
                                         request.form.get('motivo',''), u['nii'])
            flash(f'Ausência registada para {db_u["Nome_completo"]}.' if ok else (err or "Falha."),
                  "ok" if ok else "error")
        return redirect(url_for('ausencias_cmd'))

    filtro_ano = f"AND u.ano={ano_cmd}" if perfil == 'cmd' else ""
    with sr.db() as conn:
        rows = [dict(r) for r in conn.execute(f"""
            SELECT a.id, u.NII, u.Nome_completo, u.NI, u.ano,
                   a.ausente_de, a.ausente_ate, a.motivo
            FROM ausencias a JOIN utilizadores u ON u.id=a.utilizador_id
            WHERE u.perfil='aluno' {filtro_ano}
            ORDER BY a.ausente_de DESC""").fetchall()]

    # Alunos do ano para pesquisa rápida
    with sr.db() as conn:
        alunos_ano = [dict(r) for r in conn.execute(
            "SELECT NII, NI, Nome_completo FROM utilizadores WHERE perfil='aluno' AND ano=? ORDER BY NI",
            (ano_cmd,)).fetchall()] if perfil == 'cmd' else []

    hoje = date.today().isoformat()
    rows_html = ''.join(f"""
      <tr>
        <td><strong>{esc(r['Nome_completo'])}</strong><br><span class="text-muted small">{esc(r['NII'])} · {r['ano']}º ano</span></td>
        <td>{r['ausente_de']}</td><td>{r['ausente_ate']}</td>
        <td>{esc(r['motivo'] or '—')}</td>
        <td>{'<span class="badge badge-warn">Atual</span>' if r['ausente_de'] <= hoje <= r['ausente_ate'] else '<span class="badge badge-muted">Inativa</span>'}</td>
        <td><form method="post" style="display:inline">{csrf_input()}<input type="hidden" name="acao" value="remover"><input type="hidden" name="id" value="{r['id']}"><button class="btn btn-danger btn-sm">🗑</button></form></td>
      </tr>""" for r in rows)

    alunos_options = ''.join(f'<option value="{esc(a["NII"])}">{esc(a["NI"])} — {esc(a["Nome_completo"])}</option>' for a in alunos_ano)
    alunos_datalist = (f'<datalist id="alunos_list">{alunos_options}</datalist>' if alunos_ano else '')

    titulo = f'🚫 Ausências — {ano_cmd}º Ano' if perfil == 'cmd' else '🚫 Ausências (todos os anos)'
    back_url = url_for('painel_dia') if perfil == 'cmd' else url_for('ausencias')

    content = f"""
    <div class="container">
      <div class="page-header">{_back_btn(back_url)}<div class="page-title">{titulo}</div></div>
      <div class="card">
        <div class="card-title">Registar ausência</div>
        {alunos_datalist}
        <form method="post">
          {csrf_input()}
          <div class="grid grid-2">
            <div class="form-group">
              <label>NII do aluno</label>
              <input type="text" name="nii" required placeholder="NII" list="alunos_list">
              {'<div class="text-muted small" style="margin-top:.25rem">💡 Escreve para ver sugestões de alunos do teu ano</div>' if alunos_ano else ''}
            </div>
            <div class="form-group"><label>Motivo (opcional)</label><input type="text" name="motivo" placeholder="Ex: deslocação, exercício..."></div>
            <div class="form-group"><label>De</label><input type="date" name="de" required value="{hoje}"></div>
            <div class="form-group"><label>Até</label><input type="date" name="ate" required value="{hoje}"></div>
          </div>
          <button class="btn btn-ok">✅ Registar ausência</button>
        </form>
      </div>
      <div class="card">
        <div class="card-title">Ausências registadas</div>
        <div class="table-wrap">
          <table>
            <thead><tr><th>Aluno</th><th>De</th><th>Até</th><th>Motivo</th><th>Estado</th><th>Ações</th></tr></thead>
            <tbody>{rows_html or '<tr><td colspan="6" class="text-muted center" style="padding:1.5rem">Sem ausências.</td></tr>'}</tbody>
          </table>
        </div>
      </div>
    </div>"""
    return render(content)

# ═══════════════════════════════════════════════════════════════════════════
# ADMIN
# ═══════════════════════════════════════════════════════════════════════════

@app.route('/admin')
@role_required('admin')
def admin_home():
    hoje = date.today()
    t = sr.get_totais_dia(hoje.isoformat())
    with sr.db() as conn:
        n_users = conn.execute("SELECT COUNT(*) c FROM utilizadores").fetchone()['c']

    action_cards = [
        (url_for('painel_dia'),        '📋', 'Painel do dia',        'Ocupação e totais'),
        (url_for('admin_utilizadores'),'👥', f'Utilizadores ({n_users})', 'Gerir contas'),
        (url_for('admin_menus'),       '🍽️', 'Menus & Capacidade',   'Ementas e limites'),
        (url_for('dashboard_semanal'), '📊', 'Dashboard Semanal',    'Gráficos e relatório'),
        (url_for('relatorio_semanal'), '📈', 'Relatório Semanal',    'Exportar dados'),
        (url_for('admin_log'),         '📜', 'Log de Refeições',     'Alterações de refeições'),
        (url_for('admin_audit'),       '🔐', 'Auditoria de Ações',   'Logins e alterações admin'),
        (url_for('admin_calendario'),  '⚙️', 'Gerir Calendário',      'Dias operacionais'),
        (url_for('ausencias'),         '🚫', 'Ausências',            'Gerir ausências'),
        (url_for('admin_notificacoes'),'🔔', 'Notificações',         'Email & SMS'),
        (url_for('calendario_publico'),   '📅', 'Calendário',           'Ver calendário'),
        (url_for('admin_companhias'),       '⚓', 'Gestão de Companhias', 'Turmas, promoções e cursos'),
        (url_for('controlo_presencas'),    '🎯', 'Controlo Presenças',    'Pesquisa rápida por NI'),
        (url_for('admin_importar_csv'),    '📥', 'Importar CSV',          'Criar alunos em massa'),
    ]

    anos = _get_anos_disponiveis()
    ano_cards = ''.join(
        f'<a class="action-card" href="{url_for("lista_alunos_ano",ano=a,d=hoje.isoformat())}">'
        f'<div class="icon">👥</div><div class="label">{_ano_label(a)}</div><div class="desc">Lista de presenças</div></a>'
        for a in anos)

    cards_html = ''.join(
        f'<a class="action-card" href="{href}"><div class="icon">{icon}</div>'
        f'<div class="label">{label}</div><div class="desc">{desc}</div></a>'
        for href, icon, label, desc in action_cards)

    total_alm = t['alm_norm']+t['alm_veg']+t['alm_dieta']
    total_jan = t['jan_norm']+t['jan_veg']+t['jan_dieta']

    content = f"""
    <div class="container">
      <div class="page-header"><div class="page-title">⚓ Administração — Escola Naval</div></div>
      <div class="card">
        <div class="card-title">📊 Hoje — {hoje.strftime('%d/%m/%Y')}</div>
        <div class="grid grid-4">
          <div class="stat-box"><div class="stat-num">{t['pa']}</div><div class="stat-lbl">Pequenos Almoços</div></div>
          <div class="stat-box"><div class="stat-num">{t['lan']}</div><div class="stat-lbl">Lanches</div></div>
          <div class="stat-box"><div class="stat-num">{total_alm}</div><div class="stat-lbl">Almoços</div></div>
          <div class="stat-box"><div class="stat-num">{total_jan}</div><div class="stat-lbl">Jantares</div></div>
        </div>
      </div>
      <div class="card">
        <div class="card-title">⚡ Módulos</div>
        <div class="grid grid-4">{cards_html}</div>
      </div>
      <div class="card">
        <div class="card-title">👥 Lista por ano</div>
        <div class="grid grid-4">{ano_cards}</div>
      </div>
    </div>"""
    return render(content)


@app.route('/admin/utilizadores', methods=['GET','POST'])
@role_required('admin')
def admin_utilizadores():
    if request.method == 'POST':
        acao = request.form.get('acao','')
        if acao == 'criar':
            ok, err = _criar_utilizador(request.form.get('nii','').strip(), request.form.get('ni','').strip(),
                request.form.get('nome','').strip(), request.form.get('ano','').strip(),
                request.form.get('perfil','aluno'), request.form.get('pw','').strip())
            flash("Utilizador criado." if ok else (err or "Erro."), "ok" if ok else "error")
        elif acao == 'editar_user':
            nii_e = request.form.get('nii','')
            nome_e = request.form.get('nome','').strip()
            ni_e = request.form.get('ni','').strip()
            ano_e = request.form.get('ano','').strip()
            perfil_e = request.form.get('perfil','aluno')
            email_e = request.form.get('email','').strip()
            tel_e = request.form.get('telemovel','').strip()
            pw_e = request.form.get('pw','').strip()
            try:
                with sr.db() as conn:
                    conn.execute("UPDATE utilizadores SET Nome_completo=?,NI=?,ano=?,perfil=?,email=?,telemovel=? WHERE NII=?",
                                 (nome_e, ni_e, ano_e, perfil_e, email_e or None, tel_e or None, nii_e))
                    conn.commit()
                if pw_e:
                    with sr.db() as conn:
                        conn.execute("UPDATE utilizadores SET Palavra_chave=?,must_change_password=1 WHERE NII=?", (pw_e, nii_e))
                        conn.commit()
                _audit(current_user().get('nii','admin'), "editar_utilizador", f"NII={nii_e}")
                flash("Utilizador atualizado.", "ok")
            except Exception as ex:
                flash(f"Erro: {ex}", "error")
        elif acao == 'editar_contactos':
            nii_e = request.form.get('nii','')
            email_e = request.form.get('email','').strip()
            tel_e = request.form.get('telemovel','').strip()
            try:
                with sr.db() as conn:
                    conn.execute("UPDATE utilizadores SET email=?, telemovel=? WHERE NII=?",
                                 (email_e or None, tel_e or None, nii_e))
                    conn.commit()
                flash("Contactos atualizados.", "ok")
            except Exception as ex:
                flash(f"Erro: {ex}", "error")
        elif acao == 'reset_pw':
            nii = request.form.get('nii','')
            ok, nova_pw = _reset_pw(nii)
            flash(f"Password resetada. Temporária: {nova_pw}" if ok else nova_pw, "ok" if ok else "error")
        elif acao == 'desbloquear':
            _unblock_user(request.form.get('nii',''))
            flash("Desbloqueado.", "ok")
        elif acao == 'eliminar':
            nii = request.form.get('nii','')
            eliminado = _eliminar_utilizador(nii)
            if eliminado:
                _audit(current_user().get('nii','admin'), "eliminar_utilizador", f"NII={nii}")
            flash(f"'{nii}' eliminado." if eliminado else "NII não encontrado.", "ok")
        return redirect(url_for('admin_utilizadores'))

    q = request.args.get('q','').strip()
    ano_f = request.args.get('ano','all')
    edit_nii = request.args.get('edit_contactos','')
    with sr.db() as conn:
        sql = "SELECT id,NII,NI,Nome_completo,ano,perfil,locked_until,email,telemovel FROM utilizadores WHERE 1=1"
        args = []
        if q: sql += " AND Nome_completo LIKE ?"; args.append(f"%{q}%")
        if ano_f != 'all': sql += " AND ano=?"; args.append(ano_f)
        sql += " ORDER BY ano, NI"
        rows = [dict(r) for r in conn.execute(sql, args).fetchall()]
    
    edit_user_nii = request.args.get('edit_user','')
    edit_user_row = next((r for r in rows if r['NII'] == edit_user_nii), None)
    edit_row = next((r for r in rows if r['NII'] == edit_nii), None)

    def action_btns(r):
        ne = esc(r['NII'])
        b  = f'<a class="btn btn-gold btn-sm" href="?edit_user={ne}" title="Editar utilizador">✏️ Editar</a>'
        b += f'<a class="btn btn-ghost btn-sm" href="?edit_contactos={ne}" title="Editar email/telemóvel">✉️</a>'
        if r.get('locked_until'):
            b += f'<form method="post" style="display:inline">{csrf_input()}<input type="hidden" name="acao" value="desbloquear"><input type="hidden" name="nii" value="{ne}"><button class="btn btn-ghost btn-sm">🔓</button></form>'
        b += f'<form method="post" style="display:inline" onsubmit="return confirm(\'Eliminar {ne}?\');">{csrf_input()}<input type="hidden" name="acao" value="eliminar"><input type="hidden" name="nii" value="{ne}"><button class="btn btn-danger btn-sm">🗑</button></form>'
        return b

    rows_html = ''.join(f"""
      <tr{'style="background:#f0f7ff"' if r['NII']==edit_user_nii or r['NII']==edit_nii else ''}>
        <td class="small text-muted">{esc(r['NII'])}</td><td>{esc(r['NI'])}</td>
        <td><strong>{esc(r['Nome_completo'])}</strong></td>
        <td class="center">{esc(r['ano'])}</td>
        <td><span class="badge badge-info">{esc(r['perfil'])}</span></td>
        <td class="small text-muted">{esc(r.get('email') or '—')}</td>
        <td class="small text-muted">{esc(r.get('telemovel') or '—')}</td>
        <td>{'<span class="badge badge-warn">Bloqueado</span>' if r.get('locked_until') else '<span class="badge badge-ok">Ativo</span>'}</td>
        <td>{action_btns(r)}</td>
      </tr>""" for r in rows)

    edit_user_form = ''
    if edit_user_row:
        er = edit_user_row
        perfil_opts = ''.join(f'<option value="{p}" {"selected" if er["perfil"]==p else ""}>{p}</option>' for p in ['aluno','oficialdia','cozinha','cmd','admin'])
        edit_user_form = f'''
        <div class="card" style="border:1.5px solid var(--primary);max-width:640px">
          <div class="card-title">✏️ Editar Utilizador — {esc(er["Nome_completo"])}</div>
          <form method="post">
            {csrf_input()}
            <input type="hidden" name="acao" value="editar_user">
            <input type="hidden" name="nii" value="{esc(er["NII"])}">
            <div class="grid grid-3">
              <div class="form-group"><label>Nome completo</label><input type="text" name="nome" value="{esc(er["Nome_completo"])}" required></div>
              <div class="form-group"><label>NI</label><input type="text" name="ni" value="{esc(er["NI"] or "")}"></div>
              <div class="form-group"><label>Ano</label>
                <select name="ano">
                  <option value="0">0 — Concluído/Inativo</option>
                  {''.join(f'<option value="{a}" {"selected" if str(er["ano"])==str(a) else ""}>{_ano_label(a)}</option>' for a, _ in ANOS_OPCOES)}
                </select>
              </div>
              <div class="form-group"><label>Perfil</label><select name="perfil">{perfil_opts}</select></div>
              <div class="form-group"><label>Email</label><input type="email" name="email" value="{esc(er.get("email") or "")}"></div>
              <div class="form-group"><label>Telemóvel</label><input type="tel" name="telemovel" value="{esc(er.get("telemovel") or "")}"></div>
            </div>
            <div class="form-group"><label>Nova password (deixa em branco para não alterar)</label><input type="text" name="pw" placeholder="Nova password opcional..."></div>
            <div class="gap-btn">
              <button class="btn btn-ok">💾 Guardar alterações</button>
              <a class="btn btn-ghost" href="{url_for("admin_utilizadores")}">Cancelar</a>
            </div>
          </form>
        </div>'''

    edit_contactos_form = ''
    if edit_row:
        edit_contactos_form = f"""
        <div class="card" style="border:1.5px solid var(--gold);max-width:520px">
          <div class="card-title">✉️ Contactos — {esc(edit_row['Nome_completo'])}</div>
          <form method="post">
            {csrf_input()}
            <input type="hidden" name="acao" value="editar_contactos">
            <input type="hidden" name="nii" value="{esc(edit_row['NII'])}">
            <div class="grid grid-2">
              <div class="form-group"><label>Email</label>
                <input type="email" name="email" value="{esc(edit_row.get('email') or '')}" placeholder="nome@exemplo.pt">
              </div>
              <div class="form-group"><label>Telemóvel</label>
                <input type="tel" name="telemovel" value="{esc(edit_row.get('telemovel') or '')}" placeholder="+351XXXXXXXXX">
              </div>
            </div>
            <div class="gap-btn">
              <button class="btn btn-ok">💾 Guardar contactos</button>
              <a class="btn btn-ghost" href="{url_for('admin_utilizadores')}">Cancelar</a>
            </div>
          </form>
          <div style="margin-top:.6rem;font-size:.78rem;color:var(--muted)">
            📌 O email e telemóvel são usados para envio de notificações de prazo.
          </div>
        </div>"""

    content = f"""
    <div class="container">
      <div class="page-header">{_back_btn(url_for('admin_home'))}<div class="page-title">👥 Utilizadores ({len(rows)})</div>
        <a class="btn btn-primary btn-sm" href="{url_for('admin_importar_csv')}">📥 Importar CSV</a>
      </div>
      {edit_user_form}
      {edit_contactos_form}
      <div class="card">
        <form method="get" style="display:flex;gap:.5rem;flex-wrap:wrap">
          <input type="text" name="q" placeholder="Pesquisar por nome..." value="{esc(q)}" style="flex:1;min-width:200px">
          <select name="ano" style="width:auto">
            <option value="all" {"selected" if ano_f=="all" else ""}>Todos os anos</option>
            {''.join(f"<option value='{a}' {'selected' if ano_f==str(a) else ''}>{_ano_label(a)}</option>" for a, _ in ANOS_OPCOES)}
          </select>
          <button class="btn btn-primary">Filtrar</button>
        </form>
      </div>
      <div class="card">
        <div class="card-title">🆕 Criar utilizador</div>
        <form method="post">
          {csrf_input()}
          <input type="hidden" name="acao" value="criar">
          <div class="grid grid-3">
            <div class="form-group"><label>NII</label><input type="text" name="nii" required></div>
            <div class="form-group"><label>NI</label><input type="text" name="ni" required></div>
            <div class="form-group"><label>Nome completo</label><input type="text" name="nome" required></div>
            <div class="form-group"><label>Ano</label>
              <select name="ano" required>
                {''.join(f"<option value='{a}'>{_ano_label(a)}</option>" for a, _ in ANOS_OPCOES)}
              </select>
            </div>
            <div class="form-group"><label>Perfil</label>
              <select name="perfil">{''.join(f"<option value='{p}'>{p}</option>" for p in ['aluno','oficialdia','cozinha','cmd','admin'])}</select>
            </div>
            <div class="form-group"><label>Password inicial</label><input type="text" name="pw" required></div>
          </div>
          <button class="btn btn-ok">Criar</button>
        </form>
      </div>
      <div class="card">
        <div class="card-title">Lista
          <span style="font-size:.74rem;font-weight:400;color:var(--muted);margin-left:.5rem">Clica em ✉️ para editar email/telemóvel (necessário para notificações)</span>
        </div>
        <div class="table-wrap">
          <table>
            <thead><tr><th>NII</th><th>NI</th><th>Nome</th><th>Ano</th><th>Perfil</th><th>Email</th><th>Telemóvel</th><th>Estado</th><th>Ações</th></tr></thead>
            <tbody>{rows_html or '<tr><td colspan="9" class="text-muted center" style="padding:1.5rem">Sem utilizadores.</td></tr>'}</tbody>
          </table>
        </div>
      </div>
    </div>"""
    return render(content)


@app.route('/admin/importar-csv', methods=['GET','POST'])
@role_required('admin')
def admin_importar_csv():
    """Importação de alunos em massa via CSV.

    Formato esperado (com ou sem cabeçalho):
        NII, NI, Nome_completo, ano
    Colunas opcionais na mesma linha: perfil, password
    Se perfil omitido → 'aluno'
    Se password omitida → NII do aluno (deve alterar no 1.º login)
    """
    import csv, io

    resultado = None

    if request.method == 'POST':
        acao = request.form.get('acao','')

        if acao == 'preview':
            f = request.files.get('csvfile')
            if not f or not f.filename:
                flash("Nenhum ficheiro selecionado.", "error")
                return redirect(url_for('admin_importar_csv'))

            raw = f.read().decode('utf-8-sig', errors='replace')
            linhas = list(csv.reader(io.StringIO(raw)))

            # Detectar cabeçalho (primeira célula da 1.ª linha)
            if linhas and linhas[0] and linhas[0][0].strip().upper() in ('NII','#','ID','NUM'):
                linhas = linhas[1:]

            preview_rows = []
            erros = []
            with sr.db() as conn:
                existentes = {r['NII'] for r in conn.execute("SELECT NII FROM utilizadores").fetchall()}

            for i, row in enumerate(linhas, 1):
                row = [c.strip() for c in row]
                if not any(row):
                    continue
                if len(row) < 4:
                    erros.append(f"Linha {i}: colunas insuficientes ({len(row)} — esperadas: NII, NI, Nome, Ano).")
                    continue
                nii, ni, nome, ano_raw = row[0], row[1], row[2], row[3]
                perfil = row[4] if len(row) > 4 and row[4] else 'aluno'
                pw     = row[5] if len(row) > 5 and row[5] else nii

                if not nii or not ni or not nome:
                    erros.append(f"Linha {i}: NII, NI e Nome são obrigatórios.")
                    continue
                try:
                    ano = int(ano_raw)
                    if ano not in [a for a, _ in ANOS_OPCOES]:
                        erros.append(f"Linha {i} ({nii}): ano inválido '{ano_raw}'. Usa 1–8.")
                        continue
                except ValueError:
                    erros.append(f"Linha {i} ({nii}): ano não é número ('{ano_raw}').")
                    continue

                duplicado = nii in existentes
                preview_rows.append({
                    'linha': i, 'nii': nii, 'ni': ni, 'nome': nome,
                    'ano': ano, 'perfil': perfil, 'pw': pw,
                    'duplicado': duplicado,
                })

            resultado = {'preview': preview_rows, 'erros': erros, 'raw': raw}

        elif acao == 'confirmar':
            raw = request.form.get('raw_csv','')
            linhas = list(csv.reader(io.StringIO(raw)))
            if linhas and linhas[0] and linhas[0][0].strip().upper() in ('NII','#','ID','NUM'):
                linhas = linhas[1:]

            criados = 0
            ignorados = 0
            erros_conf = []
            with sr.db() as conn:
                existentes = {r['NII'] for r in conn.execute("SELECT NII FROM utilizadores").fetchall()}

            for i, row in enumerate(linhas, 1):
                row = [c.strip() for c in row]
                if not any(row) or len(row) < 4:
                    continue
                nii, ni, nome, ano_raw = row[0], row[1], row[2], row[3]
                perfil = row[4] if len(row) > 4 and row[4] else 'aluno'
                pw     = row[5] if len(row) > 5 and row[5] else nii

                if nii in existentes:
                    ignorados += 1
                    continue

                try:
                    ano = int(ano_raw)
                except ValueError:
                    erros_conf.append(f"Linha {i} ({nii}): ano inválido.")
                    continue

                ok, err = _criar_utilizador(nii, ni, nome, str(ano), perfil, pw)
                if ok:
                    criados += 1
                    existentes.add(nii)
                else:
                    erros_conf.append(f"Linha {i} ({nii}): {err}")

            _audit(current_user().get('nii','admin'), 'importar_csv',
                   f"criados={criados} ignorados={ignorados} erros={len(erros_conf)}")
            msgs = [f"✅ {criados} aluno(s) criado(s)."]
            if ignorados:
                msgs.append(f"⚠️ {ignorados} ignorado(s) (NII já existe).")
            if erros_conf:
                msgs.append(f"❌ {len(erros_conf)} erro(s): " + "; ".join(erros_conf[:5]))
            flash(" ".join(msgs), "ok" if not erros_conf else "warn")
            return redirect(url_for('admin_utilizadores'))

    # ── Render ───────────────────────────────────────────────────────────────
    preview_html = ''
    erros_html   = ''
    hidden_raw   = ''

    if resultado:
        rows_prev = resultado['preview']
        erros_list = resultado['erros']

        if erros_list:
            erros_html = '<div class="alert alert-warn">⚠️ <strong>Avisos de parsing:</strong><ul style="margin:.4rem 0 0 1.2rem">' + \
                         ''.join(f'<li>{esc(e)}</li>' for e in erros_list) + '</ul></div>'

        novos   = [r for r in rows_prev if not r['duplicado']]
        dupls   = [r for r in rows_prev if r['duplicado']]
        raw_csv_escaped = esc(resultado['raw'])
        hidden_raw = f'<input type="hidden" name="raw_csv" value="{raw_csv_escaped}">'

        def _ano_badge(a):
            return f'<span class="badge badge-info">{_ano_label(a)}</span>'

        trs = ''.join(f"""
          <tr style="{'background:#f0fff4' if not r['duplicado'] else 'background:#fff9e6;opacity:.7'}">
            <td class="small text-muted">{r['linha']}</td>
            <td><strong>{esc(r['nii'])}</strong></td>
            <td>{esc(r['ni'])}</td>
            <td>{esc(r['nome'])}</td>
            <td>{_ano_badge(r['ano'])}</td>
            <td><span class="badge badge-{'info' if r['perfil']=='aluno' else 'warn'}">{esc(r['perfil'])}</span></td>
            <td class="small text-muted">{esc(r['pw']) if not r['duplicado'] else '—'}</td>
            <td>{'<span class="badge badge-warn">⚠️ Já existe</span>' if r['duplicado'] else '<span class="badge badge-ok">✅ Novo</span>'}</td>
          </tr>""" for r in rows_prev)

        sumario = f'<div class="alert alert-info" style="margin-bottom:.5rem">'\
                  f'📊 <strong>{len(novos)} a criar</strong>'\
                  f'{f", {len(dupls)} ignorados (já existem)" if dupls else ""}'\
                  f', {len(erros_list)} avisos de formato.</div>'

        confirmar_btn = f'''
        <form method="post" style="margin-top:.9rem">
          {csrf_input()}
          <input type="hidden" name="acao" value="confirmar">
          {hidden_raw}
          <button class="btn btn-ok" {'disabled' if not novos else ''}>
            ✅ Confirmar e importar {len(novos)} aluno(s)
          </button>
          <a class="btn btn-ghost" href="{url_for('admin_importar_csv')}" style="margin-left:.5rem">↩️ Cancelar</a>
        </form>''' if novos else f'<div class="alert alert-warn">Nenhum aluno novo para importar.</div>'

        preview_html = f'''
        <div class="card">
          <div class="card-title">👁️ Pré-visualização ({len(rows_prev)} linha(s))</div>
          {sumario}
          {erros_html}
          <div class="table-wrap">
            <table>
              <thead><tr><th>#</th><th>NII</th><th>NI</th><th>Nome</th><th>Ano</th><th>Perfil</th><th>Password inicial</th><th>Estado</th></tr></thead>
              <tbody>{trs}</tbody>
            </table>
          </div>
          {confirmar_btn}
        </div>'''

    content = f"""
    <div class="container">
      <div class="page-header">
        {_back_btn(url_for('admin_utilizadores'))}
        <div class="page-title">📥 Importar Alunos via CSV</div>
      </div>

      <div class="card" style="max-width:680px">
        <div class="card-title">📋 Instruções</div>
        <p style="font-size:.85rem;color:var(--muted);line-height:1.6">
          Carrega um ficheiro <strong>.csv</strong> com uma linha por aluno. Colunas aceites:<br>
          <code style="background:#f0f4f8;padding:.1rem .4rem;border-radius:4px;font-size:.83rem">NII, NI, Nome_completo, Ano [, Perfil] [, Password]</code><br><br>
          • <strong>Perfil</strong> omitido → <code>aluno</code><br>
          • <strong>Password</strong> omitida → igual ao NII (deve alterar no 1.º login)<br>
          • <strong>Ano</strong>: 1–6 para anos curriculares, 7 para CFBO, 8 para CFCO<br>
          • Linhas com NII já existente são ignoradas (sem sobrescrever)<br>
          • A 1.ª linha é ignorada se começar por <code>NII</code>, <code>#</code>, <code>ID</code> ou <code>NUM</code>
        </p>
        <div class="alert alert-info" style="margin-top:.8rem;font-size:.82rem">
          💡 <strong>Exemplo de CSV:</strong><br>
          <pre style="margin:.4rem 0 0;font-size:.78rem;background:#f0f4f8;padding:.5rem;border-radius:6px;overflow-x:auto">NII,NI,Nome_completo,Ano,Perfil,Password
20240001,A001,João Silva,1,aluno,senha123
20240002,A002,Maria Costa,1
20240003,A003,Pedro Santos,2</pre>
        </div>
      </div>

      <div class="card" style="max-width:680px">
        <div class="card-title">📤 Carregar ficheiro</div>
        <form method="post" enctype="multipart/form-data">
          {csrf_input()}
          <input type="hidden" name="acao" value="preview">
          <div class="form-group">
            <label>Ficheiro CSV</label>
            <input type="file" name="csvfile" accept=".csv,.txt" required style="padding:.42rem .6rem">
          </div>
          <button class="btn btn-primary">🔍 Pré-visualizar</button>
        </form>
      </div>

      {preview_html}
    </div>"""
    return render(content)


@app.route('/admin/menus', methods=['GET','POST'])
@role_required('cozinha','admin','oficialdia')
def admin_menus():
    d_str = request.args.get('d', date.today().isoformat())
    dt = _parse_date(d_str)

    if request.method == 'POST':
        d_save = request.form.get('data', dt.isoformat())
        campos = ['pequeno_almoco','lanche','almoco_normal','almoco_veg','almoco_dieta','jantar_normal','jantar_veg','jantar_dieta']
        vals = [request.form.get(c,'').strip() or None for c in campos]
        with sr.db() as conn:
            conn.execute("""INSERT OR REPLACE INTO menus_diarios
                (data,pequeno_almoco,lanche,almoco_normal,almoco_veg,almoco_dieta,jantar_normal,jantar_veg,jantar_dieta)
                VALUES (?,?,?,?,?,?,?,?,?)""", (d_save, *vals))
            for ref in ['Pequeno Almoço','Lanche','Almoço','Jantar']:
                cap_key = 'cap_' + ref.lower().replace(' ','_').replace('ç','c').replace('ã','a')
                cap_val = request.form.get(cap_key,'').strip()
                if cap_val:
                    try:
                        cap_int = int(cap_val)
                        if cap_int < 0:
                            conn.execute("DELETE FROM capacidade_refeicao WHERE data=? AND refeicao=?", (d_save, ref))
                        else:
                            conn.execute("INSERT OR REPLACE INTO capacidade_refeicao(data,refeicao,max_total) VALUES (?,?,?)", (d_save, ref, cap_int))
                    except ValueError:
                        pass
            conn.commit()
        flash("Menu e capacidades guardados.", "ok")
        return redirect(url_for('admin_menus', d=d_save))

    with sr.db() as conn:
        menu = conn.execute("SELECT * FROM menus_diarios WHERE data=?", (dt.isoformat(),)).fetchone()
        caps = {r['refeicao']: r['max_total'] for r in conn.execute("SELECT refeicao,max_total FROM capacidade_refeicao WHERE data=?", (dt.isoformat(),))}

    def mv(k): return esc(menu[k] if menu and menu[k] else '')
    def cv(ref): return caps.get(ref,'')
    back_url = url_for('painel_dia') if current_user().get('perfil') in ('cozinha','oficialdia') else url_for('admin_home')

    content = f"""
    <div class="container">
      <div class="page-header">{_back_btn(back_url)}<div class="page-title">🍽️ Menus & Capacidade</div></div>
      <div class="card" style="max-width:640px">
        <form method="post">
          {csrf_input()}
          <div class="form-group"><label>Data</label><input type="date" name="data" value="{dt.isoformat()}" required></div>
          <div class="card-title" style="margin:.7rem 0 .55rem">Ementa</div>
          <div class="grid grid-2">
            <div class="form-group"><label>☕ Pequeno Almoço</label><input type="text" name="pequeno_almoco" value="{mv('pequeno_almoco')}"></div>
            <div class="form-group"><label>🥐 Lanche</label><input type="text" name="lanche" value="{mv('lanche')}"></div>
            <div class="form-group"><label>🍽️ Almoço Normal</label><input type="text" name="almoco_normal" value="{mv('almoco_normal')}"></div>
            <div class="form-group"><label>🥗 Almoço Vegetariano</label><input type="text" name="almoco_veg" value="{mv('almoco_veg')}"></div>
            <div class="form-group"><label>🥙 Almoço Dieta</label><input type="text" name="almoco_dieta" value="{mv('almoco_dieta')}"></div>
            <div class="form-group"><label>🌙 Jantar Normal</label><input type="text" name="jantar_normal" value="{mv('jantar_normal')}"></div>
            <div class="form-group"><label>🌿 Jantar Vegetariano</label><input type="text" name="jantar_veg" value="{mv('jantar_veg')}"></div>
            <div class="form-group"><label>🥗 Jantar Dieta</label><input type="text" name="jantar_dieta" value="{mv('jantar_dieta')}"></div>
          </div>
          <div class="card-title" style="margin:.7rem 0 .55rem">Capacidades <span class="text-muted small">(-1 ou vazio = sem limite)</span></div>
          <div class="grid grid-2">
            <div class="form-group"><label>PA</label><input type="number" name="cap_pequeno_almoco" value="{cv('Pequeno Almoço')}"></div>
            <div class="form-group"><label>Lanche</label><input type="number" name="cap_lanche" value="{cv('Lanche')}"></div>
            <div class="form-group"><label>Almoço</label><input type="number" name="cap_almoco" value="{cv('Almoço')}"></div>
            <div class="form-group"><label>Jantar</label><input type="number" name="cap_jantar" value="{cv('Jantar')}"></div>
          </div>
          <hr>
          <div class="gap-btn"><button class="btn btn-ok">💾 Guardar</button><a class="btn btn-ghost" href="{back_url}">Cancelar</a></div>
        </form>
      </div>
    </div>"""
    return render(content)


@app.route('/admin/log')
@role_required('admin')
def admin_log():
    # ── Filtros ──────────────────────────────────────────────────────────────
    q_nome      = request.args.get('q_nome','').strip()
    q_por       = request.args.get('q_por','').strip()
    q_campo     = request.args.get('q_campo','').strip()
    q_d0        = request.args.get('d0','').strip()
    q_d1        = request.args.get('d1','').strip()
    q_limit_str = request.args.get('limite','500')
    try:
        q_limit = min(int(q_limit_str), 5000)
    except Exception:
        q_limit = 500

    sql = """SELECT l.id, l.alterado_em, u.NII, u.Nome_completo, u.ano,
                    l.data_refeicao, l.campo, l.valor_antes, l.valor_depois, l.alterado_por
             FROM refeicoes_log l LEFT JOIN utilizadores u ON u.id=l.utilizador_id
             WHERE 1=1"""
    args = []

    if q_nome:
        sql += " AND u.Nome_completo LIKE ?"
        args.append(f"%{q_nome}%")
    if q_por:
        sql += " AND l.alterado_por LIKE ?"
        args.append(f"%{q_por}%")
    if q_campo:
        sql += " AND l.campo=?"
        args.append(q_campo)
    if q_d0:
        sql += " AND l.data_refeicao >= ?"
        args.append(q_d0)
    if q_d1:
        sql += " AND l.data_refeicao <= ?"
        args.append(q_d1)

    sql += " ORDER BY l.alterado_em DESC LIMIT ?"
    args.append(q_limit)

    with sr.db() as conn:
        rows = conn.execute(sql, args).fetchall()
        total_logs = conn.execute("SELECT COUNT(*) c FROM refeicoes_log").fetchone()['c']
        campos_disponiveis = [r[0] for r in conn.execute(
            "SELECT DISTINCT campo FROM refeicoes_log ORDER BY campo").fetchall()]

    # Paginação info
    mostrando = len(rows)

    campos_opts = '<option value="">Todos os campos</option>' + ''.join(
        f'<option value="{c}" {"selected" if q_campo==c else ""}>{c}</option>'
        for c in campos_disponiveis)

    limites_opts = ''.join(
        f'<option value="{n}" {"selected" if str(q_limit)==str(n) else ""}>{n} linhas</option>'
        for n in [100,200,500,1000,2000,5000])

    rows_html = ''.join(f"""
      <tr>
        <td class="small" style="white-space:nowrap">{(r['alterado_em'] or '')[:16]}</td>
        <td>
          <span style="font-weight:600">{esc(r['Nome_completo'] or r['NII'] or '—')}</span>
          {'<br><span class="text-muted small">'+esc(r["NII"])+(f' · {r["ano"]}º ano' if r["ano"] else '')+'</span>' if r['Nome_completo'] else ''}
        </td>
        <td style="white-space:nowrap">{r['data_refeicao']}</td>
        <td><span class="badge badge-info">{esc(r['campo'])}</span></td>
        <td class="small text-muted">{esc(r['valor_antes'] or '—')}</td>
        <td class="small" style="color:var(--ok);font-weight:600">{esc(r['valor_depois'] or '—')}</td>
        <td class="small text-muted">{esc(r['alterado_por'] or '—')}</td>
      </tr>""" for r in rows)

    filtros_ativos = any([q_nome, q_por, q_campo, q_d0, q_d1])
    limpar_btn = (f'<a class="btn btn-ghost btn-sm" href="{url_for("admin_log")}">✕ Limpar filtros</a>'
                  if filtros_ativos else '')

    content = f"""
    <div class="container">
      <div class="page-header">{_back_btn(url_for('admin_home'))}<div class="page-title">📜 Log de Alterações</div></div>
      <div class="card">
        <div class="card-title">🔍 Filtros
          <span class="badge badge-muted" style="margin-left:.5rem;font-size:.72rem">{total_logs} registos totais</span>
          {f'<span class="badge badge-warn" style="margin-left:.3rem;font-size:.72rem">A mostrar {mostrando}</span>' if filtros_ativos else ''}
        </div>
        <form method="get" style="display:flex;flex-wrap:wrap;gap:.5rem;align-items:flex-end">
          <div class="form-group" style="margin:0;min-width:180px;flex:1">
            <label style="font-size:.77rem">👤 Utilizador (nome)</label>
            <input type="text" name="q_nome" value="{esc(q_nome)}" placeholder="Nome do aluno..." style="font-size:.82rem">
          </div>
          <div class="form-group" style="margin:0;min-width:140px">
            <label style="font-size:.77rem">✏️ Alterado por (NII)</label>
            <input type="text" name="q_por" value="{esc(q_por)}" placeholder="NII..." style="font-size:.82rem">
          </div>
          <div class="form-group" style="margin:0;min-width:140px">
            <label style="font-size:.77rem">🏷 Campo</label>
            <select name="q_campo" style="font-size:.82rem">{campos_opts}</select>
          </div>
          <div class="form-group" style="margin:0">
            <label style="font-size:.77rem">📅 Data ref. de</label>
            <input type="date" name="d0" value="{esc(q_d0)}" style="width:auto;font-size:.82rem">
          </div>
          <div class="form-group" style="margin:0">
            <label style="font-size:.77rem">📅 até</label>
            <input type="date" name="d1" value="{esc(q_d1)}" style="width:auto;font-size:.82rem">
          </div>
          <div class="form-group" style="margin:0">
            <label style="font-size:.77rem">📊 Máx. linhas</label>
            <select name="limite" style="width:auto;font-size:.82rem">{limites_opts}</select>
          </div>
          <button class="btn btn-primary btn-sm" style="align-self:flex-end">🔍 Filtrar</button>
          {limpar_btn}
        </form>
      </div>
      <div class="card">
        <div class="card-title">Resultados
          <span class="badge badge-info" style="margin-left:.5rem;font-size:.72rem;font-weight:400">
            {mostrando} {"(filtrado)" if filtros_ativos else "mais recentes"}
          </span>
        </div>
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Quando</th>
                <th>Utilizador</th>
                <th>Data Ref.</th>
                <th>Campo</th>
                <th>Antes</th>
                <th>Depois</th>
                <th>Por (NII)</th>
              </tr>
            </thead>
            <tbody>{rows_html or '<tr><td colspan="7" class="text-muted center" style="padding:2rem">Sem registos com estes filtros.</td></tr>'}</tbody>
          </table>
        </div>
        {f'<div style="margin-top:.6rem;font-size:.8rem;color:var(--muted)">💡 A mostrar os primeiros {q_limit} resultados. Usa os filtros para refinar.</div>' if mostrando == q_limit else ''}
      </div>
    </div>"""
    return render(content)


@app.route('/admin/auditoria')
@role_required('admin')
def admin_audit():
    """Registo de ações administrativas (logins, criação/edição de utilizadores, etc.)."""
    limite = min(int(request.args.get('limite', 500)), 5000)
    q_actor = request.args.get('actor', '').strip()
    q_action = request.args.get('action', '').strip()

    try:
        with sr.db() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS admin_audit_log (
                id INTEGER PRIMARY KEY,
                ts TEXT DEFAULT (datetime('now','localtime')),
                actor TEXT NOT NULL,
                action TEXT NOT NULL,
                detail TEXT
            )""")
            sql = "SELECT id,ts,actor,action,detail FROM admin_audit_log WHERE 1=1"
            args: list = []
            if q_actor:
                sql += " AND actor LIKE ?"
                args.append(f"%{q_actor}%")
            if q_action:
                sql += " AND action LIKE ?"
                args.append(f"%{q_action}%")
            sql += " ORDER BY id DESC LIMIT ?"
            args.append(limite)
            rows = [dict(r) for r in conn.execute(sql, args).fetchall()]
            total = conn.execute("SELECT COUNT(*) c FROM admin_audit_log").fetchone()['c']
    except Exception as exc:
        app.logger.error(f"admin_audit: {exc}")
        rows, total = [], 0

    ACTION_ICONS = {
        'login': '🔑', 'criar_utilizador': '➕', 'editar_utilizador': '✏️',
        'reset_password': '🔄', 'eliminar_utilizador': '🗑️',
    }

    rows_html = ''.join(f"""
      <tr>
        <td class="small text-muted" style="white-space:nowrap">{esc(r['ts'] or '')[:16]}</td>
        <td><strong>{esc(r['actor'])}</strong></td>
        <td>{ACTION_ICONS.get(r['action'], '📌')} {esc(r['action'])}</td>
        <td class="small text-muted">{esc(r.get('detail') or '—')}</td>
      </tr>""" for r in rows)

    limites_opts = ''.join(
        f'<option value="{n}" {"selected" if str(limite)==str(n) else ""}>{n}</option>'
        for n in [100, 200, 500, 1000, 2000, 5000])

    content = f"""
    <div class="container">
      <div class="page-header">{_back_btn(url_for('admin_home'))}<div class="page-title">🔐 Auditoria de Ações</div></div>
      <div class="card">
        <div class="card-title">🔍 Filtros
          <span class="badge badge-muted" style="margin-left:.5rem;font-size:.72rem">{total} entradas</span>
        </div>
        <form method="get" style="display:flex;gap:.5rem;flex-wrap:wrap;align-items:flex-end">
          <div class="form-group" style="margin:0;min-width:160px;flex:1">
            <label style="font-size:.77rem">👤 Actor (NII)</label>
            <input type="text" name="actor" value="{esc(q_actor)}" placeholder="NII...">
          </div>
          <div class="form-group" style="margin:0;min-width:160px;flex:1">
            <label style="font-size:.77rem">📌 Ação</label>
            <input type="text" name="action" value="{esc(q_action)}" placeholder="ex: login, criar_utilizador...">
          </div>
          <div class="form-group" style="margin:0">
            <label style="font-size:.77rem">Máx.</label>
            <select name="limite" style="width:auto">{limites_opts}</select>
          </div>
          <button class="btn btn-primary btn-sm">🔍 Filtrar</button>
          <a class="btn btn-ghost btn-sm" href="{url_for('admin_audit')}">✕ Limpar</a>
        </form>
      </div>
      <div class="card">
        <div class="table-wrap">
          <table>
            <thead><tr><th>Quando</th><th>Actor</th><th>Ação</th><th>Detalhe</th></tr></thead>
            <tbody>{rows_html or '<tr><td colspan="4" class="text-muted center" style="padding:2rem">Sem registos.</td></tr>'}</tbody>
          </table>
        </div>
      </div>
    </div>"""
    return render(content)


@app.route('/admin/calendario', methods=['GET','POST'])
@role_required('admin','cmd')
def admin_calendario():
    u = current_user()
    if request.method == 'POST':
        acao = request.form.get('acao','')
        if acao == 'adicionar':
            try:
                dia_de  = request.form.get('dia_de','').strip()
                dia_ate = request.form.get('dia_ate','').strip() or dia_de
                tipo    = request.form.get('tipo','normal')
                nota    = request.form.get('nota','') or None
                if not dia_de:
                    flash("Data de início obrigatória.", "error")
                else:
                    d_de  = datetime.strptime(dia_de,  "%Y-%m-%d").date()
                    d_ate = datetime.strptime(dia_ate, "%Y-%m-%d").date()
                    if d_de > d_ate:
                        flash("A data de início não pode ser posterior à data de fim.", "error")
                    else:
                        count = 0
                        with sr.db() as conn:
                            cur = d_de
                            while cur <= d_ate:
                                conn.execute("INSERT OR REPLACE INTO calendario_operacional(data,tipo,nota) VALUES (?,?,?)",
                                             (cur.isoformat(), tipo, nota))
                                cur += timedelta(days=1)
                                count += 1
                            conn.commit()
                        n_dias = (d_ate - d_de).days + 1
                        flash(f"{count} dia(s) adicionado(s) ao calendário ({dia_de} → {dia_ate}).", "ok")
            except ValueError as e:
                flash(f"Data inválida: {e}", "error")
            except Exception as e:
                flash(str(e), "error")
        elif acao == 'remover':
            with sr.db() as conn:
                conn.execute("DELETE FROM calendario_operacional WHERE data=?", (request.form.get('dia',''),))
                conn.commit()
            flash("Removido.", "ok")
        return redirect(url_for('admin_calendario'))

    hoje = date.today()
    with sr.db() as conn:
        entradas = conn.execute("SELECT data,tipo,nota FROM calendario_operacional WHERE data >= ? ORDER BY data LIMIT 90",
                                (hoje.isoformat(),)).fetchall()

    TIPOS = ['normal','fim_semana','feriado','exercicio','outro']
    ICONES = {'normal':'✅','fim_semana':'🔵','feriado':'🔴','exercicio':'🟡','outro':'⚪'}

    rows_html = ''.join(f"""
      <tr><td>{r['data']}</td><td>{ICONES.get(r['tipo'],'⚪')} {esc(r['tipo'])}</td><td>{esc(r['nota'] or '—')}</td>
      <td><form method="post" style="display:inline">{csrf_input()}<input type="hidden" name="acao" value="remover"><input type="hidden" name="dia" value="{r['data']}"><button class="btn btn-danger btn-sm">🗑</button></form></td></tr>"""
      for r in entradas)

    back_url = url_for('admin_home') if u.get('perfil') == 'admin' else url_for('painel_dia')
    content = f"""
    <div class="container">
      <div class="page-header">{_back_btn(back_url)}<div class="page-title">📅 Calendário Operacional</div></div>
      <div class="card">
        <div class="card-title">Adicionar / atualizar período</div>
        <div class="alert alert-info" style="margin-bottom:.8rem">
          💡 Para um único dia, preenche apenas a <strong>Data de início</strong> (ou coloca a mesma data nos dois campos).
          Para um período, preenche ambas as datas — todos os dias do intervalo serão atualizados.
        </div>
        <form method="post">
          {csrf_input()}
          <input type="hidden" name="acao" value="adicionar">
          <div class="grid grid-2" style="max-width:520px">
            <div class="form-group"><label>📅 Data de início</label><input type="date" name="dia_de" required value="{hoje.isoformat()}"></div>
            <div class="form-group"><label>📅 Data de fim <span class="text-muted small">(inclusive)</span></label><input type="date" name="dia_ate" value="{hoje.isoformat()}"></div>
          </div>
          <div class="grid grid-2" style="max-width:520px">
            <div class="form-group"><label>Tipo</label>
              <select name="tipo">{''.join(f"<option value='{t}'>{ICONES.get(t,'')} {t}</option>" for t in TIPOS)}</select>
            </div>
            <div class="form-group"><label>Nota</label><input type="text" name="nota" placeholder="ex: Natal, Exercício..."></div>
          </div>
          <button class="btn btn-ok">💾 Guardar</button>
        </form>
      </div>
      <div class="card">
        <div class="card-title">Próximas entradas (até 90 dias)</div>
        <div class="table-wrap">
          <table>
            <thead><tr><th>Data</th><th>Tipo</th><th>Nota</th><th></th></tr></thead>
            <tbody>{rows_html or '<tr><td colspan="4" class="text-muted center" style="padding:1.5rem">Sem entradas.</td></tr>'}</tbody>
          </table>
        </div>
      </div>
    </div>"""
    return render(content)



# ═══════════════════════════════════════════════════════════════════════════
# CALENDÁRIO PÚBLICO — Visível por todos os utilizadores
# ═══════════════════════════════════════════════════════════════════════════

@app.route('/calendario')
@login_required
def calendario_publico():
    import calendar as _cal
    u = current_user()
    hoje = date.today()
    mes_str = request.args.get('mes', hoje.strftime('%Y-%m'))
    try:
        ano_m, mes_m = int(mes_str[:4]), int(mes_str[5:7])
    except Exception:
        ano_m, mes_m = hoje.year, hoje.month

    ICONES = {'normal':'✅','fim_semana':'🔵','feriado':'🔴','exercicio':'🟡','outro':'⚪'}
    LABELS = {'normal':'Normal','fim_semana':'Fim de semana','feriado':'Feriado','exercicio':'Exercício','outro':'Outro'}
    CORES = {'normal':'#eafaf1','fim_semana':'#ebf5fb','feriado':'#fdecea','exercicio':'#fef9e7','outro':'#f8f9fa'}
    CORES_TEXT = {'normal':'#1e8449','fim_semana':'#1a5276','feriado':'#922b21','exercicio':'#9a7d0a','outro':'#6c757d'}

    ultimo_dia = _cal.monthrange(ano_m, mes_m)[1]
    d_inicio = date(ano_m, mes_m, 1)
    d_fim = date(ano_m, mes_m, ultimo_dia)
    with sr.db() as conn:
        entradas = {r['data']: dict(r) for r in conn.execute(
            "SELECT data,tipo,nota FROM calendario_operacional WHERE data>=? AND data<=?",
            (d_inicio.isoformat(), d_fim.isoformat())).fetchall()}

    cal_grid = _cal.monthcalendar(ano_m, mes_m)
    DIAS_CAB = ['Seg','Ter','Qua','Qui','Sex','Sáb','Dom']

    grid_html = ''
    for semana in cal_grid:
        grid_html += '<tr>'
        for dia_n in semana:
            if dia_n == 0:
                grid_html += '<td style="background:#f9fafb;border:1px solid var(--border);border-radius:6px"></td>'
                continue
            d_obj = date(ano_m, mes_m, dia_n)
            entrada = entradas.get(d_obj.isoformat())
            tipo = entrada['tipo'] if entrada else ('fim_semana' if d_obj.weekday() >= 5 else 'normal')
            nota = entrada['nota'] if entrada else ''
            is_hoje = d_obj == hoje
            bg = CORES.get(tipo,'#fff')
            tc = CORES_TEXT.get(tipo,'#1a2533')
            ic = ICONES.get(tipo,'✅')
            border_style = 'border:2.5px solid var(--primary)' if is_hoje else 'border:1px solid var(--border)'
            hoje_label = '<div style="font-size:.58rem;color:var(--primary);font-weight:900;text-align:center">HOJE</div>' if is_hoje else ''
            nota_html = '<div style="font-size:.62rem;color:'+tc+';margin-top:.12rem">'+esc(nota)+'</div>' if nota else ''
            grid_html += '<td style="background:'+bg+';'+border_style+';border-radius:7px;padding:.38rem;vertical-align:top">'+hoje_label+'<div style="font-weight:800;font-size:.82rem;color:'+tc+'">'+str(dia_n)+'</div><div style="font-size:.6rem">'+ic+'</div>'+nota_html+'</td>'
        grid_html += '</tr>'

    if mes_m == 1: prev_mes = f'{ano_m-1}-12'
    else: prev_mes = f'{ano_m}-{mes_m-1:02d}'
    if mes_m == 12: next_mes = f'{ano_m+1}-01'
    else: next_mes = f'{ano_m}-{mes_m+1:02d}'

    MESES_PT = ['Janeiro','Fevereiro','Março','Abril','Maio','Junho','Julho','Agosto','Setembro','Outubro','Novembro','Dezembro']
    mes_titulo = f'{MESES_PT[mes_m-1]} {ano_m}'
    perfil = u.get('perfil')
    back_url = url_for('admin_home') if perfil=='admin' else (url_for('aluno_home') if perfil=='aluno' else url_for('painel_dia'))

    legenda_html = ''.join(
        '<span style="display:inline-flex;align-items:center;gap:.3rem;font-size:.78rem">'
        '<span style="width:.75rem;height:.75rem;background:'+CORES[t]+';border:1px solid '+CORES_TEXT[t]+';border-radius:3px;display:inline-block"></span>'+LABELS[t]+'</span>'
        for t in ['normal','fim_semana','feriado','exercicio'])

    header_cells = ''.join('<th style="text-align:center;padding:.3rem;font-size:.78rem;color:var(--primary);font-weight:700">'+d+'</th>' for d in DIAS_CAB)

    admin_link = ('<a class="btn btn-primary btn-sm" href="'+url_for('admin_calendario')+'">⚙️ Gerir calendário</a>'
                  if perfil in ('admin','cmd') else
                  '<div class="alert alert-info" style="margin-top:.6rem;font-size:.82rem">📌 O calendário é gerido pelo administrador.</div>')

    c = (
        '<div class="container">'
        '<div class="page-header">'+_back_btn(back_url)+'<div class="page-title">📅 Calendário Operacional</div></div>'
        '<div class="card">'
        '<div class="flex-between" style="margin-bottom:.9rem">'
        '<a class="btn btn-ghost btn-sm" href="'+url_for('calendario_publico',mes=prev_mes)+'">← Mês anterior</a>'
        '<strong style="font-size:1.05rem">'+mes_titulo+'</strong>'
        '<a class="btn btn-ghost btn-sm" href="'+url_for('calendario_publico',mes=next_mes)+'">Mês seguinte →</a>'
        '</div>'
        '<div class="table-wrap"><table style="width:100%;border-collapse:separate;border-spacing:3px">'
        '<thead><tr>'+header_cells+'</tr></thead>'
        '<tbody>'+grid_html+'</tbody></table></div>'
        '<div style="margin-top:.8rem;display:flex;gap:.75rem;flex-wrap:wrap">'+legenda_html+'</div>'
        + admin_link +
        '</div></div>'
    )
    return render(c)


# ═══════════════════════════════════════════════════════════════════════════
# IMPRESSÃO — Mapa de refeições por ano
# ═══════════════════════════════════════════════════════════════════════════

@app.route('/imprimir/<int:ano>')
@role_required('oficialdia','cozinha','cmd','admin')
def imprimir_ano(ano):
    u = current_user()
    perfil = u.get('perfil')
    if perfil == 'cmd' and str(ano) != str(u.get('ano','')):
        abort(403)

    d_str = request.args.get('d', date.today().isoformat())
    dt = _parse_date(d_str)

    with sr.db() as conn:
        alunos = [dict(r) for r in conn.execute("""
            SELECT u.NI, u.Nome_completo,
                   r.pequeno_almoco, r.lanche, r.almoco, r.jantar_tipo, r.jantar_sai_unidade,
                   EXISTS(SELECT 1 FROM ausencias a WHERE a.utilizador_id=u.id
                          AND a.ausente_de<=? AND a.ausente_ate>=?) AS ausente
            FROM utilizadores u
            LEFT JOIN refeicoes r ON r.utilizador_id=u.id AND r.data=?
            WHERE u.ano=? ORDER BY u.NI
        """, (dt.isoformat(), dt.isoformat(), dt.isoformat(), ano)).fetchall()]

    def sim_nao(v): return '✓' if v else '–'
    rows = ''.join(f"""
        <tr{'style="background:#fff9ec"' if a['ausente'] else ''}>
          <td>{esc(a['NI'])}</td>
          <td style="text-align:left">{esc(a['Nome_completo'])}{'  🏖' if a['ausente'] else ''}</td>
          <td>{sim_nao(a['pequeno_almoco'])}</td>
          <td>{sim_nao(a['lanche'])}</td>
          <td>{(a['almoco'] or '–')[:3]}</td>
          <td>{(a['jantar_tipo'] or '–')[:3]}</td>
          <td>{'✓' if a['jantar_sai_unidade'] else '–'}</td>
        </tr>""" for a in alunos)

    t = sr.get_totais_dia(dt.isoformat(), ano)
    gerado_em = datetime.now().strftime('%d/%m/%Y %H:%M')

    html = f"""<!doctype html>
<html lang="pt"><head><meta charset="utf-8">
<title>Mapa {ano}º Ano — {dt.strftime('%d/%m/%Y')}</title>
<style>
  @page{{size:A4;margin:1.5cm}}
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{font-family:Arial,sans-serif;font-size:10pt;color:#111}}
  .header{{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:.8cm;border-bottom:2px solid #0a2d4e;padding-bottom:.4cm}}
  .header-left h1{{font-size:14pt;color:#0a2d4e;font-weight:900}}
  .header-left p{{font-size:9pt;color:#555;margin-top:.15cm}}
  .header-right{{text-align:right;font-size:8pt;color:#555}}
  table{{width:100%;border-collapse:collapse;font-size:9pt}}
  th{{background:#0a2d4e;color:#fff;padding:.25cm .3cm;text-align:center;font-weight:700}}
  td{{padding:.22cm .3cm;border:1px solid #ccc;text-align:center;vertical-align:middle}}
  tr:nth-child(even){{background:#f5f8fc}}
  tr[style]{{background:#fff9ec!important}}
  .totais{{margin-top:.6cm;font-size:9pt;border:1px solid #ccc;border-radius:6px;padding:.4cm}}
  .totais-title{{font-weight:800;font-size:10pt;color:#0a2d4e;margin-bottom:.25cm}}
  .totais-grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:.3cm}}
  .totais-item{{text-align:center;background:#f0f4f8;border-radius:4px;padding:.2cm}}
  .totais-num{{font-size:14pt;font-weight:900;color:#0a2d4e}}
  .totais-lbl{{font-size:8pt;color:#555}}
  .footer{{margin-top:.6cm;font-size:7.5pt;color:#888;text-align:right}}
  .legenda{{margin-top:.4cm;font-size:8pt;color:#555}}
  @media print{{button{{display:none!important}}}}
</style>
</head><body>
<div class="header">
  <div class="header-left">
    <h1>⚓ Escola Naval — Mapa de Refeições</h1>
    <p><strong>{ano}º Ano</strong> &nbsp;|&nbsp; {NOMES_DIAS[dt.weekday()]}, {dt.strftime('%d/%m/%Y')}</p>
  </div>
  <div class="header-right">
    Gerado em: {gerado_em}<br>
    Por: {esc(u['nome'])}
    <br><br>
    <button onclick="window.print()" style="background:#0a2d4e;color:#fff;border:none;padding:.3cm .6cm;border-radius:5px;cursor:pointer;font-size:9pt">🖨 Imprimir</button>
  </div>
</div>

<table>
  <thead><tr>
    <th style="width:1.2cm">NI</th>
    <th style="width:6cm;text-align:left">Nome</th>
    <th>PA</th><th>Lanche</th><th>Almoço</th><th>Jantar</th><th>Sai</th>
  </tr></thead>
  <tbody>{rows}</tbody>
</table>

<div class="totais">
  <div class="totais-title">📊 Totais — {ano}º Ano</div>
  <div class="totais-grid">
    <div class="totais-item"><div class="totais-num">{t['pa']}</div><div class="totais-lbl">Peq. Almoços</div></div>
    <div class="totais-item"><div class="totais-num">{t['lan']}</div><div class="totais-lbl">Lanches</div></div>
    <div class="totais-item"><div class="totais-num">{t['alm_norm']+t['alm_veg']+t['alm_dieta']}</div><div class="totais-lbl">Almoços</div></div>
    <div class="totais-item"><div class="totais-num">{t['jan_norm']+t['jan_veg']+t['jan_dieta']}</div><div class="totais-lbl">Jantares</div></div>
    <div class="totais-item"><div class="totais-num">{t['alm_norm']}</div><div class="totais-lbl">Alm. Normal</div></div>
    <div class="totais-item"><div class="totais-num">{t['alm_veg']}</div><div class="totais-lbl">Alm. Veg.</div></div>
    <div class="totais-item"><div class="totais-num">{t['alm_dieta']}</div><div class="totais-lbl">Alm. Dieta</div></div>
    <div class="totais-item"><div class="totais-num">{t['jan_sai']}</div><div class="totais-lbl">Saem após jantar</div></div>
  </div>
</div>
<div class="legenda">PA=Pequeno Almoço &nbsp;|&nbsp; Nor=Normal &nbsp;|&nbsp; Veg=Vegetariano &nbsp;|&nbsp; Die=Dieta &nbsp;|&nbsp; 🏖=Ausente</div>
<div class="footer">Escola Naval &nbsp;|&nbsp; Documento de uso interno</div>
</body></html>"""

    return Response(html, mimetype='text/html')

# ═══════════════════════════════════════════════════════════════════════════
# DASHBOARD VISUAL SEMANAL
# ═══════════════════════════════════════════════════════════════════════════

@app.route('/dashboard-semanal')
@role_required('cozinha','oficialdia','admin')
def dashboard_semanal():
    u = current_user(); perfil = u.get('perfil')
    hoje = date.today()
    segunda = hoje - timedelta(days=hoje.weekday())
    d0_str = request.args.get('d0', segunda.isoformat())
    d0 = _parse_date(d0_str); d1 = d0 + timedelta(days=6)
    prev_w = (d0 - timedelta(days=7)).isoformat()
    next_w = (d0 + timedelta(days=7)).isoformat()

    dias = []
    for i in range(7):
        di = d0 + timedelta(days=i)
        t = sr.get_totais_dia(di.isoformat())
        tipo = sr.dia_operacional(di)
        dias.append({'data': di, 't': t, 'tipo': tipo, 'is_wknd': di.weekday() >= 5})

    max_alm = max((d['t']['alm_norm']+d['t']['alm_veg']+d['t']['alm_dieta'] for d in dias), default=1) or 1
    max_jan = max((d['t']['jan_norm']+d['t']['jan_veg']+d['t']['jan_dieta'] for d in dias), default=1) or 1
    max_pa  = max((d['t']['pa'] for d in dias), default=1) or 1

    def bar(val, maximo, cor, label):
        pct = int(round(100 * val / maximo)) if maximo else 0
        return (f'<div style="display:flex;align-items:flex-end;gap:.2rem;height:80px">'
                f'<div style="width:100%;display:flex;flex-direction:column;align-items:center;justify-content:flex-end;height:100%">'
                f'<span style="font-size:.7rem;font-weight:700;color:#1a2533;margin-bottom:.15rem">{val}</span>'
                f'<div style="width:100%;background:{cor};border-radius:5px 5px 0 0;height:{max(4,pct)}%"></div>'
                f'</div></div>')

    # Chart almoços por dia
    alm_chart = ''
    jan_chart = ''
    pa_chart  = ''
    table_rows = ''
    CORES_ALM = {'Normal':'#1e8449','Vegetariano':'#2471a3','Dieta':'#d68910'}

    for d in dias:
        t = d['t']; di = d['data']
        alm = t['alm_norm']+t['alm_veg']+t['alm_dieta']
        jan = t['jan_norm']+t['jan_veg']+t['jan_dieta']
        tipo = d['tipo']; is_wk = d['is_wknd']
        off = tipo in ('feriado','exercicio')
        col_bg = '#f9fafb' if off else ('#fffdf5' if is_wk else '#fff')
        dow_col = '#c9a227' if is_wk else '#0a2d4e'

        # Stacked bar almoço
        alm_tot = alm or 0
        pn = int(round(80*(t['alm_norm']/max_alm))) if max_alm else 0
        pv = int(round(80*(t['alm_veg']/max_alm))) if max_alm else 0
        pd = int(round(80*(t['alm_dieta']/max_alm))) if max_alm else 0
        alm_chart += f"""
        <div style="display:flex;flex-direction:column;align-items:center;flex:1;background:{col_bg};padding:.4rem .2rem;border-radius:6px">
          <div style="width:100%;height:80px;display:flex;flex-direction:column;justify-content:flex-end;align-items:center">
            <span style="font-size:.68rem;font-weight:800;color:#1a2533;margin-bottom:.1rem">{alm_tot or '–'}</span>
            <div style="width:70%;display:flex;flex-direction:column;border-radius:4px 4px 0 0;overflow:hidden">
              {'<div style="height:'+str(pd)+'px;background:#d68910"></div>' if pd else ''}
              {'<div style="height:'+str(pv)+'px;background:#2471a3"></div>' if pv else ''}
              {'<div style="height:'+str(pn)+'px;background:#1e8449"></div>' if pn else ''}
            </div>
          </div>
          <div style="font-size:.68rem;font-weight:800;color:{dow_col};margin-top:.2rem">{ABREV_DIAS[di.weekday()]}</div>
          <div style="font-size:.62rem;color:#6c757d">{di.strftime('%d/%m')}</div>
        </div>"""

        # Bar jantar
        pj = int(round(80*(jan/max_jan))) if max_jan else 0
        jan_chart += f"""
        <div style="display:flex;flex-direction:column;align-items:center;flex:1;background:{col_bg};padding:.4rem .2rem;border-radius:6px">
          <div style="width:100%;height:80px;display:flex;flex-direction:column;justify-content:flex-end;align-items:center">
            <span style="font-size:.68rem;font-weight:800;color:#1a2533;margin-bottom:.1rem">{jan or '–'}</span>
            <div style="width:70%;height:{max(0,pj)}px;background:#1a5276;border-radius:4px 4px 0 0"></div>
          </div>
          <div style="font-size:.68rem;font-weight:800;color:{dow_col};margin-top:.2rem">{ABREV_DIAS[di.weekday()]}</div>
        </div>"""

        # Bar PA
        pp = int(round(80*(t['pa']/max_pa))) if max_pa else 0
        pa_chart += f"""
        <div style="display:flex;flex-direction:column;align-items:center;flex:1;background:{col_bg};padding:.4rem .2rem;border-radius:6px">
          <div style="width:100%;height:80px;display:flex;flex-direction:column;justify-content:flex-end;align-items:center">
            <span style="font-size:.68rem;font-weight:800;color:#1a2533;margin-bottom:.1rem">{t['pa'] or '–'}</span>
            <div style="width:70%;height:{max(0,pp)}px;background:#c9a227;border-radius:4px 4px 0 0"></div>
          </div>
          <div style="font-size:.68rem;font-weight:800;color:{dow_col};margin-top:.2rem">{ABREV_DIAS[di.weekday()]}</div>
        </div>"""

        sai_td = '' if perfil == 'cozinha' else f'<td class="center">{t["jan_sai"]}</td>'
        table_rows += f"""<tr style="background:{col_bg}">
          <td><strong style="color:{dow_col}">{ABREV_DIAS[di.weekday()]}</strong> {di.strftime('%d/%m')}</td>
          <td class="center">{t['pa']}</td><td class="center">{t['lan']}</td>
          <td class="center">{t['alm_norm']}</td><td class="center">{t['alm_veg']}</td><td class="center">{t['alm_dieta']}</td>
          <td class="center">{t['jan_norm']}</td><td class="center">{t['jan_veg']}</td><td class="center">{t['jan_dieta']}</td>
          {sai_td}
        </tr>"""

    sai_th = '' if perfil == 'cozinha' else '<th class="center">Sai</th>'
    totais_semana = {k: sum(d['t'][k] for d in dias) for k in
                     ['pa','lan','alm_norm','alm_veg','alm_dieta','jan_norm','jan_veg','jan_dieta','jan_sai']}
    back_url = url_for('admin_home') if perfil=='admin' else url_for('painel_dia')

    legenda_alm = (''.join(f'<span style="display:inline-flex;align-items:center;gap:.3rem;font-size:.72rem">'
                           f'<span style="width:.65rem;height:.65rem;background:{c};border-radius:2px;display:inline-block"></span>{lb}</span>'
                           for lb,c in [('Normal','#1e8449'),('Veg.','#2471a3'),('Dieta','#d68910')]))

    content = f"""
    <div class="container">
      <div class="page-header">
        {_back_btn(back_url)}
        <div class="page-title">📊 Dashboard Semanal</div>
      </div>
      <div class="card" style="padding:.85rem 1.1rem;margin-bottom:.75rem">
        <div class="flex-between">
          <div class="flex">
            <a class="btn btn-ghost btn-sm" href="{url_for('dashboard_semanal',d0=prev_w)}">← Semana anterior</a>
            <strong>{d0.strftime('%d/%m/%Y')} — {d1.strftime('%d/%m/%Y')}</strong>
            <a class="btn btn-ghost btn-sm" href="{url_for('dashboard_semanal',d0=next_w)}">Semana seguinte →</a>
          </div>
          <form method="get" style="display:flex;gap:.3rem">
            <input type="date" name="d0" value="{d0_str}" style="width:auto">
            <button class="btn btn-primary btn-sm">Ir</button>
          </form>
        </div>
      </div>

      <div class="grid grid-4" style="margin-bottom:.85rem">
        <div class="stat-box"><div class="stat-num">{totais_semana['pa']}</div><div class="stat-lbl">PA semana</div></div>
        <div class="stat-box"><div class="stat-num">{totais_semana['alm_norm']+totais_semana['alm_veg']+totais_semana['alm_dieta']}</div><div class="stat-lbl">Almoços semana</div></div>
        <div class="stat-box"><div class="stat-num">{totais_semana['jan_norm']+totais_semana['jan_veg']+totais_semana['jan_dieta']}</div><div class="stat-lbl">Jantares semana</div></div>
        <div class="stat-box"><div class="stat-num">{totais_semana['lan']}</div><div class="stat-lbl">Lanches semana</div></div>
      </div>

      <div class="card">
        <div class="card-title">🍽️ Almoços por dia
          <span style="margin-left:.6rem;display:inline-flex;gap:.6rem">{legenda_alm}</span>
        </div>
        <div style="display:flex;gap:.3rem;align-items:flex-end;padding:.3rem 0">
          {alm_chart}
        </div>
        <div style="border-top:2px solid #e9ecef;margin-top:.3rem"></div>
      </div>

      <div class="grid grid-2">
        <div class="card">
          <div class="card-title">🌙 Jantares por dia</div>
          <div style="display:flex;gap:.3rem;align-items:flex-end;padding:.3rem 0">{jan_chart}</div>
        </div>
        <div class="card">
          <div class="card-title">☕ Pequenos Almoços por dia</div>
          <div style="display:flex;gap:.3rem;align-items:flex-end;padding:.3rem 0">{pa_chart}</div>
        </div>
      </div>

      <div class="card">
        <div class="card-title">📋 Tabela detalhada</div>
        <div class="table-wrap">
          <table>
            <thead><tr><th>Dia</th><th>PA</th><th>Lan</th><th>Alm N</th><th>Alm V</th><th>Alm D</th><th>Jan N</th><th>Jan V</th><th>Jan D</th>{sai_th}</tr></thead>
            <tbody>{table_rows}</tbody>
          </table>
        </div>
        <div class="gap-btn" style="margin-top:.8rem">
          <a class="btn btn-primary" href="{url_for('exportar_relatorio',d0=d0_str,fmt='csv')}">⬇ CSV</a>
          <a class="btn btn-primary" href="{url_for('exportar_relatorio',d0=d0_str,fmt='xlsx')}">⬇ Excel</a>
        </div>
      </div>
    </div>"""
    return render(content)

# ═══════════════════════════════════════════════════════════════════════════
# CONFIGURAÇÕES DE NOTIFICAÇÕES (Admin)
# ═══════════════════════════════════════════════════════════════════════════

@app.route('/admin/notificacoes', methods=['GET','POST'])
@role_required('admin')
def admin_notificacoes():
    msg_ok = msg_err = ''
    if request.method == 'POST':
        acao = request.form.get('acao','')
        if acao == 'test_email':
            dest = request.form.get('email_teste','').strip()
            if dest:
                ok = _send_email(dest,
                    '⚓ Escola Naval — Teste de notificações',
                    '<h2>✅ Email de teste</h2><p>As notificações por email estão a funcionar correctamente!</p>',
                    'Escola Naval: teste de email OK.')
                flash('Email de teste enviado!' if ok else 'Falha ao enviar — verifica as configurações SMTP.', 'ok' if ok else 'error')
            else:
                flash('Introduz um endereço de email para teste.', 'warn')
        elif acao == 'test_sms':
            num = request.form.get('sms_teste','').strip()
            if num:
                ok = _send_sms(num, '[Escola Naval] SMS de teste — notificações OK!')
                flash('SMS enviado!' if ok else 'Falha ao enviar SMS — verifica as configurações Twilio.', 'ok' if ok else 'error')
            else:
                flash('Introduz um número para o SMS de teste.', 'warn')
        elif acao == 'enviar_avisos':
            _verificar_e_enviar_avisos()
            flash('Verificação de avisos executada. Consulta os logs para detalhes.', 'ok')
        return redirect(url_for('admin_notificacoes'))

    smtp_ok = bool(SMTP_HOST and SMTP_USER and SMTP_PASSWORD)
    twilio_ok = bool(TWILIO_SID and TWILIO_TOKEN and TWILIO_FROM)
    scheduler_ativo = _scheduler_timer is not None and _scheduler_timer.is_alive()
    canal_ok = smtp_ok or twilio_ok

    # Stats de notificações enviadas
    try:
        with sr.db() as conn:
            notif_count = conn.execute("SELECT COUNT(*) c FROM notificacoes_enviadas").fetchone()['c']
            notif_recentes = [dict(r) for r in conn.execute("""
                SELECT n.enviado_em, u.Nome_completo, u.NII, n.data, n.tipo
                FROM notificacoes_enviadas n
                JOIN utilizadores u ON u.id=n.utilizador_id
                ORDER BY n.enviado_em DESC LIMIT 20
            """).fetchall()]
            # Contar alunos sem email e sem telemóvel
            sem_contacto = conn.execute(
                "SELECT COUNT(*) c FROM utilizadores WHERE perfil='aluno' AND is_active=1 AND (email IS NULL OR email='') AND (telemovel IS NULL OR telemovel='')"
            ).fetchone()['c']
    except Exception:
        notif_count = 0; notif_recentes = []; sem_contacto = 0

    rows_notif = ''.join(f"""<tr>
        <td class="small">{(r['enviado_em'] or '')[:16]}</td>
        <td>{esc(r['Nome_completo'])}</td>
        <td class="small text-muted">{esc(r['NII'])}</td>
        <td>{r['data']}</td>
        <td><span class="badge badge-info">{esc(r['tipo'])}</span></td>
    </tr>""" for r in notif_recentes)

    sched_badge = (
        '<span class="badge badge-ok" style="margin-left:.5rem">✓ A correr</span>'
        if scheduler_ativo else
        '<span class="badge badge-warn" style="margin-left:.5rem">⏸ Inativo</span>'
    )
    sched_info = (
        f'<div style="font-size:.82rem;color:var(--ok)">✅ Scheduler automático ativo — verifica a cada '
        f'{NOTIF_INTERVALO_SCHEDULER//60} minutos.</div>'
        if scheduler_ativo else
        '<div style="font-size:.82rem;color:var(--warn)">⚠️ Scheduler não está ativo. '
        'Certifica-te de que o servidor correu com <code>python app6.py</code>. '
        'Em alternativa, usa o botão manual abaixo ou configura um cron job.</div>'
    )

    aviso_sem_contacto = (
        f'<div class="alert alert-warn" style="margin-top:.6rem">⚠️ <strong>{sem_contacto} aluno(s)</strong> '
        f'não têm email nem telemóvel registado — não receberão avisos. '
        f'<a href="{url_for("admin_utilizadores")}">Editar utilizadores →</a></div>'
        if sem_contacto else ''
    )

    content = f"""
    <div class="container">
      <div class="page-header">{_back_btn(url_for('admin_home'))}<div class="page-title">🔔 Notificações & Avisos</div></div>

      {'<div class="alert alert-warn">⚠️ <strong>Nenhum canal configurado.</strong> Sem SMTP ou Twilio, não é possível enviar avisos automáticos. Segue o guia abaixo para ativar.</div>' if not canal_ok else '<div class="alert alert-ok">✅ Canal de notificações ativo e operacional.</div>'}

      <!-- GUIA DE CONFIGURAÇÃO RÁPIDA -->
      <div class="card" style="margin-bottom:.9rem">
        <div class="card-title">📋 Guia de configuração rápida</div>
        <div style="font-size:.85rem;line-height:1.75">
          <p>As notificações funcionam via <strong>variáveis de ambiente</strong>. Define-as antes de arrancar o servidor:</p>
          <div style="background:#f4f6f8;padding:.7rem 1rem;border-radius:8px;margin:.6rem 0;font-family:monospace;font-size:.82rem;line-height:2">
            <strong style="font-family:sans-serif;font-size:.78rem;color:var(--primary)">Linux/Mac (terminal):</strong><br>
            export SMTP_HOST=smtp.gmail.com<br>
            export SMTP_PORT=587<br>
            export SMTP_USER=o-teu-email@gmail.com<br>
            export SMTP_PASSWORD=xxxx-xxxx-xxxx-xxxx &nbsp;<em style="font-family:sans-serif;font-size:.75rem;color:#6c757d"># App Password do Gmail</em><br>
            export SMTP_FROM=o-teu-email@gmail.com<br>
            python app6.py
          </div>
          <div style="background:#f4f6f8;padding:.7rem 1rem;border-radius:8px;margin:.6rem 0;font-family:monospace;font-size:.82rem;line-height:2">
            <strong style="font-family:sans-serif;font-size:.78rem;color:var(--primary)">Windows (PowerShell):</strong><br>
            $env:SMTP_HOST="smtp.gmail.com"<br>
            $env:SMTP_USER="o-teu-email@gmail.com"<br>
            $env:SMTP_PASSWORD="xxxx-xxxx-xxxx-xxxx"<br>
            python app6.py
          </div>
          <p style="font-size:.8rem;color:#6c757d">💡 <strong>Gmail:</strong> Vai a <em>Conta Google → Segurança → Verificação em dois passos → Palavras-passe de aplicações</em> e gera uma App Password. Usa essa como SMTP_PASSWORD.</p>
          <p style="font-size:.8rem;color:#6c757d">📌 <strong>Alternativa sem email:</strong> O sistema notifica visualmente no painel e os alunos veem o prazo nas suas páginas. O email é um extra.</p>
        </div>
      </div>

      <!-- ESTADO DO SCHEDULER -->
      <div class="card" style="margin-bottom:.9rem">
        <div class="card-title">🤖 Scheduler automático {sched_badge}</div>
        {sched_info}
        <div style="font-size:.81rem;margin-top:.5rem;color:var(--muted)">
          Prazo de edição: <strong>{sr.PRAZO_LIMITE_HORAS or '—'}h antes da refeição</strong> &nbsp;|&nbsp;
          Aviso enviado: <strong>{NOTIF_HORAS_AVISO}h antes do prazo</strong>
        </div>
        {aviso_sem_contacto}
        <div style="margin-top:.8rem;display:flex;gap:.5rem;flex-wrap:wrap">
          <form method="post" style="display:inline">
            {csrf_input()}<input type="hidden" name="acao" value="enviar_avisos">
            <button class="btn btn-warn btn-sm">🔔 Verificar e enviar avisos agora</button>
          </form>
          <span style="font-size:.79rem;color:var(--muted);align-self:center">
            Ou via cron: <code>curl "http://localhost:8080/api/avisos-cron?key={app.secret_key[:16]}"</code>
          </span>
        </div>
      </div>

      <!-- CANAIS -->
      <div class="grid grid-2" style="margin-bottom:.9rem">
        <div class="card">
          <div class="card-title">📧 Email (SMTP)
            <span class="badge {'badge-ok' if smtp_ok else 'badge-warn'}" style="margin-left:.4rem">{'✓ Configurado' if smtp_ok else '⚠ Não configurado'}</span>
          </div>
          {'<div style="font-size:.82rem;margin-bottom:.7rem"><div><strong>Host:</strong> '+esc(SMTP_HOST)+'</div><div><strong>Porta:</strong> '+str(SMTP_PORT)+'</div><div><strong>Utilizador:</strong> '+esc(SMTP_USER)+'</div></div>' if smtp_ok else ''}
          <div style="font-size:.81rem;background:#f4f6f8;padding:.55rem .7rem;border-radius:8px;margin-bottom:.6rem;line-height:1.7">
            <strong>Variáveis de ambiente a definir:</strong><br>
            <code>SMTP_HOST=smtp.gmail.com</code><br>
            <code>SMTP_PORT=587</code><br>
            <code>SMTP_USER=refeicoes@escola.pt</code><br>
            <code>SMTP_PASSWORD=app-password-aqui</code><br>
            <code>SMTP_FROM=refeicoes@escola.pt</code><br>
            <span class="text-muted" style="font-size:.77rem">💡 Gmail: activa 2FA e usa uma <em>App Password</em> (não a password normal).</span>
          </div>
          {'<form method="post" style="display:flex;gap:.4rem">'+str(csrf_input())+'<input type="hidden" name="acao" value="test_email"><input type="email" name="email_teste" placeholder="email@exemplo.com" style="flex:1"><button class="btn btn-primary btn-sm">📤 Testar</button></form>' if smtp_ok else '<div class="text-muted small">Define as variáveis acima e reinicia o servidor.</div>'}
        </div>
        <div class="card">
          <div class="card-title">📱 SMS (Twilio)
            <span class="badge {'badge-ok' if twilio_ok else 'badge-warn'}" style="margin-left:.4rem">{'✓ Configurado' if twilio_ok else '⚠ Não configurado'}</span>
          </div>
          {'<div style="font-size:.82rem;margin-bottom:.7rem"><div><strong>SID:</strong> '+esc(TWILIO_SID[:8])+'...</div><div><strong>From:</strong> '+esc(TWILIO_FROM)+'</div></div>' if twilio_ok else ''}
          <div style="font-size:.81rem;background:#f4f6f8;padding:.55rem .7rem;border-radius:8px;margin-bottom:.6rem;line-height:1.7">
            <strong>Variáveis de ambiente a definir:</strong><br>
            <code>TWILIO_SID=ACxxxxxxxxxxxxxxxx</code><br>
            <code>TWILIO_TOKEN=xxxxxxxxxxxxxxxx</code><br>
            <code>TWILIO_FROM=+351XXXXXXXXX</code><br>
            <span class="text-muted" style="font-size:.77rem">💡 Cria conta gratuita em <strong>twilio.com</strong>. O número de origem é fornecido pelo Twilio.</span>
          </div>
          {'<form method="post" style="display:flex;gap:.4rem">'+str(csrf_input())+'<input type="hidden" name="acao" value="test_sms"><input type="tel" name="sms_teste" placeholder="+351XXXXXXXXX" style="flex:1"><button class="btn btn-primary btn-sm">📤 Testar</button></form>' if twilio_ok else '<div class="text-muted small">Define as variáveis acima e reinicia o servidor.</div>'}
        </div>
      </div>

      <!-- HISTÓRICO -->
      <div class="card">
        <div class="card-title">📋 Últimas notificações enviadas ({notif_count} total)</div>
        <div class="table-wrap"><table>
          <thead><tr><th>Quando</th><th>Utilizador</th><th>NII</th><th>Data Ref.</th><th>Tipo</th></tr></thead>
          <tbody>{rows_notif or '<tr><td colspan="5" class="text-muted center" style="padding:1.2rem">Sem notificações enviadas ainda.</td></tr>'}</tbody>
        </table></div>
      </div>
    </div>"""
    return render(content)

@app.route('/api/avisos-cron')
def api_avisos_cron():
    """Endpoint para cron job externo invocar verificação de avisos."""
    key = request.args.get('key','')
    if key != app.secret_key[:16]:
        abort(403)
    _verificar_e_enviar_avisos()
    return {'status': 'ok', 'ts': datetime.now().isoformat()}

# ═══════════════════════════════════════════════════════════════════════════
# EXPORTAÇÕES
# ═══════════════════════════════════════════════════════════════════════════

@app.route('/exportar/dia')
@role_required('cozinha','oficialdia','cmd','admin')
def exportar_dia():
    import io, csv as _csv
    d_str = request.args.get('d', date.today().isoformat())
    fmt = request.args.get('fmt','csv')
    dt = _parse_date(d_str)
    t = sr.get_totais_dia(dt.isoformat())

    # Tentar xlsx via openpyxl; cair para CSV se não disponível
    if fmt == 'xlsx':
        try:
            import openpyxl
            from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = f"Totais {dt.strftime('%d-%m-%Y')}"

            # Cabeçalho
            header_fill = PatternFill("solid", fgValue="0A2D4E")
            header_font = Font(color="FFFFFF", bold=True, size=11)
            border = Border(
                left=Side(style='thin'), right=Side(style='thin'),
                top=Side(style='thin'), bottom=Side(style='thin'))

            headers = ["Data","Dia","PA","Lanche","Alm. Normal","Alm. Veg.","Alm. Dieta",
                       "Jan. Normal","Jan. Veg.","Jan. Dieta","Jan. Sai Unidade","Total Almoços","Total Jantares"]
            for col, h in enumerate(headers, 1):
                c = ws.cell(row=1, column=col, value=h)
                c.fill = header_fill; c.font = header_font
                c.alignment = Alignment(horizontal='center')
                c.border = border

            total_alm = t['alm_norm']+t['alm_veg']+t['alm_dieta']
            total_jan = t['jan_norm']+t['jan_veg']+t['jan_dieta']
            data_row = [dt.isoformat(), NOMES_DIAS[dt.weekday()],
                        t['pa'], t['lan'], t['alm_norm'], t['alm_veg'], t['alm_dieta'],
                        t['jan_norm'], t['jan_veg'], t['jan_dieta'], t['jan_sai'],
                        total_alm, total_jan]
            alt_fill = PatternFill("solid", fgValue="EBF5FB")
            for col, val in enumerate(data_row, 1):
                c = ws.cell(row=2, column=col, value=val)
                c.fill = alt_fill; c.border = border
                c.alignment = Alignment(horizontal='center')

            # Auto-largura
            for col in ws.columns:
                max_len = max(len(str(c.value or '')) for c in col) + 4
                ws.column_dimensions[col[0].column_letter].width = min(max_len, 22)

            buf = io.BytesIO()
            wb.save(buf); buf.seek(0)
            return Response(buf.read(),
                headers={"Content-Disposition": f"attachment; filename=totais_{dt.isoformat()}.xlsx",
                         "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"})
        except ImportError:
            flash("openpyxl não instalado — a exportar CSV.", "warn")
            fmt = 'csv'
        except Exception as ex:
            flash(f"Erro ao gerar Excel: {ex} — a exportar CSV.", "warn")
            fmt = 'csv'

    # CSV
    buf = io.StringIO()
    writer = _csv.writer(buf, delimiter=';')
    writer.writerow(["Data","Dia","PA","Lanche","Alm. Normal","Alm. Veg.","Alm. Dieta",
                     "Jan. Normal","Jan. Veg.","Jan. Dieta","Jan. Sai Unidade","Total Almoços","Total Jantares"])
    total_alm = t['alm_norm']+t['alm_veg']+t['alm_dieta']
    total_jan = t['jan_norm']+t['jan_veg']+t['jan_dieta']
    writer.writerow([dt.isoformat(), NOMES_DIAS[dt.weekday()],
                     t['pa'], t['lan'], t['alm_norm'], t['alm_veg'], t['alm_dieta'],
                     t['jan_norm'], t['jan_veg'], t['jan_dieta'], t['jan_sai'],
                     total_alm, total_jan])
    csv_bytes = ('\ufeff' + buf.getvalue()).encode('utf-8')
    return Response(csv_bytes,
        headers={"Content-Disposition": f"attachment; filename=totais_{dt.isoformat()}.csv",
                 "Content-Type": "text/csv; charset=utf-8-sig"})


@app.route('/exportar/relatorio')
@role_required('cozinha','oficialdia','admin')
def exportar_relatorio():
    import io, csv as _csv
    d0_str = request.args.get('d0', date.today().isoformat())
    fmt = request.args.get('fmt','csv')
    d0 = _parse_date(d0_str)
    d1 = d0 + timedelta(days=6)

    dias_data = []
    totais = {k:0 for k in ['pa','lan','alm_norm','alm_veg','alm_dieta','jan_norm','jan_veg','jan_dieta','jan_sai']}
    for i in range(7):
        di = d0 + timedelta(days=i)
        t = sr.get_totais_dia(di.isoformat())
        tipo = sr.dia_operacional(di)
        alm = t['alm_norm']+t['alm_veg']+t['alm_dieta']
        jan = t['jan_norm']+t['jan_veg']+t['jan_dieta']
        dias_data.append((di, tipo, t, alm, jan))
        for k in totais: totais[k] += t[k]

    HEADERS = ["Data","Dia da Semana","Tipo Dia","PA","Lanche",
               "Alm. Normal","Alm. Veg.","Alm. Dieta","Total Almoços",
               "Jan. Normal","Jan. Veg.","Jan. Dieta","Total Jantares","Sai Unidade"]

    def make_row(di, tipo, t, alm, jan):
        return [di.isoformat(), NOMES_DIAS[di.weekday()], tipo,
                t['pa'], t['lan'], t['alm_norm'], t['alm_veg'], t['alm_dieta'], alm,
                t['jan_norm'], t['jan_veg'], t['jan_dieta'], jan, t['jan_sai']]

    nome = f"relatorio_{d0_str}_a_{d1.isoformat()}"

    if fmt == 'xlsx':
        try:
            import openpyxl
            from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = f"Relatório {d0.strftime('%d-%m')} a {d1.strftime('%d-%m-%Y')}"

            header_fill = PatternFill("solid", fgValue="0A2D4E")
            header_font = Font(color="FFFFFF", bold=True)
            thin = Side(style='thin')
            border = Border(left=thin, right=thin, top=thin, bottom=thin)

            for col, h in enumerate(HEADERS, 1):
                c = ws.cell(row=1, column=col, value=h)
                c.fill = header_fill; c.font = header_font
                c.alignment = Alignment(horizontal='center'); c.border = border

            TIPO_CORES = {'feriado':'FFD6D6','exercicio':'FFFACD','fim_semana':'DDEEFF','normal':'FFFFFF','outro':'F0F0F0'}
            for ri, (di, tipo, t, alm, jan) in enumerate(dias_data, 2):
                row_fill = PatternFill("solid", fgValue=TIPO_CORES.get(tipo,'FFFFFF'))
                for col, val in enumerate(make_row(di, tipo, t, alm, jan), 1):
                    c = ws.cell(row=ri, column=col, value=val)
                    c.fill = row_fill; c.border = border
                    c.alignment = Alignment(horizontal='center' if col > 2 else 'left')

            # Linha de totais
            total_alm = totais['alm_norm']+totais['alm_veg']+totais['alm_dieta']
            total_jan = totais['jan_norm']+totais['jan_veg']+totais['jan_dieta']
            total_row = ["TOTAL","—","—", totais['pa'], totais['lan'],
                         totais['alm_norm'], totais['alm_veg'], totais['alm_dieta'], total_alm,
                         totais['jan_norm'], totais['jan_veg'], totais['jan_dieta'], total_jan, totais['jan_sai']]
            total_fill = PatternFill("solid", fgValue="D5E8F0")
            total_font = Font(bold=True)
            for col, val in enumerate(total_row, 1):
                c = ws.cell(row=9, column=col, value=val)
                c.fill = total_fill; c.font = total_font
                c.border = border; c.alignment = Alignment(horizontal='center' if col > 2 else 'left')

            for col in ws.columns:
                max_len = max(len(str(c.value or '')) for c in col) + 3
                ws.column_dimensions[col[0].column_letter].width = min(max_len, 20)

            buf = io.BytesIO(); wb.save(buf); buf.seek(0)
            return Response(buf.read(),
                headers={"Content-Disposition": f"attachment; filename={nome}.xlsx",
                         "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"})
        except ImportError:
            flash("openpyxl não instalado — a exportar CSV.", "warn")
        except Exception as ex:
            flash(f"Erro ao gerar Excel: {ex} — a exportar CSV.", "warn")

    # CSV (com BOM para Excel abrir correctamente)
    buf = io.StringIO()
    writer = _csv.writer(buf, delimiter=';')
    writer.writerow(HEADERS)
    for di, tipo, t, alm, jan in dias_data:
        writer.writerow(make_row(di, tipo, t, alm, jan))
    total_alm = totais['alm_norm']+totais['alm_veg']+totais['alm_dieta']
    total_jan = totais['jan_norm']+totais['jan_veg']+totais['jan_dieta']
    writer.writerow(["TOTAL","—","—", totais['pa'], totais['lan'],
                     totais['alm_norm'], totais['alm_veg'], totais['alm_dieta'], total_alm,
                     totais['jan_norm'], totais['jan_veg'], totais['jan_dieta'], total_jan, totais['jan_sai']])
    csv_bytes = ('\ufeff' + buf.getvalue()).encode('utf-8')
    return Response(csv_bytes,
        headers={"Content-Disposition": f"attachment; filename={nome}.csv",
                 "Content-Type": "text/csv; charset=utf-8-sig"})





# ═══════════════════════════════════════════════════════════════════════════
# CONTROLO DE PRESENÇAS — Módulo rápido via NI (Oficial de Dia)
# ═══════════════════════════════════════════════════════════════════════════

@app.route('/presencas', methods=['GET', 'POST'])
@role_required('oficialdia', 'admin', 'cmd')
def controlo_presencas():
    u = current_user()
    hoje = date.today()
    d_str = request.args.get('d', hoje.isoformat())
    dt = _parse_date(d_str)

    resultado = None
    ni_q = ''

    if request.method == 'POST':
        acao = request.form.get('acao', '')
        ni_q = request.form.get('ni', '').strip()

        if acao == 'consultar' and ni_q:
            with sr.db() as conn:
                aluno = conn.execute(
                    "SELECT id,NII,NI,Nome_completo,ano,email,telemovel FROM utilizadores WHERE NI=? AND perfil='aluno'",
                    (ni_q,)).fetchone()
            if aluno:
                aluno = dict(aluno)
                uid = aluno['id']
                ausente = _tem_ausencia_ativa(uid, dt)
                with sr.db() as conn:
                    ref = conn.execute(
                        "SELECT * FROM refeicoes WHERE utilizador_id=? AND data=?",
                        (uid, dt.isoformat())).fetchone()
                    ref = dict(ref) if ref else {}
                resultado = {'aluno': aluno, 'ausente': ausente, 'ref': ref, 'ni': ni_q}
            else:
                flash(f'NI "{ni_q}" não encontrado.', 'error')

        elif acao == 'dar_saida' and ni_q:
            with sr.db() as conn:
                aluno = conn.execute(
                    "SELECT id,NII,Nome_completo FROM utilizadores WHERE NI=? AND perfil='aluno'",
                    (ni_q,)).fetchone()
            if aluno:
                aluno = dict(aluno)
                _registar_ausencia(aluno['id'], dt.isoformat(), dt.isoformat(),
                                   f'Saída registada por {u["nome"]} ({u["perfil"]})', u['nii'])
                flash(f'✅ Saída registada para {aluno["Nome_completo"]} (NI {ni_q}).', 'ok')
            else:
                flash(f'NI "{ni_q}" não encontrado.', 'error')

        elif acao == 'dar_entrada' and ni_q:
            with sr.db() as conn:
                aluno = conn.execute(
                    "SELECT id,NII,Nome_completo FROM utilizadores WHERE NI=? AND perfil='aluno'",
                    (ni_q,)).fetchone()
            if aluno:
                aluno = dict(aluno)
                with sr.db() as conn:
                    conn.execute("""DELETE FROM ausencias WHERE utilizador_id=?
                                    AND ausente_de=? AND ausente_ate=?""",
                                 (aluno['id'], dt.isoformat(), dt.isoformat()))
                    conn.commit()
                flash(f'✅ Entrada registada para {aluno["Nome_completo"]} (NI {ni_q}).', 'ok')
            else:
                flash(f'NI "{ni_q}" não encontrado.', 'error')

        # Após POST sem resultado, redirecionar limpo
        if resultado is None:
            return redirect(url_for('controlo_presencas', d=dt.isoformat()))

    # Resumo de todos os anos para a data
    anos_resumo = []
    for ano in _get_anos_disponiveis():
        with sr.db() as conn:
            total = conn.execute(
                "SELECT COUNT(*) c FROM utilizadores WHERE ano=? AND perfil='aluno'", (ano,)).fetchone()['c']
            ausentes_a = conn.execute("""
                SELECT COUNT(*) c FROM utilizadores u
                WHERE u.ano=? AND u.perfil='aluno'
                AND EXISTS(SELECT 1 FROM ausencias a WHERE a.utilizador_id=u.id
                           AND a.ausente_de<=? AND a.ausente_ate>=?)""",
                (ano, dt.isoformat(), dt.isoformat())).fetchone()['c']
            com_ref = conn.execute("""
                SELECT COUNT(*) c FROM utilizadores u
                WHERE u.ano=? AND u.perfil='aluno'
                AND EXISTS(SELECT 1 FROM refeicoes r WHERE r.utilizador_id=u.id
                           AND r.data=? AND (r.almoco IS NOT NULL OR r.jantar_tipo IS NOT NULL))""",
                (ano, dt.isoformat())).fetchone()['c']
        anos_resumo.append({'ano': ano, 'total': total, 'ausentes': ausentes_a,
                            'presentes': total - ausentes_a, 'com_ref': com_ref})

    resumo_html = ''
    for r in anos_resumo:
        pct_aus = int(100 * r['ausentes'] / r['total']) if r['total'] else 0
        resumo_html += f"""
        <div class="stat-box" style="cursor:pointer" onclick="window.location='{url_for('lista_alunos_ano', ano=r['ano'], d=dt.isoformat())}'">
          <div class="stat-num">{r['presentes']} <small style="font-size:.6em;color:var(--muted)">/ {r['total']}</small></div>
          <div class="stat-lbl">{_ano_label(r['ano'])} — Presentes</div>
          <div style="margin-top:.35rem;font-size:.75rem">
            <span style="color:var(--warn)">✖ {r['ausentes']} ausentes</span> &nbsp;
            <span style="color:var(--ok)">🍽 {r['com_ref']} c/ refeições</span>
          </div>
        </div>"""

    # Resultado da pesquisa
    resultado_html = ''
    if resultado:
        al = resultado['aluno']
        ref = resultado['ref']
        ausente = resultado['ausente']
        ni_val = resultado['ni']

        estado_cor = '#fdecea' if ausente else '#d5f5e3'
        estado_txt = '🔴 AUSENTE' if ausente else '🟢 PRESENTE'

        def ref_chip(val, label, tipo=None):
            if val:
                txt = tipo if tipo else '✓'
                return f'<span style="background:#eafaf1;border:1.5px solid #a9dfbf;border-radius:7px;padding:.25rem .55rem;font-size:.8rem;font-weight:700">{label} {txt}</span>'
            return f'<span style="background:#fdecea;border:1.5px solid #f1948a;border-radius:7px;padding:.25rem .55rem;font-size:.8rem;color:var(--muted)">{label} ✗</span>'

        acao_presenca = f"""
        <div style="display:flex;gap:.5rem;flex-wrap:wrap;margin-top:.8rem">
          {"" if not ausente else f'''
          <form method="post">
            {csrf_input()}
            <input type="hidden" name="acao" value="dar_entrada">
            <input type="hidden" name="ni" value="{esc(ni_val)}">
            <button class="btn btn-ok">✅ Dar Entrada (marcar presente)</button>
          </form>'''}
          {"" if ausente else f'''
          <form method="post" onsubmit="return confirm('Confirmar saída de {esc(al["Nome_completo"])}?')">
            {csrf_input()}
            <input type="hidden" name="acao" value="dar_saida">
            <input type="hidden" name="ni" value="{esc(ni_val)}">
            <button class="btn btn-danger">🚪 Dar Saída (marcar ausente)</button>
          </form>'''}
          <a class="btn btn-ghost" href="{url_for('ver_perfil_aluno', nii=al['NII'], ano=al['ano'], d=dt.isoformat())}">👁 Ver perfil completo</a>
        </div>"""

        resultado_html = f"""
        <div class="card" style="border-left:4px solid {'var(--danger)' if ausente else 'var(--ok)'}">
          <div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:.5rem">
            <div>
              <div style="font-size:1.15rem;font-weight:800">{esc(al['Nome_completo'])}</div>
              <div class="text-muted small">NI: <strong>{esc(al['NI'])}</strong> &nbsp;|&nbsp; {al['ano']}º Ano &nbsp;|&nbsp; NII: {esc(al['NII'])}</div>
            </div>
            <div style="background:{estado_cor};padding:.4rem .9rem;border-radius:20px;font-weight:800;font-size:1rem">{estado_txt}</div>
          </div>
          <hr style="margin:.7rem 0">
          <div class="card-title" style="font-size:.82rem;margin-bottom:.5rem">🍽️ Refeições em {dt.strftime('%d/%m/%Y')}</div>
          <div style="display:flex;gap:.4rem;flex-wrap:wrap">
            {ref_chip(ref.get('pequeno_almoco'), '☕ PA')}
            {ref_chip(ref.get('lanche'), '🥐 Lanche')}
            {ref_chip(ref.get('almoco'), '🍽️ Almoço', ref.get('almoco','')[:3] if ref.get('almoco') else None)}
            {ref_chip(ref.get('jantar_tipo'), '🌙 Jantar', ref.get('jantar_tipo','')[:3] if ref.get('jantar_tipo') else None)}
            {f'<span style="background:#fef9e7;border:1.5px solid #f9e79f;border-radius:7px;padding:.25rem .55rem;font-size:.8rem">🚪 Sai</span>' if ref.get('jantar_sai_unidade') else ''}
          </div>
          {acao_presenca}
        </div>"""

    prev_d = (dt - timedelta(days=1)).isoformat()
    next_d = (dt + timedelta(days=1)).isoformat()

    content = f"""
    <div class="container">
      <div class="page-header">
        {_back_btn(url_for('painel_dia', d=dt.isoformat()), 'Painel')}
        <div class="page-title">🎯 Controlo de Presenças</div>
      </div>

      <!-- Navegação de datas -->
      <div class="card" style="padding:.75rem 1.1rem;margin-bottom:.8rem">
        <div class="flex-between">
          <div class="flex">
            <a class="btn btn-ghost btn-sm" href="{url_for('controlo_presencas', d=prev_d)}">← Anterior</a>
            <strong>{NOMES_DIAS[dt.weekday()]}, {dt.strftime('%d/%m/%Y')}</strong>
            <a class="btn btn-ghost btn-sm" href="{url_for('controlo_presencas', d=next_d)}">Próximo →</a>
          </div>
          <form method="get" style="display:flex;gap:.3rem">
            <input type="date" name="d" value="{dt.isoformat()}" style="width:auto">
            <button class="btn btn-primary btn-sm">Ir</button>
          </form>
        </div>
      </div>

      <!-- Pesquisa rápida por NI -->
      <div class="card" style="border-top:3px solid var(--primary)">
        <div class="card-title">🔍 Pesquisa rápida por NI</div>
        <div class="alert alert-info" style="margin-bottom:.8rem;font-size:.82rem">
          💡 Introduz o NI do aluno (ex: <strong>222</strong>) para consultar o estado de presença e refeições. Podes depois dar entrada ou saída diretamente.
        </div>
        <form method="post" style="display:flex;gap:.5rem;flex-wrap:wrap">
          {csrf_input()}
          <input type="hidden" name="acao" value="consultar">
          <input type="text" name="ni" value="{esc(ni_q)}" placeholder="NI do aluno (ex: 222)"
            style="flex:1;min-width:140px;font-size:1.05rem;font-weight:700;letter-spacing:.05em"
            autofocus autocomplete="off">
          <button class="btn btn-primary" style="font-size:1rem">🔍 Consultar</button>
        </form>
      </div>

      {resultado_html}

      <!-- Resumo por ano -->
      <div class="card">
        <div class="card-title">📊 Resumo geral — {dt.strftime('%d/%m/%Y')}</div>
        <div class="grid grid-3">{resumo_html or '<div class="text-muted">Sem dados.</div>'}</div>
        <div style="margin-top:.6rem;font-size:.8rem;color:var(--muted)">Clica num ano para ver a lista completa.</div>
      </div>
    </div>"""
    return render(content)


# ═══════════════════════════════════════════════════════════════════════════
# GESTÃO DE COMPANHIAS — Unificação de Turmas + Promoção de Alunos
# ═══════════════════════════════════════════════════════════════════════════

@app.route('/admin/companhias', methods=['GET', 'POST'])
@role_required('admin')
def admin_companhias():
    if request.method == 'POST':
        acao = request.form.get('acao', '')

        # ── Criar turma ──────────────────────────────────────────────────
        if acao == 'criar_turma':
            nome_turma = request.form.get('nome_turma', '').strip()
            ano_turma  = request.form.get('ano_turma', '').strip()
            descricao  = request.form.get('descricao', '').strip()
            if not nome_turma or not ano_turma:
                flash('Nome e ano são obrigatórios.', 'error')
            else:
                try:
                    ano_int = int(ano_turma)
                    with sr.db() as conn:
                        conn.execute("""CREATE TABLE IF NOT EXISTS turmas (
                            id INTEGER PRIMARY KEY,
                            nome TEXT NOT NULL UNIQUE,
                            ano INTEGER NOT NULL,
                            descricao TEXT,
                            criado_em TEXT DEFAULT (datetime('now','localtime'))
                        )""")
                        conn.execute("INSERT INTO turmas (nome, ano, descricao) VALUES (?,?,?)",
                                     (nome_turma, ano_int, descricao or None))
                        conn.commit()
                    flash(f'Turma "{nome_turma}" ({_ano_label(ano_int)}) criada com sucesso!', 'ok')
                except Exception as ex:
                    flash(f'Erro ao criar turma: {ex}', 'error')

        # ── Eliminar turma ───────────────────────────────────────────────
        elif acao == 'eliminar_turma':
            tid = request.form.get('tid', '')
            try:
                with sr.db() as conn:
                    conn.execute("DELETE FROM turmas WHERE id=?", (tid,))
                    conn.commit()
                flash('Turma eliminada.', 'ok')
            except Exception as ex:
                flash(f'Erro: {ex}', 'error')

        # ── Mover aluno de ano ───────────────────────────────────────────
        elif acao == 'mover_aluno':
            nii_m   = request.form.get('nii_m', '').strip()
            novo_ano = request.form.get('novo_ano', '').strip()
            if nii_m and novo_ano:
                try:
                    with sr.db() as conn:
                        conn.execute("UPDATE utilizadores SET ano=? WHERE NII=? AND perfil='aluno'",
                                     (int(novo_ano), nii_m))
                        conn.commit()
                    flash(f'Aluno {nii_m} movido para {_ano_label(int(novo_ano))}.', 'ok')
                except Exception as ex:
                    flash(f'Erro: {ex}', 'error')

        # ── Promover aluno individual ─────────────────────────────────────
        elif acao == 'promover_um':
            uid_p   = request.form.get('uid', '')
            novo_ni = request.form.get('novo_ni', '').strip()
            with sr.db() as conn:
                al = conn.execute("SELECT ano,NI FROM utilizadores WHERE id=?", (uid_p,)).fetchone()
            if al:
                ano_a = al['ano']
                # CFBO(7) e CFCO(8) não têm progressão automática para acima
                if ano_a >= 6:
                    novo_ano_p = 0
                else:
                    novo_ano_p = ano_a + 1
                with sr.db() as conn:
                    conn.execute("UPDATE utilizadores SET ano=?,NI=? WHERE id=?",
                                 (novo_ano_p, novo_ni or al['NI'], uid_p))
                    conn.commit()
                dest = _ano_label(novo_ano_p) if novo_ano_p else 'Concluído'
                flash(f'Aluno promovido para {dest}.', 'ok')

        # ── Promoção global de um ano ─────────────────────────────────────
        elif acao == 'promover_todos':
            ano_origem = int(request.form.get('ano_origem', 0))
            if ano_origem >= 6:
                novo_ano_p = 0
            else:
                novo_ano_p = ano_origem + 1
            with sr.db() as conn:
                conn.execute("UPDATE utilizadores SET ano=? WHERE perfil='aluno' AND ano=?",
                             (novo_ano_p, ano_origem))
                conn.commit()
            dest = _ano_label(novo_ano_p) if novo_ano_p else 'Concluído'
            flash(f'Todos os alunos do {_ano_label(ano_origem)} promovidos para {dest}.', 'ok')

        # ── Promoção global todos os anos ────────────────────────────────
        elif acao == 'promover_todos_anos':
            with sr.db() as conn:
                # Promover do maior para o menor para evitar conflitos
                for ano_a in range(6, 0, -1):
                    novo_ano_p = 0 if ano_a >= 6 else ano_a + 1
                    conn.execute("UPDATE utilizadores SET ano=? WHERE perfil='aluno' AND ano=?",
                                 (novo_ano_p, ano_a))
                conn.commit()
            flash('Promoção global concluída.', 'ok')

        return redirect(url_for('admin_companhias'))

    # ── Carregar dados ───────────────────────────────────────────────────
    try:
        with sr.db() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS turmas (
                id INTEGER PRIMARY KEY, nome TEXT NOT NULL UNIQUE, ano INTEGER NOT NULL,
                descricao TEXT, criado_em TEXT DEFAULT (datetime('now','localtime'))
            )""")
            turmas = [dict(r) for r in conn.execute(
                "SELECT * FROM turmas ORDER BY ano, nome").fetchall()]
    except Exception:
        turmas = []

    # Contagens por ano (inclui CFBO e CFCO)
    anos_data = {}
    all_anos = list(range(1, 7)) + [7, 8]
    for a in all_anos:
        with sr.db() as conn:
            cnt = conn.execute(
                "SELECT COUNT(*) c FROM utilizadores WHERE ano=? AND perfil='aluno'", (a,)).fetchone()['c']
        anos_data[a] = cnt

    # ── HTML alunos por ano ───────────────────────────────────────────────
    anos_grid = ''
    for a in all_anos:
        n = anos_data.get(a, 0)
        anos_grid += f'<div class="stat-box"><div class="stat-num">{n}</div><div class="stat-lbl">{_ano_label(a)}</div></div>'

    # ── HTML promoção ─────────────────────────────────────────────────────
    def _build_promover_html():
        cards = ''
        promovable = list(range(1, 7)) + [7, 8]
        for a in promovable:
            with sr.db() as conn:
                alunos_a = [dict(r) for r in conn.execute(
                    "SELECT id,NI,Nome_completo,ano FROM utilizadores WHERE perfil='aluno' AND ano=? ORDER BY NI", (a,)).fetchall()]
            n = len(alunos_a)
            if a >= 6:
                destino = 'Concluído'
                cor = '#922b21'
            else:
                destino = _ano_label(a + 1)
                cor = '#1e8449'
            alunos_list = ''.join(
                '<div style="display:flex;justify-content:space-between;align-items:center;padding:.3rem 0;border-bottom:1px solid var(--border);font-size:.82rem;gap:.4rem">'
                '<span><strong>'+esc(al['NI'])+'</strong> — '+esc(al['Nome_completo'])+'</span>'
                '<form method="post" style="display:inline;display:flex;gap:.3rem;align-items:center">'
                +str(csrf_input())+
                '<input type="hidden" name="acao" value="promover_um">'
                '<input type="hidden" name="uid" value="'+str(al['id'])+'">'
                '<input type="text" name="novo_ni" placeholder="Novo NI" style="width:110px;padding:.25rem .45rem;font-size:.78rem;border-radius:7px;border:1.5px solid var(--border)">'
                '<button class="btn btn-ghost btn-sm" title="Promover este aluno">↑ Promover</button>'
                '</form></div>'
                for al in alunos_a)
            disabled = ' disabled' if not alunos_a else ''
            cards += (
                '<div class="card" style="border-top:3px solid '+cor+'">'
                '<div class="card-title" style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:.4rem">'
                '<span>'+_ano_label(a)+' <span class="badge badge-info" style="margin-left:.4rem">'+str(n)+' alunos</span></span>'
                '<form method="post" style="display:inline" onsubmit="return confirm(\'Promover todos os alunos deste ano?\')">'
                +str(csrf_input())+'<input type="hidden" name="acao" value="promover_todos"><input type="hidden" name="ano_origem" value="'+str(a)+'">'
                '<button class="btn btn-sm" style="background:'+cor+';color:#fff"'+disabled+'>🎖️ Promover todos → '+destino+'</button></form></div>'
                '<div style="max-height:180px;overflow-y:auto;border-top:1px solid var(--border);padding-top:.4rem">'
                +(alunos_list or '<div class="text-muted small" style="padding:.3rem">Sem alunos.</div>')+
                '</div></div>')
        return cards

    anos_cards_prom = _build_promover_html()

    # ── HTML turmas criadas ───────────────────────────────────────────────
    turmas_html = ''
    for t in turmas:
        turmas_html += f"""
        <tr>
          <td><strong>{esc(t['nome'])}</strong></td>
          <td>{_ano_label(t['ano'])}</td>
          <td>{esc(t.get('descricao') or '—')}</td>
          <td class="small text-muted">{(t.get('criado_em') or '')[:16]}</td>
          <td>
            <form method="post" style="display:inline" onsubmit="return confirm('Eliminar turma?')">
              {csrf_input()}
              <input type="hidden" name="acao" value="eliminar_turma">
              <input type="hidden" name="tid" value="{t['id']}">
              <button class="btn btn-danger btn-sm">🗑</button>
            </form>
          </td>
        </tr>"""

    # ── Alunos para mover ─────────────────────────────────────────────────
    with sr.db() as conn:
        alunos_all = conn.execute(
            "SELECT NII, NI, Nome_completo, ano FROM utilizadores WHERE perfil='aluno' ORDER BY ano, NI").fetchall()
    alunos_opts = ''.join(
        f'<option value="{esc(a["NII"])}">[{_ano_label(a["ano"])}] {esc(a["NI"])} — {esc(a["Nome_completo"])}</option>'
        for a in alunos_all)

    ano_select_opts = ''.join(f'<option value="{a}">{_ano_label(a)}</option>' for a, _ in ANOS_OPCOES)
    ano_select_criar = ''.join(f'<option value="{a}">{lbl}</option>' for a, lbl in ANOS_OPCOES)
    ano_select_mover = ano_select_opts + '<option value="0">Concluído / Inativo</option>'

    # ── Tabs de secção (via hash) ─────────────────────────────────────────
    content = f"""
    <div class="container">
      <div class="page-header">
        {_back_btn(url_for('admin_home'))}
        <div class="page-title">⚓ Gestão de Companhias</div>
      </div>

      <!-- Tabs -->
      <div class="year-tabs" style="margin-bottom:1rem">
        <a class="year-tab" href="#turmas" onclick="showTab('turmas')">📚 Turmas</a>
        <a class="year-tab" href="#promocao" onclick="showTab('promocao')">🎖️ Promoção</a>
        <a class="year-tab" href="#mover" onclick="showTab('mover')">🔄 Mover Aluno</a>
      </div>
      <script>
        function showTab(id) {{
          ['turmas','promocao','mover'].forEach(function(t) {{
            document.getElementById('tab-'+t).style.display = (t===id) ? '' : 'none';
          }});
          document.querySelectorAll('.year-tab').forEach(function(el) {{
            el.classList.toggle('active', el.getAttribute('href')==='#'+id);
          }});
        }}
        // Mostrar tab por hash ou primeiro
        var hash = window.location.hash.replace('#','') || 'turmas';
        showTab(hash);
      </script>

      <!-- Tab: Turmas -->
      <div id="tab-turmas">
        <div class="card">
          <div class="card-title">📊 Alunos por ano/curso</div>
          <div class="grid grid-4">{anos_grid}</div>
        </div>
        <div class="grid grid-2">
          <div class="card">
            <div class="card-title">➕ Criar nova turma / companhia</div>
            <form method="post">
              {csrf_input()}
              <input type="hidden" name="acao" value="criar_turma">
              <div class="form-group">
                <label>Nome da turma <span class="text-muted small">(ex: Alpha, Bravo...)</span></label>
                <input type="text" name="nome_turma" required placeholder="Ex: Alpha">
              </div>
              <div class="form-group">
                <label>Ano curricular / Curso</label>
                <select name="ano_turma" required>
                  {ano_select_criar}
                </select>
              </div>
              <div class="form-group">
                <label>Descrição <span class="text-muted small">(opcional)</span></label>
                <input type="text" name="descricao" placeholder="Ex: Turma de engenharia naval">
              </div>
              <button class="btn btn-ok">💾 Criar turma</button>
            </form>
          </div>
          <div class="card">
            <div class="card-title">📋 Turmas criadas ({len(turmas)})</div>
            {'<div class="table-wrap"><table><thead><tr><th>Nome</th><th>Ano/Curso</th><th>Descrição</th><th>Criada em</th><th></th></tr></thead><tbody>' + turmas_html + '</tbody></table></div>' if turmas else '<div class="text-muted" style="padding:.8rem">Nenhuma turma criada ainda.</div>'}
          </div>
        </div>
      </div>

      <!-- Tab: Promoção -->
      <div id="tab-promocao" style="display:none">
        <div class="alert alert-warn">⚠️ <strong>Atenção:</strong> A promoção é permanente. Recomenda-se fazer backup antes.</div>
        <div class="card">
          <div class="card-title">🚀 Promoção global — todos os anos em simultâneo</div>
          <p style="font-size:.85rem;color:var(--muted);margin-bottom:.8rem">Promove todos: 1º→2º, 2º→3º, ..., 5º→6º, 6º→Concluído. CFBO e CFCO não são afetados pela promoção global.</p>
          <form method="post" onsubmit="return confirm('Promover TODOS os alunos de todos os anos?')">
            {csrf_input()}<input type="hidden" name="acao" value="promover_todos_anos">
            <button class="btn btn-danger">🎖️ Promoção Global</button>
          </form>
        </div>
        <div class="grid grid-2">{anos_cards_prom}</div>
      </div>

      <!-- Tab: Mover Aluno -->
      <div id="tab-mover" style="display:none">
        <div class="card" style="max-width:520px">
          <div class="card-title">🔄 Mover aluno de ano</div>
          <div class="alert alert-info" style="font-size:.81rem;margin-bottom:.8rem">
            💡 Usa esta função para mover um aluno individualmente para outro ano sem usar a promoção global, incluindo para os cursos CFBO e CFCO.
          </div>
          <form method="post">
            {csrf_input()}
            <input type="hidden" name="acao" value="mover_aluno">
            <div class="form-group">
              <label>Aluno (NII)</label>
              <select name="nii_m" required>
                <option value="">— Selecionar aluno —</option>
                {alunos_opts}
              </select>
            </div>
            <div class="form-group">
              <label>Mover para</label>
              <select name="novo_ano" required>
                {ano_select_mover}
              </select>
            </div>
            <button class="btn btn-warn">🔄 Mover aluno</button>
          </form>
        </div>
      </div>
    </div>"""
    return render(content)


# Rota de compatibilidade — redireciona para o novo módulo
@app.route('/admin/turmas')
@role_required('admin')
def admin_turmas():
    return redirect(url_for('admin_companhias'))

@app.route('/admin/promover', methods=['GET','POST'])
@role_required('admin')
def admin_promover():
    return redirect(url_for('admin_companhias') + '#promocao')



@app.route('/api/backup-cron')
def api_backup_cron():
    """Endpoint para cron job externo invocar backup diário.
    Uso: curl "http://localhost:8080/api/backup-cron?key=<primeiros 16 chars da SECRET_KEY>"
    """
    key = request.args.get('key', '')
    if key != app.secret_key[:16]:
        abort(403)
    try:
        sr.ensure_daily_backup()
        sr.limpar_backups_antigos()
        return {'status': 'ok', 'ts': datetime.now().isoformat()}
    except Exception as exc:
        app.logger.error(f"api_backup_cron: {exc}")
        return {'status': 'error', 'msg': str(exc)}, 500


if __name__ == '__main__':
    sr.ensure_schema()
    sr.ensure_daily_backup()
    sr.limpar_backups_antigos()
    sr.autopreencher_refeicoes_semanais(DIAS_ANTECEDENCIA)
    _iniciar_scheduler()
    print("=" * 60)
    print("⚓ Escola Naval — Sistema de Refeições")
    print("  Acede em: http://localhost:8080")
    print("  admin/admin123 | cozinha/cozinha123")
    print("  oficialdia/oficial123 | teste1/teste1")
    print()
    print("  Endpoints de automação (cron jobs):")
    sk16 = app.secret_key[:16]
    print(f"    Backup:  curl 'http://localhost:8080/api/backup-cron?key={sk16}'")
    print(f"    Avisos:  curl 'http://localhost:8080/api/avisos-cron?key={sk16}'")
    print("=" * 60)
    debug = os.environ.get('DEBUG', 'false').lower() == 'true'
    app.run(debug=debug, host='0.0.0.0', port=int(os.environ.get('PORT', '8080')))
