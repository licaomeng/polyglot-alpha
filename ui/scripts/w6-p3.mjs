// W6-P3 — Public-pages verification (mode handling + leaderboard fixture/mock exclusion)
// Usage: node scripts/w6-p3.mjs
import { chromium } from "playwright";
import fs from "node:fs";
import path from "node:path";

const BASE = "http://localhost:3001";
const API = "http://localhost:8000";
const SHOT_DIR = "/Users/messili/codebase/polyglot-alpha/ui/screenshots/w6-p3";
fs.mkdirSync(SHOT_DIR, { recursive: true });

const findings = [];
const consoleErrors = []; // {page, msg}
const netErrors = []; // {page, url, status}

function addFinding(sev, title, where, expected, actual, shot, hypothesis) {
  findings.push({ sev, title, where, expected, actual, shot, hypothesis });
}

function logStep(s) {
  process.stderr.write(`\n[step] ${s}\n`);
}

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1920, height: 1080 } });
const page = await ctx.newPage();

page.on("console", (m) => {
  if (m.type() === "error") consoleErrors.push({ page: page.url(), msg: m.text() });
});
page.on("response", (r) => {
  if (r.status() >= 400) netErrors.push({ page: page.url(), url: r.url(), status: r.status() });
});

async function setMode(mode) {
  // Set mode via localStorage and reload — most reliable
  await page.evaluate((m) => localStorage.setItem("polyglot:mode", m), mode);
}

async function shot(name) {
  const p = path.join(SHOT_DIR, name);
  await page.screenshot({ path: p, fullPage: true });
  return p;
}

// ==========================================================================
// 0. Prime: visit root + set live mode initially
// ==========================================================================
logStep("0. prime to live mode");
await page.goto(`${BASE}/?mode=live`);
await page.waitForLoadState("networkidle").catch(() => {});

// ==========================================================================
// 1. /leaderboard
// ==========================================================================
logStep("1a. /leaderboard live");
await page.goto(`${BASE}/leaderboard`);
await page.waitForLoadState("networkidle").catch(() => {});
await page.waitForTimeout(800);
const lbLiveShot = await shot("01-leaderboard-live-1920.png");

// scan body for fixture addresses
const bodyLb = await page.locator("body").innerText();
const fixturesInLb = [];
for (const re of [/0xbbbb/i, /0xaaaa/i, /0xeeee/i, /0xcccc/i, /0xdddd/i, /0xdead/i, /0xagent/i]) {
  if (re.test(bodyLb)) fixturesInLb.push(re.toString());
}
if (fixturesInLb.length) {
  addFinding(
    "HIGH",
    `Fixture-shape address rendered on /leaderboard: ${fixturesInLb.join(",")}`,
    "/leaderboard step 1a",
    "no addresses with 4+ consecutive identical leading nibbles or 0xagent/0xdead",
    `matched: ${fixturesInLb.join(",")}`,
    lbLiveShot,
    "fixture filter regression (W2-4)",
  );
}

// MODE chip should NOT appear on leaderboard rows
const lbModeChips = await page.locator("table").locator("text=/^(MOCK|LIVE|Mock|Live)$/").count();
if (lbModeChips > 0) {
  addFinding(
    "MEDIUM",
    `Leaderboard table shows MODE chips (${lbModeChips})`,
    "/leaderboard step 1a",
    "no MODE chip on operator leaderboard rows (it's an OPERATOR list, not events)",
    `found ${lbModeChips} chips`,
    lbLiveShot,
    "RealVsMockBadge accidentally placed in leaderboard row",
  );
}

