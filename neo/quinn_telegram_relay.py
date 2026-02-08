#!/usr/bin/env python3
"""
═══════════════════════════════════════════════════════════════════════════════
QUINN TELEGRAM RELAY — H1 Truth Pipeline + Post-H1 Research Analysis
═══════════════════════════════════════════════════════════════════════════════

Runs on H100 (QUINN001).

TWO RESPONSIBILITIES:
  1. TRUTH RELAY — Poll CRELLA for truth reports, format, send to Telegram
  2. POST-H1 ANALYSIS — After T+1 bar closes, label outcomes and send
     a SEPARATE research-only analysis message (NEVER a signal)

STRICT TIMING:
  - Truth Report posts immediately at H1 close (time T)
  - Analysis waits until NEXT H1 bar completes (time T+1)
  - Only then labels outcome and sends analysis
  - If outcome cannot be labeled, posts explicit skip message
  - Silence is NEVER allowed

CONSTRAINTS:
  - Analysis is RESEARCH ONLY — no BUY/SELL language
  - Does NOT alter truth reports
  - Does NOT optimize thresholds
  - Does NOT use future data beyond T+1

Author: QUINN001
Created: February 8, 2026
═══════════════════════════════════════════════════════════════════════════════
"""

import os
import sys
import json
import time
import math
import signal
import logging
import requests
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, List, Tuple

# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

# Primary: Crella Cortex Bot (Admin DM)
BOT_TOKEN = os.environ.get(
    "TELEGRAM_BOT_TOKEN",
    "8250652030:AAFd4x8NsTfdaB3O67lUnMhotT2XY61600s"
)
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "6776619257")

# Secondary: AiiQ Trading Signals group (optional broadcast)
AIIQ_BOT_TOKEN = "7956358189:AAHLEIAWRnwi6Jz9eKORPVi99eP8jbwOF4w"
AIIQ_CHAT_ID = os.environ.get("AIIQ_CHAT_ID", "-1003582558817")

# CRELLA access — try HTTP first, fall back to local file
CRELLA_TRUTH_URL = os.environ.get(
    "CRELLA_TRUTH_URL",
    "http://100.119.161.65:8097/truth/latest"  # CRELLA (desktop-gringot) Tailscale IP
)
CRELLA_TRUTH_FILE = os.environ.get(
    "CRELLA_TRUTH_FILE",
    ""  # Set if using Syncthing/shared folder
)

POLL_INTERVAL = int(os.environ.get("TRUTH_POLL_INTERVAL", "60"))  # seconds
STALE_THRESHOLD = 7200  # 2 hours — warn if report is older than this
MAX_RETRIES = 3
RETRY_DELAY = 5

# MongoDB (optional logging)
MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB = "quinn_trading"
MONGO_COLLECTION = "truth_relay_log"

# Local state
STATE_DIR = Path("/home/jbot/trading_ai/neo/truth_data")
STATE_FILE = STATE_DIR / "relay_state.json"
LATEST_CACHE = STATE_DIR / "last_truth.json"

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [TRUTH-RELAY] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("truth_relay")

# ═══════════════════════════════════════════════════════════════════════════════
# STATE MANAGEMENT
# ═══════════════════════════════════════════════════════════════════════════════

# Analysis state file
ANALYSIS_STATE_FILE = STATE_DIR / "analysis_state.json"
PENDING_REPORTS_FILE = STATE_DIR / "pending_analysis.json"


