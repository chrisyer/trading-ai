#!/usr/bin/env python3
"""
NEO OUTCOME FEEDER
==================
Feed historical trade outcomes to NEO for learning.
Also generates "failed bearish" scenarios to teach NEO when shorts fail.

Usage:
    python feed_outcomes.py --manual  # Interactive mode
    python feed_outcomes.py --bulk    # Feed from JSON file
    python feed_outcomes.py --failed-shorts  # Generate failed short examples
"""

import json
import requests
from datetime import datetime, timedelta
from pathlib import Path
import sys

NEO_API = "http://localhost:8897"
DATA_DIR = Path("/home/jbot/trading_ai/data/neo")

# ══════════════════════════════════════════════════════════════════════════════
# FEED SINGLE OUTCOME
# ══════════════════════════════════════════════════════════════════════════════

def feed_outcome(signal_id: str, outcome: str, profit: float, 
                 entry_price: float, exit_price: float, pips: float = None, lots: float = 0.5):
    """Feed a single trade outcome to NEO"""
    
    if pips is None:
        pips = abs(exit_price - entry_price)
    
    payload = {
        "signal_id": signal_id,
        "outcome": outcome,  # "WIN" or "LOSS"
        "profit": profit,
        "entry_price": entry_price,
        "exit_price": exit_price,
        "pips": pips,
        "lots": lots
    }
    
    try:
        resp = requests.post(f"{NEO_API}/api/trade/outcome", json=payload, timeout=10)
        if resp.ok:
            result = resp.json()
            print(f"✅ Fed outcome: {signal_id} -> {outcome} (${profit:+.2f})")
            return result
        else:
            print(f"❌ Failed: {resp.status_code} - {resp.text}")
            return None
    except Exception as e:
        print(f"❌ Error: {e}")
        return None


def feed_market_data(price: float, adx: float = 30, plus_di: float = 25, minus_di: float = 15,
                     rsi: float = 55, ema20: float = None, ema50: float = None,
                     h4_trend: str = "BULLISH", shooting_star: bool = False,
                     candle_1_red: bool = False, candle_2_red: bool = False):
    """Feed market data to NEO for signal generation"""
    
    if ema20 is None:
        ema20 = price - 10
    if ema50 is None:
        ema50 = price - 30
    
    payload = {
        "symbol": "XAUUSD",
        "timestamp": datetime.now().isoformat(),
        "price": price,
        "adx": adx,
        "plus_di": plus_di,
        "minus_di": minus_di,
        "rsi": rsi,
        "atr": 18.0,
        "ema20": ema20,
        "ema50": ema50,
        "h4_trend": h4_trend,
        "shooting_star": shooting_star,
        "candle_1_red": candle_1_red,
        "candle_2_red": candle_2_red
    }
    
    try:
        resp = requests.post(f"{NEO_API}/api/market/xauusd", json=payload, timeout=10)
        if resp.ok:
            result = resp.json()
            print(f"📊 Fed market data: ${price:.2f}, Signal: {result.get('signal', {}).get('direction', 'N/A')}")
            return result
        else:
            print(f"❌ Failed: {resp.status_code}")
            return None
    except Exception as e:
        print(f"❌ Error: {e}")
        return None


# ══════════════════════════════════════════════════════════════════════════════
# GENERATE FAILED BEARISH SCENARIOS
# ══════════════════════════════════════════════════════════════════════════════