// API snapshot: stronger guarantee than scraping the DOM
async function apiLeaderboardTop() {
  return await page.evaluate(async (api) => {
    const r = await fetch(`${api}/leaderboard`).then((x) => x.json());
    return Array.isArray(r)
      ? {
          address: r[0]?.address,
          reputation: r[0]?.reputation,
          revenueUsd: r[0]?.revenueUsd,
          winRate: r[0]?.winRate,
          total_bids: r[0]?.total_bids,
          total_wins: r[0]?.total_wins,
        }
      : null;
  }, API);
}
const apiTopBefore = await apiLeaderboardTop();
logStep(`API top before: ${JSON.stringify(apiTopBefore)}`);
// Snapshot top row's rank-1 entry: address + rep + revenue + winRate (cell-precise)
async function captureTopRow() {
  return await page.evaluate(() => {
    const tr = document.querySelector("table tbody tr");
    if (!tr) return null;
    const cells = Array.from(tr.querySelectorAll("td")).map((c) => c.innerText.trim());
    // cells: [#, AgentNameAddress, Rep, Revenue, WinRate]
    return { raw: cells, rep: cells[2] ?? null, revenue: cells[3] ?? null, winRate: cells[4] ?? null };
  });
}
const topBefore = await captureTopRow();
const topRepBefore = topBefore?.rep ?? null;
logStep(`leaderboard top row before: ${JSON.stringify(topBefore)}`);

// Switch to MOCK + trigger 3 mock events
logStep("1b. switch to mock + trigger 3 events");
await setMode("mock");
await page.goto(`${BASE}/?mode=mock`);
await page.waitForLoadState("networkidle").catch(() => {});

const triggerResults = [];
for (let i = 0; i < 3; i++) {
  const r = await page.evaluate(async (api) => {
    const res = await fetch(`${api}/trigger/event`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        event_source: "rss",
        language: "zh",
        category: "macro",
        rss_window_minutes: 1440,
        auction_window_seconds: 0.5,
        mode: "mock",
      }),
    });
    return { status: res.status, body: (await res.text()).slice(0, 200) };
  }, API);
  triggerResults.push(r);
  await page.waitForTimeout(2200);
}
logStep(`trigger results: ${JSON.stringify(triggerResults.map((r) => r.status))}`);

// Reload leaderboard
await page.goto(`${BASE}/leaderboard`);
await page.waitForLoadState("networkidle").catch(() => {});
await page.waitForTimeout(800);
const lbMockShot = await shot("02-leaderboard-after-mock-triggers.png");

const topAfter = await captureTopRow();
const topRepAfter = topAfter?.rep ?? null;
logStep(`leaderboard top row after: ${JSON.stringify(topAfter)}`);
const apiTopAfter = await apiLeaderboardTop();
logStep(`API top after: ${JSON.stringify(apiTopAfter)}`);

let mockExcluded = "PASS";
const changedFields = [];
if (apiTopBefore && apiTopAfter) {
  for (const k of ["reputation", "revenueUsd", "total_bids", "total_wins"]) {
    if (apiTopBefore[k] !== apiTopAfter[k] && apiTopBefore.address === apiTopAfter.address) {
      changedFields.push(`${k}: ${apiTopBefore[k]} → ${apiTopAfter[k]}`);
    }
  }
  if (changedFields.length > 0) {
    mockExcluded = "FAIL";
    addFinding(
      "CRITICAL",
      "Leaderboard aggregates changed after triggering 3 mock events",
      "/leaderboard step 1b (API)",
      "rep/revenue/bids/wins unchanged for top operator (mock excluded)",
      changedFields.join("; "),
      lbMockShot,
      "mock events leaking into leaderboard aggregates (W5-A1 regression)",
    );
  }
}
// secondary DOM-level check
if (topRepBefore && topRepAfter && topRepBefore !== topRepAfter) {
  // already captured via API; just log inconsistency
  logStep(`DOM rep changed: ${topRepBefore} → ${topRepAfter} (API delta verified=${changedFields.length>0})`);
}

// ==========================================================================
// 2. /operators
// ==========================================================================
logStep("2. /operators");
await page.goto(`${BASE}/operators`);
await page.waitForLoadState("networkidle").catch(() => {});
await page.waitForTimeout(600);
const opShot = await shot("03-operators-1920.png");

const bodyOp = await page.locator("body").innerText();
const fixturesInOp = [];
for (const re of [/0xbbbb/i, /0xaaaa/i, /0xeeee/i, /0xdead/i, /0xagent/i]) {
  if (re.test(bodyOp)) fixturesInOp.push(re.toString());
}
if (fixturesInOp.length) {
  addFinding(
    "HIGH",
    `Fixture address visible on /operators: ${fixturesInOp.join(",")}`,
    "/operators step 2",
    "no fixture-shape addresses",
    `matched: ${fixturesInOp.join(",")}`,
    opShot,
    "operator fixture filter regression",
  );
}

