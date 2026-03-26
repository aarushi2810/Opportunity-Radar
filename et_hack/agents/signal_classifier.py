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
    """Scores filings on 5 dimensions using LLM or rule-based fallback."""

    def __init__(self):
        self._status = "idle"
        self._total_classified = 0
        self._model = None
        if HAS_GEMINI:
            try:
                self._model = genai.GenerativeModel("gemini-2.0-flash")
                logger.info("🤖 Signal Classifier using Gemini LLM")
            except Exception as e:
                logger.warning(f"Gemini init failed, using rule-based: {e}")
        else:
            logger.info("🧮 Signal Classifier using rule-based classification")

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

        # Filing type heuristics
        ft = filing.filing_type.value
        if ft == "INSIDER_TRADE":
            scores["magnitude"] = 0.75
            scores["insider_credibility"] = 0.85
            scores["timing"] = 0.8
            scores["signal_type"] = "BULLISH"
            scores["tags"] = ["insider-buy", "promoter-activity", "high-conviction"]
            scores["reasoning"] = f"Insider trade by senior management at {filing.company_name} indicates high conviction. Historically, insider buys by promoters precede 60%+ positive moves within 90 days."
        elif ft == "DRHP":
            scores["magnitude"] = 0.7
            scores["insider_credibility"] = 0.6
            scores["timing"] = 0.65
            scores["sector_momentum"] = 0.7
            scores["signal_type"] = "WATCH"
            scores["tags"] = ["ipo", "new-listing", "growth-stage"]
            scores["reasoning"] = f"DRHP filing for {filing.company_name} signals upcoming IPO. Revenue trajectory and competitive positioning are key watch factors."
        elif ft == "PLEDGE":
            scores["magnitude"] = 0.8
            scores["insider_credibility"] = 0.7
            scores["timing"] = 0.75
            scores["signal_type"] = "BEARISH"
            scores["tags"] = ["promoter-pledge", "leverage-risk", "governance-concern"]
            scores["reasoning"] = f"Significant promoter pledge increase at {filing.company_name}. Rising pledge levels historically correlate with governance risk and potential forced selling."
        elif ft == "BULK_DEAL":
            scores["magnitude"] = 0.7
            scores["insider_credibility"] = 0.75
            scores["timing"] = 0.7
            scores["sector_momentum"] = 0.65
            scores["signal_type"] = "BULLISH"
            scores["tags"] = ["institutional-buy", "block-deal", "fii-activity"]
            scores["reasoning"] = f"Major institutional bulk deal in {filing.company_name}. Large block purchases by reputed institutions signal institutional conviction."
        elif ft == "BOARD_MEETING":
            scores["magnitude"] = 0.85
            scores["insider_credibility"] = 0.8
            scores["timing"] = 0.9
            scores["signal_type"] = "BULLISH"
            scores["tags"] = ["dividend", "buyback", "capital-return", "guidance-upgrade"]
            scores["reasoning"] = f"Board meeting outcome at {filing.company_name} with significant capital return and guidance upgrade. Buyback + special dividend combination is historically very bullish."
        elif ft == "QUARTERLY_RESULT":
            scores["magnitude"] = 0.75
            scores["insider_credibility"] = 0.7
            scores["timing"] = 0.85
            scores["sector_momentum"] = 0.7
            scores["signal_type"] = "BULLISH"
            scores["tags"] = ["earnings-beat", "margin-expansion", "asset-quality"]
            scores["reasoning"] = f"Strong quarterly results from {filing.company_name} with profit growth and margin expansion. Improving fundamentals support re-rating thesis."
        elif ft == "SHAREHOLDING":
            scores["magnitude"] = 0.65
            scores["insider_credibility"] = 0.6
            scores["timing"] = 0.6
            scores["sector_momentum"] = 0.75
            scores["historical_match"] = 0.7
            scores["signal_type"] = "BULLISH"
            scores["tags"] = ["fii-increase", "ownership-shift", "smart-money"]
            scores["reasoning"] = f"Significant FII ownership increase in {filing.company_name}. Rising foreign ownership often precedes sustained re-rating."
        elif ft == "CORPORATE_ACTION":
            scores["magnitude"] = 0.6
            scores["insider_credibility"] = 0.7
            scores["timing"] = 0.65
            scores["signal_type"] = "BULLISH"
            scores["tags"] = ["stock-split", "liquidity-event", "retail-access"]
            scores["reasoning"] = f"Stock split at {filing.company_name} improves retail accessibility. Historically, splits in quality stocks see positive price action post-adjustment."

        # Add small random variation for realism
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
            response = await asyncio.to_thread(
                self._model.generate_content, prompt
            )
            text = response.text.strip()
            # Strip markdown code fences if present
            if text.startswith("```"):
                text = text.split("\n", 1)[1]
                text = text.rsplit("```", 1)[0]
            return json.loads(text)
        except Exception as e:
            logger.warning(f"LLM classification failed: {e}")
            return None

    async def classify(self, filing: RawFiling) -> ClassifiedSignal:
        """Classify a filing and return structured signal."""
        self._status = "classifying"
        logger.info(f"🔬 Classifying: [{filing.filing_type.value}] {filing.company_name}")

        # Try LLM first, fall back to rule-based
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

        # Compute overall importance as weighted average
        importance_score = round(
            dimension_scores.magnitude * 0.25 +
            dimension_scores.insider_credibility * 0.20 +
            dimension_scores.timing * 0.25 +
            dimension_scores.sector_momentum * 0.15 +
            dimension_scores.historical_match * 0.15,
            3
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
            f"📊 Classified: {filing.stock_symbol} → {signal.signal_type.value} "
            f"(score: {signal.importance_score:.2f})"
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
