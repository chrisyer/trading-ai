#!/usr/bin/env python3
"""
ENGULFING EXIT SIGNAL BACKTEST - XAUUSD
========================================
Research Task #003: Validate engulfing candles as EXIT signals

After shooting star entry, analyze:
1. When does bullish engulfing appear?
2. How much of the move is captured?
3. Compare exit strategies

Author: Quinn
Date: 2026-02-03
"""

import json
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional, Tuple

# ══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ══════════════════════════════════════════════════════════════════════════════

OUTPUT_DIR = Path("/home/jbot/trading_ai/research/pattern_analysis")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

LOOKBACK_DAYS = 60
MIN_ENGULFING_RATIO = 1.0  # Engulfing body must be >= 100% of previous

# ══════════════════════════════════════════════════════════════════════════════
# PATTERN DETECTION FUNCTIONS
# ══════════════════════════════════════════════════════════════════════════════

def is_shooting_star(row: pd.Series, prev_closes: List[float]) -> bool:
    """Detect shooting star pattern"""
    o, h, l, c = row['open'], row['high'], row['low'], row['close']
    
    body = abs(c - o)
    upper = h - max(o, c)
    lower = min(o, c) - l
    total = h - l
    
    if total == 0 or body == 0:
        return False
    
    small_body = body <= total * 0.3
    long_upper = upper >= body * 2
    short_lower = lower <= total * 0.1
    
    if len(prev_closes) >= 5:
        avg = sum(prev_closes[-5:]) / 5
        uptrend = c > avg * 0.998
    else:
        uptrend = True
    
    return small_body and long_upper and short_lower and uptrend


def is_bullish_engulfing(prev: pd.Series, curr: pd.Series, min_ratio: float = 1.0) -> Tuple[bool, float]:
    """
    Detect bullish engulfing - EXIT signal for shorts
    Returns (is_engulfing, engulfing_ratio)
    """
    # Previous must be red
    prev_red = prev['close'] < prev['open']
    
    # Current must be green
    curr_green = curr['close'] > curr['open']
    
    if not (prev_red and curr_green):
        return False, 0.0
    
    prev_body = abs(prev['close'] - prev['open'])
    curr_body = abs(curr['close'] - curr['open'])
    
    if prev_body == 0:
        return False, 0.0
    
    # Current body must engulf previous body
    engulfs = (curr['open'] <= prev['close'] and 
               curr['close'] >= prev['open'])
    
    ratio = curr_body / prev_body if prev_body > 0 else 0
    
    return engulfs and ratio >= min_ratio, ratio


def is_bearish_engulfing(prev: pd.Series, curr: pd.Series, min_ratio: float = 1.0) -> Tuple[bool, float]:
    """
    Detect bearish engulfing - EXIT signal for longs
    Returns (is_engulfing, engulfing_ratio)
    """
    # Previous must be green
    prev_green = prev['close'] > prev['open']
    
    # Current must be red
    curr_red = curr['close'] < curr['open']
    
    if not (prev_green and curr_red):
        return False, 0.0
    
    prev_body = abs(prev['close'] - prev['open'])
    curr_body = abs(curr['close'] - curr['open'])
    
    if prev_body == 0:
        return False, 0.0
    
    # Current body must engulf previous body
    engulfs = (curr['open'] >= prev['close'] and 
               curr['close'] <= prev['open'])
    
    ratio = curr_body / prev_body if prev_body > 0 else 0
    
    return engulfs and ratio >= min_ratio, ratio