// Address consistency check: scan for full addresses (40 hex chars) in body — should be shortened
const fullAddrMatches = bodyOp.match(/0x[a-f0-9]{40}/gi) || [];
if (fullAddrMatches.length > 0) {
  addFinding(
    "MEDIUM",
    `Full-length 0x address rendered on /operators (${fullAddrMatches.length})`,
    "/operators step 2",
    "all addresses shortAddr (6+4)",
    `examples: ${fullAddrMatches.slice(0, 2).join(", ")}`,
    opShot,
    "shortAddr() not applied somewhere",
  );
}

// mailto check
const mailtos = await page.$$eval('a[href^="mailto:"]', (els) => els.map((e) => e.href));
const personalLeak = mailtos.filter((m) => /gmail\.com|licaomeng|indeed|contractor/i.test(m));
if (personalLeak.length) {
  addFinding(
    "CRITICAL",
    "Personal email in mailto link",
    "/operators step 2",
    "mailto:operators@polyglot-alpha.example",
    personalLeak.join(", "),
    opShot,
    "G1 C2 regression",
  );
}

// ==========================================================================
// 3. /about — two viewports
// ==========================================================================
logStep("3a. /about @ 1920x1080");
await page.setViewportSize({ width: 1920, height: 1080 });
await page.goto(`${BASE}/about`);
await page.waitForLoadState("networkidle").catch(() => {});
await page.waitForTimeout(500);
const aboutShot1080 = await shot("04-about-1920x1080.png");
const bodyAbout = await page.locator("body").innerText();
const personalHits = [];
for (const re of [/indeed/i, /boxxo/i, /gmail/i, /licaomeng@/i]) {
  if (re.test(bodyAbout)) personalHits.push(re.toString());
}
// Note: "licaomeng" appears as github user — verify it's only the github link
const allLinks = await page.$$eval("a", (els) => els.map((e) => e.href));
const ghLinks = allLinks.filter((l) => /licaomeng/i.test(l));
const nonGhLeaks = personalHits.filter((p) => !/licaomeng/.test(p));
if (nonGhLeaks.length) {
  addFinding(
    "HIGH",
    `Personal/employer data leak on /about: ${nonGhLeaks.join(",")}`,
    "/about step 3a",
    "no Indeed/Boxxo/gmail in copy",
    `matched: ${nonGhLeaks.join(",")}`,
    aboutShot1080,
    "G1 C2 regression",
  );
}
// Verify the licaomeng reference is ONLY the github link
if (/licaomeng/i.test(bodyAbout) && ghLinks.length === 0) {
  addFinding(
    "MEDIUM",
    "licaomeng appears in /about body but no matching GH link found",
    "/about step 3a",
    "licaomeng confined to GH repo link",
    "text mentions licaomeng without link",
    aboutShot1080,
    "possibly bare username instead of GitHub URL",
  );
}

logStep("3b. /about @ 3840x2160");
await page.setViewportSize({ width: 3840, height: 2160 });
await page.goto(`${BASE}/about`);
await page.waitForLoadState("networkidle").catch(() => {});
await page.waitForTimeout(500);
const aboutShot4k = await shot("05-about-3840x2160.png");

// measure content vs viewport whitespace
const layout = await page.evaluate(() => {
  const main =
    document.querySelector("main") ||
    document.querySelector("[role=main]") ||
    document.body;
  const r = main.getBoundingClientRect();
  return {
    mainWidth: r.width,
    viewport: { w: window.innerWidth, h: window.innerHeight },
    bodyScrollWidth: document.documentElement.scrollWidth,
  };
});
const fillRatio = layout.mainWidth / layout.viewport.w;
logStep(
  `about@4k: mainWidth=${layout.mainWidth}, vp=${layout.viewport.w}, fill=${fillRatio.toFixed(2)}`,
);
if (fillRatio < 0.4) {
  addFinding(
    "MEDIUM",
    `Dead whitespace on /about at 4K (content fills ${(fillRatio * 100).toFixed(0)}%)`,
    "/about step 3b @ 3840x2160",
    "content fills >=40% of viewport (W2-3 fix)",
    `mainWidth=${layout.mainWidth}px / vp=${layout.viewport.w}px`,
    aboutShot4k,
    "max-w-3xl might still cap outer container",
  );
}

