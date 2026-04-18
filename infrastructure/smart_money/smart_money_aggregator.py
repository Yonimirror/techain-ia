"""
Smart Money Aggregator — combina Whale Alert + Order Flow en una señal unificada.

Produce SmartMoneySignal con:
- Bias: bullish / bearish / neutral
- Conviction: 0.0 a 1.0 (cuánto confiamos en la señal)
- Recomendación de tamaño de posición

La convicción se usa para escalar el tamaño de la posición:
  Conviction 0.9 + RSI oversold → posición máxima (15% capital)
  Conviction 0.5 + RSI oversold → posición normal (8% capital)
  Conviction 0.0 + RSI oversold → posición mínima (3% capital)
  Conviction any + RSI NOT oversold → NO entra
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Literal

from infrastructure.smart_money.whale_alert_provider import WhaleAlertProvider
from infrastructure.smart_money.order_flow_provider import OrderFlowProvider

logger = logging.getLogger(__name__)


class SmartMoneyBias(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


@dataclass
class SmartMoneySignal:
    """Señal consolidada de Smart Money para un activo."""
    symbol: str
    timestamp: datetime
    bias: SmartMoneyBias
    conviction: float          # 0.0 a 1.0

    # Detalle de fuentes
    whale_bias: str            # bullish/bearish/neutral
    whale_net_flow_usd: float  # flujo neto de ballenas
    orderflow_bias: str        # bullish/bearish/neutral
    orderflow_imbalance: float # -1 a 1

    # Para logging y explicabilidad
    reason: str

    @property
    def position_size_multiplier(self) -> float:
        """
        Factor de escala para el tamaño de posición.
        Usado por la estrategia RSI+SmartMoney.

        bullish high conviction  → 1.5x (posición más grande)
        bullish medium           → 1.0x (posición normal)
        neutral                  → 0.7x (posición reducida)
        bearish                  → 0.0x (no entrar)
        """
        if self.bias == SmartMoneyBias.BEARISH:
            return 0.0
        if self.bias == SmartMoneyBias.NEUTRAL:
            return 0.7
        # bullish
        if self.conviction >= 0.7:
            return 1.5
        elif self.conviction >= 0.4:
            return 1.0
        else:
            return 0.7

    @property
    def should_skip(self) -> bool:
        """True si Smart Money indica que NO se debe entrar."""
        return self.bias == SmartMoneyBias.BEARISH

    @property
    def is_high_conviction(self) -> bool:
        return self.bias == SmartMoneyBias.BULLISH and self.conviction >= 0.7


class SmartMoneyAggregator:
    """
    Agrega señales de múltiples fuentes Smart Money.

    Fuentes:
    1. Whale Alert: transacciones on-chain grandes
    2. Binance Order Flow: desequilibrio comprador/vendedor

    Lógica de consenso:
    - Ambas bullish → conviction alta (0.8-1.0)
    - Una bullish, otra neutral → conviction media (0.5-0.7)
    - Una bullish, otra bearish → neutral (conflicto)
    - Ambas bearish → bearish (evitar entrada)
    """

    def __init__(
        self,
        whale_provider: WhaleAlertProvider | None = None,
        orderflow_provider: OrderFlowProvider | None = None,
        lookback_hours: int = 24,
    ) -> None:
        self._whale = whale_provider or WhaleAlertProvider()
        self._orderflow = orderflow_provider or OrderFlowProvider()
        self._lookback_hours = lookback_hours

    async def get_signal(self, symbol: str = "BTC") -> SmartMoneySignal:
        """Obtiene señal Smart Money consolidada para un activo."""
        import asyncio

        # Obtener señales de ambas fuentes en paralelo
        whale_flow, orderflow = await asyncio.gather(
            self._whale.get_net_flow(symbol, self._lookback_hours),
            self._orderflow.get_signal(symbol),
        )

        whale_bias = whale_flow["bias"]
        of_bias = orderflow.bias

        # Combinar señales
        bias, conviction, reason = self._combine(
            whale_bias, whale_flow["net_flow_usd"],
            of_bias, orderflow.bid_ask_imbalance,
        )

        signal = SmartMoneySignal(
            symbol=symbol,
            timestamp=datetime.now(timezone.utc),
            bias=SmartMoneyBias(bias),
            conviction=conviction,
            whale_bias=whale_bias,
            whale_net_flow_usd=whale_flow["net_flow_usd"],
            orderflow_bias=of_bias,
            orderflow_imbalance=orderflow.bid_ask_imbalance,
            reason=reason,
        )

        logger.info(
            "SmartMoney %s: bias=%s | conviction=%.2f | whale=%s ($%s) | flow=%s | %s",
            symbol, bias, conviction,
            whale_bias, f"{whale_flow['net_flow_usd']:,.0f}",
            of_bias, reason,
        )

        return signal

    def _combine(
        self,
        whale_bias: str,
        whale_net_flow: float,
        of_bias: str,
        of_imbalance: float,
    ) -> tuple[str, float, str]:
        """
        Combina las dos señales en bias + conviction.
        Returns: (bias, conviction, reason)
        """
        scores = {"bullish": 1, "neutral": 0, "bearish": -1}
        whale_score = scores.get(whale_bias, 0)
        of_score = scores.get(of_bias, 0)
        combined = whale_score + of_score  # -2 a 2

        if combined == 2:
            return "bullish", 0.9, "Ballenas acumulando + compradores dominantes en book"
        elif combined == 1 and whale_score == 1:
            return "bullish", 0.6, "Ballenas acumulando, book neutral"
        elif combined == 1 and of_score == 1:
            return "bullish", 0.5, "Compradores dominantes en book, ballenas neutras"
        elif combined == 0 and whale_score != of_score:
            return "neutral", 0.3, "Conflicto: ballenas y order flow divergen"
        elif combined == 0:
            return "neutral", 0.2, "Sin señal clara de Smart Money"
        elif combined == -1 and whale_score == -1:
            return "bearish", 0.6, "Ballenas distribuyendo a exchanges"
        elif combined == -1 and of_score == -1:
            return "bearish", 0.5, "Vendedores dominantes en book"
        else:  # combined == -2
            return "bearish", 0.9, "Ballenas distribuyendo + vendedores dominantes"
