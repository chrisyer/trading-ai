#!/usr/bin/env python3
"""
NEO LEARNER - Team B Intelligence System
==========================================
Learns from every trade outcome to improve signal accuracy.
Competing against Team A (Ronin's Oracle).

Author: Quinn
Version: 1.0.0
"""

import json
import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import math

# ══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ══════════════════════════════════════════════════════════════════════════════

DATA_DIR = Path("/home/jbot/trading_ai/data/neo")
SIGNALS_DIR = DATA_DIR / "signals"
OUTCOMES_DIR = DATA_DIR / "outcomes"
WEIGHTS_FILE = DATA_DIR / "weights/current.json"
PATTERNS_DB = DATA_DIR / "patterns/library.db"
METRICS_DAILY_DIR = DATA_DIR / "metrics/daily"
METRICS_WEEKLY_DIR = DATA_DIR / "metrics/weekly"

# Ensure directories exist
for d in [SIGNALS_DIR, OUTCOMES_DIR, WEIGHTS_FILE.parent, PATTERNS_DB.parent, 
          METRICS_DAILY_DIR, METRICS_WEEKLY_DIR]:
    d.mkdir(parents=True, exist_ok=True)


# ══════════════════════════════════════════════════════════════════════════════
# NEO LEARNER CLASS
# ══════════════════════════════════════════════════════════════════════════════

