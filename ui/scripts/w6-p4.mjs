// Wave6 P4: concurrent stress + console hygiene + SSE behavior
// Run: node scripts/w6-p4.mjs (cwd = ui/)
import { chromium } from "playwright";
import fs from "node:fs";

const BASE_UI = process.env.W6_UI_BASE || "http://127.0.0.1:3001";
const BASE_API = process.env.W6_API_BASE || "http://127.0.0.1:8000";
const SS_DIR = "/Users/messili/codebase/polyglot-alpha/screenshots/wave6-p4";
const OUT_MD = "/tmp/wave6-p4-findings.md";
const BACKEND_LOG = "/tmp/polyglot-backend.log";

fs.mkdirSync(SS_DIR, { recursive: true });

const findings = [];
const consoleErrors = [];
const consoleWarns = [];
const networkErrors = [];
const pageErrors = [];

function logFinding(sev, title, where, expected, actual, screenshot, hypothesis) {
  findings.push({ sev, title, where, expected, actual, screenshot, hypothesis });
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function attachListeners(page, label) {
  page.on("console", (msg) => {
    const t = msg.type();
    const txt = msg.text();
    if (t === "error") consoleErrors.push({ label, txt });
    else if (t === "warning") consoleWarns.push({ label, txt });
  });
  page.on("pageerror", (err) => pageErrors.push({ label, txt: String(err && err.message || err) }));
  page.on("requestfailed", (req) => {
    const url = req.url();
    if (!/^https?:/.test(url)) return;
    const fail = req.failure()?.errorText || "unknown";
    networkErrors.push({ label, url, status: "FAILED", error: fail });
  });
  page.on("response", (resp) => {
    const url = resp.url();
    const st = resp.status();
    if (st >= 400) {
      if (st === 404 && /\/events\/9999999/.test(url)) return;
      networkErrors.push({ label, url, status: st });
    }
  });
}

async function nfetch(url, opts) {
  try {
    const r = await fetch(url, opts);
    const txt = await r.text();
    let body = txt;
    try { body = JSON.parse(txt); } catch {}
    return { ok: r.ok, status: r.status, body };
  } catch (e) {
    return { ok: false, status: 0, error: String(e && e.message || e) };
  }
}

async function backendOffset() {
  try { return fs.statSync(BACKEND_LOG).size; } catch { return 0; }
}
async function readBackendLog(sinceByte) {
  try {
    const buf = fs.readFileSync(BACKEND_LOG);
    return buf.slice(sinceByte).toString("utf8");
  } catch { return ""; }
}

function eventLifecycleMs(ev) {
  if (!ev || !ev.phases || !ev.phases.length) return null;
  const t0 = ev.phases[0].completedAt || ev.triggered_at;
  const completed = ev.phases.filter(p => p.completedAt).map(p => Date.parse(p.completedAt));
  if (!completed.length || !t0) return null;
  return Math.max(...completed) - Date.parse(t0);
}

const TERMINAL = new Set(["SUBMITTED", "FAILED", "REJECTED"]);

const backendLogStart = await backendOffset();
const browser = await chromium.launch({ headless: true });

// ===================================================================
// SCENARIO A: Mock burst of 10 concurrent triggers (Node-level fetch)
// ===================================================================
console.log("[A] start");
const ctxA = await browser.newContext({ viewport: { width: 1280, height: 800 } });
const pageA = await ctxA.newPage();
attachListeners(pageA, "scenarioA");

try {
  await pageA.goto(`${BASE_UI}/?mode=mock`, { waitUntil: "domcontentloaded", timeout: 30_000 });
  await pageA.waitForTimeout(800);
} catch (e) {
  logFinding("HIGH", "Scenario A: home page navigation failed", `${BASE_UI}/?mode=mock`, "200 OK", String(e.message || e), "", "UI server may be slow or unreachable");
}

const burstStart = Date.now();
const triggerPromisesA = [];
for (let i = 0; i < 10; i++) {
  triggerPromisesA.push(nfetch(`${BASE_API}/trigger/event`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mode: "mock" }),
  }));
}
const triggerResultsA = await Promise.all(triggerPromisesA);

const triggeredIdsA = triggerResultsA
  .filter(r => r.ok && r.body && r.body.event_id != null)
  .map(r => String(r.body.event_id));
