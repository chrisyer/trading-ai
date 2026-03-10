#!/usr/bin/env python3
"""
SIGNAL WRITER V2 - WITH REAL MARKET DATA
==========================================
Fetches REAL market data and generates signals for Crella's bots.
NO FAKE DATA. Uses multiple sources with fallbacks.
"""

import json
import time
import os
import subprocess
from pathlib import Path
from datetime import datetime, timedelta

# ══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ══════════════════════════════════════════════════════════════════════════════

OUTPUT_DIR = Path("/home/jbot/trading_ai/crella_signals")
EA_SIGNAL_FILE = OUTPUT_DIR / "ea_signal.json"
DEFCON_FILE = OUTPUT_DIR / "defcon_state.json"
MARKET_DATA_FILE = OUTPUT_DIR / "market_data.json"
MACRO_INTEL_FILE = OUTPUT_DIR / "macro_intel.json"

SENTIMENT_FILE = Path("/home/jbot/.wine/drive_c/Program Files/MetaTrader 5/MQL5/Files/aiiq_sentiment.json")

# NEO's existing signals (reuse, don't regenerate)
NEO_PATTERN_ALERTS = Path("/home/jbot/trading_ai/neo/signals/pattern_alerts.json")
NEO_DEFCON_STATUS = Path("/home/jbot/trading_ai/neo/signals/defcon_status.json")

UPDATE_SECONDS = 30
OLLAMA_MODEL = "llama3.1:8b"

# ══════════════════════════════════════════════════════════════════════════════
# REAL DATA FETCHING
# ══════════════════════════════════════════════════════════════════════════════

def fetch_real_gold_price():
    """Fetch REAL gold price from multiple sources"""
    import yfinance as yf
    
    # Primary: GLD ETF (most reliable) - multiply by ~10.5 for approx spot
    try:
        gld = yf.Ticker('GLD')
        price = gld.fast_info.get('lastPrice') or gld.fast_info.get('regularMarketPrice')
        if price and price > 100:
            # GLD tracks ~1/10th of gold price
            spot_approx = price * 10.5
            return {
                "source": "GLD_ETF",
                "gld_price": round(price, 2),
                "spot_approx": round(spot_approx, 2),
                "timestamp": datetime.now().isoformat()
            }
    except:
        pass
    
    # Fallback: GC=F futures
    try:
        gc = yf.Ticker('GC=F')
        hist = gc.history(period='1d')
        if not hist.empty:
            price = hist['Close'].iloc[-1]
            return {
                "source": "GC_FUTURES",
                "price": round(price, 2),
                "spot_approx": round(price, 2),
                "timestamp": datetime.now().isoformat()
            }
    except:
        pass
    
    return None


def fetch_real_spy_price():
    """Fetch REAL SPY price"""
    import yfinance as yf
    
    try:
        spy = yf.Ticker('SPY')
        price = spy.fast_info.get('lastPrice') or spy.fast_info.get('regularMarketPrice')
        if price:
            return {
                "source": "SPY",
                "price": round(price, 2),
                "timestamp": datetime.now().isoformat()
            }
    except:
        pass
    
    return None


def fetch_vix():
    """Fetch VIX for volatility assessment"""
    import yfinance as yf
    
    try:
        vix = yf.Ticker('^VIX')
        price = vix.fast_info.get('lastPrice') or vix.fast_info.get('regularMarketPrice')
        if price:
            return round(price, 2)
    except:
        pass
    
    return None


# ══════════════════════════════════════════════════════════════════════════════
# OLLAMA ANALYSIS
# ══════════════════════════════════════════════════════════════════════════════

