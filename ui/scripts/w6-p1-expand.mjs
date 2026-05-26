// Expand phase panels and check details
import { chromium } from "playwright";
import { writeFileSync, mkdirSync } from "node:fs";

mkdirSync("/Users/messili/codebase/polyglot-alpha/screenshots/wave6-p1", { recursive: true });

const browser = await chromium.launch({ headless: true });
const ctx = await browser.newContext({ viewport: { width: 1920, height: 1080 } });
const page = await ctx.newPage();
await page.goto("http://localhost:3001/events/144?mode=mock", { waitUntil: "domcontentloaded" });
await page.waitForTimeout(2500);

// Find all "INPUTS · OUTPUTS · DIAGRAM" tab/button rows; click on OUTPUTS for phase 5 and phase 7
const tabsInfo = await page.evaluate(() => {
  const all = Array.from(document.querySelectorAll('button, [role="tab"], [role="button"]'));
  const inputs = all.filter((b) => /^inputs$/i.test((b.textContent || "").trim()));
  const outputs = all.filter((b) => /^outputs$/i.test((b.textContent || "").trim()));
  const diagram = all.filter((b) => /^diagram$/i.test((b.textContent || "").trim()));
  return { inputs: inputs.length, outputs: outputs.length, diagram: diagram.length };
});
console.log("tab counts:", JSON.stringify(tabsInfo));

// Try clicking each OUTPUTS button in turn, capturing text
const phaseTexts = {};
for (let i = 0; i < 7; i++) {
  const clicked = await page.evaluate((idx) => {
    const outputs = Array.from(document.querySelectorAll('button, [role="tab"], [role="button"]')).filter(
      (b) => /^outputs$/i.test((b.textContent || "").trim())
    );
    if (!outputs[idx]) return false;
    outputs[idx].scrollIntoView({ block: "center" });
    outputs[idx].click();
    return true;
  }, i);
  if (!clicked) continue;
  await page.waitForTimeout(400);
}
await page.waitForTimeout(800);
await page.screenshot({ path: "/Users/messili/codebase/polyglot-alpha/screenshots/wave6-p1/07-all-outputs-expanded.png", fullPage: true });

const fullBodyAfter = await page.evaluate(() => document.body.innerText);

// Phase 2 — bids & arcscan after expand
const phase2After = await page.evaluate(() => {
  const links = Array.from(document.querySelectorAll('a[href*="arcscan"]')).map((a) => ({
    href: a.href,
    text: a.textContent?.slice(0, 80),
    rect: a.getBoundingClientRect(),
  }));
  // Find phase 2 panel and pull inner text from outputs region
  return { arcscanLinks: links.length, linksSample: links.slice(0, 5) };
});
console.log("phase2 arcscan post-expand:", JSON.stringify(phase2After).slice(0, 600));

// Phase 5 — search for ipfs in full body after expansion
const phase5After = await page.evaluate(() => {
  const text = document.body.innerText;
  const hasIpfs = /ipfs:\/\//i.test(text);
  const ipfsMatches = [...text.matchAll(/ipfs:\/\/[^\s]+/gi)].map((m) => m[0]);
  return { hasIpfs, ipfsMatches: ipfsMatches.slice(0, 10) };
});
console.log("phase5 after expand:", JSON.stringify(phase5After));

// Phase 7 — Streaming Revenue scope
const phase7After = await page.evaluate(() => {
  const heads = Array.from(document.querySelectorAll("h1, h2, h3, h4"));
  const target = heads.find((h) => /streaming revenue/i.test(h.textContent || ""));
  if (!target) return { found: false };
  let scope = target;
  for (let i = 0; i < 10; i++) {
    if (scope.parentElement) scope = scope.parentElement;
    if (scope.tagName === "SECTION" || scope.tagName === "ARTICLE") break;
  }
  return { found: true, scopedText: scope.innerText.slice(0, 5000) };
});
console.log("phase7 after expand text (first 2000):", phase7After.scopedText?.slice(0, 2000));

// Check for "Mock mode: panel.evaluate short-circuited" exact phrase
const exactPhrase = fullBodyAfter.toLowerCase().includes("panel.evaluate short-circuit");
const synthetic = fullBodyAfter.toLowerCase().includes("synthetic");
console.log("exact phrase 'panel.evaluate short-circuited':", exactPhrase);
console.log("'synthetic' appears in body:", synthetic);

// Check for "Partial:" banner
const partialIdx = fullBodyAfter.toLowerCase().indexOf("partial");
console.log("partial text around index", partialIdx, ":", partialIdx >= 0 ? fullBodyAfter.slice(partialIdx, partialIdx + 200) : "n/a");

// Phase 8 reputation (mock should not show updates)
const reputationSlice = (() => {
  const idx = fullBodyAfter.toLowerCase().indexOf("reputation");
  return idx >= 0 ? fullBodyAfter.slice(idx, idx + 2000) : null;
})();
console.log("reputation slice:", reputationSlice);

// Check arcscan-leak: is the <a href> text actually the sim hash, AND is it actually a clickable real link?
const arcscanAnalysis = await page.evaluate(() => {
  const links = Array.from(document.querySelectorAll('a[href*="arcscan"]'));
  return links.map((a) => {
    return {
      href: a.href,
      text: (a.textContent || "").slice(0, 100),
      isSimHash: /0xsim_/i.test(a.textContent || "") || /0xsim_/i.test(a.href),
      target: a.target,
      role: a.getAttribute("role"),
    };
  });
});
console.log("arcscan link analysis:", JSON.stringify(arcscanAnalysis, null, 2));

await browser.close();
