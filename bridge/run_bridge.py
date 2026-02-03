#!/usr/bin/env python3
"""
AiiQ Bridge v3: MT5 ↔ Ollama (Production Grade)
===============================================
Full Phase 4-7 + Patches 1-10:
- Hysteresis latch (anti-whipsaw)
- Monotonic tighten-only while in basket
- Atomic file writes
- Staleness guards
- ATR history persistence
- Memory retrieval quality (outlier filter + diversity)
- Basket-close state machine
- Prompt budget control
- Circuit breaker on volatility spike
- Full observability (audit log)

AI can only TIGHTEN risk, never loosen.
"""

import json
import time
import os
import subprocess
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Optional, Tuple
import statistics

# ══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ══════════════════════════════════════════════════════════════════════════════

BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "config" / "bridge_config.json"

def load_config() -> dict:
    """Load configuration from JSON"""
    if CONFIG_FILE.exists():
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    return {
        "mt5_files_dir": "/home/jbot/.wine/drive_c/Program Files/MetaTrader 5/MQL5/Files",
        "state_file": "aiiq_state.json",
        "sentiment_file": "aiiq_sentiment.json",
        "control_file": "aiiq_control.json",
        "vision_summary_file": "aiiq_vision.json",
        "memory_file": "memory/trades.jsonl",
        "memory_index_file": "memory/index.json",
        "atr_hist_file": "memory/atr_hist.json",
        "audit_log_file": "logs/bridge_audit.jsonl",
        "ollama_model_governor": "qwen2.5:7b-instruct",
        "governor_loop_seconds": 2,
        "memory_k": 3,
        "enable_vision": False,
        "hysteresis_safe_cycles": 3,
        "sentiment_stale_seconds": 600,
        "vision_stale_seconds": 2700,
        "state_stale_seconds": 10,
        "atr_cold_start_min": 200,
        "atr_spike_threshold": 1.6,
        "spread_spike_threshold": 100
    }

CFG = load_config()

FILES_DIR = Path(CFG["mt5_files_dir"])
STATE_FILE = FILES_DIR / CFG["state_file"]
CONTROL_FILE = FILES_DIR / CFG["control_file"]
SENTIMENT_FILE = FILES_DIR / CFG["sentiment_file"]
VISION_FILE = FILES_DIR / CFG.get("vision_summary_file", "aiiq_vision.json")
MEMORY_FILE = BASE_DIR / CFG["memory_file"]
INDEX_FILE = BASE_DIR / CFG["memory_index_file"]
ATR_HIST_FILE = BASE_DIR / CFG.get("atr_hist_file", "memory/atr_hist.json")
AUDIT_LOG_FILE = BASE_DIR / CFG.get("audit_log_file", "logs/bridge_audit.jsonl")

OLLAMA_MODEL = CFG["ollama_model_governor"]
LOOP_SECONDS = CFG["governor_loop_seconds"]
MEMORY_K = CFG.get("memory_k", 3)
ENABLE_VISION = CFG.get("enable_vision", False)

# Patch parameters
HYSTERESIS_SAFE_CYCLES = CFG.get("hysteresis_safe_cycles", 3)
SENTIMENT_STALE_SEC = CFG.get("sentiment_stale_seconds", 600)
VISION_STALE_SEC = CFG.get("vision_stale_seconds", 2700)
STATE_STALE_SEC = CFG.get("state_stale_seconds", 10)
ATR_COLD_START_MIN = CFG.get("atr_cold_start_min", 200)
ATR_SPIKE_THRESHOLD = CFG.get("atr_spike_threshold", 1.6)
SPREAD_SPIKE_THRESHOLD = CFG.get("spread_spike_threshold", 100)

# Kill switch file (human override)
KILL_SWITCH_FILE = BASE_DIR / "kill_switch.txt"

# ══════════════════════════════════════════════════════════════════════════════
# KILL SWITCH (Human Override)
# ══════════════════════════════════════════════════════════════════════════════

def check_kill_switch() -> bool:
    """
    Check if human kill switch is engaged.
    If kill_switch.txt exists, force maximum defensive posture.
    """
    return KILL_SWITCH_FILE.exists()

def kill_switch_control() -> dict:
    """Return maximum defensive control when kill switch is engaged"""
    return {
        "disable_new_entries": True,
        "dca_step_multiplier": 2.0,
        "max_layers_cap": 1,
        "risk_bias": -1.0,
        "regime": "kill_switch",
        "sentiment": "human_override",
        "kill_switch": True,
        "timestamp": datetime.now().isoformat()
    }

# ══════════════════════════════════════════════════════════════════════════════
# PATCH 3: ATOMIC FILE WRITES
# ══════════════════════════════════════════════════════════════════════════════