class NeoLearner:
    """
    NEO's learning brain - improves signals based on trade outcomes.
    """
    
    def __init__(self):
        # Load or initialize confidence weights
        self.confidence_weights = self._load_weights()
        
        # Initialize pattern database
        self._init_pattern_db()
        
        # Cache for recent signals (for outcome matching)
        self.recent_signals: Dict[str, dict] = {}
        
        # Today's metrics
        self.today = datetime.now().strftime("%Y-%m-%d")
        self.daily_metrics = self._load_daily_metrics()
        
        print(f"NEO Learner initialized")
        print(f"  Weights: {self.confidence_weights}")
        print(f"  Today's trades: {self.daily_metrics.get('signals_generated', 0)}")
    
    def _load_weights(self) -> Dict[str, float]:
        """Load confidence weights from file or use defaults"""
        default_weights = {
            'adx_strong': 1.0,          # ADX > 25
            'di_divergence': 1.0,       # |+DI - -DI| > 10
            'rsi_momentum': 1.0,        # RSI in optimal zone
            'h4_alignment': 1.2,        # Higher timeframe trend alignment
            'ema_trend': 1.0,           # Price above EMA50
            'ema_crossover': 1.0,       # EMA20 > EMA50
            'support_bounce': 1.0,      # Price near support
            'resistance_rejection': 1.0, # Price rejected at resistance
            'volume_confirm': 1.0,      # Volume confirmation
            'momentum_divergence': 1.0  # RSI/price divergence
        }
        
        try:
            if WEIGHTS_FILE.exists():
                data = json.loads(WEIGHTS_FILE.read_text())
                # Merge with defaults (in case new features added)
                for k, v in default_weights.items():
                    if k not in data:
                        data[k] = v
                return data
        except Exception as e:
            print(f"Error loading weights: {e}")
        
        return default_weights
    
    def _save_weights(self):
        """Save current weights to file"""
        WEIGHTS_FILE.write_text(json.dumps(self.confidence_weights, indent=2))
    
    def _init_pattern_db(self):
        """Initialize SQLite pattern database"""
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
    
    def _load_daily_metrics(self) -> dict:
        """Load today's metrics"""
        metrics_file = METRICS_DAILY_DIR / f"{self.today}.json"
        
        default = {
            "date": self.today,
            "signals_generated": 0,
            "signals_executed": 0,
            "wins": 0,
            "losses": 0,
            "total_pnl": 0.0,
            "avg_win_confidence": 0.0,
            "avg_loss_confidence": 0.0,
            "win_confidences": [],
            "loss_confidences": []
        }
        
        try:
            if metrics_file.exists():
                return json.loads(metrics_file.read_text())
        except:
            pass
        
        return default
    
    def _save_daily_metrics(self):
        """Save today's metrics"""
        metrics_file = METRICS_DAILY_DIR / f"{self.today}.json"
        
        # Calculate averages
        if self.daily_metrics.get("win_confidences"):
            self.daily_metrics["avg_win_confidence"] = sum(
                self.daily_metrics["win_confidences"]) / len(self.daily_metrics["win_confidences"])
        if self.daily_metrics.get("loss_confidences"):
            self.daily_metrics["avg_loss_confidence"] = sum(
                self.daily_metrics["loss_confidences"]) / len(self.daily_metrics["loss_confidences"])
        
        # Win rate
        total_trades = self.daily_metrics.get("wins", 0) + self.daily_metrics.get("losses", 0)
        if total_trades > 0:
            self.daily_metrics["win_rate"] = self.daily_metrics["wins"] / total_trades
        else:
            self.daily_metrics["win_rate"] = 0
        
        metrics_file.write_text(json.dumps(self.daily_metrics, indent=2))
    
    # ══════════════════════════════════════════════════════════════════════════
    # SHOOTING STAR PATTERN DETECTION (Research Task #001)
    # ══════════════════════════════════════════════════════════════════════════
    # 
    # Backtest Results (30 days, M15):
    #   - 33 patterns found, 66.7% overall win rate
    #   - With 1 red candle confirmation: 100% win rate (12/12)
    #   - With 2 back-to-back reds: 100% win rate, avg drop $20.5
    #
    # Tiered Confidence:
    #   - Shooting star alone: 47.6% win (LOW)
    #   - SS + 1 red candle: 100% win (HIGH)
    #   - SS + 2 red candles: 100% win, larger moves (VERY HIGH)
    # ══════════════════════════════════════════════════════════════════════════
    
    def detect_shooting_star(self, candle: dict, prev_candles: List[dict] = None) -> dict:
        """
        Detect shooting star pattern from candle data.
        
        Returns:
            {
                'detected': bool,
                'confirmed': int (0=none, 1=one red, 2=back-to-back reds),
                'confidence_tier': str ('NONE', 'LOW', 'HIGH', 'VERY_HIGH'),
                'confidence_boost': float
            }
        """
        result = {
            'detected': False,
            'confirmed': 0,
            'confidence_tier': 'NONE',
            'confidence_boost': 1.0,
            'sell_signal': False
        }
        
        # Need OHLC data
        open_price = candle.get('open', 0)
        high = candle.get('high', 0)
        low = candle.get('low', 0)
        close = candle.get('close', 0)
        
        if not all([open_price, high, low, close]):
            return result
        
        body = abs(close - open_price)
        upper_wick = high - max(open_price, close)
        lower_wick = min(open_price, close) - low
        total_range = high - low
        
        if total_range == 0 or body == 0:
            return result
        
        # Shooting star criteria:
        # 1. Small body (<=30% of range)
        # 2. Long upper wick (>=2x body)
        # 3. Short lower wick (<=10% of range)
        small_body = body <= total_range * 0.3
        long_upper = upper_wick >= body * 2
        short_lower = lower_wick <= total_range * 0.1
        
        # Check if after uptrend (price above recent average)
        after_uptrend = True
        if prev_candles and len(prev_candles) >= 5:
            recent_avg = sum(c.get('close', 0) for c in prev_candles[-5:]) / 5
            after_uptrend = close > recent_avg * 0.998  # Small tolerance
        
        if small_body and long_upper and short_lower and after_uptrend:
            result['detected'] = True
            result['confidence_tier'] = 'LOW'  # 47.6% win rate unconfirmed
            result['confidence_boost'] = 0.8   # Actually reduces buy confidence
            result['sell_signal'] = True
            
            # Check for red candle confirmation
            if prev_candles and len(prev_candles) >= 1:
                # The "prev_candles" here should be candles AFTER the shooting star
                # Let's check the market_data for confirmation signals
                pass
        
        return result
    
    def analyze_shooting_star_confirmation(self, market_data: dict) -> dict:
        """
        Analyze shooting star with confirmation from subsequent candles.
        
        Expected market_data fields:
            - shooting_star: bool (pattern detected)
            - candle_1_red: bool (1st candle after SS was red)
            - candle_2_red: bool (2nd candle after SS was red)
            OR
            - recent_candles: list of OHLC dicts
        """
        result = {
            'detected': market_data.get('shooting_star', False),
            'confirmed': 0,
            'confidence_tier': 'NONE',
            'confidence_boost': 1.0,
            'sell_signal': False,
            'reasoning': ''
        }
        
        if not result['detected']:
            return result
        
        result['sell_signal'] = True
        result['confidence_tier'] = 'LOW'
        result['confidence_boost'] = 0.8  # Reduce BUY confidence
        result['reasoning'] = 'Shooting star detected (47.6% win unconfirmed)'
        
        # Check confirmation level
        candle_1_red = market_data.get('candle_1_red', False)
        candle_2_red = market_data.get('candle_2_red', False)
        
        # Alternative: check from recent_candles array
        recent_candles = market_data.get('recent_candles', [])
        if recent_candles and len(recent_candles) >= 2:
            # Candles should be in chronological order (oldest first)
            # Last candle is current, second-to-last is candle_1, etc.
            if len(recent_candles) >= 2:
                c1 = recent_candles[-1]  # Most recent
                candle_1_red = c1.get('close', 0) < c1.get('open', 0)
            if len(recent_candles) >= 3:
                c2 = recent_candles[-2]  # Second most recent
                candle_2_red = c2.get('close', 0) < c2.get('open', 0)
        
        if candle_1_red:
            result['confirmed'] = 1
            result['confidence_tier'] = 'HIGH'
            result['confidence_boost'] = 1.5  # 100% win rate
            result['reasoning'] = 'Shooting star + red candle confirmation (100% win rate)'
            
            if candle_2_red:
                result['confirmed'] = 2
                result['confidence_tier'] = 'VERY_HIGH'
                result['confidence_boost'] = 1.8  # 100% win, larger avg moves ($20+)
                result['reasoning'] = 'Shooting star + back-to-back reds (100% win, avg $20+ drop)'
        
        return result
    
    # ══════════════════════════════════════════════════════════════════════════
    # FEATURE EXTRACTION
    # ══════════════════════════════════════════════════════════════════════════
    
    def extract_features(self, market_data: dict) -> Dict[str, bool]:
        """Extract feature flags from market data"""
        
        adx = market_data.get('adx', 0)
        plus_di = market_data.get('plus_di', 0)
        minus_di = market_data.get('minus_di', 0)
        rsi = market_data.get('rsi', 50)
        price = market_data.get('price', 0)
        ema20 = market_data.get('ema20', 0)
        ema50 = market_data.get('ema50', 0)
        h4_trend = market_data.get('h4_trend', 'NEUTRAL')
        atr = market_data.get('atr', 0)
        
        # Detect current direction
        if plus_di > minus_di:
            direction = "BUY"
        else:
            direction = "SELL"
        
        # ════════════════════════════════════════════════════════════════════
        # SHOOTING STAR ANALYSIS (Research Task #001)
        # ════════════════════════════════════════════════════════════════════
        ss_analysis = self.analyze_shooting_star_confirmation(market_data)
        
        # Feature flags based on confirmation level
        shooting_star_confirmed_1 = ss_analysis['confirmed'] >= 1  # 1 red candle
        shooting_star_confirmed_2 = ss_analysis['confirmed'] >= 2  # 2 red candles
        shooting_star_unconfirmed = ss_analysis['detected'] and ss_analysis['confirmed'] == 0
        
        features = {
            'adx_strong': adx > 25,
            'di_divergence': abs(plus_di - minus_di) > 10,
            'rsi_momentum': 40 < rsi < 70,
            'rsi_overbought': rsi > 70,  # NEW: RSI overbought for SS context
            'h4_alignment': (h4_trend == "BULLISH" and direction == "BUY") or \
                           (h4_trend == "BEARISH" and direction == "SELL"),
            'ema_trend': price > ema50 if direction == "BUY" else price < ema50,
            'ema_crossover': ema20 > ema50 if direction == "BUY" else ema20 < ema50,
            'support_bounce': False,  # Would need support levels
            'resistance_rejection': False,  # Would need resistance levels
            'volume_confirm': True,  # Default true without volume data
            'momentum_divergence': False,  # Complex calculation
            
            # SHOOTING STAR FEATURES (Research Task #001)
            # Tiered by confirmation level
            'shooting_star_unconfirmed': shooting_star_unconfirmed,    # 47.6% win - caution
            'shooting_star_confirmed_1': shooting_star_confirmed_1,    # 100% win - strong SELL
            'shooting_star_confirmed_2': shooting_star_confirmed_2,    # 100% win, $20+ moves
        }
        
        # Store SS analysis for signal generation
        self._last_ss_analysis = ss_analysis
        
        return features
    
    def features_to_key(self, features: Dict[str, bool]) -> str:
        """Convert features dict to a sortable key for pattern matching"""
        active = sorted([k for k, v in features.items() if v])
        return "|".join(active)
    
    # ══════════════════════════════════════════════════════════════════════════
    # SIGNAL GENERATION
    # ══════════════════════════════════════════════════════════════════════════
    
    def generate_signal(self, market_data: dict) -> dict:
        """Generate a trading signal based on current market data"""
        
        # Extract features (also runs shooting star analysis)
        features = self.extract_features(market_data)
        
        # Get shooting star analysis (populated by extract_features)
        ss_analysis = getattr(self, '_last_ss_analysis', {
            'detected': False, 'confirmed': 0, 'confidence_tier': 'NONE',
            'confidence_boost': 1.0, 'sell_signal': False, 'reasoning': ''
        })
        
        # Calculate weighted confidence
        total_weight = sum(self.confidence_weights.values())
        active_weight = sum(
            self.confidence_weights.get(k, 1.0) for k, v in features.items() if v
        )
        
        base_confidence = active_weight / total_weight if total_weight > 0 else 0
        
        # Determine direction
        plus_di = market_data.get('plus_di', 0)
        minus_di = market_data.get('minus_di', 0)
        
        if plus_di > minus_di:
            direction = "BUY"
        elif minus_di > plus_di:
            direction = "SELL"
        else:
            direction = "HOLD"
        
        # ════════════════════════════════════════════════════════════════════
        # SHOOTING STAR OVERRIDE (Research Task #001)
        # ════════════════════════════════════════════════════════════════════
        # If shooting star with confirmation detected, it overrides direction
        shooting_star_override = None
        if ss_analysis['detected'] and ss_analysis['sell_signal']:
            if ss_analysis['confirmed'] >= 1:
                # Confirmed shooting star = STRONG SELL signal
                # Override any BUY direction
                if direction == "BUY":
                    shooting_star_override = "SELL"
                    direction = "SELL"
                    # Boost confidence for SELL based on confirmation level
                    base_confidence = min(0.95, base_confidence * ss_analysis['confidence_boost'])
                elif direction == "SELL":
                    # Already SELL, boost confidence
                    base_confidence = min(0.95, base_confidence * ss_analysis['confidence_boost'])
            elif ss_analysis['confirmed'] == 0:
                # Unconfirmed shooting star - just reduce BUY confidence
                if direction == "BUY":
                    base_confidence *= 0.7  # Reduce buy confidence
        
        # Check pattern library for similar setups
        similar_patterns = self.find_similar_patterns(features, direction)
        
        if len(similar_patterns) >= 3:
            wins = sum(1 for p in similar_patterns if p['outcome'] == 'WIN')
            historical_winrate = wins / len(similar_patterns)
            # Blend historical performance (40%) with current analysis (60%)
            confidence = (base_confidence * 0.6) + (historical_winrate * 0.4)
        else:
            confidence = base_confidence
        
        # Generate signal ID
        signal_id = f"NEO_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        
        # Build reasoning
        reasoning = self._build_reasoning(features, market_data)
        if ss_analysis['detected']:
            reasoning = f"{ss_analysis['reasoning']}; {reasoning}"
        
        # Adjust SL/TP for shooting star trades
        suggested_sl = self._calculate_sl(market_data)
        suggested_tp = self._calculate_tp(market_data)
        
        if ss_analysis['detected'] and ss_analysis['confirmed'] >= 1 and direction == "SELL":
            # Use researched SL/TP from backtest
            suggested_sl = 8.0   # $8 above pattern high
            suggested_tp = 11.2  # $11.2 target (70% of avg drop)
            if ss_analysis['confirmed'] >= 2:
                # Back-to-back reds have larger avg moves
                suggested_tp = 15.0  # Larger target for double confirmation
        
        # Build signal
        signal = {
            'signal_id': signal_id,
            'direction': direction,
            'confidence': round(confidence, 2),
            'reasoning': reasoning,
            'timestamp': datetime.now().isoformat(),
            'suggested_lots': self._calculate_lots(confidence),
            'suggested_sl': suggested_sl,
            'suggested_tp': suggested_tp,
            'features_active': features,
            'similar_patterns_count': len(similar_patterns),
            'weights_snapshot': dict(self.confidence_weights),
            
            # Shooting star details
            'shooting_star': {
                'detected': ss_analysis['detected'],
                'confirmed': ss_analysis['confirmed'],
                'tier': ss_analysis['confidence_tier'],
                'override_applied': shooting_star_override is not None
            }
        }
        
        # Cache signal for outcome matching
        self.recent_signals[signal_id] = {
            **signal,
            'market_context': market_data
        }
        
        # Save to daily signals file
        self._save_signal(signal)
        
        # Update daily metrics
        self.daily_metrics['signals_generated'] = self.daily_metrics.get('signals_generated', 0) + 1
        self._save_daily_metrics()
        
        return signal
    
    def _build_reasoning(self, features: Dict[str, bool], market_data: dict) -> str:
        """Build human-readable reasoning for the signal"""
        reasons = []
        
        # Shooting star patterns (highest priority)
        if features.get('shooting_star_confirmed_2'):
            reasons.append("🔥 SHOOTING STAR + 2 RED CANDLES (100% win rate, avg $20+ drop)")
        elif features.get('shooting_star_confirmed_1'):
            reasons.append("⚡ SHOOTING STAR + RED CANDLE (100% win rate)")
        elif features.get('shooting_star_unconfirmed'):
            reasons.append("⚠️ Shooting star detected (wait for red candle)")
        
        if features.get('rsi_overbought'):
            reasons.append(f"RSI overbought ({market_data.get('rsi', 0):.1f})")
        if features.get('adx_strong'):
            reasons.append(f"Strong trend (ADX={market_data.get('adx', 0):.1f})")
        if features.get('di_divergence'):
            di_diff = abs(market_data.get('plus_di', 0) - market_data.get('minus_di', 0))
            reasons.append(f"DI divergence ({di_diff:.1f})")
        if features.get('h4_alignment'):
            reasons.append(f"H4 aligned ({market_data.get('h4_trend', 'N/A')})")
        if features.get('ema_trend'):
            reasons.append("EMA trend")
        if features.get('rsi_momentum'):
            reasons.append(f"RSI momentum ({market_data.get('rsi', 0):.1f})")
        
        return "; ".join(reasons) if reasons else "Weak setup"
    
    def _calculate_lots(self, confidence: float) -> float:
        """Calculate suggested lot size based on confidence"""
        base_lots = 0.5
        if confidence >= 0.8:
            return round(base_lots * 1.5, 2)
        elif confidence >= 0.65:
            return round(base_lots * 1.2, 2)
        elif confidence >= 0.5:
            return round(base_lots, 2)
        else:
            return round(base_lots * 0.5, 2)
    
    def _calculate_sl(self, market_data: dict) -> float:
        """Calculate suggested stop loss in pips"""
        atr = market_data.get('atr', 15)
        return round(atr * 1.5, 1)
    
    def _calculate_tp(self, market_data: dict) -> float:
        """Calculate suggested take profit in pips"""
        atr = market_data.get('atr', 15)
        return round(atr * 2.5, 1)
    
    def _save_signal(self, signal: dict):
        """Save signal to daily file"""
        today = datetime.now().strftime("%Y-%m-%d")
        signals_file = SIGNALS_DIR / f"{today}.json"
        
        signals = []
        try:
            if signals_file.exists():
                signals = json.loads(signals_file.read_text())
        except:
            pass
        
        signals.append(signal)
        signals_file.write_text(json.dumps(signals, indent=2))
    
    # ══════════════════════════════════════════════════════════════════════════
    # LEARNING FROM OUTCOMES
    # ══════════════════════════════════════════════════════════════════════════
    
    def process_outcome(self, outcome_data: dict) -> dict:
        """
        Process a trade outcome and update learning.
        Called when v0202 reports a trade result.
        """
        
        signal_id = outcome_data.get('signal_id')
        outcome = outcome_data.get('outcome', 'LOSS')  # WIN or LOSS
        pnl = outcome_data.get('profit', 0)
        pips = outcome_data.get('pips', 0)
        
        is_win = outcome == 'WIN'
        
        # Find the original signal
        original_signal = self.recent_signals.get(signal_id)
        
        if not original_signal:
            # Try to load from file
            original_signal = self._find_signal_in_files(signal_id)
        
        if not original_signal:
            print(f"WARNING: Could not find original signal {signal_id}")
            return {"status": "signal_not_found", "signal_id": signal_id}
        
        # Get the market context and features
        market_context = original_signal.get('market_context', {})
        features = original_signal.get('features_active', {})
        
        if not features and market_context:
            features = self.extract_features(market_context)
        
        original_confidence = original_signal.get('confidence', 0.5)
        
        # ════════════════════════════════════════════════════════════════════
        # UPDATE CONFIDENCE WEIGHTS
        # ════════════════════════════════════════════════════════════════════
        
        learning_rate = 0.05
        
        for feature, was_active in features.items():
            if was_active and feature in self.confidence_weights:
                if is_win:
                    # Boost this feature's weight
                    self.confidence_weights[feature] *= (1 + learning_rate)
                else:
                    # Reduce this feature's weight (2x penalty for losses)
                    self.confidence_weights[feature] *= (1 - learning_rate * 2)
                
                # Clamp between 0.3 and 2.5
                self.confidence_weights[feature] = max(0.3, min(2.5, 
                    self.confidence_weights[feature]))
        
        # Save updated weights
        self._save_weights()
        
        # ════════════════════════════════════════════════════════════════════
        # STORE PATTERN IN DATABASE
        # ════════════════════════════════════════════════════════════════════
        
        self._store_pattern(
            features=features,
            direction=original_signal.get('direction', 'UNKNOWN'),
            outcome=outcome,
            pnl=pnl,
            pips=pips,
            signal_id=signal_id,
            market_context=market_context
        )
        
        # ════════════════════════════════════════════════════════════════════
        # UPDATE DAILY METRICS
        # ════════════════════════════════════════════════════════════════════
        
        if is_win:
            self.daily_metrics['wins'] = self.daily_metrics.get('wins', 0) + 1
            self.daily_metrics.setdefault('win_confidences', []).append(original_confidence)
        else:
            self.daily_metrics['losses'] = self.daily_metrics.get('losses', 0) + 1
            self.daily_metrics.setdefault('loss_confidences', []).append(original_confidence)
        
        self.daily_metrics['total_pnl'] = self.daily_metrics.get('total_pnl', 0) + pnl
        self.daily_metrics['signals_executed'] = self.daily_metrics.get('signals_executed', 0) + 1
        
        self._save_daily_metrics()
        
        # ════════════════════════════════════════════════════════════════════
        # SAVE OUTCOME
        # ════════════════════════════════════════════════════════════════════
        
        self._save_outcome(outcome_data, original_signal)
        
        # Log learning
        print(f"NEO LEARNED: {signal_id} -> {outcome} (PnL: ${pnl:.2f})")
        print(f"  Features active: {[k for k,v in features.items() if v]}")
        print(f"  Updated weights: {self.confidence_weights}")
        
        return {
            "status": "learned",
            "signal_id": signal_id,
            "outcome": outcome,
            "pnl": pnl,
            "weights_updated": dict(self.confidence_weights),
            "daily_stats": {
                "wins": self.daily_metrics.get('wins', 0),
                "losses": self.daily_metrics.get('losses', 0),
                "total_pnl": self.daily_metrics.get('total_pnl', 0)
            }
        }
    
    def _store_pattern(self, features: dict, direction: str, outcome: str, 
                       pnl: float, pips: float, signal_id: str, market_context: dict):
        """Store pattern in SQLite database"""
        
        conn = sqlite3.connect(str(PATTERNS_DB))
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO patterns (timestamp, features, direction, outcome, pnl, pips, signal_id, market_context)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            datetime.now().isoformat(),
            self.features_to_key(features),
            direction,
            outcome,
            pnl,
            pips,
            signal_id,
            json.dumps(market_context)
        ))
        
        conn.commit()
        conn.close()
    
    def _save_outcome(self, outcome_data: dict, original_signal: dict):
        """Save outcome to daily file"""
        today = datetime.now().strftime("%Y-%m-%d")
        outcomes_file = OUTCOMES_DIR / f"{today}.json"
        
        outcomes = []
        try:
            if outcomes_file.exists():
                outcomes = json.loads(outcomes_file.read_text())
        except:
            pass
        
        outcomes.append({
            **outcome_data,
            'original_signal': original_signal,
            'processed_at': datetime.now().isoformat()
        })
        
        outcomes_file.write_text(json.dumps(outcomes, indent=2))
    
    def _find_signal_in_files(self, signal_id: str) -> Optional[dict]:
        """Search for a signal in recent files"""
        # Check last 7 days
        for i in range(7):
            date = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
            signals_file = SIGNALS_DIR / f"{date}.json"
            
            try:
                if signals_file.exists():
                    signals = json.loads(signals_file.read_text())
                    for s in signals:
                        if s.get('signal_id') == signal_id:
                            return s
            except:
                continue
        
        return None
    
    # ══════════════════════════════════════════════════════════════════════════
    # PATTERN MATCHING
    # ══════════════════════════════════════════════════════════════════════════
    
    def find_similar_patterns(self, features: Dict[str, bool], direction: str, 
                              min_matches: int = 3) -> List[dict]:
        """Find similar historical patterns"""
        
        conn = sqlite3.connect(str(PATTERNS_DB))
        cursor = conn.cursor()
        
        # Get current feature key
        current_key = self.features_to_key(features)
        active_features = [k for k, v in features.items() if v]
        
        # Search for patterns with similar features
        # We want at least 60% feature overlap
        results = []
        
        cursor.execute('''
            SELECT features, direction, outcome, pnl, pips, market_context
            FROM patterns
            WHERE direction = ?
            ORDER BY timestamp DESC
            LIMIT 100
        ''', (direction,))
        
        for row in cursor.fetchall():
            stored_features = set(row[0].split('|')) if row[0] else set()
            current_features = set(active_features)
            
            if len(current_features) > 0:
                overlap = len(stored_features & current_features) / len(current_features)
                if overlap >= 0.6:  # 60% feature match
                    results.append({
                        'features': row[0],
                        'direction': row[1],
                        'outcome': row[2],
                        'pnl': row[3],
                        'pips': row[4]
                    })
        
        conn.close()
        return results[:20]  # Return top 20 matches
    
    # ══════════════════════════════════════════════════════════════════════════
    # DAILY PLAN
    # ══════════════════════════════════════════════════════════════════════════
    
    def generate_daily_plan(self, market_data: dict) -> dict:
        """Generate daily trading plan based on current conditions"""
        
        h4_trend = market_data.get('h4_trend', 'NEUTRAL')
        adx = market_data.get('adx', 0)
        price = market_data.get('price', 0)
        
        # Determine strategy based on conditions
        if adx > 30:
            strategy = "TREND_FOLLOW"
        elif adx > 20:
            strategy = "MOMENTUM"
        else:
            strategy = "RANGE_TRADE"
        
        # Determine direction
        if h4_trend == "BULLISH":
            direction = "BULLISH"
        elif h4_trend == "BEARISH":
            direction = "BEARISH"
        else:
            direction = "NEUTRAL"
        
        # Calculate key levels (simplified)
        support = [
            round(price - 50, 0),
            round(price - 100, 0),
            round(price - 150, 0)
        ]
        resistance = [
            round(price + 50, 0),
            round(price + 100, 0),
            round(price + 150, 0)
        ]
        
        # Risk mode based on recent performance
        total_trades = self.daily_metrics.get('wins', 0) + self.daily_metrics.get('losses', 0)
        if total_trades >= 5:
            win_rate = self.daily_metrics.get('wins', 0) / total_trades
            if win_rate < 0.4:
                risk_mode = "CONSERVATIVE"
            elif win_rate > 0.7:
                risk_mode = "AGGRESSIVE"
            else:
                risk_mode = "NORMAL"
        else:
            risk_mode = "NORMAL"
        
        plan = {
            "date": datetime.now().strftime("%Y-%m-%d"),
            "strategy": strategy,
            "direction": direction,
            "key_levels": {
                "support": support,
                "resistance": resistance
            },
            "risk_mode": risk_mode,
            "notes": self._generate_plan_notes(strategy, direction, market_data),
            "weight_highlights": self._get_top_weights()
        }
        
        return plan
    
    def _generate_plan_notes(self, strategy: str, direction: str, market_data: dict) -> str:
        """Generate notes for daily plan"""
        adx = market_data.get('adx', 0)
        
        if strategy == "TREND_FOLLOW":
            if direction == "BULLISH":
                return f"Strong bullish trend (ADX={adx:.1f}), buy dips near support"
            else:
                return f"Strong bearish trend (ADX={adx:.1f}), sell rallies near resistance"
        elif strategy == "MOMENTUM":
            return f"Moderate trend (ADX={adx:.1f}), trade breakouts with confirmation"
        else:
            return f"Range conditions (ADX={adx:.1f}), trade bounces at extremes"
    
    def _get_top_weights(self) -> dict:
        """Get top performing features by weight"""
        sorted_weights = sorted(
            self.confidence_weights.items(),
            key=lambda x: x[1],
            reverse=True
        )
        return {k: round(v, 3) for k, v in sorted_weights[:5]}
    
    # ══════════════════════════════════════════════════════════════════════════
    # TEAM COMPETITION
    # ══════════════════════════════════════════════════════════════════════════
    
    def get_scoreboard(self) -> dict:
        """Get current competition scoreboard"""
        
        total_trades = self.daily_metrics.get('wins', 0) + self.daily_metrics.get('losses', 0)
        win_rate = (self.daily_metrics.get('wins', 0) / total_trades * 100) if total_trades > 0 else 0
        
        team_b = {
            "team": "B (NEO)",
            "trades": total_trades,
            "wins": self.daily_metrics.get('wins', 0),
            "win_rate": round(win_rate, 1),
            "pnl": round(self.daily_metrics.get('total_pnl', 0), 2)
        }
        
        # Try to read Team A from scoreboard file
        team_a = {"team": "A (Oracle)", "trades": 0, "wins": 0, "win_rate": 0, "pnl": 0}
        scoreboard_file = Path("/home/jbot/.wine/drive_c/Program Files/MetaTrader 5/MQL5/Files/team_scoreboard.txt")
        
        try:
            if scoreboard_file.exists():
                content = scoreboard_file.read_text()
                for line in content.strip().split('\n'):
                    if line.startswith('TEAM_A='):
                        parts = line.split('=')[1].split('|')
                        if len(parts) >= 4:
                            team_a = {
                                "team": "A (Oracle)",
                                "trades": int(parts[0]),
                                "wins": int(parts[1]),
                                "win_rate": float(parts[2]),
                                "pnl": float(parts[3])
                            }
        except:
            pass
        
        return {
            "date": self.today,
            "team_a": team_a,
            "team_b": team_b,
            "leader": "B" if team_b["win_rate"] > team_a["win_rate"] else "A"
        }


# ══════════════════════════════════════════════════════════════════════════════
# SINGLETON INSTANCE
# ══════════════════════════════════════════════════════════════════════════════

_learner_instance = None

def get_learner() -> NeoLearner:
    """Get singleton instance of NeoLearner"""
    global _learner_instance
    if _learner_instance is None:
        _learner_instance = NeoLearner()
    return _learner_instance


if __name__ == "__main__":
    # Test the learner
    learner = NeoLearner()
    
    # Test signal generation
    test_market = {
        "symbol": "XAUUSD",
        "timestamp": datetime.now().isoformat(),
        "price": 4938.50,
        "adx": 46.5,
        "plus_di": 35.6,
        "minus_di": 5.6,
        "rsi": 63.2,
        "atr": 18.4,
        "ema20": 4925.30,
        "ema50": 4910.15,
        "h4_trend": "BULLISH"
    }
    
    signal = learner.generate_signal(test_market)
    print("\nGenerated Signal:")
    print(json.dumps(signal, indent=2))
    
    # Test daily plan
    plan = learner.generate_daily_plan(test_market)
    print("\nDaily Plan:")
    print(json.dumps(plan, indent=2))
