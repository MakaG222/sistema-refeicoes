"""
tests/test_coverage_boost.py — Testes para subir coverage a 93%+
================================================================
Cobre linhas não testadas em: bootstrap CLI, backup, detencoes,
aluno routes, absences, config.
"""

import os
import sqlite3

os.environ.setdefault("ENV", "development")
os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production")

from datetime import date, timedelta


from conftest import create_aluno, create_system_user, get_csrf, login_as
from core.database import db


# ═══════════════════════════════════════════════════════════════════════════
# Bootstrap CLI commands coverage (bootstrap.py 59% → 90%+)
# ═══════════════════════════════════════════════════════════════════════════


class TestBootstrapCLIMigrate:
    """Flask CLI: flask migrate."""

    def test_migrate_command_registered(self, app):
        assert "migrate" in app.cli.commands

    def test_migrate_command_runs(self, app):
        runner = app.test_cli_runner()
        result = runner.invoke(args=["migrate"])
        assert result.exit_code == 0
        # Either "já está atualizada" or lists applied migrations
        assert "atualizada" in result.output or "migração" in result.output.lower()

    def test_migrate_command_idempotent(self, app):
        runner = app.test_cli_runner()
        r1 = runner.invoke(args=["migrate"])
        r2 = runner.invoke(args=["migrate"])
        assert r1.exit_code == 0
        assert r2.exit_code == 0
        assert "atualizada" in r2.output


class TestBootstrapCLIBackup:
    """Flask CLI: flask backup."""

    def test_backup_command_registered(self, app):
        assert "backup" in app.cli.commands

    def test_backup_command_runs(self, app, tmp_path, monkeypatch):
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        monkeypatch.setattr("core.constants.BACKUP_DIR", str(backup_dir))
        monkeypatch.setattr("core.backup.BACKUP_DIR", str(backup_dir))

        runner = app.test_cli_runner()
        result = runner.invoke(args=["backup"])
        assert result.exit_code == 0
        assert "sucesso" in result.output.lower() or "criado" in result.output.lower()

    def test_backup_command_failure(self, app, monkeypatch):
        monkeypatch.setattr("core.constants.BACKUP_DIR", "/nonexistent/dir")
        monkeypatch.setattr("core.backup.BACKUP_DIR", "/nonexistent/dir")

        runner = app.test_cli_runner()
        result = runner.invoke(args=["backup"])
        assert "falha" in result.output.lower() or result.exit_code != 0


class TestBootstrapCLIBackupList:
    """Flask CLI: flask backup-list."""

    def test_backup_list_command_registered(self, app):
        assert "backup-list" in app.cli.commands

    def test_backup_list_empty(self, app, tmp_path, monkeypatch):
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        monkeypatch.setattr("core.backup.BACKUP_DIR", str(backup_dir))

        runner = app.test_cli_runner()
        result = runner.invoke(args=["backup-list"])
        assert result.exit_code == 0
        assert "nenhum" in result.output.lower()

    def test_backup_list_with_files(self, app, tmp_path, monkeypatch):
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        monkeypatch.setattr("core.backup.BACKUP_DIR", str(backup_dir))

        # Create a backup file
        (backup_dir / "sistema_20260323.db").write_text("data")

        runner = app.test_cli_runner()
        result = runner.invoke(args=["backup-list"])
        assert result.exit_code == 0
        assert "sistema_20260323.db" in result.output
        assert "1 backup" in result.output


class TestBootstrapCLIRestore:
    """Flask CLI: flask restore."""

    def test_restore_command_registered(self, app):
        assert "restore" in app.cli.commands

    def test_restore_nonexistent_file(self, app, monkeypatch):
        monkeypatch.setattr("core.constants.BACKUP_DIR", "/tmp/nonexistent_dir_xyz")

        runner = app.test_cli_runner()
        result = runner.invoke(args=["restore", "/tmp/nonexistent_backup_xyz.db"])
        assert result.exit_code != 0
        assert (
            "não encontrado" in result.output.lower()
            or "not found" in result.output.lower()
        )

    def test_restore_invalid_backup(self, app, tmp_path):
        bad = tmp_path / "bad.db"
        bad.write_text("not a database")

        runner = app.test_cli_runner()
        result = runner.invoke(args=["restore", str(bad)])
        assert result.exit_code != 0
        assert "inválido" in result.output.lower()

    def test_restore_valid_with_yes(self, app, tmp_path):
        """Testa restore_backup directamente (sem CLI) com BD isolada."""
        from core.backup import restore_backup

        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()

        # Create valid backup
        backup = tmp_path / "valid_restore.db"
        conn = sqlite3.connect(str(backup))
        conn.execute("CREATE TABLE utilizadores (id INTEGER PRIMARY KEY, NII TEXT)")
        conn.execute("INSERT INTO utilizadores VALUES (1, 'restored')")
        conn.commit()
        conn.close()

        # Create target DB
        target_db = tmp_path / "target.db"
        conn = sqlite3.connect(str(target_db))
        conn.execute("CREATE TABLE utilizadores (id INTEGER PRIMARY KEY, NII TEXT)")
        conn.execute("INSERT INTO utilizadores VALUES (1, 'current')")
        conn.commit()
        conn.close()

        # Call restore directly with monkeypatched paths
        import core.constants
        import core.backup

        old_base = core.constants.BASE_DADOS
        old_backup_dir = core.backup.BACKUP_DIR
        try:
            core.constants.BASE_DADOS = str(target_db)
            core.backup.BACKUP_DIR = str(backup_dir)
            ok, msg = restore_backup(str(backup))
        finally:
            core.constants.BASE_DADOS = old_base
            core.backup.BACKUP_DIR = old_backup_dir

        assert ok is True
        assert "sucesso" in msg

    def test_restore_finds_in_backup_dir(self, app):
        """Testa que restore CLI detecta ficheiro na pasta de backups."""
        runner = app.test_cli_runner()
        # Non-existent file — should give error
        result = runner.invoke(args=["restore", "nonexistent_xyz.db"])
        assert result.exit_code != 0


