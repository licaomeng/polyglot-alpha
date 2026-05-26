// A2 sub-agent: 5-cycle Playwright loop covering UI + backend triggers.
// All triggers use auction_mode='mock' to avoid auction LLM calls;
// translation/eval phases still call Haiku (cheapest tier).
//
// Outputs:
//   outputs/playwright_loop_findings.md (running journal)
//   outputs/loop_screenshots/cycle_N_step_M.png

/* eslint-disable no-console */
const fs = require("fs");
const path = require("path");
const http = require("http");
const { chromium } = require("playwright");

const BASE_UI = process.env.BASE_UI || "http://127.0.0.1:3001";
const BASE_API = process.env.BASE_API || "http://127.0.0.1:8000";
const OUT_DIR = path.resolve(__dirname, "..", "..", "outputs");
const SHOT_DIR = path.join(OUT_DIR, "loop_screenshots");
const FINDINGS = path.join(OUT_DIR, "playwright_loop_findings.md");

fs.mkdirSync(SHOT_DIR, { recursive: true });

function ts() {
  return new Date().toISOString();
}
function append(line) {
  fs.appendFileSync(FINDINGS, line + "\n");
}

function postJson(url, body) {
  return new Promise((resolve, reject) => {
    const data = JSON.stringify(body);
    const u = new URL(url);
    const req = http.request(
      {
        hostname: u.hostname,
        port: u.port,
        path: u.pathname,
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Content-Length": Buffer.byteLength(data),
        },
      },
      (res) => {
        let chunks = "";
        res.on("data", (c) => (chunks += c));
        res.on("end", () => {
          try {
            resolve({ status: res.statusCode, body: JSON.parse(chunks) });
          } catch (e) {
            resolve({ status: res.statusCode, body: chunks });
          }
        });
      },
    );
    req.on("error", reject);
    req.write(data);
    req.end();
  });
}

function getJson(url) {
  return new Promise((resolve, reject) => {
    http
      .get(url, (res) => {
        let chunks = "";
        res.on("data", (c) => (chunks += c));
        res.on("end", () => {
          try {
            resolve({ status: res.statusCode, body: JSON.parse(chunks) });
          } catch (e) {
            resolve({ status: res.statusCode, body: chunks });
          }
        });
      })
      .on("error", reject);
  });
}

async function waitForTerminal(eventId, timeoutMs = 90000) {
  const t0 = Date.now();
  const terminal = new Set([
    "COMMITTED",
    "SUBMITTED",
    "FAILED",
    "REJECTED",
  ]);
  while (Date.now() - t0 < timeoutMs) {
    const r = await getJson(`${BASE_API}/events/${eventId}`);
    if (r.body && terminal.has(r.body.status)) {
      return r.body;
    }
    await new Promise((r) => setTimeout(r, 2500));
  }
  return null;
}

const SESSION_TAG = `a2-${Date.now()}`;

