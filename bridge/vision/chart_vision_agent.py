#!/usr/bin/env python3
"""
Chart Vision Agent
==================
Reads chart screenshots from MT5, sends to vision model.
Outputs pattern classification (NOT trade signals).

Run: python chart_vision_agent.py
"""

import json
import time
import subprocess
import base64
from pathlib import Path
from datetime import datetime

# Vision system prompt - NEVER gives trade entries
VISION_SYSTEM = """You are a chart pattern risk classifier for XAUUSD (gold).
You do NOT give trade entries. You identify RISK PATTERNS only.

OUTPUT (JSON only):
{
  "pattern": one of ["none", "parabolic", "exhaustion", "range", "breakout", "stop_run", "accumulation"],
  "reversion_risk": number 0.0-1.0 (likelihood price reverts),
  "trend_continuation_risk": number 0.0-1.0 (likelihood trend continues strongly),
  "note": short observation <= 80 chars
}

PATTERN MEANINGS:
- parabolic: Unsustainable vertical move, high reversion risk
- exhaustion: Climactic volume/move, trend likely ending
- range: Sideways consolidation, low directional risk
- breakout: Breaking key level, could continue or fail
- stop_run: Spike to clear stops, may reverse sharply
- accumulation: Quiet building of position, may precede move
- none: No clear pattern

RULES:
- Focus on what you SEE, not predictions
- High reversion_risk = be defensive
- High trend_continuation_risk = don't fade the move
- Output ONLY valid JSON"""


def encode_image_base64(path: Path) -> str:
    """Encode image as base64 for Ollama"""
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def ollama_vision(model: str, image_path: Path) -> str:
    """
    Query Ollama vision model via HTTP API (supports Qwen3-VL, LLaVA, etc.)
    """
    import requests

    prompt = f"""{VISION_SYSTEM}

Analyze the chart image and return JSON only."""

    # Encode image as base64
    img_b64 = encode_image_base64(image_path)

    try:
        resp = requests.post(
            "http://localhost:11434/api/chat",
            json={
                "model": model,
                "messages": [
                    {
                        "role": "user",
                        "content": prompt,
                        "images": [img_b64]
                    }
                ],
                "stream": False,
                "options": {"temperature": 0.2, "num_predict": 2048},
            },
            timeout=120,
        )

        if resp.status_code != 200:
            raise RuntimeError(f"Ollama {resp.status_code}: {resp.text[:200]}")

        return resp.json().get("message", {}).get("content", "").strip()

    except requests.Timeout:
        raise RuntimeError("Vision model timeout (120s)")
    except requests.ConnectionError:
        raise RuntimeError("Ollama not reachable")


def extract_json(raw: str) -> dict:
    """Extract JSON from model response"""
    start = raw.find("{")
    end = raw.rfind("}")
    
    if start == -1 or end == -1 or end <= start:
        return {
            "pattern": "none",
            "reversion_risk": 0.5,
            "trend_continuation_risk": 0.5,
            "note": "parse_fail"
        }
    
    try:
        return json.loads(raw[start:end+1])
    except:
        return {
            "pattern": "none",
            "reversion_risk": 0.5,
            "trend_continuation_risk": 0.5,
            "note": "json_error"
        }


def validate_vision_output(raw: dict) -> dict:
    """Validate and clamp vision output"""
    valid_patterns = ["none", "parabolic", "exhaustion", "range", "breakout", "stop_run", "accumulation"]
    
    out = {
        "pattern": "none",
        "reversion_risk": 0.5,
        "trend_continuation_risk": 0.5,
        "note": "",
        "timestamp": datetime.now().isoformat()
    }
    
    # Pattern
    pattern = str(raw.get("pattern", "none")).lower().strip()
    if pattern in valid_patterns:
        out["pattern"] = pattern
    
    # Risks (clamp to 0-1)
    try:
        out["reversion_risk"] = max(0.0, min(1.0, float(raw.get("reversion_risk", 0.5))))
    except:
        pass
    
    try:
        out["trend_continuation_risk"] = max(0.0, min(1.0, float(raw.get("trend_continuation_risk", 0.5))))
    except:
        pass
    
    # Note (truncate)
    out["note"] = str(raw.get("note", ""))[:80]
    
    return out


def safe_default() -> dict:
    """Return safe default when vision fails"""
    return {
        "pattern": "none",
        "reversion_risk": 0.5,
        "trend_continuation_risk": 0.5,
        "note": "vision_unavailable",
        "timestamp": datetime.now().isoformat()
    }


def main():
    """Main vision agent loop"""
    base = Path(__file__).resolve().parents[1]
    cfg_path = base / "config" / "bridge_config.json"
    
    if cfg_path.exists():
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    else:
        cfg = {
            "mt5_files_dir": "/home/jbot/.wine/drive_c/Program Files/MetaTrader 5/MQL5/Files",
            "chart_image_file": "aiiq_chart.png",
            "vision_summary_file": "aiiq_vision.json",
            "ollama_model_vision": "llava:7b",
            "screenshot_every_minutes": 15,
            "enable_vision": True
        }
    
    if not cfg.get("enable_vision", False):
        print("Vision disabled in config. Set enable_vision: true to enable.")
        return
    
    files_dir = Path(cfg["mt5_files_dir"])
    img_path = files_dir / cfg["chart_image_file"]
    out_path = files_dir / cfg["vision_summary_file"]
    model = cfg["ollama_model_vision"]
    interval = int(cfg.get("screenshot_every_minutes", 15)) * 60
    
    print("="*60)
    print("👁️ CHART VISION AGENT")
    print("="*60)
    print(f"Model: {model}")
    print(f"Image: {img_path}")
    print(f"Output: {out_path}")
    print(f"Interval: {interval}s")
    print("="*60)
    
    # Write initial safe default
    out_path.write_text(json.dumps(safe_default(), indent=2), encoding="utf-8")
    
    last_mtime = 0
    
    while True:
        try:
            if img_path.exists():
                mtime = img_path.stat().st_mtime
                
                # Only process if image changed
                if mtime != last_mtime:
                    last_mtime = mtime
                    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] New chart image detected")
                    
                    # Query vision model
                    raw_response = ollama_vision(model, img_path)
                    
                    if raw_response:
                        result = extract_json(raw_response)
                        result = validate_vision_output(result)
                    else:
                        result = safe_default()
                    
                    # Write output
                    out_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
                    
                    print(f"  Pattern: {result['pattern']}")
                    print(f"  Reversion risk: {result['reversion_risk']:.2f}")
                    print(f"  Continuation risk: {result['trend_continuation_risk']:.2f}")
                    print(f"  Note: {result['note']}")
            
        except KeyboardInterrupt:
            print("\nStopped")
            break
        except Exception as e:
            print(f"Error: {e}")
            # Write safe default on error
            try:
                out_path.write_text(json.dumps(safe_default(), indent=2), encoding="utf-8")
            except:
                pass
        
        # Check every minute, but only process on new images
        time.sleep(60)


if __name__ == "__main__":
    main()
