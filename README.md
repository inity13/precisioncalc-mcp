# PrecisionCalc MCP

A deterministic **Model Context Protocol (MCP)** server that gives LLM agents
reliable, **high-precision business, finance, and operational calculations**.

LLMs routinely lose precision or hallucinate on multi-step financial formulas,
currency conversions, business-day logic, and growth math. PrecisionCalc offloads
that work to exact, transparent tools. Every monetary/financial value is computed
with Python's `decimal` module (**never floats**), and every result is returned in
a **consistent, agent-parseable JSON envelope** that includes the exact value, a
human-readable value, the **formula applied**, the **inputs used**, the unit, and
any **assumptions/warnings**.

> **v2 highlights:** live + historical FX (ECB), 14 SaaS metrics, NPV/IRR,
> loan amortization, depreciation, a `batch_calculate` tool, per-country holidays,
> API-key auth + rate limiting + usage metering on the HTTP transport, structured
> JSON logging, optional OpenTelemetry tracing, and property-based tests.

## 🌐 Live hosted server (free, no install)

A public remote MCP server runs on Cloudflare's edge — point any Streamable-HTTP
MCP client at it:

```
https://precisioncalc-mcp.pages.dev/mcp
```

```json
{ "mcpServers": { "precisioncalc": {
    "type": "http", "url": "https://precisioncalc-mcp.pages.dev/mcp" } } }
```

The edge build (`worker-src/`) is a Cloudflare Pages Function that mirrors the
Python engine using `decimal.js` — verified **17/17 exact output parity**. Landing
page + docs: <https://precisioncalc-mcp.pages.dev>.

### Plans (hosted endpoint)

| Plan | Price | Daily calls | Live/historical FX | `batch_calculate` |
|------|-------|-------------|--------------------|-------------------|
| **Free** (no key) | $0 | 15 / day (per IP) | ❌ static only | ❌ |
| **Starter** | $12/mo | 5,000 / day | ✅ | ✅ |
| **Pro** | $39/mo | 50,000 / day | ✅ | ✅ |

Checkout is Stripe (subscription). On success you get an API key instantly; send it as
`X-API-Key: <key>` (or `Authorization: Bearer <key>`). Manage/cancel at `/portal`.
When a limit is hit, tools return a structured `status:"error"` envelope with `type`,
`usage`, and an `upgrade` block containing checkout URLs — so an **agent can surface the
paywall to the user and act on it**. Self-host (below) for unlimited calls with your own keys.

Billing internals live in `worker-src/billing.mjs` (Stripe REST + Cloudflare KV for keys
and daily counters). Server env: `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`,
`PRICE_STARTER`, `PRICE_PRO`, `FREE_DAILY`, `STARTER_DAILY`, `PRO_DAILY`, and a
`PRECISIONCALC_KV` namespace binding (see `wrangler.toml`).

Rebuild/redeploy the edge server:
```bash
npm install          # decimal.js + esbuild
npm run deploy       # bundles worker-src -> site/_worker.js and deploys to Pages
```

---

## What it does

11 tools, all returning a uniform structured response:

| Tool | Purpose |
|------|---------|
| `calculate_metric` | 14 SaaS/business metrics (LTV, CAC, churn, MRR growth, NRR, GRR, Rule of 40, magic number, break-even, ...) |
| `currency_convert` | Convert 9 major currencies; static (offline) or live/historical ECB rates |
| `business_days` | Add/count business days, next/previous; US/UK/EU + **any ISO country** + custom holidays |
| `compound_growth` | Future value, present value, CAGR; 7 compounding frequencies incl. continuous |
| `net_present_value` | NPV / discounted cash flow of a cashflow series |
| `internal_rate_of_return` | IRR (Newton + bisection fallback) |
| `loan_amortization` | Level-payment loan: payment, totals, full schedule, extra-payment payoff |
| `depreciation` | straight-line / declining-balance / sum-of-years-digits schedules |
| `batch_calculate` | Run many calculations in one request |
| `list_metrics` | Discovery: every metric with descriptions + required params |
| `health_check` | Server status, version, capabilities |

