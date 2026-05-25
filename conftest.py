"""Top-level conftest that puts the project root on sys.path.

This avoids a `pip install -e .` requirement for the bare-bones venv
that ships with the repo. Pytest auto-discovers this file when invoked
from the project root.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).parent.resolve()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
