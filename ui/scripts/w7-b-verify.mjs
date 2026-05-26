// W7-B verify: gate ALL sim-prefix hashes against being wrapped in external
// explorer links.
//
// Probe walks every <a> tag on the mock event detail page and counts links
// that leak synthetic values into clickable hrefs:
//
//   1. 0xsim_…           (mock Arc tx hashes)
//   2. polymarket.com/market/{sim-,dryrun-}…  (mock Polymarket market_ids)
//   3. ipfs.io/ipfs/sim…  (synthetic IPFS gateway URLs)
//   4. ipfs://           (raw IPFS scheme should never appear as a href)
//
// All four counts MUST be 0. Run from /Users/messili/codebase/polyglot-alpha:
//
//     node ui/scripts/w7-b-verify.mjs
//
// Expects UI on http://localhost:3001 and API on http://localhost:8000.

import { chromium } from "playwright";

const BASE_UI = process.env.UI_BASE ?? "http://localhost:3001";
const BASE_API = process.env.API_BASE ?? "http://localhost:8000";

const log = (...a) => console.log("[w7-b]", ...a);
const fail = (msg) => {
  console.error("[w7-b][FAIL]", msg);
  process.exitCode = 1;
};

// Pick a mock event with synthetic anchor + polymarket data. Event 202 is the
// canonical W5-A2 fixture; allow override via $W7B_EVENT_ID.
const pickEventId = async () => {
  if (process.env.W7B_EVENT_ID) return process.env.W7B_EVENT_ID;
  const res = await fetch(`${BASE_API}/events`);
  const data = await res.json();
  // Prefer the highest-id event (most recent), fall back to first.
  if (Array.isArray(data) && data.length > 0) return data[0].id;
  throw new Error("no events available from API");
};

const eventId = await pickEventId();
log(`probing event ${eventId} on ${BASE_UI}`);

const browser = await chromium.launch({ headless: true });
const ctx = await browser.newContext({ viewport: { width: 1440, height: 2400 } });
const page = await ctx.newPage();

try {
  // Use `domcontentloaded` rather than `networkidle` because the event page
  // keeps a long-poll SSE stream open and would otherwise never go idle.
  await page.goto(`${BASE_UI}/events/${eventId}`, { waitUntil: "domcontentloaded" });
  // Give React time to hydrate and render the phase accordions.
  await page.waitForTimeout(2500);

  // Expand every progressive-disclosure accordion so phase-details links are
  // rendered into the DOM. Click "View details" / chevron-toggle buttons that
  // are collapsed (`aria-expanded="false"`).
  const toggles = await page.locator('button[aria-expanded="false"]').all();
  log(`expanding ${toggles.length} accordion sections`);
  for (const t of toggles) {
    try {
      await t.click({ timeout: 1000 });
    } catch {
      // best-effort; some toggles may be off-screen / disabled
    }
  }
  await page.waitForTimeout(500);

  // Pull every href on the page.
  const hrefs = await page.$$eval("a[href]", (els) =>
    els.map((e) => e.getAttribute("href") ?? ""),
  );
  log(`scanned ${hrefs.length} <a href> tags`);

  const leaks = {
    sim_tx: hrefs.filter((h) => h.includes("0xsim_")),
    sim_polymarket: hrefs.filter(
      (h) =>
        /polymarket\.com\/market\/(sim-|dryrun-)/i.test(h),
    ),
    sim_ipfs_gateway: hrefs.filter((h) =>
      /ipfs\.io\/ipfs\/sim/i.test(h),
    ),
    raw_ipfs_scheme: hrefs.filter((h) => h.startsWith("ipfs://")),
  };

  const report = {
    event_id: eventId,
    total_hrefs: hrefs.length,
    sim_leaks: {
      arcscan_0xsim: leaks.sim_tx.length,
      polymarket_sim_or_dryrun: leaks.sim_polymarket.length,
      ipfs_gateway_sim: leaks.sim_ipfs_gateway.length,
      raw_ipfs_scheme: leaks.raw_ipfs_scheme.length,
    },
    sample_leaks: {
      sim_tx: leaks.sim_tx.slice(0, 3),
      sim_polymarket: leaks.sim_polymarket.slice(0, 3),
      sim_ipfs_gateway: leaks.sim_ipfs_gateway.slice(0, 3),
      raw_ipfs_scheme: leaks.raw_ipfs_scheme.slice(0, 3),
    },
  };
  console.log(JSON.stringify(report, null, 2));

  for (const [k, arr] of Object.entries(leaks)) {
    if (arr.length > 0) {
      fail(`${k} leak: ${arr.length} link(s) (sample: ${arr[0]})`);
    }
  }
  if (!process.exitCode) log("PASS — no sim-prefix link leaks");
} finally {
  await browser.close();
}
