"""
Pure indicator functions.

All functions are stateless and deterministic:
- Input: list/array of prices (Decimal or float)
- Output: list/array of float indicator values (None for warmup bars)

No side effects. No I/O. Fully testable in isolation.

NOTE: Uses float arithmetic for performance (not Decimal).
Indicator values are used for signal comparison only, not financial math.
"""
from __future__ import annotations
from decimal import Decimal


def _to_float(v) -> float:
    return float(v) if not isinstance(v, float) else v


def sma(prices, period: int) -> list[float | None]:
    """Simple Moving Average."""
    fp = [_to_float(p) for p in prices]
    result: list[float | None] = [None] * (period - 1)
    window_sum = sum(fp[:period - 1])
    for i in range(period - 1, len(fp)):
        window_sum += fp[i]
        result.append(window_sum / period)
        window_sum -= fp[i - period + 1]
    return result


def ema(prices, period: int) -> list[float | None]:
    """Exponential Moving Average."""
    fp = [_to_float(p) for p in prices]
    if len(fp) < period:
        return [None] * len(fp)

    k = 2.0 / (period + 1)
    result: list[float | None] = [None] * (period - 1)
    seed = sum(fp[:period]) / period
    result.append(seed)
    prev = seed

    for price in fp[period:]:
        val = price * k + prev * (1.0 - k)
        result.append(val)
        prev = val

    return result


def rsi(prices, period: int = 14) -> list[float | None]:
    """Relative Strength Index (Wilder smoothing)."""
    fp = [_to_float(p) for p in prices]
    if len(fp) <= period:
        return [None] * len(fp)

    result: list[float | None] = [None] * period

    deltas = [fp[i] - fp[i - 1] for i in range(1, len(fp))]
    gains = [max(d, 0.0) for d in deltas]
    losses = [abs(min(d, 0.0)) for d in deltas]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    if avg_loss == 0:
        result.append(100.0)
    else:
        rs = avg_gain / avg_loss
        result.append(100.0 - 100.0 / (1.0 + rs))

    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            result.append(100.0)
        else:
            rs = avg_gain / avg_loss
            result.append(100.0 - 100.0 / (1.0 + rs))

    return result


def atr(highs, lows, closes, period: int = 14) -> list[float | None]:
    """Average True Range."""
    fh = [_to_float(p) for p in highs]
    fl = [_to_float(p) for p in lows]
    fc = [_to_float(p) for p in closes]

    if len(fc) < 2:
        return [None] * len(fc)

    true_ranges: list[float] = []
    for i in range(1, len(fc)):
        hl = fh[i] - fl[i]
        hc = abs(fh[i] - fc[i - 1])
        lc = abs(fl[i] - fc[i - 1])
        true_ranges.append(max(hl, hc, lc))

    result: list[float | None] = [None] * period
    avg = sum(true_ranges[:period]) / period
    result.append(avg)

    for tr in true_ranges[period:]:
        avg = (avg * (period - 1) + tr) / period
        result.append(avg)

    return result


def bollinger_bands(
    prices,
    period: int = 20,
    num_std: float = 2.0,
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    """Bollinger Bands: (upper, middle, lower)."""
    middle = sma(prices, period)
    fp = [_to_float(p) for p in prices]
    upper: list[float | None] = []
    lower: list[float | None] = []

    for i, mid in enumerate(middle):
        if mid is None or i < period - 1:
            upper.append(None)
            lower.append(None)
        else:
            window = fp[i - period + 1: i + 1]
            std = (sum((p - mid) ** 2 for p in window) / period) ** 0.5
            upper.append(mid + num_std * std)
            lower.append(mid - num_std * std)

    return upper, middle, lower


def adx(highs, lows, closes, period: int = 14) -> list[float | None]:
    """
    Average Directional Index (Wilder smoothing).

    Returns ADX values — measures trend strength (not direction).
    ADX >= 25: trending market. ADX < 25: ranging/sideways.
    Requires 2*period bars for first valid value.
    """
    fh = [_to_float(p) for p in highs]
    fl = [_to_float(p) for p in lows]
    fc = [_to_float(p) for p in closes]
    n = len(fc)

    if n < period * 2 + 1:
        return [None] * n

    # Step 1: raw TR, +DM, -DM for each bar
    trs, pdms, mdms = [], [], []
    for i in range(1, n):
        tr = max(fh[i] - fl[i], abs(fh[i] - fc[i - 1]), abs(fl[i] - fc[i - 1]))
        up = fh[i] - fh[i - 1]
        dn = fl[i - 1] - fl[i]
        pdm = up if up > dn and up > 0 else 0.0
        mdm = dn if dn > up and dn > 0 else 0.0
        trs.append(tr)
        pdms.append(pdm)
        mdms.append(mdm)

    # Step 2: Wilder smooth seed (sum of first period values)
    s_tr = sum(trs[:period])
    s_pdm = sum(pdms[:period])
    s_mdm = sum(mdms[:period])

    def _di(dm, tr_val):
        return 100.0 * dm / tr_val if tr_val > 0 else 0.0

    dx_values: list[float] = []
    for i in range(period, len(trs)):
        s_tr = s_tr - s_tr / period + trs[i]
        s_pdm = s_pdm - s_pdm / period + pdms[i]
        s_mdm = s_mdm - s_mdm / period + mdms[i]
        pdi = _di(s_pdm, s_tr)
        mdi = _di(s_mdm, s_tr)
        denom = pdi + mdi
        dx = 100.0 * abs(pdi - mdi) / denom if denom > 0 else 0.0
        dx_values.append(dx)

    # Step 3: Wilder smooth DX into ADX
    result: list[float | None] = [None] * (n - len(dx_values))
    if len(dx_values) < period:
        return [None] * n

    adx_val = sum(dx_values[:period]) / period
    result.append(adx_val)
    for dx in dx_values[period:]:
        adx_val = (adx_val * (period - 1) + dx) / period
        result.append(adx_val)

    return result


def crossover(series_a: list, series_b: list) -> list[bool]:
    """Returns True at index where series_a crosses above series_b."""
    result = [False] * len(series_a)
    for i in range(1, len(series_a)):
        a_curr, a_prev = series_a[i], series_a[i - 1]
        b_curr, b_prev = series_b[i], series_b[i - 1]
        if None in (a_curr, a_prev, b_curr, b_prev):
            continue
        if a_prev <= b_prev and a_curr > b_curr:
            result[i] = True
    return result


def crossunder(series_a: list, series_b: list) -> list[bool]:
    """Returns True at index where series_a crosses below series_b."""
    result = [False] * len(series_a)
    for i in range(1, len(series_a)):
        a_curr, a_prev = series_a[i], series_a[i - 1]
        b_curr, b_prev = series_b[i], series_b[i - 1]
        if None in (a_curr, a_prev, b_curr, b_prev):
            continue
        if a_prev >= b_prev and a_curr < b_curr:
            result[i] = True
    return result
