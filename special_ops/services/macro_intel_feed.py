#!/usr/bin/env python3
"""
MACRO INTELLIGENCE FEED — 24/7 Gold Trading Context
=====================================================
Polls DXY, Oil, VIX, 10Y, SPY, BTC every 60s. Runs geopolitical headline
analysis every 5 min. Computes liquidity-sweep risk and a composite gold-bias
score. Serves everything on port 5001 and writes macro_intel.json for EAs.

PM2: pm2 start macro_intel_feed.py --name macro-intel-feed --interpreter python3
"""

import asyncio
import json
import logging
import os
import signal
import sys
import time
import traceback
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Any

import requests
import uvicorn
import yfinance as yf
from fastapi import FastAPI
from fastapi.responses import JSONResponse

# ═══════════════════════════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════════════════════════

log = logging.getLogger("macro-intel")
log.setLevel(logging.INFO)
_h = logging.StreamHandler()
_h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
log.addHandler(_h)

PORT = int(os.environ.get("MACRO_INTEL_PORT", "5001"))
CRELLA_IP = os.environ.get("CRELLA_IP", "100.119.161.65")

BOT_TOKEN = os.environ.get("CORTEX_BOT_TOKEN",
                           os.environ.get("TELEGRAM_BOT_TOKEN",
                                          "8250652030:AAFd4x8NsTfdaB3O67lUnMhotT2XY61600s"))
CHAT_ID = os.environ.get("CORTEX_CHAT_ID",
                         os.environ.get("TELEGRAM_CHAT_ID", "6776619257"))

OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.1:8b")

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
GNEWS_API_KEY = os.environ.get("GNEWS_API_KEY", "")

DATA_DIR = Path(__file__).parent.parent / "data" / "macro_intel"
DATA_DIR.mkdir(parents=True, exist_ok=True)
SIGNAL_DIR = Path("/home/jbot/trading_ai/crella_signals")
MACRO_FILE = SIGNAL_DIR / "macro_intel.json"
HISTORY_FILE = DATA_DIR / "macro_history.jsonl"

MARKET_POLL_INTERVAL = 60
NEWS_POLL_INTERVAL = 300
TELEGRAM_COOLDOWN = 600

# yfinance tickers
TICKERS = {
    "dxy": "DX-Y.NYB",
    "oil_wti": "CL=F",
    "vix": "^VIX",
    "us10y": "^TNX",
    "spy": "SPY",
    "btc": "BTC-USD",
}

# ═══════════════════════════════════════════════════════════════════════════════
# STATE
# ═══════════════════════════════════════════════════════════════════════════════

_state: Dict[str, Any] = {
    "macro_intel": {},
    "last_market_poll": None,
    "last_news_poll": None,
    "last_alerts": {},
    "prev_composite_bias": None,
    "started_at": datetime.now(timezone.utc).isoformat(),
    "polls_completed": 0,
    "errors": 0,
}

# ═══════════════════════════════════════════════════════════════════════════════
# MARKET DATA
# ═══════════════════════════════════════════════════════════════════════════════

def _safe_float(val, default=0.0):
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _pct_change(current, previous):
    if not previous or previous == 0:
        return 0.0
    return round((current - previous) / previous * 100, 3)


def _ema(series, span):
    return float(series.ewm(span=span, adjust=False).mean().iloc[-1])


def fetch_instrument(symbol: str, period="5d", interval="1h") -> Optional[dict]:
    try:
        t = yf.Ticker(symbol)
        hist = t.history(period=period, interval=interval)
        if hist.empty:
            return None

        close = hist["Close"]
        latest = float(close.iloc[-1])
        n = len(close)

        ref_1h = float(close.iloc[-2]) if n > 1 else latest
        ref_4h = float(close.iloc[-5]) if n > 4 else ref_1h
        ref_24h = float(close.iloc[-25]) if n > 24 else ref_4h

        result = {
            "price": round(latest, 4),
            "change_pct_1h": _pct_change(latest, ref_1h),
            "change_pct_4h": _pct_change(latest, ref_4h),
            "change_pct_24h": _pct_change(latest, ref_24h),
            "high_24h": round(float(close.tail(min(25, n)).max()), 4),
            "low_24h": round(float(close.tail(min(25, n)).min()), 4),
        }

        if n >= 20:
            result["ema_20"] = round(_ema(close, 20), 4)
        if n >= 50:
            result["ema_50"] = round(_ema(close, 50), 4)

        return result
    except Exception as e:
        log.warning(f"fetch {symbol} failed: {e}")
        return None


