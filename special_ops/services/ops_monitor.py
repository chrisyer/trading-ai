#!/usr/bin/env python3
"""
OPS MONITOR — Unified System Health + Daily/Weekly Recap for Telegram
Runs on H100 (QUINN001). Monitors CRELLA + local ML services.

Usage:
  python3 ops_monitor.py --test     Send a test alert to verify Telegram
  python3 ops_monitor.py --recap    Send daily recap immediately
  python3 ops_monitor.py --weekly   Send weekly recap immediately
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
GOLD_BOT_TOKEN = os.environ.get(
    "AIIQ_BOT_TOKEN",
    "7956358189:AAHLEIAWRnwi6Jz9eKORPVi99eP8jbwOF4w",
)
GOLD_CHAT_ID = os.environ.get(
    "GOLD_TELEGRAM_CHAT_ID",
    os.environ.get("AIIQ_CHAT_ID", "-1003582558817"),
)

CRELLA_IP = os.environ.get("CRELLA_IP", "100.119.161.65")
CRELLA_PORT = os.environ.get("CRELLA_PORT", "8097")
CRELLA_OPS_URL = f"http://{CRELLA_IP}:{CRELLA_PORT}/ops/health"

ML_SERVICES = {
    "Direction Predictor": "http://localhost:8040/health",
    "RL Position Sizer": "http://localhost:8041/health",
    "LoRA Governor": "http://localhost:8043/health",
}

GOLD_MIRROR_URL = f"http://{CRELLA_IP}:8099/gold/state"

POLL_INTERVAL = int(os.environ.get("OPS_POLL_SECONDS", "300"))  # 5 minutes
ALERT_COOLDOWN = 1800  # 30 minutes between repeated alerts for same issue
GOLD_ALERT_COOLDOWN = 900  # 15 minutes between gold signal alerts
RECAP_HOUR_UTC = 13  # 8 AM EST = 13:00 UTC
WEEKLY_RECAP_DOW = 5  # Saturday (0=Mon, 5=Sat)
WEEKLY_RECAP_HOUR_UTC = 14  # 9 AM EST = 14:00 UTC
EST = timezone(timedelta(hours=-5))

STATE_FILE = Path(__file__).parent / "ops_monitor_state.json"
WEEKLY_STATE_FILE = Path(__file__).parent / "weekly_state.json"

_running = True


def extract_pnl(crella_data: dict) -> dict:
    """Extract PnL fields from CRELLA health response (handles nested 'pnl' block)."""
    pnl_block = crella_data.get("pnl", {})
    if isinstance(pnl_block, dict) and "equity" in pnl_block:
        return pnl_block
    return crella_data


# ─── Weekly State ─────────────────────────────────────────────────────────

def load_weekly_state() -> dict:
    if WEEKLY_STATE_FILE.exists():
        try:
            with open(WEEKLY_STATE_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return _new_weekly_period()


def save_weekly_state(ws: dict):
    try:
        with open(WEEKLY_STATE_FILE, "w") as f:
            json.dump(ws, f, indent=2, default=str)
    except Exception as e:
        log.warning(f"Failed to save weekly state: {e}")


def _new_weekly_period() -> dict:
    return {
        "week_start": datetime.now(timezone.utc).isoformat(),
        "start_equity": None,
        "latest_equity": None,
        "latest_floating_pnl": None,
        "equity_snapshots": [],
        "alert_counts": {},
        "crella_checks": 0,
        "crella_failures": 0,
        "ml_checks": {},
        "ml_failures": {},
        "gold_mirror_checks": 0,
        "gold_mirror_failures": 0,
        "gold_signal_counts": {},
        "last_weekly_recap_date": "",
    }


def record_weekly_check(ws: dict, crella_ok: bool, crella_data: Optional[dict],
                        ml_results: Dict[str, dict], alerts_fired: List[str],
                        gold_ok: bool = False, gold_data: Optional[dict] = None):
    """Record one poll cycle's results into weekly tracking state."""
    ws["crella_checks"] = ws.get("crella_checks", 0) + 1
    if not crella_ok:
        ws["crella_failures"] = ws.get("crella_failures", 0) + 1

    for name, result in ml_results.items():
        ws.setdefault("ml_checks", {})[name] = ws.get("ml_checks", {}).get(name, 0) + 1
        if not result["healthy"]:
            ws.setdefault("ml_failures", {})[name] = ws.get("ml_failures", {}).get(name, 0) + 1

    for alert_key in alerts_fired:
        ws.setdefault("alert_counts", {})[alert_key] = ws.get("alert_counts", {}).get(alert_key, 0) + 1

    ws["gold_mirror_checks"] = ws.get("gold_mirror_checks", 0) + 1
    if not gold_ok:
        ws["gold_mirror_failures"] = ws.get("gold_mirror_failures", 0) + 1
    if gold_ok and gold_data:
        rec = gold_data.get("recommendation", {})
        for instrument in ("mgc", "gld"):
            action = rec.get(instrument, {}).get("action", "NO_TRADE")
            if action in _ACTIONABLE_SIGNALS:
                sig_key = f"{instrument.upper()}_{action}"
                ws.setdefault("gold_signal_counts", {})[sig_key] = \
                    ws.get("gold_signal_counts", {}).get(sig_key, 0) + 1

    if crella_ok and crella_data:
        pnl_info = extract_pnl(crella_data)
        equity = pnl_info.get("equity", 0)
        fpnl = pnl_info.get("floating_pnl", 0)
        if equity > 0:
            if ws.get("start_equity") is None:
                ws["start_equity"] = equity
            ws["latest_equity"] = equity
            ws["latest_floating_pnl"] = fpnl
            snapshots = ws.setdefault("equity_snapshots", [])
            snapshots.append({
                "ts": datetime.now(timezone.utc).isoformat(),
                "equity": equity,
                "fpnl": fpnl,
            })
            if len(snapshots) > 2016:
                ws["equity_snapshots"] = snapshots[-2016:]

    save_weekly_state(ws)


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

