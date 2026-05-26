"""Tests for :mod:`polyglot_alpha.ipfs`."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from polyglot_alpha import ipfs


_SAMPLE_CANDIDATE = {
    "event_id": "evt-test-001",
    "question_text": "Will it rain on demo day?",
    "agent": "gemini",
    "translation": "Test candidate body",
}


def test_canonical_json_is_deterministic() -> None:
    a = ipfs._canonical_json_bytes({"b": 2, "a": 1})
    b = ipfs._canonical_json_bytes({"a": 1, "b": 2})
    assert a == b


def test_content_hash_changes_with_payload() -> None:
    h1 = ipfs._content_hash({"x": 1})
    h2 = ipfs._content_hash({"x": 2})
    assert h1 != h2
    assert len(h1) == 64


def test_pin_candidate_falls_back_to_local_file(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No PINATA_JWT / no W3S_TOKEN / no daemon → must fall back to local file."""

    monkeypatch.delenv("PINATA_JWT", raising=False)
    monkeypatch.delenv("W3S_TOKEN", raising=False)
    monkeypatch.setattr(ipfs, "_LOCAL_PIN_DIR", tmp_path)

    # Force the local-daemon probe to fail by pointing it at an unreachable port.
    monkeypatch.setattr(ipfs, "_LOCAL_DAEMON_URL", "http://127.0.0.1:1/api/v0/add")

    uri = asyncio.run(ipfs.pin_candidate(_SAMPLE_CANDIDATE))
    assert uri.startswith("ipfs-local://")
    sha = uri.removeprefix("ipfs-local://")
    out_path = tmp_path / f"{sha}.json"
    assert out_path.exists()
    # Content-addressing property holds.
    assert ipfs._content_hash(_SAMPLE_CANDIDATE) == sha


def test_pin_candidate_with_meta_marks_local_as_not_real_pin(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("PINATA_JWT", raising=False)
    monkeypatch.delenv("W3S_TOKEN", raising=False)
    monkeypatch.setattr(ipfs, "_LOCAL_PIN_DIR", tmp_path)
    monkeypatch.setattr(ipfs, "_LOCAL_DAEMON_URL", "http://127.0.0.1:1/api/v0/add")

    uri, is_real = asyncio.run(ipfs.pin_candidate_with_meta(_SAMPLE_CANDIDATE))
    assert uri.startswith("ipfs-local://")
    assert is_real is False


def test_pin_candidate_idempotent_for_same_payload(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("PINATA_JWT", raising=False)
    monkeypatch.delenv("W3S_TOKEN", raising=False)
    monkeypatch.setattr(ipfs, "_LOCAL_PIN_DIR", tmp_path)
    monkeypatch.setattr(ipfs, "_LOCAL_DAEMON_URL", "http://127.0.0.1:1/api/v0/add")

    uri1 = asyncio.run(ipfs.pin_candidate(_SAMPLE_CANDIDATE))
    uri2 = asyncio.run(ipfs.pin_candidate(_SAMPLE_CANDIDATE))
    assert uri1 == uri2  # SHA-based content addressing is stable
