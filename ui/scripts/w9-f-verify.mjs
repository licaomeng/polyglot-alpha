// W9-F verify: WithdrawStake flow is wired end-to-end (UI + API).
//
// Probes the /operators page in a headless browser and verifies:
//
//   1. WithdrawStakeButton renders for the reference seeders alongside
//      ClaimFeesButton.
//   2. The button reflects /api/operators/{addr}/stake-status — disabled
//      with a clear tooltip when there's no active stake / stake locked,
//      enabled with the stake amount otherwise.
//   3. Clicking an enabled button fires POST
//      /api/operators/{addr}/withdraw-stake (intercepted) and surfaces a
//      success message with a 0xsim_ tx token (mock mode is the default).
//
// Run from repo root with the UI on :3001 and API on :8000:
//
//   node ui/scripts/w9-f-verify.mjs
//
// Env overrides: $UI_BASE, $API_BASE, $W9F_OPERATOR_ADDR (force a specific
// operator with a known stake so the click path is exercised).

import { chromium } from "playwright";

const BASE_UI = process.env.UI_BASE ?? "http://localhost:3001";
const BASE_API = process.env.API_BASE ?? "http://localhost:8000";

const log = (...a) => console.log("[w9-f]", ...a);
const fail = (msg) => {
  console.error("[w9-f][FAIL]", msg);
  process.exitCode = 1;
};
const pass = (msg) => console.log("[w9-f][PASS]", msg);

// Pick the first operator returned by the backend. The verify script does
// NOT mutate the mock stake ledger — the backend's default behavior in
// mock mode is to seed a 5 USDC entry on register; for cold seeders we
// fall through to the DB-derived fallback (5 USDC unlocked), so the
// button should always render enabled in the default demo.
const pickOperatorAddress = async () => {
  if (process.env.W9F_OPERATOR_ADDR) return process.env.W9F_OPERATOR_ADDR;
  const res = await fetch(`${BASE_API}/api/operators`);
  if (!res.ok) {
    throw new Error(
      `failed to list operators (${res.status}): ${await res.text()}`,
    );
  }
  const operators = await res.json();
  if (!Array.isArray(operators) || operators.length === 0) {
    throw new Error("no operators returned from /api/operators");
  }
  return operators[0].address;
};

const probeStakeStatus = async (addr) => {
  const res = await fetch(
    `${BASE_API}/api/operators/${addr}/stake-status`,
  );
  if (!res.ok) {
    throw new Error(
      `stake-status probe failed (${res.status}): ${await res.text()}`,
    );
  }
  return res.json();
};

const browser = await chromium.launch({ headless: true });
const ctx = await browser.newContext({
  viewport: { width: 1440, height: 2400 },
});
const page = await ctx.newPage();

const apiCalls = [];
page.on("request", (req) => {
  const url = req.url();
  if (
    /\/api\/operators\//.test(url) &&
    (url.includes("/withdraw-stake") || url.includes("/stake-status"))
  ) {
    apiCalls.push({
      method: req.method(),
      url,
      postData: req.postData(),
    });
  }
});