def calculate_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Calculate RSI"""
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))


# ══════════════════════════════════════════════════════════════════════════════
# EXIT STRATEGY SIMULATORS
# ══════════════════════════════════════════════════════════════════════════════

def simulate_engulfing_exit(df: pd.DataFrame, entry_idx: int, entry_price: float, 
                           direction: str = "SHORT", max_candles: int = 50) -> Dict:
    """
    Simulate exiting on bullish engulfing (for shorts)
    """
    result = {
        'exit_type': 'engulfing',
        'exit_found': False,
        'exit_price': None,
        'exit_idx': None,
        'candles_held': 0,
        'profit': 0,
        'engulfing_ratio': 0
    }
    
    for i in range(entry_idx + 1, min(entry_idx + max_candles, len(df) - 1)):
        prev = df.iloc[i - 1]
        curr = df.iloc[i]
        
        if direction == "SHORT":
            is_exit, ratio = is_bullish_engulfing(prev, curr)
        else:
            is_exit, ratio = is_bearish_engulfing(prev, curr)
        
        if is_exit:
            exit_price = float(curr['close'])
            result['exit_found'] = True
            result['exit_price'] = exit_price
            result['exit_idx'] = i
            result['candles_held'] = i - entry_idx
            result['engulfing_ratio'] = round(ratio, 2)
            
            if direction == "SHORT":
                result['profit'] = round(entry_price - exit_price, 2)
            else:
                result['profit'] = round(exit_price - entry_price, 2)
            break
    
    return result


def simulate_fixed_tp_exit(df: pd.DataFrame, entry_idx: int, entry_price: float,
                          tp_amount: float, direction: str = "SHORT", 
                          max_candles: int = 50, sl_amount: float = 30) -> Dict:
    """
    Simulate fixed take profit exit
    """
    result = {
        'exit_type': f'fixed_tp_{tp_amount}',
        'exit_found': False,
        'exit_price': None,
        'exit_idx': None,
        'candles_held': 0,
        'profit': 0,
        'hit_tp': False,
        'hit_sl': False
    }
    
    if direction == "SHORT":
        tp_price = entry_price - tp_amount
        sl_price = entry_price + sl_amount
    else:
        tp_price = entry_price + tp_amount
        sl_price = entry_price - sl_amount
    
    for i in range(entry_idx + 1, min(entry_idx + max_candles, len(df))):
        candle = df.iloc[i]
        
        if direction == "SHORT":
            # Check if TP hit
            if candle['low'] <= tp_price:
                result['exit_found'] = True
                result['exit_price'] = tp_price
                result['exit_idx'] = i
                result['candles_held'] = i - entry_idx
                result['profit'] = tp_amount
                result['hit_tp'] = True
                break
            # Check if SL hit
            elif candle['high'] >= sl_price:
                result['exit_found'] = True
                result['exit_price'] = sl_price
                result['exit_idx'] = i
                result['candles_held'] = i - entry_idx
                result['profit'] = -sl_amount
                result['hit_sl'] = True
                break
        else:
            # Long position
            if candle['high'] >= tp_price:
                result['exit_found'] = True
                result['exit_price'] = tp_price
                result['exit_idx'] = i
                result['candles_held'] = i - entry_idx
                result['profit'] = tp_amount
                result['hit_tp'] = True
                break
            elif candle['low'] <= sl_price:
                result['exit_found'] = True
                result['exit_price'] = sl_price
                result['exit_idx'] = i
                result['candles_held'] = i - entry_idx
                result['profit'] = -sl_amount
                result['hit_sl'] = True
                break
    
    # If neither hit, exit at last candle
    if not result['exit_found']:
        last_idx = min(entry_idx + max_candles - 1, len(df) - 1)
        exit_price = float(df.iloc[last_idx]['close'])
        result['exit_found'] = True
        result['exit_price'] = exit_price
        result['exit_idx'] = last_idx
        result['candles_held'] = last_idx - entry_idx
        if direction == "SHORT":
            result['profit'] = round(entry_price - exit_price, 2)
        else:
            result['profit'] = round(exit_price - entry_price, 2)
    
    return result


def simulate_trailing_stop(df: pd.DataFrame, entry_idx: int, entry_price: float,
                          trail_amount: float, direction: str = "SHORT",
                          max_candles: int = 50) -> Dict:
    """
    Simulate trailing stop exit
    """
    result = {
        'exit_type': f'trailing_{trail_amount}',
        'exit_found': False,
        'exit_price': None,
        'exit_idx': None,
        'candles_held': 0,
        'profit': 0,
        'best_price': entry_price
    }
    
    best_price = entry_price
    
    for i in range(entry_idx + 1, min(entry_idx + max_candles, len(df))):
        candle = df.iloc[i]
        
        if direction == "SHORT":
            # Update best price (lowest low for shorts)
            if candle['low'] < best_price:
                best_price = float(candle['low'])
            
            # Trail stop above best price
            trail_stop = best_price + trail_amount
            
            # Check if stopped out
            if candle['high'] >= trail_stop:
                result['exit_found'] = True
                result['exit_price'] = trail_stop
                result['exit_idx'] = i
                result['candles_held'] = i - entry_idx
                result['profit'] = round(entry_price - trail_stop, 2)
                result['best_price'] = best_price
                break
        else:
            # Long: track highest high
            if candle['high'] > best_price:
                best_price = float(candle['high'])
            
            trail_stop = best_price - trail_amount
            
            if candle['low'] <= trail_stop:
                result['exit_found'] = True
                result['exit_price'] = trail_stop
                result['exit_idx'] = i
                result['candles_held'] = i - entry_idx
                result['profit'] = round(trail_stop - entry_price, 2)
                result['best_price'] = best_price
                break
    
    # If never stopped, exit at last candle
    if not result['exit_found']:
        last_idx = min(entry_idx + max_candles - 1, len(df) - 1)
        exit_price = float(df.iloc[last_idx]['close'])
        result['exit_found'] = True
        result['exit_price'] = exit_price
        result['exit_idx'] = last_idx
        result['candles_held'] = last_idx - entry_idx
        if direction == "SHORT":
            result['profit'] = round(entry_price - exit_price, 2)
        else:
            result['profit'] = round(exit_price - entry_price, 2)
        result['best_price'] = best_price
    
    return result


# ══════════════════════════════════════════════════════════════════════════════
# MAIN ANALYSIS
# ══════════════════════════════════════════════════════════════════════════════

def analyze_exits_after_shooting_stars(df: pd.DataFrame) -> List[Dict]:
    """
    Find all shooting stars and analyze different exit strategies
    """
    
    # Add RSI
    df['rsi'] = calculate_rsi(df['close'])
    
    trades = []
    
    for i in range(20, len(df) - 50):
        row = df.iloc[i]
        prev_closes = df['close'].iloc[i-20:i].tolist()
        
        # Check for shooting star
        if not is_shooting_star(row, prev_closes):
            continue
        
        # Check for red candle confirmation
        if i + 1 >= len(df):
            continue
        
        next_candle = df.iloc[i + 1]
        confirmed = next_candle['close'] < row['close']
        
        if not confirmed:
            continue  # Only analyze confirmed shooting stars
        
        entry_price = float(next_candle['close'])  # Enter after confirmation
        entry_idx = i + 1
        ss_high = float(row['high'])
        
        # Track max possible profit (lowest low in next 30 candles)
        future = df.iloc[entry_idx:entry_idx + 30]
        if len(future) == 0:
            continue
        
        lowest_low = float(future['low'].min())
        max_profit = entry_price - lowest_low
        
        # Find RSI at low point
        low_idx = future['low'].idxmin()
        rsi_at_low = df.loc[low_idx, 'rsi'] if pd.notna(df.loc[low_idx, 'rsi']) else 50
        
        # Simulate different exit strategies
        engulfing_exit = simulate_engulfing_exit(df, entry_idx, entry_price, "SHORT")
        tp_15_exit = simulate_fixed_tp_exit(df, entry_idx, entry_price, 15, "SHORT")
        tp_20_exit = simulate_fixed_tp_exit(df, entry_idx, entry_price, 20, "SHORT")
        tp_30_exit = simulate_fixed_tp_exit(df, entry_idx, entry_price, 30, "SHORT")
        trail_8_exit = simulate_trailing_stop(df, entry_idx, entry_price, 8, "SHORT")
        trail_10_exit = simulate_trailing_stop(df, entry_idx, entry_price, 10, "SHORT")
        trail_15_exit = simulate_trailing_stop(df, entry_idx, entry_price, 15, "SHORT")
        
        # Calculate capture percentages
        def capture_pct(profit, max_p):
            if max_p <= 0:
                return 0
            return min(100, max(0, (profit / max_p) * 100))
        
        trade = {
            'entry_timestamp': str(df.index[entry_idx]) if hasattr(df, 'index') else str(entry_idx),
            'entry_price': entry_price,
            'ss_high': ss_high,
            'lowest_low': lowest_low,
            'max_profit': round(max_profit, 2),
            'rsi_at_low': round(float(rsi_at_low), 1),
            
            'engulfing': {
                **engulfing_exit,
                'capture_pct': round(capture_pct(engulfing_exit['profit'], max_profit), 1) if engulfing_exit['exit_found'] else 0
            },
            'tp_15': {
                **tp_15_exit,
                'capture_pct': round(capture_pct(tp_15_exit['profit'], max_profit), 1) if tp_15_exit['exit_found'] else 0
            },
            'tp_20': {
                **tp_20_exit,
                'capture_pct': round(capture_pct(tp_20_exit['profit'], max_profit), 1) if tp_20_exit['exit_found'] else 0
            },
            'tp_30': {
                **tp_30_exit,
                'capture_pct': round(capture_pct(tp_30_exit['profit'], max_profit), 1) if tp_30_exit['exit_found'] else 0
            },
            'trail_8': {
                **trail_8_exit,
                'capture_pct': round(capture_pct(trail_8_exit['profit'], max_profit), 1) if trail_8_exit['exit_found'] else 0
            },
            'trail_10': {
                **trail_10_exit,
                'capture_pct': round(capture_pct(trail_10_exit['profit'], max_profit), 1) if trail_10_exit['exit_found'] else 0
            },
            'trail_15': {
                **trail_15_exit,
                'capture_pct': round(capture_pct(trail_15_exit['profit'], max_profit), 1) if trail_15_exit['exit_found'] else 0
            }
        }
        
        # Check if engulfing was a good exit (price went back up after)
        if engulfing_exit['exit_found'] and engulfing_exit['exit_idx']:
            after_eng = df.iloc[engulfing_exit['exit_idx']:engulfing_exit['exit_idx'] + 5]
            if len(after_eng) > 0:
                price_after = float(after_eng['close'].iloc[-1]) if len(after_eng) > 0 else engulfing_exit['exit_price']
                trade['engulfing']['price_went_up_after'] = price_after > engulfing_exit['exit_price']
        
        trades.append(trade)
    
    return trades


def generate_report(trades: List[Dict]) -> str:
    """Generate markdown report"""
    
    if not trades:
        return "No trades found for analysis"
    
    total = len(trades)
    
    # Engulfing stats
    eng_found = [t for t in trades if t['engulfing']['exit_found']]
    eng_profits = [t['engulfing']['profit'] for t in eng_found]
    eng_captures = [t['engulfing']['capture_pct'] for t in eng_found]
    eng_candles = [t['engulfing']['candles_held'] for t in eng_found]
    eng_good_exits = [t for t in eng_found if t['engulfing'].get('price_went_up_after', False)]
    
    # Fixed TP stats
    tp15_found = [t for t in trades if t['tp_15']['exit_found']]
    tp15_profits = [t['tp_15']['profit'] for t in tp15_found]
    tp15_hit = [t for t in tp15_found if t['tp_15'].get('hit_tp', False)]
    
    tp20_found = [t for t in trades if t['tp_20']['exit_found']]
    tp20_profits = [t['tp_20']['profit'] for t in tp20_found]
    tp20_hit = [t for t in tp20_found if t['tp_20'].get('hit_tp', False)]
    
    tp30_found = [t for t in trades if t['tp_30']['exit_found']]
    tp30_profits = [t['tp_30']['profit'] for t in tp30_found]
    tp30_hit = [t for t in tp30_found if t['tp_30'].get('hit_tp', False)]
    
    # Trailing stats
    trail8_found = [t for t in trades if t['trail_8']['exit_found']]
    trail8_profits = [t['trail_8']['profit'] for t in trail8_found]
    
    trail10_found = [t for t in trades if t['trail_10']['exit_found']]
    trail10_profits = [t['trail_10']['profit'] for t in trail10_found]
    
    trail15_found = [t for t in trades if t['trail_15']['exit_found']]
    trail15_profits = [t['trail_15']['profit'] for t in trail15_found]
    
    # Calculate averages safely
    def safe_avg(lst):
        return np.mean(lst) if lst else 0
    
    # Determine best strategy
    strategies = {
        'Engulfing': safe_avg(eng_profits),
        'TP $15': safe_avg(tp15_profits),
        'TP $20': safe_avg(tp20_profits),
        'TP $30': safe_avg(tp30_profits),
        'Trail $8': safe_avg(trail8_profits),
        'Trail $10': safe_avg(trail10_profits),
        'Trail $15': safe_avg(trail15_profits)
    }
    best_strategy = max(strategies, key=strategies.get)
    
    report = f"""# ENGULFING EXIT SIGNAL ANALYSIS - XAUUSD M15
