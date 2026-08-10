"""
PrecisionCalc MCP server (v2).

A deterministic utility MCP server exposing high-precision business, finance,
and operational calculation tools. All monetary/financial math uses the
:mod:`decimal` module -- never floats -- so results are exact and reproducible.

Transports
----------
* stdio (default):     ``python server.py``  or  ``python server.py stdio``
* Streamable HTTP:     ``python server.py http``  (API-key/rate-limit aware)

Environment variables (see security.py / observability.py / calculations for the
full list): PRECISIONCALC_HOST, PRECISIONCALC_PORT, PRECISIONCALC_API_KEYS,
PRECISIONCALC_RATE_LIMIT_PER_MIN, PRECISIONCALC_FX_PROVIDER, PRECISIONCALC_OTEL.
"""

from __future__ import annotations

import os
import sys
from typing import Any, Optional

# --- MCP server framework import (compatible across SDK versions) -----------
try:  # mcp >= 2.0
    from mcp.server.mcpserver import MCPServer as _ServerClass  # type: ignore
    _SERVER_FLAVOR = "mcpserver"
except ImportError:  # pragma: no cover
    try:  # mcp 1.x
        from mcp.server.fastmcp import FastMCP as _ServerClass  # type: ignore
        _SERVER_FLAVOR = "fastmcp"
    except ImportError:
        from fastmcp import FastMCP as _ServerClass  # type: ignore
        _SERVER_FLAVOR = "fastmcp"

from calculations import business_days as bd
from calculations import currency as fx
from calculations import finance as fin
from calculations import growth as gr
from calculations import metrics as mx
from observability import log_event, observe_tool
from schemas.responses import CalcError, error_response

SERVER_NAME = "PrecisionCalc"
SERVER_VERSION = "2.0.0"

mcp = _ServerClass(SERVER_NAME)


# ---------------------------------------------------------------------------
# Cross-cutting: uniform error handling + observability
# ---------------------------------------------------------------------------

def _guarded(tool: str, fn, *args, **kwargs) -> dict[str, Any]:
    """Run a calculation with structured logging, translating exceptions into
    the standard error envelope so tools never raise across the MCP boundary."""
    with observe_tool(tool) as ctx:
        try:
            result = fn(*args, **kwargs)
            if isinstance(result, dict) and result.get("status") == "error":
                ctx["status"] = "error"
            return result
        except CalcError as exc:
            ctx["status"] = "error"
            return error_response(str(exc), error_type=exc.error_type, hint=exc.hint)
        except ZeroDivisionError:
            ctx["status"] = "error"
            return error_response("Division by zero in calculation.", error_type="math_error",
                                  hint="Check that denominators (rates, counts, prices) are non-zero.")
        except Exception as exc:  # pragma: no cover - safety net
            ctx["status"] = "error"
            log_event(f"unexpected_error in {tool}: {exc}", level=40)
            return error_response(f"Unexpected error: {exc}", error_type="internal_error",
                                  hint="Verify input types and values; report if this persists.")


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@mcp.tool()
def calculate_metric(metric: str, params: dict[str, Any], currency: str = "USD") -> dict[str, Any]:
    """Compute a business / SaaS / finance metric with exact decimal precision.

    Supported ``metric`` values -> ``params`` keys (rates/margins are decimals,
    0.05 = 5%):
      * ltv                  -> arpu, churn_rate, [gross_margin=1]
      * cac                  -> total_spend, new_customers
      * ltv_cac_ratio        -> ltv, cac
      * payback_period_months-> cac, monthly_revenue_per_customer, [gross_margin=1]
      * contribution_margin  -> revenue, variable_costs
      * gross_margin         -> revenue, cogs
      * churn_rate           -> customers_lost, customers_at_start
      * mrr_growth_rate      -> beginning_mrr, ending_mrr
      * arr                  -> mrr
      * break_even_units     -> fixed_costs, price_per_unit, variable_cost_per_unit
      * nrr                  -> starting_mrr, expansion_mrr, contraction_mrr, churned_mrr
      * grr                  -> starting_mrr, contraction_mrr, churned_mrr
      * rule_of_40           -> growth_rate, profit_margin
      * magic_number         -> current_quarter_revenue, prior_quarter_revenue, prior_quarter_sm_spend

    Call ``list_metrics`` for full schemas.

    Args:
        metric: Name of the metric to compute.
        params: Object of named numeric parameters for the chosen metric.
        currency: ISO currency code used to format monetary results.
    """
    return _guarded("calculate_metric", mx.calculate_metric, metric, params, currency)


