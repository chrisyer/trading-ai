#!/usr/bin/env python3
"""
Chaos Simulator
===============
Stress-tests the governor by injecting synthetic market shocks.
Useful for regression testing risk rules.

Run: python chaos_sim.py
"""

import json
import random
import time
from pathlib import Path
from datetime import datetime


def load_states(path: Path) -> list:
    """Load state snapshots for replay"""
    if not path.exists():
        return []
    
    txt = path.read_text(encoding="utf-8", errors="ignore").strip()
    if not txt:
        return []
    
    # Handle single JSON object
    if txt.startswith("{"):
        return [json.loads(txt)]
    
    # Handle JSONL
    out = []
    for line in txt.splitlines():
        try:
            out.append(json.loads(line))
        except:
            pass
    return out


def shock(state: dict, mode: str, severity: float, seed: int) -> dict:
    """
    Apply a synthetic shock to a state snapshot.
    
    Modes:
    - news_spike: ATR up, spread up, price moves against position
    - liquidity_gap: Spread explodes, thin book
    - flash_crash: Extreme adverse move
    """
    rnd = random.Random(seed)
    s = dict(state)
    
    if mode == "news_spike":
        # ATR spikes
        s["atr"] = float(s.get("atr", 3.0)) * (1.0 + severity * (1.5 + rnd.random()))
        # Spread widens
        s["spread_points"] = int(float(s.get("spread_points", 30)) * (1.0 + severity * (2.0 + rnd.random())))
        # Adversarial price move
        direction = int(s.get("dir", 0))
        if direction != 0:
            bump = float(s.get("atr", 3.0)) * (1.0 + severity)
            if direction == 1:  # Long gets hit down
                s["bid"] = float(s.get("bid", 2300)) - bump
                s["ask"] = float(s.get("ask", 2300)) - bump
            else:  # Short gets hit up
                s["bid"] = float(s.get("bid", 2300)) + bump
                s["ask"] = float(s.get("ask", 2300)) + bump
    
    elif mode == "liquidity_gap":
        # Extreme spread
        s["spread_points"] = int(float(s.get("spread_points", 30)) * (3.0 + severity * 2))
        # ATR unchanged (quiet but illiquid)
    
    elif mode == "flash_crash":
        # Everything goes wrong at once
        s["atr"] = float(s.get("atr", 3.0)) * (2.0 + severity * 2)
        s["spread_points"] = int(float(s.get("spread_points", 30)) * (5.0 + severity))
        direction = int(s.get("dir", 0))
        bump = float(s.get("atr", 3.0)) * (3.0 + severity * 2)
        if direction == 1:
            s["bid"] = float(s.get("bid", 2300)) - bump
            s["ask"] = float(s.get("ask", 2300)) - bump
        elif direction == -1:
            s["bid"] = float(s.get("bid", 2300)) + bump
            s["ask"] = float(s.get("ask", 2300)) + bump
    
    s["chaos_mode"] = mode
    s["chaos_severity"] = severity
    s["timestamp"] = datetime.now().isoformat()
    
    return s


def generate_baseline_state() -> dict:
    """Generate a reasonable baseline state if no history exists"""
    return {
        "symbol": "XAUUSD",
        "bid": 2320.50,
        "ask": 2320.80,
        "atr": 4.2,
        "rsi": 55.0,
        "bb_width": 12.5,
        "spread_points": 30,
        "layers": 2,
        "dir": 1,
        "net_lots": 0.06,
        "floating_pnl": -85.0,
        "timestamp": datetime.now().isoformat()
    }


def main():
    """Run chaos simulation"""
    base = Path(__file__).resolve().parents[1]
    cfg_path = base / "config" / "bridge_config.json"
    
    if cfg_path.exists():
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    else:
        cfg = {
            "mt5_files_dir": "/home/jbot/.wine/drive_c/Program Files/MetaTrader 5/MQL5/Files",
            "state_file": "aiiq_state.json",
            "control_file": "aiiq_control.json"
        }
    
    files_dir = Path(cfg["mt5_files_dir"])
    state_file = files_dir / cfg["state_file"]
    control_file = files_dir / cfg["control_file"]
    
    # Load existing states or generate baseline
    states = load_states(state_file)
    if not states:
        print("No existing states found. Generating baseline.")
        states = [generate_baseline_state()]
    
    print("="*70)
    print("🔥 CHAOS SIMULATOR")
    print("="*70)
    print(f"Source states: {len(states)}")
    print(f"Output: {state_file}")
    print(f"Reading: {control_file}")
    print("="*70)
    print()
    
    modes = ["news_spike", "liquidity_gap", "flash_crash"]
    results = {"halted": 0, "tightened": 0, "unchanged": 0, "total": 0}
    
    # Run scenarios
    for i in range(50):
        st = random.choice(states)
        mode = random.choice(modes)
        severity = random.uniform(0.3, 1.0)
        
        shocked = shock(st, mode, severity, seed=i)
        
        # Write shocked state
        state_file.write_text(json.dumps(shocked, indent=2), encoding="utf-8")
        
        # Wait for bridge to process
        time.sleep(0.5)
        
        # Read control response
        ctrl = {}
        if control_file.exists():
            try:
                ctrl = json.loads(control_file.read_text(encoding="utf-8", errors="ignore"))
            except:
                pass
        
        # Analyze response
        disabled = ctrl.get("disable_new_entries", False)
        dca_mult = ctrl.get("dca_step_multiplier", 1.0)
        max_layers = ctrl.get("max_layers_cap", 3)
        
        results["total"] += 1
        
        if disabled:
            status = "🛑 HALTED"
            results["halted"] += 1
        elif dca_mult > 1.0 or max_layers < 3:
            status = "⚠️ TIGHTENED"
            results["tightened"] += 1
        else:
            status = "✅ UNCHANGED"
            results["unchanged"] += 1
        
        print(f"[{i:02d}] {mode:15s} sev={severity:.2f} "
              f"ATR={shocked.get('atr', 0):.1f} spread={shocked.get('spread_points', 0):3d} "
              f"→ {status} (dca={dca_mult:.1f}, layers={max_layers})")
    
    # Summary
    print()
    print("="*70)
    print("RESULTS:")
    print(f"  Halted:    {results['halted']:3d} / {results['total']} ({100*results['halted']/results['total']:.0f}%)")
    print(f"  Tightened: {results['tightened']:3d} / {results['total']} ({100*results['tightened']/results['total']:.0f}%)")
    print(f"  Unchanged: {results['unchanged']:3d} / {results['total']} ({100*results['unchanged']/results['total']:.0f}%)")
    print("="*70)
    
    # Expectation: during chaos, most responses should be halted or tightened
    protection_rate = (results['halted'] + results['tightened']) / results['total']
    if protection_rate >= 0.7:
        print("✅ PASS: Governor protected during chaos")
    else:
        print("❌ WARN: Governor may be too permissive during chaos")


if __name__ == "__main__":
    main()