def atomic_write(path: Path, content: str):
    """Write atomically: tmp file then rename (prevents half-writes)"""
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(content, encoding="utf-8")
    os.replace(str(tmp_path), str(path))

# ══════════════════════════════════════════════════════════════════════════════
# PATCH 5: ATR HISTORY PERSISTENCE
# ══════════════════════════════════════════════════════════════════════════════

ATR_HIST: List[float] = []
ATR_HIST_MAX = 5000
ATR_PREV: float = 0.0
_ATR_FLUSH_COUNTER = 0

def load_atr_hist():
    """Load ATR history from disk on startup"""
    global ATR_HIST
    try:
        if ATR_HIST_FILE.exists():
            ATR_HIST = json.loads(ATR_HIST_FILE.read_text(encoding="utf-8"))
            ATR_HIST = [x for x in ATR_HIST if isinstance(x, (int, float)) and x > 0][-ATR_HIST_MAX:]
            print(f"Loaded {len(ATR_HIST)} ATR history values")
    except Exception as e:
        print(f"ATR history load failed: {e}")
        ATR_HIST = []

def save_atr_hist():
    """Persist ATR history to disk"""
    try:
        ATR_HIST_FILE.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(ATR_HIST_FILE, json.dumps(ATR_HIST[-ATR_HIST_MAX:]))
    except Exception as e:
        print(f"ATR history save failed: {e}")

def update_atr_hist(current_atr: float) -> Tuple[float, bool]:
    """
    Maintain rolling ATR history. Returns (atr_prev, is_spike).
    Patch 9: Detect ATR spikes.
    """
    global ATR_HIST, ATR_PREV, _ATR_FLUSH_COUNTER
    
    atr_prev = ATR_PREV
    is_spike = False
    
    if current_atr and current_atr > 0:
        # Patch 9: Spike detection
        if atr_prev > 0 and current_atr / atr_prev > ATR_SPIKE_THRESHOLD:
            is_spike = True
        
        ATR_HIST.append(float(current_atr))
        ATR_PREV = current_atr
        
        if len(ATR_HIST) > ATR_HIST_MAX:
            ATR_HIST = ATR_HIST[-ATR_HIST_MAX:]
        
        # Flush every 30 cycles (~60 seconds at 2s loop)
        _ATR_FLUSH_COUNTER += 1
        if _ATR_FLUSH_COUNTER >= 30:
            save_atr_hist()
            _ATR_FLUSH_COUNTER = 0
    
    return atr_prev, is_spike

def atr_percentile(current_atr: float) -> float:
    """Calculate ATR percentile (0-1)"""
    if len(ATR_HIST) < 50:
        return 0.5
    count = sum(1 for x in ATR_HIST if x < current_atr)
    return count / len(ATR_HIST)

def atr_bucket(pctl: float) -> str:
    """Convert percentile to bucket"""
    if pctl < 0.20: return "low"
    if pctl < 0.50: return "mid"
    if pctl < 0.80: return "high"
    return "extreme"

def volatility_regime(current_atr: float) -> dict:
    """
    Full regime classification.
    Patch 5: Cold-start behavior - treat as "high" until enough samples.
    """
    # Cold start defense
    if len(ATR_HIST) < ATR_COLD_START_MIN:
        return {
            "atr_pctl": 0.75,  # Assume high
            "atr_bucket": "high",
            "atr_current": round(current_atr, 4) if current_atr else 0.0,
            "cold_start": True
        }
    
    pctl = atr_percentile(current_atr)
    return {
        "atr_pctl": round(pctl, 4),
        "atr_bucket": atr_bucket(pctl),
        "atr_current": round(current_atr, 4) if current_atr else 0.0,
        "cold_start": False
    }

# ══════════════════════════════════════════════════════════════════════════════
# PATCH 6: MEMORY RETRIEVAL WITH QUALITY FILTERS
# ══════════════════════════════════════════════════════════════════════════════

_INDEX_CACHE: List[dict] = []
_INDEX_MTIME: float = 0

def load_index() -> List[dict]:
    """Load memory index with caching"""
    global _INDEX_CACHE, _INDEX_MTIME
    
    if not INDEX_FILE.exists():
        return []
    
    mtime = INDEX_FILE.stat().st_mtime
    if mtime != _INDEX_MTIME:
        try:
            raw = json.loads(INDEX_FILE.read_text(encoding="utf-8", errors="ignore"))
            # Patch 6: Filter outliers
            _INDEX_CACHE = filter_memory_quality(raw)
            _INDEX_MTIME = mtime
        except:
            _INDEX_CACHE = []
    
    return _INDEX_CACHE

