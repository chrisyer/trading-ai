#!/usr/bin/env python3
"""
HYDRA SCALPER — Main Entry Point
═════════════════════════════════

Usage:
  python -m forex_scalper.run backtest              # Backtest with synthetic data
  python -m forex_scalper.run backtest --data ./csv  # Backtest with CSV data
  python -m forex_scalper.run optimize              # Run Optuna optimization
  python -m forex_scalper.run optimize --trials 500 # Custom trial count
  python -m forex_scalper.run live                  # Live trading (OANDA paper)
  python -m forex_scalper.run live --real            # Live trading (OANDA real)
  python -m forex_scalper.run generate              # Generate synthetic data to CSV
  python -m forex_scalper.run describe              # Print strategy description
"""

import argparse
import sys
import time
import json
from pathlib import Path

from .config import DEFAULT_PARAMS, DEFAULT_BACKTEST, PAIRS, StrategyParams
from .strategy import HydraScalper
from .backtest import VectorizedBacktester
from .data_loader import (
    generate_multi_synthetic, load_csv_directory,
    load_multi_oanda, validate_data, generate_synthetic_data,
)


def cmd_backtest(args):
    """Run backtesting."""
    print("═" * 60)
    print("  HYDRA SCALPER — BACKTESTING MODE")
    print("═" * 60)

    # Load data
    if args.data:
        print(f"\n  Loading CSV data from: {args.data}")
        data = load_csv_directory(args.data, PAIRS)
    elif args.oanda:
        print(f"\n  Loading from OANDA API ({args.bars} bars per pair)...")
        data = load_multi_oanda(PAIRS, count=args.bars)
    else:
        days = args.days or 252 * 5  # Default 5 years
        print(f"\n  Generating synthetic data ({days} days, 7 pairs)...")
        data = generate_multi_synthetic(PAIRS, days=days)

    if not data:
        print("  ERROR: No data loaded!")
        return

    # Validate
    print("\n  Validating data...")
    for pair, df in data.items():
        result = validate_data(df, pair)
        status = "OK" if result["valid"] else "ISSUES"
        print(f"    {pair}: {result['bars']} bars [{status}] "
              f"({result['start'][:10]} -> {result['end'][:10]})")
        if result["issues"]:
            for issue in result["issues"]:
                print(f"      ! {issue}")

    # Load params (optimized or default)
    params = DEFAULT_PARAMS
    if args.params:
        print(f"\n  Loading optimized params from: {args.params}")
        with open(args.params) as f:
            param_dict = json.load(f)
        if "best_params" in param_dict:
            param_dict = param_dict["best_params"]
        params = StrategyParams(**{
            k: v for k, v in param_dict.items()
            if hasattr(StrategyParams, k)
        })

    # Print strategy config
    strategy = HydraScalper(params)
    print(strategy.get_description())

    # Run backtest
    print("\n  Running backtest...")
    t0 = time.time()
    bt = VectorizedBacktester(params, DEFAULT_BACKTEST)
    results = bt.run_portfolio(data)
    elapsed = time.time() - t0

    # Print results
    bt.print_results(results)
    print(f"\n  Total backtest time: {elapsed:.2f} seconds")

    # Save results
    output_dir = Path("/home/jbot/trading_ai/forex_scalper/backtest_results")
    output_dir.mkdir(parents=True, exist_ok=True)

    summary = {}
    for pair, r in results.items():
        summary[pair] = {
            "total_trades": r.total_trades,
            "wins": r.wins,
            "losses": r.losses,
            "win_rate": round(r.win_rate, 2),
            "profit_factor": round(r.profit_factor, 2),
            "total_pnl_usd": round(r.total_pnl, 2),
            "avg_pnl_pips": round(r.avg_pnl_pips, 2),
            "max_drawdown_pct": round(r.max_drawdown_pct, 2),
            "sortino_ratio": round(r.sortino_ratio, 2),
            "sharpe_ratio": round(r.sharpe_ratio, 2),
            "trades_per_day": round(r.trades_per_day, 1),
            "avg_bars_held": round(r.avg_bars_held, 1),
            "backtest_seconds": round(r.backtest_time_sec, 2),
        }

    summary_file = output_dir / "backtest_summary.json"
    with open(summary_file, "w") as f:
        json.dump(summary, f, indent=2)
    print(f"\n  Results saved to: {summary_file}")


