#!/usr/bin/env python3
"""
SIGNAL WRITER FOR CRELLA'S DESKTOP
===================================
Reads bridge state and writes ea_signal.json for Crella's bots.
Runs continuously, updates every 30 seconds.

Output folder can be synced via Syncthing/SMB to Crella's MQL5/Files/
"""

import json
import time
import os
from pathlib import Path
from datetime import datetime, timedelta

# ══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ══════════════════════════════════════════════════════════════════════════════

# Where to read bridge state from
BRIDGE_DIR = Path("/home/jbot/trading_ai/bridge")
CONTROL_FILE = Path("/home/jbot/.wine/drive_c/Program Files/MetaTrader 5/MQL5/Files/aiiq_control.json")
SENTIMENT_FILE = Path("/home/jbot/.wine/drive_c/Program Files/MetaTrader 5/MQL5/Files/aiiq_sentiment.json")

# Where to write signals for Crella (sync this folder!)
OUTPUT_DIR = Path("/home/jbot/trading_ai/crella_signals")
EA_SIGNAL_FILE = OUTPUT_DIR / "ea_signal.json"
DEFCON_FILE = OUTPUT_DIR / "defcon_state.json"

# Update interval
UPDATE_SECONDS = 30

# ══════════════════════════════════════════════════════════════════════════════
# SIGNAL GENERATION
# ══════════════════════════════════════════════════════════════════════════════

def load_json(path: Path) -> dict:
    """Load JSON file safely"""
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except:
        pass
    return {}


def map_to_defcon(control: dict, sentiment: dict) -> int:
    """Map bridge state to DEFCON level"""
    
    # DEFCON 5 = Full halt
    if control.get("disable_new_entries", False):
        return 5
    
    # Check sentiment
    sent_regime = sentiment.get("sentiment_regime", "neutral")
    intensity = float(sentiment.get("intensity", 0.5))
    event_risk = sentiment.get("event_risk", "low")
    
    # DEFCON 4 = Restricted (panic or high event risk)
    if "panic" in sent_regime or event_risk == "high":
        return 4
    
    # DEFCON 3 = Elevated (high intensity or reduced layers)
    if intensity > 0.7 or control.get("max_layers_cap", 3) < 3:
        return 3
    
    # DEFCON 2 = Caution (mild bearish/bullish)
    if "mild" in sent_regime and sent_regime != "neutral":
        return 2
    
    # DEFCON 1 = Normal
    return 1


def create_ea_signal(control: dict, sentiment: dict) -> dict:
    """Create ea_signal.json from bridge state"""
    
    defcon = map_to_defcon(control, sentiment)
    defcon_colors = {1: "GREEN", 2: "YELLOW", 3: "ORANGE", 4: "RED", 5: "BLACK"}
    
    # Determine direction hint (not a trade signal, just sentiment)
    direction = "HOLD"
    sent_regime = sentiment.get("sentiment_regime", "neutral")
    if "bullish" in sent_regime:
        direction = "BULLISH_BIAS"
    elif "bearish" in sent_regime:
        direction = "BEARISH_BIAS"
    
    # Calculate lot multiplier
    lot_mult = 1.0
    if defcon >= 4:
        lot_mult = 0.5
    if defcon >= 5:
        lot_mult = 0.0
    
    # DCA multiplier affects lot sizing indirectly
    dca_mult = float(control.get("dca_step_multiplier", 1.0))
    if dca_mult > 1.5:
        lot_mult *= 0.7  # Reduce size if DCA is very wide
    
    return {
        "timestamp": datetime.now().isoformat(),
        "symbol": "XAUUSD",
        "direction": direction,
        "conviction": int((1 - (defcon - 1) / 4) * 100),  # DEFCON 1=100%, DEFCON 5=0%
        "defcon": defcon,
        "defcon_color": defcon_colors.get(defcon, "ORANGE"),
        "action": f"DEFCON {defcon} - {defcon_colors.get(defcon, 'UNKNOWN')}",
        
        "targets": {
            "tp": 0,
            "sl": 0,
            "hunt_zone": 0
        },
        
        "ea_instructions": {
            "pause_longs": defcon >= 5 or control.get("disable_new_entries", False),
            "pause_shorts": defcon >= 5 or control.get("disable_new_entries", False),
            "reduce_lot_multiplier": lot_mult,
            "tighten_sl_pips": 0,
            "max_drawdown_override": 0,
            "close_partial": 0,
            "set_breakeven": False,
            "consider_hedge": defcon >= 4
        },
        
        "reasoning": f"Sentiment: {sent_regime}, Intensity: {sentiment.get('intensity', 0.5):.0%}, Event Risk: {sentiment.get('event_risk', 'low')}",
        "valid_until": (datetime.now() + timedelta(minutes=5)).isoformat(),
        "source": "quinn_bridge_v3"
    }


def create_defcon_state(defcon: int) -> dict:
    """Create defcon_state.json"""
    defcon_colors = {1: "GREEN", 2: "YELLOW", 3: "ORANGE", 4: "RED", 5: "BLACK"}
    return {
        "level": defcon,
        "color": defcon_colors.get(defcon, "ORANGE"),
        "timestamp": datetime.now().isoformat()
    }


def atomic_write(path: Path, content: str):
    """Write atomically"""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(str(tmp), str(path))


def main():
    """Main loop"""
    print("="*60)
    print("📡 QUINN SIGNAL WRITER FOR CRELLA")
    print("="*60)
    print(f"Reading:  {CONTROL_FILE}")
    print(f"          {SENTIMENT_FILE}")
    print(f"Writing:  {EA_SIGNAL_FILE}")
    print(f"          {DEFCON_FILE}")
    print(f"Interval: {UPDATE_SECONDS}s")
    print("="*60)
    print()
    print("⚠️  SYNC THIS FOLDER TO CRELLA'S MQL5/Files/:")
    print(f"    {OUTPUT_DIR}")
    print()
    print("="*60)
    
    # Create output directory
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    
    while True:
        try:
            # Read current state
            control = load_json(CONTROL_FILE)
            sentiment = load_json(SENTIMENT_FILE)
            
            # Generate signals
            ea_signal = create_ea_signal(control, sentiment)
            defcon = ea_signal["defcon"]
            defcon_state = create_defcon_state(defcon)
            
            # Write files
            atomic_write(EA_SIGNAL_FILE, json.dumps(ea_signal, indent=2))
            atomic_write(DEFCON_FILE, json.dumps(defcon_state, indent=2))
            
            # Log
            print(f"[{datetime.now().strftime('%H:%M:%S')}] "
                  f"DEFCON {defcon} ({ea_signal['defcon_color']}) "
                  f"lots={ea_signal['ea_instructions']['reduce_lot_multiplier']:.1f} "
                  f"pause={ea_signal['ea_instructions']['pause_longs']}")
            
        except Exception as e:
            print(f"Error: {e}")
        
        time.sleep(UPDATE_SECONDS)


if __name__ == "__main__":
    main()
