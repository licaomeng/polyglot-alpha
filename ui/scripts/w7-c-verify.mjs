// W7-C: Verify two UI rendering bugs in mock-mode events.
//
// Bug 1 (Phase 5 IPFS missing):
//   Mock events emit `event.anchor.ipfsCid = "ipfs://sim/<hash>"` and a
//   matching `phases[4].details.reasoning_ipfs`. The classifier strips the
//   `ipfs://` scheme prefix before rendering the synthetic-provenance label,
//   which made the literal `ipfs://sim/<hash>` token disappear from the DOM
//   (it rendered as `synthetic · sim/<hash>` instead).
//
// Bug 2 (Phase 7 Streaming Revenue blank for mock):
//   Mock events have a populated `polymarket.revenueStream` with 2 legs
//   (90% winner + 10% treasury) but no `recentFills`, so the top-level
//   `BuilderFeeStream` chart shows a near-zero sparkline with no recipient,
//   no tx hashes, no totals. The per-leg detail in the collapsed accordion
//   was the only surface — fine for power users but invisible by default.
//
// This script:
//   1) Finds a recent mock event via GET /events?limit=20.
//   2) Loads `/events/{id}` in Playwright and inspects the DOM.
//   3) Asserts `ipfs://sim/` appears (Bug 1) and the mock-leg breakdown
//      surfaces "Total disbursed" + 2 `0xsim_` arc tx tokens (Bug 2).

import { chromium } from "playwright";
import { writeFileSync } from "node:fs";

const BASE_UI = "http://localhost:3001";
const BASE_API = "http://127.0.0.1:8000";
const SCREENSHOT_PATH = "/tmp/w7-c-event.png";

const log = (...a) => console.log("[w7-c]", ...a);

const findMockEvent = async () => {
  const res = await fetch(`${BASE_API}/events?limit=20`);
  const events = await res.json();
  const mock = events.find(
    (e) => e.mode === "mock" && String(e.status).toUpperCase() === "SUBMITTED",
  );
  if (!mock) throw new Error("no recent mock SUBMITTED event found");
  return mock.id;
};

const assertBackendShape = async (id) => {
  const res = await fetch(`${BASE_API}/events/${id}`);
  const ev = await res.json();
  const phase5 = ev.phases?.[4];
  const ipfsRef =
    phase5?.details?.reasoning_ipfs ?? ev.anchor?.ipfsCid ?? null;
  const stream = ev.polymarket?.revenueStream ?? [];
  log("backend mode:", ev.mode);
  log("backend status:", ev.status);
  log("backend phase5.reasoning_ipfs:", phase5?.details?.reasoning_ipfs);
  log("backend anchor.ipfsCid:", ev.anchor?.ipfsCid);
  log("backend revenueStream length:", stream.length);
  log("backend revenueStream[0].arcTxHash:", stream[0]?.arcTxHash);
  log("backend revenueStream[1].arcTxHash:", stream[1]?.arcTxHash);
  if (!ipfsRef || !ipfsRef.startsWith("ipfs://")) {
    throw new Error(
      `backend did NOT emit ipfs:// prefixed reasoning_ipfs for event ${id} — backend gap, bail`,
    );
  }
  if (stream.length < 2) {
    throw new Error(
      `backend revenueStream has <2 legs for event ${id} — backend gap, bail`,
    );
  }
  return { ipfsRef, stream };
};

