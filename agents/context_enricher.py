"""Context Enrichment Agent — adds price, fundamentals, peer data, and impact analysis."""

from __future__ import annotations

import asyncio
import json
import logging

import config
from models import ClassifiedSignal, EnrichedSignal, RawFiling
from message_bus import bus
from infra.market_data import market_data

logger = logging.getLogger("opportunity_radar.context_enricher")

try:
    import google.generativeai as genai
    HAS_GEMINI = bool(config.GEMINI_API_KEY)
    if HAS_GEMINI:
        genai.configure(api_key=config.GEMINI_API_KEY)
except ImportError:
    HAS_GEMINI = False

IMPACT_PROMPT = """You are an Indian equity research analyst. Given this corporate filing and market context, provide a concise "So What?" impact analysis.

Filing: {title}
Company: {company_name} ({stock_symbol})
Filing Summary: {summary}
Signal Type: {signal_type} (Score: {importance_score})

Market Context:
- Current Price: Rs {current_price}
- 1D Change: {change_1d}%  |  1W Change: {change_1w}%  |  1M Change: {change_1m}%
- P/E Ratio: {pe_ratio}  |  EPS: Rs {eps}
- Market Cap: Rs {market_cap_cr} Cr
- Sector: {sector}
- Analyst Consensus: {consensus}

Peers: {peers}

In 3-4 sentences, explain:
1. What this event means for the stock's fundamentals (EPS / margin / debt impact)
2. How this compares to peer companies
3. The likely market reaction based on historical patterns

Be specific with numbers. Do NOT give buy/sell recommendations — only factual analysis."""


