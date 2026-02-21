"""
HYDRA SCALPER — Vectorized Indicator Library
All indicators computed as numpy arrays for maximum speed.
No loops where avoidable — pure vectorized operations.
"""

import numpy as np
import pandas as pd
from numba import njit


# ═══════════════════════════════════════════════════════════════════════════════
# CORE MOVING AVERAGES
# ═══════════════════════════════════════════════════════════════════════════════

def ema(series: np.ndarray, period: int) -> np.ndarray:
    """Exponential Moving Average — vectorized via pandas for correctness."""
    return pd.Series(series).ewm(span=period, adjust=False).mean().values


def sma(series: np.ndarray, period: int) -> np.ndarray:
    """Simple Moving Average — rolling mean."""
    s = pd.Series(series)
    return s.rolling(window=period, min_periods=period).mean().values


# ═══════════════════════════════════════════════════════════════════════════════
# VOLATILITY INDICATORS
# ═══════════════════════════════════════════════════════════════════════════════

def true_range(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> np.ndarray:
    """True Range — max(H-L, |H-Cp|, |L-Cp|)."""
    prev_close = np.roll(close, 1)
    prev_close[0] = close[0]
    tr1 = high - low
    tr2 = np.abs(high - prev_close)
    tr3 = np.abs(low - prev_close)
    return np.maximum(tr1, np.maximum(tr2, tr3))


def atr(high: np.ndarray, low: np.ndarray, close: np.ndarray,
        period: int = 14) -> np.ndarray:
    """Average True Range — EMA of True Range."""
    tr = true_range(high, low, close)
    return ema(tr, period)


def atr_percentile(atr_values: np.ndarray, lookback: int = 200,
                   percentile: float = 75.0) -> np.ndarray:
    """Rolling percentile of ATR — for volatility gating."""
    s = pd.Series(atr_values)
    return s.rolling(window=lookback, min_periods=50).quantile(
        percentile / 100.0
    ).values


# ═══════════════════════════════════════════════════════════════════════════════
# TREND INDICATORS
# ═══════════════════════════════════════════════════════════════════════════════

@njit
def _adx_core(high, low, close, period):
    """ADX calculation core — numba-accelerated for speed."""
    n = len(high)
    plus_dm = np.zeros(n)
    minus_dm = np.zeros(n)
    tr = np.zeros(n)

    for i in range(1, n):
        up_move = high[i] - high[i - 1]
        down_move = low[i - 1] - low[i]
        plus_dm[i] = up_move if (up_move > down_move and up_move > 0) else 0.0
        minus_dm[i] = down_move if (down_move > up_move and down_move > 0) else 0.0
        tr1 = high[i] - low[i]
        tr2 = abs(high[i] - close[i - 1])
        tr3 = abs(low[i] - close[i - 1])
        tr[i] = max(tr1, max(tr2, tr3))

    # Wilder smoothing
    atr_arr = np.zeros(n)
    plus_di_arr = np.zeros(n)
    minus_di_arr = np.zeros(n)
    dx_arr = np.zeros(n)
    adx_arr = np.zeros(n)

    sm_plus_dm = np.zeros(n)
    sm_minus_dm = np.zeros(n)
    sm_tr = np.zeros(n)

    # Initial sums
    if n > period:
        for i in range(1, period + 1):
            sm_plus_dm[period] += plus_dm[i]
            sm_minus_dm[period] += minus_dm[i]
            sm_tr[period] += tr[i]

        for i in range(period + 1, n):
            sm_plus_dm[i] = sm_plus_dm[i - 1] - sm_plus_dm[i - 1] / period + plus_dm[i]
            sm_minus_dm[i] = sm_minus_dm[i - 1] - sm_minus_dm[i - 1] / period + minus_dm[i]
            sm_tr[i] = sm_tr[i - 1] - sm_tr[i - 1] / period + tr[i]

        for i in range(period, n):
            if sm_tr[i] > 0:
                plus_di_arr[i] = 100.0 * sm_plus_dm[i] / sm_tr[i]
                minus_di_arr[i] = 100.0 * sm_minus_dm[i] / sm_tr[i]
            di_sum = plus_di_arr[i] + minus_di_arr[i]
            if di_sum > 0:
                dx_arr[i] = 100.0 * abs(plus_di_arr[i] - minus_di_arr[i]) / di_sum

        # ADX = smoothed DX
        if n > 2 * period:
            adx_arr[2 * period - 1] = 0.0
            for i in range(period, 2 * period):
                adx_arr[2 * period - 1] += dx_arr[i]
            adx_arr[2 * period - 1] /= period
            for i in range(2 * period, n):
                adx_arr[i] = (adx_arr[i - 1] * (period - 1) + dx_arr[i]) / period

    return adx_arr, plus_di_arr, minus_di_arr


def adx(high: np.ndarray, low: np.ndarray, close: np.ndarray,
        period: int = 14) -> tuple:
    """ADX + +DI / -DI. Returns (adx, plus_di, minus_di)."""
    return _adx_core(
        high.astype(np.float64),
        low.astype(np.float64),
        close.astype(np.float64),
        period,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# MOMENTUM INDICATORS
# ═══════════════════════════════════════════════════════════════════════════════

def rsi(close: np.ndarray, period: int = 14) -> np.ndarray:
    """RSI — Wilder's smoothed relative strength index."""
    delta = np.diff(close, prepend=close[0])
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    avg_gain = ema(gain, period)
    avg_loss = ema(loss, period)
    with np.errstate(divide='ignore', invalid='ignore'):
        rs = np.where(avg_loss > 1e-15, avg_gain / avg_loss, 100.0)
        rs = np.nan_to_num(rs, nan=100.0, posinf=100.0)
    return 100.0 - (100.0 / (1.0 + rs))


def macd(close: np.ndarray, fast: int = 12, slow: int = 26,
         signal: int = 9) -> tuple:
    """MACD line, signal line, histogram."""
    ema_fast = ema(close, fast)
    ema_slow = ema(close, slow)
    macd_line = ema_fast - ema_slow
    signal_line = ema(macd_line, signal)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


# ═══════════════════════════════════════════════════════════════════════════════
# BAND INDICATORS
# ═══════════════════════════════════════════════════════════════════════════════

def bollinger_bands(close: np.ndarray, period: int = 20,
                    std_mult: float = 2.0) -> tuple:
    """Bollinger Bands. Returns (upper, middle, lower)."""
    middle = sma(close, period)
    std = pd.Series(close).rolling(window=period, min_periods=period).std().values
    upper = middle + std_mult * std
    lower = middle - std_mult * std
    return upper, middle, lower


def keltner_channel(high: np.ndarray, low: np.ndarray, close: np.ndarray,
                    period: int = 20, atr_mult: float = 1.5) -> tuple:
    """Keltner Channel. Returns (upper, middle, lower)."""
    middle = ema(close, period)
    atr_val = atr(high, low, close, period)
    upper = middle + atr_mult * atr_val
    lower = middle - atr_mult * atr_val
    return upper, middle, lower


def squeeze_detector(close: np.ndarray, high: np.ndarray, low: np.ndarray,
                     bb_period: int = 20, bb_std: float = 2.0,
                     kc_period: int = 20, kc_mult: float = 1.5) -> tuple:
    """
    Detect Bollinger/Keltner squeeze and breakout.
    Returns:
        squeeze_on: bool array — True when BB is inside KC (compression)
        squeeze_fire: bool array — True on first bar after squeeze releases
        momentum: float array — momentum direction/strength
    """
    bb_upper, bb_mid, bb_lower = bollinger_bands(close, bb_period, bb_std)
    kc_upper, kc_mid, kc_lower = keltner_channel(high, low, close, kc_period, kc_mult)

    # Squeeze is ON when BB is inside KC
    squeeze_on = (bb_lower > kc_lower) & (bb_upper < kc_upper)

    # Squeeze fires on the bar AFTER squeeze releases
    squeeze_prev = np.roll(squeeze_on, 1)
    squeeze_prev[0] = False
    squeeze_fire = squeeze_prev & ~squeeze_on  # Was on, now off

    # Momentum = distance of close from BB midline, normalized by ATR
    atr_val = atr(high, low, close, bb_period)
    safe_atr = np.where(atr_val > 0, atr_val, 1e-10)
    momentum = (close - bb_mid) / safe_atr

    return squeeze_on, squeeze_fire, momentum


# ═══════════════════════════════════════════════════════════════════════════════
# CANDLE ANALYSIS
# ═══════════════════════════════════════════════════════════════════════════════

def candle_body_position(open_: np.ndarray, high: np.ndarray,
                         low: np.ndarray, close: np.ndarray) -> np.ndarray:
    """
    Where the close sits within the candle range (0=low, 1=high).
    Bullish candle close near high -> ~1.0
    Bearish candle close near low -> ~0.0
    """
    candle_range = high - low
    safe_range = np.where(candle_range > 0, candle_range, 1e-10)
    return (close - low) / safe_range


def volume_spike(volume: np.ndarray, lookback: int = 20,
                 multiplier: float = 1.5) -> np.ndarray:
    """True when current volume > multiplier * average volume."""
    avg_vol = sma(volume, lookback)
    safe_avg = np.where(avg_vol > 0, avg_vol, 1.0)
    return volume > (multiplier * safe_avg)


# ═══════════════════════════════════════════════════════════════════════════════
# HIGHER-TIMEFRAME RESAMPLING
# ═══════════════════════════════════════════════════════════════════════════════

def resample_to_htf(df: pd.DataFrame, tf_minutes: int = 15) -> pd.DataFrame:
    """
    Resample 1-minute OHLCV data to higher timeframe.
    Returns a DataFrame with the same index as input (forward-filled).
    """
    rule = f"{tf_minutes}min"
    htf = df.resample(rule).agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }).dropna()

    # Reindex back to 1-min and forward fill
    htf_aligned = htf.reindex(df.index, method="ffill")
    return htf_aligned


