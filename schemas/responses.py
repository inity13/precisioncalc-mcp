"""
Consistent, agent-friendly response envelopes for every PrecisionCalc tool.

Every calculation tool returns the SAME top-level shape so that an LLM/agent can
parse results deterministically:

    {
      "status": "success" | "error",
      "value": <exact machine-readable number as string>,   # success only
      "formatted_value": "<human readable>",                 # success only
      "formula": "<the exact formula applied>",              # success only
      "inputs_used": { ... },                                # success only
      "unit": "<unit / currency / dimensionless>",           # success only
      "notes": ["assumption 1", "edge-case note", ...],      # success only
      "error": { "type": ..., "message": ..., "hint": ... }  # error only
    }

Design decisions
----------------
* Monetary / rate values are produced with :mod:`decimal` and serialized as
  *strings* in ``value`` to guarantee no float precision is lost in JSON.
  ``formatted_value`` is the pretty, human-readable form.
* ``notes`` is always a list (possibly empty) so agents can iterate safely.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Iterable, Mapping


class CalcError(ValueError):
    """Raised for invalid input / unsupported operations.

    Carries an optional ``hint`` with an actionable next step for the agent.
    """

    def __init__(self, message: str, *, hint: str | None = None, error_type: str = "invalid_input"):
        super().__init__(message)
        self.hint = hint
        self.error_type = error_type


def _stringify(value: Any) -> Any:
    """Make a value JSON-safe while preserving Decimal precision as a string."""
    if isinstance(value, Decimal):
        # Normalize away exponent noise (e.g. 1.230 -> 1.23) but keep full value.
        return format(value.normalize(), "f")
    return value


def success_response(
    *,
    value: Decimal | int | float | str,
    formatted_value: str,
    formula: str,
    inputs_used: Mapping[str, Any],
    unit: str,
    notes: Iterable[str] | None = None,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a standardized success envelope.

    Parameters
    ----------
    value:
        The exact calculated result. ``Decimal`` values are serialized as full
        precision strings.
    formatted_value:
        Human-readable rendering (e.g. ``"$1,250.00"`` or ``"14.29%"``).
    formula:
        The exact formula that was applied, as a readable string.
    inputs_used:
        Every input that actually fed the calculation (post-validation).
    unit:
        Unit of the result (currency code, ``"months"``, ``"ratio"``,
        ``"percent"``, ``"units"``, ``"dimensionless"``...).
    notes:
        Assumptions or edge-case handling. Always emitted as a list.
    extra:
        Optional additional structured fields (e.g. intermediate steps, rate
        metadata). Merged into the top level.
    """
    payload: dict[str, Any] = {
        "status": "success",
        "value": _stringify(value),
        "formatted_value": formatted_value,
        "formula": formula,
        "inputs_used": {k: _stringify(v) for k, v in inputs_used.items()},
        "unit": unit,
        "notes": list(notes or []),
    }
    if extra:
        payload.update({k: _stringify(v) for k, v in extra.items()})
    return payload


def error_response(
    message: str,
    *,
    error_type: str = "invalid_input",
    hint: str | None = None,
) -> dict[str, Any]:
    """Build a standardized error envelope with an actionable hint."""
    return {
        "status": "error",
        "error": {
            "type": error_type,
            "message": message,
            "hint": hint,
        },
    }
