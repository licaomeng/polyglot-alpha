// Quick screenshot capture for the W5-B demo-mode toggle (LIVE vs MOCK).
// Run with: node scripts/screenshot-w5b.mjs
import { chromium } from "playwright";

const OUT = "screenshots/w5b";
const BASE = "http://localhost:3001";

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1440, height: 800 } });
const page = await ctx.newPage();

// LIVE
await page.goto(`${BASE}/?mode=live`);
await page.waitForSelector('[role="radiogroup"][aria-label="Demo mode"]');
await page.waitForTimeout(400);
await page.screenshot({ path: `${OUT}/header-live.png`, fullPage: false, clip: { x: 0, y: 0, width: 1440, height: 80 } });

// MOCK
await page.goto(`${BASE}/?mode=mock`);
await page.waitForSelector('[role="radiogroup"][aria-label="Demo mode"]');
await page.waitForTimeout(400);
await page.screenshot({ path: `${OUT}/header-mock.png`, fullPage: false, clip: { x: 0, y: 0, width: 1440, height: 80 } });

// Full-page for context
await page.goto(`${BASE}/?mode=mock`);
await page.waitForTimeout(500);
await page.screenshot({ path: `${OUT}/home-mock.png`, fullPage: false });
await page.goto(`${BASE}/?mode=live`);
await page.waitForTimeout(500);
await page.screenshot({ path: `${OUT}/home-live.png`, fullPage: false });

await browser.close();
console.log("Done:", OUT);