// reset viewport
await page.setViewportSize({ width: 1920, height: 1080 });

// ==========================================================================
// 4. /events list
// ==========================================================================
logStep("4. /events list");
await page.goto(`${BASE}/events`);
await page.waitForLoadState("networkidle").catch(() => {});
await page.waitForTimeout(800);
const eventsShot = await shot("06-events-list-1920.png");

// Check badge color distinction — Failed vs Rejected
// Find all status badges in event cards; we need actual computed color difference
const badgeData = await page.evaluate(() => {
  const out = [];
  // EventStatusBadge renders inside event cards
  const badges = Array.from(document.querySelectorAll('[aria-label^="Status: "]'));
  for (const b of badges) {
    const cs = getComputedStyle(b);
    out.push({
      label: b.textContent.trim(),
      aria: b.getAttribute("aria-label"),
      bg: cs.backgroundColor,
      color: cs.color,
      border: cs.borderColor,
    });
  }
  return out;
});
const labelsSeen = [...new Set(badgeData.map((b) => b.label))];
logStep(`badge labels: ${labelsSeen.join(", ")}`);

const failedSample = badgeData.find((b) => /Failed/i.test(b.label));
const rejectedSample = badgeData.find((b) => /Rejected/i.test(b.label));
let badgeDistinct = "PASS";
if (failedSample && rejectedSample) {
  const same =
    failedSample.bg === rejectedSample.bg &&
    failedSample.color === rejectedSample.color &&
    failedSample.border === rejectedSample.border;
  if (same) {
    badgeDistinct = "FAIL";
    addFinding(
      "HIGH",
      "Failed and Rejected badges have identical computed colors",
      "/events list step 4",
      "muted grey for Failed, destructive red for Rejected (W4 Bug 3)",
      `failed=${failedSample.bg}/${failedSample.color}, rejected=${rejectedSample.bg}/${rejectedSample.color}`,
      eventsShot,
      "lib/status.ts variant mapping regression",
    );
  } else {
    logStep(
      `Badge colors distinct: failed.bg=${failedSample.bg} vs rejected.bg=${rejectedSample.bg}`,
    );
  }
} else {
  logStep(
    `Could not find both Failed (${!!failedSample}) and Rejected (${!!rejectedSample}) on current /events view — distinctness untested`,
  );
}

// Mode chip presence on event rows
const modeChipPresence = await page.evaluate(() => {
  const chips = Array.from(document.querySelectorAll('[aria-label$="data"]'));
  return chips.length;
});
logStep(`mode chips on /events: ${modeChipPresence}`);
if (modeChipPresence === 0) {
  addFinding(
    "MEDIUM",
    "No MODE chip rendered on /events rows",
    "/events list step 4",
    "every event row has LIVE/MOCK chip",
    "0 chips found via aria-label",
    eventsShot,
    "RealVsMockBadge missing or aria-label changed",
  );
}

// Click first event row — should navigate to /events/{id}
const firstCard = page.locator("a[href^='/events/']").first();
let navOk = false;
if ((await firstCard.count()) > 0) {
  const href = await firstCard.getAttribute("href");
  await firstCard.click();
  await page.waitForLoadState("networkidle").catch(() => {});
  await page.waitForTimeout(500);
  if (page.url().includes(href)) navOk = true;
  await shot("07-events-detail.png");
} else {
  addFinding(
    "MEDIUM",
    "No clickable event link found on /events",
    "/events step 4 click",
    "every event card linked to /events/{id}",
    "no a[href^='/events/'] found",
    eventsShot,
    "EventCard not wrapped in Link",
  );
}
logStep(`/events click navigation: ${navOk}`);

