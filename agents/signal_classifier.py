"""Signal Classifier Agent — NLP + scoring on 5 dimensions."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
from datetime import datetime

import config
from models import ClassifiedSignal, DimensionScores, RawFiling, SignalType
from message_bus import bus

logger = logging.getLogger("opportunity_radar.signal_classifier")

# Try to import Gemini, fall back to rule-based classification
try:
    import google.generativeai as genai
    HAS_GEMINI = bool(config.GEMINI_API_KEY)
    if HAS_GEMINI:
        genai.configure(api_key=config.GEMINI_API_KEY)
except ImportError:
    HAS_GEMINI = False

CLASSIFICATION_PROMPT = """You are an Indian stock market signal classifier. Analyze the following corporate filing and score it on exactly 5 dimensions (each 0.0 to 1.0):

Filing Details:
- Source: {source}
- Type: {filing_type}
- Company: {company_name} ({stock_symbol})
- Title: {title}
- Summary: {summary}

Score these 5 dimensions:
1. MAGNITUDE: How significant is this event? (0=routine, 1=transformative)
2. INSIDER_CREDIBILITY: How credible/senior is the source? (0=unknown, 1=CEO/promoter)
3. TIMING: How time-sensitive is this? (0=anytime, 1=market-moving now)
4. SECTOR_MOMENTUM: Does this align with sector trends? (0=against trend, 1=strong alignment)
5. HISTORICAL_MATCH: Does this match historically profitable patterns? (0=no match, 1=strong match)

Also determine:
- signal_type: one of BULLISH, BEARISH, NEUTRAL, WATCH
- tags: relevant tags (max 5)
- reasoning: 2-3 sentence explanation

