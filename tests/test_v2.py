"""
Unit tests for the v2 additions: new metrics, finance tools, richer holidays.

Run:  python tests/test_v2.py    (or pytest -q)
"""

from __future__ import annotations

import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from calculations import business_days as bd  # noqa: E402
from calculations import finance as fin  # noqa: E402
from calculations import metrics as mx  # noqa: E402
from schemas.responses import CalcError  # noqa: E402


def _ok(resp: dict) -> dict:
    assert resp["status"] == "success", resp
    return resp


# ---- new metrics ------------------------------------------------------------

def test_nrr():
    r = _ok(mx.calculate_metric("nrr", {"starting_mrr": 100000, "expansion_mrr": 20000,
                                        "contraction_mrr": 5000, "churned_mrr": 3000}))
    assert Decimal(r["value"]) == Decimal("1.12")


def test_grr():
    r = _ok(mx.calculate_metric("grr", {"starting_mrr": 100000,
                                        "contraction_mrr": 5000, "churned_mrr": 3000}))
    assert Decimal(r["value"]) == Decimal("0.92")


def test_rule_of_40():
    r = _ok(mx.calculate_metric("rule_of_40", {"growth_rate": 0.30, "profit_margin": 0.15}))
    assert Decimal(r["value"]) == Decimal("0.45")


def test_magic_number():
    r = _ok(mx.calculate_metric("magic_number", {"current_quarter_revenue": 1200000,
                                                 "prior_quarter_revenue": 1000000,
                                                 "prior_quarter_sm_spend": 400000}))
    # (200000 * 4) / 400000 = 2.0
    assert Decimal(r["value"]) == Decimal("2")


# ---- NPV / IRR --------------------------------------------------------------

def test_npv():
    r = _ok(fin.net_present_value("0.10", [-1000, 500, 500, 500]))
    # 500/1.1 + 500/1.21 + 500/1.331 - 1000
    expected = (Decimal(500) / Decimal("1.1") + Decimal(500) / (Decimal("1.1") ** 2)
                + Decimal(500) / (Decimal("1.1") ** 3) - Decimal(1000))
    assert abs(Decimal(r["value"]) - expected) < Decimal("1e-20")


def test_npv_zero_at_irr():
    flows = [-1000, 500, 500, 500]
    irr = _ok(fin.internal_rate_of_return(flows))
    rate = Decimal(irr["value"])
    npv = fin._npv(rate, [Decimal(str(x)) for x in flows])
    assert abs(npv) < Decimal("1e-6")


def test_irr_known():
    # -100, +110 over one period -> IRR = 10%
    r = _ok(fin.internal_rate_of_return([-100, 110]))
    assert abs(Decimal(r["value"]) - Decimal("0.10")) < Decimal("1e-9")


def test_irr_requires_sign_change():
    try:
        fin.internal_rate_of_return([100, 200, 300])
    except CalcError:
        return
    raise AssertionError("expected CalcError")


# ---- loan amortization ------------------------------------------------------

def test_loan_payment():
    # 200k, 6% annual, 360 months -> ~1199.10/mo
    r = _ok(fin.loan_amortization(200000, 0.06, 360))
    assert Decimal(r["monthly_payment"]) == Decimal("1199.10")


def test_loan_pays_off():
    r = _ok(fin.loan_amortization(10000, 0.05, 12, include_schedule=True))
    assert r["months_to_payoff"] == 12
    assert Decimal(r["schedule"][-1]["balance"]) == Decimal("0.00")


def test_loan_extra_payment_shortens():
    base = _ok(fin.loan_amortization(50000, 0.06, 120))
    faster = _ok(fin.loan_amortization(50000, 0.06, 120, extra_payment=200))
    assert faster["months_to_payoff"] < base["months_to_payoff"]


def test_loan_zero_interest():
    r = _ok(fin.loan_amortization(1200, 0.0, 12))
    assert Decimal(r["monthly_payment"]) == Decimal("100.00")


# ---- depreciation -----------------------------------------------------------

def test_straight_line():
    r = _ok(fin.depreciation("straight_line", 10000, 1000, 5))
    assert Decimal(r["value"]) == Decimal("1800.00")  # (10000-1000)/5
    assert Decimal(r["schedule"][-1]["book_value"]) == Decimal("1000.00")


def test_declining_balance_converges_to_salvage():
    r = _ok(fin.depreciation("declining_balance", 10000, 1000, 5))
    assert Decimal(r["schedule"][-1]["book_value"]) == Decimal("1000.00")


def test_syd():
    r = _ok(fin.depreciation("sum_of_years_digits", 10000, 1000, 5))
    # Year 1 = 5/15 * 9000 = 3000
    assert Decimal(r["value"]) == Decimal("3000.00")
    assert Decimal(r["schedule"][-1]["book_value"]) == Decimal("1000.00")


# ---- richer holidays --------------------------------------------------------

def test_germany_via_holidays_lib():
    if not bd.HOLIDAYS_LIB_AVAILABLE:
        return  # optional dependency not installed
    # 2024-10-03 is German Unity Day (Thu) -> add 1 bd from 10-02 lands on 10-04.
    r = _ok(bd.business_days("add_business_days", "2024-10-02", days=1, region="DE"))
    assert r["value"] == "2024-10-04"


def test_unknown_country_errors():
    try:
        bd.business_days("next_business_day", "2024-01-01", region="ZZ")
    except CalcError as e:
        assert e.error_type == "unsupported_region"
        return
    raise AssertionError("expected CalcError")


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"\n{len(fns)}/{len(fns)} v2 tests passed.")


if __name__ == "__main__":
    _run_all()
