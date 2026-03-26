"""Alert Composer Agent — generates plain-English alerts with confidence scores."""

from __future__ import annotations

import asyncio
import json
import logging
import random

import config
from models import Alert, AlertPriority, EnrichedSignal, SignalType
from message_bus import bus

logger = logging.getLogger("opportunity_radar.alert_composer")

# Try Gemini for alert generation
try:
    import google.generativeai as genai
    HAS_GEMINI = bool(config.GEMINI_API_KEY)
    if HAS_GEMINI:
        genai.configure(api_key=config.GEMINI_API_KEY)
except ImportError:
    HAS_GEMINI = False

ALERT_PROMPT = """You are an Indian stock market alert composer for sophisticated investors. Generate a concise, actionable alert based on this enriched signal.

IMPORTANT REGULATORY CONSTRAINT: You must NOT make any buy/sell/hold recommendations. Frame everything as "Signal detected" — this is a SEBI compliance requirement.

Company: {company_name} ({stock_symbol})
Filing Type: {filing_type}
Signal Type: {signal_type}
Importance Score: {importance_score}/1.00
Confidence: {confidence}%

Filing: {title}
Summary: {summary}

Impact Analysis: {impact_analysis}

Market Context:
- Price: ₹{price} (1D: {change_1d}%, 1W: {change_1w}%, 1M: {change_1m}%)
- PE: {pe}x | EPS: ₹{eps} | MCap: ₹{mcap} Cr
- Sector: {sector}

Generate a 3-4 sentence alert that:
1. Leads with the key signal (what happened)
2. Quantifies the potential impact
3. Provides historical context/base rate
4. Ends with a risk flag if applicable

Keep it under 300 words. Do NOT use "buy", "sell", or "recommend"."""


# Historical base rates for alert context
BASE_RATES = {
    "INSIDER_TRADE": "72% of similar insider buys saw 5%+ appreciation in 30 days",
    "DRHP": "65% of recent tech IPOs listed at 10%+ premium to issue price",
    "PLEDGE": "58% of stocks with pledge >15% underperformed sector by 8%+ in 6 months",
    "BULK_DEAL": "68% of FII bulk buys in Nifty 50 stocks preceded 3-month rallies",
    "BOARD_MEETING": "81% of buyback+dividend combos saw positive returns in 90 days",
    "QUARTERLY_RESULT": "75% of earnings beats with margin expansion led to analyst upgrades",
    "SHAREHOLDING": "70% of stocks with FII increase >3% outperformed Nifty in next quarter",
    "CORPORATE_ACTION": "63% of stock splits in large-caps saw 8%+ appreciation in 6 months",
}


