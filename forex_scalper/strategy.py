"""
HYDRA SCALPER — Core Strategy Engine
═════════════════════════════════════

Multi-layer 1-minute aggressive scalper combining:
  Layer 1: 15-min regime filter (EMA stack + ADX > 22)
  Layer 2: Volatility gate (ATR > 75th percentile rolling 200 bars)
  Layer 3: Squeeze breakout + MACD expansion + EMA pullback confirmation
  Layer 4: ATR-scaled exits — TP 2x ATR, SL 0.8x ATR, trailing, breakeven
  Layer 5: Session/spread/news filters

Designed for vectorized backtesting at maximum speed.

STRATEGY PSEUDOCODE
═══════════════════

FOR EACH 1-MINUTE BAR:

  [LAYER 1] Check 15-min regime:
    IF EMA(9) > EMA(21) > EMA(50) AND ADX(14) > 22 → regime = BULLISH
    IF EMA(9) < EMA(21) < EMA(50) AND ADX(14) > 22 → regime = BEARISH
    ELSE → regime = FLAT → SKIP

  [LAYER 2] Check volatility:
    IF ATR(14) on 1-min < 75th percentile of last 200 bars → SKIP
    (market too quiet for scalping)

  [LAYER 3] Check entry signal:
    PRIMARY: Bollinger(20,2) / Keltner(20,1.5) squeeze just fired
             (BB was inside KC for 6+ bars, now broke out)
             AND momentum in direction of regime
    CONFIRMATION (need at least 1):
      a) MACD(8,17,9) histogram expanding in trade direction
      b) Price is in pullback zone (within 0.3x ATR of 8/21 EMA midline)
      c) Strong candle: close in top/bottom 80% of range in direction
      d) Volume spike: current bar volume > 1.5x average

    LONG ENTRY = regime == BULLISH AND squeeze_fire AND momentum > 0
                 AND (macd_expanding OR in_pullback OR strong_candle)
                 AND RSI < 85 (not overbought)

    SHORT ENTRY = regime == BEARISH AND squeeze_fire AND momentum < 0
                  AND (macd_expanding OR in_pullback OR strong_candle)
                  AND RSI > 15 (not oversold)

  [LAYER 4] Set exit levels:
    SL = min(0.8 × ATR, 12 pips), at least 3 pips
    TP = min(2.0 × ATR, 18 pips), at least 6 pips
    Breakeven trigger: +6 pips → move SL to entry
    Trailing trigger:  +8 pips → trail at 0.6 × ATR behind price

  [LAYER 5] Final filters:
    - Must be in session (07:00-17:00 UTC)
    - Spread must be below threshold
    - Must respect cooldown (3 bars between trades)
    - Max concurrent positions check
"""

import numpy as np
import pandas as pd
from typing import Optional

from .config import StrategyParams, DEFAULT_PARAMS, PIP_SIZE
from .indicators import compute_all_indicators


