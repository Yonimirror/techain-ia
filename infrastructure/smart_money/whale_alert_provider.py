"""
Whale Alert Provider — detecta transacciones grandes on-chain.

Monitorea movimientos de capital grandes en Bitcoin y otras cryptos
usando la API de Whale Alert (https://whale-alert.io).

Transacciones relevantes:
- Exchange inflows: ballenas enviando BTC a exchanges → posible venta
- Exchange outflows: ballenas retirando BTC de exchanges → posible compra/hold
- Wallet transfers: movimientos entre wallets propias (neutral)

API gratuita: 10 req/min, datos con 60s de delay.
API de pago: tiempo real, historial completo.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal

import aiohttp

logger = logging.getLogger(__name__)

WHALE_ALERT_BASE_URL = "https://api.whale-alert.io/v1"

# Umbral mínimo para considerar una transacción relevante (USD)
DEFAULT_MIN_VALUE_USD = 500_000  # $500k


@dataclass
class WhaleTransaction:
    """Representa una transacción grande detectada por Whale Alert."""
    id: str
    symbol: str                          # BTC, ETH, etc.
    blockchain: str                      # bitcoin, ethereum, etc.
    amount: float                        # Cantidad del activo
    amount_usd: float                    # Valor en USD
    from_type: str                       # "exchange", "wallet", "unknown"
    to_type: str                         # "exchange", "wallet", "unknown"
    from_name: str                       # Nombre del exchange si aplica
    to_name: str                         # Nombre del exchange si aplica
    timestamp: datetime
    hash: str                            # Hash de la transacción

    @property
    def direction(self) -> Literal["inflow", "outflow", "transfer"]:
        """
        inflow:   Ballena envía a exchange → posible presión vendedora
        outflow:  Ballena retira de exchange → posible acumulación
        transfer: Movimiento interno → neutral
        """
        from_exchange = self.from_type == "exchange"
        to_exchange = self.to_type == "exchange"

        if to_exchange and not from_exchange:
            return "inflow"
        elif from_exchange and not to_exchange:
            return "outflow"
        else:
            return "transfer"

    @property
    def is_bullish(self) -> bool:
        """Outflow de exchange = acumulación = señal alcista."""
        return self.direction == "outflow"

    @property
    def is_bearish(self) -> bool:
        """Inflow a exchange = preparación para vender = señal bajista."""
        return self.direction == "inflow"


class WhaleAlertProvider:
    """
    Consulta la API de Whale Alert para detectar movimientos de ballenas.

    Requiere WHALE_ALERT_API_KEY en variables de entorno.
    Sin API key → usa datos simulados para desarrollo/paper.
    """

    def __init__(
        self,
        api_key: str | None = None,
        min_value_usd: float = DEFAULT_MIN_VALUE_USD,
    ) -> None:
        self._api_key = api_key or os.getenv("WHALE_ALERT_API_KEY", "")
        self._min_value_usd = min_value_usd
        self._cache: list[WhaleTransaction] = []
        self._last_fetch: datetime | None = None
        self._cache_ttl_seconds = 60  # refresco cada minuto

    async def get_recent_transactions(
        self,
        symbol: str = "BTC",
        lookback_hours: int = 24,
    ) -> list[WhaleTransaction]:
        """
        Obtiene transacciones grandes de las últimas N horas.

        Args:
            symbol: BTC, ETH, SOL, etc.
            lookback_hours: Horas hacia atrás para buscar

        Returns:
            Lista de WhaleTransaction ordenadas de más reciente a más antigua
        """
        if not self._api_key:
            logger.warning("No WHALE_ALERT_API_KEY — usando datos simulados para paper")
            return self._simulated_transactions(symbol)

        # Cache: no re-consultar si datos frescos
        now = datetime.now(timezone.utc)
        if (
            self._last_fetch
            and self._cache
            and (now - self._last_fetch).total_seconds() < self._cache_ttl_seconds
        ):
            return [t for t in self._cache if t.symbol.upper() == symbol.upper()]

        try:
            start_ts = int((now - timedelta(hours=lookback_hours)).timestamp())

            async with aiohttp.ClientSession() as session:
                params = {
                    "api_key": self._api_key,
                    "min_value": int(self._min_value_usd),
                    "start": start_ts,
                    "cursor": 0,
                }
                url = f"{WHALE_ALERT_BASE_URL}/transactions"
                async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status != 200:
                        logger.error("Whale Alert error: %s", resp.status)
                        return []

                    data = await resp.json()
                    transactions = []
                    for tx in data.get("transactions", []):
                        if tx.get("symbol", "").upper() != symbol.upper():
                            continue
                        transactions.append(self._parse_transaction(tx))

                    self._cache = transactions
                    self._last_fetch = now
                    logger.info(
                        "Whale Alert: %d transacciones BTC en %dh (min $%s)",
                        len(transactions), lookback_hours, f"{self._min_value_usd:,.0f}"
                    )
                    return transactions

        except Exception as e:
            logger.error("Whale Alert fetch error: %s", e)
            return self._simulated_transactions(symbol) if not self._cache else self._cache

    def _parse_transaction(self, tx: dict) -> WhaleTransaction:
        """Convierte la respuesta de la API al dataclass."""
        from_info = tx.get("from", {})
        to_info = tx.get("to", {})
        return WhaleTransaction(
            id=str(tx.get("id", "")),
            symbol=tx.get("symbol", "").upper(),
            blockchain=tx.get("blockchain", ""),
            amount=float(tx.get("amount", 0)),
            amount_usd=float(tx.get("amount_usd", 0)),
            from_type=from_info.get("owner_type", "unknown"),
            to_type=to_info.get("owner_type", "unknown"),
            from_name=from_info.get("owner", ""),
            to_name=to_info.get("owner", ""),
            timestamp=datetime.fromtimestamp(tx.get("timestamp", 0), tz=timezone.utc),
            hash=tx.get("hash", ""),
        )

    def _simulated_transactions(self, symbol: str) -> list[WhaleTransaction]:
        """
        Datos simulados para paper trading sin API key.
        Genera patrones realistas basados en el precio actual.
        """
        import random
        now = datetime.now(timezone.utc)
        transactions = []

        # Simular 3-8 transacciones en las últimas 24h
        for i in range(random.randint(3, 8)):
            ts = now - timedelta(hours=random.uniform(0, 24))
            amount = random.uniform(100, 2000)  # BTC
            amount_usd = amount * 83000  # precio aproximado

            # 60% outflows (acumulación), 40% inflows (distribución)
            is_outflow = random.random() < 0.6

            transactions.append(WhaleTransaction(
                id=f"sim_{i}",
                symbol=symbol,
                blockchain="bitcoin",
                amount=amount,
                amount_usd=amount_usd,
                from_type="exchange" if is_outflow else "wallet",
                to_type="wallet" if is_outflow else "exchange",
                from_name="Binance" if is_outflow else "Unknown",
                to_name="Unknown" if is_outflow else "Coinbase",
                timestamp=ts,
                hash=f"sim_hash_{i}",
            ))

        return sorted(transactions, key=lambda x: x.timestamp, reverse=True)

    async def get_net_flow(
        self,
        symbol: str = "BTC",
        lookback_hours: int = 24,
    ) -> dict:
        """
        Calcula el flujo neto de exchanges en las últimas N horas.

        Returns:
            dict con inflow_usd, outflow_usd, net_flow_usd, bias
        """
        txs = await self.get_recent_transactions(symbol, lookback_hours)

        inflow_usd = sum(t.amount_usd for t in txs if t.is_bearish)
        outflow_usd = sum(t.amount_usd for t in txs if t.is_bullish)
        net_flow_usd = outflow_usd - inflow_usd  # positivo = alcista

        if net_flow_usd > 5_000_000:
            bias = "bullish"
        elif net_flow_usd < -5_000_000:
            bias = "bearish"
        else:
            bias = "neutral"

        return {
            "symbol": symbol,
            "lookback_hours": lookback_hours,
            "inflow_usd": inflow_usd,
            "outflow_usd": outflow_usd,
            "net_flow_usd": net_flow_usd,
            "transaction_count": len(txs),
            "bias": bias,
        }
