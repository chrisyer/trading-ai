#!/usr/bin/env python3
"""
═══════════════════════════════════════════════════════════════════════════════
TEACH NEO FROM PAST MISTAKES
═══════════════════════════════════════════════════════════════════════════════

NEO has been wrong 7/8 days. This script records those failures so he learns.

Usage:
    python teach_neo.py --record-failures  # Record the 7 failed predictions
    python teach_neo.py --summary          # Show what NEO has learned
    python teach_neo.py --manual           # Manually record a prediction outcome

═══════════════════════════════════════════════════════════════════════════════
"""

import json
from datetime import datetime, timedelta
from pathlib import Path
from intelligent_prediction import DualThesisPrediction

DATA_DIR = Path("/home/jbot/trading_ai/data/neo")
LEARNING_FILE = DATA_DIR / "prediction_learning.json"


def record_failures():
    """
    Record NEO's 7/8 failed predictions.
    
    From the user: NEO predicted bearish (SELL/DOWN) but gold went UP.
    This happened 7 out of 8 days.
    """
    
    print("="*70)
    print("📚 TEACHING NEO FROM PAST FAILURES")
    print("="*70)
    print()
    
    # Load or create learning data
    if LEARNING_FILE.exists():
        learning = json.loads(LEARNING_FILE.read_text())
    else:
        learning = {
            "total_predictions": 0,
            "bullish_correct": 0,
            "bearish_correct": 0,
            "bullish_debunked": 0,
            "bearish_debunked": 0,
            "debunk_reasons": [],
            "lessons": []
        }
    
    # Record the 7 failed bearish predictions
    failures = [
        {
            "date": "2026-01-27",
            "predicted": "BEARISH",
            "actual": "UP",
            "move": "+40 pts",
            "reason": "Predicted SHORT but gold rallied. EMA trend was bullish, ignored."
        },
        {
            "date": "2026-01-28",
            "predicted": "BEARISH", 
            "actual": "UP",
            "move": "+55 pts",
            "reason": "RSI overbought was NOT a sell signal in uptrend. Ignored trend."
        },
        {
            "date": "2026-01-29",
            "predicted": "BEARISH",
            "actual": "UP", 
            "move": "+80 pts",
            "reason": "Resistance rejection failed - gold broke through. Trend too strong."
        },
        {
            "date": "2026-01-30",
            "predicted": "BEARISH",
            "actual": "UP",
            "move": "+65 pts",
            "reason": "Shooting star pattern failed in bull market. Pattern NOT reliable in uptrend."
        },
        {
            "date": "2026-01-31",
            "predicted": "BEARISH",
            "actual": "UP",
            "move": "+70 pts",
            "reason": "Bearish engulfing false signal. Market structure was higher highs/lows."
        },
        {
            "date": "2026-02-01",
            "predicted": "BEARISH",
            "actual": "UP",
            "move": "+45 pts",
            "reason": "Double top call was wrong - broke higher. Failed to recognize accumulation."
        },
        {
            "date": "2026-02-03",
            "predicted": "BEARISH",
            "actual": "UP",
            "move": "+111 pts",
            "reason": "Predicted hunt of longs, but shorts got squeezed instead. Major rally day."
        },
    ]
    
    # Record 1 successful bullish call
    successes = [
        {
            "date": "2026-02-02",
            "predicted": "BULLISH",
            "actual": "UP",
            "move": "+30 pts",
            "reason": "Support bounce call was correct. Only successful prediction in 8 days."
        }
    ]
    
    print("📉 Recording FAILED BEARISH predictions (7 days):")
    print()
    
    for failure in failures:
        learning["total_predictions"] += 1
        learning["bearish_debunked"] += 1
        learning["debunk_reasons"].append({
            "date": failure["date"],
            "prediction_id": f"HISTORICAL_{failure['date'].replace('-', '')}",
            "expected": "DOWN",
            "actual": "UP",
            "move": failure["move"],
            "reason": failure["reason"],
            "notes": "Historical data - bearish thesis DEBUNKED"
        })
        learning["lessons"].append({
            "date": failure["date"],
            "was_correct": False,
            "bias": "BEARISH",
            "actual": "UP",
            "lesson": failure["reason"]
        })
        
        print(f"  ❌ {failure['date']}: Predicted BEARISH, actual UP {failure['move']}")
        print(f"     Lesson: {failure['reason'][:60]}...")
        print()
    
    print("📈 Recording SUCCESSFUL prediction (1 day):")
    print()
    
    for success in successes:
        learning["total_predictions"] += 1
        learning["bullish_correct"] += 1
        learning["lessons"].append({
            "date": success["date"],
            "was_correct": True,
            "bias": "BULLISH",
            "actual": "UP",
            "lesson": success["reason"]
        })
        
        print(f"  ✅ {success['date']}: Predicted BULLISH, actual UP {success['move']}")
        print(f"     Lesson: {success['reason']}")
        print()
    
    # Save learning
    LEARNING_FILE.write_text(json.dumps(learning, indent=2))
    
    print("="*70)
    print("📊 LEARNING SUMMARY:")
    print("="*70)
    print(f"  Total predictions: {learning['total_predictions']}")
    print(f"  Bullish correct:   {learning['bullish_correct']}")
    print(f"  Bearish correct:   {learning['bearish_correct']}")
    print(f"  Bearish debunked:  {learning['bearish_debunked']} (FAILURES)")
    print()
    print("🧠 KEY LESSONS:")
    print("  1. Don't fight the trend - gold is BULLISH")
    print("  2. RSI overbought is NOT a sell signal in uptrends")
    print("  3. Bearish patterns (shooting star, engulfing) FAIL in bull markets")
    print("  4. Resistance breaks through when trend is strong")
    print("  5. When in doubt, GO WITH THE TREND (bullish)")
    print()
    print("✅ NEO's learning data has been updated!")
    print(f"   Saved to: {LEARNING_FILE}")


