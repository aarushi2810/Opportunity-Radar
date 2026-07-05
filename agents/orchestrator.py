"""Orchestrator Agent — coordinates all agents, manages dedup, routes alerts."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import datetime
from typing import Optional

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
    6. Orchestrator persists alert to DB and appends to in-memory cache
    """

    def __init__(self):
        self._filing_watcher = FilingWatcherAgent()
        self._signal_classifier = SignalClassifierAgent()
        self._context_enricher = ContextEnrichmentAgent()
        self._alert_composer = AlertComposerAgent()
        self._status = "idle"
        self._start_time = 0.0

        # In-memory alert cache (latest 200 alerts) — populated from DB on startup
        self._alerts: list[Alert] = []
        self._held_for_review: list[Alert] = []

        # Counters — derived from DB on startup, incremented in memory thereafter
        self._total_filings = 0
        self._total_signals = 0
        self._total_alerts_sent = 0

    # ── Handlers ─────────────────────────────────────────────────────────────

    async def _handle_raw_filing(self, filing: RawFiling):
        """Process a raw filing: dedup, freshness check, dispatch to classifier."""
        logger.info("Orchestrator received filing: %s — %s", filing.stock_symbol, filing.filing_type.value)

        content_key = f"{filing.source}:{filing.stock_symbol}:{filing.content_hash}"
        if dedup_cache.check_and_mark(content_key):
            logger.info("Duplicate filing skipped: %s", filing.stock_symbol)
            return

        if filing.data_freshness_ms > config.DATA_FRESHNESS_MAX_MS:
            logger.warning(
                "Stale filing suppressed: %s (freshness: %dms > %dms)",
                filing.stock_symbol, filing.data_freshness_ms, config.DATA_FRESHNESS_MAX_MS,
            )
            return

        self._total_filings += 1
        await self._signal_classifier.handle_filing(filing)

    async def _handle_classified_signal(self, data: tuple):
        """Process classified signal: threshold check, route to enricher."""
        filing, signal = data
        self._total_signals += 1

        if signal.importance_score < config.SIGNAL_THRESHOLD:
            logger.info(
                "Low-score signal filtered: %s (score: %.2f)",
                filing.stock_symbol, signal.importance_score,
            )
            return

        enriched = await self._context_enricher.enrich(filing, signal)
        await bus.publish("enriched_signals", enriched)

    async def _handle_enriched_signal(self, enriched):
        """Process enriched signal: compose alert."""
        await self._alert_composer.handle_enriched_signal(enriched)

    async def _handle_alert(self, alert: Alert):
        """Persist alert to DB, update in-memory cache, fan out to users."""
        logger.info("Alert received: %s", alert.title[:60])

        # Persist to database
        await self._persist_alert(alert)

        if alert.needs_human_review:
            logger.warning(
                "Alert held for human review: %s (confidence: %.0f%%)",
                alert.stock_symbol, alert.confidence_score * 100,
            )
            self._held_for_review.append(alert)

        matching_users = await user_store.get_users_for_stock(alert.stock_symbol)
        if matching_users:
            user_names = [u.name for u in matching_users]
            logger.info("Alert routed to %d users: %s", len(matching_users), ", ".join(user_names))
        else:
            logger.info("Alert broadcast (no watchlist matches for %s)", alert.stock_symbol)

        self._alerts.append(alert)
        # Keep in-memory cache bounded
        if len(self._alerts) > 200:
            self._alerts = self._alerts[-200:]

        self._total_alerts_sent += 1

    # ── DB Persistence ────────────────────────────────────────────────────────

    async def _persist_alert(self, alert: Alert):
        """Write alert to PostgreSQL alerts table."""
        try:
            from infra.database import AsyncSessionLocal
            from infra.db_models import AlertModel

            async with AsyncSessionLocal() as db:
                record = AlertModel(
                    id=alert.id,
                    enriched_signal_id=alert.enriched_signal_id,
                    stock_symbol=alert.stock_symbol,
                    company_name=alert.company_name,
                    signal_type=alert.signal_type.value,
                    priority=alert.priority.value,
                    confidence_score=alert.confidence_score,
                    title=alert.title,
                    body=alert.body,
                    risk_flags=alert.risk_flags,
                    historical_base_rate=alert.historical_base_rate,
                    tags=alert.tags,
                    needs_human_review=alert.needs_human_review,
                    filing_type=alert.filing_type,
                    dimension_scores=(
                        alert.dimension_scores.model_dump()
                        if alert.dimension_scores else None
                    ),
                    data_freshness_ms=alert.data_freshness_ms,
                )
                db.add(record)
                await db.commit()
        except Exception as exc:
            logger.error("Failed to persist alert %s: %s", alert.id, exc)

    async def _load_alerts_from_db(self):
        """Load the most recent 200 alerts from DB into the in-memory cache on startup."""
        try:
            from infra.database import AsyncSessionLocal
            from infra.db_models import AlertModel
            from sqlalchemy import select, desc

            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    select(AlertModel)
                    .order_by(desc(AlertModel.created_at))
                    .limit(200)
                )
                rows = result.scalars().all()
                count = await db.scalar(
                    select(__import__("sqlalchemy").func.count()).select_from(AlertModel)
                )
                self._total_alerts_sent = count or 0

            from models import Alert as AlertModel_pydantic, AlertPriority, SignalType, DimensionScores
            for row in reversed(rows):
                try:
                    ds = DimensionScores(**row.dimension_scores) if row.dimension_scores else None
                    alert = AlertModel_pydantic(
                        id=row.id,
                        enriched_signal_id=row.enriched_signal_id,
                        stock_symbol=row.stock_symbol,
                        company_name=row.company_name,
                        signal_type=SignalType(row.signal_type),
                        priority=AlertPriority(row.priority),
                        confidence_score=row.confidence_score,
                        title=row.title,
                        body=row.body,
                        risk_flags=row.risk_flags or [],
                        historical_base_rate=row.historical_base_rate,
                        tags=row.tags or [],
                        needs_human_review=row.needs_human_review,
                        filing_type=row.filing_type,
                        dimension_scores=ds,
                        data_freshness_ms=row.data_freshness_ms,
                        created_at=row.created_at,
                    )
                    self._alerts.append(alert)
                except Exception as exc:
                    logger.debug("Could not deserialize stored alert: %s", exc)

            logger.info("Loaded %d alerts from database", len(self._alerts))
        except Exception as exc:
            logger.warning("Could not load alerts from DB (may be first run): %s", exc)

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self):
        """Start the full agent pipeline."""
        self._status = "starting"
        self._start_time = time.time()
        logger.info("Orchestrator starting all agents...")

        # Create DB tables and load persisted state
        from infra.database import create_tables
        await create_tables()
        await user_store.seed_demo_users()
        await self._load_alerts_from_db()

        await bus.start()

        bus.start_consumer("raw_filings", self._handle_raw_filing)
        bus.start_consumer("classified_signals", self._handle_classified_signal)
        bus.start_consumer("enriched_signals", self._handle_enriched_signal)
        bus.start_consumer("alerts", self._handle_alert)

        self._status = "running"
        logger.info("All agent consumers started")

        asyncio.create_task(self._filing_watcher.run())
        logger.info("Opportunity Radar pipeline is live")

    async def stop(self):
        """Stop all agents."""
        self._status = "stopping"
        await bus.stop()
        self._status = "stopped"
        logger.info("Orchestrator stopped")

    # ── Queries ───────────────────────────────────────────────────────────────

    def get_alerts(self, limit: int = 50) -> list[Alert]:
        """Get latest alerts from in-memory cache, newest first."""
        return list(reversed(self._alerts[-limit:]))

    def get_alerts_after(self, last_seen_id: Optional[str], limit: int = 20) -> list[Alert]:
        """Cursor-based alert retrieval for SSE — only return alerts newer than last_seen_id."""
        if not last_seen_id:
            return list(reversed(self._alerts[-limit:]))
        ids = [a.id for a in self._alerts]
        try:
            idx = ids.index(last_seen_id)
            newer = self._alerts[idx + 1:]
            return newer[-limit:]
        except ValueError:
            return list(reversed(self._alerts[-limit:]))

    def get_held_alerts(self) -> list[Alert]:
        return list(self._held_for_review)

    def approve_alert(self, alert_id: str) -> bool:
        for i, alert in enumerate(self._held_for_review):
            if alert.id == alert_id:
                alert.needs_human_review = False
                self._held_for_review.pop(i)
                logger.info("Alert approved: %s", alert.id)
                return True
        return False

    def get_pipeline_status(self) -> PipelineStatus:
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