def classify_momentum(change_1h, change_4h, change_24h) -> str:
    short = change_1h + change_4h * 0.5
    if short > 0.3:
        return "SURGING"
    if short > 0.1:
        return "RISING"
    if short < -0.3:
        return "PLUNGING"
    if short < -0.1:
        return "FADING"
    return "FLAT"


def classify_trend(data: dict) -> str:
    price = data.get("price", 0)
    ema20 = data.get("ema_20")
    ema50 = data.get("ema_50")
    if ema20 and ema50:
        if price > ema20 > ema50:
            return "BULLISH"
        if price < ema20 < ema50:
            return "BEARISH"
        if price > ema20 and ema20 < ema50:
            return "BULLISH_SHORT_TERM"
        if price < ema20 and ema20 > ema50:
            return "BEARISH_SHORT_TERM"
    if ema20:
        return "BULLISH_SHORT_TERM" if price > ema20 else "BEARISH_SHORT_TERM"
    return "MIXED"


def gold_implication_dxy(data: dict) -> str:
    """DXY inverse relationship to gold."""
    mom = data.get("momentum", "FLAT")
    if mom in ("PLUNGING", "FADING"):
        return "BULLISH"
    if mom in ("SURGING", "RISING"):
        return "BEARISH"
    return "NEUTRAL"


def gold_implication_oil(data: dict) -> str:
    mom = data.get("momentum", "FLAT")
    if mom in ("SURGING", "RISING"):
        return "BULLISH"
    if mom in ("PLUNGING", "FADING"):
        return "NEUTRAL"
    return "NEUTRAL"


def gold_implication_vix(data: dict) -> str:
    price = data.get("price", 0)
    if price > 30:
        return "STRONGLY_BULLISH"
    if price > 25:
        return "CAUTIOUS"
    if price < 15:
        return "NEUTRAL"
    return "NEUTRAL"


def gold_implication_yields(data: dict) -> str:
    change = data.get("change_pct_24h", 0)
    if change < -0.5:
        return "BULLISH"
    if change > 0.5:
        return "BEARISH"
    return "NEUTRAL"


def poll_all_markets() -> dict:
    results = {}
    for name, sym in TICKERS.items():
        data = fetch_instrument(sym)
        if not data:
            continue

        data["momentum"] = classify_momentum(
            data["change_pct_1h"], data["change_pct_4h"], data["change_pct_24h"]
        )
        data["trend"] = classify_trend(data)

        if name == "dxy":
            data["gold_implication"] = gold_implication_dxy(data)
        elif name == "oil_wti":
            data["gold_implication"] = gold_implication_oil(data)
        elif name == "vix":
            data["level"] = (
                "EXTREME" if data["price"] > 35 else
                "HIGH" if data["price"] > 30 else
                "ELEVATED" if data["price"] > 20 else
                "LOW" if data["price"] < 15 else "NORMAL"
            )
            data["gold_implication"] = gold_implication_vix(data)
        elif name == "us10y":
            data["yield_pct"] = data["price"]
            bps_24h = round(data["change_pct_24h"] * data["price"], 1)
            data["change_bps_24h"] = bps_24h
            data["gold_implication"] = gold_implication_yields(data)
        elif name == "btc":
            data["gold_implication"] = (
                "BEARISH" if data["change_pct_1h"] < -3 else
                "CAUTIOUS" if data["change_pct_1h"] < -1 else "NEUTRAL"
            )

        results[name] = data

    return results


# ═══════════════════════════════════════════════════════════════════════════════
# LIQUIDITY SWEEP DETECTION
# ═══════════════════════════════════════════════════════════════════════════════

