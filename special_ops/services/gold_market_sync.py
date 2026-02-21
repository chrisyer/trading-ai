#!/usr/bin/env python3
"""
Gold Market Sync — Daily MGC/GLD price feed for the Gold Mirror system.

Closes the gap between advisory Gold Mirror signals (derived from XAUUSD spot)
and actual futures/ETF market data. Fetches MGC=F, GC=F, GLD prices and
GLD options chain via yfinance, then:

  1. Caches daily snapshots locally for historical tracking
  2. Compares Gold Mirror recommendations against real MGC/GLD prices
  3. POSTs price context to CRELLA's /gold/positions (price_context field)
  4. Sends a Telegram summary when market closes (weekdays 4:15 PM ET)

Runs as a PM2 daemon — polls every 15 min during market hours, hourly off-hours.
"""

import argparse
import json
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import requests
import yfinance as yf

log = logging.getLogger("gold-market-sync")
log.setLevel(logging.INFO)
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
log.addHandler(handler)

# --- Configuration ---
CRELLA_IP = os.environ.get("CRELLA_IP", "100.119.161.65")
GOLD_MIRROR_URL = f"http://{CRELLA_IP}:8099/gold/state"
POSITIONS_URL = f"http://{CRELLA_IP}:8099/gold/positions"

BOT_TOKEN = os.environ.get("AIIQ_BOT_TOKEN", "7956358189:AAHLEIAWRnwi6Jz9eKORPVi99eP8jbwOF4w")
CHAT_ID = os.environ.get("AIIQ_CHAT_ID", "-1003582558817")

DATA_DIR = Path(__file__).parent.parent / "data" / "gold_market"
DATA_DIR.mkdir(parents=True, exist_ok=True)
CACHE_FILE = DATA_DIR / "price_cache.json"
HISTORY_FILE = DATA_DIR / "daily_history.jsonl"

ET = timezone(timedelta(hours=-5))
MARKET_OPEN_HOUR = 9
MARKET_OPEN_MIN = 30
MARKET_CLOSE_HOUR = 16
MARKET_CLOSE_MIN = 15
FUTURES_OPEN_HOUR = 18  # CME gold futures open 6 PM ET Sunday-Friday

POLL_MARKET_HOURS = 900     # 15 min
POLL_OFF_HOURS = 3600       # 1 hour
POLL_WEEKEND = 7200         # 2 hours


def is_market_hours(now_et: datetime) -> bool:
    """US equity market hours (9:30-16:15 ET, weekdays)."""
    if now_et.weekday() >= 5:
        return False
    t = now_et.time()
    from datetime import time as dtime
    return dtime(MARKET_OPEN_HOUR, MARKET_OPEN_MIN) <= t <= dtime(MARKET_CLOSE_HOUR, MARKET_CLOSE_MIN)


def is_futures_hours(now_et: datetime) -> bool:
    """CME gold futures trade Sun 6PM - Fri 5PM ET with a daily halt 5-6PM."""
    wd = now_et.weekday()
    hour = now_et.hour
    if wd == 5:
        return False
    if wd == 6:
        return hour >= 18
    if 0 <= wd <= 4:
        if hour == 17:
            return False
        return True
    return False


def get_poll_interval(now_et: datetime) -> int:
    if is_market_hours(now_et):
        return POLL_MARKET_HOURS
    if now_et.weekday() >= 5:
        return POLL_WEEKEND
    return POLL_OFF_HOURS


