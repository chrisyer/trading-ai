"""
HYDRA SCALPER — Ultimate Forex Scalping System
Built by QUINN001 for CRELLA001
2026-02-10

Multi-layer aggressive 1-minute scalper targeting 30-100+ trades/day
across 7 major pairs during London/NY sessions.

Architecture:
  Layer 1: Regime Filter (15-min EMA stack + ADX)
  Layer 2: Volatility Gate (ATR 75th percentile)
  Layer 3: Squeeze Breakout + EMA Pullback entry
  Layer 4: ATR-scaled exits with trailing + breakeven
  Layer 5: News/spread/session filters
"""

__version__ = "1.0.0"
__codename__ = "HYDRA"