### Consistent response envelope

Success:
```json
{
  "status": "success",
  "value": "1600",                       // exact, full-precision (string for money/rates)
  "formatted_value": "$1,600.00",        // human-readable
  "formula": "LTV = (ARPU * gross_margin) / churn_rate",
  "inputs_used": { "arpu": "100", "gross_margin": "0.8", "churn_rate": "0.05" },
  "unit": "USD",
  "notes": ["LTV = (ARPU x gross_margin) / churn_rate.", "..."]
}
```

Error (never raised across the tool boundary):
```json
{
  "status": "error",
  "error": {
    "type": "missing_parameter",
    "message": "Missing required parameter 'churn_rate'.",
    "hint": "Include 'churn_rate' in params. See list_metrics for the full schema."
  }
}
```

---

## Project structure

```
precisioncalc-mcp/
├── server.py                 # MCP server: tool definitions + transports
├── security.py               # API-key auth + token-bucket rate limit + metering (ASGI)
├── observability.py          # Structured JSON logging + optional OpenTelemetry
├── requirements.txt / pyproject.toml
├── Dockerfile / .dockerignore
├── fly.toml / render.yaml    # One-click hosting configs
├── .env.example
├── calculations/
│   ├── _util.py              # Decimal coercion, validation, formatting
│   ├── metrics.py            # 14 business/SaaS metrics + catalog
│   ├── currency.py           # FX: static + Frankfurter (live/historical) providers
│   ├── business_days.py      # Region-aware holidays (built-in + `holidays` lib)
│   ├── growth.py             # FV / PV / CAGR
│   └── finance.py            # NPV / IRR / loan amortization / depreciation
├── schemas/responses.py      # Response envelope helpers
├── examples/agent_example.py # End-to-end MCP client demo
├── site/                     # Static landing/docs page (Cloudflare Pages)
└── tests/                    # 49 unit tests + Hypothesis property tests
```

---

## Requirements

* Python **3.11+** (developed/tested on 3.12)
* Core: `mcp`, `python-dateutil`
* Recommended: `uvicorn` + `starlette` (HTTP transport), `holidays` (per-country calendars)
* Optional: `opentelemetry-sdk` (tracing), `pytest` + `hypothesis` (tests)

The server auto-detects the SDK layout and works with `mcp >= 2.0`
(`MCPServer`), `mcp 1.x` (`FastMCP`), or the standalone `fastmcp` package.

---

## Run it locally

```bash
cd precisioncalc-mcp
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt          # or: pip install -e ".[all]"

# stdio transport (default; how MCP clients launch it)
python server.py            # or: precisioncalc-mcp   (console entrypoint)

# Streamable HTTP transport (endpoint: /mcp)
python server.py http
PRECISIONCALC_API_KEYS=key1,key2 PRECISIONCALC_FX_PROVIDER=frankfurter python server.py http
```

Demo + tests:
```bash
python examples/agent_example.py         # live end-to-end over stdio
python tests/test_calculations.py        # 28 core tests (no pytest needed)
python tests/test_v2.py                  # 17 v2 tests
python tests/test_properties.py          # Hypothesis property tests
# or simply:  pytest -q
```

### Register with an MCP client (stdio)
```json
{ "mcpServers": { "precisioncalc": {
    "command": "python", "args": ["/absolute/path/to/precisioncalc-mcp/server.py"] } } }
```

---

## Deploy

### Docker
```bash
docker build -t precisioncalc-mcp .
docker run --rm -p 8000:8000 -e PRECISIONCALC_API_KEYS=your-key precisioncalc-mcp
docker run --rm -i precisioncalc-mcp python server.py stdio
```

### Fly.io
```bash
fly launch --no-deploy
fly secrets set PRECISIONCALC_API_KEYS=key1,key2
fly deploy
```

