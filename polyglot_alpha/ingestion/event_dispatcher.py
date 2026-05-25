"""Event dispatcher.

Take :class:`ConfirmedEvent` instances and dispatch them on chain by calling
``TranslationAuction.openAuction(eventId, contentHash)``. Records every
attempt in the SQLite ``events`` table with a 24-hour cooldown per content
hash so we never re-trigger the same auction.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from sqlmodel import Session, select

from polyglot_alpha.ingestion.cross_reference import content_hash
from polyglot_alpha.ingestion.models import (
    ConfirmedEvent,
    Event,
    EventStatus,
    RawEvent,
    get_engine,
)

LOGGER = logging.getLogger(__name__)

DEDUP_WINDOW = timedelta(hours=24)
TRANSLATION_AUCTION_ABI = [
    {
        "type": "function",
        "name": "openAuction",
        "inputs": [
            {"name": "eventId", "type": "bytes32"},
            {"name": "eventHash", "type": "bytes32"},
        ],
        "outputs": [],
        "stateMutability": "nonpayable",
    }
]


# --------------------------------------------------------------------------- #
# Chain client.                                                               #
# --------------------------------------------------------------------------- #


class ChainClient:
    """Thin wrapper around web3.py for TranslationAuction.openAuction."""

    def __init__(
        self,
        *,
        rpc_url: str,
        contract_address: str,
        private_key: str,
        chain_id: int | None = None,
    ) -> None:
        from web3 import Web3
        from web3.middleware import ExtraDataToPOAMiddleware

        self._web3 = Web3(Web3.HTTPProvider(rpc_url))
        try:
            self._web3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
        except Exception:  # pragma: no cover - middleware optional
            pass
        self._address = Web3.to_checksum_address(contract_address)
        self._account = self._web3.eth.account.from_key(private_key)
        self._chain_id = chain_id or int(self._web3.eth.chain_id)
        self._contract = self._web3.eth.contract(
            address=self._address, abi=TRANSLATION_AUCTION_ABI
        )

    def open_auction(self, event_id: bytes, event_hash: bytes) -> str:
        nonce = self._web3.eth.get_transaction_count(self._account.address)
        tx = self._contract.functions.openAuction(event_id, event_hash).build_transaction(
            {
                "from": self._account.address,
                "nonce": nonce,
                "chainId": self._chain_id,
            }
        )
        signed = self._account.sign_transaction(tx)
        raw_tx = getattr(signed, "raw_transaction", None) or getattr(
            signed, "rawTransaction"
        )
        tx_hash = self._web3.eth.send_raw_transaction(raw_tx)
        return tx_hash.hex()


# --------------------------------------------------------------------------- #
# Dispatcher.                                                                 #
# --------------------------------------------------------------------------- #


class EventDispatcher:
    """Persist confirmed events and trigger ``openAuction`` on chain."""

    def __init__(
        self,
        *,
        engine: Any | None = None,
        chain: ChainClient | None = None,
        dedup_window: timedelta = DEDUP_WINDOW,
    ) -> None:
        self.engine = engine or get_engine()
        self.chain = chain
        self.dedup_window = dedup_window

    # ---- public API --------------------------------------------------------

    def is_duplicate(self, hash_hex: str) -> bool:
        """True iff we already dispatched (or are about to) within window."""

        cutoff = datetime.now(tz=timezone.utc).replace(tzinfo=None) - self.dedup_window
        with Session(self.engine) as session:
            row = session.exec(
                select(Event).where(Event.content_hash == hash_hex)
            ).first()
            if row is None:
                return False
            return row.triggered_at >= cutoff

    async def dispatch(self, event: ConfirmedEvent) -> Optional[Event]:
        """Persist + optionally trigger on chain. Returns the persisted row.

        Writes are routed at the canonical
        :class:`polyglot_alpha.persistence.models.Event` table. The
        legacy dispatcher status vocabulary (``NEW`` / ``DISPATCHED`` /
        ``FAILED``) is stored verbatim in the free-form ``status``
        column so readers comparing against :class:`EventStatus` keep
        working.
        """

        if self.is_duplicate(event.content_hash):
            LOGGER.info("Skipping duplicate event: %s", event.content_hash[:12])
            return None

        row = Event(
            content_hash=event.content_hash,
            sources=[{"url": u} for u in event.all_sources],
            language=",".join(event.languages) or "multi",
            triggered_at=datetime.now(tz=timezone.utc).replace(tzinfo=None),
            status=EventStatus.NEW.value,
            title=event.primary_title,
            tx_hash=None,
        )
        with Session(self.engine) as session:
            session.add(row)
            session.commit()
            session.refresh(row)

        if self.chain is None:
            LOGGER.info(
                "Chain client not configured; recording event only: %s",
                event.content_hash[:12],
            )
            return row

        try:
            event_id = _hash_to_bytes32(event.content_hash)
            event_hash = _hash_to_bytes32(event.content_hash)
            tx_hash = await asyncio.to_thread(
                self.chain.open_auction, event_id, event_hash
            )
            row.tx_hash = tx_hash
            row.status = EventStatus.DISPATCHED.value
            LOGGER.info(
                "Dispatched openAuction tx=%s for %s", tx_hash, event.primary_title
            )
        except Exception as exc:
            LOGGER.exception("openAuction call failed: %s", exc)
            row.status = EventStatus.FAILED.value
            row.tx_hash = None

        with Session(self.engine) as session:
            session.add(row)
            session.commit()
            session.refresh(row)
        return row

    async def dispatch_many(
        self, events: Iterable[ConfirmedEvent]
    ) -> list[Event]:
        out: list[Event] = []
        for ev in events:
            row = await self.dispatch(ev)
            if row is not None:
                out.append(row)
        return out


def _hash_to_bytes32(hash_hex: str) -> bytes:
    cleaned = hash_hex[2:] if hash_hex.startswith("0x") else hash_hex
    raw = bytes.fromhex(cleaned)
    if len(raw) > 32:
        return raw[:32]
    return raw.rjust(32, b"\x00")


# --------------------------------------------------------------------------- #
# Demo mode.                                                                  #
# --------------------------------------------------------------------------- #


SAMPLE_GLOB = "sample_*.json"


def _load_demo_samples(outputs_dir: Path) -> list[ConfirmedEvent]:
    """Build ConfirmedEvent instances from outputs/sample_*.json (if present)."""

    paths = sorted(outputs_dir.glob(SAMPLE_GLOB))[:5]
    if not paths:
        return _hardcoded_chinese_samples()

    events: list[ConfirmedEvent] = []
    for idx, path in enumerate(paths):
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        title = str(data.get("title") or path.stem)
        source_news = str(data.get("source_news") or data.get("description") or "")
        resolution = str(data.get("resolution_source") or "")
        all_sources = [resolution] if resolution else [f"file://{path}"]
        chash = content_hash(title, all_sources)
        events.append(
            ConfirmedEvent(
                cluster_id=f"demo-{idx}",
                sources_count=max(2, len(all_sources) + 1),
                primary_title=title,
                all_sources=all_sources,
                content_hash=chash,
                languages=["zh", "en"],
                summary=source_news,
            )
        )
    return events


def _hardcoded_chinese_samples() -> list[ConfirmedEvent]:
    items = [
        ("央行行长潘功胜：将根据需要适时降准", ["http://www.pbc.gov.cn/"], "zh"),
        ("中国央行宣布下调存款准备金率0.5个百分点", ["http://www.xinhua.com/"], "zh"),
        ("人民币兑美元中间价上调200个基点", ["http://www.safe.gov.cn/"], "zh"),
        ("中证500指数收盘上涨2.3%", ["http://www.csindex.com.cn/"], "zh"),
        ("国务院常务会议部署支持民营经济", ["http://www.gov.cn/"], "zh"),
    ]
    out: list[ConfirmedEvent] = []
    for idx, (title, urls, lang) in enumerate(items):
        out.append(
            ConfirmedEvent(
                cluster_id=f"demo-{idx}",
                sources_count=2,
                primary_title=title,
                all_sources=urls,
                content_hash=content_hash(title, urls),
                languages=[lang, "en"],
                summary=title,
            )
        )
    return out


async def run_demo(
    outputs_dir: Path,
    *,
    interval_seconds: float = 30.0,
    use_chain: bool = False,
) -> list[Event]:
    """Dispatch demo samples with ``interval_seconds`` between each."""

    dispatcher = EventDispatcher(chain=_build_chain_if_requested() if use_chain else None)
    samples = _load_demo_samples(outputs_dir)
    LOGGER.info("Demo: dispatching %d samples (interval=%ss)", len(samples), interval_seconds)

    dispatched: list[Event] = []
    for idx, sample in enumerate(samples):
        LOGGER.info("[demo %d/%d] %s", idx + 1, len(samples), sample.primary_title[:80])
        row = await dispatcher.dispatch(sample)
        if row is not None:
            dispatched.append(row)
        if idx < len(samples) - 1:
            await asyncio.sleep(interval_seconds)
    return dispatched


def _build_chain_if_requested() -> Optional[ChainClient]:
    rpc = os.getenv("ARC_TESTNET_RPC")
    contract = os.getenv("TRANSLATION_AUCTION_ADDRESS")
    private_key = os.getenv("OPERATOR_WALLET_PRIVATE_KEY") or os.getenv(
        "HACKATHON_WALLET_PRIVATE_KEY"
    )
    chain_id_env = os.getenv("ARC_CHAIN_ID")
    chain_id = int(chain_id_env) if chain_id_env else None
    if not (rpc and contract and private_key):
        LOGGER.warning(
            "Chain not fully configured (need ARC_TESTNET_RPC, "
            "TRANSLATION_AUCTION_ADDRESS, OPERATOR_WALLET_PRIVATE_KEY)."
        )
        return None
    return ChainClient(
        rpc_url=rpc,
        contract_address=contract,
        private_key=private_key,
        chain_id=chain_id,
    )


# --------------------------------------------------------------------------- #
# CLI entry point.                                                            #
# --------------------------------------------------------------------------- #


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="PolyglotAlpha event dispatcher")
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Replay 5 hardcoded / sample_*.json events with 30s intervals.",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=30.0,
        help="Seconds between dispatches in demo mode.",
    )
    parser.add_argument(
        "--outputs-dir",
        default=str(Path(__file__).resolve().parents[2] / "outputs"),
        help="Directory containing sample_*.json files for demo mode.",
    )
    parser.add_argument(
        "--use-chain",
        action="store_true",
        help="Actually submit openAuction transactions (defaults to dry run).",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
    )
    if not args.demo:
        LOGGER.error("This entry point currently only supports --demo mode.")
        return 2

    asyncio.run(
        run_demo(
            Path(args.outputs_dir),
            interval_seconds=args.interval,
            use_chain=args.use_chain,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
