// W15-UI: real-browser Playwright debug of LIVE trigger user experience.
//
// Goal: simulate the user's click path, capture timings (click -> URL nav,
// SSE open), progressive labels, console errors, screenshots over time,
// and contrast against the mock path. Read-only diagnostic — do NOT modify
// any application code.
//
// Usage: from polyglot-alpha/ui:
//   node scripts/w15-ui-debug.mjs
//
// Outputs:
//   screenshots/w15-ui/00..NN.png
//   /tmp/w15-ui-findings.md

import { chromium } from "playwright";
import { promises as fs } from "fs";
import path from "path";

const UI = "http://localhost:3001";
// Node 18's undici has a localhost resolution quirk that makes fetch() to
// `http://localhost:8000` fail with "fetch failed"; use 127.0.0.1 directly.
const API = "http://127.0.0.1:8000";
const SHOT_DIR = path.resolve(
  path.dirname(new URL(import.meta.url).pathname),
  "..",
  "screenshots",
  "w15-ui",
);
const MANIFEST = "/tmp/w15-ui-findings.md";

const now = () => Date.now();
const ms = (t0) => `${(now() - t0).toString().padStart(6, " ")}ms`;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// Aggregated diagnostic record (rendered into the final markdown manifest).
const report = {
  startedAt: new Date().toISOString(),
  live: {
    clickT0: null,
    urlNavMs: null,
    sseOpenMs: null,
    eventId: null,
    progressiveLabels: [],
    consoleErrors: [],
    consoleWarns: [],
    httpErrors: [],
    finalStatus: null,
    finalReason: null,
    finalPhases: [],
    bidTxHashes: [],
    screenshots: [],
    timeline: [],
  },
  mock: {
    clickT0: null,
    urlNavMs: null,
    submittedMs: null,
    eventId: null,
    finalStatus: null,
    screenshots: [],
    consoleErrors: [],
  },
};

function pushTimeline(bucket, msg) {
  const line = `+${(now() - bucket.clickT0).toString().padStart(6, " ")}ms  ${msg}`;
  bucket.timeline.push(line);
  console.log(line);
}

async function attachListeners(page, bucket) {
  page.on("console", (msg) => {
    const type = msg.type();
    const text = msg.text();
    if (type === "error") bucket.consoleErrors.push(text);
    else if (type === "warning") bucket.consoleWarns?.push?.(text);
  });
  page.on("pageerror", (err) => {
    bucket.consoleErrors.push(`pageerror: ${err.message}`);
  });
  page.on("response", (resp) => {
    const s = resp.status();
    if (s >= 400) {
      bucket.httpErrors?.push?.(`${s} ${resp.url()}`);
    }
  });
  page.on("request", (req) => {
    const url = req.url();
    if (url.includes("/sse/events") && bucket.sseOpenMs === null && bucket.clickT0) {
      bucket.sseOpenMs = now() - bucket.clickT0;
      pushTimeline(bucket, `SSE request opened: ${url.slice(API.length)}`);
    }
  });
}

async function clearStateAndGoto(page, url) {
  // Visit a blank page first so we can safely clear storage for the origin.
  await page.goto(url, { waitUntil: "domcontentloaded" });
  await page.evaluate(() => {
    try {
      localStorage.clear();
      sessionStorage.clear();
    } catch {}
  });
  await page.reload({ waitUntil: "domcontentloaded" });
}

async function findTriggerButton(page, modeWord) {
  // Click handler is on the visible "Trigger live demo" / "Trigger mock demo"
  // button rendered by components/TriggerButton.tsx.
  const namePattern = new RegExp(`Trigger ${modeWord} demo`, "i");
  return page.getByRole("button", { name: namePattern });
}

async function captureLabelSamples(page, bucket, samples) {
  // Read the trigger button's current label so we can record the progressive
  // status messages while the lifecycle ticks.
  try {
    const text = (await page
      .locator('button:has-text("…"), button:has-text("Triggered"), button:has-text("Trigger")')
      .first()
      .textContent()) ?? "";
    const trimmed = text.trim().slice(0, 120);
    if (
      trimmed &&
      (samples.length === 0 || samples[samples.length - 1].label !== trimmed)
    ) {
      samples.push({ atMs: now() - bucket.clickT0, label: trimmed });
      pushTimeline(bucket, `LABEL: "${trimmed}"`);
    }
  } catch {}
}

