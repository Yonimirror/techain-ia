"""
Order Flow Provider — detecta órdenes grandes en Binance.

Analiza el libro de órdenes y trades recientes en Binance para detectar:
- Large buy walls: soporte institucional
- Large sell walls: resistencia institucional
- Iceberg orders: órdenes grandes divididas
- Buy/Sell imbalance: presión compradora vs vendedora
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

import aiohttp

logger = logging.getLogger(__name__)

BINANCE_BASE_URL = "https://api.binance.com/api/v3"

# Umbral para considerar una orden como "grande" (BTC)
LARGE_ORDER_BTC = 10.0    # 10 BTC ≈ $830k
LARGE_ORDER_USD = 500_000  # $500k


@dataclass
class OrderFlowSignal:
    """Señal derivada del análisis del order book de Binance."""
    symbol: str
    timestamp: datetime

    # Volumen en las últimas N velas
    buy_volume: float       # Volumen comprador (BTC)
    sell_volume: float      # Volumen vendedor (BTC)
    volume_ratio: float     # buy_volume / sell_volume (>1 = más compradores)

    # Order book snapshot
    best_bid: float
    best_ask: float
    spread_bps: float       # Spread en puntos básicos

    # Grandes órdenes detectadas
    large_bids_usd: float   # Valor total de bids grandes
    large_asks_usd: float   # Valor total de asks grandes
    bid_ask_imbalance: float  # (bids - asks) / (bids + asks) ∈ [-1, 1]

    @property
    def bias(self) -> Literal["bullish", "bearish", "neutral"]:
        """Sesgo basado en volumen + order book."""
        if self.bid_ask_imbalance > 0.3 and self.volume_ratio > 1.2:
            return "bullish"
        elif self.bid_ask_imbalance < -0.3 and self.volume_ratio < 0.8:
            return "bearish"
        return "neutral"

    @property
    def is_bullish(self) -> bool:
        return self.bias == "bullish"

    @property
    def is_bearish(self) -> bool:
        return self.bias == "bearish"


class OrderFlowProvider:
    """
    Analiza el order book y flujo de órdenes de Binance.

    No requiere API key — endpoints públicos.
    """

    def __init__(self, depth: int = 20) -> None:
        self._depth = depth  # niveles del order book a analizar
        self._symbol_map = {
            "BTC": "BTCUSDT",
            "ETH": "ETHUSDT",
            "SOL": "SOLUSDT",
        }

    async def get_signal(self, symbol: str = "BTC") -> OrderFlowSignal:
        """
        Obtiene señal de order flow para un símbolo.

        Combina:
        1. Order book snapshot (bids vs asks)
        2. Últimos trades (volumen comprador vs vendedor)
        """
        pair = self._symbol_map.get(symbol.upper(), f"{symbol}USDT")

        try:
            async with aiohttp.ClientSession() as session:
                # Fetch order book y trades en paralelo
                ob_task = self._fetch_orderbook(session, pair)
                trades_task = self._fetch_recent_trades(session, pair)

                import asyncio
                orderbook, trades = await asyncio.gather(ob_task, trades_task)

                return self._compute_signal(symbol, orderbook, trades)

        except Exception as e:
            logger.warning("OrderFlow fetch error para %s: %s — usando señal neutral", symbol, e)
            return self._neutral_signal(symbol)

    async def _fetch_orderbook(self, session: aiohttp.ClientSession, pair: str) -> dict:
        url = f"{BINANCE_BASE_URL}/depth"
        async with session.get(url, params={"symbol": pair, "limit": self._depth},
                               timeout=aiohttp.ClientTimeout(total=5)) as resp:
            return await resp.json()

    async def _fetch_recent_trades(self, session: aiohttp.ClientSession, pair: str) -> list:
        url = f"{BINANCE_BASE_URL}/trades"
        async with session.get(url, params={"symbol": pair, "limit": 500},
                               timeout=aiohttp.ClientTimeout(total=5)) as resp:
            return await resp.json()

    def _compute_signal(self, symbol: str, orderbook: dict, trades: list) -> OrderFlowSignal:
        """Calcula señal de order flow a partir de datos crudos."""
        now = datetime.now(timezone.utc)

        # Order book análisis
        bids = [(float(p), float(q)) for p, q in orderbook.get("bids", [])]
        asks = [(float(p), float(q)) for p, q in orderbook.get("asks", [])]

        best_bid = bids[0][0] if bids else 0.0
        best_ask = asks[0][0] if asks else 0.0
        mid_price = (best_bid + best_ask) / 2
        spread_bps = ((best_ask - best_bid) / mid_price * 10000) if mid_price > 0 else 0.0

        # Grandes órdenes en el book
        large_bids_usd = sum(p * q for p, q in bids if p * q >= LARGE_ORDER_USD)
        large_asks_usd = sum(p * q for p, q in asks if p * q >= LARGE_ORDER_USD)
        total_book = large_bids_usd + large_asks_usd
        bid_ask_imbalance = (
            (large_bids_usd - large_asks_usd) / total_book
            if total_book > 0 else 0.0
        )

        # Volumen de trades recientes (maker = vendedor, taker = comprador)
        buy_volume = sum(float(t["qty"]) for t in trades if not t.get("isBuyerMaker", True))
        sell_volume = sum(float(t["qty"]) for t in trades if t.get("isBuyerMaker", False))
        volume_ratio = buy_volume / sell_volume if sell_volume > 0 else 1.0

        signal = OrderFlowSignal(
            symbol=symbol,
            timestamp=now,
            buy_volume=buy_volume,
            sell_volume=sell_volume,
            volume_ratio=volume_ratio,
            best_bid=best_bid,
            best_ask=best_ask,
            spread_bps=spread_bps,
            large_bids_usd=large_bids_usd,
            large_asks_usd=large_asks_usd,
            bid_ask_imbalance=bid_ask_imbalance,
        )

        logger.info(
            "OrderFlow %s: bias=%s | vol_ratio=%.2f | book_imbalance=%.2f | spread=%.1fbps",
            symbol, signal.bias, volume_ratio, bid_ask_imbalance, spread_bps,
        )
        return signal

    def _neutral_signal(self, symbol: str) -> OrderFlowSignal:
        return OrderFlowSignal(
            symbol=symbol,
            timestamp=datetime.now(timezone.utc),
            buy_volume=0, sell_volume=0, volume_ratio=1.0,
            best_bid=0, best_ask=0, spread_bps=0,
            large_bids_usd=0, large_asks_usd=0, bid_ask_imbalance=0,
        )