def fetch_mgc() -> dict:
    """Fetch Micro Gold Futures (MGC=F) data."""
    try:
        t = yf.Ticker("MGC=F")
        hist = t.history(period="5d")
        if hist.empty:
            return {"ok": False, "error": "no data"}

        latest = hist.iloc[-1]
        prev = hist.iloc[-2] if len(hist) > 1 else latest

        return {
            "ok": True,
            "symbol": "MGC=F",
            "price": round(float(latest["Close"]), 2),
            "prev_close": round(float(prev["Close"]), 2),
            "change": round(float(latest["Close"] - prev["Close"]), 2),
            "change_pct": round(float((latest["Close"] - prev["Close"]) / prev["Close"] * 100), 3),
            "volume": int(latest.get("Volume", 0)),
            "high": round(float(latest["High"]), 2),
            "low": round(float(latest["Low"]), 2),
            "date": str(latest.name.date()),
        }
    except Exception as e:
        log.warning(f"MGC=F fetch failed: {e}")
        return {"ok": False, "error": str(e)}


def fetch_gc() -> dict:
    """Fetch full-size Gold Futures (GC=F) for reference."""
    try:
        t = yf.Ticker("GC=F")
        hist = t.history(period="5d")
        if hist.empty:
            return {"ok": False, "error": "no data"}
        latest = hist.iloc[-1]
        prev = hist.iloc[-2] if len(hist) > 1 else latest
        return {
            "ok": True,
            "symbol": "GC=F",
            "price": round(float(latest["Close"]), 2),
            "prev_close": round(float(prev["Close"]), 2),
            "change_pct": round(float((latest["Close"] - prev["Close"]) / prev["Close"] * 100), 3),
            "volume": int(latest.get("Volume", 0)),
            "date": str(latest.name.date()),
        }
    except Exception as e:
        log.warning(f"GC=F fetch failed: {e}")
        return {"ok": False, "error": str(e)}


def fetch_gld() -> dict:
    """Fetch GLD ETF price + nearest options chain snapshot."""
    try:
        t = yf.Ticker("GLD")
        hist = t.history(period="5d")
        if hist.empty:
            return {"ok": False, "error": "no data"}

        latest = hist.iloc[-1]
        prev = hist.iloc[-2] if len(hist) > 1 else latest
        gld_price = float(latest["Close"])

        result = {
            "ok": True,
            "symbol": "GLD",
            "price": round(gld_price, 2),
            "prev_close": round(float(prev["Close"]), 2),
            "change": round(gld_price - float(prev["Close"]), 2),
            "change_pct": round((gld_price - float(prev["Close"])) / float(prev["Close"]) * 100, 3),
            "volume": int(latest.get("Volume", 0)),
            "date": str(latest.name.date()),
        }

        # Grab nearest options chain for context
        try:
            expiries = t.options
            if expiries:
                nearest = expiries[0]
                chain = t.option_chain(nearest)
                atm_strike = min(chain.calls["strike"], key=lambda x: abs(x - gld_price))

                atm_call = chain.calls[chain.calls["strike"] == atm_strike].iloc[0]
                atm_put = chain.puts[chain.puts["strike"] == atm_strike].iloc[0]

                result["options"] = {
                    "expiry": nearest,
                    "atm_strike": float(atm_strike),
                    "atm_call_bid": float(atm_call.get("bid", 0)),
                    "atm_call_ask": float(atm_call.get("ask", 0)),
                    "atm_call_iv": round(float(atm_call.get("impliedVolatility", 0)) * 100, 1),
                    "atm_put_bid": float(atm_put.get("bid", 0)),
                    "atm_put_ask": float(atm_put.get("ask", 0)),
                    "atm_put_iv": round(float(atm_put.get("impliedVolatility", 0)) * 100, 1),
                    "strangle_cost": round(
                        float(atm_call.get("ask", 0)) + float(atm_put.get("ask", 0)), 2
                    ),
                }
        except Exception as e:
            log.debug(f"GLD options chain error: {e}")

        return result
    except Exception as e:
        log.warning(f"GLD fetch failed: {e}")
        return {"ok": False, "error": str(e)}