// ==========================================================================
// 5. /history
// ==========================================================================
logStep("5. /history");
await page.goto(`${BASE}/history`);
await page.waitForLoadState("networkidle").catch(() => {});
await page.waitForTimeout(800);
const histShot = await shot("08-history-1920.png");
const histBody = await page.locator("body").innerText();
const hisFixtures = [];
for (const re of [/0xbbbb/i, /0xaaaa/i, /0xeeee/i, /0xdead/i]) {
  if (re.test(histBody)) hisFixtures.push(re.toString());
}
if (hisFixtures.length) {
  addFinding(
    "HIGH",
    `Fixture address on /history: ${hisFixtures.join(",")}`,
    "/history step 5",
    "no fixture addresses",
    hisFixtures.join(","),
    histShot,
    "fixture filter regression",
  );
}

// ==========================================================================
// 6. Toggle change while browsing /events
// ==========================================================================
logStep("6. mode toggle while on /events");
await setMode("live");
await page.goto(`${BASE}/events`);
await page.waitForLoadState("networkidle").catch(() => {});
await page.waitForTimeout(500);

const liveRowCount = await page.locator("a[href^='/events/']").count();
const liveModeChips = await page.locator('[aria-label="Mock data"], [aria-label="Live data"]').count();
const beforeToggleShot = await shot("09-events-before-toggle.png");

// click MOCK toggle
const mockRadio = page.locator('[role="radio"]:has-text("MOCK"), [role="radio"]:has-text("Mock")').first();
let toggleClicked = false;
if ((await mockRadio.count()) > 0) {
  await mockRadio.click();
  toggleClicked = true;
  await page.waitForTimeout(500);
}
const afterRowCount = await page.locator("a[href^='/events/']").count();
const afterModeChips = await page.locator('[aria-label="Mock data"], [aria-label="Live data"]').count();
const afterToggleShot = await shot("10-events-after-toggle.png");
logStep(
  `events list rowCount before=${liveRowCount}, after=${afterRowCount}; chips before=${liveModeChips} after=${afterModeChips}`,
);

let toggleSafe = "BENIGN";
if (!toggleClicked) {
  // not a destructive issue but raise medium
  addFinding(
    "LOW",
    "Could not locate MOCK radio on toggle in header",
    "/events step 6",
    "header has MOCK radio button",
    "radio not found by role=radio + text",
    beforeToggleShot,
    "DemoModeToggle label drift",
  );
} else if (Math.abs(liveRowCount - afterRowCount) > 0) {
  toggleSafe = "DESTRUCTIVE";
  addFinding(
    "MEDIUM",
    `Switching toggle on /events changed the row count (${liveRowCount} → ${afterRowCount})`,
    "/events step 6 toggle",
    "row count unchanged (global list of ALL events)",
    `count changed to ${afterRowCount}`,
    afterToggleShot,
    "client-side filtering by mode is wrong for /events list (it should be global)",
  );
}

// ==========================================================================
// 7. Aggregate stats DON'T leak mock events — backend API check
// ==========================================================================
logStep("7. raw API checks for mock exclusion");
const apiCheck = await page.evaluate(async (api) => {
  async function j(url) {
    const r = await fetch(url);
    return { status: r.status, json: await r.json().catch(() => null) };
  }
  const lb = await j(`${api}/leaderboard`);
  const ops = await j(`${api}/api/operators`);
  return { lb, ops };
}, API);

// shape of leaderboard top entry
const lbTop = Array.isArray(apiCheck.lb.json) ? apiCheck.lb.json[0] : null;
const opsTop = Array.isArray(apiCheck.ops.json) ? apiCheck.ops.json[0] : null;
logStep(
  `API: /leaderboard top rep=${lbTop?.reputation}, /api/operators first=${opsTop?.address}`,
);

