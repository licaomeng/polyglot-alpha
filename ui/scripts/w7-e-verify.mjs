// W7-E: Verify SSR/CSR hydration parity for mode-dependent rendering.
// Run: node ui/scripts/w7-e-verify.mjs
//
// What this exercises:
//   1. Cold load `/?mode=mock` headless — capture ALL console messages and
//      assert no hydration warnings ("did not match", "hydrating", "Warning:
//      Prop", "Hydration failed", "Text content does not match").
//   2. Take a screenshot at first DOM-ready (before the hydration effect has
//      had a chance to flip mode), and a second screenshot ~250ms later
//      (after the effect commits and switches to mock).
//   3. Verify the post-mount DOM reports `data-mode="mock"` on the header
//      and the trigger button label reads "Trigger mock demo".
//   4. Sanity: also do a clean `/` (live) reload to confirm we didn't break
//      the live-mode rendering path.
//
// Pass criteria: 0 hydration warnings AND post-mount DOM reflects mock mode
//   AND the live-mode reload shows live (cyan / Zap / "Trigger live demo").

import { chromium } from "playwright";
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const BASE_UI = "http://localhost:3001";
const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));
const SHOT_DIR = resolve(SCRIPT_DIR, "..", "screenshots", "w7-e");
const REPORT = resolve(SHOT_DIR, "report.txt");

mkdirSync(SHOT_DIR, { recursive: true });

const log = (...a) => console.log("[w7-e]", ...a);