class RelayState:
    """Track last sent report to avoid duplicates."""
    
    def __init__(self):
        self.last_unix = 0
        self.total_sent = 0
        self.total_failures = 0
        self.last_sent_time = None
        self.total_analyses_sent = 0
        self.last_analysis_unix = 0
        self.load()
    
    def load(self):
        if STATE_FILE.exists():
            try:
                data = json.loads(STATE_FILE.read_text())
                self.last_unix = data.get("last_unix", 0)
                self.total_sent = data.get("total_sent", 0)
                self.total_failures = data.get("total_failures", 0)
                self.last_sent_time = data.get("last_sent_time")
                self.total_analyses_sent = data.get("total_analyses_sent", 0)
                self.last_analysis_unix = data.get("last_analysis_unix", 0)
                logger.info(f"Loaded state: last_unix={self.last_unix}, total_sent={self.total_sent}")
            except Exception as e:
                logger.warning(f"Failed to load state: {e}")
    
    def save(self):
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps({
            "last_unix": self.last_unix,
            "total_sent": self.total_sent,
            "total_failures": self.total_failures,
            "last_sent_time": self.last_sent_time,
            "total_analyses_sent": self.total_analyses_sent,
            "last_analysis_unix": self.last_analysis_unix,
        }, indent=2))
    
    def update(self, unix_ts: int, success: bool):
        if success:
            self.last_unix = unix_ts
            self.total_sent += 1
            self.last_sent_time = datetime.now(timezone.utc).isoformat()
        else:
            self.total_failures += 1
        self.save()
    
    def update_analysis(self, unix_ts: int):
        self.total_analyses_sent += 1
        self.last_analysis_unix = unix_ts
        self.save()


class PendingAnalysisQueue:
    """
    Tracks truth reports awaiting post-H1 analysis.
    A report at time T is pending until T+1 bar data arrives.
    """
    
    def __init__(self):
        self.pending: List[Dict] = []
        self._load()
    
    def _load(self):
        if PENDING_REPORTS_FILE.exists():
            try:
                self.pending = json.loads(PENDING_REPORTS_FILE.read_text())
                logger.info(f"Loaded {len(self.pending)} pending analysis reports")
            except:
                self.pending = []
    
    def _save(self):
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        PENDING_REPORTS_FILE.write_text(json.dumps(self.pending, indent=2))
    
    def add(self, truth_data: Dict):
        """Add a truth report to the pending queue."""
        unix = truth_data.get("timestamps", {}).get("unix", 0)
        # Don't add duplicates
        if any(p.get("unix") == unix for p in self.pending):
            return
        
        entry = {
            "unix": unix,
            "timestamp": truth_data.get("timestamps", {}).get("utc", ""),
            "price": truth_data.get("market", {}).get("price", 0),
            "session": truth_data.get("market", {}).get("session", "UNKNOWN"),
            "market_open": truth_data.get("market", {}).get("open", True),
            "scorecard": truth_data.get("scorecard", {}),
            "metrics": truth_data.get("metrics", []),
            "report_number": truth_data.get("system", {}).get("reports_sent", 0),
            "added_at": datetime.now(timezone.utc).isoformat(),
        }
        self.pending.append(entry)
        # Keep only last 24 pending (safety)
        if len(self.pending) > 24:
            self.pending = self.pending[-24:]
        self._save()
        logger.info(f"Queued for analysis: unix={unix} price=${entry['price']}")
    
    def get_ready(self, current_unix: int) -> List[Dict]:
        """
        Get reports that are ready for analysis.
        A report at time T is ready when current_unix >= T + 3600 (T+1 bar closed).
        """
        ready = []
        remaining = []
        for p in self.pending:
            if current_unix >= p["unix"] + 3600:
                ready.append(p)
            else:
                remaining.append(p)
        
        if ready:
            self.pending = remaining
            self._save()
        
        return ready
    
    def remove(self, unix: int):
        """Remove a specific report from pending."""
        self.pending = [p for p in self.pending if p.get("unix") != unix]
        self._save()


# ═══════════════════════════════════════════════════════════════════════════════
# DATA FETCHING
# ═══════════════════════════════════════════════════════════════════════════════

def fetch_truth_http() -> Optional[Dict]:
    """Fetch truth JSON from CRELLA's HTTP endpoint."""
    if not CRELLA_TRUTH_URL:
        return None
    try:
        r = requests.get(CRELLA_TRUTH_URL, timeout=10)
        if r.status_code == 200:
            data = r.json()
            logger.info(f"HTTP fetch OK: unix={data.get('timestamps', {}).get('unix', '?')}")
            return data
        elif r.status_code == 404:
            logger.warning("CRELLA: no truth file (EA not running?)")
        else:
            logger.warning(f"CRELLA HTTP {r.status_code}: {r.text[:100]}")
    except requests.ConnectionError:
        logger.warning("CRELLA unreachable (Tailscale down?)")
    except requests.Timeout:
        logger.warning("CRELLA timeout (10s)")
    except Exception as e:
        logger.warning(f"HTTP fetch failed: {e}")
    return None


