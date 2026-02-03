#!/usr/bin/env python3
"""
GOLD HUNTER - OLLAMA AI BRAIN
=============================
The thinking part of the trading system.
MT5 executes. Ollama thinks.

Architecture:
  1. Observer: Reads market state from MT5
  2. Regime Classifier: Identifies market regime
  3. Risk Governor: Adjusts parameters based on conditions
  4. Sentiment Analyzer: Processes news/social
  5. Memory: Stores experiences for learning
"""

import json
import os
import time
import logging
import requests
from datetime import datetime, timedelta
from pathlib import Path
import sqlite3

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [BRAIN] %(levelname)s: %(message)s'
)
logger = logging.getLogger("OLLAMA_BRAIN")

# Configuration
OLLAMA_URL = "http://localhost:11434/api/generate"
REGIME_MODEL = "llama3.1:8b"      # Fast regime classification
RISK_MODEL = "qwen2.5:32b"        # Structured JSON output
SENTIMENT_MODEL = "mistral:latest" # Sentiment analysis

# File paths - MT5 common files folder or custom path
MT5_FILES = os.path.expanduser("~/.wine/drive_c/Program Files/MetaTrader 5/MQL5/Files")
STATE_FILE = os.path.join(MT5_FILES, "gold_state.json")
CONTROL_FILE = os.path.join(MT5_FILES, "gold_control.json")
TRADES_LOG = os.path.join(MT5_FILES, "gold_trades_log.jsonl")

# Local paths
MEMORY_DB = "/home/jbot/trading_ai/gold_hunter/memory.db"
EXPERIENCE_LOG = "/home/jbot/trading_ai/gold_hunter/experience.jsonl"

# Update intervals
STATE_CHECK_INTERVAL = 5  # seconds
REGIME_UPDATE_INTERVAL = 60  # seconds
SENTIMENT_UPDATE_INTERVAL = 300  # seconds


