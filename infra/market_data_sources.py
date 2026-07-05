"""Live market data providers: NSE India (unofficial) and Alpha Vantage.

NSE requires a browser-like cookie session:
  1. GET homepage to acquire session cookies (nseappid, nsit)
  2. GET the quote endpoint with those cookies + a realistic User-Agent

Alpha Vantage is a clean REST API — used as fallback when NSE is blocked.
"""

from __future__ import annotations

import json
import logging
import random
from typing import Optional

import aiohttp

logger = logging.getLogger("opportunity_radar.market_data_sources")

_NSE_BASE = "https://www.nseindia.com"
_NSE_QUOTE_URL = "https://www.nseindia.com/api/quote-equity?symbol={symbol}"
_NSE_SERIES_URL = "https://www.nseindia.com/api/quote-equity?symbol={symbol}&series=EQ"
_AV_QUOTE_URL = (
    "https://www.alphavantage.co/query"
    "?function=GLOBAL_QUOTE&symbol={symbol}.BSE&apikey={key}"
)

_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/605.1.15 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/123.0 Safari/537.36",
]


class NSEProvider:
    """
    Fetches real-time quotes from NSE India's unofficial JSON API.

    The API requires a fresh session cookie obtained by hitting the homepage
    first.  The session is refreshed every ~5 minutes automatically.
    Rate limit: approximately 2 requests/second.
    """

    def __init__(self):
        self._session: Optional[aiohttp.ClientSession] = None
        self._cookies: dict = {}

    def _headers(self) -> dict:
        return {
            "User-Agent": random.choice(_USER_AGENTS),
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.nseindia.com/",
            "Connection": "keep-alive",
        }

    async def _refresh_session(self) -> bool:
        """Hit the NSE homepage to obtain session cookies."""
        try:
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(_NSE_BASE, headers=self._headers()) as resp:
                    if resp.status == 200:
                        self._cookies = {c.key: c.value for c in resp.cookies.values()}
                        logger.debug("NSE session cookies refreshed")
                        return True
        except Exception as exc:
            logger.warning("NSE session refresh failed: %s", exc)
        return False

    async def get_quote(self, symbol: str) -> Optional[dict]:
        """Fetch current quote for a stock symbol.  Returns None on failure."""
        if not self._cookies:
            ok = await self._refresh_session()
            if not ok:
                return None
        url = _NSE_QUOTE_URL.format(symbol=symbol.upper())
        try:
            timeout = aiohttp.ClientTimeout(total=8)
            async with aiohttp.ClientSession(timeout=timeout, cookies=self._cookies) as session:
                async with session.get(url, headers=self._headers()) as resp:
                    if resp.status == 401:
                        # Session expired — refresh and retry once
                        self._cookies = {}
                        return None
                    if resp.status == 200:
                        data = await resp.json(content_type=None)
                        pd = data.get("priceInfo", {})
                        return {
                            "current_price": float(pd.get("lastPrice", 0)),
                            "change_pct": float(pd.get("pChange", 0)),
                            "open": float(pd.get("open", 0)),
                            "high": float(pd.get("intraDayHighLow", {}).get("max", 0)),
                            "low": float(pd.get("intraDayHighLow", {}).get("min", 0)),
                            "close_prev": float(pd.get("previousClose", 0)),
                        }
        except Exception as exc:
            logger.warning("NSE quote fetch failed for %s: %s", symbol, exc)
            # Force session refresh on next call
            self._cookies = {}
        return None


class AlphaVantageProvider:
    """
    Fetches quotes from Alpha Vantage (BSE symbol with .BSE suffix).
    Free tier: 25 requests/day.  Used as fallback when NSE is unavailable.
    """

    def __init__(self, api_key: str):
        self._api_key = api_key

    async def get_quote(self, symbol: str) -> Optional[dict]:
        if not self._api_key:
            return None
        url = _AV_QUOTE_URL.format(symbol=symbol.upper(), key=self._api_key)
        try:
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        gq = data.get("Global Quote", {})
                        if not gq:
                            return None
                        price = float(gq.get("05. price", 0))
                        prev_close = float(gq.get("08. previous close", 0))
                        change_pct = (
                            ((price - prev_close) / prev_close * 100) if prev_close else 0
                        )
                        return {
                            "current_price": price,
                            "change_pct": round(change_pct, 2),
                            "open": float(gq.get("02. open", 0)),
                            "high": float(gq.get("03. high", 0)),
                            "low": float(gq.get("04. low", 0)),
                            "close_prev": prev_close,
                        }
        except Exception as exc:
            logger.warning("Alpha Vantage quote fetch failed for %s: %s", symbol, exc)
        return None