def generate_failed_shorts():
    """
    Feed NEO examples of FAILED SHORT setups.
    This teaches NEO that bearish signals often fail in a bull market.
    """
    
    print("="*70)
    print("🐻➡️🐂 GENERATING FAILED SHORT SCENARIOS")
    print("="*70)
    print("Teaching NEO that bearish setups fail in bull markets\n")
    
    # Real-world failed shorts from recent gold action
    failed_shorts = [
        {
            "signal_id": "FAILED_SHORT_20260203_01",
            "scenario": "Shooting star at $4920, but gold rallied to $5018",
            "entry": 4920, "exit": 4970, "profit": -50,
            "features": {"shooting_star_confirmed_1": True, "rsi_overbought": True, "h4_trend": "BULLISH"}
        },
        {
            "signal_id": "FAILED_SHORT_20260203_02", 
            "scenario": "RSI overbought at $4850, gold pumped to $4962",
            "entry": 4850, "exit": 4920, "profit": -70,
            "features": {"rsi_overbought": True, "resistance_rejection": True}
        },
        {
            "signal_id": "FAILED_SHORT_20260202_01",
            "scenario": "Resistance rejection at $4800, but broke through",
            "entry": 4800, "exit": 4880, "profit": -80,
            "features": {"resistance_rejection": True, "bearish_engulfing": True}
        },
        {
            "signal_id": "FAILED_SHORT_20260201_01",
            "scenario": "Bearish engulfing at $4750, gold continued up",
            "entry": 4750, "exit": 4820, "profit": -70,
            "features": {"bearish_engulfing": True, "rsi_overbought": True}
        },
        {
            "signal_id": "FAILED_SHORT_20260131_01",
            "scenario": "Double top at $4700, but broke higher",
            "entry": 4700, "exit": 4780, "profit": -80,
            "features": {"resistance_rejection": True, "momentum_divergence": True}
        },
        # More realistic failures
        {
            "signal_id": "FAILED_SHORT_20260130_01",
            "scenario": "Shooting star unconfirmed, price reversed up",
            "entry": 4650, "exit": 4720, "profit": -70,
            "features": {"shooting_star_unconfirmed": True}
        },
        {
            "signal_id": "FAILED_SHORT_20260129_01",
            "scenario": "RSI 80+ but trend too strong",
            "entry": 4600, "exit": 4680, "profit": -80,
            "features": {"rsi_overbought": True, "adx_strong": True, "h4_alignment": True}
        },
        {
            "signal_id": "FAILED_SHORT_20260128_01",
            "scenario": "Bearish engulfing false signal in uptrend",
            "entry": 4550, "exit": 4630, "profit": -80,
            "features": {"bearish_engulfing": True, "ema_trend": True}
        },
    ]
    
    # Also add some SUCCESSFUL longs to show what works
    successful_longs = [
        {
            "signal_id": "SUCCESS_LONG_20260203_01",
            "scenario": "Support bounce at $4880, rallied to $4962",
            "entry": 4880, "exit": 4960, "profit": 80,
            "features": {"support_bounce": True, "rsi_oversold": False, "h4_trend": "BULLISH"}
        },
        {
            "signal_id": "SUCCESS_LONG_20260202_01",
            "scenario": "Bullish engulfing at $4800, continued up",
            "entry": 4800, "exit": 4890, "profit": 90,
            "features": {"bullish_engulfing": True, "ema_trend": True}
        },
        {
            "signal_id": "SUCCESS_LONG_20260201_01",
            "scenario": "Hammer at support $4720, bounced hard",
            "entry": 4720, "exit": 4820, "profit": 100,
            "features": {"hammer_confirmed": True, "support_bounce": True}
        },
        {
            "signal_id": "SUCCESS_LONG_20260131_01",
            "scenario": "Doji at EMA50, trend continuation",
            "entry": 4680, "exit": 4780, "profit": 100,
            "features": {"doji_at_support": True, "ema_trend": True, "h4_alignment": True}
        },
        {
            "signal_id": "SUCCESS_LONG_20260130_01",
            "scenario": "RSI oversold bounce from $4600",
            "entry": 4600, "exit": 4720, "profit": 120,
            "features": {"rsi_oversold": True, "support_bounce": True}
        },
    ]
    
    # Feed failed shorts (LOSS outcomes)
    print("📉 Feeding FAILED SHORT trades (teaching NEO shorts fail in bull market):\n")
    for trade in failed_shorts:
        print(f"  {trade['scenario']}")
        
        # First feed market data with the features
        feed_market_data(
            price=trade['entry'],
            h4_trend="BULLISH",  # Key: it was bullish!
            shooting_star=trade['features'].get('shooting_star_confirmed_1', False) or 
                         trade['features'].get('shooting_star_unconfirmed', False),
            rsi=75 if trade['features'].get('rsi_overbought') else 55
        )
        
        # Then feed the outcome
        feed_outcome(
            signal_id=trade['signal_id'],
            outcome="LOSS",
            profit=trade['profit'],
            entry_price=trade['entry'],
            exit_price=trade['exit']
        )
        print()
    
    # Feed successful longs (WIN outcomes)
    print("\n📈 Feeding SUCCESSFUL LONG trades (teaching NEO what works):\n")
    for trade in successful_longs:
        print(f"  {trade['scenario']}")
        
        feed_market_data(
            price=trade['entry'],
            h4_trend="BULLISH",
            rsi=35 if trade['features'].get('rsi_oversold') else 55
        )
        
        feed_outcome(
            signal_id=trade['signal_id'],
            outcome="WIN",
            profit=trade['profit'],
            entry_price=trade['entry'],
            exit_price=trade['exit']
        )
        print()
    
    print("="*70)
    print(f"✅ Fed {len(failed_shorts)} failed shorts + {len(successful_longs)} successful longs")
    print("NEO should now be skeptical of bearish signals in bull markets!")
    print("="*70)


