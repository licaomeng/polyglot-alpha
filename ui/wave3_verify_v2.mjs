// Wave 3 verify v2 — targeted DOM probes for the items the broad regex missed.
import { chromium } from "playwright";
import fs from "node:fs";
import path from "node:path";

const UI = "http://localhost:3001";
const SHOTS = "/Users/messili/codebase/polyglot-alpha/screenshots/wave3-regression";
fs.mkdirSync(SHOTS, { recursive: true });

const notes = {};

async function main() {
  const browser = await chromium.launch({ headless: true });
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 4000 } });
  const page = await ctx.newPage();

  // ---- /events/118 ----
  await page.goto(`${UI}/events/118`, { waitUntil: "domcontentloaded", timeout: 30000 }).catch(()=>{});
  await page.waitForLoadState("networkidle", { timeout: 15000 }).catch(()=>{});
  await new Promise(r => setTimeout(r, 2000));

  // Phase 4 — locate the "11-Judge Panel" section then its text
  const phase4Section = page.locator('section, div, article').filter({ hasText: /11.?Judge Panel/i }).first();
  notes.event118_phase4Text = (await phase4Section.innerText().catch(()=> "")).slice(0, 1500);

  // partial banner more flexible
  notes.event118_partialBannerLoose = /\b(partial|pending|incomplete)\b/i.test(notes.event118_phase4Text);
  notes.event118_partialBannerSpecific = /partial[: ]*\d|completed.*11|11.*completed|pending.*bleu|pending.*comet/i.test(notes.event118_phase4Text);

  // BLEU + COMET rows
  // Find rows containing "bleu" or "comet"
  notes.event118_bleuRowText = await page.locator(':is(div,li,tr,td,span):has-text("bleu")').first().innerText().catch(()=> "");
  notes.event118_cometRowText = await page.locator(':is(div,li,tr,td,span):has-text("comet")').first().innerText().catch(()=> "");
  // any INSUFFICIENT_DATA visible anywhere
  const allText118 = await page.locator("body").innerText();
  notes.event118_insufficientDataAny = /INSUFFICIENT|insufficient/i.test(allText118);
  notes.event118_partialBadgeAny = /\bpartial\b/i.test(allText118);

  // Phase 6 — find Polymarket section
  const phase6Section = page.locator('section, div, article').filter({ hasText: /Polymarket/i }).first();
  notes.event118_phase6Text = (await phase6Section.innerText().catch(()=> "")).slice(0, 1500);
  notes.event118_phase6Skipped = /skip/i.test(notes.event118_phase6Text) || /verdict.*REJECTED|REJECTED.*FAILED|REJECTED.*verdict/i.test(notes.event118_phase6Text);

  // any MOCK/REAL badge contradiction on 118 page?
  notes.event118_mockReal = /\bMOCK\b/.test(allText118) && /\bREAL\b/.test(allText118);

  // Phase 7 — Streaming
  const phase7Section = page.locator('section, div, article').filter({ hasText: /Streaming Revenue|Revenue Stream/i }).first();
  notes.event118_phase7Text = (await phase7Section.innerText().catch(()=> "")).slice(0, 1500);
  notes.event118_phase7Skipped = /skip|never.*created|no revenue/i.test(notes.event118_phase7Text);

  // ---- /events/112 ----
  await page.goto(`${UI}/events/112`, { waitUntil: "domcontentloaded", timeout: 30000 }).catch(()=>{});
  await page.waitForLoadState("networkidle", { timeout: 15000 }).catch(()=>{});
  await new Promise(r => setTimeout(r, 2000));

  const allText112 = await page.locator("body").innerText();
  notes.event112_hasBuilderHexFull = /0xa93402f8ae6ac4a7b1d863d80145daa74f89cb4834fc0d86b36c1e4e1d6fbeb1/i.test(allText112);
  // shorter substring (truncated)
  notes.event112_hasBuilderHexShort = /0xa93402/i.test(allText112);
  notes.event112_hasPolyglotAlphaLiteral = /\bpolyglot_alpha\b/.test(allText112);
  notes.event112_marketId = /dryrun-b2d69cd7f790/i.test(allText112);
  notes.event112_anchorTxShort = /0x7ae2/i.test(allText112);
  notes.event112_revenueRecipientShort = /0x5554/i.test(allText112) && /0x928a/i.test(allText112);
  notes.event112_arcscan = /arcscan/i.test(allText112);

  // BLEU + COMET — find phase 4 then look for score text
  const phase4_112 = page.locator('section, div, article').filter({ hasText: /11.?Judge Panel/i }).first();
  notes.event112_phase4Text = (await phase4_112.innerText().catch(()=> "")).slice(0, 1500);

  // explicit BLEU + COMET rows look for "0.85" in same row
  notes.event112_bleuScore085 = /bleu[\s\S]{0,200}0\.85/i.test(notes.event112_phase4Text);
  notes.event112_cometScore085 = /comet[\s\S]{0,200}0\.85/i.test(notes.event112_phase4Text);
  notes.event112_bleuDashOnly = /bleu[\s\S]{0,80}—\s*$/im.test(notes.event112_phase4Text);

  // Polymarket: marketId chip + View API Payload accordion
  const phase6_112 = page.locator('section, div, article').filter({ hasText: /Polymarket V2/i }).first();
  notes.event112_phase6Text = (await phase6_112.innerText().catch(()=> "")).slice(0, 2000);
  notes.event112_viewApiPayload = /View API Payload|API Payload|payload/i.test(notes.event112_phase6Text);
  notes.event112_modeBadge = /\bmode\b/i.test(notes.event112_phase6Text);

  // count anchors that contain ipfs://
  notes.event112_ipfsRendered = await page.evaluate(() => {
    const out = { text: null, isLink: false, href: null };
    function walk(node) {
      if (!node) return;
      if (node.nodeType === 3 && /ipfs:\/\//.test(node.textContent)) {
        out.text = node.textContent.trim().slice(0, 200);
        let p = node.parentElement;
        while (p && p.tagName !== "A" && p !== document.body) p = p.parentElement;
        if (p && p.tagName === "A") {
          out.isLink = true;
          out.href = p.getAttribute("href");
        }
        return;
      }
      for (const c of node.childNodes) walk(c);
    }
    walk(document.body);
    return out;
  });

  // Debate UI specifics
  notes.event112_debate_l3 = /\bL3\b/.test(allText112);
  notes.event112_debate_l4 = /\bL4\b/.test(allText112);
  notes.event112_debate_l5 = /\bL5\b/.test(allText112);
  notes.event112_debate_candidates = /candidates?/i.test(allText112);
  notes.event112_debate_moderator = /moderator/i.test(allText112);
  notes.event112_debate_refine = /refine/i.test(allText112);
  notes.event112_debate_l2_blocker = /will appear here once translation pipeline advances past L2/i.test(allText112);

  // ---- trigger button on / ----
  await page.goto(`${UI}/`, { waitUntil: "domcontentloaded", timeout: 30000 }).catch(()=>{});
  await page.waitForLoadState("networkidle", { timeout: 15000 }).catch(()=>{});
  await new Promise(r => setTimeout(r, 2000));
  const triggerBtn = page.locator('button').filter({ hasText: /trigger.*demo|run.*demo|live demo|trigger/i }).first();
  notes.trigger_btnCount = await triggerBtn.count();
  notes.trigger_initialLabel = await triggerBtn.textContent().catch(()=> "");

  const labels = [];
  if (notes.trigger_btnCount > 0) {
    await triggerBtn.click({ timeout: 5000 }).catch(e => { notes.triggerClickErr = e.message; });
    const t0 = Date.now();
    while (Date.now() - t0 < 12000) {
      const t = (await triggerBtn.textContent().catch(()=> "")).trim();
      const disabled = await triggerBtn.getAttribute("disabled").catch(()=> null);
      const ariaBusy = await triggerBtn.getAttribute("aria-busy").catch(()=> null);
      labels.push({ ts: Date.now()-t0, text: t, disabled: !!disabled, ariaBusy });
      await new Promise(r => setTimeout(r, 500));
    }
  }
  notes.trigger_labels = labels;
  notes.trigger_uniqueLabels = [...new Set(labels.map(l => l.text))];

  // ---- /events failed badge zoom ----
  await page.goto(`${UI}/events`, { waitUntil: "domcontentloaded", timeout: 30000 }).catch(()=>{});
  await page.waitForLoadState("networkidle", { timeout: 15000 }).catch(()=>{});
  await new Promise(r => setTimeout(r, 1500));
  // crop the badges
  const firstFailed = page.locator(':is(span,div,td):has-text("Failed")').first();
  const firstRejected = page.locator(':is(span,div,td):has-text("Rejected")').first();
  try {
    await firstFailed.scrollIntoViewIfNeeded({ timeout: 5000 });
    await firstFailed.screenshot({ path: path.join(SHOTS, "wave3-r13b-failed-zoom.png") });
  } catch(e) { notes.failedZoomErr = e.message; }
  try {
    await firstRejected.scrollIntoViewIfNeeded({ timeout: 5000 });
    await firstRejected.screenshot({ path: path.join(SHOTS, "wave3-r13c-rejected-zoom.png") });
  } catch(e) { notes.rejectedZoomErr = e.message; }

  // distinct badge stats
  const allBadges = await page.evaluate(() => {
    const out = { failed: [], rejected: [] };
    const els = document.querySelectorAll("[class*=badge], span, div");
    for (const el of els) {
      if (el.children.length !== 0) continue;
      const t = (el.textContent || "").trim().toLowerCase();
      if (t !== "failed" && t !== "rejected") continue;
      const cs = getComputedStyle(el);
      out[t].push({ color: cs.color, bg: cs.backgroundColor, cn: (el.className || "").toString().slice(0,200) });
    }
    return out;
  });
  notes.distinctBadgeColors = {
    failedUnique: [...new Set(allBadges.failed.map(b => `${b.color}|${b.bg}`))],
    rejectedUnique: [...new Set(allBadges.rejected.map(b => `${b.color}|${b.bg}`))],
  };

  // ---- 4K hero text scale ----
  await page.setViewportSize({ width: 3840, height: 2160 });
  await page.goto(`${UI}/`, { waitUntil: "domcontentloaded", timeout: 30000 }).catch(()=>{});
  await page.waitForLoadState("networkidle", { timeout: 15000 }).catch(()=>{});
  await new Promise(r => setTimeout(r, 1500));
  notes.home4K = await page.evaluate(() => {
    const h1 = document.querySelector("h1");
    const main = document.querySelector("main");
    const hero = document.querySelector("section, .hero");
    return {
      h1Font: h1 ? getComputedStyle(h1).fontSize : null,
      h1Width: h1 ? h1.getBoundingClientRect().width : null,
      mainWidth: main ? main.getBoundingClientRect().width : null,
      heroWidth: hero ? hero.getBoundingClientRect().width : null,
    };
  });

  await browser.close();

  fs.writeFileSync("/tmp/w3-verify-v2.json", JSON.stringify(notes, null, 2));
  console.log(JSON.stringify(notes, null, 2));
}
main().catch(e => { console.error(e); process.exit(1); });
