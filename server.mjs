#!/usr/bin/env node
// PrecisionCalc MCP — local stdio server (self-host / npm / Glama-runnable).
// Dependency-light: wraps the same deterministic engine as the hosted edge
// worker (worker-src/tools.mjs) and speaks MCP over newline-delimited JSON-RPC
// on stdio. No state. Run: `node server.mjs` (or `npx precisioncalc-mcp`).
import readline from "node:readline";
import * as T from "./worker-src/tools.mjs";

const PROTOCOL_VERSION = "2024-11-05";
const SERVER_INFO = { name: "PrecisionCalc", version: "2.1.0" };

const str = { type: "string" };
const num = { type: "number" };

const TOOLS = {
  calculate_metric: { description: "Compute a business/SaaS/finance metric with exact decimal precision. Metrics: ltv, cac, ltv_cac_ratio, payback_period_months, contribution_margin, gross_margin, churn_rate, mrr_growth_rate, arr, break_even_units, nrr, grr, rule_of_40, magic_number. Rates/margins are decimals (0.05=5%). Call list_metrics for schemas.", inputSchema: { type: "object", properties: { metric: str, params: { type: "object" }, currency: { type: "string", default: "USD" } }, required: ["metric", "params"] }, handler: (a) => T.calculate_metric(a) },
  list_metrics: { description: "List every supported metric with descriptions and required/optional params.", inputSchema: { type: "object", properties: {} }, handler: () => T.list_metrics() },
  currency_convert: { description: "Convert an amount between major currencies (USD, EUR, GBP, JPY, CAD, AUD, CHF, CNY, INR) with Decimal precision. Static offline table by default; live/historical ECB rates via date/live=true.", inputSchema: { type: "object", properties: { amount: num, from_currency: str, to_currency: str, date: { type: "string" }, live: { type: "boolean" } }, required: ["amount", "from_currency", "to_currency"] }, handler: (a) => T.currency_convert(a) },
  business_days: { description: "Business-day arithmetic honoring weekends + regional holidays. operation: add_business_days | count_business_days | next_business_day | previous_business_day. region: US | UK | EU | NONE. custom_holidays: list of YYYY-MM-DD.", inputSchema: { type: "object", properties: { operation: str, start_date: str, days: { type: "integer" }, end_date: str, region: { type: "string", default: "US" }, custom_holidays: { type: "array", items: str } }, required: ["operation", "start_date"] }, handler: (a) => T.business_days(a) },
  compound_growth: { description: "Compound-interest/growth math. operation: future_value | present_value | cagr. rate is annual decimal (0.08=8%). compounding: daily|weekly|monthly|quarterly|semiannually|annually|continuous.", inputSchema: { type: "object", properties: { operation: str, rate: num, years: num, present_value: num, future_value: num, begin_value: num, end_value: num, compounding: { type: "string", default: "annually" }, currency: { type: "string", default: "USD" } }, required: ["operation"] }, handler: (a) => T.compound_growth(a) },
  net_present_value: { description: "Net Present Value (discounted cash flow). NPV = sum(CF_t/(1+rate)^t). cashflows[0] = period 0 (usually the negative outlay).", inputSchema: { type: "object", properties: { rate: num, cashflows: { type: "array", items: num }, currency: { type: "string", default: "USD" } }, required: ["rate", "cashflows"] }, handler: (a) => T.net_present_value(a) },
  internal_rate_of_return: { description: "Internal Rate of Return: per-period rate where NPV=0 (Newton + bisection). Requires a sign change in cashflows.", inputSchema: { type: "object", properties: { cashflows: { type: "array", items: num }, guess: { type: "number", default: 0.1 } }, required: ["cashflows"] }, handler: (a) => T.internal_rate_of_return(a) },
  loan_amortization: { description: "Level-payment loan: monthly payment, total interest, payoff, and (optional) full schedule. payment = P*r/(1-(1+r)^-n), r=annual_rate/12.", inputSchema: { type: "object", properties: { principal: num, annual_rate: num, term_months: { type: "integer" }, extra_payment: { type: "number", default: 0 }, currency: { type: "string", default: "USD" }, include_schedule: { type: "boolean", default: false } }, required: ["principal", "annual_rate", "term_months"] }, handler: (a) => T.loan_amortization(a) },
  depreciation: { description: "Asset depreciation schedule. method: straight_line | declining_balance | sum_of_years_digits. Book value converges to salvage_value.", inputSchema: { type: "object", properties: { method: str, cost: num, salvage_value: num, useful_life_years: { type: "integer" }, currency: { type: "string", default: "USD" } }, required: ["method", "cost", "salvage_value", "useful_life_years"] }, handler: (a) => T.depreciation(a) },
  batch_calculate: { description: "Run many calculations in one request. calls: list of {tool, arguments} (max 100). One failure never aborts the batch.", inputSchema: { type: "object", properties: { calls: { type: "array", items: { type: "object" } } }, required: ["calls"] }, handler: (a) => batchCalculate(a) },
  health_check: { description: "Server health/status metadata.", inputSchema: { type: "object", properties: {} }, handler: () => T.health_check() },
};

