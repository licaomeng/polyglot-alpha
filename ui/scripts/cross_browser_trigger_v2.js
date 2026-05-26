// V2: Per-browser trigger test — waits for hydration via networkidle (best
// effort), captures the POST response, and uses waitForURL with a 90s
// budget. Sequential per browser to avoid hammering the backend.
const fs = require("fs");
const path = require("path");
const { chromium, firefox, webkit } = require("playwright");

const BASE_URL = process.env.BASE_URL || "http://localhost:3001";
const OUT_DIR = path.resolve(__dirname, "..", "..", "outputs");
const SHOT_DIR = path.join(OUT_DIR, "screenshots");

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
  const triggerPosts = [];
  page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
  page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
  page.on("requestfinished", async (req) => {
    if (req.method() === "POST" && /trigger\/event/.test(req.url())) {
      try {
        const resp = await req.response();
        const status = resp?.status();
        let body = null;
        try { body = await resp?.text(); } catch (_) {}
        triggerPosts.push({ url: req.url(), status, body: body?.slice(0, 200) });
      } catch (e) { triggerPosts.push({ url: req.url(), error: e.message }); }
    }
  });

  const out = {
    browser: engine.name, postCount: 0, postStatus: null, postBody: null,
    waitForURLSucceeded: false, finalUrl: null, msToNavigate: null,
    spinnerSeen: false, errors: [],
  };

  try {
    await page.goto(`${BASE_URL}/`, { waitUntil: "domcontentloaded", timeout: 30000 });
    // Wait for hydration: either networkidle within 5s or 1500ms grace if SSE keeps it open.
    await page.waitForLoadState("networkidle", { timeout: 5000 }).catch(async () => {
      await page.waitForTimeout(1500);
    });
    const btn = page.getByRole("button", { name: /trigger.*live demo/i });
    await btn.waitFor({ state: "visible", timeout: 10000 });
    const t0 = Date.now();
    await btn.click();
    try { await page.waitForSelector(".animate-spin", { timeout: 2500 }); out.spinnerSeen = true; } catch (_) {}
    try {
      await page.waitForURL(/\/events\/\d+/, { timeout: 90000 });
      out.waitForURLSucceeded = true;
      out.finalUrl = page.url();
      out.msToNavigate = Date.now() - t0;
    } catch (err) {
      out.errors.push(`waitForURL: ${err.message.split("\n")[0]}`);
    }
    out.postCount = triggerPosts.length;
    if (triggerPosts.length) {
      out.postStatus = triggerPosts[0].status;
      out.postBody = triggerPosts[0].body;
    }
    const shot = path.join(SHOT_DIR, `xbrowser_${engine.name}_trigger_v2.png`);
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
  const out = path.join(OUT_DIR, "cross_browser_trigger_v2.json");
  fs.writeFileSync(out, JSON.stringify(all, null, 2));
  console.log(`Wrote ${out}`);
})();
