#!/usr/bin/env python3
"""
═══════════════════════════════════════════════════════════════════════════════
NEO TRAINING SYNC — Fetch CRELLA Bridge Outcome Data
═══════════════════════════════════════════════════════════════════════════════

Polls CRELLA's Truth Pipeline for trade outcomes and feeds them into NEO's
learning system. This closes the feedback loop:

  CRELLA trades → outcomes → this script → NEO calibration → better signals

Modes:
  python neo_training_sync.py           # Fetch compact index (hourly)
  python neo_training_sync.py full      # Fetch full annotated dataset (daily)
  python neo_training_sync.py daemon    # Run as daemon (hourly index + daily full)

Data Flow:
  CRELLA (100.119.161.65:8097/truth/training) → crella_index.json
  CRELLA (100.119.161.65:8097/truth/training/full) → crella_trades_annotated.jsonl
  → NeoLearner.calibrate_from_crella() reads crella_index.json

Author: Quinn
Created: 2026-02-09
═══════════════════════════════════════════════════════════════════════════════
"""

import json
import sys
import time
import logging
import requests
from datetime import datetime, timedelta
from pathlib import Path

# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

CRELLA_HOST = "100.119.161.65"  # desktop-gringot via Tailscale
CRELLA_PORT = 8097
CRELLA_TRAINING_URL = f"http://{CRELLA_HOST}:{CRELLA_PORT}/truth/training"
CRELLA_TRAINING_FULL_URL = f"http://{CRELLA_HOST}:{CRELLA_PORT}/truth/training/full"
CRELLA_LATEST_URL = f"http://{CRELLA_HOST}:{CRELLA_PORT}/truth/latest"
CRELLA_HEALTH_URL = f"http://{CRELLA_HOST}:{CRELLA_PORT}/truth/health"

# Where to store fetched data
TRAINING_DIR = Path("/home/jbot/trading_ai/neo/training_data")
TRAINING_DIR.mkdir(parents=True, exist_ok=True)

INDEX_FILE = TRAINING_DIR / "crella_index.json"
FULL_DATASET_FILE = TRAINING_DIR / "crella_trades_annotated.jsonl"
SYNC_STATE_FILE = TRAINING_DIR / "sync_state.json"