async function batchCalculate({ calls }) {
  if (!Array.isArray(calls) || calls.length === 0) return T.errEnvelope("calls must be a non-empty list of {tool, arguments}.", "invalid_input");
  if (calls.length > 100) return T.errEnvelope("Batch too large (max 100 calls).", "too_many_calls", "Split into batches of <= 100.");
  const results = [];
  for (let i = 0; i < calls.length; i++) {
    const item = calls[i];
    if (!item || typeof item !== "object" || !item.tool) { results.push({ index: i, status: "error", error: { type: "invalid_item", message: "Each item needs a 'tool' key." } }); continue; }
    const spec = TOOLS[item.tool];
    if (!spec || item.tool === "batch_calculate") { results.push({ index: i, tool: item.tool, status: "error", error: { type: "unknown_tool", message: `'${item.tool}' is not batchable.` } }); continue; }
    try { results.push({ index: i, tool: item.tool, ...(await spec.handler(item.arguments || {})) }); }
    catch (e) { results.push({ index: i, tool: item.tool, status: "error", error: { type: e.type || "internal_error", message: e.message } }); }
  }
  return { status: "success", count: results.length, results };
}

function reply(id, result) { return { jsonrpc: "2.0", id, result }; }
function rpcError(id, code, message) { return { jsonrpc: "2.0", id, error: { code, message } }; }

async function handle(msg) {
  const { id, method, params } = msg;
  if (method === "initialize") return reply(id, { protocolVersion: params?.protocolVersion || PROTOCOL_VERSION, capabilities: { tools: { listChanged: false } }, serverInfo: SERVER_INFO });
  if (method === "ping") return reply(id, {});
  if (method === "notifications/initialized" || (method && method.startsWith("notifications/"))) return null;
  if (method === "tools/list") return reply(id, { tools: Object.entries(TOOLS).map(([name, s]) => ({ name, description: s.description, inputSchema: s.inputSchema })) });
  if (method === "tools/call") {
    const spec = TOOLS[params?.name]; const args = params?.arguments || {};
    if (!spec) return rpcError(id, -32602, `Unknown tool '${params?.name}'.`);
    let result;
    try { result = await spec.handler(args); }
    catch (e) { result = T.errEnvelope(e.message, e.type || "internal_error", e.hint || "Verify input types and values."); }
    return reply(id, { content: [{ type: "text", text: JSON.stringify(result, null, 2) }], structuredContent: result, isError: result?.status === "error" });
  }
  if (id === undefined || id === null) return null;
  return rpcError(id, -32601, `Method not found: ${method}`);
}

const rl = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
rl.on("line", async (line) => { line = line.trim(); if (!line) return; let msg; try { msg = JSON.parse(line); } catch { return; } const res = await handle(msg); if (res) process.stdout.write(JSON.stringify(res) + "\n"); });
process.stderr.write("PrecisionCalc MCP stdio server ready\n");