@mcp.tool()
def list_metrics() -> dict[str, Any]:
    """List every supported metric with description, required and optional params."""
    return {
        "status": "success",
        "count": len(mx.METRIC_CATALOG),
        "metrics": mx.METRIC_CATALOG,
        "notes": ["Rates and margins are decimals (0.05 = 5%).",
                  "All monetary results use exact Decimal arithmetic."],
    }


@mcp.tool()
def currency_convert(
    amount: float,
    from_currency: str,
    to_currency: str,
    date: Optional[str] = None,
    live: Optional[bool] = None,
) -> dict[str, Any]:
    """Convert an amount between major currencies with Decimal precision.

    Supported: USD, EUR, GBP, JPY, CAD, AUD, CHF, CNY, INR. Returns the converted
    amount, exact cross-rate, provider, and timestamps.

    Rates: static offline table by default. A ``date`` (YYYY-MM-DD) or ``live=true``
    uses live/historical ECB rates (frankfurter.app); on any network failure the
    server falls back to static rates with a warning note.

    Args:
        amount: Amount in ``from_currency`` (>= 0).
        from_currency: Source ISO 4217 code.
        to_currency: Target ISO 4217 code.
        date: Optional historical date (YYYY-MM-DD) -> live provider.
        live: Force live (true) or static (false); null = auto.
    """
    return _guarded("currency_convert", fx.convert_currency, amount, from_currency,
                    to_currency, date, live)


