// W6-P2 Playwright deep-dive: live mode regression + toggle/URL UX.
// Run: node scripts/w6-p2.mjs
//
// Exhaustively tests:
//   1. Default mode = live
//   2. URL ?mode= precedence
//   3. Toggle click keeps URL clean
//   4. localStorage persistence
//   5. Live trigger lifecycle
//   6. Mode badge on event detail (per-event, independent of toggle)
//   7. Sticky header across 5 pages
//   8. Keyboard navigation on toggle
//   9. console.errors / network 4xx-5xx telemetry
import { chromium } from "playwright";
import { mkdirSync, writeFileSync } from "node:fs";

const BASE = "http://localhost:3001";
const SHOT_DIR = "/Users/messili/codebase/polyglot-alpha/ui/screenshots/w6-p2";
mkdirSync(SHOT_DIR, { recursive: true });

const findings = [];
const consoleErrors = [];
const consoleWarnings = [];
const networkBad = [];

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
const page = await ctx.newPage();

page.on("console", (msg) => {
  const type = msg.type();
  if (type === "error") {
    consoleErrors.push({ text: msg.text(), url: page.url() });
  } else if (type === "warning") {
    consoleWarnings.push({ text: msg.text(), url: page.url() });
  }
});
page.on("response", (resp) => {
  const s = resp.status();
  if (s >= 400 && s !== 429) {
    networkBad.push({ status: s, url: resp.url(), page: page.url() });
  }
});

function shot(name) {
  const p = `${SHOT_DIR}/${name}.png`;
  return page.screenshot({ path: p, fullPage: false }).then(() => p);
}
function shotFull(name) {
  const p = `${SHOT_DIR}/${name}.png`;
  return page.screenshot({ path: p, fullPage: true }).then(() => p);
}

async function getStorage() {
  return await page.evaluate(() => localStorage.getItem("polyglot:mode"));
}
// Wait for hydration to settle — the SSR markup has live as default; the
// client first-effect cycle may flip to mock from URL/storage. We wait until
// the header's data-mode attribute matches the expected mode, otherwise we
// read a transient pre-hydration value.
async function waitForMode(expected, timeout = 5000) {
  try {
    await page.waitForFunction(
      (m) => document.querySelector(`header[data-mode="${m}"]`) !== null,
      expected,
      { timeout },
    );
    return true;
  } catch {
    return false;
  }
}
async function checkedLabel() {
  // Read the aria-checked button via inner attribute, not text — text can be
  // stale between renders on the same node.
  return await page.evaluate(() => {
    const btns = Array.from(document.querySelectorAll('[role="radio"]'));
    const hit = btns.find((b) => b.getAttribute("aria-checked") === "true");
    return hit ? hit.textContent?.trim() : null;
  });
}
async function headerDataMode() {
  return await page.locator("header[data-mode]").first().getAttribute("data-mode");
}
async function headerHasZap() {
  // Zap icon is the live icon; FlaskConical is mock. Read presence via the
  // lucide SVG class name.
  return await page.locator("header svg.lucide-zap").count();
}
async function headerHasFlask() {
  return await page.locator("header svg.lucide-flask-conical").count();
}

function record(level, title, where, expected, actual, screenshot, hypothesis) {
  findings.push({ level, title, where, expected, actual, screenshot, hypothesis });
}

// ────────────────────────────────────────────────────────────────────────────
// Step 1: default mode on '/'  -> live
// ────────────────────────────────────────────────────────────────────────────
{
  await page.goto(BASE, { waitUntil: "domcontentloaded" });
  await page.waitForSelector('[role="radiogroup"][aria-label="Demo mode"]');
  // Clear storage to test true default
  await page.evaluate(() => localStorage.removeItem("polyglot:mode"));
  await page.reload({ waitUntil: "domcontentloaded" });
  await page.waitForSelector('[role="radiogroup"][aria-label="Demo mode"]');
  await waitForMode("live");
  await page.waitForTimeout(400);
  const checked = await checkedLabel();
  const mode = await headerDataMode();
  const zap = await headerHasZap();
  const flask = await headerHasFlask();
  const trigText = await page.locator('button[aria-label="Trigger a live demo event"]').innerText();
  const liveBadge = await page.locator("header").locator('text=/^live$/i').count();
  const path = await shot("step1-default-live");

  const okAll =
    checked === "LIVE" &&
    mode === "live" &&
    zap >= 1 &&
    flask === 0 &&
    /Trigger live demo/i.test(trigText);
  if (!okAll) {
    record(
      "HIGH",
      "Default mode not live on fresh load",
      `${BASE}/  (step 1)`,
      "toggle=LIVE, header data-mode=live, Zap icon present, FlaskConical absent, trigger text 'Trigger live demo'",
      `toggle=${checked}, data-mode=${mode}, zap=${zap}, flask=${flask}, trigger='${trigText}', liveBadge=${liveBadge}`,
      path,
      "ModeContext default fallback or storage clear not applied"
    );
  }
}