### Render.com
Push to GitHub, then **New + → Blueprint** and point at the repo (`render.yaml`).
Set `PRECISIONCALC_API_KEYS` as a secret in the dashboard.

---

## Configuration (env vars)

| Var | Default | Purpose |
|-----|---------|---------|
| `PRECISIONCALC_HOST` / `PRECISIONCALC_PORT` | `127.0.0.1` / `8000` | HTTP bind |
| `PRECISIONCALC_API_KEYS` | *(empty)* | Comma-separated keys. Empty = open mode (still metered/limited by IP) |
| `PRECISIONCALC_RATE_LIMIT_PER_MIN` / `_BURST` | `120` / `40` | Token-bucket limits |
| `PRECISIONCALC_METRICS_PATH` | `/metrics` | Usage-metrics endpoint |
| `PRECISIONCALC_FX_PROVIDER` | `static` | `static` or `frankfurter` (live/historical ECB) |
| `PRECISIONCALC_FX_TTL` / `_TIMEOUT` | `3600` / `4` | FX cache TTL / HTTP timeout (s) |
| `PRECISIONCALC_LOG_LEVEL` / `_LOG_JSON` | `INFO` / `1` | Logging |
| `PRECISIONCALC_OTEL` | `0` | `1` enables OpenTelemetry tracing if SDK present |

---

## Tools & parameters

### `calculate_metric(metric, params, currency="USD")`
Rates/margins are decimals (`0.05` = 5%).

| metric | params | unit |
|--------|--------|------|
| `ltv` | `arpu`, `churn_rate`, `gross_margin`(=1) | currency |
| `cac` | `total_spend`, `new_customers` | currency |
| `ltv_cac_ratio` | `ltv`, `cac` | ratio |
| `payback_period_months` | `cac`, `monthly_revenue_per_customer`, `gross_margin`(=1) | months |
| `contribution_margin` | `revenue`, `variable_costs` | currency |
| `gross_margin` | `revenue`, `cogs` | percent |
| `churn_rate` | `customers_lost`, `customers_at_start` | percent |
| `mrr_growth_rate` | `beginning_mrr`, `ending_mrr` | percent |
| `arr` | `mrr` | currency |
| `break_even_units` | `fixed_costs`, `price_per_unit`, `variable_cost_per_unit` | units |
| `nrr` | `starting_mrr`, `expansion_mrr`, `contraction_mrr`, `churned_mrr` | percent |
| `grr` | `starting_mrr`, `contraction_mrr`, `churned_mrr` | percent |
| `rule_of_40` | `growth_rate`, `profit_margin` | percent |
| `magic_number` | `current_quarter_revenue`, `prior_quarter_revenue`, `prior_quarter_sm_spend` | ratio |

### `currency_convert(amount, from_currency, to_currency, date=None, live=None)`
USD, EUR, GBP, JPY, CAD, AUD, CHF, CNY, INR. `date` (YYYY-MM-DD) or `live=true`
uses live/historical ECB rates (frankfurter.app), with automatic **static fallback**
on any network failure. Returns rate, provider, `is_live`, and timestamps.

### `business_days(operation, start_date, days=None, end_date=None, region="US", custom_holidays=None)`
`operation`: `add_business_days` | `count_business_days` (inclusive) | `next_business_day` |
`previous_business_day`. `region`: `US` | `UK` | `EU` | `NONE`, or **any ISO country code**
when the `holidays` package is installed (DE, FR, CA, AU, JP, IN, ...).

### `compound_growth(operation, rate, years, present_value, future_value, begin_value, end_value, compounding="annually", currency="USD")`
`operation`: `future_value` | `present_value` | `cagr`.
`compounding`: `daily | weekly | monthly | quarterly | semiannually | annually | continuous`.

### `net_present_value(rate, cashflows, currency="USD")`
NPV = Σ CFₜ/(1+rate)ᵗ. `cashflows[0]` = period 0 (usually the negative outlay).

