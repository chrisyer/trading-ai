"""
HYDRA SCALPER — Optuna Hyperparameter Optimizer
════════════════════════════════════════════════

Walk-forward optimization:
  - 6-month in-sample for fitting
  - 3-month out-of-sample for validation
  - Re-optimize every 2 weeks
  - Objective: maximize Sortino ratio (or profit factor as secondary)

Uses Optuna's TPE sampler for efficient search.
"""

import optuna
import numpy as np
import pandas as pd
from typing import Dict, Optional, Tuple
import time
import json
from pathlib import Path

from .config import StrategyParams, BacktestConfig, DEFAULT_BACKTEST, PAIRS
from .backtest import VectorizedBacktester


def create_trial_params(trial: optuna.Trial) -> StrategyParams:
    """
    Define the Optuna search space.
    Maps trial suggestions to StrategyParams.
    """
    return StrategyParams(
        # Layer 1: Regime
        regime_ema_fast=trial.suggest_int("regime_ema_fast", 5, 13),
        regime_ema_mid=trial.suggest_int("regime_ema_mid", 15, 30),
        regime_ema_slow=trial.suggest_int("regime_ema_slow", 40, 65),
        regime_adx_period=14,  # Fixed — standard
        regime_adx_threshold=trial.suggest_float("regime_adx_threshold", 18.0, 30.0, step=1.0),

        # Layer 2: Volatility
        vol_atr_period=trial.suggest_int("vol_atr_period", 10, 20),
        vol_percentile=trial.suggest_float("vol_percentile", 60.0, 85.0, step=5.0),
        vol_lookback=trial.suggest_int("vol_lookback", 100, 300, step=50),

        # Layer 3: Entry
        bb_period=trial.suggest_int("bb_period", 15, 25),
        bb_std=trial.suggest_float("bb_std", 1.5, 2.5, step=0.25),
        kc_period=trial.suggest_int("kc_period", 15, 25),
        kc_mult=trial.suggest_float("kc_mult", 1.0, 2.0, step=0.25),
        squeeze_min_bars=trial.suggest_int("squeeze_min_bars", 3, 10),
        macd_fast=trial.suggest_int("macd_fast", 6, 12),
        macd_slow=trial.suggest_int("macd_slow", 14, 26),
        macd_signal=trial.suggest_int("macd_signal", 7, 12),
        rsi_period=trial.suggest_int("rsi_period", 3, 9),
        rsi_ob=trial.suggest_float("rsi_ob", 75.0, 90.0, step=5.0),
        rsi_os=trial.suggest_float("rsi_os", 10.0, 25.0, step=5.0),
        entry_ema_fast=trial.suggest_int("entry_ema_fast", 5, 13),
        entry_ema_slow=trial.suggest_int("entry_ema_slow", 15, 30),
        pullback_tolerance_atr=trial.suggest_float("pullback_tolerance_atr", 0.15, 0.5, step=0.05),
        candle_body_pct=trial.suggest_float("candle_body_pct", 0.65, 0.90, step=0.05),

        # Layer 4: Exits
        tp_atr_mult=trial.suggest_float("tp_atr_mult", 1.5, 3.0, step=0.25),
        sl_atr_mult=trial.suggest_float("sl_atr_mult", 0.5, 1.2, step=0.1),
        breakeven_pips=trial.suggest_float("breakeven_pips", 4.0, 10.0, step=1.0),
        trail_activation_pips=trial.suggest_float("trail_activation_pips", 6.0, 14.0, step=1.0),
        trail_distance_atr=trial.suggest_float("trail_distance_atr", 0.3, 0.9, step=0.1),

        # Layer 5: Filters
        min_bars_between_trades=trial.suggest_int("min_bars_between_trades", 2, 8),

        # Risk
        risk_per_trade_pct=trial.suggest_float("risk_per_trade_pct", 0.5, 2.5, step=0.25),
    )