def compute_sweep_risk(markets: dict) -> dict:
    score = 0.0
    triggers = []

    vix = markets.get("vix", {})
    vix_price = vix.get("price", 0)
    vix_1h = vix.get("change_pct_1h", 0)

    if vix_price > 25:
        score += 0.2
        triggers.append(f"VIX elevated ({vix_price:.1f})")
    if vix_price > 30:
        score += 0.3
        triggers.append(f"VIX panic (>{vix_price:.0f})")
    if vix_1h > 10:
        score += 0.2
        triggers.append(f"VIX spiking +{vix_1h:.1f}% in 1h")

    spy = markets.get("spy", {})
    spy_1h = spy.get("change_pct_1h", 0)
    spy_4h = spy.get("change_pct_4h", 0)

    if spy_1h < -1:
        score += 0.2
        triggers.append(f"SPY dumping {spy_1h:.2f}% in 1h")
    if spy_4h < -2:
        score += 0.2
        triggers.append(f"SPY selloff {spy_4h:.2f}% in 4h")

    btc = markets.get("btc", {})
    btc_1h = btc.get("change_pct_1h", 0)
    btc_24h = btc.get("change_pct_24h", 0)

    if btc_1h < -3:
        score += 0.15
        triggers.append(f"BTC crashing {btc_1h:.2f}% in 1h")
    if btc_24h < -8:
        score += 0.15
        triggers.append(f"BTC liquidation zone ({btc_24h:.1f}% in 24h)")

    dxy = markets.get("dxy", {})
    dxy_1h = dxy.get("change_pct_1h", 0)
    dxy_4h = dxy.get("change_pct_4h", 0)

    if dxy_1h > 0.3:
        score += 0.2
        triggers.append(f"DXY spiking +{dxy_1h:.2f}% in 1h")
    if dxy_4h > 0.5:
        score += 0.15
        triggers.append(f"DXY surging +{dxy_4h:.2f}% in 4h")

    us10y = markets.get("us10y", {})
    y_change = us10y.get("change_bps_24h", 0)
    if y_change > 10:
        score += 0.1
        triggers.append(f"10Y yields rising +{y_change:.0f}bps")

    score = min(score, 1.0)

    if score >= 0.7:
        level = "CRITICAL"
    elif score >= 0.5:
        level = "HIGH"
    elif score >= 0.3:
        level = "MEDIUM"
    else:
        level = "LOW"

    warning = ""
    if level == "CRITICAL":
        warning = "Gold WILL face forced selling from cross-asset margin calls. Reduce all exposure immediately."
    elif level == "HIGH":
        warning = "Gold likely to face selling pressure from cross-asset margin calls. Reduce exposure."
    elif level == "MEDIUM":
        warning = "Watch closely — gold may face selling pressure from cross-asset margin calls."

    return {
        "level": level,
        "score": round(score, 3),
        "triggers": triggers,
        "warning": warning,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# GEOPOLITICAL INTEL
# ═══════════════════════════════════════════════════════════════════════════════

GEO_KEYWORDS = [
    "Iran", "Israel", "Middle East", "sanctions", "war", "nuclear",
    "oil embargo", "OPEC", "Fed", "rate cut", "rate hike", "tariff",
    "gold", "missile", "invasion", "peace deal", "ceasefire",
    "bank failure", "default", "inflation", "recession",
]

GEO_PROMPT = """You are a geopolitical risk analyst for gold (XAUUSD) trading.

Given these recent news headlines, assess the impact on gold prices.

Gold responds to:
- Military conflict (especially Middle East) → STRONGLY BULLISH
- US/Iran/Israel escalation → STRONGLY BULLISH
- Sanctions on oil-producing nations → BULLISH
- Fed rate cuts or dovish signals → BULLISH
- Fed rate hikes or hawkish signals → BEARISH
- Strong US jobs/GDP data → BEARISH (strong dollar)
- Weak US data → BULLISH (rate cut expectations)
- Trade wars / tariffs → MIXED
- Nuclear threats → STRONGLY BULLISH
- Peace negotiations → BEARISH
- OPEC production cuts → BULLISH
- Major bank failures → BULLISH
- Crypto crashes → SHORT-TERM BEARISH then BULLISH

Headlines:
{headlines}

Output ONLY valid JSON:
{{
  "risk_level": "LOW" or "MEDIUM" or "HIGH" or "EXTREME",
  "intensity": 0.0 to 1.0,
  "gold_implication": "STRONGLY_BULLISH" or "BULLISH" or "NEUTRAL" or "BEARISH" or "STRONGLY_BEARISH",
  "active_events": [{{"event": "short description", "impact": "GOLD_BULLISH or GOLD_BEARISH or NEUTRAL", "severity": "LOW or MEDIUM or HIGH"}}],
  "reasoning": "1-2 sentence summary"
}}"""


def fetch_news_headlines() -> List[str]:
    """Fetch geo-relevant headlines from free sources."""
    headlines = []

    # Try GNews API (free tier: 100 req/day)
    if GNEWS_API_KEY:
        try:
            resp = requests.get(
                "https://gnews.io/api/v4/search",
                params={
                    "q": "gold OR Iran OR Israel OR Fed OR OPEC OR tariff OR sanctions",
                    "lang": "en",
                    "max": 10,
                    "apikey": GNEWS_API_KEY,
                },
                timeout=10,
            )
            if resp.ok:
                for art in resp.json().get("articles", []):
                    headlines.append(art.get("title", ""))
        except Exception as e:
            log.debug(f"GNews error: {e}")

    # RSS fallback: Reuters world news
    try:
        resp = requests.get(
            "https://news.google.com/rss/search?q=gold+price+OR+Iran+OR+Fed+rate&hl=en-US&gl=US&ceid=US:en",
            timeout=10,
        )
        if resp.ok:
            import re
            titles = re.findall(r"<title>(.+?)</title>", resp.text)
            for t in titles[2:12]:
                cleaned = re.sub(r"<[^>]+>", "", t).strip()
                if cleaned and len(cleaned) > 20:
                    headlines.append(cleaned)
    except Exception as e:
        log.debug(f"RSS error: {e}")

    # Deduplicate
    seen = set()
    unique = []
    for h in headlines:
        key = h.lower()[:50]
        if key not in seen:
            seen.add(key)
            unique.append(h)

    return unique[:15]


def analyze_headlines_ollama(headlines: List[str]) -> dict:
    """Send headlines to Ollama for geopolitical risk assessment."""
    if not headlines:
        return {
            "risk_level": "LOW",
            "intensity": 0.1,
            "gold_implication": "NEUTRAL",
            "active_events": [],
            "reasoning": "No significant geopolitical headlines detected.",
        }

    prompt = GEO_PROMPT.format(headlines="\n".join(f"- {h}" for h in headlines))

    try:
        resp = requests.post(
            OLLAMA_URL,
            json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False},
            timeout=45,
        )
        resp.raise_for_status()
        text = resp.json().get("response", "")
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            result = json.loads(text[start:end])
            if "risk_level" in result:
                return result
    except Exception as e:
        log.warning(f"Ollama geo analysis failed: {e}")

    return {
        "risk_level": "LOW",
        "intensity": 0.1,
        "gold_implication": "NEUTRAL",
        "active_events": [],
        "reasoning": "Analysis unavailable — defaulting to low risk.",
    }


