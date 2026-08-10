// PrecisionCalc MCP — Cloudflare Pages Function (_worker.js advanced mode).
// Serves a live remote MCP server over Streamable HTTP at /mcp with tiered
// metering + Stripe billing, a /checkout + /success + /webhook flow, a usage
// endpoint at /metrics, and the static landing site for all other paths.
import * as T from "./tools.mjs";
import { CalcError, errEnvelope } from "./tools.mjs";
import * as B from "./billing.mjs";

const PROTOCOL_VERSION = "2024-11-05";
const SERVER_INFO = { name: "PrecisionCalc", version: "2.1.0-edge" };

const num = { type: "number" };
const str = { type: "string" };
const TOOLS = {
  calculate_metric: { description: "Compute a business/SaaS/finance metric with exact decimal precision. Metrics: ltv, cac, ltv_cac_ratio, payback_period_months, contribution_margin, gross_margin, churn_rate, mrr_growth_rate, arr, break_even_units, nrr, grr, rule_of_40, magic_number. Rates/margins are decimals (0.05=5%). Call list_metrics for schemas.", inputSchema: { type: "object", properties: { metric: str, params: { type: "object" }, currency: { type: "string", default: "USD" } }, required: ["metric", "params"] }, handler: (a) => T.calculate_metric(a) },
  list_metrics: { description: "List every supported metric with descriptions and required/optional params.", inputSchema: { type: "object", properties: {} }, handler: () => T.list_metrics() },
  currency_convert: { description: "Convert an amount between major currencies (USD, EUR, GBP, JPY, CAD, AUD, CHF, CNY, INR) with Decimal precision. Static offline table on the free tier; live/historical ECB rates (date/live=true) require a paid plan.", inputSchema: { type: "object", properties: { amount: num, from_currency: str, to_currency: str, date: { type: "string" }, live: { type: "boolean" } }, required: ["amount", "from_currency", "to_currency"] }, handler: (a) => T.currency_convert(a) },
  business_days: { description: "Business-day arithmetic honoring weekends + regional holidays. operation: add_business_days (needs days, negative ok) | count_business_days (needs end_date, inclusive) | next_business_day | previous_business_day. region: US | UK | EU | NONE. custom_holidays: list of YYYY-MM-DD.", inputSchema: { type: "object", properties: { operation: str, start_date: str, days: { type: "integer" }, end_date: str, region: { type: "string", default: "US" }, custom_holidays: { type: "array", items: str } }, required: ["operation", "start_date"] }, handler: (a) => T.business_days(a) },
  compound_growth: { description: "Compound-interest/growth math. operation: future_value (rate, years, present_value) | present_value (rate, years, future_value) | cagr (begin_value, end_value, years). rate is annual decimal (0.08=8%). compounding: daily|weekly|monthly|quarterly|semiannually|annually|continuous.", inputSchema: { type: "object", properties: { operation: str, rate: num, years: num, present_value: num, future_value: num, begin_value: num, end_value: num, compounding: { type: "string", default: "annually" }, currency: { type: "string", default: "USD" } }, required: ["operation"] }, handler: (a) => T.compound_growth(a) },
  net_present_value: { description: "Net Present Value (discounted cash flow). NPV = sum(CF_t/(1+rate)^t). cashflows[0] = period 0 (usually the negative outlay).", inputSchema: { type: "object", properties: { rate: num, cashflows: { type: "array", items: num }, currency: { type: "string", default: "USD" } }, required: ["rate", "cashflows"] }, handler: (a) => T.net_present_value(a) },
  internal_rate_of_return: { description: "Internal Rate of Return: per-period rate where NPV=0 (Newton + bisection). Requires a sign change in cashflows.", inputSchema: { type: "object", properties: { cashflows: { type: "array", items: num }, guess: { type: "number", default: 0.1 } }, required: ["cashflows"] }, handler: (a) => T.internal_rate_of_return(a) },
  loan_amortization: { description: "Level-payment loan: monthly payment, total interest, payoff, and (optional) full schedule. payment = P*r/(1-(1+r)^-n), r=annual_rate/12.", inputSchema: { type: "object", properties: { principal: num, annual_rate: num, term_months: { type: "integer" }, extra_payment: { type: "number", default: 0 }, currency: { type: "string", default: "USD" }, include_schedule: { type: "boolean", default: false } }, required: ["principal", "annual_rate", "term_months"] }, handler: (a) => T.loan_amortization(a) },
  depreciation: { description: "Asset depreciation schedule. method: straight_line | declining_balance | sum_of_years_digits. Book value converges to salvage_value.", inputSchema: { type: "object", properties: { method: str, cost: num, salvage_value: num, useful_life_years: { type: "integer" }, currency: { type: "string", default: "USD" } }, required: ["method", "cost", "salvage_value", "useful_life_years"] }, handler: (a) => T.depreciation(a) },
  batch_calculate: { description: "PAID FEATURE. Run many calculations in one request. calls: list of {tool, arguments} (max 100). One failure never aborts the batch.", inputSchema: { type: "object", properties: { calls: { type: "array", items: { type: "object" } } }, required: ["calls"] }, handler: (a) => batchCalculate(a) },
  health_check: { description: "Server health/status metadata.", inputSchema: { type: "object", properties: {} }, handler: () => T.health_check() },
};
const PAID_ONLY_TOOLS = new Set(["batch_calculate"]);