console.log(`[A] triggered ${triggeredIdsA.length}/10 ids: ${triggeredIdsA.join(",")}`);

const triggerFailuresA = triggerResultsA.filter(r => !r.ok);
const rateLimitedA = triggerResultsA.filter(r => r.status === 429 || (r.body && typeof r.body === "object" && /rate limit/i.test(r.body.error || ""))).length;
if (rateLimitedA > 0) {
  logFinding("HIGH", `Scenario A: ${rateLimitedA}/10 triggers blocked by 10/minute rate limit`, "POST /trigger/event @limiter.limit('10/minute')", "all 10 accepted", `${rateLimitedA} returned 429`, "", "Trigger rate limit exactly equals the requested burst — boundary race causes the 10th request to be rejected when slowapi counts the in-flight call as already consumed");
} else if (triggerFailuresA.length > 0) {
  logFinding("HIGH", `Scenario A: ${triggerFailuresA.length}/10 trigger requests failed`, "POST /trigger/event", "10x 200 OK", JSON.stringify(triggerFailuresA.slice(0, 3)), "", "Backend rejecting concurrent triggers");
}

const deadlineA = Date.now() + 60_000;
let lastFinalizedA = 0;
let finalEventsA = [];
while (Date.now() < deadlineA) {
  const res = await nfetch(`${BASE_API}/events?limit=30`);
  if (res.ok && Array.isArray(res.body)) {
    const matching = res.body.filter(e => triggeredIdsA.includes(String(e.id)));
    const finalized = matching.filter(e => TERMINAL.has(e.status));
    lastFinalizedA = finalized.length;
    if (finalized.length === triggeredIdsA.length && triggeredIdsA.length > 0) {
      finalEventsA = matching;
      break;
    }
  }
  await sleep(1500);
}
const burstElapsedMs = Date.now() - burstStart;

const detailsA = await Promise.all(triggeredIdsA.map(id => nfetch(`${BASE_API}/events/${id}`)));
const lifecycleMsList = [];
for (const d of detailsA) {
  if (d.ok && d.body) {
    const ms = eventLifecycleMs(d.body);
    if (ms != null && ms >= 0) lifecycleMsList.push(ms);
  }
}
const avgLifecycleMs = lifecycleMsList.length ? Math.round(lifecycleMsList.reduce((a, b) => a + b, 0) / lifecycleMsList.length) : null;

const finalizedCountA = finalEventsA.length || lastFinalizedA;
if (finalizedCountA < triggeredIdsA.length) {
  logFinding("HIGH", `Scenario A: only ${finalizedCountA}/${triggeredIdsA.length} events reached terminal state in 60s`, "/events?limit=30 (mock burst)", "all 10 SUBMITTED/FAILED/REJECTED", `${finalizedCountA} finalized`, "", "LIFECYCLE_MAX_CONCURRENCY too low or PENDING stuck");
}

try { await pageA.screenshot({ path: `${SS_DIR}/A_burst_after.png`, fullPage: true }); } catch {}
await ctxA.close();

// take pre-B leaderboard snapshot
const lbBefore = (await nfetch(`${BASE_API}/leaderboard`)).body;

// Trigger endpoint is @limiter.limit("10/minute"). A burned 10 budget. Wait ~65s for window to slide.
console.log("[A->B] waiting 65s for trigger rate limit window to reset");
await sleep(65_000);

// ===================================================================
// SCENARIO B: Mixed mode burst (5 live + 5 mock alternating)
// ===================================================================
console.log("[B] start");
const ctxB = await browser.newContext({ viewport: { width: 1280, height: 800 } });
const pageB = await ctxB.newPage();
attachListeners(pageB, "scenarioB");

try {
  await pageB.goto(`${BASE_UI}/`, { waitUntil: "domcontentloaded", timeout: 30_000 });
  await pageB.waitForTimeout(500);
} catch (e) {
  logFinding("MEDIUM", "Scenario B: home page navigation failed", `${BASE_UI}/`, "200", String(e.message || e), "", "");
}

