"""Unit tests for the four translator agents.

All tests stay offline:

* The on-chain client is monkey-patched onto each agent so no RPC calls
  are made.
* The LLM is replaced with a deterministic ``MockLLM``.

Run with: ``.venv/bin/pytest tests/test_agents.py -q``
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict
from unittest.mock import MagicMock

import pytest
from eth_account import Account

from polyglot_alpha.agents import (
    AGENT_REGISTRY,
    BaseTranslatorAgent,
    DeepSeekAgent,
    GeminiAgent,
    LlamaAgent,
    QwenAgent,
)
from polyglot_alpha.agents.runner import bootstrap_wallets
from polyglot_alpha.llm import MockLLM
from polyglot_alpha.onchain import OnChainClient, usdc_to_units
from polyglot_alpha.schemas import EvaluationResult, NewsEvent, Question


# --------------------------------------------------------------------------- #
# Fixtures                                                                    #
# --------------------------------------------------------------------------- #


@pytest.fixture()
def fresh_pk() -> str:
    """A throw-away private key. The associated wallet is never funded."""

    return Account.create().key.hex()


@pytest.fixture()
def sample_event() -> Dict[str, Any]:
    return {
        "event_id": "evt_test_001",
        "url": "https://example.com/cn/news/001",
        "title_zh": "测试事件",
        "body_zh": "中国宣布将就关税政策做出回应。" * 30,
        "cutoff_ts": 1_900_000_000,
        "topic": "geopolitics",
        "source": "test",
    }


@pytest.fixture()
def mock_llm_factory():
    """Factory returning a MockLLM that always emits a parseable JSON blob."""

    canned = json.dumps(
        {
            "question_en": "Will the tariff response be announced by 2026-12-31?",
            "resolution_criteria": (
                "Resolves YES if the State Council issues an official tariff response "
                "before 2026-12-31T23:59:59Z."
            ),
            "end_date_iso": "2026-12-31T23:59:59Z",
            "tags": ["geopolitics", "tariffs"],
            "entities": ["State Council"],
            "risks": ["delayed announcement"],
        }
    )
    return lambda: MockLLM(model_id="mock", canned_response=canned)


@pytest.fixture()
def mock_onchain():
    """A MagicMock standing in for ``OnChainClient`` so no RPC calls fire."""

    client = MagicMock(spec=OnChainClient)
    client.get_reputation.return_value = 1.0
    client.is_registered.return_value = False
    client.approve_usdc.return_value = "0xapprove"
    client.register_agent.return_value = "0xregister"
    client.submit_bid.return_value = "0xbid"
    # account_from_pk is a classmethod-style helper; delegate to the real one.
    client.account_from_pk.side_effect = OnChainClient.account_from_pk
    return client


def _make_agent(
    cls: type[BaseTranslatorAgent],
    fresh_pk: str,
    mock_llm_factory,
    mock_onchain,
) -> BaseTranslatorAgent:
    return cls(
        wallet_pk=fresh_pk,
        llm_factory=mock_llm_factory,
        reputation_history=1.0,
        onchain=mock_onchain,
    )


# --------------------------------------------------------------------------- #
# Tests                                                                       #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "cls,expected_min,expected_max",
    [
        (GeminiAgent, 0.30, 0.50),
        (DeepSeekAgent, 0.60, 0.90),
        (QwenAgent, 0.30, 1.20),
        (LlamaAgent, 0.80, 1.20),
    ],
)
def test_each_agent_bid_in_band(
    cls, expected_min, expected_max, fresh_pk, mock_llm_factory, mock_onchain, sample_event
):
    agent = _make_agent(cls, fresh_pk, mock_llm_factory, mock_onchain)
    bid = agent.bid_strategy(sample_event)
    assert expected_min <= bid <= expected_max, (
        f"{cls.__name__} bid {bid} outside [{expected_min}, {expected_max}]"
    )


@pytest.mark.asyncio
async def test_evaluate_event_returns_valid_result(
    fresh_pk, mock_llm_factory, mock_onchain, sample_event
):
    agent = GeminiAgent(
        wallet_pk=fresh_pk, llm_factory=mock_llm_factory, onchain=mock_onchain
    )
    result = await agent.evaluate_event(sample_event)
    assert isinstance(result, EvaluationResult)
    assert 0.0 <= result.confidence <= 1.0
    assert 0.0 <= result.estimated_quality <= 1.0
    assert result.expected_cost_usdc >= 0.0
    assert GeminiAgent.BID_MIN_USDC <= result.bid_amount_usdc <= GeminiAgent.BID_MAX_USDC


@pytest.mark.asyncio
@pytest.mark.parametrize("cls", list(AGENT_REGISTRY.values()))
async def test_pipeline_runs_end_to_end(
    cls, fresh_pk, mock_llm_factory, mock_onchain, sample_event
):
    agent = _make_agent(cls, fresh_pk, mock_llm_factory, mock_onchain)
    question = await agent.run_pipeline(sample_event)
    assert isinstance(question, Question)
    assert question.event_id == sample_event["event_id"]
    assert question.question_en  # non-empty
    assert question.resolution_criteria
    assert question.end_date_iso.endswith("Z")
    assert 0.0 <= question.quality_score <= 1.0


@pytest.mark.asyncio
async def test_submit_bid_serializes_correctly(
    fresh_pk, mock_llm_factory, mock_onchain, sample_event
):
    agent = GeminiAgent(
        wallet_pk=fresh_pk, llm_factory=mock_llm_factory, onchain=mock_onchain
    )
    question = Question(
        event_id="evt_test_001",
        question_en="Q?",
        resolution_criteria="criteria",
        end_date_iso="2026-12-31T23:59:59Z",
    )
    candidate_hash = agent.hash_question(question)
    assert len(candidate_hash) == 32  # sha256 -> bytes32
    tx_hash = await agent.submit_bid(
        event_id=sample_event["event_id"],
        bid_amount=0.42,
        candidate_metadata_hash=candidate_hash,
    )
    assert tx_hash == "0xbid"
    mock_onchain.submit_bid.assert_called_once()
    args, _ = mock_onchain.submit_bid.call_args
    # Args: (account, event_id_bytes, bid_units, candidate_hash)
    _, event_id_bytes, bid_units, sent_hash = args
    assert isinstance(event_id_bytes, bytes) and len(event_id_bytes) == 32
    assert bid_units == usdc_to_units(0.42)
    assert sent_hash == candidate_hash


@pytest.mark.asyncio
async def test_ensure_registered_skips_when_already_registered(
    fresh_pk, mock_llm_factory, mock_onchain
):
    mock_onchain.is_registered.return_value = True
    agent = LlamaAgent(
        wallet_pk=fresh_pk, llm_factory=mock_llm_factory, onchain=mock_onchain
    )
    result = await agent.ensure_registered()
    assert result is None
    mock_onchain.register_agent.assert_not_called()
    mock_onchain.approve_usdc.assert_not_called()


@pytest.mark.asyncio
async def test_ensure_registered_registers_when_not_yet(
    fresh_pk, mock_llm_factory, mock_onchain
):
    mock_onchain.is_registered.return_value = False
    agent = LlamaAgent(
        wallet_pk=fresh_pk, llm_factory=mock_llm_factory, onchain=mock_onchain
    )
    result = await agent.ensure_registered()
    assert result == "0xregister"
    mock_onchain.approve_usdc.assert_called_once()
    mock_onchain.register_agent.assert_called_once()


def test_bid_strategies_are_distinct(fresh_pk, mock_llm_factory, mock_onchain, sample_event):
    """Sanity check: each agent's bid for the same event is different."""

    bids = {
        name: _make_agent(cls, fresh_pk, mock_llm_factory, mock_onchain).bid_strategy(
            sample_event
        )
        for name, cls in AGENT_REGISTRY.items()
    }
    # At least 3 of the 4 should be distinct values (Qwen's topic-conditional
    # bid for "geopolitics" may coincide with Gemini's at the boundary).
    assert len(set(bids.values())) >= 3, f"Bids not differentiated: {bids}"


def test_bootstrap_wallets_writes_addresses_only(tmp_path):
    target = tmp_path / "agent_wallets.json"
    wallets = bootstrap_wallets(write_to=target)
    assert set(wallets) == set(AGENT_REGISTRY)
    on_disk = json.loads(target.read_text())
    assert set(on_disk) == set(AGENT_REGISTRY)
    # Verify private keys are NOT persisted.
    for name, entry in on_disk.items():
        assert "private_key" not in entry
        assert entry["address"].startswith("0x") and len(entry["address"]) == 42
        assert entry["env_var"] == f"{name.upper()}_WALLET_PRIVATE_KEY"
    # In-memory return value DOES include the private keys.
    for name, entry in wallets.items():
        assert entry["private_key"].startswith("0x")
        assert len(entry["private_key"]) == 66