const CYCLES = [
  {
    label: "3-mock-bids",
    trigger: {
      event_source: "user_payload",
      title: `Will event A1 happen by 2026-12-31? [${SESSION_TAG}-c1]`,
      sources: [{ name: "test-c1", url: `https://test/c1?s=${SESSION_TAG}` }],
      language: "en",
      auction_mode: "mock",
      mock_bids: [
        {
          agent_address: "0xagent_a",
          bid_amount: 0.5,
          stake_amount: 5.0,
          reputation: 0.9,
        },
        {
          agent_address: "0xagent_b",
          bid_amount: 0.7,
          stake_amount: 5.0,
          reputation: 0.8,
        },
        {
          agent_address: "0xagent_c",
          bid_amount: 0.45,
          stake_amount: 5.0,
          reputation: 0.85,
        },
      ],
    },
  },
  {
    label: "1-mock-bid",
    trigger: {
      event_source: "user_payload",
      title: `Will event B2 happen by 2026-12-31? [${SESSION_TAG}-c2]`,
      sources: [{ name: "test-c2", url: `https://test/c2?s=${SESSION_TAG}` }],
      language: "en",
      auction_mode: "mock",
      mock_bids: [
        {
          agent_address: "0xagent_solo",
          bid_amount: 0.6,
          stake_amount: 5.0,
          reputation: 0.95,
        },
      ],
    },
  },
  {
    label: "0-mock-bids-edge",
    trigger: {
      event_source: "user_payload",
      title: `Will event C3 happen by 2026-12-31? [${SESSION_TAG}-c3]`,
      sources: [{ name: "test-c3", url: `https://test/c3?s=${SESSION_TAG}` }],
      language: "en",
      auction_mode: "mock",
      mock_bids: [],
    },
  },
  {
    label: "rep-gate-high-vs-low",
    trigger: {
      event_source: "user_payload",
      title: `Will event D4 happen by 2026-12-31? [${SESSION_TAG}-c4]`,
      sources: [{ name: "test-c4", url: `https://test/c4?s=${SESSION_TAG}` }],
      language: "en",
      auction_mode: "mock",
      mock_bids: [
        {
          agent_address: "0xagent_high",
          bid_amount: 0.4,
          stake_amount: 5.0,
          reputation: 0.99,
        },
        {
          agent_address: "0xagent_low1",
          bid_amount: 0.55,
          stake_amount: 5.0,
          reputation: 0.1,
        },
        {
          agent_address: "0xagent_low2",
          bid_amount: 0.6,
          stake_amount: 5.0,
          reputation: 0.15,
        },
      ],
    },
  },
  {
    label: "explore-other-pages",
    trigger: {
      event_source: "user_payload",
      title: `Will event E5 happen by 2026-12-31? [${SESSION_TAG}-c5]`,
      sources: [{ name: "test-c5", url: `https://test/c5?s=${SESSION_TAG}` }],
      language: "en",
      auction_mode: "mock",
      mock_bids: [
        {
          agent_address: "0xagent_a",
          bid_amount: 0.5,
          stake_amount: 5.0,
          reputation: 0.9,
        },
        {
          agent_address: "0xagent_b",
          bid_amount: 0.7,
          stake_amount: 5.0,
          reputation: 0.8,
        },
      ],
    },
  },
];

