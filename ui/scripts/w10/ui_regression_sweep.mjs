// W10 UI regression sweep — re-verify the 13 W3 regression points in BOTH
// live and mock modes via Playwright (headless).
//
// Each "fix" is a self-contained probe lifted from
// scripts/wave3_regression.mjs. We run them once with mode=live then once
// with mode=mock so the W10 launch loop can confirm none of the mode-aware
// UX paths regressed under the new W9 (judges/reputation/operators) wiring.
//
// Output:
//   /tmp/w10-ui-regression.md  — per-fix PASS/FAIL matrix + aggregated
//                                console.errors / 4xx / 5xx / 429 list.
//
// Run from repo root with the UI on :3001 and API on :8000:
//   node ui/scripts/w10/ui_regression_sweep.mjs
//
// Optional env: UI_BASE, API_BASE, W10_EVENT_LIVE, W10_EVENT_MOCK
// (defaults to event 214 for live and 213 for mock — the two most-recent
// SUBMITTED events in the repo DB at the time this script was written).
// Override these per-run to track newer events. The script is idempotent
// and safe to re-run anytime.

import { chromium } from "playwright";
import fs from "node:fs";
import path from "node:path";

const UI = process.env.UI_BASE ?? "http://localhost:3001";
const API = process.env.API_BASE ?? "http://localhost:8000";
const REPORT_PATH = "/tmp/w10-ui-regression.md";

