# Security Audit Report — PolyglotAlpha v2

Audit date: 2026-05-26 (read-only)
Auditor: Claude (adversarial pass)

## Summary

| Metric | Value |
|---|---|
| Total findings | 9 |
| Critical | 1 |
| High | 3 |
| Medium | 3 |
| Low | 2 |
| Secrets exposed (staged in git) | **YES** |
| SQL injection risk | NO |
| XSS risk in app code | NO |
| Wallet key handling | SAFE in source code (env-only, never persisted) |

## Findings

### #1 [CRITICAL] `.env` staged in git index with real API keys + wallet private key
- **Location**: `/Users/messili/codebase/polyglot-alpha/.env` (also listed in `.gitignore` but already in index)
- **Evidence**: `git ls-files .env` returns `.env`; `git status` shows `new file: .env` staged for the initial commit. File contains populated values:
  - `GEMINI_API_KEY` (39 chars, [REDACTED])
  - `GOOGLE_API_KEY` (39 chars, [REDACTED])
  - `OPENROUTER_API_KEY` (73 chars, [REDACTED])
  - `HACKATHON_WALLET_PRIVATE_KEY` (66 chars = 0x + 64 hex, [REDACTED])
  - `POLYMARKET_BUILDER_CODE` (25 chars, [REDACTED])
- **Risk**: As soon as the operator runs `git commit && git push`, all four secrets leak permanently to GitHub history. The wallet private key controls `HACKATHON_WALLET_ADDRESS` and any USDC / contract operator role wired to it; the OpenRouter key bills the operator's account.
- **Fix**:
  1. `git rm --cached .env` immediately (do this BEFORE the first commit).
  2. Confirm `.env` is in `.gitignore` (it is, line 13).
  3. Rotate all four secrets even if no commit has happened — they have been on disk in cleartext for ~24h and there is no audit trail.
  4. Provide a `.env.example` template with key names only.

### #2 [HIGH] No authentication on any API endpoint
- **Location**: `polyglot_alpha/api/main.py` + all of `polyglot_alpha/api/routes/*.py`
- **Evidence**: `grep -rE "Depends\(|HTTPBearer|require_auth|authenticate" polyglot_alpha/api/` returns only `Depends(get_db)` (DB session injection, not auth). The destructive `POST /trigger/event` is publicly invokable and spawns full lifecycle work (LLM calls, DB writes, on-chain txs).
- **Risk**: Anyone reaching the FastAPI host can: trigger arbitrary lifecycle runs (burning OpenRouter / Gemini quota and on-chain gas), enumerate all events / bids / translations, read agent reputation. Combined with finding #3, weaponizable as wallet-draining DoS.
- **Fix**: Add HTTP bearer or API key dep on mutating routes (`/trigger/*`). Read-only routes can stay open behind CORS.

### #3 [HIGH] Permissive CORS + no rate limiting → wallet drain DoS
- **Location**: `polyglot_alpha/api/main.py:33-52`
- **Evidence**:
  ```python
  raw = os.environ.get("CORS_ORIGINS", "*")
  ...
  allow_origins=_build_cors_origins(),
  allow_credentials=True,
  allow_methods=["*"], allow_headers=["*"],
  ```
  Default is `*` (no `CORS_ORIGINS` set in `.env`). `grep -rE "limiter|RateLimit|slowapi|throttle" polyglot_alpha/api/` returns nothing — zero rate limiting.
