// Cross-browser compatibility test runner for PolyglotAlpha v2.
// Runs the same matrix of routes + viewports + a Trigger flow against
// chromium, firefox, and webkit, then writes results to outputs/.
//
// Usage: node scripts/cross_browser_test.js
// Env:
//   BASE_URL           default http://localhost:3001
//   EVENT_ID           an existing event id for /events/{id}
//   SKIP_TRIGGER       set to '1' to skip the (~75s) lifecycle test
//   OUT_DIR            absolute path to write outputs (default ../outputs)

/* eslint-disable no-console */
const fs = require("fs");
const path = require("path");

const { chromium, firefox, webkit } = require("playwright");

const BASE_URL = process.env.BASE_URL || "http://localhost:3001";
const EVENT_ID = process.env.EVENT_ID || "114";
const SKIP_TRIGGER = process.env.SKIP_TRIGGER === "1";
const OUT_DIR =
  process.env.OUT_DIR ||
  path.resolve(__dirname, "..", "..", "outputs");
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

const ENGINES = [
  { name: "chromium", launcher: chromium },
  { name: "firefox", launcher: firefox },
  { name: "webkit", launcher: webkit },
];

/** Try to launch a browser; if it can't (binary missing), return null. */
async function tryLaunch(engine) {
  try {
    const browser = await engine.launcher.launch({ headless: true });
    return browser;
  } catch (err) {
    console.error(`[${engine.name}] launch failed: ${err.message}`);
    return null;
  }
}

/** Read Paint timings via PerformanceObserver entries already buffered. */
async function readPaintTimings(page) {
  return page.evaluate(() => {
    const result = { FCP: null, LCP: null };
    try {
      const paints = performance.getEntriesByType("paint");
      const fcp = paints.find((p) => p.name === "first-contentful-paint");
      if (fcp) result.FCP = Math.round(fcp.startTime);
    } catch (_) {}
    // LCP is best-effort; relies on the browser exposing the entry type.
    return new Promise((resolve) => {
      let lcpValue = result.LCP;
      try {
        const po = new PerformanceObserver((list) => {
          for (const entry of list.getEntries()) {
            lcpValue = Math.round(entry.startTime);
          }
        });
        po.observe({ type: "largest-contentful-paint", buffered: true });
        // Resolve after a short tick so we capture any already-buffered entries.
        setTimeout(() => {
          try { po.disconnect(); } catch (_) {}
          resolve({ FCP: result.FCP, LCP: lcpValue });
        }, 600);
      } catch (_) {
        resolve(result);
      }
    });
  });
}

/** Sanity heuristic: detect Flash of Unstyled Content by looking at
 *  the computed background color of body. The v2 UI uses a dark
 *  theme (background near #0a0a0a). A "white" computed bg indicates
 *  CSS likely hasn't loaded. */
async function checkCssApplied(page) {
  return page.evaluate(() => {
    const bg = getComputedStyle(document.body).backgroundColor;
    const color = getComputedStyle(document.body).color;
    // Parse "rgb(r, g, b)" — if rgb sum > 600 we treat as light/unstyled.
    const m = bg.match(/(\d+),\s*(\d+),\s*(\d+)/);
    const sum = m ? Number(m[1]) + Number(m[2]) + Number(m[3]) : 0;
    return { bg, color, looksStyled: sum > 0 && sum < 600 };
  });
}

async function testRoute({ browserName, browser, route, viewport }) {
  const ctx = await browser.newContext({
    viewport: { width: viewport.width, height: viewport.height },
    colorScheme: "dark",
  });
  const page = await ctx.newPage();
  const consoleErrors = [];
  const networkFailures = [];

  page.on("console", (msg) => {
    if (msg.type() === "error") consoleErrors.push(msg.text());
  });
  page.on("response", (resp) => {
    const status = resp.status();
    if (status >= 400) {
      networkFailures.push({ url: resp.url(), status });
    }
  });
  page.on("pageerror", (err) => {
    consoleErrors.push(`pageerror: ${err.message}`);
  });

  const url = `${BASE_URL}${route.path}`;
  const startedAt = Date.now();
  let pageError = null;
  try {
    await page.goto(url, { waitUntil: "domcontentloaded", timeout: 30000 });
    // Give Next.js a moment to hydrate and run any lazy imports.
    await page.waitForLoadState("networkidle", { timeout: 15000 }).catch(() => {});
  } catch (err) {
    pageError = err.message;
  }
  const loadMs = Date.now() - startedAt;

  let cssCheck = null;
  let timings = { FCP: null, LCP: null };
  let titleText = null;
  if (!pageError) {
    try {
      cssCheck = await checkCssApplied(page);
      timings = await readPaintTimings(page);
      titleText = await page.title();
    } catch (err) {
      pageError = pageError || `eval failure: ${err.message}`;
    }
  }

  const shotFile = path.join(
    SHOT_DIR,
    `xbrowser_${browserName}_${route.name}_${viewport.name}.png`,
  );
  try {
    await page.screenshot({ path: shotFile, fullPage: false });
  } catch (err) {
    // Non-fatal: continue with the rest of the matrix.
  }

  await ctx.close();

  return {
    browser: browserName,
    route: route.name,
    path: route.path,
    viewport: viewport.name,
    loadMs,
    pageError,
    consoleErrorCount: consoleErrors.length,
    consoleErrors: consoleErrors.slice(0, 5),
    networkFailureCount: networkFailures.length,
    networkFailures: networkFailures.slice(0, 5),
    cssApplied: cssCheck?.looksStyled ?? null,
    bodyBg: cssCheck?.bg ?? null,
    title: titleText,
    FCP: timings.FCP,
    LCP: timings.LCP,
    screenshot: path.relative(OUT_DIR, shotFile),
  };
}

