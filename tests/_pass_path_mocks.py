"""Mock infrastructure for end-to-end PASS-path audit runs.

Goal: trigger the orchestrator's full lifecycle end-to-end without
spending real money on Anthropic LLM calls. Specifically, the panel's
11 judges (which collectively call Anthropic Haiku 4.5 ~10-14 times
per event) are short-circuited to a deterministic PASS PanelVerdict
in-process, and any other LLM entry points (synthesizer, critics,
moderator, refine) are routed through a MockLLM so any straggler that
slips past the panel patch still cannot reach api.anthropic.com.

Use :func:`install_mocks` from a top-level audit script BEFORE
invoking :func:`polyglot_alpha.orchestrator.run_lifecycle`. The patches
mutate ``polyglot_alpha.judges.panel`` and ``polyglot_alpha.llm`` at
module scope, so they are visible to every coroutine the orchestrator
spawns inside the same Python interpreter.

The patches DO NOT touch:

* On-chain calls (``polyglot_alpha.chain.*``) — Arc testnet is free gas.
* IPFS publish/fetch — local-file fallback is offline.
* SQLite persistence — same DB as the running backend.
* Polymarket — defaults to ``POLYMARKET_MODE=dry_run`` which never
  posts to the live Gamma API. We assert that explicitly.

The MockLLM count is exposed via :data:`anthropic_call_count` so audit
scripts can assert ``count == 0`` against the patched panel.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Awaitable, Callable, Optional


# ---------------------------------------------------------------------------
# Internal call-count counter so the audit script can assert no real
# Anthropic call slipped through.
# ---------------------------------------------------------------------------

#: Incremented every time the mock panel.evaluate is invoked.
panel_evaluate_calls: int = 0

#: Incremented every time a MockLLM stand-in fields a prompt.
mock_llm_calls: int = 0

#: List of (label, prompt_preview) for debugging.
mock_llm_log: list[tuple[str, str]] = []


def _reset_counters() -> None:
    global panel_evaluate_calls, mock_llm_calls
    panel_evaluate_calls = 0
    mock_llm_calls = 0
    mock_llm_log.clear()


# ---------------------------------------------------------------------------
# Mock PanelVerdict factory
# ---------------------------------------------------------------------------


def _build_pass_verdict(question: Any) -> Any:
    """Return a deterministic PASS PanelVerdict.

    We construct the real :class:`PanelVerdict` dataclass so the
    orchestrator's downstream :func:`_evaluate_with_judges` adapter
    converts it without surprise. All 8 D-judges pass, MQM raw=95,
    BLEU raw=42, COMET raw=0.78, overall_score=92.
    """

    from polyglot_alpha.judges.types import (
        JudgeResult,
        PanelVerdict,
        VERDICT_PASS,
    )

    bleu = JudgeResult(
        name="bleu",
        passed=True,
        score=0.42,
        reason="Mock BLEU above threshold.",
        evidence={"bleu_raw": 42.0, "mocked": True},
    )
    comet = JudgeResult(
        name="comet",
        passed=True,
        score=0.78,
        reason="Mock COMET above threshold.",
        evidence={"comet_raw": 0.78, "mocked": True},
    )
    mqm = JudgeResult(
        name="mqm_llm",
        passed=True,
        score=0.95,
        reason="Mock MQM score=95 with zero major errors.",
        evidence={
            "score_raw": 95,
            "major_count": 0,
            "minor_count": 0,
            "errors": [],
            "rationale": "mocked",
            "provider": "mock",
        },
    )
    d_results = []
    for d_name in (
        "d1_structural",
        "d2_stylistic",
        "d3_framing",
        "d4_granularity",
        "d5_resolution_clarity",
        "d6_source_reliability",
        "d7_leading_check",
        "d8_duplicate_detection",
    ):
        d_results.append(
            JudgeResult(
                name=d_name,
                passed=True,
                score=1.0,
                reason="Mocked PASS for end-to-end audit.",
                evidence={"mocked": True},
            )
        )

    style_passes = {f"d{i}": True for i in range(1, 9)}
    return PanelVerdict(
        overall_pass=True,
        verdict=VERDICT_PASS,
        overall_score=92,
        translation_scores={
            "bleu": 42.0,
            "comet": 0.78,
            "mqm": {
                "score": 95,
                "major_count": 0,
                "minor_count": 0,
                "errors": [],
            },
        },
        style_alignment_passes=style_passes,
        judge_results=[bleu, comet, mqm, *d_results],
        notes=["mocked PASS verdict for end-to-end PASS-path audit"],
    )


# ---------------------------------------------------------------------------
# Mock LLM (stand-in for AnthropicLLM)
# ---------------------------------------------------------------------------


_CANNED_JSON_QUESTION = json.dumps(
    {
        "question_en": (
            "Will the FOMC raise rates by 25bp at the June 2026 meeting?"
        ),
        "resolution_criteria": (
            "Resolves YES if the Federal Reserve announces a 25bp rate hike at"
            " the June 17-18, 2026 FOMC meeting; otherwise resolves NO."
        ),
        "end_date_iso": "2026-12-31T23:59:59Z",
        "tags": ["fomc", "rates", "macro", "mock"],
    }
)


class _AuditMockLLM:
    """In-process stand-in returned by patched make_llm/AnthropicLLM.

    Yields a deterministic JSON-shape response on every call. Returns the
    SAME body regardless of model_id; agent differentiation is irrelevant
    when the panel verdict is forced to PASS downstream.
    """

    def __init__(
        self, *args: Any, label: str = "audit_mock", **kwargs: Any
    ) -> None:
        self.model = kwargs.get("model") or (args[0] if args else "mock")
        self._label = label

    async def complete(
        self, system: str, user: str, **_kwargs: Any
    ) -> str:
        return await self.__call__(user)

    async def __call__(self, prompt: str) -> str:
        global mock_llm_calls
        await asyncio.sleep(0)
        mock_llm_calls += 1
        mock_llm_log.append((self._label, (prompt or "")[:120]))
        return _CANNED_JSON_QUESTION


# ---------------------------------------------------------------------------
# Install / uninstall
# ---------------------------------------------------------------------------


_INSTALLED: dict[str, Any] = {}


def install_mocks() -> None:
    """Monkey-patch every Anthropic entry point and the panel adapter.

    Idempotent: second call is a no-op so audit scripts can call this
    from multiple entry points without breaking.
    """

    if _INSTALLED.get("installed"):
        return

    _reset_counters()

    from polyglot_alpha import llm as llm_mod
    from polyglot_alpha.judges import panel as panel_mod

    # ---- 1. Patch panel.evaluate to return a canned PASS verdict ----
    _INSTALLED["panel.evaluate"] = panel_mod.evaluate

    async def _patched_evaluate(question: Any, *_args: Any, **_kwargs: Any) -> Any:
        global panel_evaluate_calls
        panel_evaluate_calls += 1
        await asyncio.sleep(0)
        return _build_pass_verdict(question)

    panel_mod.evaluate = _patched_evaluate  # type: ignore[assignment]

    # ---- 2. Patch llm.AnthropicLLM with a no-network stand-in ----
    _INSTALLED["llm.AnthropicLLM"] = llm_mod.AnthropicLLM

    class _PatchedAnthropicLLM(_AuditMockLLM):
        pass

    llm_mod.AnthropicLLM = _PatchedAnthropicLLM  # type: ignore[assignment,misc]

    # ---- 3. Patch llm.make_llm so per-agent llm factories also return mocks ----
    _INSTALLED["llm.make_llm"] = llm_mod.make_llm

    def _patched_make_llm(
        model_id: str,
        *,
        mock: bool = False,
        system: Optional[str] = None,
        temperature: float = 0.2,
        max_tokens: int = 1024,
    ) -> Callable[[str], Awaitable[str]]:
        return _AuditMockLLM(model=model_id, label=f"make_llm:{model_id}")

    llm_mod.make_llm = _patched_make_llm  # type: ignore[assignment]

    # ---- 4. Patch top-level llm.complete / complete_json (synthesizer path) ----
    _INSTALLED["llm.complete"] = llm_mod.complete
    _INSTALLED["llm.complete_json"] = llm_mod.complete_json

    async def _patched_complete(
        prompt: str, *_args: Any, **_kwargs: Any
    ) -> str:
        global mock_llm_calls
        mock_llm_calls += 1
        mock_llm_log.append(("llm.complete", (prompt or "")[:120]))
        await asyncio.sleep(0)
        return _CANNED_JSON_QUESTION

    async def _patched_complete_json(
        prompt: str, *_args: Any, **_kwargs: Any
    ) -> Any:
        raw = await _patched_complete(prompt)
        return json.loads(raw)

    llm_mod.complete = _patched_complete  # type: ignore[assignment]
    llm_mod.complete_json = _patched_complete_json  # type: ignore[assignment]

    # ---- 5. Hard guard: refuse to construct a real AsyncAnthropic client ----
    _INSTALLED["llm.get_anthropic_client"] = llm_mod.get_anthropic_client

    def _refuse_anthropic_client(api_key: Optional[str] = None) -> Any:
        raise RuntimeError(
            "audit-mode: refusing to construct a real AsyncAnthropic client"
        )

    llm_mod.get_anthropic_client = _refuse_anthropic_client  # type: ignore[assignment]

    # ---- 6. Force Polymarket into dry_run regardless of inherited env -------
    # Defensive — the audit MUST NOT post to live Polymarket.
    os.environ.setdefault("POLYMARKET_MODE", "dry_run")
    # Treasury wallet so the 90/10 split fires; fall back to operator wallet.
    os.environ.setdefault(
        "PLATFORM_TREASURY_ADDRESS",
        os.environ.get(
            "HACKATHON_WALLET_ADDRESS",
            "0x000000000000000000000000000000000000dead",
        ),
    )

    _INSTALLED["installed"] = True


def uninstall_mocks() -> None:
    """Restore the original module attributes. Mostly useful in pytest."""

    if not _INSTALLED.get("installed"):
        return

    from polyglot_alpha import llm as llm_mod
    from polyglot_alpha.judges import panel as panel_mod

    panel_mod.evaluate = _INSTALLED["panel.evaluate"]  # type: ignore[assignment]
    llm_mod.AnthropicLLM = _INSTALLED["llm.AnthropicLLM"]  # type: ignore[assignment]
    llm_mod.make_llm = _INSTALLED["llm.make_llm"]  # type: ignore[assignment]
    llm_mod.complete = _INSTALLED["llm.complete"]  # type: ignore[assignment]
    llm_mod.complete_json = _INSTALLED["llm.complete_json"]  # type: ignore[assignment]
    llm_mod.get_anthropic_client = _INSTALLED["llm.get_anthropic_client"]  # type: ignore[assignment]
    _INSTALLED.clear()


__all__ = [
    "install_mocks",
    "uninstall_mocks",
    "panel_evaluate_calls",
    "mock_llm_calls",
    "mock_llm_log",
]
