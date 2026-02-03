#!/usr/bin/env python3
"""
ATR Regime Classifier
=====================
Converts raw ATR into percentile buckets for regime-based decisions.
"""

import numpy as np
from typing import List


def atr_percentile(current_atr: float, atr_series: List[float]) -> float:
    """
    Calculate what percentile current ATR is relative to history.
    Returns 0.0-1.0
    """
    arr = np.array([x for x in atr_series if x and x > 0], dtype=float)
    if arr.size < 50:
        return 0.5  # Not enough data, assume middle
    return float((arr < current_atr).mean())


def atr_bucket(percentile: float) -> str:
    """
    Convert percentile to human-readable bucket.
    Tuned for gold's volatility patterns.
    """
    if percentile < 0.20:
        return "low"       # Quiet market, safe to trade
    if percentile < 0.50:
        return "mid"       # Normal conditions
    if percentile < 0.80:
        return "high"      # Elevated volatility, reduce risk
    return "extreme"       # Danger zone, consider halting


def volatility_regime(current_atr: float, atr_hist: List[float]) -> dict:
    """
    Full regime classification.
    Returns dict with percentile and bucket for prompt injection.
    """
    pctl = atr_percentile(current_atr, atr_hist)
    bucket = atr_bucket(pctl)
    
    return {
        "atr_pctl": round(pctl, 4),
        "atr_bucket": bucket,
        "atr_current": round(current_atr, 4) if current_atr else 0.0
    }


def regime_risk_adjustment(bucket: str) -> dict:
    """
    Deterministic risk adjustments based on ATR bucket.
    These can override AI suggestions.
    """
    if bucket == "extreme":
        return {
            "disable_new_entries": True,
            "dca_mult_min": 2.0,
            "max_layers": 1
        }
    elif bucket == "high":
        return {
            "disable_new_entries": False,
            "dca_mult_min": 1.5,
            "max_layers": 2
        }
    elif bucket == "mid":
        return {
            "disable_new_entries": False,
            "dca_mult_min": 1.2,
            "max_layers": 3
        }
    else:  # low
        return {
            "disable_new_entries": False,
            "dca_mult_min": 1.0,
            "max_layers": 3
        }


if __name__ == "__main__":
    # Demo
    import random
    history = [random.uniform(2.0, 8.0) for _ in range(500)]
    
    for test_atr in [2.5, 4.0, 6.5, 9.0]:
        result = volatility_regime(test_atr, history)
        print(f"ATR {test_atr:.1f} -> {result}")
