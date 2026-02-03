#!/usr/bin/env python3
"""
NEO TRAINING API - Team B Intelligence System
==============================================
API endpoints for v0202 (Ghost Commander) to communicate with NEO.

Endpoints:
  POST /api/trade/outcome    - Report trade results
  POST /api/market/xauusd    - Send market data
  GET  /api/neo/signal       - Get trading signal (polled every 60s)
  GET  /api/daily-plan/xauusd - Get daily trading plan
  GET  /api/scoreboard       - Get Team A vs Team B scoreboard
  GET  /health               - Health check

Author: Quinn
Version: 1.0.0
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, Dict, List
from datetime import datetime, timedelta
import json
from pathlib import Path

# Import NEO Learner
import sys
sys.path.insert(0, str(Path(__file__).parent))
from neo_learner import get_learner, NeoLearner

# ══════════════════════════════════════════════════════════════════════════════
# FASTAPI APP
# ══════════════════════════════════════════════════════════════════════════════

app = FastAPI(
    title="NEO Training API",
    description="Team B Intelligence System - Learning from every trade",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ══════════════════════════════════════════════════════════════════════════════
# PYDANTIC MODELS
# ══════════════════════════════════════════════════════════════════════════════

class TradeOutcome(BaseModel):
    signal_id: str
    outcome: str  # "WIN" or "LOSS"
    profit: float
    entry_price: float
    exit_price: float
    pips: float
    lots: float

class MarketData(BaseModel):
    symbol: str
    timestamp: str
    price: float
    adx: float
    plus_di: float
    minus_di: float
    rsi: float
    atr: float
    ema20: float
    ema50: float
    h4_trend: str

# ══════════════════════════════════════════════════════════════════════════════
# STATE STORAGE
# ══════════════════════════════════════════════════════════════════════════════

# Cache for latest market data
latest_market_data: Dict = {}

# Cache for latest signal (refreshed by market data updates)
latest_signal: Dict = {}
last_signal_time: datetime = datetime.min

# ══════════════════════════════════════════════════════════════════════════════
# ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/")
async def root():
    """API info"""
    return {
        "name": "NEO Training API",
        "team": "B (NEO)",
        "version": "1.0.0",
        "endpoints": {
            "POST /api/trade/outcome": "Report trade outcome for learning",
            "POST /api/market/xauusd": "Send market data",
            "GET /api/neo/signal": "Get trading signal (polled by v0202)",
            "GET /api/daily-plan/xauusd": "Get daily trading plan",
            "GET /api/scoreboard": "Team competition scoreboard",
            "GET /health": "Health check"
        },
        "status": "LEARNING"
    }


@app.get("/health")
async def health():
    """Health check"""
    learner = get_learner()
    return {
        "status": "ok",
        "service": "neo-training-api",
        "team": "B (NEO)",
        "timestamp": datetime.now().isoformat(),
        "weights_loaded": len(learner.confidence_weights) > 0,
        "daily_metrics": learner.daily_metrics
    }


@app.post("/api/trade/outcome")
async def receive_trade_outcome(outcome: TradeOutcome):
    """
    Receive trade outcome from v0202.
    NEO learns from this and updates confidence weights.
    """
    global latest_signal
    
    learner = get_learner()
    
    result = learner.process_outcome(outcome.dict())
    
    return {
        "status": "received",
        "learning_result": result,
        "message": f"NEO learned from {outcome.signal_id}: {outcome.outcome}"
    }


@app.post("/api/market/xauusd")
async def receive_market_data(market: MarketData):
    """
    Receive market data from v0202.
    NEO uses this to generate signals.
    """
    global latest_market_data, latest_signal, last_signal_time
    
    latest_market_data = market.dict()
    
    # Generate new signal if enough time has passed (1 minute)
    now = datetime.now()
    if (now - last_signal_time).total_seconds() >= 60:
        learner = get_learner()
        latest_signal = learner.generate_signal(market.dict())
        last_signal_time = now
        
        return {
            "status": "received",
            "signal_generated": True,
            "signal": latest_signal
        }
    
    return {
        "status": "received",
        "signal_generated": False,
        "next_signal_in_seconds": 60 - (now - last_signal_time).total_seconds()
    }


@app.get("/api/neo/signal")
async def get_signal():
    """
    Get current trading signal.
    v0202 polls this every 60 seconds.
    
    Filtering rules (applied by v0202):
    - Signals < 50% confidence are IGNORED
    - Signals > 5 minutes old are IGNORED
    """
    global latest_signal, last_signal_time, latest_market_data
    
    # If we have recent market data but no signal, generate one
    if latest_market_data and not latest_signal:
        learner = get_learner()
        latest_signal = learner.generate_signal(latest_market_data)
        last_signal_time = datetime.now()
    
    # If no signal at all, generate from defaults
    if not latest_signal:
        return {
            "signal_id": None,
            "direction": "HOLD",
            "confidence": 0.0,
            "reasoning": "No market data received yet",
            "timestamp": datetime.now().isoformat(),
            "suggested_lots": 0,
            "suggested_sl": 0,
            "suggested_tp": 0
        }
    
    # Check signal age
    signal_time = datetime.fromisoformat(latest_signal['timestamp'].replace('Z', '+00:00').replace('+00:00', ''))
    age_seconds = (datetime.now() - signal_time).total_seconds()
    
    # Add metadata
    response = {
        **latest_signal,
        "age_seconds": age_seconds,
        "is_stale": age_seconds > 300,  # 5 minutes
        "meets_confidence_threshold": latest_signal.get('confidence', 0) >= 0.5
    }
    
    return response


@app.get("/api/daily-plan/xauusd")
async def get_daily_plan():
    """
    Get daily trading plan for XAUUSD.
    v0202 can use this for strategic direction.
    """
    learner = get_learner()
    
    if latest_market_data:
        plan = learner.generate_daily_plan(latest_market_data)
    else:
        # Default plan without market data
        plan = {
            "date": datetime.now().strftime("%Y-%m-%d"),
            "strategy": "WAIT",
            "direction": "NEUTRAL",
            "key_levels": {
                "support": [],
                "resistance": []
            },
            "risk_mode": "CONSERVATIVE",
            "notes": "Waiting for market data from v0202",
            "weight_highlights": learner._get_top_weights()
        }
    
    return plan


@app.get("/api/scoreboard")
async def get_scoreboard():
    """
    Get Team A vs Team B competition scoreboard.
    """
    learner = get_learner()
    return learner.get_scoreboard()


@app.get("/api/weights")
async def get_weights():
    """
    Get current confidence weights.
    Shows what features NEO values most.
    """
    learner = get_learner()
    
    # Sort by weight descending
    sorted_weights = sorted(
        learner.confidence_weights.items(),
        key=lambda x: x[1],
        reverse=True
    )
    
    return {
        "weights": dict(sorted_weights),
        "top_features": [k for k, v in sorted_weights[:3]],
        "bottom_features": [k for k, v in sorted_weights[-3:]],
        "last_updated": datetime.now().isoformat()
    }


@app.get("/api/metrics/daily")
async def get_daily_metrics():
    """
    Get today's performance metrics.
    """
    learner = get_learner()
    return learner.daily_metrics


@app.get("/api/metrics/weekly")
async def get_weekly_metrics():
    """
    Get weekly performance summary.
    """
    learner = get_learner()
    
    # Aggregate last 7 days
    total_signals = 0
    total_wins = 0
    total_losses = 0
    total_pnl = 0
    
    for i in range(7):
        date = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
        metrics_file = Path(f"/home/jbot/trading_ai/data/neo/metrics/daily/{date}.json")
        
        try:
            if metrics_file.exists():
                daily = json.loads(metrics_file.read_text())
                total_signals += daily.get('signals_generated', 0)
                total_wins += daily.get('wins', 0)
                total_losses += daily.get('losses', 0)
                total_pnl += daily.get('total_pnl', 0)
        except:
            continue
    
    total_trades = total_wins + total_losses
    
    return {
        "week": datetime.now().strftime("%Y-W%W"),
        "days_included": 7,
        "signals_generated": total_signals,
        "trades_executed": total_trades,
        "wins": total_wins,
        "losses": total_losses,
        "win_rate": round(total_wins / total_trades * 100, 1) if total_trades > 0 else 0,
        "total_pnl": round(total_pnl, 2),
        "avg_daily_pnl": round(total_pnl / 7, 2)
    }


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import uvicorn
    
    print("="*60)
    print("🧠 NEO TRAINING API - Team B Intelligence")
    print("="*60)
    print("Endpoints:")
    print("  POST /api/trade/outcome - Report trade results")
    print("  POST /api/market/xauusd - Send market data")
    print("  GET  /api/neo/signal    - Get trading signal")
    print("  GET  /api/daily-plan/xauusd - Get daily plan")
    print("  GET  /api/scoreboard    - Competition scoreboard")
    print("="*60)
    
    uvicorn.run(app, host="0.0.0.0", port=8897)