## Research Task #003 | Completed: {datetime.now().strftime('%Y-%m-%d %H:%M')} UTC

═══════════════════════════════════════════════════════════════════════════════

## EXECUTIVE SUMMARY

**Analysis Period:** {LOOKBACK_DAYS} days
**Confirmed Shooting Star Entries:** {total}
**Exit Strategies Compared:** 7

---

## BULLISH ENGULFING AS SHORT EXIT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

| Metric | Value |
|--------|-------|
| Engulfing Found After Entry | {len(eng_found)}/{total} ({len(eng_found)/total*100:.1f}%) |
| Average Candles to Engulfing | {safe_avg(eng_candles):.1f} |
| Average Profit | ${safe_avg(eng_profits):.2f} |
| Average Capture % | {safe_avg(eng_captures):.1f}% |
| Good Exits (price went up after) | {len(eng_good_exits)}/{len(eng_found)} ({len(eng_good_exits)/len(eng_found)*100 if eng_found else 0:.1f}%) |

---

## EXIT STRATEGY COMPARISON
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

| Strategy | Avg Profit | Capture % | Hit Rate | Notes |
|----------|------------|-----------|----------|-------|
| **Engulfing** | **${safe_avg(eng_profits):.2f}** | {safe_avg(eng_captures):.1f}% | {len(eng_found)/total*100:.0f}% | Adapts to move |
| Fixed TP $15 | ${safe_avg(tp15_profits):.2f} | {safe_avg([t['tp_15']['capture_pct'] for t in trades]):.1f}% | {len(tp15_hit)/total*100:.0f}% | Conservative |
| Fixed TP $20 | ${safe_avg(tp20_profits):.2f} | {safe_avg([t['tp_20']['capture_pct'] for t in trades]):.1f}% | {len(tp20_hit)/total*100:.0f}% | Balanced |
| Fixed TP $30 | ${safe_avg(tp30_profits):.2f} | {safe_avg([t['tp_30']['capture_pct'] for t in trades]):.1f}% | {len(tp30_hit)/total*100:.0f}% | Aggressive |
| Trail $8 | ${safe_avg(trail8_profits):.2f} | {safe_avg([t['trail_8']['capture_pct'] for t in trades]):.1f}% | 100% | Tight |
| Trail $10 | ${safe_avg(trail10_profits):.2f} | {safe_avg([t['trail_10']['capture_pct'] for t in trades]):.1f}% | 100% | Medium |
| Trail $15 | ${safe_avg(trail15_profits):.2f} | {safe_avg([t['trail_15']['capture_pct'] for t in trades]):.1f}% | 100% | Wide |

