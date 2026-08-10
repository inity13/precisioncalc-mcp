"""
Unit tests for PrecisionCalc calculation engines.

Run from the project root:
    python -m pytest -q
or without pytest:
    python tests/test_calculations.py
"""

from __future__ import annotations

import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from calculations import business_days as bd  # noqa: E402
from calculations import currency as fx  # noqa: E402
from calculations import growth as gr  # noqa: E402
from calculations import metrics as mx  # noqa: E402
from schemas.responses import CalcError  # noqa: E402


def _ok(resp: dict) -> dict:
    assert resp["status"] == "success", resp
    return resp


# ---- metrics ----------------------------------------------------------------

def test_ltv():
    r = _ok(mx.calculate_metric("ltv", {"arpu": 100, "churn_rate": 0.05, "gross_margin": 0.8}))
    assert Decimal(r["value"]) == Decimal("1600")


def test_cac():
    r = _ok(mx.calculate_metric("cac", {"total_spend": 50000, "new_customers": 250}))
    assert Decimal(r["value"]) == Decimal("200")


def test_ltv_cac_ratio():
    r = _ok(mx.calculate_metric("ltv_cac_ratio", {"ltv": 1600, "cac": 200}))
    assert Decimal(r["value"]) == Decimal("8")


def test_payback():
    r = _ok(mx.calculate_metric("payback_period_months",
                                {"cac": 1200, "monthly_revenue_per_customer": 100, "gross_margin": 0.75}))
    assert Decimal(r["value"]) == Decimal("16")


def test_contribution_margin():
    r = _ok(mx.calculate_metric("contribution_margin", {"revenue": 1000, "variable_costs": 400}))
    assert Decimal(r["value"]) == Decimal("600")
    assert Decimal(r["margin_ratio"]) == Decimal("0.6")


def test_gross_margin():
    r = _ok(mx.calculate_metric("gross_margin", {"revenue": 1000, "cogs": 350}))
    assert Decimal(r["value"]) == Decimal("0.65")


def test_churn_rate():
    r = _ok(mx.calculate_metric("churn_rate", {"customers_lost": 30, "customers_at_start": 600}))
    assert Decimal(r["value"]) == Decimal("0.05")


def test_mrr_growth():
    r = _ok(mx.calculate_metric("mrr_growth_rate", {"beginning_mrr": 10000, "ending_mrr": 11500}))
    assert Decimal(r["value"]) == Decimal("0.15")


def test_arr():
    r = _ok(mx.calculate_metric("arr", {"mrr": 12500}))
    assert Decimal(r["value"]) == Decimal("150000")


def test_break_even():
    r = _ok(mx.calculate_metric("break_even_units",
                                {"fixed_costs": 10000, "price_per_unit": 50, "variable_cost_per_unit": 30}))
    assert Decimal(r["value"]) == Decimal("500")
    assert r["units_rounded_up"] == "500"


def test_break_even_no_margin_errors():
    try:
        mx.calculate_metric("break_even_units",
                            {"fixed_costs": 10000, "price_per_unit": 30, "variable_cost_per_unit": 30})
    except CalcError:
        return
    raise AssertionError("expected CalcError")


def test_unknown_metric():
    try:
        mx.calculate_metric("nope", {})
    except CalcError as e:
        assert e.error_type == "unknown_metric"
        return
    raise AssertionError("expected CalcError")


# ---- currency ---------------------------------------------------------------

def test_currency_usd_eur():
    r = _ok(fx.convert_currency(100, "USD", "EUR"))
    assert Decimal(r["value"]) == Decimal("92")


def test_currency_cross():
    r = _ok(fx.convert_currency(100, "EUR", "GBP"))
    # 100 EUR -> USD (100/0.92) -> GBP (*0.785)
    expected = Decimal(100) * (Decimal("0.7850") / Decimal("0.9200"))
    assert Decimal(r["value"]) == expected


def test_currency_jpy_zero_decimals():
    r = _ok(fx.convert_currency(1, "USD", "JPY"))
    assert r["rounded_value"] == "157"


def test_currency_unsupported():
    try:
        fx.convert_currency(1, "USD", "XYZ")
    except CalcError as e:
        assert e.error_type == "unsupported_currency"
        return
    raise AssertionError("expected CalcError")


# ---- business days ----------------------------------------------------------

def test_add_business_days_skips_weekend():
    # Fri 2024-06-07 + 1 business day -> Mon 2024-06-10
    r = _ok(bd.business_days("add_business_days", "2024-06-07", days=1, region="NONE"))
    assert r["value"] == "2024-06-10"


def test_add_business_days_us_holiday():
    # 2024-07-03 (Wed) + 1 bd, July 4 is a US holiday -> Fri 2024-07-05
    r = _ok(bd.business_days("add_business_days", "2024-07-03", days=1, region="US"))
    assert r["value"] == "2024-07-05"


def test_count_business_days():
    # Mon..Fri inclusive = 5
    r = _ok(bd.business_days("count_business_days", "2024-06-03", end_date="2024-06-07", region="NONE"))
    assert r["value"] == 5


def test_count_business_days_us_holiday_excluded():
    # 2024-07-01..2024-07-05, July 4 excluded -> 4 business days
    r = _ok(bd.business_days("count_business_days", "2024-07-01", end_date="2024-07-05", region="US"))
    assert r["value"] == 4


def test_next_business_day_over_weekend():
    r = _ok(bd.business_days("next_business_day", "2024-06-07", region="NONE"))  # Fri -> Mon
    assert r["value"] == "2024-06-10"


def test_custom_holiday():
    r = _ok(bd.business_days("next_business_day", "2024-06-07",
                             region="NONE", custom_holidays=["2024-06-10"]))
    assert r["value"] == "2024-06-11"


def test_uk_good_friday():
    # 2024 Good Friday = 2024-03-29; count over that week excludes it + Easter Monday.
    r = _ok(bd.business_days("count_business_days", "2024-03-25", end_date="2024-04-01", region="UK"))
    # Mon25,Tue26,Wed27,Thu28 business; Fri29 GoodFri holiday; Sat/Sun weekend; Mon Apr1 Easter Monday holiday
    assert r["value"] == 4


# ---- growth -----------------------------------------------------------------

def test_future_value_annual():
    r = _ok(gr.compound_growth("future_value", rate=0.10, years=2, present_value=1000, compounding="annually"))
    assert Decimal(r["rounded_value"]) == Decimal("1210.00")


def test_future_value_monthly():
    r = _ok(gr.compound_growth("future_value", rate=0.12, years=1, present_value=1000, compounding="monthly"))
    # 1000 * (1.01)^12
    assert Decimal(r["rounded_value"]) == Decimal("1126.83")


def test_present_value():
    r = _ok(gr.compound_growth("present_value", rate=0.10, years=2, future_value=1210, compounding="annually"))
    assert Decimal(r["rounded_value"]) == Decimal("1000.00")


def test_cagr():
    r = _ok(gr.compound_growth("cagr", begin_value=1000, end_value=2000, years=3))
    # (2)^(1/3)-1 ~ 0.2599
    assert abs(Decimal(r["value"]) - Decimal("0.259921049894873")) < Decimal("1e-9")


def test_continuous():
    r = _ok(gr.compound_growth("future_value", rate=0.05, years=10, present_value=1000, compounding="continuous"))
    # 1000 * e^0.5 = 1648.72...
    assert Decimal(r["rounded_value"]) == Decimal("1648.72")


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in fns:
        fn()
        passed += 1
        print(f"  ok  {fn.__name__}")
    print(f"\n{passed}/{len(fns)} tests passed.")


if __name__ == "__main__":
    _run_all()