def fetch_gold_mirror() -> Optional[dict]:
    try:
        r = requests.get(GOLD_MIRROR_URL, timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.warning(f"Gold Mirror unreachable: {e}")
        return None


def compare_signals(mirror: dict, mgc: dict, gld: dict) -> dict:
    """Compare Gold Mirror advisory signals against real market data."""
    result = {"ts": datetime.now(timezone.utc).isoformat()}

    rec = mirror.get("recommendation", {})
    xauusd_spot = mirror.get("price", 0)

    # XAUUSD spot vs MGC futures basis
    if mgc.get("ok") and xauusd_spot:
        mgc_price = mgc["price"]
        basis = mgc_price - xauusd_spot
        basis_pct = (basis / xauusd_spot * 100) if xauusd_spot else 0
        result["mgc_vs_spot"] = {
            "xauusd_spot": xauusd_spot,
            "mgc_price": mgc_price,
            "basis": round(basis, 2),
            "basis_pct": round(basis_pct, 3),
            "note": "positive = futures premium (contango)"
        }

    # MGC recommendation context
    mgc_rec = rec.get("mgc", {})
    if mgc_rec and mgc.get("ok"):
        result["mgc_signal"] = {
            "mirror_action": mgc_rec.get("action"),
            "mirror_contracts": mgc_rec.get("contracts", 0),
            "mirror_reason": mgc_rec.get("reason"),
            "actual_mgc_price": mgc["price"],
            "mgc_daily_change": mgc.get("change_pct", 0),
            "mgc_volume": mgc.get("volume", 0),
        }

    # GLD recommendation context
    gld_rec = rec.get("gld", {})
    if gld_rec and gld.get("ok"):
        result["gld_signal"] = {
            "mirror_action": gld_rec.get("action"),
            "mirror_structure": gld_rec.get("structure"),
            "mirror_reason": gld_rec.get("reason"),
            "actual_gld_price": gld["price"],
            "gld_daily_change": gld.get("change_pct", 0),
            "gld_volume": gld.get("volume", 0),
        }
        if "options" in gld:
            result["gld_signal"]["atm_iv_call"] = gld["options"].get("atm_call_iv")
            result["gld_signal"]["atm_iv_put"] = gld["options"].get("atm_put_iv")
            result["gld_signal"]["strangle_cost"] = gld["options"].get("strangle_cost")

    return result


def save_cache(mgc: dict, gc: dict, gld: dict, mirror: Optional[dict], comparison: dict):
    """Write latest snapshot to cache file."""
    data = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "mgc": mgc,
        "gc": gc,
        "gld": gld,
        "mirror_state": {
            "price": mirror.get("price") if mirror else None,
            "regime": mirror.get("regime") if mirror else None,
            "recommendation": mirror.get("recommendation") if mirror else None,
        },
        "comparison": comparison,
    }
    CACHE_FILE.write_text(json.dumps(data, indent=2))
    log.info(f"Cache updated: MGC=${mgc.get('price','?')} GLD=${gld.get('price','?')}")