def send_telegram(text: str, parse_mode: str = "HTML", chat_id: str = None,
                   bot_token: str = None) -> bool:
    target = chat_id or CHAT_ID
    token = bot_token or BOT_TOKEN
    if not token or not target:
        log.warning("Telegram not configured")
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        resp = requests.post(url, json={
            "chat_id": target,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }, timeout=15)
        if resp.status_code == 200:
            ch_label = "gold" if target == GOLD_CHAT_ID else "ops"
            log.info(f"Telegram [{ch_label}] sent ({len(text)} chars)")
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


def check_gold_mirror() -> Tuple[bool, Optional[dict]]:
    """Poll CRELLA Gold Mirror API for current state."""
    try:
        resp = requests.get(GOLD_MIRROR_URL, timeout=10)
        resp.raise_for_status()
        return True, resp.json()
    except Exception as e:
        log.debug(f"Gold Mirror check failed: {e}")
        return False, None


_ACTIONABLE_SIGNALS = {"BUY_STARTER", "ADD_DCA", "FLATTEN", "DEPLOY_CALLS", "DEPLOY_PUTS",
                       "SELL_STARTER", "ROLL_SPREAD", "TRIM", "HEDGE"}
_ACTION_EMOJIS = {
    "BUY_STARTER": "🟢", "SELL_STARTER": "🔴", "ADD_DCA": "🔵",
    "FLATTEN": "⚪", "TRIM": "🟡", "DEPLOY_CALLS": "📗",
    "DEPLOY_PUTS": "📕", "ROLL_SPREAD": "🔄", "HEDGE": "🛡️",
}