// ────────────────────────────────────────────────────────────────────────────
// Step 2: URL precedence — ?mode=mock should switch toggle + header
// ────────────────────────────────────────────────────────────────────────────
let urlPrecedencePass = false;
{
  await page.goto(`${BASE}/?mode=mock`, { waitUntil: "domcontentloaded" });
  await page.waitForSelector('[role="radiogroup"][aria-label="Demo mode"]');
  await waitForMode("mock");
  await page.waitForTimeout(300);
  const checked = await checkedLabel();
  const mode = await headerDataMode();
  const flask = await headerHasFlask();
  const storage = await getStorage();
  const url = page.url();
  const path = await shot("step2-url-mode-mock");

  urlPrecedencePass =
    checked === "MOCK" && mode === "mock" && flask >= 1 && storage === "mock" && /\?mode=mock/.test(url);
  if (!urlPrecedencePass) {
    record(
      "HIGH",
      "URL ?mode=mock did not override toggle/header/storage",
      `${BASE}/?mode=mock  (step 2)`,
      "toggle=MOCK, header data-mode=mock, FlaskConical icon, localStorage polyglot:mode=mock, URL retains ?mode=mock",
      `toggle=${checked}, data-mode=${mode}, flask=${flask}, storage=${storage}, url=${url}`,
      path,
      "ModeProvider URL useEffect not firing or readInitialMode mis-parsing"
    );
  }
}

// ────────────────────────────────────────────────────────────────────────────
// Step 3: Toggle click on live keeps URL clean
// ────────────────────────────────────────────────────────────────────────────
let toggleClickPass = false;
{
  await page.goto(`${BASE}/?mode=live`, { waitUntil: "domcontentloaded" });
  await page.waitForSelector('[role="radiogroup"][aria-label="Demo mode"]');
  await waitForMode("live");
  await page.waitForTimeout(300);
  // sanity check we started live
  const before = await checkedLabel();
  await page.locator('[role="radio"]:has-text("MOCK")').click();
  await page.waitForTimeout(700);
  const after = await checkedLabel();
  const mode = await headerDataMode();
  const flask = await headerHasFlask();
  const storage = await getStorage();
  const url = page.url();
  const path = await shot("step3-toggle-click-mock");

  toggleClickPass =
    before === "LIVE" &&
    after === "MOCK" &&
    mode === "mock" &&
    flask >= 1 &&
    storage === "mock" &&
    /\?mode=live/.test(url); // URL stays as it was
  if (!toggleClickPass) {
    record(
      "HIGH",
      "Toggle click did not update visuals OR mutated URL",
      `${BASE}/?mode=live then click MOCK (step 3)`,
      "before=LIVE, after=MOCK, header amber, storage=mock, URL stays ?mode=live",
      `before=${before}, after=${after}, data-mode=${mode}, flask=${flask}, storage=${storage}, url=${url}`,
      path,
      "Toggle handler is writing URL OR URL effect overrides storage write"
    );
  }
}

