#!/usr/bin/env python3
"""
SHOOTING STAR BACKTEST - XAUUSD
================================
Research Task #001: Quantify shooting star pattern edge

Analyzes M15 and H1 timeframes to:
1. Identify all shooting star patterns
2. Measure subsequent price action  
3. Calculate win rate and average move size
4. Determine optimal entry/exit parameters

Author: Quinn
Date: 2026-02-03
"""

import json
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Tuple

# ══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ══════════════════════════════════════════════════════════════════════════════

OUTPUT_DIR = Path("/home/jbot/trading_ai/research/pattern_analysis")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Analysis parameters
LOOKBACK_DAYS = 30
BEARISH_REACTION_THRESHOLD = 10  # Pips ($10 for gold)
CANDLES_TO_ANALYZE = 10  # How many candles after pattern to track

# ══════════════════════════════════════════════════════════════════════════════
# DATA FETCHING
# ══════════════════════════════════════════════════════════════════════════════

def fetch_gold_data(interval: str = "15m", days: int = 30) -> pd.DataFrame:
    """
    Fetch XAUUSD data from Yahoo Finance.
    Uses GC=F (Gold Futures) as proxy.
    
    interval: "15m", "1h", "1d"
    """
    print(f"Fetching Gold data: {interval} for {days} days...")
    
    # Yahoo Finance limits: 
    # - 15m data only available for last 60 days
    # - 1h data available for last 730 days
    
    try:
        # Use GC=F (Gold Futures) 
        ticker = yf.Ticker("GC=F")
        
        if interval == "15m":
            # For 15m, we can only get ~60 days
            df = ticker.history(period=f"{min(days, 60)}d", interval="15m")
        elif interval == "1h":
            df = ticker.history(period=f"{days}d", interval="1h")
        else:
            df = ticker.history(period=f"{days}d", interval="1d")
        
        if df.empty:
            print(f"  Warning: No data returned for {interval}")
            return pd.DataFrame()
        
        # Rename columns to lowercase
        df.columns = [c.lower() for c in df.columns]
        df = df.reset_index()
        
        # Handle different index names
        if 'datetime' in df.columns:
            df = df.rename(columns={'datetime': 'timestamp'})
        elif 'date' in df.columns:
            df = df.rename(columns={'date': 'timestamp'})
        elif 'index' in df.columns:
            df = df.rename(columns={'index': 'timestamp'})
        
        # Ensure we have required columns
        required = ['open', 'high', 'low', 'close']
        for col in required:
            if col not in df.columns:
                print(f"  Warning: Missing column {col}")
                return pd.DataFrame()
        
        print(f"  Fetched {len(df)} candles")
        print(f"  Columns: {list(df.columns)}")
        return df
        
    except Exception as e:
        print(f"  Error fetching data: {e}")
        return pd.DataFrame()