# ═══════════════════════════════════════════════════════════════════════════════
# DXY-GOLD MOMENTUM PATTERNS
# ═══════════════════════════════════════════════════════════════════════════════

def detect_dxy_patterns(markets: dict) -> List[dict]:
    patterns = []
    dxy = markets.get("dxy", {})
    vix = markets.get("vix", {})
    oil = markets.get("oil_wti", {})

    dxy_mom = dxy.get("momentum", "FLAT")
    dxy_1h = dxy.get("change_pct_1h", 0)
    vix_mom = vix.get("momentum", "FLAT")
    oil_mom = oil.get("momentum", "FLAT")
    dxy_price = dxy.get("price", 0)
    dxy_ema20 = dxy.get("ema_20", 0)

    if dxy_ema20 and dxy_price < dxy_ema20:
        patterns.append({
            "pattern": "DXY_BELOW_EMA20",
            "signal": "Gold continuation likely",
            "action": "Increase lot multiplier",
        })

    if dxy_1h > 0.3:
        patterns.append({
            "pattern": "DXY_SPIKE",
            "signal": "Gold incoming selloff",
            "action": "Reduce exposure, tighten stops",
        })

    if dxy_mom in ("FADING", "PLUNGING") and vix_mom in ("FADING", "PLUNGING"):
        patterns.append({
            "pattern": "DXY_DOWN_VIX_DOWN",
            "signal": "Risk-on — gold may fade",
            "action": "Reduce long bias",
        })

    if dxy_mom in ("FADING", "PLUNGING") and vix_mom in ("RISING", "SURGING"):
        patterns.append({
            "pattern": "DXY_DOWN_VIX_UP",
            "signal": "Pure flight to gold",
            "action": "Maximum conviction long",
        })

    if oil_mom in ("RISING", "SURGING") and dxy_mom in ("FADING", "PLUNGING"):
        patterns.append({
            "pattern": "OIL_UP_DXY_DOWN",
            "signal": "Inflation trade — gold rocket",
            "action": "Aggressive long",
        })

    if oil_mom in ("RISING", "SURGING") and dxy_mom in ("RISING", "SURGING"):
        patterns.append({
            "pattern": "OIL_UP_DXY_UP",
            "signal": "Stagflation risk — gold choppy",
            "action": "Reduce all positions",
        })

    return patterns