// Patterns that flag a real hydration mismatch in React 18 / Next 15. We
// keep these strict so any future regression surfaces immediately.
const HYDRATION_PATTERNS = [
  /did not match/i,
  /hydration failed/i,
  /hydration mismatch/i,
  /text content does not match/i,
  /server rendered html didn't match the client/i,
  /there was an error while hydrating/i,
  /warning:\s*prop\s+`[^`]+`\s+did not match/i,
];

const isHydrationWarning = (text) =>
  HYDRATION_PATTERNS.some((re) => re.test(text));

const launchTab = async (browser, label) => {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  const messages = [];
  page.on("console", (msg) => {
    const entry = { type: msg.type(), text: msg.text() };
    messages.push(entry);
    if (msg.type() === "error" || msg.type() === "warning") {
      log(`[${label}:${msg.type()}]`, msg.text());
    }
  });
  page.on("pageerror", (err) => {
    messages.push({ type: "pageerror", text: String(err) });
    log(`[${label}:pageerror]`, String(err));
  });
  return { ctx, page, messages };
};

const browser = await chromium.launch({ headless: true });

// ─── Scenario 1a: raw SSR shell for /?mode=mock ───────────────────────────
// We hit the server with plain fetch (no JS) to assert the HTML payload
// itself reports the safe "live" shell — that's what proves the SSR/CSR
// output match and hydration cannot mismatch. Playwright's DOMContentLoaded
// fires too late to observe this (the hydration effect commits first).
log("=== Scenario 1a: SSR shell (raw fetch /?mode=mock) ===");
const ssrHtml = await (await fetch(`${BASE_UI}/?mode=mock`)).text();
const ssrModeMatch = ssrHtml.match(/data-mode="([^"]+)"/);
const ssrMode = ssrModeMatch ? ssrModeMatch[1] : null;
const ssrHasMockLabel = /Trigger mock demo/.test(ssrHtml);
const ssrHasLiveLabel = /Trigger live demo/.test(ssrHtml);
log(
  `SSR shell: data-mode=${ssrMode} hasLiveLabel=${ssrHasLiveLabel} hasMockLabel=${ssrHasMockLabel}`,
);
const ssrShellOk = ssrMode === "live" && ssrHasLiveLabel && !ssrHasMockLabel;

// ─── Scenario 1b: client hydration + post-mount switch ────────────────────
log("=== Scenario 1b: client load /?mode=mock ===");
const mockTab = await launchTab(browser, "mock");
await mockTab.page.goto(`${BASE_UI}/?mode=mock`, {
  waitUntil: "domcontentloaded",
  timeout: 30000,
});

// Screenshot immediately after DOMContentLoaded. By this point React has
// usually already committed the post-mount mode flip (millisecond budget),
// so this captures the resolved state — useful for visual diffing.
const preMountShot = resolve(SHOT_DIR, "01-pre-mount.png");
await mockTab.page.screenshot({ path: preMountShot, fullPage: false });
const preMountMode = await mockTab.page
  .locator("header[data-mode]")
  .first()
  .getAttribute("data-mode")
  .catch(() => null);
const preMountLabel = await mockTab.page
  .locator('button[aria-label*="Trigger"]')
  .first()
  .textContent()
  .catch(() => null);
log(
  `at-DCL: data-mode=${preMountMode} button="${preMountLabel?.trim() ?? null}"`,
);

// Wait for the hydration effect + mode-switch effect to commit.
await mockTab.page.waitForTimeout(400);

const postMountShot = resolve(SHOT_DIR, "02-post-mount.png");
await mockTab.page.screenshot({ path: postMountShot, fullPage: false });
const postMountMode = await mockTab.page
  .locator("header[data-mode]")
  .first()
  .getAttribute("data-mode")
  .catch(() => null);
const postMountLabel = await mockTab.page
  .locator('button[aria-label*="Trigger"]')
  .first()
  .textContent()
  .catch(() => null);
log(
  `post-mount: data-mode=${postMountMode} button="${postMountLabel?.trim() ?? null}"`,
);

// Let any deferred hydration logs flush before we tally warnings.
await mockTab.page.waitForTimeout(200);
const mockHydrationHits = mockTab.messages.filter((m) =>
  isHydrationWarning(m.text),
);

// ─── Scenario 2: clean live load / ────────────────────────────────────────
log("=== Scenario 2: clean live load / ===");
const liveTab = await launchTab(browser, "live");
// Use a fresh context (already from launchTab) so no localStorage carryover.
await liveTab.page.goto(`${BASE_UI}/`, {
  waitUntil: "domcontentloaded",
  timeout: 30000,
});
await liveTab.page.waitForTimeout(400);
const liveShot = resolve(SHOT_DIR, "03-live-mode.png");
await liveTab.page.screenshot({ path: liveShot, fullPage: false });
const liveMode = await liveTab.page
  .locator("header[data-mode]")
  .first()
  .getAttribute("data-mode")
  .catch(() => null);
const liveLabel = await liveTab.page
  .locator('button[aria-label*="Trigger"]')
  .first()
  .textContent()
  .catch(() => null);
log(`live: data-mode=${liveMode} button="${liveLabel?.trim() ?? null}"`);
await liveTab.page.waitForTimeout(200);
const liveHydrationHits = liveTab.messages.filter((m) =>
  isHydrationWarning(m.text),
);

await browser.close();

// ─── Build report ─────────────────────────────────────────────────────────
const postMountOk =
  postMountMode === "mock" &&
  typeof postMountLabel === "string" &&
  postMountLabel.toLowerCase().includes("trigger mock demo");
const liveOk =
  liveMode === "live" &&
  typeof liveLabel === "string" &&
  liveLabel.toLowerCase().includes("trigger live demo");
const noHydrationWarnings =
  mockHydrationHits.length === 0 && liveHydrationHits.length === 0;

const allPass = ssrShellOk && postMountOk && liveOk && noHydrationWarnings;

const lines = [
  `W7-E hydration-mismatch fix — verify run @ ${new Date().toISOString()}`,
  ``,
  `Scenario 1a: SSR shell (raw fetch /?mode=mock)`,
  `  data-mode=${ssrMode} hasLiveLabel=${ssrHasLiveLabel} hasMockLabel=${ssrHasMockLabel}   ${ssrShellOk ? "PASS (server emits safe live shell)" : "FAIL (expected live shell)"}`,
  ``,
  `Scenario 1b: client load /?mode=mock`,
  `  at-DCL     data-mode=${preMountMode}  button="${preMountLabel?.trim() ?? null}"`,
  `  post-mount data-mode=${postMountMode} button="${postMountLabel?.trim() ?? null}"   ${postMountOk ? "PASS (switched to mock)" : "FAIL (expected mock)"}`,
  `  hydration warnings: ${mockHydrationHits.length}   ${mockHydrationHits.length === 0 ? "PASS" : "FAIL"}`,
  ...mockHydrationHits.map((m) => `    - [${m.type}] ${m.text}`),
  ``,
  `Scenario 2: clean live load /`,
  `  data-mode=${liveMode} button="${liveLabel?.trim() ?? null}"   ${liveOk ? "PASS" : "FAIL"}`,
  `  hydration warnings: ${liveHydrationHits.length}   ${liveHydrationHits.length === 0 ? "PASS" : "FAIL"}`,
  ...liveHydrationHits.map((m) => `    - [${m.type}] ${m.text}`),
  ``,
  `Screenshots:`,
  `  ${preMountShot}`,
  `  ${postMountShot}`,
  `  ${liveShot}`,
  ``,
  `OVERALL: ${allPass ? "PASS" : "FAIL"}`,
];

const report = lines.join("\n");
writeFileSync(REPORT, report + "\n");
log("\n" + report);
log(`wrote ${REPORT}`);

process.exit(allPass ? 0 : 1);
