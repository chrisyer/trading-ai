"""
NEO-GOLD: Gold Trading Specialist
═══════════════════════════════════════════════════════════════════════

The GOLD ONLY AI trader that:
- Understands Gold's unique behavior
- Predicts Market Maker tactics
- Respects session timing and round numbers
- Outputs high-confidence signals ONLY

"I don't trade everything. I trade GOLD. And I trade it well."
"""

import json
import os
import requests
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import hashlib

from .config import (
    SYMBOL, MIN_CONFIDENCE, MAX_SIGNALS_PER_DAY, LLM_MODEL,
    SIGNAL_FILE, STATE_FILE, MT5_API_URL, OLLAMA_URL,
    FRANKFURTER_URL, logger
)
from .features import GoldFeatureExtractor
from .patterns import GoldPatternDetector, DetectedPattern
from .mm_predictor import MarketMakerPredictor, MMPrediction
from .rules import GoldTradingRules

# Import signal manager for deduplication
import sys
sys.path.insert(0, '/home/jbot/trading_ai/neo')
from signal_manager import (
    get_signal_manager, should_send_signal, record_signal_sent, generate_signal_id
)


class NeoGold:
    """
    Gold-specialized trading AI.
    
    Only trades XAUUSD.
    Learns human patterns (session behavior, news reactions).
    Predicts MM tactics (sweeps, fake breakouts).
    Outputs high-confidence signals ONLY.
    """
    
    def __init__(self):
        self.symbol = SYMBOL
        self.min_confidence = MIN_CONFIDENCE
        self.max_signals_per_day = MAX_SIGNALS_PER_DAY
        self.llm_model = LLM_MODEL
        
        # Components
        self.feature_extractor = GoldFeatureExtractor()
        self.pattern_detector = GoldPatternDetector()
        self.mm_predictor = MarketMakerPredictor()
        self.trading_rules = GoldTradingRules()
        
        # State
        self.signals_today: int = 0
        self.last_signal_time: Optional[datetime] = None
        self.current_features: Dict = {}
        self.current_patterns: List[DetectedPattern] = []
        self.current_mm_predictions: List[MMPrediction] = []
        
        # Load state
        self._load_state()
        
        logger.info("═══════════════════════════════════════════════════════════")
        logger.info("🥇 NEO-GOLD INITIALIZED")
        logger.info(f"   Symbol: {self.symbol}")
        logger.info(f"   Min Confidence: {self.min_confidence}%")
        logger.info(f"   Max Signals/Day: {self.max_signals_per_day}")
        logger.info(f"   LLM: {self.llm_model}")
        logger.info("═══════════════════════════════════════════════════════════")
    
    # ═══════════════════════════════════════════════════════════════════
    # MAIN LOOP
    # ═══════════════════════════════════════════════════════════════════
    
    def run(self, interval_seconds: int = 60):
        """Main trading loop."""
        
        logger.info("🚀 NEO-GOLD starting main loop...")
        cycle = 0
        
        while True:
            cycle += 1
            logger.info(f"\n{'='*60}")
            logger.info(f"🥇 CYCLE {cycle} - {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC")
            logger.info(f"{'='*60}")
            
            try:
                # Reset daily counter at midnight UTC
                self._check_daily_reset()
                
                # 🔄 AUTO-REFRESH: Keep signals fresh so Ghost can execute
                self._refresh_signal_if_needed()
                
                # Check if we can signal
                if self.signals_today >= self.max_signals_per_day:
                    logger.info(f"📊 Max signals reached ({self.max_signals_per_day}). Waiting...")
                    time.sleep(interval_seconds)
                    continue
                
                # Run analysis cycle
                signal = self.analyze()
                
                if signal and signal.get("action") in ["BUY", "SELL"]:
                    # ═══════════════════════════════════════════════════════════
                    # DEDUPLICATION CHECK - Prevent position stacking!
                    # Only send signal if it's DIFFERENT from last signal
                    # ═══════════════════════════════════════════════════════════
                    action = signal.get("action")
                    entry_price = signal.get("entry_price", 0)
                    
                    can_send, reason = should_send_signal(SYMBOL, action, entry_price)
                    
                    if can_send:
                        # Generate content-based signal ID (not time-based!)
                        signal["signal_id"] = generate_signal_id(SYMBOL, action, entry_price)
                        
                        # Write signal
                        self._write_signal(signal)
                        
                        # Record that we sent this signal
                        record_signal_sent(SYMBOL, action, entry_price, signal["signal_id"])
                        
                        logger.info(f"✅ Signal sent: {signal['signal_id']}")
                    else:
                        # Same signal already pending - don't spam Ghost!
                        logger.info(f"⏸️ Signal SKIPPED: {reason}")
                        logger.info(f"   Same {action} @ ${entry_price:.2f} already sent")
                    
            except Exception as e:
                logger.error(f"❌ Error in cycle {cycle}: {e}")
            
            logger.info(f"💤 Sleeping {interval_seconds}s...")
            time.sleep(interval_seconds)
    
    def analyze(self) -> Optional[Dict]:
        """
        Full analysis cycle:
        1. Get market data
        2. Extract features
        3. Detect patterns
        4. Predict MM behavior
        5. Make decision via LLM
        6. Check rules
        7. Output signal if valid
        """
        
        # Step 1: Get market data
        logger.info("📊 Fetching Gold price data...")
        price_data = self._get_price_data()
        
        if not price_data or price_data.get("price", 0) == 0:
            logger.warning("⚠️ No price data available")
            return None
        
        logger.info(f"   XAUUSD: ${price_data['price']:.2f}")
        
        # Step 2: Extract features
        logger.info("🔍 Extracting Gold-specific features...")
        self.current_features = self.feature_extractor.extract_all(price_data)
        
        # Step 3: Detect patterns
        logger.info("📈 Detecting patterns...")
        candles = price_data.get("candles", [])
        self.current_patterns = self.pattern_detector.detect_all(candles, self.current_features)
        
        # Step 4: Predict MM behavior
        logger.info("🎯 Predicting Market Maker behavior...")
        self.current_mm_predictions = self.mm_predictor.predict(
            self.current_features, self.current_patterns
        )
        
        # Step 5: LLM decision
        logger.info(f"🧠 Consulting {self.llm_model}...")
        decision = self._get_llm_decision()
        
        if not decision:
            logger.info("   No actionable decision from LLM")
            return None
        
        action = decision.get("action", "HOLD")
        confidence = decision.get("confidence", 0)
        
        logger.info(f"   Decision: {action} (confidence: {confidence}%)")
        
        if action not in ["BUY", "SELL"]:
            logger.info("   ⏸️ HOLD - No trade")
            return None
        
        if confidence < self.min_confidence:
            logger.info(f"   ⚠️ Confidence {confidence}% < {self.min_confidence}% minimum")
            return None
        
        # Step 6: Build signal
        signal = self._build_signal(decision)
        
        # Step 7: Check rules
        logger.info("📋 Checking trading rules...")
        can_trade, rule_results, size_multiplier = self.trading_rules.check_all(
            self.current_features, signal
        )
        
        if not can_trade:
            blocking = self.trading_rules.get_blocking_rules()
            logger.info(f"   ❌ BLOCKED by: {[r.rule_name for r in blocking]}")
            return None
        
        # Apply size adjustment
        original_size = signal.get("position_size_usd", 0)
        signal["position_size_usd"] = round(original_size * size_multiplier, 2)
        signal["size_multiplier"] = size_multiplier
        signal["rules"] = self.trading_rules.format_for_signal()
        
        # Also keep entry_price at top level for backward compatibility
        signal["entry_price"] = signal["trade_plan"]["entry_price"]
        signal["stop_loss"] = signal["trade_plan"]["stop_loss_price"]
        signal["take_profit"] = signal["trade_plan"]["take_profit_price"]
        signal["action"] = action
        
        logger.info(f"   ✅ SIGNAL VALID: {action} XAUUSD @ ${signal['entry_price']:.2f}")
        
        return signal
    
    # ═══════════════════════════════════════════════════════════════════
    # DATA FETCHING
    # ═══════════════════════════════════════════════════════════════════
    
    def _get_price_data(self) -> Dict:
        """Fetch Gold price and candle data."""
        
        price_data = {
            "price": 0,
            "candles": [],
            "dxy": 0
        }
        
        # PRIMARY: Yahoo Finance (free, reliable, real-time during market hours)
        try:
            import yfinance as yf
            gold = yf.Ticker('GC=F')
            
            # Use info['regularMarketPrice'] for CURRENT price (history() can be stale!)
            info = gold.info
            if info.get('regularMarketPrice'):
                price_data["price"] = float(info['regularMarketPrice'])
                logger.info(f"   📡 Price from Yahoo Finance (live): ${price_data['price']:.2f}")
            else:
                # Fallback to history if info fails
                hist = gold.history(period='2d', interval='1m')
                if not hist.empty:
                    price_data["price"] = float(hist['Close'].iloc[-1])
                    logger.warning(f"   ⚠️ Price from Yahoo history (may be stale): ${price_data['price']:.2f}")
                
                # Also get real candles (H1)
                h1_data = gold.history(period='5d', interval='1h')
                if not h1_data.empty:
                    price_data["candles"] = [
                        {
                            "hour": row.name.hour if hasattr(row.name, 'hour') else 0,
                            "open": float(row['Open']),
                            "high": float(row['High']),
                            "low": float(row['Low']),
                            "close": float(row['Close']),
                            "volume": int(row['Volume'])
                        }
                        for _, row in h1_data.tail(50).iterrows()
                    ]
                    logger.info(f"   📊 Loaded {len(price_data['candles'])} real H1 candles")
        except Exception as e:
            logger.warning(f"Yahoo Finance error: {e}")
        
        # Fallback 1: MT5 API
        if price_data["price"] == 0:
            try:
                response = requests.get(f"{MT5_API_URL}/prices/XAUUSD", timeout=5)
                if response.status_code == 200:
                    data = response.json()
                    if data.get("bid"):
                        price_data["price"] = data.get("bid", 0)
                        logger.info(f"   📡 Price from MT5 API: ${price_data['price']:.2f}")
            except Exception as e:
                logger.warning(f"MT5 API error: {e}")
        
        # Fallback 2: Twelve Data API
        if price_data["price"] == 0:
            api_key = os.getenv("TWELVE_DATA_API_KEY", "a2542c3955c5417d99226668f7709301")
            if api_key:
                try:
                    response = requests.get(
                        f"https://api.twelvedata.com/price?symbol=XAU/USD&apikey={api_key}",
                        timeout=10
                    )
                    if response.status_code == 200:
                        data = response.json()
                        if "price" in data:
                            price_data["price"] = float(data["price"])
                            logger.info(f"   📡 Price from Twelve Data: ${price_data['price']:.2f}")
                except Exception as e:
                    logger.warning(f"Twelve Data error: {e}")
        
        # NO STATIC FALLBACK - If we can't get real data, log error and return 0
        if price_data["price"] == 0:
            logger.error("   ❌ CRITICAL: Could not fetch real Gold price from ANY source!")
            logger.error("   ❌ NOT generating signal without real market data!")
        
        # Only generate synthetic candles if we have price but no candle data
        # (This should rarely happen now that we use Yahoo Finance)
        if price_data["price"] > 0 and not price_data["candles"]:
            logger.warning("   ⚠️ No candle data - generating synthetic (NOT recommended)")
            price_data["candles"] = self._generate_test_candles(price_data["price"])
        
        return price_data
    
    def _generate_test_candles(self, current_price: float, count: int = 50) -> List[Dict]:
        """Generate test candle data (for development)."""
        candles = []
        price = current_price - 20  # Start $20 lower
        
        for i in range(count):
            hour = (datetime.utcnow().hour - count + i) % 24
            
            open_price = price
            high = price + (hash(str(i)) % 10) / 2
            low = price - (hash(str(i+1)) % 10) / 2
            close = price + (hash(str(i+2)) % 20 - 10) / 2
            
            candles.append({
                "hour": hour,
                "open": open_price,
                "high": high,
                "low": low,
                "close": close,
                "volume": 1000 + hash(str(i)) % 5000
            })
            
            price = close
        
        return candles
    
    # ═══════════════════════════════════════════════════════════════════
    # LLM DECISION
    # ═══════════════════════════════════════════════════════════════════
    
    def _get_llm_decision(self) -> Optional[Dict]:
        """Get trading decision from LLM."""
        
        prompt = self._build_llm_prompt()
        
        try:
            response = requests.post(
                f"{OLLAMA_URL}/api/generate",
                json={
                    "model": self.llm_model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {
                        "temperature": 0.3,
                        "num_predict": 1000
                    }
                },
                timeout=120
            )
            
            if response.status_code != 200:
                logger.error(f"LLM error: {response.status_code}")
                return None
            
            result = response.json()
            text = result.get("response", "")
            
            # Parse JSON from response
            decision = self._parse_llm_response(text)
            return decision
            
        except Exception as e:
            logger.error(f"LLM request failed: {e}")
            return None
    
    def _build_llm_prompt(self) -> str:
        """Build the prompt for the LLM."""
        
        features = self.current_features
        patterns = self.current_patterns
        mm_predictions = self.current_mm_predictions
        
        # Format patterns
        patterns_text = ""
        if patterns:
            for p in patterns[:3]:
                patterns_text += f"- {p.type.value}: {p.direction} ({p.confidence}%)\n"
                patterns_text += f"  {p.description}\n"
        else:
            patterns_text = "No significant patterns detected.\n"
        
        # Format MM predictions
        mm_text = self.mm_predictor.format_for_llm() if mm_predictions else "No MM predictions."
        
        prompt = f"""You are NEO-GOLD, a specialized Gold (XAUUSD) trading AI.
Your job is to analyze the current market and decide: BUY, SELL, or HOLD.

═══════════════════════════════════════════════════════════════════════
CURRENT MARKET STATE
═══════════════════════════════════════════════════════════════════════

Symbol: XAUUSD (Gold)
Price: ${features.get('price', 0):.2f}
Time: {datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC

SESSION: {features.get('session', 'UNKNOWN')}
- Minutes into session: {features.get('session_details', {}).get('minutes_into_session', 0)}
- Is London/NY Overlap: {features.get('session_details', {}).get('is_overlap', False)}
- Expected Volatility: {features.get('session_details', {}).get('expected_volatility', 'MEDIUM')}

ROUND NUMBERS:
- Nearest: ${features.get('round_number', {}).get('nearest', 0):.0f}
- Distance: {features.get('round_number', {}).get('distance_pips', 0):.0f} pips
- Type: {features.get('round_number', {}).get('type', 'none')}
- Magnet Strength: {features.get('round_number', {}).get('magnet_strength', 'WEAK')}

ASIAN RANGE:
- High: ${features.get('asian_range', {}).get('high', 0):.2f}
- Low: ${features.get('asian_range', {}).get('low', 0):.2f}
- Range: {features.get('asian_range', {}).get('range_pips', 0):.0f} pips
- Type: {features.get('asian_range', {}).get('range_type', 'UNKNOWN')}
- Breakout Expected: {features.get('asian_range', {}).get('breakout_expected', False)}

VOLATILITY:
- Regime: {features.get('volatility', {}).get('regime', 'NORMAL')}
- ATR: ${features.get('volatility', {}).get('atr', 25):.2f}

MOMENTUM:
- RSI(2): {features.get('momentum', {}).get('rsi_2', 50):.1f}
- RSI(14): {features.get('momentum', {}).get('rsi_14', 50):.1f}
- Bias: {features.get('momentum', {}).get('momentum_bias', 'NEUTRAL')}

═══════════════════════════════════════════════════════════════════════
DETECTED PATTERNS
═══════════════════════════════════════════════════════════════════════

{patterns_text}

═══════════════════════════════════════════════════════════════════════
MARKET MAKER PREDICTION (What Would Citadel Do?)
═══════════════════════════════════════════════════════════════════════

{mm_text}

═══════════════════════════════════════════════════════════════════════
YOUR DECISION
═══════════════════════════════════════════════════════════════════════

Based on the above analysis, provide your trading decision.

RULES:
1. Only trade if confidence >= 75%
2. Respect session timing (no trading first 15min of London)
3. Don't fight price moving toward round numbers
4. If RSI(2) < 10, look for BUY. If RSI(2) > 90, look for SELL.
5. If MM prediction says "expect reversal", wait for confirmation.

Respond with a JSON object. BE SPECIFIC WITH PRICES!

For Gold (XAUUSD): 1 pip = $0.10
- 25 pips = $2.50 move
- 50 pips = $5.00 move
- 75 pips = $7.50 move

Example trade plan:
- Current price: $2750
- Entry: $2745 (wait for dip)
- Stop Loss: $2738 (70 pips below entry = $7 risk)
- Take Profit: $2770 (250 pips above entry = $25 reward)

{{
  "action": "BUY" | "SELL" | "HOLD",
  "confidence": 0-100,
  "entry_price": <SPECIFIC price - e.g. 2745.00>,
  "stop_loss": <SPECIFIC price - e.g. 2738.00>,
  "take_profit": <SPECIFIC price - e.g. 2770.00>,
  "entry_type": "LIMIT" | "MARKET",
  "entry_condition": "If price drops to $X, BUY" or "BUY now at market",
  "reasoning": [
    "Entry: Why this specific price level",
    "SL: Why stop here (support/resistance/round number)",
    "TP: Why target here (next resistance/25 pips/round number)"
  ],
  "mm_consideration": "How you factored in MM prediction"
}}

If holding:
{{
  "action": "HOLD",
  "confidence": 0,
  "reasoning": ["Why no trade - be specific"]
}}

IMPORTANT: 
- Use ROUND numbers when possible ($2750, $2745, $2740)
- Stop loss should be 30-75 pips (typical Gold SL)
- Take profit should be 50-150 pips (depends on setup)
- Aim for minimum 1:1.5 risk:reward

JSON RESPONSE:"""

        return prompt
    
    def _parse_llm_response(self, text: str) -> Optional[Dict]:
        """Parse JSON from LLM response."""
        
        # Find JSON in response
        try:
            # Try to find JSON block
            start = text.find('{')
            end = text.rfind('}') + 1
            
            if start >= 0 and end > start:
                json_str = text[start:end]
                return json.loads(json_str)
        except json.JSONDecodeError as e:
            logger.error(f"JSON parse error: {e}")
        
        return None
    
    # ═══════════════════════════════════════════════════════════════════
    # SIGNAL BUILDING
    # ═══════════════════════════════════════════════════════════════════
    
    def _build_signal(self, decision: Dict) -> Dict:
        """Build the final signal object with ACTIONABLE trade plan."""
        
        action = decision.get("action", "HOLD")
        current_price = self.current_features.get("price", 0)
        
        # Get entry price - fallback to current price if not provided
        entry = decision.get("entry_price", 0) or current_price
        
        # Get SL/TP from decision, or calculate defaults
        sl = decision.get("stop_loss", 0)
        tp = decision.get("take_profit", 0)
        
        # If LLM didn't provide SL/TP, calculate based on action and ATR
        atr = self.current_features.get("volatility", {}).get("atr", 25)
        
        if not sl or sl == 0:
            # Default SL: 50 pips (for Gold, 50 pips = $5)
            if action == "BUY":
                sl = entry - 5.0  # $5 below entry
            elif action == "SELL":
                sl = entry + 5.0  # $5 above entry
        
        if not tp or tp == 0:
            # Default TP: 100 pips (for Gold, 100 pips = $10)
            if action == "BUY":
                tp = entry + 10.0  # $10 above entry
            elif action == "SELL":
                tp = entry - 10.0  # $10 below entry
        
        # For Gold: 1 pip = $0.10, so 25 pips = $2.50
        PIP_VALUE = 0.10
        
        # Calculate pips for SL and TP
        sl_pips = abs(entry - sl) / PIP_VALUE if sl else 0
        tp_pips = abs(tp - entry) / PIP_VALUE if tp else 0
        
        # Calculate R:R
        if sl_pips > 0 and tp_pips > 0:
            rr = f"1:{tp_pips/sl_pips:.1f}"
            rr_ratio = tp_pips / sl_pips
        else:
            rr = "N/A"
            rr_ratio = 0
        
        # Calculate position size (5% of $88K account)
        account_balance = 88000
        position_size = account_balance * 0.05
        
        # Build entry condition (limit order style)
        if action == "BUY":
            if entry < current_price:
                entry_condition = f"BUY LIMIT at ${entry:.2f}"
                entry_type = "LIMIT"
            else:
                entry_condition = f"BUY NOW at market (~${current_price:.2f})"
                entry_type = "MARKET"
        elif action == "SELL":
            if entry > current_price:
                entry_condition = f"SELL LIMIT at ${entry:.2f}"
                entry_type = "LIMIT"
            else:
                entry_condition = f"SELL NOW at market (~${current_price:.2f})"
                entry_type = "MARKET"
        else:
            entry_condition = "NO TRADE"
            entry_type = "NONE"
        
        # Build the ACTIONABLE trade plan
        signal = {
            "signal_id": f"GOLD_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}",
            "timestamp": datetime.utcnow().isoformat(),
            "symbol": self.symbol,
            
            # ════════════════════════════════════════════════════════════
            # ACTIONABLE TRADE PLAN (Ghost Commander reads this)
            # ════════════════════════════════════════════════════════════
            "trade_plan": {
                "action": action,
                "entry_type": entry_type,  # LIMIT or MARKET
                "entry_condition": entry_condition,
                "entry_price": round(entry, 2),
                "current_price": round(current_price, 2),
                
                # Stop Loss
                "stop_loss_price": round(sl, 2) if sl else 0,
                "stop_loss_pips": round(sl_pips),
                "stop_loss_dollars": round(sl_pips * PIP_VALUE, 2),
                
                # Take Profit
                "take_profit_price": round(tp, 2) if tp else 0,
                "take_profit_pips": round(tp_pips),
                "take_profit_dollars": round(tp_pips * PIP_VALUE, 2),
                
                # Risk/Reward
                "risk_reward": rr,
                "risk_reward_ratio": round(rr_ratio, 2),
                
                # Size
                "position_size_usd": round(position_size, 2),
                "lot_size": round(position_size / 100000, 2),  # Approximate
            },
            
            # ════════════════════════════════════════════════════════════
            # HUMAN-READABLE SUMMARY
            # ════════════════════════════════════════════════════════════
            "summary": self._build_human_summary(action, entry, sl, tp, sl_pips, tp_pips, current_price),
            
            # ════════════════════════════════════════════════════════════
            # ANALYSIS
            # ════════════════════════════════════════════════════════════
            "confidence": decision.get("confidence", 0),
            "reasoning": {
                "pattern": self._get_top_pattern_name(),
                "mm_prediction": self._get_top_mm_prediction(),
                "session": self.current_features.get("session", "UNKNOWN"),
                "llm_reasons": decision.get("reasoning", []),
            },
            "hold_time_estimate": self._estimate_hold_time(),
            "features": {
                "rsi_2": round(self.current_features.get("momentum", {}).get("rsi_2", 50), 1),
                "rsi_14": round(self.current_features.get("momentum", {}).get("rsi_14", 50), 1),
                "volatility": self.current_features.get("volatility", {}).get("regime", "NORMAL"),
                "round_number": self.current_features.get("round_number", {}).get("nearest", 0)
            }
        }
        
        return signal
    
    def _build_human_summary(self, action: str, entry: float, sl: float, tp: float, 
                             sl_pips: float, tp_pips: float, current_price: float) -> str:
        """Build a human-readable trade summary."""
        
        if action == "BUY":
            direction = "📈 BUY"
            sl_direction = "below"
            tp_direction = "above"
        elif action == "SELL":
            direction = "📉 SELL"
            sl_direction = "above"
            tp_direction = "below"
        else:
            return "⏸️ NO TRADE - Waiting for setup"
        
        lines = [
            f"═══════════════════════════════════════════",
            f"{direction} XAUUSD",
            f"═══════════════════════════════════════════",
            f"",
            f"📍 ENTRY: ${entry:.2f}",
        ]
        
        if entry < current_price and action == "BUY":
            lines.append(f"   → If price DROPS to ${entry:.2f}, BUY")
        elif entry > current_price and action == "SELL":
            lines.append(f"   → If price RISES to ${entry:.2f}, SELL")
        else:
            lines.append(f"   → Enter NOW at market")
        
        lines.extend([
            f"",
            f"🛑 STOP LOSS: ${sl:.2f} ({sl_pips:.0f} pips {sl_direction})",
            f"   → Risk: ${sl_pips * 0.10:.2f} per 0.01 lot",
            f"",
            f"🎯 TAKE PROFIT: ${tp:.2f} ({tp_pips:.0f} pips {tp_direction})",
            f"   → Reward: ${tp_pips * 0.10:.2f} per 0.01 lot",
            f"",
            f"📊 RISK:REWARD = 1:{tp_pips/sl_pips:.1f}" if sl_pips > 0 else "",
            f"═══════════════════════════════════════════",
        ])
        
        return "\n".join(lines)
    
    def _get_top_pattern_name(self) -> str:
        """Get name of highest confidence pattern."""
        if not self.current_patterns:
            return "none"
        return self.current_patterns[0].type.value
    
    def _get_top_mm_prediction(self) -> str:
        """Get top MM prediction description."""
        if not self.current_mm_predictions:
            return "No MM prediction"
        pred = self.current_mm_predictions[0]
        return f"{pred.tactic.value} → {pred.direction_after}"
    
    def _estimate_hold_time(self) -> str:
        """Estimate hold time based on session and volatility."""
        session = self.current_features.get("session", "")
        volatility = self.current_features.get("volatility", {}).get("regime", "NORMAL")
        
        if session == "OVERLAP_LONDON_NY" and volatility in ["VOLATILE", "CHAOS"]:
            return "30min - 2 hours"
        elif session in ["LONDON", "NEW_YORK"]:
            return "1-4 hours"
        else:
            return "2-8 hours"
    
    # ═══════════════════════════════════════════════════════════════════
    # SIGNAL OUTPUT
    # ═══════════════════════════════════════════════════════════════════
    
    def _write_signal(self, signal: Dict):
        """Write signal to file and update state."""
        
        # Write signal
        with open(SIGNAL_FILE, 'w') as f:
            json.dump(signal, f, indent=2)
        
        # Also push to API
        try:
            requests.post(f"{MT5_API_URL}/neo/gold/signal", json=signal, timeout=5)
        except Exception as e:
            logger.warning(f"Could not push to API: {e}")
        
        # Log the ACTIONABLE trade plan
        plan = signal.get("trade_plan", {})
        logger.info(f"")
        logger.info(f"═══════════════════════════════════════════════════════════")
        logger.info(f"📤 NEW GOLD SIGNAL!")
        logger.info(f"═══════════════════════════════════════════════════════════")
        logger.info(f"")
        logger.info(f"   {plan.get('action', 'N/A')} XAUUSD")
        logger.info(f"")
        logger.info(f"   📍 ENTRY: ${plan.get('entry_price', 0):.2f}")
        logger.info(f"      {plan.get('entry_condition', '')}")
        logger.info(f"")
        logger.info(f"   🛑 STOP LOSS: ${plan.get('stop_loss_price', 0):.2f} ({plan.get('stop_loss_pips', 0):.0f} pips)")
        logger.info(f"   🎯 TAKE PROFIT: ${plan.get('take_profit_price', 0):.2f} ({plan.get('take_profit_pips', 0):.0f} pips)")
        logger.info(f"")
        logger.info(f"   📊 Risk:Reward = {plan.get('risk_reward', 'N/A')}")
        logger.info(f"   🎯 Confidence: {signal.get('confidence', 0)}%")
        logger.info(f"")
        logger.info(f"═══════════════════════════════════════════════════════════")
        
        # Update state
        self.signals_today += 1
        self.last_signal_time = datetime.utcnow()
        self._save_state()
    
    # ═══════════════════════════════════════════════════════════════════
    # SIGNAL AUTO-REFRESH
    # Keeps signals fresh so Ghost Commander can always execute
    # ═══════════════════════════════════════════════════════════════════
    
    def _refresh_signal_if_needed(self, max_age_minutes: int = 15):
        """
        Auto-refresh signals that are getting old.
        Ghost Commander rejects signals >30 minutes old.
        We refresh at 15 minutes to keep them fresh.
        """
        try:
            if not os.path.exists(SIGNAL_FILE):
                return
            
            with open(SIGNAL_FILE, 'r') as f:
                signal = json.load(f)
            
            # Check signal age
            timestamp = signal.get("timestamp")
            if not timestamp:
                return
            
            signal_time = datetime.fromisoformat(timestamp.replace('Z', ''))
            age_minutes = (datetime.utcnow() - signal_time).total_seconds() / 60
            
            # Only refresh if signal is BUY or SELL and getting old
            action = signal.get("action") or signal.get("trade", {}).get("direction")
            if action not in ["BUY", "SELL"]:
                return
            
            if age_minutes < max_age_minutes:
                return  # Signal is still fresh
            
            logger.info(f"🔄 Signal is {age_minutes:.1f}min old - checking if refresh needed...")
            
            # Get current price
            price_data = self._get_price_data()
            current_price = price_data.get("price", 0)
            
            if current_price == 0:
                logger.warning("   Cannot refresh - no price data")
                return
            
            # Get original entry and check if still valid
            entry_price = signal.get("entry_price") or signal.get("trade", {}).get("entry_price", 0)
            sl_price = signal.get("stop_loss") or signal.get("trade", {}).get("stop_loss", 0)
            tp_price = signal.get("take_profit") or signal.get("trade", {}).get("take_profit", 0)
            
            # Validate the signal is still valid
            is_valid = False
            
            if action == "BUY":
                # BUY is valid if price hasn't hit SL or TP
                if current_price > sl_price and current_price < tp_price:
                    is_valid = True
            elif action == "SELL":
                # SELL is valid if price hasn't hit SL or TP
                if current_price < sl_price and current_price > tp_price:
                    is_valid = True
            
            if is_valid:
                # Refresh the signal with new timestamp
                signal["timestamp"] = datetime.utcnow().isoformat()
                signal["refreshed_at"] = datetime.utcnow().isoformat()
                signal["refresh_count"] = signal.get("refresh_count", 0) + 1
                
                # Update entry price to current for market orders
                trade_plan = signal.get("trade_plan", {})
                if trade_plan.get("entry_type") == "MARKET":
                    trade_plan["entry_price"] = round(current_price, 2)
                    trade_plan["current_price"] = round(current_price, 2)
                    signal["entry_price"] = round(current_price, 2)
                    if "trade" in signal:
                        signal["trade"]["entry_price"] = round(current_price, 2)
                
                # Write refreshed signal
                with open(SIGNAL_FILE, 'w') as f:
                    json.dump(signal, f, indent=2)
                
                # Push to API
                try:
                    requests.post(f"{MT5_API_URL}/neo/signal", json=signal, timeout=5)
                    requests.post(f"{MT5_API_URL}/neo/gold/signal", json=signal, timeout=5)
                except:
                    pass
                
                logger.info(f"   ✅ Signal REFRESHED! (refresh #{signal['refresh_count']})")
                logger.info(f"      {action} XAUUSD @ ${current_price:.2f}")
            else:
                # Signal no longer valid - clear it
                logger.info(f"   ⚠️ Signal no longer valid (price moved) - clearing")
                self._clear_signal()
                
        except Exception as e:
            logger.warning(f"   Auto-refresh error: {e}")
    
    def _clear_signal(self):
        """Clear the current signal."""
        try:
            wait_signal = {
                "signal_id": f"WAIT_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}",
                "timestamp": datetime.utcnow().isoformat(),
                "action": "WAIT",
                "symbol": self.symbol,
                "reason": "Previous signal invalidated"
            }
            with open(SIGNAL_FILE, 'w') as f:
                json.dump(wait_signal, f, indent=2)
            
            requests.post(f"{MT5_API_URL}/neo/signal", json=wait_signal, timeout=5)
        except:
            pass
    
    # ═══════════════════════════════════════════════════════════════════
    # STATE MANAGEMENT
    # ═══════════════════════════════════════════════════════════════════
    
    def _load_state(self):
        """Load state from file."""
        try:
            if os.path.exists(STATE_FILE):
                with open(STATE_FILE, 'r') as f:
                    state = json.load(f)
                
                # Check if state is from today
                state_date = state.get("date", "")
                today = datetime.utcnow().strftime("%Y-%m-%d")
                
                if state_date == today:
                    self.signals_today = state.get("signals_today", 0)
                    last_time = state.get("last_signal_time")
                    if last_time:
                        self.last_signal_time = datetime.fromisoformat(last_time)
                        
                logger.info(f"📂 State loaded: {self.signals_today} signals today")
        except Exception as e:
            logger.warning(f"Could not load state: {e}")
    
    def _save_state(self):
        """Save state to file."""
        try:
            state = {
                "date": datetime.utcnow().strftime("%Y-%m-%d"),
                "signals_today": self.signals_today,
                "last_signal_time": self.last_signal_time.isoformat() if self.last_signal_time else None
            }
            
            with open(STATE_FILE, 'w') as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            logger.warning(f"Could not save state: {e}")
    
    def _check_daily_reset(self):
        """Reset daily counter at midnight UTC."""
        if self.last_signal_time:
            today = datetime.utcnow().date()
            last_date = self.last_signal_time.date()
            
            if today > last_date:
                logger.info("📅 New day - resetting signal counter")
                self.signals_today = 0
                self._save_state()


# ═══════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════

def main():
    """Entry point for NEO-GOLD."""
    neo = NeoGold()
    neo.run(interval_seconds=60)


if __name__ == "__main__":
    main()
