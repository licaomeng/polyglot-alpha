// W7-A: Verify SSE `event.finalized` race fix for OWN-trigger autonav.
// Run: node ui/scripts/w7-a-verify.mjs
//
// Test plan:
//   1) Single-tab mock trigger — click Trigger and measure click→/events/{N}
//      navigation latency. MUST be <5000ms (was previously 120s fallback).
//   2) Concurrent two-tab — open two tabs, click Trigger in each within
//      ~1s, verify both navigate to their OWN event_id (no crosstalk).

import { chromium } from "playwright";
import { writeFileSync } from "node:fs";

const BASE_UI = "http://localhost:3001";
const TIMING_FILE = "/tmp/w7-a-timing.txt";

const log = (...a) => console.log("[w7-a]", ...a);

const launchTab = async (browser, label) => {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  page.on("console", (msg) => {
    if (msg.type() === "error") log(`[${label}:console.error]`, msg.text());
  });
  // Track the POST so we know which event_id this tab triggered.
  const triggerInfo = { eventId: null, postReturnedAt: null };
  page.on("response", async (resp) => {
    if (
      resp.url().includes("/trigger/event") &&
      resp.request().method() === "POST" &&
      resp.status() === 200
    ) {
      try {
        const body = await resp.json();
        if (body && body.event_id !== undefined && body.event_id !== null) {
          triggerInfo.eventId = String(body.event_id);
          triggerInfo.postReturnedAt = Date.now();
          log(`[${label}] POST returned event_id=${triggerInfo.eventId}`);
        } else {
          log(`[${label}] POST returned no event_id:`, JSON.stringify(body));
        }
      } catch (e) {
        log(`[${label}] POST body parse error:`, e.message);
      }
    }
  });
  return { ctx, page, triggerInfo };
};

const goAndClick = async (page, label) => {
  await page.goto(`${BASE_UI}/?mode=mock`, {
    waitUntil: "domcontentloaded",
    timeout: 30000,
  });
  await page.waitForLoadState("networkidle", { timeout: 30000 }).catch(() => {});
  // Wait for the trigger button to be ready. The SSR initial render shows
  // "Trigger live demo"; after client hydration applies mode=mock it flips
  // to "Trigger mock demo". Either label works as a click target — we just
  // need the button to exist and be visible.
  const btn = page.locator('button[aria-label*="Trigger"]').first();
  await btn.waitFor({ state: "visible", timeout: 30000 });
  log(`[${label}] page ready, clicking trigger`);
  const clickStart = Date.now();
  await btn.click();
  return clickStart;
};

const waitForEventNav = (page, clickStart, label) => {
  return page
    .waitForURL((url) => /\/events\/[^/]+$/.test(url.toString()) && !url.toString().endsWith("/events/"), {
      timeout: 30000,
    })
    .then(() => {
      const elapsed = Date.now() - clickStart;
      const finalUrl = page.url();
      log(`[${label}] navigated to ${finalUrl} in ${elapsed}ms`);
      return { ok: true, elapsed, url: finalUrl };
    })
    .catch((e) => {
      const elapsed = Date.now() - clickStart;
      log(`[${label}] nav-wait failed after ${elapsed}ms:`, e.message);
      return { ok: false, elapsed, url: page.url(), error: e.message };
    });
};

const browser = await chromium.launch({ headless: true });

// ─── Scenario 1: single-tab mock trigger ──────────────────────────────────
log("=== Scenario 1: single-tab mock trigger ===");
const tab1 = await launchTab(browser, "T1");
const t1ClickStart = await goAndClick(tab1.page, "T1");
const t1Result = await waitForEventNav(tab1.page, t1ClickStart, "T1");
const t1EventIdFromUrl = (t1Result.url.match(/\/events\/(\d+)/) || [])[1] || null;
log(`T1: triggered event=${tab1.triggerInfo.eventId} navigated-to=${t1EventIdFromUrl}`);
const t1OwnEventMatch = tab1.triggerInfo.eventId === t1EventIdFromUrl;