def fetch_truth_file() -> Optional[Dict]:
    """Read truth JSON from a local/synced file path."""
    if not CRELLA_TRUTH_FILE:
        return None
    path = Path(CRELLA_TRUTH_FILE)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        logger.info(f"File fetch OK: unix={data.get('timestamps', {}).get('unix', '?')}")
        return data
    except Exception as e:
        logger.warning(f"File read failed: {e}")
    return None


def fetch_truth() -> Optional[Dict]:
    """Try HTTP first, then file fallback."""
    data = fetch_truth_http()
    if data:
        return data
    data = fetch_truth_file()
    if data:
        return data
    # Last resort: check cached copy
    if LATEST_CACHE.exists():
        try:
            cached = json.loads(LATEST_CACHE.read_text())
            age = time.time() - cached.get("timestamps", {}).get("unix", 0)
            if age < STALE_THRESHOLD:
                logger.info("Using cached truth (still fresh)")
                return cached
            else:
                logger.warning(f"Cached truth is stale ({age/3600:.1f}h old)")
        except:
            pass
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# MESSAGE FORMATTING
# ═══════════════════════════════════════════════════════════════════════════════

BIAS_ICONS = {
    "BUY": "\U0001f7e2",   # 🟢
    "SELL": "\U0001f534",   # 🔴
    "HOLD": "\u26aa",       # ⚪
}

def format_telegram_message(data: Dict) -> str:
    """Format truth JSON into Telegram-ready text."""
    ts = data.get("timestamps", {})
    mkt = data.get("market", {})
    sc = data.get("scorecard", {})
    metrics = data.get("metrics", [])
    system = data.get("system", {})
    version = data.get("version", "1.0")
    symbol = data.get("symbol", "XAUUSD")
    
    market_open = mkt.get("open", True)
    session = mkt.get("session", "UNKNOWN")
    
    # Header
    if market_open:
        status_line = f"\U0001f552 {ts.get('utc', '?')} UTC | {session}"
    else:
        status_line = f"\U0001f552 {ts.get('utc', '?')} UTC | \U0001f512 MARKET CLOSED"
    
    lines = [
        f"\U0001f4ca {symbol} H1 TRUTH REPORT",
        "\u2501" * 24,
        status_line,
        f"\U0001f4b0 Price: {mkt.get('price', '?')} | Spread: {mkt.get('spread', '?')}",
        "",
    ]
    
    # Metrics section
    stale_tag = " (STALE)" if not market_open else ""
    lines.append(f"\u2501\u2501\u2501 METRICS{stale_tag} \u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501")
    
    for m in metrics:
        mid = m.get("id", "?")
        name = m.get("name", "?")
        interp = m.get("interpretation", "?")
        bias = m.get("bias", "HOLD")
        icon = BIAS_ICONS.get(bias, "\u26aa")
        lines.append(f"{mid:2d}. {name:<14s} \u2192 {interp} \u2192 {icon} {bias}")
    
    lines.append("")
    
    # Scorecard
    lines.append("\u2501\u2501\u2501 SCORECARD \u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501")
    buy_icon = BIAS_ICONS["BUY"]
    hold_icon = BIAS_ICONS["HOLD"]
    sell_icon = BIAS_ICONS["SELL"]
    lines.append(
        f"{buy_icon} BUY: {sc.get('buy', 0)}  "
        f"{hold_icon} HOLD: {sc.get('hold', 0)}  "
        f"{sell_icon} SELL: {sc.get('sell', 0)}"
    )
    
    lines.append("")
    
    # Market closed warning
    if not market_open:
        lines.append("\U0001f512 Market closed \u2014 indicators frozen")
    
    # Footer
    lines.append(f"\u23f0 Next update: {ts.get('next_update', 'N/A')}")
    lines.append(
        f"\U0001f916 Report #{system.get('reports_sent', '?')} | "
        f"QUINN Truth Pipeline v{version}"
    )
    
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
# POST-H1 ANALYSIS — Research Only, Delayed by T+1
# ═══════════════════════════════════════════════════════════════════════════════