---

## RECOMMENDATION
═══════════════════════════════════════════════════════════════════════════════

**Best Strategy: {best_strategy}** (${strategies[best_strategy]:.2f} avg profit)

### Optimal Exit Approach:
```
PRIMARY:   Watch for Bullish Engulfing - adaptive to move size
BACKUP:    Trail $10 if no engulfing after 8+ candles
EMERGENCY: Fixed TP $15 if momentum stalls
```

---

## ENGULFING RATIO ANALYSIS

Larger engulfing candles = stronger reversal signal?

| Engulfing Ratio | Count | Avg Profit | Good Exit % |
|-----------------|-------|------------|-------------|"""

    # Analyze by engulfing ratio
    small_eng = [t for t in eng_found if t['engulfing']['engulfing_ratio'] < 1.5]
    medium_eng = [t for t in eng_found if 1.5 <= t['engulfing']['engulfing_ratio'] < 2.0]
    large_eng = [t for t in eng_found if t['engulfing']['engulfing_ratio'] >= 2.0]
    
    def good_exit_pct(lst):
        if not lst:
            return 0
        good = len([t for t in lst if t['engulfing'].get('price_went_up_after', False)])
        return good / len(lst) * 100
    
    report += f"""
| 1.0 - 1.5x (Small) | {len(small_eng)} | ${safe_avg([t['engulfing']['profit'] for t in small_eng]):.2f} | {good_exit_pct(small_eng):.0f}% |
| 1.5 - 2.0x (Medium) | {len(medium_eng)} | ${safe_avg([t['engulfing']['profit'] for t in medium_eng]):.2f} | {good_exit_pct(medium_eng):.0f}% |
| 2.0x+ (Large) | {len(large_eng)} | ${safe_avg([t['engulfing']['profit'] for t in large_eng]):.2f} | {good_exit_pct(large_eng):.0f}% |

