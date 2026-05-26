// Test only the trigger POST + autonav SSE behavior more carefully.
import { chromium } from "playwright";

const BASE = "http://localhost:3001";
const browser = await chromium.launch({ headless: true });
const ctx = await browser.newContext({ viewport: { width: 1920, height: 1080 } });
const page = await ctx.newPage();

const reqs = [];
const sses = [];

page.on("request", (req) => {
  const url = req.url();
  if (url.includes("/events/trigger") || (url.includes(":8000") && req.method() === "POST")) {
    reqs.push({
      method: req.method(),
      url,
      body: req.postDataBuffer()?.toString("utf-8") || req.postData(),
      headers: req.headers(),
    });
  }
});
page.on("response", async (resp) => {
  const url = resp.url();
  if (url.includes("/events/trigger")) {
    try {
      const body = await resp.text();
      console.log("[resp]", resp.status(), url, body.slice(0, 400));
    } catch {}
  }
});

// SSE: monitor EventSource frames via JS
await page.goto(`${BASE}/?mode=mock`, { waitUntil: "domcontentloaded" });
await page.waitForTimeout(800);

// install instrumented EventSource hook BEFORE clicking
await page.evaluate(() => {
  window.__sseEvents = [];
  const OrigES = window.EventSource;
  window.EventSource = function (url, init) {
    const inst = new OrigES(url, init);
    const log = (typ) => (ev) => {
      window.__sseEvents.push({ type: typ, name: ev.type, data: (ev.data || "").slice(0, 400), t: Date.now() });
    };
    inst.addEventListener("message", log("message"));
    inst.addEventListener("event.finalized", log("event.finalized"));
    inst.addEventListener("phase.completed", log("phase.completed"));
    ["error", "open"].forEach((e) => inst.addEventListener(e, log(e)));
    return inst;
  };
  window.EventSource.prototype = OrigES.prototype;
});

console.log("Starting trigger flow...");
const t0 = Date.now();
const trigBtn = page.locator('button:has-text("Trigger")').first();
await trigBtn.click();

const navP = page
  .waitForURL((u) => /\/events\/\d+$/.test(u.toString()), { timeout: 130000 })
  .catch((e) => ({ error: e.message }));
const navResult = await navP;
const navTime = Date.now() - t0;
console.log("navigated to", page.url(), "in", navTime, "ms", navResult);

// Read SSE events captured
const sseEvts = await page.evaluate(() => window.__sseEvents || []);
console.log("SSE events on home page:", JSON.stringify(sseEvts.slice(0, 30)));

// Trigger POST capture
console.log("Trigger requests captured:");
console.log(JSON.stringify(reqs, null, 2).slice(0, 3000));

// Now also test directly: curl the API to see the actual response shape
const apiResp = await page.evaluate(async () => {
  try {
    const r = await fetch("http://localhost:8000/events/trigger", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode: "mock" }),
    });
    const body = await r.text();
    return { status: r.status, body: body.slice(0, 500) };
  } catch (e) {
    return { error: e.message };
  }
});
console.log("Direct API call:", JSON.stringify(apiResp));

await browser.close();
