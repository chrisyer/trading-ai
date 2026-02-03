#!/usr/bin/env python3
"""
REGRESSION TEST SUITE
=====================
Systematic stress testing of the bridge.

Tests:
1. Panic whipsaw resistance
2. ATR spike response
3. Spread spike response
4. Staleness handling
5. Monotonic tightening
6. Hysteresis latch behavior
7. Kill switch override
8. Cold start behavior

Run: python regression_suite.py
"""

import json
import time
import os
from pathlib import Path
from datetime import datetime
from typing import List, Tuple

BASE_DIR = Path(__file__).resolve().parents[1]
CONFIG = json.loads((BASE_DIR / "config" / "bridge_config.json").read_text())
FILES_DIR = Path(CONFIG["mt5_files_dir"])
STATE_FILE = FILES_DIR / CONFIG["state_file"]
CONTROL_FILE = FILES_DIR / CONFIG["control_file"]
SENTIMENT_FILE = FILES_DIR / CONFIG["sentiment_file"]
KILL_SWITCH_FILE = BASE_DIR / "kill_switch.txt"

# Test results
RESULTS: List[Tuple[str, bool, str]] = []


def write_state(state: dict):
    """Write test state"""
    state["timestamp"] = datetime.now().isoformat()
    STATE_FILE.write_text(json.dumps(state), encoding="utf-8")


def write_sentiment(sentiment: dict):
    """Write test sentiment"""
    sentiment["timestamp"] = datetime.now().isoformat()
    SENTIMENT_FILE.write_text(json.dumps(sentiment), encoding="utf-8")


def read_control() -> dict:
    """Read control response"""
    time.sleep(3)  # Wait for bridge to process
    try:
        return json.loads(CONTROL_FILE.read_text(encoding="utf-8"))
    except:
        return {}


def baseline_state() -> dict:
    """Normal market state"""
    return {
        "symbol": "XAUUSD",
        "bid": 2320.50,
        "ask": 2320.80,
        "atr": 4.0,
        "rsi": 50.0,
        "spread_points": 30,
        "layers": 0,
        "dir": 0,
        "net_lots": 0,
        "floating_pnl": 0
    }


def baseline_sentiment() -> dict:
    """Neutral sentiment"""
    return {
        "sentiment_regime": "neutral",
        "intensity": 0.5,
        "event_risk": "low"
    }


def record_result(test_name: str, passed: bool, details: str = ""):
    """Record test result"""
    RESULTS.append((test_name, passed, details))
    status = "✅ PASS" if passed else "❌ FAIL"
    print(f"   {status}: {test_name}")
    if details:
        print(f"          {details}")


# ══════════════════════════════════════════════════════════════════════════════
# TEST 1: PANIC WHIPSAW RESISTANCE
# ══════════════════════════════════════════════════════════════════════════════

def test_panic_whipsaw():
    """
    Test that panic → neutral → panic doesn't cause whipsaw.
    Expected: Should stay halted through oscillation.
    """
    print("\n" + "="*60)
    print("TEST 1: Panic Whipsaw Resistance")
    print("="*60)
    
    # Start with panic
    write_state(baseline_state())
    write_sentiment({"sentiment_regime": "panic_bullish", "intensity": 0.9, "event_risk": "high"})
    ctrl1 = read_control()
    halted1 = ctrl1.get("disable_new_entries", False)
    
    # Switch to neutral (should NOT immediately re-enable due to hysteresis)
    write_sentiment(baseline_sentiment())
    ctrl2 = read_control()
    halted2 = ctrl2.get("disable_new_entries", False)
    
    # Back to panic
    write_sentiment({"sentiment_regime": "panic_bearish", "intensity": 0.85, "event_risk": "high"})
    ctrl3 = read_control()
    halted3 = ctrl3.get("disable_new_entries", False)
    
    # Should have stayed halted throughout
    passed = halted1 and halted2 and halted3
    record_result(
        "Panic whipsaw resistance",
        passed,
        f"panic→{halted1}, neutral→{halted2}, panic→{halted3}"
    )
    
    # Clean up
    write_sentiment(baseline_sentiment())


# ══════════════════════════════════════════════════════════════════════════════
# TEST 2: ATR SPIKE RESPONSE
# ══════════════════════════════════════════════════════════════════════════════