async function testTriggerFlow({ browserName, browser }) {
  const ctx = await browser.newContext({
    viewport: { width: 1280, height: 800 },
    colorScheme: "dark",
  });
  const page = await ctx.newPage();
  const consoleErrors = [];
  page.on("console", (m) => m.type() === "error" && consoleErrors.push(m.text()));
  page.on("pageerror", (err) => consoleErrors.push(`pageerror: ${err.message}`));

  const out = {
    browser: browserName,
    triggered: false,
    urlChanged: false,
    spinnerSeen: false,
    finalUrl: null,
    error: null,
    consoleErrors: [],
  };

  try {
    await page.goto(`${BASE_URL}/`, { waitUntil: "domcontentloaded", timeout: 30000 });
    await page.waitForLoadState("networkidle", { timeout: 10000 }).catch(() => {});

    const triggerButton = page.getByRole("button", {
      name: /trigger.*live demo/i,
    });
    await triggerButton.waitFor({ state: "visible", timeout: 10000 });
    const startUrl = page.url();

    await triggerButton.click();
    out.triggered = true;

    // Check spinner visibility within 1.5s (lucide .animate-spin)
    try {
      await page.waitForSelector(".animate-spin", { timeout: 1500 });
      out.spinnerSeen = true;
    } catch (_) {}

    // Wait up to 90s for either URL change or busy state to resolve
    const deadline = Date.now() + 90000;
    while (Date.now() < deadline) {
      const cur = page.url();
      if (cur !== startUrl && /\/events\//.test(cur)) {
        out.urlChanged = true;
        out.finalUrl = cur;
        break;
      }
      await page.waitForTimeout(1000);
    }
    // Capture final screenshot regardless
    const shotFile = path.join(
      SHOT_DIR,
      `xbrowser_${browserName}_trigger_final.png`,
    );
    await page.screenshot({ path: shotFile, fullPage: false });
    out.screenshot = path.relative(OUT_DIR, shotFile);
  } catch (err) {
    out.error = err.message;
  } finally {
    out.consoleErrors = consoleErrors.slice(0, 5);
    await ctx.close();
  }
  return out;
}

(async () => {
  const startedAt = new Date().toISOString();
  const results = {
    startedAt,
    baseUrl: BASE_URL,
    eventId: EVENT_ID,
    skipTrigger: SKIP_TRIGGER,
    engines: {},
  };

  for (const engine of ENGINES) {
    console.log(`\n=== ${engine.name} ===`);
    const browser = await tryLaunch(engine);
    if (!browser) {
      results.engines[engine.name] = {
        available: false,
        error: "launch failed (binary missing or platform unsupported)",
      };
      continue;
    }
    const engineResult = {
      available: true,
      routeViewport: [],
      triggerFlow: null,
    };
    // Desktop viewport: all routes
    for (const route of ROUTES) {
      const r = await testRoute({
        browserName: engine.name,
        browser,
        route,
        viewport: VIEWPORTS[2], // desktop
      });
      console.log(
        `  [desktop] ${route.name.padEnd(16)} load=${r.loadMs}ms ` +
          `FCP=${r.FCP} LCP=${r.LCP} err=${r.consoleErrorCount} net4xx5xx=${r.networkFailureCount} ` +
          `css=${r.cssApplied}`,
      );
      engineResult.routeViewport.push(r);
    }
    // Mobile + tablet: home only (lightweight responsive check)
    for (const vp of [VIEWPORTS[0], VIEWPORTS[1]]) {
      const r = await testRoute({
        browserName: engine.name,
        browser,
        route: ROUTES[0],
        viewport: vp,
      });
      console.log(
        `  [${vp.name}] home load=${r.loadMs}ms FCP=${r.FCP} err=${r.consoleErrorCount}`,
      );
      engineResult.routeViewport.push(r);
    }

    if (!SKIP_TRIGGER) {
      console.log(`  [trigger] starting demo flow on ${engine.name}…`);
      engineResult.triggerFlow = await testTriggerFlow({
        browserName: engine.name,
        browser,
      });
      console.log(
        `  [trigger] triggered=${engineResult.triggerFlow.triggered} ` +
          `urlChanged=${engineResult.triggerFlow.urlChanged} ` +
          `spinnerSeen=${engineResult.triggerFlow.spinnerSeen} ` +
          `error=${engineResult.triggerFlow.error}`,
      );
    }

    await browser.close();
    results.engines[engine.name] = engineResult;
  }

  results.finishedAt = new Date().toISOString();

  const jsonPath = path.join(OUT_DIR, "cross_browser_iter_1.json");
  fs.writeFileSync(jsonPath, JSON.stringify(results, null, 2));
  console.log(`\nWrote ${jsonPath}`);
})();