# Log setup
LOG_DIR = Path("/home/jbot/trading_ai/logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | NEO-SYNC | %(levelname)s | %(message)s',
    handlers=[
        logging.FileHandler(LOG_DIR / 'neo_training_sync.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('NeoTrainingSync')

# ═══════════════════════════════════════════════════════════════════════════════
# SYNC STATE
# ═══════════════════════════════════════════════════════════════════════════════

def load_sync_state() -> dict:
    """Load sync state (last successful fetch times, etc.)"""
    default = {
        "last_index_sync": None,
        "last_full_sync": None,
        "last_index_baskets": 0,
        "last_full_records": 0,
        "total_syncs": 0,
        "consecutive_failures": 0,
        "crella_reachable": False
    }
    try:
        if SYNC_STATE_FILE.exists():
            data = json.loads(SYNC_STATE_FILE.read_text())
            for k, v in default.items():
                data.setdefault(k, v)
            return data
    except Exception:
        pass
    return default


def save_sync_state(state: dict):
    """Persist sync state"""
    SYNC_STATE_FILE.write_text(json.dumps(state, indent=2))


# ═══════════════════════════════════════════════════════════════════════════════
# FETCH TRAINING INDEX (compact — hourly)
# ═══════════════════════════════════════════════════════════════════════════════

def fetch_training_index() -> dict:
    """
    Fetch compact training index from CRELLA.
    Contains basket-level outcomes: sentiment regime, ATR bucket, layers,
    direction, control params, P&L, drawdown, duration.
    
    Returns: parsed data dict or None on failure.
    """
    state = load_sync_state()
    
    try:
        logger.info(f"Fetching training index from {CRELLA_TRAINING_URL}...")
        r = requests.get(CRELLA_TRAINING_URL, timeout=30)
        
        if r.status_code == 200:
            data = r.json()
            
            # Save index
            INDEX_FILE.write_text(json.dumps(data, indent=2))
            
            # Extract stats
            stats = data.get("stats", {})
            index = data.get("index", [])
            baskets_with_outcome = sum(1 for b in index if b.get("has_outcome"))
            
            logger.info(
                f"✅ Index synced: {len(index)} baskets "
                f"({baskets_with_outcome} with outcomes), "
                f"WR={stats.get('win_rate', 0)}%, "
                f"PnL=${stats.get('total_pnl', 0):,.2f}"
            )
            
            # Update state
            state["last_index_sync"] = datetime.utcnow().isoformat()
            state["last_index_baskets"] = len(index)
            state["total_syncs"] = state.get("total_syncs", 0) + 1
            state["consecutive_failures"] = 0
            state["crella_reachable"] = True
            save_sync_state(state)
            
            # Trigger calibration if we have new data
            _trigger_calibration(data)
            
            return data
        else:
            logger.warning(f"Training index fetch failed: HTTP {r.status_code}")
            state["consecutive_failures"] = state.get("consecutive_failures", 0) + 1
            state["crella_reachable"] = False
            save_sync_state(state)
            
    except requests.ConnectionError:
        logger.warning(f"CRELLA unreachable at {CRELLA_HOST}:{CRELLA_PORT} (connection refused)")
        state["consecutive_failures"] = state.get("consecutive_failures", 0) + 1
        state["crella_reachable"] = False
        save_sync_state(state)
        
    except Exception as e:
        logger.error(f"Error fetching training index: {e}")
        state["consecutive_failures"] = state.get("consecutive_failures", 0) + 1
        save_sync_state(state)
    
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# FETCH LATEST TRUTH DATA (market metrics — every 5 min)
# ═══════════════════════════════════════════════════════════════════════════════

LATEST_FILE = TRAINING_DIR / "crella_truth_latest.json"


def fetch_latest_truth() -> dict:
    """
    Fetch latest truth data from CRELLA (market metrics, bias, indicators).
    This endpoint IS live. Contains VWAP, RSI, ATR, session, EMA, Ichimoku, etc.
    
    Returns: parsed data dict or None on failure.
    """
    state = load_sync_state()
    
    try:
        r = requests.get(CRELLA_LATEST_URL, timeout=15)
        
        if r.status_code == 200:
            data = r.json()
            
            # Save latest
            LATEST_FILE.write_text(json.dumps(data, indent=2))
            
            market = data.get("market", {})
            metrics = data.get("metrics", [])
            
            # Count biases
            buys = sum(1 for m in metrics if m.get("bias") == "BUY")
            sells = sum(1 for m in metrics if m.get("bias") == "SELL")
            holds = sum(1 for m in metrics if m.get("bias") == "HOLD")
            
            logger.info(
                f"✅ Truth latest: ${market.get('price', 0):.2f} "
                f"| BUY:{buys} SELL:{sells} HOLD:{holds} "
                f"| Session: {market.get('session', 'N/A')}"
            )
            
            state["last_latest_sync"] = datetime.utcnow().isoformat()
            state["crella_reachable"] = True
            state["consecutive_failures"] = 0
            save_sync_state(state)
            
            return data
        else:
            logger.warning(f"Truth latest fetch failed: HTTP {r.status_code}")
            
    except requests.ConnectionError:
        logger.warning(f"CRELLA unreachable for /truth/latest")
        state["crella_reachable"] = False
        save_sync_state(state)
        
    except Exception as e:
        logger.error(f"Error fetching truth latest: {e}")
    
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# FETCH FULL DATASET (heavy — daily)
# ═══════════════════════════════════════════════════════════════════════════════

def fetch_full_dataset() -> int:
    """
    Fetch full annotated dataset from CRELLA (~5MB).
    Contains 5000+ individual decision records with full market state,
    controls applied, and outcome stamps.
    
    Returns: number of records fetched, or 0 on failure.
    """
    state = load_sync_state()
    
    try:
        logger.info(f"Fetching full dataset from {CRELLA_TRAINING_FULL_URL}...")
        r = requests.get(CRELLA_TRAINING_FULL_URL, timeout=120)
        
        if r.status_code == 200:
            data = r.json()
            records = data.get("records", [])
            
            if not records:
                logger.warning("Full dataset returned 0 records")
                return 0
            
            # Save as JSONL for training pipelines
            with open(FULL_DATASET_FILE, "w") as f:
                for rec in records:
                    f.write(json.dumps(rec) + "\n")
            
            # Also save a dated snapshot
            date_str = datetime.utcnow().strftime("%Y%m%d")
            snapshot = TRAINING_DIR / f"crella_full_{date_str}.jsonl"
            with open(snapshot, "w") as f:
                for rec in records:
                    f.write(json.dumps(rec) + "\n")
            
            logger.info(f"✅ Full dataset synced: {len(records)} annotated records")
            
            # Update state
            state["last_full_sync"] = datetime.utcnow().isoformat()
            state["last_full_records"] = len(records)
            state["crella_reachable"] = True
            save_sync_state(state)
            
            return len(records)
        else:
            logger.warning(f"Full dataset fetch failed: HTTP {r.status_code}")
            
    except requests.ConnectionError:
        logger.warning(f"CRELLA unreachable for full dataset")
        
    except Exception as e:
        logger.error(f"Error fetching full dataset: {e}")
    
    return 0


# ═══════════════════════════════════════════════════════════════════════════════
# TRIGGER CALIBRATION
# ═══════════════════════════════════════════════════════════════════════════════

def _trigger_calibration(data: dict):
    """
    After fetching new index data, trigger NEO learner calibration.
    Calls the local NeoLearner to update confidence weights.
    """
    try:
        sys.path.insert(0, str(Path(__file__).parent))
        from neo_learner import get_learner
        
        learner = get_learner()
        learner.calibrate_from_crella()
        logger.info("✅ NEO calibration triggered from CRELLA outcomes")
        
    except ImportError:
        logger.warning("NeoLearner not available — calibration skipped")
    except Exception as e:
        logger.error(f"Calibration error: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# DAEMON MODE
# ═══════════════════════════════════════════════════════════════════════════════

def run_daemon():
    """
    Run as a long-lived process:
    - Fetch index every hour
    - Fetch full dataset once daily at 00:30 UTC
    - Exponential backoff on failures (max 30 min)
    """
    logger.info("=" * 70)
    logger.info("NEO TRAINING SYNC — DAEMON MODE")
    logger.info(f"  CRELLA: {CRELLA_HOST}:{CRELLA_PORT}")
    logger.info(f"  Truth latest: every 5 min")
    logger.info(f"  Training index: every 1 hour (when endpoint available)")
    logger.info(f"  Full dataset: daily at 00:30 UTC (when endpoint available)")
    logger.info(f"  Output: {TRAINING_DIR}")
    logger.info("=" * 70)
    
    last_latest_fetch = datetime.min
    last_index_fetch = datetime.min
    last_full_fetch = datetime.min
    
    LATEST_INTERVAL = 300      # 5 minutes
    INDEX_INTERVAL = 3600      # 1 hour
    FULL_INTERVAL = 86400      # 1 day
    BASE_SLEEP = 60            # Check every minute
    
    # Initial fetch
    fetch_latest_truth()
    
    while True:
        try:
            now = datetime.utcnow()
            
            # Every 5 min: fetch latest truth (market metrics)
            if (now - last_latest_fetch).total_seconds() >= LATEST_INTERVAL:
                result = fetch_latest_truth()
                if result:
                    last_latest_fetch = now
            
            # Hourly: fetch training index (basket outcomes)
            if (now - last_index_fetch).total_seconds() >= INDEX_INTERVAL:
                result = fetch_training_index()
                if result:
                    last_index_fetch = now
                else:
                    # On failure, retry in 5 min instead of waiting full hour
                    last_index_fetch = now - timedelta(seconds=INDEX_INTERVAL - 300)
            
            # Daily full dataset fetch (around 00:30 UTC)
            if (now - last_full_fetch).total_seconds() >= FULL_INTERVAL:
                if now.hour == 0 and now.minute >= 30:
                    count = fetch_full_dataset()
                    if count > 0:
                        last_full_fetch = now
                elif (now - last_full_fetch).total_seconds() >= FULL_INTERVAL * 1.5:
                    # Fallback: if we missed the window, fetch anyway
                    count = fetch_full_dataset()
                    if count > 0:
                        last_full_fetch = now
            
            time.sleep(BASE_SLEEP)
            
        except KeyboardInterrupt:
            logger.info("Daemon shutdown requested")
            break
        except Exception as e:
            logger.error(f"Daemon error: {e}")
            time.sleep(BASE_SLEEP)
    
    logger.info("NEO Training Sync daemon stopped")


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    if len(sys.argv) > 1:
        mode = sys.argv[1].lower()
        
        if mode == "full":
            count = fetch_full_dataset()
            print(f"Fetched {count} records")
            
        elif mode == "daemon":
            run_daemon()
            
        elif mode == "status":
            state = load_sync_state()
            print(json.dumps(state, indent=2))
            
        else:
            print(f"Unknown mode: {mode}")
            print("Usage: neo_training_sync.py [full|daemon|status]")
    else:
        # Default: fetch compact index
        data = fetch_training_index()
        if data:
            stats = data.get("stats", {})
            print(f"Win rate: {stats.get('win_rate', 0)}%, PnL: ${stats.get('total_pnl', 0):,.2f}")
        else:
            print("Fetch failed (CRELLA may be unreachable)")