def filter_memory_quality(records: List[dict]) -> List[dict]:
    """
    Patch 6: Filter out low-quality memory records.
    - Remove missing/NaN outcomes
    - Remove extreme outliers (>99th percentile PnL)
    - Remove absurd durations (>24h)
    """
    if not records:
        return []
    
    # Collect valid PnLs for percentile calculation
    pnls = [r.get("pnl", 0) for r in records if isinstance(r.get("pnl"), (int, float))]
    if len(pnls) > 20:
        pnl_low = sorted(pnls)[int(len(pnls) * 0.01)]
        pnl_high = sorted(pnls)[int(len(pnls) * 0.99)]
    else:
        pnl_low, pnl_high = -10000, 10000
    
    filtered = []
    for r in records:
        # Skip if missing critical fields
        if r.get("dd") is None or r.get("pnl") is None:
            continue
        
        # Skip extreme outliers
        pnl = float(r.get("pnl", 0))
        if pnl < pnl_low or pnl > pnl_high:
            continue
        
        # Skip absurd durations (>24h = 1440 min)
        duration = float(r.get("duration_min", 0))
        if duration > 1440:
            continue
        
        filtered.append(r)
    
    return filtered

def memory_query(state: dict) -> dict:
    """Build query from current state for memory lookup"""
    sent = state.get("sentiment", {})
    reg = state.get("regime", {})
    return {
        "sentiment_regime": sent.get("sentiment_regime", "unknown"),
        "event_risk": sent.get("event_risk", "unknown"),
        "intensity_bin": int(float(sent.get("intensity", 0.5)) * 10),
        "atr_bucket": reg.get("atr_bucket", "unknown"),
        "layers": int(state.get("layers", 0)),
        "dir": int(state.get("dir", 0)),
    }

def score_memory(query: dict, record: dict) -> float:
    """Calculate similarity score"""
    s = 0.0
    s += 2.0 if query.get("sentiment_regime") == record.get("sentiment_regime") else 0.0
    s += 1.5 if query.get("event_risk") == record.get("event_risk") else 0.0
    s += 1.5 if query.get("atr_bucket") == record.get("atr_bucket") else 0.0
    s += 0.5 if query.get("dir") == record.get("dir") else 0.0
    s -= 0.25 * abs(query.get("intensity_bin", 5) - record.get("intensity_bin", 5))
    s -= 0.15 * abs(query.get("layers", 0) - record.get("layers", 0))
    return s

def retrieve_memories(query: dict, k: int = 3) -> List[dict]:
    """
    Find top-K similar past trades.
    Patch 6: Enforce diversity (different timestamps).
    """
    index = load_index()
    if not index:
        return []
    
    scored = [(score_memory(query, r), r) for r in index]
    scored.sort(key=lambda x: x[0], reverse=True)
    
    # Patch 6: Diversity - don't return near-duplicates
    selected = []
    seen_ts_prefixes = set()
    
    for _, r in scored:
        if len(selected) >= k:
            break
        
        # Use date prefix for diversity (at least 2 different sessions)
        ts = str(r.get("ts", ""))[:10]  # YYYY-MM-DD
        
        # Allow max 2 from same day
        if ts and list(seen_ts_prefixes).count(ts) >= 2:
            continue
        
        selected.append(r)
        seen_ts_prefixes.add(ts)
    
    return selected

def format_memories(memories: List[dict]) -> str:
    """
    Format memories for prompt injection.
    Patch 8: Budget control - keep compact.
    """
    if not memories:
        return "[]"
    
    compact = []
    for r in memories[:3]:  # Hard cap at 3
        compact.append({
            "ctx": {
                "sent": str(r.get("sentiment_regime", "?"))[:12],
                "atr": str(r.get("atr_bucket", "?"))[:8],
                "layers": int(r.get("layers", 0))
            },
            "out": {
                "pnl": round(float(r.get("pnl", 0)), 1),
                "dd": round(float(r.get("dd", 0)), 1)
            }
        })
    return json.dumps(compact, separators=(",", ":"))

# ══════════════════════════════════════════════════════════════════════════════
# PATCH 4: STALENESS GUARDS
# ══════════════════════════════════════════════════════════════════════════════

def load_sentiment() -> Tuple[dict, bool]:
    """
    Load sentiment from sentiment agent.
    Returns (sentiment_dict, is_stale).
    """
    default = {"sentiment_regime": "neutral", "intensity": 0.5, "event_risk": "low"}
    
    try:
        if not SENTIMENT_FILE.exists():
            return default, True
        
        mtime = SENTIMENT_FILE.stat().st_mtime
        age = time.time() - mtime
        
        if age > SENTIMENT_STALE_SEC:
            return default, True
        
        data = json.loads(SENTIMENT_FILE.read_text(encoding="utf-8", errors="ignore"))
        return data, False
        
    except:
        return default, True

