"""Polymarket V2 REST client with safe mock fallback.

Endpoints (Polymarket V2, May 2026):
  - Gamma (markets metadata): https://gamma-api.polymarket.com
  - CLOB (orderbook + fills):  https://clob.polymarket.com

The hackathon flow only needs three operations:
  - ``submit_question`` — create a market.
  - ``get_market_status`` — confirm it went live.
  - ``list_fills`` — pull recent fills routed through our builder code.

We default to ``POLYMARKET_MODE=mock`` so the orchestrator never
accidentally posts real markets while running locally. Even when
``mode=real`` we fall back to the mock client on any transport error
and label the returned ``SubmissionResult`` honestly with
``is_simulated=True`` so the UI can warn the operator.
"""

from __future__ import annotations

import logging
import os
import uuid
from typing import Optional

import httpx

from polyglot_alpha.polymarket.mock_client import (
    BUILDER_FEE_RATE,
    MockPolymarketClient,
)
from polyglot_alpha.polymarket.types import (
    Fill,
    PolymarketMode,
    Question,
    SubmissionResult,
)

log = logging.getLogger(__name__)

GAMMA_API_BASE = "https://gamma-api.polymarket.com"
CLOB_API_BASE = "https://clob.polymarket.com"

DEFAULT_TIMEOUT_SECONDS = 15.0

# Minimum overall quality score required for a real Polymarket submission.
# Dry-run mode skips this gate so judges can see the payload regardless.
REAL_QUALITY_GATE: float = 0.80
# Daily cap on real-mode submissions (per process restart; the orchestrator
# itself enforces the persistent count via DB).
REAL_DAILY_LIMIT: int = 5


def _mode_from_env(explicit: Optional[PolymarketMode]) -> PolymarketMode:
    """Resolve effective mode, defaulting to DRY_RUN when env is unset/invalid.

    Hackathon default is ``dry_run`` so the demo shows the real-shape
    Gamma payload without actually posting markets.
    """

    if explicit is not None:
        return explicit
    raw = os.getenv("POLYMARKET_MODE", PolymarketMode.DRY_RUN.value).strip().lower()
    try:
        return PolymarketMode(raw)
    except ValueError:
        log.warning(
            "POLYMARKET_MODE=%r is not real|dry_run|mock; defaulting to dry_run",
            raw,
        )
        return PolymarketMode.DRY_RUN


def _build_gamma_payload(
    question: Question, builder_code: str, builder_name: Optional[str]
) -> dict:
    """Construct the full Gamma-API ``/markets`` request body.

    Includes every field the real submission needs: ``question``,
    ``category``, ``resolution_source``, ``end_date_iso``,
    ``initial_liquidity_usdc``, ``builder_code``, ``builder_name`` and
    a stable ``external_id`` so duplicate submissions resolve idempotently.
    """

    return {
        "question": question.text,
        "category": question.category,
        "resolution_source": question.resolution_source,
        "end_date_iso": question.end_date_iso,
        "initial_liquidity_usdc": question.initial_liquidity_usdc,
        "builder_code": builder_code,
        "builder_name": builder_name or os.getenv("POLYMARKET_BUILDER_NAME"),
        "external_id": question.question_id,
        "outcomes": ["Yes", "No"],
        "client_id": "polyglot-alpha",
    }