const triggerPromisesB = [];
for (let i = 0; i < 10; i++) {
  const mode = (i % 2 === 0) ? "live" : "mock";
  // For live, default event_source=user_payload requires a title; use hardcoded sample to mimic demo button.
  const body = mode === "live"
    ? { mode: "live", event_source: "hardcoded" }
    : { mode: "mock" };
  triggerPromisesB.push(
    nfetch(`${BASE_API}/trigger/event`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(r => ({ ...r, mode }))
  );
}
const triggerResultsB = await Promise.all(triggerPromisesB);
const rateLimitedB = triggerResultsB.filter(r => r.status === 429 || (r.body && typeof r.body === "object" && /rate limit/i.test(r.body.error || ""))).length;
if (rateLimitedB > 0) {
  logFinding("MEDIUM", `Scenario B: ${rateLimitedB}/10 triggers hit 10/minute rate limit`, "POST /trigger/event @limiter.limit('10/minute')", "all 10 accepted", `${rateLimitedB} returned 429 / 'Rate limit exceeded'`, "", "Trigger rate limit too restrictive for concurrent stress");
}

const triggeredMockIdsB = triggerResultsB.filter(r => r.mode === "mock" && r.ok && r.body && r.body.event_id != null).map(r => String(r.body.event_id));
const triggeredLiveIdsB = triggerResultsB.filter(r => r.mode === "live" && r.ok && r.body && r.body.event_id != null).map(r => String(r.body.event_id));
console.log(`[B] triggered mock=${triggeredMockIdsB.length}/5 live=${triggeredLiveIdsB.length}/5`);

const allIdsB = [...triggeredMockIdsB, ...triggeredLiveIdsB];
const deadlineB = Date.now() + 180_000; // live events take longer
let finalizedMockB = 0, finalizedLiveB = 0, succMockB = 0, succLiveB = 0;
while (Date.now() < deadlineB) {
  const res = await nfetch(`${BASE_API}/events?limit=60`);
  if (res.ok && Array.isArray(res.body)) {
    const matching = res.body.filter(e => allIdsB.includes(String(e.id)));
    finalizedMockB = matching.filter(e => triggeredMockIdsB.includes(String(e.id)) && TERMINAL.has(e.status)).length;
    finalizedLiveB = matching.filter(e => triggeredLiveIdsB.includes(String(e.id)) && TERMINAL.has(e.status)).length;
    succMockB = matching.filter(e => triggeredMockIdsB.includes(String(e.id)) && e.status === "SUBMITTED").length;
    succLiveB = matching.filter(e => triggeredLiveIdsB.includes(String(e.id)) && e.status === "SUBMITTED").length;
    if (finalizedMockB === triggeredMockIdsB.length && finalizedLiveB === triggeredLiveIdsB.length) break;
  }
  await sleep(2000);
}

if (succMockB < triggeredMockIdsB.length) {
  logFinding("HIGH", `Scenario B: only ${succMockB}/${triggeredMockIdsB.length} mock events SUBMITTED`, "mixed burst", "all mock SUBMITTED", `${succMockB} SUBMITTED, ${finalizedMockB} finalized`, "", "Mock should never fail; check log");
}
// live may fail at auction (acceptable per W3-FIX-AUCTION)

try { await pageB.screenshot({ path: `${SS_DIR}/B_mixed_after.png`, fullPage: true }); } catch {}
await ctxB.close();

// Leaderboard pollution check
const lbAfter = (await nfetch(`${BASE_API}/leaderboard`)).body;
// Mock wallets in code are 0x1010..., 0x2020..., 0x3030... — these are the AGENTS that won mock auctions.
// Check that they did NOT get added to leaderboard.
const mockWalletPrefixes = ["0x101010", "0x202020", "0x303030"];
const mockInLb = (lbAfter || []).filter(row => {
  const a = (row.address || "").toLowerCase();
  return mockWalletPrefixes.some(p => a.startsWith(p));
});
let polluted = mockInLb.length > 0;

// Additionally check: live wallets' total_bids delta should equal number of live finalized events that ran
// (one bid per live event per leaderboard wallet, roughly). We don't enforce this strictly.
const lbBids = (arr) => (arr || []).reduce((acc, r) => acc + (r.total_bids || 0), 0);
const totalBidsDelta = lbBids(lbAfter) - lbBids(lbBefore);
// A's 10 mock events should have added 0 to live-wallet totalBids. B's 5 live events should have added bids only for live wallets.
// If totalBidsDelta > expected_live_bid_increment, something polluted.
// We log it but don't assert.

if (polluted) {
  logFinding("HIGH", "Leaderboard polluted by mock wallets", "/leaderboard", "no mock wallet addresses", `${mockInLb.length} mock wallets present: ${mockInLb.map(r => r.address).join(", ")}`, "", "Mock-mode bids leaked into leaderboard aggregation");
}

// Wait for trigger rate-limit window before C/D (their evaluator triggers a single event each).
console.log("[B->C] waiting 65s for trigger rate limit window to reset");
await sleep(65_000);

// ===================================================================
// SCENARIO C: SSE reliability under refresh storm
// ===================================================================
console.log("[C] start");
const sseEventId = triggeredIdsA[0] || "1";

const ctxC = await browser.newContext({ viewport: { width: 1280, height: 800 } });
const pageC = await ctxC.newPage();
let sse429Count = 0;
let sse5xxCount = 0;
let sseFailCount = 0;
pageC.on("response", (resp) => {
  const url = resp.url();
  if (/\/sse\//.test(url) || /\/events\/stream/.test(url)) {
    const st = resp.status();
    if (st === 429) sse429Count++;
    if (st >= 500 && st < 600) sse5xxCount++;
  }
});
pageC.on("requestfailed", (req) => {
  if (/\/sse\//.test(req.url())) sseFailCount++;
});
attachListeners(pageC, "scenarioC");

try {
  await pageC.goto(`${BASE_UI}/events/${sseEventId}`, { waitUntil: "domcontentloaded", timeout: 30_000 });
} catch (e) {
  logFinding("MEDIUM", "Scenario C: initial event detail nav failed", `${BASE_UI}/events/${sseEventId}`, "200", String(e.message || e), "", "");
}
await pageC.waitForTimeout(1000);

for (let i = 0; i < 10; i++) {
  try {
    await pageC.reload({ waitUntil: "domcontentloaded", timeout: 15_000 });
  } catch {}
  await sleep(3000);
}
try { await pageC.screenshot({ path: `${SS_DIR}/C_after_refresh_storm.png`, fullPage: true }); } catch {}

// post-storm: navigate list page in pageC; trigger a new mock event via Node fetch; observe finalization
try {
  await pageC.goto(`${BASE_UI}/events`, { waitUntil: "domcontentloaded", timeout: 30_000 });
  await pageC.waitForTimeout(1500);
} catch {}

const triggerC = await nfetch(`${BASE_API}/trigger/event`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ mode: "mock" }),
});
const newIdC = triggerC.ok && triggerC.body && triggerC.body.event_id != null ? String(triggerC.body.event_id) : null;
console.log(`[C] post-storm trigger ok=${triggerC.ok} newId=${newIdC}`);
await sleep(10_000);