async function batchCalculate({ calls }) {
  if (!Array.isArray(calls) || calls.length === 0) return errEnvelope("calls must be a non-empty list of {tool, arguments}.", "invalid_input");
  if (calls.length > 100) return errEnvelope("Batch too large (max 100 calls).", "too_many_calls", "Split into batches of <= 100.");
  const results = [];
  for (let i = 0; i < calls.length; i++) {
    const item = calls[i];
    if (!item || typeof item !== "object" || !item.tool) { results.push({ index: i, status: "error", error: { type: "invalid_item", message: "Each item needs a 'tool' key." } }); continue; }
    const spec = TOOLS[item.tool];
    if (!spec || item.tool === "batch_calculate") { results.push({ index: i, tool: item.tool, status: "error", error: { type: "unknown_tool", message: `'${item.tool}' is not batchable.` } }); continue; }
    results.push({ index: i, tool: item.tool, result: await runTool(item.tool, item.arguments || {}) });
  }
  return { status: "success", count: results.length, results };
}
async function runTool(name, args) {
  const spec = TOOLS[name];
  if (!spec) return errEnvelope(`Unknown tool '${name}'.`, "unknown_tool");
  try { return await spec.handler(args || {}); }
  catch (e) { if (e instanceof CalcError) return errEnvelope(e.message, e.type, e.hint); return errEnvelope(`Unexpected error: ${e.message}`, "internal_error", "Verify input types and values."); }
}

const meter = { total: 0, rejected: 0, byTool: {}, started: 0 };