class MarketMemory:
    """SQLite-based experience storage for learning"""
    
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_db()
    
    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS experiences (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                regime TEXT,
                sentiment TEXT,
                sentiment_intensity INTEGER,
                risk_bias REAL,
                action TEXT,
                entry_price REAL,
                exit_price REAL,
                pnl REAL,
                duration_min INTEGER,
                buy_signals INTEGER,
                sell_signals INTEGER,
                atr REAL,
                rsi REAL,
                outcome TEXT
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS regime_accuracy (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                predicted_regime TEXT,
                actual_outcome TEXT,
                correct INTEGER
            )
        ''')
        
        conn.commit()
        conn.close()
    
    def store_experience(self, experience: dict):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO experiences (
                timestamp, regime, sentiment, sentiment_intensity,
                risk_bias, action, entry_price, exit_price, pnl,
                duration_min, buy_signals, sell_signals, atr, rsi, outcome
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            experience.get('timestamp'),
            experience.get('regime'),
            experience.get('sentiment'),
            experience.get('sentiment_intensity'),
            experience.get('risk_bias'),
            experience.get('action'),
            experience.get('entry_price'),
            experience.get('exit_price'),
            experience.get('pnl'),
            experience.get('duration_min'),
            experience.get('buy_signals'),
            experience.get('sell_signals'),
            experience.get('atr'),
            experience.get('rsi'),
            experience.get('outcome')
        ))
        
        conn.commit()
        conn.close()
    
    def get_similar_experiences(self, regime: str, limit: int = 5) -> list:
        """Get past experiences with similar regime for few-shot prompting"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT * FROM experiences 
            WHERE regime = ? 
            ORDER BY timestamp DESC 
            LIMIT ?
        ''', (regime, limit))
        
        columns = [desc[0] for desc in cursor.description]
        results = [dict(zip(columns, row)) for row in cursor.fetchall()]
        
        conn.close()
        return results
    
    def get_regime_performance(self) -> dict:
        """Get performance stats by regime"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT regime, 
                   COUNT(*) as trades,
                   SUM(pnl) as total_pnl,
                   AVG(pnl) as avg_pnl,
                   SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins
            FROM experiences 
            GROUP BY regime
        ''')
        
        results = {}
        for row in cursor.fetchall():
            regime, trades, total_pnl, avg_pnl, wins = row
            results[regime] = {
                'trades': trades,
                'total_pnl': total_pnl or 0,
                'avg_pnl': avg_pnl or 0,
                'win_rate': (wins / trades * 100) if trades > 0 else 0
            }
        
        conn.close()
        return results


class OllamaBrain:
    """The AI brain that thinks for MT5"""
    
    def __init__(self):
        self.memory = MarketMemory(MEMORY_DB)
        self.current_state = {}
        self.current_regime = "unknown"
        self.current_sentiment = "neutral"
        self.sentiment_intensity = 50
        self.last_regime_update = 0
        self.last_sentiment_update = 0
        
        logger.info("="*60)
        logger.info("🧠 OLLAMA AI BRAIN INITIALIZED")
        logger.info("="*60)
        logger.info(f"Regime Model: {REGIME_MODEL}")
        logger.info(f"Risk Model: {RISK_MODEL}")
        logger.info(f"Sentiment Model: {SENTIMENT_MODEL}")
        logger.info(f"State File: {STATE_FILE}")
        logger.info(f"Control File: {CONTROL_FILE}")
        logger.info("="*60)
    
    def read_market_state(self) -> dict:
        """Read current market state from MT5"""
        try:
            if not os.path.exists(STATE_FILE):
                return {}
            
            # Check if file was recently updated
            mtime = os.path.getmtime(STATE_FILE)
            if time.time() - mtime > 30:  # Stale data
                logger.warning("State file is stale (>30s old)")
                return {}
            
            with open(STATE_FILE, 'r') as f:
                state = json.load(f)
            
            self.current_state = state
            return state
            
        except Exception as e:
            logger.error(f"Error reading state: {e}")
            return {}
    
    def classify_regime(self, state: dict) -> str:
        """Use Ollama to classify current market regime"""
        
        if not state:
            return "unknown"
        
        # Get past experiences for context
        past_experiences = self.memory.get_similar_experiences(self.current_regime, limit=3)
        past_context = ""
        if past_experiences:
            past_context = "\nPast similar situations:\n"
            for exp in past_experiences:
                past_context += f"- {exp.get('regime')}: PnL={exp.get('pnl')}, Outcome={exp.get('outcome')}\n"
        
        prompt = f"""You are a market regime classifier for XAUUSD (Gold).

Current Market State:
- Price: ${state.get('price', 0):.2f}
- ATR: {state.get('atr', 0):.2f}
- RSI(14): {state.get('rsi', 50):.1f}
- RSI(2): {state.get('rsi2', 50):.1f}
- BB Width: {state.get('bb_width', 0):.1f}%
- BB Position: {state.get('bb_position', 50):.1f}%
- EMA Distance: {state.get('ema_distance', 0):.2f}%
- MACD Histogram: {state.get('macd_hist', 0):.4f}
- Trend: {state.get('trend', 'neutral')}
- Open Positions: {state.get('open_positions', 0)}
- Floating PnL: ${state.get('floating_pnl', 0):.2f}
{past_context}

Classify the regime as ONE of:
- trending_up: Strong uptrend, momentum buying
- trending_down: Strong downtrend, momentum selling  
- ranging: Sideways, mean reversion works
- expansion_spike: Volatility explosion, be cautious
- exhaustion_top: Potential reversal from top
- exhaustion_bottom: Potential reversal from bottom
- accumulation: Building positions, low volatility
- distribution: Exiting positions, increased selling