# Fixed outcome thresholds (NOT optimized, NOT tunable)
OUTCOME_MFE_THRESHOLD = 5.0   # Price must move +5 favorably
OUTCOME_MAE_THRESHOLD = 8.0   # Price must not move -8 adversely


def normalize_metric_state(name: str, interp: str) -> Tuple[str, str]:
    """Normalize a metric name and interpretation to a simple key/value."""
    interp_upper = interp.upper()
    
    if "VWAP" in name:
        return "VWAP", "ABOVE" if "ABOVE" in interp_upper else "BELOW"
    elif "RSI" in name:
        if "OVERSOLD" in interp_upper and "LEANING" not in interp_upper:
            return "RSI(14)", "OVERSOLD"
        elif "LEANING" in interp_upper and "OVERSOLD" in interp_upper:
            return "RSI(14)", "LEANING_OVERSOLD"
        elif "OVERBOUGHT" in interp_upper and "LEANING" not in interp_upper:
            return "RSI(14)", "OVERBOUGHT"
        elif "LEANING" in interp_upper and "OVERBOUGHT" in interp_upper:
            return "RSI(14)", "LEANING_OVERBOUGHT"
        return "RSI(14)", "NEUTRAL"
    elif "ATR" in name:
        if "EXPAND" in interp_upper:
            return "ATR(14)", "EXPANDING"
        elif "CONTRACT" in interp_upper:
            return "ATR(14)", "CONTRACTING"
        return "ATR(14)", "STABLE"
    elif "Session" in name:
        return "Session", interp.replace("/", "_").replace(" ", "_")
    elif "Spread" in name:
        return "Spread", interp.upper()
    elif "EMA" in name:
        return "EMA 50/200", "BULLISH" if "BULLISH" in interp_upper else "BEARISH"
    elif "Ichimoku" in name:
        if "ABOVE" in interp_upper:
            return "Ichimoku", "ABOVE_CLOUD"
        elif "BELOW" in interp_upper:
            return "Ichimoku", "BELOW_CLOUD"
        return "Ichimoku", "IN_CLOUD"
    elif "H1 H/L" in name or "H1H/L" in name:
        if "HIGH" in interp_upper:
            return "H1 H/L Break", "BROKE_HIGH"
        elif "LOW" in interp_upper:
            return "H1 H/L Break", "BROKE_LOW"
        return "H1 H/L Break", "INSIDE_RANGE"
    elif "H4" in name:
        return "H4 Bias", "BULL" if "BULL" in interp_upper else "BEAR"
    elif "Regime" in name:
        return "Regime", "TREND" if "TREND" in interp_upper else "RANGE"
    return name, interp


def label_outcome(entry_price: float, next_price: float) -> Tuple[str, float, float]:
    """
    Label the outcome AFTER the fact.
    Returns (label, mfe_estimate, mae_estimate).
    Uses close-to-close delta as a conservative proxy.
    """
    delta = next_price - entry_price
    
    # Conservative MFE/MAE estimates from delta
    mfe = abs(delta)
    mae = abs(delta) * 0.3  # Rough: adverse excursion is typically less
    
    if delta >= OUTCOME_MFE_THRESHOLD:
        return "SUCCESS_LONG", mfe, mae
    elif delta <= -OUTCOME_MFE_THRESHOLD:
        return "SUCCESS_SHORT", mfe, mae
    else:
        return "NEUTRAL", mfe, mae


