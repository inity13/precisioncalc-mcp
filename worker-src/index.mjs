// PrecisionCalc MCP — Cloudflare Pages Function (_worker.js advanced mode).
// Serves a live, remote MCP server over Streamable HTTP at /mcp, a usage
// endpoint at /metrics, and the static landing site for all other paths.
import * as T from "./tools.mjs";
import { CalcError, errEnvelope } from "./tools.mjs";

const PROTOCOL_VERSION = "2024-11-05";
const SERVER_INFO = { name: "PrecisionCalc", version: "2.0.0-edge" };

// ---- tool registry (name -> {description, inputSchema, handler}) ------------
const num = { type: "number" };
const str = { type: "string" };
const TOOLS = {
  calculate_metric: {
    description:
      "Compute a business/SaaS/finance metric with exact decimal precision. Metrics: ltv, cac, ltv_cac_ratio, payback_period_months, contribution_margin, gross_margin, churn_rate, mrr_growth_rate, arr, break_even_units, nrr, grr, rule_of_40, magic_number. Rates/margins are decimals (0.05=5%). Call list_metrics for schemas.",
    inputSchema: { type: "object", properties: { metric: str, params: { type: "object" }, currency: { type: "string", default: "USD" } }, required: ["metric", "params"] },
    handler: (a) => T.calculate_metric(a),
  },
  list_metrics: { description: "List every supported metric with descriptions and required/optional params.", inputSchema: { type: "object", properties: {} }, handler: () => T.list_metrics() },
  currency_convert: {
    description: "Convert an amount between major currencies (USD, EUR, GBP, JPY, CAD, AUD, CHF, CNY, INR) with Decimal precision. Static offline table by default; a date (YYYY-MM-DD) or live=true uses live/historical ECB rates with static fallback.",
    inputSchema: { type: "object", properties: { amount: num, from_currency: str, to_currency: str, date: { type: "string" }, live: { type: "boolean" } }, required: ["amount", "from_currency", "to_currency"] },
    handler: (a) => T.currency_convert(a),
  },
  business_days: {
    description: "Business-day arithmetic honoring weekends + regional holidays. operation: add_business_days (needs days, negative ok) | count_business_days (needs end_date, inclusive) | next_business_day | previous_business_day. region: US | UK | EU | NONE. custom_holidays: list of YYYY-MM-DD.",
    inputSchema: { type: "object", properties: { operation: str, start_date: str, days: { type: "integer" }, end_date: str, region: { type: "string", default: "US" }, custom_holidays: { type: "array", items: str } }, required: ["operation", "start_date"] },
    handler: (a) => T.business_days(a),
  },
  compound_growth: {
    description: "Compound-interest/growth math. operation: future_value (rate, years, present_value) | present_value (rate, years, future_value) | cagr (begin_value, end_value, years). rate is annual decimal (0.08=8%). compounding: daily|weekly|monthly|quarterly|semiannually|annually|continuous.",
    inputSchema: { type: "object", properties: { operation: str, rate: num, years: num, present_value: num, future_value: num, begin_value: num, end_value: num, compounding: { type: "string", default: "annually" }, currency: { type: "string", default: "USD" } }, required: ["operation"] },
    handler: (a) => T.compound_growth(a),
  },
  net_present_value: {
    description: "Net Present Value (discounted cash flow). NPV = sum(CF_t/(1+rate)^t). cashflows[0] = period 0 (usually the negative outlay).",
    inputSchema: { type: "object", properties: { rate: num, cashflows: { type: "array", items: num }, currency: { type: "string", default: "USD" } }, required: ["rate", "cashflows"] },
    handler: (a) => T.net_present_value(a),
  },
  internal_rate_of_return: {
    description: "Internal Rate of Return: per-period rate where NPV=0 (Newton + bisection). Requires a sign change in cashflows.",
    inputSchema: { type: "object", properties: { cashflows: { type: "array", items: num }, guess: { type: "number", default: 0.1 } }, required: ["cashflows"] },
    handler: (a) => T.internal_rate_of_return(a),
  },
  loan_amortization: {
    description: "Level-payment loan: monthly payment, total interest, payoff, and (optional) full schedule. payment = P*r/(1-(1+r)^-n), r=annual_rate/12.",
    inputSchema: { type: "object", properties: { principal: num, annual_rate: num, term_months: { type: "integer" }, extra_payment: { type: "number", default: 0 }, currency: { type: "string", default: "USD" }, include_schedule: { type: "boolean", default: false } }, required: ["principal", "annual_rate", "term_months"] },
    handler: (a) => T.loan_amortization(a),
  },
  depreciation: {
    description: "Asset depreciation schedule. method: straight_line | declining_balance | sum_of_years_digits. Book value converges to salvage_value.",
    inputSchema: { type: "object", properties: { method: str, cost: num, salvage_value: num, useful_life_years: { type: "integer" }, currency: { type: "string", default: "USD" } }, required: ["method", "cost", "salvage_value", "useful_life_years"] },
    handler: (a) => T.depreciation(a),
  },
  batch_calculate: {
    description: "Run many calculations in one request. calls: list of {tool, arguments} (max 100). One failure never aborts the batch.",
    inputSchema: { type: "object", properties: { calls: { type: "array", items: { type: "object" } } }, required: ["calls"] },
    handler: (a) => batchCalculate(a),
  },
  health_check: { description: "Server health/status metadata.", inputSchema: { type: "object", properties: {} }, handler: () => T.health_check() },
};