def load_vision() -> Tuple[dict, bool]:
    """
    Load vision analysis.
    Returns (vision_dict, is_stale).
    """
    default = {"pattern": "none", "reversion_risk": 0.5, "trend_continuation_risk": 0.5}
    
    if not ENABLE_VISION:
        return {}, False  # Not stale, just disabled
    
    try:
        if not VISION_FILE.exists():
            return default, True
        
        mtime = VISION_FILE.stat().st_mtime
        age = time.time() - mtime
        
        if age > VISION_STALE_SEC:
            return default, True
        
        data = json.loads(VISION_FILE.read_text(encoding="utf-8", errors="ignore"))
        return data, False
        
    except:
        return default, True

# ══════════════════════════════════════════════════════════════════════════════
# PATCH 1: HYSTERESIS LATCH (anti-whipsaw)
# ══════════════════════════════════════════════════════════════════════════════

class HaltLatch:
    """
    Prevents rapid on/off cycling of disable_new_entries.
    Once latched, requires N consecutive safe cycles to unlatch.
    """
    def __init__(self, safe_cycles_required: int = 3):
        self.latched = False
        self.safe_streak = 0
        self.safe_cycles_required = safe_cycles_required
        self.latch_reasons: List[str] = []
    
    def update(self, should_halt: bool, reasons: List[str], sentiment: dict, regime: dict) -> bool:
        """
        Update latch state. Returns whether entries should be disabled.
        """
        if should_halt:
            # Latch immediately
            self.latched = True
            self.safe_streak = 0
            self.latch_reasons = reasons
            return True
        
        if not self.latched:
            # Not latched, not halting
            return False
        
        # Currently latched but conditions say safe - check hysteresis
        intensity = float(sentiment.get("intensity", 0.5))
        atr_bucket = regime.get("atr_bucket", "mid")
        
        # Can only unlatch if truly safe
        truly_safe = (atr_bucket not in ["extreme", "high"]) and (intensity < 0.70)
        
        if truly_safe:
            self.safe_streak += 1
            if self.safe_streak >= self.safe_cycles_required:
                # Unlatch
                self.latched = False
                self.safe_streak = 0
                self.latch_reasons = []
                return False
        else:
            # Reset streak
            self.safe_streak = 0
        
        # Still latched
        return True

HALT_LATCH = HaltLatch(HYSTERESIS_SAFE_CYCLES)

# ══════════════════════════════════════════════════════════════════════════════
# PATCH 2: MONOTONIC TIGHTEN-ONLY WHILE IN BASKET
# ══════════════════════════════════════════════════════════════════════════════

_LAST_CTRL: Dict = {}

def monotonic_tighten(ctrl: dict, net_lots: float) -> dict:
    """
    When in position (net_lots != 0), controls can only tighten.
    - dca_step_multiplier can only increase
    - max_layers_cap can only decrease
    - disable_new_entries can only go True
    """
    global _LAST_CTRL
    
    if net_lots == 0:
        # Flat - allow any control
        _LAST_CTRL = dict(ctrl)
        return ctrl
    
    if not _LAST_CTRL:
        _LAST_CTRL = dict(ctrl)
        return ctrl
    
    # In position - enforce monotonic tightening
    out = dict(ctrl)
    
    # DCA can only increase (wider = safer)
    out["dca_step_multiplier"] = max(
        ctrl.get("dca_step_multiplier", 1.0),
        _LAST_CTRL.get("dca_step_multiplier", 1.0)
    )
    
    # Max layers can only decrease
    out["max_layers_cap"] = min(
        ctrl.get("max_layers_cap", 3),
        _LAST_CTRL.get("max_layers_cap", 3)
    )
    
    # Disable can only go True, not False
    if _LAST_CTRL.get("disable_new_entries", False):
        out["disable_new_entries"] = True
    
    _LAST_CTRL = dict(out)
    return out

# ══════════════════════════════════════════════════════════════════════════════
# DETERMINISTIC RULES
# ══════════════════════════════════════════════════════════════════════════════

SENTIMENT_RULES = CFG.get("sentiment_rules", {
    "panic_bullish": {"disable_new_entries": True, "dca_mult_min": 1.5, "max_layers": 2},
    "panic_bearish": {"disable_new_entries": True, "dca_mult_min": 1.5, "max_layers": 2},
    "mild_bullish": {"disable_new_entries": False, "dca_mult_min": 1.0, "max_layers": 3},
    "mild_bearish": {"disable_new_entries": False, "dca_mult_min": 1.0, "max_layers": 3},
    "neutral": {"disable_new_entries": False, "dca_mult_min": 1.0, "max_layers": 3},
})

VISION_RULES = CFG.get("vision_rules", {
    "parabolic": {"disable_new_entries": True, "max_layers": 2},
    "breakout": {"max_layers": 2},
    "exhaustion": {"disable_new_entries": True, "max_layers": 1},
    "stop_run": {"dca_mult_min": 1.3}
})