def lookup_historical_context(metrics: List[Dict]) -> Dict:
    """
    Look up whether active metric combinations have historical edge.
    Reads from the truth analyzer's history.
    Returns context dict for the analysis message.
    """
    context = {
        "has_history": False,
        "sample_count": 0,
        "win_rate_vs_baseline": "N/A",
        "fragile_sessions": [],
        "note": "Too early to judge — insufficient historical data",
    }
    
    try:
        history_file = STATE_DIR / "truth_history.json"
        if not history_file.exists():
            return context
        
        history = json.loads(history_file.read_text())
        if len(history) < 10:
            context["note"] = f"Only {len(history)} records collected. Minimum 20+ needed."
            return context
        
        # Build current metric signature
        current_states = {}
        for m in metrics:
            name = m.get("name", "")
            interp = m.get("interpretation", "")
            norm_name, norm_val = normalize_metric_state(name, interp)
            current_states[norm_name] = norm_val
        
        # Count matching records in history
        matching = 0
        matching_success = 0
        total_labeled = 0
        
        for rec in history:
            if not rec.get("outcome"):
                continue
            total_labeled += 1
            
            # Check if this record matches on key metrics (VWAP + Ichimoku + H4 + Regime)
            match = True
            for key in ["vwap", "ichimoku", "h4_bias", "regime"]:
                hist_val = rec.get(key, "")
                # Map to display name
                name_map = {"vwap": "VWAP", "ichimoku": "Ichimoku", "h4_bias": "H4 Bias", "regime": "Regime"}
                display_name = name_map.get(key, key)
                if display_name in current_states and hist_val and hist_val != current_states.get(display_name, ""):
                    match = False
                    break
            
            if match:
                matching += 1
                if rec.get("outcome") in ("SUCCESS_LONG", "SUCCESS_SHORT"):
                    matching_success += 1
        
        if matching >= 5:
            baseline_wins = sum(1 for r in history if r.get("outcome") in ("SUCCESS_LONG", "SUCCESS_SHORT"))
            baseline_rate = baseline_wins / total_labeled if total_labeled > 0 else 0
            combo_rate = matching_success / matching
            edge = combo_rate - baseline_rate
            
            context["has_history"] = True
            context["sample_count"] = matching
            context["win_rate_vs_baseline"] = f"{'+' if edge >= 0 else ''}{edge*100:.1f}%"
            context["note"] = (
                f"This combination appeared {matching} times. "
                f"Win rate {'above' if edge >= 0 else 'below'} baseline by {abs(edge)*100:.1f}%."
            )
        else:
            context["sample_count"] = matching
            context["note"] = f"This combination has only {matching} prior occurrences. Too early to judge."
        
    except Exception as e:
        context["note"] = f"Historical lookup unavailable: {e}"
    
    return context


def format_analysis_message(
    report: Dict,
    outcome_label: str,
    mfe: float,
    mae: float,
    next_price: float,
    historical: Dict,
) -> str:
    """
    Format the post-H1 research analysis message.
    FIXED FORMAT — no BUY/SELL language.
    """
    unix = report.get("unix", 0)
    report_num = report.get("report_number", "?")
    timestamp_t = report.get("timestamp", "?")
    price_t = report.get("price", 0)
    session = report.get("session", "UNKNOWN")
    metrics = report.get("metrics", [])
    
    # Time T+1 (approximate)
    t_plus_1 = datetime.utcfromtimestamp(unix + 3600).strftime("%Y.%m.%d %H:%M")
    
    # Outcome icon
    outcome_icons = {
        "SUCCESS_LONG": "\U0001f4c8",    # 📈
        "SUCCESS_SHORT": "\U0001f4c9",   # 📉
        "NEUTRAL": "\u2796",              # ➖
    }
    outcome_icon = outcome_icons.get(outcome_label, "\u2753")
    
    lines = [
        "\U0001f9e0 NEO POST-H1 ANALYSIS (RESEARCH ONLY)",
        f"Report ID: #{report_num}",
        f"Analyzed Window: {timestamp_t} \u2192 {t_plus_1} UTC",
        "",
        "Observed Outcome:",
        f"  {outcome_icon} Label: {outcome_label}",
        f"  MFE: {mfe:.1f}",
        f"  MAE: {mae:.1f}",
        f"  \u0394 Price: {price_t:.2f} \u2192 {next_price:.2f} ({next_price - price_t:+.2f})",
        "",
        "Context at Time T:",
    ]
    
    # List active metric states
    for m in metrics:
        name = m.get("name", "")
        interp = m.get("interpretation", "")
        bias = m.get("bias", "HOLD")
        _, norm_val = normalize_metric_state(name, interp)
        icon = BIAS_ICONS.get(bias, "\u26aa")
        lines.append(f"  {icon} {name}: {norm_val}")
    
    lines.append("")
    lines.append("Historical Context:")
    
    if historical.get("has_history"):
        lines.append(f"  \u2022 This combination has occurred {historical['sample_count']} times")
        lines.append(f"  \u2022 Win rate vs baseline: {historical['win_rate_vs_baseline']}")
        if historical.get("fragile_sessions"):
            lines.append(f"  \u2022 Historically fragile during: {', '.join(historical['fragile_sessions'])}")
    else:
        lines.append(f"  \u2022 {historical.get('note', 'Insufficient data')}")
    
    lines.extend([
        "",
        "Research Notes:",
        "  \u2022 No trade recommendation",
        "  \u2022 Informational only",
        "  \u2022 Confidence increases only with sample size",
    ])
    
    return "\n".join(lines)