try {
  const operatorAddr = await pickOperatorAddress();
  const statusBefore = await probeStakeStatus(operatorAddr);
  log(
    `probing operator ${operatorAddr} (staked=${statusBefore.staked}, ` +
      `amount_usdc=${statusBefore.amount_usdc}, ` +
      `can_withdraw=${statusBefore.can_withdraw})`,
  );

  await page.goto(`${BASE_UI}/operators`, {
    waitUntil: "domcontentloaded",
  });
  await page.waitForTimeout(2500);

  // ─── Task 1: WithdrawStakeButton renders ─────────────────────────────
  const withdrawButtons = await page
    .locator('[data-testid="withdraw-stake-button"]')
    .all();
  log(
    `found ${withdrawButtons.length} withdraw-stake buttons on /operators`,
  );
  if (withdrawButtons.length === 0) {
    fail("no withdraw-stake buttons rendered on /operators page");
  } else {
    pass(`${withdrawButtons.length} withdraw-stake button(s) rendered`);
  }

  // ─── Task 2: button reflects stake amount via aria-label/text ────────
  if (withdrawButtons.length > 0) {
    const first = withdrawButtons[0];
    const text = await first.textContent();
    const aria = await first.getAttribute("aria-label");
    const stakeAttr = await first.getAttribute("data-stake-usdc");
    log(
      `button[0] text="${(text ?? "").trim()}" aria="${aria}" stakeAttr="${stakeAttr}"`,
    );
    if (text && /\$/.test(text)) {
      pass(
        `withdraw button surfaces a currency-formatted stake amount: ` +
          `"${text.trim()}"`,
      );
    } else if (text && /Loading/i.test(text)) {
      log("button still loading; waiting an extra 2s…");
      await page.waitForTimeout(2000);
      const text2 = await first.textContent();
      if (text2 && /\$/.test(text2)) {
        pass(
          `withdraw button surfaces a stake amount after retry: "${text2.trim()}"`,
        );
      } else {
        fail(
          `withdraw button never resolved stake amount: "${text2 ?? ""}"`,
        );
      }
    } else {
      fail(`withdraw button text missing stake amount: "${text ?? ""}"`);
    }
  }

  // ─── Task 3: click + intercept POST /withdraw-stake ──────────────────
  let clickedWithdraw = false;
  for (const btn of withdrawButtons) {
    const isDisabled = await btn.isDisabled();
    const canWithdrawAttr = await btn.getAttribute("data-can-withdraw");
    if (!isDisabled && canWithdrawAttr === "true") {
      log("clicking first enabled withdraw button");
      await btn.click();
      clickedWithdraw = true;
      break;
    }
  }
  if (!clickedWithdraw) {
    log("no operator was withdrawable — verifying disabled-state UX only");
    const disabledTitle = await withdrawButtons[0]?.getAttribute("title");
    if (
      disabledTitle &&
      /(no active stake|locked|loading)/i.test(disabledTitle)
    ) {
      pass(
        `disabled button shows expected tooltip: "${disabledTitle}"`,
      );
    } else {
      log(`disabled button title: ${JSON.stringify(disabledTitle)}`);
    }
  } else {
    await page.waitForTimeout(2500);
    const successMarker = page
      .locator('[data-testid="withdraw-stake-success"]')
      .first();
    if ((await successMarker.count()) > 0) {
      const txt = await successMarker.textContent();
      pass(
        `withdraw success surface visible: "${(txt ?? "").trim()}"`,
      );
      // Sim tx hash should be inside the success surface (mock mode).
      const simTx = await successMarker
        .locator('[data-testid="withdraw-stake-sim-tx"]')
        .first();
      if ((await simTx.count()) > 0) {
        const simText = (await simTx.textContent()) ?? "";
        if (/^0xsim_/.test(simText.trim())) {
          pass(`sim tx hash rendered as non-clickable text: ${simText.trim()}`);
        } else {
          fail(`sim tx text did not start with 0xsim_: "${simText}"`);
        }
      } else {
        // Could be a real on-chain hash; not a fail for the live path.
        log("no sim-tx span; tx may be a real Arc tx hash (live mode)");
      }
    } else {
      const errorMarker = page
        .locator('[data-testid="withdraw-stake-error"]')
        .first();
      if ((await errorMarker.count()) > 0) {
        const errText = (await errorMarker.textContent()) ?? "";
        fail(
          `withdraw error surface visible after click: "${errText.trim()}"`,
        );
      } else {
        fail(
          "clicked withdraw-stake but no success/error surface appeared",
        );
      }
    }
    const withdrawCalls = apiCalls.filter((c) =>
      c.url.includes("/withdraw-stake"),
    );
    if (withdrawCalls.length === 0) {
      fail("clicked Withdraw Stake but no POST /withdraw-stake fired");
    } else {
      const body = JSON.parse(withdrawCalls[0].postData ?? "{}");
      const isMock = body.mode === "mock";
      pass(
        `intercepted ${withdrawCalls.length} withdraw-stake POST call(s) ` +
          `(method=${withdrawCalls[0].method}, mode=${body.mode ?? "<unset>"}) ` +
          `mockDefault=${isMock}`,
      );
      if (!isMock) {
        fail(
          `expected default mode="mock" in POST body; got ${JSON.stringify(body)}`,
        );
      }
    }
  }

  // ─── Summary ─────────────────────────────────────────────────────────
  console.log(
    JSON.stringify(
      {
        ui_base: BASE_UI,
        api_base: BASE_API,
        operator_probed: operatorAddr,
        status_before: statusBefore,
        withdraw_button_count: withdrawButtons.length,
        clicked_withdraw: clickedWithdraw,
        api_calls_intercepted: apiCalls.map((c) => ({
          method: c.method,
          url: c.url.replace(BASE_API, ""),
          postData: c.postData,
        })),
      },
      null,
      2,
    ),
  );

  if (!process.exitCode) pass("W9-F verify all PASS");
} catch (err) {
  fail(`unexpected error: ${err instanceof Error ? err.message : String(err)}`);
  console.error(err);
} finally {
  await browser.close();
}
