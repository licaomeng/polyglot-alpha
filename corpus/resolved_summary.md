# Polymarket Resolved Markets — Ground Truth

Snapshot of closed/resolved binary markets pulled from the Polymarket Gamma API, ordered by `endDate` descending. Used by PolyglotAlpha v2 for backtesting the 4-agent system, calibrating judge reputation, and validating the D5 dispute-detection signal.

## Stats

- **Total markets**: 5,000
- **YES resolution**: 22.0%
- **NO resolution**: 74.3%
- **DISPUTED / REFUNDED**: 0.4%
- **UMA dispute trace present**: 2.06% (critical signal for D5 — any market whose UMA status transitions include `disputed` went through at least one oracle challenge cycle)

## Top categories by lifetime volume

- **Politics** — $649.0M
- **Sports** — $570.7M
- **Geopolitics** — $264.8M
- **Crypto** — $239.6M
- **Other** — $98.2M
- **Economics** — $81.5M
- **Tech** — $16.4M
- **Pop-Culture** — $3.4M

## Per-category outcome breakdown

| category | total | YES | NO | DISPUTED | uma_rate |
| --- | --- | --- | --- | --- | --- |
| Sports | 2053 | 223 | 1683 | 18 | 0.0% |
| Other | 1294 | 288 | 979 | 0 | 3.5% |
| Crypto | 796 | 269 | 518 | 0 | 4.6% |
| Politics | 292 | 81 | 210 | 0 | 2.1% |
| Geopolitics | 279 | 67 | 211 | 0 | 2.9% |
| Economics | 172 | 93 | 79 | 0 | 1.7% |
| Tech | 69 | 50 | 19 | 0 | 4.3% |
| Pop-Culture | 45 | 31 | 14 | 0 | 0.0% |

## 20 sample markets (most recently ended)

| question | category | volume | outcome | disputed |
| --- | --- | --- | --- | --- |
| Katana FDV above $1B one day after launch? | Crypto | $0 | NO | no |
| Espresso FDV above $700M one day after launch? | Crypto | $42.1K | NO | no |
| Espresso FDV above $200M one day after launch? | Crypto | $136.1K | YES | no |
| Espresso FDV above $100M one day after launch? | Crypto | $211.1K | YES | no |
| Espresso FDV above $1B one day after launch? | Crypto | $79.5K | NO | no |
| Espresso FDV above $50M one day after launch? | Crypto | $83.3K | YES | no |
| Espresso FDV above $500M one day after launch? | Crypto | $56.1K | NO | no |
| Espresso FDV above $400M one day after launch? | Crypto | $90.7K | NO | no |
| Espresso FDV above $300M one day after launch? | Crypto | $140.4K | NO | no |
| Block Street FDV above $300M one day after launch? | Crypto | $0 | NO | no |
| Unitas Labs FDV above $20M one day after launch? | Crypto | $0 | YES | no |
| Nebula3 FDV above $200M one day after launch? | Crypto | $22.0K | NO | no |
| R2 FDV above $300M one day after launch? | Crypto | $5.4K | NO | no |
| R2 FDV above $200M one day after launch? | Crypto | $12.5K | NO | no |
| R2 FDV above $20M one day after launch? | Crypto | $1.9K | NO | no |
| P2P FDV above $10M one day after launch? | Crypto | $101.0K | YES | no |
| Unitas Labs FDV above $200M one day after launch? | Crypto | $0 | NO | no |
| Unitas Labs FDV above $500M one day after launch? | Crypto | $0 | NO | no |
| Unitas Labs FDV above $300M one day after launch? | Crypto | $0 | NO | no |
| Unitas Labs FDV above $800M one day after launch? | Crypto | $0 | NO | no |

## Top 10 by volume

| # | question | category | volume | outcome | uma |
| --- | --- | --- | --- | --- | --- |
| 1 | Will Trump nominate Judy Shelton as the next Fed chair? | Politics | $127.7M | NO | no |
| 2 | Russia x Ukraine ceasefire by June 30, 2026? | Geopolitics | $60.7M | YES | no |
| 3 | Will Trump nominate Kevin Warsh as the next Fed chair? | Politics | $59.9M | YES | yes |
| 4 | Will Trump nominate Scott Bessent as the next Fed chair? | Politics | $38.7M | NO | no |
| 5 | Will the Charlotte Hornets win the 2026 NBA Finals? | Sports | $36.5M | NO | no |
| 6 | Will Trump nominate Kevin Hassett as the next Fed chair? | Politics | $36.1M | NO | no |
| 7 | Will Trump nominate Rick Rieder as the next Fed chair? | Politics | $35.6M | NO | no |
| 8 | Monad market cap (FDV) >$4B one day after launch? | Crypto | $34.9M | NO | yes |
| 9 | Will Trump nominate Christopher Waller as the next Fed chair? | Politics | $29.2M | NO | no |
| 10 | Will Trump nominate Jerome Powell as the next Fed chair? | Politics | $27.9M | NO | no |

## UMA dispute case studies (top 8 disputed markets by volume)

- **Will Trump nominate Kevin Warsh as the next Fed chair?** — Politics, $59.9M volume, outcome `YES`
- **Monad market cap (FDV) >$4B one day after launch?** — Crypto, $34.9M volume, outcome `NO`
- **Will Trump nominate himself as the next Fed chair?** — Politics, $23.6M volume, outcome `NO`
- **US forces enter Iran by December 31?** — Geopolitics, $22.0M volume, outcome `YES`
- **Clavicular pregnancy in 2026?** — Other, $13.9M volume, outcome `YES`
- **Tesla launches unsupervised full self driving (FSD) by June 30?** — Tech, $12.2M volume, outcome `YES`
- **Iran x Israel/US conflict ends by June 30?** — Sports, $5.9M volume, outcome `YES`
- **Will Iran close the Strait of Hormuz before 2027?** — Geopolitics, $5.5M volume, outcome `YES`

## Notes on data quality

- The deprecated Gamma `/markets` endpoint caps offsets around 10k and returns at most 100 markets per page; pagination is offset-based with `order=endDate, ascending=false`.
- The `umaResolutionStatuses` field is the canonical dispute trace; we set `uma_dispute=true` whenever the JSON array contains a `"disputed"` token.
- `category` is empty in most newer market payloads. When raw category is missing we derive a coarse label from question + event-title keyword matching; markets with no keyword match are bucketed as `Other`.
- Non-Yes/No binary markets (e.g. team-vs-team sports props) expose the literal winning team label in `outcome` so backtest can still score them.