def append_history(mgc: dict, gc: dict, gld: dict, comparison: dict):
    """Append daily snapshot to JSONL history file."""
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "mgc_price": mgc.get("price") if mgc.get("ok") else None,
        "gc_price": gc.get("price") if gc.get("ok") else None,
        "gld_price": gld.get("price") if gld.get("ok") else None,
        "gld_volume": gld.get("volume") if gld.get("ok") else None,
        "basis": comparison.get("mgc_vs_spot", {}).get("basis"),
        "mgc_action": comparison.get("mgc_signal", {}).get("mirror_action"),
        "gld_action": comparison.get("gld_signal", {}).get("mirror_action"),
    }
    if gld.get("options"):
        entry["gld_atm_iv"] = gld["options"].get("atm_call_iv")
        entry["strangle_cost"] = gld["options"].get("strangle_cost")

    with open(HISTORY_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")


def post_price_context(mgc: dict, gc: dict, gld: dict):
    """POST current market prices to CRELLA's /gold/positions as price_context.

    This doesn't change position data — it adds a price_context sidecar
    that the Gold Mirror can use for display and calculations.
    """
    if not mgc.get("ok") and not gld.get("ok"):
        return

    payload = {
        "price_context": {
            "mgc_price": mgc.get("price") if mgc.get("ok") else None,
            "gc_price": gc.get("price") if gc.get("ok") else None,
            "gld_price": gld.get("price") if gld.get("ok") else None,
            "gld_options": gld.get("options") if gld.get("ok") else None,
            "source": "quinn_yfinance",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    }

    try:
        r = requests.post(POSITIONS_URL, json=payload, timeout=10)
        if r.ok:
            log.info("Price context posted to CRELLA")
        else:
            log.debug(f"Price context POST returned {r.status_code}")
    except Exception as e:
        log.debug(f"Price context POST failed: {e}")


def build_daily_summary(mgc: dict, gc: dict, gld: dict, comparison: dict) -> str:
    """Build a Telegram daily market summary."""
    now = datetime.now(ET)
    lines = [f"<b>📊 Gold Market Daily — {now.strftime('%b %d, %Y')}</b>", ""]

    # MGC Futures
    if mgc.get("ok"):
        arrow = "🟢" if mgc["change_pct"] >= 0 else "🔴"
        lines.append(f"<b>MGC (Micro Gold Futures)</b>")
        lines.append(f"  {arrow} ${mgc['price']:,.2f} ({mgc['change_pct']:+.2f}%)")
        lines.append(f"  Range: ${mgc['low']:,.2f} — ${mgc['high']:,.2f}")
        lines.append(f"  Volume: {mgc['volume']:,}")
    else:
        lines.append("MGC: unavailable")

    lines.append("")

    # GLD ETF
    if gld.get("ok"):
        arrow = "🟢" if gld["change_pct"] >= 0 else "🔴"
        lines.append(f"<b>GLD (Gold ETF)</b>")
        lines.append(f"  {arrow} ${gld['price']:.2f} ({gld['change_pct']:+.2f}%)")
        lines.append(f"  Volume: {gld['volume']:,}")
        if "options" in gld:
            opts = gld["options"]
            lines.append(f"  ATM IV: {opts['atm_call_iv']:.0f}%C / {opts['atm_put_iv']:.0f}%P")
            lines.append(f"  Strangle cost (ATM {opts['expiry']}): ${opts['strangle_cost']:.2f}")
    else:
        lines.append("GLD: unavailable")

    # Basis
    basis_info = comparison.get("mgc_vs_spot", {})
    if basis_info:
        lines.append("")
        lines.append(f"<b>Basis (MGC − XAUUSD spot)</b>")
        lines.append(f"  ${basis_info['basis']:+.2f} ({basis_info['basis_pct']:+.3f}%)")

    # Mirror signal context
    mgc_sig = comparison.get("mgc_signal", {})
    gld_sig = comparison.get("gld_signal", {})
    if mgc_sig or gld_sig:
        lines.append("")
        lines.append("<b>Gold Mirror Signals</b>")
        if mgc_sig:
            lines.append(f"  MGC: {mgc_sig.get('mirror_action','—')} ({mgc_sig.get('mirror_reason','—')})")
        if gld_sig:
            lines.append(f"  GLD: {gld_sig.get('mirror_action','—')} ({gld_sig.get('mirror_reason','—')})")

    return "\n".join(lines)


def send_telegram(text: str) -> bool:
    if not BOT_TOKEN or not CHAT_ID:
        log.warning("Telegram not configured")
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=15,
        )
        return r.ok
    except Exception as e:
        log.warning(f"Telegram send failed: {e}")
        return False


def run_sync(send_summary: bool = False) -> dict:
    """Execute one full sync cycle."""
    log.info("--- Sync cycle start ---")

    mgc = fetch_mgc()
    gc = fetch_gc()
    gld = fetch_gld()
    mirror = fetch_gold_mirror()

    comparison = {}
    if mirror:
        comparison = compare_signals(mirror, mgc, gld)

    save_cache(mgc, gc, gld, mirror, comparison)
    append_history(mgc, gc, gld, comparison)
    post_price_context(mgc, gc, gld)

    if send_summary and (mgc.get("ok") or gld.get("ok")):
        summary = build_daily_summary(mgc, gc, gld, comparison)
        if send_telegram(summary):
            log.info("Daily summary sent to Telegram")
        else:
            log.warning("Daily summary Telegram send failed")

    log.info("--- Sync cycle complete ---")
    return {"mgc": mgc, "gc": gc, "gld": gld, "comparison": comparison}


def daemon_loop():
    """Main daemon loop — adaptive polling based on market hours."""
    log.info("Gold Market Sync daemon starting")
    log.info(f"  CRELLA: {CRELLA_IP}")
    log.info(f"  Data dir: {DATA_DIR}")
    log.info(f"  Telegram: {CHAT_ID}")

    last_daily_summary_date = None
    running = True

    def handle_stop(sig, frame):
        nonlocal running
        log.info(f"Received signal {sig}, shutting down")
        running = False

    signal.signal(signal.SIGTERM, handle_stop)
    signal.signal(signal.SIGINT, handle_stop)

    while running:
        now_et = datetime.now(ET)
        now_date = now_et.date()

        # Send daily summary once after market close (4:20 PM ET, weekdays)
        send_summary = False
        if (now_et.weekday() < 5
                and now_et.hour == 16 and now_et.minute >= 20
                and last_daily_summary_date != now_date):
            send_summary = True
            last_daily_summary_date = now_date

        try:
            run_sync(send_summary=send_summary)
        except Exception as e:
            log.error(f"Sync cycle error: {e}", exc_info=True)

        interval = get_poll_interval(now_et)
        log.info(f"Next poll in {interval}s ({'market' if is_market_hours(now_et) else 'off-hours'})")

        # Sleep in small increments for clean shutdown
        slept = 0
        while slept < interval and running:
            time.sleep(min(30, interval - slept))
            slept += 30


def main():
    parser = argparse.ArgumentParser(description="Gold Market Sync — MGC/GLD daily price feed")
    parser.add_argument("--once", action="store_true", help="Run one sync cycle and exit")
    parser.add_argument("--summary", action="store_true", help="Force send daily summary to Telegram")
    parser.add_argument("--daemon", action="store_true", help="Run as persistent daemon")
    args = parser.parse_args()

    if args.once or args.summary:
        result = run_sync(send_summary=args.summary)
        mgc = result["mgc"]
        gld = result["gld"]
        comp = result["comparison"]

        print(f"\nMGC: {'$' + str(mgc['price']) if mgc.get('ok') else 'unavailable'}")
        print(f"GLD: {'$' + str(gld['price']) if gld.get('ok') else 'unavailable'}")

        basis = comp.get("mgc_vs_spot", {})
        if basis:
            print(f"Basis (MGC-XAUUSD): ${basis['basis']:+.2f} ({basis['basis_pct']:+.3f}%)")

        mgc_sig = comp.get("mgc_signal", {})
        if mgc_sig:
            print(f"Mirror MGC: {mgc_sig['mirror_action']} ({mgc_sig['mirror_reason']})")

        gld_sig = comp.get("gld_signal", {})
        if gld_sig:
            print(f"Mirror GLD: {gld_sig['mirror_action']} ({gld_sig['mirror_reason']})")

    elif args.daemon:
        daemon_loop()
    else:
        parser.print_help()
        print("\nExamples:")
        print("  python gold_market_sync.py --once        # Single fetch, print results")
        print("  python gold_market_sync.py --summary     # Fetch + send Telegram summary")
        print("  python gold_market_sync.py --daemon      # Run as persistent service")


if __name__ == "__main__":
    main()