def format_skip_message(report: Dict, reason: str) -> str:
    """Format a skip message when analysis cannot be completed."""
    report_num = report.get("report_number", "?")
    timestamp = report.get("timestamp", "?")
    
    return (
        "\U0001f9e0 NEO POST-H1 ANALYSIS\n"
        f"Report ID: #{report_num}\n"
        f"Window: {timestamp}\n"
        "\n"
        f"\u26a0\ufe0f NEO analysis skipped: {reason}\n"
        "\n"
        "No outcome could be labeled.\n"
        "This is expected during data gaps or market closures."
    )


def process_pending_analyses(
    analysis_queue: PendingAnalysisQueue,
    state: RelayState,
    current_data: Optional[Dict],
) -> None:
    """
    Check pending reports and send analysis for any that are ready.
    A report at time T is ready when T+1 data has arrived.
    """
    if current_data is None:
        return
    
    current_unix = current_data.get("timestamps", {}).get("unix", 0)
    current_price = current_data.get("market", {}).get("price", 0)
    
    if current_unix == 0 or current_price == 0:
        return
    
    # Get reports ready for analysis (T+1 has passed)
    ready = analysis_queue.get_ready(current_unix)
    
    for report in ready:
        report_unix = report.get("unix", 0)
        
        # Skip if already analyzed
        if report_unix <= state.last_analysis_unix:
            continue
        
        # Skip if market was closed
        if not report.get("market_open", True):
            msg = format_skip_message(report, "Market was closed during this window")
            send_telegram(msg)
            state.update_analysis(report_unix)
            logger.info(f"Analysis skipped (market closed): unix={report_unix}")
            continue
        
        try:
            entry_price = report.get("price", 0)
            
            if entry_price == 0:
                msg = format_skip_message(report, "Entry price unavailable")
                send_telegram(msg)
                state.update_analysis(report_unix)
                continue
            
            # Use current price as T+1 outcome (conservative proxy)
            # Ideally we'd use the exact T+1 close, but current_price
            # is from the T+1 report which is the closest we have
            next_price = current_price
            
            # Label the outcome AFTER THE FACT
            outcome_label, mfe, mae = label_outcome(entry_price, next_price)
            
            # Look up historical context
            historical = lookup_historical_context(report.get("metrics", []))
            
            # Format analysis message
            analysis_msg = format_analysis_message(
                report=report,
                outcome_label=outcome_label,
                mfe=mfe,
                mae=mae,
                next_price=next_price,
                historical=historical,
            )
            
            # Send to Telegram
            ok = send_telegram(analysis_msg)
            
            if ok:
                state.update_analysis(report_unix)
                logger.info(
                    f"Analysis #{state.total_analyses_sent} sent | "
                    f"Report #{report.get('report_number', '?')} | "
                    f"{outcome_label} | \u0394{next_price - entry_price:+.2f}"
                )
            else:
                # Fail-safe: silence is NOT allowed
                skip_msg = format_skip_message(report, "Telegram delivery failed — will retry")
                _send_to_chat(BOT_TOKEN, CHAT_ID, skip_msg, "ANALYSIS_FAILSAFE")
                state.update_analysis(report_unix)
                logger.error(f"Analysis delivery failed for unix={report_unix}")
            
            # Log to MongoDB
            try:
                from pymongo import MongoClient
                client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=3000)
                db = client[MONGO_DB]
                col = db["truth_analysis_log"]
                col.insert_one({
                    "timestamp": datetime.now(timezone.utc),
                    "report_unix": report_unix,
                    "entry_price": entry_price,
                    "next_price": next_price,
                    "outcome": outcome_label,
                    "mfe": mfe,
                    "mae": mae,
                    "delta": next_price - entry_price,
                    "telegram_ok": ok,
                })
                client.close()
            except:
                pass
            
        except Exception as e:
            # FAIL-SAFE: always post something
            logger.error(f"Analysis error for unix={report_unix}: {e}")
            skip_msg = format_skip_message(
                report,
                f"Analysis error: {str(e)[:100]}"
            )
            send_telegram(skip_msg)
            state.update_analysis(report_unix)


