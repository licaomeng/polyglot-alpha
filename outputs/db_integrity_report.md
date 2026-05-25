# PolyglotAlpha v2 — DB Integrity Audit

**DB:** `/Users/messili/codebase/polyglot-alpha/polyglot_alpha.db` (30 MB, SQLite)
**Mode:** READ-ONLY (`?mode=ro`)
**Audited:** 2026-05-26
**Tables audited:** 15 / 15

> Note: row counts grew between two consecutive sweeps (e.g. `events` 25 → 39, `bids` 45 → 46) — DB is being actively written by a running backend. Findings reflect the second, larger snapshot unless noted.

---

## 1. Row counts (second sweep)

| Table | Rows |
|---|---|
| events | 39 |
| bids | 46 |
| auctions | 38 |
| translations | 38 |
| quality_scores | 38 |
| questions | 31 |
| polymarket_submissions | 31 |
| builder_fee_events | 17 |
| agent_reputation | 22 |
| sources | **0 (empty)** |
| corpus_markets | 79,073 |
| few_shot_exemplars | 50 |
| style_rules | 5 |
| reference_translations | 5 |
| backtest_results | **0 (empty)** |

Two tables (`sources`, `backtest_results`) are completely empty despite having schemas + indexes.

---

## 2. Critical findings

### CRIT-1 — Bid sanity not enforced (negative + Infinity bids accepted, won auctions)

`bids` has no CHECK constraint. Found:

| bid id | event_id | agent | bid_amount | won? |
|---|---|---|---|---|
| 41 | 21 (title="neg bid") | 0x1 | **-100.0** | YES — winning_bid=-100 |
| 42 | 23 (title="inf bid") | 0x1 | **1e+308** | YES — winning_bid=1e+308 |

Both got `settlement_tx_hash` and a settled auction. Numeric overflow propagates into `agent_reputation.cumulative_fees` arithmetic if these ever pay out. Test data, but real risk if the same path runs in prod.

Bid distribution after these: `min=-100`, `max=1e+308`, `avg≈1.7e+306` (the average is dominated entirely by the infinity bid).

### CRIT-2 — Reputation table drift (race-condition artifact)

Cross-check `agent_reputation.total_wins` vs `SELECT COUNT(*) FROM auctions WHERE winner_address=ar.agent_address`:

| agent | stored wins | actual wins | drift | last_updated |
|---|---|---|---|---|
| 0xllama_agent | 2 | 5 | **+3** | 16:22:05 |
| 0xqwen_agent | 8 | 9 | +1 | 16:22:29 |
| 0xgemini_agent | 0 | 1 | +1 | 15:54:57 (stale) |
| 0xdeepseek_agent | 0 | 1 | +1 | 15:54:57 (stale) |
| 0xbbbb…bbbb | 0 | 1 | +1 | 15:43:53 (stale) |

`total_bids` matches actual for all rows; only `total_wins` lags. Smell: reputation is updated in a separate code path from auction settlement, and the win-update either fails silently, runs on a slower poll, or is racing the bid-update transaction. `cumulative_fees` matches `builder_fee_events` for all agents — so the issue is isolated to the win-counter write.

### CRIT-3 — Auction winner-selection is HIGHEST-bid (probably wrong)

For every multi-bid event, `auctions.winning_bid` equals **MAX(bids.bid_amount)** for that event — never MIN. Sample:

```
event=1  winning=0.95  min=0.45  max=0.95   <- max won
event=8  winning=0.65  min=0.45  max=0.65   <- max won
... (10/10 sampled)
```

In a PSP / pay-the-bid translator auction the lowest ask should win (cheapest translator). Either:
- the column `bid_amount` is being used as a *quality score* not a *price ask*, in which case it is mis-named, OR
- the auction selector inverted the comparison.

This is the single biggest semantic bug to verify with the product owner.

### CRIT-4 — `corpus_markets` data quality issues

- **324** rows with `state='resolved'` but `outcome IS NULL`/empty (impossible).
- **2,919** rows with `end_date < created_at` (~3.7% of corpus). Polymarket import bug or timezone confusion.
- **74,073 / 79,073 (93.7%)** rows have `embedding_idx IS NULL`. Whatever pipeline builds the embedding store has only indexed ~5,000 markets.
- **10,325** rows with `total_volume_usdc IS NULL` (column was likely added post-hoc and back-fill skipped).

### CRIT-5 — `few_shot_exemplars` is monolithic

All 50 exemplars have `role='POSITIVE_EXAMPLE'`, `judge_dimension='D2'`, `weight=1.0`, `market_id=NULL`. No NEGATIVE_EXAMPLE, no other dimensions (D1, D3–D7), no diversity. Few-shot prompting will only ever inject D2 positive examples.

`style_rules` similarly has only 5 rows covering D2/D4/D5/D7 (no D1, D3, D6).

---

## 3. FK / orphan checks — CLEAN

No orphans found:
- bids → events: 0
- auctions → events: 0
- translations → events: 0
- quality_scores → events: 0
- builder_fee_events → polymarket_submissions: 0
- few_shot_exemplars → corpus_markets: 0 dangling

Status consistency:
- 0 SUBMITTED/STREAMING/SETTLED events missing a `questions` row.
- 0 REJECTED events with PASS verdict; 0 SUBMITTED events with FAIL verdict.
- 0 NEW events with a settled auction.
- 1 event (id=22, status=AUCTION_OPEN) without auction — legitimate, auction still open.

