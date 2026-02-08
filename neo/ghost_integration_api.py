#!/usr/bin/env python3
"""
═══════════════════════════════════════════════════════════════════════════════
NEO → GHOST INTEGRATION API
═══════════════════════════════════════════════════════════════════════════════

Provides API endpoints for Ghost Commander to:
1. Fetch daily trading plan
2. Get real-time signal updates
3. Report trade results back for learning

Flow:
  NEO Pre-Market → JSON API → Ghost reads → Auto trading → Results back → NEO learns

Endpoints:
  GET  /api/neo/xauusd/daily-plan    - Get today's XAUUSD trading plan
  GET  /api/neo/iren/daily-plan      - Get today's IREN trading plan
  POST /api/neo/trade-result         - Report trade result for learning
  GET  /api/neo/learning-stats       - Get accuracy statistics

═══════════════════════════════════════════════════════════════════════════════
"""

import os
import sys
import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

# Add parent paths
sys.path.insert(0, '/home/jbot/trading_ai/neo')
sys.path.insert(0, '/home/jbot/trading_ai')

# Forex Intel Spy Service
try:
    from forex_intel_service import forex_intel_router, start_forex_intel, stop_forex_intel
    FOREX_INTEL_AVAILABLE = True
except ImportError as e:
    FOREX_INTEL_AVAILABLE = False
    print(f"WARNING: Forex Intel Service not available: {e}")

# Truth Analyzer — Research Auditor (analysis only, no signals)
try:
    from truth_analyzer import truth_analyzer_router, start_truth_analyzer, stop_truth_analyzer, auto_ingest_from_cache
    TRUTH_ANALYZER_AVAILABLE = True
except ImportError as e:
    TRUTH_ANALYZER_AVAILABLE = False
    print(f"WARNING: Truth Analyzer not available: {e}")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s:%(name)s:%(message)s'
)
logger = logging.getLogger("GhostIntegration")

# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

DATA_DIR = Path("/home/jbot/trading_ai/neo/daily_data")
DATA_DIR.mkdir(exist_ok=True)

RESULTS_DIR = Path("/home/jbot/trading_ai/neo/trade_results")
RESULTS_DIR.mkdir(exist_ok=True)

LEARNING_FILE = DATA_DIR / "learning_stats.json"

# ═══════════════════════════════════════════════════════════════════════════════
# SIGNAL STATE TRACKING - CRITICAL FIX FOR EA POSITION STACKING
# ═══════════════════════════════════════════════════════════════════════════════
# 
# BUG: Previously generated new signal_id every 60 seconds regardless of direction
# RESULT: EAs saw "new signal" and opened positions → 8 stacked SELLs in minutes
# 
# FIX: Only generate new signal_id when DIRECTION CHANGES
# Same direction = same signal_id = EAs don't open duplicate positions
#
_signal_state = {
    "last_direction": None,        # BUY, SELL, HOLD, or None
    "current_signal_id": None,     # Current signal ID (only changes on direction change)
    "direction_since": None,       # When this direction started
    "signals_same_direction": 0,   # How many signals generated with same direction
}

def get_or_create_signal_id(new_direction: str) -> tuple:
    """
    Get signal_id - ONLY creates new ID when direction changes.
    
    Returns: (signal_id, is_new_signal, direction_age_seconds)
    """
    global _signal_state
    
    now = datetime.utcnow()
    
    # Direction changed? Create new signal_id
    if new_direction != _signal_state["last_direction"]:
        new_id = f"NEO_XAUUSD_{now.strftime('%Y%m%d_%H%M%S')}"
        
        logger.info(f"🔄 DIRECTION CHANGE: {_signal_state['last_direction']} → {new_direction}")
        logger.info(f"📝 NEW signal_id: {new_id}")
        
        _signal_state["last_direction"] = new_direction
        _signal_state["current_signal_id"] = new_id
        _signal_state["direction_since"] = now
        _signal_state["signals_same_direction"] = 1
        
        return new_id, True, 0
    
    # Same direction - keep same signal_id
    _signal_state["signals_same_direction"] += 1
    direction_age = (now - _signal_state["direction_since"]).total_seconds() if _signal_state["direction_since"] else 0
    
    logger.info(f"↔️ SAME DIRECTION: {new_direction} (signal #{_signal_state['signals_same_direction']}, age: {direction_age:.0f}s)")
    
    return _signal_state["current_signal_id"], False, direction_age


# ═══════════════════════════════════════════════════════════════════════════════
# FASTAPI APP
# ═══════════════════════════════════════════════════════════════════════════════