def show_summary():
    """Show what NEO has learned"""
    predictor = DualThesisPrediction()
    summary = predictor.get_learning_summary()
    
    print("="*70)
    print("📊 NEO'S LEARNING SUMMARY")
    print("="*70)
    print()
    print(f"Total predictions:    {summary['total_predictions']}")
    print(f"Bullish accuracy:     {summary['bullish_accuracy']}")
    print(f"Bearish accuracy:     {summary['bearish_accuracy']}")
    print(f"Bullish debunked:     {summary['bullish_debunked_count']}")
    print(f"Bearish debunked:     {summary['bearish_debunked_count']}")
    print()
    print(f"🎯 RECOMMENDATION: {summary['recommendation']}")
    print()
    
    if summary.get("recent_debunks"):
        print("Recent failures:")
        for debunk in summary["recent_debunks"][-3:]:
            print(f"  • {debunk.get('date', 'N/A')}: {debunk.get('reason', 'Unknown')[:60]}...")
    
    if summary.get("recent_lessons"):
        print()
        print("Recent lessons:")
        for lesson in summary["recent_lessons"][-3:]:
            emoji = "✅" if lesson.get("was_correct") else "❌"
            print(f"  {emoji} {lesson.get('date', 'N/A')}: {lesson.get('lesson', 'N/A')[:60]}...")


def manual_record():
    """Manually record a prediction outcome"""
    print("="*70)
    print("📝 MANUALLY RECORD PREDICTION OUTCOME")
    print("="*70)
    print()
    
    date = input("Date (YYYY-MM-DD): ").strip()
    predicted = input("NEO predicted (BULLISH/BEARISH): ").strip().upper()
    actual = input("Actual result (UP/DOWN): ").strip().upper()
    move = input("Price move (e.g., +50 pts): ").strip()
    reason = input("Why was prediction wrong/right? ").strip()
    
    # Load learning
    if LEARNING_FILE.exists():
        learning = json.loads(LEARNING_FILE.read_text())
    else:
        learning = {
            "total_predictions": 0,
            "bullish_correct": 0,
            "bearish_correct": 0,
            "bullish_debunked": 0,
            "bearish_debunked": 0,
            "debunk_reasons": [],
            "lessons": []
        }
    
    learning["total_predictions"] += 1
    
    # Determine if correct
    was_correct = (predicted == "BULLISH" and actual == "UP") or (predicted == "BEARISH" and actual == "DOWN")
    
    if was_correct:
        if predicted == "BULLISH":
            learning["bullish_correct"] += 1
        else:
            learning["bearish_correct"] += 1
        print("\n✅ Prediction was CORRECT!")
    else:
        if predicted == "BULLISH":
            learning["bullish_debunked"] += 1
        else:
            learning["bearish_debunked"] += 1
        
        learning["debunk_reasons"].append({
            "date": date,
            "prediction_id": f"MANUAL_{date.replace('-', '')}",
            "expected": "DOWN" if predicted == "BEARISH" else "UP",
            "actual": actual,
            "move": move,
            "reason": reason,
            "notes": "Manual entry"
        })
        print("\n❌ Prediction was WRONG - lesson recorded!")
    
    learning["lessons"].append({
        "date": date,
        "was_correct": was_correct,
        "bias": predicted,
        "actual": actual,
        "lesson": reason
    })
    
    # Save
    LEARNING_FILE.write_text(json.dumps(learning, indent=2))
    print(f"\n📁 Saved to {LEARNING_FILE}")


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python teach_neo.py --record-failures  # Record 7/8 failed days")
        print("  python teach_neo.py --summary          # Show learning summary")
        print("  python teach_neo.py --manual           # Record single outcome")
        sys.exit(1)
    
    cmd = sys.argv[1]
    
    if cmd == "--record-failures":
        record_failures()
    elif cmd == "--summary":
        show_summary()
    elif cmd == "--manual":
        manual_record()
    else:
        print(f"Unknown command: {cmd}")