@mcp.tool()
def business_days(
    operation: str,
    start_date: str,
    days: Optional[int] = None,
    end_date: Optional[str] = None,
    region: str = "US",
    custom_holidays: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Business-day arithmetic honoring weekends and regional public holidays.

    Operations: add_business_days (needs days, may be negative) |
    count_business_days (needs end_date, inclusive) | next_business_day |
    previous_business_day.

    Regions: US, UK, EU, NONE (weekends only) are built in and offline. If the
    optional ``holidays`` package is installed, ANY ISO country code also works
    (DE, FR, CA, AU, JP, IN, ...). ``custom_holidays`` (YYYY-MM-DD list) are added.

    Args:
        operation: One of the four operations above.
        start_date: Anchor date, ISO YYYY-MM-DD.
        days: Business days to add (add_business_days; negative allowed).
        end_date: End date (count_business_days), ISO.
        region: US | UK | EU | NONE | ISO country code.
        custom_holidays: Optional extra holiday dates (YYYY-MM-DD).
    """
    return _guarded("business_days", bd.business_days, operation, start_date, days,
                    end_date, region, custom_holidays)


@mcp.tool()
def compound_growth(
    operation: str,
    rate: Optional[float] = None,
    years: Optional[float] = None,
    present_value: Optional[float] = None,
    future_value: Optional[float] = None,
    begin_value: Optional[float] = None,
    end_value: Optional[float] = None,
    compounding: str = "annually",
    currency: str = "USD",
) -> dict[str, Any]:
    """Compound-interest / growth math (future value, present value, or CAGR).

    Operations: future_value (needs rate, years, present_value) | present_value
    (needs rate, years, future_value) | cagr (needs begin_value, end_value, years).
    ``rate`` is an annual decimal (0.08 = 8%). ``compounding``: daily | weekly |
    monthly | quarterly | semiannually | annually | continuous.
    """
    return _guarded("compound_growth", gr.compound_growth, operation, rate, years,
                    present_value, future_value, begin_value, end_value, compounding, currency)


@mcp.tool()
def net_present_value(rate: float, cashflows: list[float], currency: str = "USD") -> dict[str, Any]:
    """Net Present Value (discounted cash flow) of a periodic cashflow series.

    NPV = sum(CF_t / (1 + rate)^t), t = 0..n. Index 0 is period 0 (typically the
    negative initial investment).

    Args:
        rate: Discount rate per period as a decimal (0.10 = 10%).
        cashflows: List of >= 2 numbers, e.g. [-10000, 3000, 4200, 6800].
        currency: ISO code for formatting.
    """
    return _guarded("net_present_value", fin.net_present_value, rate, cashflows, currency)


@mcp.tool()
def internal_rate_of_return(cashflows: list[float], guess: float = 0.1) -> dict[str, Any]:
    """Internal Rate of Return: the per-period rate where NPV == 0.

    Solved with Newton's method + a bisection fallback. Requires at least one
    negative and one positive cashflow.

    Args:
        cashflows: List of >= 2 numbers, e.g. [-10000, 3000, 4200, 6800].
        guess: Optional starting rate for Newton's method (decimal).
    """
    return _guarded("internal_rate_of_return", fin.internal_rate_of_return, cashflows, guess)


@mcp.tool()
def loan_amortization(
    principal: float,
    annual_rate: float,
    term_months: int,
    extra_payment: float = 0,
    currency: str = "USD",
    include_schedule: bool = False,
) -> dict[str, Any]:
    """Level-payment loan: monthly payment, total interest, payoff, and schedule.

    payment = P * r / (1 - (1 + r)^-n), r = annual_rate/12, n = term_months.

    Args:
        principal: Loan amount (> 0).
        annual_rate: Nominal annual rate as a decimal (0.06 = 6%).
        term_months: Number of monthly payments (> 0).
        extra_payment: Optional extra principal each month (shortens the term).
        currency: ISO code for formatting.
        include_schedule: If true, return the full month-by-month schedule.
    """
    return _guarded("loan_amortization", fin.loan_amortization, principal, annual_rate,
                    term_months, extra_payment, currency, include_schedule)


@mcp.tool()
def depreciation(
    method: str,
    cost: float,
    salvage_value: float,
    useful_life_years: int,
    currency: str = "USD",
) -> dict[str, Any]:
    """Asset depreciation schedule.

    Methods: straight_line | declining_balance (double-declining) |
    sum_of_years_digits. Returns Year-1 depreciation plus the full yearly schedule
    (book value converges to salvage_value).

    Args:
        method: Depreciation method (see above).
        cost: Initial asset cost.
        salvage_value: Residual value at end of life (<= cost).
        useful_life_years: Whole years (> 0).
        currency: ISO code for formatting.
    """
    return _guarded("depreciation", fin.depreciation, method, cost, salvage_value,
                    useful_life_years, currency)


# Registry of tools callable via batch_calculate (name -> underlying function).
_BATCHABLE = {
    "calculate_metric": calculate_metric,
    "currency_convert": currency_convert,
    "business_days": business_days,
    "compound_growth": compound_growth,
    "net_present_value": net_present_value,
    "internal_rate_of_return": internal_rate_of_return,
    "loan_amortization": loan_amortization,
    "depreciation": depreciation,
    "list_metrics": list_metrics,
}


@mcp.tool()
def batch_calculate(calls: list[dict[str, Any]]) -> dict[str, Any]:
    """Run many calculations in one request to cut agent round-trips.

    Each item is ``{"tool": <name>, "arguments": {...}}``. Results are returned in
    order; a failure in one item never aborts the batch (its slot holds an error
    envelope). Batchable tools: calculate_metric, currency_convert, business_days,
    compound_growth, net_present_value, internal_rate_of_return, loan_amortization,
    depreciation, list_metrics.

    Args:
        calls: List of {"tool": str, "arguments": object} items (max 100).
    """
    if not isinstance(calls, list) or not calls:
        return error_response("calls must be a non-empty list of {tool, arguments}.",
                              hint='Example: [{"tool":"arr","arguments":{...}}] is WRONG; '
                                   'use {"tool":"calculate_metric","arguments":{"metric":"arr","params":{"mrr":1000}}}.')
    if len(calls) > 100:
        return error_response("Batch too large (max 100 calls).", error_type="too_many_calls",
                              hint="Split into batches of <= 100.")
    results = []
    for i, item in enumerate(calls):
        if not isinstance(item, dict) or "tool" not in item:
            results.append({"index": i, "status": "error",
                            "error": {"type": "invalid_item", "message": "Each item needs a 'tool' key."}})
            continue
        name = item.get("tool")
        args = item.get("arguments", {}) or {}
        fn = _BATCHABLE.get(name)
        if fn is None:
            results.append({"index": i, "tool": name, "status": "error",
                            "error": {"type": "unknown_tool",
                                      "message": f"'{name}' is not batchable.",
                                      "hint": f"Batchable: {', '.join(_BATCHABLE)}."}})
            continue
        try:
            out = fn(**args)
        except TypeError as exc:
            out = error_response(f"Bad arguments for '{name}': {exc}", error_type="invalid_arguments",
                                 hint="Check parameter names against the tool's schema.")
        results.append({"index": i, "tool": name, "result": out})
    return {"status": "success", "count": len(results), "results": results}


@mcp.tool()
def health_check() -> dict[str, Any]:
    """Return server health/status metadata (name, version, tools, capabilities)."""
    return {
        "status": "ok",
        "server": SERVER_NAME,
        "version": SERVER_VERSION,
        "precision": "decimal (50 significant digits)",
        "tools": sorted(list(_BATCHABLE.keys()) + ["batch_calculate", "list_metrics", "health_check"]),
        "supported_metrics": sorted(mx.METRIC_CATALOG.keys()),
        "supported_currencies": list(fx.SUPPORTED_CURRENCIES),
        "fx_provider": os.getenv("PRECISIONCALC_FX_PROVIDER", "static"),
        "supported_regions": list(bd.SUPPORTED_REGIONS),
        "holidays_library": bd.HOLIDAYS_LIB_AVAILABLE,
        "auth_required": bool(os.getenv("PRECISIONCALC_API_KEYS", "").strip()),
    }


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def _run_http() -> None:
    """Serve streamable HTTP with API-key auth + rate limiting via uvicorn."""
    import uvicorn

    from security import AuthRateLimitMiddleware

    host = os.getenv("PRECISIONCALC_HOST", "127.0.0.1")
    port = int(os.getenv("PRECISIONCALC_PORT", "8000"))

    if _SERVER_FLAVOR == "fastmcp":
        mcp.settings.host = host  # type: ignore[attr-defined]
        mcp.settings.port = port  # type: ignore[attr-defined]
    app = mcp.streamable_http_app()  # Starlette ASGI app exposing /mcp
    wrapped = AuthRateLimitMiddleware(app)
    log_event("http_server_starting", host=host, port=port, endpoint="/mcp")
    uvicorn.run(wrapped, host=host, port=port, log_level=os.getenv("PRECISIONCALC_LOG_LEVEL", "info").lower())


def main() -> None:
    """Run the server on the requested transport (stdio default)."""
    transport = (sys.argv[1] if len(sys.argv) > 1 else "stdio").lower()
    if transport in ("stdio", ""):
        mcp.run(transport="stdio")
    elif transport in ("http", "streamable-http", "streamable_http"):
        _run_http()
    else:
        sys.stderr.write(f"Unknown transport '{transport}'. Use 'stdio' or 'http'.\n")
        raise SystemExit(2)


if __name__ == "__main__":
    main()
