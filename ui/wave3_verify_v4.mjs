// Find every button labeled trigger/demo/run, click each, observe labels
import { chromium } from "playwright";
import fs from "node:fs";

const UI = "http://localhost:3001";
const SHOTS = "/Users/messili/codebase/polyglot-alpha/screenshots/wave3-regression";

async function main() {
  const notes = {};
  const browser = await chromium.launch({ headless: true });
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  page.on("console", m => { if (m.type() === "error") console.log("[browser err]", m.text()); });

  // Home page — find every trigger candidate
  await page.goto(`${UI}/`, { waitUntil: "domcontentloaded", timeout: 30000 }).catch(()=>{});
  await page.waitForLoadState("networkidle", { timeout: 15000 }).catch(()=>{});
  await new Promise(r => setTimeout(r, 2000));

  notes.allButtons = await page.evaluate(() => {
    const out = [];
    document.querySelectorAll("button").forEach((b, i) => {
      const r = b.getBoundingClientRect();
      out.push({
        i,
        text: (b.textContent || "").trim().slice(0, 100),
        aria: b.getAttribute("aria-label"),
        visible: r.width > 0 && r.height > 0,
        x: r.x, y: r.y, w: r.width, h: r.height,
        disabled: b.disabled,
      });
    });
    return out;
  });

  // Find specifically "Trigger live demo"
  const triggerBtns = page.locator('button:has-text("Trigger live demo")');
  notes.triggerCount = await triggerBtns.count();

  if (notes.triggerCount > 0) {
    const btn = triggerBtns.first();
    notes.triggerInitialBox = await btn.boundingBox().catch(()=>null);
    notes.triggerInitialText = await btn.textContent().catch(()=>null);
    // Scroll into view, screenshot it
    await btn.scrollIntoViewIfNeeded().catch(()=>{});
    await page.screenshot({ path: `${SHOTS}/wave3-r15a-trigger-before.png`, fullPage: false });

    // listen for the network call
    const reqs = [];
    page.on("request", r => {
      if (/trigger|demo|seed|simulate/i.test(r.url())) reqs.push({ url: r.url(), method: r.method() });
    });

    await btn.click().catch(e => notes.clickErr = e.message);
    // Sample for 12s every 250ms
    const samples = [];
    const t0 = Date.now();
    while (Date.now() - t0 < 12000) {
      const ts = Date.now() - t0;
      // The button might be replaced/disabled/relabeled
      const html = await page.evaluate(() => {
        const btns = [...document.querySelectorAll("button")];
        // find any button whose ariaLabel mentions trigger OR whose text mentions trigger|busy|fetch|bid|judge|anchor|submit|stream
        for (const b of btns) {
          const t = (b.textContent || "").trim();
          const a = b.getAttribute("aria-label") || "";
          if (/trigger|busy|fetch|bid|judge|anchor|submit|stream|wait|progress|running|live demo/i.test(t + " " + a)) {
            return { text: t, aria: a, disabled: b.disabled, busy: b.getAttribute("aria-busy") };
          }
        }
        return null;
      });
      samples.push({ ts, html });
      await new Promise(r => setTimeout(r, 400));
    }
    notes.triggerSamples = samples;
    notes.triggerRequests = reqs;
    await page.screenshot({ path: `${SHOTS}/wave3-r15b-trigger-after.png`, fullPage: false });
  }

  await browser.close();
  fs.writeFileSync("/tmp/w3-verify-v4.json", JSON.stringify(notes, null, 2));
  console.log(JSON.stringify(notes, null, 2));
}
main().catch(e => { console.error(e); process.exit(1); });