def _should_gold_alert(key: str, state: dict) -> bool:
    last = state.get("last_alerts", {}).get(key)
    if not last:
        return True
    try:
        elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(last)).total_seconds()
        return elapsed >= GOLD_ALERT_COOLDOWN
    except Exception:
        return True


def process_gold_alerts(gold_ok: bool, gold_data: Optional[dict], state: dict) -> List[str]:
    """Generate Telegram alerts for actionable Gold Mirror signals."""
    alerts_sent = []
    if not gold_ok or not gold_data:
        return alerts_sent

    now_str = datetime.now(EST).strftime("%I:%M %p EST")
    rec = gold_data.get("recommendation", {})
    price = gold_data.get("price", 0)
    regime = gold_data.get("regime", "?")
    gate = gold_data.get("oracle_gate", "?")
    confidence = gold_data.get("confidence", 0)

    for instrument in ("mgc", "gld"):
        inst_rec = rec.get(instrument, {})
        action = inst_rec.get("action", "NO_TRADE")
        reason = inst_rec.get("reason", "")

        if action not in _ACTIONABLE_SIGNALS:
            continue

        prev_key = f"gold_{instrument}_last_action"
        prev_action = state.get(prev_key, "")
        if action == prev_action:
            continue

        key = f"gold_{instrument}_{action.lower()}"
        if not _should_gold_alert(key, state):
            continue

        emoji = _ACTION_EMOJIS.get(action, "🔔")
        label = instrument.upper()
        extra = ""
        if "contracts" in inst_rec:
            extra = f"\nContracts: {inst_rec['contracts']}"
        elif "structure" in inst_rec and inst_rec["structure"]:
            extra = f"\nStructure: {inst_rec['structure']}"

        blocks = gold_data.get("blocks", {})
        active_blocks = [k for k, v in blocks.items() if v]
        block_line = f"\n⛔ Blocks: {', '.join(active_blocks)}" if active_blocks else ""

        msg = (
            f"{emoji} <b>GOLD MIRROR: {label} → {action}</b>\n"
            f"Reason: {reason}\n"
            f"Price: ${price:,.2f} | Regime: {regime}\n"
            f"Gate: {gate} | Confidence: {confidence:.0%}"
            f"{extra}{block_line}\n"
            f"Time: {now_str}"
        )
        if send_telegram(msg, chat_id=GOLD_CHAT_ID, bot_token=GOLD_BOT_TOKEN):
            mark_alerted(key, state)
            state[prev_key] = action
            alerts_sent.append(key)

    return alerts_sent


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
        pnl_info = extract_pnl(crella_data)
        equity = pnl_info.get("equity", 0)
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
                      ml_results: Dict[str, dict],
                      gold_ok: bool = False, gold_data: Optional[dict] = None) -> str:
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
        pnl_info = extract_pnl(crella_data)
        equity = pnl_info.get("equity", 0)
        balance = pnl_info.get("balance", 0)
        pnl = pnl_info.get("floating_pnl", 0)
        positions = crella_data.get("open_positions", crella_data.get("positions", pnl_info.get("layers", 0)))
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

    # Gold Mirror
    lines.append(f"<b>━━━ GOLD MIRROR ━━━</b>")
    if gold_ok and gold_data:
        gm_price = gold_data.get("price", 0)
        gm_regime = gold_data.get("regime", "?")
        gm_gate = gold_data.get("oracle_gate", "?")
        gm_conf = gold_data.get("confidence", 0)
        gm_rec = gold_data.get("recommendation", {})
        mgc_act = gm_rec.get("mgc", {}).get("action", "?")
        mgc_rsn = gm_rec.get("mgc", {}).get("reason", "")
        gld_act = gm_rec.get("gld", {}).get("action", "?")
        gld_rsn = gm_rec.get("gld", {}).get("reason", "")
        gm_pos = gold_data.get("positions", {})
        mgc_contracts = gm_pos.get("mgc", {}).get("contracts", 0)
        gld_contracts = gm_pos.get("gld", {}).get("contracts", 0)

        lines.append(f"  Price: ${gm_price:,.2f} | Regime: {gm_regime}")
        lines.append(f"  Gate: {gm_gate} | Confidence: {gm_conf:.0%}")
        mgc_emoji = _ACTION_EMOJIS.get(mgc_act, "•")
        gld_emoji = _ACTION_EMOJIS.get(gld_act, "•")
        lines.append(f"  MGC: {mgc_emoji} {mgc_act} ({mgc_rsn})")
        lines.append(f"  GLD: {gld_emoji} {gld_act} ({gld_rsn})")
        if mgc_contracts or gld_contracts:
            lines.append(f"  Positions: MGC={mgc_contracts} contracts, GLD={gld_contracts} contracts")

        blocks = gold_data.get("blocks", {})
        active_blocks = [k for k, v in blocks.items() if v]
        if active_blocks:
            lines.append(f"  ⛔ Active blocks: {', '.join(active_blocks)}")
    else:
        lines.append(f"  Status: {format_status_emoji(False)} Unreachable")

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


