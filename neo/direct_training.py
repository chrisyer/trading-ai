#!/usr/bin/env python3
"""
NEO DIRECT TRAINING
====================
Directly train NEO's pattern library without going through the API.
This bypasses the signal-first requirement for bulk training.

Usage:
    python direct_training.py --bullish-bias   # Train with bullish market examples
    python direct_training.py --weights        # Show current weights
"""

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Dict, List

DATA_DIR = Path("/home/jbot/trading_ai/data/neo")
WEIGHTS_FILE = DATA_DIR / "weights/current.json"
PATTERNS_DB = DATA_DIR / "patterns/library.db"

# ══════════════════════════════════════════════════════════════════════════════
# PATTERN DATABASE
# ══════════════════════════════════════════════════════════════════════════════

def init_db():
    """Initialize pattern database"""
    conn = sqlite3.connect(str(PATTERNS_DB))
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS patterns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            features TEXT,
            direction TEXT,
            outcome TEXT,
            pnl REAL,
            pips REAL,
            signal_id TEXT,
            market_context TEXT
        )
    ''')
    
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_features ON patterns(features)
    ''')
    
    conn.commit()
    conn.close()
    print("✅ Pattern database initialized")


def add_pattern(features: List[str], direction: str, outcome: str, 
                pnl: float, pips: float, signal_id: str, market_context: dict):
    """Add a pattern directly to the database"""
    
    conn = sqlite3.connect(str(PATTERNS_DB))
    cursor = conn.cursor()
    
    features_key = "|".join(sorted(features))
    
    cursor.execute('''
        INSERT INTO patterns (timestamp, features, direction, outcome, pnl, pips, signal_id, market_context)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        datetime.now().isoformat(),
        features_key,
        direction,
        outcome,
        pnl,
        pips,
        signal_id,
        json.dumps(market_context)
    ))
    
    conn.commit()
    conn.close()


def count_patterns():
    """Count patterns in database"""
    conn = sqlite3.connect(str(PATTERNS_DB))
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM patterns")
    count = cursor.fetchone()[0]
    conn.close()
    return count


def get_pattern_stats():
    """Get pattern statistics"""
    conn = sqlite3.connect(str(PATTERNS_DB))
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT direction, outcome, COUNT(*), AVG(pnl)
        FROM patterns
        GROUP BY direction, outcome
    """)
    
    stats = {}
    for row in cursor.fetchall():
        key = f"{row[0]}_{row[1]}"
        stats[key] = {"count": row[2], "avg_pnl": round(row[3], 2)}
    
    conn.close()
    return stats


# ══════════════════════════════════════════════════════════════════════════════
# WEIGHT MANAGEMENT
# ══════════════════════════════════════════════════════════════════════════════

def load_weights() -> Dict:
    """Load current weights"""
    if WEIGHTS_FILE.exists():
        return json.loads(WEIGHTS_FILE.read_text())
    return {}


def save_weights(weights: Dict):
    """Save weights"""
    WEIGHTS_FILE.write_text(json.dumps(weights, indent=2))


def adjust_weight(feature: str, is_win: bool, learning_rate: float = 0.05):
    """Adjust a single weight based on outcome"""
    weights = load_weights()
    
    if feature not in weights:
        weights[feature] = 1.0
    
    if is_win:
        weights[feature] *= (1 + learning_rate)
    else:
        weights[feature] *= (1 - learning_rate * 2)  # 2x penalty for losses
    
    # Clamp
    weights[feature] = max(0.3, min(2.5, weights[feature]))
    
    save_weights(weights)
    return weights[feature]


# ══════════════════════════════════════════════════════════════════════════════
# BULLISH MARKET TRAINING
# ══════════════════════════════════════════════════════════════════════════════

def train_bullish_bias():
    """Train NEO with bullish market patterns"""
    
    print("="*70)
    print("🐂 BULLISH MARKET TRAINING")
    print("="*70)
    print("Teaching NEO that in bull markets:")
    print("  - Bearish signals often FAIL")
    print("  - Bullish signals often WIN")
    print("  - Always err on the BUY side")
    print()
    
    init_db()
    
    # ════════════════════════════════════════════════════════════════════════
    # FAILED BEARISH PATTERNS (shorts that lose in bull market)
    # ════════════════════════════════════════════════════════════════════════
    
    failed_bearish = [
        # Shooting stars that failed
        (["shooting_star_confirmed_1", "rsi_overbought", "h4_alignment"], "SELL", "LOSS", -50, 50),
        (["shooting_star_confirmed_1", "resistance_rejection"], "SELL", "LOSS", -60, 60),
        (["shooting_star_confirmed_2", "rsi_overbought"], "SELL", "LOSS", -40, 40),
        (["shooting_star_unconfirmed", "adx_strong"], "SELL", "LOSS", -70, 70),
        (["shooting_star_confirmed_1", "ema_crossover"], "SELL", "LOSS", -55, 55),
        
        # Bearish engulfing that failed
        (["bearish_engulfing", "rsi_overbought"], "SELL", "LOSS", -80, 80),
        (["bearish_engulfing", "resistance_rejection"], "SELL", "LOSS", -65, 65),
        (["bearish_engulfing", "momentum_divergence"], "SELL", "LOSS", -70, 70),
        
        # RSI overbought that failed
        (["rsi_overbought", "adx_strong", "ema_trend"], "SELL", "LOSS", -90, 90),
        (["rsi_overbought", "resistance_rejection"], "SELL", "LOSS", -75, 75),
        (["rsi_overbought", "di_divergence"], "SELL", "LOSS", -60, 60),
        
        # Resistance rejection that failed (broke through)
        (["resistance_rejection", "h4_alignment"], "SELL", "LOSS", -85, 85),
        (["resistance_rejection", "volume_confirm"], "SELL", "LOSS", -70, 70),
    ]
    
    # ════════════════════════════════════════════════════════════════════════
    # SUCCESSFUL BULLISH PATTERNS (longs that win in bull market)
    # ════════════════════════════════════════════════════════════════════════
    
    successful_bullish = [
        # Support bounces
        (["support_bounce", "h4_alignment", "ema_trend"], "BUY", "WIN", 100, 100),
        (["support_bounce", "rsi_oversold"], "BUY", "WIN", 120, 120),
        (["support_bounce", "bullish_engulfing"], "BUY", "WIN", 90, 90),
        (["support_bounce", "adx_strong"], "BUY", "WIN", 110, 110),
        (["support_bounce", "volume_confirm"], "BUY", "WIN", 85, 85),
        
        # Bullish engulfing
        (["bullish_engulfing", "ema_trend"], "BUY", "WIN", 80, 80),
        (["bullish_engulfing", "support_bounce"], "BUY", "WIN", 95, 95),
        (["bullish_engulfing", "h4_alignment"], "BUY", "WIN", 75, 75),
        (["bullish_engulfing", "rsi_momentum"], "BUY", "WIN", 70, 70),
        
        # Hammer patterns
        (["hammer_confirmed", "support_bounce"], "BUY", "WIN", 100, 100),
        (["hammer_confirmed", "ema_trend"], "BUY", "WIN", 85, 85),
        (["hammer_confirmed", "h4_alignment"], "BUY", "WIN", 90, 90),
        
        # RSI oversold bounces
        (["rsi_oversold", "support_bounce"], "BUY", "WIN", 130, 130),
        (["rsi_oversold", "ema_trend"], "BUY", "WIN", 110, 110),
        (["rsi_oversold", "bullish_engulfing"], "BUY", "WIN", 120, 120),
        
        # EMA trend continuation
        (["ema_trend", "ema_crossover", "adx_strong"], "BUY", "WIN", 80, 80),
        (["ema_trend", "h4_alignment"], "BUY", "WIN", 75, 75),
        (["ema_trend", "rsi_momentum"], "BUY", "WIN", 65, 65),
        
        # Doji at support
        (["doji_at_support", "ema_trend"], "BUY", "WIN", 70, 70),
        (["doji_at_support", "h4_alignment"], "BUY", "WIN", 80, 80),
    ]
    
    # ════════════════════════════════════════════════════════════════════════
    # ADD PATTERNS TO DATABASE
    # ════════════════════════════════════════════════════════════════════════
    
    print("📉 Adding FAILED BEARISH patterns (13 examples):")
    for i, (features, direction, outcome, pnl, pips) in enumerate(failed_bearish):
        signal_id = f"BEAR_FAIL_{i+1:03d}"
        add_pattern(features, direction, outcome, pnl, pips, signal_id, 
                   {"h4_trend": "BULLISH", "market_regime": "STRONG_UPTREND"})
        
        # Also adjust weights DOWN for these bearish features
        for f in features:
            if f in ["shooting_star_confirmed_1", "shooting_star_confirmed_2", 
                     "bearish_engulfing", "resistance_rejection", "rsi_overbought"]:
                new_weight = adjust_weight(f, is_win=False)
                print(f"  ↓ {f}: {new_weight:.3f}")
    
    print(f"\n📈 Adding SUCCESSFUL BULLISH patterns (21 examples):")
    for i, (features, direction, outcome, pnl, pips) in enumerate(successful_bullish):
        signal_id = f"BULL_WIN_{i+1:03d}"
        add_pattern(features, direction, outcome, pnl, pips, signal_id,
                   {"h4_trend": "BULLISH", "market_regime": "STRONG_UPTREND"})
        
        # Adjust weights UP for bullish features
        for f in features:
            if f in ["support_bounce", "bullish_engulfing", "hammer_confirmed", 
                     "rsi_oversold", "doji_at_support", "ema_trend"]:
                new_weight = adjust_weight(f, is_win=True)
                print(f"  ↑ {f}: {new_weight:.3f}")
    
    # ════════════════════════════════════════════════════════════════════════
    # SUMMARY
    # ════════════════════════════════════════════════════════════════════════
    
    print("\n" + "="*70)
    print("📊 TRAINING COMPLETE")
    print("="*70)
    
    stats = get_pattern_stats()
    print(f"\nPattern Library: {count_patterns()} patterns")
    print("\nBreakdown:")
    for key, data in stats.items():
        print(f"  {key}: {data['count']} patterns, avg PnL ${data['avg_pnl']:.2f}")
    
    print("\n📋 Updated Weights:")
    weights = load_weights()
    
    # Show bearish (should be lower)
    print("\n  BEARISH (should be lower):")
    for k in ["shooting_star_confirmed_1", "shooting_star_confirmed_2", "bearish_engulfing", 
              "resistance_rejection", "rsi_overbought"]:
        if k in weights:
            emoji = "🔴" if weights[k] < 1.0 else "🟢"
            print(f"    {emoji} {k}: {weights[k]:.3f}")
    
    # Show bullish (should be higher)
    print("\n  BULLISH (should be higher):")
    for k in ["support_bounce", "bullish_engulfing", "hammer_confirmed", 
              "rsi_oversold", "doji_at_support", "ema_trend"]:
        if k in weights:
            emoji = "🟢" if weights[k] > 1.0 else "🔴"
            print(f"    {emoji} {k}: {weights[k]:.3f}")
    
    print("\n✅ NEO is now BULLISH-BIASED!")


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Direct NEO training")
    parser.add_argument('--bullish-bias', action='store_true', help="Train with bullish bias")
    parser.add_argument('--weights', action='store_true', help="Show current weights")
    parser.add_argument('--stats', action='store_true', help="Show pattern stats")
    
    args = parser.parse_args()
    
    if args.weights:
        print("Current weights:")
        weights = load_weights()
        for k, v in sorted(weights.items()):
            if not k.startswith('_'):
                print(f"  {k}: {v}")
    elif args.stats:
        init_db()
        print(f"Patterns: {count_patterns()}")
        stats = get_pattern_stats()
        for k, v in stats.items():
            print(f"  {k}: {v}")
    elif args.bullish_bias:
        train_bullish_bias()
    else:
        print("Usage:")
        print("  python direct_training.py --bullish-bias  # Full bullish training")
        print("  python direct_training.py --weights       # Show weights")
        print("  python direct_training.py --stats         # Show pattern stats")
