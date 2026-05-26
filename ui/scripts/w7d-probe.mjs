// W7-D probe: verify the two UX fixes on event 202 (mock).
//  1. Phase 4 dossier reasons contain the full "Mock mode: panel.evaluate
//     short-circuited" wording.
//  2. Phase 7 (Streaming Revenue) shows the
//     "(mock — not recorded to reputation)" hint.
import { chromium } from "playwright";

const URL = "http://localhost:3010/events/208";
const PHRASE_JUDGE = /Mock mode: panel\.evaluate short-circuited with synthetic PASS/;
const PHRASE_REP = /\(mock — not recorded to reputation\)/;

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 1800 } });
page.on("console", (msg) => {
  if (msg.type() === "error") console.log("[browser-err]", msg.text());
});
await page.goto(URL, { waitUntil: "domcontentloaded", timeout: 60_000 });
// Wait for the React app to hydrate + populate the judge dossier list.
await page.waitForSelector('[data-testid="judge-panel-dossier"], h2', {
  timeout: 30_000,
});
await page.waitForTimeout(2000);
// Try expanding all phase detail accordions ("inputs · outputs · diagram")
// — they own the per-judge dossier rows.
const accordionButtons = await page
  .locator('button:has-text("inputs · outputs · diagram")')
  .all();
for (const btn of accordionButtons) {
  try {
    await btn.scrollIntoViewIfNeeded({ timeout: 1500 });
    await btn.click({ timeout: 1500 });
  } catch {
    /* ignore */
  }
}
await page.waitForTimeout(1500);

const text = await page.evaluate(() => document.body.innerText);
const judgeMatch = PHRASE_JUDGE.test(text);
const repMatch = PHRASE_REP.test(text);

// Sample a judge dossier reason row to confirm the new wording.
const sampleJudgeReason = await page.evaluate(() => {
  const el = document.querySelector('[data-testid="judge-row-bleu-reason"]');
  return el ? el.textContent : null;
});
const repHintEl = await page.evaluate(() => {
  const el = document.querySelector('[data-testid="revenue-mock-rep-hint"]');
  return el ? el.textContent : null;
});

console.log(JSON.stringify({
  url: URL,
  judgePhraseFound: judgeMatch,
  repPhraseFound: repMatch,
  sampleJudgeReason,
  repHintEl,
}, null, 2));

await browser.close();
process.exit(judgeMatch && repMatch ? 0 : 1);