// ---- JSON-RPC with metering/gating context ----------------------------------
async function handleRpc(msg, ctx, env) {
  const { id, method, params } = msg;
  if (method === "initialize") return reply(id, { protocolVersion: params?.protocolVersion || PROTOCOL_VERSION, capabilities: { tools: { listChanged: false } }, serverInfo: SERVER_INFO });
  if (method === "ping") return reply(id, {});
  if (method === "tools/list") return reply(id, { tools: Object.entries(TOOLS).map(([name, s]) => ({ name, description: s.description, inputSchema: s.inputSchema })) });
  if (method === "tools/call") {
    const name = params?.name; const args = params?.arguments || {};
    if (!TOOLS[name]) return rpcError(id, -32602, `Unknown tool '${name}'.`);

    // Auth-state gating (revoked / invalid keys).
    if (ctx.plan === "revoked" || ctx.plan === "invalid_key") {
      meter.rejected++;
      return toolResult(id, B.upsell(env, ctx.plan, { tool: name }));
    }
    // Paid-only tool gating on the free tier.
    if (ctx.plan === "free" && PAID_ONLY_TOOLS.has(name)) {
      meter.rejected++;
      return toolResult(id, B.upsell(env, "upgrade_required", { tool: name }));
    }
    // Free tier: force static FX (strip live/historical), then note it.
    let downgradedFx = false;
    if (ctx.plan === "free" && name === "currency_convert" && (args.live === true || args.date)) {
      delete args.live; delete args.date; downgradedFx = true;
    }
    // Quota.
    const q = await B.consumeQuota(env, ctx.identity, ctx.limit);
    if (!q.allowed) {
      meter.rejected++;
      return toolResult(id, B.upsell(env, "quota_exceeded", { tool: name, usage: { plan: ctx.plan, used: q.used, limit: q.limit, remaining: 0, resets: "daily 00:00 UTC" } }));
    }
    meter.total++; meter.byTool[name] = (meter.byTool[name] || 0) + 1;
    const result = await runTool(name, args);
    if (downgradedFx && result && Array.isArray(result.notes)) {
      result.notes.unshift("Live/historical FX is a paid feature; static mid-market rates were used. Upgrade at " + (env.PRECISIONCALC_BASE_URL || "") + "/#pricing");
    }
    if (result && typeof result === "object") result.quota = { plan: ctx.plan, used: q.used, limit: q.limit, remaining: q.remaining };
    return toolResult(id, result);
  }
  if (typeof id === "undefined" || id === null) return null;
  return rpcError(id, -32601, `Method not found: ${method}`);
}
function reply(id, result) { return { jsonrpc: "2.0", id, result }; }
function rpcError(id, code, message) { return { jsonrpc: "2.0", id, error: { code, message } }; }
function toolResult(id, obj) { return reply(id, { content: [{ type: "text", text: JSON.stringify(obj, null, 2) }], structuredContent: obj, isError: obj?.status === "error" }); }

// ---- HTTP ------------------------------------------------------------------
const CORS = { "Access-Control-Allow-Origin": "*", "Access-Control-Allow-Methods": "GET, POST, OPTIONS", "Access-Control-Allow-Headers": "content-type, authorization, x-api-key, mcp-session-id, mcp-protocol-version, accept", "Access-Control-Expose-Headers": "mcp-session-id" };
function sse(obj, extra = {}) { return new Response(`event: message\ndata: ${JSON.stringify(obj)}\n\n`, { status: 200, headers: { "Content-Type": "text/event-stream", "Cache-Control": "no-cache", "mcp-session-id": "precisioncalc-stateless", ...CORS, ...extra } }); }
function json(obj, status = 200) { return new Response(JSON.stringify(obj), { status, headers: { "Content-Type": "application/json", ...CORS } }); }
function redirect(url) { return new Response(null, { status: 302, headers: { Location: url, ...CORS } }); }
function html(body, status = 200) { return new Response(body, { status, headers: { "Content-Type": "text/html; charset=utf-8", ...CORS } }); }

function successPage(key, plan, reused) {
  return `<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>PrecisionCalc — your API key</title><style>
body{margin:0;background:#0b0f1a;color:#e7ecf5;font-family:ui-sans-serif,system-ui,Segoe UI,Roboto,Arial;line-height:1.6}
.wrap{max-width:680px;margin:0 auto;padding:56px 20px}a{color:#63a4ff}
.k{font-family:ui-monospace,Menlo,Consolas,monospace;background:#0e1424;border:1px solid #20293f;border-radius:12px;padding:16px;font-size:16px;word-break:break-all;color:#5ce1a6}
.btn{cursor:pointer;background:#5ce1a6;color:#06231a;font-weight:700;border:0;border-radius:9px;padding:9px 14px;margin-top:12px}
pre{background:#0e1424;border:1px solid #20293f;border-radius:12px;padding:14px;overflow:auto;font-size:13px;color:#d7e2ff}
.badge{display:inline-block;color:#5ce1a6;border:1px solid #20293f;border-radius:999px;padding:4px 12px;font-size:12px;text-transform:uppercase;letter-spacing:.1em}
</style></head><body><div class="wrap">
<span class="badge">Payment successful · ${plan} plan</span>
<h1>🎉 Your PrecisionCalc API key</h1>
<p>Save this now — it's shown once. Send it as the <code>X-API-Key</code> header (or <code>Authorization: Bearer</code>).</p>
<div class="k" id="key">${key}</div>
<button class="btn" onclick="navigator.clipboard.writeText(document.getElementById('key').innerText);this.textContent='Copied ✓'">Copy key</button>
${reused ? '<p style="color:#ffcf6b">(This session was already provisioned; same key returned.)</p>' : ""}
<h3>Use it</h3>
<pre>{
  "mcpServers": {
    "precisioncalc": {
      "url": "https://precisioncalc-mcp.pages.dev/mcp",
      "headers": { "X-API-Key": "${key}" }
    }
  }
}</pre>
<p><a href="/#pricing">← Back to PrecisionCalc</a></p>
</div></body></html>`;
}