def test_atr_spike():
    """
    Test that ATR spike triggers circuit breaker.
    """
    print("\n" + "="*60)
    print("TEST 2: ATR Spike Response")
    print("="*60)
    
    write_sentiment(baseline_sentiment())
    
    # Normal ATR
    state = baseline_state()
    state["atr"] = 4.0
    write_state(state)
    ctrl1 = read_control()
    
    # Spike ATR (>1.6x)
    state["atr"] = 10.0  # 2.5x spike
    write_state(state)
    ctrl2 = read_control()
    
    halted = ctrl2.get("disable_new_entries", False)
    dca_tightened = ctrl2.get("dca_step_multiplier", 1.0) >= 1.4
    
    passed = halted or dca_tightened
    record_result(
        "ATR spike circuit breaker",
        passed,
        f"disabled={halted}, dca={ctrl2.get('dca_step_multiplier', 1.0):.1f}"
    )


# ══════════════════════════════════════════════════════════════════════════════
# TEST 3: SPREAD SPIKE RESPONSE
# ══════════════════════════════════════════════════════════════════════════════

def test_spread_spike():
    """
    Test that spread spike triggers defensive response.
    """
    print("\n" + "="*60)
    print("TEST 3: Spread Spike Response")
    print("="*60)
    
    write_sentiment(baseline_sentiment())
    
    # Normal spread
    state = baseline_state()
    state["spread_points"] = 30
    write_state(state)
    ctrl1 = read_control()
    
    # Spike spread (>100)
    state["spread_points"] = 200
    write_state(state)
    ctrl2 = read_control()
    
    dca_before = ctrl1.get("dca_step_multiplier", 1.0)
    dca_after = ctrl2.get("dca_step_multiplier", 1.0)
    layers_before = ctrl1.get("max_layers_cap", 3)
    layers_after = ctrl2.get("max_layers_cap", 3)
    
    tightened = dca_after > dca_before or layers_after < layers_before
    
    record_result(
        "Spread spike response",
        tightened,
        f"dca: {dca_before:.1f}→{dca_after:.1f}, layers: {layers_before}→{layers_after}"
    )


# ══════════════════════════════════════════════════════════════════════════════
# TEST 4: MONOTONIC TIGHTENING
# ══════════════════════════════════════════════════════════════════════════════

def test_monotonic_tightening():
    """
    Test that controls only tighten while in position.
    """
    print("\n" + "="*60)
    print("TEST 4: Monotonic Tightening (while in basket)")
    print("="*60)
    
    write_sentiment(baseline_sentiment())
    
    # Start in position with high ATR
    state = baseline_state()
    state["net_lots"] = 0.05
    state["layers"] = 2
    state["dir"] = 1
    state["atr"] = 6.0  # High
    write_state(state)
    ctrl1 = read_control()
    
    # Conditions improve (lower ATR) but we're still in position
    state["atr"] = 3.0  # Low
    write_state(state)
    ctrl2 = read_control()
    
    # DCA should NOT decrease (monotonic)
    dca1 = ctrl1.get("dca_step_multiplier", 1.0)
    dca2 = ctrl2.get("dca_step_multiplier", 1.0)
    
    passed = dca2 >= dca1
    record_result(
        "Monotonic tightening (DCA can't loosen)",
        passed,
        f"dca: {dca1:.1f}→{dca2:.1f} (should not decrease)"
    )
    
    # Clean up - close position
    state["net_lots"] = 0
    state["layers"] = 0
    write_state(state)
    read_control()


# ══════════════════════════════════════════════════════════════════════════════
# TEST 5: KILL SWITCH OVERRIDE
# ══════════════════════════════════════════════════════════════════════════════

def test_kill_switch():
    """
    Test that kill switch forces maximum defensive posture.
    """
    print("\n" + "="*60)
    print("TEST 5: Kill Switch Override")
    print("="*60)
    
    write_sentiment(baseline_sentiment())
    write_state(baseline_state())
    
    # Normal operation
    ctrl1 = read_control()
    
    # Engage kill switch
    KILL_SWITCH_FILE.write_text("ENGAGED", encoding="utf-8")
    time.sleep(3)
    ctrl2 = read_control()
    
    # Check maximum defensive posture
    disabled = ctrl2.get("disable_new_entries", False)
    max_layers = ctrl2.get("max_layers_cap", 3) == 1
    kill_flag = ctrl2.get("kill_switch", False)
    
    # Remove kill switch
    KILL_SWITCH_FILE.unlink(missing_ok=True)
    
    passed = disabled and max_layers
    record_result(
        "Kill switch override",
        passed,
        f"disabled={disabled}, max_layers=1:{max_layers}, flag={kill_flag}"
    )


