// W16-C production-mode UI probe.
// Visit each page; capture console errors / warnings (hydration mismatch etc),
// then measure click-to-render latency for the live trigger path.
//
// Runs against the production server at http://localhost:3001.

import { chromium } from "playwright";

const UI = "http://localhost:3001";
const PAGES = [
  { path: "/", name: "home" },
  { path: "/events", name: "events" },
  { path: "/events/252", name: "event-detail" },
  { path: "/operators", name: "operators" },
  { path: "/leaderboard", name: "leaderboard" },
  { path: "/about", name: "about" },
  { path: "/history", name: "history" },
];

const HYDRATION_PAT = /hydrat|did not match|server.*client|mismatch/i;

function attach(page) {
  const errors = [];
  const warnings = [];
  const hydration = [];
  page.on("console", (msg) => {
    const type = msg.type();
    const text = msg.text();
    if (type === "error") errors.push(text);
    else if (type === "warning") warnings.push(text);
    if (HYDRATION_PAT.test(text)) hydration.push(`[${type}] ${text}`);
  });
  page.on("pageerror", (err) => errors.push(`pageerror: ${err.message}`));
  return { errors, warnings, hydration };
}

async function main() {
  const browser = await chromium.launch({ headless: true });
  const ctx = await browser.newContext();
  const pageResults = [];

  for (const p of PAGES) {
    const page = await ctx.newPage();
    const log = attach(page);
    const t0 = Date.now();
    let status = null;
    try {
      // Pages with SSE keep network open — use domcontentloaded + a settle delay.
      const resp = await page.goto(UI + p.path, {
        waitUntil: "domcontentloaded",
        timeout: 30000,
      });
      status = resp ? resp.status() : null;
      await page.waitForTimeout(2500); // let mount effects + SSE handshake settle
    } catch (e) {
      log.errors.push(`goto failed: ${e.message}`);
    }
    const t1 = Date.now();
    pageResults.push({
      path: p.path,
      name: p.name,
      status,
      ms: t1 - t0,
      errors: log.errors,
      warnings: log.warnings,
      hydration: log.hydration,
    });
    await page.close();
  }

  // ---- Click-to-render latency on live mode ----
  const click = {};
  {
    const page = await ctx.newPage();
    const log = attach(page);
    // Use domcontentloaded — SSE keeps the network busy so 'networkidle' never fires.
    await page.goto(UI + "/?mode=mock", { waitUntil: "domcontentloaded" });
    // SSR renders "Trigger live demo"; mode context hydrates from URL and flips
    // to "Trigger mock demo". Use locator(button:has-text) for the visible one.
    const btn = page.locator('button:has-text("Trigger mock demo"), button:has-text("Trigger live demo")').first();
    await btn.waitFor({ state: "visible", timeout: 15000 });
    // Brief settle so the mode context hydrates and label flips to mock.
    await page.waitForTimeout(500);

    const clickT0 = Date.now();
    let urlChangedMs = null;
    let dagVisibleMs = null;

    page
      .waitForURL(/\/events\/[^/?#]+/, { timeout: 30000 })
      .then(() => {
        urlChangedMs = Date.now() - clickT0;
      })
      .catch(() => {});

    await btn.click({ noWaitAfter: true });

    // wait until URL changes
    try {
      await page.waitForURL(/\/events\/[^/?#]+/, { timeout: 30000 });
      urlChangedMs = urlChangedMs ?? Date.now() - clickT0;
    } catch {}

    // wait for DAG element from xyflow to be visible
    try {
      await page.locator(".react-flow, [data-testid='rf__wrapper'], .xyflow").first().waitFor({
        state: "visible",
        timeout: 30000,
      });
      dagVisibleMs = Date.now() - clickT0;
    } catch (e) {
      // fallback: look for any heading on event page
      try {
        await page.getByRole("heading").first().waitFor({ state: "visible", timeout: 5000 });
        dagVisibleMs = Date.now() - clickT0;
      } catch {}
    }

    click.urlChangedMs = urlChangedMs;
    click.dagVisibleMs = dagVisibleMs;
    click.errors = log.errors;
    click.warnings = log.warnings;
    click.hydration = log.hydration;
    click.finalUrl = page.url();
    await page.close();
  }

  await browser.close();

  // Print report
  console.log("\n=== Production-mode page checks ===");
  for (const r of pageResults) {
    console.log(
      `${r.path.padEnd(20)} status=${r.status} load=${r.ms}ms errors=${r.errors.length} hydration=${r.hydration.length}`,
    );
    if (r.errors.length) console.log("   errors:", r.errors.slice(0, 3));
    if (r.hydration.length) console.log("   hydration:", r.hydration.slice(0, 3));
  }

  console.log("\n=== Click-to-render (mock mode on production build) ===");
  console.log("urlChangedMs:", click.urlChangedMs);
  console.log("dagVisibleMs:", click.dagVisibleMs);
  console.log("finalUrl:", click.finalUrl);
  console.log("errors:", click.errors.length, "hydration:", click.hydration.length);
  if (click.errors.length) console.log("  errs:", click.errors.slice(0, 5));
  if (click.hydration.length) console.log("  hyd:", click.hydration.slice(0, 5));

  // summary JSON
  const summary = {
    pages: pageResults.map((r) => ({
      path: r.path,
      status: r.status,
      ms: r.ms,
      errorCount: r.errors.length,
      hydrationCount: r.hydration.length,
      firstError: r.errors[0] || null,
    })),
    click,
  };
  console.log("\nJSON:", JSON.stringify(summary, null, 2));
}

main().catch((e) => {
  console.error("FATAL:", e);
  process.exit(1);
});
