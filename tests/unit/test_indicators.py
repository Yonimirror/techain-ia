"""Unit tests for technical indicators."""
import pytest
from decimal import Decimal

from core.strategies.indicators import sma, ema, rsi, crossover, crossunder, atr


def d(v: float) -> Decimal:
    return Decimal(str(v))


class TestSMA:
    def test_basic(self):
        prices = [d(1), d(2), d(3), d(4), d(5)]
        result = sma(prices, 3)
        assert result[0] is None
        assert result[1] is None
        assert result[2] == d(2)
        assert result[4] == d(4)

    def test_period_1(self):
        prices = [d(5), d(10)]
        result = sma(prices, 1)
        assert result == prices

    def test_insufficient_data(self):
        result = sma([d(1), d(2)], 5)
        assert all(v is None for v in result)


class TestEMA:
    def test_length(self):
        prices = [d(i) for i in range(1, 21)]
        result = ema(prices, 9)
        assert len(result) == 20

    def test_first_value_is_sma_seed(self):
        prices = [d(i) for i in range(1, 11)]
        result = ema(prices, 5)
        seed = result[4]
        expected_sma = sum(float(p) for p in prices[:5]) / 5
        assert seed is not None
        assert abs(float(seed) - expected_sma) < 0.001

    def test_ema_follows_price(self):
        """EMA should be >= SMA in uptrend (more weight on recent prices)."""
        prices = [d(i) for i in range(1, 31)]
        ema_vals = ema(prices, 10)
        sma_vals = sma(prices, 10)
        last_ema = ema_vals[-1]
        last_sma = sma_vals[-1]
        assert last_ema is not None and last_sma is not None
        assert last_ema >= last_sma - 1e-9  # EMA ≈ SMA at boundary due to float precision


class TestRSI:
    def test_length(self):
        prices = [d(i) for i in range(1, 30)]
        result = rsi(prices, 14)
        assert len(result) == len(prices)

    def test_overbought(self):
        """Consistently rising prices should produce high RSI."""
        prices = [d(100 + i) for i in range(30)]
        result = rsi(prices, 14)
        last = result[-1]
        assert last is not None
        assert float(last) > 70

    def test_oversold(self):
        """Consistently falling prices should produce low RSI."""
        prices = [d(100 - i) for i in range(30)]
        result = rsi(prices, 14)
        last = result[-1]
        assert last is not None
        assert float(last) < 30

    def test_rsi_bounds(self):
        """RSI must always be in [0, 100]."""
        import random
        rng = random.Random(42)
        prices = [d(rng.uniform(10, 200)) for _ in range(100)]
        result = rsi(prices, 14)
        for v in result:
            if v is not None:
                assert 0 <= float(v) <= 100


class TestCrossover:
    def test_cross_up(self):
        fast = [d(1), d(2), d(3), d(5)]
        slow = [d(3), d(3), d(3), d(3)]
        result = crossover(fast, slow)
        assert result[3] is True

    def test_no_cross(self):
        fast = [d(1), d(1), d(1)]
        slow = [d(2), d(2), d(2)]
        result = crossover(fast, slow)
        assert all(not r for r in result)

    def test_cross_down(self):
        fast = [d(5), d(4), d(3), d(1)]
        slow = [d(2), d(2), d(2), d(2)]
        result = crossunder(fast, slow)
        assert result[3] is True