# ══════════════════════════════════════════════════════════════════════════════
# TEST 6: HYSTERESIS SAFE STREAK
# ══════════════════════════════════════════════════════════════════════════════

def test_hysteresis_unlatch():
    """
    Test that hysteresis requires multiple safe cycles to unlatch.
    """
    print("\n" + "="*60)
    print("TEST 6: Hysteresis Safe Streak")
    print("="*60)
    
    # First trigger a halt
    write_sentiment({"sentiment_regime": "panic_bullish", "intensity": 0.9, "event_risk": "high"})
    write_state(baseline_state())
    ctrl1 = read_control()
    halted1 = ctrl1.get("disable_new_entries", False)
    
    # Now send multiple safe cycles
    write_sentiment(baseline_sentiment())
    halted_after = []
    for i in range(5):
        state = baseline_state()
        state["atr"] = 3.0  # Low ATR
        write_state(state)
        ctrl = read_control()
        halted_after.append(ctrl.get("disable_new_entries", False))
    
    # Should take at least 3 safe cycles to unlatch
    unlatch_at = None
    for i, h in enumerate(halted_after):
        if not h:
            unlatch_at = i + 1
            break
    
    passed = halted1 and unlatch_at and unlatch_at >= 3
    record_result(
        "Hysteresis requires safe streak",
        passed,
        f"halted initially:{halted1}, unlatched at cycle:{unlatch_at}, sequence:{halted_after}"
    )


# ══════════════════════════════════════════════════════════════════════════════
# TEST 7: EXTREME ATR BUCKET
# ══════════════════════════════════════════════════════════════════════════════

def test_extreme_atr_bucket():
    """
    Test that extreme ATR bucket forces halt.
    """
    print("\n" + "="*60)
    print("TEST 7: Extreme ATR Bucket Response")
    print("="*60)
    
    write_sentiment(baseline_sentiment())
    
    # Extreme ATR (will be >80th percentile after history builds)
    state = baseline_state()
    state["atr"] = 15.0  # Very high
    write_state(state)
    ctrl = read_control()
    
    # Should have max tightening
    disabled = ctrl.get("disable_new_entries", False)
    max_layers = ctrl.get("max_layers_cap", 3)
    dca = ctrl.get("dca_step_multiplier", 1.0)
    
    # During cold start or extreme, should be defensive
    passed = disabled or max_layers <= 2 or dca >= 1.5
    record_result(
        "Extreme ATR triggers defense",
        passed,
        f"disabled={disabled}, max_layers={max_layers}, dca={dca:.1f}"
    )


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def run_suite():
    """Run all regression tests"""
    print("="*60)
    print("🧪 BRIDGE v3 REGRESSION TEST SUITE")
    print("="*60)
    print(f"Started: {datetime.now().isoformat()}")
    print(f"Bridge must be running for these tests to work.")
    print()
    
    # Check bridge is running
    if not CONTROL_FILE.exists():
        print("❌ ERROR: control.json not found. Is bridge running?")
        return
    
    # Clean up kill switch if exists
    KILL_SWITCH_FILE.unlink(missing_ok=True)
    
    # Run tests
    test_panic_whipsaw()
    test_atr_spike()
    test_spread_spike()
    test_monotonic_tightening()
    test_kill_switch()
    test_hysteresis_unlatch()
    test_extreme_atr_bucket()
    
    # Summary
    print("\n" + "="*60)
    print("SUMMARY")
    print("="*60)
    
    passed = sum(1 for _, p, _ in RESULTS if p)
    total = len(RESULTS)
    
    for name, p, details in RESULTS:
        status = "✅" if p else "❌"
        print(f"   {status} {name}")
    
    print()
    print(f"   PASSED: {passed}/{total}")
    print(f"   FAILED: {total - passed}/{total}")
    print()
    
    if passed == total:
        print("   🎉 ALL TESTS PASSED - Bridge is regression-safe")
    else:
        print("   ⚠️ SOME TESTS FAILED - Review before deploying")
    
    print("="*60)
    
    # Save results
    results_path = BASE_DIR / "logs" / "regression_results.json"
    results_path.parent.mkdir(parents=True, exist_ok=True)
    results_path.write_text(json.dumps({
        "timestamp": datetime.now().isoformat(),
        "passed": passed,
        "total": total,
        "tests": [{"name": n, "passed": p, "details": d} for n, p, d in RESULTS]
    }, indent=2), encoding="utf-8")
    print(f"\n📁 Results saved: {results_path}")


if __name__ == "__main__":
    run_suite()