- **Risk**: A malicious page (or curl loop) can hit `/trigger/event` thousands of times, draining OpenRouter quota, exhausting the agent wallet gas, and flooding the DB. The `allow_credentials=True` + `*` combination is technically rejected by browsers but the wildcard still allows any non-credentialed origin.
- **Fix**: (a) Default `CORS_ORIGINS` to an explicit allowlist (e.g. `http://localhost:3000`). (b) Install `slowapi` and rate-limit `/trigger/event` (e.g. 5/min/IP). (c) Cap `mock_bids` list length (see #4).

### #4 [HIGH] Unbounded input on `/trigger/event` (memory/DOS)
- **Location**: `polyglot_alpha/api/routes/trigger.py:21-46`
- **Evidence**: `TriggerRequest` has no `max_length` on `title`, no max items on `sources`, and `mock_bids: list[dict[str, Any]] | None` accepts arbitrary-size JSON with no schema validation on inner dicts.
- **Risk**: An attacker can POST a multi-megabyte `mock_bids` array; FastAPI will materialize the full list, the orchestrator will iterate it, and memory may balloon. Combined with #2 (no auth), trivially exploitable.
- **Fix**: Add `Field(max_length=200)` on `title`, `max_length=20` on `sources`, define a proper `BidRequest` BaseModel for items, and cap `mock_bids` to e.g. 50 entries.

### #5 [MEDIUM] Reentrancy pattern in `JudgePanel` and `TranslationAuction` (checks-effects-interactions violation)
- **Location**:
  - `contracts/src/JudgePanel.sol:98-105` (`registerTranslationJudge`)
  - `contracts/src/JudgePanel.sol:109-116` (`registerStyleJudge`)
  - `contracts/src/TranslationAuction.sol:166-174` (`registerAgent`)
- **Evidence**: `usdc.transferFrom(...)` happens BEFORE the state updates (`judgeStakes`, `isTranslationJudge`, `registered`). Slither flags as `reentrancy-no-eth` (9 medium findings total per `outputs/coverage/slither-summary.txt`).
- **Risk**: With canonical USDC (no fallback hook on `transferFrom`) this is **safe in practice**. However, the contracts accept any `IERC20` constructor argument; if ever pointed at a malicious / ERC777-style token, the attacker could re-enter `registerAgent` repeatedly before `registered[msg.sender] = true` takes effect, double-registering or bypassing the unique-stake invariant.
- **Fix**: Reorder all three functions to set state first, then call `transferFrom`. Optionally pin the USDC address in the constructor and `require(_usdc == CANONICAL_USDC)`.

### #6 [MEDIUM] `divide-before-multiply` in `ReputationRegistry` (precision loss)
- **Location**: `contracts/src/ReputationRegistry.sol:237-254` (`_recompute`), `:259-299` (`_fillSignal`)
- **Evidence**: Slither flags 6 instances:
  - `signal = (((winRate * qualityRate) / ONE) * ...)`
  - `x = cumulativeFees / FEE_SCALE` followed by later multiplication
  - `t = (num * ONE) / den` then multiplied
- **Risk**: Reputation scores rounded down at intermediate steps; over time, accumulated drift could push an agent below the 0.7 reputation gate that ranks bidders. Not exploitable for theft but affects fairness.
- **Fix**: Reorder operations — multiply first, divide last (or use a higher fixed-point scale).

### #7 [MEDIUM] Critical Next.js vulnerabilities in production build
- **Location**: `ui/package.json` → `"next": "14.2.18"`
- **Evidence**: `npm audit --omit=dev` reports:
  - 1 critical: `next` (23 advisories at this version — SSRF, auth bypass in middleware, DoS, cache poisoning, XSS in App Router CSP nonces, etc.)
  - 1 moderate: `postcss < 8.5.10` (XSS via unescaped `</style>`)
  - Fixed in `next@15.5.18` (breaking change)
- **Risk**: If the UI is ever exposed beyond localhost, multiple unauthenticated attacks apply (auth bypass GHSA-f82v-jwr5-mffw, SSRF GHSA-4342-x723-ch2f, cache poisoning).
- **Fix**: Upgrade to `next@15.5.x` and test the App Router routes (Next 15 changes async params/headers). For demo-only localhost use, document the risk in README.

### #8 [LOW] Vulnerable `transformers` dependency
- **Location**: `polyglot-alpha` Python venv → `transformers==4.57.6`
- **Evidence**: `pip-audit` reports 2 known vulns (`PYSEC-2025-217`, `CVE-2026-1839`); fixed in `5.0.0rc3`.
- **Risk**: Only matters if a translator agent ever loads an untrusted HuggingFace model artifact (which the current code does not — translators call hosted LLMs through OpenRouter/Gemini). No exploit path today, but it's a latent supply chain hole.
- **Fix**: Pin `transformers>=5.0.0` in `pyproject.toml`, or drop the dependency if unused.

### #9 [LOW] `outputs/agent_wallets.json` written world-readable
- **Location**: `polyglot_alpha/agents/runner.py:42-73`
- **Evidence**: `bootstrap_wallets()` writes `outputs/agent_wallets.json` with the public payload (addresses + env var names only — verified, no private keys). File created with default umask (0644 on macOS dev).
- **Risk**: Low. Only addresses are leaked; private keys correctly stay in memory and are printed to stderr for the operator to stash in env vars. Documented behavior in module docstring.
- **Fix**: Optional — `os.chmod(target, 0o600)` for defense in depth.

## OWASP Top 10 Coverage

| ID | Category | Status |
|---|---|---|
| A01 | Broken Access Control | **FAIL** (#2 no auth on `/trigger`, #3 wildcard CORS) |
| A02 | Cryptographic Failures | **FAIL** (#1 secrets in `.env` staged in git) |
| A03 | Injection | PASS (parameterized SQLModel queries throughout; no shell/eval/yaml.load) |
| A04 | Insecure Design | **PARTIAL** (#4 no input limits, #5 CEI violation) |
| A05 | Security Misconfiguration | **FAIL** (#3 CORS `*`, `allow_credentials=True`) |
| A06 | Vulnerable Components | **FAIL** (#7 Next.js critical, #8 transformers) |
| A07 | Auth Failures | **FAIL** (#2) |
| A08 | Software & Data Integrity | PASS (no insecure deserialization; no pickle.loads, no yaml.load without SafeLoader) |
| A09 | Logging Failures | PASS (no private keys / passwords / tokens in logger calls) |
| A10 | SSRF | **PARTIAL** (`TriggerSource.url` is consumed by the orchestrator without allowlist — out of scope for this pass; review `polyglot_alpha/ingestion/`) |

## Recommendations (priority order)

1. **Right now, before any `git commit`**: `git rm --cached .env`, then rotate the 4 secrets in the file (Gemini, Google, OpenRouter, hackathon wallet). Replace with a `.env.example` template.
2. Add an API key check (`X-API-Key` header verified against `os.environ["API_TOKEN"]`) on `/trigger/event` at minimum.
3. Replace default `CORS_ORIGINS="*"` with `"http://localhost:3000"` and require explicit env override for prod.
4. Install `slowapi`, rate-limit `/trigger/event` to 5/min/IP, cap `TriggerRequest.title` (max_length=200) and `mock_bids` (max 50 items).
5. Apply checks-effects-interactions reorder in `JudgePanel.register*` and `TranslationAuction.registerAgent` — cheap one-line fix per function.
6. Plan a Next.js 14 → 15 upgrade post-demo; document the unpatched advisories in README for honesty.
7. Pin `transformers>=5.0.0` or remove if unused (it isn't called anywhere in `polyglot_alpha/`).

## Files reviewed
- `polyglot_alpha/api/main.py`, `polyglot_alpha/api/routes/{agents,builder_fees,events,leaderboard,sse,trigger}.py`
- `polyglot_alpha/agents/{base,runner,deepseek_agent,gemini_agent,llama_agent,qwen_agent}.py`
- `polyglot_alpha/onchain.py`, `polyglot_alpha/persistence/`
- `contracts/src/{BuilderFeeRouter,JudgePanel,QuestionRegistry,ReputationRegistry,TranslationAuction}.sol`
- `ui/package.json`, `ui/next.config.mjs`, `ui/lib/api.ts`
- `.env`, `.gitignore`, `pyproject.toml`
- `outputs/coverage/{slither-summary.txt,slither-issues.json}`
- `outputs/agent_wallets.json`
