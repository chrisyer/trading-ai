#!/usr/bin/env python3
"""
═══════════════════════════════════════════════════════════════════════════════
NEO SIGNAL GENERATOR SERVICE
═══════════════════════════════════════════════════════════════════════════════

This service runs continuously and:
1. Generates fresh XAUUSD signals every 60 seconds
2. Saves signals to files for Ghost/Casper to read
3. Reports signals to the H100 API for consensus
4. Writes to ghost_directives.txt

CRITICAL: This is what was MISSING - nothing was actually generating signals!
═══════════════════════════════════════════════════════════════════════════════
"""

import os
import sys
import json
import time
import logging
import requests
from datetime import datetime
from pathlib import Path

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [NEO-SIGNAL-GEN] %(levelname)s: %(message)s'
)
logger = logging.getLogger("SignalGenerator")

# Paths
SIGNAL_DIR = Path("/home/jbot/trading_ai/neo/signals")
SIGNAL_DIR.mkdir(exist_ok=True)

DIRECTIVES_FILE = Path("/home/jbot/trading_ai/intel/ghost_directives.txt")
FRESH_SIGNAL_FILE = SIGNAL_DIR / "xauusd_fresh_signal.json"

# API endpoints
H100_API = "http://127.0.0.1:8036"
UNIFIED_ENDPOINT = f"{H100_API}/api/unified/xauusd/signal"
STATUS_ENDPOINT = f"{H100_API}/api/neo/status"

# Configuration
SIGNAL_INTERVAL = 60  # seconds between signal generation
SIGNALS_TODAY = 0
LAST_RESET_DATE = None


def fetch_signal_from_api() -> dict:
    """Fetch fresh signal from the unified API."""
    try:
        response = requests.get(UNIFIED_ENDPOINT, timeout=30)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logger.error(f"Failed to fetch signal: {e}")
        return None


def save_signal_to_file(signal: dict):
    """Save signal to JSON file."""
    global SIGNALS_TODAY
    
    try:
        # Save fresh signal file (always overwrite)
        FRESH_SIGNAL_FILE.write_text(json.dumps(signal, indent=2))
        
        # Also save timestamped signal
        timestamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
        signal_id = signal.get("signal_id", f"NEO_XAUUSD_{timestamp}")
        timestamped_file = SIGNAL_DIR / f"signal_{signal_id}.json"
        timestamped_file.write_text(json.dumps(signal, indent=2))
        
        SIGNALS_TODAY += 1
        logger.info(f"✅ Signal saved: {FRESH_SIGNAL_FILE}")
        
    except Exception as e:
        logger.error(f"Failed to save signal: {e}")


def write_ghost_directives(signal: dict):
    """Write signal to ghost_directives.txt for EAs to read."""
    try:
        action = signal.get("action", "WAIT")
        confidence = signal.get("confidence", 0)
        current_price = signal.get("current_price", 0)
        entry = signal.get("entry", current_price)
        stop_loss = signal.get("stop_loss", 0)
        take_profit_1 = signal.get("take_profit_1", 0)
        take_profit_2 = signal.get("take_profit_2", 0)
        strategy = signal.get("strategy", "UNKNOWN")
        reasoning = signal.get("reasoning", "")
        
        # Get consensus info
        neo_action = signal.get("neo", {}).get("action", "N/A")
        meta_action = signal.get("meta", {}).get("action", "N/A")
        
        content = f"""# NEO GHOST DIRECTIVES
# Generated: {datetime.utcnow().isoformat()}Z
# This file is auto-generated every {SIGNAL_INTERVAL} seconds

[SIGNAL]
SYMBOL=XAUUSD
ACTION={action}
CONFIDENCE={confidence}
STRATEGY={strategy}

[LEVELS]
CURRENT_PRICE={current_price}
ENTRY={entry}
ENTRY_ZONE_LOW={signal.get('entry_zone_low', entry - 20)}
ENTRY_ZONE_HIGH={signal.get('entry_zone_high', entry + 20)}
STOP_LOSS={stop_loss}
TAKE_PROFIT_1={take_profit_1}
TAKE_PROFIT_2={take_profit_2}

[CONSENSUS]
NEO_H100={neo_action}
META_BOT={meta_action}
UNIFIED={action}

[META]
SIGNAL_ID={signal.get('signal_id', 'N/A')}
VALID={signal.get('valid', False)}
SIGNALS_TODAY={SIGNALS_TODAY}
LAST_UPDATED={datetime.utcnow().isoformat()}Z

[REASONING]
{reasoning}
"""
        
        DIRECTIVES_FILE.parent.mkdir(exist_ok=True)
        DIRECTIVES_FILE.write_text(content)
        logger.info(f"📝 Directives written: {DIRECTIVES_FILE}")
        
    except Exception as e:
        logger.error(f"Failed to write directives: {e}")


def check_daily_reset():
    """Reset daily counter at midnight UTC."""
    global SIGNALS_TODAY, LAST_RESET_DATE
    
    today = datetime.utcnow().date()
    if LAST_RESET_DATE != today:
        SIGNALS_TODAY = 0
        LAST_RESET_DATE = today
        logger.info(f"🔄 Daily reset - new day: {today}")


def run_signal_generation():
    """Main signal generation loop."""
    global SIGNALS_TODAY
    
    logger.info("="*70)
    logger.info("🚀 NEO SIGNAL GENERATOR SERVICE STARTING")
    logger.info("="*70)
    logger.info(f"Signal Interval: {SIGNAL_INTERVAL} seconds")
    logger.info(f"Output: {FRESH_SIGNAL_FILE}")
    logger.info(f"Directives: {DIRECTIVES_FILE}")
    logger.info("="*70)
    
    consecutive_failures = 0
    
    while True:
        try:
            check_daily_reset()
            
            # Fetch signal from API
            logger.info(f"📡 Fetching signal from {UNIFIED_ENDPOINT}...")
            signal = fetch_signal_from_api()
            
            if signal and signal.get("valid"):
                # Save signal
                save_signal_to_file(signal)
                
                # Write directives for Ghost/Casper
                write_ghost_directives(signal)
                
                # Log summary
                action = signal.get("action", "N/A")
                confidence = signal.get("confidence", 0)
                price = signal.get("current_price", 0)
                
                logger.info(f"")
                logger.info(f"═══════════════════════════════════════════════════════")
                logger.info(f"  SIGNAL: {action} @ ${price:.2f} ({confidence:.0f}% confidence)")
                logger.info(f"  Strategy: {signal.get('strategy', 'N/A')}")
                logger.info(f"  Signals Today: {SIGNALS_TODAY}")
                logger.info(f"═══════════════════════════════════════════════════════")
                logger.info(f"")
                
                consecutive_failures = 0
                
            elif signal:
                logger.warning(f"⚠️ Signal invalid: {signal.get('invalidation', {}).get('reason', 'Unknown')}")
                # Still save it but mark as invalid
                save_signal_to_file(signal)
                write_ghost_directives(signal)
                consecutive_failures = 0
            else:
                consecutive_failures += 1
                logger.error(f"❌ Failed to get signal (attempt {consecutive_failures})")
                
                # If too many failures, restart might be needed
                if consecutive_failures >= 5:
                    logger.critical("🚨 5 consecutive failures! Check API health.")
            
        except Exception as e:
            logger.error(f"❌ Error in signal generation: {e}")
            consecutive_failures += 1
        
        # Wait for next cycle
        logger.info(f"⏳ Next signal in {SIGNAL_INTERVAL} seconds...")
        time.sleep(SIGNAL_INTERVAL)


if __name__ == "__main__":
    run_signal_generation()