# ═══════════════════════════════════════════════════════════════════════════════
# COMPOSITE SCORING
# ═══════════════════════════════════════════════════════════════════════════════

_IMPLICATION_SCORES = {
    "STRONGLY_BULLISH": 2,
    "BULLISH": 1,
    "NEUTRAL": 0,
    "CAUTIOUS": -0.3,
    "BEARISH": -1,
    "STRONGLY_BEARISH": -2,
}


def compute_composite(markets: dict, sweep: dict, geo: dict, patterns: list) -> dict:
    score = 0.0
    bullish_factors = []
    bearish_factors = []

    weights = {"dxy": 3.0, "oil_wti": 1.5, "vix": 2.0, "us10y": 1.5, "spy": 1.0, "btc": 0.5}

    for name, weight in weights.items():
        impl = markets.get(name, {}).get("gold_implication", "NEUTRAL")
        val = _IMPLICATION_SCORES.get(impl, 0) * weight
        score += val
        if val > 0:
            mom = markets.get(name, {}).get("momentum", "")
            bullish_factors.append(f"{name.upper()} {mom.lower()}" if mom else name.upper())
        elif val < 0:
            mom = markets.get(name, {}).get("momentum", "")
            bearish_factors.append(f"{name.upper()} {mom.lower()}" if mom else name.upper())

    geo_impl = geo.get("gold_implication", "NEUTRAL")
    geo_val = _IMPLICATION_SCORES.get(geo_impl, 0) * 2.5
    score += geo_val
    if geo_val > 0:
        bullish_factors.append(f"Geopolitical risk {geo.get('risk_level', 'LOW')}")
    elif geo_val < 0:
        bearish_factors.append("Geopolitical de-escalation")

    sweep_level = sweep.get("level", "LOW")
    if sweep_level == "CRITICAL":
        score -= 3
        bearish_factors.append("Liquidity sweep CRITICAL")
    elif sweep_level == "HIGH":
        score -= 1.5
        bearish_factors.append("Liquidity sweep risk HIGH")

    for p in patterns:
        if "conviction long" in p.get("action", "").lower():
            score += 1.5
        elif "aggressive long" in p.get("action", "").lower():
            score += 1.0
        elif "reduce" in p.get("action", "").lower():
            score -= 0.5

    max_abs = 15.0
    conviction = int(min(max(50 + (score / max_abs) * 50, 5), 99))

    if conviction >= 70:
        bias = "STRONGLY_BULLISH"
    elif conviction >= 58:
        bias = "BULLISH"
    elif conviction >= 42:
        bias = "NEUTRAL"
    elif conviction >= 30:
        bias = "BEARISH"
    else:
        bias = "STRONGLY_BEARISH"

    if conviction >= 72 and sweep_level in ("LOW", "MEDIUM"):
        stance = "AGGRESSIVE_LONG"
        lot_mult = 1.2
    elif conviction >= 58:
        stance = "LONG"
        lot_mult = 1.0
    elif conviction >= 42:
        stance = "NEUTRAL"
        lot_mult = 0.7
    elif conviction >= 30:
        stance = "REDUCE_LONG"
        lot_mult = 0.5
    else:
        stance = "DEFENSIVE"
        lot_mult = 0.3

    if sweep_level == "CRITICAL":
        stance = "DEFENSIVE"
        lot_mult = 0.3
    elif sweep_level == "HIGH":
        lot_mult = min(lot_mult, 0.5)
        if stance == "AGGRESSIVE_LONG":
            stance = "LONG"

    return {
        "gold_bias": bias,
        "conviction": conviction,
        "raw_score": round(score, 2),
        "factors_bullish": bullish_factors,
        "factors_bearish": bearish_factors,
        "recommended_stance": stance,
        "max_lot_multiplier": lot_mult,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# TELEGRAM
# ═══════════════════════════════════════════════════════════════════════════════

def _should_alert(key: str) -> bool:
    last = _state["last_alerts"].get(key, 0)
    return time.time() - last > TELEGRAM_COOLDOWN


def _mark_alerted(key: str):
    _state["last_alerts"][key] = time.time()


def send_telegram(text: str) -> bool:
    if not BOT_TOKEN or not CHAT_ID:
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=15,
        )
        return r.ok
    except Exception:
        return False


