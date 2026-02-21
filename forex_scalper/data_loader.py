"""
HYDRA SCALPER — Data Loading Module
════════════════════════════════════

Supports:
  1. CSV files (Dukascopy/TrueFX format)
  2. OANDA v20 API historical data
  3. Synthetic data generation (for testing)
  4. Session filtering and data validation
"""

import numpy as np
import pandas as pd
from pathlib import Path
from typing import Dict, Optional, List
import json
import os

from .config import PAIRS, SESSION_START_UTC, SESSION_END_UTC


# ═══════════════════════════════════════════════════════════════════════════════
# CSV LOADING
# ═══════════════════════════════════════════════════════════════════════════════

def load_csv(filepath: str, pair: str = "EURUSD",
             datetime_col: str = "datetime",
             date_format: Optional[str] = None) -> pd.DataFrame:
    """
    Load 1-minute OHLCV data from CSV file.

    Expected columns: datetime, open, high, low, close, volume
    (column names are case-insensitive)

    Supports Dukascopy, TrueFX, and generic CSV formats.
    """
    df = pd.read_csv(filepath)

    # Normalize column names
    df.columns = [c.strip().lower() for c in df.columns]

    # Handle various datetime column names
    dt_candidates = ["datetime", "date", "time", "timestamp", "date_time"]
    dt_col = None
    for c in dt_candidates:
        if c in df.columns:
            dt_col = c
            break

    if dt_col is None:
        # Try first column
        dt_col = df.columns[0]

    if date_format:
        df.index = pd.to_datetime(df[dt_col], format=date_format)
    else:
        df.index = pd.to_datetime(df[dt_col])

    df.index.name = "datetime"

    # Ensure required columns
    required = ["open", "high", "low", "close"]
    for col in required:
        if col not in df.columns:
            raise ValueError(f"Missing required column: {col}")

    if "volume" not in df.columns:
        # Dukascopy sometimes has "tick_volume" or no volume
        if "tick_volume" in df.columns:
            df["volume"] = df["tick_volume"]
        elif "tickvol" in df.columns:
            df["volume"] = df["tickvol"]
        else:
            df["volume"] = 1.0  # Dummy volume

    df = df[["open", "high", "low", "close", "volume"]].copy()
    df = df.sort_index()
    df = df[~df.index.duplicated(keep="first")]

    return df


def load_csv_directory(directory: str,
                       pairs: Optional[List[str]] = None) -> Dict[str, pd.DataFrame]:
    """
    Load all pair CSVs from a directory.

    Expects files named like: EURUSD_M1.csv, GBPUSD_1min.csv, etc.
    Or subdirectories per pair.
    """
    dirpath = Path(directory)
    pairs = pairs or PAIRS
    data = {}

    for pair in pairs:
        # Try various filename patterns
        candidates = [
            dirpath / f"{pair}_M1.csv",
            dirpath / f"{pair}_1min.csv",
            dirpath / f"{pair}.csv",
            dirpath / f"{pair.lower()}_m1.csv",
            dirpath / f"{pair.lower()}.csv",
            dirpath / pair / "M1.csv",
            dirpath / pair / "1min.csv",
        ]

        found = False
        for candidate in candidates:
            if candidate.exists():
                print(f"  Loading {pair} from {candidate}...")
                data[pair] = load_csv(str(candidate), pair)
                found = True
                break

        if not found:
            print(f"  WARNING: No data file found for {pair}")

    return data


# ═══════════════════════════════════════════════════════════════════════════════
# OANDA API LOADING
# ═══════════════════════════════════════════════════════════════════════════════