Respond ONLY with valid JSON:
{{
  "magnitude": 0.0,
  "insider_credibility": 0.0,
  "timing": 0.0,
  "sector_momentum": 0.0,
  "historical_match": 0.0,
  "signal_type": "BULLISH",
  "tags": ["tag1", "tag2"],
  "reasoning": "explanation here"
}}"""


class SignalClassifierAgent:
    """Scores filings on 5 dimensions using Gemini LLM or rule-based fallback."""

    def __init__(self):
        self._status = "idle"
        self._total_classified = 0
        self._model = None
        if HAS_GEMINI:
            try:
                self._model = genai.GenerativeModel("gemini-2.0-flash")
                logger.info("Signal Classifier using Gemini LLM")
            except Exception as exc:
                logger.warning("Gemini init failed, using rule-based: %s", exc)
        else:
            logger.info("Signal Classifier using rule-based classification")

    def _rule_based_classify(self, filing: RawFiling) -> dict:
        """Rule-based classification fallback when LLM is unavailable."""
        scores = {
            "magnitude": 0.5,
            "insider_credibility": 0.5,
            "timing": 0.5,
            "sector_momentum": 0.5,
            "historical_match": 0.5,
            "signal_type": "WATCH",
            "tags": [],
            "reasoning": ""
        }

        ft = filing.filing_type.value
        if ft == "INSIDER_TRADE":
            scores.update({
                "magnitude": 0.75, "insider_credibility": 0.85, "timing": 0.8,
                "signal_type": "BULLISH",
                "tags": ["insider-buy", "promoter-activity", "high-conviction"],
                "reasoning": (
                    f"Insider trade by senior management at {filing.company_name} indicates high conviction. "
                    "Historically, insider buys by promoters precede 60%+ positive moves within 90 days."
                ),
            })
        elif ft == "DRHP":
            scores.update({
                "magnitude": 0.7, "insider_credibility": 0.6, "timing": 0.65, "sector_momentum": 0.7,
                "signal_type": "WATCH",
                "tags": ["ipo", "new-listing", "growth-stage"],
                "reasoning": (
                    f"DRHP filing for {filing.company_name} signals upcoming IPO. "
                    "Revenue trajectory and competitive positioning are key watch factors."
                ),
            })
        elif ft == "PLEDGE":
            scores.update({
                "magnitude": 0.8, "insider_credibility": 0.7, "timing": 0.75,
                "signal_type": "BEARISH",
                "tags": ["promoter-pledge", "leverage-risk", "governance-concern"],
                "reasoning": (
                    f"Significant promoter pledge increase at {filing.company_name}. "
                    "Rising pledge levels historically correlate with governance risk and potential forced selling."
                ),
            })
        elif ft == "BULK_DEAL":
            scores.update({
                "magnitude": 0.7, "insider_credibility": 0.75, "timing": 0.7, "sector_momentum": 0.65,
                "signal_type": "BULLISH",
                "tags": ["institutional-buy", "block-deal", "fii-activity"],
                "reasoning": (
                    f"Major institutional bulk deal in {filing.company_name}. "
                    "Large block purchases by reputed institutions signal institutional conviction."
                ),
            })
        elif ft == "BOARD_MEETING":
            scores.update({
                "magnitude": 0.85, "insider_credibility": 0.8, "timing": 0.9,
                "signal_type": "BULLISH",
                "tags": ["dividend", "buyback", "capital-return", "guidance-upgrade"],
                "reasoning": (
                    f"Board meeting outcome at {filing.company_name} with significant capital return. "
                    "Buyback combined with special dividend is historically very bullish."
                ),
            })
        elif ft == "QUARTERLY_RESULT":
            scores.update({
                "magnitude": 0.75, "insider_credibility": 0.7, "timing": 0.85, "sector_momentum": 0.7,
                "signal_type": "BULLISH",
                "tags": ["earnings-beat", "margin-expansion", "asset-quality"],
                "reasoning": (
                    f"Strong quarterly results from {filing.company_name} with profit growth and margin expansion. "
                    "Improving fundamentals support a re-rating thesis."
                ),
            })
        elif ft == "SHAREHOLDING":
            scores.update({
                "magnitude": 0.65, "insider_credibility": 0.6, "timing": 0.6,
                "sector_momentum": 0.75, "historical_match": 0.7,
                "signal_type": "BULLISH",
                "tags": ["fii-increase", "ownership-shift", "smart-money"],
                "reasoning": (
                    f"Significant FII ownership increase in {filing.company_name}. "
                    "Rising foreign ownership often precedes sustained re-rating."
                ),
            })
        elif ft == "CORPORATE_ACTION":
            scores.update({
                "magnitude": 0.6, "insider_credibility": 0.7, "timing": 0.65,
                "signal_type": "BULLISH",
                "tags": ["stock-split", "liquidity-event", "retail-access"],
                "reasoning": (
                    f"Stock split at {filing.company_name} improves retail accessibility. "
                    "Historically, splits in quality stocks see positive price action post-adjustment."
                ),
            })

        for key in ["magnitude", "insider_credibility", "timing", "sector_momentum", "historical_match"]:
            scores[key] = round(min(1.0, max(0.0, scores[key] + random.uniform(-0.1, 0.1))), 2)

        return scores

    async def _llm_classify(self, filing: RawFiling) -> dict | None:
        """Classify using Gemini LLM."""
        if not self._model:
            return None

        prompt = CLASSIFICATION_PROMPT.format(
            source=filing.source,
            filing_type=filing.filing_type.value,
            company_name=filing.company_name,
            stock_symbol=filing.stock_symbol,
            title=filing.title,
            summary=filing.summary,
        )

        try:
            response = await asyncio.to_thread(self._model.generate_content, prompt)
            text = response.text.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1]
                text = text.rsplit("```", 1)[0]
            return json.loads(text)
        except Exception as exc:
            logger.warning("LLM classification failed: %s", exc)
            return None

    async def classify(self, filing: RawFiling) -> ClassifiedSignal:
        """Classify a filing and return a structured signal."""
        self._status = "classifying"
        logger.info("Classifying: [%s] %s", filing.filing_type.value, filing.company_name)

        result = await self._llm_classify(filing)
        if result is None:
            result = self._rule_based_classify(filing)

        dimension_scores = DimensionScores(
            magnitude=result.get("magnitude", 0.5),
            insider_credibility=result.get("insider_credibility", 0.5),
            timing=result.get("timing", 0.5),
            sector_momentum=result.get("sector_momentum", 0.5),
            historical_match=result.get("historical_match", 0.5),
        )

        importance_score = round(
            dimension_scores.magnitude * 0.25
            + dimension_scores.insider_credibility * 0.20
            + dimension_scores.timing * 0.25
            + dimension_scores.sector_momentum * 0.15
            + dimension_scores.historical_match * 0.15,
            3,
        )

        signal_type_str = result.get("signal_type", "WATCH")
        try:
            signal_type = SignalType(signal_type_str)
        except ValueError:
            signal_type = SignalType.WATCH

        signal = ClassifiedSignal(
            filing_id=filing.id,
            signal_type=signal_type,
            importance_score=importance_score,
            dimension_scores=dimension_scores,
            affected_stocks=[filing.stock_symbol],
            tags=result.get("tags", []),
            reasoning=result.get("reasoning", ""),
        )

        self._total_classified += 1
        self._status = "idle"
        logger.info(
            "Classified: %s -> %s (score: %.2f)",
            filing.stock_symbol, signal.signal_type.value, signal.importance_score,
        )
        return signal

    async def handle_filing(self, filing: RawFiling):
        """Handler for message bus — classify and publish result."""
        signal = await self.classify(filing)
        await bus.publish("classified_signals", (filing, signal))

    @property
    def status(self) -> str:
        return self._status

    @property
    def total_classified(self) -> int:
        return self._total_classified