app = FastAPI(
    title="NEO Ghost Integration API",
    description="API for Ghost Commander to fetch trading plans and report results",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount Forex Intel Spy Service router
if FOREX_INTEL_AVAILABLE:
    app.include_router(forex_intel_router)
    logger.info("Forex Intel Spy Service router mounted")

    @app.on_event("startup")
    async def on_startup():
        await start_forex_intel()

    @app.on_event("shutdown")
    async def on_shutdown():
        await stop_forex_intel()

# Mount Truth Analyzer router (research auditor — analysis only)
if TRUTH_ANALYZER_AVAILABLE:
    app.include_router(truth_analyzer_router)
    logger.info("Truth Analyzer router mounted (RESEARCH AUDITOR mode)")

    @app.on_event("startup")
    async def on_startup_truth():
        await start_truth_analyzer()

    @app.on_event("shutdown")
    async def on_shutdown_truth():
        await stop_truth_analyzer()


# ═══════════════════════════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

def calculate_weighted_pips_dca(current_price: float, symbol: str) -> Dict:
    """
    Calculate DCA ladder using WEIGHTED PIPS for stocks.
    
    Formula: 100 weighted pips = 1.5% of stock price
    This ensures "100 pip drop" means the same thing for all stocks!
    
    Works for: IREN ($62), CLSK ($12), CIFR ($17), or ANY stock price.
    
    For Gold (XAUUSD), we use standard forex pips, not weighted pips.
    """
    pip_100_value = current_price * 0.015  # 100 pips = 1.5%
    pip_1_value = current_price * 0.00015  # 1 pip = 0.015%
    
    dca_ladder = {
        "note": f"Weighted Pips: 100 pips = 1.5% = ${pip_100_value:.2f}. Scales to any stock!",
        "symbol": symbol,
        "reference_price": current_price,
        "pip_value": round(pip_1_value, 4),
        "pip_100_value": round(pip_100_value, 2),
        "L1": {
            "price": current_price,
            "pips": 0,
            "drop_pct": "0%",
            "drop_usd": "$0.00",
            "size": "1 contract",
            "status": "ENTRY"
        },
        "L2": {
            "price": round(current_price * 0.985, 2),
            "pips": -100,
            "drop_pct": "-1.5%",
            "drop_usd": f"-${pip_100_value:.2f}",
            "size": "1 contract",
            "status": "WAITING"
        },
        "L3": {
            "price": round(current_price * 0.970, 2),
            "pips": -200,
            "drop_pct": "-3.0%",
            "drop_usd": f"-${pip_100_value * 2:.2f}",
            "size": "2 contracts",
            "status": "WAITING"
        },
        "L4": {
            "price": round(current_price * 0.950, 2),
            "pips": -333,
            "drop_pct": "-5.0%",
            "drop_usd": f"-${current_price * 0.05:.2f}",
            "size": "2 contracts",
            "status": "WAITING"
        },
        "L5": {
            "price": round(current_price * 0.920, 2),
            "pips": -533,
            "drop_pct": "-8.0%",
            "drop_usd": f"-${current_price * 0.08:.2f}",
            "size": "3 contracts (MAX)",
            "status": "WAITING"
        },
    }
    
    return dca_ladder


def calculate_weighted_pips_tp(current_price: float, symbol: str) -> Dict:
    """
    Calculate Take Profit ladder using weighted pips.
    
    TP targets based on thesis strength:
    - TP1: +10% (conservative)
    - TP2: +25% (moderate)  
    - TP3: +50%+ (moonshot - for LEAPs)
    """
    return {
        "note": "Take profits scaled to thesis. Always keep runners for moonshot!",
        "symbol": symbol,
        "reference_price": current_price,
        "TP1": {
            "price": round(current_price * 1.10, 2),
            "gain_pct": "+10%",
            "gain_usd": f"+${current_price * 0.10:.2f}",
            "action": "SELL_33%",
            "pips": 667
        },
        "TP2": {
            "price": round(current_price * 1.25, 2),
            "gain_pct": "+25%",
            "gain_usd": f"+${current_price * 0.25:.2f}",
            "action": "SELL_33%",
            "pips": 1667
        },
        "TP3": {
            "price": round(current_price * 1.50, 2),
            "gain_pct": "+50%",
            "gain_usd": f"+${current_price * 0.50:.2f}",
            "action": "SELL_REMAINING",
            "pips": 3333
        },
        "MOONSHOT": {
            "price": round(current_price * 2.50, 2),
            "gain_pct": "+150%",
            "note": "Keep LEAPs for this! Never sell all."
        }
    }


# BTC Miner specific thesis
BTC_MINER_THESIS = {
    "IREN": {
        "name": "Iris Energy",
        "thesis": "AI hyperscaling + Microsoft contract + legacy energy rights",
        "target": 150.00,
        "invalidation": "Contract cancellation or AI demand collapse",
        "moat": "3-5 year energy permit advantage in Texas"
    },
    "CLSK": {
        "name": "CleanSpark",
        "thesis": "Largest US miner by hashrate + AI pivot potential",
        "target": 40.00,
        "invalidation": "BTC below $60K sustained or AI pivot fails",
        "moat": "Massive mining infrastructure convertible to AI"
    },
    "CIFR": {
        "name": "Cipher Mining",
        "thesis": "AI data center pivot + Texas energy assets",
        "target": 50.00,
        "invalidation": "No AI contracts by Q2 2026",
        "moat": "Strategic land and power agreements"
    }
}


def calculate_options_packet_cost(current_price: float, symbol: str) -> Dict:
    """
    Calculate OPTIONS-ONLY DCA with packet costs.
    
    1 Packet = ~$2,000 risk (equivalent to Gold 0.5 lot)
    
    Options cost estimate based on stock price:
    - Premium ≈ 8-12% of strike for 30 DTE ATM calls
    - 1 contract = 100 shares exposure
    """
    # Estimate ATM call premium (30 DTE)
    # Rule of thumb: ATM 30 DTE call ≈ 8-12% of stock price for volatile stocks
    premium_pct = 0.10  # 10% estimate for BTC miners (high IV)
    atm_premium_per_share = current_price * premium_pct
    atm_premium_per_contract = atm_premium_per_share * 100
    
    # 1 Packet = ~$2,000 risk (matches Gold 0.5 lot equivalent)
    target_packet_cost = 2000
    contracts_per_packet = max(1, int(target_packet_cost / atm_premium_per_contract))
    actual_packet_cost = contracts_per_packet * atm_premium_per_contract
    
    # DCA Ladder - OPTIONS ONLY (no shares!)
    # Using weighted pips: 100 pips = 1.5% drop
    pip_100_value = current_price * 0.015
    
    return {
        "mode": "OPTIONS_ONLY",
        "symbol": symbol,
        "current_price": current_price,
        
        # Packet Definition
        "packet": {
            "definition": "1 packet ≈ $2,000 risk (same as Gold 0.5 lot)",
            "atm_premium_estimate": round(atm_premium_per_contract, 0),
            "contracts_per_packet": contracts_per_packet,
            "actual_cost": round(actual_packet_cost, 0),
            "strike": int(current_price),  # ATM
            "expiry": "30-45 DTE recommended",
        },
        
        # Weighted Pips for DCA timing
        "pip_system": {
            "note": "100 weighted pips = 1.5% drop = time to add!",
            "pip_100_value": round(pip_100_value, 2),
        },
        
        # OPTIONS DCA Ladder
        "dca_ladder": {
            "L1": {
                "trigger": "Entry",
                "price": current_price,
                "pips": 0,
                "contracts": contracts_per_packet,
                "cost": round(actual_packet_cost, 0),
                "strike": int(current_price),
                "note": "ATM call, 30-45 DTE"
            },
            "L2": {
                "trigger": "-100 pips (-1.5%)",
                "price": round(current_price * 0.985, 2),
                "pips": -100,
                "contracts": contracts_per_packet,
                "cost": round(actual_packet_cost * 0.85, 0),  # Cheaper premium
                "strike": int(current_price * 0.985),
                "note": "Lower strike = cheaper premium"
            },
            "L3": {
                "trigger": "-200 pips (-3%)",
                "price": round(current_price * 0.970, 2),
                "pips": -200,
                "contracts": contracts_per_packet * 2,
                "cost": round(actual_packet_cost * 0.70 * 2, 0),
                "strike": int(current_price * 0.970),
                "note": "Double up on deeper dip"
            },
            "L4": {
                "trigger": "-333 pips (-5%)",
                "price": round(current_price * 0.950, 2),
                "pips": -333,
                "contracts": contracts_per_packet * 2,
                "cost": round(actual_packet_cost * 0.55 * 2, 0),
                "strike": int(current_price * 0.950),
                "note": "Aggressive add"
            },
            "L5": {
                "trigger": "-533 pips (-8%)",
                "price": round(current_price * 0.920, 2),
                "pips": -533,
                "contracts": contracts_per_packet * 3,
                "cost": round(actual_packet_cost * 0.40 * 3, 0),
                "strike": int(current_price * 0.920),
                "note": "MAX POSITION - deep discount"
            },
        },
        
        # Total max exposure
        "max_exposure": {
            "total_contracts": contracts_per_packet * 9,  # 1+1+2+2+3
            "total_cost": round(actual_packet_cost * (1 + 0.85 + 1.40 + 1.10 + 1.20), 0),
            "max_risk": "Premium paid only - defined risk!"
        },
        
        # Take Profit for options
        "tp_ladder": {
            "TP1": {
                "stock_price": round(current_price * 1.10, 2),
                "gain_pct": "+10%",
                "option_gain": "~50-80% on contracts",
                "action": "SELL_33%"
            },
            "TP2": {
                "stock_price": round(current_price * 1.25, 2),
                "gain_pct": "+25%",
                "option_gain": "~150-200% on contracts",
                "action": "SELL_33%"
            },
            "TP3": {
                "stock_price": round(current_price * 1.50, 2),
                "gain_pct": "+50%",
                "option_gain": "~300-500% on contracts",
                "action": "SELL_REMAINING"
            },
        },
        
        # Gold correlation
        "gold_correlation": {
            "theory": "Gold may LEAD miners by 1-2 days",
            "strategy": "If Gold breaks out → Buy miner calls next day",
            "correlation_estimate": "0.60-0.75 in risk-on regime"
        }
    }


def get_today_prediction(symbol: str) -> Optional[Dict]:
    """Load today's prediction file"""
    date_str = datetime.utcnow().strftime("%Y%m%d")
    file_path = DATA_DIR / f"{symbol}_{date_str}.json"
    
    if file_path.exists():
        with open(file_path) as f:
            return json.load(f)
    return None


def get_learning_stats() -> Dict:
    """Get accumulated learning statistics"""
    if LEARNING_FILE.exists():
        with open(LEARNING_FILE) as f:
            return json.load(f)
    return {
        "total_trades": 0,
        "wins": 0,
        "losses": 0,
        "breakeven": 0,
        "win_rate": 0.0,
        "total_pips": 0.0,
        "avg_pips_per_trade": 0.0,
        "best_trade": 0.0,
        "worst_trade": 0.0,
        "hunt_accuracy": 0.0,
        "direction_accuracy": 0.0,
        "by_strategy": {},
        "by_day_of_week": {},
        "recent_trades": []
    }


def update_learning_stats(result: Dict):
    """Update learning statistics with new trade result"""
    stats = get_learning_stats()
    
    pnl = result.get("pnl_pips", 0)
    
    stats["total_trades"] += 1
    stats["total_pips"] += pnl
    
    if pnl > 5:
        stats["wins"] += 1
    elif pnl < -5:
        stats["losses"] += 1
    else:
        stats["breakeven"] += 1
    
    if stats["total_trades"] > 0:
        stats["win_rate"] = (stats["wins"] / stats["total_trades"]) * 100
        stats["avg_pips_per_trade"] = stats["total_pips"] / stats["total_trades"]
    
    if pnl > stats.get("best_trade", 0):
        stats["best_trade"] = pnl
    if pnl < stats.get("worst_trade", 0):
        stats["worst_trade"] = pnl
    
    # Track by strategy
    strategy = result.get("strategy", "UNKNOWN")
    if strategy not in stats["by_strategy"]:
        stats["by_strategy"][strategy] = {"trades": 0, "wins": 0, "pips": 0}
    stats["by_strategy"][strategy]["trades"] += 1
    if pnl > 5:
        stats["by_strategy"][strategy]["wins"] += 1
    stats["by_strategy"][strategy]["pips"] += pnl
    
    # Track by day
    day = datetime.utcnow().strftime("%A")
    if day not in stats["by_day_of_week"]:
        stats["by_day_of_week"][day] = {"trades": 0, "wins": 0, "pips": 0}
    stats["by_day_of_week"][day]["trades"] += 1
    if pnl > 5:
        stats["by_day_of_week"][day]["wins"] += 1
    stats["by_day_of_week"][day]["pips"] += pnl
    
    # Track hunt/direction accuracy
    if result.get("hunt_hit"):
        stats["hunt_accuracy"] = ((stats.get("hunt_accuracy", 0) * (stats["total_trades"] - 1) + 100) / stats["total_trades"])
    else:
        stats["hunt_accuracy"] = ((stats.get("hunt_accuracy", 0) * (stats["total_trades"] - 1)) / stats["total_trades"])
    
    if result.get("direction_correct"):
        stats["direction_accuracy"] = ((stats.get("direction_accuracy", 0) * (stats["total_trades"] - 1) + 100) / stats["total_trades"])
    else:
        stats["direction_accuracy"] = ((stats.get("direction_accuracy", 0) * (stats["total_trades"] - 1)) / stats["total_trades"])
    
    # Keep last 20 trades
    stats["recent_trades"] = stats.get("recent_trades", [])[-19:] + [{
        "date": datetime.utcnow().isoformat(),
        "symbol": result.get("symbol"),
        "strategy": strategy,
        "pnl": pnl,
        "direction_correct": result.get("direction_correct", False)
    }]
    
    # Save
    with open(LEARNING_FILE, 'w') as f:
        json.dump(stats, f, indent=2)
    
    return stats


# ═══════════════════════════════════════════════════════════════════════════════
# API ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/")
async def root():
    """API root"""
    return {
        "service": "NEO Ghost Integration API",
        "version": "1.0.0",
        "status": "online",
        "endpoints": {
            "xauusd_plan": "/api/neo/xauusd/daily-plan",
            "iren_plan": "/api/neo/iren/daily-plan",
            "trade_result": "/api/neo/trade-result",
            "learning_stats": "/api/neo/learning-stats"
        }
    }


@app.get("/health")
async def health():
    """Health check"""
    return {"status": "healthy", "timestamp": datetime.utcnow().isoformat()}


# ═══════════════════════════════════════════════════════════════════════════════
# XAUUSD DAILY PLAN
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/api/neo/xauusd/daily-plan")
async def get_xauusd_daily_plan():
    """
    Get today's XAUUSD trading plan for Ghost Commander.
    
    Returns a structured plan that Ghost can execute:
    - Strategy type (FADE_THE_SQUEEZE, BUY_THE_DIP, etc.)
    - Entry conditions
    - Stop loss and take profit levels
    - Risk parameters
    """
    try:
        # Try to load today's prediction
        pred = get_today_prediction("XAUUSD")
        
        if not pred:
            # Generate fresh prediction if none exists
            logger.info("No prediction found, generating fresh...")
            from daily_reports import XAUUSDReports
            reporter = XAUUSDReports()
            reporter.generate_premarket()  # This saves the prediction
            pred = get_today_prediction("XAUUSD")
        
        if not pred:
            raise HTTPException(status_code=404, detail="Could not generate daily plan")
        
        # Determine strategy type
        expected_hunt = pred.get("expected_direction", "NONE")
        
        if expected_hunt == "SHORTS":
            strategy = "FADE_THE_SQUEEZE"
            direction = "SELL"
            entry_condition = f"Price breaks above {pred.get('hunt_target', 0):.0f} then fails"
            wait_for = f"squeeze_above_{pred.get('hunt_target', 0):.0f}"
        elif expected_hunt == "LONGS":
            strategy = "BUY_THE_DIP"
            direction = "BUY"
            entry_condition = f"Price drops to {pred.get('hunt_target', 0):.0f} and reverses"
            wait_for = f"hunt_at_{pred.get('hunt_target', 0):.0f}"
        else:
            strategy = "WAIT"
            direction = "NONE"
            entry_condition = "No clear setup - wait for better opportunity"
            wait_for = "none"
        
        # Calculate position size based on risk
        risk_pips = abs(pred.get("entry_level", 0) - pred.get("stop_loss", 0)) or 20
        risk_pct = 1.0  # 1% account risk
        
        # Get current price for reference
        import yfinance as yf
        try:
            data = yf.Ticker("GC=F").history(period="1d", interval="1m")
            current_price = float(data['Close'].iloc[-1]) if not data.empty else pred.get("pre_market_price", 0)
        except:
            current_price = pred.get("pre_market_price", 0)
        
        plan = {
            "signal_id": f"NEO_DAILY_{datetime.utcnow().strftime('%Y%m%d')}",
            "timestamp": datetime.utcnow().isoformat(),
            "date": pred.get("date"),
            "symbol": "XAUUSD",
            "current_price": current_price,
            
            # Strategy
            "strategy": strategy,
            "direction": direction,
            "confidence": pred.get("primary_probability", 50),
            
            # Entry conditions
            "entry": {
                "type": "LIMIT" if strategy != "WAIT" else "NONE",
                "condition": entry_condition,
                "wait_for": wait_for,
                "level": pred.get("entry_level", 0),
                "hunt_target": pred.get("hunt_target", 0),
                "valid_from": "08:00 UTC",  # London open
                "valid_until": "20:00 UTC",  # Before Asia
            },
            
            # Risk management
            "risk": {
                "stop_loss": pred.get("stop_loss", 0),
                "take_profit_1": pred.get("take_profit", 0),
                "take_profit_2": pred.get("take_profit", 0) + (30 if direction == "BUY" else -30),
                "risk_pips": risk_pips,
                "risk_percent": risk_pct,
                "max_position_usd": 5000,
                "trail_stop_after": 20,  # Trail after 20 pips profit
            },
            
            # Execution rules
            "execution": {
                "wait_for_reversal_candle": True,
                "min_reversal_size": 5,  # pips
                "confirmation_timeframe": "M15",
                "avoid_times": ["12:00-13:00 UTC"],  # London lunch
                "best_times": ["08:00-09:00 UTC", "13:00-14:00 UTC"],
            },
            
            # Context
            "context": {
                "overnight_bias": pred.get("overnight_bias", "UNKNOWN"),
                "overnight_high": pred.get("overnight_high", 0),
                "overnight_low": pred.get("overnight_low", 0),
                "scenario": pred.get("primary_scenario", "N/A"),
                "scenario_probability": pred.get("primary_probability", 0),
            },
            
            # Learning metadata
            "learning": {
                "prediction_id": f"XAUUSD_{datetime.utcnow().strftime('%Y%m%d')}",
                "report_result_to": "/api/neo/trade-result",
            }
        }
        
        logger.info(f"XAUUSD daily plan: {strategy} @ {pred.get('entry_level', 0):.0f}")
        return plan
        
    except Exception as e:
        logger.error(f"Error getting XAUUSD plan: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ═══════════════════════════════════════════════════════════════════════════════
# IREN DAILY PLAN
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/api/neo/iren/daily-plan")
async def get_iren_daily_plan():
    """
    Get today's IREN trading plan for Ghost Commander.
    
    Includes both share and options strategies.
    """
    try:
        pred = get_today_prediction("IREN")
        
        if not pred:
            logger.info("No IREN prediction found, generating fresh...")
            from daily_reports import IRENReports
            reporter = IRENReports()
            reporter.generate_premarket()
            pred = get_today_prediction("IREN")
        
        if not pred:
            raise HTTPException(status_code=404, detail="Could not generate IREN plan")
        
        # Get BTC correlation
        import yfinance as yf
        try:
            btc = yf.Ticker("BTC-USD").history(period="2d", interval="1h")
            btc_price = float(btc['Close'].iloc[-1]) if not btc.empty else 0
            btc_prev = float(btc['Close'].iloc[-24]) if len(btc) >= 24 else btc_price
            btc_change = ((btc_price - btc_prev) / btc_prev) * 100
        except:
            btc_price = 0
            btc_change = 0
        
        # Get current IREN price
        try:
            iren = yf.Ticker("IREN").history(period="1d", interval="1m")
            current_price = float(iren['Close'].iloc[-1]) if not iren.empty else pred.get("pre_market_price", 0)
        except:
            current_price = pred.get("pre_market_price", 0)
        
        # Determine strategy - HYPERSCALING THESIS with EXHAUSTION CHECK
        # Like Gold: We don't FOMO at tops, we wait for dips!
        overnight_change = pred.get("overnight_bias", "UNKNOWN")
        
        # Calculate technicals for exhaustion check
        try:
            iren_df = yf.Ticker("IREN").history(period="30d", interval="1h")
            close = iren_df['Close']
            
            # RSI
            delta = close.diff()
            gain = delta.where(delta > 0, 0)
            loss = -delta.where(delta < 0, 0)
            avg_gain = gain.rolling(14).mean()
            avg_loss = loss.rolling(14).mean()
            rs = avg_gain / avg_loss
            rsi = float((100 - (100 / (1 + rs))).iloc[-1])
            
            # EMAs
            ema20 = float(close.ewm(span=20).mean().iloc[-1])
            ema50 = float(close.ewm(span=50).mean().iloc[-1])
            dist_from_ema20 = ((current_price - ema20) / ema20) * 100
            
            # 52W High
            hist_1y = yf.Ticker("IREN").history(period="1y")
            high_52w = float(hist_1y['High'].max())
            pct_from_high = ((high_52w - current_price) / high_52w) * 100
        except:
            rsi = 50
            ema20 = current_price
            dist_from_ema20 = 0
            pct_from_high = 10
        
        # EXHAUSTION CHECK (Like Gold - don't buy at tops!)
        exhaustion_score = 0
        exhaustion_reasons = []
        
        if rsi > 70:
            exhaustion_score += 30
            exhaustion_reasons.append(f"RSI {rsi:.0f} > 70")
        if dist_from_ema20 > 5:
            exhaustion_score += 30
            exhaustion_reasons.append(f"+{dist_from_ema20:.1f}% above EMA20")
        if pct_from_high < 3:
            exhaustion_score += 20
            exhaustion_reasons.append(f"Only {pct_from_high:.1f}% from 52W high")
        
        is_exhausted = exhaustion_score >= 50
        
        # DCA Entry Zone (like Gold's entry detection)
        entry_zone_high = ema20
        entry_zone_low = ema20 * 0.92
        in_entry_zone = entry_zone_low <= current_price <= entry_zone_high
        is_dip = rsi < 40 or current_price < ema20 * 0.97
        
        # NEW DCA LOGIC: DCA is relative to ENTRY, not fixed price levels!
        # 
        # WRONG: Wait for $57 (EMA20) that may never come
        # RIGHT: Enter small position, DCA on % drops from entry
        #
        # If exhausted (RSI>75), enter smaller position or wait for 3% pullback
        # DCA levels are % drops from entry, NOT fixed prices
        
        # Check if we have existing positions
        has_positions = False  # Would check paper trading positions
        
        if is_exhausted:
            # EXHAUSTED but still want to participate
            # Option 1: Wait for 3% pullback for L1
            # Option 2: Enter small L1 position anyway (half size)
            strategy = "CAUTIOUS_ENTRY"
            direction = "BUY"  # Changed! Still BUY, but cautiously
            pullback_entry = current_price * 0.97  # 3% pullback
            entry_condition = f"⚠️ Exhausted (RSI {rsi:.0f}). Enter SMALL L1 or wait for 3% pullback to ${pullback_entry:.2f}"
        # Good dip - aggressive entry
        elif is_dip:
            strategy = "BUY_THE_DIP"
            direction = "BUY"
            entry_condition = f"🎯 GOOD DIP! RSI {rsi:.0f}, Buy L1 at ${current_price:.2f}"
        # Overnight gap down
        elif overnight_change == "BEARISH" or (current_price < pred.get("pre_market_price", 100) * 0.95):
            strategy = "BUY_THE_DIP"
            direction = "BUY"
            entry_condition = "Gap down = accumulation opportunity"
        # BTC momentum - can still enter
        elif btc_change > 3:
            strategy = "MOMENTUM_LONG"
            direction = "BUY"
            entry_condition = f"BTC +{btc_change:.1f}% → IREN momentum play"
        # BTC crash - wait for reversal
        elif btc_change < -5:
            strategy = "WAIT_FOR_BOTTOM"
            direction = "WAIT"
            entry_condition = f"BTC {btc_change:.1f}% → wait for reversal signal"
        # DEFAULT: Participate! DCA protects us
        else:
            strategy = "ACCUMULATE"
            direction = "BUY"  # Changed from WAIT - we want to be IN the trade
            entry_condition = f"Thesis intact. Enter L1 at ${current_price:.2f}, DCA on drops"
        
        plan = {
            "signal_id": f"NEO_IREN_{datetime.utcnow().strftime('%Y%m%d')}",
            "timestamp": datetime.utcnow().isoformat(),
            "date": pred.get("date"),
            "symbol": "IREN",
            "current_price": current_price,
            
            # Strategy
            "strategy": strategy,
            "direction": direction,
            "confidence": pred.get("primary_probability", 50),
            
            # BTC correlation
            "btc_correlation": {
                "btc_price": btc_price,
                "btc_change_24h": btc_change,
                "iren_expected": "UP" if btc_change > 2 else "DOWN" if btc_change < -2 else "FLAT"
            },
            
            # Share strategy with DYNAMIC DCA
            "shares": {
                "action": direction,
                "entry_level": current_price if direction == "BUY" else pred.get("entry_level", current_price),
                "stop_loss": None,  # NO SL - DCA protects us
                "take_profit": current_price * 2.5,  # $150 target for hyperscaling
                "position_size": "L1: 1 unit, scale on dips",
            },
            
            # WEIGHTED PIPS DCA - Scales to stock price!
            # 100 pips = 1.5% of stock price (not fixed $1)
            # IREN @ $62: 100 pips = $0.93 (1.5%)
            # CLSK @ $12: 100 pips = $0.18 (1.5%)
            # This way 100 pips MEANS THE SAME for all stocks!
            "dca_ladder": {
                "note": f"Weighted Pips: 100 pips = 1.5% = ${current_price * 0.015:.2f}. Scales to any stock!",
                "reference_price": current_price,
                "pip_value": round(current_price * 0.00015, 4),  # 1 pip = 0.015% of price
                "pip_100_value": round(current_price * 0.015, 2),  # 100 pips = 1.5%
                "L1": {"price": current_price, "pips": 0, "drop": "0%", "size": "1 contract", "status": "ENTRY"},
                "L2": {"price": round(current_price * 0.985, 2), "pips": -100, "drop": "-1.5%", "size": "1 contract", "status": "WAITING"},
                "L3": {"price": round(current_price * 0.970, 2), "pips": -200, "drop": "-3.0%", "size": "2 contracts", "status": "WAITING"},
                "L4": {"price": round(current_price * 0.950, 2), "pips": -333, "drop": "-5.0%", "size": "2 contracts", "status": "WAITING"},
                "L5": {"price": round(current_price * 0.920, 2), "pips": -533, "drop": "-8.0%", "size": "3 contracts (MAX)", "status": "WAITING"},
            },
            
            # Take Profit Ladder
            "tp_ladder": {
                "TP1": {"price": round(current_price * 1.15, 2), "gain": "+15%", "action": "Sell 33%"},
                "TP2": {"price": round(current_price * 1.30, 2), "gain": "+30%", "action": "Sell 33%"},
                "TP3": {"price": 150.00, "gain": "Target", "action": "Sell remaining (runner)"},
            },
            
            # OPTIONS DCA - Using WEIGHTED PIPS (100 pips = 1.5% of price)
            "options": {
                "recommendation": "BUY_CALLS" if direction == "BUY" else "WAIT",
                "strike": int(current_price) + 5,
                "expiry": "3-4 weeks out (DTE 21-28)",
                "pip_100_equals": f"1.5% = ${current_price * 0.015:.2f}",
                "dca_schedule": {
                    "L1": {"trigger": f"Entry @ ${current_price:.2f}", "contracts": 1, "pips": 0, "pct": "0%"},
                    "L2": {"trigger": f"-100 pips @ ${current_price * 0.985:.2f}", "contracts": 1, "pips": -100, "pct": "-1.5%"},
                    "L3": {"trigger": f"-200 pips @ ${current_price * 0.970:.2f}", "contracts": 2, "pips": -200, "pct": "-3.0%"},
                    "L4": {"trigger": f"-333 pips @ ${current_price * 0.950:.2f}", "contracts": 2, "pips": -333, "pct": "-5.0%"},
                    "L5": {"trigger": f"-533 pips @ ${current_price * 0.920:.2f}", "contracts": 3, "pips": -533, "pct": "-8.0%"},
                },
                "max_contracts": 9,
                "max_risk_per_level": "$500",
                "note": "WEIGHTED PIPS: 100 pips = 1.5% regardless of stock price! Same mental model for any stock.",
            },
            
            # Execution rules
            "execution": {
                "best_times": ["9:30-10:00 EST", "15:00-16:00 EST"],
                "avoid_times": ["12:00-14:00 EST"],
                "watch_btc": True,
            },
            
            # Context
            "context": {
                "overnight_high": pred.get("overnight_high", 0),
                "overnight_low": pred.get("overnight_low", 0),
                "overnight_change": ((current_price - pred.get("pre_market_price", 100)) / pred.get("pre_market_price", 100)) * 100,
                "hunt_target": pred.get("hunt_target", 0),
            },
            
            # Exhaustion info
            "exhaustion": {
                "is_exhausted": is_exhausted,
                "score": exhaustion_score,
                "reasons": exhaustion_reasons,
                "rsi": rsi,
                "dist_from_ema20": dist_from_ema20,
            },
            
            # Clear action summary
            "action_summary": entry_condition,
            
            # Learning
            "learning": {
                "prediction_id": f"IREN_{datetime.utcnow().strftime('%Y%m%d')}",
                "report_result_to": "/api/neo/trade-result",
            },
            
            # CRITICAL NOTE
            "dca_philosophy": "DCA is a SAFETY NET for existing positions. Enter L1 first, add on drops. Levels MOVE with price. Don't wait on sidelines!"
        }
        
        logger.info(f"IREN daily plan: {strategy} @ ${current_price:.2f}")
        return plan
        
    except Exception as e:
        logger.error(f"Error getting IREN plan: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ═══════════════════════════════════════════════════════════════════════════════
# CLSK DAILY PLAN (BTC Miner #2 - Weighted Pips)
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/api/neo/clsk/daily-plan")
async def get_clsk_daily_plan():
    """
    Get today's CLSK trading plan with WEIGHTED PIPS.
    Same logic as IREN but with CLSK-specific thesis.
    """
    try:
        import yfinance as yf
        
        # Get current CLSK price
        try:
            ticker = yf.Ticker("CLSK")
            hist = ticker.history(period="1d", interval="1m")
            current_price = float(hist['Close'].iloc[-1]) if not hist.empty else 12.0
        except:
            current_price = 12.0
        
        # Get BTC price for correlation
        try:
            btc = yf.Ticker("BTC-USD").history(period="2d", interval="1h")
            btc_price = float(btc['Close'].iloc[-1])
            btc_prev = float(btc['Close'].iloc[-24]) if len(btc) >= 24 else btc_price
            btc_change = ((btc_price - btc_prev) / btc_prev) * 100
        except:
            btc_price = 0
            btc_change = 0
        
        # Calculate technicals
        try:
            df = yf.Ticker("CLSK").history(period="30d", interval="1h")
            close = df['Close']
            
            delta = close.diff()
            gain = delta.where(delta > 0, 0)
            loss = -delta.where(delta < 0, 0)
            avg_gain = gain.rolling(14).mean()
            avg_loss = loss.rolling(14).mean()
            rs = avg_gain / avg_loss
            rsi = float((100 - (100 / (1 + rs))).iloc[-1])
            
            ema20 = float(close.ewm(span=20).mean().iloc[-1])
            dist_from_ema20 = ((current_price - ema20) / ema20) * 100
        except:
            rsi = 50
            ema20 = current_price
            dist_from_ema20 = 0
        
        # EXHAUSTION CHECK
        exhaustion_score = 0
        exhaustion_reasons = []
        
        if rsi > 70:
            exhaustion_score += 30
            exhaustion_reasons.append(f"RSI {rsi:.0f} > 70")
        if dist_from_ema20 > 5:
            exhaustion_score += 30
            exhaustion_reasons.append(f"+{dist_from_ema20:.1f}% above EMA20")
        
        is_exhausted = exhaustion_score >= 50
        is_dip = rsi < 40 or current_price < ema20 * 0.97
        
        # Strategy determination
        if is_exhausted:
            strategy = "CAUTIOUS_ENTRY"
            direction = "BUY"
            entry_condition = f"⚠️ Exhausted (RSI {rsi:.0f}). Enter small L1 or wait for 3% pullback"
        elif is_dip:
            strategy = "BUY_THE_DIP"
            direction = "BUY"
            entry_condition = f"🎯 GOOD DIP! RSI {rsi:.0f}, Buy L1 at ${current_price:.2f}"
        elif btc_change > 3:
            strategy = "MOMENTUM_LONG"
            direction = "BUY"
            entry_condition = f"BTC momentum +{btc_change:.1f}%, ride the wave"
        else:
            strategy = "THESIS_HOLD"
            direction = "BUY"
            entry_condition = f"Thesis intact. Enter L1 at ${current_price:.2f}, DCA on drops"
        
        # Get weighted pips ladders
        dca_ladder = calculate_weighted_pips_dca(current_price, "CLSK")
        tp_ladder = calculate_weighted_pips_tp(current_price, "CLSK")
        thesis = BTC_MINER_THESIS["CLSK"]
        
        plan = {
            "signal_id": f"NEO_CLSK_{datetime.utcnow().strftime('%Y%m%d')}",
            "timestamp": datetime.utcnow().isoformat(),
            "symbol": "CLSK",
            "current_price": current_price,
            "thesis": thesis,
            
            # Strategy
            "strategy": strategy,
            "direction": direction,
            "confidence": 70 if not is_exhausted else 55,
            "action_summary": entry_condition,
            
            # BTC correlation
            "btc_correlation": {
                "btc_price": btc_price,
                "btc_change_24h": btc_change,
                "expected_move": "UP" if btc_change > 2 else "DOWN" if btc_change < -2 else "FLAT"
            },
            
            # Weighted Pips DCA
            "dca_ladder": dca_ladder,
            "tp_ladder": tp_ladder,
            
            # Options
            "options": {
                "recommendation": "BUY_CALLS" if direction == "BUY" else "WAIT",
                "strike": int(current_price) + 2,  # Lower strikes for cheaper stock
                "expiry": "3-4 weeks out (DTE 21-28)",
                "pip_100_equals": f"1.5% = ${current_price * 0.015:.2f}",
                "max_contracts": 9,
                "note": "WEIGHTED PIPS: 100 pips = 1.5% for any stock!"
            },
            
            # Exhaustion info
            "exhaustion": {
                "is_exhausted": is_exhausted,
                "score": exhaustion_score,
                "reasons": exhaustion_reasons,
                "rsi": rsi,
            },
            
            "execution": {
                "best_times": ["9:30-10:00 EST", "15:00-16:00 EST"],
                "avoid_times": ["12:00-14:00 EST"],
                "watch_btc": True,
            },
            
            "learning": {
                "prediction_id": f"CLSK_{datetime.utcnow().strftime('%Y%m%d')}",
                "report_result_to": "/api/neo/trade-result",
            },
        }
        
        logger.info(f"CLSK daily plan: {strategy} @ ${current_price:.2f}")
        return plan
        
    except Exception as e:
        logger.error(f"Error getting CLSK plan: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ═══════════════════════════════════════════════════════════════════════════════
# CIFR DAILY PLAN (BTC Miner #3 - Weighted Pips)
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/api/neo/cifr/daily-plan")
async def get_cifr_daily_plan():
    """
    Get today's CIFR trading plan with WEIGHTED PIPS.
    Same logic as IREN/CLSK but with CIFR-specific thesis.
    """
    try:
        import yfinance as yf
        
        # Get current CIFR price
        try:
            ticker = yf.Ticker("CIFR")
            hist = ticker.history(period="1d", interval="1m")
            current_price = float(hist['Close'].iloc[-1]) if not hist.empty else 17.0
        except:
            current_price = 17.0
        
        # Get BTC price for correlation
        try:
            btc = yf.Ticker("BTC-USD").history(period="2d", interval="1h")
            btc_price = float(btc['Close'].iloc[-1])
            btc_prev = float(btc['Close'].iloc[-24]) if len(btc) >= 24 else btc_price
            btc_change = ((btc_price - btc_prev) / btc_prev) * 100
        except:
            btc_price = 0
            btc_change = 0
        
        # Calculate technicals
        try:
            df = yf.Ticker("CIFR").history(period="30d", interval="1h")
            close = df['Close']
            
            delta = close.diff()
            gain = delta.where(delta > 0, 0)
            loss = -delta.where(delta < 0, 0)
            avg_gain = gain.rolling(14).mean()
            avg_loss = loss.rolling(14).mean()
            rs = avg_gain / avg_loss
            rsi = float((100 - (100 / (1 + rs))).iloc[-1])
            
            ema20 = float(close.ewm(span=20).mean().iloc[-1])
            dist_from_ema20 = ((current_price - ema20) / ema20) * 100
        except:
            rsi = 50
            ema20 = current_price
            dist_from_ema20 = 0
        
        # EXHAUSTION CHECK
        exhaustion_score = 0
        exhaustion_reasons = []
        
        if rsi > 70:
            exhaustion_score += 30
            exhaustion_reasons.append(f"RSI {rsi:.0f} > 70")
        if dist_from_ema20 > 5:
            exhaustion_score += 30
            exhaustion_reasons.append(f"+{dist_from_ema20:.1f}% above EMA20")
        
        is_exhausted = exhaustion_score >= 50
        is_dip = rsi < 40 or current_price < ema20 * 0.97
        
        # Strategy determination
        if is_exhausted:
            strategy = "CAUTIOUS_ENTRY"
            direction = "BUY"
            entry_condition = f"⚠️ Exhausted (RSI {rsi:.0f}). Enter small L1 or wait for 3% pullback"
        elif is_dip:
            strategy = "BUY_THE_DIP"
            direction = "BUY"
            entry_condition = f"🎯 GOOD DIP! RSI {rsi:.0f}, Buy L1 at ${current_price:.2f}"
        elif btc_change > 3:
            strategy = "MOMENTUM_LONG"
            direction = "BUY"
            entry_condition = f"BTC momentum +{btc_change:.1f}%, ride the wave"
        else:
            strategy = "THESIS_HOLD"
            direction = "BUY"
            entry_condition = f"Thesis intact. Enter L1 at ${current_price:.2f}, DCA on drops"
        
        # Get weighted pips ladders
        dca_ladder = calculate_weighted_pips_dca(current_price, "CIFR")
        tp_ladder = calculate_weighted_pips_tp(current_price, "CIFR")
        thesis = BTC_MINER_THESIS["CIFR"]
        
        plan = {
            "signal_id": f"NEO_CIFR_{datetime.utcnow().strftime('%Y%m%d')}",
            "timestamp": datetime.utcnow().isoformat(),
            "symbol": "CIFR",
            "current_price": current_price,
            "thesis": thesis,
            
            # Strategy
            "strategy": strategy,
            "direction": direction,
            "confidence": 70 if not is_exhausted else 55,
            "action_summary": entry_condition,
            
            # BTC correlation
            "btc_correlation": {
                "btc_price": btc_price,
                "btc_change_24h": btc_change,
                "expected_move": "UP" if btc_change > 2 else "DOWN" if btc_change < -2 else "FLAT"
            },
            
            # Weighted Pips DCA
            "dca_ladder": dca_ladder,
            "tp_ladder": tp_ladder,
            
            # Options
            "options": {
                "recommendation": "BUY_CALLS" if direction == "BUY" else "WAIT",
                "strike": int(current_price) + 3,
                "expiry": "3-4 weeks out (DTE 21-28)",
                "pip_100_equals": f"1.5% = ${current_price * 0.015:.2f}",
                "max_contracts": 9,
                "note": "WEIGHTED PIPS: 100 pips = 1.5% for any stock!"
            },
            
            # Exhaustion info
            "exhaustion": {
                "is_exhausted": is_exhausted,
                "score": exhaustion_score,
                "reasons": exhaustion_reasons,
                "rsi": rsi,
            },
            
            "execution": {
                "best_times": ["9:30-10:00 EST", "15:00-16:00 EST"],
                "avoid_times": ["12:00-14:00 EST"],
                "watch_btc": True,
            },
            
            "learning": {
                "prediction_id": f"CIFR_{datetime.utcnow().strftime('%Y%m%d')}",
                "report_result_to": "/api/neo/trade-result",
            },
        }
        
        logger.info(f"CIFR daily plan: {strategy} @ ${current_price:.2f}")
        return plan
        
    except Exception as e:
        logger.error(f"Error getting CIFR plan: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ═══════════════════════════════════════════════════════════════════════════════
# UNIFIED BTC MINERS ENDPOINT (All 3 at once)
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/api/neo/btc-miners/daily-plan")
async def get_all_btc_miners_plan():
    """
    Get daily plans for ALL 3 BTC miners in one call.
    OPTIONS-ONLY mode with packet costs!
    Perfect for Paul's trading dashboard!
    """
    import yfinance as yf
    
    miners = ["IREN", "CLSK", "CIFR"]
    plans = {}
    
    # Get BTC price once
    try:
        btc = yf.Ticker("BTC-USD").history(period="2d", interval="1h")
        btc_price = float(btc['Close'].iloc[-1])
        btc_prev = float(btc['Close'].iloc[-24]) if len(btc) >= 24 else btc_price
        btc_change = ((btc_price - btc_prev) / btc_prev) * 100
    except:
        btc_price = 100000
        btc_change = 0
    
    # Get Gold price for correlation
    try:
        gold = yf.Ticker("GC=F").history(period="2d", interval="1h")
        gold_price = float(gold['Close'].iloc[-1])
        gold_prev = float(gold['Close'].iloc[-24]) if len(gold) >= 24 else gold_price
        gold_change = ((gold_price - gold_prev) / gold_prev) * 100
    except:
        gold_price = 2700
        gold_change = 0
    
    total_packet_cost = 0
    
    for symbol in miners:
        try:
            ticker = yf.Ticker(symbol)
            hist = ticker.history(period="1d", interval="1m")
            current_price = float(hist['Close'].iloc[-1]) if not hist.empty else 0
            
            # Quick technicals
            df = ticker.history(period="30d", interval="1h")
            close = df['Close']
            delta = close.diff()
            gain = delta.where(delta > 0, 0)
            loss = -delta.where(delta < 0, 0)
            avg_gain = gain.rolling(14).mean()
            avg_loss = loss.rolling(14).mean()
            rs = avg_gain / avg_loss
            rsi = float((100 - (100 / (1 + rs))).iloc[-1])
            ema20 = float(close.ewm(span=20).mean().iloc[-1])
            
            is_exhausted = rsi > 70
            is_dip = rsi < 40
            
            # Determine action
            if is_exhausted:
                action = "⚠️ CAUTION"
                signal = "Wait for -100 pips pullback"
            elif is_dip:
                action = "🎯 BUY CALLS"
                signal = f"DCA at ${current_price:.2f}"
            else:
                action = "✅ ADD CALLS"
                signal = f"L1 entry OK"
            
            # Get OPTIONS packet costs
            options = calculate_options_packet_cost(current_price, symbol)
            packet = options["packet"]
            thesis = BTC_MINER_THESIS.get(symbol, {})
            
            total_packet_cost += packet["actual_cost"]
            
            plans[symbol] = {
                "price": current_price,
                "rsi": round(rsi, 1),
                "ema20": round(ema20, 2),
                "action": action,
                "signal": signal,
                
                # OPTIONS PACKET (key info!)
                "packet_cost": int(packet["actual_cost"]),
                "contracts_per_packet": packet["contracts_per_packet"],
                "strike": packet["strike"],
                "pip_100": round(current_price * 0.015, 2),
                
                # DCA Levels
                "L2_trigger": options["dca_ladder"]["L2"]["price"],
                "L3_trigger": options["dca_ladder"]["L3"]["price"],
                
                # TP Targets
                "tp1": options["tp_ladder"]["TP1"]["stock_price"],
                "tp2": options["tp_ladder"]["TP2"]["stock_price"],
                
                # Thesis
                "target": thesis.get("target", 0),
                "thesis": thesis.get("thesis", "")[:50] + "..."
            }
            
        except Exception as e:
            plans[symbol] = {"error": str(e)}
    
    return {
        "timestamp": datetime.utcnow().isoformat(),
        "mode": "OPTIONS_ONLY",
        
        # Market context
        "btc": {
            "price": btc_price,
            "change_24h": round(btc_change, 2)
        },
        "gold": {
            "price": gold_price,
            "change_24h": round(gold_change, 2),
            "leads_miners": "Gold may lead miners by 1-2 days!"
        },
        
        # Packet info
        "packet_definition": "1 packet ≈ $2,000 risk (same as Gold 0.5 lot)",
        "weighted_pips": "100 pips = 1.5% for ALL stocks",
        
        # Miners
        "miners": plans,
        
        # Summary
        "summary": {
            "buy_signals": sum(1 for p in plans.values() if "BUY" in p.get("action", "") or "ADD" in p.get("action", "")),
            "caution_signals": sum(1 for p in plans.values() if "CAUTION" in p.get("action", "")),
            "total_L1_cost": int(total_packet_cost),
            "overall": "BULLISH" if btc_change > 0 else "CAUTIOUS",
            "gold_signal": "🚀 GOLD UP - Miners may follow!" if gold_change > 0.5 else "⏳ Watch Gold for miner lead"
        }
    }


# ═══════════════════════════════════════════════════════════════════════════════
# TRADE RESULT REPORTING (Learning Loop)
# ═══════════════════════════════════════════════════════════════════════════════

@app.post("/api/neo/trade-result")
async def report_trade_result(result: Dict):
    """
    Report trade result back to NEO for learning.
    
    Ghost should call this after each trade completes with:
    - prediction_id: From the daily plan
    - symbol: XAUUSD or IREN
    - strategy: The strategy used
    - direction: BUY or SELL
    - entry_price: Actual entry
    - exit_price: Actual exit
    - pnl_pips: Profit/loss in pips
    - hunt_hit: Did price hit the hunt target?
    - direction_correct: Was the direction prediction correct?
    - notes: Any additional notes
    """
    try:
        logger.info(f"Trade result received: {result}")
        
        # Validate required fields
        required = ["prediction_id", "symbol", "pnl_pips"]
        for field in required:
            if field not in result:
                raise HTTPException(status_code=400, detail=f"Missing required field: {field}")
        
        # Add timestamp
        result["reported_at"] = datetime.utcnow().isoformat()
        
        # Save individual trade result
        date_str = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        result_file = RESULTS_DIR / f"{result['symbol']}_{date_str}.json"
        with open(result_file, 'w') as f:
            json.dump(result, f, indent=2)
        
        # Update learning statistics
        stats = update_learning_stats(result)
        
        # Update the daily prediction with actual results
        pred_file = DATA_DIR / f"{result['symbol']}_{datetime.utcnow().strftime('%Y%m%d')}.json"
        if pred_file.exists():
            with open(pred_file) as f:
                pred = json.load(f)
            
            pred["actual_pnl"] = result.get("pnl_pips", 0)
            pred["trade_taken"] = True
            pred["trade_result"] = result
            
            with open(pred_file, 'w') as f:
                json.dump(pred, f, indent=2)
        
        return {
            "status": "received",
            "message": "Trade result recorded for learning",
            "updated_stats": {
                "total_trades": stats["total_trades"],
                "win_rate": f"{stats['win_rate']:.1f}%",
                "total_pips": stats["total_pips"],
                "hunt_accuracy": f"{stats['hunt_accuracy']:.1f}%",
                "direction_accuracy": f"{stats['direction_accuracy']:.1f}%"
            }
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error recording trade result: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ═══════════════════════════════════════════════════════════════════════════════
# LEARNING STATISTICS
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/api/neo/learning-stats")
async def get_learning_statistics():
    """
    Get NEO's accumulated learning statistics.
    
    Shows:
    - Overall win rate
    - Total P&L
    - Accuracy by strategy type
    - Accuracy by day of week
    - Recent trades
    """
    stats = get_learning_stats()
    
    # Calculate additional metrics
    if stats["total_trades"] > 0:
        stats["profit_factor"] = (stats["wins"] / stats["losses"]) if stats["losses"] > 0 else float('inf')
    else:
        stats["profit_factor"] = 0
    
    return {
        "timestamp": datetime.utcnow().isoformat(),
        "summary": {
            "total_trades": stats["total_trades"],
            "wins": stats["wins"],
            "losses": stats["losses"],
            "breakeven": stats["breakeven"],
            "win_rate": f"{stats['win_rate']:.1f}%",
            "profit_factor": f"{stats.get('profit_factor', 0):.2f}",
        },
        "performance": {
            "total_pips": stats["total_pips"],
            "avg_pips_per_trade": f"{stats['avg_pips_per_trade']:.1f}",
            "best_trade": stats["best_trade"],
            "worst_trade": stats["worst_trade"],
        },
        "accuracy": {
            "hunt_target": f"{stats['hunt_accuracy']:.1f}%",
            "direction": f"{stats['direction_accuracy']:.1f}%",
        },
        "by_strategy": stats.get("by_strategy", {}),
        "by_day_of_week": stats.get("by_day_of_week", {}),
        "recent_trades": stats.get("recent_trades", [])[-10:]
    }


@app.get("/api/neo/learning-stats/reset")
async def reset_learning_stats():
    """Reset learning statistics (use with caution)"""
    if LEARNING_FILE.exists():
        # Backup first
        backup = LEARNING_FILE.with_suffix('.backup.json')
        LEARNING_FILE.rename(backup)
    
    return {"status": "reset", "message": "Learning stats reset. Backup saved."}


# ═══════════════════════════════════════════════════════════════════════════════
# QUICK SIGNAL ENDPOINTS (For Ghost polling with INVALIDATION support)
# ═══════════════════════════════════════════════════════════════════════════════

def get_realtime_price(symbol: str) -> float:
    """Get real-time price for invalidation checks"""
    try:
        import yfinance as yf
        ticker_map = {"XAUUSD": "GC=F", "IREN": "IREN", "CLSK": "CLSK", "CIFR": "CIFR"}
        ticker = ticker_map.get(symbol, symbol)
        data = yf.Ticker(ticker).history(period="1d", interval="1m")
        if not data.empty:
            return float(data['Close'].iloc[-1])
    except Exception as e:
        logger.warning(f"Could not fetch real-time price for {symbol}: {e}")
    return 0.0


def generate_fresh_xauusd_signal(current_price: float) -> Dict:
    """
    Generate a FRESH intraday signal based on current market conditions.
    
    Called when the pre-market signal becomes stale (price moved significantly).
    This ensures Ghost always has actionable, up-to-date signals.
    
    🎓 LEARNING INTEGRATED:
    - Uses learned feature weights from /home/jbot/trading_ai/neo/learning/
    - Avoids SELL signals during strong uptrends (learned from mistakes)
    - RSI overbought weight reduced to 0.1 (was causing bad SELL signals)
    """
    import yfinance as yf
    import numpy as np
    from pathlib import Path
    
    logger.info(f"🔄 GENERATING FRESH XAUUSD SIGNAL @ ${current_price:.2f}")
    
    # Load learned weights
    weights_file = Path("/home/jbot/trading_ai/neo/learning/feature_weights.json")
    weights = {}
    if weights_file.exists():
        import json
        with open(weights_file) as f:
            weights = json.load(f)
        logger.info(f"📚 Loaded learned weights: RSI_overbought={weights.get('rsi_overbought', 'N/A')}")
    
    try:
        # Fetch recent data for analysis
        ticker = yf.Ticker("GC=F")
        df = ticker.history(period="5d", interval="1h")
        
        if df.empty:
            return _fallback_signal("XAUUSD", current_price, "NO_DATA")
        
        close = df['Close']
        high = df['High']
        low = df['Low']
        
        # Calculate EMAs
        ema20 = close.ewm(span=20).mean()
        ema50 = close.ewm(span=50).mean()
        
        # Calculate RSI
        delta = close.diff()
        gain = (delta.where(delta > 0, 0)).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        current_rsi = float(rsi.iloc[-1])
        
        # Calculate ATR for stop loss
        tr = np.maximum(high - low, 
                       np.maximum(abs(high - close.shift(1)), 
                                  abs(low - close.shift(1))))
        atr = float(tr.rolling(14).mean().iloc[-1])
        
        # Support/Resistance from recent swing points
        recent_high = float(high.tail(20).max())
        recent_low = float(low.tail(20).min())
        
        # Determine trend and direction
        ema20_val = float(ema20.iloc[-1])
        ema50_val = float(ema50.iloc[-1])
        
        # DETERMINE SIGNAL BASED ON CURRENT CONDITIONS
        if current_price > ema20_val > ema50_val:
            # BULLISH: Price above rising EMAs
            direction = "BUY"
            strategy = "BUY_THE_DIP"
            
            # Entry zone: near EMA20 or recent support
            entry_zone_low = max(ema20_val - atr * 0.5, recent_low)
            entry_zone_high = ema20_val + atr * 0.3
            optimal_entry = (entry_zone_low + entry_zone_high) / 2
            
            # Stop below recent swing low
            stop_loss = min(ema50_val, recent_low) - atr * 0.5
            
            # Targets
            take_profit_1 = current_price + atr * 1.5
            take_profit_2 = current_price + atr * 3.0
            
            reasoning = f"BULLISH: Price ${current_price:.0f} > EMA20 ${ema20_val:.0f} > EMA50 ${ema50_val:.0f}"
            confidence = 70 if current_rsi < 70 else 55  # Lower conf if overbought
            
        elif current_price < ema20_val < ema50_val:
            # BEARISH: Price below falling EMAs
            # 🎓 LEARNED: Check if we're in a macro uptrend before SELL
            macro_bullish = current_price > close.rolling(50).mean().iloc[-1]
            
            if macro_bullish:
                # Don't SELL in macro uptrend - learned from mistakes!
                direction = "HOLD"
                strategy = "MACRO_UPTREND_PROTECTION"
                reasoning = f"🎓 LEARNED: Avoiding SELL in macro uptrend. Price > 50-period avg."
                confidence = 40
                logger.warning(f"🎓 LEARNING APPLIED: Blocked SELL signal in macro uptrend")
            else:
                direction = "SELL"
                strategy = "SELL_THE_RALLY"
                reasoning = f"BEARISH: Price ${current_price:.0f} < EMA20 ${ema20_val:.0f} < EMA50 ${ema50_val:.0f}"
                confidence = 70 if current_rsi > 30 else 55
            
            # Entry zone: near EMA20 or recent resistance
            entry_zone_low = ema20_val - atr * 0.3
            entry_zone_high = min(ema20_val + atr * 0.5, recent_high)
            optimal_entry = (entry_zone_low + entry_zone_high) / 2
            
            # Stop above recent swing high
            stop_loss = max(ema50_val, recent_high) + atr * 0.5
            
            # Targets
            take_profit_1 = current_price - atr * 1.5
            take_profit_2 = current_price - atr * 3.0
            
        else:
            # MIXED/CONSOLIDATION - Wait for breakout or buy dip
            # 🎓 LEARNED: In Gold, prefer BUY on dips (fundamental thesis)
            rsi_overbought_weight = weights.get('rsi_overbought', 0.3)  # Default 0.3, learned may be 0.1
            
            # 🔧 FIX: Initialize entry zones for all consolidation branches
            entry_zone_low = current_price - atr * 0.5
            entry_zone_high = current_price + atr * 0.5
            
            if current_rsi < 40:
                direction = "BUY"
                strategy = "OVERSOLD_BOUNCE"
                optimal_entry = current_price
                entry_zone_low = current_price - atr * 0.3
                entry_zone_high = current_price + atr * 0.5
                stop_loss = recent_low - atr * 0.5
                take_profit_1 = ema20_val
                take_profit_2 = ema50_val
                confidence = 65  # Increased from 60 - oversold bounces work better
                reasoning = f"OVERSOLD: RSI {current_rsi:.0f} < 40, looking for bounce (Gold dip = opportunity)"
            elif current_rsi > 60 and rsi_overbought_weight > 0.2:
                # 🎓 LEARNED: Only SELL on overbought if weight is still significant
                # Weight was reduced to 0.1 due to bad SELL signals, so this won't trigger!
                direction = "SELL"
                strategy = "OVERBOUGHT_FADE"
                optimal_entry = current_price
                entry_zone_low = current_price - atr * 0.5
                entry_zone_high = current_price + atr * 0.3
                stop_loss = recent_high + atr * 0.5
                take_profit_1 = ema20_val
                take_profit_2 = ema50_val
                confidence = int(55 * rsi_overbought_weight)  # Reduced by learned weight!
                reasoning = f"OVERBOUGHT: RSI {current_rsi:.0f} > 60 (weight={rsi_overbought_weight})"
            elif current_rsi > 60 and rsi_overbought_weight <= 0.2:
                # 🎓 LEARNED: RSI overbought weight too low - prefer HOLD/BUY
                direction = "HOLD"
                strategy = "RSI_OVERBOUGHT_IGNORED"
                optimal_entry = current_price
                entry_zone_low = current_price - atr * 0.3
                entry_zone_high = current_price + atr * 0.5
                stop_loss = recent_low - atr * 0.5
                take_profit_1 = current_price + atr
                take_profit_2 = current_price + atr * 2
                confidence = 45
                reasoning = f"🎓 LEARNED: RSI overbought ignored (weight={rsi_overbought_weight}). Gold can stay overbought in uptrends!"
                logger.info(f"🎓 LEARNING APPLIED: RSI overbought signal ignored due to low weight")
            else:
                direction = "WAIT"
                strategy = "NO_CLEAR_SETUP"
                optimal_entry = current_price
                # entry_zone_low/high already set at start of else block
                stop_loss = current_price - atr
                take_profit_1 = current_price + atr
                take_profit_2 = current_price + atr * 2
                confidence = 40
                reasoning = f"CONSOLIDATION: EMAs mixed, RSI neutral at {current_rsi:.0f}"
        
        # Distance from optimal entry
        distance_from_entry = abs(current_price - optimal_entry)
        in_entry_zone = distance_from_entry < atr * 0.5
        
        # CRITICAL: Get signal_id that only changes on direction change
        signal_id, is_new_signal, direction_age = get_or_create_signal_id(direction)
        
        signal = {
            "symbol": "XAUUSD",
            "signal_type": "FRESH_INTRADAY",
            "generated_at": datetime.utcnow().isoformat(),
            "action": direction,
            "direction": direction,
            "strategy": strategy,
            
            "current_price": round(current_price, 2),
            "entry": round(optimal_entry, 2),
            "entry_zone_low": round(entry_zone_low if direction != "WAIT" else current_price - atr * 0.5, 2),
            "entry_zone_high": round(entry_zone_high if direction != "WAIT" else current_price + atr * 0.5, 2),
            "in_entry_zone": in_entry_zone,
            "distance_to_entry": round(distance_from_entry, 2),
            
            "stop_loss": round(stop_loss, 2),
            "take_profit_1": round(take_profit_1, 2),
            "take_profit_2": round(take_profit_2, 2),
            
            "confidence": confidence,
            "reasoning": reasoning,
            
            "technicals": {
                "ema20": round(ema20_val, 2),
                "ema50": round(ema50_val, 2),
                "rsi": round(current_rsi, 1),
                "atr": round(atr, 2),
                "recent_high": round(recent_high, 2),
                "recent_low": round(recent_low, 2),
            },
            
            "valid": direction != "WAIT" and confidence >= 50,
            "invalidation": {
                "level": round(stop_loss, 2),
                "reason": None if direction != "WAIT" else "NO_CLEAR_SETUP",
                "confidence_check": "PASS" if confidence >= 50 else "FAIL"
            },
            
            "last_updated": datetime.utcnow().isoformat(),
            
            # CRITICAL: Signal ID only changes when direction changes!
            "signal_id": signal_id,
            "is_new_signal": is_new_signal,
            "direction_age_seconds": int(direction_age),
            "signals_same_direction": _signal_state["signals_same_direction"]
        }
        
        logger.info(f"✅ FRESH SIGNAL: {direction} @ ${optimal_entry:.0f} (conf: {confidence}%)")
        return signal
        
    except Exception as e:
        logger.error(f"Error generating fresh signal: {e}")
        return _fallback_signal("XAUUSD", current_price, str(e))


def _fallback_signal(symbol: str, current_price: float, error: str) -> Dict:
    """Return a safe fallback signal when generation fails"""
    return {
        "symbol": symbol,
        "signal_type": "FALLBACK",
        "generated_at": datetime.utcnow().isoformat(),
        "action": "WAIT",
        "direction": "WAIT",
        "strategy": "ERROR_FALLBACK",
        "current_price": round(current_price, 2),
        "entry": round(current_price, 2),
        "stop_loss": round(current_price - 50, 2),
        "take_profit_1": round(current_price + 50, 2),
        "take_profit_2": round(current_price + 100, 2),
        "confidence": 0,
        "reasoning": f"Fallback due to: {error}",
        "valid": False,
        "invalidation": {
            "level": round(current_price - 50, 2),
            "reason": f"FALLBACK: {error}",
            "confidence_check": "FAIL"
        },
        "last_updated": datetime.utcnow().isoformat(),
        "signal_id": f"NEO_{symbol}_FALLBACK_{datetime.utcnow().strftime('%Y%m%d_%H%M')}"
    }


def check_invalidation_xauusd(plan: Dict, current_price: float) -> Dict:
    """
    Check if XAUUSD signal should be invalidated.
    
    Returns invalidation status with:
    - valid: bool
    - level: Price that invalidates
    - reason: Why invalidated (if applicable)
    """
    entry = plan["entry"]["level"]
    stop_loss = plan["risk"]["stop_loss"]
    direction = plan["direction"]
    strategy = plan["strategy"]
    confidence = plan["confidence"]
    
    # Default: signal is valid
    result = {
        "valid": True,
        "level": stop_loss,
        "reason": None,
        "confidence_check": "PASS"
    }
    
    # Rule 1: Confidence too low
    if confidence < 50:
        result["valid"] = False
        result["reason"] = f"LOW_CONFIDENCE: {confidence}% < 50%"
        result["confidence_check"] = "FAIL"
        return result
    
    # Rule 2: Strategy is WAIT
    if strategy == "WAIT":
        result["valid"] = False
        result["reason"] = "NO_SETUP: Strategy is WAIT"
        return result
    
    # Rule 3: Price moved too far from entry (missed the trade)
    if direction == "SELL" and current_price < entry - 50:
        result["valid"] = False
        result["reason"] = f"MISSED_ENTRY: Price {current_price:.0f} too far below entry {entry:.0f}"
        result["level"] = entry - 50
        return result
    
    if direction == "BUY" and current_price > entry + 50:
        result["valid"] = False
        result["reason"] = f"MISSED_ENTRY: Price {current_price:.0f} too far above entry {entry:.0f}"
        result["level"] = entry + 50
        return result
    
    # Rule 4: Stop loss would be hit (dangerous zone)
    if direction == "SELL" and current_price > stop_loss:
        result["valid"] = False
        result["reason"] = f"SL_BREACH: Price {current_price:.0f} above SL {stop_loss:.0f}"
        return result
    
    if direction == "BUY" and current_price < stop_loss:
        result["valid"] = False
        result["reason"] = f"SL_BREACH: Price {current_price:.0f} below SL {stop_loss:.0f}"
        return result
    
    # Rule 5: Time-based validity (check if still in valid window)
    current_hour = datetime.utcnow().hour
    if current_hour < 6 or current_hour > 20:
        result["valid"] = False
        result["reason"] = f"OUTSIDE_HOURS: Current hour {current_hour} UTC not in 06:00-20:00"
        return result
    
    return result


def check_invalidation_iren(plan: Dict, current_price: float) -> Dict:
    """Check if IREN signal should be invalidated"""
    entry = plan["shares"]["entry_level"]
    stop_loss = plan["shares"]["stop_loss"]
    direction = plan["direction"]
    strategy = plan["strategy"]
    confidence = plan["confidence"]
    btc_change = plan["btc_correlation"]["btc_change_24h"]
    
    result = {
        "valid": True,
        "level": stop_loss,
        "reason": None,
        "confidence_check": "PASS"
    }
    
    # Rule 1: Confidence too low
    if confidence < 50:
        result["valid"] = False
        result["reason"] = f"LOW_CONFIDENCE: {confidence}% < 50%"
        result["confidence_check"] = "FAIL"
        return result
    
    # Rule 2: Strategy is WAIT
    if strategy == "WAIT" or direction == "WAIT":
        result["valid"] = False
        result["reason"] = "NO_SETUP: Strategy is WAIT"
        return result
    
    # Rule 3: BTC crash invalidates long
    if direction == "BUY" and btc_change < -5:
        result["valid"] = False
        result["reason"] = f"BTC_CRASH: BTC {btc_change:.1f}% invalidates long"
        return result
    
    # Rule 4: Stop loss breach
    if direction == "BUY" and current_price < stop_loss:
        result["valid"] = False
        result["reason"] = f"SL_BREACH: Price ${current_price:.2f} below SL ${stop_loss:.2f}"
        return result
    
    # Rule 5: Missed entry - BUT for hyperscaling thesis, breakouts are VALID entries
    # IREN has hyperscaling thesis - buying breakouts is acceptable
    # Only invalidate if price is EXTREMELY extended (>30% above 20-day EMA)
    if direction == "BUY" and current_price > entry * 1.30:
        # Even then, don't invalidate - just note it
        result["reason"] = f"BREAKOUT_CHASE: Price ${current_price:.2f} extended - consider smaller position"
        # STILL VALID - hyperscaling thesis allows breakout buying
        # result["valid"] = False  # REMOVED - don't invalidate breakouts for IREN!
        result["level"] = current_price * 0.92  # Adjust SL to current price
        return result
    
    # Rule 6: Market hours check (US market)
    current_hour = datetime.utcnow().hour
    # US market: 14:30 - 21:00 UTC
    if current_hour < 13 or current_hour > 21:
        result["valid"] = False
        result["reason"] = f"OUTSIDE_HOURS: Current hour {current_hour} UTC not in US market hours"
        return result
    
    return result


@app.get("/api/neo/xauusd/signal")
async def get_xauusd_quick_signal():
    """
    Quick signal endpoint for Ghost - with INVALIDATION support + FRESH SIGNAL GENERATION.
    
    Ghost should poll this every 20 minutes and check:
    - valid: true/false
    - invalidation.level: Price that breaks the trade
    - invalidation.reason: Why invalidated
    - last_updated: When signal was last checked
    
    🆕 NEW: When pre-market signal becomes stale, this endpoint generates a FRESH
    intraday signal based on current market conditions instead of returning invalid data.
    """
    plan = await get_xauusd_daily_plan()
    current_price = get_realtime_price("XAUUSD") or plan["current_price"]
    
    # Check invalidation rules against the pre-market plan
    invalidation = check_invalidation_xauusd(plan, current_price)
    
    # 🆕 NEW: If signal is invalidated due to MISSED_ENTRY, generate FRESH signal!
    if not invalidation["valid"]:
        reason = invalidation.get("reason", "")
        
        # These invalidation types mean we should generate a fresh signal
        should_regenerate = any([
            "MISSED_ENTRY" in reason,  # Price moved significantly
            "SL_BREACH" in reason,     # Original setup failed
        ])
        
        if should_regenerate:
            logger.info(f"⚠️ Pre-market signal stale ({reason}). Generating FRESH signal...")
            fresh_signal = generate_fresh_xauusd_signal(current_price)
            fresh_signal["original_plan_invalidated"] = True
            fresh_signal["invalidation_reason"] = reason
            return fresh_signal
    
    # Original pre-market signal still valid - return it
    return {
        "symbol": "XAUUSD",
        "signal_type": "PREMARKET_PLAN",
        "action": plan["direction"],
        "strategy": plan["strategy"],
        "entry": plan["entry"]["level"],
        "entry_zone_low": plan["entry"]["level"] - 15,
        "entry_zone_high": plan["entry"]["level"] + 15,
        "stop_loss": plan["risk"]["stop_loss"],
        "take_profit": plan["risk"]["take_profit_1"],
        "take_profit_1": plan["risk"]["take_profit_1"],
        "take_profit_2": plan["risk"]["take_profit_2"],
        "confidence": plan["confidence"],
        "current_price": current_price,
        "in_entry_zone": abs(current_price - plan["entry"]["level"]) < 20,
        "distance_to_entry": abs(current_price - plan["entry"]["level"]),
        
        # INVALIDATION FIELDS (for Ghost's 20-min checks)
        "valid": invalidation["valid"],
        "invalidation": {
            "level": invalidation["level"],
            "reason": invalidation["reason"],
            "confidence_check": invalidation["confidence_check"]
        },
        
        "last_updated": datetime.utcnow().isoformat(),
        "timestamp": plan["timestamp"],
        "signal_id": plan.get("signal_id", f"NEO_XAUUSD_{datetime.utcnow().strftime('%Y%m%d')}")
    }


@app.get("/api/neo/iren/signal")
async def get_iren_quick_signal():
    """
    Quick signal endpoint for Ghost - with INVALIDATION support.
    """
    plan = await get_iren_daily_plan()
    current_price = get_realtime_price("IREN") or plan["current_price"]
    
    # Check invalidation rules
    invalidation = check_invalidation_iren(plan, current_price)
    
    return {
        "symbol": "IREN",
        "action": plan["direction"],
        "strategy": plan["strategy"],
        "entry": plan["shares"]["entry_level"],
        "stop_loss": plan["shares"]["stop_loss"],
        "take_profit": plan["shares"]["take_profit"],
        "confidence": plan["confidence"],
        "current_price": current_price,
        "btc_change": plan["btc_correlation"]["btc_change_24h"],
        
        # INVALIDATION FIELDS
        "valid": invalidation["valid"],
        "invalidation": {
            "level": invalidation["level"],
            "reason": invalidation["reason"],
            "confidence_check": invalidation["confidence_check"]
        },
        
        "last_updated": datetime.utcnow().isoformat(),
        "timestamp": plan["timestamp"],
        "signal_id": plan.get("signal_id", f"NEO_IREN_{datetime.utcnow().strftime('%Y%m%d')}")
    }


# ═══════════════════════════════════════════════════════════════════════════════
# SUPERTREND COMMAND SYSTEM
# ═══════════════════════════════════════════════════════════════════════════════

# Store last SuperTrend state for change detection
_supertrend_state = {"XAUUSD": None, "IREN": None}


def calculate_supertrend_command(symbol: str) -> Dict:
    """
    Calculate SuperTrend direction based on:
    - EMA 20/50 crossovers (Golden/Death cross)
    - Price vs EMA 200
    - Higher highs/Lower lows pattern
    - Volume confirmation
    
    Returns: LONG, SHORT, or NEUTRAL with confidence and reasoning
    """
    import yfinance as yf
    import pandas as pd
    
    try:
        ticker_map = {"XAUUSD": "GC=F", "IREN": "IREN"}
        ticker = ticker_map.get(symbol, symbol)
        
        # Fetch data
        df = yf.Ticker(ticker).history(period="30d", interval="1h")
        if df.empty or len(df) < 200:
            return {"supertrend": "NEUTRAL", "supertrend_confidence": 0, "supertrend_reason": "Insufficient data"}
        
        close = df['Close']
        high = df['High']
        low = df['Low']
        volume = df['Volume']
        
        # Calculate EMAs
        ema20 = close.ewm(span=20).mean()
        ema50 = close.ewm(span=50).mean()
        ema200 = close.ewm(span=200).mean() if len(close) >= 200 else close.ewm(span=len(close)).mean()
        
        current_price = float(close.iloc[-1])
        current_ema20 = float(ema20.iloc[-1])
        current_ema50 = float(ema50.iloc[-1])
        current_ema200 = float(ema200.iloc[-1])
        
        prev_ema20 = float(ema20.iloc[-2])
        prev_ema50 = float(ema50.iloc[-2])
        
        # Scoring system
        score = 0
        reasons = []
        
        # 1. EMA 20/50 Cross (Golden Cross = +30, Death Cross = -30)
        golden_cross = prev_ema20 < prev_ema50 and current_ema20 > current_ema50
        death_cross = prev_ema20 > prev_ema50 and current_ema20 < current_ema50
        ema_bullish = current_ema20 > current_ema50
        ema_bearish = current_ema20 < current_ema50
        
        if golden_cross:
            score += 35
            reasons.append("🌟 Golden cross (EMA20 crossed above EMA50)")
        elif death_cross:
            score -= 35
            reasons.append("💀 Death cross (EMA20 crossed below EMA50)")
        elif ema_bullish:
            score += 15
            reasons.append("EMA20 > EMA50 (bullish)")
        elif ema_bearish:
            score -= 15
            reasons.append("EMA20 < EMA50 (bearish)")
        
        # 2. Price vs EMA200 (+25 or -25)
        if current_price > current_ema200 * 1.01:  # 1% above
            score += 25
            reasons.append("Price above EMA200 (bullish trend)")
        elif current_price < current_ema200 * 0.99:  # 1% below
            score -= 25
            reasons.append("Price below EMA200 (bearish trend)")
        
        # 3. Higher Highs / Lower Lows (+20 or -20)
        recent_highs = high.iloc[-20:]
        recent_lows = low.iloc[-20:]
        
        hh = recent_highs.iloc[-5:].max() > recent_highs.iloc[-10:-5].max()
        hl = recent_lows.iloc[-5:].min() > recent_lows.iloc[-10:-5].min()
        lh = recent_highs.iloc[-5:].max() < recent_highs.iloc[-10:-5].max()
        ll = recent_lows.iloc[-5:].min() < recent_lows.iloc[-10:-5].min()
        
        if hh and hl:
            score += 20
            reasons.append("Higher highs & higher lows (uptrend)")
        elif lh and ll:
            score -= 20
            reasons.append("Lower highs & lower lows (downtrend)")
        
        # 4. Volume confirmation (+10 or -10)
        avg_volume = volume.iloc[-20:].mean()
        recent_volume = volume.iloc[-5:].mean()
        volume_increasing = recent_volume > avg_volume * 1.2
        
        if volume_increasing:
            if score > 0:
                score += 10
                reasons.append("Volume confirming bullish move")
            elif score < 0:
                score -= 10
                reasons.append("Volume confirming bearish move")
        
        # Determine SuperTrend direction
        if score >= 30:
            supertrend = "LONG"
            confidence = min(90, 50 + score)
        elif score <= -30:
            supertrend = "SHORT"
            confidence = min(90, 50 + abs(score))
        else:
            supertrend = "NEUTRAL"
            confidence = 50
        
        # Check if changed from last state
        global _supertrend_state
        last_state = _supertrend_state.get(symbol)
        changed = last_state is not None and last_state != supertrend
        _supertrend_state[symbol] = supertrend
        
        return {
            "supertrend": supertrend,
            "supertrend_confidence": confidence,
            "supertrend_changed": changed,
            "supertrend_reason": " | ".join(reasons) if reasons else "No clear trend",
            "supertrend_score": score,
            "indicators": {
                "ema20": round(current_ema20, 2),
                "ema50": round(current_ema50, 2),
                "ema200": round(current_ema200, 2),
                "price": round(current_price, 2),
                "golden_cross": golden_cross,
                "death_cross": death_cross
            }
        }
        
    except Exception as e:
        logger.error(f"SuperTrend calculation error for {symbol}: {e}")
        return {
            "supertrend": "NEUTRAL",
            "supertrend_confidence": 0,
            "supertrend_changed": False,
            "supertrend_reason": f"Error: {str(e)}"
        }


def get_consensus_signal(symbol: str) -> Dict:
    """Get unified consensus signal combining NEO + Meta Bot"""
    neo_action = "HOLD"
    neo_conf = 0
    meta_action = "HOLD"
    meta_conf = 0
    st = {"supertrend": "NEUTRAL", "supertrend_confidence": 0, "supertrend_changed": False, "supertrend_reason": ""}
    conflict_warning = None
    
    try:
        # Get Meta Bot signal
        try:
            meta_resp = requests.get(f"http://localhost:8035/api/meta/ghost/{symbol.lower()}", timeout=5)
            if meta_resp.ok:
                meta = meta_resp.json()
                meta_action = meta.get("action", "HOLD").upper()
                meta_conf = meta.get("confidence", 0) or 0
        except Exception as e:
            logger.warning(f"Could not fetch Meta Bot: {e}")
        
        # Get NEO prediction
        try:
            pred = get_today_prediction(symbol.upper())
            if pred:
                exp_dir = pred.get("expected_direction", "")
                neo_action = "SELL" if exp_dir == "SHORTS" else "BUY" if exp_dir == "LONGS" else "HOLD"
                neo_conf = pred.get("primary_probability", 0) or 0
        except Exception as e:
            logger.warning(f"Could not load NEO prediction: {e}")
        
        # Calculate SuperTrend command
        try:
            st = calculate_supertrend_command(symbol.upper())
        except Exception as e:
            logger.warning(f"Could not calculate SuperTrend: {e}")
        
        # Consensus logic
        if neo_action == meta_action and neo_action != "HOLD":
            action = neo_action
            confidence = int((neo_conf + meta_conf) / 2 * 1.2)
            consensus_level = "STRONG"
        elif neo_action == "HOLD" and meta_action == "HOLD":
            action = "HOLD"
            confidence = 50
            consensus_level = "NEUTRAL"
        elif (neo_action == "BUY" and meta_action == "SELL") or (neo_action == "SELL" and meta_action == "BUY"):
            action = "HOLD"
            confidence = 30
            consensus_level = "CONFLICT"
            conflict_warning = f"⚠️ CONFLICT: NEO says {neo_action}, Meta says {meta_action}. WAITING for alignment."
        elif neo_action in ["BUY", "SELL"] and meta_action == "HOLD":
            action = neo_action
            confidence = int(neo_conf * 0.7) if neo_conf else 50
            consensus_level = "MEDIUM"
        elif meta_action in ["BUY", "SELL"] and neo_action == "HOLD":
            action = meta_action
            confidence = int(meta_conf * 0.7) if meta_conf else 50
            consensus_level = "MEDIUM"
        else:
            action = "HOLD"
            confidence = 40
            consensus_level = "WEAK"
        
        return {
            "action": action,
            "confidence": min(confidence, 95),
            "consensus_level": consensus_level,
            "neo": {"action": neo_action, "confidence": neo_conf},
            "meta": {"action": meta_action, "confidence": meta_conf},
            "supertrend": st,
            "conflict_warning": conflict_warning
        }
    except Exception as e:
        logger.error(f"Consensus error: {e}")
        return {
            "action": "HOLD",
            "confidence": 0,
            "consensus_level": "ERROR",
            "neo": {"action": neo_action, "confidence": neo_conf},
            "meta": {"action": meta_action, "confidence": meta_conf},
            "supertrend": st,
            "conflict_warning": f"Error: {str(e)}"
        }


# ═══════════════════════════════════════════════════════════════════════════════
# UNIFIED SIGNAL ENDPOINT (Ghost should use THIS!)
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/api/neo/xauusd/unified")
@app.get("/api/consensus/xauusd")
async def get_xauusd_unified():
    """
    UNIFIED signal for Ghost - combines NEO + Meta + SuperTrend command.
    Ghost should use THIS endpoint for all decisions!
    """
    plan = await get_xauusd_daily_plan()
    current_price = get_realtime_price("XAUUSD") or plan["current_price"]
    
    # Get consensus (NEO + Meta)
    consensus = get_consensus_signal("XAUUSD")
    
    # Get SuperTrend command
    st = consensus.get("supertrend", {})
    
    # Check invalidation
    invalidation = check_invalidation_xauusd(plan, current_price)
    
    return {
        "symbol": "XAUUSD",
        "action": consensus["action"],
        "confidence": consensus["confidence"],
        "consensus_level": consensus["consensus_level"],
        
        # SuperTrend Command (NEW - Ghost uses this!)
        "supertrend": st.get("supertrend", "NEUTRAL"),
        "supertrend_confidence": st.get("supertrend_confidence", 0),
        "supertrend_changed": st.get("supertrend_changed", False),
        "supertrend_reason": st.get("supertrend_reason", ""),
        
        # Individual signals
        "neo": consensus["neo"],
        "meta": consensus["meta"],
        
        # Trading levels
        "entry": plan["entry"]["level"],
        "stop_loss": plan["risk"]["stop_loss"],
        "take_profit": plan["risk"]["take_profit_1"],
        "take_profit_2": plan["risk"]["take_profit_2"],
        "current_price": current_price,
        
        # Validation
        "valid": invalidation["valid"] and consensus["action"] != "HOLD",
        "invalidation": invalidation,
        "conflict_warning": consensus.get("conflict_warning"),
        
        "last_updated": datetime.utcnow().isoformat(),
        "timestamp": plan["timestamp"]
    }


@app.get("/api/neo/iren/unified")
@app.get("/api/consensus/iren")
async def get_iren_unified():
    """UNIFIED signal for IREN - combines NEO + Meta + SuperTrend command."""
    plan = await get_iren_daily_plan()
    current_price = get_realtime_price("IREN") or plan["current_price"]
    
    # Get consensus (NEO + Meta)
    consensus = get_consensus_signal("IREN")
    
    # Get SuperTrend command
    st = consensus.get("supertrend", {})
    
    # Check invalidation
    invalidation = check_invalidation_iren(plan, current_price)
    
    return {
        "symbol": "IREN",
        "action": consensus["action"],
        "confidence": consensus["confidence"],
        "consensus_level": consensus["consensus_level"],
        
        # SuperTrend Command
        "supertrend": st.get("supertrend", "NEUTRAL"),
        "supertrend_confidence": st.get("supertrend_confidence", 0),
        "supertrend_changed": st.get("supertrend_changed", False),
        "supertrend_reason": st.get("supertrend_reason", ""),
        
        # Individual signals
        "neo": consensus["neo"],
        "meta": consensus["meta"],
        
        # Trading levels
        "entry": plan["shares"]["entry_level"],
        "stop_loss": plan["shares"]["stop_loss"],
        "take_profit": plan["shares"]["take_profit"],
        "current_price": current_price,
        "btc_change": plan["btc_correlation"]["btc_change_24h"],
        
        # Validation
        "valid": invalidation["valid"] and consensus["action"] != "HOLD",
        "invalidation": invalidation,
        "conflict_warning": consensus.get("conflict_warning"),
        
        "last_updated": datetime.utcnow().isoformat(),
        "timestamp": plan["timestamp"]
    }


@app.get("/api/neo/supertrend/{symbol}")
async def get_supertrend_only(symbol: str):
    """Get just the SuperTrend command for a symbol"""
    st = calculate_supertrend_command(symbol.upper())
    return {
        "symbol": symbol.upper(),
        **st,
        "timestamp": datetime.utcnow().isoformat()
    }


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════════════════
# LEARNING ENDPOINTS - Track NEO's mistakes and improvements
# ═══════════════════════════════════════════════════════════════════════════════

sys.path.insert(0, '/home/jbot/trading_ai/neo/learning')

@app.get("/api/neo/learning/stats")
async def get_neo_learning_stats():
    """Get NEO's learning statistics - what it has learned from mistakes"""
    try:
        from neo_trainer import get_learning_stats, get_recent_mistakes
        stats = get_learning_stats()
        recent_mistakes = get_recent_mistakes(5)
        
        return {
            "status": "success",
            "learning_stats": stats,
            "recent_mistakes": recent_mistakes,
            "key_learnings": [
                {
                    "lesson": "RSI overbought does NOT mean SELL in strong Gold uptrends",
                    "weight_before": 0.3,
                    "weight_after": stats.get("current_weights", {}).get("rsi_overbought", 0.1),
                    "outcome": "Now ignoring RSI > 70 as SELL trigger"
                },
                {
                    "lesson": "Bear flag patterns often fail in Gold uptrends",
                    "weight_before": 0.6,
                    "weight_after": stats.get("current_weights", {}).get("bear_flag", 0.1),
                    "outcome": "Reduced bearish pattern weight significantly"
                }
            ],
            "biggest_problem": stats.get("biggest_problem"),
            "accuracy_trend": f"{stats.get('accuracy_overall', 0):.1f}% overall → {stats.get('accuracy_recent_20', 0):.1f}% recent"
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/api/neo/learning/teach")
async def teach_neo_manually(lesson: Dict):
    """
    Manually teach NEO about a mistake.
    
    Body:
    {
        "symbol": "XAUUSD",
        "action_taken": "SELL",
        "price_at_signal": 5100,
        "actual_price": 5300,
        "reason": "NEO sold during rally, price went up 4%"
    }
    """
    try:
        from neo_trainer import force_learn_from_current_state
        
        result = force_learn_from_current_state(
            symbol=lesson.get("symbol", "XAUUSD"),
            price_when_sold=lesson.get("price_at_signal"),
            current_price=lesson.get("actual_price"),
            action=lesson.get("action_taken", "SELL")
        )
        
        return {
            "status": "success",
            "message": "NEO has learned from this mistake",
            "grade": result["grade"],
            "lesson": result.get("lesson", ""),
            "weights_adjusted": True
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.get("/api/neo/learning/weights")
async def get_neo_feature_weights():
    """Get current feature weights used in signal generation"""
    try:
        from neo_trainer import load_weights
        weights = load_weights()
        
        # Categorize weights
        high_weight = {k: v for k, v in weights.items() if v >= 1.0}
        medium_weight = {k: v for k, v in weights.items() if 0.5 <= v < 1.0}
        low_weight = {k: v for k, v in weights.items() if v < 0.5}
        
        return {
            "status": "success",
            "weights": weights,
            "by_priority": {
                "high_impact": high_weight,
                "medium_impact": medium_weight,
                "low_impact_or_penalized": low_weight
            },
            "explanation": {
                "rsi_overbought": "REDUCED to 0.1 - was causing bad SELL signals during Gold rallies",
                "bear_flag": "REDUCED to 0.1 - patterns often wrong in strong uptrends",
                "ema_trend": "HIGH at 1.5 - trend following is key",
                "supertrend": "HIGH at 1.4 - reliable trend indicator"
            }
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


# ═══════════════════════════════════════════════════════════════════════════════
# UNIFIED SIGNAL ENDPOINT - NEO + META BOT COMBINED
# ═══════════════════════════════════════════════════════════════════════════════

import httpx

async def get_meta_bot_signal(symbol: str) -> Optional[Dict]:
    """Fetch signal from Meta Bot API"""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"http://127.0.0.1:8035/api/meta/{symbol.lower()}/signal",
                timeout=5
            )
            if response.status_code == 200:
                return response.json()
    except Exception as e:
        logger.warning(f"Meta Bot not available: {e}")
    return None


@app.get("/api/unified/xauusd/signal")
async def get_unified_xauusd_signal():
    """
    UNIFIED SIGNAL - Combines NEO + Meta Bot for consensus.
    
    This is the PRIMARY endpoint Ghost should use!
    
    Decision Matrix:
    - NEO BUY + Meta BUY → STRONG BUY (confidence avg)
    - NEO BUY + Meta HOLD → BUY (reduced confidence)
    - NEO BUY + Meta SELL → HOLD (conflict - wait)
    - NEO SELL + Meta SELL → STRONG SELL (but Gold bias overrides)
    - NEO HOLD + Meta HOLD → HOLD
    
    Gold Bias Rule: For XAUUSD, prefer LONG due to fundamental thesis.
    """
    # Get NEO signal
    neo_signal = await get_xauusd_quick_signal()
    
    # Get Meta Bot signal
    meta_signal = await get_meta_bot_signal("xauusd")
    
    # Default values
    neo_action = neo_signal.get("action", "HOLD")
    neo_confidence = neo_signal.get("confidence", 50)
    neo_direction = neo_signal.get("direction", neo_action)
    
    meta_action = "HOLD"
    meta_confidence = 50
    meta_reasoning = "Meta Bot unavailable"
    
    if meta_signal:
        meta_action = meta_signal.get("action", "HOLD")
        meta_confidence = meta_signal.get("confidence", 50)
        meta_reasoning = meta_signal.get("reasoning", "")
    
    # Consensus logic
    consensus_action = "HOLD"
    consensus_confidence = 50
    consensus_reason = ""
    
    # Both agree on BUY
    if neo_action == "BUY" and meta_action == "BUY":
        consensus_action = "BUY"
        consensus_confidence = (neo_confidence + meta_confidence) / 2
        consensus_reason = "CONSENSUS: NEO + Meta both BUY"
    
    # NEO BUY, Meta HOLD - lean BUY with reduced confidence
    elif neo_action == "BUY" and meta_action == "HOLD":
        consensus_action = "BUY"
        consensus_confidence = neo_confidence * 0.8  # 20% reduction
        consensus_reason = "NEO BUY, Meta HOLD - cautious BUY"
    
    # Both agree on SELL (but Gold bias favors waiting)
    elif neo_action == "SELL" and meta_action == "SELL":
        # Gold fundamental thesis: Don't short, wait for dip to buy
        consensus_action = "HOLD"
        consensus_confidence = 60
        consensus_reason = "GOLD BIAS: Both say SELL but prefer to HOLD for dip buying opportunity"
    
    # Conflict - NEO and Meta disagree
    elif (neo_action == "BUY" and meta_action == "SELL") or (neo_action == "SELL" and meta_action == "BUY"):
        consensus_action = "HOLD"
        consensus_confidence = 40
        consensus_reason = f"CONFLICT: NEO={neo_action}, Meta={meta_action} - waiting for agreement"
    
    # Default: Follow NEO with reduced confidence
    else:
        consensus_action = neo_action if neo_action != "HOLD" else "HOLD"
        consensus_confidence = neo_confidence * 0.75
        consensus_reason = f"Following NEO ({neo_action}) with caution"
    
    # CRITICAL: Get signal_id - ONLY changes when direction changes!
    # This prevents EAs from stacking positions on the same direction
    signal_id, is_new_signal, direction_age = get_or_create_signal_id(consensus_action)
    
    # Build unified response
    return {
        "symbol": "XAUUSD",
        "timestamp": datetime.utcnow().isoformat(),
        "signal_type": "UNIFIED_CONSENSUS",
        
        # UNIFIED SIGNAL (use this!)
        "action": consensus_action,
        "direction": consensus_action if consensus_action in ["BUY", "SELL"] else neo_direction,
        "confidence": round(consensus_confidence, 1),
        "reasoning": consensus_reason,
        
        # Trade parameters (from NEO)
        "current_price": neo_signal.get("current_price"),
        "entry": neo_signal.get("entry"),
        "entry_zone_low": neo_signal.get("entry_zone_low"),
        "entry_zone_high": neo_signal.get("entry_zone_high"),
        "in_entry_zone": neo_signal.get("in_entry_zone"),
        "stop_loss": neo_signal.get("stop_loss"),
        "take_profit_1": neo_signal.get("take_profit_1"),
        "take_profit_2": neo_signal.get("take_profit_2"),
        "strategy": neo_signal.get("strategy"),
        
        # Validity (from NEO)
        "valid": neo_signal.get("valid", True),
        "invalidation": neo_signal.get("invalidation"),
        
        # Individual signals (for debugging)
        "neo": {
            "action": neo_action,
            "confidence": neo_confidence,
            "strategy": neo_signal.get("strategy"),
            "signal_type": neo_signal.get("signal_type")
        },
        "meta": {
            "action": meta_action,
            "confidence": meta_confidence,
            "reasoning": meta_reasoning,
            "bullish_count": meta_signal.get("indicator_count", {}).get("bullish", 0) if meta_signal else 0,
            "bearish_count": meta_signal.get("indicator_count", {}).get("bearish", 0) if meta_signal else 0
        },
        
        # SUPERTREND (from NEO)
        "supertrend": neo_signal.get("supertrend", {}).get("direction", "UNKNOWN") if isinstance(neo_signal.get("supertrend"), dict) else "BULLISH",
        "supertrend_confidence": neo_signal.get("supertrend", {}).get("confidence", 50) if isinstance(neo_signal.get("supertrend"), dict) else 50,
        
        "last_updated": datetime.utcnow().isoformat(),
        
        # CRITICAL FIX: signal_id only changes on DIRECTION CHANGE
        # Prevents EAs from stacking 8 SELLs in 8 minutes!
        "signal_id": signal_id,
        "is_new_signal": is_new_signal,  # True = direction just changed, EAs can open position
        "direction_age_seconds": int(direction_age),  # How long this direction has been active
        "signals_same_direction": _signal_state["signals_same_direction"]  # How many signals with same direction
    }


@app.get("/api/unified/iren/signal")
async def get_unified_iren_signal():
    """UNIFIED IREN signal - NEO + Meta Bot combined"""
    # Get NEO signal
    neo_signal = await get_iren_quick_signal()
    
    # Get Meta Bot signal
    meta_signal = await get_meta_bot_signal("iren")
    
    neo_action = neo_signal.get("action", "HOLD")
    neo_confidence = neo_signal.get("confidence", 50)
    
    meta_action = "HOLD"
    meta_confidence = 50
    
    if meta_signal:
        meta_action = meta_signal.get("action", "HOLD")
        meta_confidence = meta_signal.get("confidence", 50)
    
    # Simple consensus for IREN (similar logic)
    if neo_action == "BUY" and meta_action == "BUY":
        action = "BUY"
        confidence = (neo_confidence + meta_confidence) / 2
        reason = "CONSENSUS: NEO + Meta both BUY"
    elif neo_action == "BUY" and meta_action == "HOLD":
        action = "BUY"
        confidence = neo_confidence * 0.8
        reason = "NEO BUY, Meta HOLD"
    elif neo_action == "SELL" and meta_action == "SELL":
        action = "SELL"
        confidence = (neo_confidence + meta_confidence) / 2
        reason = "CONSENSUS: NEO + Meta both SELL"
    else:
        action = "HOLD"
        confidence = 50
        reason = f"No consensus: NEO={neo_action}, Meta={meta_action}"
    
    return {
        "symbol": "IREN",
        "timestamp": datetime.utcnow().isoformat(),
        "signal_type": "UNIFIED_CONSENSUS",
        "action": action,
        "confidence": round(confidence, 1),
        "reasoning": reason,
        "current_price": neo_signal.get("current_price"),
        "entry": neo_signal.get("entry"),
        "stop_loss": neo_signal.get("stop_loss"),
        "take_profit": neo_signal.get("take_profit"),
        "valid": neo_signal.get("valid", True),
        "neo": {"action": neo_action, "confidence": neo_confidence},
        "meta": {"action": meta_action, "confidence": meta_confidence},
        "last_updated": datetime.utcnow().isoformat()
    }


# ═══════════════════════════════════════════════════════════════════════════════
# INSTITUTIONAL DETECTOR ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

# Import institutional detector
try:
    from institutional_detector import InstitutionalDetector
    institutional_detector = InstitutionalDetector()
    INSTITUTIONAL_DETECTOR_AVAILABLE = True
    logger.info("✅ Institutional Detector loaded successfully")
except ImportError as e:
    INSTITUTIONAL_DETECTOR_AVAILABLE = False
    institutional_detector = None
    logger.warning(f"⚠️ Institutional Detector not available: {e}")


@app.get("/api/neo/institutional/{symbol}")
async def get_institutional_analysis(symbol: str):
    """
    Get institutional analysis for a symbol.
    Includes: options flow, sentiment, stop hunt risk, cascade protection.
    
    This helps NEO see what Citadel sees - trade the TRADER, not just the chart.
    """
    if not INSTITUTIONAL_DETECTOR_AVAILABLE:
        return {
            "error": "Institutional detector not available",
            "symbol": symbol,
            "fallback": True,
            "signal": "NEUTRAL",
            "action": "FOLLOW_TREND"
        }
    
    # Get current price (would be from market feed in production)
    current_prices = {
        "XAUUSD": 5270.0,
        "GOLD": 5270.0,
        "GLD": 265.0,
        "IREN": 59.94,
        "CIFR": 12.50,
        "CLSK": 15.75
    }
    
    current_price = current_prices.get(symbol.upper(), 100.0)
    
    # Recent price levels (would be from market data in production)
    recent_lows = {
        "XAUUSD": [5200, 5180, 5150, 5100, 5050],
        "IREN": [52, 48, 45, 42, 40],
        "CIFR": [11, 10.5, 10, 9.5, 9],
        "CLSK": [14, 13.5, 13, 12.5, 12]
    }
    
    recent_highs = {
        "XAUUSD": [5280, 5300, 5320],
        "IREN": [60, 62, 65],
        "CIFR": [13, 13.5, 14],
        "CLSK": [16, 16.5, 17]
    }
    
    try:
        analysis = await institutional_detector.get_full_analysis(
            symbol=symbol.upper(),
            current_price=current_price,
            recent_lows=recent_lows.get(symbol.upper(), []),
            recent_highs=recent_highs.get(symbol.upper(), [])
        )
        
        return analysis
        
    except Exception as e:
        logger.error(f"Institutional analysis error for {symbol}: {e}")
        return {
            "error": str(e),
            "symbol": symbol,
            "fallback": True,
            "signal": "NEUTRAL",
            "action": "FOLLOW_TREND"
        }


@app.get("/api/neo/options-flow/{symbol}")
async def get_options_flow(symbol: str):
    """
    Get options flow analysis (put/call ratio, unusual activity).
    
    Key signals:
    - Put/Call > 2.0 = Retail euphoria (DANGER - institutions may dump)
    - Put/Call < 0.5 = Retail panic (OPPORTUNITY - institutions may buy)
    """
    if not INSTITUTIONAL_DETECTOR_AVAILABLE:
        return {"error": "Institutional detector not available", "fallback": True}
    
    try:
        return await institutional_detector.options_detector.get_put_call_ratio(symbol)
    except Exception as e:
        return {"error": str(e), "fallback": True}


@app.get("/api/neo/sentiment/{symbol}")
async def get_social_sentiment(symbol: str):
    """
    Get social media sentiment analysis.
    
    Key signals:
    - Score > 80 = EUPHORIA (everyone bullish = potential top)
    - Score < 20 = PANIC (everyone bearish = potential bottom)
    """
    if not INSTITUTIONAL_DETECTOR_AVAILABLE:
        return {"error": "Institutional detector not available", "fallback": True}
    
    try:
        return await institutional_detector.sentiment_detector.get_sentiment_score(symbol)
    except Exception as e:
        return {"error": str(e), "fallback": True}


@app.get("/api/neo/cascade-protection")
async def get_cascade_protection_status():
    """
    Get current cascade protection status.
    
    Unlike other algos that freeze at fixed 92% volatility,
    NEO uses RANDOMIZED thresholds (85-95%) to avoid coordinated hunts.
    """
    if not INSTITUTIONAL_DETECTOR_AVAILABLE:
        return {"error": "Institutional detector not available", "fallback": True}
    
    try:
        return institutional_detector.cascade_protection.get_current_threshold()
    except Exception as e:
        return {"error": str(e), "fallback": True}


@app.get("/api/neo/gold-thesis")
async def get_gold_thesis():
    """
    Get NEO's Gold macro thesis ($10K-$50K target).
    This is the fundamental backdrop for all Gold trading decisions.
    """
    thesis_file = DATA_DIR.parent / "learning" / "gold_macro_thesis.json"
    
    if thesis_file.exists():
        with open(thesis_file) as f:
            return json.load(f)
    
    return {
        "core_thesis": "Gold in multi-decade bull market",
        "2030_target": 15000,
        "2035_target": 50000,
        "bias": "EXTREME_LONG",
        "file_missing": True
    }


# ═══════════════════════════════════════════════════════════════════════════════
# HEALTH CHECK & MONITORING
# ═══════════════════════════════════════════════════════════════════════════════
# CRITICAL: This endpoint must ALWAYS work. If it fails, the system is broken.

_health_stats = {
    "signals_generated_24h": 0,
    "last_signal_time": None,
    "api_errors_consecutive": 0,
    "last_error": None,
    "service_start_time": datetime.utcnow().isoformat()
}


@app.get("/api/neo/health")
async def get_health_status():
    """
    CRITICAL HEALTH CHECK ENDPOINT
    
    This endpoint is monitored. If it returns unhealthy:
    - Alert is triggered
    - Service needs immediate attention
    - $5k/month H100 value is questioned
    
    Returns:
        {
            "status": "healthy" | "degraded" | "critical",
            "last_signal_generated": "ISO timestamp",
            "signals_generated_24h": count,
            "ghost_directives_age_seconds": seconds since last update,
            "api_latency_ms": response time,
            "details": {...}
        }
    """
    import time
    start_time = time.time()
    
    issues = []
    status = "healthy"
    
    # Check 1: ghost_directives.txt freshness
    directives_file = Path("/home/jbot/trading_ai/intel/ghost_directives.txt")
    directives_age = 999999
    if directives_file.exists():
        directives_age = (datetime.utcnow() - datetime.fromtimestamp(directives_file.stat().st_mtime)).total_seconds()
        if directives_age > 120:  # More than 2 minutes old
            issues.append(f"ghost_directives.txt is {directives_age:.0f}s old (should be < 120s)")
            status = "degraded"
        if directives_age > 300:  # More than 5 minutes
            status = "critical"
    else:
        issues.append("ghost_directives.txt does not exist!")
        status = "critical"
    
    # Check 2: Fresh signal file
    signal_file = Path("/home/jbot/trading_ai/neo/signals/xauusd_fresh_signal.json")
    signal_age = 999999
    last_signal_time = None
    if signal_file.exists():
        signal_age = (datetime.utcnow() - datetime.fromtimestamp(signal_file.stat().st_mtime)).total_seconds()
        last_signal_time = datetime.fromtimestamp(signal_file.stat().st_mtime).isoformat() + "Z"
        if signal_age > 120:
            issues.append(f"xauusd_fresh_signal.json is {signal_age:.0f}s old")
            if status == "healthy":
                status = "degraded"
        if signal_age > 300:
            status = "critical"
    else:
        issues.append("xauusd_fresh_signal.json does not exist!")
        status = "critical"
    
    # Check 3: Signal generator service
    try:
        import subprocess
        result = subprocess.run(
            ["pm2", "jlist"],
            capture_output=True,
            text=True,
            timeout=5
        )
        pm2_data = json.loads(result.stdout)
        signal_gen = next((p for p in pm2_data if p.get("name") == "neo-signal-generator"), None)
        
        if signal_gen:
            if signal_gen.get("pm2_env", {}).get("status") != "online":
                issues.append(f"neo-signal-generator is {signal_gen.get('pm2_env', {}).get('status', 'unknown')}")
                status = "critical"
            restarts = signal_gen.get("pm2_env", {}).get("restart_time", 0)
            if restarts > 5:
                issues.append(f"neo-signal-generator has restarted {restarts} times")
        else:
            issues.append("neo-signal-generator service not found in PM2!")
            status = "critical"
    except Exception as e:
        issues.append(f"Could not check PM2: {str(e)[:50]}")
    
    # Check 4: API can generate signal
    api_healthy = False
    try:
        # Quick test - can we generate a signal?
        import yfinance as yf
        gold = yf.Ticker("GC=F")
        price = gold.fast_info.get("lastPrice", 0)
        if price > 0:
            api_healthy = True
        else:
            issues.append("Cannot fetch gold price from yfinance")
            if status == "healthy":
                status = "degraded"
    except Exception as e:
        issues.append(f"API test failed: {str(e)[:50]}")
        if status == "healthy":
            status = "degraded"
    
    # Calculate response time
    latency_ms = (time.time() - start_time) * 1000
    
    # Count signals in last 24h (approximate from file count)
    signals_24h = 0
    try:
        signal_dir = Path("/home/jbot/trading_ai/neo/signals")
        cutoff = datetime.utcnow() - timedelta(hours=24)
        for f in signal_dir.glob("signal_*.json"):
            if datetime.fromtimestamp(f.stat().st_mtime) > cutoff:
                signals_24h += 1
    except:
        pass
    
    return {
        "status": status,
        "last_signal_generated": last_signal_time,
        "signals_generated_24h": signals_24h,
        "ghost_directives_age_seconds": int(directives_age),
        "signal_file_age_seconds": int(signal_age),
        "api_latency_ms": round(latency_ms, 1),
        "api_can_generate": api_healthy,
        "issues": issues if issues else None,
        "service_uptime": _health_stats.get("service_start_time"),
        "checked_at": datetime.utcnow().isoformat() + "Z"
    }


# ═══════════════════════════════════════════════════════════════════════════════
# NEO DESKTOP ↔ H100 SYNC
# ═══════════════════════════════════════════════════════════════════════════════
# Two-way sync between NEO Desktop (Crella's Windows machine) and H100 NEO

# Global storage for Desktop signals
_desktop_signal = {
    "signal": None,
    "received_at": None,
    "signals_today": 0,
    "last_reset": datetime.utcnow().date().isoformat()
}


@app.post("/api/neo/desktop/report")
async def receive_desktop_report(data: dict):
    """
    Receive signal report from NEO Desktop.
    
    This enables two-way sync:
    - Desktop sends its local analysis here
    - H100 uses it in consensus calculations
    - Desktop gets back the combined consensus
    
    Expected payload:
    {
        "source": "NEO_DESKTOP",
        "timestamp": "2026-02-04T14:26:35",
        "current_price": 4948.40,
        "action": "BUY",
        "confidence": 65,
        "technicals": {
            "rsi": 28.5,
            "ema20": 4955.00,
            "ema50": 4960.00
        },
        "reasoning": "RSI oversold; Price near support"
    }
    """
    global _desktop_signal
    
    try:
        # Reset counter if new day
        today = datetime.utcnow().date().isoformat()
        if _desktop_signal["last_reset"] != today:
            _desktop_signal["signals_today"] = 0
            _desktop_signal["last_reset"] = today
        
        # Store desktop signal
        _desktop_signal["signal"] = {
            **data,
            "source": data.get("source", "NEO_DESKTOP"),
            "received_at": datetime.utcnow().isoformat()
        }
        _desktop_signal["received_at"] = datetime.utcnow().isoformat()
        _desktop_signal["signals_today"] += 1
        
        logger.info(f"📱 Desktop signal received: {data.get('action', 'N/A')} @ {data.get('current_price', 'N/A')}")
        
        return {
            "status": "received",
            "will_use_in_consensus": True,
            "signals_today": _desktop_signal["signals_today"],
            "received_at": _desktop_signal["received_at"]
        }
        
    except Exception as e:
        logger.error(f"Error receiving desktop report: {e}")
        return {"status": "error", "message": str(e)}


def get_recent_desktop_signal(max_age_seconds: int = 120) -> Optional[Dict]:
    """Get Desktop signal if recent (within max_age_seconds)."""
    global _desktop_signal
    
    if not _desktop_signal["signal"] or not _desktop_signal["received_at"]:
        return None
    
    try:
        received_at = datetime.fromisoformat(_desktop_signal["received_at"])
        age = (datetime.utcnow() - received_at).total_seconds()
        
        if age <= max_age_seconds:
            return _desktop_signal["signal"]
        else:
            return None  # Too stale
    except:
        return None


@app.get("/api/neo/status")
async def get_neo_status():
    """
    Dashboard showing status of all NEO sources:
    - H100 NEO (this server)
    - Desktop NEO (Crella's Windows machine)
    - Meta Bot (multi-indicator consensus)
    
    Returns current signals and health from all sources.
    """
    global _desktop_signal
    
    # Get H100 NEO signal (use unified endpoint data)
    h100_signal = None
    h100_status = "unknown"
    try:
        # Get current price from yfinance
        import yfinance as yf
        gold = yf.Ticker("GC=F")
        current_price = gold.info.get("regularMarketPrice") or gold.fast_info.get("lastPrice", 5000)
        h100_signal = generate_fresh_xauusd_signal(float(current_price))
        h100_status = "online" if h100_signal.get("valid") else "degraded"
    except Exception as e:
        h100_status = f"error: {str(e)[:50]}"
    
    # Get Meta Bot signal (async call)
    meta_signal = None
    meta_status = "unknown"
    try:
        meta_signal = await get_meta_bot_signal("xauusd")
        if meta_signal:
            meta_status = "online"
        else:
            meta_status = "no_response"
    except Exception as e:
        meta_status = f"error: {str(e)[:50]}"
    
    # Get Desktop signal status
    desktop_signal = get_recent_desktop_signal(max_age_seconds=120)
    desktop_status = "online" if desktop_signal else "stale" if _desktop_signal["signal"] else "no_data"
    
    # Calculate consensus
    consensus = calculate_three_way_consensus(h100_signal, meta_signal, desktop_signal)
    
    return {
        "timestamp": datetime.utcnow().isoformat(),
        "h100_neo": {
            "status": h100_status,
            "action": h100_signal.get("action") if h100_signal else None,
            "confidence": h100_signal.get("confidence") if h100_signal else None,
            "strategy": h100_signal.get("strategy") if h100_signal else None,
            "current_price": h100_signal.get("current_price") if h100_signal else None
        },
        "desktop_neo": {
            "status": desktop_status,
            "action": desktop_signal.get("action") if desktop_signal else None,
            "confidence": desktop_signal.get("confidence") if desktop_signal else None,
            "last_seen": _desktop_signal.get("received_at"),
            "signals_today": _desktop_signal.get("signals_today", 0)
        },
        "meta_bot": {
            "status": meta_status,
            "action": meta_signal.get("action") if meta_signal else None,
            "confidence": meta_signal.get("confidence") if meta_signal else None,
            "bullish_count": meta_signal.get("bullish_count") if meta_signal else None,
            "bearish_count": meta_signal.get("bearish_count") if meta_signal else None
        },
        "consensus": consensus
    }


def calculate_three_way_consensus(h100_signal: Dict, meta_signal: Dict, desktop_signal: Dict) -> Dict:
    """
    Calculate three-way consensus from H100, Meta Bot, and Desktop NEO.
    
    Returns:
        {
            "action": "BUY/SELL/HOLD",
            "votes": "2/3" or "3/3",
            "confidence": weighted average,
            "sources_agree": ["H100", "Desktop"] etc
        }
    """
    signals = []
    sources = []
    
    if h100_signal and h100_signal.get("action") not in [None, "WAIT", "ERROR"]:
        signals.append({
            "source": "H100",
            "action": h100_signal.get("action"),
            "confidence": h100_signal.get("confidence", 50)
        })
        sources.append("H100")
    
    if meta_signal and meta_signal.get("action") not in [None, "WAIT", "HOLD"]:
        signals.append({
            "source": "Meta",
            "action": meta_signal.get("action"),
            "confidence": meta_signal.get("confidence", 50)
        })
        sources.append("Meta")
    
    if desktop_signal and desktop_signal.get("action") not in [None, "WAIT"]:
        signals.append({
            "source": "Desktop",
            "action": desktop_signal.get("action"),
            "confidence": desktop_signal.get("confidence", 50)
        })
        sources.append("Desktop")
    
    if not signals:
        return {
            "action": "HOLD",
            "votes": "0/3",
            "confidence": 0,
            "sources_agree": [],
            "reason": "No valid signals from any source"
        }
    
    # Count votes
    buy_votes = [s for s in signals if s["action"] == "BUY"]
    sell_votes = [s for s in signals if s["action"] == "SELL"]
    
    total = len(signals)
    
    if len(buy_votes) > len(sell_votes):
        action = "BUY"
        agreeing = [s["source"] for s in buy_votes]
        avg_conf = sum(s["confidence"] for s in buy_votes) / len(buy_votes)
    elif len(sell_votes) > len(buy_votes):
        action = "SELL"
        agreeing = [s["source"] for s in sell_votes]
        avg_conf = sum(s["confidence"] for s in sell_votes) / len(sell_votes)
    else:
        action = "HOLD"
        agreeing = []
        avg_conf = 40
    
    # Boost confidence if all agree
    if len(agreeing) == 3:
        avg_conf = min(95, avg_conf * 1.2)
    elif len(agreeing) == 2:
        avg_conf = min(85, avg_conf * 1.1)
    
    return {
        "action": action,
        "votes": f"{len(agreeing)}/{total}",
        "confidence": round(avg_conf, 1),
        "sources_agree": agreeing,
        "all_sources": sources
    }


@app.get("/api/neo/desktop/latest")
async def get_desktop_latest():
    """
    Get the latest signal from NEO Desktop.
    Used by H100 to fetch what Desktop is seeing.
    """
    global _desktop_signal
    
    desktop_signal = get_recent_desktop_signal(max_age_seconds=300)  # 5 min
    
    if desktop_signal:
        return {
            "status": "online",
            "signal": desktop_signal,
            "age_seconds": (datetime.utcnow() - datetime.fromisoformat(_desktop_signal["received_at"])).total_seconds(),
            "signals_today": _desktop_signal["signals_today"]
        }
    elif _desktop_signal["signal"]:
        return {
            "status": "stale",
            "signal": _desktop_signal["signal"],
            "last_seen": _desktop_signal["received_at"],
            "signals_today": _desktop_signal["signals_today"]
        }
    else:
        return {
            "status": "no_data",
            "signal": None,
            "message": "No signals received from Desktop NEO yet"
        }


if __name__ == "__main__":
    uvicorn.run(
        "ghost_integration_api:app",
        host="0.0.0.0",
        port=8036,
        reload=False,
        log_level="info"
    )