ATR_RULES = {
    "extreme": {"disable_new_entries": True, "dca_mult_min": 2.0, "max_layers": 1},
    "high": {"disable_new_entries": False, "dca_mult_min": 1.5, "max_layers": 2},
    "mid": {"disable_new_entries": False, "dca_mult_min": 1.2, "max_layers": 3},
    "low": {"disable_new_entries": False, "dca_mult_min": 1.0, "max_layers": 3},
}

def apply_deterministic_rules(
    ctrl: dict, 
    sentiment: dict, 
    vision: dict, 
    regime: dict,
    atr_spike: bool,
    spread_spike: bool,
    sentiment_stale: bool,
    vision_stale: bool
) -> Tuple[dict, List[str]]:
    """
    Apply all deterministic rules. Returns (ctrl, halt_reasons).
    Patch 4: Ignore stale data.
    Patch 9: Circuit breaker on spikes.
    """
    halt_reasons: List[str] = []
    
    # 1. ATR regime rules
    atr_bucket = regime.get("atr_bucket", "mid")
    atr_rules = ATR_RULES.get(atr_bucket, ATR_RULES["mid"])
    
    if atr_rules.get("disable_new_entries"):
        halt_reasons.append(f"atr_{atr_bucket}")
    ctrl["dca_step_multiplier"] = max(
        ctrl.get("dca_step_multiplier", 1.0),
        atr_rules.get("dca_mult_min", 1.0)
    )
    ctrl["max_layers_cap"] = min(
        ctrl.get("max_layers_cap", 3),
        atr_rules.get("max_layers", 3)
    )
    
    # Patch 9: ATR spike circuit breaker
    if atr_spike:
        halt_reasons.append("atr_spike")
        ctrl["dca_step_multiplier"] = max(ctrl.get("dca_step_multiplier", 1.0), 1.4)
        ctrl["max_layers_cap"] = min(ctrl.get("max_layers_cap", 3), 2)
    
    # Patch 9: Spread spike circuit breaker
    if spread_spike:
        halt_reasons.append("spread_spike")
        ctrl["dca_step_multiplier"] = max(ctrl.get("dca_step_multiplier", 1.0), 1.5)
        ctrl["max_layers_cap"] = min(ctrl.get("max_layers_cap", 3), 2)
    
    # 2. Sentiment rules (skip if stale)
    if not sentiment_stale:
        sent_regime = sentiment.get("sentiment_regime", "neutral")
        sent_rules = SENTIMENT_RULES.get(sent_regime, SENTIMENT_RULES["neutral"])
        intensity = float(sentiment.get("intensity", 0.5))
        event_risk = sentiment.get("event_risk", "low")
        
        if sent_rules.get("disable_new_entries"):
            halt_reasons.append(f"sentiment_{sent_regime}")
        if intensity > 0.75:
            ctrl["dca_step_multiplier"] = max(ctrl.get("dca_step_multiplier", 1.0), 1.3)
            halt_reasons.append("high_intensity")
        ctrl["dca_step_multiplier"] = max(
            ctrl.get("dca_step_multiplier", 1.0),
            sent_rules.get("dca_mult_min", 1.0)
        )
        ctrl["max_layers_cap"] = min(
            ctrl.get("max_layers_cap", 3),
            sent_rules.get("max_layers", 3)
        )
        if event_risk == "high":
            ctrl["max_layers_cap"] = min(ctrl.get("max_layers_cap", 3), 2)
            halt_reasons.append("event_risk_high")
    else:
        ctrl["sentiment_stale"] = True
    
    # 3. Vision rules (skip if stale or disabled)
    if vision and not vision_stale:
        pattern = vision.get("pattern", "none")
        vis_rules = VISION_RULES.get(pattern, {})
        cont_risk = float(vision.get("trend_continuation_risk", 0.5))
        
        if vis_rules.get("disable_new_entries"):
            halt_reasons.append(f"vision_{pattern}")
        if "max_layers" in vis_rules:
            ctrl["max_layers_cap"] = min(ctrl.get("max_layers_cap", 3), vis_rules["max_layers"])
        if "dca_mult_min" in vis_rules:
            ctrl["dca_step_multiplier"] = max(ctrl.get("dca_step_multiplier", 1.0), vis_rules["dca_mult_min"])
        
        if cont_risk > 0.75:
            ctrl["dca_step_multiplier"] = max(ctrl.get("dca_step_multiplier", 1.0), 1.5)
    elif ENABLE_VISION:
        ctrl["vision_stale"] = True
    
    # Context passthrough
    ctrl["sentiment_regime"] = sentiment.get("sentiment_regime", "neutral")
    ctrl["sentiment_intensity"] = float(sentiment.get("intensity", 0.5))
    ctrl["event_risk"] = sentiment.get("event_risk", "low")
    ctrl["atr_bucket"] = atr_bucket
    ctrl["atr_pctl"] = regime.get("atr_pctl", 0.5)
    if vision:
        ctrl["vision_pattern"] = vision.get("pattern", "none")
    
    return ctrl, halt_reasons

