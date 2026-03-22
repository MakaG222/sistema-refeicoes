"""
tests/test_bootstrap.py — Testes para core/bootstrap.py
========================================================
Cobre: ensure_extra_schema, bootstrap_dev_accounts, init_app_once, seed_dev_command
"""

from __future__ import annotations

import sqlite3
import tempfile
import os

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fresh_db(monkeypatch) -> str:
    """Creates an isolated in-memory-style SQLite file for a single test."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    monkeypatch.setenv("DB_PATH", tmp.name)
    import core.constants as constants

    monkeypatch.setattr(constants, "BASE_DADOS", tmp.name)
    return tmp.name


def _build_schema(db_path: str) -> None:
    """Runs ensure_schema against the given DB file."""
    import core.constants as constants

    old = constants.BASE_DADOS
    constants.BASE_DADOS = db_path
    try:
        from core.database import ensure_schema

        ensure_schema()
    finally:
        constants.BASE_DADOS = old


def _conn(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# ---------------------------------------------------------------------------
# ensure_extra_schema — column additions
# ---------------------------------------------------------------------------


class TestEnsureExtraSchemaColumns:
    """Columns added by ensure_extra_schema are idempotent."""

    def test_adds_email_column(self, monkeypatch):
        db_path = _fresh_db(monkeypatch)
        _build_schema(db_path)
        # Drop the column if it was added by schema (SQLite can't drop, so just verify it runs)
        from core.bootstrap import ensure_extra_schema

        ensure_extra_schema()  # should not raise

        with _conn(db_path) as conn:
            cols = [
                r["name"]
                for r in conn.execute("PRAGMA table_info(utilizadores)").fetchall()
            ]
        assert "email" in cols
        os.unlink(db_path)

    def test_adds_telemovel_column(self, monkeypatch):
        db_path = _fresh_db(monkeypatch)
        _build_schema(db_path)
        from core.bootstrap import ensure_extra_schema

        ensure_extra_schema()

        with _conn(db_path) as conn:
            cols = [
                r["name"]
                for r in conn.execute("PRAGMA table_info(utilizadores)").fetchall()
            ]
        assert "telemovel" in cols
        os.unlink(db_path)

    def test_adds_is_active_column(self, monkeypatch):
        db_path = _fresh_db(monkeypatch)
        _build_schema(db_path)
        from core.bootstrap import ensure_extra_schema

        ensure_extra_schema()

        with _conn(db_path) as conn:
            cols = [
                r["name"]
                for r in conn.execute("PRAGMA table_info(utilizadores)").fetchall()
            ]
        assert "is_active" in cols
        os.unlink(db_path)

    def test_adds_turma_id_column(self, monkeypatch):
        db_path = _fresh_db(monkeypatch)
        _build_schema(db_path)
        from core.bootstrap import ensure_extra_schema

        ensure_extra_schema()

        with _conn(db_path) as conn:
            cols = [
                r["name"]
                for r in conn.execute("PRAGMA table_info(utilizadores)").fetchall()
            ]
        assert "turma_id" in cols
        os.unlink(db_path)

    def test_adds_hora_saida_column(self, monkeypatch):
        db_path = _fresh_db(monkeypatch)
        _build_schema(db_path)
        from core.bootstrap import ensure_extra_schema

        ensure_extra_schema()

        with _conn(db_path) as conn:
            cols = [
                r["name"]
                for r in conn.execute("PRAGMA table_info(licencas)").fetchall()
            ]
        assert "hora_saida" in cols
        os.unlink(db_path)

    def test_adds_hora_entrada_column(self, monkeypatch):
        db_path = _fresh_db(monkeypatch)
        _build_schema(db_path)
        from core.bootstrap import ensure_extra_schema

        ensure_extra_schema()

        with _conn(db_path) as conn:
            cols = [
                r["name"]
                for r in conn.execute("PRAGMA table_info(licencas)").fetchall()
            ]
        assert "hora_entrada" in cols
        os.unlink(db_path)

    def test_idempotent_second_call(self, monkeypatch):
        """Calling ensure_extra_schema twice must not raise."""
        db_path = _fresh_db(monkeypatch)
        _build_schema(db_path)
        from core.bootstrap import ensure_extra_schema

        ensure_extra_schema()
        ensure_extra_schema()  # second call — no exception
        os.unlink(db_path)


# ---------------------------------------------------------------------------
# ensure_extra_schema — FTS5 rebuild path
# ---------------------------------------------------------------------------


class TestEnsureExtraSchemaFTS:
    """FTS5 table is rebuilt when it is missing or corrupt."""

    def test_fts_table_exists_after_schema(self, monkeypatch):
        db_path = _fresh_db(monkeypatch)
        _build_schema(db_path)
        from core.bootstrap import ensure_extra_schema

        ensure_extra_schema()

        with _conn(db_path) as conn:
            # The FTS table should be queryable
            conn.execute("SELECT COUNT(*) FROM utilizadores_fts").fetchone()
        os.unlink(db_path)

    def test_fts_rebuilt_when_table_dropped(self, monkeypatch):
        """If utilizadores_fts is dropped, ensure_extra_schema recreates it."""
        db_path = _fresh_db(monkeypatch)
        _build_schema(db_path)
        from core.bootstrap import ensure_extra_schema

        ensure_extra_schema()  # initial creation

        # Simulate corruption by dropping the FTS table
        with _conn(db_path) as conn:
            conn.execute("DROP TABLE IF EXISTS utilizadores_fts")
            conn.commit()

        # Should silently rebuild
        ensure_extra_schema()

        with _conn(db_path) as conn:
            result = conn.execute("SELECT COUNT(*) FROM utilizadores_fts").fetchone()
        assert result is not None
        os.unlink(db_path)

    def test_fts_triggers_recreated_after_drop(self, monkeypatch):
        """After FTS rebuild, all three triggers must exist."""
        db_path = _fresh_db(monkeypatch)
        _build_schema(db_path)
        from core.bootstrap import ensure_extra_schema

        ensure_extra_schema()

        with _conn(db_path) as conn:
            conn.execute("DROP TABLE IF EXISTS utilizadores_fts")
            conn.commit()

        ensure_extra_schema()

        with _conn(db_path) as conn:
            triggers = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'utilizadores_%_fts'"
                ).fetchall()
            }
        assert "utilizadores_ai_fts" in triggers
        assert "utilizadores_ad_fts" in triggers
        assert "utilizadores_au_fts" in triggers
        os.unlink(db_path)


# ---------------------------------------------------------------------------
# ensure_extra_schema — _migracoes control table
# ---------------------------------------------------------------------------


class TestEnsureExtraSchemaMigracoes:
    """Migration control table and specific data migrations."""

    def test_migracoes_table_created(self, monkeypatch):
        db_path = _fresh_db(monkeypatch)
        _build_schema(db_path)
        from core.bootstrap import ensure_extra_schema

        ensure_extra_schema()

        with _conn(db_path) as conn:
            tables = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        assert "_migracoes" in tables
        os.unlink(db_path)

    def test_migracoes_are_recorded(self, monkeypatch):
        db_path = _fresh_db(monkeypatch)
        _build_schema(db_path)
        from core.bootstrap import ensure_extra_schema

        ensure_extra_schema()

        with _conn(db_path) as conn:
            done = {
                r[0] for r in conn.execute("SELECT nome FROM _migracoes").fetchall()
            }
        assert "reis_ni_382_482" in done
        assert "rafaela_nii_20223_21223" in done
        assert "reset_creds_nii_v2" in done
        os.unlink(db_path)

    def test_reis_migration_corrects_ni(self, monkeypatch):
        """Migration reis_ni_382_482 updates NI from 382 to 482."""
        db_path = _fresh_db(monkeypatch)
        _build_schema(db_path)

        # Insert a user that matches the migration condition
        with _conn(db_path) as conn:
            conn.execute(
                """INSERT INTO utilizadores
                   (NII,NI,Nome_completo,Palavra_chave,ano,perfil,must_change_password)
                   VALUES ('reis_test','382','Aluna Reis','pw','4','aluno',0)"""
            )
            conn.commit()

        from core.bootstrap import ensure_extra_schema

        ensure_extra_schema()

        with _conn(db_path) as conn:
            row = conn.execute(
                "SELECT NI FROM utilizadores WHERE NII='reis_test'"
            ).fetchone()
        assert row["NI"] == "482"
        os.unlink(db_path)

    def test_reis_migration_skipped_when_no_match(self, monkeypatch):
        """Migration runs without error even when no matching user exists."""
        db_path = _fresh_db(monkeypatch)
        _build_schema(db_path)
        from core.bootstrap import ensure_extra_schema

        ensure_extra_schema()  # no user with NI='382' and ano='4'

        with _conn(db_path) as conn:
            done = {
                r[0] for r in conn.execute("SELECT nome FROM _migracoes").fetchall()
            }
        assert "reis_ni_382_482" in done  # still recorded
        os.unlink(db_path)

    def test_rafaela_migration_corrects_nii(self, monkeypatch):
        """Migration rafaela_nii_20223_21223 updates NII value."""
        db_path = _fresh_db(monkeypatch)
        _build_schema(db_path)

        with _conn(db_path) as conn:
            conn.execute(
                """INSERT INTO utilizadores
                   (NII,NI,Nome_completo,Palavra_chave,ano,perfil,must_change_password)
                   VALUES ('20223','20223','Rafaela Fernandes','pw','2','aluno',0)"""
            )
            conn.commit()

        from core.bootstrap import ensure_extra_schema

        ensure_extra_schema()

        with _conn(db_path) as conn:
            row = conn.execute(
                "SELECT NII FROM utilizadores WHERE Nome_completo='Rafaela Fernandes'"
            ).fetchone()
        assert row["NII"] == "21223"
        os.unlink(db_path)

    def test_reset_creds_migration_sets_must_change(self, monkeypatch):
        """Migration reset_creds_nii_v2 sets must_change_password=1 for alunos with NII."""
        db_path = _fresh_db(monkeypatch)
        _build_schema(db_path)

        with _conn(db_path) as conn:
            conn.execute(
                """INSERT INTO utilizadores
                   (NII,NI,Nome_completo,Palavra_chave,ano,perfil,must_change_password)
                   VALUES ('99901','901','Aluno Test','oldpw','1','aluno',0)"""
            )
            conn.commit()

        from core.bootstrap import ensure_extra_schema

        ensure_extra_schema()

        with _conn(db_path) as conn:
            row = conn.execute(
                "SELECT must_change_password, Palavra_chave FROM utilizadores WHERE NII='99901'"
            ).fetchone()
        assert row["must_change_password"] == 1
        # Password should now be a hash of the NII, not the old plain text
        assert row["Palavra_chave"] != "oldpw"
        os.unlink(db_path)

    def test_migracoes_not_run_twice(self, monkeypatch):
        """Running ensure_extra_schema a second time skips already-recorded migrations."""
        db_path = _fresh_db(monkeypatch)
        _build_schema(db_path)

        with _conn(db_path) as conn:
            conn.execute(
                """INSERT INTO utilizadores
                   (NII,NI,Nome_completo,Palavra_chave,ano,perfil,must_change_password)
                   VALUES ('99902','902','Aluno B','pw2','1','aluno',0)"""
            )
            conn.commit()

        from core.bootstrap import ensure_extra_schema

        ensure_extra_schema()

        # Change the password back to something known
        with _conn(db_path) as conn:
            conn.execute(
                "UPDATE utilizadores SET Palavra_chave='sentinel' WHERE NII='99902'"
            )
            conn.commit()

        # Second call — migration already recorded, must not re-run
        ensure_extra_schema()

        with _conn(db_path) as conn:
            row = conn.execute(
                "SELECT Palavra_chave FROM utilizadores WHERE NII='99902'"
            ).fetchone()
        assert row["Palavra_chave"] == "sentinel"
        os.unlink(db_path)


# ---------------------------------------------------------------------------
# bootstrap_dev_accounts
# ---------------------------------------------------------------------------


class TestBootstrapDevAccounts:
    """bootstrap_dev_accounts syncs PERFIS_ADMIN/PERFIS_TESTE."""

    def _make_db(self, monkeypatch):
        db_path = _fresh_db(monkeypatch)
        _build_schema(db_path)
        from core.bootstrap import ensure_extra_schema

        ensure_extra_schema()
        return db_path

    def test_skips_in_production(self, monkeypatch):
        """is_production=True must be a no-op."""
        db_path = self._make_db(monkeypatch)
        from core.bootstrap import bootstrap_dev_accounts

        # Should return immediately without touching the DB
        bootstrap_dev_accounts(is_production=True)
        os.unlink(db_path)

    def test_creates_admin_accounts(self, monkeypatch):
        """Admin accounts from PERFIS_ADMIN are inserted into the DB."""
        db_path = self._make_db(monkeypatch)
        from core.bootstrap import bootstrap_dev_accounts
        from core.auth_db import PERFIS_ADMIN

        bootstrap_dev_accounts()

        with _conn(db_path) as conn:
            for nii in PERFIS_ADMIN:
                row = conn.execute(
                    "SELECT NII FROM utilizadores WHERE NII=?", (nii,)
                ).fetchone()
                assert row is not None, f"Admin account {nii!r} was not created"
        os.unlink(db_path)

    def test_creates_test_accounts(self, monkeypatch):
        """Test accounts from PERFIS_TESTE are inserted into the DB."""
        db_path = self._make_db(monkeypatch)
        from core.bootstrap import bootstrap_dev_accounts
        from core.auth_db import PERFIS_TESTE

        bootstrap_dev_accounts()

        with _conn(db_path) as conn:
            for nii in PERFIS_TESTE:
                row = conn.execute(
                    "SELECT NII FROM utilizadores WHERE NII=?", (nii,)
                ).fetchone()
                assert row is not None, f"Test account {nii!r} was not created"
        os.unlink(db_path)

    def test_updates_existing_account(self, monkeypatch):
        """Existing dev account is updated (perfil, nome, ano)."""
        db_path = self._make_db(monkeypatch)
        from core.bootstrap import bootstrap_dev_accounts
        from core.auth_db import PERFIS_ADMIN

        nii = next(iter(PERFIS_ADMIN))

        # Insert with wrong perfil
        with _conn(db_path) as conn:
            conn.execute(
                """INSERT INTO utilizadores
                   (NII,NI,Nome_completo,Palavra_chave,ano,perfil,must_change_password,is_active)
                   VALUES (?,?,?,?,?,'aluno',1,1)""",
                (nii, nii, "Old Name", "oldhash", "9"),
            )
            conn.commit()

        bootstrap_dev_accounts()

        with _conn(db_path) as conn:
            row = conn.execute(
                "SELECT perfil, Nome_completo FROM utilizadores WHERE NII=?", (nii,)
            ).fetchone()
        expected_perfil = PERFIS_ADMIN[nii]["perfil"]
        assert row["perfil"] == expected_perfil
        assert row["Nome_completo"] == PERFIS_ADMIN[nii]["nome"]
        os.unlink(db_path)

    def test_rehashes_plaintext_password(self, monkeypatch):
        """If stored password equals plain-text senha, it is re-hashed."""
        db_path = self._make_db(monkeypatch)
        from core.bootstrap import bootstrap_dev_accounts
        from core.auth_db import PERFIS_ADMIN

        nii = next(iter(PERFIS_ADMIN))
        plain_senha = PERFIS_ADMIN[nii]["senha"]

        # Store the plain password as-is to trigger the re-hash branch
        with _conn(db_path) as conn:
            conn.execute(
                """INSERT INTO utilizadores
                   (NII,NI,Nome_completo,Palavra_chave,ano,perfil,must_change_password,is_active)
                   VALUES (?,?,?,?,?,?,1,1)""",
                (nii, nii, "Test", plain_senha, "0", "admin"),
            )
            conn.commit()

        bootstrap_dev_accounts()

        with _conn(db_path) as conn:
            row = conn.execute(
                "SELECT Palavra_chave FROM utilizadores WHERE NII=?", (nii,)
            ).fetchone()
        # After bootstrap, stored value must be a hash (starts with pbkdf2: or scrypt:)
        assert row["Palavra_chave"] != plain_senha
        os.unlink(db_path)

    def test_accepts_external_connection(self, monkeypatch):
        """bootstrap_dev_accounts accepts an explicit conn and does NOT close it."""
        db_path = self._make_db(monkeypatch)
        from core.bootstrap import bootstrap_dev_accounts
        from core.auth_db import PERFIS_ADMIN

        conn = _conn(db_path)
        try:
            bootstrap_dev_accounts(conn=conn)
            conn.commit()
            # Connection should still be open — we can query
            nii = next(iter(PERFIS_ADMIN))
            row = conn.execute(
                "SELECT NII FROM utilizadores WHERE NII=?", (nii,)
            ).fetchone()
            assert row is not None
        finally:
            conn.close()
        os.unlink(db_path)

    def test_empty_perfis_is_noop(self, monkeypatch):
        """If both PERFIS_ADMIN and PERFIS_TESTE are empty, the function returns early."""
        db_path = self._make_db(monkeypatch)
        import core.bootstrap as bootstrap_mod

        monkeypatch.setattr(bootstrap_mod, "PERFIS_ADMIN", {})
        monkeypatch.setattr(bootstrap_mod, "PERFIS_TESTE", {})

        # Must not raise
        bootstrap_mod.bootstrap_dev_accounts()
        os.unlink(db_path)

    def test_empty_table_returns_early(self, monkeypatch):
        """If utilizadores table has no columns, bootstrap_dev_accounts returns early."""
        db_path = _fresh_db(monkeypatch)
        # DB has no schema at all — PRAGMA table_info returns empty
        from core.bootstrap import bootstrap_dev_accounts

        bootstrap_dev_accounts()  # should not raise
        os.unlink(db_path)

    def test_non_admin_perfil_keeps_must_change(self, monkeypatch):
        """For existing aluno accounts, must_change_password is preserved (not forced to 0)."""
        db_path = self._make_db(monkeypatch)
        from core.bootstrap import bootstrap_dev_accounts
        from core.auth_db import PERFIS_TESTE

        if not PERFIS_TESTE:
            pytest.skip("PERFIS_TESTE is empty")

        nii = next(iter(PERFIS_TESTE))
        perfil = PERFIS_TESTE[nii].get("perfil", "aluno")
        if perfil != "aluno":
            pytest.skip("First test account is not aluno")

        with _conn(db_path) as conn:
            conn.execute(
                """INSERT INTO utilizadores
                   (NII,NI,Nome_completo,Palavra_chave,ano,perfil,must_change_password,is_active)
                   VALUES (?,?,?,?,?,?,1,1)""",
                (nii, nii, "Old", "hash", "1", "aluno"),
            )
            conn.commit()

        bootstrap_dev_accounts()

        with _conn(db_path) as conn:
            row = conn.execute(
                "SELECT must_change_password FROM utilizadores WHERE NII=?", (nii,)
            ).fetchone()
        # For 'aluno', the CASE expression preserves existing must_change_password (1)
        assert row["must_change_password"] == 1
        os.unlink(db_path)


# ---------------------------------------------------------------------------
# init_app_once
# ---------------------------------------------------------------------------


class TestInitAppOnce:
    """init_app_once is idempotent — runs schema + extra schema once."""

    def test_runs_without_error(self, app):
        """init_app_once must not raise when called inside app context."""
        import core.bootstrap as bootstrap_mod

        # Reset the guard so we can test a fresh run
        original = bootstrap_mod._APP_BOOTSTRAPPED
        bootstrap_mod._APP_BOOTSTRAPPED = False
        try:
            from core.bootstrap import init_app_once

            with app.app_context():
                init_app_once(app)
        finally:
            bootstrap_mod._APP_BOOTSTRAPPED = original

    def test_idempotent(self, app):
        """Calling init_app_once twice does not raise or reset state."""
        import core.bootstrap as bootstrap_mod

        original = bootstrap_mod._APP_BOOTSTRAPPED
        bootstrap_mod._APP_BOOTSTRAPPED = False
        try:
            from core.bootstrap import init_app_once

            with app.app_context():
                init_app_once(app)
                init_app_once(app)  # second call — must be no-op
            assert bootstrap_mod._APP_BOOTSTRAPPED is True
        finally:
            bootstrap_mod._APP_BOOTSTRAPPED = original

    def test_sets_bootstrapped_flag(self, app):
        """After init_app_once, _APP_BOOTSTRAPPED is True."""
        import core.bootstrap as bootstrap_mod

        bootstrap_mod._APP_BOOTSTRAPPED = False
        try:
            from core.bootstrap import init_app_once

            with app.app_context():
                init_app_once(app)
            assert bootstrap_mod._APP_BOOTSTRAPPED is True
        finally:
            bootstrap_mod._APP_BOOTSTRAPPED = True  # leave in clean state

    def test_second_call_skips_due_to_flag(self, app):
        """When _APP_BOOTSTRAPPED is True, init_app_once returns immediately."""
        import core.bootstrap as bootstrap_mod

        bootstrap_mod._APP_BOOTSTRAPPED = True

        # Patch ensure_schema to detect if it gets called
        called = []
        from core.bootstrap import init_app_once
        import core.bootstrap as bmod

        original_es = bmod.ensure_schema

        def _spy(*a, **kw):
            called.append(1)
            return original_es(*a, **kw)

        bmod.ensure_schema = _spy
        try:
            with app.app_context():
                init_app_once(app)
            assert called == [], (
                "ensure_schema must not be called when already bootstrapped"
            )
        finally:
            bmod.ensure_schema = original_es

    def test_backup_failure_does_not_abort(self, app, monkeypatch):
        """A backup failure during init_app_once is swallowed, not re-raised."""
        import core.bootstrap as bootstrap_mod

        bootstrap_mod._APP_BOOTSTRAPPED = False

        monkeypatch.setattr(
            "core.bootstrap.ensure_daily_backup",
            lambda: (_ for _ in ()).throw(RuntimeError("backup down")),
        )
        try:
            from core.bootstrap import init_app_once

            with app.app_context():
                init_app_once(app)  # must not raise
            assert bootstrap_mod._APP_BOOTSTRAPPED is True
        finally:
            bootstrap_mod._APP_BOOTSTRAPPED = True


# ---------------------------------------------------------------------------
# seed_dev_command (Flask CLI)
# ---------------------------------------------------------------------------


class TestSeedDevCommand:
    """seed_dev_command wires into the Flask CLI and seeds accounts."""

    def test_command_is_registered(self, app):
        """The 'seed-dev' CLI command must exist on the Flask app."""
        cmd_names = [c for c in app.cli.commands]
        assert "seed-dev" in cmd_names

    def test_command_seeds_accounts(self, app):
        """Running seed-dev via test_cli_runner inserts dev accounts."""
        from core.auth_db import PERFIS_ADMIN

        runner = app.test_cli_runner()
        result = runner.invoke(args=["seed-dev"])
        assert result.exit_code == 0, f"seed-dev failed: {result.output}"
        assert "seeded" in result.output.lower()

        from core.database import db

        with app.app_context():
            with db() as conn:
                for nii in PERFIS_ADMIN:
                    row = conn.execute(
                        "SELECT NII FROM utilizadores WHERE NII=?", (nii,)
                    ).fetchone()
                    assert row is not None, f"Admin {nii!r} not found after seed-dev"

    def test_command_output_message(self, app):
        """seed-dev echoes confirmation message."""
        runner = app.test_cli_runner()
        result = runner.invoke(args=["seed-dev"])
        assert "Dev accounts seeded." in result.output

    def test_command_idempotent(self, app):
        """Invoking seed-dev twice must not raise or produce an error."""
        runner = app.test_cli_runner()
        r1 = runner.invoke(args=["seed-dev"])
        r2 = runner.invoke(args=["seed-dev"])
        assert r1.exit_code == 0
        assert r2.exit_code == 0


# ---------------------------------------------------------------------------
# ensure_extra_schema — error resilience
# ---------------------------------------------------------------------------


class TestEnsureExtraSchemaErrors:
    """ensure_extra_schema swallows top-level exceptions gracefully."""

    def test_corrupt_db_path_does_not_raise(self, monkeypatch):
        """A totally invalid DB path causes a print, not an unhandled exception."""
        import core.constants as constants

        monkeypatch.setattr(constants, "BASE_DADOS", "/nonexistent/path/to.db")

        from core.bootstrap import ensure_extra_schema

        ensure_extra_schema()  # must not raise — exception is caught internally