def cmd_optimize(args):
    """Run Optuna optimization."""
    from .optimizer import HydraOptimizer, quick_optimize

    print("═" * 60)
    print("  HYDRA SCALPER — OPTIMIZATION MODE")
    print("═" * 60)

    # Load data
    if args.data:
        print(f"\n  Loading CSV data from: {args.data}")
        data = load_csv_directory(args.data, PAIRS)
    else:
        days = args.days or 252 * 3  # Default 3 years for optimization
        print(f"\n  Generating synthetic data ({days} days)...")
        data = generate_multi_synthetic(PAIRS, days=days)

    n_trials = args.trials or 200

    if args.walkforward:
        print(f"\n  Walk-forward optimization with {n_trials} trials per window...")
        optimizer = HydraOptimizer(
            data, n_trials=n_trials,
            objective_metric=args.metric or "combined",
        )
        windows = optimizer.walk_forward(
            in_sample_months=6,
            out_sample_months=3,
            step_weeks=2,
        )
        print(f"\n  Completed {len(windows)} walk-forward windows")
    else:
        print(f"\n  Quick optimization with {n_trials} trials...")
        best_params = quick_optimize(data, n_trials=n_trials)
        print(f"\n  Best params found. Running validation backtest...")

        bt = VectorizedBacktester(best_params, DEFAULT_BACKTEST)
        results = bt.run_portfolio(data)
        bt.print_results(results)


def cmd_live(args):
    """Run live trading."""
    import logging
    from .live_executor import OandaExecutor

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    params = DEFAULT_PARAMS
    if args.params:
        with open(args.params) as f:
            param_dict = json.load(f)
        if "best_params" in param_dict:
            param_dict = param_dict["best_params"]
        params = StrategyParams(**{
            k: v for k, v in param_dict.items()
            if hasattr(StrategyParams, k)
        })

    executor = OandaExecutor(
        params=params,
        paper=not args.real,
    )
    executor.run()


def cmd_generate(args):
    """Generate synthetic data and save to CSV."""
    output_dir = Path(args.output or "/home/jbot/trading_ai/forex_scalper/synthetic_data")
    output_dir.mkdir(parents=True, exist_ok=True)

    days = args.days or 252 * 5
    print(f"Generating {days} days of synthetic data for {len(PAIRS)} pairs...")

    for i, pair in enumerate(PAIRS):
        df = generate_synthetic_data(pair=pair, days=days, seed=42 + i)
        filepath = output_dir / f"{pair}_M1.csv"
        df.to_csv(filepath)
        print(f"  {pair}: {len(df)} bars -> {filepath}")

    print(f"\nDone! Data saved to {output_dir}")


def cmd_describe(args):
    """Print strategy description."""
    strategy = HydraScalper(DEFAULT_PARAMS)
    print(strategy.get_description())


def main():
    parser = argparse.ArgumentParser(
        description="HYDRA SCALPER — Ultimate Forex Scalping System",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Backtest
    bt_parser = subparsers.add_parser("backtest", help="Run backtesting")
    bt_parser.add_argument("--data", type=str, help="Path to CSV data directory")
    bt_parser.add_argument("--oanda", action="store_true", help="Load from OANDA API")
    bt_parser.add_argument("--bars", type=int, default=5000, help="Bars to fetch from OANDA")
    bt_parser.add_argument("--days", type=int, help="Days of synthetic data (default: 1260)")
    bt_parser.add_argument("--params", type=str, help="Path to optimized params JSON")

    # Optimize
    opt_parser = subparsers.add_parser("optimize", help="Run Optuna optimization")
    opt_parser.add_argument("--data", type=str, help="Path to CSV data directory")
    opt_parser.add_argument("--days", type=int, help="Days of synthetic data (default: 756)")
    opt_parser.add_argument("--trials", type=int, default=200, help="Number of Optuna trials")
    opt_parser.add_argument("--metric", type=str, default="combined",
                            choices=["sortino", "profit_factor", "combined"])
    opt_parser.add_argument("--walkforward", action="store_true", help="Run walk-forward optimization")

    # Live
    live_parser = subparsers.add_parser("live", help="Run live trading")
    live_parser.add_argument("--real", action="store_true", help="Use real money (default: paper)")
    live_parser.add_argument("--params", type=str, help="Path to optimized params JSON")

    # Generate
    gen_parser = subparsers.add_parser("generate", help="Generate synthetic data")
    gen_parser.add_argument("--output", type=str, help="Output directory")
    gen_parser.add_argument("--days", type=int, help="Days to generate")

    # Describe
    subparsers.add_parser("describe", help="Print strategy description")

    args = parser.parse_args()

    if args.command == "backtest":
        cmd_backtest(args)
    elif args.command == "optimize":
        cmd_optimize(args)
    elif args.command == "live":
        cmd_live(args)
    elif args.command == "generate":
        cmd_generate(args)
    elif args.command == "describe":
        cmd_describe(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