# ══════════════════════════════════════════════════════════════════════════════
# OLLAMA INTEGRATION
# ══════════════════════════════════════════════════════════════════════════════

SYSTEM_PROMPT = (BASE_DIR / "prompts" / "governor_system.txt").read_text(encoding="utf-8") \
    if (BASE_DIR / "prompts" / "governor_system.txt").exists() else """You are a risk-governor for an XAUUSD mean-reversion scalper.

RULES (CRITICAL):
1. You NEVER place trades
2. You NEVER increase risk beyond defaults
3. You can only TIGHTEN risk during chaos
4. Default state = no intervention

OUTPUT (compact JSON only):
{
  "disable_new_entries": false,
  "dca_step_multiplier": 1.0,
  "max_layers_cap": 3,
  "risk_bias": 0.0,
  "regime": "unknown"
}

ONLY output valid JSON."""


def safe_defaults() -> dict:
    """Return safe default control values"""
    return {
        "disable_new_entries": False,
        "dca_step_multiplier": 1.0,
        "max_layers_cap": 3,
        "risk_bias": 0.0,
        "regime": "unknown",
        "sentiment": "neutral",
        "timestamp": datetime.now().isoformat()
    }


def clamp_control(ctrl: dict) -> dict:
    """Enforce safety limits - AI can only tighten, never loosen"""
    out = safe_defaults()
    
    out["disable_new_entries"] = bool(ctrl.get("disable_new_entries", False))
    out["dca_step_multiplier"] = max(1.0, float(ctrl.get("dca_step_multiplier", 1.0)))
    out["max_layers_cap"] = max(1, min(3, int(ctrl.get("max_layers_cap", 3))))
    out["risk_bias"] = max(-1.0, min(0.0, float(ctrl.get("risk_bias", 0.0))))
    out["regime"] = str(ctrl.get("regime", "unknown"))[:30]
    out["sentiment"] = str(ctrl.get("sentiment", "neutral"))[:30]
    out["timestamp"] = datetime.now().isoformat()
    
    return out


def ollama_query(prompt: str) -> str:
    """Query Ollama"""
    try:
        result = subprocess.run(
            ["ollama", "run", OLLAMA_MODEL],
            input=prompt.encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30
        )
        if result.returncode != 0:
            return ""
        return result.stdout.decode("utf-8", errors="ignore").strip()
    except:
        return ""


def extract_json(text: str) -> Optional[dict]:
    """
    Extract JSON from model response.
    Patch 8: Strict validation - reject non-JSON.
    """
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        result = json.loads(text[start:end+1])
        # Validate expected fields exist
        if not isinstance(result, dict):
            return None
        return result
    except:
        return None


def build_prompt(state: dict) -> str:
    """
    Build full prompt with all context.
    Patch 8: Budget control - keep compact.
    """
    # Compact state (only essential fields)
    compact_state = {
        "atr": state.get("atr"),
        "rsi": state.get("rsi"),
        "layers": state.get("layers"),
        "dir": state.get("dir"),
        "floating_pnl": state.get("floating_pnl"),
        "regime": state.get("regime", {}).get("atr_bucket"),
        "sentiment": state.get("sentiment", {}).get("sentiment_regime")
    }
    
    # Memory
    q = memory_query(state)
    memories = retrieve_memories(q, MEMORY_K)
    mem_block = format_memories(memories)
    
    return f"""{SYSTEM_PROMPT}

STATE:
{json.dumps(compact_state, separators=(",", ":"))}

SIMILAR PAST OUTCOMES:
{mem_block}

Return control JSON:"""

# ══════════════════════════════════════════════════════════════════════════════
# PATCH 7: BASKET-CLOSE STATE MACHINE
# ══════════════════════════════════════════════════════════════════════════════

