import { chromium } from "playwright";

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await ctx.newPage();

page.on("request", (r) => console.log("REQ", r.url()));
page.on("requestfailed", (r) => console.log("FAIL", r.url(), r.failure()?.errorText));

await page.goto("http://localhost:3001/leaderboard");
console.log("--- nav done");
await page.waitForLoadState("domcontentloaded");
console.log("--- dom loaded");
console.log("URL:", page.url());
console.log("Title:", await page.title());
console.log("Body length:", (await page.content()).length);
await page.waitForTimeout(8000);
console.log("--- after 8s");
console.log("Total reqs above. Checking selectors...");
console.log("table?", await page.locator('table').count());
console.log("error?", await page.locator('text=/error|fail/i').count());
console.log("empty state?", await page.locator('text=/No agents yet/').count());

await browser.close();
