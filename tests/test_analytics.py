"""Tests for core/analytics.py — period_days and series_consumo_por_dia."""

from datetime import date, timedelta

from core.analytics import period_days, series_consumo_por_dia
from tests.conftest import create_aluno

from core.meals import refeicao_save


# ── period_days (pure) ────────────────────────────────────────────────────


def test_period_days_returns_correct_dates():
    """7 days counting back from today gives correct sequence."""
    today = date.today()
    result = period_days(today, 7)
    assert len(result) == 7
    expected = [today - timedelta(days=i) for i in range(6, -1, -1)]
    assert result == expected


def test_period_days_single_day():
    """days=1 returns only the base date."""
    base = date(2025, 6, 15)
    result = period_days(base, 1)
    assert result == [base]


def test_period_days_order():
    """First element is the oldest date."""
    base = date(2025, 3, 10)
    result = period_days(base, 5)
    assert result[0] < result[-1]
    assert result[0] == base - timedelta(days=4)
    assert result[-1] == base


# ── series_consumo_por_dia (DB) ──────────────────────────────────────────


def test_series_consumo_empty_db(app):
    """With no meal data, all count lists are zeros."""
    with app.app_context():
        d0 = date(2099, 1, 1)
        d1 = date(2099, 1, 3)
        days_list, pa, ln, alm, jan, exc = series_consumo_por_dia(d0, d1)
        assert len(days_list) == 3
        assert pa == [0, 0, 0]
        assert ln == [0, 0, 0]
        assert alm == [0, 0, 0]
        assert jan == [0, 0, 0]
        assert exc == [0, 0, 0]


def test_series_consumo_with_data(app):
    """Inserted meals are reflected in the series counts."""
    with app.app_context():
        uid = create_aluno("an_s001", "NIS001", "Aluno Analytics", ano="1")
        d = date(2098, 6, 10)
        refeicao_save(
            uid,
            d,
            {
                "pequeno_almoco": 1,
                "lanche": 1,
                "almoco": "Normal",
                "jantar_tipo": "Normal",
            },
        )

        days_list, pa, ln, alm, jan, exc = series_consumo_por_dia(d, d)
        assert len(days_list) == 1
        assert pa == [1]
        assert ln == [1]
        assert alm == [1]
        assert jan == [1]


def test_series_consumo_filtered_by_ano(app):
    """Passing ano filters to only students of that year."""
    with app.app_context():
        uid_a1 = create_aluno("an_s010", "NIS010", "Aluno Ano1 S", ano="1")
        uid_a2 = create_aluno("an_s011", "NIS011", "Aluno Ano2 S", ano="2")
        d = date(2098, 7, 20)

        refeicao_save(uid_a1, d, {"pequeno_almoco": 1, "almoco": "Normal"})
        refeicao_save(uid_a2, d, {"pequeno_almoco": 1, "almoco": "Normal"})

        # Unfiltered — both students counted
        _, pa_all, _, alm_all, _, _ = series_consumo_por_dia(d, d)
        assert pa_all == [2]
        assert alm_all == [2]

        # Filter ano=1 — only first student
        _, pa_1, _, alm_1, _, _ = series_consumo_por_dia(d, d, ano=1)
        assert pa_1 == [1]
        assert alm_1 == [1]

        # Filter ano=2 — only second student
        _, pa_2, _, alm_2, _, _ = series_consumo_por_dia(d, d, ano=2)
        assert pa_2 == [1]
        assert alm_2 == [1]
