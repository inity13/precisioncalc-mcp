"""
Business & SaaS metric calculations.

All monetary math uses :class:`decimal.Decimal`. Each public function returns a
standardized success envelope (see :mod:`schemas.responses`).

Supported metrics
-----------------
ltv, cac, ltv_cac_ratio, payback_period_months, contribution_margin,
gross_margin, churn_rate, mrr_growth_rate, arr, break_even_units
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Callable

from schemas.responses import CalcError, success_response

from ._util import (
    format_money,
    format_percent,
    quantize_money,
    require_non_negative,
    require_positive,
    to_decimal,
)

# ---------------------------------------------------------------------------
# Metric registry / discovery metadata
# ---------------------------------------------------------------------------

METRIC_CATALOG: dict[str, dict[str, Any]] = {
    "ltv": {
        "description": "Customer Lifetime Value: expected gross-profit a customer "
        "generates over their lifetime.",
        "required_params": ["arpu", "churn_rate"],
        "optional_params": {"gross_margin": "1 (100%)"},
        "unit": "currency",
    },
    "cac": {
        "description": "Customer Acquisition Cost: sales+marketing spend divided by "
        "new customers acquired.",
        "required_params": ["total_spend", "new_customers"],
        "optional_params": {},
        "unit": "currency",
    },
    "ltv_cac_ratio": {
        "description": "LTV:CAC ratio. Healthy SaaS benchmark is >= 3.0.",
        "required_params": ["ltv", "cac"],
        "optional_params": {},
        "unit": "ratio",
    },
    "payback_period_months": {
        "description": "Months to recover CAC from a customer's monthly gross profit.",
        "required_params": ["cac", "monthly_revenue_per_customer"],
        "optional_params": {"gross_margin": "1 (100%)"},
        "unit": "months",
    },
    "contribution_margin": {
        "description": "Revenue minus variable costs (absolute + percentage).",
        "required_params": ["revenue", "variable_costs"],
        "optional_params": {},
        "unit": "currency",
    },
    "gross_margin": {
        "description": "Gross margin ratio: (revenue - COGS) / revenue.",
        "required_params": ["revenue", "cogs"],
        "optional_params": {},
        "unit": "percent",
    },
    "churn_rate": {
        "description": "Customer churn: customers lost / customers at period start.",
        "required_params": ["customers_lost", "customers_at_start"],
        "optional_params": {},
        "unit": "percent",
    },
    "mrr_growth_rate": {
        "description": "Month-over-month MRR growth: (end - start) / start.",
        "required_params": ["beginning_mrr", "ending_mrr"],
        "optional_params": {},
        "unit": "percent",
    },
    "arr": {
        "description": "Annual Recurring Revenue from MRR (MRR x 12).",
        "required_params": ["mrr"],
        "optional_params": {},
        "unit": "currency",
    },
    "break_even_units": {
        "description": "Units to sell to cover fixed costs: "
        "fixed_costs / (price - variable_cost_per_unit).",
        "required_params": ["fixed_costs", "price_per_unit", "variable_cost_per_unit"],
        "optional_params": {},
        "unit": "units",
    },
    "nrr": {
        "description": "Net Revenue Retention: (start + expansion - contraction - churn) "
        "/ start. Includes upsell; >100% = net expansion.",
        "required_params": ["starting_mrr", "expansion_mrr", "contraction_mrr", "churned_mrr"],
        "optional_params": {},
        "unit": "percent",
    },
    "grr": {
        "description": "Gross Revenue Retention: (start - contraction - churn) / start. "
        "Excludes upsell; capped at 100%.",
        "required_params": ["starting_mrr", "contraction_mrr", "churned_mrr"],
        "optional_params": {},
        "unit": "percent",
    },
    "rule_of_40": {
        "description": "Rule of 40: revenue_growth_rate + profit_margin. Healthy SaaS >= 0.40.",
        "required_params": ["growth_rate", "profit_margin"],
        "optional_params": {},
        "unit": "percent",
    },
    "magic_number": {
        "description": "SaaS Magic Number: (QoQ revenue increase x 4) / prior-quarter S&M spend. "
        ">= 0.75 signals efficient growth.",
        "required_params": ["current_quarter_revenue", "prior_quarter_revenue",
                            "prior_quarter_sm_spend"],
        "optional_params": {},
        "unit": "ratio",
    },
}


def _get(params: dict[str, Any], key: str) -> Any:
    if key not in params or params[key] is None:
        raise CalcError(
            f"Missing required parameter '{key}'.",
            hint=f"Include '{key}' in params. See list_metrics for the full schema.",
            error_type="missing_parameter",
        )
    return params[key]


# ---------------------------------------------------------------------------
# Individual metric implementations
# ---------------------------------------------------------------------------

def _ltv(p: dict[str, Any], currency: str) -> dict[str, Any]:
    arpu = to_decimal("arpu", _get(p, "arpu"))
    churn = require_positive("churn_rate", to_decimal("churn_rate", _get(p, "churn_rate")))
    gm = to_decimal("gross_margin", p.get("gross_margin", 1))
    require_non_negative("gross_margin", gm)
    value = (arpu * gm) / churn
    notes = ["LTV = (ARPU x gross_margin) / churn_rate.",
             "churn_rate and gross_margin are decimals (0.05 = 5%)."]
    if p.get("gross_margin") is None:
        notes.append("gross_margin defaulted to 1.0 (100%); result is revenue LTV, not gross-profit LTV.")
    return success_response(
        value=value,
        formatted_value=format_money(value, currency),
        formula="LTV = (ARPU * gross_margin) / churn_rate",
        inputs_used={"arpu": arpu, "gross_margin": gm, "churn_rate": churn},
        unit=currency,
        notes=notes,
    )


def _cac(p: dict[str, Any], currency: str) -> dict[str, Any]:
    spend = require_non_negative("total_spend", to_decimal("total_spend", _get(p, "total_spend")))
    new = require_positive("new_customers", to_decimal("new_customers", _get(p, "new_customers")))
    value = spend / new
    return success_response(
        value=value,
        formatted_value=format_money(value, currency),
        formula="CAC = total_spend / new_customers",
        inputs_used={"total_spend": spend, "new_customers": new},
        unit=currency,
        notes=["Include fully-loaded sales + marketing spend for the period."],
    )


def _ltv_cac_ratio(p: dict[str, Any], currency: str) -> dict[str, Any]:
    ltv = require_non_negative("ltv", to_decimal("ltv", _get(p, "ltv")))
    cac = require_positive("cac", to_decimal("cac", _get(p, "cac")))
    value = ltv / cac
    verdict = "healthy (>=3)" if value >= 3 else ("acceptable" if value >= 1 else "unprofitable (<1)")
    return success_response(
        value=value,
        formatted_value=f"{value.quantize(Decimal('0.01'))}:1",
        formula="LTV:CAC = ltv / cac",
        inputs_used={"ltv": ltv, "cac": cac},
        unit="ratio",
        notes=[f"Interpretation: {verdict}.", "SaaS rule of thumb: aim for 3:1 or higher."],
    )


def _payback_period_months(p: dict[str, Any], currency: str) -> dict[str, Any]:
    cac = require_non_negative("cac", to_decimal("cac", _get(p, "cac")))
    mrpc = require_positive(
        "monthly_revenue_per_customer",
        to_decimal("monthly_revenue_per_customer", _get(p, "monthly_revenue_per_customer")),
    )
    gm = to_decimal("gross_margin", p.get("gross_margin", 1))
    require_positive("gross_margin", gm)
    monthly_profit = mrpc * gm
    value = cac / monthly_profit
    notes = ["Payback = CAC / (monthly_revenue_per_customer x gross_margin)."]
    if p.get("gross_margin") is None:
        notes.append("gross_margin defaulted to 1.0; supply it for gross-profit payback.")
    return success_response(
        value=value,
        formatted_value=f"{value.quantize(Decimal('0.01'))} months",
        formula="payback_months = CAC / (monthly_revenue_per_customer * gross_margin)",
        inputs_used={"cac": cac, "monthly_revenue_per_customer": mrpc, "gross_margin": gm},
        unit="months",
        notes=notes,
    )


def _contribution_margin(p: dict[str, Any], currency: str) -> dict[str, Any]:
    revenue = require_positive("revenue", to_decimal("revenue", _get(p, "revenue")))
    var_costs = require_non_negative("variable_costs", to_decimal("variable_costs", _get(p, "variable_costs")))
    value = revenue - var_costs
    ratio = value / revenue
    return success_response(
        value=value,
        formatted_value=format_money(value, currency),
        formula="contribution_margin = revenue - variable_costs",
        inputs_used={"revenue": revenue, "variable_costs": var_costs},
        unit=currency,
        notes=[f"Contribution margin ratio = {format_percent(ratio)}."],
        extra={"margin_ratio": ratio, "margin_ratio_formatted": format_percent(ratio)},
    )


def _gross_margin(p: dict[str, Any], currency: str) -> dict[str, Any]:
    revenue = require_positive("revenue", to_decimal("revenue", _get(p, "revenue")))
    cogs = require_non_negative("cogs", to_decimal("cogs", _get(p, "cogs")))
    value = (revenue - cogs) / revenue
    return success_response(
        value=value,
        formatted_value=format_percent(value),
        formula="gross_margin = (revenue - cogs) / revenue",
        inputs_used={"revenue": revenue, "cogs": cogs},
        unit="percent",
        notes=["Value is a ratio (0.60 = 60%). formatted_value is the percentage."],
    )


def _churn_rate(p: dict[str, Any], currency: str) -> dict[str, Any]:
    lost = require_non_negative("customers_lost", to_decimal("customers_lost", _get(p, "customers_lost")))
    start = require_positive("customers_at_start", to_decimal("customers_at_start", _get(p, "customers_at_start")))
    value = lost / start
    notes = ["Value is a ratio for the measured period (e.g. monthly)."]
    if lost > start:
        notes.append("customers_lost exceeds customers_at_start; churn > 100% - verify inputs.")
    return success_response(
        value=value,
        formatted_value=format_percent(value),
        formula="churn_rate = customers_lost / customers_at_start",
        inputs_used={"customers_lost": lost, "customers_at_start": start},
        unit="percent",
        notes=notes,
    )


def _mrr_growth_rate(p: dict[str, Any], currency: str) -> dict[str, Any]:
    begin = require_positive("beginning_mrr", to_decimal("beginning_mrr", _get(p, "beginning_mrr")))
    end = require_non_negative("ending_mrr", to_decimal("ending_mrr", _get(p, "ending_mrr")))
    value = (end - begin) / begin
    return success_response(
        value=value,
        formatted_value=format_percent(value),
        formula="mrr_growth_rate = (ending_mrr - beginning_mrr) / beginning_mrr",
        inputs_used={"beginning_mrr": begin, "ending_mrr": end},
        unit="percent",
        notes=["Negative value indicates contraction."],
    )


def _arr(p: dict[str, Any], currency: str) -> dict[str, Any]:
    mrr = require_non_negative("mrr", to_decimal("mrr", _get(p, "mrr")))
    value = mrr * Decimal(12)
    return success_response(
        value=value,
        formatted_value=format_money(value, currency),
        formula="ARR = MRR * 12",
        inputs_used={"mrr": mrr},
        unit=currency,
        notes=["Assumes stable MRR across 12 months (no mid-year changes)."],
    )


def _break_even_units(p: dict[str, Any], currency: str) -> dict[str, Any]:
    fixed = require_non_negative("fixed_costs", to_decimal("fixed_costs", _get(p, "fixed_costs")))
    price = to_decimal("price_per_unit", _get(p, "price_per_unit"))
    var = require_non_negative(
        "variable_cost_per_unit", to_decimal("variable_cost_per_unit", _get(p, "variable_cost_per_unit"))
    )
    unit_margin = price - var
    if unit_margin <= 0:
        raise CalcError(
            "price_per_unit must exceed variable_cost_per_unit for a break-even to exist.",
            hint="Increase price_per_unit or reduce variable_cost_per_unit.",
        )
    exact = fixed / unit_margin
    # Break-even units are whole units in practice -> round up.
    import math

    rounded_up = Decimal(math.ceil(exact))
    return success_response(
        value=exact,
        formatted_value=f"{rounded_up} units (exact: {exact.quantize(Decimal('0.0001'))})",
        formula="break_even_units = fixed_costs / (price_per_unit - variable_cost_per_unit)",
        inputs_used={"fixed_costs": fixed, "price_per_unit": price, "variable_cost_per_unit": var},
        unit="units",
        notes=[f"Contribution per unit = {format_money(unit_margin, currency)}.",
               "Round up to fully cover fixed costs."],
        extra={"units_rounded_up": rounded_up},
    )


def _nrr(p: dict[str, Any], currency: str) -> dict[str, Any]:
    start = require_positive("starting_mrr", to_decimal("starting_mrr", _get(p, "starting_mrr")))
    exp = require_non_negative("expansion_mrr", to_decimal("expansion_mrr", _get(p, "expansion_mrr")))
    contr = require_non_negative("contraction_mrr", to_decimal("contraction_mrr", _get(p, "contraction_mrr")))
    churn = require_non_negative("churned_mrr", to_decimal("churned_mrr", _get(p, "churned_mrr")))
    value = (start + exp - contr - churn) / start
    verdict = "excellent (>=1.1)" if value >= Decimal("1.1") else (
        "healthy (>=1.0)" if value >= 1 else "leaky (<1.0)")
    return success_response(
        value=value,
        formatted_value=format_percent(value),
        formula="NRR = (starting_mrr + expansion - contraction - churn) / starting_mrr",
        inputs_used={"starting_mrr": start, "expansion_mrr": exp,
                     "contraction_mrr": contr, "churned_mrr": churn},
        unit="percent",
        notes=[f"Interpretation: {verdict}.", "Includes expansion/upsell revenue."],
    )


def _grr(p: dict[str, Any], currency: str) -> dict[str, Any]:
    start = require_positive("starting_mrr", to_decimal("starting_mrr", _get(p, "starting_mrr")))
    contr = require_non_negative("contraction_mrr", to_decimal("contraction_mrr", _get(p, "contraction_mrr")))
    churn = require_non_negative("churned_mrr", to_decimal("churned_mrr", _get(p, "churned_mrr")))
    value = (start - contr - churn) / start
    notes = ["Excludes expansion; GRR <= 100% by definition."]
    if value > 1:
        notes.append("Computed GRR > 100% - check that contraction/churn are non-negative.")
    return success_response(
        value=value,
        formatted_value=format_percent(value),
        formula="GRR = (starting_mrr - contraction - churn) / starting_mrr",
        inputs_used={"starting_mrr": start, "contraction_mrr": contr, "churned_mrr": churn},
        unit="percent",
        notes=notes,
    )


def _rule_of_40(p: dict[str, Any], currency: str) -> dict[str, Any]:
    growth = to_decimal("growth_rate", _get(p, "growth_rate"))
    margin = to_decimal("profit_margin", _get(p, "profit_margin"))
    value = growth + margin
    verdict = "passes (>=40%)" if value >= Decimal("0.40") else "below 40%"
    return success_response(
        value=value,
        formatted_value=format_percent(value),
        formula="rule_of_40 = revenue_growth_rate + profit_margin",
        inputs_used={"growth_rate": growth, "profit_margin": margin},
        unit="percent",
        notes=[f"Interpretation: {verdict}.",
               "Both inputs are decimals (0.30 = 30%). Margin may be negative."],
    )


def _magic_number(p: dict[str, Any], currency: str) -> dict[str, Any]:
    cur_rev = to_decimal("current_quarter_revenue", _get(p, "current_quarter_revenue"))
    prior_rev = to_decimal("prior_quarter_revenue", _get(p, "prior_quarter_revenue"))
    sm = require_positive("prior_quarter_sm_spend",
                          to_decimal("prior_quarter_sm_spend", _get(p, "prior_quarter_sm_spend")))
    value = ((cur_rev - prior_rev) * Decimal(4)) / sm
    verdict = ("efficient (>=0.75)" if value >= Decimal("0.75")
               else ("acceptable (>=0.5)" if value >= Decimal("0.5") else "inefficient (<0.5)"))
    return success_response(
        value=value,
        formatted_value=f"{value.quantize(Decimal('0.01'))}",
        formula="magic_number = ((current_quarter_revenue - prior_quarter_revenue) * 4) / prior_quarter_sm_spend",
        inputs_used={"current_quarter_revenue": cur_rev, "prior_quarter_revenue": prior_rev,
                     "prior_quarter_sm_spend": sm},
        unit="ratio",
        notes=[f"Interpretation: {verdict}.",
               "Annualizes the quarter-over-quarter revenue gain (x4)."],
    )


_DISPATCH: dict[str, Callable[[dict[str, Any], str], dict[str, Any]]] = {
    "ltv": _ltv,
    "cac": _cac,
    "ltv_cac_ratio": _ltv_cac_ratio,
    "payback_period_months": _payback_period_months,
    "contribution_margin": _contribution_margin,
    "gross_margin": _gross_margin,
    "churn_rate": _churn_rate,
    "mrr_growth_rate": _mrr_growth_rate,
    "arr": _arr,
    "break_even_units": _break_even_units,
    "nrr": _nrr,
    "grr": _grr,
    "rule_of_40": _rule_of_40,
    "magic_number": _magic_number,
}


def calculate_metric(metric: str, params: dict[str, Any], currency: str = "USD") -> dict[str, Any]:
    """Dispatch a metric calculation.

    Parameters
    ----------
    metric:
        One of the keys in :data:`METRIC_CATALOG`.
    params:
        Metric-specific numeric parameters (see catalog).
    currency:
        ISO currency code used for formatting monetary results.
    """
    key = (metric or "").strip().lower()
    if key not in _DISPATCH:
        raise CalcError(
            f"Unknown metric '{metric}'.",
            hint=f"Supported metrics: {', '.join(sorted(_DISPATCH))}. Call list_metrics for schemas.",
            error_type="unknown_metric",
        )
    if not isinstance(params, dict):
        raise CalcError("params must be an object/dict of named numeric parameters.",
                        hint='Example: {"arpu": 100, "churn_rate": 0.05}')
    return _DISPATCH[key](params, (currency or "USD").upper())
