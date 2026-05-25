"""11-judge quality panel: 3 translation + 8 style-alignment judges.

Public entry point is :func:`polyglot_alpha.judges.panel.evaluate` which
fans out to every judge in parallel and returns a :class:`PanelVerdict`.

See ``panel.py`` for the orchestrator and aggregate scoring rules.
"""

from polyglot_alpha.judges.types import (
    JudgeResult,
    PanelVerdict,
    PanelQuestion,
)

__all__ = ["JudgeResult", "PanelVerdict", "PanelQuestion"]