async function shot(page, bucket, name) {
  const full = path.join(SHOT_DIR, name);
  await page.screenshot({ path: full, fullPage: false });
  bucket.screenshots.push(name);
  pushTimeline(bucket, `screenshot ${name}`);
}

async function fetchEventDetail(eventId) {
  try {
    const r = await fetch(`${API}/events/${eventId}`);
    if (!r.ok) return null;
    return await r.json();
  } catch (e) {
    return null;
  }
}

async function scrapeBidTxHashes(page) {
  // Find rows under Phase 2 (USDC Auction) that have a tx-hash-looking string.
  // The structure is unknown at runtime; fall back to a global regex sweep.
  try {
    const txes = await page.evaluate(() => {
      const hashRe = /0x(sim_[A-Za-z0-9_]+|[0-9a-fA-F]{64})/g;
      const text = document.body?.innerText ?? "";
      return Array.from(new Set(text.match(hashRe) ?? []));
    });
    return txes;
  } catch {
    return [];
  }
}

async function runLive(browser) {
  const bucket = report.live;
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  await attachListeners(page, bucket);

  console.log("\n=== LIVE RUN ===");
  await clearStateAndGoto(page, `${UI}/`);
  await page.waitForLoadState("networkidle").catch(() => {});

  // Wait explicitly for the trigger button to be present + enabled. The
  // first home-page load can take several seconds because Next.js dev mode
  // compiles the route lazily and the WorkflowOverview client component is
  // dynamically imported.
  const triggerBtn = page.getByRole("button", { name: /Trigger a live demo event/i });
  try {
    await triggerBtn.waitFor({ state: "visible", timeout: 30_000 });
  } catch {
    // Fall back to text-based locator if the aria-label changed.
  }

  await shot(page, bucket, "00-home-live.png");

  // Press the trigger.
  bucket.clickT0 = now();
  pushTimeline(bucket, "CLICK Trigger live demo");
  await triggerBtn.first().click({ timeout: 10_000 });

  // Poll URL + label every 250ms until navigation OR 30s timeout.
  const navTimeoutMs = 30_000;
  const navDeadline = now() + navTimeoutMs;
  let navigated = false;
  while (now() < navDeadline) {
    await captureLabelSamples(page, bucket, bucket.progressiveLabels);
    const url = page.url();
    if (/\/events\/(\d+)/.test(url)) {
      navigated = true;
      bucket.urlNavMs = now() - bucket.clickT0;
      bucket.eventId = url.match(/\/events\/(\d+)/)[1];
      pushTimeline(bucket, `URL nav -> /events/${bucket.eventId}`);
      break;
    }
    await sleep(250);
  }

  if (!navigated) {
    pushTimeline(bucket, `STUCK: no URL nav within ${navTimeoutMs}ms`);
    await shot(page, bucket, "01-stuck.png");
  } else {
    // Give the new route a moment to settle then snapshot.
    await page.waitForLoadState("domcontentloaded").catch(() => {});
    await sleep(500);
    await shot(page, bucket, "01-after-click.png");
  }

  // Watch phase progression for up to 180s, screenshot every 30s.
  if (navigated) {
    const intervals = [30, 60, 90, 120, 150, 180];
    const baseT = now();
    for (let i = 0; i < intervals.length; i++) {
      const targetMs = intervals[i] * 1000;
      while (now() - baseT < targetMs) {
        await sleep(500);
      }
      const fname = `0${i + 2}-${intervals[i]}s.png`;
      await shot(page, bucket, fname);

      // Scrape DOM for error banners we care about.
      const errText = await page.evaluate(() => {
        const t = document.body?.innerText ?? "";
        const out = [];
        if (/RSS unreachable/i.test(t)) out.push("RSS unreachable");
        if (/all_seeders_low_gas/i.test(t)) out.push("all_seeders_low_gas");
        if (/FAILED/i.test(t)) out.push("FAILED-banner");
        return out;
      });
      if (errText.length) pushTimeline(bucket, `DOM markers: ${errText.join(", ")}`);

      // Check terminal status via API; bail out early if final.
      const detail = await fetchEventDetail(bucket.eventId);
      if (detail) {
        const st = String(detail.status || "").toUpperCase();
        pushTimeline(bucket, `API status: ${st}`);
        if (["SUBMITTED", "REJECTED", "FAILED"].includes(st)) {
          bucket.finalStatus = st;
          bucket.finalReason = detail.failure_reason ?? detail.reason ?? null;
          bucket.finalPhases = (detail.phases ?? []).map((p) => ({
            name: p.name,
            status: p.status,
          }));
          break;
        }
      }
    }

    // Final detail snapshot.
    if (!bucket.finalStatus && bucket.eventId) {
      const detail = await fetchEventDetail(bucket.eventId);
      if (detail) {
        bucket.finalStatus = String(detail.status ?? "");
        bucket.finalReason = detail.failure_reason ?? detail.reason ?? null;
        bucket.finalPhases = (detail.phases ?? []).map((p) => ({
          name: p.name,
          status: p.status,
        }));
      }
    }

    // Try to expand the Phase 2 (USDC Auction) accordion before scraping so
    // its bid rows are present in the DOM. The accordion buttons are rendered
    // by EventTimeline; we try common selectors and fall back to clicking any
    // header whose text contains "USDC Auction".
    try {
      const auctionToggle = page.getByRole("button", { name: /USDC Auction|Phase 2/i }).first();
      if ((await auctionToggle.count()) > 0) {
        await auctionToggle.click({ timeout: 3_000 }).catch(() => {});
        await sleep(500);
        await shot(page, bucket, "98-phase2-expanded.png");
      }
    } catch {}

    // Scrape bid tx hashes from the rendered DOM.
    bucket.bidTxHashes = await scrapeBidTxHashes(page);
    pushTimeline(bucket, `tx hashes found in DOM: ${JSON.stringify(bucket.bidTxHashes)}`);

    await shot(page, bucket, "99-final.png");
  }

  await ctx.close();
}