Output ONLY the regime name, nothing else."""

        try:
            response = requests.post(
                OLLAMA_URL,
                json={
                    "model": REGIME_MODEL,
                    "prompt": prompt,
                    "stream": False,
                    "options": {"temperature": 0.3, "num_predict": 20}
                },
                timeout=30
            )
            
            if response.status_code == 200:
                result = response.json()
                regime = result.get('response', '').strip().lower().replace(' ', '_')
                
                valid_regimes = [
                    'trending_up', 'trending_down', 'ranging',
                    'expansion_spike', 'exhaustion_top', 'exhaustion_bottom',
                    'accumulation', 'distribution'
                ]
                
                if regime in valid_regimes:
                    logger.info(f"📊 Regime classified: {regime}")
                    return regime
                else:
                    # Try to extract from response
                    for r in valid_regimes:
                        if r in regime:
                            return r
                    logger.warning(f"Invalid regime response: {regime}")
                    return "unknown"
            
        except Exception as e:
            logger.error(f"Regime classification failed: {e}")
        
        return "unknown"
    
    def analyze_sentiment(self) -> tuple:
        """Analyze market sentiment (simplified - would integrate news/social APIs)"""
        
        # In production, this would:
        # 1. Pull X/Twitter data for "gold", "XAUUSD", "rates"
        # 2. Check news headlines
        # 3. Check economic calendar
        
        # For now, derive from price action
        state = self.current_state
        if not state:
            return "neutral", 50
        
        rsi = state.get('rsi', 50)
        rsi2 = state.get('rsi2', 50)
        trend = state.get('trend', 'neutral')
        bb_pos = state.get('bb_position', 50)
        
        prompt = f"""Analyze gold market sentiment based on technicals:

- RSI(14): {rsi:.1f}
- RSI(2): {rsi2:.1f}  
- Trend: {trend}
- BB Position: {bb_pos:.1f}%
- Floating PnL: ${state.get('floating_pnl', 0):.2f}

Output ONLY in this exact format:
SENTIMENT INTENSITY

Where SENTIMENT is one of: bullish, bearish, panic_bullish, panic_bearish, neutral, fearful, greedy
And INTENSITY is 0-100

Example: bullish 72"""

        try:
            response = requests.post(
                OLLAMA_URL,
                json={
                    "model": SENTIMENT_MODEL,
                    "prompt": prompt,
                    "stream": False,
                    "options": {"temperature": 0.3, "num_predict": 20}
                },
                timeout=30
            )
            
            if response.status_code == 200:
                result = response.json()
                text = result.get('response', '').strip().lower()
                parts = text.split()
                
                if len(parts) >= 2:
                    sentiment = parts[0]
                    try:
                        intensity = int(parts[1])
                        intensity = max(0, min(100, intensity))
                        logger.info(f"💭 Sentiment: {sentiment} ({intensity}%)")
                        return sentiment, intensity
                    except:
                        pass
        
        except Exception as e:
            logger.error(f"Sentiment analysis failed: {e}")
        
        return "neutral", 50
    
    def calculate_risk_parameters(self, state: dict, regime: str, sentiment: str, intensity: int) -> dict:
        """Calculate risk parameters based on all inputs"""
        
        # Get regime performance history
        regime_stats = self.memory.get_regime_performance()
        regime_perf = regime_stats.get(regime, {})
        
        prompt = f"""You are a risk governor for a Gold trading bot.

Current Situation:
- Regime: {regime}
- Sentiment: {sentiment} (intensity: {intensity}%)
- RSI: {state.get('rsi', 50):.1f}
- RSI2: {state.get('rsi2', 50):.1f}
- ATR: {state.get('atr', 0):.2f}
- Open Positions: {state.get('open_positions', 0)}
- Floating PnL: ${state.get('floating_pnl', 0):.2f}
- Buy Signals: {state.get('buy_signals', 0)}
- Sell Signals: {state.get('sell_signals', 0)}

Historical Performance for this regime:
- Trades: {regime_perf.get('trades', 0)}
- Win Rate: {regime_perf.get('win_rate', 0):.1f}%
- Avg PnL: ${regime_perf.get('avg_pnl', 0):.2f}

Rules:
1. risk_bias: -1 (very cautious) to +1 (aggressive). Negative = reduce risk.
2. dca_multiplier: 0.5 to 2.0. Higher = wider DCA steps.
3. max_layers: 1 to 3. Max simultaneous positions.
4. min_signals: 2 to 6. Required confluence.
5. disable_new_entries: true if conditions are dangerous.
6. event_risk: low/medium/high.

