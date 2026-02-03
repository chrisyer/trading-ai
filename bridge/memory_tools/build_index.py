#!/usr/bin/env python3
"""
Memory Index Builder
====================
Builds a lightweight, searchable index from trades.jsonl
for few-shot experience replay.

Run occasionally: python build_index.py
"""

import json
from pathlib import Path


def load_jsonl(path: Path) -> list:
    """Load JSONL file, handling errors gracefully"""
    if not path.exists():
        return []
    
    rows = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return rows


def keyify(rec: dict) -> dict:
    """
    Extract compact feature vector from a trade record.
    This is what we use for similarity matching.
    """
    s = rec.get("state", {})
    sent = s.get("sentiment", {})
    reg = s.get("regime", {})
    out = rec.get("outcome", {})
    ctrl = rec.get("control", {})
    
    return {
        # Context features (for similarity)
        "sentiment_regime": sent.get("sentiment_regime", "unknown"),
        "event_risk": sent.get("event_risk", "unknown"),
        "intensity_bin": int(float(sent.get("intensity", 0)) * 10),  # 0-10
        "atr_bucket": reg.get("atr_bucket", "unknown"),
        "layers": int(s.get("layers", 0)),
        "dir": int(s.get("dir", 0)),  # 1=long, -1=short, 0=flat
        
        # Outcome (for learning)
        "pnl": float(out.get("pnl", 0.0)),
        "dd": float(out.get("dd", 0.0)),  # max drawdown during basket
        "duration_min": float(out.get("mins", 0.0)),
        
        # What control was active (for reasoning)
        "ctrl_dca_mult": float(ctrl.get("dca_step_multiplier", 1.0)),
        "ctrl_max_layers": int(ctrl.get("max_layers_cap", 3)),
        "ctrl_disabled": bool(ctrl.get("disable_new_entries", False)),
        
        # Timestamp for recency
        "ts": s.get("timestamp", rec.get("timestamp", ""))
    }


def main():
    """Build index from trades.jsonl"""
    base = Path(__file__).resolve().parents[1]
    cfg_path = base / "config" / "bridge_config.json"
    
    if cfg_path.exists():
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    else:
        cfg = {
            "memory_file": "memory/trades.jsonl",
            "memory_index_file": "memory/index.json"
        }
    
    mem_path = base / cfg["memory_file"]
    idx_path = base / cfg["memory_index_file"]
    
    print(f"Loading trades from: {mem_path}")
    rows = load_jsonl(mem_path)
    
    # Index last 50K records max (keeps file manageable)
    idx = [keyify(r) for r in rows[-50000:]]
    
    # Ensure output directory exists
    idx_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Write index
    idx_path.write_text(json.dumps(idx, ensure_ascii=False), encoding="utf-8")
    print(f"Indexed {len(idx)} records -> {idx_path}")


if __name__ == "__main__":
    main()