let sseLiveUpdateWorks = false;
let postStormStatus = null;
if (newIdC) {
  const det = await nfetch(`${BASE_API}/events/${newIdC}`);
  postStormStatus = det.ok && det.body ? det.body.status : null;
  sseLiveUpdateWorks = det.ok && TERMINAL.has(postStormStatus);
  if (!sseLiveUpdateWorks) {
    logFinding("MEDIUM", "Scenario C: post-storm new event did not finalize", `${BASE_API}/events/${newIdC}`, "SUBMITTED", `${postStormStatus || "unknown"}`, "", "Backend stuck after SSE refresh storm");
  }
  let listOk = false;
  try {
    listOk = await pageC.evaluate((id) => (document.body.innerText || "").includes(id), newIdC);
  } catch {}
  if (!listOk) {
    logFinding("LOW", "Scenario C: post-storm list page DOM did not show new event id", `${BASE_UI}/events`, `id ${newIdC} visible`, "absent", `${SS_DIR}/C_after_refresh_storm.png`, "May be pagination, or SSE-driven list update lag");
  }
}

if (sse429Count > 0) {
  logFinding("HIGH", `Scenario C: SSE got ${sse429Count} 429 responses`, "SSE under refresh storm", "0", `${sse429Count}`, "", "Rate limit not exempt for SSE — W2-2 fix regressed");
}
if (sse5xxCount > 0) {
  logFinding("HIGH", `Scenario C: SSE got ${sse5xxCount} 5xx responses`, "SSE under refresh storm", "0", `${sse5xxCount}`, "", "Backend SSE handler errored under storm");
}

