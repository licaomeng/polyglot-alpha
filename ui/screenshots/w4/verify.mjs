// Playwright headless verification for W4 UI coherence fixes.
// Run from /Users/messili/codebase/polyglot-alpha/ui.

import { chromium } from "playwright";
import { writeFile } from "node:fs/promises";

const BASE = process.env.UI_BASE ?? "http://localhost:3001";
const OUT = "screenshots/w4";

const results = [];
function note(bug, label, value) {
  results.push({ bug, label, value });
  console.log(`[${bug}] ${label}: ${value}`);
}

async function probe(page, selector) {
  return await page.evaluate(
    (sel) => {
      const el = document.querySelector(sel);
      if (!el) return null;
      const cs = getComputedStyle(el);
      return {
        text: el.textContent?.trim().slice(0, 200) ?? "",
        color: cs.color,
        background: cs.backgroundColor,
      };
    },
    selector,
  );
}

(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 1800 } });
  const page = await ctx.newPage();

  // ── Bug 1: judge dossier partial banner + per-judge partial pills on /events/118
  await page.goto(`${BASE}/events/118`, { waitUntil: "domcontentloaded", timeout: 15000 });
  await page.waitForTimeout(3000);
  await page.screenshot({ path: `${OUT}/bug1-event118-full.png`, fullPage: true });

  const bug1Banner = await probe(page, '[data-testid="judge-panel-partial-banner"]');
  note("BUG1", "partial banner present", String(bug1Banner !== null));
  if (bug1Banner) note("BUG1", "banner text", bug1Banner.text);

  const bleuCell = await probe(page, '[data-testid="judge-cell-bleu-partial"]');
  note("BUG1", "BLEU partial pill", String(bleuCell !== null));
  if (bleuCell) note("BUG1", "BLEU pill text", bleuCell.text);
  const cometCell = await probe(page, '[data-testid="judge-cell-comet-partial"]');
  note("BUG1", "COMET partial pill", String(cometCell !== null));

  const bug1Keywords = await page.evaluate(() => {
    const t = document.body.innerText;
    return {
      partial: (t.match(/partial/gi) ?? []).length,
      insufficient: (t.match(/insufficient_data/gi) ?? []).length,
      pending: (t.match(/pending judges|pending\b/gi) ?? []).length,
    };
  });
  note("BUG1", "keyword counts", JSON.stringify(bug1Keywords));

  // ── Bug 2: skipped panels on Phase 6 / Phase 7
  // First expand all phase cards by scrolling so the timeline renders.
  await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
  await page.waitForTimeout(500);
  await page.screenshot({ path: `${OUT}/bug2-event118-bottom.png`, fullPage: true });

  const polymarketEmpty = await probe(page, '[data-testid="polymarket-empty-rejected"]');
  note("BUG2", "polymarket-empty-rejected", String(polymarketEmpty !== null));
  if (polymarketEmpty) note("BUG2", "polymarket text", polymarketEmpty.text);
  const revenueEmpty = await probe(page, '[data-testid="revenue-empty-rejected"]');
  note("BUG2", "revenue-empty-rejected", String(revenueEmpty !== null));
  if (revenueEmpty) note("BUG2", "revenue text", revenueEmpty.text);
  const bug2Keywords = await page.evaluate(() => {
    const t = document.body.innerText;
    return {
      submissionSkipped: t.includes("Submission skipped") || t.includes("submission skipped"),
      streamingSkipped: t.includes("Streaming skipped") || t.includes("streaming skipped"),
      mockBadgePresent: /MOCK\b/.test(t),
    };
  });
  note("BUG2", "skipped strings", JSON.stringify(bug2Keywords));

  // ── Bug 3: Failed vs Rejected badge colors on /events list
  await page.goto(`${BASE}/events`, { waitUntil: "domcontentloaded", timeout: 15000 });
  await page.waitForTimeout(3000);
  await page.screenshot({ path: `${OUT}/bug3-events-list.png`, fullPage: true });

  const badgeColors = await page.evaluate(() => {
    const out = { rejected: [], failed: [] };
    // EventStatusBadge uses aria-label like "Status: Rejected (REJECTED)" or
    // "Status: Failed (FAILED)".
    document.querySelectorAll("[aria-label]").forEach((el) => {
      const label = el.getAttribute("aria-label") ?? "";
      if (/Status: Rejected/i.test(label)) {
        const cs = getComputedStyle(el);
        out.rejected.push({ color: cs.color, background: cs.backgroundColor });
      }
      if (/Status: Failed/i.test(label)) {
        const cs = getComputedStyle(el);
        out.failed.push({ color: cs.color, background: cs.backgroundColor });
      }
    });
    return out;
  });
  note("BUG3", "Rejected badge samples", JSON.stringify(badgeColors.rejected.slice(0, 2)));
  note("BUG3", "Failed badge samples", JSON.stringify(badgeColors.failed.slice(0, 2)));
  const distinct =
    badgeColors.rejected.length > 0 &&
    badgeColors.failed.length > 0 &&
    !badgeColors.rejected.some((r) =>
      badgeColors.failed.some(
        (f) => f.color === r.color && f.background === r.background,
      ),
    );
  note("BUG3", "rejected ≠ failed (color)", String(distinct));

  // ── Bug 4: rail vs timeline status consistency on /events/118
  await page.goto(`${BASE}/events/118`, { waitUntil: "domcontentloaded", timeout: 15000 });
  await page.waitForTimeout(3000);
  const phaseStatuses = await page.evaluate(() => {
    // Pull the phase cards' header pills + the workflow nodes.
    const cards = Array.from(
      document.querySelectorAll("[id^='phase-card-'], [aria-label^='Spotlight phase']"),
    );
    const cardStatuses = cards.map((c) => {
      const badge = c.querySelector("span.inline-flex");
      const title = c.querySelector("h3");
      return {
        title: title?.textContent ?? "",
        badge: badge?.textContent ?? "",
      };
    });
    const nodes = Array.from(
      document.querySelectorAll("[aria-label^='Phase '][aria-label*='status']"),
    ).map((n) => n.getAttribute("aria-label") ?? "");
    return { cards: cardStatuses, nodes };
  });
  note("BUG4", "phase cards (truncated)", JSON.stringify(phaseStatuses.cards.slice(0, 7)));
  note("BUG4", "node aria-labels", JSON.stringify(phaseStatuses.nodes.slice(0, 11)));
  await page.screenshot({ path: `${OUT}/bug4-event118-rail+timeline.png`, fullPage: true });

  // ── Bug 5: Submit Real button on /events/112 should be disabled
  await page.goto(`${BASE}/events/112`, { waitUntil: "domcontentloaded", timeout: 15000 });
  await page.waitForTimeout(3000);
  await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
  await page.waitForTimeout(500);
  await page.screenshot({ path: `${OUT}/bug5-event112.png`, fullPage: true });
  const submitReal = await page.evaluate(() => {
    const btn = Array.from(document.querySelectorAll("button")).find((b) =>
      /Submit Real/i.test(b.textContent ?? ""),
    );
    if (!btn) return null;
    return {
      text: btn.textContent?.trim() ?? "",
      disabled: btn.disabled,
      title: btn.getAttribute("title") ?? "",
    };
  });
  note("BUG5", "Submit Real button", JSON.stringify(submitReal));

  await writeFile(`${OUT}/results.json`, JSON.stringify(results, null, 2));
  console.log(`\nWrote ${results.length} probes to ${OUT}/results.json`);

  await browser.close();
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
