"""Market data provider with live NSE data + Redis cache.

Provider chain: NSEProvider -> AlphaVantageProvider -> Static JSON fallback
Cache: Redis with 60-second TTL (fakeredis for local dev).
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Optional

import config
from models import PeerComparison

logger = logging.getLogger("opportunity_radar.market_data")

DATA_PATH = Path(__file__).parent.parent / "data" / "market_data.json"

# ── Redis Setup (shared instance) ─────────────────────────────────────────────

try:
    import redis as redis_lib
    _redis_url = config.REDIS_URL
    if _redis_url:
        _redis = redis_lib.from_url(_redis_url, decode_responses=True)
        logger.info("Market data cache: Redis (%s)", _redis_url[:30])
    else:
        import fakeredis
        _redis = fakeredis.FakeRedis(decode_responses=True)
        logger.info("Market data cache: fakeredis (local dev)")
except Exception as exc:
    import fakeredis
    _redis = fakeredis.FakeRedis(decode_responses=True)
    logger.warning("Redis init failed, using fakeredis: %s", exc)

_TTL = config.MARKET_DATA_CACHE_TTL_SEC


def _cache_get(key: str) -> Optional[dict]:
    try:
        raw = _redis.get(key)
        return json.loads(raw) if raw else None
    except Exception:
        return None


def _cache_set(key: str, value: dict):
    try:
        _redis.setex(key, _TTL, json.dumps(value))
    except Exception:
        pass


# ── Lazy provider imports (avoid import-time aiohttp overhead) ────────────────

_nse: Optional[object] = None
_av: Optional[object] = None


def _get_nse():
    global _nse
    if _nse is None:
        try:
            from infra.market_data_sources import NSEProvider
            _nse = NSEProvider()
        except Exception:
            _nse = False
    return _nse if _nse else None


def _get_av():
    global _av
    if _av is None:
        try:
            from infra.market_data_sources import AlphaVantageProvider
            _av = AlphaVantageProvider(api_key=config.ALPHA_VANTAGE_KEY)
        except Exception:
            _av = False
    return _av if _av else None


class MarketDataProvider:
    """Provides price history, fundamentals, and peer data for Indian stocks.

    In live mode (DEMO_MODE=false) prices are fetched from NSE/Alpha Vantage.
    In demo mode the static JSON file is used for all data.
    Prices are Redis-cached for MARKET_DATA_CACHE_TTL_SEC seconds.
    """

    def __init__(self):
        with open(DATA_PATH) as f:
            self._data: dict = json.load(f)
        logger.info("Loaded static market data for %d stocks", len(self._data))

    def get_stock(self, symbol: str) -> Optional[dict]:
        return self._data.get(symbol)

    def get_price(self, symbol: str) -> float:
        """Return current price. Uses cache then static fallback."""
        cached = _cache_get(f"price:{symbol}")
        if cached:
            return float(cached.get("current_price", 0))

        stock = self.get_stock(symbol)
        price = stock["current_price"] if stock else 0
        _cache_set(f"price:{symbol}", {"current_price": price})
        return price

    async def get_price_live(self, symbol: str) -> float:
        """Fetch live price from NSE or Alpha Vantage, with cache and static fallback."""
        cached = _cache_get(f"price:{symbol}")
        if cached:
            return float(cached.get("current_price", 0))

        quote = None

        nse = _get_nse()
        if nse and not config.DEMO_MODE:
            try:
                quote = await nse.get_quote(symbol)
            except Exception as exc:
                logger.debug("NSE quote error for %s: %s", symbol, exc)

        if not quote:
            av = _get_av()
            if av and not config.DEMO_MODE and config.ALPHA_VANTAGE_KEY:
                try:
                    quote = await av.get_quote(symbol)
                except Exception as exc:
                    logger.debug("Alpha Vantage error for %s: %s", symbol, exc)

        if quote and quote.get("current_price"):
            price = float(quote["current_price"])
            _cache_set(f"price:{symbol}", quote)
            logger.debug("Live price fetched for %s: %.2f", symbol, price)
            return price

        # Static fallback
        stock = self.get_stock(symbol)
        return stock["current_price"] if stock else 0

    def get_price_changes(self, symbol: str) -> dict:
        stock = self.get_stock(symbol)
        if not stock:
            return {"1d": 0, "1w": 0, "1m": 0}
        cp = stock["current_price"]
        return {
            "1d": round((cp - stock["price_1d_ago"]) / stock["price_1d_ago"] * 100, 2),
            "1w": round((cp - stock["price_1w_ago"]) / stock["price_1w_ago"] * 100, 2),
            "1m": round((cp - stock["price_1m_ago"]) / stock["price_1m_ago"] * 100, 2),
        }

    def get_fundamentals(self, symbol: str) -> dict:
        stock = self.get_stock(symbol)
        if not stock:
            return {}
        return {
            "pe_ratio": stock["pe_ratio"],
            "eps": stock["eps"],
            "market_cap_cr": stock["market_cap_cr"],
            "52w_high": stock["52w_high"],
            "52w_low": stock["52w_low"],
            "analyst_consensus": stock["analyst_consensus"],
            "target_price": stock["target_price"],
        }

    def get_peers(self, symbol: str) -> list[PeerComparison]:
        stock = self.get_stock(symbol)
        if not stock:
            return []
        peers = []
        for peer_symbol in stock.get("peers", []):
            peer_data = self.get_stock(peer_symbol)
            if peer_data:
                cp = peer_data["current_price"]
                price_1m = peer_data["price_1m_ago"]
                peers.append(PeerComparison(
                    symbol=peer_symbol,
                    name=peer_data["name"],
                    pe_ratio=peer_data["pe_ratio"],
                    market_cap_cr=peer_data["market_cap_cr"],
                    ytd_return_pct=round((cp - price_1m) / price_1m * 100, 2),
                ))
        return peers

    def get_sector(self, symbol: str) -> str:
        stock = self.get_stock(symbol)
        return stock["sector"] if stock else "Unknown"


# Singleton
market_data = MarketDataProvider()
