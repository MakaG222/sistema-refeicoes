"""
tests/test_input_validation.py — Testes de validação de inputs
===============================================================
Testa os validadores centralizados e a sua aplicação nas rotas.
"""

from conftest import create_aluno, create_system_user, get_csrf, login_as


# ═══════════════════════════════════════════════════════════════════════════
# Testes unitários dos validadores
# ═══════════════════════════════════════════════════════════════════════════


class TestValidadores:
    """Testa funções _val_* directamente."""

    def test_val_email_valido(self, app):
        from app import _val_email

        assert _val_email("user@example.com") == "user@example.com"

    def test_val_email_invalido(self, app):
        from app import _val_email

        assert _val_email("not-an-email") is False
        assert _val_email("@missing.com") is False
        assert _val_email("user@") is False

    def test_val_email_vazio(self, app):
        from app import _val_email

        assert _val_email("") is None
        assert _val_email(None) is None

    def test_val_phone_valido(self, app):
        from app import _val_phone

        assert _val_phone("+351912345678") == "+351912345678"
        assert _val_phone("912 345 678") == "912 345 678"

    def test_val_phone_invalido(self, app):
        from app import _val_phone

        assert _val_phone("abc") is False
        assert _val_phone("12") is False  # muito curto

    def test_val_phone_vazio(self, app):
        from app import _val_phone

        assert _val_phone("") is None

    def test_val_nii_valido(self, app):
        from app import _val_nii

        assert _val_nii("20223") == "20223"
        assert _val_nii("admin") == "admin"
        assert _val_nii("cmd1") == "cmd1"

    def test_val_nii_invalido(self, app):
        from app import _val_nii

        assert _val_nii("") is None
        assert _val_nii("abc def") is None  # espaço
        assert _val_nii("abc@def") is None  # caractere especial
        assert _val_nii(None) is None

    def test_val_ni_valido(self, app):
        from app import _val_ni

        assert _val_ni("303") == "303"
        assert _val_ni("") == ""  # vazio é OK

    def test_val_ni_invalido(self, app):
        from app import _val_ni

        assert _val_ni("abc def") is None

    def test_val_nome_valido(self, app):
        from app import _val_nome

        assert _val_nome("João Silva") == "João Silva"

    def test_val_nome_vazio(self, app):
        from app import _val_nome

        assert _val_nome("") is None
        assert _val_nome("   ") is None

    def test_val_nome_trunca(self, app):
        from app import _val_nome

        long_name = "A" * 300
        result = _val_nome(long_name)
        assert len(result) == 200

    def test_val_ano_valido(self, app):
        from app import _val_ano

        assert _val_ano("1") == 1
        assert _val_ano("0") == 0
        assert _val_ano("8") == 8

    def test_val_ano_invalido(self, app):
        from app import _val_ano

        assert _val_ano("9") is None
        assert _val_ano("-1") is None
        assert _val_ano("abc") is None
        assert _val_ano("") is None

    def test_val_perfil_valido(self, app):
        from app import _val_perfil

        assert _val_perfil("admin") == "admin"
        assert _val_perfil("aluno") == "aluno"
        assert _val_perfil("cmd") == "cmd"
        assert _val_perfil("cozinha") == "cozinha"
        assert _val_perfil("oficialdia") == "oficialdia"

    def test_val_perfil_invalido(self, app):
        from app import _val_perfil

        assert _val_perfil("hacker") is None
        assert _val_perfil("superadmin") is None

    def test_val_tipo_calendario(self, app):
        from app import _val_tipo_calendario

        assert _val_tipo_calendario("feriado") == "feriado"
        assert _val_tipo_calendario("invalido") == "normal"  # fallback

    def test_val_refeicao(self, app):
        from app import _val_refeicao

        assert _val_refeicao("Normal") == "Normal"
        assert _val_refeicao("Vegetariano") == "Vegetariano"
        assert _val_refeicao("Dieta") == "Dieta"
        assert _val_refeicao("") == ""
        assert _val_refeicao("InjeçãoSQL") == ""  # fallback

    def test_val_text_trunca(self, app):
        from app import _val_text

        long_text = "X" * 1000
        result = _val_text(long_text)
        assert len(result) == 500

    def test_val_int_id(self, app):
        from app import _val_int_id

        assert _val_int_id("42") == 42
        assert _val_int_id("abc") is None
        assert _val_int_id("") is None
        assert _val_int_id(None) is None

    def test_val_date_range_ok(self, app):
        from datetime import date

        from app import _val_date_range

        ok, msg = _val_date_range(date(2025, 1, 1), date(2025, 1, 31))
        assert ok is True

    def test_val_date_range_invertido(self, app):
        from datetime import date

        from app import _val_date_range

        ok, msg = _val_date_range(date(2025, 1, 31), date(2025, 1, 1))
        assert ok is False
        assert "anterior" in msg

    def test_val_date_range_demasiado_grande(self, app):
        from datetime import date

        from app import _val_date_range

        ok, msg = _val_date_range(date(2025, 1, 1), date(2027, 1, 1))
        assert ok is False
        assert "366" in msg

    def test_val_cap(self, app):
        from app import _val_cap

        assert _val_cap("100") == 100
        assert _val_cap("0") == 0
        assert _val_cap("10000") is None  # > 9999
        assert _val_cap("-1") is None
        assert _val_cap("abc") is None