Output ONLY valid JSON:
{{"risk_bias": 0.0, "dca_multiplier": 1.0, "max_layers": 2, "min_signals": 3, "disable_new_entries": false, "event_risk": "low"}}"""

        try:
            response = requests.post(
                OLLAMA_URL,
                json={
                    "model": RISK_MODEL,
                    "prompt": prompt,
                    "stream": False,
                    "options": {"temperature": 0.2, "num_predict": 100}
                },
                timeout=60
            )
            
            if response.status_code == 200:
                result = response.json()
                text = result.get('response', '').strip()
                
                # Extract JSON from response
                start = text.find('{')
                end = text.rfind('}') + 1
                if start >= 0 and end > start:
                    json_str = text[start:end]
                    params = json.loads(json_str)
                    
                    # Validate and clamp values
                    return {
                        'risk_bias': max(-1.0, min(1.0, params.get('risk_bias', 0))),
                        'dca_multiplier': max(0.5, min(2.0, params.get('dca_multiplier', 1.0))),
                        'max_layers': max(1, min(3, params.get('max_layers', 2))),
                        'min_signals': max(2, min(6, params.get('min_signals', 3))),
                        'disable_new_entries': bool(params.get('disable_new_entries', False)),
                        'event_risk': params.get('event_risk', 'low')
                    }
        
        except Exception as e:
            logger.error(f"Risk calculation failed: {e}")
        
        # Safe defaults
        return {
            'risk_bias': 0,
            'dca_multiplier': 1.0,
            'max_layers': 2,
            'min_signals': 3,
            'disable_new_entries': False,
            'event_risk': 'medium'
        }
    
    def write_control(self, params: dict):
        """Write control parameters for MT5 to read"""
        
        control = {
            'risk_bias': params.get('risk_bias', 0),
            'dca_multiplier': params.get('dca_multiplier', 1.0),
            'max_layers': params.get('max_layers', 2),
            'min_signals': params.get('min_signals', 3),
            'disable_new_entries': params.get('disable_new_entries', False),
            'close_all_positions': params.get('close_all_positions', False),
            'regime': self.current_regime,
            'sentiment': self.current_sentiment,
            'sentiment_intensity': self.sentiment_intensity,
            'event_risk': params.get('event_risk', 'low'),
            'timestamp': datetime.now().isoformat()
        }
        
        try:
            # Ensure directory exists
            os.makedirs(os.path.dirname(CONTROL_FILE), exist_ok=True)
            
            with open(CONTROL_FILE, 'w') as f:
                json.dump(control, f, indent=2)
            
            logger.info(f"📤 Control written: risk_bias={control['risk_bias']:.2f}, regime={control['regime']}")
            
        except Exception as e:
            logger.error(f"Failed to write control: {e}")
    
    def run(self):
        """Main brain loop"""
        logger.info("🧠 Brain loop starting...")
        
        while True:
            try:
                now = time.time()
                
                # Read market state
                state = self.read_market_state()
                
                if state:
                    # Update regime periodically
                    if now - self.last_regime_update >= REGIME_UPDATE_INTERVAL:
                        self.current_regime = self.classify_regime(state)
                        self.last_regime_update = now
                    
                    # Update sentiment periodically
                    if now - self.last_sentiment_update >= SENTIMENT_UPDATE_INTERVAL:
                        self.current_sentiment, self.sentiment_intensity = self.analyze_sentiment()
                        self.last_sentiment_update = now
                    
                    # Calculate risk parameters
                    params = self.calculate_risk_parameters(
                        state, 
                        self.current_regime,
                        self.current_sentiment,
                        self.sentiment_intensity
                    )
                    
                    # Write control for MT5
                    self.write_control(params)
                
                time.sleep(STATE_CHECK_INTERVAL)
                
            except KeyboardInterrupt:
                logger.info("🛑 Brain stopped by user")
                break
            except Exception as e:
                logger.error(f"Brain error: {e}")
                time.sleep(10)


def main():
    brain = OllamaBrain()
    brain.run()


if __name__ == "__main__":
    main()