---

## 4. NULL pollution — CLEAN

| Check | Count |
|---|---|
| bids NULL bid_amount | 0 |
| events NULL/empty content_hash | 0 |
| quality_scores NULL verdict | 0 |
| agent_reputation negative cumulative_fees | 0 |
| polymarket_submissions NULL market_id | 0 |
| questions NULL question_id_onchain / builder_code | 0 |
| translations empty IPFS trace | 0 |

---

## 5. Time anomalies — CLEAN (backend) / DIRTY (corpus)

Backend tables clean:
- 0 events `triggered_at` in the future.
- 0 auctions settled before event triggered.
- 0 translations completed before auction settled.
- 0 bids submitted after auction settled.

Corpus: **2,919** `corpus_markets` rows have `end_date < created_at`.

---

## 6. Duplicate detection — CLEAN

- `events.content_hash` UNIQUE index already in place; no dupes.
- `polymarket_submissions.market_id`: no dupes.
- `corpus_markets.market_id` is PK; no dupes.
- `questions.event_id`: no dupes (1:1 holds).
- `corpus_markets.embedding_idx` (non-NULL only): no dupes.

---

## 7. Index analysis

Existing indexes cover the hot paths well: status/triggered_at on events, event_id/submitted_at/agent_address on bids, market_id/status on polymarket_submissions, market_id/timestamp/translator_address on builder_fee_events, judge_dimension/role/market_id on few_shot_exemplars, state/category/outcome/resolved_at/embedding_idx on corpus_markets.

Gaps:
- No index on `auctions.event_id` (it is the PK so SQLite has the autoindex — fine).
- No index on `translations.event_id` (also PK — fine).
- No index on `polymarket_submissions.event_id`? Actually there is (`ix_polymarket_submissions_event_id`). Good.
- `sources` has 3 indexes on a 0-row table — wasted DDL, harmless.

---

## 8. Recommendations (schema-level — DO NOT APPLY in this session)

### Must-add CHECK constraints

```sql
-- bids
ALTER TABLE bids ADD CHECK (bid_amount > 0 AND bid_amount < 1e6);
ALTER TABLE bids ADD CHECK (stake_amount >= 0);

-- auctions
ALTER TABLE auctions ADD CHECK (winning_bid IS NULL OR (winning_bid > 0 AND winning_bid < 1e6));
ALTER TABLE auctions ADD CHECK (settled_at IS NULL OR winner_address IS NOT NULL);

-- quality_scores
ALTER TABLE quality_scores ADD CHECK (overall_score BETWEEN 0 AND 1);
ALTER TABLE quality_scores ADD CHECK (verdict IN ('PASS','FAIL'));

-- events
ALTER TABLE events ADD CHECK (status IN ('NEW','AUCTION_OPEN','SUBMITTED','REJECTED','STREAMING_REVENUE','SETTLED'));

-- builder_fee_events
ALTER TABLE builder_fee_events ADD CHECK (fill_amount >= 0 AND fee_amount >= 0 AND fee_amount <= fill_amount);

-- agent_reputation
ALTER TABLE agent_reputation ADD CHECK (total_bids >= 0 AND total_wins >= 0 AND total_wins <= total_bids AND cumulative_fees >= 0 AND avg_quality BETWEEN 0 AND 1);

-- corpus_markets
ALTER TABLE corpus_markets ADD CHECK (end_date IS NULL OR created_at IS NULL OR end_date >= created_at);
ALTER TABLE corpus_markets ADD CHECK (state != 'resolved' OR outcome IS NOT NULL);
ALTER TABLE corpus_markets ADD CHECK (total_volume_usdc IS NULL OR total_volume_usdc >= 0);
```

SQLite doesn't support ADD CHECK via ALTER — these need a table-rebuild migration. Capture as the next schema migration.

### Application-layer fixes

1. **Reputation update race** — wrap auction-settle + reputation-win-increment in a single transaction (or move reputation derivation to a view, not a denormalized table).
2. **Auction direction** — confirm whether the auction is supposed to be lowest-ask-wins; if yes, fix the selector.
3. **Bid validation** — reject `bid_amount <= 0` and `bid_amount > MAX_REASONABLE` (e.g. 100 USDC) at the API gateway.
4. **Few-shot diversity** — backfill NEGATIVE_EXAMPLEs and other judge dimensions (D1, D3–D7) so prompt construction isn't D2-only.
5. **Corpus backfill** — re-run market ingestion to (a) populate `outcome` for resolved markets, (b) fix `end_date < created_at` timestamp parsing, (c) compute embeddings for the missing 74k rows.
6. **Drop empty tables or populate them** — `sources` and `backtest_results` carry indexes but no data; either wire them up or remove.

---

## 9. Demo data quality

**Backend (events / bids / auctions / etc.)**: *mostly clean* — orphan-free, NULL-free, time-consistent. The only blemishes are 3 deliberate adversarial rows (bid=-100, bid=1e308, plus their downstream auctions) and a 5-agent reputation lag.

**Corpus (corpus_markets / few_shot_exemplars / style_rules)**: *messy* — 93.7% missing embeddings, 13% missing total_volume, 0.4% logically impossible resolved rows, and few-shot/style-rule tables severely under-populated.

**Overall:** mostly clean for the live H2H demo loop; corpus side needs work before the few-shot/judge pipeline is convincing.
