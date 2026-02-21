"""
HYDRA SCALPER — Vectorized Backtesting Engine
══════════════════════════════════════════════

High-speed backtester that simulates trade execution with:
- SL/TP hit detection using intra-bar high/low
- Breakeven logic
- Trailing stop
- Spread and slippage modeling
- Full metrics computation (Sortino, profit factor, max DD, etc.)

Designed to backtest 5 years × 7 pairs in < 60 seconds.
"""

import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple
import time

from .config import (
    StrategyParams, BacktestConfig, DEFAULT_PARAMS, DEFAULT_BACKTEST,
    PIP_SIZE, TYPICAL_SPREAD, PAIRS,
)
from .strategy import HydraScalper
from .risk import RiskManager


@dataclass
class Trade:
    """Represents a single completed trade."""
    pair: str
    direction: int          # 1=long, -1=short
    entry_price: float
    exit_price: float
    entry_time: object      # pd.Timestamp
    exit_time: object
    sl_price: float
    tp_price: float
    sl_pips: float
    tp_pips: float
    pnl_pips: float
    pnl_usd: float
    lots: float
    exit_reason: str        # "tp", "sl", "trail", "breakeven_sl", "session_close"
    bars_held: int


@dataclass
class BacktestResult:
    """Complete backtest results for one pair or portfolio."""
    pair: str
    trades: List[Trade] = field(default_factory=list)
    equity_curve: Optional[np.ndarray] = None
    total_pnl: float = 0.0
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    avg_pnl_pips: float = 0.0
    avg_win_pips: float = 0.0
    avg_loss_pips: float = 0.0
    max_drawdown_pct: float = 0.0
    sortino_ratio: float = 0.0
    sharpe_ratio: float = 0.0
    avg_bars_held: float = 0.0
    trades_per_day: float = 0.0
    expectancy_pips: float = 0.0
    max_consecutive_losses: int = 0
    backtest_time_sec: float = 0.0


