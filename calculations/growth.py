"""
Compound growth calculations with Decimal precision.

Operations
----------
future_value   : FV = PV * (1 + r/n)^(n*t)
present_value  : PV = FV / (1 + r/n)^(n*t)
cagr           : CAGR = (end_value / begin_value)^(1/years) - 1

Compounding frequencies map to periods-per-year ``n``:
    daily=365, weekly=52, monthly=12, quarterly=4, semiannually=2, annually=1
    continuous => FV = PV * e^(r*t)   (uses Decimal.exp / ln)
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from schemas.responses import CalcError, success_response

from ._util import format_money, format_percent, quantize_money, require_positive, to_decimal

_FREQUENCIES = {
    "daily": Decimal(365),
    "weekly": Decimal(52),
    "monthly": Decimal(12),
    "quarterly": Decimal(4),
    "semiannually": Decimal(2),
    "annually": Decimal(1),
}


def _periods_per_year(freq: str) -> Decimal:
    key = (freq or "annually").strip().lower()
    if key == "continuous":
        return Decimal("Infinity")
    if key not in _FREQUENCIES:
        raise CalcError(
            f"Unsupported compounding frequency '{freq}'.",
            hint=f"Use one of: {', '.join(_FREQUENCIES)}, continuous.",
            error_type="unsupported_frequency",
        )
    return _FREQUENCIES[key]


def compound_growth(
    operation: str,
    rate: Any = None,
    years: Any = None,
    present_value: Any = None,
    future_value: Any = None,
    begin_value: Any = None,
    end_value: Any = None,
    compounding: str = "annually",
    currency: str = "USD",
) -> dict[str, Any]:
    """Dispatch a compound-growth calculation.

    Parameters
    ----------
    operation:
        ``future_value`` | ``present_value`` | ``cagr``.
    rate:
        Annual nominal rate as a decimal (0.08 = 8%). Required for FV/PV.
    years:
        Time horizon in years (may be fractional).
    present_value / future_value:
        Provide the known one; the other is computed.
    begin_value / end_value:
        Used by ``cagr``.
    compounding:
        daily | weekly | monthly | quarterly | semiannually | annually | continuous.
    currency:
        ISO code for formatting monetary outputs.
    """
    op = (operation or "").strip().lower()
    cur = (currency or "USD").upper()

    if op == "future_value":
        return _future_value(rate, years, present_value, compounding, cur)
    if op == "present_value":
        return _present_value(rate, years, future_value, compounding, cur)
    if op == "cagr":
        return _cagr(begin_value, end_value, years)

    raise CalcError(
        f"Unknown operation '{operation}'.",
        hint="Use future_value | present_value | cagr.",
        error_type="unknown_operation",
    )


def _growth_factor(rate: Decimal, years: Decimal, n: Decimal) -> tuple[Decimal, str]:
    """Return the multiplicative growth factor and a formula string."""
    if n.is_infinite():  # continuous compounding
        factor = (rate * years).exp()
        return factor, "e^(r*t)"
    base = Decimal(1) + rate / n
    if base <= 0:
        raise CalcError(
            "Effective period rate <= -100%; growth factor is undefined.",
            hint="Check that rate and compounding frequency are sensible.",
        )
    exponent = n * years
    factor = base ** exponent
    return factor, "(1 + r/n)^(n*t)"


def _future_value(rate, years, present_value, compounding, cur) -> dict[str, Any]:
    r = to_decimal("rate", _req(rate, "rate"))
    t = require_positive("years", to_decimal("years", _req(years, "years")))
    pv = to_decimal("present_value", _req(present_value, "present_value"))
    n = _periods_per_year(compounding)
    factor, ff = _growth_factor(r, t, n)
    fv = pv * factor
    return success_response(
        value=fv,
        formatted_value=format_money(fv, cur),
        formula=f"FV = PV * {ff}   (r=annual rate, n=periods/yr, t=years)",
        inputs_used={"present_value": pv, "rate": r, "years": t, "compounding": compounding},
        unit=cur,
        notes=[f"Growth factor = {factor.quantize(Decimal('0.00000001'))}.",
               "rate is a decimal (0.08 = 8%)."],
        extra={"growth_factor": factor, "rounded_value": quantize_money(fv)},
    )


def _present_value(rate, years, future_value, compounding, cur) -> dict[str, Any]:
    r = to_decimal("rate", _req(rate, "rate"))
    t = require_positive("years", to_decimal("years", _req(years, "years")))
    fv = to_decimal("future_value", _req(future_value, "future_value"))
    n = _periods_per_year(compounding)
    factor, ff = _growth_factor(r, t, n)
    pv = fv / factor
    return success_response(
        value=pv,
        formatted_value=format_money(pv, cur),
        formula=f"PV = FV / {ff}   (r=annual rate, n=periods/yr, t=years)",
        inputs_used={"future_value": fv, "rate": r, "years": t, "compounding": compounding},
        unit=cur,
        notes=[f"Discount factor = {factor.quantize(Decimal('0.00000001'))}.",
               "rate is a decimal (0.08 = 8%)."],
        extra={"discount_factor": factor, "rounded_value": quantize_money(pv)},
    )


def _cagr(begin_value, end_value, years) -> dict[str, Any]:
    begin = require_positive("begin_value", to_decimal("begin_value", _req(begin_value, "begin_value")))
    end = require_positive("end_value", to_decimal("end_value", _req(end_value, "end_value")))
    t = require_positive("years", to_decimal("years", _req(years, "years")))
    ratio = end / begin
    cagr = ratio ** (Decimal(1) / t) - Decimal(1)
    return success_response(
        value=cagr,
        formatted_value=format_percent(cagr),
        formula="CAGR = (end_value / begin_value)^(1/years) - 1",
        inputs_used={"begin_value": begin, "end_value": end, "years": t},
        unit="percent",
        notes=["Value is a ratio (0.15 = 15% annualized)."],
    )


def _req(value: Any, name: str) -> Any:
    if value is None:
        raise CalcError(
            f"Missing required parameter '{name}' for this operation.",
            hint=f"Provide '{name}'.",
            error_type="missing_parameter",
        )
    return value
