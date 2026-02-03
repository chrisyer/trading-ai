#!/usr/bin/env python3
"""
Memory Retriever
================
Finds similar past trades for few-shot injection.
Uses explicit, interpretable similarity scoring.
"""

import json
from pathlib import Path
from typing import List, Dict


def score(query: dict, record: dict) -> float:
    """
    Calculate similarity between current state and a past trade.
    Higher = more similar.
    
    Weights tuned for what actually matters in gold trading:
    - Sentiment regime match is most important
    - ATR bucket determines volatility environment
    - Event risk affects timing
    """
    s = 0.0
    
    # High-weight matches (context)
    s += 2.0 if query.get("sentiment_regime") == record.get("sentiment_regime") else 0.0
    s += 1.5 if query.get("event_risk") == record.get("event_risk") else 0.0
    s += 1.5 if query.get("atr_bucket") == record.get("atr_bucket") else 0.0
    s += 0.5 if query.get("dir") == record.get("dir") else 0.0
    
    # Distance penalties (soft matches)
    s -= 0.25 * abs(query.get("intensity_bin", 5) - record.get("intensity_bin", 5))
    s -= 0.15 * abs(query.get("layers", 0) - record.get("layers", 0))
    
    return s


def retrieve_top(query: dict, index: List[dict], k: int = 3) -> List[dict]:
    """
    Find top-K most similar past trades.
    """
    if not index:
        return []
    
    scored = [(score(query, r), r) for r in index]
    scored.sort(key=lambda x: x[0], reverse=True)
    
    return [r for _, r in scored[:k]]


def load_index(idx_path: Path) -> List[dict]:
    """Load index from disk"""
    if not idx_path.exists():
        return []
    try:
        return json.loads(idx_path.read_text(encoding="utf-8", errors="ignore"))
    except:
        return []


def format_memories_for_prompt(memories: List[dict]) -> str:
    """
    Format memories for injection into governor prompt.
    Keep it compact for token efficiency.
    """
    if not memories:
        return "[]"
    
    lines = []
    for r in memories:
        lines.append({
            "sentiment": r.get("sentiment_regime"),
            "risk": r.get("event_risk"),
            "atr": r.get("atr_bucket"),
            "layers": r.get("layers"),
            "dir": r.get("dir"),
            "outcome": {
                "pnl": r.get("pnl"),
                "dd": r.get("dd"),
                "mins": r.get("duration_min")
            },
            "ctrl_used": {
                "dca": r.get("ctrl_dca_mult"),
                "layers": r.get("ctrl_max_layers")
            }
        })
    
    return json.dumps(lines, separators=(",", ":"))


if __name__ == "__main__":
    # Demo
    base = Path(__file__).resolve().parents[1]
    cfg_path = base / "config" / "bridge_config.json"
    
    if cfg_path.exists():
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    else:
        cfg = {"memory_index_file": "memory/index.json"}
    
    idx = load_index(base / cfg["memory_index_file"])
    
    # Example query
    q = {
        "sentiment_regime": "mild_bearish",
        "event_risk": "low",
        "intensity_bin": 6,
        "atr_bucket": "high",
        "layers": 3,
        "dir": -1
    }
    
    top = retrieve_top(q, idx, k=3)
    print("Query:", json.dumps(q, indent=2))
    print("\nTop matches:")
    print(json.dumps(top, indent=2))