def check_alerts(intel: dict):
    sweep = intel.get("liquidity_sweep_risk", {})
    geo = intel.get("geopolitical", {})
    dxy = intel.get("dxy", {})
    composite = intel.get("composite_score", {})

    # 1. Sweep CRITICAL or HIGH
    if sweep.get("level") in ("CRITICAL", "HIGH") and _should_alert("sweep"):
        triggers = ", ".join(sweep.get("triggers", [])[:3])
        send_telegram(
            f"🚨 <b>SWEEP ALERT: {sweep['level']}</b>\n\n"
            f"Gold may face forced selling.\n"
            f"Score: {sweep.get('score', 0):.2f}\n"
            f"Triggers: {triggers}\n\n"
            f"<i>{sweep.get('warning', '')}</i>"
        )
        _mark_alerted("sweep")

    # 2. Geopolitical HIGH/EXTREME
    if geo.get("risk_level") in ("HIGH", "EXTREME") and _should_alert("geo"):
        events = geo.get("active_events", [])
        event_text = events[0].get("event", "Unknown") if events else "Escalation detected"
        send_telegram(
            f"🌍 <b>GEO ALERT: {geo['risk_level']}</b>\n\n"
            f"{event_text}\n"
            f"Gold implication: <b>{geo.get('gold_implication', 'UNKNOWN')}</b>\n"
            f"Intensity: {geo.get('intensity', 0):.0%}\n\n"
            f"<i>{geo.get('reasoning', '')}</i>"
        )
        _mark_alerted("geo")

    # 3. DXY flash move >0.5% in 1h
    dxy_1h = abs(dxy.get("change_pct_1h", 0))
    if dxy_1h > 0.5 and _should_alert("dxy_flash"):
        direction = "UP" if dxy.get("change_pct_1h", 0) > 0 else "DOWN"
        impl = "BEARISH" if direction == "UP" else "BULLISH"
        send_telegram(
            f"💵 <b>DXY FLASH MOVE</b>\n\n"
            f"Dollar {direction} {dxy_1h:.2f}% in 1h\n"
            f"DXY: {dxy.get('price', 0):.2f}\n"
            f"Gold implication: <b>{impl}</b>"
        )
        _mark_alerted("dxy_flash")

    # 4. Composite bias flip
    new_bias = composite.get("gold_bias", "NEUTRAL")
    prev_bias = _state.get("prev_composite_bias")
    if prev_bias and new_bias != prev_bias and _should_alert("macro_flip"):
        send_telegram(
            f"🔄 <b>MACRO FLIP</b>\n\n"
            f"Gold bias changed: <b>{prev_bias}</b> → <b>{new_bias}</b>\n"
            f"Conviction: {composite.get('conviction', 0)}%\n"
            f"Stance: {composite.get('recommended_stance', 'NEUTRAL')}\n\n"
            f"Bullish: {', '.join(composite.get('factors_bullish', [])[:3])}\n"
            f"Bearish: {', '.join(composite.get('factors_bearish', [])[:3])}"
        )
        _mark_alerted("macro_flip")

    _state["prev_composite_bias"] = new_bias


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN LOOP
# ═══════════════════════════════════════════════════════════════════════════════

def build_intel(markets: dict, geo: dict) -> dict:
    sweep = compute_sweep_risk(markets)
    patterns = detect_dxy_patterns(markets)
    composite = compute_composite(markets, sweep, geo, patterns)

    now = datetime.now(timezone.utc)

    intel = {
        "timestamp": now.isoformat(),
        "timestamp_unix": now.timestamp(),
        **{k: v for k, v in markets.items()},
        "liquidity_sweep_risk": sweep,
        "geopolitical": geo,
        "dxy_patterns": patterns,
        "composite_score": composite,
    }

    return intel