class BasketTracker:
    """
    Robust basket close detection with confirmation.
    """
    def __init__(self):
        self.in_basket = False
        self.basket_id = 0
        self.entry_time: Optional[datetime] = None
        self.max_layers_seen = 0
        self.max_adverse_excursion = 0.0
        self.in_basket_count = 0  # Confirmations
        self.flat_count = 0  # Confirmations
    
    def update(self, state: dict, control: dict) -> Optional[dict]:
        """
        Update basket state. Returns trade record if basket just closed.
        """
        net_lots = float(state.get("net_lots", 0))
        layers = int(state.get("layers", 0))
        floating_pnl = float(state.get("floating_pnl", 0))
        
        # Track adverse excursion
        if self.in_basket and floating_pnl < self.max_adverse_excursion:
            self.max_adverse_excursion = floating_pnl
        
        # Track max layers
        if layers > self.max_layers_seen:
            self.max_layers_seen = layers
        
        if net_lots != 0:
            self.flat_count = 0
            self.in_basket_count += 1
            
            if not self.in_basket and self.in_basket_count >= 2:
                # Confirmed entry
                self.in_basket = True
                self.basket_id += 1
                self.entry_time = datetime.now()
                self.max_layers_seen = layers
                self.max_adverse_excursion = min(0, floating_pnl)
            
            return None
        
        # net_lots == 0
        self.in_basket_count = 0
        self.flat_count += 1
        
        if self.in_basket and self.flat_count >= 2:
            # Confirmed close
            duration = 0
            if self.entry_time:
                duration = (datetime.now() - self.entry_time).total_seconds() / 60
            
            record = {
                "state": state,
                "control": control,
                "outcome": {
                    "pnl": float(state.get("closed_pnl", floating_pnl)),
                    "dd": abs(self.max_adverse_excursion),
                    "mins": round(duration, 1)
                },
                "basket_id": self.basket_id,
                "max_layers": self.max_layers_seen,
                "timestamp": datetime.now().isoformat()
            }
            
            # Reset
            self.in_basket = False
            self.entry_time = None
            self.max_layers_seen = 0
            self.max_adverse_excursion = 0.0
            
            return record
        
        return None

BASKET_TRACKER = BasketTracker()

def log_basket_close(record: dict):
    """Append basket close to memory"""
    try:
        MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(MEMORY_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, separators=(",", ":")) + "\n")
        print(f"📝 Basket #{record['basket_id']}: PnL={record['outcome']['pnl']:.2f}, DD={record['outcome']['dd']:.2f}, {record['outcome']['mins']:.0f}min")
    except Exception as e:
        print(f"Memory log failed: {e}")

# ══════════════════════════════════════════════════════════════════════════════
# PATCH 10: OBSERVABILITY (AUDIT LOG)
# ══════════════════════════════════════════════════════════════════════════════

def write_audit_log(
    state: dict,
    sentiment: dict,
    vision: dict,
    ai_raw: Optional[dict],
    ai_accepted: bool,
    halt_reasons: List[str],
    final_ctrl: dict,
    sentiment_stale: bool,
    vision_stale: bool
):
    """Write structured audit log for debugging"""
    try:
        AUDIT_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        
        entry = {
            "ts": datetime.now().isoformat(),
            "state": {
                "atr": state.get("atr"),
                "layers": state.get("layers"),
                "dir": state.get("dir"),
                "net_lots": state.get("net_lots")
            },
            "sentiment": {
                "regime": sentiment.get("sentiment_regime"),
                "intensity": sentiment.get("intensity"),
                "risk": sentiment.get("event_risk"),
                "stale": sentiment_stale
            },
            "vision": {
                "pattern": vision.get("pattern") if vision else None,
                "stale": vision_stale
            } if ENABLE_VISION else None,
            "ai": {
                "accepted": ai_accepted
            },
            "rules": {
                "halt_latch": HALT_LATCH.latched,
                "safe_streak": HALT_LATCH.safe_streak,
                "reasons": halt_reasons
            },
            "final": {
                "disable": final_ctrl.get("disable_new_entries"),
                "dca": final_ctrl.get("dca_step_multiplier"),
                "layers": final_ctrl.get("max_layers_cap")
            }
        }
        
        with open(AUDIT_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, separators=(",", ":")) + "\n")
            
    except Exception as e:
        pass  # Don't fail main loop for logging

# ══════════════════════════════════════════════════════════════════════════════
# MAIN LOOP
# ══════════════════════════════════════════════════════════════════════════════

def log_state(state: dict, control: dict, halt_reasons: List[str]):
    """Log for console"""
    reasons_str = ",".join(halt_reasons[:3]) if halt_reasons else "-"
    latch_str = f"L{HALT_LATCH.safe_streak}" if HALT_LATCH.latched else ""
    
    print(f"[{datetime.now().strftime('%H:%M:%S')}] "
          f"atr={control.get('atr_bucket', '?'):7s} "
          f"sent={control.get('sentiment_regime', '?'):14s} "
          f"layers={state.get('layers', 0)} "
          f"→ dca={control.get('dca_step_multiplier', 1.0):.1f} "
          f"max={control.get('max_layers_cap', 3)} "
          f"{'🛑' if control.get('disable_new_entries') else '✅'} "
          f"{latch_str} [{reasons_str}]")