// ────────────────────────────────────────────────────────────────────────────
// Step 4: localStorage persistence — toggle to mock, refresh without ?mode=
// ────────────────────────────────────────────────────────────────────────────
let storagePersistPass = false;
{
  // start clean
  await page.goto(BASE, { waitUntil: "domcontentloaded" });
  await page.evaluate(() => localStorage.removeItem("polyglot:mode"));
  await page.reload({ waitUntil: "domcontentloaded" });
  await page.waitForSelector('[role="radiogroup"][aria-label="Demo mode"]');
  await waitForMode("live");
  await page.waitForTimeout(400); // hydration
  // toggle to mock
  await page.locator('[role="radio"]:has-text("MOCK")').click();
  await waitForMode("mock");
  await page.waitForTimeout(400);
  const beforePath = await shot("step4-before-refresh");
  const storageBefore = await getStorage();
  // refresh
  await page.goto(BASE, { waitUntil: "domcontentloaded" });
  await page.waitForSelector('[role="radiogroup"][aria-label="Demo mode"]');
  await page.waitForTimeout(400);
  const checked = await checkedLabel();
  const mode = await headerDataMode();
  const storage = await getStorage();
  const afterPath = await shot("step4-after-refresh");

  storagePersistPass = storageBefore === "mock" && checked === "MOCK" && mode === "mock" && storage === "mock";
  if (!storagePersistPass) {
    record(
      "HIGH",
      "localStorage persistence not restored on refresh",
      `${BASE}/ refresh (step 4)`,
      "before storage=mock, after refresh: toggle=MOCK, data-mode=mock, storage=mock",
      `storageBefore=${storageBefore}, checked=${checked}, data-mode=${mode}, storage=${storage}`,
      `${beforePath} | ${afterPath}`,
      "readInitialMode not reading localStorage OR hydration mismatch"
    );
  }
}

// ────────────────────────────────────────────────────────────────────────────
// Step 3b: re-verify toggle click DOES work when URL has no ?mode= param.
// ────────────────────────────────────────────────────────────────────────────
let toggleClickCleanUrlPass = false;
{
  await page.goto(BASE, { waitUntil: "domcontentloaded" });
  await page.evaluate(() => localStorage.removeItem("polyglot:mode"));
  await page.reload({ waitUntil: "domcontentloaded" });
  await page.waitForSelector('[role="radiogroup"][aria-label="Demo mode"]');
  await waitForMode("live");
  await page.waitForTimeout(400);
  const before = await checkedLabel();
  await page.locator('[role="radio"]:has-text("MOCK")').click();
  await waitForMode("mock");
  await page.waitForTimeout(400);
  const after = await checkedLabel();
  const mode = await headerDataMode();
  const storage = await getStorage();
  const url = page.url();
  const path = await shot("step3b-toggle-click-clean-url");

  toggleClickCleanUrlPass =
    before === "LIVE" &&
    after === "MOCK" &&
    mode === "mock" &&
    storage === "mock" &&
    !/\?mode=/.test(url);
  if (!toggleClickCleanUrlPass) {
    record(
      "HIGH",
      "Toggle click failed on clean URL (no ?mode= param)",
      `${BASE}/ click MOCK (step 3b)`,
      "before=LIVE, after=MOCK, header amber, storage=mock, URL clean",
      `before=${before}, after=${after}, data-mode=${mode}, storage=${storage}, url=${url}`,
      path,
      "click handler not invoking setMode or React not hydrated"
    );
  }
}

