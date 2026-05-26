"""IPFS pinning helper for candidate-provenance proofs.

The orchestrator pins the canonical candidate JSON (the one whose SHA256
becomes ``candidate_hash`` on Arc ``QuestionRegistry``) to IPFS so anyone
can later verify::

    SHA256(IPFS content at <cid>) == candidate_hash on-chain

This module exposes a single :func:`pin_candidate` coroutine that tries
providers in order and degrades gracefully when none are available.

Resolution order:

1. **Pinata** — production-grade, needs ``PINATA_JWT`` env var.
2. **web3.storage** — alternative, needs ``W3S_TOKEN`` env var.
3. **Local IPFS daemon** — at ``http://localhost:5001`` if reachable.
4. **Local content-addressable file** — writes the JSON to
   ``outputs/ipfs_pins/<sha256>.json`` and returns ``ipfs-local://<sha256>``
   (clearly not a real CID, but lets the demo continue).

Every path returns the URI as a string. The :func:`pin_candidate_with_meta`
variant returns both the URI and a flag indicating whether the pin is
verifiable on the public IPFS DHT.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)


_REPO_ROOT = Path(__file__).resolve().parents[1]
_LOCAL_PIN_DIR = _REPO_ROOT / "outputs" / "ipfs_pins"


_PINATA_PIN_URL = "https://api.pinata.cloud/pinning/pinJSONToIPFS"
_W3S_UPLOAD_URL = "https://api.web3.storage/upload"
_LOCAL_DAEMON_URL = "http://localhost:5001/api/v0/add"

_HTTP_TIMEOUT_SECONDS: float = 8.0


def _canonical_json_bytes(payload: dict[str, Any]) -> bytes:
    """Encode ``payload`` deterministically (sorted keys, no extra space)."""

    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _content_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()


async def _try_pinata(payload: dict[str, Any]) -> Optional[str]:
    jwt = os.environ.get("PINATA_JWT")
    if not jwt:
        return None
    headers = {
        "Authorization": f"Bearer {jwt}",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SECONDS) as client:
            resp = await client.post(
                _PINATA_PIN_URL,
                headers=headers,
                json={"pinataContent": payload},
            )
        if resp.status_code >= 300:
            logger.warning("pinata: status=%s body=%s", resp.status_code, resp.text[:200])
            return None
        cid = resp.json().get("IpfsHash")
        if not cid:
            logger.warning("pinata: missing IpfsHash in response")
            return None
        logger.info("pinata: pinned candidate cid=%s", cid)
        return f"ipfs://{cid}"
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("pinata: %s", exc)
        return None


async def _try_web3_storage(payload: dict[str, Any]) -> Optional[str]:
    token = os.environ.get("W3S_TOKEN")
    if not token:
        return None
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SECONDS) as client:
            resp = await client.post(
                _W3S_UPLOAD_URL,
                headers=headers,
                content=_canonical_json_bytes(payload),
            )
        if resp.status_code >= 300:
            logger.warning("w3s: status=%s body=%s", resp.status_code, resp.text[:200])
            return None
        cid = resp.json().get("cid")
        if not cid:
            logger.warning("w3s: missing cid in response")
            return None
        logger.info("w3s: pinned candidate cid=%s", cid)
        return f"ipfs://{cid}"
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("w3s: %s", exc)
        return None


async def _try_local_daemon(payload: dict[str, Any]) -> Optional[str]:
    try:
        files = {"file": ("candidate.json", _canonical_json_bytes(payload), "application/json")}
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SECONDS) as client:
            resp = await client.post(
                _LOCAL_DAEMON_URL,
                params={"pin": "true"},
                files=files,
            )
        if resp.status_code >= 300:
            return None
        cid = resp.json().get("Hash")
        if not cid:
            return None
        logger.info("ipfs-local: pinned candidate cid=%s", cid)
        return f"ipfs://{cid}"
    except (httpx.HTTPError, ValueError, ConnectionError, OSError) as exc:
        logger.debug("ipfs-local: %s", exc)
        return None


def _local_file_fallback(payload: dict[str, Any]) -> str:
    """Write the payload to ``outputs/ipfs_pins/<sha256>.json`` as a Phase 2 stub.

    Returns ``ipfs-local://<sha256>`` so the caller can pass it through to
    downstream code without crashing. This is **not a real IPFS pin** —
    only the SHA256 content-addressing property is preserved.
    """

    _LOCAL_PIN_DIR.mkdir(parents=True, exist_ok=True)
    digest = _content_hash(payload)
    out_path = _LOCAL_PIN_DIR / f"{digest}.json"
    out_path.write_bytes(_canonical_json_bytes(payload))
    logger.info(
        "ipfs-fallback: wrote local content-addressable file at %s (Phase 2: use real IPFS pin)",
        out_path,
    )
    return f"ipfs-local://{digest}"


async def pin_candidate(candidate_dict: dict[str, Any]) -> str:
    """Pin a candidate JSON to IPFS. Returns the URI.

    Always returns *some* URI — never raises. Callers should call
    :func:`pin_candidate_with_meta` if they need to know whether the
    returned URI is a real on-DHT pin or just a local content-addressable
    file.
    """

    uri, _is_real = await pin_candidate_with_meta(candidate_dict)
    return uri


async def pin_candidate_with_meta(
    candidate_dict: dict[str, Any],
) -> tuple[str, bool]:
    """Like :func:`pin_candidate` but also returns ``is_real_pin: bool``.

    ``is_real_pin`` is ``True`` if the URI is resolvable through the public
    IPFS network (Pinata / web3.storage / local daemon connected to the
    DHT) and ``False`` if we degraded to the local-file fallback.
    """

    for provider in (_try_pinata, _try_web3_storage, _try_local_daemon):
        uri = await provider(candidate_dict)
        if uri:
            return uri, True

    uri = _local_file_fallback(candidate_dict)
    return uri, False


__all__ = ["pin_candidate", "pin_candidate_with_meta"]
