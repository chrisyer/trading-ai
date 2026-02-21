"""
HYDRA SCALPER — Risk & Position Sizing Module
Dynamic Kelly, volatility targeting, drawdown protection.
"""

import numpy as np
from dataclasses import dataclass
from typing import Optional


@dataclass
class RiskState:
    """Tracks current risk exposure."""
    equity: float = 100_000.0
    peak_equity: float = 100_000.0
    daily_pnl: float = 0.0
    daily_trades: int = 0
    open_risk_pct: float = 0.0       # Sum of risk % for open positions
    consecutive_losses: int = 0
    win_count: int = 0
    loss_count: int = 0
    total_win_pnl: float = 0.0
    total_loss_pnl: float = 0.0


class RiskManager:
    """
    Position sizing and risk management engine.

    Implements:
    1. Fixed fractional risk (base)
    2. Dynamic Kelly criterion (optional upgrade)
    3. Volatility-targeted sizing
    4. Drawdown circuit breaker
    5. Daily risk budget
    """

    def __init__(self, initial_capital: float = 100_000.0,
                 risk_per_trade_pct: float = 1.0,
                 max_daily_risk_pct: float = 5.0,
                 kelly_fraction: float = 0.5,
                 use_dynamic_kelly: bool = True,
                 max_dd_pct: float = 8.0,
                 leverage: int = 200,
                 lot_size: float = 100_000,
                 min_lot: float = 0.01,
                 max_lot: float = 10.0):

        self.initial_capital = initial_capital
        self.base_risk_pct = risk_per_trade_pct
        self.max_daily_risk_pct = max_daily_risk_pct
        self.kelly_fraction = kelly_fraction
        self.use_dynamic_kelly = use_dynamic_kelly
        self.max_dd_pct = max_dd_pct
        self.leverage = leverage
        self.lot_size = lot_size
        self.min_lot = min_lot
        self.max_lot = max_lot

        self.state = RiskState(
            equity=initial_capital,
            peak_equity=initial_capital,
        )

    def reset_daily(self):
        """Reset daily counters (call at session start)."""
        self.state.daily_pnl = 0.0
        self.state.daily_trades = 0
        self.state.open_risk_pct = 0.0

    def update_equity(self, pnl: float):
        """Update after trade close."""
        self.state.equity += pnl
        self.state.daily_pnl += pnl
        self.state.peak_equity = max(self.state.peak_equity, self.state.equity)

        if pnl > 0:
            self.state.win_count += 1
            self.state.total_win_pnl += pnl
            self.state.consecutive_losses = 0
        else:
            self.state.loss_count += 1
            self.state.total_loss_pnl += abs(pnl)
            self.state.consecutive_losses += 1

        self.state.daily_trades += 1

    @property
    def current_drawdown_pct(self) -> float:
        """Current drawdown from peak equity."""
        if self.state.peak_equity <= 0:
            return 0.0
        return (1.0 - self.state.equity / self.state.peak_equity) * 100.0

    @property
    def win_rate(self) -> float:
        """Historical win rate."""
        total = self.state.win_count + self.state.loss_count
        if total == 0:
            return 0.5  # Assume 50% if no data
        return self.state.win_count / total

    @property
    def avg_win_loss_ratio(self) -> float:
        """Average win / average loss ratio."""
        if self.state.loss_count == 0 or self.state.total_loss_pnl == 0:
            return 2.0  # Default assumption
        avg_win = self.state.total_win_pnl / max(self.state.win_count, 1)
        avg_loss = self.state.total_loss_pnl / max(self.state.loss_count, 1)
        if avg_loss == 0:
            return 2.0
        return avg_win / avg_loss

    def kelly_optimal_fraction(self) -> float:
        """
        Kelly criterion: f* = (p * b - q) / b
        where p = win rate, q = 1-p, b = avg win/loss ratio
        """
        p = self.win_rate
        q = 1.0 - p
        b = self.avg_win_loss_ratio
        if b <= 0:
            return 0.0
        kelly = (p * b - q) / b
        # Apply fractional Kelly for safety
        return max(0.0, min(kelly * self.kelly_fraction, 0.05))  # Cap at 5%

    def can_trade(self, max_daily_trades: int = 150) -> bool:
        """Check if we're allowed to open a new trade."""
        # Circuit breaker: max drawdown
        if self.current_drawdown_pct >= self.max_dd_pct:
            return False

        # Daily risk budget
        daily_risk_used = abs(self.state.daily_pnl / max(self.state.equity, 1)) * 100
        if daily_risk_used >= self.max_daily_risk_pct:
            return False

        # Daily trade limit
        if self.state.daily_trades >= max_daily_trades:
            return False

        # Consecutive loss cooldown: reduce after 5 straight losses
        if self.state.consecutive_losses >= 5:
            return False

        return True

    def calculate_position_size(self, sl_pips: float, pair: str,
                                pip_value: float = 0.0) -> float:
        """
        Calculate lot size for a trade.

        Args:
            sl_pips: Stop loss in pips
            pair: Currency pair name
            pip_value: USD value per pip per standard lot (auto-detected if 0)

        Returns:
            Lot size (e.g., 0.5 = half a standard lot)
        """
        if sl_pips <= 0:
            return self.min_lot

        # Auto-detect pip value per standard lot
        # For XXX/USD pairs: $10 per pip per lot
        # For USD/XXX pairs: ~$10 / price per pip per lot (approximate)
        # For JPY pairs: different pip size
        if pip_value <= 0:
            if pair.endswith("USD"):
                pip_value = 10.0  # Direct: 1 pip = $10 per standard lot
            elif pair.startswith("USD"):
                pip_value = 7.5   # Indirect: approximate $7.50 per pip
            else:
                pip_value = 10.0  # Default assumption

        # Determine risk percentage
        if self.use_dynamic_kelly and (self.state.win_count + self.state.loss_count) >= 30:
            risk_pct = self.kelly_optimal_fraction() * 100.0
        else:
            risk_pct = self.base_risk_pct

        # Scale down if in drawdown
        dd = self.current_drawdown_pct
        if dd > 4.0:
            risk_pct *= 0.5  # Half size in deep drawdown
        elif dd > 2.0:
            risk_pct *= 0.75

        # Scale down after consecutive losses
        if self.state.consecutive_losses >= 3:
            risk_pct *= 0.5

        # Calculate dollar risk
        risk_dollars = self.state.equity * (risk_pct / 100.0)

        # Convert to lots: risk_dollars / (sl_pips * pip_value_per_lot)
        risk_per_lot = sl_pips * pip_value
        if risk_per_lot <= 0:
            return self.min_lot

        lots = risk_dollars / risk_per_lot

        # Clamp
        lots = max(self.min_lot, min(lots, self.max_lot))

        # Check leverage constraint
        max_notional = self.state.equity * self.leverage
        max_lots_leverage = max_notional / self.lot_size
        lots = min(lots, max_lots_leverage)

        return round(lots, 2)

    def get_risk_report(self) -> dict:
        """Get current risk state summary."""
        total_trades = self.state.win_count + self.state.loss_count
        return {
            "equity": round(self.state.equity, 2),
            "peak_equity": round(self.state.peak_equity, 2),
            "drawdown_pct": round(self.current_drawdown_pct, 2),
            "daily_pnl": round(self.state.daily_pnl, 2),
            "daily_trades": self.state.daily_trades,
            "total_trades": total_trades,
            "win_rate": round(self.win_rate * 100, 1),
            "avg_rr": round(self.avg_win_loss_ratio, 2),
            "kelly_fraction": round(self.kelly_optimal_fraction() * 100, 2),
            "consecutive_losses": self.state.consecutive_losses,
            "can_trade": self.can_trade(),
        }