def load_from_oanda(pair: str, count: int = 5000,
                    granularity: str = "M1",
                    account_id: Optional[str] = None,
                    token: Optional[str] = None) -> pd.DataFrame:
    """
    Load historical candles from OANDA v20 API.

    Requires OANDA_TOKEN and OANDA_ACCOUNT_ID env vars (or pass directly).
    Free practice accounts provide full historical data.
    """
    try:
        import oandapyV20
        from oandapyV20.endpoints.instruments import InstrumentsCandles
    except ImportError:
        raise ImportError("Install oandapyV20: pip install oandapyV20")

    token = token or os.environ.get("OANDA_TOKEN", "")
    account_id = account_id or os.environ.get("OANDA_ACCOUNT_ID", "")

    if not token:
        raise ValueError("OANDA_TOKEN not set. Get a free practice account at oanda.com")

    client = oandapyV20.API(access_token=token, environment="practice")

    # OANDA uses underscore format: EUR_USD
    oanda_pair = pair[:3] + "_" + pair[3:]

    params = {
        "count": min(count, 5000),  # OANDA max per request
        "granularity": granularity,
        "price": "M",  # Midpoint
    }

    r = InstrumentsCandles(instrument=oanda_pair, params=params)
    response = client.request(r)

    candles = response["candles"]
    rows = []
    for c in candles:
        if c["complete"]:
            mid = c["mid"]
            rows.append({
                "datetime": pd.Timestamp(c["time"]),
                "open": float(mid["o"]),
                "high": float(mid["h"]),
                "low": float(mid["l"]),
                "close": float(mid["c"]),
                "volume": int(c["volume"]),
            })

    df = pd.DataFrame(rows)
    df.set_index("datetime", inplace=True)
    df.index = df.index.tz_localize(None)  # Remove timezone for consistency
    return df


def load_multi_oanda(pairs: Optional[List[str]] = None,
                     count: int = 5000) -> Dict[str, pd.DataFrame]:
    """Load multiple pairs from OANDA."""
    pairs = pairs or PAIRS
    data = {}
    for pair in pairs:
        print(f"  Loading {pair} from OANDA ({count} candles)...")
        try:
            data[pair] = load_from_oanda(pair, count)
            print(f"    Got {len(data[pair])} bars")
        except Exception as e:
            print(f"    ERROR: {e}")
    return data


# ═══════════════════════════════════════════════════════════════════════════════
# SYNTHETIC DATA GENERATION (for testing)
# ═══════════════════════════════════════════════════════════════════════════════

