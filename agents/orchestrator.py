"""Orchestrator Agent — coordinates all agents, manages dedup, routes alerts."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime

import config
from models import Alert, PipelineStatus, RawFiling
from message_bus import bus
from infra.dedup import dedup_cache
from infra.user_store import user_store
from agents.filing_watcher import FilingWatcherAgent
from agents.signal_classifier import SignalClassifierAgent
from agents.context_enricher import ContextEnrichmentAgent
from agents.alert_composer import AlertComposerAgent

logger = logging.getLogger("opportunity_radar.orchestrator")


class OrchestratorAgent:
    """Central coordinator: starts all agents, manages lifecycle, dedup, routing.
    
    Flow:
    1. Filing Watcher emits to raw_filings
    2. Orchestrator deduplicates and checks novelty
    3. Dispatches to Signal Classifier
    4. If score > threshold, routes to Context Enricher
    5. Alert Composer generates final alert
    6. Orchestrator fans out to matching users
    """

    def __init__(self):
        self._filing_watcher = FilingWatcherAgent()
        self._signal_classifier = SignalClassifierAgent()
        self._context_enricher = ContextEnrichmentAgent()
        self._alert_composer = AlertComposerAgent()
        self._status = "idle"
        self._start_time = 0.0
        self._alerts: list[Alert] = []
        self._held_for_review: list[Alert] = []
        self._total_filings = 0
        self._total_signals = 0
        self._total_alerts_sent = 0

    async def _handle_raw_filing(self, filing: RawFiling):
        """Process a raw filing: dedup, novelty check, dispatch to classifier."""
        logger.info(f"📥 Orchestrator received filing: {filing.stock_symbol} — {filing.filing_type.value}")

        # Dedup check
        content_key = f"{filing.source}:{filing.stock_symbol}:{filing.content_hash}"
        if dedup_cache.check_and_mark(content_key):
            logger.info(f"🔄 Duplicate filing skipped: {filing.stock_symbol}")
            return

        # Data freshness check
        if filing.data_freshness_ms > config.DATA_FRESHNESS_MAX_MS:
            logger.warning(
                f"⏰ Stale filing suppressed: {filing.stock_symbol} "
                f"(freshness: {filing.data_freshness_ms}ms > {config.DATA_FRESHNESS_MAX_MS}ms)"
            )
            return

        self._total_filings += 1

        # Dispatch to Signal Classifier
        await self._signal_classifier.handle_filing(filing)

    async def _handle_classified_signal(self, data: tuple):
        """Process classified signal: threshold check, route to enricher."""
        filing, signal = data
        self._total_signals += 1

        if signal.importance_score < config.SIGNAL_THRESHOLD:
            logger.info(
                f"📉 Low-score signal filtered: {filing.stock_symbol} "
                f"(score: {signal.importance_score:.2f})"
            )
            return

        # Route to Context Enricher
        enriched = await self._context_enricher.enrich(filing, signal)
        await bus.publish("enriched_signals", enriched)

    async def _handle_enriched_signal(self, enriched):
        """Process enriched signal: compose alert."""
        await self._alert_composer.handle_enriched_signal(enriched)

    async def _handle_alert(self, alert: Alert):
        """Process final alert: human-in-loop check, fan out to users."""
        logger.info(f"🔔 Alert received: {alert.title[:60]}")

        # Human-in-loop override for low confidence
        if alert.needs_human_review:
            logger.warning(
                f"⚠️ Alert held for human review: {alert.stock_symbol} "
                f"(confidence: {alert.confidence_score:.0%})"
            )
            self._held_for_review.append(alert)
            # Still add to alerts list but mark it
            self._alerts.append(alert)
            self._total_alerts_sent += 1
            return

        # Fan out to matching users
        matching_users = user_store.get_users_for_stock(alert.stock_symbol)
        if matching_users:
            user_names = [u.name for u in matching_users]
            logger.info(f"📤 Alert routed to {len(matching_users)} users: {', '.join(user_names)}")
        else:
            logger.info(f"📤 Alert broadcast (no specific watchlist matches)")

        self._alerts.append(alert)
        self._total_alerts_sent += 1

    async def start(self):
        """Start the full agent pipeline."""
        self._status = "starting"
        self._start_time = time.time()
        logger.info("🚀 Orchestrator starting all agents...")

        # Start the message bus
        await bus.start()

        # Register handlers on the bus
        bus.start_consumer("raw_filings", self._handle_raw_filing)
        bus.start_consumer("classified_signals", self._handle_classified_signal)
        bus.start_consumer("enriched_signals", self._handle_enriched_signal)
        bus.start_consumer("alerts", self._handle_alert)

        self._status = "running"
        logger.info("✅ All agent consumers started")

        # Start the filing watcher (this runs in background)
        asyncio.create_task(self._filing_watcher.run())

        logger.info("🟢 Opportunity Radar pipeline is live!")

    async def stop(self):
        """Stop all agents."""
        self._status = "stopping"
        await bus.stop()
        self._status = "stopped"
        logger.info("🔴 Orchestrator stopped")

    def get_alerts(self, limit: int = 50) -> list[Alert]:
        """Get latest alerts, newest first."""
        return list(reversed(self._alerts[-limit:]))

    def get_held_alerts(self) -> list[Alert]:
        """Get alerts held for human review."""
        return list(self._held_for_review)

    def approve_alert(self, alert_id: str) -> bool:
        """Approve a held alert for release."""
        for i, alert in enumerate(self._held_for_review):
            if alert.id == alert_id:
                alert.needs_human_review = False
                self._held_for_review.pop(i)
                logger.info(f"✅ Alert approved: {alert.id}")
                return True
        return False

    def get_pipeline_status(self) -> PipelineStatus:
        """Get current status of all agents."""
        uptime = time.time() - self._start_time if self._start_time else 0
        return PipelineStatus(
            filing_watcher=self._filing_watcher.status,
            signal_classifier=self._signal_classifier.status,
            context_enricher=self._context_enricher.status,
            alert_composer=self._alert_composer.status,
            orchestrator=self._status,
            total_filings_processed=self._total_filings,
            total_signals_generated=self._total_signals,
            total_alerts_sent=self._total_alerts_sent,
            uptime_seconds=round(uptime, 1),
        )
