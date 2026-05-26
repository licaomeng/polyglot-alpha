// Wave 3 PLAYWRIGHT regression sweep — headless
// Outputs screenshots to screenshots/wave3-regression/ and a JSON manifest
import { chromium } from "playwright";
import fs from "node:fs";
import path from "node:path";

const ROOT = "/Users/messili/codebase/polyglot-alpha";
const SHOTS = path.join(ROOT, "screenshots/wave3-regression");
fs.mkdirSync(SHOTS, { recursive: true });

const UI = "http://localhost:3001";
const API = "http://localhost:8000";

const findings = {
  steps: [],
  consoleErrors: [],
  pageErrors: [],
  networkErrors: [], // status >= 500 or 429, excluding intentional 404
  network404s: [],
  network429s: [],
  notes: {},
};

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

async function snap(page, name) {
  const file = path.join(SHOTS, `${name}.png`);
  try {
    await page.screenshot({ path: file, fullPage: true });
    findings.steps.push({ name, file, ok: true });
    console.log("[snap]", name);
  } catch (e) {
    findings.steps.push({ name, file, ok: false, error: String(e) });
    console.log("[snap FAIL]", name, e.message);
  }
}

function attachListeners(page) {
  page.on("console", (msg) => {
    if (msg.type() === "error") {
      findings.consoleErrors.push({ text: msg.text(), location: msg.location() });
    }
  });
  page.on("pageerror", (err) => {
    findings.pageErrors.push({ message: err.message, stack: err.stack });
  });
  page.on("response", (resp) => {
    const status = resp.status();
    const url = resp.url();
    if (status === 404) findings.network404s.push({ url, status });
    else if (status === 429) findings.network429s.push({ url, status });
    else if (status >= 500) findings.networkErrors.push({ url, status });
  });
}