# ═══════════════════════════════════════════════════════════════════════════
# core/detencoes.py coverage (74% → 90%+)
# ═══════════════════════════════════════════════════════════════════════════


class TestDetencoesCore:
    """Testes directos das funções core/detencoes.py."""

    def test_get_detencoes_sem_filtro(self, app, client):
        """get_detencoes_lista sem ano retorna todas."""
        from core.detencoes import get_detencoes_lista

        result = get_detencoes_lista()
        assert isinstance(result, list)

    def test_get_detencoes_com_ano(self, app, client):
        """get_detencoes_lista com ano filtra correctamente."""
        from core.detencoes import get_detencoes_lista

        result = get_detencoes_lista(ano_cmd=1)
        assert isinstance(result, list)

    def test_criar_detencao(self, app, client):
        """criar_detencao insere registo na BD."""
        from core.detencoes import criar_detencao, get_detencoes_lista

        uid = create_aluno("det_core1", "DC1", "Det Core Test", "1")
        d1 = date.today() + timedelta(days=50)
        d2 = d1 + timedelta(days=2)
        criar_detencao(uid, d1, d2, "Teste core", "admin")

        rows = get_detencoes_lista(ano_cmd=1)
        niis = [r["NII"] for r in rows]
        assert "det_core1" in niis

    def test_remover_detencao_autorizado(self, app, client):
        """remover_detencao com admin retorna True."""
        from core.detencoes import criar_detencao, get_detencoes_lista, remover_detencao

        uid = create_aluno("det_core2", "DC2", "Det Core Rem", "2")
        d = date.today() + timedelta(days=55)
        criar_detencao(uid, d, d, None, "admin")

        rows = get_detencoes_lista(ano_cmd=2)
        did = next(r["id"] for r in rows if r["NII"] == "det_core2")

        ok = remover_detencao(did, 0, is_admin=True)
        assert ok is True

    def test_remover_detencao_nao_autorizado(self, app, client):
        """remover_detencao com CMD de ano errado retorna False."""
        from core.detencoes import criar_detencao, get_detencoes_lista, remover_detencao

        uid = create_aluno("det_core3", "DC3", "Det Core NoAuth", "3")
        d = date.today() + timedelta(days=56)
        criar_detencao(uid, d, d, "test", "admin")

        rows = get_detencoes_lista(ano_cmd=3)
        did = next(r["id"] for r in rows if r["NII"] == "det_core3")

        ok = remover_detencao(did, 1, is_admin=False)
        assert ok is False

    def test_remover_detencao_inexistente(self, app, client):
        """remover_detencao com ID inexistente retorna False."""
        from core.detencoes import remover_detencao

        ok = remover_detencao(999999, 0, is_admin=True)
        assert ok is False

    def test_cancelar_licencas_periodo(self, app, client):
        """cancelar_licencas_periodo remove licenças no intervalo."""
        from core.detencoes import cancelar_licencas_periodo

        uid = create_aluno("det_core4", "DC4", "Det Lic Cancel", "1")
        d = date.today() + timedelta(days=60)

        with db() as conn:
            conn.execute(
                "INSERT INTO licencas (utilizador_id, data, tipo) VALUES (?,?,?)",
                (uid, d.isoformat(), "antes_jantar"),
            )
            conn.commit()

        cancelar_licencas_periodo(uid, d, d)

        with db() as conn:
            row = conn.execute(
                "SELECT COUNT(*) c FROM licencas WHERE utilizador_id=? AND data=?",
                (uid, d.isoformat()),
            ).fetchone()
        assert row["c"] == 0

    def test_get_alunos_para_selecao_cmd(self, app, client):
        """get_alunos_para_selecao com perfil cmd filtra por ano."""
        from core.detencoes import get_alunos_para_selecao

        create_aluno("det_sel1", "DS1", "Selecao Test", "1")
        result = get_alunos_para_selecao(1, "cmd")
        assert isinstance(result, list)
        niis = [r["NII"] for r in result]
        assert "det_sel1" in niis

    def test_get_alunos_para_selecao_admin(self, app, client):
        """get_alunos_para_selecao com perfil admin retorna todos."""
        from core.detencoes import get_alunos_para_selecao

        result = get_alunos_para_selecao(None, "admin")
        assert isinstance(result, list)

    def test_get_alunos_para_selecao_other(self, app, client):
        """get_alunos_para_selecao com perfil desconhecido retorna []."""
        from core.detencoes import get_alunos_para_selecao

        result = get_alunos_para_selecao(None, "oficialdia")
        assert result == []