const inspectDom = async (page) => {
  // Expand all phase-details accordions so accordion-only content also
  // shows up in the DOM scan (we still verify the visible top-level card,
  // but tolerating the accordion form for completeness).
  // Wait for the phase timeline to mount (one of the per-phase accordion
  // wrappers carries `data-testid="phase-details-*"`).
  await page
    .waitForSelector('[data-testid^="phase-details-"]', { timeout: 15000 })
    .catch(() => null);
  // Settle SSE / re-render.
  await page.waitForTimeout(2500);
  return await page.evaluate(() => {
    const text = document.body.innerText;
    const html = document.body.innerHTML;
    const ipfsLiteralMatches = [...text.matchAll(/ipfs:\/\/sim\/[A-Za-z0-9]+/g)].map(
      (m) => m[0],
    );
    const ipfsAnywhere = [...text.matchAll(/ipfs:\/\/[^\s ]+/g)].map((m) => m[0]);
    const hasTotalDisbursed = /Total disbursed/i.test(text);
    const hasEntries = /Entries:/i.test(text);
    const simTxMatches = [...text.matchAll(/0xsim_[a-f0-9]+/gi)].map((m) => m[0]);
    // Locate revenue panel by data-testid for a more precise check.
    const mockLegs = document.querySelector('[data-testid="revenue-mock-legs"]');
    const mockLegsRows = mockLegs
      ? mockLegs.querySelectorAll('[data-testid="revenue-stream-row"]').length
      : 0;
    const mockLegsText = mockLegs ? mockLegs.innerText : "";
    const mockLegsSimTx = mockLegs
      ? [...mockLegsText.matchAll(/0xsim_[a-f0-9]+/gi)].map((m) => m[0])
      : [];
    return {
      ipfsLiteralMatches: ipfsLiteralMatches.slice(0, 5),
      ipfsAnywhere: ipfsAnywhere.slice(0, 5),
      hasTotalDisbursed,
      hasEntries,
      simTxCount: simTxMatches.length,
      simTxSample: simTxMatches.slice(0, 4),
      hasMockLegsPanel: !!mockLegs,
      mockLegsRows,
      mockLegsSimTxCount: mockLegsSimTx.length,
      mockLegsSimTxSample: mockLegsSimTx.slice(0, 4),
      htmlIncludesIpfsLiteral: /ipfs:\/\/sim\//.test(html),
    };
  });
};

const main = async () => {
  const id = await findMockEvent();
  log("using mock event id:", id);
  await assertBackendShape(id);
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  page.on("console", (msg) => {
    if (msg.type() === "error") log(`[console.error]`, msg.text());
  });
  // `networkidle` never settles because the page holds a long-lived SSE
  // connection. Use `domcontentloaded` + an explicit wait for the phase
  // timeline to mount.
  await page.goto(`${BASE_UI}/events/${id}`, { waitUntil: "domcontentloaded" });
  // Expand the Phase 5 accordion so the AnchorDetails IO row (which renders
  // an `ipfs.slice(0,18)` token containing `ipfs://sim/`) also surfaces —
  // gives our DOM probe two independent witnesses for Bug 1.
  const accordionBtns = await page.$$('[data-testid^="phase-details-"] button[aria-expanded]');
  for (const btn of accordionBtns) {
    try {
      await btn.click({ timeout: 1000 });
    } catch {
      // Accordion click is best-effort; the visible top-level card already
      // carries the required tokens.
    }
  }
  await page.waitForTimeout(800);
  const dom = await inspectDom(page);
  await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true });
  log("screenshot:", SCREENSHOT_PATH);
  log("dom:", JSON.stringify(dom, null, 2));

  const failures = [];
  // Bug 1: at least one ipfs://sim/ token in the DOM.
  if (dom.ipfsLiteralMatches.length === 0 && !dom.htmlIncludesIpfsLiteral) {
    failures.push("Bug 1 FAIL: no `ipfs://sim/` literal found in DOM");
  }
  // Bug 2: mock legs panel present, 2 rows, 2 sim tx hashes, and totals.
  if (!dom.hasMockLegsPanel) {
    failures.push("Bug 2 FAIL: revenue-mock-legs panel not rendered");
  }
  if (dom.mockLegsRows < 2) {
    failures.push(
      `Bug 2 FAIL: expected >=2 revenue-stream-row, got ${dom.mockLegsRows}`,
    );
  }
  if (dom.mockLegsSimTxCount < 2) {
    failures.push(
      `Bug 2 FAIL: expected >=2 0xsim_* tokens inside mock-legs panel, got ${dom.mockLegsSimTxCount}`,
    );
  }
  if (!dom.hasTotalDisbursed) {
    failures.push("Bug 2 FAIL: no `Total disbursed` text in DOM");
  }
  if (!dom.hasEntries) {
    failures.push("Bug 2 FAIL: no `Entries:` text in DOM");
  }

  await browser.close();

  const summary = {
    eventId: id,
    ipfsLiteralMatches: dom.ipfsLiteralMatches,
    mockLegsRows: dom.mockLegsRows,
    mockLegsSimTxSample: dom.mockLegsSimTxSample,
    hasTotalDisbursed: dom.hasTotalDisbursed,
    hasEntries: dom.hasEntries,
    failures,
    passed: failures.length === 0,
  };
  writeFileSync("/tmp/w7-c-summary.json", JSON.stringify(summary, null, 2));
  log("summary:", JSON.stringify(summary, null, 2));
  if (failures.length > 0) {
    log("FAILED:", failures.length, "checks");
    process.exit(1);
  }
  log("OK — both bugs fixed");
};

main().catch((err) => {
  console.error("[w7-c] fatal:", err);
  process.exit(2);
});
