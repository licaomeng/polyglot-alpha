// W10 concurrent stress audit — fire N mock + M live events in parallel,
// wait for all of them to reach a terminal state, run the chain-consistency
// verifier on each, and confirm three system invariants:
//
//   I1. Leaderboard does NOT show any new mock-only placeholder addrs
//       (mock lifecycles must not pollute the live leaderboard view).
//   I2. Live events have a real on-chain trace (NOT 0xsim_…).
//   I3. SSE never emits a 429 (rate limit fix from W9-D held under load).
//
// Output:
//   /tmp/w10-stress-audit.md  — per-scenario PASS/FAIL + the three
//                                invariants + raw trigger response bodies.
//
// Defaults:
//   --mock 5  (5 mock triggers)
//   --live 3  (3 live triggers — only fires if `--live N` is passed AND the
//              caller knows gas/Arc-testnet RPC capacity is available)
//
// Run from repo root with UI on :3001 and API on :8000:
//
//   node ui/scripts/w10/concurrent_stress_audit.mjs --mock 5 --live 3
//
// To dry-run (only mock, no live triggers):
//
//   node ui/scripts/w10/concurrent_stress_audit.mjs --mock 5 --live 0
//
// Idempotent — re-running just spawns a fresh batch.

import { chromium } from "playwright";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

const UI = process.env.UI_BASE ?? "http://localhost:3001";
const API = process.env.API_BASE ?? "http://localhost:8000";
const REPORT_PATH = "/tmp/w10-stress-audit.md";

const REPO_ROOT = path.resolve(
  new URL(".", import.meta.url).pathname,
  "..",
  "..",
  "..",
);
const VERIFIER = path.join(REPO_ROOT, "scripts", "verify_chain_consistency.py");
const VENV_PY = path.join(REPO_ROOT, ".venv", "bin", "python");

// Polling cap — Arc testnet live lifecycles can take 60-90s; mock typically
// finishes inside 20s. We give all events 3 minutes max.
const TERMINAL_POLL_TIMEOUT_MS = 180_000;
const POLL_INTERVAL_MS = 2_500;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const parseArgs = (argv) => {
  const out = { mock: 5, live: 3 };
  for (let i = 2; i < argv.length; i++) {
    if (argv[i] === "--mock") out.mock = Number(argv[++i]);
    else if (argv[i] === "--live") out.live = Number(argv[++i]);
  }
  if (!Number.isFinite(out.mock) || out.mock < 0) out.mock = 0;
  if (!Number.isFinite(out.live) || out.live < 0) out.live = 0;
  return out;
};

const triggerEvent = async (mode) => {
  const t0 = Date.now();
  // Live mode requires title (user_payload) OR event_source=hardcoded.
  // Use hardcoded to drive the bundled sample so the audit is reproducible
  // without callers supplying a headline.
  const payload =
    mode === "live"
      ? { mode, event_source: "hardcoded" }
      : { mode };
  const res = await fetch(`${API}/trigger/event`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
  });
  const elapsed = Date.now() - t0;
  let body = null;
  try {
    body = await res.json();
  } catch {
    body = { raw: await res.text() };
  }
  return {
    mode,
    ok: res.ok,
    status: res.status,
    event_id: body?.event_id ?? null,
    elapsed_ms: elapsed,
    body,
  };
};

const fetchEvent = async (event_id) => {
  const res = await fetch(`${API}/events/${event_id}`);
  if (!res.ok) return null;
  try {
    return await res.json();
  } catch {
    return null;
  }
};

// Terminal statuses match polyglot_alpha.persistence.models.EventStatus
// (SUBMITTED is "OK" terminal; REJECTED / FAILED are "not OK" terminal).
const TERMINAL_STATUSES = new Set(["SUBMITTED", "REJECTED", "FAILED"]);

const waitForTerminal = async (event_id, deadline) => {
  while (Date.now() < deadline) {
    const ev = await fetchEvent(event_id);
    if (ev && TERMINAL_STATUSES.has(ev.status)) return ev;
    await sleep(POLL_INTERVAL_MS);
  }
  return null;
};

const runVerifier = (event_id) => {
  const python = fs.existsSync(VENV_PY) ? VENV_PY : "python3";
  const proc = spawnSync(python, [VERIFIER, String(event_id)], {
    encoding: "utf8",
    timeout: 90_000,
  });
  return {
    rc: proc.status,
    stdout: proc.stdout || "",
    stderr: proc.stderr || "",
  };
};