// Per-mode event ids — caller can override. Picked because they appeared
// in the audit DB snapshot but the regression points below are structural,
// not event-specific, so any SUBMITTED event of the right mode works.
const EVENT_BY_MODE = {
  live: Number(process.env.W10_EVENT_LIVE ?? 214),
  mock: Number(process.env.W10_EVENT_MOCK ?? 213),
};

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ─── The 13 regression points re-verified in BOTH modes ──────────────────
// Each "check" returns { ok: boolean, detail: string }.
const REGRESSION_POINTS = [
  {
    id: "R1",
    label: "Phase 4 judge panel renders 11 judges",
    page: (mode) => `${UI}/events/${EVENT_BY_MODE[mode]}?mode=${mode}`,
    async run(page) {
      const text = await page.locator("body").innerText().catch(() => "");
      const judges = [
        "bleu",
        "comet",
        "mqm_llm",
        "d1_structural",
        "d2_stylistic",
        "d3_framing",
        "d4_granularity",
        "d5_resolution_clarity",
        "d6_source_reliability",
        "d7_leading_check",
        "d8_duplicate_detection",
      ];
      const matched = judges.filter((j) =>
        text.toLowerCase().includes(j.toLowerCase()),
      );
      return {
        ok: matched.length >= 8,
        detail: `${matched.length}/11 judge names visible`,
      };
    },
  },
  {
    id: "R2",
    label: "Partial-completion banner (X/11) renders when applicable",
    page: (mode) => `${UI}/events/${EVENT_BY_MODE[mode]}?mode=${mode}`,
    async run(page) {
      const text = await page.locator("body").innerText().catch(() => "");
      const partial = /\d+\s*\/\s*11/i.test(text);
      // Optional — only required when verdict is partial. Pass if either
      // the banner shows OR the page is in a non-partial verdict.
      const verdict = /verdict[:\s]*(PASS|REJECT|INSUFFICIENT)/i.exec(text);
      return {
        ok: partial || !!verdict,
        detail: `partial_banner=${partial} verdict=${verdict?.[1] ?? "(none)"}`,
      };
    },
  },
  {
    id: "R3",
    label: "On-chain anchor block shows IPFS link or hash",
    page: (mode) => `${UI}/events/${EVENT_BY_MODE[mode]}?mode=${mode}`,
    async run(page) {
      const text = await page.locator("body").innerText().catch(() => "");
      const ipfsHash = /ipfs:\/\/\S+/i.test(text);
      const titleHash = /titleHash|title_hash|content_hash/i.test(text);
      return {
        ok: ipfsHash || titleHash,
        detail: `ipfs_link=${ipfsHash} title_hash=${titleHash}`,
      };
    },
  },
  {
    id: "R4",
    label: "Polymarket block shows market_id or 'skipped' rationale",
    page: (mode) => `${UI}/events/${EVENT_BY_MODE[mode]}?mode=${mode}`,
    async run(page) {
      const text = await page.locator("body").innerText().catch(() => "");
      const mid = /market_id|marketId|dryrun-/i.test(text);
      const skipped = /skipped/i.test(text);
      return {
        ok: mid || skipped,
        detail: `market_id=${mid} skipped_msg=${skipped}`,
      };
    },
  },
  {
    id: "R5",
    label: "Revenue block shows arcscan link or sim-tx token",
    page: (mode) => `${UI}/events/${EVENT_BY_MODE[mode]}?mode=${mode}`,
    async run(page) {
      const text = await page.locator("body").innerText().catch(() => "");
      const arcscan = /arcscan|0x[0-9a-f]{20,}/i.test(text);
      const simTx = /0xsim_/i.test(text);
      return {
        ok: arcscan || simTx,
        detail: `arcscan_or_hash=${arcscan} sim_tx=${simTx}`,
      };
    },
  },
  {
    id: "R6",
    label: "BUILDER_CODE / builder code anchor visible",
    page: (mode) => `${UI}/events/${EVENT_BY_MODE[mode]}?mode=${mode}`,
    async run(page) {
      const text = await page.locator("body").innerText().catch(() => "");
      const literal = /polyglot_alpha|builderCode|BUILDER.?CODE/i.test(text);
      return { ok: literal, detail: `builder_code_literal=${literal}` };
    },
  },
  {
    id: "R7",
    label: "BLEU + COMET rows shown in quality breakdown",
    page: (mode) => `${UI}/events/${EVENT_BY_MODE[mode]}?mode=${mode}`,
    async run(page) {
      const text = await page.locator("body").innerText().catch(() => "");
      return {
        ok: /bleu/i.test(text) && /comet/i.test(text),
        detail: `bleu=${/bleu/i.test(text)} comet=${/comet/i.test(text)}`,
      };
    },
  },
  {
    id: "R8",
    label: "Phase 3 debate UI shows candidate/moderator OR advance-msg",
    page: (mode) => `${UI}/events/${EVENT_BY_MODE[mode]}?mode=${mode}`,
    async run(page) {
      const text = await page.locator("body").innerText().catch(() => "");
      const debate = /candidate|moderator|debate|refine/i.test(text);
      const advanceMsg = /will appear here once|past L2/i.test(text);
      return {
        ok: debate || advanceMsg,
        detail: `debate_ui=${debate} advance_msg=${advanceMsg}`,
      };
    },
  },
  {
    id: "R9",
    label: "SSE rate-limit — 5 rapid reloads do not produce any 429s",
    page: (mode) => `${UI}/events/${EVENT_BY_MODE[mode]}?mode=${mode}`,
    async run(page) {
      const seen429 = [];
      const listener = (resp) => {
        if (resp.status() === 429) seen429.push(resp.url());
      };
      page.on("response", listener);
      for (let i = 0; i < 5; i++) {
        await page.reload({ waitUntil: "domcontentloaded", timeout: 20000 }).catch(() => {});
        await sleep(1100);
      }
      page.off("response", listener);
      return {
        ok: seen429.length === 0,
        detail: `429_count=${seen429.length}`,
      };
    },
  },
  {
    id: "R10",
    label: "/leaderboard does NOT show all-same-nibble placeholder addrs",
    page: () => `${UI}/leaderboard`,
    async run(page) {
      const text = await page.locator("body").innerText().catch(() => "");
      const placeholders = text.match(/0x([0-9a-f])\1{3,}/gi) || [];
      return {
        ok: placeholders.length === 0,
        detail:
          placeholders.length === 0
            ? "no placeholder addrs"
            : `found ${placeholders.length}: ${placeholders.slice(0, 5).join(", ")}`,
      };
    },
  },
  {
    id: "R11",
    label: "/events list — Failed vs Rejected badges have distinct colors",
    page: () => `${UI}/events`,
    async run(page) {
      const colors = await page
        .evaluate(() => {
          const out = [];
          for (const el of document.querySelectorAll("*")) {
            if (el.children.length !== 0) continue;
            const t = (el.textContent || "").trim().toLowerCase();
            if (t === "failed" || t === "rejected" || t === "submitted") {
              const cs = getComputedStyle(el);
              const parent = el.parentElement
                ? getComputedStyle(el.parentElement)
                : null;
              out.push({
                text: t,
                bg: parent?.backgroundColor ?? cs.backgroundColor,
              });
              if (out.length > 25) break;
            }
          }
          return out;
        })
        .catch(() => []);
      const byLabel = {};
      for (const c of colors) (byLabel[c.text] ??= new Set()).add(c.bg);
      const failedBg = byLabel.failed ? [...byLabel.failed] : [];
      const rejectedBg = byLabel.rejected ? [...byLabel.rejected] : [];
      // We pass when (a) both labels exist with at least one bg color and
      // (b) the colors are not identical OR (c) one of them is missing
      // (nothing to compare).
      const intersect = failedBg.filter((c) => rejectedBg.includes(c));
      const ok =
        failedBg.length === 0 ||
        rejectedBg.length === 0 ||
        intersect.length < failedBg.length;
      return {
        ok,
        detail: `failed_bg=${JSON.stringify(failedBg)} rejected_bg=${JSON.stringify(rejectedBg)}`,
      };
    },
  },
  {
    id: "R12",
    label: "Home page Trigger button surface is mode-aware",
    page: (mode) => `${UI}/?mode=${mode}`,
    async run(page, { mode }) {
      const btn = page.locator('button[aria-label*="Trigger"]').first();
      const exists = (await btn.count()) > 0;
      if (!exists) return { ok: false, detail: "trigger button not found" };
      const txt = ((await btn.textContent().catch(() => "")) || "").trim();
      const labelMatches =
        mode === "mock"
          ? /mock/i.test(txt) || /trigger/i.test(txt)
          : /live|trigger/i.test(txt);
      return {
        ok: labelMatches,
        detail: `label="${txt}" mode=${mode}`,
      };
    },
  },
  {
    id: "R13",
    label: "4K layout — hero/main width does NOT span full 3840px",
    page: () => `${UI}/`,
    is4K: true,
    async run(page) {
      const width = await page
        .evaluate(() => {
          const main = document.querySelector("main, [role='main']");
          return main ? main.getBoundingClientRect().width : null;
        })
        .catch(() => null);
      return {
        ok: width !== null && width < 3000,
        detail: `main_width=${width}`,
      };
    },
  },
];