# ─── Weekly Recap ────────────────────────────────────────────────────────

def should_send_weekly_recap(ws: dict) -> bool:
    """Saturday at 14:00-14:10 UTC (9:00-9:10 AM EST)."""
    now = datetime.now(timezone.utc)
    if now.weekday() != WEEKLY_RECAP_DOW:
        return False
    last = ws.get("last_weekly_recap_date", "")
    today_str = now.strftime("%Y-%m-%d")
    if last == today_str:
        return False
    return now.hour == WEEKLY_RECAP_HOUR_UTC and now.minute < 10


def build_weekly_recap(ws: dict, crella_ok: bool, crella_data: Optional[dict],
                       ml_results: Dict[str, dict],
                       gold_ok: bool = False, gold_data: Optional[dict] = None) -> str:
    now = datetime.now(EST)
    date_str = now.strftime("%A, %B %d %Y")
    time_str = now.strftime("%I:%M %p EST")

    week_start = ws.get("week_start", "?")
    try:
        ws_dt = datetime.fromisoformat(week_start).astimezone(EST)
        week_range = f"{ws_dt.strftime('%b %d')} – {now.strftime('%b %d, %Y')}"
    except Exception:
        week_range = f"? – {now.strftime('%b %d, %Y')}"

    lines = [
        f"📈 <b>WEEKLY OPS RECAP — {week_range}</b>",
        f"<i>Generated {time_str} on {date_str}</i>",
        "",
    ]

    # ── Equity ──
    lines.append("<b>━━━ EQUITY ━━━</b>")
    start_eq = ws.get("start_equity")
    end_eq = ws.get("latest_equity")
    fpnl = ws.get("latest_floating_pnl", 0)

    if start_eq and end_eq:
        delta = end_eq - start_eq
        pct = (delta / start_eq) * 100 if start_eq else 0
        arrow = "📈" if delta >= 0 else "📉"
        lines.append(f"  Start:  <b>${start_eq:,.0f}</b>")
        lines.append(f"  End:    <b>${end_eq:,.0f}</b>")
        lines.append(f"  Change: {arrow} <b>${delta:+,.0f}</b> ({pct:+.2f}%)")
    elif end_eq:
        lines.append(f"  Current: <b>${end_eq:,.0f}</b>")
        lines.append(f"  <i>(No start-of-week snapshot available)</i>")
    else:
        lines.append("  <i>No equity data collected this week</i>")

    if fpnl is not None and fpnl != 0:
        lines.append(f"  Floating P&L: ${fpnl:+,.1f}")
    lines.append("")

    # ── Gold Mirror ──
    gold_signals = ws.get("gold_signal_counts", {})
    total_gold = sum(gold_signals.values())
    lines.append("<b>━━━ GOLD MIRROR ━━━</b>")
    if gold_ok and gold_data:
        gm_price = gold_data.get("price", 0)
        gm_regime = gold_data.get("regime", "?")
        lines.append(f"  Current: ${gm_price:,.2f} | Regime: {gm_regime}")
    if total_gold > 0:
        lines.append(f"  Actionable signals this week: <b>{total_gold}</b>")
        for sig, cnt in sorted(gold_signals.items(), key=lambda x: -x[1]):
            lines.append(f"  • {sig}: {cnt}x")
    else:
        lines.append("  No actionable gold signals this week")
    gold_checks = ws.get("gold_mirror_checks", 0)
    gold_fails = ws.get("gold_mirror_failures", 0)
    if gold_checks > 0:
        gm_up = ((gold_checks - gold_fails) / gold_checks) * 100
        gm_emoji = "✅" if gm_up >= 99 else ("🟡" if gm_up >= 90 else "🔴")
        lines.append(f"  API uptime: {gm_emoji} {gm_up:.1f}%")
    lines.append("")

    # ── Health Alerts ──
    alert_counts = ws.get("alert_counts", {})
    total_alerts = sum(alert_counts.values())
    lines.append("<b>━━━ ALERTS THIS WEEK ━━━</b>")
    if total_alerts == 0:
        lines.append("  ✅ Zero alerts — clean week")
    else:
        lines.append(f"  Total alerts fired: <b>{total_alerts}</b>")
        sorted_alerts = sorted(alert_counts.items(), key=lambda x: -x[1])
        for key, count in sorted_alerts[:8]:
            label = key.replace("_", " ").title()
            repeat_flag = " ⚠️ <i>recurring</i>" if count >= 3 else ""
            lines.append(f"  • {label}: {count}x{repeat_flag}")
    lines.append("")

    # ── CRELLA Uptime ──
    crella_checks = ws.get("crella_checks", 0)
    crella_fails = ws.get("crella_failures", 0)
    lines.append("<b>━━━ CRELLA UPTIME ━━━</b>")
    if crella_checks > 0:
        uptime_pct = ((crella_checks - crella_fails) / crella_checks) * 100
        emoji = "✅" if uptime_pct >= 99 else ("🟡" if uptime_pct >= 90 else "🔴")
        lines.append(f"  {emoji} {uptime_pct:.1f}% ({crella_checks - crella_fails}/{crella_checks} checks passed)")
        if crella_fails > 0:
            lines.append(f"  Disconnections: {crella_fails} check(s) failed")
    else:
        lines.append("  <i>No checks recorded</i>")
    lines.append("")

    # ── ML Service Uptime ──
    ml_checks_map = ws.get("ml_checks", {})
    ml_fails_map = ws.get("ml_failures", {})
    lines.append("<b>━━━ ML SERVICE UPTIME ━━━</b>")
    for name in ML_SERVICES:
        checks = ml_checks_map.get(name, 0)
        fails = ml_fails_map.get(name, 0)
        if checks > 0:
            up_pct = ((checks - fails) / checks) * 100
            emoji = "✅" if up_pct >= 99 else ("🟡" if up_pct >= 90 else "🔴")
            outage_note = "" if fails == 0 else f" ({fails} outage check(s))"
            lines.append(f"  {emoji} {name}: {up_pct:.1f}%{outage_note}")
        else:
            lines.append(f"  ❓ {name}: no data")
    lines.append("")

    # ── Model Versions & Retrain Dates ──
    lines.append("<b>━━━ MODEL STATUS ━━━</b>")
    for name, result in ml_results.items():
        details = result.get("details", {})
        meta = details.get("model_meta", {})
        trained_at = meta.get("trained_at", "unknown")
        if trained_at != "unknown":
            try:
                td = datetime.fromisoformat(trained_at).astimezone(EST)
                age = datetime.now(timezone.utc) - datetime.fromisoformat(trained_at).replace(
                    tzinfo=timezone.utc if td.tzinfo is None else td.tzinfo)
                trained_str = td.strftime("%b %d %I:%M %p")
                days_ago = age.days
                age_str = f"{days_ago}d ago" if days_ago > 0 else "today"
            except Exception:
                trained_str = str(trained_at)[:16]
                age_str = ""
        else:
            trained_str = "unknown"
            age_str = ""

        version_info = ""
        if "best_val_acc" in meta:
            version_info = f"acc={meta['best_val_acc']:.1%}"
        elif "total_timesteps" in meta:
            version_info = f"{meta['total_timesteps']:,} steps"
        elif "n_examples" in meta:
            version_info = f"{meta['n_examples']} examples"
        base = meta.get("base_model", "")
        if base:
            short_base = base.split("/")[-1][:25]
            version_info = f"{short_base}, {version_info}" if version_info else short_base

        status_emoji = format_status_emoji(result["healthy"])
        trained_line = f"  {status_emoji} <b>{name}</b>"
        if version_info:
            trained_line += f"\n      {version_info}"
        trained_line += f"\n      Last train: {trained_str}"
        if age_str:
            trained_line += f" ({age_str})"
        lines.append(trained_line)
    lines.append("")

    # ── Recurring Issues ──
    recurring = {k: v for k, v in alert_counts.items() if v >= 3}
    if recurring:
        lines.append("<b>━━━ RECURRING ISSUES ━━━</b>")
        for key, count in sorted(recurring.items(), key=lambda x: -x[1]):
            label = key.replace("_", " ").title()
            lines.append(f"  ⚠️ {label}: {count}x — investigate root cause")
        lines.append("")

    lines.append(f"<i>Next weekly recap: Saturday 9:00 AM EST</i>")
    return "\n".join(lines)