async function runCycle(browser, cycleN, spec) {
  const cycleStart = ts();
  const findings = [];
  findings.push(`\n## Cycle ${cycleN}: ${spec.label} (${cycleStart})`);
  console.log(`\n=== Cycle ${cycleN}: ${spec.label} ===`);

  // 1. Trigger
  let eventId = null;
  let triggerResp = null;
  try {
    triggerResp = await postJson(`${BASE_API}/trigger/event`, spec.trigger);
    findings.push(
      `- Trigger HTTP ${triggerResp.status}: \`${JSON.stringify(triggerResp.body).slice(0, 200)}\``,
    );
    eventId = triggerResp.body && triggerResp.body.event_id;
  } catch (e) {
    findings.push(`- Trigger FAILED with exception: ${e.message}`);
  }

  if (cycleN === 3) {
    // 0-bid edge: we expect this to either fail at validation OR succeed via fallback.
    findings.push(`- Edge case: 0 mock bids — recording behavior.`);
  }

  let finalEvent = null;
  if (eventId) {
    finalEvent = await waitForTerminal(eventId);
    if (finalEvent) {
      findings.push(
        `- Lifecycle terminal status: **${finalEvent.status}**, winner=${finalEvent.winner_address || "n/a"}, winning_bid=${finalEvent.winning_bid ?? "n/a"}`,
      );
    } else {
      findings.push(`- Lifecycle did NOT reach terminal within 90s.`);
    }
  }

  // 2. Playwright UI walk
  const context = await browser.newContext({
    viewport: { width: 1280, height: 800 },
  });
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
  page.on("console", (msg) => {
    if (msg.type() === "error") errors.push(`console.error: ${msg.text()}`);
  });

  let step = 0;
  const shot = async (label) => {
    step += 1;
    const p = path.join(SHOT_DIR, `cycle_${cycleN}_step_${step}_${label}.png`);
    try {
      await page.screenshot({ path: p, fullPage: false });
    } catch (e) {
      findings.push(`- Screenshot failed for ${label}: ${e.message}`);
    }
  };

  try {
    // /events list page
    await page.goto(`${BASE_UI}/events`, {
      waitUntil: "domcontentloaded",
      timeout: 20000,
    });
    await page.waitForTimeout(1500);
    await shot("events_list");

    if (eventId) {
      await page.goto(`${BASE_UI}/events/${eventId}`, {
        waitUntil: "domcontentloaded",
        timeout: 20000,
      });
      await page.waitForTimeout(2500);
      await shot("event_detail");

      // Try click any DAG node
      const dagNodes = await page.$$('[data-testid^="dag-node"], .dag-node, svg g[data-id]');
      findings.push(`- Found ${dagNodes.length} DAG-ish nodes on /events/${eventId}.`);
      if (dagNodes.length > 0) {
        try {
          await dagNodes[0].click({ timeout: 3000 });
          await page.waitForTimeout(800);
          await shot("dag_click");
        } catch (e) {
          findings.push(`- DAG node click failed: ${e.message}`);
        }
      }

      // Check Timeline element presence
      const tl = await page.$('[data-testid*="timeline"], .timeline, [class*="Timeline"]');
      findings.push(`- Timeline element present: ${tl ? "yes" : "no"}`);

      // Probe DOM for status text matching our final event
      if (finalEvent) {
        const html = await page.content();
        const seen = html.includes(finalEvent.status);
        findings.push(`- Final status \`${finalEvent.status}\` visible in DOM: ${seen}`);
      }
    }

    // Cycle 5: explore other pages
    if (cycleN === 5) {
      const otherRoutes = ["/", "/leaderboard", "/about", "/operators"];
      for (const r of otherRoutes) {
        try {
          await page.goto(`${BASE_UI}${r}`, {
            waitUntil: "domcontentloaded",
            timeout: 15000,
          });
          await page.waitForTimeout(1200);
          await shot(`route_${r.replace(/\//g, "_") || "root"}`);
          findings.push(`- ${r}: loaded OK`);
        } catch (e) {
          findings.push(`- ${r}: FAILED ${e.message}`);
        }
      }
    }
  } catch (e) {
    findings.push(`- Playwright walk error: ${e.message}`);
  }

  if (errors.length > 0) {
    findings.push(`- JS errors observed (${errors.length}):`);
    for (const er of errors.slice(0, 5)) findings.push(`  - ${er}`);
  } else {
    findings.push(`- JS errors observed: 0`);
  }

  await context.close();
  findings.push(`- Cycle finished at ${ts()}`);
  for (const f of findings) append(f);
  return { eventId, finalEvent, errors: errors.length };
}

(async () => {
  if (!fs.existsSync(FINDINGS)) {
    append(`# Playwright Loop Findings (A2 sub-agent)`);
    append(`Started ${ts()}`);
  } else {
    append(`\n---`);
    append(`# A2 Loop Session ${ts()}`);
  }

  const browser = await chromium.launch({ headless: true });
  const results = [];
  for (let i = 0; i < CYCLES.length; i++) {
    try {
      const r = await runCycle(browser, i + 1, CYCLES[i]);
      results.push(r);
    } catch (e) {
      append(`- Cycle ${i + 1} threw: ${e.message}`);
      results.push({ error: e.message });
    }
  }
  await browser.close();

  append(`\n## Session summary`);
  append(`- Cycles attempted: ${CYCLES.length}`);
  append(
    `- Cycles completed: ${results.filter((r) => !r.error).length}`,
  );
  append(`- Session end: ${ts()}`);

  console.log("DONE");
  console.log(JSON.stringify(results, null, 2));
})();
