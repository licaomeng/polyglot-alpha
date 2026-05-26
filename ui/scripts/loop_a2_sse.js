// A2 sub-agent: focused SSE + extra UI walk session.
// One trigger, then probe SSE streams, /agents/{address}, /events/{id}/bids,
// /events/{id}/translations, /builder_fees, /health endpoints.

/* eslint-disable no-console */
const fs = require("fs");
const path = require("path");
const http = require("http");
const { chromium } = require("playwright");

const BASE_UI = "http://127.0.0.1:3001";
const BASE_API = "http://127.0.0.1:8000";
const OUT_DIR = path.resolve(__dirname, "..", "..", "outputs");
const SHOT_DIR = path.join(OUT_DIR, "loop_screenshots");
const FINDINGS = path.join(OUT_DIR, "playwright_loop_findings.md");
const SESSION_TAG = `a2sse-${Date.now()}`;

function ts() { return new Date().toISOString(); }
function append(line) { fs.appendFileSync(FINDINGS, line + "\n"); }

function postJson(url, body) {
  return new Promise((resolve, reject) => {
    const data = JSON.stringify(body);
    const u = new URL(url);
    const req = http.request({ hostname: u.hostname, port: u.port, path: u.pathname, method: "POST",
      headers: { "Content-Type": "application/json", "Content-Length": Buffer.byteLength(data) }},
      (res) => { let chunks = ""; res.on("data", c => chunks += c);
        res.on("end", () => { try { resolve({ status: res.statusCode, body: JSON.parse(chunks) }); }
          catch (e) { resolve({ status: res.statusCode, body: chunks }); }});
      });
    req.on("error", reject); req.write(data); req.end();
  });
}
function getJson(url) {
  return new Promise((resolve, reject) => {
    http.get(url, (res) => { let chunks = ""; res.on("data", c => chunks += c);
      res.on("end", () => { try { resolve({ status: res.statusCode, body: JSON.parse(chunks) }); }
        catch (e) { resolve({ status: res.statusCode, body: chunks }); }});
    }).on("error", reject);
  });
}

async function waitForTerminal(eventId, timeoutMs = 150000) {
  const t0 = Date.now();
  const terminal = new Set(["COMMITTED", "SUBMITTED", "FAILED", "REJECTED"]);
  while (Date.now() - t0 < timeoutMs) {
    const r = await getJson(`${BASE_API}/events/${eventId}`);
    if (r.body && terminal.has(r.body.status)) return r.body;
    await new Promise(r => setTimeout(r, 2500));
  }
  return null;
}

// SSE probe: collect first ~5 lines or 10s
function sseProbe(pathPart) {
  return new Promise((resolve) => {
    const u = new URL(`${BASE_API}${pathPart}`);
    const req = http.get({ hostname: u.hostname, port: u.port, path: u.pathname, headers: { Accept: "text/event-stream" }},
      (res) => {
        const lines = [];
        const status = res.statusCode;
        res.on("data", (c) => {
          lines.push(c.toString().slice(0, 200));
          if (lines.length >= 8) { req.destroy(); resolve({ status, lines, ended: false }); }
        });
        res.on("end", () => resolve({ status, lines, ended: true }));
        setTimeout(() => { req.destroy(); resolve({ status, lines, ended: false }); }, 10000);
      });
    req.on("error", (e) => resolve({ error: e.message }));
  });
}

