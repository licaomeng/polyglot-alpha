# Licensing

PolyglotAlpha v2 uses a tiered, source-available license model.

## Why BSL 1.1

The Business Source License 1.1 keeps the backend and frontend source code public and inspectable while protecting commercial interests during the build-out phase. It is widely adopted (HashiCorp, MariaDB, CockroachDB, Sentry, Materialize) and converts automatically to a permissive OSI license after a fixed time window.

## Free Tier (no fee, no contract)

- Development, testing, evaluation, benchmarking
- Academic research and publication
- Security audit and responsible disclosure
- Personal experimentation
- Internal business operations capped at **100 markets / calendar month**

## Commercial Tiers

| Tier | Annual fee | Suitable for |
| --- | --- | --- |
| Startup | $10,000 / yr | Up to 1,000 markets / month, 1 production env |
| Growth | $50,000 / yr | Up to 25,000 markets / month, multi-region |
| Enterprise | Custom | Unlimited markets, SLA, dedicated support, on-prem |

Contact `licaomeng@gmail.com` to negotiate a commercial license.

## Source-Available Components

The following subtrees are public and covered by BSL 1.1:

- `polyglot_alpha/` (Python backend) excluding proprietary marker directories
- `ui/` (frontend)
- `scripts/`, `tests/`, `corpus/` shared infrastructure

Smart contracts under `contracts/` are MIT-licensed for ecosystem reuse.

## Proprietary Components (not in public repo)

These directories contain closed evaluator IP and are excluded from public distribution:

- `polyglot_alpha/judges/` - evaluator weights, anti-patterns, scoring rubrics
- `polyglot_alpha/corpus/` - proprietary corpus and gold references
- `polyglot_alpha/style_align/` - style alignment models

Any inadvertent appearance of these directories in a public mirror does not constitute a grant of license. Contact `licaomeng@gmail.com` for source access under NDA.

## 4-Year Auto-Conversion

On **2030-05-26** (or four years after first public distribution, whichever is earlier), the Licensed Work converts to **Apache License, Version 2.0** for the version of the work released at that date.

## Contact

- General and commercial licensing: `licaomeng@gmail.com`
- GitHub issue template: `.github/COMMERCIAL_LICENSE_INQUIRY.md`

> Note: Commercial entity (LLC) transition in progress. Until incorporation, licenses are issued by licaomeng as a natural person under the pseudonym above.