def calculate_rsi(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Calculate RSI indicator"""
    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return rsi


def calculate_adx(df: pd.DataFrame, period: int = 14) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """Calculate ADX, +DI, -DI"""
    high = df['high']
    low = df['low']
    close = df['close']
    
    # True Range
    tr1 = high - low
    tr2 = abs(high - close.shift(1))
    tr3 = abs(low - close.shift(1))
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.rolling(window=period).mean()
    
    # Directional Movement
    up_move = high - high.shift(1)
    down_move = low.shift(1) - low
    
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0)
    
    plus_di = 100 * pd.Series(plus_dm).rolling(window=period).mean() / atr
    minus_di = 100 * pd.Series(minus_dm).rolling(window=period).mean() / atr
    
    dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di)
    adx = dx.rolling(window=period).mean()
    
    return adx, plus_di, minus_di


def calculate_ema(df: pd.DataFrame, period: int = 20) -> pd.Series:
    """Calculate EMA"""
    return df['close'].ewm(span=period, adjust=False).mean()


# ══════════════════════════════════════════════════════════════════════════════
# SHOOTING STAR DETECTION
# ══════════════════════════════════════════════════════════════════════════════

def is_shooting_star(row: pd.Series, prev_closes: List[float], 
                     body_ratio: float = 0.3, 
                     upper_wick_mult: float = 2.0,
                     lower_wick_ratio: float = 0.1) -> bool:
    """
    Detect shooting star candlestick pattern.
    
    Criteria:
    1. Small real body (open ≈ close) - body <= 30% of range
    2. Long upper shadow (wick) >= 2x body size
    3. Little or no lower shadow (< 10% of range)
    4. Appears after an uptrend (price above EMA20)
    """
    open_price = row['open']
    high = row['high']
    low = row['low']
    close = row['close']
    
    body = abs(close - open_price)
    upper_wick = high - max(open_price, close)
    lower_wick = min(open_price, close) - low
    total_range = high - low
    
    if total_range == 0 or body == 0:
        return False
    
    # Criteria checks
    small_body = body <= total_range * body_ratio
    long_upper = upper_wick >= body * upper_wick_mult
    short_lower = lower_wick <= total_range * lower_wick_ratio
    
    # Must be after uptrend (current price above recent average)
    if len(prev_closes) >= 5:
        recent_avg = sum(prev_closes[-5:]) / 5
        after_uptrend = close > recent_avg
    else:
        after_uptrend = True  # Default if not enough history
    
    return small_body and long_upper and short_lower and after_uptrend


def find_shooting_stars(df: pd.DataFrame) -> List[Dict]:
    """
    Find all shooting star patterns in the dataframe.
    Returns list of pattern data with context.
    """
    patterns = []
    
    # Add indicators
    df['rsi'] = calculate_rsi(df)
    df['adx'], df['plus_di'], df['minus_di'] = calculate_adx(df)
    df['ema20'] = calculate_ema(df, 20)
    
    for i in range(20, len(df) - CANDLES_TO_ANALYZE):
        row = df.iloc[i]
        prev_closes = df['close'].iloc[i-20:i].tolist()
        
        if is_shooting_star(row, prev_closes):
            # Collect subsequent price action
            subsequent = df.iloc[i+1:i+1+CANDLES_TO_ANALYZE]
            
            if len(subsequent) < CANDLES_TO_ANALYZE:
                continue
            
            pattern_high = row['high']
            pattern_close = row['close']
            
            # Track price movements
            prices_after = subsequent['close'].tolist()
            lows_after = subsequent['low'].tolist()
            highs_after = subsequent['high'].tolist()
            
            max_drop = pattern_close - min(lows_after)
            max_rise = max(highs_after) - pattern_close
            
            # Find when max drop occurred
            min_low_idx = lows_after.index(min(lows_after))
            
            # Bearish reaction = dropped more than threshold
            bearish_reaction = max_drop >= BEARISH_REACTION_THRESHOLD
            
            # Get timestamp - try different approaches
            if 'timestamp' in df.columns:
                ts = str(row['timestamp'])
            else:
                ts = str(df.index[i])
            
            pattern_data = {
                "timestamp": ts,
                "timeframe": "unknown",  # Will be set by caller
                "price_at_pattern": float(round(pattern_close, 2)),
                "pattern_high": float(round(pattern_high, 2)),
                "pattern_low": float(round(row['low'], 2)),
                "rsi_at_pattern": float(round(row['rsi'], 1)) if pd.notna(row['rsi']) else None,
                "adx_at_pattern": float(round(row['adx'], 1)) if pd.notna(row['adx']) else None,
                "above_ema20": bool(pattern_close > row['ema20']),
                
                # Subsequent price action
                "price_after_1_candle": float(round(prices_after[0], 2)) if len(prices_after) > 0 else None,
                "price_after_3_candles": float(round(prices_after[2], 2)) if len(prices_after) > 2 else None,
                "price_after_5_candles": float(round(prices_after[4], 2)) if len(prices_after) > 4 else None,
                "price_after_10_candles": float(round(prices_after[9], 2)) if len(prices_after) > 9 else None,
                
                # Calculated metrics
                "max_drop_within_10_candles": float(round(max_drop, 2)),
                "max_rise_within_10_candles": float(round(max_rise, 2)),
                "candles_to_low": int(min_low_idx + 1),
                "bearish_reaction": bool(bearish_reaction),
                "reaction_size": float(round(max_drop, 2)),
                
                # Next candle confirmation
                "next_candle_red": bool(prices_after[0] < pattern_close) if prices_after else False
            }
            
            patterns.append(pattern_data)
    
    return patterns


# ══════════════════════════════════════════════════════════════════════════════
# ANALYSIS
# ══════════════════════════════════════════════════════════════════════════════

def analyze_patterns(patterns: List[Dict], timeframe: str) -> Dict:
    """
    Analyze shooting star patterns and calculate statistics.
    """
    if not patterns:
        return {
            "timeframe": timeframe,
            "total_patterns": 0,
            "error": "No patterns found"
        }
    
    total = len(patterns)
    
    # Basic stats
    bearish_reactions = [p for p in patterns if p['bearish_reaction']]
    win_rate = len(bearish_reactions) / total * 100
    
    avg_drop = np.mean([p['max_drop_within_10_candles'] for p in patterns])
    avg_rise = np.mean([p['max_rise_within_10_candles'] for p in patterns])
    avg_candles_to_low = np.mean([p['candles_to_low'] for p in patterns])
    
    # RSI analysis
    high_rsi = [p for p in patterns if p['rsi_at_pattern'] and p['rsi_at_pattern'] > 70]
    low_rsi = [p for p in patterns if p['rsi_at_pattern'] and p['rsi_at_pattern'] <= 70]
    
    high_rsi_wins = [p for p in high_rsi if p['bearish_reaction']]
    low_rsi_wins = [p for p in low_rsi if p['bearish_reaction']]
    
    high_rsi_winrate = len(high_rsi_wins) / len(high_rsi) * 100 if high_rsi else 0
    low_rsi_winrate = len(low_rsi_wins) / len(low_rsi) * 100 if low_rsi else 0
    
    # Confirmation analysis (next candle red)
    confirmed = [p for p in patterns if p['next_candle_red']]
    confirmed_wins = [p for p in confirmed if p['bearish_reaction']]
    confirmed_winrate = len(confirmed_wins) / len(confirmed) * 100 if confirmed else 0
    
    unconfirmed = [p for p in patterns if not p['next_candle_red']]
    unconfirmed_wins = [p for p in unconfirmed if p['bearish_reaction']]
    unconfirmed_winrate = len(unconfirmed_wins) / len(unconfirmed) * 100 if unconfirmed else 0
    
    # Above EMA20 analysis
    above_ema = [p for p in patterns if p['above_ema20']]
    above_ema_wins = [p for p in above_ema if p['bearish_reaction']]
    above_ema_winrate = len(above_ema_wins) / len(above_ema) * 100 if above_ema else 0
    
    return {
        "timeframe": timeframe,
        "total_patterns": total,
        "bearish_reactions": len(bearish_reactions),
        "win_rate": round(win_rate, 1),
        
        "avg_drop_size": round(avg_drop, 2),
        "avg_rise_size": round(avg_rise, 2),
        "avg_candles_to_low": round(avg_candles_to_low, 1),
        
        "rsi_analysis": {
            "rsi_above_70": {
                "count": len(high_rsi),
                "wins": len(high_rsi_wins),
                "win_rate": round(high_rsi_winrate, 1)
            },
            "rsi_below_70": {
                "count": len(low_rsi),
                "wins": len(low_rsi_wins),
                "win_rate": round(low_rsi_winrate, 1)
            }
        },
        
        "confirmation_analysis": {
            "with_red_candle": {
                "count": len(confirmed),
                "wins": len(confirmed_wins),
                "win_rate": round(confirmed_winrate, 1)
            },
            "without_confirmation": {
                "count": len(unconfirmed),
                "wins": len(unconfirmed_wins),
                "win_rate": round(unconfirmed_winrate, 1)
            }
        },
        
        "ema_analysis": {
            "above_ema20": {
                "count": len(above_ema),
                "wins": len(above_ema_wins),
                "win_rate": round(above_ema_winrate, 1)
            }
        },
        
        # Distribution of drop sizes
        "drop_distribution": {
            "0-10": len([p for p in patterns if p['max_drop_within_10_candles'] < 10]),
            "10-20": len([p for p in patterns if 10 <= p['max_drop_within_10_candles'] < 20]),
            "20-30": len([p for p in patterns if 20 <= p['max_drop_within_10_candles'] < 30]),
            "30+": len([p for p in patterns if p['max_drop_within_10_candles'] >= 30])
        }
    }


def generate_report(m15_analysis: Dict, h1_analysis: Dict, 
                    m15_patterns: List[Dict], h1_patterns: List[Dict]) -> str:
    """Generate markdown report"""
    
    # Determine best setup
    best_timeframe = "M15" if m15_analysis.get('win_rate', 0) >= h1_analysis.get('win_rate', 0) else "H1"
    
    # Check RSI enhancement
    m15_rsi = m15_analysis.get('rsi_analysis', {})
    h1_rsi = h1_analysis.get('rsi_analysis', {})
    
    best_rsi_threshold = 70
    rsi_helps = False
    
    if m15_rsi:
        high_rsi_wr = m15_rsi.get('rsi_above_70', {}).get('win_rate', 0)
        low_rsi_wr = m15_rsi.get('rsi_below_70', {}).get('win_rate', 0)
        if high_rsi_wr > low_rsi_wr + 10:  # Significant improvement
            rsi_helps = True
    
    # Confirmation analysis
    m15_conf = m15_analysis.get('confirmation_analysis', {})
    conf_helps = False
    if m15_conf:
        with_conf = m15_conf.get('with_red_candle', {}).get('win_rate', 0)
        without_conf = m15_conf.get('without_confirmation', {}).get('win_rate', 0)
        if with_conf > without_conf + 10:
            conf_helps = True
    
    # Calculate suggested parameters
    avg_drop = m15_analysis.get('avg_drop_size', 20)
    suggested_tp = round(avg_drop * 0.7, 1)  # 70% of average drop
    suggested_sl = round(avg_drop * 0.5, 1)  # 50% of average drop as SL
    
    # Confidence boost recommendation
    best_winrate = max(m15_analysis.get('win_rate', 0), h1_analysis.get('win_rate', 0))
    if best_winrate >= 70:
        confidence_boost = 1.35
    elif best_winrate >= 65:
        confidence_boost = 1.25
    elif best_winrate >= 55:
        confidence_boost = 1.15
    else:
        confidence_boost = 1.0
    
    report = f"""# SHOOTING STAR BACKTEST RESULTS - XAUUSD
## Research Task #001 | Completed: {datetime.now().strftime('%Y-%m-%d %H:%M')} UTC

═══════════════════════════════════════════════════════════════════════════════

## EXECUTIVE SUMMARY

**Pattern Analyzed:** Shooting Star (Bearish Reversal)
**Data Period:** Last {LOOKBACK_DAYS} days
**Bearish Threshold:** ${BEARISH_REACTION_THRESHOLD} drop within 10 candles

---

## M15 TIMEFRAME RESULTS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

| Metric | Value |
|--------|-------|
| Total Shooting Stars Found | {m15_analysis.get('total_patterns', 0)} |
| Bearish Reactions (>${BEARISH_REACTION_THRESHOLD} drop) | {m15_analysis.get('bearish_reactions', 0)} |
| **Win Rate** | **{m15_analysis.get('win_rate', 0)}%** |
| Average Drop Size | ${m15_analysis.get('avg_drop_size', 0)} |
| Average Rise (against) | ${m15_analysis.get('avg_rise_size', 0)} |
| Average Candles to Low | {m15_analysis.get('avg_candles_to_low', 0)} |

### RSI Enhancement (M15)
| RSI Level | Count | Wins | Win Rate |
|-----------|-------|------|----------|
| RSI > 70 (Overbought) | {m15_rsi.get('rsi_above_70', {}).get('count', 0)} | {m15_rsi.get('rsi_above_70', {}).get('wins', 0)} | **{m15_rsi.get('rsi_above_70', {}).get('win_rate', 0)}%** |
| RSI ≤ 70 | {m15_rsi.get('rsi_below_70', {}).get('count', 0)} | {m15_rsi.get('rsi_below_70', {}).get('wins', 0)} | {m15_rsi.get('rsi_below_70', {}).get('win_rate', 0)}% |

### Confirmation Analysis (M15)
| Entry Timing | Count | Wins | Win Rate |
|--------------|-------|------|----------|
| Wait for Red Candle | {m15_conf.get('with_red_candle', {}).get('count', 0)} | {m15_conf.get('with_red_candle', {}).get('wins', 0)} | **{m15_conf.get('with_red_candle', {}).get('win_rate', 0)}%** |
| Enter Immediately | {m15_conf.get('without_confirmation', {}).get('count', 0)} | {m15_conf.get('without_confirmation', {}).get('wins', 0)} | {m15_conf.get('without_confirmation', {}).get('win_rate', 0)}% |

### Drop Size Distribution (M15)
```
$0-10:   {'█' * m15_analysis.get('drop_distribution', {}).get('0-10', 0)} ({m15_analysis.get('drop_distribution', {}).get('0-10', 0)})
$10-20:  {'█' * m15_analysis.get('drop_distribution', {}).get('10-20', 0)} ({m15_analysis.get('drop_distribution', {}).get('10-20', 0)})
$20-30:  {'█' * m15_analysis.get('drop_distribution', {}).get('20-30', 0)} ({m15_analysis.get('drop_distribution', {}).get('20-30', 0)})
$30+:    {'█' * m15_analysis.get('drop_distribution', {}).get('30+', 0)} ({m15_analysis.get('drop_distribution', {}).get('30+', 0)})
```

---

## H1 TIMEFRAME RESULTS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

| Metric | Value |
|--------|-------|
| Total Shooting Stars Found | {h1_analysis.get('total_patterns', 0)} |
| Bearish Reactions (>${BEARISH_REACTION_THRESHOLD} drop) | {h1_analysis.get('bearish_reactions', 0)} |
| **Win Rate** | **{h1_analysis.get('win_rate', 0)}%** |
| Average Drop Size | ${h1_analysis.get('avg_drop_size', 0)} |
| Average Rise (against) | ${h1_analysis.get('avg_rise_size', 0)} |
| Average Candles to Low | {h1_analysis.get('avg_candles_to_low', 0)} |

### RSI Enhancement (H1)
| RSI Level | Count | Wins | Win Rate |
|-----------|-------|------|----------|
| RSI > 70 (Overbought) | {h1_rsi.get('rsi_above_70', {}).get('count', 0)} | {h1_rsi.get('rsi_above_70', {}).get('wins', 0)} | **{h1_rsi.get('rsi_above_70', {}).get('win_rate', 0)}%** |
| RSI ≤ 70 | {h1_rsi.get('rsi_below_70', {}).get('count', 0)} | {h1_rsi.get('rsi_below_70', {}).get('wins', 0)} | {h1_rsi.get('rsi_below_70', {}).get('win_rate', 0)}% |

---

## RECOMMENDATION
═══════════════════════════════════════════════════════════════════════════════

### Best Setup
```
Timeframe:     {best_timeframe}
RSI Filter:    > {best_rsi_threshold} (overbought zone)
Confirmation:  {"Wait for red candle" if conf_helps else "Enter immediately on close"}
```

### Suggested Trade Parameters
```
Entry:         Short after shooting star {"+ red candle confirmation" if conf_helps else "close"}
Stop Loss:     ${suggested_sl} above shooting star high
Take Profit:   ${suggested_tp} (70% of average drop)
Risk:Reward:   1:{round(suggested_tp/suggested_sl, 1) if suggested_sl > 0 else 'N/A'}
```

### NEO Confidence Adjustment
```python
# When shooting star detected with RSI > {best_rsi_threshold}:
# - For SELL signals: confidence *= {confidence_boost}
# - For BUY signals:  confidence *= {round(1/confidence_boost, 2)}
```

### Recommended Feature Weight
```
shooting_star_weight: {confidence_boost if best_winrate >= 55 else 1.0}
```

---

## PATTERN QUALITY ASSESSMENT

| Criteria | Result |
|----------|--------|
| Win Rate > 55% | {"✅ YES" if best_winrate >= 55 else "❌ NO"} |
| Win Rate > 65% | {"✅ YES" if best_winrate >= 65 else "❌ NO"} |
| RSI Enhancement Significant | {"✅ YES" if rsi_helps else "❌ NO"} |
| Confirmation Helps | {"✅ YES" if conf_helps else "❌ NO"} |
| Average Drop > $15 | {"✅ YES" if avg_drop >= 15 else "❌ NO"} |

**Overall Assessment:** {"🟢 RELIABLE PATTERN - Integrate into NEO" if best_winrate >= 55 else "🟡 MARGINAL PATTERN - Use with caution" if best_winrate >= 45 else "🔴 UNRELIABLE PATTERN - Do not use"}

---

## RAW DATA FILES

- Patterns: `shooting_star_analysis.json`
- M15 Data: `{m15_analysis.get('total_patterns', 0)} patterns recorded`
- H1 Data: `{h1_analysis.get('total_patterns', 0)} patterns recorded`

---

*Report generated by Quinn Research System*
*Task #001 - Shooting Star Analysis*
"""
    
    return report


# ══════════════════════════════════════════════════════════════════════════════
# MAIN EXECUTION
# ══════════════════════════════════════════════════════════════════════════════

def main():
    print("="*70)
    print("🔬 SHOOTING STAR BACKTEST - XAUUSD")
    print("="*70)
    print(f"Period: Last {LOOKBACK_DAYS} days")
    print(f"Bearish threshold: ${BEARISH_REACTION_THRESHOLD}")
    print()
    
    # Fetch M15 data
    print("━" * 50)
    print("ANALYZING M15 TIMEFRAME")
    print("━" * 50)
    df_m15 = fetch_gold_data(interval="15m", days=LOOKBACK_DAYS)
    
    if df_m15.empty:
        print("Failed to fetch M15 data")
        m15_patterns = []
        m15_analysis = {"timeframe": "M15", "total_patterns": 0, "error": "No data"}
    else:
        m15_patterns = find_shooting_stars(df_m15)
        for p in m15_patterns:
            p['timeframe'] = 'M15'
        print(f"Found {len(m15_patterns)} shooting star patterns on M15")
        m15_analysis = analyze_patterns(m15_patterns, "M15")
    
    print()
    
    # Fetch H1 data
    print("━" * 50)
    print("ANALYZING H1 TIMEFRAME")
    print("━" * 50)
    df_h1 = fetch_gold_data(interval="1h", days=LOOKBACK_DAYS)
    
    if df_h1.empty:
        print("Failed to fetch H1 data")
        h1_patterns = []
        h1_analysis = {"timeframe": "H1", "total_patterns": 0, "error": "No data"}
    else:
        h1_patterns = find_shooting_stars(df_h1)
        for p in h1_patterns:
            p['timeframe'] = 'H1'
        print(f"Found {len(h1_patterns)} shooting star patterns on H1")
        h1_analysis = analyze_patterns(h1_patterns, "H1")
    
    print()
    
    # Save raw data
    all_patterns = m15_patterns + h1_patterns
    patterns_file = OUTPUT_DIR / "shooting_star_analysis.json"
    patterns_file.write_text(json.dumps({
        "generated_at": datetime.now().isoformat(),
        "lookback_days": LOOKBACK_DAYS,
        "bearish_threshold": BEARISH_REACTION_THRESHOLD,
        "m15_analysis": m15_analysis,
        "h1_analysis": h1_analysis,
        "patterns": all_patterns
    }, indent=2))
    print(f"Saved raw data to: {patterns_file}")
    
    # Generate report
    report = generate_report(m15_analysis, h1_analysis, m15_patterns, h1_patterns)
    report_file = OUTPUT_DIR / "SHOOTING_STAR_REPORT.md"
    report_file.write_text(report)
    print(f"Saved report to: {report_file}")
    
    # Print summary
    print()
    print("="*70)
    print("📊 QUICK SUMMARY")
    print("="*70)
    print(f"M15: {m15_analysis.get('total_patterns', 0)} patterns, {m15_analysis.get('win_rate', 0)}% win rate")
    print(f"H1:  {h1_analysis.get('total_patterns', 0)} patterns, {h1_analysis.get('win_rate', 0)}% win rate")
    
    best_wr = max(m15_analysis.get('win_rate', 0), h1_analysis.get('win_rate', 0))
    if best_wr >= 55:
        print()
        print("✅ PATTERN IS RELIABLE - Recommend integrating into NEO")
    else:
        print()
        print("⚠️  PATTERN NEEDS MORE DATA - Continue monitoring")
    
    return {
        "m15_analysis": m15_analysis,
        "h1_analysis": h1_analysis,
        "report_path": str(report_file),
        "data_path": str(patterns_file)
    }


if __name__ == "__main__":
    results = main()