async function runMock(browser) {
  const bucket = report.mock;
  bucket.timeline = [];
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();

  page.on("console", (msg) => {
    if (msg.type() === "error") bucket.consoleErrors.push(msg.text());
  });

  console.log("\n=== MOCK RUN ===");
  await clearStateAndGoto(page, `${UI}/?mode=mock`);
  await page.waitForLoadState("networkidle").catch(() => {});

  // Mode badge should pick up mock from ?mode=mock query.
  await page.waitForTimeout(800);
  const triggerBtn = await findTriggerButton(page, "mock");
  // Fallback: any "Trigger" button.
  const btn = (await triggerBtn.count()) > 0 ? triggerBtn.first() : page.getByRole("button", { name: /Trigger/i }).first();

  await page.screenshot({ path: path.join(SHOT_DIR, "10-home-mock.png") });
  bucket.screenshots.push("10-home-mock.png");

  bucket.clickT0 = now();
  pushTimeline(bucket, "CLICK Trigger mock demo");
  await btn.click();

  // Wait for URL nav (mock should be <5s).
  const navDeadline = now() + 30_000;
  while (now() < navDeadline) {
    const url = page.url();
    if (/\/events\/(\d+)/.test(url)) {
      bucket.urlNavMs = now() - bucket.clickT0;
      bucket.eventId = url.match(/\/events\/(\d+)/)[1];
      pushTimeline(bucket, `URL nav -> /events/${bucket.eventId}`);
      break;
    }
    await sleep(200);
  }

  await page.screenshot({ path: path.join(SHOT_DIR, "11-mock-after-nav.png") });
  bucket.screenshots.push("11-mock-after-nav.png");

  // Wait for backend status SUBMITTED, max 30s.
  const stDeadline = now() + 30_000;
  while (now() < stDeadline) {
    const d = bucket.eventId ? await fetchEventDetail(bucket.eventId) : null;
    if (d && String(d.status).toUpperCase() === "SUBMITTED") {
      bucket.submittedMs = now() - bucket.clickT0;
      bucket.finalStatus = "SUBMITTED";
      pushTimeline(bucket, `mock SUBMITTED at ${bucket.submittedMs}ms`);
      break;
    }
    await sleep(500);
  }

  await page.screenshot({ path: path.join(SHOT_DIR, "12-mock-final.png") });
  bucket.screenshots.push("12-mock-final.png");

  await ctx.close();
}