def ollama_analyze(market_data: dict) -> dict:
    """Ask Ollama for market analysis based on REAL data via HTTP API"""
    import requests as _req

    prompt = f"""You are a risk analyst for gold trading. Based on this REAL market data:

Gold (GLD ETF): ${market_data.get('gold', {}).get('gld_price', 'N/A')}
Gold Spot Approx: ${market_data.get('gold', {}).get('spot_approx', 'N/A')}
SPY: ${market_data.get('spy', {}).get('price', 'N/A')}
VIX: {market_data.get('vix', 'N/A')}
Time: {datetime.now().strftime('%Y-%m-%d %H:%M')} UTC

Output ONLY valid JSON with these fields:
{{
  "sentiment": "bullish" | "bearish" | "neutral",
  "intensity": 0.0-1.0,
  "risk_level": "low" | "medium" | "high",
  "reasoning": "one sentence explanation"
}}

Analyze based on:
- VIX > 25 = high fear, gold bullish
- VIX < 15 = complacency, neutral
- Gold rising with SPY falling = flight to safety
- Weekend/off-hours = lower liquidity risk
"""

    # Primary: Ollama HTTP API (fast, no cold-start penalty)
    try:
        resp = _req.post(
            "http://localhost:11434/api/generate",
            json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False},
            timeout=30,
        )
        resp.raise_for_status()
        response = resp.json().get("response", "")

        start = response.find('{')
        end = response.rfind('}') + 1
        if start >= 0 and end > start:
            return json.loads(response[start:end])
    except Exception as e:
        print(f"  Ollama HTTP API error: {e}")

    # Fallback: subprocess CLI
    try:
        result = subprocess.run(
            ["ollama", "run", OLLAMA_MODEL, prompt],
            capture_output=True,
            text=True,
            timeout=60,
        )
        response = result.stdout.strip()
        start = response.find('{')
        end = response.rfind('}') + 1
        if start >= 0 and end > start:
            return json.loads(response[start:end])
    except Exception as e:
        print(f"  Ollama CLI fallback error: {e}")

    # Default safe response
    return {
        "sentiment": "neutral",
        "intensity": 0.3,
        "risk_level": "medium",
        "reasoning": "Unable to analyze - defaulting to neutral"
    }


# ══════════════════════════════════════════════════════════════════════════════
# SIGNAL GENERATION
# ══════════════════════════════════════════════════════════════════════════════

def load_sentiment() -> dict:
    """Load sentiment from file if exists"""
    try:
        if SENTIMENT_FILE.exists():
            return json.loads(SENTIMENT_FILE.read_text(encoding="utf-8"))
    except:
        pass
    return {"sentiment_regime": "neutral", "intensity": 0.3, "event_risk": "low"}


def load_neo_signals() -> dict:
    """Load NEO's existing pattern alerts and DEFCON status (reuse, don't regenerate)"""
    neo_data = {
        "patterns": [],
        "defcon": 2,
        "defcon_rules": {},
        "latest_pattern": None,
        "bullish_count": 0,
        "bearish_count": 0
    }
    
    # Load pattern alerts
    try:
        if NEO_PATTERN_ALERTS.exists():
            data = json.loads(NEO_PATTERN_ALERTS.read_text(encoding="utf-8"))
            alerts = data.get("alerts", [])
            neo_data["patterns"] = alerts[:10]  # Keep latest 10
            
            # Count bullish vs bearish
            for a in alerts:
                if a.get("direction") == "BULLISH":
                    neo_data["bullish_count"] += 1
                elif a.get("direction") == "BEARISH":
                    neo_data["bearish_count"] += 1
            
            if alerts:
                neo_data["latest_pattern"] = alerts[0]
    except:
        pass
    
    # Load DEFCON status
    try:
        if NEO_DEFCON_STATUS.exists():
            data = json.loads(NEO_DEFCON_STATUS.read_text(encoding="utf-8"))
            neo_data["defcon"] = data.get("defcon", 2)
            neo_data["defcon_rules"] = data.get("rules", {})
    except:
        pass
    
    return neo_data


def calculate_defcon(analysis: dict, vix: float, neo_data: dict) -> int:
    """Calculate DEFCON level from analysis + NEO's existing signals"""
    
    # Start with NEO's DEFCON (reuse existing intelligence)
    neo_defcon = neo_data.get("defcon", 2)
    
    risk = analysis.get("risk_level", "medium")
    intensity = float(analysis.get("intensity", 0.5))
    
    # VIX-based override (takes priority)
    if vix and vix > 30:
        return max(neo_defcon, 4)  # High fear
    if vix and vix > 25:
        return max(neo_defcon, 3)  # Elevated
    
    # NEO pattern bias
    bullish = neo_data.get("bullish_count", 0)
    bearish = neo_data.get("bearish_count", 0)
    if bearish > bullish + 5:
        # Strong bearish bias from patterns
        return max(neo_defcon, 3)
    
    # Analysis-based
    if risk == "high" or intensity > 0.8:
        return max(neo_defcon, 4)
    if risk == "medium" or intensity > 0.6:
        return max(neo_defcon, 3)
    if intensity > 0.4:
        return max(neo_defcon, 2)
    
    # Use NEO's DEFCON as baseline
    return neo_defcon