# ─── Main Loop ───────────────────────────────────────────────────────────────

def run_check(state: dict, weekly_state: dict) -> Tuple[dict, dict]:
    """Run one cycle of health checks and alerts."""
    crella_ok, crella_data = check_crella()
    ml_results = check_ml_services()
    gold_ok, gold_data = check_gold_mirror()

    if crella_ok:
        pnl_info = extract_pnl(crella_data)
        equity_val = pnl_info.get('equity', 0)
        fpnl = pnl_info.get('floating_pnl', 0)
        log.info(f"CRELLA: OK | Equity: ${equity_val:,.0f} | PnL: ${fpnl:+,.1f}")
    else:
        log.warning("CRELLA: UNREACHABLE")

    if gold_ok and gold_data:
        rec = gold_data.get("recommendation", {})
        mgc = rec.get("mgc", {}).get("action", "?")
        gld = rec.get("gld", {}).get("action", "?")
        log.info(f"Gold Mirror: OK | MGC={mgc} GLD={gld}")
    else:
        log.debug("Gold Mirror: unreachable")

    ml_up = sum(1 for r in ml_results.values() if r["healthy"])
    log.info(f"ML Services: {ml_up}/{len(ml_results)} healthy")

    alerts = process_alerts(crella_ok, crella_data, ml_results, state)
    gold_alerts = process_gold_alerts(gold_ok, gold_data, state)
    alerts.extend(gold_alerts)
    if alerts:
        log.info(f"Alerts sent: {alerts}")

    if crella_ok and crella_data:
        pnl_info = extract_pnl(crella_data)
        state["last_crella_data"] = {
            "equity": pnl_info.get("equity", 0),
            "balance": pnl_info.get("balance", 0),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    record_weekly_check(weekly_state, crella_ok, crella_data, ml_results, alerts,
                        gold_ok, gold_data)

    if should_send_recap(state):
        log.info("Sending daily recap...")
        recap = build_daily_recap(crella_ok, crella_data, ml_results, gold_ok, gold_data)
        if send_telegram(recap):
            state["last_recap_date"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            log.info("Daily recap sent")

    if should_send_weekly_recap(weekly_state):
        log.info("Sending weekly recap...")
        recap = build_weekly_recap(weekly_state, crella_ok, crella_data, ml_results,
                                   gold_ok, gold_data)
        if send_telegram(recap):
            weekly_state["last_weekly_recap_date"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            save_weekly_state(weekly_state)
            log.info("Weekly recap sent — resetting weekly counters")
            weekly_state = _new_weekly_period()
            weekly_state["last_weekly_recap_date"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            if crella_ok and crella_data:
                pnl_info = extract_pnl(crella_data)
                weekly_state["start_equity"] = pnl_info.get("equity", 0)
            save_weekly_state(weekly_state)

    save_state(state)
    return state, weekly_state


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
    gold_ok, gold_data = check_gold_mirror()
    recap = build_daily_recap(crella_ok, crella_data, ml_results, gold_ok, gold_data)
    print(recap.replace("<b>", "").replace("</b>", "").replace("<i>", "").replace("</i>", "").replace("<code>", "").replace("</code>", ""))
    print()
    success = send_telegram(recap)
    print(f"Recap {'sent to Telegram' if success else 'FAILED to send'}")
    return 0 if success else 1


def cmd_weekly():
    """Send the weekly recap immediately (for testing)."""
    ws = load_weekly_state()
    crella_ok, crella_data = check_crella()
    ml_results = check_ml_services()
    gold_ok, gold_data = check_gold_mirror()
    if crella_ok and crella_data:
        pnl_info = extract_pnl(crella_data)
        eq = pnl_info.get("equity", 0)
        if ws.get("start_equity") is None and eq > 0:
            ws["start_equity"] = eq
        if eq > 0:
            ws["latest_equity"] = eq
            ws["latest_floating_pnl"] = pnl_info.get("floating_pnl", 0)
    recap = build_weekly_recap(ws, crella_ok, crella_data, ml_results, gold_ok, gold_data)
    stripped = recap
    for tag in ("<b>", "</b>", "<i>", "</i>", "<code>", "</code>"):
        stripped = stripped.replace(tag, "")
    print(stripped)
    print()
    success = send_telegram(recap)
    print(f"Weekly recap {'sent to Telegram' if success else 'FAILED to send'}")
    return 0 if success else 1


def cmd_daemon():
    """Run continuously — PM2 mode."""
    log.info("=" * 60)
    log.info("OPS MONITOR — Starting daemon")
    log.info(f"  CRELLA:     {CRELLA_OPS_URL}")
    log.info(f"  Gold:       {GOLD_MIRROR_URL}")
    log.info(f"  ML ports:   8040, 8041, 8043")
    log.info(f"  Poll:       every {POLL_INTERVAL}s")
    log.info(f"  Recap:      {RECAP_HOUR_UTC}:00 UTC (8 AM EST) weekdays")
    log.info(f"  Weekly:     Sat {WEEKLY_RECAP_HOUR_UTC}:00 UTC (9 AM EST)")
    log.info(f"  Cooldown:   {ALERT_COOLDOWN}s between alerts")
    log.info(f"  OPS chat:   {CHAT_ID} (Crella Cortex)")
    log.info(f"  Gold chat:  {GOLD_CHAT_ID} (AiiQ Trading Signals)")
    log.info("=" * 60)

    state = load_state()
    weekly_state = load_weekly_state()

    while _running:
        try:
            state, weekly_state = run_check(state, weekly_state)
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
    group.add_argument("--weekly", action="store_true", help="Send weekly recap now")
    group.add_argument("--daemon", action="store_true", help="Run continuously (PM2 mode)")
    args = parser.parse_args()

    if args.test:
        sys.exit(cmd_test())
    elif args.recap:
        sys.exit(cmd_recap())
    elif args.weekly:
        sys.exit(cmd_weekly())
    elif args.daemon:
        cmd_daemon()


if __name__ == "__main__":
    main()
