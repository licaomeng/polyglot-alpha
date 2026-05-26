// W6-P1: Mock mode end-to-end + UI rendering verification.
// Run from /Users/messili/codebase/polyglot-alpha: node ui/scripts/w6-p1.mjs

import { chromium } from "playwright";
import { mkdirSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

const PROJECT_ROOT = "/Users/messili/codebase/polyglot-alpha";
const SHOTS_DIR = resolve(PROJECT_ROOT, "screenshots/wave6-p1");
mkdirSync(SHOTS_DIR, { recursive: true });

const BASE_UI = "http://localhost:3001";
const BASE_API = "http://localhost:8000";

const findings = [];
const consoleErrors = [];
const consoleWarnings = [];
const networkFailures = [];

const log = (...a) => console.log("[w6-p1]", ...a);

const shot = async (page, id, full = false) => {
  const p = resolve(SHOTS_DIR, `${id}.png`);
  await page.screenshot({ path: p, fullPage: full });
  return `screenshots/wave6-p1/${id}.png`;
};

const addFinding = (sev, title, where, expected, actual, screenshot, hypothesis) => {
  findings.push({ sev, title, where, expected, actual, screenshot, hypothesis });
};

const browser = await chromium.launch({ headless: true });
const ctx = await browser.newContext({ viewport: { width: 1920, height: 1080 } });
const page = await ctx.newPage();

page.on("console", (msg) => {
  const t = msg.type();
  const text = msg.text();
  if (t === "error") consoleErrors.push(text);
  else if (t === "warning") consoleWarnings.push(text);
});

page.on("response", (resp) => {
  const status = resp.status();
  const url = resp.url();
  if (status >= 400) {
    // Filter intentional 404 — favicon, source maps, etc.
    const isIntentional =
      url.includes("favicon") ||
      url.endsWith(".map") ||
      url.includes("/_next/static");
    if (!isIntentional) {
      networkFailures.push({ url, status });
    }
  }
});

// Capture POST body to backend
let triggerRequestBody = null;
page.on("request", (req) => {
  const u = req.url();
  if (u.includes(`${BASE_API}/events/trigger`) || u.includes("/events/trigger")) {
    try {
      triggerRequestBody = req.postData();
    } catch {}
  }
});

// =============== STEP 1: load home in mock mode ===============
log("STEP 1: open home in mock mode");
const t0 = Date.now();
await page.goto(`${BASE_UI}/?mode=mock`, { waitUntil: "domcontentloaded", timeout: 60000 });
await page.waitForLoadState("load", { timeout: 30000 }).catch(() => {});
await page.waitForTimeout(1500);

// =============== STEP 2: screenshot home + verify header amber ===============
log("STEP 2: verify home / header");
const homeShot = await shot(page, "01-home-mock");

const headerInfo = await page.evaluate(() => {
  const header = document.querySelector("header");
  if (!header) return { found: false };
  const cs = getComputedStyle(header);
  const borderColor = cs.borderBottomColor;
  const bg = cs.backgroundColor;
  const dataMode = header.getAttribute("data-mode");
  // Find logo icon (lucide svg)
  const icons = Array.from(header.querySelectorAll("svg"));
  const iconClasses = icons.map((i) => i.getAttribute("class") || i.outerHTML.slice(0, 120));
  // Logo text
  const logoText = header.querySelector("a")?.innerText || header.innerText.slice(0, 80);
  // MOCK pill?
  const text = header.innerText;
  const hasMock = /MOCK/i.test(text);
  const hasLive = /LIVE/i.test(text);
  // Position info — sticky?
  const position = cs.position;
  const top = cs.top;
  return {
    found: true,
    dataMode,
    borderColor,
    bg,
    iconClasses,
    logoText,
    hasMock,
    hasLive,
    position,
    top,
    headerHTML: header.outerHTML.slice(0, 4000),
  };
});

log("header info:", JSON.stringify({
  dataMode: headerInfo.dataMode,
  borderColor: headerInfo.borderColor,
  bg: headerInfo.bg,
  position: headerInfo.position,
  top: headerInfo.top,
  hasMock: headerInfo.hasMock,
  hasLive: headerInfo.hasLive,
}));

if (headerInfo.dataMode !== "mock") {
  addFinding(
    "HIGH",
    `<header> data-mode attribute is "${headerInfo.dataMode}" not "mock"`,
    "<header> element",
    "data-mode=\"mock\"",
    `data-mode=\"${headerInfo.dataMode}\"`,
    homeShot,
    "Mode context not propagated to header DOM attribute"
  );
}

// Verify icon is FlaskConical (mock) not Zap (live)
const flaskIconPresent = await page
  .locator('header svg.lucide-flask-conical, header svg[class*="flask"]')
  .count();
const zapIconPresent = await page
  .locator('header svg.lucide-zap, header svg[class*="lucide-zap"]')
  .count();
log("icons: flask=", flaskIconPresent, "zap=", zapIconPresent);

if (flaskIconPresent === 0) {
  addFinding(
    "HIGH",
    "FlaskConical icon not found in header in mock mode",
    "header svg",
    "FlaskConical icon visible (amber)",
    `flask=${flaskIconPresent}, zap=${zapIconPresent}`,
    homeShot,
    "Mock-mode icon swap not wired up correctly"
  );
}
if (zapIconPresent > 0) {
  addFinding(
    "MEDIUM",
    "Zap (live) icon still present in header while in mock mode",
    "header svg.lucide-zap",
    "Zap icon hidden when mode=mock",
    `zap count=${zapIconPresent}`,
    homeShot,
    "Conditional icon rendering not exclusive"
  );
}

// Check trigger button text — should say "Trigger mock demo"
const triggerBtn = page.locator('button:has-text("Trigger")').first();
const triggerText = (await triggerBtn.innerText().catch(() => "")) || "";
log("trigger button text:", triggerText);
if (!/mock/i.test(triggerText)) {
  addFinding(
    "MEDIUM",
    `Trigger button still says "${triggerText}" — not adapted to mock mode`,
    'button:has-text("Trigger")',
    `Trigger mock demo`,
    triggerText,
    homeShot,
    "Mode-aware label not applied to trigger CTA"
  );
}

// Verify there is no "local-mock" dead chip
const localMockChip = await page
  .locator('text="local-mock"')
  .count()
  .catch(() => 0);
if (localMockChip > 0) {
  addFinding(
    "LOW",
    `"local-mock" dead chip still rendered (${localMockChip} times)`,
    "text=local-mock",
    "Should be removed (W5-B cleanup)",
    `count=${localMockChip}`,
    homeShot,
    "Dead chip removal incomplete"
  );
}

// MOCK pill top-right — capture amber color
const mockPillInfo = await page.evaluate(() => {
  const header = document.querySelector("header");
  if (!header) return null;
  // Find element whose text is MOCK and parent is a pill-like
  const all = Array.from(header.querySelectorAll("*"));
  const pill = all.find((el) => /^MOCK$/.test((el.textContent || "").trim()) && el.children.length === 0);
  if (!pill) return null;
  const cs = getComputedStyle(pill);
  return { color: cs.color, bg: cs.backgroundColor, text: pill.textContent };
});
log("MOCK pill:", JSON.stringify(mockPillInfo));

// =============== STEP 3: click trigger ===============
log("STEP 3: click trigger");
const clickStart = Date.now();

// Wait for redirect — monitor URL changes via navigation event
const navPromise = page
  .waitForURL((url) => /\/events\/[^/]+$/.test(url.toString()) && !url.toString().endsWith("/events/"), {
    timeout: 130000,
  })
  .catch((e) => {
    log("nav wait error:", e.message);
    return null;
  });

await triggerBtn.click();
log("trigger clicked at", new Date().toISOString());

// Wait small moment then capture busy state
await page.waitForTimeout(800);
const busyShot = await shot(page, "02-trigger-busy");
const busyLabel = await triggerBtn.innerText().catch(() => "");
log("busy label:", busyLabel);

// Wait for navigation
await navPromise;
const navTime = Date.now() - clickStart;
const currentURL = page.url();
log("navigated to:", currentURL, "in", navTime, "ms");

const eventIdMatch = currentURL.match(/\/events\/([^/?#]+)/);
const eventId = eventIdMatch ? eventIdMatch[1] : null;
log("event id:", eventId);

// =============== STEP 4: capture timing & request body ===============
log("STEP 4: timing + request body");
log("trigger request body was:", triggerRequestBody);
const triggerBodyParsed = (() => {
  try {
    return JSON.parse(triggerRequestBody || "{}");
  } catch {
    return null;
  }
})();
if (!triggerBodyParsed || triggerBodyParsed.mode !== "mock") {
  addFinding(
    "HIGH",
    `Trigger POST body did not include mode:\"mock\" (got: ${JSON.stringify(triggerBodyParsed)})`,
    `POST ${BASE_API}/events/trigger`,
    '{"mode":"mock"}',
    triggerRequestBody || "(none)",
    busyShot,
    "Trigger endpoint not receiving mode override"
  );
}

// =============== STEP 5: verify event page ===============
log("STEP 5: verify event page render");
await page.waitForLoadState("load", { timeout: 15000 }).catch(() => {});
await page.waitForTimeout(2500); // give SSE settle time
const eventShot = await shot(page, "03-event-page-full", true);
const eventTopShot = await shot(page, "03b-event-page-top");

// MODE chip in header next to event title
const modeChipText = await page.evaluate(() => {
  // Look for a small chip showing MOCK near title
  const h1 = document.querySelector("h1");
  if (!h1) return null;
  // Find sibling badges/chips
  const root = h1.closest("section, header, div");
  if (!root) return null;
  const text = root.innerText;
  const m = text.match(/MOCK|LIVE/);
  return { match: m ? m[0] : null, sample: text.slice(0, 400) };
});
log("mode chip context near h1:", JSON.stringify(modeChipText));
if (!modeChipText || modeChipText.match !== "MOCK") {
  // try more broadly
  const anyMockChip = await page.locator('text=/^MOCK$/').count();
  if (anyMockChip === 0) {
    addFinding(
      "HIGH",
      "MODE chip showing MOCK not found near event title",
      "event detail page header",
      "MOCK amber chip visible next to event title",
      `near-h1=${JSON.stringify(modeChipText)}, anyMockChip=${anyMockChip}`,
      eventTopShot,
      "Mode chip render condition missing on event detail page"
    );
  }
}

// DAG: 11 nodes completed
const dagInfo = await page.evaluate(() => {
  // try multiple selectors
  const cands = [
    '[data-dag-node]',
    '[data-testid^="dag-node"]',
    'g.dag-node',
    'circle.dag-node',
    '[class*="DagNode"]',
    '[data-node-state]',
  ];
  for (const sel of cands) {
    const els = Array.from(document.querySelectorAll(sel));
    if (els.length > 0) {
      const states = els.map((e) =>
        e.getAttribute("data-state") ||
        e.getAttribute("data-node-state") ||
        e.getAttribute("data-status") ||
        e.className.toString()
      );
      return { selector: sel, count: els.length, states };
    }
  }
  // fallback — collect by inner text in any svg labels
  const svgs = Array.from(document.querySelectorAll('svg'));
  return { selector: null, count: 0, svgCount: svgs.length };
});
log("DAG nodes detected:", JSON.stringify(dagInfo).slice(0, 500));
if (dagInfo.count > 0 && dagInfo.count !== 11) {
  addFinding(
    "MEDIUM",
    `DAG shows ${dagInfo.count} nodes, expected 11`,
    `${dagInfo.selector}`,
    "11 DAG nodes",
    `count=${dagInfo.count}`,
    eventTopShot,
    "DAG node count mismatch — possibly phase missing or duplicated"
  );
}
const allCompleted =
  dagInfo.states &&
  dagInfo.states.every((s) =>
    /completed|complete|success|done|finalized/i.test(s)
  );
if (dagInfo.count > 0 && !allCompleted) {
  addFinding(
    "HIGH",
    "Not all DAG nodes show completed state",
    "DAG visualization",
    "All 11 nodes completed (mock = instant success)",
    `states=${JSON.stringify(dagInfo.states).slice(0, 500)}`,
    eventTopShot,
    "Mock mode failed to finalize all phases OR state attribute different"
  );
}

// Phase 1 — source_language and direction
const phase1Info = await page.evaluate(() => {
  // search the doc for source_language or "Source Language" labels and grab title with dir
  const allElements = Array.from(document.querySelectorAll("[dir]"));
  const dirInfo = allElements.map((e) => ({
    tag: e.tagName,
    dir: e.getAttribute("dir"),
    text: (e.textContent || "").slice(0, 200),
  })).slice(0, 20);
  // Find any text containing a known source-lang flag
  const allText = document.body.innerText;
  const langMatch = allText.match(/source[_ ]language[:\s]*([a-z]{2})/i);
  return {
    detectedSourceLang: langMatch ? langMatch[1] : null,
    dirElements: dirInfo,
  };
});
log("phase1:", JSON.stringify(phase1Info).slice(0, 600));

// Phase 2 — USDC Auction, 3 bids, tx hashes 0xsim_
const phase2Info = await page.evaluate(() => {
  const allText = document.body.innerText;
  // collect tx hashes
  const txMatches = [...allText.matchAll(/0x[a-zA-Z0-9_]{8,}/g)].map((m) => m[0]);
  const simTx = txMatches.filter((t) => /^0xsim_/i.test(t));
  // count bid rows — heuristic: "Bid" near a tx
  const bidCount = (allText.match(/Bid\s+\d|bid #/gi) || []).length;
  // Check for arcscan links
  const arcscanLinks = Array.from(document.querySelectorAll('a[href*="arcscan"]')).map((a) => ({
    href: a.href,
    text: a.textContent,
  }));
  return { txMatches: txMatches.slice(0, 20), simTx: simTx.slice(0, 20), bidCount, arcscanLinks };
});
log("phase2:", JSON.stringify(phase2Info).slice(0, 600));
if (phase2Info.arcscanLinks.length > 0) {
  const simArcscan = phase2Info.arcscanLinks.filter((l) => /sim/i.test(l.text) || /sim/i.test(l.href));
  if (simArcscan.length > 0) {
    addFinding(
      "HIGH",
      "Sim tx hashes link out to arcscan.app",
      "<a href> arcscan links",
      "0xsim_* should render as muted text, no link",
      JSON.stringify(simArcscan).slice(0, 400),
      eventShot,
      "Link wrapper not gated on 0xsim_ prefix"
    );
  }
}

// Phase 3 — Translation Pipeline
const phase3Info = await page.evaluate(() => {
  const text = document.body.innerText;
  const hasL2Block = /L2 (blocker|blocked|unavailable)/i.test(text);
  const hasCandA = /candidate.?A|cand[_ ]?a/i.test(text);
  const hasCandB = /candidate.?B|cand[_ ]?b/i.test(text);
  const hasModerator = /moderat/i.test(text);
  const hasRefine = /refin/i.test(text);
  return { hasL2Block, hasCandA, hasCandB, hasModerator, hasRefine };
});
log("phase3:", JSON.stringify(phase3Info));
if (phase3Info.hasL2Block) {
  addFinding(
    "HIGH",
    "Phase 3 still shows L2 blocker message in mock mode",
    "Phase 3 section",
    "Candidate A/B + moderator + refine visible (W4 Bug 5 fix)",
    "L2 blocker text present",
    eventShot,
    "Mock mode is hitting L2 unavailable code path"
  );
}

// Phase 4 — Panel of 11 judges
const phase4Info = await page.evaluate(() => {
  const text = document.body.innerText;
  const hasPartial = /Partial:?\s*\d+\/11/i.test(text);
  const hasMockShortCircuit = /panel\.evaluate\s+short[- ]circuit|Mock mode.*short[- ]circuit|short[- ]circuit.*synthetic\s+PASS/i.test(text);
  // Try to count judge scores 0.8-0.95
  const scoreMatches = [...text.matchAll(/0\.(8\d|9[0-5])/g)].map((m) => m[0]);
  // judge list
  const judgeRows = (text.match(/(judge|adjudicator)/gi) || []).length;
  return { hasPartial, hasMockShortCircuit, scoreSample: scoreMatches.slice(0, 20), judgeRows };
});
log("phase4:", JSON.stringify(phase4Info).slice(0, 600));
if (phase4Info.hasPartial) {
  addFinding(
    "HIGH",
    "Phase 4 shows 'Partial: X/11' banner in mock mode",
    "Phase 4 section",
    "No partial banner — mock = full pass",
    "Partial banner present",
    eventShot,
    "Mock-mode short-circuit not applied uniformly"
  );
}
if (!phase4Info.hasMockShortCircuit) {
  addFinding(
    "MEDIUM",
    "Phase 4 missing 'Mock mode: panel.evaluate short-circuited' reason text",
    "Phase 4 panel dossier",
    "Reason text visible per W5-A mock spec",
    "Not found in DOM text",
    eventShot,
    "Synthetic reason field not rendered in dossier"
  );
}

// Phase 5 — On-chain anchor
const phase5Info = await page.evaluate(() => {
  const text = document.body.innerText;
  const hasIpfsSim = /ipfs:\/\/sim\//i.test(text);
  const ipfsLinks = Array.from(document.querySelectorAll('a[href*="ipfs"], a[href*="ipfs.io"]'));
  const ipfsLinkInfo = ipfsLinks.map((a) => ({ href: a.href, text: a.textContent }));
  return { hasIpfsSim, ipfsLinkInfo: ipfsLinkInfo.slice(0, 10) };
});
log("phase5:", JSON.stringify(phase5Info).slice(0, 400));
if (phase5Info.ipfsLinkInfo.length > 0) {
  const simIpfsLinks = phase5Info.ipfsLinkInfo.filter((l) => /sim/i.test(l.href) || /sim/i.test(l.text));
  if (simIpfsLinks.length > 0) {
    addFinding(
      "HIGH",
      "ipfs://sim/* hash rendered as gateway link (should be muted)",
      "Phase 5 ipfs link",
      "ipfs://sim/* should be muted text, no link (W2-3 fix)",
      JSON.stringify(simIpfsLinks).slice(0, 400),
      eventShot,
      "Link wrapper not gated on sim ipfs prefix"
    );
  }
}

// Phase 6 — Polymarket V2
const phase6Info = await page.evaluate(() => {
  const text = document.body.innerText;
  const hasSimMarket = /sim_|dryrun-/i.test(text);
  // accordion / details
  const details = Array.from(document.querySelectorAll("details"));
  return { hasSimMarket, detailsCount: details.length };
});
log("phase6:", JSON.stringify(phase6Info));

// Phase 7 — Streaming Revenue
const phase7Info = await page.evaluate(() => {
  const text = document.body.innerText;
  const has9010 = /\b90\s*%.*10\s*%|\b90\/10\b|90 ?: ?10/i.test(text);
  const totalDisbursed = /Total\s+disbursed/i.test(text);
  const entries = /Entries/i.test(text);
  return { has9010, totalDisbursed, entries };
});
log("phase7:", JSON.stringify(phase7Info));

// Phase 8 — Reputation: should NOT show updates
const phase8Info = await page.evaluate(() => {
  const text = document.body.innerText;
  // Look for "Reputation" header and see if updates are present
  const reputationIdx = text.search(/Reputation/i);
  if (reputationIdx < 0) return { found: false };
  const slice = text.slice(reputationIdx, reputationIdx + 1500);
  const hasUpdates = /Δ|delta|\+\d|change/i.test(slice) && /score|rep/i.test(slice);
  const hasNoUpdate =
    /excluded|skipped|not.*recorded|no reputation|reputation.*not/i.test(slice);
  return { found: true, slice: slice.slice(0, 400), hasUpdates, hasNoUpdate };
});
log("phase8:", JSON.stringify(phase8Info).slice(0, 600));

// =============== STEP 6: scroll, verify sticky header ===============
log("STEP 6: scroll and verify sticky");
await page.evaluate(() => window.scrollTo({ top: 0, behavior: "instant" }));
await page.waitForTimeout(300);
const headerYBefore = await page.evaluate(() => {
  const h = document.querySelector("header");
  return h ? h.getBoundingClientRect().top : null;
});
// scroll smoothly down
await page.evaluate(() => window.scrollTo({ top: 2000, behavior: "instant" }));
await page.waitForTimeout(500);
const headerYAfter = await page.evaluate(() => {
  const h = document.querySelector("header");
  return h ? h.getBoundingClientRect().top : null;
});
const scrollY = await page.evaluate(() => window.scrollY);
const stickyShot = await shot(page, "04-scrolled-sticky");
log("header y before/after:", headerYBefore, "/", headerYAfter, "scrollY=", scrollY);
const stickyWorks = headerYAfter !== null && headerYAfter >= -1 && headerYAfter <= 5;
if (!stickyWorks) {
  addFinding(
    "HIGH",
    `Sticky header broken: top=${headerYAfter}px after scroll (scrollY=${scrollY})`,
    "<header>",
    "header.top remains 0 (sticky)",
    `top=${headerYAfter}px`,
    stickyShot,
    "overflow on a container may be clipping sticky again"
  );
}

// =============== STEP 7: hover tooltips ===============
log("STEP 7: scan tooltips");
await page.evaluate(() => window.scrollTo({ top: 0, behavior: "instant" }));
await page.waitForTimeout(300);
const tooltipTargets = await page.locator('[title], [aria-describedby], [data-tooltip], [role="tooltip"]').all().catch(() => []);
log("tooltip-bearing elements:", tooltipTargets.length);
let tooltipBgIssues = 0;
let firstBadTooltipShot = null;
// Hover up to 6 to keep runtime sane
for (let i = 0; i < Math.min(tooltipTargets.length, 6); i++) {
  try {
    await tooltipTargets[i].scrollIntoViewIfNeeded({ timeout: 1000 });
    await tooltipTargets[i].hover({ timeout: 1500 });
    await page.waitForTimeout(400);
    const tooltipBg = await page.evaluate(() => {
      const candidates = Array.from(
        document.querySelectorAll('[role="tooltip"], .tooltip, [data-state="open"][data-side]')
      );
      const visible = candidates.find((c) => {
        const r = c.getBoundingClientRect();
        return r.width > 0 && r.height > 0;
      });
      if (!visible) return null;
      const cs = getComputedStyle(visible);
      return { bg: cs.backgroundColor, opacity: cs.opacity, text: visible.textContent?.slice(0, 80) };
    });
    if (tooltipBg && (tooltipBg.bg === "rgba(0, 0, 0, 0)" || tooltipBg.bg === "transparent")) {
      tooltipBgIssues++;
      if (!firstBadTooltipShot) firstBadTooltipShot = await shot(page, `05-tooltip-bad-${i}`);
    }
  } catch (e) {
    // ignore failures hovering hidden elements
  }
}
log("tooltip bg issues:", tooltipBgIssues);
if (tooltipBgIssues > 0 && firstBadTooltipShot) {
  addFinding(
    "MEDIUM",
    `${tooltipBgIssues} tooltip(s) have transparent background`,
    "[role=tooltip]",
    "tooltip background = card / opaque",
    "background transparent",
    firstBadTooltipShot,
    "Tooltip variant missing bg-card class"
  );
}

// =============== STEP 8: console + network counts =================
log("STEP 8: console/network");
log("console errors:", consoleErrors.length);
log("console warnings:", consoleWarnings.length);
log("network failures:", JSON.stringify(networkFailures));

// =============== STEP 9: click DAG node 4 (Polymarket) ===============
log("STEP 9: click DAG node 4");
await page.evaluate(() => window.scrollTo({ top: 0, behavior: "instant" }));
await page.waitForTimeout(300);
const clickResult = await page.evaluate(() => {
  const cands = ['[data-dag-node]', '[data-node-state]', '[data-testid^="dag-node"]', 'g.dag-node', 'circle.dag-node'];
  for (const sel of cands) {
    const els = Array.from(document.querySelectorAll(sel));
    if (els.length >= 4) {
      const el = els[3];
      el.scrollIntoView({ block: "center" });
      el.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
      return { clicked: true, selector: sel, index: 3, rect: el.getBoundingClientRect() };
    }
  }
  return { clicked: false };
});
log("dag click attempt:", JSON.stringify(clickResult).slice(0, 300));
await page.waitForTimeout(900);
const afterDagClickY = await page.evaluate(() => window.scrollY);
const phase6Visible = await page.evaluate(() => {
  // Find any heading mentioning Polymarket
  const headings = Array.from(document.querySelectorAll("h1, h2, h3, h4"));
  const target = headings.find((h) => /polymarket/i.test(h.textContent || ""));
  if (!target) return null;
  const r = target.getBoundingClientRect();
  return { top: r.top, inView: r.top >= -50 && r.top <= window.innerHeight };
});
log("after dag click, scrollY=", afterDagClickY, "phase6 visible:", JSON.stringify(phase6Visible));
const dagClickShot = await shot(page, "06-after-dag-click");
if (clickResult.clicked && (!phase6Visible || !phase6Visible.inView)) {
  addFinding(
    "MEDIUM",
    "Clicking DAG node 4 did not scroll Polymarket phase into view",
    "DAG node 4",
    "Polymarket phase 6 scrolls into viewport",
    `phase6=${JSON.stringify(phase6Visible)} scrollY=${afterDagClickY}`,
    dagClickShot,
    "DAG node click handler not wired to phase anchor"
  );
}

// =============== STEP 10: data-mode on header ===============
log("STEP 10: header data-mode on event page");
const headerOnEventPage = await page.evaluate(() => {
  const h = document.querySelector("header");
  return h ? { dataMode: h.getAttribute("data-mode") } : null;
});
log("event-page header:", JSON.stringify(headerOnEventPage));

// =============== Build manifest ===============
const totalRuntime = Date.now() - t0;

// Mode badge consistency check
const modeBadgeConsistency =
  headerInfo.dataMode === "mock" &&
  headerOnEventPage?.dataMode === "mock" &&
  flaskIconPresent > 0 &&
  zapIconPresent === 0;

const verdict =
  findings.filter((f) => f.sev === "CRITICAL").length > 0
    ? "failed"
    : findings.length > 0
    ? "has-issues"
    : "clean";

const simHashLinking =
  (phase2Info.arcscanLinks.length === 0 ||
    phase2Info.arcscanLinks.filter((l) => /sim/i.test(l.text) || /sim/i.test(l.href)).length === 0) &&
  (phase5Info.ipfsLinkInfo.length === 0 ||
    phase5Info.ipfsLinkInfo.filter((l) => /sim/i.test(l.href) || /sim/i.test(l.text)).length === 0)
    ? "muted-correctly"
    : "leaks-to-arcscan";

const summary = {
  eventId,
  navTimeMs: navTime,
  totalRuntimeMs: totalRuntime,
  consoleErrors: consoleErrors.length,
  consoleWarnings: consoleWarnings.length,
  networkFailures: networkFailures.length,
  stickyHeader: stickyWorks ? "WORKS" : "BROKEN",
  simHashLinking,
  modeBadgeConsistency: modeBadgeConsistency ? "PASS" : "FAIL",
  findingsCount: findings.length,
  verdict,
  triggerBody: triggerBodyParsed,
  headerData: {
    home: { dataMode: headerInfo.dataMode, borderColor: headerInfo.borderColor, bg: headerInfo.bg, position: headerInfo.position },
    eventPage: headerOnEventPage,
  },
  iconsHome: { flask: flaskIconPresent, zap: zapIconPresent },
  phase2: { simTxCount: phase2Info.simTx.length, arcscanLinkCount: phase2Info.arcscanLinks.length },
  phase3: phase3Info,
  phase4: { hasPartial: phase4Info.hasPartial, hasMockShortCircuit: phase4Info.hasMockShortCircuit },
  phase5: { hasIpfsSim: phase5Info.hasIpfsSim, ipfsLinkCount: phase5Info.ipfsLinkInfo.length },
  phase6: phase6Info,
  phase7: phase7Info,
  phase8: phase8Info,
  consoleErrorsList: consoleErrors.slice(0, 20),
  consoleWarningsList: consoleWarnings.slice(0, 20),
  networkFailuresList: networkFailures.slice(0, 20),
};

const truncated = findings.slice(0, 10);
const findingsMd = truncated
  .map(
    (f, i) =>
      `### ${i + 1}. [${f.sev}] ${f.title}\n` +
      `  - **Where:** ${f.where}\n` +
      `  - **Expected:** ${f.expected}\n` +
      `  - **Actual:** ${f.actual}\n` +
      `  - **Screenshot:** ${f.screenshot}\n` +
      `  - **Hypothesis:** ${f.hypothesis}\n`
  )
  .join("\n");

const manifest = `# W6-P1 Mock Mode E2E Findings

Run: ${new Date().toISOString()}
Total runtime: ${totalRuntime}ms

## Summary

- **VERDICT**: ${verdict}
- **Mock event_id**: \`${eventId}\`
- **Time-to-auto-navigate**: ${navTime}ms
- **Console errors**: ${consoleErrors.length}
- **Network 4xx/5xx** (excl. intentional): ${networkFailures.length}
- **Sticky header**: ${stickyWorks ? "WORKS" : "BROKEN"}
- **Sim hash linking**: ${simHashLinking}
- **MODE badge consistency**: ${modeBadgeConsistency ? "PASS" : "FAIL"}
- **Findings count**: ${findings.length}

## Findings

${findingsMd || "_No findings — clean run._"}

## Detail dump

\`\`\`json
${JSON.stringify(summary, null, 2)}
\`\`\`
`;

writeFileSync("/tmp/wave6-p1-findings.md", manifest);
log("\n=========== SUMMARY ===========");
console.log(JSON.stringify(summary, null, 2));
log("Findings written to /tmp/wave6-p1-findings.md");

await browser.close();