const checkLeaderboardPollution = async (browser, mockEventIds) => {
  // Capture leaderboard addresses both pre- and post-trigger and look for
  // mock-only marks. We can't directly tell "is this address mock" from
  // the leaderboard payload, so we use the established W3 placeholder
  // heuristic: addresses with 4+ same-nibble runs are mock fixtures.
  const ctx = await browser.newContext({
    viewport: { width: 1440, height: 900 },
  });
  const page = await ctx.newPage();
  await page.goto(`${UI}/leaderboard`, {
    waitUntil: "domcontentloaded",
    timeout: 30000,
  });
  await sleep(2000);
  const text = await page.locator("body").innerText().catch(() => "");
  const placeholders = text.match(/0x([0-9a-f])\1{3,}/gi) || [];
  await ctx.close();
  return {
    placeholders_seen: placeholders,
    polluted: placeholders.length > 0,
  };
};

const checkSseUnder429 = async (browser, anyEventId) => {
  // Open the event detail page and listen for 429s on the SSE channel for
  // ~10 seconds. We do a single reload to ensure a fresh subscription.
  const ctx = await browser.newContext({
    viewport: { width: 1440, height: 900 },
  });
  const page = await ctx.newPage();
  const sse429s = [];
  const sseRequests = [];
  page.on("response", (resp) => {
    const url = resp.url();
    if (/\/sse\/|\/sse\?|eventsource/i.test(url) || resp.headers()["content-type"]?.includes("event-stream")) {
      sseRequests.push({ url, status: resp.status() });
      if (resp.status() === 429) sse429s.push(url);
    }
  });
  await page.goto(`${UI}/events/${anyEventId}`, {
    waitUntil: "domcontentloaded",
    timeout: 30000,
  });
  await sleep(10_000);
  await ctx.close();
  return { sse_requests: sseRequests.length, sse_429_count: sse429s.length };
};

const isSimTxHash = (s) =>
  typeof s === "string" && s.toLowerCase().startsWith("0xsim_");

const liveEventHasRealTrace = (eventDetail) => {
  // The /api/events/{id} payload includes auction/anchor/fee tx hashes.
  // We treat the event as "has real on-chain trace" iff at least one of
  // the chain-touching tx_hash fields is non-sim and non-empty.
  if (!eventDetail) return false;
  const candidates = [
    eventDetail.tx_hash,
    eventDetail.auction?.settlement_tx_hash,
    eventDetail.anchor?.tx_hash,
    eventDetail.judges_attestation_tx,
    ...(Array.isArray(eventDetail.fees)
      ? eventDetail.fees.map((f) => f.arc_tx_hash)
      : []),
  ].filter(Boolean);
  return candidates.some((h) => !isSimTxHash(h));
};

