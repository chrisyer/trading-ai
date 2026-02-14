#!/usr/bin/env python3
"""
ROBINHOOD GLD OPTIONS EXECUTOR — Strangle Edition
══════════════════════════════════════════════════

Reads strangle recommendations from the GLD Dashboard and executes
on Robinhood via robin_stocks API.

Features:
  - Strangle order execution (calls + puts)
  - Profit target monitoring (100-150% gain)
  - Roll alerts (10 DTE threshold)
  - PDT compliance (3 day trades per 5 rolling days)
  - Position tracking with P&L
  - Alert system for key events

IMPORTANT: Requires robin_stocks library and Robinhood credentials
stored as environment variables (ROBINHOOD_USER, ROBINHOOD_PASS, ROBINHOOD_MFA).
"""

import os
import json
import time
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, Dict, List

logging.basicConfig(level=logging.INFO, format="%(asctime)s [RH-EXEC] %(message)s")
logger = logging.getLogger("rh_executor")

# Configuration
POSITIONS_FILE = Path("/home/jbot/trading_ai/gld_dashboard/positions.json")
TRADES_LOG = Path("/home/jbot/trading_ai/robinhood_bridge/trades_log.json")
ALERT_WEBHOOK = os.environ.get("ALERT_WEBHOOK", "")

# Strategy constants
PROFIT_TARGET_PCT = 100.0     # Close at 100% gain
PROFIT_TARGET_MAX = 150.0     # Hard close at 150% gain
ROLL_THRESHOLD_DTE = 10       # Roll at 10 DTE
MAX_BUYING_POWER_PCT = 50.0   # Never use more than 50% buying power
PDT_LIMIT = 3                 # Max 3 day trades per 5 rolling days


