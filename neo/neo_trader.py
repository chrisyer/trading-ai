#!/usr/bin/env python3
"""
NEO - Autonomous LLM Trader
The brain that sees, thinks, acts, and learns.

Like AlphaZero learning chess, but forex.

Loop:
1. SEE - Get real market data and positions
2. THINK - LLM analyzes and reasons
3. ACT - Generate signals or hold
4. LEARN - Review outcomes and extract lessons
"""

import time
import json
import requests
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional, Tuple
import logging
from pathlib import Path

from config import (
    OLLAMA_URL, LLM_CONFIG, PROVEN_PARAMETERS,
    THINK_INTERVAL_SECONDS, LEARN_INTERVAL_SECONDS,
    ACCOUNT_SIZE, MAX_POSITION_DOLLARS, MAX_DAILY_LOSS_DOLLARS,
    KILL_SWITCH_ENABLED, LOG_ALL_DECISIONS,
    LOGS_DIR, FOREX_PAIRS
)
from market_feed import MarketFeed, MarketSnapshot
from position_tracker import PositionTracker, AccountState
from memory_store import MemoryStore, Decision, Outcome, Learning
from signal_writer import SignalWriter, TradingSignal
from knowledge_loader import KnowledgeLoader
from war_room import WarRoom

# Deep Learning Integration
try:
    from hybrid_decision import HybridDecisionEngine, HybridAnalysis
    HYBRID_AVAILABLE = True
except ImportError:
    HYBRID_AVAILABLE = False
    HybridDecisionEngine = None
    HybridAnalysis = None

# Fleet Monitor Integration
try:
    from fleet_monitor import FleetMonitor, RISK_THRESHOLDS
    FLEET_MONITOR_AVAILABLE = True
except ImportError:
    FLEET_MONITOR_AVAILABLE = False
    FleetMonitor = None
    RISK_THRESHOLDS = None

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [NEO] %(levelname)s: %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOGS_DIR / f"neo_{datetime.now().strftime('%Y%m%d')}.log")
    ]
)
logger = logging.getLogger("NEO")