// ─── Scenario 2: concurrent two-tab ───────────────────────────────────────
log("=== Scenario 2: concurrent two-tab ===");
const tabA = await launchTab(browser, "TA");
const tabB = await launchTab(browser, "TB");
// Load both pages in parallel.
await Promise.all([
  tabA.page.goto(`${BASE_UI}/?mode=mock`, { waitUntil: "domcontentloaded", timeout: 30000 }),
  tabB.page.goto(`${BASE_UI}/?mode=mock`, { waitUntil: "domcontentloaded", timeout: 30000 }),
]);
await Promise.all([
  tabA.page.waitForLoadState("networkidle", { timeout: 30000 }).catch(() => {}),
  tabB.page.waitForLoadState("networkidle", { timeout: 30000 }).catch(() => {}),
]);
const btnA = tabA.page.locator('button[aria-label*="Trigger"]').first();
const btnB = tabB.page.locator('button[aria-label*="Trigger"]').first();
await btnA.waitFor({ state: "visible", timeout: 30000 });
await btnB.waitFor({ state: "visible", timeout: 30000 });
// Click both as close as possible to each other.
const concurrentStart = Date.now();
log("clicking both triggers concurrently");
await Promise.all([btnA.click(), btnB.click()]);
const [resultA, resultB] = await Promise.all([
  waitForEventNav(tabA.page, concurrentStart, "TA"),
  waitForEventNav(tabB.page, concurrentStart, "TB"),
]);
const tAEventIdFromUrl = (resultA.url.match(/\/events\/(\d+)/) || [])[1] || null;
const tBEventIdFromUrl = (resultB.url.match(/\/events\/(\d+)/) || [])[1] || null;
log(`TA: triggered=${tabA.triggerInfo.eventId} navigated-to=${tAEventIdFromUrl}`);
log(`TB: triggered=${tabB.triggerInfo.eventId} navigated-to=${tBEventIdFromUrl}`);
const tabAOwnMatch = tabA.triggerInfo.eventId === tAEventIdFromUrl;
const tabBOwnMatch = tabB.triggerInfo.eventId === tBEventIdFromUrl;
const noCrosstalk = tAEventIdFromUrl !== tBEventIdFromUrl;

await browser.close();

// ─── Build report ──────────────────────────────────────────────────────────
const PASS_THRESHOLD_MS = 5000;
const scenarios = [
  {
    name: "single-tab",
    elapsedMs: t1Result.elapsed,
    ok: t1Result.ok && t1Result.elapsed < PASS_THRESHOLD_MS && t1OwnEventMatch,
    ownMatch: t1OwnEventMatch,
    triggered: tab1.triggerInfo.eventId,
    landed: t1EventIdFromUrl,
  },
  {
    name: "concurrent-A",
    elapsedMs: resultA.elapsed,
    ok: resultA.ok && resultA.elapsed < PASS_THRESHOLD_MS && tabAOwnMatch,
    ownMatch: tabAOwnMatch,
    triggered: tabA.triggerInfo.eventId,
    landed: tAEventIdFromUrl,
  },
  {
    name: "concurrent-B",
    elapsedMs: resultB.elapsed,
    ok: resultB.ok && resultB.elapsed < PASS_THRESHOLD_MS && tabBOwnMatch,
    ownMatch: tabBOwnMatch,
    triggered: tabB.triggerInfo.eventId,
    landed: tBEventIdFromUrl,
  },
];
const allPass = scenarios.every((s) => s.ok) && noCrosstalk;

const lines = [
  `W7-A SSE-finalized race fix — verify run @ ${new Date().toISOString()}`,
  `PASS_THRESHOLD_MS = ${PASS_THRESHOLD_MS}`,
  ``,
  `Scenario 1 (single-tab mock):`,
  `  click→nav: ${t1Result.elapsed} ms   ${t1Result.elapsed < PASS_THRESHOLD_MS ? "PASS" : "FAIL"}`,
  `  triggered event_id: ${tab1.triggerInfo.eventId}`,
  `  landed event_id:    ${t1EventIdFromUrl}`,
  `  own-event match:    ${t1OwnEventMatch ? "YES" : "NO"}`,
  ``,
  `Scenario 2 (concurrent two-tab):`,
  `  tabA click→nav: ${resultA.elapsed} ms   ${resultA.elapsed < PASS_THRESHOLD_MS ? "PASS" : "FAIL"}`,
  `  tabA triggered=${tabA.triggerInfo.eventId}  landed=${tAEventIdFromUrl}  own-match=${tabAOwnMatch ? "YES" : "NO"}`,
  `  tabB click→nav: ${resultB.elapsed} ms   ${resultB.elapsed < PASS_THRESHOLD_MS ? "PASS" : "FAIL"}`,
  `  tabB triggered=${tabB.triggerInfo.eventId}  landed=${tBEventIdFromUrl}  own-match=${tabBOwnMatch ? "YES" : "NO"}`,
  `  no-crosstalk:   ${noCrosstalk ? "YES" : "NO"}`,
  ``,
  `OVERALL: ${allPass ? "PASS" : "FAIL"}`,
];
const report = lines.join("\n");
writeFileSync(TIMING_FILE, report + "\n");
log("\n" + report);
log(`wrote ${TIMING_FILE}`);

process.exit(allPass ? 0 : 1);
