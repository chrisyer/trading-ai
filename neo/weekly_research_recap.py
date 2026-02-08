#!/usr/bin/env python3
"""
═══════════════════════════════════════════════════════════════════════════════
NEO WEEKLY RESEARCH RECAP — After-the-Fact XAUUSD Analysis
═══════════════════════════════════════════════════════════════════════════════

ROLE:
  QUINN, coordinating NEO as a research auditor.
  Generates a WEEKLY RESEARCH RECAP for XAUUSD after markets close.

  NOT a trading signal. NOT predictive. NOT allowed to influence live trading.

WHEN:
  - Runs ONCE per week (Saturday UTC, after Friday market close)
  - Never during live market hours
  - Never blocks hourly truth reports

DATA SOURCES:
  1. Internal: Truth reports, outcome labels, combination stats
  2. External: Ollama-summarized YouTube/news/sentiment (read-only)

ABSOLUTE CONSTRAINTS:
  - NO BUY/SELL language
  - NO strategy ranking by profit
  - NO cherry-picking
  - NO threshold optimization
  - NO future certainty
  - Weak evidence → say so explicitly

Author: QUINN001
Created: February 8, 2026
═══════════════════════════════════════════════════════════════════════════════
"""

import os
import json
import time
import math
import logging
import asyncio
import requests
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from collections import defaultdict

# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

DATA_DIR = Path("/home/jbot/trading_ai/neo/truth_data")
HISTORY_FILE = DATA_DIR / "truth_history.json"
REPORTS_DIR = DATA_DIR / "reports"
WEEKLY_STATE_FILE = DATA_DIR / "weekly_recap_state.json"

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("WEEKLY_OLLAMA_MODEL", "dolphin3")
OLLAMA_FALLBACK = "mistral:7b"
OLLAMA_TIMEOUT = 120  # seconds

# Telegram (reuse existing bot config)
BOT_TOKEN = os.environ.get(
    "TELEGRAM_BOT_TOKEN",
    "8250652030:AAFd4x8NsTfdaB3O67lUnMhotT2XY61600s"
)
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "6776619257")
AIIQ_BOT_TOKEN = "7956358189:AAHLEIAWRnwi6Jz9eKORPVi99eP8jbwOF4w"
AIIQ_CHAT_ID = os.environ.get("AIIQ_CHAT_ID", "-1003582558817")

# MongoDB
MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB = "quinn_trading"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [WEEKLY-RECAP] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("weekly_recap")


# ═══════════════════════════════════════════════════════════════════════════════
# SCHEDULING — Weekend Only
# ═══════════════════════════════════════════════════════════════════════════════

def is_weekend() -> bool:
    """Check if current UTC time is Saturday or Sunday."""
    now = datetime.now(timezone.utc)
    return now.weekday() in (5, 6)  # Saturday=5, Sunday=6


def get_week_end_date() -> str:
    """Get the Friday date ending this trading week."""
    now = datetime.now(timezone.utc)
    # Walk back to most recent Friday
    days_since_friday = (now.weekday() - 4) % 7
    friday = now - timedelta(days=days_since_friday)
    return friday.strftime("%Y-%m-%d")


def already_ran_this_week() -> bool:
    """Check if weekly recap already ran for this week."""
    if not WEEKLY_STATE_FILE.exists():
        return False
    try:
        state = json.loads(WEEKLY_STATE_FILE.read_text())
        last_week = state.get("last_week_ending", "")
        return last_week == get_week_end_date()
    except:
        return False