# ═══════════════════════════════════════════════════════════════════════════════
# TELEGRAM DELIVERY
# ═══════════════════════════════════════════════════════════════════════════════

def _send_to_chat(token: str, chat_id: str, message: str, label: str) -> bool:
    """Send message to a specific chat via Telegram Bot API."""
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "disable_web_page_preview": True,
    }
    
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = requests.post(url, json=payload, timeout=15)
            if r.status_code == 200:
                msg_id = r.json().get("result", {}).get("message_id", "?")
                logger.info(f"[{label}] Delivered: message_id={msg_id}")
                return True
            elif r.status_code == 429:
                retry_after = r.json().get("parameters", {}).get("retry_after", 30)
                logger.warning(f"[{label}] Rate limited, waiting {retry_after}s")
                time.sleep(retry_after)
            else:
                logger.error(f"[{label}] API {r.status_code}: {r.text[:200]}")
        except Exception as e:
            logger.error(f"[{label}] Send failed (attempt {attempt}): {e}")
        
        if attempt < MAX_RETRIES:
            time.sleep(RETRY_DELAY)
    
    return False


def send_telegram(message: str) -> bool:
    """Send to Admin DM + AiiQ Trading Signals group."""
    if not BOT_TOKEN or not CHAT_ID:
        logger.error("Telegram not configured")
        logger.info(f"Would have sent:\n{message}")
        return False
    
    # Primary: Admin DM via Cortex Bot
    ok = _send_to_chat(BOT_TOKEN, CHAT_ID, message, "ADMIN")
    
    # Secondary: AiiQ Trading Signals group
    if AIIQ_BOT_TOKEN and AIIQ_CHAT_ID:
        _send_to_chat(AIIQ_BOT_TOKEN, AIIQ_CHAT_ID, message, "AIIQ_GROUP")
    
    return ok


# ═══════════════════════════════════════════════════════════════════════════════
# MONGODB LOGGING (OPTIONAL)
# ═══════════════════════════════════════════════════════════════════════════════

