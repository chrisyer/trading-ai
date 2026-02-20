#!/usr/bin/env python3
"""
OPS MONITOR — Unified System Health + Daily Recap for Telegram
Runs on H100 (QUINN001). Monitors CRELLA + local ML services.

Usage:
  python3 ops_monitor.py --test     Send a test alert to verify Telegram
  python3 ops_monitor.py --recap    Send daily recap immediately
  python3 ops_monitor.py --daemon   Run continuously (PM2 mode)

PM2:
  pm2 start ops_monitor.py --name ops-monitor --interpreter python3 -- --daemon
"""

import os
import sys
import json
import time
import signal
import logging
import argparse
import requests
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, Dict, List, Tuple

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [OPS-MON] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ─── Configuration ───────────────────────────────────────────────────────────

BOT_TOKEN = os.environ.get(
    "QUINN_TELEGRAM_BOT_TOKEN",
    os.environ.get("TELEGRAM_BOT_TOKEN", "8250652030:AAFd4x8NsTfdaB3O67lUnMhotT2XY61600s"),
)
CHAT_ID = os.environ.get(
    "QUINN_TELEGRAM_CHAT_ID",
    os.environ.get("TELEGRAM_CHAT_ID", "6776619257"),
)

CRELLA_IP = os.environ.get("CRELLA_IP", "100.91.17.86")
CRELLA_PORT = os.environ.get("CRELLA_PORT", "8097")
CRELLA_OPS_URL = f"http://{CRELLA_IP}:{CRELLA_PORT}/ops/health"

ML_SERVICES = {
    "Direction Predictor": "http://localhost:8040/health",
    "RL Position Sizer": "http://localhost:8041/health",
    "LoRA Governor": "http://localhost:8043/health",
}

POLL_INTERVAL = int(os.environ.get("OPS_POLL_SECONDS", "300"))  # 5 minutes
ALERT_COOLDOWN = 1800  # 30 minutes between repeated alerts for same issue
RECAP_HOUR_UTC = 13  # 8 AM EST = 13:00 UTC
EST = timezone(timedelta(hours=-5))

STATE_FILE = Path(__file__).parent / "ops_monitor_state.json"

_running = True


def _signal_handler(sig, frame):
    global _running
    log.info("Shutdown signal received")
    _running = False


signal.signal(signal.SIGINT, _signal_handler)
signal.signal(signal.SIGTERM, _signal_handler)


# ─── State Management ────────────────────────────────────────────────────────

def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {"last_alerts": {}, "last_recap_date": "", "last_crella_data": {}}


def save_state(state: dict):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=2, default=str)
    except Exception as e:
        log.warning(f"Failed to save state: {e}")


# ─── Telegram ────────────────────────────────────────────────────────────────

def send_telegram(text: str, parse_mode: str = "HTML") -> bool:
    if not BOT_TOKEN or not CHAT_ID:
        log.warning("Telegram not configured")
        return False

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        resp = requests.post(url, json={
            "chat_id": CHAT_ID,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }, timeout=15)
        if resp.status_code == 200:
            log.info(f"Telegram sent ({len(text)} chars)")
            return True
        else:
            log.error(f"Telegram error {resp.status_code}: {resp.text[:200]}")
            return False
    except Exception as e:
        log.error(f"Telegram send failed: {e}")
        return False


def should_alert(key: str, state: dict) -> bool:
    """Check if enough time has passed since last alert for this key."""
    last_alerts = state.get("last_alerts", {})
    last_time = last_alerts.get(key)
    if not last_time:
        return True
    try:
        last_dt = datetime.fromisoformat(last_time)
        elapsed = (datetime.now(timezone.utc) - last_dt).total_seconds()
        return elapsed >= ALERT_COOLDOWN
    except Exception:
        return True


def mark_alerted(key: str, state: dict):
    if "last_alerts" not in state:
        state["last_alerts"] = {}
    state["last_alerts"][key] = datetime.now(timezone.utc).isoformat()


def clear_alert(key: str, state: dict):
    state.get("last_alerts", {}).pop(key, None)


# ─── Health Checks ───────────────────────────────────────────────────────────