def save_intel(intel: dict):
    MACRO_FILE.write_text(json.dumps(intel, indent=2))
    with open(HISTORY_FILE, "a") as f:
        compact = {
            "ts": intel["timestamp"],
            "dxy": intel.get("dxy", {}).get("price"),
            "oil": intel.get("oil_wti", {}).get("price"),
            "vix": intel.get("vix", {}).get("price"),
            "us10y": intel.get("us10y", {}).get("price"),
            "spy": intel.get("spy", {}).get("price"),
            "btc": intel.get("btc", {}).get("price"),
            "sweep": intel.get("liquidity_sweep_risk", {}).get("level"),
            "geo": intel.get("geopolitical", {}).get("risk_level"),
            "bias": intel.get("composite_score", {}).get("gold_bias"),
            "conviction": intel.get("composite_score", {}).get("conviction"),
        }
        f.write(json.dumps(compact) + "\n")


_geo_cache: dict = {
    "risk_level": "LOW",
    "intensity": 0.1,
    "gold_implication": "NEUTRAL",
    "active_events": [],
    "reasoning": "Initializing — no data yet.",
}


async def poll_loop():
    global _geo_cache
    last_news_time = 0

    log.info("Macro Intel Feed starting")
    log.info(f"  Port: {PORT}")
    log.info(f"  Telegram: {CHAT_ID}")
    log.info(f"  Ollama: {OLLAMA_MODEL}")
    log.info(f"  Output: {MACRO_FILE}")

    while True:
        try:
            # Market data every 60s
            markets = poll_all_markets()
            _state["last_market_poll"] = datetime.now(timezone.utc).isoformat()

            # News every 5 min
            now = time.time()
            if now - last_news_time > NEWS_POLL_INTERVAL:
                try:
                    headlines = fetch_news_headlines()
                    if headlines:
                        _geo_cache = analyze_headlines_ollama(headlines)
                        _state["last_news_poll"] = datetime.now(timezone.utc).isoformat()
                        log.info(f"Geo update: {_geo_cache.get('risk_level')} — {_geo_cache.get('gold_implication')}")
                except Exception as e:
                    log.warning(f"News poll error: {e}")
                last_news_time = now

            intel = build_intel(markets, _geo_cache)
            save_intel(intel)
            _state["macro_intel"] = intel
            _state["polls_completed"] += 1

            check_alerts(intel)

            comp = intel.get("composite_score", {})
            dxy_p = intel.get("dxy", {}).get("price", 0)
            sweep_l = intel.get("liquidity_sweep_risk", {}).get("level", "?")
            log.info(
                f"DXY:{dxy_p:.2f} | Bias:{comp.get('gold_bias','?')} "
                f"Conv:{comp.get('conviction', 0)}% | Sweep:{sweep_l} | "
                f"Stance:{comp.get('recommended_stance','?')}"
            )

        except Exception as e:
            _state["errors"] += 1
            log.error(f"Poll error: {e}", exc_info=True)

        await asyncio.sleep(MARKET_POLL_INTERVAL)


# ═══════════════════════════════════════════════════════════════════════════════
# FASTAPI
# ═══════════════════════════════════════════════════════════════════════════════

app = FastAPI(title="Macro Intelligence Feed", version="1.0")


@app.on_event("startup")
async def startup():
    asyncio.create_task(poll_loop())


@app.get("/macro")
def get_macro():
    return _state.get("macro_intel", {})


@app.get("/macro/dxy")
def get_dxy():
    return _state.get("macro_intel", {}).get("dxy", {})


@app.get("/macro/geo")
def get_geo():
    return _state.get("macro_intel", {}).get("geopolitical", {})


@app.get("/macro/sweep")
def get_sweep():
    return _state.get("macro_intel", {}).get("liquidity_sweep_risk", {})


@app.get("/macro/score")
def get_score():
    return _state.get("macro_intel", {}).get("composite_score", {})


@app.get("/macro/patterns")
def get_patterns():
    return _state.get("macro_intel", {}).get("dxy_patterns", [])


@app.get("/macro/health")
def get_health():
    intel = _state.get("macro_intel", {})
    ts = intel.get("timestamp")
    age = None
    if ts:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(ts)).total_seconds()

    return {
        "status": "ok" if age and age < 120 else "stale",
        "data_age_seconds": round(age, 1) if age else None,
        "last_market_poll": _state.get("last_market_poll"),
        "last_news_poll": _state.get("last_news_poll"),
        "polls_completed": _state.get("polls_completed", 0),
        "errors": _state.get("errors", 0),
        "started_at": _state.get("started_at"),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# ENTRY
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning")
