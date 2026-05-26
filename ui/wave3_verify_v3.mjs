// Focused: event 112 builder_code + Polymarket payload, trigger button raw HTML
import { chromium } from "playwright";
import fs from "node:fs";

const UI = "http://localhost:3001";
const notes = {};

async function main() {
  const browser = await chromium.launch({ headless: true });
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 8000 } });
  const page = await ctx.newPage();

  // === /events/112 ===
  await page.goto(`${UI}/events/112`, { waitUntil: "domcontentloaded", timeout: 30000 }).catch(()=>{});
  await page.waitForLoadState("networkidle", { timeout: 15000 }).catch(()=>{});
  await new Promise(r => setTimeout(r, 2500));

  // Full body text + all data-* and aria attributes for keyword searches
  const body112 = await page.locator("body").innerText();
  notes.event112_text_includes_polyglot_alpha_lower = body112.includes("polyglot_alpha");
  notes.event112_text_includes_POLYGLOT_ALPHA_upper = body112.includes("POLYGLOT_ALPHA");
  notes.event112_text_includes_full_builder_hex = body112.includes("0xa93402f8");
  notes.event112_text_includes_short_builder_hex = body112.includes("0xa934") || body112.includes("a93402");
  notes.event112_text_includes_market_id = body112.includes("dryrun-b2d69cd7f790");
  notes.event112_text_includes_arcscan_link = body112.includes("arcscan.app") || body112.includes("arcTxHash") || body112.includes("0xc568") || body112.includes("0x017e");
  notes.event112_text_includes_recipient_5554 = body112.includes("0x5554") || body112.includes("5554a1Ce");
  notes.event112_text_includes_recipient_928a = body112.includes("0x928a") || body112.includes("928a7f8b");

  // Find Phase 6 (Polymarket V2 Submission) — scroll to its specific row
  const p6 = page.locator('text="Polymarket V2 Submission"').first();
  const p6Box = await p6.boundingBox().catch(()=> null);
  notes.phase6_position = p6Box;
  // Get the parent container's HTML
  const p6Container = page.locator('text="Polymarket V2 Submission"').first().locator("xpath=ancestor::*[5]");
  notes.phase6_container_html = (await p6Container.innerHTML().catch(()=> "")).slice(0, 4000);
  notes.phase6_container_text = (await p6Container.innerText().catch(()=> "")).slice(0, 2500);

  // Phase 7
  const p7 = page.locator('text="Streaming Revenue"').first();
  const p7Container = p7.locator("xpath=ancestor::*[5]");
  notes.phase7_container_text = (await p7Container.innerText().catch(()=> "")).slice(0, 2500);

  // Phase 5 / On-chain anchor
  const p5 = page.locator('text="On-chain Anchor"').first();
  const p5Container = p5.locator("xpath=ancestor::*[5]");
  notes.phase5_container_text = (await p5Container.innerText().catch(()=> "")).slice(0, 2500);

  // ALL anchor tags with hrefs for ipfs/arcscan
  notes.allArcscanHrefs = await page.evaluate(() => {
    return [...document.querySelectorAll("a[href]")]
      .map(a => a.getAttribute("href"))
      .filter(h => /arcscan|ipfs/i.test(h));
  });

  // ipfs related text occurrences with surrounding context
  notes.ipfsContexts = await page.evaluate(() => {
    const out = [];
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    let n;
    while ((n = walker.nextNode())) {
      if (n.textContent && /ipfs|qwen|bafaf/i.test(n.textContent)) {
        const parent = n.parentElement;
        out.push({
          text: n.textContent.trim().slice(0, 200),
          parentTag: parent?.tagName,
          parentClass: (parent?.className || "").toString().slice(0, 200),
          isInsideAnchor: !!parent?.closest("a"),
          anchorHref: parent?.closest("a")?.getAttribute("href") || null,
        });
        if (out.length > 10) break;
      }
    }
    return out;
  });

  // === /events/118 — look for 'INSUFFICIENT_DATA' or 'PARTIAL' explicit DOM hits ===
  await page.goto(`${UI}/events/118`, { waitUntil: "domcontentloaded", timeout: 30000 }).catch(()=>{});
  await page.waitForLoadState("networkidle", { timeout: 15000 }).catch(()=>{});
  await new Promise(r => setTimeout(r, 2000));

  const body118 = await page.locator("body").innerText();
  notes.event118_anyInsufficientData = body118.toUpperCase().includes("INSUFFICIENT");
  notes.event118_anyPartialWord = body118.toLowerCase().split(/\s+/).filter(w => /^partial/i.test(w)).slice(0,10);
  notes.event118_anyPanelPartial = body118.toLowerCase().includes("partial");
  notes.event118_anySkipped = body118.toLowerCase().includes("skipped") || body118.toLowerCase().includes("market was never");
  notes.event118_anyPendingJudge = body118.toLowerCase().includes("pending judges");

  // === / — trigger button HTML ===
  await page.goto(`${UI}/`, { waitUntil: "domcontentloaded", timeout: 30000 }).catch(()=>{});
  await page.waitForLoadState("networkidle", { timeout: 15000 }).catch(()=>{});
  await new Promise(r => setTimeout(r, 2000));
  const btn = page.locator('button:has-text("Trigger live demo")').first();
  notes.trigger_btnHtmlBefore = await btn.evaluate(el => el.outerHTML).catch(()=> null);
  await btn.click().catch(e => notes.triggerErr = e.message);
  await new Promise(r => setTimeout(r, 500));
  notes.trigger_btnHtmlAt500 = await btn.evaluate(el => el.outerHTML).catch(()=> null);
  await new Promise(r => setTimeout(r, 1500));
  notes.trigger_btnHtmlAt2000 = await btn.evaluate(el => el.outerHTML).catch(()=> null);
  await new Promise(r => setTimeout(r, 2000));
  notes.trigger_btnHtmlAt4000 = await btn.evaluate(el => el.outerHTML).catch(()=> null);
  await new Promise(r => setTimeout(r, 3000));
  notes.trigger_btnHtmlAt7000 = await btn.evaluate(el => el.outerHTML).catch(()=> null);

  await browser.close();
  fs.writeFileSync("/tmp/w3-verify-v3.json", JSON.stringify(notes, null, 2));
  console.log(JSON.stringify(notes, null, 2));
}
main().catch(e => { console.error(e); process.exit(1); });
