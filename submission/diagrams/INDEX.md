# Submission Diagrams

PNG renders of Mermaid diagrams from `submission/architecture.md`. All rendered at 1600x1200 with dark slate background (`#0f172a`) for Loom recording and PDF embedding. The original Mermaid source remains in `submission/architecture.md` for evaluators who want to copy-paste into mermaid.live.

| File | Content | Source |
|---|---|---|
| mmd_00.png | Component graph (10+1 components, Arc-testnet contract topology) | `submission/architecture.md` §1 |
| mmd_01.png | Phase lifecycle flowchart (Phase 1–7 with hard/soft gates) | `submission/architecture.md` §2 |
| mmd_02.png | Open / closed IP boundary (MIT vs proprietary split) | `submission/architecture.md` §3 |
| mmd_03.png | Phase 1 ship-state sequence diagram (one "Trigger live demo" click) | `submission/architecture.md` §4 |

## Render command

Rendered locally with `@mermaid-js/mermaid-cli` (`mmdc`):

```bash
for f in /tmp/mmd_*.mmd; do
  name=$(basename "$f" .mmd)
  mmdc -i "$f" -o "submission/diagrams/${name}.png" \
       -w 1600 -H 1200 --backgroundColor "#0f172a" -t dark
done
```

## Notes

- Thesis `README.md` (`/Users/messili/codebase/agora-agents-hackathon/README.md`) currently contains zero Mermaid blocks, so no thesis diagrams were rendered.
- GitHub renders the Mermaid source inline; these PNGs are for offline / PDF / video-recording use cases where Mermaid is not natively rendered.