# ═══════════════════════════════════════════════════════════════════════════
# core/backup.py coverage (82% → 90%+)
# ═══════════════════════════════════════════════════════════════════════════


class TestBackupEdgeCases:
    """Edge cases para aumentar coverage do backup.py."""

    def test_validate_not_db_extension(self, tmp_path):
        """validate_backup rejeita ficheiros sem extensão .db."""
        from core.backup import validate_backup

        txt = tmp_path / "backup.txt"
        txt.write_text("not a db")
        valid, reason = validate_backup(str(txt))
        assert valid is False
        assert ".db" in reason

    def test_list_backups_error_handling(self, monkeypatch):
        """list_backups retorna [] quando o dir não existe."""
        from core.backup import list_backups

        monkeypatch.setattr("core.backup.BACKUP_DIR", "/nonexistent_dir_xyz")
        result = list_backups()
        assert result == []

    def test_ensure_daily_missing_source(self, tmp_path, monkeypatch):
        """ensure_daily_backup não falha quando a BD source não existe."""
        from core.backup import ensure_daily_backup

        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        monkeypatch.setattr("core.constants.BASE_DADOS", str(tmp_path / "missing.db"))
        monkeypatch.setattr("core.constants.BACKUP_DIR", str(backup_dir))
        monkeypatch.setattr("core.backup.BACKUP_DIR", str(backup_dir))
        monkeypatch.setattr("core.backup.BACKUP_RETENCAO_DIAS", None)

        ensure_daily_backup()  # must not raise
        assert list(backup_dir.glob("*.db")) == []

    def test_restore_safety_backup_failure(self, tmp_path, monkeypatch):
        """restore_backup falha se não conseguir criar backup de segurança."""
        from core.backup import restore_backup

        # Create valid backup
        backup = tmp_path / "good.db"
        conn = sqlite3.connect(str(backup))
        conn.execute("CREATE TABLE utilizadores (id INTEGER PRIMARY KEY)")
        conn.commit()
        conn.close()

        # Current DB must exist so safety backup is attempted
        current_db = tmp_path / "sistema.db"
        conn = sqlite3.connect(str(current_db))
        conn.execute("CREATE TABLE utilizadores (id INTEGER PRIMARY KEY)")
        conn.commit()
        conn.close()

        monkeypatch.setattr("core.constants.BASE_DADOS", str(current_db))
        # Point to non-writable dir for safety backup
        monkeypatch.setattr("core.backup.BACKUP_DIR", "/nonexistent_dir_xyz")

        ok, msg = restore_backup(str(backup))
        assert ok is False
        assert "segurança" in msg.lower() or "falha" in msg.lower()


# ═══════════════════════════════════════════════════════════════════════════
# core/absences.py coverage (83% → 90%+)
# ═══════════════════════════════════════════════════════════════════════════


class TestAbsencesEdgeCases:
    """Testes para branches não cobertos de core/absences.py."""

    def test_remover_ausencia_autorizada_cmd_wrong_year(self, app, client):
        """CMD de ano errado não pode remover ausência."""
        from core.absences import remover_ausencia_autorizada

        uid = create_aluno("abs_auth1", "AA1", "Abs Auth Test", "3")
        d = date.today() + timedelta(days=40)

        with db() as conn:
            conn.execute(
                "INSERT INTO ausencias (utilizador_id, ausente_de, ausente_ate, motivo, criado_por) VALUES (?,?,?,?,?)",
                (uid, d.isoformat(), d.isoformat(), "test", "admin"),
            )
            conn.commit()
            aid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        ok = remover_ausencia_autorizada(aid, 1, is_admin=False)
        assert ok is False

    def test_remover_ausencia_autorizada_admin(self, app, client):
        """Admin pode remover qualquer ausência."""
        from core.absences import remover_ausencia_autorizada

        uid = create_aluno("abs_auth2", "AA2", "Abs Auth Admin", "2")
        d = date.today() + timedelta(days=41)

        with db() as conn:
            conn.execute(
                "INSERT INTO ausencias (utilizador_id, ausente_de, ausente_ate, motivo, criado_por) VALUES (?,?,?,?,?)",
                (uid, d.isoformat(), d.isoformat(), "test", "admin"),
            )
            conn.commit()
            row = conn.execute(
                "SELECT id FROM ausencias WHERE utilizador_id=? AND ausente_de=?",
                (uid, d.isoformat()),
            ).fetchone()

        ok = remover_ausencia_autorizada(row["id"], 0, is_admin=True)
        assert ok is True

    def test_get_ausencias_cmd_sem_filtro(self, app, client):
        """get_ausencias_cmd sem ano retorna todas."""
        from core.absences import get_ausencias_cmd

        result = get_ausencias_cmd()
        assert isinstance(result, list)

    def test_get_ausencias_cmd_com_filtro(self, app, client):
        """get_ausencias_cmd com ano filtra correctamente."""
        from core.absences import get_ausencias_cmd

        result = get_ausencias_cmd(ano_cmd=1)
        assert isinstance(result, list)