### `internal_rate_of_return(cashflows, guess=0.1)`
Per-period rate where NPV = 0. Requires a sign change in the cashflows.

### `loan_amortization(principal, annual_rate, term_months, extra_payment=0, currency="USD", include_schedule=false)`
Returns monthly payment, months-to-payoff, total interest, total paid, and (optionally)
the full month-by-month schedule.

### `depreciation(method, cost, salvage_value, useful_life_years, currency="USD")`
`method`: `straight_line` | `declining_balance` | `sum_of_years_digits`. Returns the
full yearly schedule; book value converges to `salvage_value`.

### `batch_calculate(calls)`
`calls`: list of `{"tool": <name>, "arguments": {...}}` (max 100). One item failing never
aborts the batch.

### `list_metrics()` / `health_check()`
Discovery + status. No parameters.

---

## Example MCP tool-call payloads

```json
{ "name": "calculate_metric",
  "arguments": { "metric": "rule_of_40", "params": { "growth_rate": 0.30, "profit_margin": 0.15 } } }
```
```json
{ "name": "currency_convert",
  "arguments": { "amount": 5000, "from_currency": "EUR", "to_currency": "GBP", "date": "2024-01-15" } }
```
```json
{ "name": "net_present_value",
  "arguments": { "rate": 0.10, "cashflows": [-10000, 3000, 4200, 6800] } }
```
```json
{ "name": "loan_amortization",
  "arguments": { "principal": 250000, "annual_rate": 0.065, "term_months": 360, "include_schedule": false } }
```
```json
{ "name": "batch_calculate",
  "arguments": { "calls": [
    { "tool": "internal_rate_of_return", "arguments": { "cashflows": [-10000, 3000, 4200, 6800] } },
    { "tool": "depreciation", "arguments": { "method": "declining_balance", "cost": 50000, "salvage_value": 5000, "useful_life_years": 5 } }
  ] } }
```

---

## Design decisions & assumptions

* **Decimal everywhere** money/rates matter; `value` is serialized as a **string** to
  prevent float loss in JSON, with a separate pretty `formatted_value`. Precision = 50 sig figs.
* **Rates/margins are decimals** (`0.05` = 5%), documented in every tool.
* **FX**: `static` USD-based table (`as_of` 2024-06-01) is the offline default; `frankfurter`
  provider adds live + historical ECB rates with in-memory TTL cache and graceful static fallback.
* **Business days**: holidays computed per-year (floating US, Easter-based UK/EU); `count` is
  inclusive; `add` accepts negatives; custom holidays unioned; any ISO country via `holidays` lib.
* **IRR** uses Newton's method with a bracketed bisection fallback; requires a sign change.
* **Errors never cross the tool boundary as exceptions** — always `status:"error"` with a machine
  `type` + actionable `hint`.
* **HTTP hardening** is opt-in via env: API keys, token-bucket rate limiting, `/metrics` usage.
* **SDK compatibility shim** runs on `mcp>=2.0`, `mcp 1.x`, or standalone `fastmcp` unchanged.

---

## Monetization hooks

* **Auth** — `PRECISIONCALC_API_KEYS`; requests need `X-API-Key` or `Authorization: Bearer`.
* **Rate limiting** — per-key token bucket (per-IP in open mode); swap for Redis to scale.
* **Usage metering** — in-memory counters exposed at `/metrics`; the seam for per-key billing.
* **FX provider** — `calculations/currency.py::RateProvider` is the drop-in point for a licensed feed.

---

## Roadmap (post-v2)

1. Redis-backed rate limiting + billing-grade usage metering.
2. Persisted historical FX + more providers; multi-currency carry through metrics.
3. Bond pricing/yield, WACC, options (Black-Scholes), tax/VAT, unit conversions.
4. Prometheus exporter + Grafana dashboard alongside OTel traces.
5. Published PyPI package + Docker image on GHCR; hosted multi-tenant SaaS.