def _load_macro_intel() -> dict:
    """Load latest macro intel from the Macro Intelligence Feed (port 5001)."""
    try:
        if MACRO_INTEL_FILE.exists():
            data = json.loads(MACRO_INTEL_FILE.read_text(encoding="utf-8"))
            ts = data.get("timestamp", "")
            if ts:
                from datetime import timezone
                age = (datetime.now(timezone.utc) - datetime.fromisoformat(ts)).total_seconds()
                if age > 180:
                    return {"status": "stale", "age_seconds": round(age)}
            comp = data.get("composite_score", {})
            sweep = data.get("liquidity_sweep_risk", {})
            dxy = data.get("dxy", {})
            geo = data.get("geopolitical", {})
            return {
                "status": "live",
                "dxy_price": dxy.get("price"),
                "dxy_momentum": dxy.get("momentum"),
                "dxy_gold_implication": dxy.get("gold_implication"),
                "oil_momentum": data.get("oil_wti", {}).get("momentum"),
                "vix_level": data.get("vix", {}).get("level"),
                "us10y_yield": data.get("us10y", {}).get("yield_pct"),
                "liquidity_sweep_risk": sweep.get("level"),
                "liquidity_sweep_score": sweep.get("score"),
                "geo_risk": geo.get("risk_level"),
                "geo_implication": geo.get("gold_implication"),
                "composite_gold_bias": comp.get("gold_bias"),
                "composite_conviction": comp.get("conviction"),
                "recommended_stance": comp.get("recommended_stance"),
                "max_lot_multiplier": comp.get("max_lot_multiplier"),
            }
    except Exception:
        pass
    return {"status": "unavailable"}


def _build_ea_instructions(defcon: int, lot_mult: float) -> dict:
    """Build EA instructions with macro sweep override."""
    macro = _load_macro_intel()
    sweep = macro.get("liquidity_sweep_risk", "LOW")
    macro_mult = macro.get("max_lot_multiplier")

    effective_mult = lot_mult
    consider_hedge = defcon >= 4

    if sweep == "CRITICAL":
        effective_mult = min(lot_mult, 0.3)
        consider_hedge = True
    elif sweep == "HIGH":
        effective_mult = min(lot_mult, 0.5)
        consider_hedge = True

    if macro_mult is not None and macro.get("status") == "live":
        effective_mult = min(effective_mult, macro_mult)

    return {
        "pause_longs": defcon >= 5,
        "pause_shorts": defcon >= 5,
        "reduce_lot_multiplier": round(effective_mult, 2),
        "tighten_sl_pips": 0,
        "max_drawdown_override": 0,
        "close_partial": 0,
        "set_breakeven": False,
        "consider_hedge": consider_hedge,
        "sweep_override_active": sweep in ("CRITICAL", "HIGH"),
    }


def create_signal(market_data: dict, analysis: dict, defcon: int, neo_data: dict) -> dict:
    """Create the signal JSON with NEO's existing intelligence"""
    
    defcon_colors = {1: "GREEN", 2: "YELLOW", 3: "ORANGE", 4: "RED", 5: "BLACK"}
    
    # Lot multiplier - use NEO's rules if available
    neo_rules = neo_data.get("defcon_rules", {})
    lot_mult = neo_rules.get("position_size_mult", {1: 1.0, 2: 0.8, 3: 0.6, 4: 0.4, 5: 0.0}.get(defcon, 0.5))
    
    # Direction hint from NEO patterns + analysis
    sentiment = analysis.get("sentiment", "neutral")
    bullish = neo_data.get("bullish_count", 0)
    bearish = neo_data.get("bearish_count", 0)
    
    direction = "HOLD"
    if bullish > bearish + 3 or sentiment == "bullish":
        direction = "BULLISH_BIAS"
    elif bearish > bullish + 3 or sentiment == "bearish":
        direction = "BEARISH_BIAS"
    
    # Get latest pattern from NEO
    latest_pattern = neo_data.get("latest_pattern", {})
    
    return {
        "timestamp": datetime.now().isoformat(),
        "symbol": "XAUUSD",
        "direction": direction,
        "conviction": int((1 - (defcon - 1) / 4) * 100),
        "defcon": defcon,
        "defcon_color": defcon_colors.get(defcon, "ORANGE"),
        "action": f"DEFCON {defcon} - {defcon_colors.get(defcon, 'UNKNOWN')}",
        
        "market_data": {
            "gold_gld": market_data.get("gold", {}).get("gld_price"),
            "gold_spot_approx": market_data.get("gold", {}).get("spot_approx"),
            "spy": market_data.get("spy", {}).get("price"),
            "vix": market_data.get("vix"),
            "data_source": market_data.get("gold", {}).get("source", "unknown")
        },
        
        "targets": {
            "tp": 20,
            "sl": 0,
            "hunt_zone": 0
        },
        
        "ea_instructions": _build_ea_instructions(defcon, lot_mult),
        
        "analysis": {
            "sentiment": analysis.get("sentiment"),
            "intensity": analysis.get("intensity"),
            "risk_level": analysis.get("risk_level"),
            "reasoning": analysis.get("reasoning")
        },
        
        "neo_intelligence": {
            "defcon": neo_data.get("defcon"),
            "bullish_patterns": neo_data.get("bullish_count"),
            "bearish_patterns": neo_data.get("bearish_count"),
            "latest_pattern": latest_pattern.get("pattern") if latest_pattern else None,
            "latest_direction": latest_pattern.get("direction") if latest_pattern else None,
            "latest_message": latest_pattern.get("message") if latest_pattern else None
        },
        
        "macro_intel": _load_macro_intel(),

        "valid_until": (datetime.now() + timedelta(minutes=5)).isoformat(),
        "source": "quinn_v2_REAL_DATA_+_NEO"
    }