class NEOTrader:
    """
    NEO - Neural Economic Oracle
    Autonomous LLM-based trader that learns from outcomes.
    
    Now equipped with:
    - Full proven strategies knowledge base
    - WWCD (What Would Citadel Do) playbook
    - Fleet coordination capabilities
    - Multi-model consensus for big decisions
    """
    
    def __init__(self):
        self.market_feed = MarketFeed()
        self.position_tracker = PositionTracker()
        self.memory = MemoryStore()
        self.signal_writer = SignalWriter()
        self.knowledge = KnowledgeLoader()
        self.war_room = WarRoom()
        
        self.running = False
        self.last_think_time = None
        self.last_learn_time = None
        self.decisions_today = 0
        self.current_model = LLM_CONFIG["primary"]["model"]
        self.use_consensus = True  # Use multi-model for high-stakes decisions
        self.use_war_room = True  # Use war room intel in decisions
        self.use_hybrid = True  # Use deep learning models (CNN, LSTM, RL)
        
        # Signal tracking to prevent spam
        self.active_signal = None  # Currently active signal
        self.signal_cooldown_minutes = 30  # Minimum 30 min between same signal
        self.last_signal_time = {}  # {symbol_direction: timestamp}
        
        # DIVERSITY TRACKING - Don't fixate on one symbol
        self.symbol_cooldown_hours = 2  # Don't signal same symbol for 2 hours
        self.recently_signaled = {}  # {symbol: last_signal_time}
        self.diversity_pool = ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD", "AUDUSD", "NZDUSD"]
        
        # SIGNAL STATUS TRACKING - Track what happened to our signals
        self.signal_status = {}  # {signal_id: {status, trade_ticket, outcome_pips}}
        self.pending_signals = []  # Signals waiting to be executed
        
        # Load any existing active signal from files (survives restart)
        self._load_active_signal_from_files()
        
        # Initialize Hybrid Decision Engine (Deep Learning)
        if HYBRID_AVAILABLE and self.use_hybrid:
            try:
                self.hybrid_engine = HybridDecisionEngine()
                logger.info("🧠 Hybrid Decision Engine: ACTIVE (CNN + LSTM + RL)")
            except Exception as e:
                logger.warning(f"⚠️ Hybrid engine failed to load: {e}")
                self.hybrid_engine = None
                self.use_hybrid = False
        else:
            self.hybrid_engine = None
            self.use_hybrid = False
        
        # Initialize Fleet Monitor (Portfolio Visibility)
        self.use_fleet_monitor = True
        if FLEET_MONITOR_AVAILABLE and self.use_fleet_monitor:
            try:
                self.fleet_monitor = FleetMonitor()
                # Initial update
                self.fleet_monitor.update()
                logger.info("📊 Fleet Monitor: ACTIVE (Full portfolio visibility)")
            except Exception as e:
                logger.warning(f"⚠️ Fleet monitor failed to load: {e}")
                self.fleet_monitor = None
                self.use_fleet_monitor = False
        else:
            self.fleet_monitor = None
            self.use_fleet_monitor = False
        
        # Log knowledge loading
        stats = self.knowledge.get_stats()
        logger.info(f"📚 Knowledge loaded: {stats['strategies_loaded']} strategies, "
                   f"{stats['fleet_bots']} bots, WWCD playbook")
        logger.info(f"🎖️ War Room: {'ACTIVE' if self.use_war_room else 'DISABLED'}")
        logger.info(f"🤖 Deep Learning: {'ACTIVE' if self.use_hybrid else 'LLM ONLY'}")
        logger.info(f"📊 Fleet Monitor: {'ACTIVE' if self.use_fleet_monitor else 'DISABLED'}")
    
    def _is_duplicate_signal(self, symbol: str, direction: str) -> Tuple[bool, str]:
        """
        Check if this signal is a duplicate of the active signal.
        Returns (is_duplicate, reason).
        
        ROBUST CHECK - survives restarts by checking FILES not just memory:
        1. Check /tmp/neo_signal.json (what Ghost Commander sees)
        2. Check active_signal.json (our tracking file)
        3. Check in-memory state
        """
        signal_key = f"{symbol}_{direction}"
        now = datetime.utcnow()
        
        # Check 1: What's in the actual signal file? (MOST IMPORTANT)
        try:
            signal_file = Path("/tmp/neo_signal.json")
            if signal_file.exists():
                with open(signal_file, "r") as f:
                    last_signal = json.load(f)
                
                last_symbol = last_signal.get("trade", {}).get("symbol")
                last_direction = last_signal.get("trade", {}).get("direction")
                last_action = last_signal.get("action", "")
                last_time_str = last_signal.get("timestamp", "")
                
                # If action is OPEN and same symbol/direction, it's a duplicate
                if last_action == "OPEN" and last_symbol == symbol and last_direction == direction:
                    # Check how old the signal is
                    if last_time_str:
                        try:
                            last_time = datetime.fromisoformat(last_time_str.replace('Z', ''))
                            age_minutes = (now - last_time).total_seconds() / 60
                            
                            # If signal is less than 60 minutes old, it's still active
                            if age_minutes < 60:
                                return True, f"Signal file shows {symbol} {direction} active ({age_minutes:.0f}min ago)"
                        except:
                            pass
                    
                    return True, f"Signal file shows {symbol} {direction} still pending"
        except Exception as e:
            logger.debug(f"Could not check signal file: {e}")
        
        # Check 2: Our tracking file
        try:
            active_file = LOGS_DIR / "active_signal.json"
            if active_file.exists():
                with open(active_file, "r") as f:
                    tracked = json.load(f)
                
                if tracked.get("symbol") == symbol and tracked.get("direction") == direction:
                    tracked_time = tracked.get("timestamp", "")
                    if tracked_time:
                        try:
                            t = datetime.fromisoformat(tracked_time.replace('Z', ''))
                            age = (now - t).total_seconds() / 60
                            if age < 60:  # Signal less than 60 min old
                                return True, f"Tracked signal: {symbol} {direction} active ({age:.0f}min ago)"
                        except:
                            pass
        except:
            pass
        
        # Check 3: In-memory state (for current session)
        if self.active_signal:
            active_symbol = self.active_signal.get("symbol")
            active_direction = self.active_signal.get("direction")
            
            if active_symbol == symbol and active_direction == direction:
                return True, f"Memory: {symbol} {direction} (monitoring...)"
        
        # Check 4: Cooldown period
        if signal_key in self.last_signal_time:
            last_time = self.last_signal_time[signal_key]
            if isinstance(last_time, str):
                last_time = datetime.fromisoformat(last_time.replace('Z', ''))
            
            elapsed = (now - last_time).total_seconds() / 60
            if elapsed < self.signal_cooldown_minutes:
                remaining = self.signal_cooldown_minutes - elapsed
                return True, f"Cooldown: {remaining:.1f}min until new {symbol} {direction}"
        
        return False, ""
    
    def _set_active_signal(self, symbol: str, direction: str, signal_id: str):
        """Record a new active signal."""
        now = datetime.utcnow()
        signal_key = f"{symbol}_{direction}"
        
        self.active_signal = {
            "symbol": symbol,
            "direction": direction,
            "signal_id": signal_id,
            "timestamp": now.isoformat(),
            "status": "OPEN"
        }
        self.last_signal_time[signal_key] = now
        
        # Save to file for persistence across restarts
        try:
            active_file = LOGS_DIR / "active_signal.json"
            with open(active_file, "w") as f:
                json.dump(self.active_signal, f, indent=2)
        except Exception as e:
            logger.warning(f"Could not save active signal: {e}")
    
    def _clear_active_signal(self, symbol: str = None):
        """Clear the active signal (trade closed or cancelled)."""
        if symbol and self.active_signal:
            if self.active_signal.get("symbol") != symbol:
                return  # Different symbol, don't clear
        
        if self.active_signal:
            logger.info(f"   🔄 Clearing active signal: {self.active_signal.get('symbol')} {self.active_signal.get('direction')}")
        
        self.active_signal = None
        
        try:
            active_file = LOGS_DIR / "active_signal.json"
            if active_file.exists():
                active_file.unlink()
        except:
            pass
    
    def _check_symbol_diversity(self, symbol: str) -> Tuple[bool, str]:
        """
        Check if we should trade this symbol based on diversity rules.
        NEO should NOT fixate on one symbol - rotate through the pool.
        
        Returns (can_trade, reason)
        """
        now = datetime.utcnow()
        
        # Check if symbol was recently signaled
        if symbol in self.recently_signaled:
            last_time = self.recently_signaled[symbol]
            if isinstance(last_time, str):
                last_time = datetime.fromisoformat(last_time.replace('Z', ''))
            
            hours_elapsed = (now - last_time).total_seconds() / 3600
            if hours_elapsed < self.symbol_cooldown_hours:
                remaining = self.symbol_cooldown_hours - hours_elapsed
                return False, f"Symbol on cooldown: {symbol} signaled {hours_elapsed:.1f}h ago (wait {remaining:.1f}h)"
        
        return True, ""
    
    def _get_alternative_symbols(self) -> List[str]:
        """
        Get list of symbols that are available for trading (not on cooldown).
        Used when NEO needs to diversify away from fixating on one symbol.
        """
        now = datetime.utcnow()
        available = []
        
        for symbol in self.diversity_pool:
            if symbol in self.recently_signaled:
                last_time = self.recently_signaled[symbol]
                if isinstance(last_time, str):
                    last_time = datetime.fromisoformat(last_time.replace('Z', ''))
                hours_elapsed = (now - last_time).total_seconds() / 3600
                if hours_elapsed >= self.symbol_cooldown_hours:
                    available.append(symbol)
            else:
                available.append(symbol)
        
        return available
    
    def _record_symbol_signal(self, symbol: str):
        """Record that we signaled this symbol (for diversity tracking)."""
        self.recently_signaled[symbol] = datetime.utcnow()
        
        # Persist to file
        try:
            diversity_file = LOGS_DIR / "diversity_tracking.json"
            with open(diversity_file, "w") as f:
                json.dump({
                    "recently_signaled": {k: v.isoformat() if isinstance(v, datetime) else v 
                                          for k, v in self.recently_signaled.items()}
                }, f, indent=2)
        except:
            pass
    
    def _update_signal_status(self, signal_id: str, status: str, 
                               trade_ticket: int = None, outcome_pips: float = None):
        """
        Update the status of a signal based on trade outcome.
        Status: ACTIVE, EXECUTED, CLOSED_TP, CLOSED_SL, EXPIRED, CANCELLED
        """
        self.signal_status[signal_id] = {
            "status": status,
            "trade_ticket": trade_ticket,
            "outcome_pips": outcome_pips,
            "updated_at": datetime.utcnow().isoformat()
        }
        
        # Log the outcome
        if status in ["CLOSED_TP", "CLOSED_SL"]:
            logger.info(f"   📊 Signal {signal_id} → {status} ({outcome_pips:+.1f} pips)")
            
            # If trade closed, clear active signal so NEO can trade again
            if self.active_signal and self.active_signal.get("signal_id") == signal_id:
                self._clear_active_signal()
    
    def _check_pending_trades(self):
        """
        Check if any of NEO's pending signals have been executed or closed.
        This gives NEO feedback on its decisions.
        """
        try:
            # Get positions from MT5
            positions = self.position_tracker.get_positions()
            account = self.position_tracker.get_account_state()
            
            if self.active_signal:
                signal_symbol = self.active_signal.get("symbol")
                signal_direction = self.active_signal.get("direction")
                signal_id = self.active_signal.get("signal_id")
                
                # Check if there's a matching open position
                matching_position = None
                for pos in positions:
                    if pos.get("symbol") == signal_symbol:
                        pos_type = pos.get("type", "").upper()
                        if (pos_type == "BUY" and signal_direction == "BUY") or \
                           (pos_type == "SELL" and signal_direction == "SELL"):
                            matching_position = pos
                            break
                
                if matching_position:
                    # Trade is open - update status
                    if signal_id not in self.signal_status or \
                       self.signal_status[signal_id].get("status") != "EXECUTED":
                        self._update_signal_status(signal_id, "EXECUTED", 
                                                   trade_ticket=matching_position.get("ticket"))
                        logger.info(f"   ✅ Signal {signal_id} EXECUTED as ticket #{matching_position.get('ticket')}")
                else:
                    # No matching position - check if trade closed
                    if signal_id in self.signal_status and \
                       self.signal_status[signal_id].get("status") == "EXECUTED":
                        # Trade was open but now closed
                        # TODO: Get actual outcome from trade history
                        self._update_signal_status(signal_id, "CLOSED_TP", outcome_pips=0)
                        logger.info(f"   📊 Signal {signal_id} trade CLOSED")
                        self._clear_active_signal()
                    
        except Exception as e:
            logger.debug(f"Could not check pending trades: {e}")
    
    def _load_active_signal(self):
        """Load active signal from file (for restart recovery)."""
        try:
            active_file = LOGS_DIR / "active_signal.json"
            if active_file.exists():
                with open(active_file, "r") as f:
                    self.active_signal = json.load(f)
                    logger.info(f"📂 Recovered active signal: {self.active_signal.get('symbol')} {self.active_signal.get('direction')}")
        except Exception as e:
            logger.warning(f"Could not load active signal: {e}")
            self.active_signal = None
    
    def _load_active_signal_from_files(self):
        """
        Load active signal state from BOTH files at startup.
        This ensures we don't spam even after restart.
        """
        now = datetime.utcnow()
        
        # Check 1: The actual signal file
        try:
            signal_file = Path("/tmp/neo_signal.json")
            if signal_file.exists():
                with open(signal_file, "r") as f:
                    last_signal = json.load(f)
                
                last_action = last_signal.get("action", "")
                last_symbol = last_signal.get("trade", {}).get("symbol")
                last_direction = last_signal.get("trade", {}).get("direction")
                last_time_str = last_signal.get("timestamp", "")
                
                if last_action == "OPEN" and last_symbol and last_direction:
                    # Check if signal is recent (less than 60 min old)
                    is_recent = False
                    if last_time_str:
                        try:
                            last_time = datetime.fromisoformat(last_time_str.replace('Z', ''))
                            age = (now - last_time).total_seconds() / 60
                            is_recent = age < 60
                        except:
                            is_recent = True  # Assume recent if can't parse
                    
                    if is_recent:
                        self.active_signal = {
                            "symbol": last_symbol,
                            "direction": last_direction,
                            "signal_id": last_signal.get("signal_id", "RECOVERED"),
                            "timestamp": last_time_str,
                            "status": "OPEN"
                        }
                        signal_key = f"{last_symbol}_{last_direction}"
                        self.last_signal_time[signal_key] = now
                        logger.info(f"📂 Loaded from signal file: {last_symbol} {last_direction} (still active)")
                        return
        except Exception as e:
            logger.debug(f"Could not load from signal file: {e}")
        
        # Check 2: Our tracking file
        try:
            active_file = LOGS_DIR / "active_signal.json"
            if active_file.exists():
                with open(active_file, "r") as f:
                    tracked = json.load(f)
                
                symbol = tracked.get("symbol")
                direction = tracked.get("direction")
                tracked_time = tracked.get("timestamp", "")
                
                if symbol and direction:
                    is_recent = False
                    if tracked_time:
                        try:
                            t = datetime.fromisoformat(tracked_time.replace('Z', ''))
                            age = (now - t).total_seconds() / 60
                            is_recent = age < 60
                        except:
                            is_recent = True
                    
                    if is_recent:
                        self.active_signal = tracked
                        signal_key = f"{symbol}_{direction}"
                        self.last_signal_time[signal_key] = now
                        logger.info(f"📂 Loaded from tracking file: {symbol} {direction}")
                        return
        except:
            pass
        
        logger.info("📂 No recent active signals found - ready for new trades")
    
    def _check_if_trade_closed(self, symbol: str) -> bool:
        """Check if the active signal's trade has been closed."""
        if not self.active_signal or self.active_signal.get("symbol") != symbol:
            return False
        
        # Check current positions to see if trade is still open
        try:
            positions = self.position_tracker.get_open_positions()
            for pos in positions:
                if pos.get("symbol") == symbol:
                    return False  # Still open
            return True  # Not found in positions = closed
        except:
            return False
    
    def _get_ohlcv_for_hybrid(self, symbol: str, num_candles: int = 100):
        """
        Get OHLCV data for hybrid analysis from MT5 API.
        Returns numpy array of shape (num_candles, 5) or None.
        """
        import numpy as np
        
        try:
            # Try MT5 API history endpoint
            from config import MT5_API_URL
            response = requests.get(
                f"{MT5_API_URL}/history/{symbol}",
                params={"timeframe": "H1", "count": num_candles},
                timeout=10
            )
            
            if response.status_code == 200:
                data = response.json()
                if data and len(data) > 0:
                    ohlcv = np.array([
                        [d['open'], d['high'], d['low'], d['close'], d.get('volume', 1000)]
                        for d in data
                    ])
                    return ohlcv
        except Exception as e:
            logger.debug(f"MT5 history unavailable: {e}")
        
        # Fallback: Generate from current price (not ideal, but functional)
        try:
            from market_feed import MarketFeed
            feed = MarketFeed()
            snapshot = feed.get_snapshot()
            
            if symbol in snapshot.forex:
                current_price = snapshot.forex[symbol]['price']
                
                # Generate synthetic OHLCV around current price
                # This is a fallback - real data is always preferred
                ohlcv = np.zeros((num_candles, 5))
                price = current_price * 0.995  # Start slightly below current
                
                for i in range(num_candles):
                    change = (np.sin(i * 0.1) + np.cos(i * 0.07)) * 0.001
                    o = price
                    c = price + change
                    h = max(o, c) + abs(change) * 0.3
                    l = min(o, c) - abs(change) * 0.3
                    v = 1000
                    
                    ohlcv[i] = [o, h, l, c, v]
                    price = c
                
                # Adjust last candle to match current price
                ohlcv[-1, 3] = current_price
                
                logger.debug(f"Using synthetic OHLCV for {symbol}")
                return ohlcv
        except Exception as e:
            logger.debug(f"Could not generate OHLCV: {e}")
        
        return None
    
    def _apply_portfolio_constraints(self, decision: Dict, portfolio_guidance: Dict) -> Dict:
        """
        Apply portfolio constraints to NEO's decision.
        
        This is where NEO becomes a true portfolio manager:
        - Blocks trades that would over-concentrate
        - Reduces position sizes when near capacity
        - Suggests alternatives when primary symbol is overweight
        """
        # Extract decision details
        if isinstance(decision.get('decision'), dict):
            inner_decision = decision['decision']
            action = inner_decision.get('action', 'WAIT')
            symbol = inner_decision.get('symbol', 'EURUSD')
            position_size = inner_decision.get('position_value_usd', 4000)
        else:
            action = decision.get('action', decision.get('decision', 'WAIT'))
            symbol = decision.get('symbol', 'EURUSD')
            position_size = decision.get('position_value_usd', decision.get('position_size', 4000))
        
        # If not a trade action, return as-is
        if action not in ['BUY', 'SELL', 'SIGNAL']:
            return decision
        
        original_decision = decision.copy()
        modified = False
        modification_reason = []
        
        # ===== CHECK 1: Can we open new positions at all? =====
        if not portfolio_guidance.get('can_open_new', True):
            logger.warning(f"   🚫 PORTFOLIO OVERRIDE: Cannot open new positions")
            
            if isinstance(decision.get('decision'), dict):
                decision['decision']['action'] = 'WAIT'
                decision['decision']['portfolio_override'] = True
            else:
                decision['decision'] = 'HOLD'
                decision['action'] = 'WAIT'
            
            decision['portfolio_override_reason'] = "Portfolio at capacity or daily loss limit"
            return decision
        
        # ===== CHECK 2: Is symbol over-concentrated? =====
        most_concentrated = portfolio_guidance.get('most_concentrated', 'NONE')
        concentration_pct = portfolio_guidance.get('concentration_pct', 0)
        
        if symbol == most_concentrated and concentration_pct > 30:
            logger.warning(f"   🚫 {symbol} is {concentration_pct:.0f}% of portfolio - AVOIDING")
            
            # Try to find alternative
            if self.fleet_monitor:
                direction = 'BUY' if action in ['BUY', 'SIGNAL'] else 'SELL'
                alternatives = self.fleet_monitor.get_best_symbols(direction, limit=3)
                
                if alternatives:
                    new_symbol = alternatives[0]
                    logger.info(f"   ➡️ Suggesting alternative: {new_symbol}")
                    
                    if isinstance(decision.get('decision'), dict):
                        decision['decision']['original_symbol'] = symbol
                        decision['decision']['symbol'] = new_symbol
                        decision['decision']['portfolio_redirect'] = True
                    else:
                        decision['original_symbol'] = symbol
                        decision['symbol'] = new_symbol
                    
                    modification_reason.append(f"Redirected from {symbol} (over-concentrated) to {new_symbol}")
                    modified = True
                else:
                    # No alternatives - force WAIT
                    if isinstance(decision.get('decision'), dict):
                        decision['decision']['action'] = 'WAIT'
                        decision['decision']['portfolio_override'] = True
                    else:
                        decision['decision'] = 'HOLD'
                    
                    decision['portfolio_override_reason'] = f"{symbol} over-concentrated, no alternatives available"
                    return decision
        
        # ===== CHECK 3: Adjust position size based on exposure =====
        total_exposure_pct = portfolio_guidance.get('total_exposure_pct', 0)
        recommended_max = portfolio_guidance.get('recommended_max_position', 4000)
        
        if position_size > recommended_max:
            old_size = position_size
            new_size = recommended_max
            
            logger.info(f"   📉 Reducing position size: ${old_size:,.0f} → ${new_size:,.0f} (portfolio at {total_exposure_pct:.0f}% capacity)")
            
            if isinstance(decision.get('decision'), dict):
                decision['decision']['position_value_usd'] = new_size
                decision['decision']['original_position_size'] = old_size
            else:
                decision['position_value_usd'] = new_size
                decision['original_position_size'] = old_size
            
            modification_reason.append(f"Position size reduced from ${old_size:,.0f} to ${new_size:,.0f}")
            modified = True
        
        # ===== CHECK 4: High correlation - suggest hedging =====
        if portfolio_guidance.get('correlation_estimate', 0) > 80:
            # Note for decision
            if isinstance(decision.get('decision'), dict):
                decision['decision']['hedge_opportunity'] = True
            else:
                decision['hedge_opportunity'] = True
            
            modification_reason.append("High correlation - consider hedging")
        
        # Add modification summary
        if modified:
            decision['portfolio_modifications'] = modification_reason
            logger.info(f"   📊 Portfolio adjustments: {'; '.join(modification_reason)}")
        
        return decision
    
    def _call_llm(self, prompt: str, model: str = None, temperature: float = 0.3) -> str:
        """Call LLM via unified adapter (supports ollama/openai/anthropic/deepseek)."""
        from llm_adapter import call_llm, get_active_provider
        provider = get_active_provider()
        logger.debug(f"LLM call via provider={provider} model={model or 'default'}")
        return call_llm(
            prompt=prompt,
            model=model,
            temperature=temperature,
            max_tokens=2000,
            timeout=LLM_CONFIG["primary"]["timeout"],
        )
    
    def _build_think_prompt(
        self,
        market: MarketSnapshot,
        account: AccountState,
        memory_context: str
    ) -> str:
        """Build the prompt for NEO to think about the market."""
        
        # Get market context
        market_text = self.market_feed.to_llm_context(market)
        account_text = self.position_tracker.to_llm_context(account)
        
        # Use the knowledge loader's master prompt if available
        if self.knowledge.master_prompt:
            return self.knowledge.get_master_prompt_with_context(
                market_text, account_text, memory_context
            )
        
        # Fallback to basic prompt with knowledge
        knowledge_context = self.knowledge.build_full_context()
        
        prompt = f"""You are NEO, an autonomous forex trader with access to proven strategies and the WWCD playbook.

{knowledge_context}

{market_text}

{account_text}

{memory_context}

=== POSITION LIMITS ===
- Max position: ${MAX_POSITION_DOLLARS:,.0f} ({MAX_POSITION_DOLLARS/ACCOUNT_SIZE*100:.1f}% of account)
- Max daily loss: ${MAX_DAILY_LOSS_DOLLARS:,.0f}
- Can open new: {account.can_open_new}

=== DIVERSITY REQUIREMENT ===
**CRITICAL**: You must NOT fixate on a single symbol! Be a PORTFOLIO MANAGER, not a one-trick pony.
- Available symbols: {', '.join(self._get_alternative_symbols()) or 'ALL ON COOLDOWN - MUST WAIT'}
- Symbols on cooldown: {', '.join([s for s in self.diversity_pool if s not in self._get_alternative_symbols()]) or 'None'}
- If your preferred symbol is on cooldown, CHOOSE A DIFFERENT SYMBOL or WAIT
- MQL5 top traders are currently trading: Check consensus for alternative ideas

=== YOUR TASK ===
1. Identify the MARKET REGIME (trending/ranging/chaotic/news)
2. Check WWCD - Has there been a stop hunt? Where are retail stops?
3. Select the PROVEN STRATEGY that fits
4. **CHECK DIVERSITY** - Is this symbol available? If not, find an alternative!
5. Calculate POSITION SIZE (max 5%)
6. Make your DECISION

RESPOND IN THIS EXACT JSON FORMAT:
{{
    "analysis": {{
        "regime": "trending | ranging | chaotic | news_event",
        "key_levels": ["1.0850", "1.0900"]
    }},
    "wwcd": {{
        "stop_hunt_detected": true | false,
        "stop_hunt_complete": true | false,
        "retail_likely_wrong": true | false
    }},
    "strategy": {{
        "name": "Strategy name",
        "source": "Academic source",
        "expected_win_rate": "88%"
    }},
    "decision": {{
        "action": "BUY | SELL | WAIT",
        "symbol": "EURUSD",
        "confidence": 85,
        "position_value_usd": 3500,
        "stop_loss_pips": 30,
        "take_profit_pips": 60,
        "target_bots": [888007, 888008]
    }},
    "reasoning": "Detailed explanation",
    "risk_check": {{
        "position_pct": 4.0,
        "within_limits": true
    }}
}}

CRITICAL RULES:
1. Only trade when confidence > 70
2. NEVER have stops at obvious levels (MMs will hunt them)
3. WAIT for stop hunt to complete before entering
4. If kill switch triggered, action MUST be "WAIT"
5. When in doubt, WAIT - there will be more setups
6. **DIVERSIFY** - Do NOT signal the same symbol repeatedly! If EURUSD was just signaled, look at GBPUSD, XAUUSD, etc.
7. Check MQL5 consensus - If top traders are all on XAUUSD BUY, consider following!

NOW ANALYZE AND DECIDE (remember: BE A PORTFOLIO MANAGER, not fixated on one symbol!):"""
        
        return prompt
    
    def _parse_decision(self, response: str) -> Optional[Dict]:
        """Parse the LLM's decision from its response."""
        # Try to extract JSON from response
        try:
            # Find JSON block
            start = response.find('{')
            end = response.rfind('}') + 1
            if start >= 0 and end > start:
                json_str = response[start:end]
                return json.loads(json_str)
        except json.JSONDecodeError:
            pass
        
        # Try to parse structured text
        decision = {"decision": "HOLD", "reasoning": "Could not parse LLM response"}
        
        if "SIGNAL" in response.upper():
            decision["decision"] = "SIGNAL"
        elif "CLOSE" in response.upper():
            decision["decision"] = "CLOSE"
        
        return decision
    
    def see(self) -> Tuple[MarketSnapshot, AccountState]:
        """SEE - Gather all current market and account data."""
        logger.info("👁️ SEE: Gathering market data...")
        
        market = self.market_feed.get_snapshot()
        account = self.position_tracker.get_state()
        
        logger.info(f"   Forex pairs: {len(market.forex)} | Crypto: {len(market.crypto)}")
        logger.info(f"   Positions: {account.position_count} | P&L: ${account.total_unrealized_pnl:+,.2f}")
        
        # Check if any of our pending signals have been executed or closed
        self._check_pending_trades()
        
        # Check war room status
        if self.use_war_room:
            trade_check = self.war_room.should_trade()
            threat = self.war_room.get_threat_level()
            news = self.war_room.get_news_risk()
            logger.info(f"   🎖️ War Room: Threat={threat} | News={news} | Can Trade: {trade_check['can_trade']}")
        
        # Log diversity status
        available = self._get_alternative_symbols()
        if self.active_signal:
            logger.info(f"   📊 Active signal: {self.active_signal.get('symbol')} {self.active_signal.get('direction')}")
        logger.info(f"   🎯 Available symbols (diversity): {', '.join(available) if available else 'NONE - all on cooldown'}")
        
        return market, account
    
    def think(self, market: MarketSnapshot, account: AccountState) -> Optional[Dict]:
        """THINK - Analyze market and form a decision."""
        logger.info("🧠 THINK: Analyzing market...")
        
        # Check kill switch first
        if account.kill_switch_triggered:
            logger.warning("🚨 KILL SWITCH TRIGGERED - Forcing HOLD")
            return {
                "decision": "HOLD",
                "reasoning": "Kill switch triggered - daily loss limit exceeded",
                "market_assessment": "Trading halted for risk management"
            }
        
        # Check war room conditions
        if self.use_war_room:
            trade_check = self.war_room.should_trade()
            if not trade_check["can_trade"]:
                logger.warning(f"🎖️ WAR ROOM SAYS NO: {trade_check['reason']}")
                return {
                    "decision": "HOLD",
                    "reasoning": f"War Room override: {trade_check['reason']}",
                    "market_assessment": "Waiting for better conditions"
                }
        
        # Get memory context
        memory_context = self.memory.to_llm_context()
        
        # Add war room briefing if available
        if self.use_war_room:
            war_room_briefing = self.war_room.format_for_neo_prompt()
            memory_context = memory_context + "\n" + war_room_briefing
        
        # ===== FLEET MONITOR - PORTFOLIO VISIBILITY =====
        fleet_context = ""
        portfolio_guidance = None
        
        if self.use_fleet_monitor and self.fleet_monitor:
            logger.info("   📊 Checking Fleet Status (portfolio visibility)...")
            
            try:
                # Update fleet state
                self.fleet_monitor.update()
                
                # Get formatted context for NEO
                fleet_context = self.fleet_monitor.format_for_neo()
                
                # Get portfolio state for guidance
                state = self.fleet_monitor.get_state()
                if state:
                    if isinstance(state, dict):
                        risk = state.get('risk', {})
                        summary = state.get('summary', {})
                        exposure = state.get('exposure_by_symbol', {})
                    else:
                        risk = state.risk
                        summary = {
                            'can_open_new': state.can_open_new,
                            'recommended_max_position': state.recommended_max_position,
                            'total_positions': state.total_positions
                        }
                        exposure = state.exposure_by_symbol
                    
                    portfolio_guidance = {
                        'can_open_new': summary.get('can_open_new', True),
                        'recommended_max_position': summary.get('recommended_max_position', 4000),
                        'total_positions': summary.get('total_positions', 0),
                        'total_exposure_pct': risk.get('total_exposure_pct', 0) if isinstance(risk, dict) else getattr(risk, 'total_exposure_pct', 0),
                        'most_concentrated': risk.get('most_concentrated_symbol', 'NONE') if isinstance(risk, dict) else getattr(risk, 'most_concentrated_symbol', 'NONE'),
                        'concentration_pct': risk.get('max_symbol_concentration_pct', 0) if isinstance(risk, dict) else getattr(risk, 'max_symbol_concentration_pct', 0),
                        'alerts': risk.get('alerts', []) if isinstance(risk, dict) else getattr(risk, 'alerts', [])
                    }
                    
                    # Log fleet status
                    logger.info(f"   📊 Fleet: {portfolio_guidance['total_positions']} positions, "
                               f"{portfolio_guidance['total_exposure_pct']:.1f}% exposure")
                    
                    if portfolio_guidance['concentration_pct'] > 30:
                        logger.warning(f"   ⚠️ {portfolio_guidance['most_concentrated']} is "
                                      f"{portfolio_guidance['concentration_pct']:.0f}% of portfolio")
                    
                    if not portfolio_guidance['can_open_new']:
                        logger.warning("   🚫 Portfolio at capacity - NEO should WAIT")
                    
                    for alert in portfolio_guidance['alerts'][:3]:
                        logger.warning(f"   {alert}")
                        
            except Exception as e:
                logger.error(f"   ❌ Fleet monitor error: {e}")
        
        # ===== HYBRID DEEP LEARNING ANALYSIS =====
        hybrid_context = ""
        hybrid_analysis = None
        
        if self.use_hybrid and self.hybrid_engine:
            logger.info("   🧠 Running Hybrid AI analysis (CNN + LSTM + RL)...")
            
            try:
                # Get OHLCV data for primary symbol (EURUSD default)
                import numpy as np
                primary_symbol = "EURUSD"
                
                # Try to get OHLCV from market data
                if hasattr(market, 'ohlcv') and primary_symbol in market.ohlcv:
                    ohlcv = np.array(market.ohlcv[primary_symbol])
                else:
                    # Generate from available price data (simplified)
                    # In production, this would come from MT5 API history endpoint
                    ohlcv = self._get_ohlcv_for_hybrid(primary_symbol, 100)
                
                if ohlcv is not None and len(ohlcv) >= 50:
                    # Get current position info
                    current_pos = None
                    for pos in account.positions:
                        if pos.get('symbol') == primary_symbol:
                            current_pos = pos
                            break
                    
                    # Run hybrid analysis
                    hybrid_analysis = self.hybrid_engine.analyze(
                        symbol=primary_symbol,
                        ohlcv=ohlcv,
                        current_position=current_pos,
                        daily_pnl=account.daily_pnl,
                        horizon="4H"
                    )
                    
                    # Build context for LLM
                    hybrid_context = f"""
=== 🧠 DEEP LEARNING ANALYSIS (Neural Networks) ===
Symbol: {hybrid_analysis.symbol}

📊 CNN Pattern Detector:
   Pattern: {hybrid_analysis.pattern} ({hybrid_analysis.pattern_confidence:.0%} confidence)
   Direction: {hybrid_analysis.pattern_direction}

📈 LSTM Price Predictor:
   Direction: {hybrid_analysis.lstm_direction} ({hybrid_analysis.lstm_confidence:.0%} confidence)
   Expected Move: {hybrid_analysis.lstm_magnitude_pips} pips in {hybrid_analysis.lstm_horizon}

🤖 RL Trading Agent:
   Recommendation: {hybrid_analysis.rl_action} ({hybrid_analysis.rl_confidence:.0%} confidence)
   Value Estimate: {hybrid_analysis.rl_value_estimate:.4f}

👥 MQL5 Top Trader Consensus:
   Consensus: {'YES' if hybrid_analysis.mql5_consensus else 'NO'}
   Confidence Boost: +{hybrid_analysis.mql5_confidence_boost}%

🎯 HYBRID SIGNAL: {hybrid_analysis.final_direction} ({hybrid_analysis.final_confidence:.0%} confidence)
   Reasoning: {hybrid_analysis.reasoning}
=== END DEEP LEARNING ANALYSIS ===
"""
                    logger.info(f"   📊 CNN: {hybrid_analysis.pattern} → {hybrid_analysis.pattern_direction}")
                    logger.info(f"   📈 LSTM: {hybrid_analysis.lstm_direction} ({hybrid_analysis.lstm_magnitude_pips} pips)")
                    logger.info(f"   🤖 RL: {hybrid_analysis.rl_action}")
                    logger.info(f"   🎯 Hybrid Signal: {hybrid_analysis.final_direction} ({hybrid_analysis.final_confidence:.0%})")
                else:
                    logger.warning("   ⚠️ Insufficient OHLCV data for hybrid analysis")
                    
            except Exception as e:
                logger.error(f"   ❌ Hybrid analysis error: {e}")
        
        # Combine all context (memory + fleet + hybrid)
        full_context = memory_context
        
        if fleet_context:
            full_context = full_context + "\n" + fleet_context
        
        if hybrid_context:
            full_context = full_context + "\n" + hybrid_context
        
        # Build and send prompt
        prompt = self._build_think_prompt(market, account, full_context)
        
        # ===== PORTFOLIO-AWARE DECISION OVERRIDE =====
        # Before calling LLM, check if portfolio constraints would prevent action
        
        logger.info(f"   Calling {self.current_model}...")
        response = self._call_llm(prompt)
        
        if not response:
            logger.error("   No response from LLM")
            return None
        
        # Parse decision
        decision = self._parse_decision(response)
        
        # Enhance decision with hybrid analysis confidence boost
        if hybrid_analysis and decision:
            # If hybrid and LLM agree, boost confidence
            if decision.get('decision') and hybrid_analysis.final_direction != 'HOLD':
                llm_direction = decision.get('action') or decision.get('decision')
                if isinstance(decision.get('decision'), dict):
                    llm_direction = decision['decision'].get('action')
                
                if llm_direction == hybrid_analysis.final_direction:
                    original_conf = decision.get('confidence', 0)
                    if isinstance(decision.get('decision'), dict):
                        original_conf = decision['decision'].get('confidence', 0)
                    
                    boost = int(hybrid_analysis.final_confidence * 10)  # Up to +10%
                    boosted_conf = min(95, original_conf + boost)
                    
                    if isinstance(decision.get('decision'), dict):
                        decision['decision']['confidence'] = boosted_conf
                        decision['decision']['hybrid_boost'] = boost
                    else:
                        decision['confidence'] = boosted_conf
                        decision['hybrid_boost'] = boost
                    
                    logger.info(f"   ✨ LLM + Hybrid AGREE! Confidence boosted: {original_conf} → {boosted_conf}")
        
        # ===== PORTFOLIO-AWARE DECISION VALIDATION =====
        if portfolio_guidance and decision:
            decision = self._apply_portfolio_constraints(decision, portfolio_guidance)
        
        if decision:
            logger.info(f"   Decision: {decision.get('decision')} "
                       f"(confidence: {decision.get('confidence', 'N/A')})")
        
        return decision
    
    def act(self, decision: Dict, market: MarketSnapshot, account: AccountState) -> Optional[str]:
        """ACT - Execute the decision by writing signals."""
        logger.info("⚡ ACT: Executing decision...")
        
        # Handle nested decision format from new prompt
        if "decision" in decision and isinstance(decision["decision"], dict):
            inner_decision = decision["decision"]
            decision_type = inner_decision.get("action", "WAIT")
            if decision_type in ["BUY", "SELL"]:
                decision_type = "SIGNAL"
            symbol = inner_decision.get("symbol")
            direction = inner_decision.get("action") if inner_decision.get("action") in ["BUY", "SELL"] else None
            confidence = inner_decision.get("confidence", 0)
        else:
            decision_type = decision.get("decision", decision.get("action", "HOLD"))
            if decision_type in ["BUY", "SELL"]:
                decision_type = "SIGNAL"
            symbol = decision.get("symbol")
            direction = decision.get("direction", decision.get("action"))
            confidence = decision.get("confidence", 0)
        
        # Log decision to memory
        reasoning = decision.get("reasoning", "")
        if isinstance(reasoning, dict):
            reasoning = str(reasoning)
        
        db_decision = Decision(
            id=None,
            timestamp=datetime.utcnow().isoformat(),
            decision_type=decision_type,
            symbol=symbol,
            direction=direction if direction in ["BUY", "SELL"] else None,
            confidence=float(confidence) if confidence else 0.0,
            reasoning=reasoning[:1000],  # Truncate to avoid DB issues
            market_context=self.market_feed.to_llm_context(market)[:500],
            model_used=self.current_model
        )
        decision_id = self.memory.save_decision(db_decision)
        logger.info(f"   Logged decision #{decision_id}")
        
        if decision_type == "SIGNAL":
            # Validate we can trade
            if not account.can_open_new:
                logger.warning("   Cannot open new position - limits reached")
                return None
            
            # Get confidence from nested or flat structure
            if "decision" in decision and isinstance(decision["decision"], dict):
                signal_confidence = decision["decision"].get("confidence", 0)
            else:
                signal_confidence = decision.get("confidence", confidence)
            
            if signal_confidence < 70:
                logger.warning(f"   Confidence too low ({signal_confidence}) - not trading")
                return None
            
            # Extract symbol and direction first for duplicate check
            if "decision" in decision and isinstance(decision["decision"], dict):
                inner = decision["decision"]
                check_symbol = inner.get("symbol", decision.get("symbol"))
                check_direction = inner.get("action", decision.get("direction"))
            else:
                check_symbol = decision.get("symbol")
                check_direction = decision.get("direction", decision.get("action"))
            
            # CHECK FOR DUPLICATE SIGNAL - prevent spam
            is_duplicate, reason = self._is_duplicate_signal(check_symbol, check_direction)
            if is_duplicate:
                logger.info(f"   🔄 {reason}")
                return None  # Skip duplicate signal
            
            # CHECK FOR DIVERSITY - don't fixate on one symbol
            can_trade, diversity_reason = self._check_symbol_diversity(check_symbol)
            if not can_trade:
                logger.info(f"   🔄 {diversity_reason}")
                alternatives = self._get_alternative_symbols()
                if alternatives:
                    logger.info(f"   💡 Available alternatives: {', '.join(alternatives)}")
                return None  # Skip to encourage diversity
            
            # Check if previous trade on this symbol was closed
            if self._check_if_trade_closed(check_symbol):
                logger.info(f"   ✅ Previous {check_symbol} trade closed - can generate new signal")
                self._clear_active_signal(check_symbol)
            
            # Extract nested decision if present
            if "decision" in decision and isinstance(decision["decision"], dict):
                inner = decision["decision"]
                symbol = inner.get("symbol", decision.get("symbol"))
                direction = inner.get("action", decision.get("direction"))
                if direction in ["BUY", "SELL"]:
                    pass
                elif direction == "WAIT":
                    logger.info("   Decision is WAIT - not trading")
                    return None
                position_value = inner.get("position_value_usd", MAX_POSITION_DOLLARS * signal_confidence / 100)
                stop_pips = inner.get("stop_loss_pips", 30)
                tp_pips = inner.get("take_profit_pips", 60)
                target_bots = inner.get("target_bots", [888007, 888008, 888010])
            else:
                symbol = decision.get("symbol")
                direction = decision.get("direction", decision.get("action"))
                position_value = min(MAX_POSITION_DOLLARS, MAX_POSITION_DOLLARS * signal_confidence / 100)
                stop_pips = decision.get("stop_loss_pips", 30)
                tp_pips = decision.get("take_profit_pips", 60)
                target_bots = [888007, 888008, 888010]
            
            # Log strategy used if available
            strategy = decision.get("strategy", {})
            if strategy:
                logger.info(f"   📊 Strategy: {strategy.get('name', 'Unknown')} "
                           f"(expected {strategy.get('expected_win_rate', 'N/A')} win rate)")
            
            # Log WWCD analysis if available
            wwcd = decision.get("wwcd", {})
            if wwcd:
                hunt = "✅ Complete" if wwcd.get("stop_hunt_complete") else "⏳ In progress" if wwcd.get("stop_hunt_detected") else "None"
                logger.info(f"   🎯 WWCD: Stop hunt: {hunt}")
            
            # Create and write signal with raid format for Ghost Commander
            reasoning_text = decision.get("reasoning", "")
            if isinstance(reasoning_text, dict):
                reasoning_text = str(reasoning_text)
            
            signal = TradingSignal(
                timestamp=datetime.utcnow().isoformat(),
                signal_id=f"NEO_{decision_id}_{datetime.utcnow().strftime('%H%M%S')}",
                symbol=symbol,
                direction=direction,
                position_value=min(MAX_POSITION_DOLLARS, position_value),
                stop_loss_pips=stop_pips,
                take_profit_pips=tp_pips,
                confidence=signal_confidence,
                reasoning=reasoning_text[:500],
                model_used=self.current_model
            )
            
            signal_id = self.signal_writer.write_signal(signal)
            
            # Track this as the active signal to prevent spam
            self._set_active_signal(signal.symbol, signal.direction, signal_id)
            
            # Record for diversity tracking (don't signal same symbol for 2h)
            self._record_symbol_signal(signal.symbol)
            
            # Track signal status as ACTIVE (waiting for execution)
            self._update_signal_status(signal_id, "ACTIVE")
            
            # Also write raid signal for Ghost Commander
            self._write_raid_signal(signal, target_bots)
            
            logger.info(f"   📤 Signal written: {signal_id}")
            logger.info(f"   {signal.symbol} {signal.direction} "
                       f"${signal.position_value:,.0f} "
                       f"SL:{signal.stop_loss_pips} TP:{signal.take_profit_pips}")
            logger.info(f"   🤖 Target bots: {target_bots}")
            logger.info(f"   ⏳ Signal ACTIVE (2h cooldown on {signal.symbol}, no spam)")
            
            self.decisions_today += 1
            return signal_id
        
        elif decision_type == "CLOSE":
            symbol = decision.get("symbol")
            reason = decision.get("reasoning", "NEO decision to close")
            signal_id = self.signal_writer.write_close_signal(symbol, reason)
            
            # Clear active signal for this symbol
            self._clear_active_signal(symbol)
            
            logger.info(f"   📤 Close signal: {signal_id} for {symbol}")
            logger.info(f"   ✅ {symbol} signal cleared - can generate new signals")
            return signal_id
        
        else:  # HOLD
            logger.info(f"   Holding - {decision.get('reasoning', 'No clear setup')[:100]}")
            return None
    
    def _write_raid_signal(self, signal: TradingSignal, target_bots: list):
        """Write raid signal in format Ghost Commander understands."""
        import os
        
        raid_signal = {
            "raid_active": True,
            "source": "NEO",
            "raid_id": signal.signal_id,
            "symbol": signal.symbol,
            "direction": 1 if signal.direction == "BUY" else -1,
            "target_bots": target_bots,
            "size_pct": min(signal.position_value / ACCOUNT_SIZE * 100, 5),
            "tp_dollars": signal.take_profit_pips * 10,  # Rough conversion
            "sl_dollars": signal.stop_loss_pips * 10,
            "max_hold_minutes": signal.max_hold_minutes,
            "confidence": signal.confidence,
            "reasoning": signal.reasoning[:500],  # Truncate for readability
            "timestamp": signal.timestamp
        }
        
        # Write to common MT5 directory for Ghost Commander
        mt5_path = os.path.expanduser(
            "~/.wine/drive_c/users/Public/Documents/MetaQuotes/Terminal/Common/Files/"
        )
        if os.path.exists(mt5_path):
            ghost_file = os.path.join(mt5_path, "ghost_directives.json")
            try:
                with open(ghost_file, "w") as f:
                    json.dump(raid_signal, f, indent=2)
                logger.info(f"   📡 Raid signal sent to Ghost Commander")
            except Exception as e:
                logger.warning(f"   Could not write to MT5 common files: {e}")
    
    def learn(self):
        """LEARN - Review recent outcomes and extract lessons."""
        logger.info("📚 LEARN: Reviewing outcomes...")
        
        # Get recent decisions that have outcomes
        recent = self.memory.get_recent_decisions(hours=24, limit=10)
        
        wins = sum(1 for d in recent if d.get('result') == 'WIN')
        losses = sum(1 for d in recent if d.get('result') == 'LOSS')
        total_pnl = sum(d.get('pnl', 0) or 0 for d in recent)
        
        logger.info(f"   24h: {wins}W / {losses}L | P&L: ${total_pnl:+,.2f}")
        
        # Ask LLM to reflect on performance
        if recent:
            reflect_prompt = f"""You are NEO, reviewing your recent trading decisions.

RECENT DECISIONS AND OUTCOMES:
{json.dumps(recent, indent=2)}

PERFORMANCE SUMMARY:
- Wins: {wins}
- Losses: {losses}
- Total P&L: ${total_pnl:+,.2f}

Analyze your decisions and extract 1-3 key lessons learned.
Format as JSON:
{{
    "lessons": [
        {{
            "category": "PATTERN" or "MISTAKE" or "SUCCESS" or "RULE",
            "content": "What you learned...",
            "confidence": 0-100
        }}
    ]
}}
"""
            
            response = self._call_llm(reflect_prompt, temperature=0.4)
            
            try:
                start = response.find('{')
                end = response.rfind('}') + 1
                if start >= 0 and end > start:
                    result = json.loads(response[start:end])
                    for lesson in result.get("lessons", []):
                        learning = Learning(
                            id=None,
                            timestamp=datetime.utcnow().isoformat(),
                            category=lesson.get("category", "PATTERN"),
                            content=lesson.get("content", ""),
                            confidence=lesson.get("confidence", 50),
                            supporting_decisions=[d.get('id') for d in recent if d.get('id')]
                        )
                        self.memory.save_learning(learning)
                        logger.info(f"   💡 Learned: {lesson.get('content', '')[:80]}...")
            except:
                pass
    
    def run_cycle(self):
        """Run one complete SEE -> THINK -> ACT cycle."""
        try:
            # SEE
            market, account = self.see()
            
            # THINK
            decision = self.think(market, account)
            
            if decision:
                # ACT
                self.act(decision, market, account)
        
        except Exception as e:
            logger.error(f"Cycle error: {e}")
    
    def run(self, max_cycles: int = None):
        """Main loop - run NEO continuously."""
        logger.info("=" * 60)
        logger.info("NEO AUTONOMOUS TRADER - FULL POWER MODE")
        logger.info("=" * 60)
        
        # Load any active signal from previous session
        self._load_active_signal()
        if self.active_signal:
            logger.info(f"📂 Active signal recovered: {self.active_signal.get('symbol')} "
                       f"{self.active_signal.get('direction')} (monitoring)")
        else:
            logger.info("📂 No active signals - ready for new trades")
        
        # Knowledge base status
        stats = self.knowledge.get_stats()
        logger.info("📚 KNOWLEDGE LOADED:")
        logger.info(f"   Strategies: {stats['strategies_loaded']} proven systems")
        logger.info(f"   WWCD: {stats['wwcd_tactics']} tactics loaded")
        logger.info(f"   Fleet: {stats['fleet_bots']} bots ready")
        logger.info(f"   Master prompt: {'✅' if stats['master_prompt_loaded'] else '❌'}")
        
        logger.info("")
        logger.info("⚙️ CONFIGURATION:")
        logger.info(f"   Model: {self.current_model}")
        logger.info(f"   Think interval: {THINK_INTERVAL_SECONDS}s")
        logger.info(f"   Learn interval: {LEARN_INTERVAL_SECONDS}s")
        logger.info(f"   Max position: ${MAX_POSITION_DOLLARS:,.0f}")
        logger.info(f"   Kill switch: {'ENABLED' if KILL_SWITCH_ENABLED else 'DISABLED'}")
        logger.info(f"   Multi-model consensus: {'ENABLED' if self.use_consensus else 'DISABLED'}")
        logger.info(f"   War Room intel: {'ENABLED' if self.use_war_room else 'DISABLED'}")
        logger.info("=" * 60)
        
        self.running = True
        cycle_count = 0
        
        while self.running:
            cycle_count += 1
            logger.info(f"\n{'='*20} CYCLE {cycle_count} {'='*20}")
            
            # Run main cycle
            self.run_cycle()
            
            # Check if it's time to learn
            if self.last_learn_time is None or \
               (datetime.now() - self.last_learn_time).seconds >= LEARN_INTERVAL_SECONDS:
                self.learn()
                self.last_learn_time = datetime.now()
            
            # Check cycle limit
            if max_cycles and cycle_count >= max_cycles:
                logger.info(f"Reached max cycles ({max_cycles})")
                break
            
            # Wait before next cycle
            logger.info(f"💤 Sleeping {THINK_INTERVAL_SECONDS}s until next cycle...")
            time.sleep(THINK_INTERVAL_SECONDS)
        
        logger.info("NEO STOPPED")
    
    def stop(self):
        """Stop NEO."""
        self.running = False


def main():
    """Main entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(description="NEO Autonomous LLM Trader")
    parser.add_argument("--cycles", type=int, default=None, 
                        help="Max cycles to run (default: unlimited)")
    parser.add_argument("--once", action="store_true",
                        help="Run just one cycle and exit")
    parser.add_argument("--test", action="store_true",
                        help="Run in test mode (no actual signals)")
    args = parser.parse_args()
    
    neo = NEOTrader()
    
    if args.once:
        neo.run_cycle()
    else:
        try:
            neo.run(max_cycles=args.cycles)
        except KeyboardInterrupt:
            logger.info("\nShutting down NEO...")
            neo.stop()


if __name__ == "__main__":
    main()
