// W14-D screenshot capture: leaderboard + operator card showing wins/bids
// primary display + on-chain EMA relegated to "advanced" row.
//
// The script mocks the backend `/leaderboard` endpoint so we can capture a
// representative UI without standing up the FastAPI service. The fixture is
// deliberately shaped so the EMA bug (rep ≈ 0.5 for every agent) is visible
// alongside the more discriminative wins/bids ratio that now leads the UI.
//
// Run with: node scripts/w14d-screenshot.mjs
import { chromium } from "playwright";

const OUT = "screenshots/w14d";
const BASE = "http://localhost:3001";

const LEADERBOARD_FIXTURE = [
  {
    rank: 1,
    address: "0x396B8578a34517eb0A6968A1798703eD5c6D51f4",
    alias: "Gemini agent",
    reputation: 0.5012,
    revenueUsd: 142.38,
    winRate: 0.43,
    total_bids: 28,
    total_wins: 12,
    avg_quality: 0.88,
    cumulative_fees: 142.38,
  },
  {
    rank: 2,
    address: "0x5554a1Ce6C0085ca54A8b9f2E50b1D1548CDE7F6",
    alias: "Qwen agent",
    reputation: 0.4998,
    revenueUsd: 98.21,
    winRate: 0.32,
    total_bids: 22,
    total_wins: 7,
    avg_quality: 0.82,
    cumulative_fees: 98.21,
  },
  {
    rank: 3,
    address: "0x144ddfDb9129FA11F1041bF2349F6193f818Eb4A",
    alias: "DeepSeek agent",
    reputation: 0.5034,
    revenueUsd: 67.5,
    winRate: 0.24,
    total_bids: 17,
    total_wins: 4,
    avg_quality: 0.79,
    cumulative_fees: 67.5,
  },
];

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await ctx.newPage();

// Intercept the backend leaderboard endpoint regardless of port (api base is
// http://localhost:8000 by default; the dev server may also use a different
// runtime override). The route matcher is intentionally permissive.
await ctx.route("http://localhost:8000/leaderboard", async (route) => {
  await route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(LEADERBOARD_FIXTURE),
  });
});

// Stub /events so the operators page sidebar doesn't spam network errors.
await ctx.route(/http:\/\/localhost:8000\/events.*/, async (route) => {
  await route.fulfill({
    status: 200,
    contentType: "application/json",
    body: "[]",
  });
});

// Stub the operator pending-fees / stake-status probes invoked by the
// per-card ClaimFees / WithdrawStake buttons. Without a backend they would
// otherwise spam ERR_CONNECTION_REFUSED in the console.
await ctx.route(/\/api\/operators\/.*\/pending-fees/, async (route) => {
  await route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ pending_usdc: 0 }),
  });
});
await ctx.route(/\/api\/operators\/.*\/stake-status/, async (route) => {
  await route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({
      staked: true,
      withdrawable: false,
      amount_usdc: 100,
    }),
  });
});

const errors = [];
page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
page.on("console", (m) => {
  if (m.type() === "error") errors.push(`console.error: ${m.text()}`);
});
page.on("requestfailed", (r) => {
  // Surface unexpected failures (e.g. unmocked backend endpoints) but ignore
  // benign favicon misses.
  const u = r.url();
  if (!u.endsWith("/favicon.ico")) {
    errors.push(`requestfailed: ${u} ${r.failure()?.errorText}`);
  }
});

// /leaderboard
await page.goto(`${BASE}/leaderboard`, { waitUntil: "domcontentloaded" });
await page.waitForSelector('table[aria-label="Agent leaderboard"]', {
  timeout: 60000,
});
await page.waitForTimeout(800);
await page.screenshot({ path: `${OUT}/leaderboard.png`, fullPage: true });

// Hover the wins/bids info button so the tooltip renders in the screenshot.
// Use keyboard focus instead of hover for a deterministic open — the
// Tooltip primitive opens on `group-focus-within` too.
const infoBtn = page.locator('button[aria-label="Why wins / bids?"]').first();
await infoBtn.focus();
await page.waitForTimeout(400);
await page.screenshot({
  path: `${OUT}/leaderboard-tooltip.png`,
  fullPage: false,
});

// /operators — wait for the cards to populate from the mocked leaderboard.
await page.goto(`${BASE}/operators`, { waitUntil: "domcontentloaded" });
await page.waitForSelector('[data-testid="operator-wins-bids"]', {
  timeout: 60000,
});
await page.waitForTimeout(800);
await page.screenshot({ path: `${OUT}/operators.png`, fullPage: true });

await browser.close();

if (errors.length > 0) {
  console.error("Browser errors captured:");
  for (const e of errors) console.error("  -", e);
  process.exitCode = 1;
} else {
  console.log("OK — no console errors. Screenshots:", OUT);
}