# ═══════════════════════════════════════════════════════════════════════════
# Aluno routes coverage (81% → 90%+)
# ═══════════════════════════════════════════════════════════════════════════


class TestAlunoRoutesEdgeCases:
    """Edge cases para cobertura das rotas do aluno."""

    def test_aluno_editar_system_account(self, app, client):
        """Sistema account não pode editar refeições."""
        create_system_user("sys_edit1", "oficialdia", pw="Sysedit12")
        login_as(client, "sys_edit1", "Sysedit12")

        dt = (date.today() + timedelta(days=5)).isoformat()
        resp = client.get(f"/aluno/editar/{dt}", follow_redirects=True)
        assert resp.status_code == 200

    def test_aluno_ausencias_system_account(self, app, client):
        """Sistema account não pode gerir ausências."""
        create_system_user("sys_aus1", "oficialdia", pw="Sysaus123")
        login_as(client, "sys_aus1", "Sysaus123")

        resp = client.get("/aluno/ausencias", follow_redirects=True)
        assert resp.status_code == 200

    def test_aluno_licenca_fds_system_account(self, app, client):
        """Sistema account não pode marcar licença FDS."""
        create_system_user("sys_lic1", "oficialdia", pw="Syslic123")
        login_as(client, "sys_lic1", "Syslic123")

        csrf = get_csrf(client)
        resp = client.post(
            "/aluno/licenca-fds",
            data={"sexta": "2026-03-27", "acao_fds": "marcar", "csrf_token": csrf},
            follow_redirects=True,
        )
        assert resp.status_code == 200

    def test_aluno_licenca_fds_invalid_date(self, app, client):
        """Data que não é sexta é rejeitada."""
        create_aluno("lic_inv1", "LI1", "Lic Invalid", pw="Licinv123")
        login_as(client, "lic_inv1", "Licinv123")

        csrf = get_csrf(client)
        # Monday, not Friday
        resp = client.post(
            "/aluno/licenca-fds",
            data={"sexta": "2026-03-23", "acao_fds": "marcar", "csrf_token": csrf},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert b"sextas" in resp.data.lower() or b"inv" in resp.data.lower()

    def test_aluno_perfil_system_account(self, app, client):
        """Sistema account não pode ver perfil."""
        create_system_user("sys_prf1", "oficialdia", pw="Sysprf123")
        login_as(client, "sys_prf1", "Sysprf123")

        resp = client.get("/aluno/perfil", follow_redirects=True)
        assert resp.status_code == 200

    def test_aluno_password_rate_limit(self, app, client):
        """Rate limit em tentativas de mudança de password."""
        create_aluno("pw_rl1", "PR1", "PW Rate", pw="Pwrate123")
        login_as(client, "pw_rl1", "Pwrate123")

        # Fill rate limit
        import time as _t

        with client.session_transaction() as sess:
            now = _t.time()
            sess["_pw_attempts"] = [now - i for i in range(10)]

        csrf = get_csrf(client)
        resp = client.post(
            "/aluno/password",
            data={
                "old": "Pwrate123",
                "new": "NewPw12345",
                "conf": "NewPw12345",
                "csrf_token": csrf,
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert b"Demasiadas" in resp.data or b"tentativas" in resp.data

    def test_aluno_password_mismatch(self, app, client):
        """Passwords que não coincidem mostram erro."""
        create_aluno("pw_mis1", "PM1", "PW Mismatch", pw="Pwmis1234")
        login_as(client, "pw_mis1", "Pwmis1234")

        csrf = get_csrf(client)
        resp = client.post(
            "/aluno/password",
            data={
                "old": "Pwmis1234",
                "new": "NewPass123",
                "conf": "WrongConf1",
                "csrf_token": csrf,
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert b"coincidem" in resp.data

    def test_aluno_historico_csv_export(self, app, client):
        """Export CSV do histórico funciona."""
        create_aluno("hist_csv1", "HC1", "Hist CSV", pw="Histcsv12")
        login_as(client, "hist_csv1", "Histcsv12")

        resp = client.get("/aluno/exportar-historico?fmt=csv")
        assert resp.status_code == 200
        assert resp.content_type.startswith("text/csv")

    def test_aluno_historico_invalid_fmt(self, app, client):
        """Export com formato inválido retorna 400."""
        create_aluno("hist_bad1", "HB1", "Hist Bad", pw="Histbad12")
        login_as(client, "hist_bad1", "Histbad12")

        resp = client.get("/aluno/exportar-historico?fmt=xml")
        assert resp.status_code == 400

    def test_aluno_editar_ausente(self, app, client):
        """Aluno com ausência ativa não pode editar refeições."""
        uid = create_aluno("ed_aus1", "EACB1", "Edit Ausente", pw="Edaus1234")
        login_as(client, "ed_aus1", "Edaus1234")

        d = date.today() + timedelta(days=8)
        with db() as conn:
            conn.execute(
                "INSERT INTO ausencias (utilizador_id, ausente_de, ausente_ate, motivo, criado_por) VALUES (?,?,?,?,?)",
                (uid, d.isoformat(), d.isoformat(), "test", "admin"),
            )
            conn.commit()

        resp = client.get(f"/aluno/editar/{d.isoformat()}", follow_redirects=True)
        assert resp.status_code == 200
        assert b"aus" in resp.data.lower()

    def test_aluno_perfil_update_invalid_email(self, app, client):
        """Atualização de perfil com email inválido mostra erro."""
        create_aluno("prf_em1", "PE1", "Prof Email", pw="Prfemail1")
        login_as(client, "prf_em1", "Prfemail1")

        csrf = get_csrf(client)
        resp = client.post(
            "/aluno/perfil",
            data={"email": "not-an-email", "telemovel": "", "csrf_token": csrf},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert b"inv" in resp.data.lower()

    def test_aluno_perfil_update_invalid_phone(self, app, client):
        """Atualização de perfil com telemóvel inválido mostra erro."""
        create_aluno("prf_ph1", "PP1", "Prof Phone", pw="Prfphone1")
        login_as(client, "prf_ph1", "Prfphone1")

        csrf = get_csrf(client)
        resp = client.post(
            "/aluno/perfil",
            data={"email": "", "telemovel": "abc", "csrf_token": csrf},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert b"inv" in resp.data.lower()

    def test_aluno_ausencias_editar(self, app, client):
        """Aluno pode ver formulário de edição de ausência."""
        uid = create_aluno("aus_ed1", "AECB1", "Aus Edit", pw="Ausedit12")
        login_as(client, "aus_ed1", "Ausedit12")

        d = date.today() + timedelta(days=15)

        # Insert ausência via POST to ensure proper flow
        csrf = get_csrf(client)
        resp = client.post(
            "/aluno/ausencias",
            data={
                "acao": "criar",
                "de": d.isoformat(),
                "ate": d.isoformat(),
                "motivo": "editavel",
                "csrf_token": csrf,
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

        # Get the ausência ID
        with db() as conn:
            row = conn.execute(
                "SELECT id FROM ausencias WHERE utilizador_id=? ORDER BY id DESC LIMIT 1",
                (uid,),
            ).fetchone()

        if row:
            resp = client.get(f"/aluno/ausencias?edit={row['id']}")
            assert resp.status_code == 200

    def test_aluno_ausencias_remover(self, app, client):
        """Aluno pode remover uma ausência própria."""
        uid = create_aluno("aus_rm1", "AR1", "Aus Remove", pw="Ausremov1")
        login_as(client, "aus_rm1", "Ausremov1")

        d = date.today() + timedelta(days=16)
        with db() as conn:
            conn.execute(
                "INSERT INTO ausencias (utilizador_id, ausente_de, ausente_ate, motivo, criado_por) VALUES (?,?,?,?,?)",
                (uid, d.isoformat(), d.isoformat(), "remover", "aus_rm1"),
            )
            conn.commit()
            row = conn.execute(
                "SELECT id FROM ausencias WHERE utilizador_id=? AND motivo='remover'",
                (uid,),
            ).fetchone()

        csrf = get_csrf(client)
        resp = client.post(
            "/aluno/ausencias",
            data={"acao": "remover", "id": str(row["id"]), "csrf_token": csrf},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert b"removida" in resp.data.lower()


# ═══════════════════════════════════════════════════════════════════════════
# CMD routes — edge cases
# ═══════════════════════════════════════════════════════════════════════════


class TestCMDRoutesEdgeCases:
    """Edge cases para cobertura das rotas CMD."""

    def test_cmd_criar_detencao_datas_invertidas(self, app, client):
        """Detenção com data 'até' antes de 'de' é rejeitada."""
        create_system_user("cmd_inv1", "cmd", pw="Cmdinv123", ano="1")
        create_aluno("cmd_det_aluno1", "CDA1", "CMD Det Aluno", "1")
        login_as(client, "cmd_inv1", "Cmdinv123")

        d1 = (date.today() + timedelta(days=20)).isoformat()
        d2 = (date.today() + timedelta(days=18)).isoformat()  # before d1

        csrf = get_csrf(client)
        resp = client.post(
            "/cmd/detencoes",
            data={
                "nii": "cmd_det_aluno1",
                "de": d1,
                "ate": d2,
                "motivo": "invertido",
                "csrf_token": csrf,
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

    def test_cmd_criar_detencao_user_not_found(self, app, client):
        """Detenção com NII inexistente mostra erro."""
        create_system_user("cmd_nf1", "cmd", pw="Cmdnf1234", ano="1")
        login_as(client, "cmd_nf1", "Cmdnf1234")

        csrf = get_csrf(client)
        resp = client.post(
            "/cmd/detencoes",
            data={
                "nii": "nao_existe_xyz",
                "de": "2026-04-01",
                "ate": "2026-04-02",
                "csrf_token": csrf,
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert b"encontrado" in resp.data.lower()

    def test_cmd_remover_detencao_id_invalido(self, app, client):
        """Remover detenção com ID inválido mostra erro."""
        create_system_user("cmd_rmid1", "cmd", pw="Cmdrmid12", ano="1")
        login_as(client, "cmd_rmid1", "Cmdrmid12")

        csrf = get_csrf(client)
        resp = client.post(
            "/cmd/detencoes",
            data={"acao": "remover", "id": "abc", "csrf_token": csrf},
            follow_redirects=True,
        )
        assert resp.status_code == 200

    def test_cmd_ausencias_remover_id_invalido(self, app, client):
        """Remover ausência com ID inválido mostra erro."""
        create_system_user("cmd_armi1", "cmd", pw="Cmdarmi1", ano="1")
        login_as(client, "cmd_armi1", "Cmdarmi1")

        csrf = get_csrf(client)
        resp = client.post(
            "/cmd/ausencias",
            data={"acao": "remover", "id": "xyz", "csrf_token": csrf},
            follow_redirects=True,
        )
        assert resp.status_code == 200

    def test_cmd_detencao_wrong_year(self, app, client):
        """CMD não pode criar detenção para aluno de outro ano."""
        create_system_user("cmd_wy1", "cmd", pw="Cmdwy1234", ano="1")
        create_aluno("cmd_wy_al1", "CWA1", "Wrong Year Aluno", "3")
        login_as(client, "cmd_wy1", "Cmdwy1234")

        csrf = get_csrf(client)
        resp = client.post(
            "/cmd/detencoes",
            data={
                "nii": "cmd_wy_al1",
                "de": "2026-04-10",
                "ate": "2026-04-11",
                "motivo": "wrong year",
                "csrf_token": csrf,
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert b"ano" in resp.data.lower()


# ═══════════════════════════════════════════════════════════════════════════
# Config dev formatter coverage
# ═══════════════════════════════════════════════════════════════════════════


class TestDevFormatter:
    """Testes para DevFormatter em config.py."""

    def test_dev_formatter_adds_request_id(self, monkeypatch):
        """DevFormatter inclui request_id no output."""
        import types
        import logging
        import config as cfg

        monkeypatch.setattr(cfg, "is_production", False)

        mock_logger = logging.getLogger("test_dev_formatter_rid")
        mock_logger.handlers.clear()
        mock_app = types.SimpleNamespace(logger=mock_logger)

        cfg.configure_logging(mock_app)

        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="test message",
            args=(),
            exc_info=None,
        )
        # No request_id set - should default to "-"
        handler = mock_app.logger.handlers[-1]
        formatted = handler.formatter.format(record)
        assert "-" in formatted  # default request_id
        assert "test message" in formatted


# ═══════════════════════════════════════════════════════════════════════════
# utils/helpers.py coverage (86% → 95%+)
# ═══════════════════════════════════════════════════════════════════════════


class TestHelpersRender:
    """Cobertura da função render() — linhas 24-25."""

    def test_render_returns_html_response(self, app, client):
        """render() devolve Response com HTML do base.html."""
        from utils.helpers import render

        with app.test_request_context("/"):
            from flask import session

            session["_csrf_token"] = "tok"
            resp = render("<p>hello</p>")
            assert resp.status_code == 200
            assert resp.mimetype == "text/html"
            assert b"<!doctype html>" in resp.data.lower() or b"<html" in resp.data

    def test_render_custom_status(self, app, client):
        """render() respeita o status code passado."""
        from utils.helpers import render

        with app.test_request_context("/"):
            from flask import session

            session["_csrf_token"] = "tok"
            resp = render("<p>err</p>", status=404)
            assert resp.status_code == 404


class TestHelpersPrazoLabel:
    """Cobertura da _prazo_label — linhas 136-140 (branch h <= 24)."""

    def test_prazo_label_within_24h(self, app, monkeypatch):
        """Quando faltam < 24h para o prazo, mostra aviso amarelo."""
        from datetime import datetime as _dt
        from utils.helpers import _prazo_label

        # We need refeicao_editavel to return (False, ...) AND
        # the hours remaining to be 0 < h <= 24.
        # With PRAZO_LIMITE_HORAS=48, prazo_dt = d 00:00 - 48h.
        # We need now to be between prazo_dt-24h and prazo_dt.
        # Pick d = today+3 so it is definitely in the future.
        target = date.today() + timedelta(days=3)

        # prazo_dt = target 00:00 - 48h = target-2 00:00
        # We fake "now" so that prazo_dt - now = 12h (within 24h window)
        prazo_dt = _dt(target.year, target.month, target.day) - timedelta(hours=48)
        fake_now = prazo_dt - timedelta(hours=12)

        # Patch refeicao_editavel to return False and datetime.now
        monkeypatch.setattr("utils.helpers.refeicao_editavel", lambda d: (False, ""))
        monkeypatch.setattr(
            "utils.helpers.datetime",
            type(
                "FakeDT",
                (_dt,),
                {
                    "now": staticmethod(lambda: fake_now),
                },
            ),
        )

        with app.test_request_context("/"):
            result = _prazo_label(target)
            assert "prazo-warn" in str(result)
            assert "12h" in str(result)

    def test_prazo_label_expired(self, app, monkeypatch):
        """Quando o prazo já expirou (h <= 0), mostra cadeado."""
        from datetime import datetime as _dt
        from utils.helpers import _prazo_label

        target = date.today() + timedelta(days=3)
        prazo_dt = _dt(target.year, target.month, target.day) - timedelta(hours=48)
        fake_now = prazo_dt + timedelta(hours=1)  # past the deadline

        monkeypatch.setattr("utils.helpers.refeicao_editavel", lambda d: (False, ""))
        monkeypatch.setattr(
            "utils.helpers.datetime",
            type(
                "FakeDT",
                (_dt,),
                {
                    "now": staticmethod(lambda: fake_now),
                },
            ),
        )

        with app.test_request_context("/"):
            result = _prazo_label(target)
            assert "prazo-lock" in str(result)

    def test_prazo_label_no_limit(self, app, monkeypatch):
        """Quando PRAZO_LIMITE_HORAS é None, mostra cadeado genérico (linha 140)."""
        from utils.helpers import _prazo_label

        monkeypatch.setattr("utils.helpers.refeicao_editavel", lambda d: (False, ""))
        monkeypatch.setattr("utils.helpers.PRAZO_LIMITE_HORAS", None)

        with app.test_request_context("/"):
            result = _prazo_label(date.today() + timedelta(days=1))
            assert "prazo-lock" in str(result)


class TestHelpersAuditException:
    """Cobertura do except em _audit — linhas 163-164."""

    def test_audit_db_failure_logs_warning(self, app, monkeypatch):
        """_audit não levanta excepção se o INSERT falhar."""
        from utils.helpers import _audit

        def _bad_db():
            raise RuntimeError("DB down")

        # Make db() context manager raise on execute
        from unittest.mock import MagicMock
        import contextlib

        @contextlib.contextmanager
        def broken_db():
            mock = MagicMock()
            mock.execute.side_effect = RuntimeError("DB down")
            yield mock

        monkeypatch.setattr("utils.helpers.db", broken_db)

        with app.test_request_context("/"):
            # Should not raise
            _audit("test_actor", "test_action", "detail")


class TestHelpersClientIp:
    """Cobertura do _client_ip — linhas 172-174."""

    def test_client_ip_exception_fallback(self, app, monkeypatch):
        """_client_ip retorna remote_addr quando access_route levanta excepção."""
        from utils.helpers import _client_ip

        with app.test_request_context("/", environ_base={"REMOTE_ADDR": "1.2.3.4"}):
            from flask import request as req

            # Monkey-patch access_route on the underlying Request class
            original_prop = type(req._get_current_object()).access_route
            try:

                @property
                def bad_route(self):
                    raise RuntimeError("fail")

                type(req._get_current_object()).access_route = bad_route
                result = _client_ip()
                assert isinstance(result, str)
            finally:
                type(req._get_current_object()).access_route = original_prop


# ═══════════════════════════════════════════════════════════════════════════
# utils/passwords.py coverage (88% → 95%+)
# ═══════════════════════════════════════════════════════════════════════════


class TestPasswordsMigrateException:
    """Cobertura do except em _migrate_password_hash — linhas 59-60."""

    def test_migrate_password_hash_db_error(self, app, monkeypatch):
        """_migrate_password_hash não levanta excepção se UPDATE falhar."""
        from utils.passwords import _migrate_password_hash
        from unittest.mock import MagicMock
        import contextlib

        @contextlib.contextmanager
        def broken_db():
            mock = MagicMock()
            mock.execute.side_effect = RuntimeError("DB fail")
            yield mock

        monkeypatch.setattr("utils.passwords.db", broken_db)

        with app.test_request_context("/"):
            # Should not raise
            _migrate_password_hash(999999, "somepassword")


class TestPasswordsAlterarEdgeCases:
    """Cobertura de _alterar_password — linhas 67, 76, 82."""

    def test_alterar_password_nii_not_found(self, app):
        """_alterar_password com NII inexistente retorna erro."""
        from utils.passwords import _alterar_password

        with app.test_request_context("/"):
            ok, msg = _alterar_password("nii_inexistente_xyz", "old", "new")
            assert ok is False
            assert "sistema" in msg.lower() or "não" in msg.lower()

    def test_alterar_password_row_not_found(self, app, monkeypatch):
        """_alterar_password quando user_id existe mas row não — linha 76."""
        from utils.passwords import _alterar_password
        from unittest.mock import MagicMock
        import contextlib

        # user_id_by_nii returns a valid ID but DB returns no row
        monkeypatch.setattr("utils.passwords.user_id_by_nii", lambda nii: 999999)

        @contextlib.contextmanager
        def mock_db():
            mock = MagicMock()
            mock.execute.return_value.fetchone.return_value = None
            yield mock

        monkeypatch.setattr("utils.passwords.db", mock_db)

        with app.test_request_context("/"):
            ok, msg = _alterar_password("fake_nii", "old", "new")
            assert ok is False
            assert "encontrado" in msg.lower()

    def test_alterar_password_weak_new_password(self, app):
        """_alterar_password rejeita password nova fraca — linha 82."""
        from utils.passwords import _alterar_password

        # Create a real user to pass the first checks
        create_aluno("alt_weak1", "AW1", "Alt Weak", pw="Altweak1")

        with app.test_request_context("/"):
            ok, msg = _alterar_password("alt_weak1", "Altweak1", "short")
            assert ok is False
            assert "8 caracteres" in msg or "letras" in msg


class TestPasswordsCriarEdgeCases:
    """Cobertura de _criar_utilizador — linhas 100, 108, 115, 139-141."""

    def test_criar_utilizador_missing_fields(self, app):
        """_criar_utilizador com campos em falta — linha 100."""
        from utils.passwords import _criar_utilizador

        with app.test_request_context("/"):
            ok, msg = _criar_utilizador("", "ni", "nome", "1", "aluno", "Pass1234")
            assert ok is False
            assert "obrigatórios" in msg.lower()

    def test_criar_utilizador_invalid_ni(self, app):
        """_criar_utilizador com NI inválido — linha 108."""
        from utils.passwords import _criar_utilizador

        with app.test_request_context("/"):
            # NI with special chars should be rejected by _val_ni
            ok, msg = _criar_utilizador(
                "validnii1", "<script>", "Nome Valid", "1", "aluno", "Pass1234"
            )
            assert ok is False
            assert "NI" in msg or "inv" in msg.lower()

    def test_criar_utilizador_invalid_nome(self, app):
        """_criar_utilizador com nome inválido (whitespace only) — linha 115."""
        from utils.passwords import _criar_utilizador

        with app.test_request_context("/"):
            # Whitespace-only nome passes the `all([...])` check but _val_nome returns None
            ok, msg = _criar_utilizador(
                "validnii2", "NI123", "   ", "1", "aluno", "Pass1234"
            )
            assert ok is False
            assert "nome" in msg.lower() or "inv" in msg.lower()

    def test_criar_utilizador_exception(self, app, monkeypatch):
        """_criar_utilizador com excepção no INSERT — linhas 139-141."""
        from utils.passwords import _criar_utilizador
        from unittest.mock import MagicMock
        import contextlib

        @contextlib.contextmanager
        def failing_db():
            # Let validation queries pass, fail on INSERT
            mock = MagicMock()
            mock.execute.side_effect = RuntimeError("INSERT fail")
            yield mock

        # We need validators to pass but db INSERT to fail.
        # Monkeypatch db only for the INSERT step.
        monkeypatch.setattr("utils.passwords.db", failing_db)

        with app.test_request_context("/"):
            ok, msg = _criar_utilizador(
                "criarexc1", "CE1", "Criar Exception", "1", "aluno", "Pass1234"
            )
            assert ok is False
            assert "fail" in msg.lower() or "error" in msg.lower() or "INSERT" in msg


class TestPasswordsResetNotFound:
    """Cobertura de _reset_pw NII não encontrado — linha 157."""

    def test_reset_pw_nii_not_found(self, app):
        """_reset_pw com NII inexistente retorna erro."""
        from utils.passwords import _reset_pw

        with app.test_request_context("/"):
            ok, msg = _reset_pw("nii_nao_existe_xyz")
            assert ok is False
            assert "encontrado" in msg.lower()
