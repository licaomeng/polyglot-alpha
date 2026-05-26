// Focused re-test of the Trigger flow using waitForURL (event-driven, not
// poll-based) to distinguish a real cross-browser bug from a test-timing
// artifact. Writes results into outputs/cross_browser_trigger_recheck.json.
const fs = require("fs");
const path = require("path");
const { chromium, firefox, webkit } = require("playwright");

const BASE_URL = process.env.BASE_URL || "http://localhost:3001";
const OUT_DIR = path.resolve(__dirname, "..", "..", "outputs");
const SHOT_DIR = path.join(OUT_DIR, "screenshots");
fs.mkdirSync(SHOT_DIR, { recursive: true });

const ENGINES = [
  { name: "chromium", launcher: chromium },
  { name: "firefox", launcher: firefox },
  { name: "webkit", launcher: webkit },
];

async function run(engine) {
  const browser = await engine.launcher.launch({ headless: true });
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 }, colorScheme: "dark" });
  const page = await ctx.newPage();
  const errors = [];
  const postResponses = [];
  page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
  page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
  page.on("response", async (r) => {
    if (r.request().method() === "POST" && /trigger/.test(r.url())) {
      postResponses.push({ url: r.url(), status: r.status() });
    }
  });

  const out = {
    browser: engine.name, postReceived: false, postStatus: null,
    waitForURLSucceeded: false, finalUrl: null, msToNavigate: null,
    spinnerSeen: false, errors: [],
  };

  try {
    await page.goto(`${BASE_URL}/`, { waitUntil: "domcontentloaded", timeout: 30000 });
    const btn = page.getByRole("button", { name: /trigger.*live demo/i });
    await btn.waitFor({ state: "visible", timeout: 10000 });
    const t0 = Date.now();
    await btn.click();
    try { await page.waitForSelector(".animate-spin", { timeout: 1500 }); out.spinnerSeen = true; } catch (_) {}
    try {
      await page.waitForURL(/\/events\/\d+/, { timeout: 30000 });
      out.waitForURLSucceeded = true;
      out.finalUrl = page.url();
      out.msToNavigate = Date.now() - t0;
    } catch (err) {
      out.errors.push(`waitForURL: ${err.message}`);
    }
    if (postResponses.length > 0) {
      out.postReceived = true;
      out.postStatus = postResponses[0].status;
    }
    const shot = path.join(SHOT_DIR, `xbrowser_${engine.name}_trigger_recheck.png`);
    await page.screenshot({ path: shot });
    out.screenshot = path.relative(OUT_DIR, shot);
  } catch (err) {
    out.errors.push(err.message);
  } finally {
    out.errors.push(...errors.slice(0, 3));
    await ctx.close();
    await browser.close();
  }
  return out;
}

(async () => {
  const all = [];
  for (const engine of ENGINES) {
    console.log(`-- ${engine.name} --`);
    const r = await run(engine);
    console.log(JSON.stringify(r));
    all.push(r);
  }
  const out = path.join(OUT_DIR, "cross_browser_trigger_recheck.json");
  fs.writeFileSync(out, JSON.stringify(all, null, 2));
  console.log(`Wrote ${out}`);
})();
