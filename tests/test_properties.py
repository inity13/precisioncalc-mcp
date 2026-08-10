"""
Property-based tests (Hypothesis) for rounding + inverse-operation invariants.

Skipped automatically if Hypothesis is not installed.

Run:  pytest -q tests/test_properties.py   (or python tests/test_properties.py)
"""

from __future__ import annotations

import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from hypothesis import given, settings
    from hypothesis import strategies as st
except ImportError:  # pragma: no cover
    print("hypothesis not installed; skipping property tests.")
    sys.exit(0)

from calculations import currency as fx  # noqa: E402
from calculations import finance as fin  # noqa: E402
from calculations import growth as gr  # noqa: E402

_money = st.decimals(min_value=Decimal("1"), max_value=Decimal("1000000"),
                     allow_nan=False, allow_infinity=False, places=2)
_rate = st.decimals(min_value=Decimal("0.001"), max_value=Decimal("0.5"),
                    allow_nan=False, allow_infinity=False, places=4)
_years = st.integers(min_value=1, max_value=40)
_freq = st.sampled_from(["annually", "monthly", "quarterly", "daily", "weekly", "semiannually"])


@settings(max_examples=200, deadline=None)
@given(pv=_money, rate=_rate, years=_years, freq=_freq)
def test_fv_pv_roundtrip(pv: Decimal, rate: Decimal, years: int, freq: str) -> None:
    """PV -> FV -> PV should recover the original present value (to tolerance)."""
    fv = gr.compound_growth("future_value", rate=rate, years=years,
                            present_value=pv, compounding=freq)
    assert fv["status"] == "success"
    back = gr.compound_growth("present_value", rate=rate, years=years,
                              future_value=Decimal(fv["value"]), compounding=freq)
    recovered = Decimal(back["value"])
    assert abs(recovered - pv) <= Decimal("0.0001") * pv + Decimal("0.01")


@settings(max_examples=200, deadline=None)
@given(begin=_money, factor=st.decimals(min_value=Decimal("1.01"), max_value=Decimal("50"),
                                        allow_nan=False, allow_infinity=False, places=2),
       years=_years)
def test_cagr_reconstructs_end(begin: Decimal, factor: Decimal, years: int) -> None:
    """Applying the solved CAGR for N years should reproduce end_value."""
    end = begin * factor
    r = gr.compound_growth("cagr", begin_value=begin, end_value=end, years=years)
    cagr = Decimal(r["value"])
    reconstructed = begin * ((Decimal(1) + cagr) ** years)
    assert abs(reconstructed - end) <= Decimal("0.0001") * end + Decimal("0.01")


@settings(max_examples=150, deadline=None)
@given(amount=_money, a=st.sampled_from(list(fx.SUPPORTED_CURRENCIES)),
       b=st.sampled_from(list(fx.SUPPORTED_CURRENCIES)))
def test_currency_roundtrip_static(amount: Decimal, a: str, b: str) -> None:
    """A -> B -> A on static rates recovers the original amount (unrounded value)."""
    fwd = fx.convert_currency(amount, a, b, live=False)
    back = fx.convert_currency(Decimal(fwd["value"]), b, a, live=False)
    assert abs(Decimal(back["value"]) - amount) <= Decimal("0.01")


@settings(max_examples=100, deadline=None)
@given(principal=st.integers(min_value=1000, max_value=1000000),
       rate=st.sampled_from([Decimal("0.03"), Decimal("0.05"), Decimal("0.07"), Decimal("0.12")]),
       months=st.integers(min_value=6, max_value=360))
def test_loan_totals_consistent(principal: int, rate: Decimal, months: int) -> None:
    """total_paid == principal + total_interest, and payoff within scheduled term."""
    r = fin.loan_amortization(principal, rate, months)
    assert r["status"] == "success"
    total_paid = Decimal(r["total_paid"])
    total_interest = Decimal(r["total_interest"])
    assert abs(total_paid - (Decimal(principal) + total_interest)) <= Decimal("0.05")
    assert r["months_to_payoff"] <= months


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  ok  {name}")
    print("\nproperty tests passed.")
