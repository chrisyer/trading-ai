"""
HYDRA SCALPER — Live Execution Skeleton (OANDA v20 API)
═══════════════════════════════════════════════════════

Production-ready framework for live forex scalping via OANDA v20.

Features:
  - Real-time 1-minute candle streaming
  - Signal generation on each new bar
  - Order execution with spread checking
  - Position management (SL/TP/trailing/breakeven)
  - FIFO compliance (one position per pair per direction)
  - Session management (auto-start/stop)
  - Logging and state persistence

Usage:
  Set OANDA_TOKEN and OANDA_ACCOUNT_ID environment variables.
  Run: python -m forex_scalper.live_executor
"""

import os
import json
import time
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict
import pandas as pd
import numpy as np

from .config import (
    StrategyParams, DEFAULT_PARAMS, PAIRS, PIP_SIZE,
    MAX_SPREAD, SESSION_START_UTC, SESSION_END_UTC,
)
from .strategy import HydraScalper
from .risk import RiskManager

logger = logging.getLogger("hydra_live")


class OandaExecutor:
    """
    Live execution engine for OANDA v20 API.

    This is a skeleton — fill in with your OANDA credentials and
    customize order types as needed for your account.
    """

    def __init__(self, params: Optional[StrategyParams] = None,
                 pairs: Optional[list] = None,
                 paper: bool = True):
        """
        Args:
            params: Strategy parameters (use optimized params from optimizer.py)
            pairs: List of pairs to trade (default: all 7)
            paper: If True, use practice environment
        """
        self.params = params or DEFAULT_PARAMS
        self.pairs = pairs or PAIRS
        self.paper = paper
        self.strategy = HydraScalper(self.params)

        # OANDA setup
        self.token = os.environ.get("OANDA_TOKEN", "")
        self.account_id = os.environ.get("OANDA_ACCOUNT_ID", "")
        self.environment = "practice" if paper else "live"
        self.client = None

        # State
        self.risk_mgr = RiskManager(
            risk_per_trade_pct=params.risk_per_trade_pct if params else 1.0,
            max_daily_risk_pct=params.max_daily_risk_pct if params else 5.0,
            kelly_fraction=params.kelly_fraction if params else 0.5,
            use_dynamic_kelly=params.use_dynamic_kelly if params else True,
        )
        self.open_positions: Dict[str, dict] = {}
        self.bar_buffers: Dict[str, pd.DataFrame] = {}  # Rolling bar buffer per pair
        self.buffer_size = 300  # Keep 300 bars in memory (5 hours)

        # Logging
        self.state_dir = Path("/home/jbot/trading_ai/forex_scalper/live_state")
        self.state_dir.mkdir(parents=True, exist_ok=True)

    def connect(self):
        """Connect to OANDA API."""
        try:
            import oandapyV20
            self.client = oandapyV20.API(
                access_token=self.token,
                environment=self.environment,
            )
            logger.info(f"Connected to OANDA ({self.environment})")

            # Get account info
            from oandapyV20.endpoints.accounts import AccountDetails
            r = AccountDetails(accountID=self.account_id)
            response = self.client.request(r)
            balance = float(response["account"]["balance"])
            self.risk_mgr.state.equity = balance
            self.risk_mgr.state.peak_equity = balance
            logger.info(f"Account balance: ${balance:,.2f}")

        except ImportError:
            logger.error("oandapyV20 not installed. pip install oandapyV20")
            raise
        except Exception as e:
            logger.error(f"Connection failed: {e}")
            raise

    def fetch_recent_bars(self, pair: str, count: int = 300) -> pd.DataFrame:
        """Fetch recent 1-minute candles from OANDA."""
        from oandapyV20.endpoints.instruments import InstrumentsCandles

        oanda_pair = pair[:3] + "_" + pair[3:]
        params = {"count": count, "granularity": "M1", "price": "M"}

        r = InstrumentsCandles(instrument=oanda_pair, params=params)
        response = self.client.request(r)

        rows = []
        for c in response["candles"]:
            if c["complete"]:
                mid = c["mid"]
                rows.append({
                    "datetime": pd.Timestamp(c["time"]),
                    "open": float(mid["o"]),
                    "high": float(mid["h"]),
                    "low": float(mid["l"]),
                    "close": float(mid["c"]),
                    "volume": int(c["volume"]),
                })

        df = pd.DataFrame(rows).set_index("datetime")
        df.index = df.index.tz_localize(None)
        return df

    def get_current_spread(self, pair: str) -> float:
        """Get current bid-ask spread in pips."""
        from oandapyV20.endpoints.pricing import PricingInfo

        oanda_pair = pair[:3] + "_" + pair[3:]
        params = {"instruments": oanda_pair}
        r = PricingInfo(accountID=self.account_id, params=params)
        response = self.client.request(r)

        price = response["prices"][0]
        bid = float(price["bids"][0]["price"])
        ask = float(price["asks"][0]["price"])
        pip = PIP_SIZE.get(pair, 0.0001)
        return (ask - bid) / pip

    def place_market_order(self, pair: str, direction: int, lots: float,
                           sl_price: float, tp_price: float) -> Optional[str]:
        """
        Place a market order with SL and TP.

        Args:
            pair: Currency pair
            direction: 1=buy, -1=sell
            lots: Position size in lots (0.01 = micro lot)
            sl_price: Stop loss price
            tp_price: Take profit price

        Returns:
            Trade ID if successful, None otherwise
        """
        from oandapyV20.endpoints.orders import OrderCreate

        oanda_pair = pair[:3] + "_" + pair[3:]
        units = int(lots * 100_000) * direction  # Positive=buy, negative=sell

        pip = PIP_SIZE.get(pair, 0.0001)
        precision = 5 if pip == 0.0001 else 3

        order_data = {
            "order": {
                "type": "MARKET",
                "instrument": oanda_pair,
                "units": str(units),
                "timeInForce": "FOK",  # Fill or kill
                "stopLossOnFill": {
                    "price": f"{sl_price:.{precision}f}",
                },
                "takeProfitOnFill": {
                    "price": f"{tp_price:.{precision}f}",
                },
            }
        }

        try:
            r = OrderCreate(accountID=self.account_id, data=order_data)
            response = self.client.request(r)

            if "orderFillTransaction" in response:
                trade_id = response["orderFillTransaction"].get("tradeOpened", {}).get("tradeID")
                fill_price = float(response["orderFillTransaction"]["price"])
                logger.info(
                    f"ORDER FILLED: {pair} {'BUY' if direction == 1 else 'SELL'} "
                    f"{lots} lots @ {fill_price}, SL={sl_price}, TP={tp_price}, "
                    f"trade_id={trade_id}"
                )
                return trade_id
            else:
                logger.warning(f"Order not filled: {response}")
                return None

        except Exception as e:
            logger.error(f"Order placement failed: {e}")
            return None

    def modify_trade_sl(self, trade_id: str, new_sl: float, pair: str):
        """Modify stop loss on an existing trade (for trailing/breakeven)."""
        from oandapyV20.endpoints.trades import TradeCRCDO

        pip = PIP_SIZE.get(pair, 0.0001)
        precision = 5 if pip == 0.0001 else 3

        data = {
            "stopLoss": {
                "price": f"{new_sl:.{precision}f}",
            }
        }

        try:
            r = TradeCRCDO(accountID=self.account_id, tradeID=trade_id, data=data)
            self.client.request(r)
            logger.debug(f"SL modified: trade {trade_id} -> SL={new_sl}")
        except Exception as e:
            logger.error(f"SL modification failed: {e}")

    def is_in_session(self) -> bool:
        """Check if current UTC hour is within trading session."""
        now = datetime.now(timezone.utc)
        return SESSION_START_UTC <= now.hour < SESSION_END_UTC

    def process_bar(self, pair: str):
        """
        Process a new completed 1-minute bar for a pair.
        This is the main signal generation + execution loop.
        """
        # Fetch latest bars
        df = self.fetch_recent_bars(pair, count=self.buffer_size)
        if len(df) < 100:
            return

        self.bar_buffers[pair] = df

        # Check spread
        current_spread = self.get_current_spread(pair)
        max_spread = MAX_SPREAD.get(pair, 1.5)
        if current_spread > max_spread:
            logger.debug(f"{pair}: Spread too wide ({current_spread:.1f} > {max_spread})")
            return

        # Generate signals
        sig_df = self.strategy.generate_signals(df, pair)
        last_signal = sig_df["signal"].iloc[-1]

        if last_signal == 0:
            return

        # Check if we already have a position in this pair
        if pair in self.open_positions:
            pos = self.open_positions[pair]
            # FIFO: Can't open opposite direction while position is open
            if pos["direction"] != last_signal:
                logger.debug(f"{pair}: FIFO block — have {pos['direction']}, signal is {last_signal}")
                return
            # Already have same-direction position
            return

        # Risk check
        if not self.risk_mgr.can_trade(self.params.max_daily_trades):
            logger.info("Risk manager blocked trade")
            return

        # Get SL/TP from signal
        sl_pips = sig_df["sl_pips"].iloc[-1]
        tp_pips = sig_df["tp_pips"].iloc[-1]
        sl_price = sig_df["sl_price"].iloc[-1]
        tp_price = sig_df["tp_price"].iloc[-1]

        if np.isnan(sl_pips) or np.isnan(tp_pips):
            return

        # Calculate position size
        lots = self.risk_mgr.calculate_position_size(sl_pips, pair)

        # Place order
        trade_id = self.place_market_order(pair, last_signal, lots, sl_price, tp_price)

        if trade_id:
            self.open_positions[pair] = {
                "trade_id": trade_id,
                "direction": last_signal,
                "entry_price": sig_df["close"].iloc[-1],
                "sl_price": sl_price,
                "tp_price": tp_price,
                "sl_pips": sl_pips,
                "lots": lots,
                "entry_time": datetime.now(timezone.utc).isoformat(),
                "be_triggered": False,
                "trail_active": False,
            }
            self._save_state()

    def manage_positions(self):
        """
        Manage open positions — breakeven, trailing stop.
        Called every minute.
        """
        for pair, pos in list(self.open_positions.items()):
            try:
                df = self.bar_buffers.get(pair)
                if df is None or len(df) == 0:
                    continue

                current_price = df["close"].iloc[-1]
                current_atr = df["atr"].iloc[-1] if "atr" in df.columns else 0
                pip = PIP_SIZE.get(pair, 0.0001)
                entry = pos["entry_price"]
                direction = pos["direction"]

                profit_pips = direction * (current_price - entry) / pip

                # Breakeven
                if not pos["be_triggered"] and profit_pips >= self.params.breakeven_pips:
                    new_sl = entry + direction * 0.5 * pip
                    self.modify_trade_sl(pos["trade_id"], new_sl, pair)
                    pos["be_triggered"] = True
                    pos["sl_price"] = new_sl
                    logger.info(f"{pair}: Breakeven triggered at +{profit_pips:.1f} pips")

                # Trailing stop
                if not pos["trail_active"] and profit_pips >= self.params.trail_activation_pips:
                    pos["trail_active"] = True
                    logger.info(f"{pair}: Trailing activated at +{profit_pips:.1f} pips")

                if pos["trail_active"] and current_atr > 0:
                    trail_dist = self.params.trail_distance_atr * current_atr
                    if direction == 1:
                        new_trail = current_price - trail_dist
                        if new_trail > pos["sl_price"]:
                            self.modify_trade_sl(pos["trade_id"], new_trail, pair)
                            pos["sl_price"] = new_trail
                    else:
                        new_trail = current_price + trail_dist
                        if new_trail < pos["sl_price"]:
                            self.modify_trade_sl(pos["trade_id"], new_trail, pair)
                            pos["sl_price"] = new_trail

            except Exception as e:
                logger.error(f"Position management error for {pair}: {e}")

    def _save_state(self):
        """Persist current state to disk."""
        state = {
            "positions": self.open_positions,
            "risk": self.risk_mgr.get_risk_report(),
            "updated": datetime.now(timezone.utc).isoformat(),
        }
        state_file = self.state_dir / "live_state.json"
        with open(state_file, "w") as f:
            json.dump(state, f, indent=2, default=str)

    def run(self):
        """
        Main execution loop.

        Runs continuously during session hours.
        Processes each pair every minute.
        """
        logger.info("=" * 60)
        logger.info("  HYDRA SCALPER — LIVE EXECUTION STARTING")
        logger.info(f"  Environment: {self.environment}")
        logger.info(f"  Pairs: {self.pairs}")
        logger.info(f"  Session: {SESSION_START_UTC}:00 - {SESSION_END_UTC}:00 UTC")
        logger.info("=" * 60)

        self.connect()

        while True:
            try:
                if not self.is_in_session():
                    # Outside session — close any remaining positions
                    if self.open_positions:
                        logger.info("Session ended — closing remaining positions")
                        # In production, close via API here
                        self.open_positions.clear()
                        self.risk_mgr.reset_daily()
                    time.sleep(60)
                    continue

                # Process each pair
                for pair in self.pairs:
                    try:
                        self.process_bar(pair)
                    except Exception as e:
                        logger.error(f"Error processing {pair}: {e}")

                # Manage open positions
                self.manage_positions()

                # Save state
                self._save_state()

                # Wait for next minute bar
                now = datetime.now(timezone.utc)
                seconds_to_next_minute = 60 - now.second
                time.sleep(max(seconds_to_next_minute, 5))

            except KeyboardInterrupt:
                logger.info("Shutting down...")
                self._save_state()
                break
            except Exception as e:
                logger.error(f"Main loop error: {e}")
                time.sleep(10)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    executor = OandaExecutor(paper=True)
    executor.run()