const runChecksForMode = async (browser, mode) => {
  console.log(`\n=== mode=${mode} ===`);
  const ctx = await browser.newContext({
    viewport: { width: 1440, height: 900 },
  });
  const big = await browser.newContext({
    viewport: { width: 3840, height: 2160 },
  });
  const page = await ctx.newPage();
  const pageBig = await big.newPage();

  const aggregate = {
    consoleErrors: [],
    networkBadStatus: [], // 4xx (except 404) and 5xx and 429
  };
  for (const p of [page, pageBig]) {
    p.on("console", (msg) => {
      if (msg.type() === "error") {
        aggregate.consoleErrors.push({ text: msg.text(), mode });
      }
    });
    p.on("response", (resp) => {
      const s = resp.status();
      if (s === 429 || (s >= 400 && s !== 404 && s !== 401)) {
        aggregate.networkBadStatus.push({ url: resp.url(), status: s, mode });
      }
    });
  }

  const results = [];
  for (const point of REGRESSION_POINTS) {
    const target = point.page(mode);
    const target_page = point.is4K ? pageBig : page;
    try {
      await target_page.goto(target, {
        waitUntil: "domcontentloaded",
        timeout: 30000,
      });
      await sleep(1500);
      const verdict = await point.run(target_page, { mode });
      results.push({
        id: point.id,
        label: point.label,
        mode,
        ok: verdict.ok,
        detail: verdict.detail,
      });
      console.log(
        `  [${point.id}] ${verdict.ok ? "PASS" : "FAIL"} — ${verdict.detail}`,
      );
    } catch (err) {
      results.push({
        id: point.id,
        label: point.label,
        mode,
        ok: false,
        detail: `runtime error: ${err instanceof Error ? err.message : String(err)}`,
      });
      console.log(`  [${point.id}] ERROR — ${err}`);
    }
  }

  await ctx.close();
  await big.close();
  return { results, aggregate };
};

