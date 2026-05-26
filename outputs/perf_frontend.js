// Dim 5: Frontend cold-start (FCP/LCP) using Playwright.
// Navigates with cold browser context to / and key routes, records FCP+LCP.
const { chromium } = require('playwright');

async function measure(url) {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext();
  const page = await context.newPage();
  const t0 = Date.now();
  const response = await page.goto(url, { waitUntil: 'load', timeout: 30000 });
  const navTime = Date.now() - t0;
  // Wait for LCP, then read perf entries
  await page.waitForLoadState('networkidle', { timeout: 15000 }).catch(() => {});
  const perf = await page.evaluate(() => {
    const nav = performance.getEntriesByType('navigation')[0] || {};
    const paints = performance.getEntriesByType('paint');
    const fcp = paints.find(p => p.name === 'first-contentful-paint');
    const fp = paints.find(p => p.name === 'first-paint');
    let lcp = null;
    try {
      const lcps = performance.getEntriesByType('largest-contentful-paint');
      if (lcps.length) lcp = lcps[lcps.length - 1].startTime;
    } catch (_) {}
    return {
      ttfb_ms: nav.responseStart ? Math.round(nav.responseStart) : null,
      dom_loaded_ms: nav.domContentLoadedEventEnd ? Math.round(nav.domContentLoadedEventEnd) : null,
      load_ms: nav.loadEventEnd ? Math.round(nav.loadEventEnd) : null,
      fp_ms: fp ? Math.round(fp.startTime) : null,
      fcp_ms: fcp ? Math.round(fcp.startTime) : null,
      lcp_ms: lcp ? Math.round(lcp) : null,
      transfer_size: nav.transferSize || null,
    };
  });
  await browser.close();
  return { url, nav_time_ms: navTime, ...perf, status: response ? response.status() : null };
}

(async () => {
  const urls = [
    'http://localhost:3001/',
    'http://localhost:3001/events',
    'http://localhost:3001/leaderboard',
    'http://localhost:3001/agents',
  ];
  const results = [];
  for (const url of urls) {
    try {
      const r = await measure(url);
      console.log(JSON.stringify(r));
      results.push(r);
    } catch (e) {
      console.log(JSON.stringify({ url, error: String(e).slice(0, 200) }));
      results.push({ url, error: String(e).slice(0, 200) });
    }
  }
  const fs = require('fs');
  fs.writeFileSync('/Users/messili/codebase/polyglot-alpha/outputs/perf_frontend.json', JSON.stringify(results, null, 2));
  console.log('done');
})();
