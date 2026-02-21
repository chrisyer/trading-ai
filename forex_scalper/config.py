"""
HYDRA SCALPER — Configuration & Default Parameters
All tunable parameters in one place. Optuna overrides these.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Tuple

# ═══════════════════════════════════════════════════════════════════════════════
# PAIRS & SESSIONS
# ═══════════════════════════════════════════════════════════════════════════════

PAIRS = [
    "EURUSD", "GBPUSD", "USDJPY", "AUDUSD",
    "USDCAD", "NZDUSD", "USDCHF",
]

# Pip values (for 5-digit brokers)
PIP_SIZE = {
    "EURUSD": 0.0001, "GBPUSD": 0.0001, "AUDUSD": 0.0001,
    "NZDUSD": 0.0001, "USDCAD": 0.0001, "USDCHF": 0.0001,
    "USDJPY": 0.01,
}

# Typical spreads in pips (FOREX.com US)
TYPICAL_SPREAD = {
    "EURUSD": 1.2, "GBPUSD": 1.6, "USDJPY": 1.3, "AUDUSD": 1.4,
    "USDCAD": 1.8, "NZDUSD": 2.0, "USDCHF": 1.7,
}

# Max allowed spread in pips before we skip the trade
MAX_SPREAD = {
    "EURUSD": 0.8, "GBPUSD": 1.2, "USDJPY": 1.0, "AUDUSD": 1.2,
    "USDCAD": 1.5, "NZDUSD": 1.5, "USDCHF": 1.5,
}

# Session hours (UTC) — London open to NY close
SESSION_START_UTC = 7   # 07:00 UTC
SESSION_END_UTC   = 17  # 17:00 UTC

# Kill zone sub-sessions (higher edge)
LONDON_OPEN  = (7, 10)   # 07:00-10:00 UTC
LONDON_NY_OVERLAP = (12, 16)  # 12:00-16:00 UTC
NY_CLOSE = (16, 17)      # 16:00-17:00 UTC wind-down


# ═══════════════════════════════════════════════════════════════════════════════
# DEFAULT STRATEGY PARAMETERS (Optuna search space defined in optimizer.py)
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class StrategyParams:
    """All tunable strategy parameters."""

    # --- Layer 1: Regime Filter (15-min) ---
    regime_ema_fast: int = 9
    regime_ema_mid: int = 21
    regime_ema_slow: int = 50
    regime_adx_period: int = 14
    regime_adx_threshold: float = 22.0
    regime_adx_skip: float = 18.0  # Below this = dead market, skip

    # --- Layer 2: Volatility Gate (1-min) ---
    vol_atr_period: int = 14
    vol_percentile: float = 75.0    # ATR must be above this percentile
    vol_lookback: int = 200         # Rolling window for percentile calc

    # --- Layer 3: Core Entry ---
    # Squeeze detection
    bb_period: int = 20
    bb_std: float = 2.0
    kc_period: int = 20
    kc_mult: float = 1.5
    squeeze_min_bars: int = 6       # Min bars in squeeze before breakout counts

    # MACD confirmation
    macd_fast: int = 8
    macd_slow: int = 17
    macd_signal: int = 9

    # RSI filter
    rsi_period: int = 5
    rsi_ob: float = 85.0            # Overbought (skip longs above this)
    rsi_os: float = 15.0            # Oversold (skip shorts below this)

    # EMA pullback zone (1-min)
    entry_ema_fast: int = 8
    entry_ema_slow: int = 21
    pullback_tolerance_atr: float = 0.3  # Price within 0.3x ATR of EMA zone

    # Volume spike
    vol_spike_mult: float = 1.5     # Volume > 1.5x 20-bar avg
    vol_spike_lookback: int = 20

    # Candle strength
    candle_body_pct: float = 0.80   # Close must be in top/bottom 80% of range

    # --- Layer 4: Exit Rules ---
    tp_atr_mult: float = 2.0       # TP = 2.0x ATR
    sl_atr_mult: float = 0.8       # SL = 0.8x ATR
    tp_max_pips: float = 18.0      # Hard cap on TP
    sl_max_pips: float = 12.0      # Hard cap on SL
    tp_min_pips: float = 6.0       # Minimum TP (skip if too tight)
    sl_min_pips: float = 3.0       # Minimum SL
    breakeven_pips: float = 6.0    # Move SL to breakeven at +6 pips
    trail_activation_pips: float = 8.0  # Start trailing at +8 pips
    trail_distance_atr: float = 0.6     # Trail distance = 0.6x ATR

    # --- Layer 5: Filters ---
    news_blackout_minutes: int = 30     # No trades 30 min before/after news
    max_concurrent_per_pair: int = 3
    max_concurrent_total: int = 10
    max_daily_trades: int = 150         # Hard limit per day
    min_bars_between_trades: int = 3    # Cooldown: 3 bars between entries on same pair

    # --- Risk ---
    risk_per_trade_pct: float = 1.0     # 1% account risk per trade
    max_daily_risk_pct: float = 5.0     # Max 5% total daily risk
    kelly_fraction: float = 0.5         # Half-Kelly (conservative)
    use_dynamic_kelly: bool = True


# Global default instance
DEFAULT_PARAMS = StrategyParams()


# ═══════════════════════════════════════════════════════════════════════════════
# BACKTEST SETTINGS
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class BacktestConfig:
    """Backtesting configuration."""
    initial_capital: float = 100_000.0
    commission_per_lot: float = 3.5     # USD per round-turn lot
    slippage_pips: float = 0.3          # Average slippage in pips
    lot_size: float = 100_000           # Standard lot = 100K units
    min_lot: float = 0.01
    max_lot: float = 10.0
    leverage: int = 200                 # 1:200


DEFAULT_BACKTEST = BacktestConfig()