class RobinhoodBridge:
    """
    Robinhood API bridge for GLD options trading.

    Uses robin_stocks library for API access.
    Credentials from environment variables.
    """

    def __init__(self):
        self.logged_in = False
        self.positions = self._load_positions()
        self.day_trades = []  # Track day trade timestamps
        self.rh = None

    def login(self):
        """Authenticate with Robinhood."""
        try:
            import robin_stocks.robinhood as rh
            self.rh = rh

            username = os.environ.get("ROBINHOOD_USER", "")
            password = os.environ.get("ROBINHOOD_PASS", "")
            mfa_code = os.environ.get("ROBINHOOD_MFA", "")

            if not username or not password:
                logger.error("ROBINHOOD_USER and ROBINHOOD_PASS env vars required")
                return False

            if mfa_code:
                rh.login(username, password, mfa_code=mfa_code)
            else:
                rh.login(username, password)

            self.logged_in = True
            logger.info("Robinhood login successful")

            # Get account info
            profile = rh.profiles.load_account_profile()
            buying_power = float(profile.get("buying_power", 0))
            logger.info(f"Buying power: ${buying_power:,.2f}")

            return True

        except ImportError:
            logger.error("robin_stocks not installed. pip install robin_stocks")
            return False
        except Exception as e:
            logger.error(f"Login failed: {e}")
            return False

    def get_buying_power(self) -> float:
        """Get current options buying power."""
        if not self.logged_in:
            return 0
        try:
            profile = self.rh.profiles.load_account_profile()
            return float(profile.get("buying_power", 0))
        except Exception as e:
            logger.error(f"Error getting buying power: {e}")
            return 0

    def get_options_chain(self, expiry: str) -> Dict:
        """
        Get GLD options chain for a specific expiry.

        Args:
            expiry: Date string "YYYY-MM-DD"

        Returns:
            Dict with 'calls' and 'puts' lists
        """
        if not self.logged_in:
            return {"calls": [], "puts": []}

        try:
            chain = self.rh.options.find_options_by_expiration(
                "GLD", expirationDate=expiry, optionType="call"
            )
            calls = [
                {
                    "strike": float(o["strike_price"]),
                    "bid": float(o.get("bid_price", 0) or 0),
                    "ask": float(o.get("ask_price", 0) or 0),
                    "mid": round((float(o.get("bid_price", 0) or 0) +
                                  float(o.get("ask_price", 0) or 0)) / 2, 2),
                    "volume": int(o.get("volume", 0) or 0),
                    "open_interest": int(o.get("open_interest", 0) or 0),
                    "iv": float(o.get("implied_volatility", 0) or 0),
                    "delta": float(o.get("delta", 0) or 0),
                    "id": o.get("id", ""),
                }
                for o in chain if o
            ]

            chain_puts = self.rh.options.find_options_by_expiration(
                "GLD", expirationDate=expiry, optionType="put"
            )
            puts = [
                {
                    "strike": float(o["strike_price"]),
                    "bid": float(o.get("bid_price", 0) or 0),
                    "ask": float(o.get("ask_price", 0) or 0),
                    "mid": round((float(o.get("bid_price", 0) or 0) +
                                  float(o.get("ask_price", 0) or 0)) / 2, 2),
                    "volume": int(o.get("volume", 0) or 0),
                    "open_interest": int(o.get("open_interest", 0) or 0),
                    "iv": float(o.get("implied_volatility", 0) or 0),
                    "delta": float(o.get("delta", 0) or 0),
                    "id": o.get("id", ""),
                }
                for o in chain_puts if o
            ]

            return {"calls": calls, "puts": puts}

        except Exception as e:
            logger.error(f"Options chain error: {e}")
            return {"calls": [], "puts": []}

    def execute_strangle(self, call_strike: float, put_strike: float,
                         call_qty: int, put_qty: int, expiry: str,
                         max_call_premium: float = 0,
                         max_put_premium: float = 0) -> Dict:
        """
        Execute a strangle: buy OTM calls + OTM puts.

        Args:
            call_strike: Call option strike price
            put_strike: Put option strike price
            call_qty: Number of call contracts
            put_qty: Number of put contracts
            expiry: Expiration date "YYYY-MM-DD"
            max_call_premium: Max price per call contract (limit order)
            max_put_premium: Max price per put contract (limit order)

        Returns:
            Dict with execution results
        """
        if not self.logged_in:
            return {"status": "error", "message": "Not logged in"}

        # PDT check
        if not self._pdt_check():
            return {"status": "blocked", "message": "PDT limit reached (3 day trades in 5 days)"}

        # Buying power check
        bp = self.get_buying_power()
        total_cost = (call_qty * max_call_premium + put_qty * max_put_premium) * 100
        max_allowed = bp * MAX_BUYING_POWER_PCT / 100
        if total_cost > max_allowed:
            return {
                "status": "blocked",
                "message": f"Cost ${total_cost:.0f} exceeds {MAX_BUYING_POWER_PCT}% "
                           f"of buying power (${max_allowed:.0f})"
            }

        results = {"call": None, "put": None, "status": "partial"}

        # Execute call leg
        try:
            if max_call_premium > 0:
                call_order = self.rh.orders.order_buy_option_limit(
                    "open", "debit", max_call_premium,
                    "GLD", call_qty, expiry, call_strike, "call"
                )
            else:
                call_order = self.rh.orders.order_buy_option_limit(
                    "open", "debit", 999,  # Market-like
                    "GLD", call_qty, expiry, call_strike, "call"
                )
            results["call"] = {
                "order_id": call_order.get("id", ""),
                "status": call_order.get("state", "unknown"),
                "strike": call_strike,
                "qty": call_qty,
                "premium": max_call_premium,
            }
            logger.info(f"Call order placed: {call_strike} x{call_qty} @ ${max_call_premium}")
        except Exception as e:
            logger.error(f"Call order failed: {e}")
            results["call"] = {"error": str(e)}

        # Execute put leg
        try:
            if max_put_premium > 0:
                put_order = self.rh.orders.order_buy_option_limit(
                    "open", "debit", max_put_premium,
                    "GLD", put_qty, expiry, put_strike, "put"
                )
            else:
                put_order = self.rh.orders.order_buy_option_limit(
                    "open", "debit", 999,
                    "GLD", put_qty, expiry, put_strike, "put"
                )
            results["put"] = {
                "order_id": put_order.get("id", ""),
                "status": put_order.get("state", "unknown"),
                "strike": put_strike,
                "qty": put_qty,
                "premium": max_put_premium,
            }
            logger.info(f"Put order placed: {put_strike} x{put_qty} @ ${max_put_premium}")
        except Exception as e:
            logger.error(f"Put order failed: {e}")
            results["put"] = {"error": str(e)}

        # Track position
        if results["call"] and "error" not in results["call"] and \
           results["put"] and "error" not in results["put"]:
            results["status"] = "filled"
            self._track_position(results, expiry)

        # Log trade
        self._log_trade(results)
        return results

    def close_leg(self, option_type: str, strike: float, qty: int,
                  expiry: str, limit_price: float = 0) -> Dict:
        """Close one leg of a strangle (profit taking)."""
        if not self.logged_in:
            return {"status": "error", "message": "Not logged in"}

        try:
            if limit_price > 0:
                order = self.rh.orders.order_sell_option_limit(
                    "close", "credit", limit_price,
                    "GLD", qty, expiry, strike, option_type
                )
            else:
                order = self.rh.orders.order_sell_option_limit(
                    "close", "credit", 0.01,  # Market-like
                    "GLD", qty, expiry, strike, option_type
                )

            logger.info(f"Close order: {option_type} {strike} x{qty}")
            self._send_alert(
                f"[GLD] CLOSED {option_type.upper()} ${strike} x{qty} @ ${limit_price}"
            )
            return {"status": "ok", "order": order}

        except Exception as e:
            logger.error(f"Close order failed: {e}")
            return {"status": "error", "message": str(e)}

    def monitor_positions(self) -> List[Dict]:
        """
        Monitor open positions for:
        1. Profit targets (100-150%)
        2. Roll alerts (10 DTE)
        3. P&L updates
        """
        alerts = []
        positions = self._load_positions()

        if not self.logged_in or not positions:
            return alerts

        try:
            # Get current options positions from Robinhood
            rh_positions = self.rh.options.get_open_option_positions()

            for pos in positions:
                if pos.get("status") == "closed":
                    continue

                # Find matching RH position
                entry_premium = pos.get("entry_premium", 0)
                if entry_premium <= 0:
                    continue

                # Get current price
                try:
                    option_data = self.rh.options.get_option_market_data_by_id(
                        pos.get("option_id", "")
                    )
                    if option_data:
                        current_price = float(option_data[0].get("adjusted_mark_price", 0))
                    else:
                        current_price = entry_premium  # Fallback
                except Exception:
                    current_price = entry_premium

                gain_pct = ((current_price - entry_premium) / entry_premium) * 100
                pos["current_price"] = current_price
                pos["gain_pct"] = round(gain_pct, 1)

                # Profit target check
                if gain_pct >= PROFIT_TARGET_MAX:
                    alert = f"[GLD] PROFIT TARGET HIT: {pos['type']} ${pos['strike']} " \
                            f"+{gain_pct:.0f}% — CLOSE NOW"
                    alerts.append({"type": "profit_max", "message": alert, "position": pos})
                    self._send_alert(alert)

                elif gain_pct >= PROFIT_TARGET_PCT:
                    alert = f"[GLD] PROFIT TARGET: {pos['type']} ${pos['strike']} " \
                            f"+{gain_pct:.0f}% — Consider closing"
                    alerts.append({"type": "profit_target", "message": alert, "position": pos})
                    self._send_alert(alert)

                # Roll check (DTE)
                expiry = datetime.strptime(pos.get("expiry", "2099-12-31"), "%Y-%m-%d")
                dte = (expiry - datetime.now()).days
                pos["dte"] = dte

                if dte <= ROLL_THRESHOLD_DTE:
                    alert = f"[GLD] ROLL ALERT: {pos['type']} ${pos['strike']} " \
                            f"— {dte} DTE remaining. Roll or close."
                    alerts.append({"type": "roll_alert", "message": alert, "position": pos})
                    self._send_alert(alert)

            # Save updated positions
            self._save_positions(positions)

        except Exception as e:
            logger.error(f"Position monitor error: {e}")

        return alerts

    def _pdt_check(self) -> bool:
        """Check Pattern Day Trader rule compliance."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=5)
        recent = [t for t in self.day_trades if t > cutoff]
        self.day_trades = recent
        return len(recent) < PDT_LIMIT

    def _track_position(self, results: dict, expiry: str):
        """Track a new strangle position."""
        positions = self._load_positions()

        strangle_id = f"strangle_{int(time.time())}"

        if results.get("call") and "error" not in results["call"]:
            positions.append({
                "id": f"{strangle_id}_call",
                "strangle_id": strangle_id,
                "type": "call",
                "strike": results["call"]["strike"],
                "qty": results["call"]["qty"],
                "entry_premium": results["call"]["premium"],
                "expiry": expiry,
                "opened_at": datetime.now(timezone.utc).isoformat(),
                "status": "open",
                "option_id": results["call"].get("order_id", ""),
            })

        if results.get("put") and "error" not in results["put"]:
            positions.append({
                "id": f"{strangle_id}_put",
                "strangle_id": strangle_id,
                "type": "put",
                "strike": results["put"]["strike"],
                "qty": results["put"]["qty"],
                "entry_premium": results["put"]["premium"],
                "expiry": expiry,
                "opened_at": datetime.now(timezone.utc).isoformat(),
                "status": "open",
                "option_id": results["put"].get("order_id", ""),
            })

        self._save_positions(positions)

    def _load_positions(self) -> list:
        if POSITIONS_FILE.exists():
            try:
                return json.loads(POSITIONS_FILE.read_text())
            except Exception:
                return []
        return []

    def _save_positions(self, positions: list):
        POSITIONS_FILE.write_text(json.dumps(positions, indent=2))

    def _log_trade(self, trade: dict):
        trades = []
        if TRADES_LOG.exists():
            try:
                trades = json.loads(TRADES_LOG.read_text())
            except Exception:
                trades = []
        trade["timestamp"] = datetime.now(timezone.utc).isoformat()
        trades.append(trade)
        TRADES_LOG.write_text(json.dumps(trades, indent=2))

    def _send_alert(self, message: str):
        """Send alert via webhook."""
        logger.info(f"ALERT: {message}")
        if not ALERT_WEBHOOK:
            return
        try:
            if "discord" in ALERT_WEBHOOK:
                requests.post(ALERT_WEBHOOK, json={"content": message}, timeout=10)
            elif "telegram" in ALERT_WEBHOOK or "api.telegram" in ALERT_WEBHOOK:
                # Extract chat_id and token from URL or use env vars
                import requests as req
                bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
                chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
                if bot_token and chat_id:
                    req.post(
                        f"https://api.telegram.org/bot{bot_token}/sendMessage",
                        json={"chat_id": chat_id, "text": message},
                        timeout=10,
                    )
            else:
                requests.post(ALERT_WEBHOOK, json={"text": message}, timeout=10)
        except Exception as e:
            logger.error(f"Alert send failed: {e}")


def run_monitor():
    """Run position monitor loop."""
    bridge = RobinhoodBridge()
    if not bridge.login():
        logger.error("Cannot start monitor — login failed")
        return

    logger.info("Position monitor starting — checking every 60 seconds")

    while True:
        try:
            alerts = bridge.monitor_positions()
            if alerts:
                for a in alerts:
                    logger.info(f"Alert: {a['type']} — {a['message']}")
        except Exception as e:
            logger.error(f"Monitor loop error: {e}")

        time.sleep(60)


if __name__ == "__main__":
    run_monitor()
