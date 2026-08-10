"""
Time-value-of-money and asset finance calculations (Decimal precision).

Tools
-----
* ``net_present_value`` -- NPV of a cashflow series at a discount rate.
* ``internal_rate_of_return`` -- IRR (rate where NPV == 0), solved numerically.
* ``loan_amortization`` -- level-payment loan: payment, totals, and schedule.
* ``depreciation`` -- straight_line | declining_balance | sum_of_years_digits.

All money uses :class:`decimal.Decimal`; results follow the standard envelope.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Sequence

from schemas.responses import CalcError, success_response

from ._util import (
    format_money,
    format_percent,
    quantize_money,
    require_non_negative,
    require_positive,
    to_decimal,
)

_MAX_SCHEDULE_ROWS = 1200  # safety cap on emitted schedule rows


# ---------------------------------------------------------------------------
# NPV / IRR
# ---------------------------------------------------------------------------

def _npv(rate: Decimal, cashflows: Sequence[Decimal]) -> Decimal:
    """NPV = sum(CF_t / (1 + rate)^t), t = 0..n (CF_0 usually the outlay)."""
    total = Decimal(0)
    for t, cf in enumerate(cashflows):
        total += cf / ((Decimal(1) + rate) ** t)
    return total


def _coerce_cashflows(cashflows: Any) -> list[Decimal]:
    if not isinstance(cashflows, (list, tuple)) or len(cashflows) < 2:
        raise CalcError(
            "cashflows must be a list of at least 2 numbers (e.g. [-1000, 300, 400, 500]).",
            hint="Index 0 is period 0 (typically the negative initial investment).",
        )
    return [to_decimal(f"cashflows[{i}]", v) for i, v in enumerate(cashflows)]


def net_present_value(rate: Any, cashflows: Any, currency: str = "USD") -> dict[str, Any]:
    """Net Present Value of a periodic cashflow series.

    Args:
        rate: Discount rate per period as a decimal (0.10 = 10%).
        cashflows: List of cashflows; index 0 = period 0 (usually negative outlay).
        currency: ISO code for formatting.
    """
    r = to_decimal("rate", rate)
    if r <= -1:
        raise CalcError("rate must be greater than -100% (-1).", hint="Use e.g. 0.08 for 8%.")
    flows = _coerce_cashflows(cashflows)
    value = _npv(r, flows)
    cur = (currency or "USD").upper()
    return success_response(
        value=value,
        formatted_value=format_money(value, cur),
        formula="NPV = sum(CF_t / (1 + rate)^t) for t = 0..n",
        inputs_used={"rate": r, "cashflows": [str(f) for f in flows]},
        unit=cur,
        notes=["Period 0 cashflow is not discounted.",
               "Positive NPV => value-creating at this discount rate."],
        extra={"rounded_value": quantize_money(value), "periods": len(flows) - 1},
    )


def internal_rate_of_return(cashflows: Any, guess: Any = "0.1") -> dict[str, Any]:
    """Internal Rate of Return: the rate where NPV == 0.

    Uses Newton's method with a bracketed bisection fallback for robustness.

    Args:
        cashflows: List of cashflows; index 0 = period 0 (usually negative outlay).
        guess: Optional starting rate for Newton's method (decimal).
    """
    flows = _coerce_cashflows(cashflows)
    if not (min(flows) < 0 < max(flows)):
        raise CalcError(
            "IRR requires at least one negative and one positive cashflow.",
            hint="Typically CF_0 is negative (investment) and later flows positive.",
        )

    g = to_decimal("guess", guess)
    rate = _irr_newton(flows, g)
    if rate is None:
        rate = _irr_bisection(flows)
    if rate is None:
        raise CalcError(
            "IRR did not converge for the provided cashflows.",
            hint="Cashflows may have multiple/no sign changes; inspect the series.",
            error_type="no_convergence",
        )

    check = _npv(rate, flows)
    return success_response(
        value=rate,
        formatted_value=format_percent(rate),
        formula="IRR solves NPV(rate) = sum(CF_t / (1 + rate)^t) = 0",
        inputs_used={"cashflows": [str(f) for f in flows], "guess": g},
        unit="percent",
        notes=[f"NPV at solved IRR ~= {check.quantize(Decimal('0.000001'))} (should be ~0).",
               "Per-period rate; annualize if periods are not years."],
    )


def _npv_derivative(rate: Decimal, flows: Sequence[Decimal]) -> Decimal:
    d = Decimal(0)
    for t, cf in enumerate(flows):
        if t == 0:
            continue
        d += (-t * cf) / ((Decimal(1) + rate) ** (t + 1))
    return d


def _irr_newton(flows: Sequence[Decimal], guess: Decimal, iters: int = 100) -> Decimal | None:
    rate = guess
    tol = Decimal("1e-12")
    for _ in range(iters):
        if rate <= -1:
            return None
        f = _npv(rate, flows)
        if abs(f) < tol:
            return rate
        d = _npv_derivative(rate, flows)
        if d == 0:
            return None
        step = f / d
        rate = rate - step
        if abs(step) < tol:
            return rate if abs(_npv(rate, flows)) < Decimal("1e-6") else None
    return None


def _irr_bisection(flows: Sequence[Decimal]) -> Decimal | None:
    lo, hi = Decimal("-0.999999"), Decimal("100")
    f_lo, f_hi = _npv(lo, flows), _npv(hi, flows)
    if f_lo * f_hi > 0:
        return None
    for _ in range(400):
        mid = (lo + hi) / 2
        f_mid = _npv(mid, flows)
        if abs(f_mid) < Decimal("1e-12"):
            return mid
        if f_lo * f_mid < 0:
            hi, f_hi = mid, f_mid
        else:
            lo, f_lo = mid, f_mid
    return (lo + hi) / 2


# ---------------------------------------------------------------------------
# Loan amortization
# ---------------------------------------------------------------------------

def loan_amortization(
    principal: Any,
    annual_rate: Any,
    term_months: Any,
    extra_payment: Any = "0",
    currency: str = "USD",
    include_schedule: bool = False,
) -> dict[str, Any]:
    """Level-payment (amortizing) loan calculator.

    Args:
        principal: Loan amount (> 0).
        annual_rate: Nominal annual interest rate as a decimal (0.06 = 6%).
        term_months: Number of monthly payments (> 0).
        extra_payment: Optional extra principal paid each month.
        currency: ISO code for formatting.
        include_schedule: If true, include the full month-by-month schedule.
    """
    p = require_positive("principal", to_decimal("principal", principal))
    ar = require_non_negative("annual_rate", to_decimal("annual_rate", annual_rate))
    n = int(require_positive("term_months", to_decimal("term_months", term_months)))
    extra = require_non_negative("extra_payment", to_decimal("extra_payment", extra_payment))
    cur = (currency or "USD").upper()

    r = ar / Decimal(12)
    if r == 0:
        payment = p / Decimal(n)
    else:
        payment = p * r / (Decimal(1) - (Decimal(1) + r) ** (-n))
    payment_q = quantize_money(payment)

    balance = p
    total_interest = Decimal(0)
    schedule: list[dict[str, str]] = []
    month = 0
    while balance > Decimal("0.005") and month < n + 1200:
        month += 1
        interest = quantize_money(balance * r)
        scheduled_principal = payment_q - interest
        principal_paid = scheduled_principal + extra
        # Final payment: last scheduled month, or the remaining balance is within
        # one payment (incl. rounding residual / accelerated payoff via extra).
        if principal_paid >= balance or (balance - principal_paid) <= Decimal("0.01") or month >= n:
            principal_paid = balance
            actual_payment = quantize_money(balance + interest)
        else:
            actual_payment = quantize_money(payment_q + extra)
        balance = quantize_money(balance - principal_paid)
        total_interest += interest
        if include_schedule and len(schedule) < _MAX_SCHEDULE_ROWS:
            schedule.append({
                "month": str(month),
                "payment": str(actual_payment),
                "interest": str(interest),
                "principal": str(quantize_money(principal_paid)),
                "balance": str(balance),
            })
        if balance <= Decimal("0.005"):
            break

    total_paid = quantize_money(p + total_interest)
    notes = [
        "Monthly payment uses the standard amortization formula.",
        "Interest each month = outstanding balance x (annual_rate / 12).",
    ]
    if extra > 0:
        notes.append(f"Extra principal of {format_money(extra, cur)}/mo shortens the term to {month} months.")
    if include_schedule and month > _MAX_SCHEDULE_ROWS:
        notes.append(f"Schedule truncated to first {_MAX_SCHEDULE_ROWS} rows.")

    extra_out: dict[str, Any] = {
        "monthly_payment": payment_q,
        "months_to_payoff": month,
        "total_interest": quantize_money(total_interest),
        "total_paid": total_paid,
    }
    if include_schedule:
        extra_out["schedule"] = schedule

    return success_response(
        value=payment_q,
        formatted_value=f"{format_money(payment_q, cur)}/month",
        formula="payment = P * r / (1 - (1 + r)^-n),  r = annual_rate/12,  n = term_months",
        inputs_used={"principal": p, "annual_rate": ar, "term_months": n,
                     "extra_payment": extra},
        unit=f"{cur}/month",
        notes=notes,
        extra=extra_out,
    )


# ---------------------------------------------------------------------------
# Depreciation
# ---------------------------------------------------------------------------

def depreciation(
    method: str,
    cost: Any,
    salvage_value: Any,
    useful_life_years: Any,
    currency: str = "USD",
) -> dict[str, Any]:
    """Depreciation schedule for an asset.

    Args:
        method: straight_line | declining_balance | sum_of_years_digits.
        cost: Initial asset cost.
        salvage_value: Residual/salvage value at end of life.
        useful_life_years: Number of years (integer, > 0).
        currency: ISO code for formatting.
    """
    m = (method or "").strip().lower()
    c = require_positive("cost", to_decimal("cost", cost))
    s = require_non_negative("salvage_value", to_decimal("salvage_value", salvage_value))
    life = int(require_positive("useful_life_years", to_decimal("useful_life_years", useful_life_years)))
    cur = (currency or "USD").upper()
    if s > c:
        raise CalcError("salvage_value cannot exceed cost.", hint="Set salvage_value <= cost.")

    depreciable = c - s
    schedule: list[dict[str, str]] = []
    book = c

    if m == "straight_line":
        annual = depreciable / Decimal(life)
        formula = "annual = (cost - salvage) / useful_life_years"
        for yr in range(1, life + 1):
            dep = quantize_money(annual) if yr < life else quantize_money(book - s)
            book = quantize_money(book - dep)
            schedule.append(_dep_row(yr, dep, book))
        first_year = quantize_money(annual)

    elif m == "declining_balance":
        rate = Decimal(2) / Decimal(life)  # double-declining
        formula = "dep_t = book_value_t * (2 / useful_life_years), floored at salvage"
        for yr in range(1, life + 1):
            dep = quantize_money(book * rate)
            if book - dep < s:
                dep = quantize_money(book - s)
            book = quantize_money(book - dep)
            schedule.append(_dep_row(yr, dep, book))
        first_year = schedule[0]["depreciation"] if schedule else "0"

    elif m == "sum_of_years_digits":
        syd = Decimal(life * (life + 1)) / Decimal(2)
        formula = "dep_t = (remaining_life / sum_of_years_digits) * (cost - salvage)"
        for yr in range(1, life + 1):
            remaining = Decimal(life - yr + 1)
            dep = quantize_money((remaining / syd) * depreciable)
            if yr == life:
                dep = quantize_money(book - s)
            book = quantize_money(book - dep)
            schedule.append(_dep_row(yr, dep, book))
        first_year = schedule[0]["depreciation"] if schedule else "0"

    else:
        raise CalcError(
            f"Unknown depreciation method '{method}'.",
            hint="Use straight_line | declining_balance | sum_of_years_digits.",
            error_type="unknown_method",
        )

    return success_response(
        value=Decimal(first_year) if isinstance(first_year, str) else first_year,
        formatted_value=f"Year 1 depreciation: {format_money(Decimal(str(first_year)), cur)}",
        formula=formula,
        inputs_used={"method": m, "cost": c, "salvage_value": s, "useful_life_years": life},
        unit=cur,
        notes=[f"Total depreciable base = {format_money(depreciable, cur)}.",
               "Final year adjusted so ending book value equals salvage."],
        extra={"schedule": schedule, "total_depreciated": quantize_money(depreciable)},
    )


def _dep_row(year: int, dep: Decimal, book: Decimal) -> dict[str, str]:
    return {"year": str(year), "depreciation": str(dep), "book_value": str(book)}