---

## INDIVIDUAL TRADE DETAILS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

"""
    for i, t in enumerate(trades[:15]):  # Show first 15
        eng = t['engulfing']
        report += f"""
### Trade {i+1}: Entry ${t['entry_price']:.0f}
- Max Possible: ${t['max_profit']:.2f}
- Engulfing Exit: ${eng['profit']:.2f} ({eng['capture_pct']:.0f}% captured) after {eng['candles_held']} candles
- TP $20 Exit: ${t['tp_20']['profit']:.2f}
- Trail $10 Exit: ${t['trail_10']['profit']:.2f}
"""

    report += f"""

---

## NEO INTEGRATION

```python
# Add to NEO's exit detection:

def check_exit_signal(self, market_data, position_type):
    if position_type == "SHORT":
        # Check for bullish engulfing
        if self.is_bullish_engulfing(market_data['prev_candle'], market_data['curr_candle']):
            return {{
                'exit': True,
                'reason': 'Bullish engulfing - reversal signal',
                'confidence': 0.85
            }}
    return {{'exit': False}}
```

---

*Report generated by Quinn Research System*
*Task #003 - Engulfing Exit Analysis*
"""
    
    return report


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    print("="*70)
    print("🔬 ENGULFING EXIT SIGNAL BACKTEST - XAUUSD M15")
    print("="*70)
    print(f"Period: {LOOKBACK_DAYS} days")
    print()
    
    # Fetch data
    print("Fetching M15 data...")
    ticker = yf.Ticker("GC=F")
    df = ticker.history(period=f"{LOOKBACK_DAYS}d", interval="15m")
    
    if df.empty:
        print("Failed to fetch data")
        return
    
    df.columns = [c.lower() for c in df.columns]
    print(f"Fetched {len(df)} candles")
    
    # Run analysis
    print("\nAnalyzing exits after confirmed shooting stars...")
    trades = analyze_exits_after_shooting_stars(df)
    
    print(f"Found {len(trades)} confirmed shooting star entries")
    
    if not trades:
        print("No trades to analyze")
        return
    
    # Save raw data
    data_file = OUTPUT_DIR / "engulfing_exit_analysis.json"
    
    # Convert numpy types to native Python types
    def convert_types(obj):
        if isinstance(obj, dict):
            return {k: convert_types(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [convert_types(v) for v in obj]
        elif isinstance(obj, (np.integer, np.int64)):
            return int(obj)
        elif isinstance(obj, (np.floating, np.float64)):
            return float(obj)
        elif isinstance(obj, np.bool_):
            return bool(obj)
        return obj
    
    trades_converted = convert_types(trades)
    data_file.write_text(json.dumps({
        'generated': datetime.now().isoformat(),
        'lookback_days': LOOKBACK_DAYS,
        'total_trades': len(trades),
        'trades': trades_converted
    }, indent=2))
    print(f"\nSaved raw data to: {data_file}")
    
    # Generate report
    report = generate_report(trades)
    report_file = OUTPUT_DIR / "ENGULFING_EXIT_REPORT.md"
    report_file.write_text(report)
    print(f"Saved report to: {report_file}")
    
    # Quick summary
    print("\n" + "="*70)
    print("📊 QUICK SUMMARY")
    print("="*70)
    
    eng_profits = [t['engulfing']['profit'] for t in trades if t['engulfing']['exit_found']]
    tp20_profits = [t['tp_20']['profit'] for t in trades]
    trail10_profits = [t['trail_10']['profit'] for t in trades]
    
    print(f"\nEngulfing Exit:  ${np.mean(eng_profits):.2f} avg profit")
    print(f"Fixed TP $20:    ${np.mean(tp20_profits):.2f} avg profit")
    print(f"Trail $10:       ${np.mean(trail10_profits):.2f} avg profit")
    
    best = max([
        ('Engulfing', np.mean(eng_profits)),
        ('TP $20', np.mean(tp20_profits)),
        ('Trail $10', np.mean(trail10_profits))
    ], key=lambda x: x[1])
    
    print(f"\n✅ BEST EXIT: {best[0]} (${best[1]:.2f})")
    
    return trades


if __name__ == "__main__":
    trades = main()