# ══════════════════════════════════════════════════════════════════════════════
# MANUAL OUTCOME ENTRY
# ══════════════════════════════════════════════════════════════════════════════

def manual_entry():
    """Interactive mode to enter trade outcomes"""
    
    print("="*70)
    print("📝 MANUAL TRADE OUTCOME ENTRY")
    print("="*70)
    print("Enter trade outcomes to teach NEO. Type 'done' when finished.\n")
    
    count = 0
    while True:
        print(f"\n--- Trade #{count + 1} ---")
        
        direction = input("Direction (BUY/SELL or 'done'): ").strip().upper()
        if direction == 'DONE':
            break
        
        if direction not in ['BUY', 'SELL']:
            print("Invalid direction. Use BUY or SELL.")
            continue
        
        try:
            entry = float(input("Entry price: $"))
            exit_price = float(input("Exit price: $"))
            
            # Calculate profit/loss
            if direction == "BUY":
                profit = exit_price - entry
                outcome = "WIN" if profit > 0 else "LOSS"
            else:
                profit = entry - exit_price
                outcome = "WIN" if profit > 0 else "LOSS"
            
            # Generate signal ID
            signal_id = f"MANUAL_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{count}"
            
            # Optional: Get features
            print("\nOptional features (press Enter to skip):")
            shooting_star = input("  Shooting star? (y/n): ").lower() == 'y'
            rsi_ob = input("  RSI overbought? (y/n): ").lower() == 'y'
            engulfing = input("  Engulfing pattern? (y/n): ").lower() == 'y'
            
            # Feed market context
            feed_market_data(
                price=entry,
                h4_trend="BULLISH",
                shooting_star=shooting_star,
                rsi=75 if rsi_ob else 55
            )
            
            # Feed outcome
            result = feed_outcome(
                signal_id=signal_id,
                outcome=outcome,
                profit=profit,
                entry_price=entry,
                exit_price=exit_price
            )
            
            if result:
                count += 1
                print(f"\n✅ Recorded: {direction} from ${entry:.2f} to ${exit_price:.2f} = {outcome} (${profit:+.2f})")
        
        except ValueError:
            print("Invalid input. Please enter numbers for prices.")
    
    print(f"\n✅ Fed {count} trade outcomes to NEO")


# ══════════════════════════════════════════════════════════════════════════════
# BULK IMPORT FROM FILE
# ══════════════════════════════════════════════════════════════════════════════

def bulk_import(filepath: str):
    """Import trades from a JSON file"""
    
    print(f"📂 Loading trades from {filepath}...")
    
    try:
        with open(filepath) as f:
            trades = json.load(f)
        
        if isinstance(trades, dict):
            trades = trades.get('trades', [])
        
        print(f"Found {len(trades)} trades to import\n")
        
        for i, trade in enumerate(trades):
            print(f"[{i+1}/{len(trades)}] {trade.get('signal_id', f'IMPORT_{i}')}")
            
            feed_outcome(
                signal_id=trade.get('signal_id', f"IMPORT_{datetime.now().strftime('%Y%m%d')}_{i}"),
                outcome=trade.get('outcome', 'LOSS'),
                profit=trade.get('profit', 0),
                entry_price=trade.get('entry_price', 0),
                exit_price=trade.get('exit_price', 0),
                pips=trade.get('pips'),
                lots=trade.get('lots', 0.5)
            )
        
        print(f"\n✅ Imported {len(trades)} trades")
        
    except Exception as e:
        print(f"❌ Error: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Feed outcomes to NEO")
    parser.add_argument('--manual', action='store_true', help="Interactive entry mode")
    parser.add_argument('--bulk', type=str, help="Path to JSON file with trades")
    parser.add_argument('--failed-shorts', action='store_true', help="Generate failed short examples")
    parser.add_argument('--all', action='store_true', help="Run failed-shorts training")
    
    args = parser.parse_args()
    
    if args.manual:
        manual_entry()
    elif args.bulk:
        bulk_import(args.bulk)
    elif args.failed_shorts or args.all:
        generate_failed_shorts()
    else:
        print("Usage:")
        print("  python feed_outcomes.py --manual        # Interactive mode")
        print("  python feed_outcomes.py --bulk FILE     # Import from JSON")
        print("  python feed_outcomes.py --failed-shorts # Teach NEO shorts fail")
        print("  python feed_outcomes.py --all           # Full training")