class VectorizedBacktester:
    """
    High-speed vectorized backtesting engine.

    Simulates trade-by-trade execution from strategy signals,
    handling SL/TP/trail/breakeven using bar-by-bar high/low data.
    """

    def __init__(self, params: Optional[StrategyParams] = None,
                 bt_config: Optional[BacktestConfig] = None):
        self.params = params or DEFAULT_PARAMS
        self.bt_config = bt_config or DEFAULT_BACKTEST
        self.strategy = HydraScalper(self.params)

    def run_single_pair(self, df: pd.DataFrame, pair: str) -> BacktestResult:
        """
        Run backtest on a single pair's 1-minute data.

        Args:
            df: DataFrame with [open, high, low, close, volume], DatetimeIndex
            pair: Currency pair name (e.g., "EURUSD")

        Returns:
            BacktestResult with all metrics
        """
        t0 = time.time()
        pip = PIP_SIZE.get(pair, 0.0001)
        spread_pips = TYPICAL_SPREAD.get(pair, 1.5)
        spread = spread_pips * pip
        slippage = self.bt_config.slippage_pips * pip

        # Generate signals
        sig_df = self.strategy.generate_signals(df, pair)

        # Extract arrays
        opens   = sig_df["open"].values
        highs   = sig_df["high"].values
        lows    = sig_df["low"].values
        closes  = sig_df["close"].values
        signals = sig_df["signal"].values
        sl_arr  = sig_df["sl_price"].values
        tp_arr  = sig_df["tp_price"].values
        sl_pips = sig_df["sl_pips"].values
        tp_pips = sig_df["tp_pips"].values
        times   = sig_df.index
        in_sess = sig_df["in_session"].values
        atr_val = sig_df["atr"].values

        n = len(sig_df)
        p = self.params
        trades: List[Trade] = []
        equity = self.bt_config.initial_capital
        equity_curve = np.full(n, equity)

        # State tracking
        in_trade = False
        trade_dir = 0
        entry_price = 0.0
        entry_bar = 0
        current_sl = 0.0
        current_tp = 0.0
        current_sl_pips = 0.0
        current_tp_pips = 0.0
        be_triggered = False
        trail_active = False
        lots = 0.0

        risk_mgr = RiskManager(
            initial_capital=self.bt_config.initial_capital,
            risk_per_trade_pct=p.risk_per_trade_pct,
            max_daily_risk_pct=p.max_daily_risk_pct,
            kelly_fraction=p.kelly_fraction,
            use_dynamic_kelly=p.use_dynamic_kelly,
        )

        prev_day = None

        for i in range(1, n):
            current_day = times[i].date() if hasattr(times[i], 'date') else None

            # Reset daily risk at session start
            if current_day != prev_day:
                risk_mgr.reset_daily()
                prev_day = current_day

            if in_trade:
                # ── MANAGE OPEN POSITION ──
                bar_high = highs[i]
                bar_low = lows[i]
                bar_close = closes[i]
                exit_price = 0.0
                exit_reason = ""

                if trade_dir == 1:  # Long position
                    # Check SL hit (low touches SL)
                    if bar_low <= current_sl:
                        exit_price = current_sl - slippage
                        exit_reason = "trail" if trail_active else "sl"

                    # Check TP hit (high touches TP)
                    elif bar_high >= current_tp:
                        exit_price = current_tp - slippage  # Slight slippage on exit
                        exit_reason = "tp"

                    # Session close — force exit
                    elif not in_sess[i] and in_sess[i - 1]:
                        exit_price = bar_close - slippage
                        exit_reason = "session_close"

                    else:
                        # ── Breakeven logic ──
                        if not be_triggered:
                            profit_pips = (bar_high - entry_price) / pip
                            if profit_pips >= p.breakeven_pips:
                                current_sl = entry_price + 0.5 * pip  # 0.5 pip above entry
                                be_triggered = True

                        # ── Trailing stop logic ──
                        if not trail_active:
                            profit_pips = (bar_high - entry_price) / pip
                            if profit_pips >= p.trail_activation_pips:
                                trail_active = True

                        if trail_active:
                            trail_dist = p.trail_distance_atr * atr_val[i]
                            new_trail = bar_high - trail_dist
                            if new_trail > current_sl:
                                current_sl = new_trail

                else:  # Short position
                    if bar_high >= current_sl:
                        exit_price = current_sl + slippage
                        exit_reason = "trail" if trail_active else "sl"

                    elif bar_low <= current_tp:
                        exit_price = current_tp + slippage
                        exit_reason = "tp"

                    elif not in_sess[i] and in_sess[i - 1]:
                        exit_price = bar_close + slippage
                        exit_reason = "session_close"

                    else:
                        if not be_triggered:
                            profit_pips = (entry_price - bar_low) / pip
                            if profit_pips >= p.breakeven_pips:
                                current_sl = entry_price - 0.5 * pip
                                be_triggered = True

                        if not trail_active:
                            profit_pips = (entry_price - bar_low) / pip
                            if profit_pips >= p.trail_activation_pips:
                                trail_active = True

                        if trail_active:
                            trail_dist = p.trail_distance_atr * atr_val[i]
                            new_trail = bar_low + trail_dist
                            if new_trail < current_sl:
                                current_sl = new_trail

                # ── CLOSE TRADE ──
                if exit_price > 0:
                    pnl_pips_val = trade_dir * (exit_price - entry_price) / pip
                    # Subtract spread cost
                    pnl_pips_val -= spread_pips
                    pnl_usd = pnl_pips_val * pip * lots * self.bt_config.lot_size

                    trade = Trade(
                        pair=pair,
                        direction=trade_dir,
                        entry_price=entry_price,
                        exit_price=exit_price,
                        entry_time=times[entry_bar],
                        exit_time=times[i],
                        sl_price=current_sl,
                        tp_price=current_tp,
                        sl_pips=current_sl_pips,
                        tp_pips=current_tp_pips,
                        pnl_pips=pnl_pips_val,
                        pnl_usd=pnl_usd,
                        lots=lots,
                        exit_reason=exit_reason,
                        bars_held=i - entry_bar,
                    )
                    trades.append(trade)
                    risk_mgr.update_equity(pnl_usd)
                    equity = risk_mgr.state.equity
                    in_trade = False

            # ── OPEN NEW POSITION ──
            if not in_trade and signals[i] != 0 and risk_mgr.can_trade(p.max_daily_trades):
                sig = signals[i]
                sl_p = sl_pips[i]
                tp_p = tp_pips[i]

                if np.isnan(sl_p) or np.isnan(tp_p):
                    equity_curve[i] = equity
                    continue

                lots = risk_mgr.calculate_position_size(sl_p, pair)

                if sig == 1:  # Long
                    entry_price = closes[i] + spread / 2 + slippage
                    current_sl = entry_price - sl_p * pip
                    current_tp = entry_price + tp_p * pip
                else:  # Short
                    entry_price = closes[i] - spread / 2 - slippage
                    current_sl = entry_price + sl_p * pip
                    current_tp = entry_price - tp_p * pip

                trade_dir = sig
                entry_bar = i
                current_sl_pips = sl_p
                current_tp_pips = tp_p
                be_triggered = False
                trail_active = False
                in_trade = True

            equity_curve[i] = equity

        # ── COMPUTE METRICS ──
        result = self._compute_metrics(trades, equity_curve, pair, time.time() - t0)
        return result

    def run_portfolio(self, data: Dict[str, pd.DataFrame]) -> Dict[str, BacktestResult]:
        """
        Run backtest on multiple pairs.

        Args:
            data: Dict mapping pair names to DataFrames

        Returns:
            Dict mapping pair names to BacktestResult, plus "PORTFOLIO" key
        """
        results = {}
        all_trades = []
        t0 = time.time()

        for pair, df in data.items():
            print(f"  Backtesting {pair}... ", end="", flush=True)
            result = self.run_single_pair(df, pair)
            results[pair] = result
            all_trades.extend(result.trades)
            print(f"{result.total_trades} trades, WR={result.win_rate:.1f}%, "
                  f"PF={result.profit_factor:.2f}")

        # Portfolio aggregate
        if all_trades:
            # Sort all trades by time for combined equity curve
            all_trades.sort(key=lambda t: t.entry_time)
            portfolio = self._compute_metrics(
                all_trades, None, "PORTFOLIO", time.time() - t0
            )
            results["PORTFOLIO"] = portfolio

        return results

    def _compute_metrics(self, trades: List[Trade],
                         equity_curve: Optional[np.ndarray],
                         pair: str, elapsed: float) -> BacktestResult:
        """Compute all performance metrics from trade list."""
        result = BacktestResult(pair=pair, backtest_time_sec=elapsed)

        if not trades:
            return result

        result.trades = trades
        result.total_trades = len(trades)

        pnls = np.array([t.pnl_pips for t in trades])
        pnl_usd = np.array([t.pnl_usd for t in trades])
        bars = np.array([t.bars_held for t in trades])

        wins = pnls > 0
        losses = pnls <= 0

        result.wins = int(np.sum(wins))
        result.losses = int(np.sum(losses))
        result.win_rate = result.wins / result.total_trades * 100

        result.avg_pnl_pips = float(np.mean(pnls))
        result.total_pnl = float(np.sum(pnl_usd))

        if result.wins > 0:
            result.avg_win_pips = float(np.mean(pnls[wins]))
        if result.losses > 0:
            result.avg_loss_pips = float(np.mean(pnls[losses]))

        # Profit factor
        gross_profit = float(np.sum(pnls[wins])) if result.wins > 0 else 0
        gross_loss = float(np.abs(np.sum(pnls[losses]))) if result.losses > 0 else 0.001
        result.profit_factor = gross_profit / gross_loss if gross_loss > 0 else 99.9

        # Max consecutive losses
        max_consec = 0
        current_consec = 0
        for p in pnls:
            if p <= 0:
                current_consec += 1
                max_consec = max(max_consec, current_consec)
            else:
                current_consec = 0
        result.max_consecutive_losses = max_consec

        # Average bars held
        result.avg_bars_held = float(np.mean(bars))

        # Trades per day
        if len(trades) >= 2:
            first_time = trades[0].entry_time
            last_time = trades[-1].entry_time
            if hasattr(first_time, 'timestamp'):
                days = (last_time - first_time).total_seconds() / 86400
            else:
                days = 252  # Fallback
            if days > 0:
                result.trades_per_day = result.total_trades / days

        # Expectancy
        result.expectancy_pips = result.avg_pnl_pips

        # Equity curve metrics
        if equity_curve is not None and len(equity_curve) > 0:
            # Max drawdown
            peak = np.maximum.accumulate(equity_curve)
            dd = (peak - equity_curve) / np.where(peak > 0, peak, 1) * 100
            result.max_drawdown_pct = float(np.max(dd))

        # Sortino ratio (annualized, using daily PnL proxy)
        if len(pnl_usd) > 1:
            daily_returns = pnl_usd / self.bt_config.initial_capital
            mean_return = np.mean(daily_returns)
            downside = daily_returns[daily_returns < 0]
            if len(downside) > 0:
                downside_std = np.std(downside)
                if downside_std > 0:
                    # Scale per-trade to approximate daily
                    trades_per_day_est = max(result.trades_per_day, 1)
                    daily_mean = mean_return * trades_per_day_est
                    daily_downside_std = downside_std * np.sqrt(trades_per_day_est)
                    result.sortino_ratio = float(
                        daily_mean / daily_downside_std * np.sqrt(252)
                    )

            # Sharpe ratio
            std_return = np.std(daily_returns)
            if std_return > 0:
                trades_per_day_est = max(result.trades_per_day, 1)
                daily_mean = mean_return * trades_per_day_est
                daily_std = std_return * np.sqrt(trades_per_day_est)
                result.sharpe_ratio = float(
                    daily_mean / daily_std * np.sqrt(252)
                )

        result.equity_curve = equity_curve
        return result

    @staticmethod
    def print_results(results: Dict[str, BacktestResult]):
        """Pretty-print backtest results."""
        print("\n" + "═" * 80)
        print("  HYDRA SCALPER — BACKTEST RESULTS")
        print("═" * 80)

        for pair, r in results.items():
            print(f"\n{'─' * 60}")
            print(f"  {pair}")
            print(f"{'─' * 60}")
            print(f"  Total Trades:     {r.total_trades}")
            print(f"  Wins / Losses:    {r.wins} / {r.losses}")
            print(f"  Win Rate:         {r.win_rate:.1f}%")
            print(f"  Profit Factor:    {r.profit_factor:.2f}")
            print(f"  Total PnL:        ${r.total_pnl:,.2f}")
            print(f"  Avg PnL/trade:    {r.avg_pnl_pips:.2f} pips")
            print(f"  Avg Win:          {r.avg_win_pips:.2f} pips")
            print(f"  Avg Loss:         {r.avg_loss_pips:.2f} pips")
            print(f"  Max Drawdown:     {r.max_drawdown_pct:.2f}%")
            print(f"  Sortino Ratio:    {r.sortino_ratio:.2f}")
            print(f"  Sharpe Ratio:     {r.sharpe_ratio:.2f}")
            print(f"  Avg Hold (bars):  {r.avg_bars_held:.1f}")
            print(f"  Trades/Day:       {r.trades_per_day:.1f}")
            print(f"  Expectancy:       {r.expectancy_pips:.2f} pips")
            print(f"  Max Consec Loss:  {r.max_consecutive_losses}")
            print(f"  Backtest Time:    {r.backtest_time_sec:.2f}s")

        print("\n" + "═" * 80)
