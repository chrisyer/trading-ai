"""
SPY HUNTER SIGNAL API
=====================
FastAPI server that:
1. Serves signals to MT5
2. Manages continuous training
3. Tracks performance
"""

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, Dict, List
import json
import os
from datetime import datetime
import asyncio
import threading
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("SPY_SIGNAL_API")

app = FastAPI(
    title="SPY Hunter Signal API",
    description="Anti-Market Maker RL Trading Signals",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global state
SIGNAL_FILE = "/home/jbot/trading_ai/spy_hunter/current_signal.json"
MODEL_DIR = "/home/jbot/trading_ai/spy_hunter/models"
TRADE_LOG_FILE = "/home/jbot/trading_ai/spy_hunter/trade_log.json"

training_status = {
    "is_training": False,
    "last_trained": None,
    "total_epochs": 0,
    "current_performance": {}
}


class Signal(BaseModel):
    symbol: str = "SPY"
    action: str  # BUY, SELL, HOLD
    price: float
    confidence: int
    sl: Optional[float] = None
    tp: Optional[float] = None
    timestamp: str = None
    
    
class TradeResult(BaseModel):
    symbol: str
    action: str
    entry_price: float
    exit_price: float
    pnl: float
    timestamp: str


def get_current_signal() -> Dict:
    """Read current signal from file"""
    if os.path.exists(SIGNAL_FILE):
        try:
            with open(SIGNAL_FILE, 'r') as f:
                return json.load(f)
        except:
            pass
    
    return {
        "symbol": "SPY",
        "action": "HOLD",
        "price": 0,
        "confidence": 0,
        "timestamp": datetime.now().isoformat(),
        "source": "SPY_HUNTER_RL"
    }


@app.get("/")
async def root():
    return {
        "service": "SPY Hunter Signal API",
        "status": "online",
        "signal_endpoint": "/spy/signal",
        "training_status": training_status["is_training"]
    }


@app.get("/spy/signal")
async def get_signal():
    """Get current trading signal for MT5"""
    signal = get_current_signal()
    return signal


@app.get("/spy/signal/mt5")
async def get_signal_mt5():
    """
    MT5-formatted signal
    Returns signal in format optimized for MQL5 parsing
    """
    signal = get_current_signal()
    
    return {
        "action": signal.get("action", "HOLD"),
        "price": signal.get("price", 0),
        "confidence": signal.get("confidence", 0),
        "sl": signal.get("sl", 0),
        "tp": signal.get("tp", 0),
        "timestamp": signal.get("timestamp", datetime.now().isoformat())
    }


@app.post("/spy/signal")
async def set_signal(signal: Signal):
    """Manually set a signal (for testing)"""
    signal_dict = {
        "symbol": signal.symbol,
        "action": signal.action,
        "price": signal.price,
        "confidence": signal.confidence,
        "sl": signal.sl,
        "tp": signal.tp,
        "timestamp": signal.timestamp or datetime.now().isoformat(),
        "source": "MANUAL"
    }
    
    with open(SIGNAL_FILE, 'w') as f:
        json.dump(signal_dict, f, indent=2)
    
    logger.info(f"Signal set: {signal.action} @ {signal.price}")
    return {"status": "ok", "signal": signal_dict}


@app.get("/spy/status")
async def get_status():
    """Get full system status"""
    signal = get_current_signal()
    
    return {
        "current_signal": signal,
        "training_status": training_status,
        "model_exists": os.path.exists(f"{MODEL_DIR}/spy_hunter_ppo.zip"),
        "timestamp": datetime.now().isoformat()
    }


@app.post("/spy/train")
async def trigger_training(background_tasks: BackgroundTasks, timesteps: int = 50000):
    """Trigger a training run"""
    if training_status["is_training"]:
        raise HTTPException(status_code=400, detail="Training already in progress")
    
    background_tasks.add_task(run_training, timesteps)
    
    return {"status": "training_started", "timesteps": timesteps}


def run_training(timesteps: int):
    """Run training in background"""
    global training_status
    
    training_status["is_training"] = True
    logger.info(f"Starting training for {timesteps} timesteps...")
    
    try:
        from spy_antimm_agent import SPYHunterTrainer
        trainer = SPYHunterTrainer(model_dir=MODEL_DIR)
        trainer.train(total_timesteps=timesteps)
        
        training_status["last_trained"] = datetime.now().isoformat()
        training_status["total_epochs"] += 1
        logger.info("Training completed successfully")
        
    except Exception as e:
        logger.error(f"Training failed: {e}")
    
    finally:
        training_status["is_training"] = False


@app.post("/spy/trade/log")
async def log_trade(trade: TradeResult):
    """Log a trade result for performance tracking"""
    trades = []
    if os.path.exists(TRADE_LOG_FILE):
        try:
            with open(TRADE_LOG_FILE, 'r') as f:
                trades = json.load(f)
        except:
            pass
    
    trades.append({
        "symbol": trade.symbol,
        "action": trade.action,
        "entry_price": trade.entry_price,
        "exit_price": trade.exit_price,
        "pnl": trade.pnl,
        "timestamp": trade.timestamp
    })
    
    with open(TRADE_LOG_FILE, 'w') as f:
        json.dump(trades, f, indent=2)
    
    return {"status": "logged", "total_trades": len(trades)}


@app.get("/spy/performance")
async def get_performance():
    """Get trading performance stats"""
    if not os.path.exists(TRADE_LOG_FILE):
        return {"message": "No trades logged yet"}
    
    with open(TRADE_LOG_FILE, 'r') as f:
        trades = json.load(f)
    
    if not trades:
        return {"message": "No trades logged yet"}
    
    total_pnl = sum(t["pnl"] for t in trades)
    winning = [t for t in trades if t["pnl"] > 0]
    losing = [t for t in trades if t["pnl"] < 0]
    
    return {
        "total_trades": len(trades),
        "winning_trades": len(winning),
        "losing_trades": len(losing),
        "win_rate": len(winning) / len(trades) * 100 if trades else 0,
        "total_pnl": total_pnl,
        "avg_win": sum(t["pnl"] for t in winning) / len(winning) if winning else 0,
        "avg_loss": sum(t["pnl"] for t in losing) / len(losing) if losing else 0,
        "profit_factor": abs(sum(t["pnl"] for t in winning) / sum(t["pnl"] for t in losing)) if losing and sum(t["pnl"] for t in losing) != 0 else 0
    }


@app.get("/health")
async def health():
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8893)