class PolymarketV2Client:
    """Async client that talks to Polymarket V2 (or its mock).

    Always construct via ``async with`` if you want the HTTP pool
    cleaned up promptly; otherwise call :meth:`close` explicitly.
    """

    def __init__(
        self,
        builder_code: str,
        api_key: Optional[str] = None,
        *,
        mode: Optional[PolymarketMode] = None,
        http_client: Optional[httpx.AsyncClient] = None,
        mock_client: Optional[MockPolymarketClient] = None,
    ) -> None:
        if not builder_code:
            raise ValueError("builder_code is required")
        self.builder_code = builder_code
        self.api_key = api_key
        self.mode = _mode_from_env(mode)
        self._owns_http = http_client is None
        self._http: Optional[httpx.AsyncClient] = http_client
        # Lazily-initialized fallback. We always have one ready so a
        # network failure can degrade gracefully without re-raising.
        self._mock = mock_client or MockPolymarketClient(builder_code=builder_code)

    # ----- lifecycle -----------------------------------------------------

    async def __aenter__(self) -> "PolymarketV2Client":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    async def close(self) -> None:
        if self._http is not None and self._owns_http:
            await self._http.aclose()
            self._http = None
        await self._mock.close()

    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            headers = {"User-Agent": "polyglot-alpha/0.2"}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            self._http = httpx.AsyncClient(
                timeout=DEFAULT_TIMEOUT_SECONDS, headers=headers
            )
        return self._http

    # ----- public surface ------------------------------------------------

    async def submit_question(
        self,
        question: Question,
        *,
        confirm_real_submission: bool = False,
        overall_score: Optional[float] = None,
    ) -> SubmissionResult:
        """Create a Polymarket market for ``question``.

        Mode behavior:

        * ``MOCK`` — delegate to :class:`MockPolymarketClient`. ``is_simulated=True``.
        * ``DRY_RUN`` (default) — build the full real-shape Gamma payload,
          log it, return ``market_id=f"dryrun-{uuid}"``. ``is_simulated=True``.
          Bypasses the quality gate so judges can inspect the payload.
        * ``REAL`` — requires builder API key/secret/passphrase **and** the
          caller-provided ``confirm_real_submission=True`` flag.
          Subject to ``REAL_QUALITY_GATE`` on ``overall_score`` (skipped if
          ``overall_score`` is None — caller is responsible for ensuring
          the panel verdict). On any failure we degrade to dry-run with
          the error stamped on the result.
        """

        builder_name = os.getenv("POLYMARKET_BUILDER_NAME")
        payload = _build_gamma_payload(question, self.builder_code, builder_name)

        # ----- MOCK ----- #
        if self.mode == PolymarketMode.MOCK:
            result = await self._mock.submit_question(question)
            result.mode = PolymarketMode.MOCK.value
            return result

        # ----- DRY_RUN ----- #
        if self.mode == PolymarketMode.DRY_RUN:
            market_id = f"dryrun-{uuid.uuid4().hex[:12]}"
            log.info(
                "polymarket dry_run submission: market_id=%s payload=%s",
                market_id,
                payload,
            )
            return SubmissionResult(
                market_id=market_id,
                polymarket_url=f"https://polymarket.com/dryrun/{market_id}",
                status="dry_run",
                fees_estimate_usdc=question.initial_liquidity_usdc * BUILDER_FEE_RATE,
                is_simulated=True,
                mode=PolymarketMode.DRY_RUN.value,
                payload=payload,
            )

        # ----- REAL ----- #
        # Mandatory safety gates.
        if not confirm_real_submission:
            log.warning(
                "real-mode submission blocked: confirm_real_submission=False"
            )
            return SubmissionResult(
                market_id=f"blocked-{uuid.uuid4().hex[:12]}",
                polymarket_url="",
                status="blocked",
                fees_estimate_usdc=0.0,
                is_simulated=True,
                error="confirm_real_submission required for real mode",
                mode=PolymarketMode.REAL.value,
                payload=payload,
            )
        if overall_score is not None and overall_score < REAL_QUALITY_GATE:
            log.warning(
                "real-mode submission blocked: overall_score=%.3f < %.2f",
                overall_score,
                REAL_QUALITY_GATE,
            )
            return SubmissionResult(
                market_id=f"blocked-{uuid.uuid4().hex[:12]}",
                polymarket_url="",
                status="blocked",
                fees_estimate_usdc=0.0,
                is_simulated=True,
                error=(
                    f"overall_score {overall_score:.3f} below "
                    f"REAL_QUALITY_GATE {REAL_QUALITY_GATE:.2f}"
                ),
                mode=PolymarketMode.REAL.value,
                payload=payload,
            )

        # Real-mode auth must be fully configured.
        missing_secrets = [
            name
            for name in (
                "POLYMARKET_BUILDER_API_KEY",
                "POLYMARKET_BUILDER_API_SECRET",
                "POLYMARKET_BUILDER_API_PASSPHRASE",
            )
            if not os.getenv(name)
        ]
        if missing_secrets:
            return SubmissionResult(
                market_id=f"misconfig-{uuid.uuid4().hex[:12]}",
                polymarket_url="",
                status="failed",
                fees_estimate_usdc=0.0,
                is_simulated=True,
                error=f"missing builder secrets: {missing_secrets}",
                mode=PolymarketMode.REAL.value,
                payload=payload,
            )

        try:
            resp = await self._client().post(
                f"{GAMMA_API_BASE}/markets", json=payload
            )
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("Polymarket submit failed, falling back to dry_run: %s", exc)
            return SubmissionResult(
                market_id=f"dryrun-{uuid.uuid4().hex[:12]}",
                polymarket_url="",
                status="failed",
                fees_estimate_usdc=question.initial_liquidity_usdc * BUILDER_FEE_RATE,
                is_simulated=True,
                error=f"real_api_unavailable: {exc!s}",
                mode=PolymarketMode.REAL.value,
                payload=payload,
            )

        market_id = str(data.get("id") or data.get("market_id") or "")
        if not market_id:
            log.warning("Polymarket response missing market id: %s", data)
            return SubmissionResult(
                market_id=f"dryrun-{uuid.uuid4().hex[:12]}",
                polymarket_url="",
                status="failed",
                fees_estimate_usdc=0.0,
                is_simulated=True,
                error="real_api_missing_market_id",
                mode=PolymarketMode.REAL.value,
                payload=payload,
            )

        return SubmissionResult(
            market_id=market_id,
            polymarket_url=str(
                data.get("url") or f"https://polymarket.com/market/{market_id}"
            ),
            status=str(data.get("status") or "submitted"),
            fees_estimate_usdc=float(
                data.get(
                    "fees_estimate_usdc",
                    question.initial_liquidity_usdc * BUILDER_FEE_RATE,
                )
            ),
            is_simulated=False,
            mode=PolymarketMode.REAL.value,
            payload=payload,
        )

    async def get_market_status(self, market_id: str) -> dict:
        if self.mode in (PolymarketMode.MOCK, PolymarketMode.DRY_RUN):
            return await self._mock.get_market_status(market_id)
        try:
            resp = await self._client().get(f"{GAMMA_API_BASE}/markets/{market_id}")
            resp.raise_for_status()
            data = resp.json()
            data["is_simulated"] = False
            return data
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("Polymarket get_market_status fell back to mock: %s", exc)
            data = await self._mock.get_market_status(market_id)
            data["error"] = f"real_api_unavailable: {exc!s}"
            return data

    async def list_fills(self, market_id: str, since_ts: int) -> list[Fill]:
        """Return fills with ``timestamp > since_ts`` for ``market_id``.

        Real mode hits the CLOB ``/fills`` endpoint filtered to our
        builder code. Mock mode returns a synthetic stream.
        """
        if self.mode in (PolymarketMode.MOCK, PolymarketMode.DRY_RUN):
            return await self._mock.list_fills(market_id, since_ts)

        try:
            resp = await self._client().get(
                f"{CLOB_API_BASE}/fills",
                params={
                    "market_id": market_id,
                    "since": since_ts,
                    "builder_code": self.builder_code,
                },
            )
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("Polymarket list_fills fell back to mock: %s", exc)
            return await self._mock.list_fills(market_id, since_ts)

        raw_fills = data.get("fills") if isinstance(data, dict) else data
        if not isinstance(raw_fills, list):
            return []
        return [_fill_from_clob(raw, market_id) for raw in raw_fills]


def _fill_from_clob(raw: dict, fallback_market_id: str) -> Fill:
    """Coerce a CLOB fill dict into our :class:`Fill` model."""
    fill_amount = float(raw.get("size_usdc") or raw.get("notional_usdc") or 0.0)
    builder_fee = float(
        raw.get("builder_fee_usdc")
        or raw.get("builder_fee")
        or fill_amount * BUILDER_FEE_RATE
    )
    return Fill(
        fill_id=str(raw.get("id") or raw.get("fill_id") or ""),
        market_id=str(raw.get("market_id") or fallback_market_id),
        fill_amount_usdc=fill_amount,
        builder_fee_usdc=builder_fee,
        timestamp=int(raw.get("timestamp") or raw.get("ts") or 0),
        taker_address=raw.get("taker") or raw.get("taker_address"),
        is_simulated=False,
    )
