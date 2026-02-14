#!/usr/bin/env python3
"""
GLD OPTIONS SCANNER
═══════════════════
Scans GLD options chain to find optimal strikes for strangle setup.

Uses the dashboard's Fibonacci levels to identify:
  - Best call strike at/above resistance (100-127.2% Fib)
  - Best put strike at/below support (0-23.6% Fib)
  - Filters by volume, open interest, bid-ask spread
  - Targets 28-35 DTE, minimum 21 DTE
"""

import os
import json
import logging
import requests
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

logging.basicConfig(level=logging.INFO, format="%(asctime)s [SCANNER] %(message)s")
logger = logging.getLogger("gld_scanner")

DASHBOARD_URL = os.environ.get("GLD_DASHBOARD_URL", "http://localhost:8080")


def get_market_data() -> Dict:
    """Fetch market data from the GLD dashboard."""
    try:
        resp = requests.get(f"{DASHBOARD_URL}/api/market", timeout=10)
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        logger.error(f"Dashboard fetch error: {e}")
    return {}


def find_optimal_expiry(min_dte: int = 21, ideal_dte: int = 30,
                        max_dte: int = 45) -> str:
    """Find the next monthly options expiry within DTE range."""
    today = datetime.now()

    # Check next 3 months of third Fridays
    candidates = []
    for month_offset in range(0, 4):
        check_date = today + timedelta(days=30 * month_offset)
        year = check_date.year
        month = check_date.month

        # Find 3rd Friday of month
        first_day = datetime(year, month, 1)
        first_friday = first_day + timedelta(days=(4 - first_day.weekday()) % 7)
        third_friday = first_friday + timedelta(weeks=2)
        dte = (third_friday - today).days

        if min_dte <= dte <= max_dte:
            candidates.append((third_friday, dte))

    if not candidates:
        # Fallback: next month 3rd Friday regardless of range
        next_month = today + timedelta(days=32)
        first_day = datetime(next_month.year, next_month.month, 1)
        first_friday = first_day + timedelta(days=(4 - first_day.weekday()) % 7)
        third_friday = first_friday + timedelta(weeks=2)
        return third_friday.strftime("%Y-%m-%d")

    # Pick closest to ideal DTE
    best = min(candidates, key=lambda x: abs(x[1] - ideal_dte))
    return best[0].strftime("%Y-%m-%d")


def score_option(option: dict, target_strike: float,
                 option_type: str = "call") -> float:
    """
    Score an option contract for suitability.

    Factors:
      - Distance from target strike (closer = better, but must be OTM)
      - Bid-ask spread (tighter = better)
      - Volume and open interest (higher = better)
      - Premium cost (reasonable range)
    """
    score = 0.0
    strike = option.get("strike", 0)
    bid = option.get("bid", 0)
    ask = option.get("ask", 0)
    volume = option.get("volume", 0)
    oi = option.get("open_interest", 0)
    mid = option.get("mid", 0)

    # Must be OTM
    if option_type == "call" and strike < target_strike * 0.99:
        return -999  # ITM call — skip
    if option_type == "put" and strike > target_strike * 1.01:
        return -999  # ITM put — skip

    # Distance from target (within $3 is ideal)
    dist = abs(strike - target_strike)
    if dist <= 1:
        score += 30
    elif dist <= 2:
        score += 25
    elif dist <= 3:
        score += 20
    elif dist <= 5:
        score += 10
    else:
        score += max(0, 15 - dist)

    # Bid-ask spread (tighter = better)
    spread = ask - bid if ask > 0 and bid > 0 else 999
    if spread <= 0.05:
        score += 25
    elif spread <= 0.10:
        score += 20
    elif spread <= 0.20:
        score += 15
    elif spread <= 0.50:
        score += 5

    # Volume (higher = better liquidity)
    if volume >= 500:
        score += 20
    elif volume >= 100:
        score += 15
    elif volume >= 50:
        score += 10
    elif volume >= 10:
        score += 5

    # Open interest
    if oi >= 5000:
        score += 15
    elif oi >= 1000:
        score += 10
    elif oi >= 500:
        score += 5

    # Premium range (not too cheap, not too expensive)
    if 0.50 <= mid <= 5.00:
        score += 10
    elif 0.20 <= mid <= 10.00:
        score += 5

    return score


def scan_and_recommend(budget: float = 5000.0) -> Dict:
    """
    Scan GLD options and recommend strangle setup.

    Returns:
        Dict with recommended call/put strikes, quantities, costs
    """
    data = get_market_data()
    if not data:
        return {"error": "No market data available"}

    gld_price = data.get("gld_price", 0)
    fib = data.get("fibonacci", {})
    strangle = data.get("strangle", {})
    indicators = data.get("indicators", {})

    call_target = float(fib.get("127.2", gld_price * 1.01))
    put_target = float(fib.get("0.0", gld_price * 0.99))
    expiry = find_optimal_expiry()

    logger.info(f"Scanning GLD options for {expiry}")
    logger.info(f"  GLD: ${gld_price}, Call target: ${call_target}, Put target: ${put_target}")

    recommendation = {
        "timestamp": datetime.now().isoformat(),
        "gld_price": gld_price,
        "expiry": expiry,
        "fibonacci": fib,
        "indicators": indicators,
        "call_target_strike": call_target,
        "put_target_strike": put_target,
        "budget": budget,
        "call_allocation": budget * 0.60,
        "put_allocation": budget * 0.40,
        "call": strangle.get("call_strike"),
        "put": strangle.get("put_strike"),
        "call_qty": strangle.get("call_qty"),
        "put_qty": strangle.get("put_qty"),
        "total_cost_est": strangle.get("total_cost_est"),
        "note": "Run with Robinhood API for live chain scanning. "
                "Currently using dashboard estimates.",
    }

    return recommendation


if __name__ == "__main__":
    result = scan_and_recommend()
    print(json.dumps(result, indent=2))