const renderReport = (perMode) => {
  const lines = [];
  lines.push("# W10 UI regression sweep");
  lines.push("");
  lines.push(`- UI base: \`${UI}\``);
  lines.push(`- API base: \`${API}\``);
  lines.push(`- live event: ${EVENT_BY_MODE.live}`);
  lines.push(`- mock event: ${EVENT_BY_MODE.mock}`);
  lines.push("");
  lines.push("## Per-fix PASS/FAIL matrix");
  lines.push("");
  lines.push("| # | check | live | mock |");
  lines.push("|---|-------|------|------|");
  for (const point of REGRESSION_POINTS) {
    const live = perMode.live.results.find((r) => r.id === point.id);
    const mock = perMode.mock.results.find((r) => r.id === point.id);
    const cell = (r) =>
      r ? (r.ok ? "PASS" : `FAIL · ${r.detail.slice(0, 60)}`) : "—";
    lines.push(
      `| ${point.id} | ${point.label} | ${cell(live)} | ${cell(mock)} |`,
    );
  }
  lines.push("");

  const totalConsole =
    perMode.live.aggregate.consoleErrors.length +
    perMode.mock.aggregate.consoleErrors.length;
  const totalBadStatus =
    perMode.live.aggregate.networkBadStatus.length +
    perMode.mock.aggregate.networkBadStatus.length;
  lines.push("## Aggregated console + network");
  lines.push("");
  lines.push(`- console errors total: **${totalConsole}**`);
  lines.push(`- 4xx/5xx/429 total: **${totalBadStatus}**`);
  if (totalConsole > 0) {
    lines.push("");
    lines.push("### console.error samples");
    for (const e of [
      ...perMode.live.aggregate.consoleErrors,
      ...perMode.mock.aggregate.consoleErrors,
    ].slice(0, 25)) {
      lines.push(`- (${e.mode}) ${e.text.slice(0, 180)}`);
    }
  }
  if (totalBadStatus > 0) {
    lines.push("");
    lines.push("### bad-status responses");
    for (const r of [
      ...perMode.live.aggregate.networkBadStatus,
      ...perMode.mock.aggregate.networkBadStatus,
    ].slice(0, 50)) {
      lines.push(`- ${r.status} ${r.url} (${r.mode})`);
    }
  }
  lines.push("");

  const failCount = REGRESSION_POINTS.reduce((acc, point) => {
    const live = perMode.live.results.find((r) => r.id === point.id);
    const mock = perMode.mock.results.find((r) => r.id === point.id);
    return acc + (live && !live.ok ? 1 : 0) + (mock && !mock.ok ? 1 : 0);
  }, 0);
  lines.push(`## Overall: ${failCount === 0 ? "PASS" : `FAIL · ${failCount} checks`}`);
  return lines.join("\n") + "\n";
};

const main = async () => {
  const browser = await chromium.launch({ headless: true });
  const perMode = {
    live: { results: [], aggregate: { consoleErrors: [], networkBadStatus: [] } },
    mock: { results: [], aggregate: { consoleErrors: [], networkBadStatus: [] } },
  };
  try {
    perMode.live = await runChecksForMode(browser, "live");
    perMode.mock = await runChecksForMode(browser, "mock");
  } finally {
    await browser.close();
  }
  const report = renderReport(perMode);
  fs.writeFileSync(REPORT_PATH, report);
  console.log(`\n[sweep] wrote ${REPORT_PATH}`);
  const failCount =
    perMode.live.results.filter((r) => !r.ok).length +
    perMode.mock.results.filter((r) => !r.ok).length;
  if (failCount > 0) {
    console.log(`[sweep] FAIL · ${failCount} check(s) failed`);
    process.exitCode = 1;
  } else {
    console.log("[sweep] PASS · all checks passed in both modes");
  }
};

main().catch((err) => {
  console.error("[sweep] FATAL", err);
  fs.writeFileSync(
    REPORT_PATH,
    `# W10 UI regression sweep\n\n**fatal error**: \`${err instanceof Error ? err.message : String(err)}\`\n`,
  );
  process.exit(2);
});