class HydraOptimizer:
    """
    Walk-forward optimizer using Optuna.

    Splits data into in-sample (6 months) and out-of-sample (3 months),
    rolls forward by 2 weeks, re-optimizes.
    """

    def __init__(self, data: Dict[str, pd.DataFrame],
                 bt_config: Optional[BacktestConfig] = None,
                 n_trials: int = 200,
                 objective_metric: str = "sortino"):
        """
        Args:
            data: Dict of pair -> 1-min DataFrame
            bt_config: Backtest configuration
            n_trials: Number of Optuna trials per window
            objective_metric: "sortino", "profit_factor", or "combined"
        """
        self.data = data
        self.bt_config = bt_config or DEFAULT_BACKTEST
        self.n_trials = n_trials
        self.objective_metric = objective_metric
        self.results_dir = Path("/home/jbot/trading_ai/forex_scalper/optimization_results")
        self.results_dir.mkdir(parents=True, exist_ok=True)

    def _objective(self, trial: optuna.Trial,
                   train_data: Dict[str, pd.DataFrame]) -> float:
        """Optuna objective function — run backtest and return metric."""
        params = create_trial_params(trial)

        # Quick validation: MACD fast < slow
        if params.macd_fast >= params.macd_slow:
            return -999.0

        bt = VectorizedBacktester(params, self.bt_config)
        total_pnl = 0.0
        total_trades = 0
        total_wins = 0
        sortinos = []
        profit_factors = []

        for pair, df in train_data.items():
            if len(df) < 500:
                continue
            result = bt.run_single_pair(df, pair)
            total_pnl += result.total_pnl
            total_trades += result.total_trades
            total_wins += result.wins
            if result.total_trades >= 10:
                sortinos.append(result.sortino_ratio)
                profit_factors.append(result.profit_factor)

        # Minimum trade count filter
        if total_trades < 50:
            return -999.0

        win_rate = total_wins / total_trades * 100 if total_trades > 0 else 0

        # Penalize extreme win rates (likely overfit)
        if win_rate > 85 or win_rate < 35:
            return -999.0

        # Compute objective
        avg_sortino = np.mean(sortinos) if sortinos else 0.0
        avg_pf = np.mean(profit_factors) if profit_factors else 0.0

        if self.objective_metric == "sortino":
            return avg_sortino
        elif self.objective_metric == "profit_factor":
            return avg_pf
        else:  # Combined
            return 0.6 * avg_sortino + 0.4 * avg_pf

    def optimize_single_window(self, train_data: Dict[str, pd.DataFrame],
                               window_name: str = "default") -> Tuple[StrategyParams, dict]:
        """
        Run Optuna optimization on a single training window.

        Returns:
            (best_params, study_summary)
        """
        study = optuna.create_study(
            direction="maximize",
            sampler=optuna.samplers.TPESampler(seed=42),
            pruner=optuna.pruners.MedianPruner(),
            study_name=f"hydra_{window_name}",
        )

        # Suppress Optuna logging noise
        optuna.logging.set_verbosity(optuna.logging.WARNING)

        print(f"\n  Running {self.n_trials} trials for window '{window_name}'...")
        t0 = time.time()

        study.optimize(
            lambda trial: self._objective(trial, train_data),
            n_trials=self.n_trials,
            show_progress_bar=True,
        )

        elapsed = time.time() - t0
        best = study.best_trial

        print(f"  Best value: {best.value:.4f} (took {elapsed:.1f}s)")
        print(f"  Best params: {json.dumps(best.params, indent=2)[:200]}...")

        # Reconstruct best params
        best_params = StrategyParams(**{
            k: v for k, v in best.params.items()
            if hasattr(StrategyParams, k)
        })
        # Fill in fixed fields not in the trial
        best_params.regime_adx_period = 14

        summary = {
            "window": window_name,
            "best_value": best.value,
            "n_trials": self.n_trials,
            "elapsed_sec": elapsed,
            "best_params": best.params,
        }

        # Save results
        out_file = self.results_dir / f"optuna_{window_name}.json"
        with open(out_file, "w") as f:
            json.dump(summary, f, indent=2)

        return best_params, summary

    def walk_forward(self, in_sample_months: int = 6,
                     out_sample_months: int = 3,
                     step_weeks: int = 2) -> list:
        """
        Walk-forward optimization across the entire dataset.

        Splits into rolling in-sample/out-of-sample windows.
        Re-optimizes every `step_weeks` weeks.

        Returns:
            List of (window_name, best_params, oos_result) tuples
        """
        # Find common date range across all pairs
        start_dates = []
        end_dates = []
        for pair, df in self.data.items():
            start_dates.append(df.index.min())
            end_dates.append(df.index.max())

        data_start = max(start_dates)
        data_end = min(end_dates)

        in_sample_td = pd.Timedelta(days=in_sample_months * 30)
        out_sample_td = pd.Timedelta(days=out_sample_months * 30)
        step_td = pd.Timedelta(weeks=step_weeks)

        windows = []
        current_start = data_start

        print(f"\nWalk-Forward Optimization")
        print(f"  Data range: {data_start} -> {data_end}")
        print(f"  In-sample: {in_sample_months} months, Out-sample: {out_sample_months} months")
        print(f"  Step: {step_weeks} weeks")

        window_idx = 0
        while current_start + in_sample_td + out_sample_td <= data_end:
            is_start = current_start
            is_end = current_start + in_sample_td
            oos_start = is_end
            oos_end = is_end + out_sample_td

            window_name = f"wf_{window_idx:03d}_{is_start.strftime('%Y%m%d')}"
            print(f"\n{'═' * 60}")
            print(f"  Window {window_idx}: IS=[{is_start.date()} -> {is_end.date()}] "
                  f"OOS=[{oos_start.date()} -> {oos_end.date()}]")

            # Slice data
            is_data = {}
            oos_data = {}
            for pair, df in self.data.items():
                is_data[pair] = df.loc[is_start:is_end]
                oos_data[pair] = df.loc[oos_start:oos_end]

            # Optimize on in-sample
            best_params, summary = self.optimize_single_window(is_data, window_name)

            # Validate on out-of-sample
            print(f"\n  Out-of-sample validation:")
            bt = VectorizedBacktester(best_params, self.bt_config)
            oos_results = bt.run_portfolio(oos_data)

            if "PORTFOLIO" in oos_results:
                oos = oos_results["PORTFOLIO"]
                print(f"  OOS Results: {oos.total_trades} trades, "
                      f"WR={oos.win_rate:.1f}%, PF={oos.profit_factor:.2f}, "
                      f"Sortino={oos.sortino_ratio:.2f}")

            windows.append((window_name, best_params, oos_results))
            current_start += step_td
            window_idx += 1

        print(f"\n{'═' * 60}")
        print(f"  Walk-forward complete: {len(windows)} windows processed")
        return windows


def quick_optimize(data: Dict[str, pd.DataFrame],
                   n_trials: int = 100) -> StrategyParams:
    """
    Quick single-window optimization.
    Use this for rapid testing before running full walk-forward.
    """
    optimizer = HydraOptimizer(data, n_trials=n_trials, objective_metric="combined")
    best_params, summary = optimizer.optimize_single_window(data, "quick")
    return best_params