// ────────────────────────────────────────────────────────────────────────────
// Step 5: Live trigger
// ────────────────────────────────────────────────────────────────────────────
let liveResult = "skipped";
let liveEventId = null;
{
  // switch to live
  await page.goto(`${BASE}/?mode=live`, { waitUntil: "domcontentloaded" });
  await page.waitForSelector('[role="radiogroup"][aria-label="Demo mode"]');
  await page.waitForTimeout(400);
  const checked = await checkedLabel();
  if (checked !== "LIVE") {
    record(
      "MEDIUM",
      "Could not switch to LIVE before live-trigger step",
      `${BASE}/?mode=live (step 5)`,
      "toggle=LIVE",
      `toggle=${checked}`,
      "n/a",
      "URL precedence broken — see step 2 finding"
    );
  }

  // Capture event id by listening to the POST /trigger/event response.
  const triggerRespP = page.waitForResponse(
    (r) => r.url().includes("/trigger/event") && r.request().method() === "POST",
    { timeout: 20_000 },
  ).catch(() => null);

  await page.locator('button[aria-label="Trigger a live demo event"]').click();
  const triggerResp = await triggerRespP;
  if (triggerResp) {
    try {
      const body = await triggerResp.json();
      liveEventId = body?.event_id ? String(body.event_id) : null;
    } catch {
      /* ignore */
    }
  }
  // Wait up to 3 min for finalization. The TriggerButton navigates to
  // /events/{id} on finalize, so we wait for that route OR a cap.
  const start = Date.now();
  const cap = 3 * 60 * 1000;
  let navigatedToDetail = false;
  while (Date.now() - start < cap) {
    if (/\/events\/\d+/.test(page.url())) {
      navigatedToDetail = true;
      break;
    }
    await page.waitForTimeout(2000);
    // Poll backend for terminal state — that lets us detect FAILED without
    // waiting for the SSE to drive a redirect.
    if (liveEventId) {
      try {
        const resp = await fetch(`http://localhost:8000/events/${liveEventId}`);
        if (resp.ok) {
          const ev = await resp.json();
          if (["SUBMITTED", "FAILED", "REJECTED"].includes(ev.status)) {
            // Give SSE a moment to redirect; if it doesn't, we navigate ourselves.
            await page.waitForTimeout(4000);
            if (!/\/events\/\d+/.test(page.url())) {
              await page.goto(`${BASE}/events/${liveEventId}`, { waitUntil: "domcontentloaded" });
            }
            break;
          }
        }
      } catch {
        /* ignore */
      }
    }
  }
  await page.waitForTimeout(1500);

  // Diagnose status
  let status = null, reason = null;
  if (liveEventId) {
    try {
      const r = await fetch(`http://localhost:8000/events/${liveEventId}`);
      if (r.ok) {
        const ev = await r.json();
        status = ev.status;
        for (const p of ev.phases || []) {
          if (p.status === "failed" && p.details?.reason) {
            reason = p.details.reason;
            break;
          }
        }
      }
    } catch {
      /* ignore */
    }
  }

  if (status === "FAILED" && reason === "all_seeders_low_gas") {
    liveResult = "FAILED-low-gas";
  } else if (status === "FAILED") {
    liveResult = `FAILED-other(${reason ?? "no reason captured"})`;
  } else if (status === "REJECTED") {
    liveResult = "REJECTED";
  } else if (status === "SUBMITTED") {
    liveResult = "SUBMITTED";
  } else {
    liveResult = `TIMEOUT(status=${status})`;
  }

  // Try to capture the amber low-gas panel if reason matches.
  if (liveEventId) {
    if (!/\/events\/\d+/.test(page.url())) {
      await page.goto(`${BASE}/events/${liveEventId}`, { waitUntil: "domcontentloaded" });
    }
    await page.waitForTimeout(1500);
  }
  const path = await shotFull("step5-live-trigger-result");

  if (liveResult.startsWith("TIMEOUT")) {
    record(
      "MEDIUM",
      "Live trigger did not finalize within 5min cap",
      `${BASE}/ trigger live (step 5)`,
      "lifecycle finalizes SUBMITTED or FAILED inside 5min",
      `eventId=${liveEventId}, terminal status=${status}, result=${liveResult}`,
      path,
      "Backend lifecycle stalled — seeder gas, RPC, or judge panel timeout"
    );
  } else if (liveResult === "FAILED-low-gas") {
    // verify the amber panel actually rendered
    const panel = page.locator("text=/all 3 reference seeders.*out of gas/i").first();
    const visible = await panel.isVisible().catch(() => false);
    if (!visible) {
      record(
        "MEDIUM",
        "Backend reports all_seeders_low_gas but amber panel not rendered",
        `${BASE}/events/${liveEventId} (step 5)`,
        "amber 'All 3 reference seeders out of gas' panel visible on phase detail",
        "panel not visible in DOM",
        path,
        "PhaseDetailsAccordion not matching reason key OR phase collapsed"
      );
    }
  } else if (liveResult.startsWith("FAILED-other") || liveResult === "REJECTED") {
    record(
      "MEDIUM",
      "Live trigger failed for non-gas reason — diagnostic only",
      `${BASE}/events/${liveEventId} (step 5)`,
      "either SUBMITTED success or FAILED-low-gas with amber panel",
      `status=${status}, reason=${reason}, result=${liveResult}`,
      path,
      "Non-gas failure path may need its own UI surfacing"
    );
  }
}

