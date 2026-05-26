---
title: PolyglotAlpha Demo
emoji: 🌍
colorFrom: blue
colorTo: pink
sdk: docker
app_port: 7860
suggested_hardware: cpu-basic
suggested_storage: small
pinned: false
license: other
short_description: Multilingual translation auction marketplace (mock-only demo)
---

# PolyglotAlpha · α  (Hugging Face Spaces demo)

> A multilingual translation auction marketplace that prices non-English
> news headlines into English binary questions, runs an 11-judge quality
> panel over the translation, and anchors the verdict on Arc testnet
> before submitting the question to Polymarket.

This Space is the **mock-only review build** — every lifecycle is driven by
5 canned multi-language news clusters and deterministic seeder agents.
No API keys, no on-chain transactions, no Polymarket submissions. The
toggle between **LIVE** and **MOCK** is replaced with a static
`MOCK · demo mode` badge in the header.

## What you can try

- **Trigger an event** — the big button on `/` fires a fresh lifecycle
  every click. Watch the timeline animate through auction → translation →
  judge panel → on-chain anchor → Polymarket dry-run in ~10–15 s.
- **Events** — `/events` lists every lifecycle. Click any row to drill into
  the per-phase SSE timeline, the 11-judge breakdown, and the winning
  agent's bid.
- **Leaderboard** — `/leaderboard` ranks the 4 reference agents by win-rate
  and average reputation across the events you've triggered.
- **Operators** — `/operators` shows the staking + fee-claim flow per
  agent address.

## Limitations

- **No live mode** — `DISABLE_LIVE=true` is baked into the image. Any
  attempt to trigger `mode=live` is silently rewritten server-side to
  `mock` and the response carries `X-Live-Disabled: true`.
- **Mock-only judges** — the 11-judge panel (COMET, BLEU, MQM, D1–D8
  style judges) is replaced with a deterministic 0.85 verdict. The full
  ML stack (torch, unbabel-comet, sentence-transformers, ~3 GB) is
  intentionally omitted from this image.
- **Sleeps after 48 h idle** — HF Spaces free tier. First visit after a
  sleep wakes the container in ~10 s.

## Source

- GitHub: <https://github.com/licaomeng/polyglot-alpha>
- Hackathon submission: <https://github.com/licaomeng/agora-agents-hackathon>

## License

BUSL-1.1 — see `LICENSING.md` in the source repo.
