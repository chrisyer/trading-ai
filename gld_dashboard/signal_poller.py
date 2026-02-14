#!/usr/bin/env python3
"""
GLD SIGNAL POLLER — H100 side
══════════════════════════════
Polls the US desktop signal relay every 30-60 seconds.
Caches signals locally. Triggers alerts on signal changes.

This is the H100 half of "Option C" — the desktop runs a tiny Flask
endpoint, this poller grabs the signal and caches it for the dashboard.
"""

import os
import json
import time
import logging
import requests
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [POLLER] %(message)s")
logger = logging.getLogger("signal_poller")

# Configuration
DESKTOP_SIGNAL_URL = os.environ.get(
    "DESKTOP_SIGNAL_URL",
    "http://100.119.161.65:8098/signal/gld"
)
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", 30))  # seconds
SIGNAL_FILE = Path("/home/jbot/trading_ai/gld_dashboard/mt5_signal.json")
ALERT_WEBHOOK = os.environ.get("ALERT_WEBHOOK", "")  # Telegram/Discord webhook


def poll_signal():
    """Fetch signal from desktop relay."""
    try:
        resp = requests.get(DESKTOP_SIGNAL_URL, timeout=10)
        if resp.status_code == 200:
            return resp.json()
        else:
            logger.warning(f"Desktop relay returned HTTP {resp.status_code}")
            return None
    except requests.exceptions.ConnectionError:
        logger.debug("Desktop relay unreachable")
        return None
    except Exception as e:
        logger.error(f"Poll error: {e}")
        return None


def check_for_changes(new_signal: dict, old_signal: dict) -> list:
    """Check if signal has changed, return list of changes."""
    changes = []
    if not old_signal:
        return ["Initial signal received"]

    for key in ["direction", "entry_signal", "recommendation", "alert"]:
        old_val = old_signal.get(key)
        new_val = new_signal.get(key)
        if old_val != new_val and new_val is not None:
            changes.append(f"{key}: {old_val} -> {new_val}")

    return changes


def send_alert(message: str):
    """Send alert via configured webhook (Telegram/Discord)."""
    if not ALERT_WEBHOOK:
        logger.info(f"ALERT (no webhook): {message}")
        return

    try:
        if "discord" in ALERT_WEBHOOK:
            requests.post(ALERT_WEBHOOK, json={"content": message}, timeout=10)
        else:
            # Telegram bot API
            requests.post(ALERT_WEBHOOK, json={"text": message}, timeout=10)
    except Exception as e:
        logger.error(f"Alert send failed: {e}")


def run():
    """Main polling loop."""
    logger.info(f"Signal Poller starting — polling {DESKTOP_SIGNAL_URL} every {POLL_INTERVAL}s")

    last_signal = None
    if SIGNAL_FILE.exists():
        try:
            last_signal = json.loads(SIGNAL_FILE.read_text())
        except Exception:
            pass

    consecutive_failures = 0

    while True:
        signal = poll_signal()

        if signal:
            consecutive_failures = 0

            # Check for changes
            changes = check_for_changes(signal, last_signal)
            if changes:
                logger.info(f"Signal changed: {changes}")
                alert_msg = f"[GLD Signal] {', '.join(changes)}"
                send_alert(alert_msg)

            # Cache locally
            signal["_polled_at"] = datetime.now(timezone.utc).isoformat()
            signal["_source"] = "desktop_relay"
            SIGNAL_FILE.write_text(json.dumps(signal, indent=2))
            last_signal = signal

        else:
            consecutive_failures += 1
            if consecutive_failures == 5:
                logger.warning("5 consecutive poll failures — desktop may be offline")
                send_alert("[GLD Signal] WARNING: Desktop relay unreachable (5 failures)")
            elif consecutive_failures % 30 == 0:
                send_alert(f"[GLD Signal] Desktop relay still offline ({consecutive_failures} failures)")

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    run()