(async () => {
  append(`\n---`);
  append(`# A2 SSE+Extras Loop ${ts()}`);

  // 1) Trigger one event with 2 mock bids that we expect to potentially succeed
  const trigger = {
    event_source: "user_payload",
    title: `Will event SSE-X happen by 2026-12-31? [${SESSION_TAG}]`,
    sources: [{ name: "test-sse", url: `https://test/sse?s=${SESSION_TAG}` }],
    language: "en",
    auction_mode: "mock",
    mock_bids: [
      { agent_address: "0xagent_a", bid_amount: 0.5, stake_amount: 5.0, reputation: 0.9 },
      { agent_address: "0xagent_b", bid_amount: 0.7, stake_amount: 5.0, reputation: 0.8 },
    ],
  };
  append(`\n## SSE Cycle (single): ${ts()}`);
  const tr = await postJson(`${BASE_API}/trigger/event`, trigger);
  append(`- Trigger HTTP ${tr.status}: \`${JSON.stringify(tr.body).slice(0,200)}\``);
  const eventId = tr.body && tr.body.event_id;

  // 2) While lifecycle runs, probe SSE in parallel (10s window)
  const ssePromise1 = sseProbe("/sse/events");
  const ssePromise2 = sseProbe("/sse/auctions");

  const [sseEvents, sseAuctions] = await Promise.all([ssePromise1, ssePromise2]);
  append(`- /sse/events: status=${sseEvents.status}, lines=${(sseEvents.lines||[]).length}, ended=${sseEvents.ended}`);
  if (sseEvents.lines && sseEvents.lines[0]) append(`  - first: \`${sseEvents.lines[0].replace(/\n/g,"\\n").slice(0,140)}\``);
  append(`- /sse/auctions: status=${sseAuctions.status}, lines=${(sseAuctions.lines||[]).length}, ended=${sseAuctions.ended}`);
  if (sseAuctions.lines && sseAuctions.lines[0]) append(`  - first: \`${sseAuctions.lines[0].replace(/\n/g,"\\n").slice(0,140)}\``);

  // 3) Wait terminal
  let finalEvent = null;
  if (eventId) {
    finalEvent = await waitForTerminal(eventId);
    append(`- Lifecycle: ${finalEvent ? finalEvent.status : "TIMEOUT"}${finalEvent ? `, winner=${finalEvent.winner_address}` : ""}`);
  }

  // 4) Probe extra REST endpoints
  const endpoints = [
    "/health",
    "/builder_fees",
    `/events/${eventId}`,
    `/events/${eventId}/bids`,
    `/events/${eventId}/phases`,
    `/events/${eventId}/translations`,
    "/agents/0xagent_a",
    "/agents/0xagent_a/history",
    "/leaderboard",
  ];
  append(`\n### REST endpoint probes`);
  for (const ep of endpoints) {
    try {
      const r = await getJson(`${BASE_API}${ep}`);
      const bodyLen = typeof r.body === "object" ? JSON.stringify(r.body).length : r.body.length;
      append(`- \`${ep}\` -> HTTP ${r.status}, body-len=${bodyLen}`);
    } catch (e) {
      append(`- \`${ep}\` -> exception: ${e.message}`);
    }
  }

  // 5) UI walk for that event + /history page
  const browser = await chromium.launch({ headless: true });
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const page = await ctx.newPage();
  const errors = [];
  page.on("pageerror", e => errors.push(`pageerror: ${e.message}`));
  page.on("console", m => { if (m.type() === "error") errors.push(`console.error: ${m.text()}`); });

  let step = 0;
  const shot = async (label) => {
    step += 1;
    try { await page.screenshot({ path: path.join(SHOT_DIR, `sse_step_${step}_${label}.png`) }); }
    catch (e) {}
  };

  append(`\n### UI walk`);
  try {
    if (eventId) {
      await page.goto(`${BASE_UI}/events/${eventId}`, { waitUntil: "domcontentloaded", timeout: 20000 });
      await page.waitForTimeout(2500);
      await shot("event_detail");
      const sub = await page.$('[data-testid="sub-phase-chips"]');
      const debate = await page.$('[data-testid="agent-debate-panel"], [data-testid="agent-debate-panel-empty"]');
      append(`- /events/${eventId}: sub-phase-chips=${!!sub}, debate-panel=${!!debate}`);
    }
    // Visit /history
    await page.goto(`${BASE_UI}/history`, { waitUntil: "domcontentloaded", timeout: 15000 });
    await page.waitForTimeout(1500);
    await shot("history");
    const histText = await page.textContent("body");
    append(`- /history: loaded, body-text-len=${(histText || "").length}`);
    // Visit /leaderboard
    await page.goto(`${BASE_UI}/leaderboard`, { waitUntil: "domcontentloaded", timeout: 15000 });
    await page.waitForTimeout(1500);
    await shot("leaderboard");
    const lbText = await page.textContent("body");
    append(`- /leaderboard: loaded, body-text-len=${(lbText || "").length}`);
  } catch (e) {
    append(`- UI walk error: ${e.message}`);
  }

  append(`- JS errors observed: ${errors.length}`);
  for (const er of errors.slice(0, 5)) append(`  - ${er}`);

  await ctx.close();
  await browser.close();
  append(`- SSE+Extras cycle finished at ${ts()}`);
  console.log("DONE");
})();
