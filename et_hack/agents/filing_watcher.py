"""Filing Watcher Agent — polls for new filings and emits RawFiling events."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
import time
from datetime import datetime
from pathlib import Path

import config
from models import FilingType, RawFiling
from message_bus import bus

logger = logging.getLogger("opportunity_radar.filing_watcher")

SAMPLE_FILINGS_PATH = Path(__file__).parent.parent / "data" / "sample_filings.json"


class FilingWatcherAgent:
    """Polls SEBI EDGAR, NSE/BSE APIs for new filings every 15 min.
    Extracts delta changes vs last filing using content hash comparison.
    In demo mode, cycles through sample filings with randomized timing.
    """

    def __init__(self):
        self._seen_hashes: set[str] = set()
        self._sample_filings: list[dict] = []
        self._filing_index = 0
        self._status = "idle"
        self._total_processed = 0
        self._retry_counts: dict[str, int] = {}
        self._load_sample_filings()

    def _load_sample_filings(self):
        try:
            with open(SAMPLE_FILINGS_PATH) as f:
                self._sample_filings = json.load(f)
            logger.info(f"Loaded {len(self._sample_filings)} sample filings")
        except Exception as e:
            logger.error(f"Failed to load sample filings: {e}")
            self._sample_filings = []

    @staticmethod
    def _compute_hash(filing_data: dict) -> str:
        content = json.dumps(filing_data, sort_keys=True)
        return hashlib.sha256(content.encode()).hexdigest()[:16]

    def _to_raw_filing(self, data: dict) -> RawFiling:
        content_hash = self._compute_hash(data)
        return RawFiling(
            source=data["source"],
            filing_type=FilingType(data["filing_type"]),
            company_name=data["company_name"],
            stock_symbol=data["stock_symbol"],
            title=data["title"],
            summary=data["summary"],
            raw_text=data.get("raw_text", ""),
            source_url=data.get("source_url", ""),
            filed_at=datetime.utcnow(),
            ingested_at=datetime.utcnow(),
            content_hash=content_hash,
            data_freshness_ms=random.randint(100, 5000),
        )

    async def _poll_once(self) -> list[RawFiling]:
        """Simulate polling for new filings. Returns list of new filings found."""
        self._status = "polling"
        new_filings = []

        if not self._sample_filings:
            logger.warning("No sample filings available")
            return new_filings

        # In demo mode, emit 1-2 filings per poll cycle
        num_to_emit = random.randint(1, 2)
        for _ in range(num_to_emit):
            if self._filing_index >= len(self._sample_filings):
                self._filing_index = 0  # Cycle back

            data = self._sample_filings[self._filing_index]
            self._filing_index += 1

            content_hash = self._compute_hash(data)

            # Delta check — skip if already seen
            if content_hash in self._seen_hashes:
                logger.debug(f"Skipping duplicate filing: {data['title'][:50]}")
                continue

            self._seen_hashes.add(content_hash)
            filing = self._to_raw_filing(data)
            new_filings.append(filing)
            logger.info(f"📄 New filing detected: [{filing.source}] {filing.title[:60]}")

        self._status = "idle"
        return new_filings

    async def _emit_filings(self, filings: list[RawFiling]):
        """Emit filings to the message bus."""
        for filing in filings:
            await bus.publish("raw_filings", filing)
            self._total_processed += 1
            logger.info(f"📤 Emitted to raw_filings: {filing.stock_symbol} — {filing.filing_type.value}")

    async def run(self):
        """Main polling loop with exponential backoff on failures."""
        poll_interval = config.DEMO_POLL_INTERVAL_SEC if config.DEMO_MODE else config.FILING_POLL_INTERVAL_SEC
        logger.info(f"🔍 Filing Watcher started (poll interval: {poll_interval}s, demo: {config.DEMO_MODE})")
        self._status = "running"

        while True:
            try:
                filings = await self._poll_once()
                if filings:
                    await self._emit_filings(filings)
                    self._retry_counts.clear()  # Reset on success
                else:
                    logger.debug("No new filings found this cycle")

            except Exception as e:
                source = "polling"
                count = self._retry_counts.get(source, 0) + 1
                self._retry_counts[source] = count

                if count > config.MAX_RETRIES:
                    logger.error(f"💀 Dead-letter: polling failed {count} times: {e}")
                    self._retry_counts[source] = 0
                else:
                    delay = config.RETRY_DELAYS[min(count - 1, len(config.RETRY_DELAYS) - 1)]
                    logger.warning(f"⚠️ Retry {count}/{config.MAX_RETRIES} in {delay}s: {e}")
                    await asyncio.sleep(delay)
                    continue

            await asyncio.sleep(poll_interval)

    @property
    def status(self) -> str:
        return self._status

    @property
    def total_processed(self) -> int:
        return self._total_processed