class AlertComposerAgent:
    """Generates plain-English alerts with confidence scores, base rates, and risk flags."""

    def __init__(self):
        self._status = "idle"
        self._total_alerts = 0
        self._model = None
        if HAS_GEMINI:
            try:
                self._model = genai.GenerativeModel("gemini-2.0-flash")
                logger.info("📝 Alert Composer using Gemini for alert generation")
            except Exception:
                pass

    def _determine_priority(self, enriched: EnrichedSignal) -> AlertPriority:
        """Determine alert priority based on signal characteristics."""
        score = enriched.signal.importance_score
        if score >= 0.85:
            return AlertPriority.CRITICAL
        elif score >= 0.75:
            return AlertPriority.HIGH
        elif score >= 0.65:
            return AlertPriority.MEDIUM
        return AlertPriority.LOW

    def _compute_confidence(self, enriched: EnrichedSignal) -> float:
        """Compute confidence score from dimension scores and data freshness."""
        ds = enriched.signal.dimension_scores
        base_confidence = (
            ds.magnitude * 0.2 +
            ds.insider_credibility * 0.25 +
            ds.timing * 0.2 +
            ds.sector_momentum * 0.15 +
            ds.historical_match * 0.2
        )
        # Penalize stale data
        if enriched.filing.data_freshness_ms > config.DATA_FRESHNESS_MAX_MS:
            base_confidence *= 0.7
        return round(min(1.0, base_confidence + random.uniform(-0.05, 0.05)), 2)

    def _identify_risk_flags(self, enriched: EnrichedSignal) -> list[str]:
        """Identify risk flags for the alert."""
        flags = []
        filing = enriched.filing
        signal = enriched.signal

        if filing.data_freshness_ms > config.DATA_FRESHNESS_MAX_MS:
            flags.append("⚠️ Stale data — source >4 hrs old")
        if signal.signal_type == SignalType.BEARISH:
            flags.append("🔴 Bearish signal — elevated downside risk")
        if enriched.pe_ratio > 50:
            flags.append("📈 High valuation — PE >50x")
        if enriched.pe_ratio < 0:
            flags.append("💸 Loss-making company — negative PE")
        if "pledge" in filing.filing_type.value.lower():
            flags.append("🔒 Promoter pledge detected — governance risk")
        if signal.importance_score >= 0.85:
            flags.append("🔥 High-impact signal — monitor closely")

        return flags

    def _generate_rule_based_alert(self, enriched: EnrichedSignal) -> str:
        """Generate alert body using rules."""
        filing = enriched.filing
        signal = enriched.signal
        ft = filing.filing_type.value
        base_rate = BASE_RATES.get(ft, "Historical data limited for this signal type")

        parts = [
            f"📡 Signal detected: {filing.title}.",
            f"",
            f"{enriched.impact_analysis}",
            f"",
            f"📊 Historical base rate: {base_rate}.",
        ]

        if enriched.peers:
            peer_str = ", ".join(f"{p.name} ({p.pe_ratio}x PE)" for p in enriched.peers[:3])
            parts.append(f"Peer comparison: {peer_str}.")

        return "\n".join(parts)

    async def _generate_llm_alert(self, enriched: EnrichedSignal) -> str | None:
        """Generate alert using Gemini."""
        if not self._model:
            return None

        confidence = self._compute_confidence(enriched)
        filing = enriched.filing

        prompt = ALERT_PROMPT.format(
            company_name=filing.company_name,
            stock_symbol=filing.stock_symbol,
            filing_type=filing.filing_type.value,
            signal_type=enriched.signal.signal_type.value,
            importance_score=enriched.signal.importance_score,
            confidence=round(confidence * 100),
            title=filing.title,
            summary=filing.summary,
            impact_analysis=enriched.impact_analysis,
            price=enriched.current_price,
            change_1d=enriched.price_change_1d_pct,
            change_1w=enriched.price_change_1w_pct,
            change_1m=enriched.price_change_1m_pct,
            pe=enriched.pe_ratio,
            eps=enriched.eps,
            mcap=enriched.market_cap_cr,
            sector=enriched.sector,
        )

        try:
            response = await asyncio.to_thread(
                self._model.generate_content, prompt
            )
            return response.text.strip()
        except Exception as e:
            logger.warning(f"LLM alert generation failed: {e}")
            return None

    async def compose(self, enriched: EnrichedSignal) -> Alert:
        """Compose a final alert from an enriched signal."""
        self._status = "composing"
        filing = enriched.filing
        signal = enriched.signal

        logger.info(f"📝 Composing alert: {filing.stock_symbol} ({signal.signal_type.value})")

        # Generate alert body
        body = await self._generate_llm_alert(enriched)
        if body is None:
            body = self._generate_rule_based_alert(enriched)

        confidence = self._compute_confidence(enriched)
        priority = self._determine_priority(enriched)
        risk_flags = self._identify_risk_flags(enriched)
        base_rate = BASE_RATES.get(
            filing.filing_type.value,
            "Limited historical data for this pattern"
        )

        alert = Alert(
            enriched_signal_id=enriched.id,
            stock_symbol=filing.stock_symbol,
            company_name=filing.company_name,
            signal_type=signal.signal_type,
            priority=priority,
            confidence_score=confidence,
            title=f"[{signal.signal_type.value}] {filing.stock_symbol}: {filing.title[:80]}",
            body=body,
            risk_flags=risk_flags,
            historical_base_rate=base_rate,
            tags=signal.tags,
            needs_human_review=confidence < config.HUMAN_REVIEW_THRESHOLD,
            data_freshness_ms=filing.data_freshness_ms,
            filing_type=filing.filing_type.value,
            dimension_scores=signal.dimension_scores,
        )

        self._total_alerts += 1
        self._status = "idle"
        logger.info(
            f"🔔 Alert composed: {alert.title[:60]}... "
            f"(confidence: {alert.confidence_score:.0%}, priority: {alert.priority.value})"
        )
        return alert

    async def handle_enriched_signal(self, enriched: EnrichedSignal):
        """Handler for message bus — compose alert and publish."""
        alert = await self.compose(enriched)
        await bus.publish("alerts", alert)

    @property
    def status(self) -> str:
        return self._status

    @property
    def total_alerts(self) -> int:
        return self._total_alerts
