// W16-A: verify TriggerButton navigates IMMEDIATELY after POST returns
// event_id, instead of waiting for the lifecycle to terminate.
//
// Expected: click -> URL change measured in ms, NOT seconds.
//
// Usage from polyglot-alpha/ui:
//   node scripts/w16a-verify.mjs
//
// Outputs:
//   /tmp/w16a-verify.md
//   screenshots/w16a/*.png

import { chromium } from "playwright";
import { promises as fs } from "fs";
import path from "path";

const UI = "http://localhost:3001";
const API = "http://127.0.0.1:8000";
const SHOT_DIR = path.resolve(
  path.dirname(new URL(import.meta.url).pathname),
  "..",
  "screenshots",
  "w16a",
);
const MANIFEST = "/tmp/w16a-verify.md";

// Targets per the W16-A spec.
const TARGET_CLICK_TO_NAV_MS = 500; // PASS if < 500ms

const now = () => Date.now();
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const report = {
  startedAt: new Date().toISOString(),
  runs: [],
};

async function runOnce(browser, mode) {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();

  const consoleErrors = [];
  page.on("console", (msg) => {
    if (msg.type() === "error") consoleErrors.push(msg.text());
  });
  page.on("pageerror", (err) =>
    consoleErrors.push(`pageerror: ${err.message}`),
  );

  // Use ?mode=mock to force the demo mode for the mock run.
  const url = mode === "mock" ? `${UI}/?mode=mock` : `${UI}/`;
  console.log(`\n=== ${mode.toUpperCase()} RUN ===`);
  console.log(`goto ${url}`);
  await page.goto(url, { waitUntil: "domcontentloaded" });
  await page.waitForLoadState("networkidle").catch(() => {});
  // Give the mode context a moment to hydrate from the query string before
  // we grab the button label (otherwise the SSR label shows "live" briefly).
  await sleep(1000);

  const namePattern = new RegExp(`Trigger ${mode} demo`, "i");
  const triggerBtn = page.getByRole("button", { name: namePattern });
  try {
    await triggerBtn.waitFor({ state: "visible", timeout: 30_000 });
  } catch {
    // Fall back to aria-label.
  }
  const btn =
    (await triggerBtn.count()) > 0
      ? triggerBtn.first()
      : page.getByRole("button", { name: /Trigger a live demo event/i }).first();

  await fs.mkdir(SHOT_DIR, { recursive: true });
  await page.screenshot({ path: path.join(SHOT_DIR, `${mode}-00-before.png`) });

  // Tight poll loop: click and then sample URL every 20ms so the
  // click->URL-change delta is measured at sub-50ms resolution.
  const clickT0 = now();
  // Fire-and-forget click — don't await the playwright click() promise itself
  // because it includes actionability checks that can add 30-100ms of slack.
  // Instead we start a polling loop and a separate click promise; whichever
  // sees the URL change wins.
  let navT = null;
  let navEventId = null;
  const navPromise = (async () => {
    const deadline = now() + 30_000;
    while (now() < deadline) {
      const u = page.url();
      const m = u.match(/\/events\/(\d+)/);
      if (m) {
        navT = now() - clickT0;
        navEventId = m[1];
        return;
      }
      await sleep(20);
    }
  })();

  await btn.click();
  await navPromise;

  // Detail page paint timing: time to first phase rail / DAG appears.
  let firstPaintMs = null;
  if (navEventId) {
    const paintDeadline = now() + 60_000;
    while (now() < paintDeadline) {
      const hasPhase = await page
        .locator("text=/phase|pending|running|Auction|Translation|Verdict/i")
        .first()
        .count()
        .catch(() => 0);
      if (hasPhase > 0) {
        firstPaintMs = now() - clickT0;
        break;
      }
      await sleep(50);
    }
    await page.screenshot({ path: path.join(SHOT_DIR, `${mode}-01-after-nav.png`) });
  } else {
    await page.screenshot({ path: path.join(SHOT_DIR, `${mode}-01-stuck.png`) });
  }

  // Quick API status sanity check — confirms backend really did pre-create
  // the event row.
  let backendStatus = null;
  if (navEventId) {
    try {
      const r = await fetch(`${API}/events/${navEventId}`);
      if (r.ok) {
        const d = await r.json();
        backendStatus = String(d.status ?? "");
      } else {
        backendStatus = `(HTTP ${r.status})`;
      }
    } catch (e) {
      backendStatus = `(fetch err ${e?.message ?? e})`;
    }
  }

  await ctx.close();
  return {
    mode,
    clickToNavMs: navT,
    firstPaintMs,
    eventId: navEventId,
    backendStatus,
    consoleErrors: consoleErrors.slice(0, 10),
    pass: navT !== null && navT < TARGET_CLICK_TO_NAV_MS,
  };
}

async function writeManifest() {
  const lines = [];
  lines.push(`# W16-A Verify`);
  lines.push(`Started: ${report.startedAt}`);
  lines.push(``);
  lines.push(`Target: click -> URL change **< ${TARGET_CLICK_TO_NAV_MS}ms**`);
  lines.push(``);
  for (const r of report.runs) {
    lines.push(`## ${r.mode.toUpperCase()}`);
    lines.push(`- event_id: \`${r.eventId ?? "n/a"}\``);
    lines.push(
      `- click -> URL change: **${r.clickToNavMs ?? "(no nav)"} ms** ${r.pass ? "PASS" : "FAIL"}`,
    );
    lines.push(
      `- click -> first phase paint: ${r.firstPaintMs ?? "(not observed)"} ms`,
    );
    lines.push(`- backend status @ verify: \`${r.backendStatus ?? "n/a"}\``);
    lines.push(`- console errors: ${r.consoleErrors.length}`);
    r.consoleErrors.forEach((e) => lines.push(`  - ${e.slice(0, 220)}`));
    lines.push(``);
  }
  await fs.writeFile(MANIFEST, lines.join("\n"), "utf8");
  console.log(`manifest: ${MANIFEST}`);
}

async function main() {
  await fs.mkdir(SHOT_DIR, { recursive: true });
  const browser = await chromium.launch({ headless: true });
  try {
    for (const mode of ["mock", "live"]) {
      try {
        const r = await runOnce(browser, mode);
        report.runs.push(r);
        console.log(
          `${mode}: click->nav=${r.clickToNavMs}ms paint=${r.firstPaintMs}ms ` +
            `id=${r.eventId} backend=${r.backendStatus} pass=${r.pass}`,
        );
      } catch (e) {
        console.error(`${mode} run error:`, e);
        report.runs.push({
          mode,
          clickToNavMs: null,
          firstPaintMs: null,
          eventId: null,
          backendStatus: null,
          consoleErrors: [`SCRIPT EXC: ${e?.message ?? e}`],
          pass: false,
        });
      }
    }
  } finally {
    await browser.close();
    await writeManifest();
  }

  const allPass = report.runs.every((r) => r.pass);
  process.exit(allPass ? 0 : 1);
}

await main();
