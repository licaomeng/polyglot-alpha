// Run only chromium (firefox+webkit already captured). Merges into the same
// outputs JSON written by cross_browser_test.js.
const fs = require("fs");
const path = require("path");

process.env.SKIP_TRIGGER = process.env.SKIP_TRIGGER || "0";
const OUT_JSON = path.resolve(__dirname, "..", "..", "outputs", "cross_browser_iter_1.json");
const existing = JSON.parse(fs.readFileSync(OUT_JSON, "utf8"));

// Re-invoke the same logic from cross_browser_test.js but force chromium only
// by monkey-patching ENGINES. Simpler: copy the relevant chunks here.
const { chromium } = require("playwright");

const BASE_URL = process.env.BASE_URL || "http://localhost:3001";
const EVENT_ID = process.env.EVENT_ID || "114";
const OUT_DIR = path.resolve(__dirname, "..", "..", "outputs");
const SHOT_DIR = path.join(OUT_DIR, "screenshots");
fs.mkdirSync(SHOT_DIR, { recursive: true });

const ROUTES = [
  { name: "home", path: "/" },
  { name: "events", path: "/events" },
  { name: `events-${EVENT_ID}`, path: `/events/${EVENT_ID}` },
  { name: "leaderboard", path: "/leaderboard" },
  { name: "about", path: "/about" },
];
const VIEWPORTS = [
  { name: "mobile", width: 375, height: 812 },
  { name: "tablet", width: 768, height: 1024 },
  { name: "desktop", width: 1280, height: 800 },
];

async function readPaintTimings(page) {
  return page.evaluate(() => {
    const result = { FCP: null, LCP: null };
    try {
      const paints = performance.getEntriesByType("paint");
      const fcp = paints.find((p) => p.name === "first-contentful-paint");
      if (fcp) result.FCP = Math.round(fcp.startTime);
    } catch (_) {}
    return new Promise((resolve) => {
      let lcpValue = result.LCP;
      try {
        const po = new PerformanceObserver((list) => {
          for (const entry of list.getEntries()) lcpValue = Math.round(entry.startTime);
        });
        po.observe({ type: "largest-contentful-paint", buffered: true });
        setTimeout(() => { try { po.disconnect(); } catch (_) {} resolve({ FCP: result.FCP, LCP: lcpValue }); }, 600);
      } catch (_) { resolve(result); }
    });
  });
}

async function checkCssApplied(page) {
  return page.evaluate(() => {
    const bg = getComputedStyle(document.body).backgroundColor;
    const m = bg.match(/(\d+),\s*(\d+),\s*(\d+)/);
    const sum = m ? Number(m[1]) + Number(m[2]) + Number(m[3]) : 0;
    return { bg, looksStyled: sum > 0 && sum < 600 };
  });
}

async function testRoute(browser, route, viewport) {
  const ctx = await browser.newContext({ viewport: { width: viewport.width, height: viewport.height }, colorScheme: "dark" });
  const page = await ctx.newPage();
  const consoleErrors = []; const networkFailures = [];
  page.on("console", (m) => m.type() === "error" && consoleErrors.push(m.text()));
  page.on("response", (r) => r.status() >= 400 && networkFailures.push({ url: r.url(), status: r.status() }));
  page.on("pageerror", (e) => consoleErrors.push(`pageerror: ${e.message}`));
  const url = `${BASE_URL}${route.path}`;
  const startedAt = Date.now();
  let pageError = null;
  try {
    await page.goto(url, { waitUntil: "domcontentloaded", timeout: 30000 });
    await page.waitForLoadState("networkidle", { timeout: 15000 }).catch(() => {});
  } catch (err) { pageError = err.message; }
  const loadMs = Date.now() - startedAt;
  let cssCheck = null, timings = { FCP: null, LCP: null }, title = null;
  if (!pageError) {
    cssCheck = await checkCssApplied(page);
    timings = await readPaintTimings(page);
    title = await page.title();
  }
  const shotFile = path.join(SHOT_DIR, `xbrowser_chromium_${route.name}_${viewport.name}.png`);
  try { await page.screenshot({ path: shotFile, fullPage: false }); } catch (_) {}
  await ctx.close();
  return {
    browser: "chromium", route: route.name, path: route.path, viewport: viewport.name,
    loadMs, pageError, consoleErrorCount: consoleErrors.length, consoleErrors: consoleErrors.slice(0, 5),
    networkFailureCount: networkFailures.length, networkFailures: networkFailures.slice(0, 5),
    cssApplied: cssCheck?.looksStyled ?? null, bodyBg: cssCheck?.bg ?? null, title,
    FCP: timings.FCP, LCP: timings.LCP, screenshot: path.relative(OUT_DIR, shotFile),
  };
}