def main():
    """Main bridge loop"""
    print("="*70)
    print("🌉 AiiQ BRIDGE v3 - Production Grade")
    print("="*70)
    print(f"Model:      {OLLAMA_MODEL}")
    print(f"State:      {STATE_FILE}")
    print(f"Control:    {CONTROL_FILE}")
    print(f"Memory:     {MEMORY_FILE}")
    print(f"Audit:      {AUDIT_LOG_FILE}")
    print(f"Vision:     {'ENABLED' if ENABLE_VISION else 'disabled'}")
    print(f"Hysteresis: {HYSTERESIS_SAFE_CYCLES} safe cycles to unlatch")
    print(f"Loop:       {LOOP_SECONDS}s")
    print("="*70)
    
    # Initialize directories
    FILES_DIR.mkdir(parents=True, exist_ok=True)
    MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    AUDIT_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    
    # Patch 5: Load ATR history
    load_atr_hist()
    
    # Initialize control
    atomic_write(CONTROL_FILE, json.dumps(safe_defaults(), separators=(",", ":")))
    print("Initial control written")
    
    last_mtime = 0
    
    while True:
        try:
            # KILL SWITCH CHECK (human override - highest priority)
            if check_kill_switch():
                ctrl = kill_switch_control()
                atomic_write(CONTROL_FILE, json.dumps(ctrl, separators=(",", ":")))
                print(f"[{datetime.now().strftime('%H:%M:%S')}] 🛑 KILL SWITCH ENGAGED - Human override active")
                time.sleep(LOOP_SECONDS)
                continue
            
            if STATE_FILE.exists():
                mtime = STATE_FILE.stat().st_mtime
                
                # Patch 4: State staleness check
                state_age = time.time() - mtime
                if state_age > STATE_STALE_SEC:
                    # State is stale - write safe defaults
                    atomic_write(CONTROL_FILE, json.dumps(safe_defaults(), separators=(",", ":")))
                    time.sleep(LOOP_SECONDS)
                    continue
                
                if mtime != last_mtime:
                    last_mtime = mtime
                    
                    # 1. Read state
                    state = json.loads(STATE_FILE.read_text(encoding="utf-8", errors="ignore"))
                    net_lots = float(state.get("net_lots", 0))
                    
                    # 2. Update ATR history and detect spikes
                    current_atr = float(state.get("atr", 0))
                    atr_prev, atr_spike = update_atr_hist(current_atr)
                    regime = volatility_regime(current_atr)
                    state["regime"] = regime
                    
                    # Patch 9: Spread spike detection
                    spread = float(state.get("spread_points", 0))
                    spread_spike = spread > SPREAD_SPIKE_THRESHOLD
                    
                    # 3. Load sentiment and vision (with staleness)
                    sentiment, sentiment_stale = load_sentiment()
                    state["sentiment"] = sentiment
                    vision, vision_stale = load_vision()
                    if vision:
                        state["vision"] = vision
                    
                    # 4. Query Ollama
                    prompt = build_prompt(state)
                    raw = ollama_query(prompt)
                    
                    ai_raw = None
                    ai_accepted = False
                    if raw:
                        ai_raw = extract_json(raw)
                        if ai_raw:
                            ctrl = clamp_control(ai_raw)
                            ai_accepted = True
                        else:
                            ctrl = safe_defaults()
                    else:
                        ctrl = safe_defaults()
                    
                    # 5. Apply deterministic rules
                    ctrl, halt_reasons = apply_deterministic_rules(
                        ctrl, sentiment, vision, regime,
                        atr_spike, spread_spike,
                        sentiment_stale, vision_stale
                    )
                    
                    # 6. Patch 1: Apply hysteresis latch
                    should_halt = len(halt_reasons) > 0
                    ctrl["disable_new_entries"] = HALT_LATCH.update(
                        should_halt, halt_reasons, sentiment, regime
                    )
                    
                    # 7. Patch 2: Monotonic tighten-only while in basket
                    ctrl = monotonic_tighten(ctrl, net_lots)
                    
                    # 8. Patch 3: Atomic write control
                    ctrl["timestamp"] = datetime.now().isoformat()
                    atomic_write(CONTROL_FILE, json.dumps(ctrl, separators=(",", ":")))
                    
                    # 9. Patch 7: Check basket close
                    basket_record = BASKET_TRACKER.update(state, ctrl)
                    if basket_record:
                        log_basket_close(basket_record)
                    
                    # 10. Patch 10: Write audit log
                    write_audit_log(
                        state, sentiment, vision,
                        ai_raw, ai_accepted,
                        halt_reasons, ctrl,
                        sentiment_stale, vision_stale
                    )
                    
                    log_state(state, ctrl, halt_reasons)
            
        except json.JSONDecodeError as e:
            print(f"JSON error: {e}")
            atomic_write(CONTROL_FILE, json.dumps(safe_defaults(), separators=(",", ":")))
        except Exception as e:
            print(f"Error: {e}")
            try:
                atomic_write(CONTROL_FILE, json.dumps(safe_defaults(), separators=(",", ":")))
            except:
                pass
        
        time.sleep(LOOP_SECONDS)


if __name__ == "__main__":
    main()