class ContextEnrichmentAgent:
    """Pulls price history, fundamentals, and peer comparisons.

    Adds a 'so what?' layer that translates the event into EPS/margin/debt impact.
    """

    def __init__(self):
        self._status = "idle"
        self._total_enriched = 0
        self._model = None
        if HAS_GEMINI:
            try:
                self._model = genai.GenerativeModel("gemini-2.0-flash")
                logger.info("Context Enricher using Gemini for impact analysis")
            except Exception:
                pass

    def _enrich_with_market_data(self, signal: ClassifiedSignal, filing: RawFiling) -> dict:
        """Pull market data for the stock."""
        symbol = filing.stock_symbol
        price_changes = market_data.get_price_changes(symbol)
        fundamentals = market_data.get_fundamentals(symbol)
        peers = market_data.get_peers(symbol)

        return {
            "current_price": market_data.get_price(symbol),
            "price_change_1d_pct": price_changes.get("1d", 0),
            "price_change_1w_pct": price_changes.get("1w", 0),
            "price_change_1m_pct": price_changes.get("1m", 0),
            "pe_ratio": fundamentals.get("pe_ratio", 0),
            "eps": fundamentals.get("eps", 0),
            "market_cap_cr": fundamentals.get("market_cap_cr", 0),
            "sector": market_data.get_sector(symbol),
            "peers": peers,
            "consensus": fundamentals.get("analyst_consensus", "N/A"),
        }

    def _generate_rule_based_impact(self, filing: RawFiling, signal: ClassifiedSignal, ctx: dict) -> str:
        """Generate impact analysis using rules when LLM is unavailable."""
        ft = filing.filing_type.value
        sym = filing.stock_symbol
        price = ctx["current_price"]
        pe = ctx["pe_ratio"]
        sector = ctx["sector"]

        base = f"{filing.company_name} ({sym}) — "

        if ft == "INSIDER_TRADE":
            return base + (
                f"Insider purchase signals strong management conviction at current levels (Rs {price}). "
                f"At PE of {pe}x, the stock trades near sector average for {sector}. "
                "Historically, insider buys of this magnitude have preceded 5-15% appreciation in 60-90 days. "
                "Key monitorable: whether additional insiders follow with similar transactions."
            )
        elif ft == "DRHP":
            return base + (
                "IPO filing indicates the company is seeking public market valuation. "
                "At the proposed issue size, implied market cap suggests premium to listed peers. "
                "Revenue growth trajectory of the company will be the key valuation driver. "
                "Watch for anchor investor allocation and grey market premium as listing indicators."
            )
        elif ft == "PLEDGE":
            return base + (
                f"Rising promoter pledge from current levels is a red flag for governance. "
                f"At PE of {pe}x and current price Rs {price}, any forced pledge invocation could trigger 10-20% downside. "
                f"Peer comparison shows lower pledge ratios in the sector. "
                "Monitor covenant triggers and margin call events weekly."
            )
        elif ft == "BULK_DEAL":
            return base + (
                f"Institutional bulk buy at Rs {price} represents significant conviction from smart money. "
                "The acquisition size relative to daily volume suggests accumulation phase. "
                f"At PE of {pe}x in {sector}, the stock appears attractively valued vs peers. "
                "Historical pattern: institutional bulk buys in quality names see follow-through buying within 2 weeks."
            )
        elif ft == "BOARD_MEETING":
            return base + (
                f"Capital return via dividend and buyback at Rs {price} signals strong cash generation. "
                "Buyback at premium to CMP is EPS-accretive; estimated 2-3% EPS boost from share reduction. "
                "Revenue guidance upgrade indicates improving demand visibility. "
                f"Combined yield makes this attractive vs {sector} peers."
            )
        elif ft == "QUARTERLY_RESULT":
            return base + (
                "Strong quarterly performance beats expectations on profit and margins. "
                "NIM expansion and declining NPAs signal improving business fundamentals. "
                f"At PE of {pe}x, re-rating potential exists if the trajectory sustains. "
                "Credit growth outpacing system average — market share gains in key segments."
            )
        elif ft == "SHAREHOLDING":
            return base + (
                f"FII ownership surge to multi-year highs at Rs {price} indicates global re-allocation to the stock. "
                "Notable institutional buyers are typically lead indicators for sustained re-rating. "
                "Declining MF ownership creates potential supply — but FII flows dominate the narrative. "
                f"At PE of {pe}x in {sector}, valuation supports the foreign investor thesis."
            )
        elif ft == "CORPORATE_ACTION":
            return base + (
                f"Stock split improves liquidity and retail accessibility at Rs {price} pre-split levels. "
                "Historically, quality stocks see 8-12% appreciation in 6 months post-split. "
                "Improved lot size enables F&O participation for retail traders. "
                f"At PE of {pe}x, fundamentals support the management's timing of this action."
            )
        else:
            return base + (
                f"Filing noted at current price Rs {price} (PE: {pe}x). "
                f"Sector: {sector}. Impact assessment requires further analysis."
            )

    async def _generate_llm_impact(self, filing: RawFiling, signal: ClassifiedSignal, ctx: dict) -> str | None:
        """Generate impact analysis using Gemini."""
        if not self._model:
            return None

        peers_str = ", ".join(
            f"{p.name} (PE: {p.pe_ratio}x, MCap: Rs {p.market_cap_cr} Cr)"
            for p in ctx["peers"]
        ) or "No peer data available"

        prompt = IMPACT_PROMPT.format(
            title=filing.title,
            company_name=filing.company_name,
            stock_symbol=filing.stock_symbol,
            summary=filing.summary,
            signal_type=signal.signal_type.value,
            importance_score=signal.importance_score,
            current_price=ctx["current_price"],
            change_1d=ctx["price_change_1d_pct"],
            change_1w=ctx["price_change_1w_pct"],
            change_1m=ctx["price_change_1m_pct"],
            pe_ratio=ctx["pe_ratio"],
            eps=ctx["eps"],
            market_cap_cr=ctx["market_cap_cr"],
            sector=ctx["sector"],
            consensus=ctx["consensus"],
            peers=peers_str,
        )

        try:
            response = await asyncio.to_thread(self._model.generate_content, prompt)
            return response.text.strip()
        except Exception as exc:
            logger.warning("LLM impact analysis failed: %s", exc)
            return None

    async def enrich(self, filing: RawFiling, signal: ClassifiedSignal) -> EnrichedSignal:
        """Enrich a classified signal with market context and impact analysis."""
        self._status = "enriching"
        logger.info("Enriching: %s (signal: %s)", filing.stock_symbol, signal.signal_type.value)

        ctx = self._enrich_with_market_data(signal, filing)

        impact = await self._generate_llm_impact(filing, signal, ctx)
        if impact is None:
            impact = self._generate_rule_based_impact(filing, signal, ctx)

        enriched = EnrichedSignal(
            signal=signal,
            filing=filing,
            current_price=ctx["current_price"],
            price_change_1d_pct=ctx["price_change_1d_pct"],
            price_change_1w_pct=ctx["price_change_1w_pct"],
            price_change_1m_pct=ctx["price_change_1m_pct"],
            pe_ratio=ctx["pe_ratio"],
            eps=ctx["eps"],
            market_cap_cr=ctx["market_cap_cr"],
            sector=ctx["sector"],
            peers=ctx["peers"],
            impact_analysis=impact,
        )

        self._total_enriched += 1
        self._status = "idle"
        logger.info("Enriched: %s — impact analysis generated", filing.stock_symbol)
        return enriched

    async def handle_signal(self, data: tuple):
        """Handler for message bus — enrich and publish result."""
        filing, signal = data

        if signal.importance_score < config.SIGNAL_THRESHOLD:
            logger.info(
                "Skipping low-score signal: %s (score: %.2f < %.2f)",
                filing.stock_symbol, signal.importance_score, config.SIGNAL_THRESHOLD,
            )
            return

        enriched = await self.enrich(filing, signal)
        await bus.publish("enriched_signals", enriched)

    @property
    def status(self) -> str:
        return self._status

    @property
    def total_enriched(self) -> int:
        return self._total_enriched