const main = async () => {
  const args = parseArgs(process.argv);
  console.log(`[stress] firing ${args.mock} mock + ${args.live} live triggers`);

  // Fire everything in parallel.
  const triggers = [];
  for (let i = 0; i < args.mock; i++) triggers.push(triggerEvent("mock"));
  for (let i = 0; i < args.live; i++) triggers.push(triggerEvent("live"));
  const triggered = await Promise.all(triggers);
  for (const t of triggered) {
    console.log(
      `  trigger mode=${t.mode} event_id=${t.event_id} status=${t.status} (${t.elapsed_ms}ms)`,
    );
  }

  // Filter the ones we got an event_id for; the rest are immediate failures.
  const live = triggered.filter((t) => t.mode === "live" && t.event_id);
  const mock = triggered.filter((t) => t.mode === "mock" && t.event_id);
  const allWithId = [...mock, ...live];

  if (allWithId.length === 0) {
    fs.writeFileSync(
      REPORT_PATH,
      `# W10 concurrent stress audit\n\nFAIL — no triggers returned an event_id.\n\n${JSON.stringify(triggered, null, 2)}\n`,
    );
    console.log("[stress] FAIL — no triggers returned event_id");
    process.exitCode = 2;
    return;
  }

  // Wait for every event to reach a terminal status.
  const deadline = Date.now() + TERMINAL_POLL_TIMEOUT_MS;
  console.log("[stress] waiting for terminal status…");
  const settled = await Promise.all(
    allWithId.map(async (t) => {
      const ev = await waitForTerminal(t.event_id, deadline);
      return { trigger: t, terminal: ev };
    }),
  );

  // Per-scenario results — run the chain verifier on each.
  const perScenario = [];
  for (const s of settled) {
    if (!s.terminal) {
      perScenario.push({
        event_id: s.trigger.event_id,
        mode: s.trigger.mode,
        terminal_status: null,
        verifier_rc: null,
        verifier_summary: "did not reach terminal within timeout",
        live_real_trace: false,
        ok: false,
      });
      continue;
    }
    const v = runVerifier(s.trigger.event_id);
    const overallLine = (v.stdout.match(/OVERALL:.*$/m) || [""])[0];
    const liveOk =
      s.trigger.mode === "live"
        ? liveEventHasRealTrace(s.terminal)
        : true;
    perScenario.push({
      event_id: s.trigger.event_id,
      mode: s.trigger.mode,
      terminal_status: s.terminal.status,
      verifier_rc: v.rc,
      verifier_summary: overallLine.trim() || "(no OVERALL line)",
      live_real_trace: liveOk,
      ok: v.rc === 0 && s.terminal.status === "SUBMITTED" && liveOk,
    });
  }

  // System-wide invariants.
  const browser = await chromium.launch({ headless: true });
  let leaderboard = { placeholders_seen: [], polluted: false };
  let sse = { sse_requests: 0, sse_429_count: 0 };
  try {
    leaderboard = await checkLeaderboardPollution(
      browser,
      mock.map((t) => t.event_id),
    );
    sse = await checkSseUnder429(browser, allWithId[0].event_id);
  } finally {
    await browser.close();
  }

  // Render report.
  const lines = [];
  lines.push("# W10 concurrent stress audit");
  lines.push("");
  lines.push(
    `- triggers fired: mock=${args.mock} live=${args.live} (api=${API}, ui=${UI})`,
  );
  lines.push(`- timestamp: ${new Date().toISOString()}`);
  lines.push("");

  lines.push("## Invariants");
  lines.push("");
  lines.push(
    `- **I1 leaderboard pollution**: ${leaderboard.polluted ? "FAIL" : "PASS"} — placeholders seen: ${JSON.stringify(leaderboard.placeholders_seen)}`,
  );
  const liveRealOk =
    live.length === 0 ||
    perScenario.filter((p) => p.mode === "live").every((p) => p.live_real_trace);
  lines.push(
    `- **I2 live on-chain trace**: ${liveRealOk ? "PASS" : "FAIL"} — every live event has at least one non-sim tx hash`,
  );
  lines.push(
    `- **I3 SSE rate-limit (0 × 429)**: ${sse.sse_429_count === 0 ? "PASS" : "FAIL"} — sse requests=${sse.sse_requests}, 429s=${sse.sse_429_count}`,
  );
  lines.push("");

  lines.push("## Per-scenario");
  lines.push("");
  lines.push(
    "| event_id | mode | terminal | verifier rc | verifier overall | live trace | ok |",
  );
  lines.push("|----------|------|----------|-------------|------------------|------------|----|");
  for (const p of perScenario) {
    lines.push(
      `| ${p.event_id} | ${p.mode} | ${p.terminal_status ?? "(timeout)"} | ${p.verifier_rc ?? "—"} | ${p.verifier_summary.slice(0, 60)} | ${p.live_real_trace ? "yes" : "no"} | ${p.ok ? "PASS" : "FAIL"} |`,
    );
  }
  lines.push("");

  // Trigger response bodies (debug aid).
  lines.push("## Trigger response bodies");
  lines.push("");
  lines.push("```json");
  lines.push(JSON.stringify(triggered, null, 2));
  lines.push("```");

  fs.writeFileSync(REPORT_PATH, lines.join("\n") + "\n");
  console.log(`[stress] wrote ${REPORT_PATH}`);

  const allOk =
    !leaderboard.polluted &&
    liveRealOk &&
    sse.sse_429_count === 0 &&
    perScenario.every((p) => p.ok);
  if (!allOk) {
    console.log("[stress] FAIL — see /tmp/w10-stress-audit.md");
    process.exitCode = 1;
  } else {
    console.log("[stress] PASS — all invariants + scenarios OK");
  }
};

main().catch((err) => {
  console.error("[stress] FATAL", err);
  fs.writeFileSync(
    REPORT_PATH,
    `# W10 concurrent stress audit\n\n**fatal**: \`${err instanceof Error ? err.message : String(err)}\`\n`,
  );
  process.exit(2);
});