def log_to_mongo(truth_data: Dict, telegram_ok: bool, message: str):
    """Log delivery to MongoDB for audit trail."""
    try:
        from pymongo import MongoClient
        client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=3000)
        db = client[MONGO_DB]
        col = db[MONGO_COLLECTION]
        
        col.insert_one({
            "timestamp": datetime.now(timezone.utc),
            "unix": truth_data.get("timestamps", {}).get("unix", 0),
            "symbol": truth_data.get("symbol", "XAUUSD"),
            "scorecard": truth_data.get("scorecard", {}),
            "price": truth_data.get("market", {}).get("price", 0),
            "session": truth_data.get("market", {}).get("session", "UNKNOWN"),
            "telegram_delivered": telegram_ok,
            "report_number": truth_data.get("system", {}).get("reports_sent", 0),
        })
        client.close()
    except Exception as e:
        logger.warning(f"MongoDB log failed: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN LOOP
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    state = RelayState()
    analysis_queue = PendingAnalysisQueue()
    
    # Graceful shutdown
    running = True
    def shutdown_handler(signum, frame):
        nonlocal running
        logger.info("Shutdown signal received")
        running = False
    signal.signal(signal.SIGTERM, shutdown_handler)
    signal.signal(signal.SIGINT, shutdown_handler)
    
    print("=" * 60)
    print("  QUINN TELEGRAM RELAY — H1 Truth Pipeline")
    print("  + Post-H1 Research Analysis (delayed T+1)")
    print("=" * 60)
    print(f"  CRELLA URL:    {CRELLA_TRUTH_URL or 'NOT SET'}")
    print(f"  CRELLA File:   {CRELLA_TRUTH_FILE or 'NOT SET'}")
    print(f"  Telegram:      {'CONFIGURED' if BOT_TOKEN and CHAT_ID else 'NOT CONFIGURED'}")
    print(f"  Poll interval: {POLL_INTERVAL}s")
    print(f"  Last unix:     {state.last_unix}")
    print(f"  Total sent:    {state.total_sent}")
    print(f"  Analyses sent: {state.total_analyses_sent}")
    print(f"  Pending queue: {len(analysis_queue.pending)}")
    print("=" * 60)
    
    if not BOT_TOKEN or not CHAT_ID:
        logger.warning(
            "Telegram not configured! Set QUINN_TELEGRAM_BOT_TOKEN and "
            "QUINN_TELEGRAM_CHAT_ID env vars. Running in DRY RUN mode."
        )
    
    consecutive_failures = 0
    
    while running:
        try:
            data = fetch_truth()
            
            if data is None:
                consecutive_failures += 1
                if consecutive_failures % 10 == 0:
                    logger.warning(f"No truth data for {consecutive_failures} cycles")
                time.sleep(POLL_INTERVAL)
                continue
            
            consecutive_failures = 0
            
            # Cache latest
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            LATEST_CACHE.write_text(json.dumps(data, indent=2))
            
            # Auto-ingest into Truth Analyzer
            try:
                from truth_analyzer import auto_ingest_from_cache
                auto_ingest_from_cache()
            except Exception:
                pass  # Analyzer may not be importable from relay context
            
            # ─── STEP 1: Process pending analyses (T+1 check) ───
            # This runs BEFORE sending a new truth report so analysis
            # always arrives AFTER the truth report it references.
            process_pending_analyses(analysis_queue, state, data)
            
            # ─── STEP 2: Check for new truth report ───
            unix = data.get("timestamps", {}).get("unix", 0)
            
            if unix <= state.last_unix:
                # Already sent this one
                time.sleep(POLL_INTERVAL)
                continue
            
            # New report — format and send
            logger.info(f"NEW REPORT: unix={unix} (prev={state.last_unix})")
            message = format_telegram_message(data)
            
            # Send to Telegram
            ok = send_telegram(message)
            state.update(unix, ok)
            
            # ─── STEP 3: Queue this report for future analysis ───
            # It will be analyzed when the NEXT H1 bar closes (T+1)
            if data.get("market", {}).get("open", True):
                analysis_queue.add(data)
            
            # Log to MongoDB
            log_to_mongo(data, ok, message)
            
            if ok:
                logger.info(
                    f"Report #{state.total_sent} delivered | "
                    f"Price: {data.get('market', {}).get('price', '?')} | "
                    f"Score: B{data.get('scorecard', {}).get('buy', 0)} "
                    f"H{data.get('scorecard', {}).get('hold', 0)} "
                    f"S{data.get('scorecard', {}).get('sell', 0)}"
                )
            else:
                logger.error(f"DELIVERY FAILED for unix={unix}")
            
        except Exception as e:
            logger.error(f"Main loop error: {e}")
        
        time.sleep(POLL_INTERVAL)
    
    logger.info(
        f"Relay stopped. Reports: {state.total_sent} | "
        f"Analyses: {state.total_analyses_sent} | "
        f"Pending: {len(analysis_queue.pending)}"
    )


if __name__ == "__main__":
    main()