def check_crella() -> Tuple[bool, Optional[dict]]:
    """Poll CRELLA /ops/health endpoint."""
    try:
        resp = requests.get(CRELLA_OPS_URL, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        return True, data
    except Exception as e:
        log.debug(f"CRELLA health check failed: {e}")
        return False, None


def check_ml_services() -> Dict[str, dict]:
    """Check all local ML service health endpoints."""
    results = {}
    for name, url in ML_SERVICES.items():
        try:
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            results[name] = {
                "status": data.get("status", "unknown"),
                "healthy": data.get("status") in ("loaded", "ok", "healthy"),
                "details": data,
            }
        except Exception as e:
            results[name] = {
                "status": "unreachable",
                "healthy": False,
                "error": str(e),
            }
    return results


# ─── Alert Logic ─────────────────────────────────────────────────────────────

def process_alerts(crella_ok: bool, crella_data: Optional[dict],
                   ml_results: Dict[str, dict], state: dict) -> List[str]:
    """Generate alerts for any issues detected. Returns list of alert messages sent."""
    alerts_sent = []
    now_str = datetime.now(EST).strftime("%I:%M %p EST")

    # CRELLA unreachable
    if not crella_ok:
        key = "crella_unreachable"
        if should_alert(key, state):
            msg = (
                f"🔴 <b>CRELLA UNREACHABLE</b>\n"
                f"<code>{CRELLA_OPS_URL}</code>\n"
                f"Time: {now_str}\n\n"
                f"Desktop may be offline or truth server crashed.\n"
                f"Check Tailscale connectivity and PM2 on desktop."
            )
            if send_telegram(msg):
                mark_alerted(key, state)
                alerts_sent.append(key)
    else:
        clear_alert("crella_unreachable", state)

    # CRELLA process issues
    if crella_ok and crella_data:
        processes = crella_data.get("processes", {})
        for proc_name, proc_info in processes.items():
            if isinstance(proc_info, dict) and not proc_info.get("running", True):
                key = f"crella_proc_{proc_name}"
                if should_alert(key, state):
                    msg = (
                        f"⚠️ <b>CRELLA Process Down: {proc_name}</b>\n"
                        f"Time: {now_str}\n"
                        f"Status: {proc_info.get('status', 'unknown')}\n\n"
                        f"Run on desktop: <code>pm2 restart {proc_name}</code>"
                    )
                    if send_telegram(msg):
                        mark_alerted(key, state)
                        alerts_sent.append(key)
            else:
                clear_alert(f"crella_proc_{proc_name}", state)

        # Kill switch / crash freeze
        kill_switch = crella_data.get("kill_switch", {})
        if isinstance(kill_switch, dict) and kill_switch.get("triggered"):
            key = "kill_switch_triggered"
            if should_alert(key, state):
                msg = (
                    f"🚨🚨 <b>KILL SWITCH TRIGGERED</b> 🚨🚨\n"
                    f"Time: {now_str}\n"
                    f"Reason: {kill_switch.get('reason', 'unknown')}\n"
                    f"Equity DD: {kill_switch.get('drawdown', '?')}%\n\n"
                    f"<b>ALL POSITIONS BEING CLOSED</b>\n"
                    f"DO NOT override. Assess next market day."
                )
                if send_telegram(msg):
                    mark_alerted(key, state)
                    alerts_sent.append(key)

        # Equity check
        equity = crella_data.get("equity", 0)
        prev_equity = state.get("last_crella_data", {}).get("equity", 0)
        if prev_equity > 0 and equity > 0:
            dd_pct = (prev_equity - equity) / prev_equity * 100
            if dd_pct > 3.0:
                key = "equity_drawdown"
                if should_alert(key, state):
                    msg = (
                        f"🟡 <b>Equity Drawdown Alert</b>\n"
                        f"Current: ${equity:,.0f}\n"
                        f"Previous: ${prev_equity:,.0f}\n"
                        f"Drawdown: {dd_pct:.1f}%\n"
                        f"Time: {now_str}"
                    )
                    if send_telegram(msg):
                        mark_alerted(key, state)
                        alerts_sent.append(key)

    # ML service issues
    for name, result in ml_results.items():
        key = f"ml_{name.lower().replace(' ', '_')}"
        if not result["healthy"]:
            if should_alert(key, state):
                msg = (
                    f"🟠 <b>ML Service Down: {name}</b>\n"
                    f"Status: {result['status']}\n"
                    f"Time: {now_str}\n"
                    f"Error: {result.get('error', 'N/A')[:100]}\n\n"
                    f"Check: <code>pm2 logs ml-{name.lower().replace(' ', '-')}</code>"
                )
                if send_telegram(msg):
                    mark_alerted(key, state)
                    alerts_sent.append(key)
        else:
            clear_alert(key, state)

    return alerts_sent


# ─── Daily Recap ─────────────────────────────────────────────────────────────

def format_status_emoji(healthy: bool) -> str:
    return "✅" if healthy else "❌"


def build_daily_recap(crella_ok: bool, crella_data: Optional[dict],
                      ml_results: Dict[str, dict]) -> str:
    """Build the daily 8 AM recap message."""
    now = datetime.now(EST)
    date_str = now.strftime("%A, %B %d %Y")
    time_str = now.strftime("%I:%M %p EST")

    lines = [
        f"📊 <b>DAILY OPS RECAP — {date_str}</b>",
        f"<i>Generated at {time_str}</i>",
        "",
    ]

    # CRELLA status
    lines.append(f"<b>━━━ CRELLA (Desktop) ━━━</b>")
    if crella_ok and crella_data:
        equity = crella_data.get("equity", 0)
        balance = crella_data.get("balance", 0)
        pnl = crella_data.get("floating_pnl", crella_data.get("pnl", 0))
        positions = crella_data.get("open_positions", crella_data.get("positions", 0))
        defcon = crella_data.get("defcon", {})
        defcon_level = defcon.get("level", "?") if isinstance(defcon, dict) else defcon

        lines.append(f"  Status: {format_status_emoji(True)} Online")
        lines.append(f"  Equity: <b>${equity:,.0f}</b>")
        if balance:
            lines.append(f"  Balance: ${balance:,.0f}")
        lines.append(f"  Floating P&L: <b>${pnl:,.0f}</b>")
        lines.append(f"  Open Positions: {positions}")
        lines.append(f"  DEFCON: {defcon_level}")

        # Process status
        processes = crella_data.get("processes", {})
        if processes:
            up = sum(1 for p in processes.values()
                     if isinstance(p, dict) and p.get("running", p.get("status") == "online"))
            total = len(processes)
            lines.append(f"  Processes: {up}/{total} running")

        # Bridge state
        bridge = crella_data.get("bridge", crella_data.get("bridge_control", {}))
        if isinstance(bridge, dict) and bridge:
            disable = bridge.get("disable_new_entries", False)
            dca = bridge.get("dca_step_multiplier", bridge.get("dca_mult", 1.0))
            cap = bridge.get("max_layers_cap", bridge.get("layer_cap", 3))
            lines.append(f"  Bridge: entries={'BLOCKED' if disable else 'OK'}, "
                         f"DCA={dca:.1f}x, layers≤{cap}")

        # Ghost signal
        ghost = crella_data.get("ghost", crella_data.get("ghost_signal", {}))
        if isinstance(ghost, dict) and ghost:
            action = ghost.get("action", ghost.get("direction", "?"))
            confidence = ghost.get("confidence", "?")
            lines.append(f"  Ghost: {action} ({confidence}% conf)")

        # MT5 file freshness
        mt5 = crella_data.get("mt5_files", crella_data.get("file_freshness", {}))
        if isinstance(mt5, dict) and mt5:
            stale_count = sum(1 for v in mt5.values()
                              if isinstance(v, dict) and v.get("stale", False))
            if stale_count > 0:
                lines.append(f"  ⚠️ {stale_count} stale MT5 file(s)")
            else:
                lines.append(f"  MT5 Files: all fresh")
    else:
        lines.append(f"  Status: {format_status_emoji(False)} <b>UNREACHABLE</b>")
        lines.append(f"  URL: {CRELLA_OPS_URL}")

    lines.append("")

    # ML Services
    lines.append(f"<b>━━━ ML SERVICES (H100) ━━━</b>")
    for name, result in ml_results.items():
        status_emoji = format_status_emoji(result["healthy"])
        status_text = result["status"]
        details = result.get("details", {})

        extra = ""
        if result["healthy"]:
            meta = details.get("model_meta", {})
            if "best_val_acc" in meta:
                extra = f" (acc={meta['best_val_acc']:.1%})"
            elif "n_baskets" in meta:
                extra = f" ({meta['n_baskets']} baskets)"
            elif "n_examples" in meta:
                extra = f" ({meta['n_examples']} examples)"

        lines.append(f"  {status_emoji} {name}: {status_text}{extra}")

    lines.append("")

    # Trading posture summary
    lines.append(f"<b>━━━ POSTURE ━━━</b>")
    if crella_ok and crella_data:
        if crella_data.get("kill_switch", {}).get("triggered"):
            lines.append("  🚨 KILL SWITCH ACTIVE — All trading halted")
        elif crella_data.get("bridge", {}).get("disable_new_entries"):
            lines.append("  ⚠️ New entries DISABLED — Monitor only")
        else:
            all_ml_ok = all(r["healthy"] for r in ml_results.values())
            if all_ml_ok:
                lines.append("  🟢 Full operational — ML + Rules active")
            else:
                lines.append("  🟡 Partial — Some ML services down, rule-based fallback active")
    else:
        lines.append("  🔴 CRELLA offline — Cannot determine posture")

    lines.append("")
    lines.append("<i>Next recap at 8:00 AM EST</i>")

    return "\n".join(lines)


def should_send_recap(state: dict) -> bool:
    """Check if we should send the daily recap right now."""
    now = datetime.now(timezone.utc)
    if now.weekday() >= 5:  # Skip weekends
        return False

    last_recap_date = state.get("last_recap_date", "")
    today_str = now.strftime("%Y-%m-%d")
    if last_recap_date == today_str:
        return False

    # Send between 13:00-13:10 UTC (8:00-8:10 AM EST)
    if now.hour == RECAP_HOUR_UTC and now.minute < 10:
        return True

    return False


# ─── Main Loop ───────────────────────────────────────────────────────────────

def run_check(state: dict) -> dict:
    """Run one cycle of health checks and alerts."""
    crella_ok, crella_data = check_crella()
    ml_results = check_ml_services()

    if crella_ok:
        log.info(f"CRELLA: OK | "
                 f"Equity: ${crella_data.get('equity', '?'):,} | "
                 f"PnL: ${crella_data.get('floating_pnl', crella_data.get('pnl', '?'))}")
    else:
        log.warning("CRELLA: UNREACHABLE")

    ml_up = sum(1 for r in ml_results.values() if r["healthy"])
    log.info(f"ML Services: {ml_up}/{len(ml_results)} healthy")

    alerts = process_alerts(crella_ok, crella_data, ml_results, state)
    if alerts:
        log.info(f"Alerts sent: {alerts}")

    if crella_ok and crella_data:
        state["last_crella_data"] = {
            "equity": crella_data.get("equity", 0),
            "balance": crella_data.get("balance", 0),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    if should_send_recap(state):
        log.info("Sending daily recap...")
        recap = build_daily_recap(crella_ok, crella_data, ml_results)
        if send_telegram(recap):
            state["last_recap_date"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            log.info("Daily recap sent")

    save_state(state)
    return state


def cmd_test():
    """Send a test alert to verify Telegram is working."""
    now = datetime.now(EST).strftime("%I:%M %p EST, %b %d %Y")
    msg = (
        f"🔧 <b>OPS MONITOR — Test Alert</b>\n\n"
        f"If you see this, Telegram alerts are working.\n"
        f"Time: {now}\n"
        f"Bot: {BOT_TOKEN[:15]}...\n"
        f"Chat: {CHAT_ID}\n"
        f"CRELLA: {CRELLA_OPS_URL}\n"
        f"ML ports: 8040, 8041, 8043"
    )
    success = send_telegram(msg)
    print(f"Test alert {'sent successfully' if success else 'FAILED'}")
    return 0 if success else 1


def cmd_recap():
    """Send the daily recap immediately."""
    crella_ok, crella_data = check_crella()
    ml_results = check_ml_services()
    recap = build_daily_recap(crella_ok, crella_data, ml_results)
    print(recap.replace("<b>", "").replace("</b>", "").replace("<i>", "").replace("</i>", "").replace("<code>", "").replace("</code>", ""))
    print()
    success = send_telegram(recap)
    print(f"Recap {'sent to Telegram' if success else 'FAILED to send'}")
    return 0 if success else 1


def cmd_daemon():
    """Run continuously — PM2 mode."""
    log.info("=" * 60)
    log.info("OPS MONITOR — Starting daemon")
    log.info(f"  CRELLA:     {CRELLA_OPS_URL}")
    log.info(f"  ML ports:   8040, 8041, 8043")
    log.info(f"  Poll:       every {POLL_INTERVAL}s")
    log.info(f"  Recap:      {RECAP_HOUR_UTC}:00 UTC (8 AM EST) weekdays")
    log.info(f"  Cooldown:   {ALERT_COOLDOWN}s between alerts")
    log.info(f"  Telegram:   {CHAT_ID}")
    log.info("=" * 60)

    state = load_state()

    while _running:
        try:
            state = run_check(state)
        except Exception as e:
            log.error(f"Check cycle error: {e}", exc_info=True)

        for _ in range(POLL_INTERVAL):
            if not _running:
                break
            time.sleep(1)

    log.info("OPS MONITOR shut down cleanly")


def main():
    parser = argparse.ArgumentParser(description="OPS Monitor — System Health + Telegram Alerts")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--test", action="store_true", help="Send test alert to Telegram")
    group.add_argument("--recap", action="store_true", help="Send daily recap now")
    group.add_argument("--daemon", action="store_true", help="Run continuously (PM2 mode)")
    args = parser.parse_args()

    if args.test:
        sys.exit(cmd_test())
    elif args.recap:
        sys.exit(cmd_recap())
    elif args.daemon:
        cmd_daemon()


if __name__ == "__main__":
    main()
