"""Mock market data provider for Indian stocks."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from models import PeerComparison

logger = logging.getLogger("opportunity_radar.market_data")

DATA_PATH = Path(__file__).parent.parent / "data" / "market_data.json"


class MarketDataProvider:
    """Provides price history, fundamentals, and peer data for Indian stocks."""

    def __init__(self):
        with open(DATA_PATH) as f:
            self._data: dict = json.load(f)
        logger.info(f"Loaded market data for {len(self._data)} stocks")

    def get_stock(self, symbol: str) -> dict | None:
        return self._data.get(symbol)

    def get_price(self, symbol: str) -> float:
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