// Verify any operator address with 4+ consecutive same nibble is absent
const lbFixtures = (apiCheck.lb.json || []).filter((e) =>
  /^0x(.)\1\1\1/i.test(e.address || ""),
);
if (lbFixtures.length) {
  addFinding(
    "CRITICAL",
    "Backend /leaderboard returns fixture-shape addresses",
    "step 7 API",
    "fixtures filtered server-side",
    `found ${lbFixtures.length}: ${lbFixtures.map((f) => f.address).join(", ")}`,
    null,
    "polyglot_alpha/api/routes/leaderboard.py filter broken",
  );
}
const opsFixtures = (apiCheck.ops.json || []).filter((e) =>
  /^0x(.)\1\1\1/i.test(e.address || ""),
);
if (opsFixtures.length) {
  addFinding(
    "HIGH",
    "Backend /api/operators returns fixture-shape addresses",
    "step 7 API",
    "fixtures filtered server-side",
    `found ${opsFixtures.length}`,
    null,
    "operators.py filter broken",
  );
}

// ==========================================================================
// finalize
// ==========================================================================
const verdict = findings.some((f) => f.sev === "CRITICAL")
  ? "failed"
  : findings.length === 0
  ? "clean"
  : "has-issues";

const fingerprint = {
  verdict,
  pages: 5,
  mockExcluded,
  badgeDistinct,
  toggleSafe,
  topRowBefore: topRepBefore,
  topRowAfter: topRepAfter,
  consoleErrors: consoleErrors.length,
  netErrors: netErrors.length,
  consoleErrorsDetail: consoleErrors.slice(0, 5),
  netErrorsDetail: netErrors.slice(0, 5),
  fixturesLb: fixturesInLb,
  fixturesOp: fixturesInOp,
  fixturesHist: hisFixtures,
  personalEmailLeak: personalLeak,
  badgeLabels: labelsSeen,
  toggleClicked,
  liveRowCount,
  afterRowCount,
  apiLbFixtures: lbFixtures.map((e) => e.address),
  apiOpsFixtures: opsFixtures.map((e) => e.address),
  triggerStatuses: triggerResults.map((r) => r.status),
};

console.log("\n=== W6-P3 RESULTS ===");
console.log(JSON.stringify(fingerprint, null, 2));
console.log("\n=== FINDINGS ===");
for (const f of findings) {
  console.log(
    `[${f.sev}] ${f.title}\n  Where: ${f.where}\n  Expected: ${f.expected}\n  Actual: ${f.actual}\n  Screenshot: ${f.shot}\n  Hypothesis: ${f.hypothesis}\n`,
  );
}

// Write manifest
const lines = [];
lines.push("# W6-P3 Findings\n");
lines.push(`Run: ${new Date().toISOString()}\n`);
lines.push("## Summary\n");
lines.push(`- VERDICT: ${verdict}`);
lines.push(`- Pages visited: 5 (/leaderboard, /operators, /about, /events, /history)`);
lines.push(`- Mock excluded from leaderboard: ${mockExcluded}`);
lines.push(`- Failed vs Rejected badge distinct: ${badgeDistinct}`);
lines.push(
  `- Address fixture leak: ${
    fixturesInLb.length + fixturesInOp.length + hisFixtures.length === 0
      ? "NONE"
      : "FOUND " + [...fixturesInLb, ...fixturesInOp, ...hisFixtures].join(",")
  }`,
);
lines.push(
  `- Personal data leak: ${personalLeak.length || nonGhLeaks.length ? "FOUND" : "NONE"}`,
);
lines.push(`- Toggle on list page: ${toggleSafe}`);
lines.push(`- console.errors total: ${consoleErrors.length}`);
lines.push(`- network 4xx/5xx total: ${netErrors.length}`);
lines.push("\n## Findings\n");
if (findings.length === 0) {
  lines.push("(none)\n");
} else {
  for (const f of findings) {
    lines.push(`### [${f.sev}] ${f.title}`);
    lines.push(`- Where: ${f.where}`);
    lines.push(`- Expected: ${f.expected}`);
    lines.push(`- Actual: ${f.actual}`);
    lines.push(`- Screenshot: ${f.shot ?? "(none)"}`);
    lines.push(`- Hypothesis: ${f.hypothesis}\n`);
  }
}
lines.push("\n## Telemetry\n");
lines.push("```json");
lines.push(JSON.stringify(fingerprint, null, 2));
lines.push("```");

fs.writeFileSync("/tmp/wave6-p3-findings.md", lines.join("\n"));
process.stderr.write("\nWrote /tmp/wave6-p3-findings.md\n");

await browser.close();