export default {
  async fetch(request, env) {
    if (!meter.started) meter.started = Date.now();
    const url = new URL(request.url);
    const path = url.pathname;
    if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: CORS });

    // ---- Billing routes ----
    if (path === "/checkout") {
      const plan = (url.searchParams.get("plan") || "starter").toLowerCase();
      if (plan !== "starter" && plan !== "pro") return html("<p>Unknown plan. <a href='/#pricing'>See pricing</a>.</p>", 400);
      try { return redirect(await B.createCheckout(env, plan)); }
      catch (e) { return html(`<p>Checkout error: ${e.message}. <a href="/#pricing">Back</a></p>`, 500); }
    }
    if (path === "/success") {
      const sid = url.searchParams.get("session_id");
      if (!sid) return html("<p>Missing session id. <a href='/#pricing'>Back</a></p>", 400);
      try { const p = await B.provisionFromSession(env, sid); return html(successPage(p.key, p.plan, p.reused)); }
      catch (e) { return html(`<p>Could not verify payment yet: ${e.message}. If you just paid, refresh in a moment. <a href="/#pricing">Back</a></p>`, 402); }
    }
    if (path === "/portal") {
      const key = url.searchParams.get("key") || (request.headers.get("x-api-key"));
      try { return redirect(await B.createPortal(env, key)); }
      catch (e) { return html(`<p>${e.message} <a href="/#pricing">Back</a></p>`, 400); }
    }
    if (path === "/webhook") {
      if (request.method !== "POST") return new Response("Method Not Allowed", { status: 405 });
      const payload = await request.text();
      const ok = await B.verifyStripeSignature(env, payload, request.headers.get("stripe-signature"));
      if (!ok) return json({ error: "invalid signature" }, 400);
      try { await B.handleWebhookEvent(env, JSON.parse(payload)); } catch (_) {}
      return json({ received: true });
    }

    if (path === "/metrics") return json({ status: "success", usage: { uptime_seconds: Math.round((Date.now() - meter.started) / 1000), total_calls: meter.total, rejected: meter.rejected, by_tool: meter.byTool } });

    // ---- MCP ----
    if (path === "/mcp" || path === "/mcp/") {
      if (request.method === "GET") return new Response("Method Not Allowed (no server-initiated stream)", { status: 405, headers: CORS });
      if (request.method !== "POST") return new Response("Method Not Allowed", { status: 405, headers: CORS });
      let payload;
      try { payload = await request.json(); } catch { return json({ jsonrpc: "2.0", id: null, error: { code: -32700, message: "Parse error" } }, 400); }

      const who = await B.identify(request, env);
      const limits = B.planLimits(env);
      who.limit = who.plan === "pro" ? limits.pro : who.plan === "starter" ? limits.starter : limits.free;

      if (Array.isArray(payload)) {
        const out = [];
        for (const m of payload) { const r = await handleRpc(m, who, env); if (r) out.push(r); }
        return out.length ? sse(out) : new Response(null, { status: 202, headers: CORS });
      }
      const resp = await handleRpc(payload, who, env);
      if (resp === null) return new Response(null, { status: 202, headers: CORS });
      return sse(resp);
    }

    return env.ASSETS.fetch(request);
  },
};