def generate_synthetic_data(pair: str = "EURUSD",
                            days: int = 252,
                            bars_per_day: int = 600,
                            base_price: Optional[float] = None,
                            volatility: Optional[float] = None,
                            seed: int = 42) -> pd.DataFrame:
    """
    Generate realistic synthetic 1-minute forex data.

    Creates trending + ranging regimes with realistic volatility patterns.
    Useful for rapid strategy testing before loading real data.

    Args:
        pair: Pair name (determines base price and volatility)
        days: Number of trading days
        bars_per_day: Bars per day (600 = 10 hours London+NY)
        base_price: Starting price (auto-detected from pair if None)
        volatility: Daily vol in price units (auto-detected if None)
        seed: Random seed for reproducibility
    """
    np.random.seed(seed)

    # Default prices and vols
    defaults = {
        "EURUSD": (1.0850, 0.0060),
        "GBPUSD": (1.2650, 0.0075),
        "USDJPY": (149.50, 0.80),
        "AUDUSD": (0.6550, 0.0045),
        "USDCAD": (1.3550, 0.0055),
        "NZDUSD": (0.6150, 0.0040),
        "USDCHF": (0.8750, 0.0050),
    }

    bp, vol = defaults.get(pair, (1.0, 0.005))
    base_price = base_price or bp
    volatility = volatility or vol

    total_bars = days * bars_per_day
    minute_vol = volatility / np.sqrt(bars_per_day)

    # Generate price path with regime changes
    prices = np.zeros(total_bars)
    prices[0] = base_price

    # Regime: periods of trending and ranging
    regime_length = bars_per_day * 5  # ~5 day regime blocks
    n_regimes = total_bars // regime_length + 1
    regimes = np.random.choice(["trend_up", "trend_down", "range"], n_regimes, p=[0.3, 0.3, 0.4])

    for i in range(1, total_bars):
        regime_idx = min(i // regime_length, len(regimes) - 1)
        current_regime = regimes[regime_idx]

        noise = np.random.normal(0, minute_vol)

        if current_regime == "trend_up":
            drift = minute_vol * 0.08
        elif current_regime == "trend_down":
            drift = -minute_vol * 0.08
        else:
            drift = 0

        # Add mean reversion in ranging regime
        if current_regime == "range":
            mean_price = prices[max(0, i - bars_per_day):i].mean()
            reversion = (mean_price - prices[i - 1]) * 0.002
            noise += reversion

        prices[i] = prices[i - 1] + drift + noise

    # Generate OHLCV from close prices
    opens = prices.copy()
    opens[1:] = prices[:-1]

    # Intra-bar volatility
    bar_vol = minute_vol * 0.8
    highs = np.maximum(opens, prices) + np.abs(np.random.normal(0, bar_vol, total_bars))
    lows = np.minimum(opens, prices) - np.abs(np.random.normal(0, bar_vol, total_bars))

    # Volume: higher during session overlap, lower at edges
    base_vol = 100
    hour_in_day = np.tile(np.arange(bars_per_day), days)[:total_bars]
    # Volume peaks at London/NY overlap (~bar 300-500 of the 600-bar day)
    vol_profile = 1.0 + 0.8 * np.exp(-((hour_in_day - 350) ** 2) / (2 * 80 ** 2))
    volume = (base_vol * vol_profile * np.random.exponential(1, total_bars)).astype(int)
    volume = np.maximum(volume, 1)

    # Create datetime index (weekdays only, 07:00-17:00 UTC)
    start_date = pd.Timestamp("2021-01-04 07:00:00")  # A Monday
    dates = []
    current_date = start_date
    bars_generated = 0

    while bars_generated < total_bars:
        if current_date.weekday() < 5:  # Mon-Fri
            for minute in range(bars_per_day):
                if bars_generated >= total_bars:
                    break
                ts = current_date + pd.Timedelta(minutes=minute)
                dates.append(ts)
                bars_generated += 1
        current_date += pd.Timedelta(days=1)

    index = pd.DatetimeIndex(dates[:total_bars])

    df = pd.DataFrame({
        "open": opens[:total_bars],
        "high": highs[:total_bars],
        "low": lows[:total_bars],
        "close": prices[:total_bars],
        "volume": volume[:total_bars],
    }, index=index)

    df.index.name = "datetime"
    return df


def generate_multi_synthetic(pairs: Optional[List[str]] = None,
                             days: int = 252 * 5,
                             bars_per_day: int = 600) -> Dict[str, pd.DataFrame]:
    """
    Generate synthetic data for multiple pairs.

    Default: 5 years of data for all 7 pairs.
    """
    pairs = pairs or PAIRS
    data = {}
    for i, pair in enumerate(pairs):
        print(f"  Generating synthetic data for {pair}...")
        data[pair] = generate_synthetic_data(
            pair=pair, days=days, bars_per_day=bars_per_day, seed=42 + i
        )
        print(f"    {len(data[pair])} bars ({days} days)")
    return data


# ═══════════════════════════════════════════════════════════════════════════════
# DATA UTILITIES
# ═══════════════════════════════════════════════════════════════════════════════

def filter_session(df: pd.DataFrame,
                   start_hour: int = SESSION_START_UTC,
                   end_hour: int = SESSION_END_UTC) -> pd.DataFrame:
    """Filter DataFrame to only include session hours."""
    hours = df.index.hour
    mask = (hours >= start_hour) & (hours < end_hour)
    return df[mask].copy()


def validate_data(df: pd.DataFrame, pair: str = "UNKNOWN") -> dict:
    """
    Validate data quality.

    Returns dict with validation results.
    """
    issues = []
    n = len(df)

    # Check for required columns
    for col in ["open", "high", "low", "close", "volume"]:
        if col not in df.columns:
            issues.append(f"Missing column: {col}")

    if issues:
        return {"valid": False, "issues": issues, "bars": n}

    # Check for NaN
    nan_count = df[["open", "high", "low", "close"]].isna().sum().sum()
    if nan_count > 0:
        issues.append(f"{nan_count} NaN values in OHLC data")

    # Check H >= L
    bad_hl = (df["high"] < df["low"]).sum()
    if bad_hl > 0:
        issues.append(f"{bad_hl} bars with high < low")

    # Check H >= O,C and L <= O,C
    bad_bounds = (
        (df["high"] < df["open"]) | (df["high"] < df["close"]) |
        (df["low"] > df["open"]) | (df["low"] > df["close"])
    ).sum()
    if bad_bounds > 0:
        issues.append(f"{bad_bounds} bars with OHLC bounds violation")

    # Check for zero-range bars (suspicious)
    zero_range = (df["high"] == df["low"]).sum()
    if zero_range > n * 0.1:
        issues.append(f"{zero_range} zero-range bars ({zero_range/n*100:.1f}%)")

    # Check date range
    date_range = df.index[-1] - df.index[0]
    trading_days = date_range.days * 5 / 7  # Rough estimate

    return {
        "valid": len(issues) == 0,
        "pair": pair,
        "bars": n,
        "date_range": str(date_range),
        "start": str(df.index[0]),
        "end": str(df.index[-1]),
        "trading_days_est": int(trading_days),
        "nan_count": nan_count,
        "issues": issues,
    }