await ctxC.close();

// ===================================================================
// SCENARIO D: Auto-redirect hijack regression
// ===================================================================
console.log("[D] start");
const ctxD = await browser.newContext({ viewport: { width: 1280, height: 800 } });
const tabD1 = await ctxD.newPage();
const tabD2 = await ctxD.newPage();
attachListeners(tabD1, "scenarioD_tab1");
attachListeners(tabD2, "scenarioD_tab2");

try {
  await tabD1.goto(`${BASE_UI}/operators`, { waitUntil: "domcontentloaded", timeout: 30_000 });
  await tabD1.waitForTimeout(1500);
} catch (e) {
  logFinding("MEDIUM", "Scenario D: /operators nav failed", `${BASE_UI}/operators`, "200", String(e.message || e), "", "");
}
const urlD1Before = tabD1.url();
try { await tabD1.screenshot({ path: `${SS_DIR}/D_operators_before.png`, fullPage: true }); } catch {}

const triggerD = await nfetch(`${BASE_API}/trigger/event`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ mode: "mock" }),
});

await tabD1.bringToFront();
const dEndAt = Date.now() + 20_000;
let urlD1AfterFinalize = urlD1Before;
let didFinalize = false;
while (Date.now() < dEndAt) {
  if (triggerD.ok && triggerD.body && triggerD.body.event_id != null) {
    const res = await nfetch(`${BASE_API}/events/${triggerD.body.event_id}`);
    if (res.ok && TERMINAL.has(res.body.status)) {
      didFinalize = true;
      await sleep(3000);
      urlD1AfterFinalize = tabD1.url();
      break;
    }
  }
  await sleep(800);
}
try { await tabD1.screenshot({ path: `${SS_DIR}/D_operators_after.png`, fullPage: true }); } catch {}

const dRegressed = urlD1AfterFinalize !== urlD1Before;
if (dRegressed) {
  logFinding("CRITICAL", "Scenario D: auto-redirect regression — /operators URL changed after mock event finalized", `${BASE_UI}/operators`, urlD1Before, urlD1AfterFinalize, `${SS_DIR}/D_operators_after.png`, "Global SSE event.finalized handler forced router.push() on /operators");
}

await ctxD.close();

// ===================================================================
// Backend log scan
// ===================================================================
const backendLogTail = await readBackendLog(backendLogStart);
const errPatterns = [
  { name: "error", re: /ERROR|Traceback|exception|Error:/gi },
  { name: "warning", re: /WARNING|warn/gi },
  { name: "oom", re: /OOM|out of memory|killed/gi },
  { name: "lock", re: /database is locked|deadlock|lock timeout/gi },
  { name: "malformed", re: /malformed|json decode|json parse/gi },
];
const backendIssues = {};
for (const p of errPatterns) {
  const matches = backendLogTail.match(p.re);
  backendIssues[p.name] = matches ? matches.length : 0;
}
if (backendIssues.error > 0) {
  const lines = backendLogTail.split("\n").filter(l => /error|traceback|exception/i.test(l)).slice(0, 3);
  logFinding("MEDIUM", `Backend log: ${backendIssues.error} error-level mentions during run`, BACKEND_LOG, "0", lines.join(" | ").slice(0, 400), "", "Backend logged errors during stress test");
}
if (backendIssues.lock > 0) {
  logFinding("HIGH", `Backend log: DB lock issues during stress`, BACKEND_LOG, "0", `${backendIssues.lock} matches`, "", "SQLite WAL contention");
}

await browser.close();

// ===================================================================
// Findings markdown
// ===================================================================
function sevRank(s) { return { CRITICAL: 0, HIGH: 1, MEDIUM: 2, LOW: 3 }[s] ?? 4; }
findings.sort((a, b) => sevRank(a.sev) - sevRank(b.sev));
const top = findings.slice(0, 12);

const errSummary = consoleErrors.length;
const warnSummary = consoleWarns.length;
const netSummary = networkErrors.length;
const peSummary = pageErrors.length;
const verdict = findings.some(f => f.sev === "CRITICAL") ? "failed" :
                findings.some(f => f.sev === "HIGH" || f.sev === "MEDIUM" || f.sev === "LOW") ? "has-issues" :
                "clean";