class HydraScalper:
    """
    The Hydra Scalper strategy engine.

    Call generate_signals() with a 1-minute OHLCV DataFrame to get
    vectorized entry/exit signals for backtesting or live trading.
    """

    def __init__(self, params: Optional[StrategyParams] = None):
        self.params = params or DEFAULT_PARAMS

    def generate_signals(self, df: pd.DataFrame, pair: str = "EURUSD") -> pd.DataFrame:
        """
        Generate entry/exit signals for a single pair.

        Input DataFrame must have columns: [open, high, low, close, volume]
        and a DatetimeIndex in UTC.

        Returns DataFrame with added columns:
            signal: 1 (long), -1 (short), 0 (no signal)
            sl_price: stop loss price for the signal bar
            tp_price: take profit price for the signal bar
            sl_pips: stop loss in pips
            tp_pips: take profit in pips
        """
        p = self.params
        pip = PIP_SIZE.get(pair, 0.0001)

        # Compute all indicators
        df = compute_all_indicators(df.copy(), p)

        n = len(df)
        signals = np.zeros(n, dtype=np.int8)
        sl_prices = np.full(n, np.nan)
        tp_prices = np.full(n, np.nan)
        sl_pips_arr = np.full(n, np.nan)
        tp_pips_arr = np.full(n, np.nan)

        # Extract arrays for speed
        regime    = df["regime"].values
        vol_pass  = df["vol_pass"].values
        sq_fire   = df["squeeze_fire"].values
        sq_dur    = df["squeeze_duration"].values
        sq_mom    = df["squeeze_mom"].values
        macd_exp  = df["macd_expanding"].values
        macd_hist = df["macd_hist"].values
        rsi_val   = df["rsi"].values
        in_pb     = df["in_pullback_zone"].values
        vol_spk   = df["vol_spike"].values
        body_pos  = df["body_pos"].values
        in_sess   = df["in_session"].values
        atr_val   = df["atr"].values
        close     = df["close"].values

        # Vectorized signal generation
        # ── LONG CONDITIONS ──
        long_regime = regime == 1
        long_vol = vol_pass
        long_squeeze = sq_fire & (sq_dur >= p.squeeze_min_bars) & (sq_mom > 0)
        long_macd = macd_exp & (macd_hist > 0)
        long_rsi = rsi_val < p.rsi_ob
        long_candle = body_pos > p.candle_body_pct
        long_confirm = long_macd | in_pb | long_candle | vol_spk

        long_signal = (
            long_regime & long_vol & long_squeeze & long_confirm &
            long_rsi & in_sess
        )

        # ── SHORT CONDITIONS ──
        short_regime = regime == -1
        short_squeeze = sq_fire & (sq_dur >= p.squeeze_min_bars) & (sq_mom < 0)
        short_macd = macd_exp & (macd_hist < 0)
        short_rsi = rsi_val > p.rsi_os
        short_candle = body_pos < (1.0 - p.candle_body_pct)
        short_confirm = short_macd | in_pb | short_candle | vol_spk

        short_signal = (
            short_regime & long_vol & short_squeeze & short_confirm &
            short_rsi & in_sess
        )

        # Apply signals
        signals[long_signal] = 1
        signals[short_signal] = -1

        # ── COOLDOWN FILTER ──
        # Enforce minimum bars between trades
        last_signal_bar = -p.min_bars_between_trades - 1
        for i in range(n):
            if signals[i] != 0:
                if (i - last_signal_bar) < p.min_bars_between_trades:
                    signals[i] = 0  # Too soon, skip
                else:
                    last_signal_bar = i

        # ── COMPUTE SL/TP FOR EACH SIGNAL ──
        for i in range(n):
            if signals[i] == 0:
                continue

            current_atr = atr_val[i]
            entry = close[i]

            # SL in price terms
            sl_raw = current_atr * p.sl_atr_mult
            sl_in_pips = sl_raw / pip
            sl_in_pips = max(p.sl_min_pips, min(sl_in_pips, p.sl_max_pips))

            # TP in price terms
            tp_raw = current_atr * p.tp_atr_mult
            tp_in_pips = tp_raw / pip
            tp_in_pips = max(p.tp_min_pips, min(tp_in_pips, p.tp_max_pips))

            # Skip if TP/SL ratio is too tight
            if tp_in_pips < 1.3 * sl_in_pips:
                signals[i] = 0
                continue

            sl_pips_arr[i] = sl_in_pips
            tp_pips_arr[i] = tp_in_pips

            if signals[i] == 1:  # Long
                sl_prices[i] = entry - sl_in_pips * pip
                tp_prices[i] = entry + tp_in_pips * pip
            else:  # Short
                sl_prices[i] = entry + sl_in_pips * pip
                tp_prices[i] = entry - tp_in_pips * pip

        df["signal"] = signals
        df["sl_price"] = sl_prices
        df["tp_price"] = tp_prices
        df["sl_pips"] = sl_pips_arr
        df["tp_pips"] = tp_pips_arr

        return df

    def get_description(self) -> str:
        """Return human-readable strategy description."""
        p = self.params
        return f"""
HYDRA SCALPER v1.0 — Configuration Summary
════════════════════════════════════════════
Regime Filter:  EMA({p.regime_ema_fast}/{p.regime_ema_mid}/{p.regime_ema_slow}) + ADX({p.regime_adx_period}) > {p.regime_adx_threshold}
Volatility:     ATR({p.vol_atr_period}) > {p.vol_percentile}th pctile of {p.vol_lookback} bars
Entry:          BB({p.bb_period},{p.bb_std})/KC({p.kc_period},{p.kc_mult}) squeeze ({p.squeeze_min_bars}+ bars)
                + MACD({p.macd_fast},{p.macd_slow},{p.macd_signal}) expansion
                + EMA({p.entry_ema_fast}/{p.entry_ema_slow}) pullback zone
                + RSI({p.rsi_period}) filter [{p.rsi_os}-{p.rsi_ob}]
Exit:           TP = {p.tp_atr_mult}x ATR (max {p.tp_max_pips} pips)
                SL = {p.sl_atr_mult}x ATR (max {p.sl_max_pips} pips)
                Breakeven at +{p.breakeven_pips} pips
                Trail at +{p.trail_activation_pips} pips ({p.trail_distance_atr}x ATR)
Risk:           {p.risk_per_trade_pct}% per trade, max {p.max_daily_risk_pct}% daily
Session:        07:00-17:00 UTC only
Cooldown:       {p.min_bars_between_trades} bars between entries
"""