def atomic_write(path: Path, content: str):
    """Write atomically"""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(str(tmp), str(path))


# ══════════════════════════════════════════════════════════════════════════════
# MAIN LOOP
# ══════════════════════════════════════════════════════════════════════════════

def main():
    print("=" * 70)
    print("📡 QUINN SIGNAL WRITER V2 - REAL DATA MODE")
    print("=" * 70)
    print(f"Output:    {EA_SIGNAL_FILE}")
    print(f"Model:     {OLLAMA_MODEL}")
    print(f"Interval:  {UPDATE_SECONDS}s")
    print("=" * 70)
    print()
    print("⚡ USING REAL MARKET DATA - NO FAKE DATA!")
    print()
    
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    
    while True:
        try:
            now = datetime.now()
            print(f"[{now.strftime('%H:%M:%S')}] Fetching real data...")
            
            # Fetch REAL data
            gold_data = fetch_real_gold_price()
            spy_data = fetch_real_spy_price()
            vix = fetch_vix()
            
            market_data = {
                "gold": gold_data,
                "spy": spy_data,
                "vix": vix,
                "timestamp": now.isoformat()
            }
            
            if gold_data:
                print(f"  Gold: ${gold_data.get('spot_approx', 'N/A')} (via {gold_data.get('source')})")
            else:
                print("  Gold: ❌ FAILED TO FETCH")
            
            if spy_data:
                print(f"  SPY:  ${spy_data.get('price', 'N/A')}")
            
            if vix:
                print(f"  VIX:  {vix}")
            
            # Load NEO's existing signals (REUSE, don't regenerate)
            print("  Loading NEO intelligence...")
            neo_data = load_neo_signals()
            print(f"  NEO: DEFCON {neo_data.get('defcon')} | Bullish:{neo_data.get('bullish_count')} Bearish:{neo_data.get('bearish_count')}")
            if neo_data.get("latest_pattern"):
                lp = neo_data["latest_pattern"]
                print(f"  Latest: {lp.get('pattern')} ({lp.get('direction')})")
            
            # Analyze with Ollama
            print("  Analyzing with Ollama...")
            analysis = ollama_analyze(market_data)
            print(f"  → {analysis.get('sentiment')} ({analysis.get('intensity'):.0%}) - {analysis.get('risk_level')}")
            
            # Calculate DEFCON (uses NEO's existing DEFCON as baseline)
            defcon = calculate_defcon(analysis, vix, neo_data)
            defcon_colors = {1: "GREEN", 2: "YELLOW", 3: "ORANGE", 4: "RED", 5: "BLACK"}
            print(f"  → DEFCON {defcon} ({defcon_colors.get(defcon)})")
            
            # Generate and save signals
            signal = create_signal(market_data, analysis, defcon, neo_data)
            
            atomic_write(EA_SIGNAL_FILE, json.dumps(signal, indent=2))
            atomic_write(DEFCON_FILE, json.dumps({
                "level": defcon,
                "color": defcon_colors.get(defcon, "ORANGE"),
                "timestamp": now.isoformat()
            }, indent=2))
            atomic_write(MARKET_DATA_FILE, json.dumps(market_data, indent=2))
            
            print(f"  ✓ Signals written")
            print()
            
        except Exception as e:
            print(f"  ERROR: {e}")
            import traceback
            traceback.print_exc()
        
        time.sleep(UPDATE_SECONDS)


if __name__ == "__main__":
    main()