const md = [];
md.push(`# W6-P4 Findings — ${new Date().toISOString()}`);
md.push("");
for (const f of top) {
  md.push(`[${f.sev}] ${f.title}`);
  md.push(`  Where: ${f.where}`);
  md.push(`  Expected: ${f.expected}`);
  md.push(`  Actual: ${f.actual}`);
  if (f.screenshot) md.push(`  Screenshot: ${f.screenshot}`);
  md.push(`  Hypothesis: ${f.hypothesis}`);
  md.push("");
}
md.push(`VERDICT: ${verdict}`);
md.push(`Scenario A (10 mock concurrent): ${finalizedCountA}/${triggeredIdsA.length} finalized, avg time ${avgLifecycleMs != null ? (avgLifecycleMs / 1000).toFixed(2) : "?"}s, total ${(burstElapsedMs / 1000).toFixed(2)}s`);
md.push(`Scenario B (mixed): mock ${succMockB}/${triggeredMockIdsB.length} succeeded, live ${succLiveB}/${triggeredLiveIdsB.length} succeeded; leaderboard pollution: ${polluted ? "FOUND" : "NONE"} (delta_total_bids=${totalBidsDelta})`);
md.push(`Scenario C (refresh storm): SSE 429s = ${sse429Count}, 5xxs = ${sse5xxCount}, SSE failures = ${sseFailCount}; post-storm event id=${newIdC} status=${postStormStatus}`);
md.push(`Scenario D (auto-redirect): ${dRegressed ? "REGRESSED" : "NO regression"} (urlBefore=${urlD1Before} urlAfter=${urlD1AfterFinalize} finalized=${didFinalize})`);
md.push(`console.errors total: ${errSummary}` + (errSummary > 0 && errSummary < 10 ? "\n  " + consoleErrors.map(e => `[${e.label}] ${e.txt}`).join("\n  ") : ""));
md.push(`console.warns total: ${warnSummary}` + (warnSummary > 0 && warnSummary < 10 ? "\n  " + consoleWarns.map(e => `[${e.label}] ${e.txt}`).join("\n  ") : ""));
md.push(`pageerrors total: ${peSummary}` + (peSummary > 0 && peSummary < 10 ? "\n  " + pageErrors.map(e => `[${e.label}] ${e.txt}`).join("\n  ") : ""));
md.push(`network errors total (excl. intentional 404): ${netSummary}` + (netSummary > 0 && netSummary < 10 ? "\n  " + networkErrors.map(e => `[${e.label}] ${e.status} ${e.url}${e.error ? " (" + e.error + ")" : ""}`).join("\n  ") : ""));
md.push(`Backend log issues during run: errors=${backendIssues.error}, warnings=${backendIssues.warning}, oom=${backendIssues.oom}, locks=${backendIssues.lock}, malformed=${backendIssues.malformed}`);
if (peSummary > 0) {
  md.push("");
  md.push("All pageerrors:");
  for (const e of pageErrors) md.push(`  [${e.label}] ${e.txt}`);
}
if (netSummary > 0) {
  md.push("");
  md.push("All network errors:");
  for (const e of networkErrors.slice(0, 20)) md.push(`  [${e.label}] ${e.status} ${e.url}${e.error ? " (" + e.error + ")" : ""}`);
}

const newFindings = [];
if (polluted) newFindings.push("Mock wallets in leaderboard");
if (dRegressed) newFindings.push("Auto-redirect regression on /operators");
md.push(`Any new finding not previously known: ${newFindings.length ? newFindings.join("; ") : "none"}`);

fs.writeFileSync(OUT_MD, md.join("\n"));
console.log(`wrote ${OUT_MD}`);

console.log({
  finalizedCountA, triggeredA: triggeredIdsA.length, avgLifecycleMs, burstElapsedMs,
  succMockB, succLiveB, triggeredMockB: triggeredMockIdsB.length, triggeredLiveB: triggeredLiveIdsB.length,
  sse429Count, sse5xxCount, sseFailCount, sseLiveUpdateWorks,
  dRegressed, polluted,
  consoleErrors: errSummary, consoleWarns: warnSummary, pageErrors: peSummary, networkErrors: netSummary,
  backendIssues, findings: findings.length,
});