def mark_ran():
    """Mark that this week's recap has been generated."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    WEEKLY_STATE_FILE.write_text(json.dumps({
        "last_week_ending": get_week_end_date(),
        "ran_at": datetime.now(timezone.utc).isoformat(),
    }, indent=2))


# ═══════════════════════════════════════════════════════════════════════════════
# INTERNAL DATA — Read from Truth History
# ═══════════════════════════════════════════════════════════════════════════════

def load_week_records() -> List[Dict]:
    """Load truth records from the past trading week (Mon-Fri)."""
    if not HISTORY_FILE.exists():
        return []

    try:
        all_records = json.loads(HISTORY_FILE.read_text())
    except:
        return []

    now = datetime.now(timezone.utc)
    # Find the Monday of this week
    days_since_monday = now.weekday()
    if now.weekday() >= 5:  # Weekend
        days_since_monday = now.weekday()
    monday = now - timedelta(days=days_since_monday)
    monday_start = monday.replace(hour=0, minute=0, second=0, microsecond=0)
    monday_unix = int(monday_start.timestamp())

    # Friday close (approx 22:00 UTC)
    friday = monday_start + timedelta(days=4, hours=22)
    friday_unix = int(friday.timestamp())

    week_records = [
        r for r in all_records
        if monday_unix <= r.get("unix", 0) <= friday_unix
    ]

    logger.info(f"Loaded {len(week_records)} records for week ending {get_week_end_date()}")
    return week_records


def analyze_internal(records: List[Dict]) -> Dict:
    """Compute internal week statistics from truth records."""
    result = {
        "total_reports": len(records),
        "labeled": 0,
        "success_long": 0,
        "success_short": 0,
        "neutral": 0,
        "unlabeled": 0,
        "avg_mfe": 0,
        "avg_mae": 0,
        "mfe_mae_ratio": 0,
        "session_breakdown": {},
        "regime_breakdown": {},
        "stable_combos": [],
        "fragile_combos": [],
        "predictability_note": "",
    }

    if not records:
        return result

    labeled = [r for r in records if r.get("outcome")]
    result["labeled"] = len(labeled)
    result["unlabeled"] = len(records) - len(labeled)
    result["success_long"] = sum(1 for r in labeled if r["outcome"] == "SUCCESS_LONG")
    result["success_short"] = sum(1 for r in labeled if r["outcome"] == "SUCCESS_SHORT")
    result["neutral"] = sum(1 for r in labeled if r["outcome"] == "NEUTRAL")

    # MFE / MAE
    if labeled:
        mfes = [r.get("mfe", 0) for r in labeled]
        maes = [r.get("mae", 0) for r in labeled]
        result["avg_mfe"] = round(sum(mfes) / len(mfes), 2)
        result["avg_mae"] = round(sum(maes) / len(maes), 2)
        if result["avg_mae"] > 0:
            result["mfe_mae_ratio"] = round(result["avg_mfe"] / result["avg_mae"], 2)

    # Session breakdown
    sessions = defaultdict(lambda: {"count": 0, "success": 0})
    for r in labeled:
        sess = r.get("session", "UNKNOWN")
        sessions[sess]["count"] += 1
        if r["outcome"] in ("SUCCESS_LONG", "SUCCESS_SHORT"):
            sessions[sess]["success"] += 1
    result["session_breakdown"] = {
        k: {"count": v["count"], "win_rate": round(v["success"] / v["count"] * 100, 1) if v["count"] > 0 else 0}
        for k, v in sessions.items()
    }

    # Regime breakdown
    regimes = defaultdict(lambda: {"count": 0, "success": 0})
    for r in labeled:
        reg = r.get("regime", "UNKNOWN")
        regimes[reg]["count"] += 1
        if r["outcome"] in ("SUCCESS_LONG", "SUCCESS_SHORT"):
            regimes[reg]["success"] += 1
    result["regime_breakdown"] = {
        k: {"count": v["count"], "win_rate": round(v["success"] / v["count"] * 100, 1) if v["count"] > 0 else 0}
        for k, v in regimes.items()
    }

    # Combination analysis (4 key metrics)
    combo_keys = ["vwap", "ichimoku", "h4_bias", "regime"]
    combos = defaultdict(lambda: {"count": 0, "success": 0, "mfes": [], "maes": []})
    for r in labeled:
        sig = "|".join(f"{k}={r.get(k, '?')}" for k in combo_keys)
        combos[sig]["count"] += 1
        combos[sig]["mfes"].append(r.get("mfe", 0))
        combos[sig]["maes"].append(r.get("mae", 0))
        if r["outcome"] in ("SUCCESS_LONG", "SUCCESS_SHORT"):
            combos[sig]["success"] += 1

    for sig, data in combos.items():
        n = data["count"]
        if n < 3:
            continue
        win_rate = data["success"] / n
        avg_mfe = sum(data["mfes"]) / n
        avg_mae = sum(data["maes"]) / n

        entry = {
            "signature": sig,
            "count": n,
            "win_rate": round(win_rate * 100, 1),
            "avg_mfe": round(avg_mfe, 2),
            "avg_mae": round(avg_mae, 2),
        }

        # Classify as stable or fragile
        mfe_std = 0
        if n > 1:
            mfe_std = math.sqrt(sum((x - avg_mfe) ** 2 for x in data["mfes"]) / (n - 1))
        cv = mfe_std / avg_mfe if avg_mfe > 0 else 999

        if cv < 0.5 and win_rate > 0.4:
            result["stable_combos"].append(entry)
        elif cv > 0.8 or win_rate < 0.25:
            entry["reason"] = f"CV={cv:.2f}, high variance" if cv > 0.8 else "Low win rate"
            result["fragile_combos"].append(entry)

    # Sort by win rate
    result["stable_combos"].sort(key=lambda x: x["win_rate"], reverse=True)
    result["fragile_combos"].sort(key=lambda x: x["win_rate"])

    # Predictability note
    total_labeled = len(labeled)
    if total_labeled < 10:
        result["predictability_note"] = f"Insufficient data ({total_labeled} labeled records). Cannot assess predictability."
    else:
        total_wins = result["success_long"] + result["success_short"]
        base_rate = total_wins / total_labeled
        if base_rate > 0.55:
            result["predictability_note"] = f"Slightly above random ({base_rate:.0%} win rate). Requires more weeks to confirm."
        elif base_rate < 0.35:
            result["predictability_note"] = f"Below random ({base_rate:.0%} win rate). Conditions may have been unfavorable."
        else:
            result["predictability_note"] = f"Near random ({base_rate:.0%} win rate). No clear edge detected this week."

    return result


# ═══════════════════════════════════════════════════════════════════════════════
# EXTERNAL CONTEXT — Ollama-Summarized (Read-Only)
# ═══════════════════════════════════════════════════════════════════════════════

EXTERNAL_CONTEXT_PROMPT = """You are a financial research summarizer. You do NOT provide trading advice.