# ═══════════════════════════════════════════════════════════════════════════
# Testes de integração — validação aplicada nas rotas
# ═══════════════════════════════════════════════════════════════════════════


class TestCriarUtilizadorValidacao:
    """Testa que _criar_utilizador rejeita inputs inválidos."""

    def test_rejeita_nii_com_caracteres_especiais(self, app):
        from app import _criar_utilizador

        ok, err = _criar_utilizador("abc@def", "100", "Nome", "1", "aluno", "pass123")
        assert ok is False
        assert "NII" in err

    def test_rejeita_ano_fora_de_range(self, app):
        from app import _criar_utilizador

        ok, err = _criar_utilizador("val01", "100", "Nome", "99", "aluno", "pass123")
        assert ok is False
        assert "Ano" in err

    def test_rejeita_perfil_invalido(self, app):
        from app import _criar_utilizador

        ok, err = _criar_utilizador("val02", "101", "Nome", "1", "hacker", "pass123")
        assert ok is False
        assert "Perfil" in err

    def test_rejeita_password_curta(self, app):
        from app import _criar_utilizador

        ok, err = _criar_utilizador("val03", "102", "Nome", "1", "aluno", "abc")
        assert ok is False
        assert "6" in err  # mínimo 6 caracteres


class TestAlunoPerfil:
    """Testa que aluno_perfil rejeita email/telemóvel inválidos."""

    def test_email_invalido_rejeitado(self, client, app):
        create_aluno("val10", "110", "Aluno Validação 10", "1")
        login_as(client, "val10")
        csrf = get_csrf(client)
        resp = client.post(
            "/aluno/perfil",
            data={"email": "nao-e-email", "telemovel": "", "csrf_token": csrf},
            follow_redirects=True,
        )
        assert "Email inv" in resp.data.decode()

    def test_telefone_invalido_rejeitado(self, client, app):
        login_as(client, "val10")
        csrf = get_csrf(client)
        resp = client.post(
            "/aluno/perfil",
            data={"email": "", "telemovel": "xx", "csrf_token": csrf},
            follow_redirects=True,
        )
        assert "inv" in resp.data.decode().lower()


class TestAdminValidacao:
    """Testa validação no painel de admin."""

    def test_editar_user_ano_invalido(self, client, app):
        create_system_user("admin_val", "admin", pw="admin_val")
        create_aluno("val20", "120", "Aluno Val 20", "1")
        login_as(client, "admin_val", "admin_val")
        csrf = get_csrf(client)
        resp = client.post(
            "/admin/utilizadores",
            data={
                "acao": "editar_user",
                "nii": "val20",
                "nome": "Aluno Val 20",
                "ni": "120",
                "ano": "99",
                "perfil": "aluno",
                "email": "",
                "telemovel": "",
                "pw": "",
                "csrf_token": csrf,
            },
            follow_redirects=True,
        )
        assert "Ano inv" in resp.data.decode()

    def test_editar_user_perfil_invalido(self, client, app):
        login_as(client, "admin_val", "admin_val")
        csrf = get_csrf(client)
        resp = client.post(
            "/admin/utilizadores",
            data={
                "acao": "editar_user",
                "nii": "val20",
                "nome": "Aluno Val 20",
                "ni": "120",
                "ano": "1",
                "perfil": "superadmin",
                "email": "",
                "telemovel": "",
                "pw": "",
                "csrf_token": csrf,
            },
            follow_redirects=True,
        )
        assert "Perfil inv" in resp.data.decode()

    def test_editar_contactos_email_invalido(self, client, app):
        login_as(client, "admin_val", "admin_val")
        csrf = get_csrf(client)
        resp = client.post(
            "/admin/utilizadores",
            data={
                "acao": "editar_contactos",
                "nii": "val20",
                "email": "not-valid",
                "telemovel": "",
                "csrf_token": csrf,
            },
            follow_redirects=True,
        )
        assert "Email inv" in resp.data.decode()