async function batchCalculate({ calls }) {
  if (!Array.isArray(calls) || calls.length === 0) return errEnvelope("calls must be a non-empty list of {tool, arguments}.", "invalid_input", 'Example: {"tool":"calculate_metric","arguments":{"metric":"arr","params":{"mrr":1000}}}');
  if (calls.length > 100) return errEnvelope("Batch too large (max 100 calls).", "too_many_calls", "Split into batches of <= 100.");
  const results = [];
  for (let i = 0; i < calls.length; i++) {
    const item = calls[i];
    if (!item || typeof item !== "object" || !item.tool) { results.push({ index: i, status: "error", error: { type: "invalid_item", message: "Each item needs a 'tool' key." } }); continue; }
    const spec = TOOLS[item.tool];
    if (!spec || item.tool === "batch_calculate") { results.push({ index: i, tool: item.tool, status: "error", error: { type: "unknown_tool", message: `'${item.tool}' is not batchable.` } }); continue; }
    const out = await runTool(item.tool, item.arguments || {});
    results.push({ index: i, tool: item.tool, result: out });
  }
  return { status: "success", count: results.length, results };
}

async function runTool(name, args) {
  const spec = TOOLS[name];
  if (!spec) return errEnvelope(`Unknown tool '${name}'.`, "unknown_tool");
  try {
    return await spec.handler(args || {});
  } catch (e) {
    if (e instanceof CalcError) return errEnvelope(e.message, e.type, e.hint);
    return errEnvelope(`Unexpected error: ${e.message}`, "internal_error", "Verify input types and values.");
  }
}

// ---- usage metering (per-isolate, ephemeral) --------------------------------
// NOTE: Cloudflare returns Date.now()==0 during top-level module evaluation
// (clock only advances inside a request), so `started` is initialized lazily.
const meter = { total: 0, rejected: 0, byTool: {}, started: 0 };

// ---- JSON-RPC handling ------------------------------------------------------
async function handleRpc(msg) {
  const { id, method, params } = msg;
  if (method === "initialize")
    return reply(id, { protocolVersion: params?.protocolVersion || PROTOCOL_VERSION, capabilities: { tools: { listChanged: false } }, serverInfo: SERVER_INFO });
  if (method === "ping") return reply(id, {});
  if (method === "tools/list")
    return reply(id, { tools: Object.entries(TOOLS).map(([name, s]) => ({ name, description: s.description, inputSchema: s.inputSchema })) });
  if (method === "tools/call") {
    const name = params?.name; const args = params?.arguments || {};
    if (!TOOLS[name]) return rpcError(id, -32602, `Unknown tool '${name}'.`);
    meter.total++; meter.byTool[name] = (meter.byTool[name] || 0) + 1;
    const result = await runTool(name, args);
    return reply(id, { content: [{ type: "text", text: JSON.stringify(result, null, 2) }], structuredContent: result, isError: result?.status === "error" });
  }
  if (typeof id === "undefined" || id === null) return null; // notification -> no response
  return rpcError(id, -32601, `Method not found: ${method}`);
}
function reply(id, result) { return { jsonrpc: "2.0", id, result }; }
function rpcError(id, code, message) { return { jsonrpc: "2.0", id, error: { code, message } }; }

// ---- HTTP layer -------------------------------------------------------------
const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
  "Access-Control-Allow-Headers": "content-type, authorization, x-api-key, mcp-session-id, mcp-protocol-version, accept",
  "Access-Control-Expose-Headers": "mcp-session-id",
};
function sse(obj, extraHeaders = {}) {
  const body = `event: message\ndata: ${JSON.stringify(obj)}\n\n`;
  return new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream", "Cache-Control": "no-cache", "mcp-session-id": "precisioncalc-stateless", ...CORS, ...extraHeaders } });
}
function json(obj, status = 200) { return new Response(JSON.stringify(obj), { status, headers: { "Content-Type": "application/json", ...CORS } }); }

function authorized(request, env) {
  const keys = (env.PRECISIONCALC_API_KEYS || "").split(",").map((k) => k.trim()).filter(Boolean);
  if (keys.length === 0) return true;
  let key = request.headers.get("x-api-key");
  if (!key) { const a = request.headers.get("authorization") || ""; if (a.toLowerCase().startsWith("bearer ")) key = a.slice(7).trim(); }
  return key && keys.includes(key);
}

export default {
  async fetch(request, env) {
    if (!meter.started) meter.started = Date.now();
    const url = new URL(request.url);
    const path = url.pathname;

    if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: CORS });

    if (path === "/metrics") {
      if (!authorized(request, env)) return json({ status: "error", error: { type: "unauthorized", message: "Valid API key required." } }, 401);
      return json({ status: "success", usage: { uptime_seconds: Math.round((Date.now() - meter.started) / 1000), total_requests: meter.total, rejected: meter.rejected, by_tool: meter.byTool } });
    }

    if (path === "/mcp" || path === "/mcp/") {
      if (request.method === "GET") return new Response("Method Not Allowed (no server-initiated stream)", { status: 405, headers: CORS });
      if (request.method !== "POST") return new Response("Method Not Allowed", { status: 405, headers: CORS });
      if (!authorized(request, env)) { meter.rejected++; return json({ jsonrpc: "2.0", id: null, error: { code: -32001, message: "Unauthorized: missing or invalid API key." } }, 401); }
      let payload;
      try { payload = await request.json(); }
      catch { return json({ jsonrpc: "2.0", id: null, error: { code: -32700, message: "Parse error" } }, 400); }

      // Batch of JSON-RPC messages
      if (Array.isArray(payload)) {
        const out = [];
        for (const m of payload) { const r = await handleRpc(m); if (r) out.push(r); }
        return out.length ? sse(out) : new Response(null, { status: 202, headers: CORS });
      }
      const resp = await handleRpc(payload);
      if (resp === null) return new Response(null, { status: 202, headers: CORS }); // notification
      return sse(resp);
    }

    // Everything else -> static landing site.
    return env.ASSETS.fetch(request);
  },
};