Summarize the dominant market narratives for XAUUSD (Gold) from the past week.
Today is {date}. The trading week just ended.

Answer ONLY these 3 questions in 1-2 sentences each:

1. YOUTUBE NARRATIVES: What were the dominant gold/XAUUSD themes from popular financial YouTube channels this week? (e.g., rate cuts, geopolitical risk, dollar weakness, new highs)

2. NEWS THEMES: What were the 2-3 biggest gold/macro news headlines? (e.g., FOMC, NFP, CPI, geopolitical events)

3. SOCIAL SENTIMENT: Based on the general tone of financial social media, was gold sentiment this week BULLISH, BEARISH, or MIXED?

Format your response EXACTLY as:
YOUTUBE: <your summary>
NEWS: <your summary>
SENTIMENT: <BULLISH or BEARISH or MIXED>

Keep it factual. No opinions. No trading recommendations."""


def fetch_external_context() -> Dict:
    """
    Query Ollama for a summary of external market narratives.
    This is a READ-ONLY summary — never injected into metrics or labels.
    """
    context = {
        "youtube": "Unavailable",
        "news": "Unavailable",
        "sentiment": "INCONCLUSIVE",
        "available": False,
    }

    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    prompt = EXTERNAL_CONTEXT_PROMPT.format(date=date_str)

    for model in [OLLAMA_MODEL, OLLAMA_FALLBACK]:
        try:
            logger.info(f"Fetching external context via {model}...")
            resp = requests.post(
                f"{OLLAMA_URL}/api/generate",
                json={
                    "model": model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {"temperature": 0.3, "num_predict": 500},
                },
                timeout=OLLAMA_TIMEOUT,
            )

            if resp.status_code != 200:
                logger.warning(f"{model} returned {resp.status_code}")
                continue

            raw = resp.json().get("response", "").strip()
            if not raw:
                continue

            # Parse structured response
            for line in raw.split("\n"):
                line = line.strip()
                if line.upper().startswith("YOUTUBE:"):
                    context["youtube"] = line[8:].strip()
                elif line.upper().startswith("NEWS:"):
                    context["news"] = line[5:].strip()
                elif line.upper().startswith("SENTIMENT:"):
                    sent = line[10:].strip().upper()
                    if "BULL" in sent:
                        context["sentiment"] = "BULLISH"
                    elif "BEAR" in sent:
                        context["sentiment"] = "BEARISH"
                    elif "MIX" in sent:
                        context["sentiment"] = "MIXED"

            context["available"] = True
            logger.info(f"External context fetched via {model}")
            return context

        except requests.Timeout:
            logger.warning(f"{model} timed out")
        except Exception as e:
            logger.warning(f"{model} failed: {e}")

    logger.warning("External context unavailable from all models")
    return context


# ═══════════════════════════════════════════════════════════════════════════════
# ALIGNMENT CHECK
# ═══════════════════════════════════════════════════════════════════════════════

def compute_alignment(internal: Dict, external: Dict) -> str:
    """
    Check if external sentiment aligned with actual outcomes.
    Returns ALIGNED / DIVERGENT / INCONCLUSIVE.
    """
    sentiment = external.get("sentiment", "INCONCLUSIVE")
    if sentiment == "INCONCLUSIVE" or not external.get("available"):
        return "INCONCLUSIVE — external context unavailable"

    long_wins = internal.get("success_long", 0)
    short_wins = internal.get("success_short", 0)

    if long_wins == 0 and short_wins == 0:
        return "INCONCLUSIVE — no labeled outcomes"

    # Determine dominant outcome direction
    if long_wins > short_wins * 1.5:
        dominant_outcome = "BULLISH"
    elif short_wins > long_wins * 1.5:
        dominant_outcome = "BEARISH"
    else:
        dominant_outcome = "MIXED"

    if sentiment == dominant_outcome:
        return f"ALIGNED — sentiment ({sentiment}) matched dominant outcomes ({dominant_outcome})"
    elif dominant_outcome == "MIXED":
        return f"INCONCLUSIVE — outcomes were mixed, sentiment was {sentiment}"
    else:
        return f"DIVERGENT — sentiment ({sentiment}) vs outcomes ({dominant_outcome})"


# ═══════════════════════════════════════════════════════════════════════════════
# RESEARCH TAKEAWAYS
# ═══════════════════════════════════════════════════════════════════════════════

def generate_takeaways(internal: Dict) -> List[str]:
    """Generate honest, boring research takeaways."""
    takeaways = []

    total = internal.get("labeled", 0)
    if total < 5:
        return [
            "Insufficient data to generate takeaways.",
            "Continue collecting H1 truth reports.",
        ]

    # What appeared repeatable
    stable = internal.get("stable_combos", [])
    if stable:
        best = stable[0]
        takeaways.append(
            f"Most repeatable pattern: {best['signature']} "
            f"({best['count']} samples, {best['win_rate']}% win rate)"
        )
    else:
        takeaways.append("No metric combinations showed stable repeatability this week")

    # What lost predictability
    fragile = internal.get("fragile_combos", [])
    if fragile:
        worst = fragile[0]
        takeaways.append(
            f"Lost predictability: {worst['signature']} "
            f"({worst.get('reason', 'high variance')})"
        )

    # MFE/MAE asymmetry
    ratio = internal.get("mfe_mae_ratio", 0)
    if ratio > 1.5:
        takeaways.append(
            f"Favorable asymmetry: MFE/MAE ratio {ratio:.1f}x — "
            f"favorable moves outpaced adverse ones"
        )
    elif ratio < 0.7:
        takeaways.append(
            f"Unfavorable asymmetry: MFE/MAE ratio {ratio:.1f}x — "
            f"adverse moves exceeded favorable ones"
        )

    # Predictability note
    note = internal.get("predictability_note", "")
    if note:
        takeaways.append(note)

    # Uncertainty warning
    if total < 30:
        takeaways.append(
            f"Sample size ({total}) remains small. "
            f"All observations may be noise."
        )

    return takeaways


# ═══════════════════════════════════════════════════════════════════════════════
# TELEGRAM FORMATTING (STRICT)
# ═══════════════════════════════════════════════════════════════════════════════

def format_weekly_recap(internal: Dict, external: Dict, alignment: str, takeaways: List[str]) -> str:
    """Format the weekly recap in the EXACT specified Telegram format."""
    week_end = get_week_end_date()

    lines = [
        "\U0001f4c5 XAUUSD WEEKLY RESEARCH RECAP",
        f"Week Ending: {week_end}",
        f"Market State: CLOSED",
        "",
    ]

    # Internal Observations
    lines.append("\U0001f4ca INTERNAL OBSERVATIONS")
    lines.append(f"  H1 Reports Analyzed: {internal['total_reports']}")
    lines.append(f"  Labeled: {internal['labeled']} | Unlabeled: {internal['unlabeled']}")
    lines.append(f"  Outcome Distribution:")
    lines.append(f"    SUCCESS_LONG:  {internal['success_long']}")
    lines.append(f"    SUCCESS_SHORT: {internal['success_short']}")
    lines.append(f"    NEUTRAL:       {internal['neutral']}")
    if internal.get("avg_mfe"):
        lines.append(f"  Avg MFE: {internal['avg_mfe']} | Avg MAE: {internal['avg_mae']} | Ratio: {internal['mfe_mae_ratio']}x")
    lines.append("")

    # Combination Stability
    lines.append("\U0001f9e9 COMBINATION STABILITY")
    stable = internal.get("stable_combos", [])
    if stable:
        lines.append("Most Stable Contexts:")
        for s in stable[:3]:
            lines.append(f"  \u2022 {s['signature']}")
            lines.append(f"    {s['count']} samples, {s['win_rate']}% win rate, MFE {s['avg_mfe']}")
    else:
        lines.append("  No combinations met stability threshold this week")

    fragile = internal.get("fragile_combos", [])
    if fragile:
        lines.append("Fragile / Failed Contexts:")
        for f in fragile[:3]:
            lines.append(f"  \u2022 {f['signature']}")
            lines.append(f"    {f.get('reason', 'High variance or low win rate')}")
    lines.append("")

    # Time & Regime Notes
    lines.append("\u23f1\ufe0f TIME & REGIME NOTES")
    sessions = internal.get("session_breakdown", {})
    if sessions:
        best_sess = max(sessions.items(), key=lambda x: x[1]["win_rate"]) if sessions else None
        worst_sess = min(sessions.items(), key=lambda x: x[1]["win_rate"]) if sessions else None
        if best_sess and best_sess[1]["count"] >= 3:
            lines.append(f"  Best session: {best_sess[0]} ({best_sess[1]['win_rate']}% win, n={best_sess[1]['count']})")
        if worst_sess and worst_sess[0] != (best_sess[0] if best_sess else None) and worst_sess[1]["count"] >= 3:
            lines.append(f"  Worst session: {worst_sess[0]} ({worst_sess[1]['win_rate']}% win, n={worst_sess[1]['count']})")

    regimes = internal.get("regime_breakdown", {})
    if regimes:
        for reg, data in regimes.items():
            if data["count"] >= 3:
                lines.append(f"  {reg}: {data['win_rate']}% win rate (n={data['count']})")
    if not sessions and not regimes:
        lines.append("  Insufficient data for session/regime analysis")
    lines.append("")

    # External Context
    lines.append("\U0001f4f0 EXTERNAL CONTEXT (RESEARCH ONLY)")
    lines.append(f"  YouTube narratives: {external.get('youtube', 'Unavailable')}")
    lines.append(f"  News themes: {external.get('news', 'Unavailable')}")
    lines.append(f"  Social sentiment: {external.get('sentiment', 'INCONCLUSIVE')}")
    lines.append("")

    # Alignment Check
    lines.append("\u26a0\ufe0f ALIGNMENT CHECK")
    lines.append(f"  External sentiment vs outcomes: {alignment}")
    lines.append("")

    # Research Takeaways
    lines.append("\U0001f9e0 RESEARCH TAKEAWAYS")
    for t in takeaways:
        lines.append(f"  \u2022 {t}")
    lines.append("")

    # Disclaimer
    lines.append("\U0001f4cc IMPORTANT")
    lines.append("This recap is AFTER-THE-FACT research.")
    lines.append("No trade recommendations.")
    lines.append("Confidence increases only with time and sample size.")

    return "\n".join(lines)


def format_skip_message(reason: str) -> str:
    """Format a skip message when recap cannot be generated."""
    return (
        "\U0001f4c5 XAUUSD WEEKLY RESEARCH RECAP\n"
        f"Week Ending: {get_week_end_date()}\n"
        f"Market State: CLOSED\n"
        "\n"
        f"\u26a0\ufe0f Weekly research skipped: {reason}\n"
        "\n"
        "This is expected during the first week of data collection\n"
        "or when the truth pipeline was not active."
    )


# ═══════════════════════════════════════════════════════════════════════════════
# TELEGRAM DELIVERY
# ═══════════════════════════════════════════════════════════════════════════════

def send_telegram(message: str) -> bool:
    """Send to Admin DM + AiiQ group."""
    ok = False
    for token, chat_id, label in [
        (BOT_TOKEN, CHAT_ID, "ADMIN"),
        (AIIQ_BOT_TOKEN, AIIQ_CHAT_ID, "AIIQ_GROUP"),
    ]:
        if not token or not chat_id:
            continue
        try:
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            r = requests.post(url, json={
                "chat_id": chat_id,
                "text": message,
                "disable_web_page_preview": True,
            }, timeout=15)
            if r.status_code == 200:
                msg_id = r.json().get("result", {}).get("message_id", "?")
                logger.info(f"[{label}] Weekly recap delivered: msg_id={msg_id}")
                ok = True
            else:
                logger.error(f"[{label}] Telegram {r.status_code}: {r.text[:200]}")
        except Exception as e:
            logger.error(f"[{label}] Send failed: {e}")
    return ok


# ═══════════════════════════════════════════════════════════════════════════════
# MONGODB LOGGING
# ═══════════════════════════════════════════════════════════════════════════════

def log_to_mongo(internal: Dict, external: Dict, alignment: str, takeaways: List[str]):
    """Log weekly recap to MongoDB."""
    try:
        from pymongo import MongoClient
        client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=3000)
        db = client[MONGO_DB]
        col = db["weekly_research_recaps"]
        col.insert_one({
            "week_ending": get_week_end_date(),
            "generated_at": datetime.now(timezone.utc),
            "internal": internal,
            "external": external,
            "alignment": alignment,
            "takeaways": takeaways,
        })
        client.close()
        logger.info("Weekly recap logged to MongoDB")
    except Exception as e:
        logger.warning(f"MongoDB log failed: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN EXECUTION
# ═══════════════════════════════════════════════════════════════════════════════

def generate_weekly_recap(force: bool = False) -> Tuple[bool, str]:
    """
    Generate and send the weekly research recap.
    Returns (success, message_text).
    """
    # Guard: only on weekends unless forced
    if not force and not is_weekend():
        return False, "Not a weekend — skipping"

    # Guard: already ran this week
    if not force and already_ran_this_week():
        return False, "Already generated this week's recap"

    logger.info(f"Generating weekly research recap for week ending {get_week_end_date()}")

    try:
        # Load week's data
        records = load_week_records()

        if not records:
            msg = format_skip_message("No truth reports collected this week")
            send_telegram(msg)
            mark_ran()
            return True, msg

        # Phase 1: Internal analysis
        internal = analyze_internal(records)

        # Phase 2: External context (via Ollama)
        external = fetch_external_context()

        # Phase 3: Alignment check
        alignment = compute_alignment(internal, external)

        # Phase 4: Takeaways
        takeaways = generate_takeaways(internal)

        # Format message
        message = format_weekly_recap(internal, external, alignment, takeaways)

        # Send to Telegram
        ok = send_telegram(message)

        if not ok:
            # Fail-safe: silence is NOT allowed
            skip_msg = format_skip_message("Telegram delivery failed")
            send_telegram(skip_msg)

        # Log
        log_to_mongo(internal, external, alignment, takeaways)

        # Save report to file
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        report_file = REPORTS_DIR / f"weekly_recap_{get_week_end_date()}.txt"
        report_file.write_text(message)
        logger.info(f"Report saved: {report_file}")

        mark_ran()
        return True, message

    except Exception as e:
        logger.error(f"Weekly recap failed: {e}")
        # FAIL-SAFE: always post something
        skip_msg = format_skip_message(f"Generation error: {str(e)[:100]}")
        send_telegram(skip_msg)
        mark_ran()
        return False, skip_msg


# ═══════════════════════════════════════════════════════════════════════════════
# FASTAPI ROUTER (Mounts on NEO API port 8036)
# ═══════════════════════════════════════════════════════════════════════════════

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

weekly_recap_router = APIRouter(tags=["Weekly Research Recap"])


@weekly_recap_router.get("/api/neo/truth/weekly/status")
async def weekly_status():
    """Status of the weekly research recap system."""
    return {
        "status": "online",
        "role": "WEEKLY RESEARCH RECAP — after-the-fact analysis, no signals",
        "is_weekend": is_weekend(),
        "week_ending": get_week_end_date(),
        "already_ran_this_week": already_ran_this_week(),
        "history_records": _count_records(),
    }


@weekly_recap_router.post("/api/neo/truth/weekly/generate")
async def weekly_generate():
    """
    Force-generate the weekly recap (bypasses weekend check).
    Use for testing or manual trigger.
    """
    ok, message = generate_weekly_recap(force=True)
    return {
        "status": "generated" if ok else "failed",
        "week_ending": get_week_end_date(),
        "message_length": len(message),
        "telegram_sent": ok,
    }


@weekly_recap_router.get("/api/neo/truth/weekly/report", response_class=PlainTextResponse)
async def weekly_report():
    """Get the latest weekly recap as plain text (or generate if missing)."""
    report_file = REPORTS_DIR / f"weekly_recap_{get_week_end_date()}.txt"
    if report_file.exists():
        return report_file.read_text()

    # Generate on demand
    ok, message = generate_weekly_recap(force=True)
    return message


def _count_records() -> int:
    if not HISTORY_FILE.exists():
        return 0
    try:
        return len(json.loads(HISTORY_FILE.read_text()))
    except:
        return 0


# ═══════════════════════════════════════════════════════════════════════════════
# STANDALONE RUNNER (for cron or manual execution)
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys

    force = "--force" in sys.argv

    if not force and not is_weekend():
        print("Not a weekend. Use --force to override.")
        sys.exit(0)

    if not force and already_ran_this_week():
        print("Already ran this week's recap.")
        sys.exit(0)

    ok, msg = generate_weekly_recap(force=force)
    if ok:
        print("Weekly recap generated and sent.")
    else:
        print(f"Failed: {msg}")