// ────────────────────────────────────────────────────────────────────────────
// Step 6: Mode persists on event detail page (per-event badge vs toggle pref)
// ────────────────────────────────────────────────────────────────────────────
{
  // Find latest live and latest mock from backend.
  let liveId = liveEventId;
  let mockId = null;
  try {
    const r = await fetch("http://localhost:8000/events?limit=50");
    const arr = await r.json();
    if (!liveId) {
      const x = arr.find((e) => e.mode === "live");
      if (x) liveId = String(x.id);
    }
    const m = arr.find((e) => e.mode === "mock");
    if (m) mockId = String(m.id);
  } catch {
    /* ignore */
  }

  // We'll set toggle to MOCK and visit the LIVE event page — badge should
  // still read "Live" because it's a per-event property.
  await page.goto(BASE, { waitUntil: "domcontentloaded" });
  await page.evaluate(() => localStorage.setItem("polyglot:mode", "mock"));
  await page.reload({ waitUntil: "domcontentloaded" });
  await page.waitForTimeout(300);

  let liveBadgeReadsLive = null;
  let liveDetailPath = null;
  if (liveId) {
    try {
      await page.goto(`${BASE}/events/${liveId}`, { waitUntil: "domcontentloaded", timeout: 30000 });
      await page.waitForTimeout(2000);
      liveBadgeReadsLive = await page
        .locator('[aria-label="Live data"]')
        .count();
      liveDetailPath = await shotFull("step6-event-detail-live");
    } catch (e) {
      record("MEDIUM", "Step 6 live event navigation failed", `${BASE}/events/${liveId}`, "page loads", e.message, "n/a", "navigation timeout");
    }
  }

  let mockBadgeReadsMock = null;
  let mockDetailPath = null;
  if (mockId) {
    try {
      await page.evaluate(() => localStorage.setItem("polyglot:mode", "live"));
      await page.goto(`${BASE}/events/${mockId}`, { waitUntil: "domcontentloaded", timeout: 30000 });
      await page.waitForTimeout(2000);
      mockBadgeReadsMock = await page
        .locator('[aria-label="Mock data"]')
        .count();
      mockDetailPath = await shotFull("step6-event-detail-mock");
    } catch (e) {
      record("MEDIUM", "Step 6 mock event navigation failed", `${BASE}/events/${mockId}`, "page loads", e.message, "n/a", "navigation timeout");
    }
  }

  if (liveId && liveBadgeReadsLive === 0) {
    record(
      "HIGH",
      "Live event detail shows wrong (or missing) mode badge",
      `${BASE}/events/${liveId} (step 6)`,
      "RealVsMockBadge 'Live' aria-label present",
      `Live aria-label count=${liveBadgeReadsLive}`,
      liveDetailPath,
      "Event detail using toggle mode instead of event.mode"
    );
  }
  if (mockId && mockBadgeReadsMock === 0) {
    record(
      "HIGH",
      "Mock event detail shows wrong (or missing) mode badge",
      `${BASE}/events/${mockId} (step 6)`,
      "RealVsMockBadge 'Mock' aria-label present",
      `Mock aria-label count=${mockBadgeReadsMock}`,
      mockDetailPath,
      "Event detail using toggle mode instead of event.mode"
    );
  }
}

// ────────────────────────────────────────────────────────────────────────────
// Step 7: Sticky header verification across pages
// ────────────────────────────────────────────────────────────────────────────
const pagesToTest = [
  { name: "home", path: "/" },
  { name: "events", path: "/events" },
  { name: "leaderboard", path: "/leaderboard" },
  { name: "about", path: "/about" },
];
// Add event detail if we have an id
if (liveEventId) pagesToTest.push({ name: "event-detail", path: `/events/${liveEventId}` });
const stickyFails = [];
// Use a short viewport so every page can scroll; long content not needed.
await page.setViewportSize({ width: 1280, height: 400 });
for (const t of pagesToTest) {
  await page.goto(`${BASE}${t.path}`, { waitUntil: "domcontentloaded" });
  await page.waitForSelector("header");
  await page.waitForTimeout(600);
  const beforeBox = await page.locator("header").first().boundingBox();
  // Inject a tall spacer so we can always scroll past the header.
  await page.evaluate(() => {
    const spacer = document.createElement("div");
    spacer.style.height = "2000px";
    spacer.id = "__w6p2_spacer";
    document.body.appendChild(spacer);
  });
  await page.evaluate(() => window.scrollTo(0, 800));
  await page.waitForTimeout(400);
  const box = await page.locator("header").first().boundingBox();
  const scrollY = await page.evaluate(() => window.scrollY);
  // Clean up spacer
  await page.evaluate(() => document.getElementById("__w6p2_spacer")?.remove());
  const stuck = box && box.y >= 0 && box.y < 10 && scrollY > 50;
  const path = await shot(`step7-sticky-${t.name}`);
  if (!stuck) {
    stickyFails.push(t.name);
    record(
      "MEDIUM",
      `Sticky header not pinned on ${t.name}`,
      `${BASE}${t.path} (step 7)`,
      "header bounding box y≈0 while scrollY > 50",
      `header.y=${box?.y}, scrollY=${scrollY}, headerBeforeY=${beforeBox?.y}`,
      path,
      "ancestor overflow rule clipping sticky positioning"
    );
  }
}
// Restore viewport for remaining steps
await page.setViewportSize({ width: 1280, height: 900 });