# ═══════════════════════════════════════════════════════════════════════════════
# MASTER INDICATOR BUILDER
# ═══════════════════════════════════════════════════════════════════════════════

def compute_all_indicators(df: pd.DataFrame, params) -> pd.DataFrame:
    """
    Compute ALL indicators for the strategy in one pass.
    Input: DataFrame with columns [open, high, low, close, volume]
           and a DatetimeIndex.
    Returns: Same DataFrame with indicator columns added.
    """
    o = df["open"].values.astype(np.float64)
    h = df["high"].values.astype(np.float64)
    l = df["low"].values.astype(np.float64)
    c = df["close"].values.astype(np.float64)
    v = df["volume"].values.astype(np.float64)

    # ── Layer 1: Regime (15-min HTF) ─────────────────────────────────
    htf = resample_to_htf(df, tf_minutes=15)
    htf_c = htf["close"].values.astype(np.float64)
    htf_h = htf["high"].values.astype(np.float64)
    htf_l = htf["low"].values.astype(np.float64)

    df["htf_ema_fast"] = ema(htf_c, params.regime_ema_fast)
    df["htf_ema_mid"]  = ema(htf_c, params.regime_ema_mid)
    df["htf_ema_slow"] = ema(htf_c, params.regime_ema_slow)

    adx_val, plus_di, minus_di = adx(htf_h, htf_l, htf_c, params.regime_adx_period)
    df["htf_adx"]      = adx_val
    df["htf_plus_di"]  = plus_di
    df["htf_minus_di"] = minus_di

    # Regime direction: 1=bullish, -1=bearish, 0=flat
    bull_stack = (
        (df["htf_ema_fast"] > df["htf_ema_mid"]) &
        (df["htf_ema_mid"] > df["htf_ema_slow"])
    )
    bear_stack = (
        (df["htf_ema_fast"] < df["htf_ema_mid"]) &
        (df["htf_ema_mid"] < df["htf_ema_slow"])
    )
    df["regime"] = np.where(
        bull_stack & (df["htf_adx"] > params.regime_adx_threshold), 1,
        np.where(
            bear_stack & (df["htf_adx"] > params.regime_adx_threshold), -1, 0
        )
    )

    # ── Layer 2: Volatility Gate (1-min) ─────────────────────────────
    df["atr"] = atr(h, l, c, params.vol_atr_period)
    df["atr_pctile"] = atr_percentile(
        df["atr"].values, params.vol_lookback, params.vol_percentile
    )
    df["vol_pass"] = df["atr"] > df["atr_pctile"]

    # ── Layer 3: Entry Signals ───────────────────────────────────────
    # Squeeze
    sq_on, sq_fire, sq_mom = squeeze_detector(
        c, h, l, params.bb_period, params.bb_std, params.kc_period, params.kc_mult
    )
    df["squeeze_on"]   = sq_on
    df["squeeze_fire"]  = sq_fire
    df["squeeze_mom"]   = sq_mom

    # Count consecutive squeeze bars (for min-bars filter)
    sq_count = np.zeros(len(df))
    for i in range(1, len(df)):
        if sq_on[i]:
            sq_count[i] = sq_count[i - 1] + 1
        else:
            sq_count[i] = 0
    # On fire bar, use the count from the previous bar (last squeeze bar)
    prev_count = np.roll(sq_count, 1)
    prev_count[0] = 0
    df["squeeze_duration"] = prev_count

    # MACD
    macd_line, macd_sig, macd_hist = macd(c, params.macd_fast, params.macd_slow, params.macd_signal)
    df["macd_line"] = macd_line
    df["macd_signal"] = macd_sig
    df["macd_hist"] = macd_hist

    # MACD histogram expansion: current hist > previous hist in same direction
    prev_hist = np.roll(macd_hist, 1)
    prev_hist[0] = 0
    df["macd_expanding"] = (
        ((macd_hist > 0) & (macd_hist > prev_hist)) |
        ((macd_hist < 0) & (macd_hist < prev_hist))
    )

    # RSI
    df["rsi"] = rsi(c, params.rsi_period)

    # EMA pullback zone (1-min)
    df["ema_fast"] = ema(c, params.entry_ema_fast)
    df["ema_slow_entry"] = ema(c, params.entry_ema_slow)
    ema_zone_mid = (df["ema_fast"].values + df["ema_slow_entry"].values) / 2.0
    dist_to_zone = np.abs(c - ema_zone_mid)
    pullback_threshold = params.pullback_tolerance_atr * df["atr"].values
    df["in_pullback_zone"] = dist_to_zone < pullback_threshold

    # Volume spike
    df["vol_spike"] = volume_spike(v, params.vol_spike_lookback, params.vol_spike_mult)

    # Candle body position
    df["body_pos"] = candle_body_position(o, h, l, c)

    # ── Layer 4: Exit helpers ────────────────────────────────────────
    # (ATR already computed; SL/TP calculated in strategy.py per-trade)

    # ── Layer 5: Session filter ──────────────────────────────────────
    if hasattr(df.index, 'hour'):
        hours = df.index.hour
    else:
        hours = pd.to_datetime(df.index).hour
    df["in_session"] = (hours >= 7) & (hours < 17)

    return df
