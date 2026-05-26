// W6-P1 deep-dive: verify specific findings from first pass.
import { chromium } from "playwright";
import { writeFileSync } from "node:fs";

const BASE = "http://localhost:3001";
const browser = await chromium.launch({ headless: true });
const ctx = await browser.newContext({ viewport: { width: 1920, height: 1080 } });
const page = await ctx.newPage();

const captured = { trigger: null, sseFinalized: null, sseEvents: [] };

page.on("request", (req) => {
  if (req.url().includes("/events/trigger") && req.method() === "POST") {
    captured.trigger = { method: req.method(), url: req.url(), body: req.postData(), headers: req.headers() };
  }
});

// Use Existing event 144 (last mock test) to inspect renderings without trigger
await page.goto(`${BASE}/events/144?mode=mock`, { waitUntil: "domcontentloaded" });
await page.waitForLoadState("load", { timeout: 15000 }).catch(() => {});
await page.waitForTimeout(2500);

// === Phase 2 deeper: bids, arcscan, winner
const phase2 = await page.evaluate(() => {
  // Look at phase 2 panel by heading
  const heads = Array.from(document.querySelectorAll("h2, h3, h4, [data-phase], [data-phase-id]"));
  const target = heads.find((h) => /USDC Auction/i.test(h.textContent || ""));
  let scope = document.body;
  if (target) {
    // ascend to its panel container
    let c = target;
    for (let i = 0; i < 8; i++) {
      if (c.parentElement) c = c.parentElement;
      if (c.tagName === "SECTION" || c.tagName === "ARTICLE" || c.getAttribute("data-phase")) break;
    }
    scope = c;
  }
  const scopedText = scope.innerText.slice(0, 6000);
  // arcscan links anywhere in doc
  const links = Array.from(document.querySelectorAll('a[href*="arcscan"]')).map((a) => ({
    href: a.href,
    text: a.textContent,
    classes: a.className,
  }));
  // also find spans with 0xsim_ prefix and check if they are inside <a>
  const simTxEls = Array.from(document.querySelectorAll("*")).filter(
    (el) => el.children.length === 0 && /0xsim_/i.test(el.textContent || "")
  );
  const simTxInfo = simTxEls.slice(0, 15).map((el) => ({
    text: (el.textContent || "").slice(0, 80),
    tag: el.tagName,
    insideLink: !!el.closest("a"),
    closestLinkHref: el.closest("a")?.href || null,
  }));
  // Bid count heuristic: look for bidder rows
  const bidElements = Array.from(document.querySelectorAll('[data-bid], [class*="bid"]')).map(
    (e) => ({ tag: e.tagName, text: (e.textContent || "").slice(0, 80), cls: e.className })
  );
  return { scopedText, links, simTxInfo, bidElements: bidElements.slice(0, 30), foundUSDCHeader: !!target };
});
// === Phase 5 deeper: ipfs
const phase5 = await page.evaluate(() => {
  const ipfsEls = Array.from(document.querySelectorAll("*")).filter(
    (el) => el.children.length === 0 && /ipfs:\/\//i.test(el.textContent || "")
  );
  const links = Array.from(document.querySelectorAll('a[href*="ipfs"]')).map((a) => ({ href: a.href, text: a.textContent }));
  return {
    ipfsEls: ipfsEls.slice(0, 10).map((e) => ({
      text: (e.textContent || "").slice(0, 100),
      tag: e.tagName,
      insideLink: !!e.closest("a"),
    })),
    links,
  };
});
// === Phase 7 deeper: Streaming Revenue
const phase7 = await page.evaluate(() => {
  const heads = Array.from(document.querySelectorAll("h1, h2, h3, h4"));
  const target = heads.find((h) => /streaming|revenue|disburse|payout/i.test(h.textContent || ""));
  if (!target) {
    return { found: false, allHeaders: heads.map((h) => h.textContent?.slice(0, 80)) };
  }
  let scope = target;
  for (let i = 0; i < 8; i++) {
    if (scope.parentElement) scope = scope.parentElement;
    if (scope.tagName === "SECTION" || scope.tagName === "ARTICLE") break;
  }
  return { found: true, header: target.textContent, scopedText: scope.innerText.slice(0, 4000) };
});
// === Phase 4 deeper: mock short-circuit reason
const phase4 = await page.evaluate(() => {
  const text = document.body.innerText;
  const lower = text.toLowerCase();
  const idx = lower.indexOf("panel");
  const reasons = [];
  if (idx >= 0) reasons.push(text.slice(idx, idx + 400));
  // any text with "short-circuit" or "synthetic"
  const allEls = Array.from(document.querySelectorAll("*")).filter(
    (e) => e.children.length === 0 && /short[- ]circuit|synthetic|mock mode/i.test(e.textContent || "")
  );
  const reasonEls = allEls.slice(0, 10).map((e) => ({
    text: (e.textContent || "").slice(0, 200),
    tag: e.tagName,
  }));
  return { reasonsSlice: reasons.slice(0, 3), reasonEls };
});
// === DAG nodes: find them by any pattern
const dag = await page.evaluate(() => {
  const allClasses = new Set();
  const cs = Array.from(document.querySelectorAll("svg circle, svg rect, svg g")).slice(0, 100);
  cs.forEach((el) => allClasses.add(el.className.baseVal || el.getAttribute("class") || ""));
  // search for elements with data-* attributes hinting node id
  const dataAttrs = Array.from(document.querySelectorAll("[data-node-id], [data-phase-id], [data-step]")).slice(0, 30).map((e) => ({
    tag: e.tagName,
    attrs: Array.from(e.attributes).map((a) => `${a.name}=${a.value.slice(0,40)}`).join(" "),
  }));
  // text around "11 graph nodes"
  const text = document.body.innerText;
  const nodesIdx = text.toLowerCase().indexOf("11 graph nodes");
  const nodeSlice = nodesIdx >= 0 ? text.slice(nodesIdx, nodesIdx + 300) : null;
  return {
    svgClassSamples: Array.from(allClasses).slice(0, 40),
    dataAttrs,
    nodeSlice,
  };
});

writeFileSync("/tmp/w6-p1-deep.json", JSON.stringify({ captured, phase2, phase4, phase5, phase7, dag }, null, 2));
console.log("phase2 found USDC header:", phase2.foundUSDCHeader);
console.log("phase2 arcscan links:", phase2.links.length);
console.log("phase2 simTx sample:", JSON.stringify(phase2.simTxInfo.slice(0, 5)));
console.log("phase4 reasons:", JSON.stringify(phase4.reasonEls.slice(0, 3)));
console.log("phase5 ipfsEls:", JSON.stringify(phase5.ipfsEls.slice(0, 3)));
console.log("phase5 ipfs links:", phase5.links.length);
console.log("phase7 found:", phase7.found, "header:", phase7.header);
if (phase7.found) console.log("phase7 scoped text:", phase7.scopedText.slice(0, 1200));
console.log("dag node slice:", dag.nodeSlice);
console.log("dag dataAttrs sample:", JSON.stringify(dag.dataAttrs.slice(0, 8)));
console.log("dag svg class samples:", JSON.stringify(dag.svgClassSamples.slice(0, 10)));

await browser.close();