async function main() {
  const browser = await chromium.launch({ headless: true });
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  attachListeners(page);

  // ===================================================================
  // W2-1: Event 118 (REJECTED) judge panel + empty states
  // ===================================================================
  console.log("\n=== W2-1: /events/118 (REJECTED) ===");
  await page.goto(`${UI}/events/118`, { waitUntil: "networkidle", timeout: 30000 }).catch(e => console.log("nav err", e.message));
  await sleep(1500);

  // capture full page first
  await snap(page, "wave3-r1-118-judges");

  // Inspect Phase 4
  const phase4Text = await page.locator('text=/11.?Judge Panel/i').first().textContent().catch(() => null);
  findings.notes.phase4HeadingFound = !!phase4Text;

  // count judge rows / cards (heuristic — look for the 11 judge names in DOM)
  const judgeNames = ["bleu", "comet", "mqm_llm", "d1_structural", "d2_stylistic",
    "d3_framing", "d4_granularity", "d5_resolution_clarity", "d6_source_reliability",
    "d7_leading_check", "d8_duplicate_detection"];
  const fullText118 = await page.locator("body").innerText().catch(() => "");
  findings.notes.event118_judgeNamesPresent = judgeNames.filter(j => fullText118.toLowerCase().includes(j.toLowerCase()));
  findings.notes.event118_partialBanner = /partial[: ]*\d+\s*\/\s*11/i.test(fullText118) || /\d+\/11\s*completed/i.test(fullText118);
  findings.notes.event118_insufficientDataBadge = /INSUFFICIENT_DATA/i.test(fullText118);
  findings.notes.event118_polymarketSkipped = /skipped/i.test(fullText118) && (/REJECTED|FAILED|verdict/i.test(fullText118));
  findings.notes.event118_hasMockRealBadge = /MOCK.*REAL|REAL.*MOCK/i.test(fullText118);
  findings.notes.event118_streamingSkipped = /Streaming skipped|market was never created/i.test(fullText118);

  // Scroll to find phase 6/7 and snap
  await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight * 0.55));
  await sleep(600);
  await snap(page, "wave3-r2-118-polymarket-skipped");
  await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight * 0.85));
  await sleep(600);
  await snap(page, "wave3-r3-118-revenue-skipped");

  // ===================================================================
  // W2-1 continued: Event 112 (SUBMITTED)
  // ===================================================================
  console.log("\n=== W2-1: /events/112 (SUBMITTED) ===");
  await page.goto(`${UI}/events/112`, { waitUntil: "networkidle", timeout: 30000 }).catch(e => console.log("nav err", e.message));
  await sleep(1500);
  const fullText112 = await page.locator("body").innerText().catch(() => "");
  findings.notes.event112_partialBanner = /partial[: ]*\d+\s*\/\s*11/i.test(fullText112) || /\d+\/11\s*completed/i.test(fullText112);
  findings.notes.event112_marketIdShown = /dryrun-b2d69cd7f790|market_id|marketId/i.test(fullText112);
  findings.notes.event112_viewApiPayloadAccordion = /View API Payload|API Payload/i.test(fullText112);
  findings.notes.event112_revenueArcScan = /arcscan/i.test(fullText112) || /arcTxHash/i.test(fullText112);
  findings.notes.event112_builderCodeLiteral = /polyglot_alpha/i.test(fullText112);
  findings.notes.event112_builderCodeHex = /0xa93402f8ae6ac4a7b1d863d80145daa74f89cb4834fc0d86b36c1e4e1d6fbeb1/i.test(fullText112);
  findings.notes.event112_bleuRow = /bleu/i.test(fullText112);
  findings.notes.event112_cometRow = /comet/i.test(fullText112);
  findings.notes.event112_bleuScore = /0\.85/.test(fullText112);
  findings.notes.event112_debateUiPresent = /candidate|moderator|refine|debate|L3|L4|L5/i.test(fullText112);
  findings.notes.event112_debateNotAdvancedMsg = /will appear here once translation pipeline advances past L2/i.test(fullText112);
  findings.notes.event112_ipfsLink = /ipfs:\/\/pipeline\/qwen\/bafaf919026c/i.test(fullText112);

  await snap(page, "wave3-r4-112-full");

  // Look for the ipfs hash specifically — check if rendered as link
  const ipfsAnchors = await page.locator('a:has-text("ipfs://")').count().catch(() => 0);
  const ipfsBrokenHrefCount = await page.locator('a[href*="ipfs://"]').count().catch(() => 0);
  const ipfsGatewayLinks = await page.locator('a[href*="ipfs.io"], a[href*="cloudflare-ipfs"], a[href*="dweb.link"]').count().catch(() => 0);
  findings.notes.event112_ipfsAnchorCount = ipfsAnchors;
  findings.notes.event112_ipfsBrokenHrefCount = ipfsBrokenHrefCount;
  findings.notes.event112_ipfsGatewayLinks = ipfsGatewayLinks;

  // builder_code check — find the on-chain anchor section text
  const builderCodeBlock = await page.locator('text=/BUILDER_CODE|Builder Code|builderCode/i').first().textContent().catch(() => null);
  findings.notes.event112_builderCodeSurroundingText = builderCodeBlock;
  await snap(page, "wave3-r11-builder-code");
  await snap(page, "wave3-r12-bleu-comet");

  // Phase 3 debate UI snapshot — scroll to it
  await page.evaluate(() => {
    const candidates = document.querySelectorAll("*");
    for (const el of candidates) {
      if (/translation pipeline/i.test(el.textContent || "") && el.children.length < 30) {
        el.scrollIntoView({ block: "center" });
        break;
      }
    }
  });
  await sleep(500);
  await snap(page, "wave3-r14-debate");

  // Look for "Failed" badge globally (later we'll also check events list)
  // ===================================================================
  // W2-3: IPFS rendering screenshot (on 118 or 112)
  // ===================================================================
  await page.goto(`${UI}/events/118`, { waitUntil: "networkidle", timeout: 30000 }).catch(() => {});
  await sleep(800);
  // scroll to on-chain anchor
  await page.evaluate(() => {
    const candidates = document.querySelectorAll("*");
    for (const el of candidates) {
      if (/on.?chain anchor|ipfs/i.test(el.textContent || "") && el.children.length < 30) {
        el.scrollIntoView({ block: "center" });
        break;
      }
    }
  });
  await sleep(500);
  await snap(page, "wave3-r9-ipfs");

  // ===================================================================
  // W2-2: SSE rate limit — refresh /events/118 5 times in 30s
  // ===================================================================
  console.log("\n=== W2-2: SSE refresh storm ===");
  const sseRequestStatus = [];
  const sseLogger = (resp) => {
    const u = resp.url();
    if (u.includes("/sse") || u.includes("eventsource") || u.includes("/events?") || u.includes("/stream")) {
      sseRequestStatus.push({ url: u, status: resp.status() });
    }
  };
  page.on("response", sseLogger);
  for (let i = 0; i < 5; i++) {
    const t0 = Date.now();
    await page.reload({ waitUntil: "domcontentloaded", timeout: 20000 }).catch(() => {});
    await sleep(1500);
    console.log(`  reload ${i + 1} in ${Date.now() - t0}ms`);
  }
  page.off("response", sseLogger);
  findings.notes.sseRequests = sseRequestStatus;
  findings.notes.sse429Count = sseRequestStatus.filter(r => r.status === 429).length;
  await snap(page, "wave3-r5-sse-refresh");

  // ===================================================================
  // W2-3: 4K layout
  // ===================================================================
  console.log("\n=== W2-3: 4K layout ===");
  const big = await ctx.newPage();
  attachListeners(big);
  await big.setViewportSize({ width: 3840, height: 2160 });
  await big.goto(`${UI}/`, { waitUntil: "networkidle", timeout: 30000 }).catch(() => {});
  await sleep(1500);
  await snap(big, "wave3-r6-home-4k");
  // measure hero container width
  const heroWidth = await big.evaluate(() => {
    const hero = document.querySelector('main, [role="main"], .hero, section');
    if (!hero) return null;
    return hero.getBoundingClientRect().width;
  }).catch(() => null);
  findings.notes.heroContainerWidth4K = heroWidth;

  await big.goto(`${UI}/about`, { waitUntil: "networkidle", timeout: 30000 }).catch(() => {});
  await sleep(1500);
  await snap(big, "wave3-r7-about-4k");
  const aboutWidth = await big.evaluate(() => {
    const main = document.querySelector("main");
    if (!main) return null;
    const inner = main.querySelector("div, article, section");
    return { mainW: main.getBoundingClientRect().width, innerW: inner?.getBoundingClientRect().width };
  }).catch(() => null);
  findings.notes.aboutWidths4K = aboutWidth;

  await big.goto(`${UI}/events/112`, { waitUntil: "networkidle", timeout: 30000 }).catch(() => {});
  await sleep(2000);
  await snap(big, "wave3-r8-dag-4k");
  // attempt to read react-flow zoom (rf__node scale)
  const dagInfo = await big.evaluate(() => {
    const viewport = document.querySelector(".react-flow__viewport");
    return viewport ? viewport.getAttribute("style") : null;
  }).catch(() => null);
  findings.notes.dagViewportTransform4K = dagInfo;

  await big.close();

  // ===================================================================
  // W2-4: leaderboard filter / failed badge / etc
  // ===================================================================
  console.log("\n=== W2-4: leaderboard ===");
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(`${UI}/leaderboard`, { waitUntil: "networkidle", timeout: 30000 }).catch(() => {});
  await sleep(1500);
  await snap(page, "wave3-r10-leaderboard");
  const lbText = await page.locator("body").innerText().catch(() => "");
  findings.notes.leaderboard_0xbbbb = /0xbbbb/i.test(lbText);
  findings.notes.leaderboard_0xaaaa = /0xaaaa/i.test(lbText);
  findings.notes.leaderboard_0xeeee = /0xeeee/i.test(lbText);
  // also generic 4+ same nibble check
  findings.notes.leaderboard_4plusSameNibble = (lbText.match(/0x([0-9a-f])\1{3,}/gi) || []);

  // ===================================================================
  // W2-4: Failed vs Rejected badge color — try /events list
  // ===================================================================
  console.log("\n=== W2-4: events list ===");
  await page.goto(`${UI}/events`, { waitUntil: "networkidle", timeout: 30000 }).catch(() => {});
  await sleep(1500);
  await snap(page, "wave3-r13-failed-rejected-badges");
  // sample the colour of any badge whose text is "Failed" or "Rejected"
  const badgeColors = await page.evaluate(() => {
    const out = [];
    const all = document.querySelectorAll("*");
    for (const el of all) {
      const t = (el.textContent || "").trim().toLowerCase();
      if (el.children.length === 0 && (t === "failed" || t === "rejected" || t === "submitted")) {
        const cs = getComputedStyle(el);
        const parent = el.parentElement ? getComputedStyle(el.parentElement) : null;
        out.push({
          text: t,
          color: cs.color,
          bg: cs.backgroundColor,
          parentBg: parent?.backgroundColor,
          className: el.className?.toString?.() || "",
        });
        if (out.length > 25) break;
      }
    }
    return out;
  }).catch(() => []);
  findings.notes.badgeColors = badgeColors;

  // ===================================================================
  // W2-4: Trigger live demo on /
  // ===================================================================
  console.log("\n=== W2-4: trigger live demo ===");
  await page.goto(`${UI}/`, { waitUntil: "networkidle", timeout: 30000 }).catch(() => {});
  await sleep(1500);
  // Find the trigger button — look for text "Trigger live demo" or similar
  const triggerBtn = page.locator('button:has-text("Trigger"), button:has-text("Run live"), button:has-text("Start demo")').first();
  const triggerExists = await triggerBtn.count();
  findings.notes.triggerBtnFound = triggerExists;
  const labelsSeen = [];
  if (triggerExists > 0) {
    try {
      await triggerBtn.click({ timeout: 5000 });
      const t0 = Date.now();
      // sample button text every 600ms for 8 seconds
      while (Date.now() - t0 < 10000) {
        const t = await triggerBtn.textContent().catch(() => "");
        const trimmed = (t || "").trim();
        if (trimmed && labelsSeen[labelsSeen.length - 1] !== trimmed) {
          labelsSeen.push(trimmed);
        }
        await sleep(600);
      }
    } catch (e) {
      findings.notes.triggerError = e.message;
    }
  }
  findings.notes.triggerLabelsSeen = labelsSeen;
  await snap(page, "wave3-r15-trigger-progressive");

  // ===================================================================
  // Persist
  // ===================================================================
  const manifestPath = "/tmp/w3-regression-findings.json";
  fs.writeFileSync(manifestPath, JSON.stringify(findings, null, 2));
  console.log("\n=== DONE ===");
  console.log("manifest:", manifestPath);
  console.log("console.errors:", findings.consoleErrors.length);
  console.log("pageErrors:", findings.pageErrors.length);
  console.log("network 5xx:", findings.networkErrors.length);
  console.log("network 429s:", findings.network429s.length);
  console.log("network 404s:", findings.network404s.length);

  await browser.close();
}

main().catch(e => { console.error("FATAL", e); process.exit(1); });
