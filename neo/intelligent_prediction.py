#!/usr/bin/env python3
"""
═══════════════════════════════════════════════════════════════════════════════
NEO INTELLIGENT PREDICTION SYSTEM
═══════════════════════════════════════════════════════════════════════════════

KEY PRINCIPLE: Always provide BOTH scenarios with invalidation levels.

When bearish thesis is DEBUNKED (price breaks above invalidation):
  → Automatically switch to bullish thesis
  → Learn WHY bearish failed

This creates adaptive predictions that learn from being wrong.

═══════════════════════════════════════════════════════════════════════════════
"""

import json
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from pathlib import Path
import logging
import yfinance as yf

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("NEO_INTEL")

DATA_DIR = Path("/home/jbot/trading_ai/data/neo")
LEARNING_FILE = DATA_DIR / "prediction_learning.json"

# ═══════════════════════════════════════════════════════════════════════════════
# DUAL THESIS PREDICTION
# ═══════════════════════════════════════════════════════════════════════════════

class DualThesisPrediction:
    """
    Always provide BOTH scenarios. Never just one.
    
    BULLISH THESIS: What conditions say BUY
    BEARISH THESIS: What conditions say SELL
    INVALIDATION: When to switch from one to the other
    """
    
    def __init__(self):
        self.learning_data = self._load_learning()
        
    def _load_learning(self) -> Dict:
        """Load historical learning data"""
        if LEARNING_FILE.exists():
            return json.loads(LEARNING_FILE.read_text())
        return {
            "total_predictions": 0,
            "bullish_correct": 0,
            "bearish_correct": 0,
            "bullish_debunked": 0,  # Bullish thesis failed
            "bearish_debunked": 0,  # Bearish thesis failed
            "debunk_reasons": [],   # WHY predictions failed
            "lessons": []
        }
    
    def _save_learning(self):
        """Save learning data"""
        LEARNING_FILE.write_text(json.dumps(self.learning_data, indent=2))
    
    def _fetch_data(self, symbol: str = "XAUUSD", period: str = "30d") -> pd.DataFrame:
        """Fetch price data"""
        try:
            ticker_map = {"XAUUSD": "GC=F", "IREN": "IREN"}
            ticker = ticker_map.get(symbol, symbol)
            df = yf.Ticker(ticker).history(period=period, interval="1h")
            if not df.empty:
                df.columns = [c.lower() for c in df.columns]
            return df
        except Exception as e:
            logger.error(f"Failed to fetch data: {e}")
            return pd.DataFrame()
    
    def _calculate_indicators(self, df: pd.DataFrame) -> Dict:
        """Calculate technical indicators"""
        if df.empty or len(df) < 20:
            return {}
        
        close = df['close']
        high = df['high']
        low = df['low']
        
        # EMAs
        ema20 = close.ewm(span=20).mean()
        ema50 = close.ewm(span=50).mean()
        
        # RSI
        delta = close.diff()
        gain = (delta.where(delta > 0, 0)).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        
        # ATR
        tr = pd.concat([
            high - low,
            (high - close.shift()).abs(),
            (low - close.shift()).abs()
        ], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()
        
        # Recent price action
        recent_high = high.tail(20).max()
        recent_low = low.tail(20).min()
        current = close.iloc[-1]
        
        # Trend strength
        above_ema20 = current > ema20.iloc[-1]
        above_ema50 = current > ema50.iloc[-1]
        ema20_rising = ema20.iloc[-1] > ema20.iloc[-5]
        
        # Higher highs/lows (bullish structure)
        highs = high.tail(10)
        lows = low.tail(10)
        higher_highs = highs.iloc[-1] > highs.iloc[0]
        higher_lows = lows.iloc[-1] > lows.iloc[0]
        
        return {
            "current_price": float(current),
            "ema20": float(ema20.iloc[-1]),
            "ema50": float(ema50.iloc[-1]),
            "rsi": float(rsi.iloc[-1]),
            "atr": float(atr.iloc[-1]),
            "recent_high": float(recent_high),
            "recent_low": float(recent_low),
            "above_ema20": bool(above_ema20),
            "above_ema50": bool(above_ema50),
            "ema20_rising": bool(ema20_rising),
            "higher_highs": bool(higher_highs),
            "higher_lows": bool(higher_lows),
            "trend_structure": "BULLISH" if (higher_highs and higher_lows) else "BEARISH" if (not higher_highs and not higher_lows) else "MIXED"
        }
    
    def generate_dual_prediction(self, symbol: str = "XAUUSD") -> Dict:
        """
        Generate prediction with BOTH bullish and bearish scenarios.
        
        Returns:
        {
            "bullish_thesis": { ... },
            "bearish_thesis": { ... },
            "current_bias": "BULLISH" or "BEARISH",
            "bias_confidence": 0-100,
            "invalidation": { ... }
        }
        """
        
        df = self._fetch_data(symbol)
        if df.empty:
            return {"error": "Could not fetch data"}
        
        ind = self._calculate_indicators(df)
        if not ind:
            return {"error": "Not enough data for analysis"}
        
        price = ind["current_price"]
        atr = ind["atr"]
        
        # Count bullish vs bearish factors
        bullish_factors = []
        bearish_factors = []
        
        # 1. EMA Position
        if ind["above_ema20"] and ind["above_ema50"]:
            bullish_factors.append("Price above both EMAs")
        elif not ind["above_ema20"] and not ind["above_ema50"]:
            bearish_factors.append("Price below both EMAs")
        else:
            bullish_factors.append("Price between EMAs (watch for breakout)")
        
        # 2. EMA Direction
        if ind["ema20_rising"]:
            bullish_factors.append("EMA20 rising (momentum bullish)")
        else:
            bearish_factors.append("EMA20 falling (momentum bearish)")
        
        # 3. RSI
        if ind["rsi"] < 35:
            bullish_factors.append(f"RSI oversold ({ind['rsi']:.0f}) - bounce likely")
        elif ind["rsi"] > 65:
            bearish_factors.append(f"RSI overbought ({ind['rsi']:.0f}) - pullback possible")
        else:
            if ind["rsi"] > 50:
                bullish_factors.append(f"RSI bullish zone ({ind['rsi']:.0f})")
            else:
                bearish_factors.append(f"RSI bearish zone ({ind['rsi']:.0f})")
        
        # 4. Market Structure
        if ind["trend_structure"] == "BULLISH":
            bullish_factors.append("Higher highs + higher lows (bullish structure)")
        elif ind["trend_structure"] == "BEARISH":
            bearish_factors.append("Lower highs + lower lows (bearish structure)")
        
        # 5. Historical learning - what has worked recently?
        if self.learning_data["bullish_correct"] > self.learning_data["bearish_correct"]:
            bullish_factors.append(f"Historical: Bullish calls {self.learning_data['bullish_correct']}/{self.learning_data['total_predictions']} correct")
        elif self.learning_data["bearish_correct"] > self.learning_data["bullish_correct"]:
            bearish_factors.append(f"Historical: Bearish calls {self.learning_data['bearish_correct']}/{self.learning_data['total_predictions']} correct")
        
        # ═══════════════════════════════════════════════════════════════════════
        # BUILD DUAL THESIS
        # ═══════════════════════════════════════════════════════════════════════
        
        # BULLISH THESIS
        bullish_entry = max(price - atr * 0.5, ind["recent_low"] + atr * 0.3)  # Buy near support
        bullish_sl = bullish_entry - atr * 1.5
        bullish_tp = bullish_entry + atr * 3.0  # 2:1 R:R minimum
        bullish_invalidation = ind["recent_low"] - atr * 0.5  # Below recent low = thesis invalid
        
        bullish_thesis = {
            "direction": "BUY",
            "confidence": min(95, len(bullish_factors) * 15 + 40),
            "reasons": bullish_factors,
            "entry_zone": f"${bullish_entry:.0f} - ${price:.0f}",
            "stop_loss": f"${bullish_sl:.0f}",
            "take_profit": f"${bullish_tp:.0f}",
            "invalidation_level": bullish_invalidation,
            "invalidation_trigger": f"ABORT BUY if price closes below ${bullish_invalidation:.0f}"
        }
        
        # BEARISH THESIS
        bearish_entry = min(price + atr * 0.5, ind["recent_high"] - atr * 0.3)  # Sell near resistance
        bearish_sl = bearish_entry + atr * 1.5
        bearish_tp = bearish_entry - atr * 3.0
        bearish_invalidation = ind["recent_high"] + atr * 0.5  # Above recent high = thesis invalid
        
        bearish_thesis = {
            "direction": "SELL",
            "confidence": min(95, len(bearish_factors) * 15 + 40),
            "reasons": bearish_factors,
            "entry_zone": f"${price:.0f} - ${bearish_entry:.0f}",
            "stop_loss": f"${bearish_sl:.0f}",
            "take_profit": f"${bearish_tp:.0f}",
            "invalidation_level": bearish_invalidation,
            "invalidation_trigger": f"ABORT SELL if price closes above ${bearish_invalidation:.0f}"
        }
        
        # Determine current bias (but provide BOTH)
        if len(bullish_factors) > len(bearish_factors):
            current_bias = "BULLISH"
            bias_confidence = bullish_thesis["confidence"]
            primary = bullish_thesis
            secondary = bearish_thesis
        elif len(bearish_factors) > len(bullish_factors):
            current_bias = "BEARISH"
            bias_confidence = bearish_thesis["confidence"]
            primary = bearish_thesis
            secondary = bullish_thesis
        else:
            current_bias = "NEUTRAL"
            bias_confidence = 50
            primary = bullish_thesis  # Default to bullish in uptrend
            secondary = bearish_thesis
        
        # ═══════════════════════════════════════════════════════════════════════
        # KEY: WHAT DEBUNKS EACH THESIS?
        # ═══════════════════════════════════════════════════════════════════════
        
        invalidation = {
            "bearish_debunked_if": [
                f"Price closes above ${bearish_invalidation:.0f} (recent high + ATR)",
                f"RSI drops below 30 then bounces (oversold reversal)",
                f"Bullish engulfing candle forms at support",
                f"Price reclaims EMA20 with volume"
            ],
            "bullish_debunked_if": [
                f"Price closes below ${bullish_invalidation:.0f} (recent low - ATR)",
                f"RSI rises above 70 then drops (overbought reversal)",
                f"Bearish engulfing candle forms at resistance",
                f"Price loses EMA50 with volume"
            ],
            "switch_protocol": "When primary thesis is debunked, IMMEDIATELY switch to secondary thesis"
        }
        
        # Record this prediction for learning
        prediction_id = f"{symbol}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
        
        result = {
            "prediction_id": prediction_id,
            "timestamp": datetime.utcnow().isoformat(),
            "symbol": symbol,
            "current_price": price,
            "indicators": ind,
            
            # BOTH THESES
            "bullish_thesis": bullish_thesis,
            "bearish_thesis": bearish_thesis,
            
            # Current recommendation
            "current_bias": current_bias,
            "bias_confidence": bias_confidence,
            "primary_thesis": primary,
            "secondary_thesis": secondary,
            
            # CRITICAL: When to switch
            "invalidation": invalidation,
            
            # Learning context
            "historical_accuracy": {
                "bullish_win_rate": f"{(self.learning_data['bullish_correct'] / max(1, self.learning_data['total_predictions']) * 100):.0f}%",
                "bearish_win_rate": f"{(self.learning_data['bearish_correct'] / max(1, self.learning_data['total_predictions']) * 100):.0f}%",
                "recent_debunks": self.learning_data.get("debunk_reasons", [])[-3:]
            }
        }
        
        # Save for later evaluation
        save_path = DATA_DIR / f"predictions/{prediction_id}.json"
        save_path.parent.mkdir(exist_ok=True)
        save_path.write_text(json.dumps(result, indent=2))
        
        return result
    
    def evaluate_prediction(self, prediction_id: str, actual_direction: str, 
                           price_at_evaluation: float, notes: str = "") -> Dict:
        """
        Evaluate a past prediction and LEARN from it.
        
        actual_direction: "UP" or "DOWN" - what actually happened
        """
        
        pred_file = DATA_DIR / f"predictions/{prediction_id}.json"
        if not pred_file.exists():
            return {"error": "Prediction not found"}
        
        pred = json.loads(pred_file.read_text())
        
        bias = pred.get("current_bias", "NEUTRAL")
        bullish_invalidation = pred["bullish_thesis"]["invalidation_level"]
        bearish_invalidation = pred["bearish_thesis"]["invalidation_level"]
        original_price = pred["current_price"]
        
        # What happened?
        price_move = price_at_evaluation - original_price
        move_pct = (price_move / original_price) * 100
        
        # Was the primary thesis correct?
        if bias == "BULLISH":
            was_correct = actual_direction == "UP"
            if not was_correct:
                # Bearish thesis was debunked - LEARN why
                debunk_reason = f"BEARISH prediction failed: Price went UP {move_pct:+.1f}% instead"
                if price_at_evaluation > bearish_invalidation:
                    debunk_reason += f" - INVALIDATION triggered at ${bearish_invalidation:.0f}"
                self.learning_data["bearish_debunked"] += 1
                self.learning_data["debunk_reasons"].append({
                    "date": datetime.utcnow().isoformat(),
                    "prediction_id": prediction_id,
                    "expected": "DOWN",
                    "actual": "UP",
                    "move_pct": move_pct,
                    "reason": debunk_reason,
                    "notes": notes
                })
            else:
                self.learning_data["bullish_correct"] += 1
                
        elif bias == "BEARISH":
            was_correct = actual_direction == "DOWN"
            if not was_correct:
                # Bullish thesis was debunked - LEARN why
                debunk_reason = f"BULLISH prediction failed: Price went DOWN {move_pct:+.1f}% instead"
                if price_at_evaluation < bullish_invalidation:
                    debunk_reason += f" - INVALIDATION triggered at ${bullish_invalidation:.0f}"
                self.learning_data["bullish_debunked"] += 1
                self.learning_data["debunk_reasons"].append({
                    "date": datetime.utcnow().isoformat(),
                    "prediction_id": prediction_id,
                    "expected": "UP",
                    "actual": "DOWN",
                    "move_pct": move_pct,
                    "reason": debunk_reason,
                    "notes": notes
                })
            else:
                self.learning_data["bearish_correct"] += 1
        else:
            was_correct = abs(move_pct) < 1  # Neutral was correct if no big move
        
        self.learning_data["total_predictions"] += 1
        
        # Add lesson
        lesson = {
            "date": datetime.utcnow().isoformat(),
            "was_correct": was_correct,
            "bias": bias,
            "actual": actual_direction,
            "move_pct": move_pct,
            "lesson": notes if notes else ("THESIS CORRECT" if was_correct else "THESIS WRONG - review invalidation levels")
        }
        self.learning_data["lessons"].append(lesson)
        
        # Keep only last 50 lessons
        self.learning_data["lessons"] = self.learning_data["lessons"][-50:]
        self.learning_data["debunk_reasons"] = self.learning_data["debunk_reasons"][-30:]
        
        self._save_learning()
        
        return {
            "prediction_id": prediction_id,
            "was_correct": was_correct,
            "bias": bias,
            "actual_direction": actual_direction,
            "move_pct": move_pct,
            "lesson_learned": lesson["lesson"],
            "updated_stats": {
                "total": self.learning_data["total_predictions"],
                "bullish_correct": self.learning_data["bullish_correct"],
                "bearish_correct": self.learning_data["bearish_correct"],
                "bullish_debunked": self.learning_data["bullish_debunked"],
                "bearish_debunked": self.learning_data["bearish_debunked"]
            }
        }
    
    def get_learning_summary(self) -> Dict:
        """Get summary of what NEO has learned"""
        total = max(1, self.learning_data["total_predictions"])
        
        return {
            "total_predictions": total,
            "bullish_accuracy": f"{(self.learning_data['bullish_correct'] / total * 100):.0f}%",
            "bearish_accuracy": f"{(self.learning_data['bearish_correct'] / total * 100):.0f}%",
            "bullish_debunked_count": self.learning_data["bullish_debunked"],
            "bearish_debunked_count": self.learning_data["bearish_debunked"],
            "recent_debunks": self.learning_data.get("debunk_reasons", [])[-5:],
            "recent_lessons": self.learning_data.get("lessons", [])[-5:],
            "recommendation": self._get_recommendation()
        }
    
    def _get_recommendation(self) -> str:
        """Based on learning, what should NEO favor?"""
        bullish_win = self.learning_data["bullish_correct"]
        bearish_win = self.learning_data["bearish_correct"]
        bullish_fail = self.learning_data["bullish_debunked"]
        bearish_fail = self.learning_data["bearish_debunked"]
        
        if bullish_win > bearish_win and bearish_fail > bullish_fail:
            return "FAVOR BULLISH - Bearish predictions have failed more often"
        elif bearish_win > bullish_win and bullish_fail > bearish_fail:
            return "FAVOR BEARISH - Bullish predictions have failed more often"
        elif bullish_win > 0 and bearish_win == 0:
            return "STRONGLY FAVOR BULLISH - No successful bearish calls yet"
        elif bearish_win > 0 and bullish_win == 0:
            return "STRONGLY FAVOR BEARISH - No successful bullish calls yet"
        else:
            return "NEUTRAL - Need more data to determine bias"
    
    def format_telegram_prediction(self, pred: Dict) -> str:
        """Format dual thesis for Telegram"""
        
        if "error" in pred:
            return f"⚠️ {pred['error']}"
        
        symbol = pred["symbol"]
        price = pred["current_price"]
        bias = pred["current_bias"]
        conf = pred["bias_confidence"]
        
        bullish = pred["bullish_thesis"]
        bearish = pred["bearish_thesis"]
        invalidation = pred["invalidation"]
        
        bias_emoji = "🐂" if bias == "BULLISH" else "🐻" if bias == "BEARISH" else "⚖️"
        
        lines = [
            f"🎯 <b>NEO DUAL PREDICTION - {symbol}</b>",
            f"📅 {datetime.utcnow().strftime('%A, %B %d')}",
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"📍 Price: ${price:.2f}",
            f"",
            f"{bias_emoji} <b>CURRENT BIAS: {bias}</b> ({conf}%)",
            f"",
            f"═══════════════════════════════",
            f"🐂 <b>BULLISH THESIS</b> ({bullish['confidence']}%)",
            f"═══════════════════════════════",
        ]
        
        for reason in bullish["reasons"]:
            lines.append(f"  ✓ {reason}")
        
        lines.extend([
            f"",
            f"  Entry: {bullish['entry_zone']}",
            f"  SL: {bullish['stop_loss']}",
            f"  TP: {bullish['take_profit']}",
            f"  <b>⚠️ ABORT IF:</b> {bullish['invalidation_trigger']}",
            f"",
            f"═══════════════════════════════",
            f"🐻 <b>BEARISH THESIS</b> ({bearish['confidence']}%)",
            f"═══════════════════════════════",
        ])
        
        for reason in bearish["reasons"]:
            lines.append(f"  ✗ {reason}")
        
        lines.extend([
            f"",
            f"  Entry: {bearish['entry_zone']}",
            f"  SL: {bearish['stop_loss']}",
            f"  TP: {bearish['take_profit']}",
            f"  <b>⚠️ ABORT IF:</b> {bearish['invalidation_trigger']}",
            f"",
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"🔄 <b>SWITCH PROTOCOL:</b>",
        ])
        
        lines.append(f"If BEARISH debunked:")
        for cond in invalidation["bearish_debunked_if"][:2]:
            lines.append(f"  → {cond}")
        lines.append(f"  <b>→ SWITCH TO BULLISH</b>")
        
        lines.append(f"")
        lines.append(f"If BULLISH debunked:")
        for cond in invalidation["bullish_debunked_if"][:2]:
            lines.append(f"  → {cond}")
        lines.append(f"  <b>→ SWITCH TO BEARISH</b>")
        
        # Add historical context
        hist = pred.get("historical_accuracy", {})
        if hist:
            lines.extend([
                f"",
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
                f"📊 <b>HISTORICAL:</b>",
                f"  Bullish win rate: {hist.get('bullish_win_rate', 'N/A')}",
                f"  Bearish win rate: {hist.get('bearish_win_rate', 'N/A')}",
            ])
            
            recent_debunks = hist.get("recent_debunks", [])
            if recent_debunks:
                lines.append(f"")
                lines.append(f"<b>⚠️ Recent failures:</b>")
                for debunk in recent_debunks[-2:]:
                    lines.append(f"  • {debunk.get('reason', 'Unknown')[:60]}...")
        
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    
    predictor = DualThesisPrediction()
    
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python intelligent_prediction.py predict [SYMBOL]")
        print("  python intelligent_prediction.py evaluate <PREDICTION_ID> <UP|DOWN> [notes]")
        print("  python intelligent_prediction.py summary")
        sys.exit(1)
    
    cmd = sys.argv[1].lower()
    
    if cmd == "predict":
        symbol = sys.argv[2] if len(sys.argv) > 2 else "XAUUSD"
        pred = predictor.generate_dual_prediction(symbol)
        
        # Print formatted
        import re
        msg = predictor.format_telegram_prediction(pred)
        print(re.sub(r'<[^>]+>', '', msg))
        
        print(f"\n📁 Saved as: {pred.get('prediction_id', 'N/A')}")
        
    elif cmd == "evaluate":
        if len(sys.argv) < 4:
            print("Usage: python intelligent_prediction.py evaluate <PREDICTION_ID> <UP|DOWN> [notes]")
            sys.exit(1)
        
        pred_id = sys.argv[2]
        direction = sys.argv[3].upper()
        notes = " ".join(sys.argv[4:]) if len(sys.argv) > 4 else ""
        
        # Need current price
        df = predictor._fetch_data("XAUUSD", "1d")
        current_price = float(df['close'].iloc[-1]) if not df.empty else 0
        
        result = predictor.evaluate_prediction(pred_id, direction, current_price, notes)
        print(json.dumps(result, indent=2))
        
    elif cmd == "summary":
        summary = predictor.get_learning_summary()
        print(json.dumps(summary, indent=2))
        
    else:
        print(f"Unknown command: {cmd}")
