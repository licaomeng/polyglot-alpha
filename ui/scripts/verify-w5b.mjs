// Manual verification flow for W5-B demo-mode toggle.
// Run: node scripts/verify-w5b.mjs
import { chromium } from "playwright";

const BASE = "http://localhost:3001";

const browser = await chromium.launch();
const ctx = await browser.newContext();
const page = await ctx.newPage();
const results = [];

async function getStorage() {
  return await page.evaluate(() => localStorage.getItem("polyglot:mode"));
}

async function getAriaChecked() {
  return await page
    .locator('[role="radio"][aria-checked="true"]')
    .first()
    .innerText()
    .catch(() => null);
}

// 1. Visit ?mode=mock — toggle should show MOCK, header amber, localStorage updated.
await page.goto(`${BASE}/?mode=mock`);
await page.waitForSelector('[role="radiogroup"][aria-label="Demo mode"]');
await page.waitForTimeout(300);
const checked1 = await getAriaChecked();
const storage1 = await getStorage();
results.push({ step: "1. ?mode=mock", checked: checked1, storage: storage1 });

// 2. Click LIVE — switches to live, localStorage updated.
await page.click('[role="radio"]:has-text("LIVE")');
await page.waitForTimeout(200);
const checked2 = await getAriaChecked();
const storage2 = await getStorage();
results.push({ step: "2. click LIVE", checked: checked2, storage: storage2 });

// 3. Reload without ?mode — should persist from localStorage (live).
await page.goto(`${BASE}/`);
await page.waitForTimeout(300);
const checked3 = await getAriaChecked();
const storage3 = await getStorage();
results.push({ step: "3. reload no-param", checked: checked3, storage: storage3 });

// 4. Visit ?mode=mock — switches to mock.
await page.goto(`${BASE}/?mode=mock`);
await page.waitForTimeout(300);
const checked4 = await getAriaChecked();
const storage4 = await getStorage();
results.push({ step: "4. ?mode=mock again", checked: checked4, storage: storage4 });

// 5. Reload to / — persists as mock.
await page.goto(`${BASE}/`);
await page.waitForTimeout(300);
const checked5 = await getAriaChecked();
const storage5 = await getStorage();
results.push({ step: "5. reload, persists from storage", checked: checked5, storage: storage5 });

// 6. URL should be clean (no ?mode=) after toggle click.
await page.click('[role="radio"]:has-text("LIVE")');
await page.waitForTimeout(200);
const url6 = page.url();
const checked6 = await getAriaChecked();
results.push({ step: "6. click LIVE, url clean", checked: checked6, url: url6 });

console.log(JSON.stringify(results, null, 2));
await browser.close();
