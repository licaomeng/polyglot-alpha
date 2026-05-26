# PolyglotAlpha v2 — Security Re-audit (2nd Pass)

**Date:** 2026-05-26
**Scope:** Verify the 1 Critical + 3 High findings from pass 1 are remediated, and look for new issues.
**Overall demo readiness:** **GREEN** (one new MEDIUM untracked-secret-leak risk; mitigation is one .gitignore line)

---

## Executive Summary

All four previous findings (CORS wildcard, no-auth on submit-real, no rate limiting, Solidity reentrancy / divide-before-multiply) are **remediated and verified by live probe**. One **new MEDIUM** has been found: the Gemini API key was captured into `outputs/perf_llm.json` and `outputs/perf_benchmark.json` via the 429 error URL. The files are untracked, but `outputs/` is not in `.gitignore`, so a future `git add .` could commit it. Fix is trivial (one .gitignore line + delete two files).

No other new High/Critical issues. No private keys in tree. No credential leakage in backend logs. No dangerous Python `eval`/`exec` or React `dangerouslySetInnerHTML`.

---

## Findings Table

| # | Check | Before | After | Status |
|---|---|---|---|---|
| 1 | npm audit (runtime) | 1 critical, 1 moderate | 0 critical, 0 high, 2 moderate (postcss transitive, not exploitable here) | PASS |
| 2 | pip-audit | 2 transformers vulns | 0 vulns ("No known vulnerabilities found") | PASS |
| 3 | slither — project code | 9 Medium | 0 Medium / 0 High (1 High + 8 Medium in `lib/openzeppelin/Math.sol` are OZ canonical mulDiv, accepted FP) | PASS |
| 4 | Secret leak in repo | n/a | 0 in committed tree; 1 in untracked `outputs/perf_*.json` (Gemini key in 429 URL) | **FAIL** (see SEC2-001) |
| 5 | CORS rejects evil.com | wildcard allowed all | 400 "Disallowed CORS origin", no ACAO header | PASS |
| 6 | Rate limit on `/trigger/event` | unlimited | 10 succeed, then 429 (slowapi 10/min) | PASS |
| 7 | submit-real safety net | open POST | 400 without confirm; 400 if `quality<0.80`; daily cap enforced in code | PASS |
| 8 | Wallet PK exposure | n/a | 0 PK refs in `ui/`; 0 PK in git tree; 0x64-hex in committed files = tx hashes only | PASS |
| 9 | Closed-IP boundary | n/a | 33 files in `judges/`+`corpus/` are committed (matches PROPRIETARY.md framing) | INFO (business choice) |
| 10 | Backend log credential leak | n/a | 0 matches for key/secret/password/token across 248 lines | PASS |

---

## Previous Findings — Verification

| Old finding | Severity | Status | Evidence |
|---|---|---|---|
| CORS `allow_origins=["*"]` with credentials | CRITICAL | **FIXED** | `polyglot_alpha/api/main.py:_build_cors_origins()` reads `CORS_ORIGINS`, rejects `*`, falls back to localhost defaults. Live probe: evil.com → HTTP 400. |
| `/events/{id}/polymarket/submit-real` open | HIGH | **FIXED** | Three gates in `polymarket.py:148-183`: `confirm_real_submission` flag, quality≥0.80, daily cap. All three verified. |
| No rate limit → DoS | HIGH | **FIXED** | slowapi `@limiter.limit("10/minute")` on trigger endpoints; default 100/min app-wide. 12-rapid-fire test produced 10 + 2x429. |
| Solidity reentrancy + divide-before-multiply | HIGH | **FIXED** | `import "@openzeppelin/contracts/utils/ReentrancyGuard.sol"`; `contract X is ReentrancyGuard` and `nonReentrant` modifier applied across TranslationAuction / JudgePanel / BuilderFeeRouter. Math.mulDiv replaces hand-rolled div/mul. |

---

## New Finding — SEC2-001 (MEDIUM)

**Title:** Gemini API key serialized into `outputs/perf_*.json` (untracked, but `outputs/` is not gitignored)

**Files:**
- `outputs/perf_llm.json`
- `outputs/perf_benchmark.json`

**Evidence:** The Gemini API returns `429 Too Many Requests for url 'https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key=AIzaSyANBPyC...'`. The performance harness writes the raw error string (including the `?key=` query param) into the JSON output. The two files are currently **untracked**, but neither `outputs/` nor `outputs/perf_*.json` is in `.gitignore` — a future blanket `git add .` would commit the key.

**Why MEDIUM not HIGH:** The key has not actually been committed (verified with `git grep` on HEAD). It is on local disk only.

**Remediation (any one):**
1. Add to `.gitignore`:
   ```
   outputs/perf_*.json
   ```
2. Or: `rm outputs/perf_llm.json outputs/perf_benchmark.json`.
3. Or: scrub the URLs in the harness (`re.sub(r"key=[^&\"]+", "key=REDACTED", text)` before writing).
4. **Rotate `GEMINI_API_KEY`** if these files have ever left this machine (Slack/email/git remote).

---

## Defense-in-depth observations (not findings)

- `polyglot_alpha/persistence/db.py:42` uses `f"PRAGMA busy_timeout={_SQLITE_BUSY_TIMEOUT_MS}"` — formatted from a module-level int constant, no user input. Safe.
- `session.exec(...)` calls throughout the codebase are SQLModel `select()` execution, not Python `exec()`. Safe.
- No `subprocess.run`, no `os.system`, no `shell=True` anywhere in `polyglot_alpha/`.
- No `dangerouslySetInnerHTML` anywhere in `ui/`.

---

## Recommendations for next pass

1. Apply SEC2-001 fix (1-line .gitignore + delete files).
2. Re-run pip-audit weekly until launch (transformers Track record shows fast CVE cycle).
3. Consider adding a pre-commit hook (`detect-secrets` or `gitleaks`) so any future `outputs/`-style leak is blocked at commit time.
4. Add a smoke test that asserts an evil-origin OPTIONS request returns 400 — protects against future regression.

---

**Demo readiness from security: GREEN** (SEC2-001 is one .gitignore line and a `rm`).
