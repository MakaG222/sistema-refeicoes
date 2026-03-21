"""
tests/test_auth_db.py — Testes unitários para core/auth_db.py
=============================================================
"""

from werkzeug.security import generate_password_hash

from core.auth_db import (
    block_user,
    existe_admin,
    recent_failures,
    recent_failures_by_ip,
    reg_login,
    user_by_ni,
    user_by_nii,
    user_id_by_nii,
    verify_password,
)
from core.database import db
from tests.conftest import create_aluno


# ── verify_password ─────────────────────────────────────────────────────


class TestVerifyPassword:
    def test_verify_password_hash_correct(self):
        pw = "Segura@123"
        hashed = generate_password_hash(pw, method="pbkdf2")
        assert verify_password(pw, hashed) is True

    def test_verify_password_hash_wrong(self):
        hashed = generate_password_hash("Segura@123", method="pbkdf2")
        assert verify_password("errada", hashed) is False

    def test_verify_password_plaintext_correct(self):
        assert verify_password("legado", "legado") is True

    def test_verify_password_plaintext_wrong(self):
        assert verify_password("errada", "legado") is False

    def test_verify_password_empty_stored(self):
        # Empty stored password — only empty input matches
        assert verify_password("qualquer", "") is False
        assert verify_password("", "") is True
        # None stored is coerced to ""
        assert verify_password("", None) is True


# ── reg_login / recent_failures ──────────────────────────────────────────


class TestRegLogin:
    def test_reg_login_success(self, app):
        with app.app_context():
            nii = "reg_ok_test"
            reg_login(nii, ok=1, ip="10.0.0.1")
            with db() as conn:
                row = conn.execute(
                    "SELECT * FROM login_eventos WHERE nii=? AND sucesso=1",
                    (nii,),
                ).fetchone()
            assert row is not None
            assert row["ip"] == "10.0.0.1"

    def test_reg_login_failure(self, app):
        with app.app_context():
            nii = "reg_fail_test"
            reg_login(nii, ok=0, ip="10.0.0.2")
            with db() as conn:
                row = conn.execute(
                    "SELECT * FROM login_eventos WHERE nii=? AND sucesso=0",
                    (nii,),
                ).fetchone()
            assert row is not None

    def test_recent_failures_counts(self, app):
        with app.app_context():
            nii = "fail_count_test"
            for _ in range(3):
                reg_login(nii, ok=0, ip="10.0.0.3")
            # Also register a success — should not count
            reg_login(nii, ok=1, ip="10.0.0.3")
            count = recent_failures(nii, minutes=10)
            assert count >= 3

    def test_recent_failures_by_ip(self, app):
        with app.app_context():
            ip = "192.168.99.99"
            for _ in range(2):
                reg_login("ip_test_a", ok=0, ip=ip)
            reg_login("ip_test_b", ok=0, ip=ip)
            count = recent_failures_by_ip(ip, minutes=15)
            assert count >= 3


# ── block_user ───────────────────────────────────────────────────────────


class TestBlockUser:
    def test_block_user(self, app):
        with app.app_context():
            nii = "block_test_user"
            create_aluno(nii, f"NI_{nii}", "Bloqueado Teste", ano="1", pw="pw123")
            block_user(nii, minutes=15)
            with db() as conn:
                row = conn.execute(
                    "SELECT locked_until FROM utilizadores WHERE NII=?", (nii,)
                ).fetchone()
            assert row is not None
            assert row["locked_until"] is not None


# ── existe_admin ─────────────────────────────────────────────────────────


class TestExisteAdmin:
    def test_existe_admin_true(self, app):
        """Dev bootstrap creates admin accounts, so this should be True."""
        with app.app_context():
            assert existe_admin() is True

    def test_existe_admin_false(self, app):
        """Temporarily remove all admins and verify False, then restore."""
        with app.app_context():
            with db() as conn:
                # Save admin rows
                admins = conn.execute(
                    "SELECT id, perfil FROM utilizadores WHERE perfil='admin'"
                ).fetchall()
                admin_ids = [r["id"] for r in admins]
                # Temporarily change profile
                conn.execute(
                    "UPDATE utilizadores SET perfil='_tmp' WHERE perfil='admin'"
                )
                conn.commit()

                assert existe_admin() is False

                # Restore
                for aid in admin_ids:
                    conn.execute(
                        "UPDATE utilizadores SET perfil='admin' WHERE id=?", (aid,)
                    )
                conn.commit()
            # Sanity check: admin restored
            assert existe_admin() is True


# ── user lookups ─────────────────────────────────────────────────────────


class TestUserLookups:
    def test_user_by_nii_found(self, app):
        with app.app_context():
            nii = "lookup_nii"
            create_aluno(nii, f"NI_{nii}", "Lookup NII", ano="1")
            u = user_by_nii(nii)
            assert u is not None
            assert u["NII"] == nii
            assert u["Nome_completo"] == "Lookup NII"

    def test_user_by_nii_not_found(self, app):
        with app.app_context():
            assert user_by_nii("inexistente_xyz_999") is None

    def test_user_by_nii_empty(self, app):
        with app.app_context():
            assert user_by_nii("") is None
            assert user_by_nii("   ") is None

    def test_user_by_ni(self, app):
        with app.app_context():
            nii = "lookup_ni_user"
            ni = "NI_lookup_ni"
            create_aluno(nii, ni, "Lookup NI", ano="2")
            row = user_by_ni(ni)
            assert row is not None
            assert row["NI"] == ni

    def test_user_id_by_nii(self, app):
        with app.app_context():
            nii = "id_lookup_user"
            uid = create_aluno(nii, f"NI_{nii}", "ID Lookup", ano="1")
            result = user_id_by_nii(nii)
            assert result == uid

    def test_user_id_by_nii_not_found(self, app):
        with app.app_context():
            assert user_id_by_nii("nao_existe_abc") is None