async function writeManifest() {
  const lines = [];
  lines.push(`# W15-UI Findings`);
  lines.push(`Started: ${report.startedAt}`);
  lines.push(``);

  lines.push(`## LIVE`);
  const L = report.live;
  lines.push(`- event_id: \`${L.eventId ?? "n/a"}\``);
  lines.push(`- click → URL nav: **${L.urlNavMs ?? "(no nav within 30s)"} ms**`);
  lines.push(`- SSE /sse/events?event_id=… opened: ${L.sseOpenMs ?? "(never)"} ms after click`);
  lines.push(`- final status: \`${L.finalStatus ?? "(unknown)"}\``);
  if (L.finalReason) lines.push(`- final reason: \`${L.finalReason}\``);
  if (L.finalPhases?.length) {
    lines.push(`- phases:`);
    L.finalPhases.forEach((p) => lines.push(`  - ${p.name}: ${p.status}`));
  }
  lines.push(`- bid tx hashes scraped from Phase 2 DOM: ${JSON.stringify(L.bidTxHashes)}`);
  lines.push(``);
  lines.push(`### Progressive labels (LIVE)`);
  if (L.progressiveLabels.length === 0) lines.push(`_(no label changes observed before nav)_`);
  L.progressiveLabels.forEach((s) =>
    lines.push(`- +${String(s.atMs).padStart(5, " ")}ms — "${s.label}"`),
  );
  lines.push(``);
  lines.push(`### Console errors (LIVE)`);
  if (L.consoleErrors.length === 0) lines.push(`_(none)_`);
  L.consoleErrors.slice(0, 30).forEach((e) => lines.push(`- ${e.slice(0, 220)}`));
  lines.push(``);
  lines.push(`### Console warnings (LIVE)`);
  if (L.consoleWarns.length === 0) lines.push(`_(none)_`);
  L.consoleWarns.slice(0, 20).forEach((e) => lines.push(`- ${e.slice(0, 220)}`));
  lines.push(``);
  lines.push(`### HTTP 4xx/5xx (LIVE)`);
  if (L.httpErrors.length === 0) lines.push(`_(none)_`);
  L.httpErrors.slice(0, 20).forEach((e) => lines.push(`- ${e}`));
  lines.push(``);
  lines.push(`### Timeline (LIVE)`);
  L.timeline.forEach((t) => lines.push(`    ${t}`));
  lines.push(``);

  lines.push(`## MOCK (control)`);
  const M = report.mock;
  lines.push(`- event_id: \`${M.eventId ?? "n/a"}\``);
  lines.push(`- click → URL nav: **${M.urlNavMs ?? "(no nav)"} ms**`);
  lines.push(`- click → SUBMITTED: **${M.submittedMs ?? "(timeout)"} ms**`);
  lines.push(`- final status: \`${M.finalStatus ?? "(unknown)"}\``);
  lines.push(`- console errors:`);
  if (M.consoleErrors.length === 0) lines.push(`  _(none)_`);
  M.consoleErrors.slice(0, 10).forEach((e) => lines.push(`  - ${e.slice(0, 220)}`));
  lines.push(``);
  lines.push(`### Timeline (MOCK)`);
  M.timeline?.forEach?.((t) => lines.push(`    ${t}`));
  lines.push(``);

  lines.push(`## Screenshots`);
  L.screenshots.forEach((s) => lines.push(`- live: \`screenshots/w15-ui/${s}\``));
  M.screenshots.forEach((s) => lines.push(`- mock: \`screenshots/w15-ui/${s}\``));

  await fs.writeFile(MANIFEST, lines.join("\n"), "utf8");
  console.log(`\nmanifest written: ${MANIFEST}`);
}

async function main() {
  await fs.mkdir(SHOT_DIR, { recursive: true });
  const browser = await chromium.launch({ headless: true });
  try {
    await runLive(browser);
  } catch (e) {
    console.error("live run error:", e);
    report.live.consoleErrors.push(`SCRIPT EXC: ${e?.message ?? e}`);
  }
  try {
    await runMock(browser);
  } catch (e) {
    console.error("mock run error:", e);
    report.mock.consoleErrors.push(`SCRIPT EXC: ${e?.message ?? e}`);
  }
  await browser.close();
  await writeManifest();
}

await main();
