#!/usr/bin/env python3
"""
GLD STRANGLE DASHBOARD v2.00
════════════════════════════
Remote-accessible dashboard for GLD options strangle strategy.
Deployed on H100, accessed from Philippines.

Port: 8080
Stack: Flask + yfinance + Ollama (dolphin3)

Features:
  - Live GLD/Gold spot price
  - Yesterday's range + Fibonacci levels (auto-calculated)
  - Strangle setup: OTM calls at resistance, OTM puts at support
  - ATR, RSI, ADX indicators
  - Ollama AI analysis (strangle recommendation)
  - MT5 signal relay status
  - Position tracker + P&L
  - CPI/NFP/FOMC calendar awareness
"""

import os
import json
import time
import logging
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from functools import lru_cache

import yfinance as yf
import requests
from flask import Flask, jsonify, render_template_string, request

# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

PORT = int(os.environ.get("GLD_DASHBOARD_PORT", 8080))
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "dolphin3")
SIGNAL_FILE = Path(os.environ.get(
    "MT5_SIGNAL_FILE",
    "/home/jbot/trading_ai/gld_dashboard/mt5_signal.json"
))
POSITIONS_FILE = Path(os.environ.get(
    "GLD_POSITIONS_FILE",
    "/home/jbot/trading_ai/gld_dashboard/positions.json"
))
STATE_FILE = Path(os.environ.get(
    "GLD_STATE_FILE",
    "/home/jbot/trading_ai/gld_dashboard/state.json"
))
# Desktop signal relay URL (Option C)
DESKTOP_SIGNAL_URL = os.environ.get(
    "DESKTOP_SIGNAL_URL",
    "http://100.119.161.65:8098/signal/gld"
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [GLD] %(message)s")
logger = logging.getLogger("gld_dashboard")

app = Flask(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
# MARKET DATA ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

class MarketData:
    """Fetch and cache GLD/Gold market data."""

    def __init__(self):
        self._cache = {}
        self._cache_time = 0
        self._cache_ttl = 60  # Refresh every 60 seconds

    def get_gld_data(self) -> dict:
        """Get comprehensive GLD market data."""
        now = time.time()
        if now - self._cache_time < self._cache_ttl and self._cache:
            return self._cache

        try:
            gld = yf.Ticker("GLD")
            gold = yf.Ticker("GC=F")  # Gold futures

            # Current prices
            gld_info = gld.fast_info
            gld_price = gld_info.last_price if hasattr(gld_info, 'last_price') else 0

            gold_info = gold.fast_info
            gold_price = gold_info.last_price if hasattr(gold_info, 'last_price') else 0

            # Historical data for indicators (60 days)
            hist = gld.history(period="60d", interval="1d")
            if hist.empty:
                hist = gld.history(period="3mo", interval="1d")

            # Yesterday's data
            if len(hist) >= 2:
                yesterday = hist.iloc[-2]
                today_open = hist.iloc[-1]["Open"] if len(hist) >= 1 else gld_price
                yesterday_high = float(yesterday["High"])
                yesterday_low = float(yesterday["Low"])
                yesterday_close = float(yesterday["Close"])
                yesterday_range = yesterday_high - yesterday_low
            else:
                yesterday_high = gld_price * 1.005
                yesterday_low = gld_price * 0.995
                yesterday_close = gld_price
                yesterday_range = yesterday_high - yesterday_low
                today_open = gld_price

            # Fibonacci levels (from yesterday's range)
            fib = self._fibonacci_levels(yesterday_low, yesterday_high)

            # ATR (14-day)
            atr = self._calculate_atr(hist, period=14)

            # RSI (14-day)
            rsi_val = self._calculate_rsi(hist["Close"].values, period=14)

            # ADX (14-day)
            adx_val = self._calculate_adx(hist, period=14)

            # Weekly range for context
            week_hist = hist.tail(5)
            week_high = float(week_hist["High"].max()) if len(week_hist) > 0 else 0
            week_low = float(week_hist["Low"].min()) if len(week_hist) > 0 else 0

            # Strangle setup
            strangle = self._calculate_strangle(
                gld_price, fib, atr, rsi_val, adx_val, yesterday_range
            )

            data = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "gld_price": round(gld_price, 2),
                "gold_price": round(gold_price, 2),
                "gold_gld_ratio": round(gold_price / gld_price, 2) if gld_price > 0 else 0,
                "today_open": round(today_open, 2),
                "yesterday": {
                    "high": round(yesterday_high, 2),
                    "low": round(yesterday_low, 2),
                    "close": round(yesterday_close, 2),
                    "range": round(yesterday_range, 2),
                    "range_pct": round(yesterday_range / yesterday_close * 100, 2),
                },
                "week": {
                    "high": round(week_high, 2),
                    "low": round(week_low, 2),
                    "range": round(week_high - week_low, 2),
                },
                "fibonacci": fib,
                "indicators": {
                    "atr_14": round(atr, 2),
                    "rsi_14": round(rsi_val, 1),
                    "adx_14": round(adx_val, 1),
                    "regime": self._market_regime(rsi_val, adx_val, atr, yesterday_range),
                },
                "strangle": strangle,
            }

            self._cache = data
            self._cache_time = now
            return data

        except Exception as e:
            logger.error(f"Market data fetch error: {e}")
            if self._cache:
                return self._cache
            return {"error": str(e), "timestamp": datetime.now(timezone.utc).isoformat()}

    def _fibonacci_levels(self, low: float, high: float) -> dict:
        """Calculate Fibonacci retracement and extension levels."""
        diff = high - low
        return {
            "0.0": round(low, 2),                          # Support base
            "23.6": round(low + 0.236 * diff, 2),          # Support zone
            "38.2": round(low + 0.382 * diff, 2),
            "50.0": round(low + 0.500 * diff, 2),          # Midpoint
            "61.8": round(low + 0.618 * diff, 2),
            "78.6": round(low + 0.786 * diff, 2),
            "100.0": round(high, 2),                        # Resistance base
            "127.2": round(high + 0.272 * diff, 2),        # Extension target
            "161.8": round(high + 0.618 * diff, 2),        # Strong extension
        }

    def _calculate_atr(self, hist, period=14) -> float:
        """Average True Range."""
        if len(hist) < period + 1:
            return 0.0
        highs = hist["High"].values
        lows = hist["Low"].values
        closes = hist["Close"].values
        trs = []
        for i in range(1, len(hist)):
            tr = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )
            trs.append(tr)
        if len(trs) < period:
            return sum(trs) / len(trs) if trs else 0
        # EMA-style ATR
        atr = sum(trs[:period]) / period
        for tr in trs[period:]:
            atr = (atr * (period - 1) + tr) / period
        return atr

    def _calculate_rsi(self, closes, period=14) -> float:
        """RSI calculation."""
        if len(closes) < period + 1:
            return 50.0
        deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
        gains = [d if d > 0 else 0 for d in deltas]
        losses = [-d if d < 0 else 0 for d in deltas]
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period
        for i in range(period, len(deltas)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def _calculate_adx(self, hist, period=14) -> float:
        """Simplified ADX."""
        if len(hist) < period * 2:
            return 25.0
        highs = hist["High"].values
        lows = hist["Low"].values
        closes = hist["Close"].values
        plus_dm = []
        minus_dm = []
        tr_list = []
        for i in range(1, len(hist)):
            up = highs[i] - highs[i - 1]
            down = lows[i - 1] - lows[i]
            plus_dm.append(up if up > down and up > 0 else 0)
            minus_dm.append(down if down > up and down > 0 else 0)
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            tr_list.append(tr)

        if len(tr_list) < period:
            return 25.0
        sm_tr = sum(tr_list[:period])
        sm_plus = sum(plus_dm[:period])
        sm_minus = sum(minus_dm[:period])
        dx_list = []
        for i in range(period, len(tr_list)):
            sm_tr = sm_tr - sm_tr / period + tr_list[i]
            sm_plus = sm_plus - sm_plus / period + plus_dm[i]
            sm_minus = sm_minus - sm_minus / period + minus_dm[i]
            if sm_tr > 0:
                pdi = 100 * sm_plus / sm_tr
                mdi = 100 * sm_minus / sm_tr
                di_sum = pdi + mdi
                dx = 100 * abs(pdi - mdi) / di_sum if di_sum > 0 else 0
                dx_list.append(dx)
        if not dx_list:
            return 25.0
        adx = sum(dx_list[-period:]) / min(len(dx_list), period)
        return adx

    def _market_regime(self, rsi, adx, atr, daily_range) -> str:
        """Determine current market regime."""
        if adx > 25:
            if rsi > 60:
                return "TRENDING_BULLISH"
            elif rsi < 40:
                return "TRENDING_BEARISH"
            else:
                return "TRENDING"
        elif adx < 18:
            return "CONSOLIDATING"
        else:
            if atr > daily_range * 1.2:
                return "VOLATILE"
            return "RANGING"

    def _calculate_strangle(self, price, fib, atr, rsi, adx, daily_range) -> dict:
        """Calculate recommended strangle setup."""
        # Strike selection
        # Calls: Fibonacci 100-127.2% (resistance zone)
        # Puts: Fibonacci 0-23.6% (support zone)
        call_strike = self._round_to_strike(fib["127.2"])
        put_strike = self._round_to_strike(fib["0.0"])

        # Estimate premium costs (rough: ATR * 0.4 for 30-day options)
        est_call_premium = round(atr * 0.40, 2)
        est_put_premium = round(atr * 0.35, 2)

        # Breakevens
        call_breakeven = round(call_strike + est_call_premium, 2)
        put_breakeven = round(put_strike - est_put_premium, 2)

        # 60/40 bullish lean
        # With $5000 budget: 60% calls, 40% puts
        budget = 5000
        call_budget = budget * 0.60
        put_budget = budget * 0.40
        call_qty = max(1, int(call_budget / (est_call_premium * 100)))
        put_qty = max(1, int(put_budget / (est_put_premium * 100)))

        # Expiry recommendation
        today = datetime.now()
        # Find next monthly expiry (3rd Friday)
        next_month = today.replace(day=1) + timedelta(days=32)
        next_month = next_month.replace(day=1)
        # Find 3rd Friday
        first_day = next_month.weekday()
        days_to_friday = (4 - first_day) % 7
        third_friday = next_month.replace(day=1 + days_to_friday + 14)
        dte = (third_friday - today).days

        # If DTE < 21, go to month after
        if dte < 21:
            next_month = next_month + timedelta(days=32)
            next_month = next_month.replace(day=1)
            first_day = next_month.weekday()
            days_to_friday = (4 - first_day) % 7
            third_friday = next_month.replace(day=1 + days_to_friday + 14)
            dte = (third_friday - today).days

        # Lean direction
        if rsi > 55:
            lean = "BULLISH"
            lean_reasoning = f"RSI at {rsi:.0f} shows bullish momentum"
        elif rsi < 45:
            lean = "SLIGHTLY_BULLISH"
            lean_reasoning = f"RSI at {rsi:.0f} but gold always beats ATH"
        else:
            lean = "BULLISH"
            lean_reasoning = "Neutral RSI but default bullish lean (gold accumulation thesis)"

        return {
            "call_strike": call_strike,
            "put_strike": put_strike,
            "call_premium_est": est_call_premium,
            "put_premium_est": est_put_premium,
            "call_breakeven": call_breakeven,
            "put_breakeven": put_breakeven,
            "call_qty": call_qty,
            "put_qty": put_qty,
            "total_cost_est": round((call_qty * est_call_premium + put_qty * est_put_premium) * 100, 2),
            "expiry": third_friday.strftime("%Y-%m-%d"),
            "dte": dte,
            "lean": lean,
            "lean_reasoning": lean_reasoning,
            "risk": "MEDIUM" if adx < 25 else "HIGH",
            "entry_timing": "WAIT" if adx > 30 else "READY",
        }

    @staticmethod
    def _round_to_strike(price: float) -> float:
        """Round price to nearest valid GLD option strike ($1 increments)."""
        return round(price)


market = MarketData()


# ═══════════════════════════════════════════════════════════════════════════════
# OLLAMA AI ANALYSIS
# ═══════════════════════════════════════════════════════════════════════════════

def get_ollama_analysis(data: dict) -> dict:
    """Get AI strangle recommendation from Ollama Dolphin3."""
    s = data.get("strangle", {})
    ind = data.get("indicators", {})
    y = data.get("yesterday", {})
    fib = data.get("fibonacci", {})

    prompt = f"""You are a GLD options strategist. Given the following market data, recommend
a strangle setup using OTM calls at Fibonacci resistance and OTM puts at
Fibonacci support. Always lean bullish (60/40 calls/puts) because gold
always beats its all-time high and is being accumulated by central banks.

Gold: ${data.get('gold_price', 0)} | GLD: ${data.get('gld_price', 0)}
ATR: ${ind.get('atr_14', 0)} | RSI: {ind.get('rsi_14', 50)} | ADX: {ind.get('adx_14', 25)}
Regime: {ind.get('regime', 'UNKNOWN')}
Yesterday Range: ${y.get('high', 0)} - ${y.get('low', 0)} (${y.get('range', 0)})
Fibonacci Levels: 0%=${fib.get('0.0', 0)}, 50%=${fib.get('50.0', 0)}, 100%=${fib.get('100.0', 0)}, 127.2%=${fib.get('127.2', 0)}
Suggested Expiry: {s.get('expiry', 'N/A')} ({s.get('dte', 0)} DTE)
Pre-calculated Call Strike: ${s.get('call_strike', 0)} | Put Strike: ${s.get('put_strike', 0)}
Est Premiums: Call ${s.get('call_premium_est', 0)} | Put ${s.get('put_premium_est', 0)}
Available buying power: $10,000

Provide:
1. Recommended call strike and quantity (with reasoning)
2. Recommended put strike and quantity (with reasoning)
3. Lean direction (bullish/bearish percentage split)
4. Risk assessment (high/medium/low) with explanation
5. Entry timing: enter now, wait for pullback, or wait for data release
6. Specific profit targets for each leg
7. When to roll (days to expiry threshold)

Be specific with numbers. Be concise."""

    try:
        resp = requests.post(
            f"{OLLAMA_URL}/api/generate",
            json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False},
            timeout=120,
        )
        if resp.status_code == 200:
            result = resp.json()
            return {
                "status": "ok",
                "analysis": result.get("response", "No response"),
                "model": OLLAMA_MODEL,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        else:
            return {"status": "error", "message": f"Ollama HTTP {resp.status_code}"}
    except requests.exceptions.ConnectionError:
        return {"status": "offline", "message": "Ollama not reachable"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


# ═══════════════════════════════════════════════════════════════════════════════
# MT5 SIGNAL RELAY
# ═══════════════════════════════════════════════════════════════════════════════

def fetch_mt5_signal() -> dict:
    """Fetch MT5 signal from US desktop relay or local cache."""
    # Try remote relay first
    try:
        resp = requests.get(DESKTOP_SIGNAL_URL, timeout=5)
        if resp.status_code == 200:
            signal = resp.json()
            # Cache locally
            SIGNAL_FILE.write_text(json.dumps(signal, indent=2))
            return signal
    except Exception:
        pass

    # Fall back to local cache
    if SIGNAL_FILE.exists():
        try:
            data = json.loads(SIGNAL_FILE.read_text())
            data["_source"] = "local_cache"
            return data
        except Exception:
            pass

    return {"status": "no_signal", "message": "MT5 signal not available"}


# ═══════════════════════════════════════════════════════════════════════════════
# POSITION TRACKER
# ═══════════════════════════════════════════════════════════════════════════════

def load_positions() -> list:
    """Load tracked positions."""
    if POSITIONS_FILE.exists():
        try:
            return json.loads(POSITIONS_FILE.read_text())
        except Exception:
            return []
    return []


def save_positions(positions: list):
    """Save positions to disk."""
    POSITIONS_FILE.write_text(json.dumps(positions, indent=2))


# ═══════════════════════════════════════════════════════════════════════════════
# API ROUTES
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/")
def index():
    """Main dashboard page."""
    data = market.get_gld_data()
    mt5 = fetch_mt5_signal()
    positions = load_positions()
    return render_template_string(DASHBOARD_HTML, data=data, mt5=mt5,
                                  positions=positions, json=json)


@app.route("/api/market")
def api_market():
    """Market data API endpoint."""
    return jsonify(market.get_gld_data())


@app.route("/api/analysis")
def api_analysis():
    """Get Ollama AI analysis."""
    data = market.get_gld_data()
    analysis = get_ollama_analysis(data)
    return jsonify(analysis)


@app.route("/api/signal")
def api_signal():
    """Get latest MT5 signal."""
    return jsonify(fetch_mt5_signal())


@app.route("/api/positions", methods=["GET"])
def api_positions_get():
    """Get tracked positions."""
    return jsonify(load_positions())


@app.route("/api/positions", methods=["POST"])
def api_positions_update():
    """Add or update a position."""
    pos = request.json
    positions = load_positions()
    # Update existing or add new
    found = False
    for i, p in enumerate(positions):
        if p.get("id") == pos.get("id"):
            positions[i] = pos
            found = True
            break
    if not found:
        pos["id"] = pos.get("id", f"pos_{int(time.time())}")
        pos["opened_at"] = datetime.now(timezone.utc).isoformat()
        positions.append(pos)
    save_positions(positions)
    return jsonify({"status": "ok", "positions": positions})


@app.route("/api/positions/<pos_id>", methods=["DELETE"])
def api_positions_delete(pos_id):
    """Close/remove a position."""
    positions = load_positions()
    positions = [p for p in positions if p.get("id") != pos_id]
    save_positions(positions)
    return jsonify({"status": "ok", "positions": positions})


@app.route("/api/health")
def api_health():
    """Health check."""
    return jsonify({
        "status": "ok",
        "service": "gld-strangle-dashboard",
        "version": "2.00",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "ollama": OLLAMA_MODEL,
        "signal_relay": DESKTOP_SIGNAL_URL,
    })


# ═══════════════════════════════════════════════════════════════════════════════
# DASHBOARD HTML (embedded for single-file deployment)
# ═══════════════════════════════════════════════════════════════════════════════

DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>GLD Strangle Dashboard v2.00</title>
<style>
  :root { --bg: #0a0e17; --card: #131a2b; --accent: #f0b90b; --green: #0ecb81;
          --red: #f6465d; --text: #eaecef; --muted: #848e9c; --border: #1e2839; }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: 'Segoe UI', system-ui, sans-serif; background: var(--bg);
         color: var(--text); padding: 16px; }
  h1 { color: var(--accent); margin-bottom: 8px; font-size: 1.5em; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
          gap: 16px; margin-top: 16px; }
  .card { background: var(--card); border: 1px solid var(--border);
          border-radius: 12px; padding: 20px; }
  .card h2 { color: var(--accent); font-size: 1.1em; margin-bottom: 12px;
             border-bottom: 1px solid var(--border); padding-bottom: 8px; }
  .price-big { font-size: 2.4em; font-weight: 700; color: var(--green); }
  .price-gold { color: var(--accent); font-size: 1.2em; }
  .row { display: flex; justify-content: space-between; padding: 4px 0;
         border-bottom: 1px solid var(--border); }
  .row:last-child { border-bottom: none; }
  .label { color: var(--muted); }
  .val { font-weight: 600; }
  .bullish { color: var(--green); }
  .bearish { color: var(--red); }
  .neutral { color: var(--accent); }
  .regime { display: inline-block; padding: 4px 12px; border-radius: 20px;
            font-size: 0.85em; font-weight: 700; }
  .regime-TRENDING_BULLISH { background: rgba(14,203,129,0.15); color: var(--green); }
  .regime-TRENDING_BEARISH { background: rgba(246,70,93,0.15); color: var(--red); }
  .regime-CONSOLIDATING { background: rgba(240,185,11,0.15); color: var(--accent); }
  .regime-RANGING { background: rgba(132,142,156,0.15); color: var(--muted); }
  .regime-TRENDING { background: rgba(14,203,129,0.1); color: var(--green); }
  .regime-VOLATILE { background: rgba(246,70,93,0.15); color: var(--red); }
  .strangle-box { background: rgba(240,185,11,0.05); border: 1px solid var(--accent);
                  border-radius: 8px; padding: 16px; margin-top: 12px; }
  .call-side { border-left: 3px solid var(--green); padding-left: 12px; margin: 8px 0; }
  .put-side { border-left: 3px solid var(--red); padding-left: 12px; margin: 8px 0; }
  btn { background: var(--accent); color: #000; border: none; padding: 10px 24px;
       border-radius: 8px; font-weight: 700; cursor: pointer; font-size: 1em; }
  .btn:hover { opacity: 0.85; }
  .btn-outline { background: transparent; border: 1px solid var(--accent);
                color: var(--accent); }
  .ai-box { background: rgba(14,203,129,0.05); border: 1px solid var(--green);
            border-radius: 8px; padding: 16px; white-space: pre-wrap;
            font-size: 0.9em; line-height: 1.5; max-height: 400px; overflow-y: auto; }
  .status-dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%;
               margin-right: 6px; }
  .status-ok { background: var(--green); }
  .status-warn { background: var(--accent); }
  .status-off { background: var(--red); }
  #ai-output { min-height: 100px; }
  .refresh-note { color: var(--muted); font-size: 0.8em; margin-top: 8px; }
  .actions { margin-top: 12px; display: flex; gap: 8px; flex-wrap: wrap; }
</style>
</head>
<body>
<h1>GLD STRANGLE DASHBOARD v2.00</h1>
<p style="color:var(--muted)">Remote Ops — {{ data.get('timestamp', '')[:19] }} UTC</p>

<div class="grid">
  <!-- PRICES -->
  <div class="card">
    <h2>MARKET PRICES</h2>
    <div class="price-big">${{ data.get('gld_price', '---') }}</div>
    <div class="price-gold">GLD ETF</div>
    <div style="margin-top:12px">
      <div class="row"><span class="label">Gold Spot</span>
        <span class="val">${{ data.get('gold_price', '---') }}</span></div>
      <div class="row"><span class="label">Gold/GLD Ratio</span>
        <span class="val">{{ data.get('gold_gld_ratio', '---') }}</span></div>
      <div class="row"><span class="label">Today Open</span>
        <span class="val">${{ data.get('today_open', '---') }}</span></div>
    </div>
    {% set ind = data.get('indicators', {}) %}
    <div style="margin-top:12px">
      <span class="regime regime-{{ ind.get('regime', 'RANGING') }}">
        {{ ind.get('regime', 'UNKNOWN') }}
      </span>
    </div>
  </div>

  <!-- YESTERDAY + INDICATORS -->
  <div class="card">
    <h2>INDICATORS & RANGE</h2>
    {% set y = data.get('yesterday', {}) %}
    <div class="row"><span class="label">Yesterday High</span>
      <span class="val">${{ y.get('high', '---') }}</span></div>
    <div class="row"><span class="label">Yesterday Low</span>
      <span class="val">${{ y.get('low', '---') }}</span></div>
    <div class="row"><span class="label">Range</span>
      <span class="val">${{ y.get('range', '---') }} ({{ y.get('range_pct', '---') }}%)</span></div>
    <div class="row"><span class="label">ATR(14)</span>
      <span class="val">${{ ind.get('atr_14', '---') }}</span></div>
    <div class="row"><span class="label">RSI(14)</span>
      <span class="val {% if ind.get('rsi_14',50) > 60 %}bullish{% elif ind.get('rsi_14',50) < 40 %}bearish{% else %}neutral{% endif %}">
        {{ ind.get('rsi_14', '---') }}</span></div>
    <div class="row"><span class="label">ADX(14)</span>
      <span class="val">{{ ind.get('adx_14', '---') }}</span></div>
  </div>

  <!-- FIBONACCI -->
  <div class="card">
    <h2>FIBONACCI LEVELS</h2>
    {% set fib = data.get('fibonacci', {}) %}
    <div class="row"><span class="label bearish">0.0% (Support)</span>
      <span class="val">${{ fib.get('0.0', '---') }}</span></div>
    <div class="row"><span class="label">23.6%</span>
      <span class="val">${{ fib.get('23.6', '---') }}</span></div>
    <div class="row"><span class="label">38.2%</span>
      <span class="val">${{ fib.get('38.2', '---') }}</span></div>
    <div class="row"><span class="label neutral">50.0% (Mid)</span>
      <span class="val">${{ fib.get('50.0', '---') }}</span></div>
    <div class="row"><span class="label">61.8%</span>
      <span class="val">${{ fib.get('61.8', '---') }}</span></div>
    <div class="row"><span class="label bullish">100.0% (Resistance)</span>
      <span class="val">${{ fib.get('100.0', '---') }}</span></div>
    <div class="row"><span class="label bullish">127.2% (Extension)</span>
      <span class="val">${{ fib.get('127.2', '---') }}</span></div>
  </div>

  <!-- STRANGLE SETUP -->
  <div class="card">
    <h2>STRANGLE SETUP</h2>
    {% set s = data.get('strangle', {}) %}
    <div class="strangle-box">
      <div class="call-side">
        <strong class="bullish">CALLS</strong> — Strike ${{ s.get('call_strike', '---') }}<br>
        Qty: {{ s.get('call_qty', '---') }} | Est Premium: ${{ s.get('call_premium_est', '---') }}<br>
        Breakeven: ${{ s.get('call_breakeven', '---') }}
      </div>
      <div class="put-side">
        <strong class="bearish">PUTS</strong> — Strike ${{ s.get('put_strike', '---') }}<br>
        Qty: {{ s.get('put_qty', '---') }} | Est Premium: ${{ s.get('put_premium_est', '---') }}<br>
        Breakeven: ${{ s.get('put_breakeven', '---') }}
      </div>
      <div class="row" style="margin-top:8px"><span class="label">Total Cost Est</span>
        <span class="val">${{ s.get('total_cost_est', '---') }}</span></div>
      <div class="row"><span class="label">Expiry</span>
        <span class="val">{{ s.get('expiry', '---') }} ({{ s.get('dte', '---') }} DTE)</span></div>
      <div class="row"><span class="label">Lean</span>
        <span class="val bullish">{{ s.get('lean', '---') }}</span></div>
      <div class="row"><span class="label">Risk</span>
        <span class="val">{{ s.get('risk', '---') }}</span></div>
      <div class="row"><span class="label">Entry</span>
        <span class="val">{{ s.get('entry_timing', '---') }}</span></div>
    </div>
    <p class="refresh-note">{{ s.get('lean_reasoning', '') }}</p>
  </div>

  <!-- MT5 SIGNAL -->
  <div class="card">
    <h2>MT5 SIGNAL RELAY</h2>
    {% if mt5.get('status') == 'no_signal' %}
    <p><span class="status-dot status-off"></span>No signal — relay offline or no data</p>
    {% else %}
    <p><span class="status-dot status-ok"></span>Signal active
      {% if mt5.get('_source') == 'local_cache' %}(cached){% endif %}</p>
    {% for k, v in mt5.items() if k not in ('_source', 'status') %}
    <div class="row"><span class="label">{{ k }}</span><span class="val">{{ v }}</span></div>
    {% endfor %}
    {% endif %}
  </div>

  <!-- AI ANALYSIS -->
  <div class="card" style="grid-column: span 2">
    <h2>AI ANALYSIS (Dolphin3)</h2>
    <div id="ai-output" class="ai-box">Click "Get Analysis" to load AI recommendation...</div>
    <div class="actions">
      <button class="btn" onclick="getAnalysis()">Get Analysis</button>
      <button class="btn btn-outline" onclick="location.reload()">Refresh Data</button>
    </div>
  </div>
</div>

<script>
async function getAnalysis() {
  const box = document.getElementById('ai-output');
  box.textContent = 'Analyzing with Dolphin3... (may take 30-60 seconds)';
  try {
    const resp = await fetch('/api/analysis');
    const data = await resp.json();
    if (data.status === 'ok') {
      box.textContent = data.analysis;
    } else {
      box.textContent = 'Error: ' + (data.message || 'Unknown error');
    }
  } catch(e) {
    box.textContent = 'Network error: ' + e.message;
  }
}
// Auto-refresh data every 60 seconds
setInterval(() => location.reload(), 60000);
</script>
</body>
</html>
"""


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    logger.info(f"GLD Strangle Dashboard v2.00 starting on port {PORT}")
    logger.info(f"Ollama: {OLLAMA_URL} ({OLLAMA_MODEL})")
    logger.info(f"Signal relay: {DESKTOP_SIGNAL_URL}")
    app.run(host="0.0.0.0", port=PORT, debug=False)