// ────────────────────────────────────────────────────────────────────────────
// Step 8: Keyboard navigation on toggle
// ────────────────────────────────────────────────────────────────────────────
let kbPass = false;
{
  await page.goto(BASE, { waitUntil: "domcontentloaded" });
  await page.evaluate(() => localStorage.setItem("polyglot:mode", "live"));
  await page.reload({ waitUntil: "domcontentloaded" });
  await page.waitForSelector('[role="radiogroup"][aria-label="Demo mode"]');
  await waitForMode("live");
  await page.waitForTimeout(300);

  // Focus the active radio
  await page.locator('[role="radio"][aria-checked="true"]').first().focus();
  await page.waitForTimeout(150);
  // Press ArrowRight — should switch to MOCK
  await page.keyboard.press("ArrowRight");
  await waitForMode("mock");
  await page.waitForTimeout(200);
  const after1 = await checkedLabel();
  // Press ArrowLeft — should switch back to LIVE
  await page.keyboard.press("ArrowLeft");
  await waitForMode("live");
  await page.waitForTimeout(200);
  const after2 = await checkedLabel();
  // Press Space on MOCK
  await page.keyboard.press("ArrowRight");
  await waitForMode("mock");
  await page.waitForTimeout(150);
  await page.keyboard.press(" ");
  await page.waitForTimeout(300);
  const after3 = await checkedLabel();
  // verify aria-checked accurately tracks
  const mockChecked = await page
    .locator('[role="radio"]:has-text("MOCK")')
    .getAttribute("aria-checked");
  const liveChecked = await page
    .locator('[role="radio"]:has-text("LIVE")')
    .getAttribute("aria-checked");
  const path = await shot("step8-keyboard-nav");

  kbPass = after1 === "MOCK" && after2 === "LIVE" && after3 === "MOCK" && mockChecked === "true" && liveChecked === "false";
  if (!kbPass) {
    record(
      "MEDIUM",
      "Keyboard navigation on toggle did not behave per W3C radiogroup pattern",
      `${BASE}/ toggle (step 8)`,
      "ArrowRight: LIVE→MOCK, ArrowLeft: MOCK→LIVE, Space: activate; aria-checked updates",
      `after ArrowRight=${after1}, after ArrowLeft=${after2}, after Space=${after3}, mock aria-checked=${mockChecked}, live aria-checked=${liveChecked}`,
      path,
      "handleKey not switching focus + selection, or aria-checked stale"
    );
  }
}

// ────────────────────────────────────────────────────────────────────────────
// Done — emit summary
// ────────────────────────────────────────────────────────────────────────────
await browser.close();

const verdict = findings.some((f) => f.level === "CRITICAL")
  ? "failed"
  : findings.length > 0
  ? "has-issues"
  : "clean";

const summary = {
  verdict,
  urlPrecedence: urlPrecedencePass ? "PASS" : "FAIL",
  toggleClick: toggleClickPass ? "PASS" : "FAIL",
  storagePersist: storagePersistPass ? "PASS" : "FAIL",
  liveResult,
  stickyFailures: stickyFails,
  keyboardNav: kbPass ? "PASS" : "FAIL",
  consoleErrors: consoleErrors.length,
  consoleWarnings: consoleWarnings.length,
  networkBad: networkBad.length,
  liveEventId,
  findings,
  networkBadList: networkBad,
  consoleErrorList: consoleErrors.slice(0, 15),
};

writeFileSync(`${SHOT_DIR}/summary.json`, JSON.stringify(summary, null, 2));
console.log(JSON.stringify(summary, null, 2));
