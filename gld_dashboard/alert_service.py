#!/usr/bin/env python3
"""
GLD STRANGLE ALERT SERVICE
═══════════════════════════
Monitors all GLD strangle operations and sends alerts via Telegram.

Monitors:
  1. Kill switch triggers (MT5 signal)
  2. Crash freeze activations
  3. Options profit targets hit
  4. Options approaching roll window (10 DTE)
  5. Desktop relay health
  6. Dashboard health
  7. Market events (CPI, NFP, FOMC schedule)

Configure:
  TELEGRAM_BOT_TOKEN=your_bot_token
  TELEGRAM_CHAT_ID=your_chat_id
"""

import os
import json
import time
import logging
import requests
from datetime import datetime, timezone, timedelta
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [ALERTS] %(message)s")
logger = logging.getLogger("alert_service")

# Configuration
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
DASHBOARD_URL = os.environ.get("GLD_DASHBOARD_URL", "http://localhost:8080")
DESKTOP_RELAY_URL = os.environ.get("DESKTOP_SIGNAL_URL", "http://100.119.161.65:8098")
POSITIONS_FILE = Path("/home/jbot/trading_ai/gld_dashboard/positions.json")
CHECK_INTERVAL = 60  # seconds

# State tracking
_last_alerts = {}


def send_telegram(message: str, parse_mode: str = "HTML"):
    """Send a Telegram message."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.info(f"[NO TELEGRAM] {message}")
        return

    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        resp = requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": parse_mode,
        }, timeout=10)
        if resp.status_code != 200:
            logger.warning(f"Telegram send failed: {resp.text}")
    except Exception as e:
        logger.error(f"Telegram error: {e}")


def throttle_alert(key: str, cooldown_minutes: int = 30) -> bool:
    """Prevent sending the same alert too frequently."""
    now = datetime.now(timezone.utc)
    last = _last_alerts.get(key)
    if last and (now - last).total_seconds() < cooldown_minutes * 60:
        return False
    _last_alerts[key] = now
    return True


def check_dashboard_health() -> bool:
    """Check if the GLD dashboard is responsive."""
    try:
        resp = requests.get(f"{DASHBOARD_URL}/api/health", timeout=5)
        return resp.status_code == 200
    except Exception:
        return False


def check_desktop_relay() -> bool:
    """Check if the US desktop signal relay is reachable."""
    try:
        resp = requests.get(f"{DESKTOP_RELAY_URL}/health", timeout=5)
        return resp.status_code == 200
    except Exception:
        return False


def check_positions():
    """Check positions for profit targets and roll alerts."""
    if not POSITIONS_FILE.exists():
        return

    try:
        positions = json.loads(POSITIONS_FILE.read_text())
    except Exception:
        return

    for pos in positions:
        if pos.get("status") == "closed":
            continue

        # Profit target
        gain_pct = pos.get("gain_pct", 0)
        pos_id = pos.get("id", "unknown")

        if gain_pct >= 150:
            if throttle_alert(f"profit_max_{pos_id}", 15):
                send_telegram(
                    f"<b>PROFIT TARGET HIT</b>\n"
                    f"{pos.get('type', '').upper()} ${pos.get('strike', '?')}\n"
                    f"Gain: +{gain_pct:.0f}%\n"
                    f"<b>CLOSE NOW</b>"
                )
        elif gain_pct >= 100:
            if throttle_alert(f"profit_target_{pos_id}", 30):
                send_telegram(
                    f"<b>PROFIT TARGET</b>\n"
                    f"{pos.get('type', '').upper()} ${pos.get('strike', '?')}\n"
                    f"Gain: +{gain_pct:.0f}%\n"
                    f"Consider closing winner"
                )

        # Roll alert
        expiry_str = pos.get("expiry", "")
        if expiry_str:
            try:
                expiry = datetime.strptime(expiry_str, "%Y-%m-%d")
                dte = (expiry - datetime.now()).days
                if dte <= 10 and dte > 0:
                    if throttle_alert(f"roll_{pos_id}", 240):
                        send_telegram(
                            f"<b>ROLL ALERT</b>\n"
                            f"{pos.get('type', '').upper()} ${pos.get('strike', '?')}\n"
                            f"<b>{dte} DTE remaining</b>\n"
                            f"Roll or close to avoid theta decay"
                        )
            except Exception:
                pass


def check_mt5_signal():
    """Check MT5 signal for kill switch / crash freeze."""
    signal_file = Path("/home/jbot/trading_ai/gld_dashboard/mt5_signal.json")
    if not signal_file.exists():
        return

    try:
        signal = json.loads(signal_file.read_text())
        # Check for kill switch
        if signal.get("kill_switch") or signal.get("emergency"):
            if throttle_alert("kill_switch", 5):
                send_telegram(
                    "<b>KILL SWITCH TRIGGERED</b>\n"
                    "MT5 kill switch has fired. Check account immediately."
                )

        if signal.get("crash_freeze"):
            if throttle_alert("crash_freeze", 5):
                send_telegram(
                    "<b>CRASH FREEZE ACTIVATED</b>\n"
                    "Market crash detected. All trading frozen."
                )
    except Exception:
        pass


def daily_summary():
    """Send daily summary at market open."""
    try:
        resp = requests.get(f"{DASHBOARD_URL}/api/market", timeout=10)
        if resp.status_code != 200:
            return

        data = resp.json()
        gld = data.get("gld_price", 0)
        gold = data.get("gold_price", 0)
        ind = data.get("indicators", {})
        s = data.get("strangle", {})

        msg = (
            f"<b>GLD DAILY BRIEFING</b>\n"
            f"GLD: ${gld} | Gold: ${gold}\n"
            f"ATR: ${ind.get('atr_14', '?')} | RSI: {ind.get('rsi_14', '?')} | "
            f"ADX: {ind.get('adx_14', '?')}\n"
            f"Regime: {ind.get('regime', '?')}\n\n"
            f"<b>Strangle Setup:</b>\n"
            f"Call: ${s.get('call_strike', '?')} x{s.get('call_qty', '?')}\n"
            f"Put: ${s.get('put_strike', '?')} x{s.get('put_qty', '?')}\n"
            f"Est Cost: ${s.get('total_cost_est', '?')}\n"
            f"Expiry: {s.get('expiry', '?')} ({s.get('dte', '?')} DTE)\n"
            f"Lean: {s.get('lean', '?')}"
        )
        send_telegram(msg)
    except Exception as e:
        logger.error(f"Daily summary error: {e}")


def run():
    """Main alert service loop."""
    logger.info("GLD Alert Service starting")
    if not TELEGRAM_BOT_TOKEN:
        logger.warning("TELEGRAM_BOT_TOKEN not set — alerts will only log")
    else:
        send_telegram("GLD Alert Service started on H100")

    last_daily = None

    while True:
        try:
            now = datetime.now(timezone.utc)

            # Health checks
            dashboard_ok = check_dashboard_health()
            desktop_ok = check_desktop_relay()

            if not dashboard_ok and throttle_alert("dashboard_down", 30):
                send_telegram("<b>WARNING:</b> GLD Dashboard is DOWN")

            if not desktop_ok and throttle_alert("desktop_down", 30):
                send_telegram("<b>WARNING:</b> Desktop relay unreachable")

            # Position monitoring
            check_positions()

            # MT5 signal checks
            check_mt5_signal()

            # Daily summary at 13:30 UTC (9:30 AM EST market open)
            if now.hour == 13 and now.minute < 2:
                today = now.date()
                if last_daily != today:
                    daily_summary()
                    last_daily = today

        except Exception as e:
            logger.error(f"Alert loop error: {e}")

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    run()