async function testTriggerFlow(browser) {
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 }, colorScheme: "dark" });
  const page = await ctx.newPage();
  const consoleErrors = [];
  page.on("console", (m) => m.type() === "error" && consoleErrors.push(m.text()));
  page.on("pageerror", (e) => consoleErrors.push(`pageerror: ${e.message}`));
  const out = { browser: "chromium", triggered: false, urlChanged: false, spinnerSeen: false, finalUrl: null, error: null, consoleErrors: [] };
  try {
    await page.goto(`${BASE_URL}/`, { waitUntil: "domcontentloaded", timeout: 30000 });
    await page.waitForLoadState("networkidle", { timeout: 10000 }).catch(() => {});
    const btn = page.getByRole("button", { name: /trigger.*live demo/i });
    await btn.waitFor({ state: "visible", timeout: 10000 });
    const startUrl = page.url();
    await btn.click();
    out.triggered = true;
    try { await page.waitForSelector(".animate-spin", { timeout: 1500 }); out.spinnerSeen = true; } catch (_) {}
    const deadline = Date.now() + 90000;
    while (Date.now() < deadline) {
      const cur = page.url();
      if (cur !== startUrl && /\/events\//.test(cur)) { out.urlChanged = true; out.finalUrl = cur; break; }
      await page.waitForTimeout(1000);
    }
    const shotFile = path.join(SHOT_DIR, `xbrowser_chromium_trigger_final.png`);
    await page.screenshot({ path: shotFile, fullPage: false });
    out.screenshot = path.relative(OUT_DIR, shotFile);
  } catch (err) { out.error = err.message; }
  finally { out.consoleErrors = consoleErrors.slice(0, 5); await ctx.close(); }
  return out;
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  const engineResult = { available: true, routeViewport: [], triggerFlow: null };
  for (const route of ROUTES) {
    const r = await testRoute(browser, route, VIEWPORTS[2]);
    console.log(`  [desktop] ${route.name.padEnd(16)} load=${r.loadMs}ms FCP=${r.FCP} LCP=${r.LCP} err=${r.consoleErrorCount}`);
    engineResult.routeViewport.push(r);
  }
  for (const vp of [VIEWPORTS[0], VIEWPORTS[1]]) {
    const r = await testRoute(browser, ROUTES[0], vp);
    console.log(`  [${vp.name}] home load=${r.loadMs}ms FCP=${r.FCP}`);
    engineResult.routeViewport.push(r);
  }
  if (process.env.SKIP_TRIGGER !== "1") {
    console.log(`  [trigger] starting demo flow on chromium…`);
    engineResult.triggerFlow = await testTriggerFlow(browser);
    console.log(`  [trigger] triggered=${engineResult.triggerFlow.triggered} urlChanged=${engineResult.triggerFlow.urlChanged} spinnerSeen=${engineResult.triggerFlow.spinnerSeen}`);
  }
  await browser.close();
  existing.engines.chromium = engineResult;
  existing.finishedAt = new Date().toISOString();
  fs.writeFileSync(OUT_JSON, JSON.stringify(existing, null, 2));
  console.log(`Wrote ${OUT_JSON}`);
})();
