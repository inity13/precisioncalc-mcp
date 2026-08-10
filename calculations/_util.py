"""Shared decimal / formatting utilities used across calculation engines."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, getcontext
from typing import Any

from schemas.responses import CalcError

# High precision context. 50 significant digits is far more than enough for any
# business/finance calculation while remaining exact for the operations we use.
getcontext().prec = 50


def to_decimal(name: str, value: Any) -> Decimal:
    """Coerce an input to :class:`~decimal.Decimal`, raising a helpful error.

    Uses ``str(value)`` so that floats like ``0.1`` are interpreted at their
    decimal literal value rather than their binary float artifact.
    """
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):  # bool is an int subclass; reject explicitly
        raise CalcError(
            f"Parameter '{name}' must be a number, got a boolean.",
            hint="Pass a numeric value such as 1000 or 12.5.",
        )
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:  # pragma: no cover
        raise CalcError(
            f"Parameter '{name}' is not a valid number: {value!r}.",
            hint="Pass a numeric value, e.g. 1000, 12.5, or '0.05'.",
        ) from exc


def require_positive(name: str, value: Decimal) -> Decimal:
    """Validate ``value > 0``."""
    if value <= 0:
        raise CalcError(
            f"Parameter '{name}' must be greater than 0 (got {value}).",
            hint=f"Provide a positive value for '{name}'.",
        )
    return value


def require_non_negative(name: str, value: Decimal) -> Decimal:
    """Validate ``value >= 0``."""
    if value < 0:
        raise CalcError(
            f"Parameter '{name}' must be zero or greater (got {value}).",
            hint=f"Provide a non-negative value for '{name}'.",
        )
    return value


def quantize_money(value: Decimal, places: int = 2) -> Decimal:
    """Round a monetary value to ``places`` decimal places (banker-safe half-up)."""
    from decimal import ROUND_HALF_UP

    quant = Decimal(1).scaleb(-places)  # e.g. Decimal("0.01")
    return value.quantize(quant, rounding=ROUND_HALF_UP)


def format_money(value: Decimal, currency: str = "USD", places: int = 2) -> str:
    """Format a Decimal as a grouped currency string, e.g. ``$1,250.00``."""
    symbols = {
        "USD": "$", "EUR": "\u20ac", "GBP": "\u00a3", "JPY": "\u00a5",
        "CNY": "\u00a5", "INR": "\u20b9", "AUD": "A$", "CAD": "C$", "CHF": "CHF ",
    }
    prefix = symbols.get(currency, "")
    q = quantize_money(value, places)
    grouped = f"{q:,.{places}f}"
    suffix = "" if prefix else f" {currency}"
    return f"{prefix}{grouped}{suffix}"


def format_percent(value: Decimal, places: int = 4) -> str:
    """Format a *ratio* (0.1429) as a percent string (``14.2900%``), trimmed."""
    pct = (value * Decimal(100)).normalize()
    # Render with fixed places then strip trailing zeros for readability.
    s = f"{pct:.{places}f}".rstrip("0").rstrip(".")
    return f"{s}%"
