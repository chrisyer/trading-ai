#!/usr/bin/env python3
"""
═══════════════════════════════════════════════════════════════════════════════
NEO TRUTH ANALYZER — Historical H1 Research Auditor
═══════════════════════════════════════════════════════════════════════════════

ROLE:
  QUINN, acting as a research auditor and statistical evaluator.
  NOT a trader. Does NOT generate signals.
  Analyzes historical hourly truth reports and their outcomes.

OBJECTIVE:
  Analyze historical XAUUSD H1 Truth Reports and determine which
  combinations of metrics have shown higher-than-random predictability
  of favorable outcomes in the following hour(s).

  ANALYSIS-ONLY. NO LIVE TRADING. NO SIGNAL GENERATION.

PHASES:
  1. Outcome Labeling — tag each report with after-the-fact results
  2. Combination Analysis — 2/3/4-metric intersections
  3. Reporting — plain-language research summaries

ABSOLUTE CONSTRAINTS:
  - NO BUY/SELL recommendations
  - NO influence on live trading
  - NO alteration of truth pipeline
  - NO indicator optimization
  - NO future data leakage
  - NO collapsing HOLD into BUY/SELL

Author: QUINN001
Created: February 8, 2026
═══════════════════════════════════════════════════════════════════════════════
"""

import os
import json
import logging
import time
import math
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Set
from itertools import combinations
from collections import defaultdict
from dataclasses import dataclass, field, asdict

# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION — FIXED, NOT TUNABLE
# ═══════════════════════════════════════════════════════════════════════════════

# Outcome labeling thresholds (conservative, NOT optimized)
MFE_THRESHOLD = 5.0    # Favorable move required (in price units)
MAE_THRESHOLD = 8.0    # Adverse move limit (in price units)

# Combination analysis constraints
MIN_SAMPLE_SIZE = 20           # Reject combos with fewer samples
MAX_VARIANCE_COEFFICIENT = 0.6 # Reject high-variance combos
MIN_WIN_RATE_ABOVE_BASELINE = 0.05  # 5% above random to be notable

# Data storage
DATA_DIR = Path("/home/jbot/trading_ai/neo/truth_data")
REPORTS_DIR = DATA_DIR / "reports"
HISTORY_FILE = DATA_DIR / "truth_history.json"
ANALYSIS_FILE = DATA_DIR / "latest_analysis.json"
OUTCOMES_FILE = DATA_DIR / "labeled_outcomes.json"

# MongoDB
MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB = "quinn_trading"
MONGO_TRUTH_COL = "truth_reports"
MONGO_ANALYSIS_COL = "truth_analysis"

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [TRUTH-ANALYZER] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("truth_analyzer")


# ═══════════════════════════════════════════════════════════════════════════════
# DATA STRUCTURES
# ═══════════════════════════════════════════════════════════════════════════════

# The 10 fixed metrics and their possible states
METRIC_NAMES = [
    "VWAP",           # ABOVE / BELOW
    "RSI(14)",        # OVERSOLD / LEANING_OVERSOLD / NEUTRAL / LEANING_OVERBOUGHT / OVERBOUGHT
    "ATR(14)",        # EXPANDING / STABLE / CONTRACTING
    "Session",        # ASIAN / LONDON / LONDON_NY / NEW_YORK / TRANSITION / OFF_HOURS
    "Spread",         # TIGHT / NORMAL / WIDE
    "EMA 50/200",     # BULLISH / BEARISH
    "Ichimoku",       # ABOVE_CLOUD / IN_CLOUD / BELOW_CLOUD
    "H1 H/L Break",   # BROKE_HIGH / INSIDE_RANGE / BROKE_LOW
    "H4 Bias",        # BULL / BEAR
    "Regime",         # TREND / RANGE
]

METRIC_KEYS = [
    "vwap", "rsi", "atr", "session", "spread",
    "ema", "ichimoku", "h1_break", "h4_bias", "regime"
]


@dataclass
class TruthRecord:
    """A single H1 truth report with outcome labels."""
    timestamp: str
    unix: int
    market_open: bool
    price: float
    spread: int
    session: str

    # Metric states (normalized to simple strings)
    vwap: str = ""          # ABOVE / BELOW
    rsi: str = ""           # OVERSOLD / LEANING_OVERSOLD / NEUTRAL / LEANING_OVERBOUGHT / OVERBOUGHT
    atr: str = ""           # EXPANDING / STABLE / CONTRACTING
    # session already captured above
    spread_state: str = ""  # TIGHT / NORMAL / WIDE
    ema: str = ""           # BULLISH / BEARISH
    ichimoku: str = ""      # ABOVE_CLOUD / IN_CLOUD / BELOW_CLOUD
    h1_break: str = ""      # BROKE_HIGH / INSIDE_RANGE / BROKE_LOW
    h4_bias: str = ""       # BULL / BEAR
    regime: str = ""        # TREND / RANGE

    # Scorecard
    buy_count: int = 0
    hold_count: int = 0
    sell_count: int = 0
    dominant: str = ""      # BUY / SELL / NEUTRAL

    # Outcome labels (filled AFTER the fact only)
    next_price: float = 0.0
    mfe: float = 0.0        # Max Favorable Excursion (next hour)
    mae: float = 0.0        # Max Adverse Excursion (next hour)
    delta: float = 0.0      # Close-to-close change
    outcome: str = ""       # SUCCESS_LONG / SUCCESS_SHORT / NEUTRAL

    def metric_signature(self, keys: List[str]) -> str:
        """Get a hashable signature for a combination of metrics."""
        parts = []
        for k in sorted(keys):
            val = getattr(self, k, "?")
            parts.append(f"{k}={val}")
        return "|".join(parts)

    def all_metrics_dict(self) -> Dict[str, str]:
        return {
            "vwap": self.vwap,
            "rsi": self.rsi,
            "atr": self.atr,
            "session": self.session,
            "spread": self.spread_state,
            "ema": self.ema,
            "ichimoku": self.ichimoku,
            "h1_break": self.h1_break,
            "h4_bias": self.h4_bias,
            "regime": self.regime,
        }


@dataclass
class ComboResult:
    """Analysis result for a metric combination."""
    combo_keys: List[str]
    signature: str
    sample_count: int = 0
    success_long: int = 0
    success_short: int = 0
    neutral: int = 0
    win_rate_long: float = 0.0
    win_rate_short: float = 0.0
    avg_mfe: float = 0.0
    avg_mae: float = 0.0
    mfe_std: float = 0.0
    mae_std: float = 0.0
    avg_delta: float = 0.0
    variance_coefficient: float = 0.0
    baseline_win_rate: float = 0.0
    edge_over_baseline: float = 0.0
    session_breakdown: Dict[str, Dict] = field(default_factory=dict)
    regime_breakdown: Dict[str, Dict] = field(default_factory=dict)
    rejected: bool = False
    rejection_reason: str = ""


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 1 — DATA COLLECTION & OUTCOME LABELING
# ═══════════════════════════════════════════════════════════════════════════════

class TruthCollector:
    """Collects truth reports and labels outcomes after the fact."""

    def __init__(self):
        self.records: List[TruthRecord] = []
        self._load_history()

    def _load_history(self):
        """Load previously saved records."""
        if HISTORY_FILE.exists():
            try:
                data = json.loads(HISTORY_FILE.read_text())
                for d in data:
                    self.records.append(TruthRecord(**d))
                logger.info(f"Loaded {len(self.records)} historical records")
            except Exception as e:
                logger.warning(f"Failed to load history: {e}")

    def save_history(self):
        """Persist records to disk."""
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        data = [asdict(r) for r in self.records]
        HISTORY_FILE.write_text(json.dumps(data, indent=2))

    def ingest_truth_report(self, truth_json: Dict) -> Optional[TruthRecord]:
        """
        Ingest a raw truth JSON from the pipeline.
        Returns the created record (without outcome labels yet).
        """
        try:
            ts = truth_json.get("timestamps", {})
            mkt = truth_json.get("market", {})
            sc = truth_json.get("scorecard", {})
            metrics = truth_json.get("metrics", [])

            # Skip if market closed
            if not mkt.get("open", True):
                return None

            # Check for duplicate
            unix = ts.get("unix", 0)
            if any(r.unix == unix for r in self.records):
                return None  # Already have this one

            # Normalize metrics
            metric_map = {}
            for m in metrics:
                name = m.get("name", "")
                bias = m.get("bias", "HOLD")
                interp = m.get("interpretation", "")

                if "VWAP" in name:
                    metric_map["vwap"] = "ABOVE" if "ABOVE" in interp else "BELOW"
                elif "RSI" in name:
                    metric_map["rsi"] = self._normalize_rsi(interp)
                elif "ATR" in name:
                    metric_map["atr"] = self._normalize_atr(interp)
                elif "Session" in name:
                    metric_map["session_state"] = interp.replace("/", "_").replace(" ", "_")
                elif "Spread" in name:
                    metric_map["spread_state"] = interp
                elif "EMA" in name:
                    metric_map["ema"] = "BULLISH" if "BULLISH" in interp else "BEARISH"
                elif "Ichimoku" in name:
                    metric_map["ichimoku"] = self._normalize_ichimoku(interp)
                elif "H1 H/L" in name:
                    metric_map["h1_break"] = self._normalize_h1_break(interp)
                elif "H4" in name:
                    metric_map["h4_bias"] = "BULL" if "BULL" in interp else "BEAR"
                elif "Regime" in name:
                    metric_map["regime"] = "TREND" if "TREND" in interp else "RANGE"

            # Determine dominant bias
            buy = sc.get("buy", 0)
            hold = sc.get("hold", 0)
            sell = sc.get("sell", 0)
            if buy > sell + 1:
                dominant = "BUY"
            elif sell > buy + 1:
                dominant = "SELL"
            else:
                dominant = "NEUTRAL"

            record = TruthRecord(
                timestamp=ts.get("utc", ""),
                unix=unix,
                market_open=mkt.get("open", True),
                price=mkt.get("price", 0),
                spread=mkt.get("spread", 0),
                session=metric_map.get("session_state", mkt.get("session", "UNKNOWN")),
                vwap=metric_map.get("vwap", ""),
                rsi=metric_map.get("rsi", ""),
                atr=metric_map.get("atr", ""),
                spread_state=metric_map.get("spread_state", ""),
                ema=metric_map.get("ema", ""),
                ichimoku=metric_map.get("ichimoku", ""),
                h1_break=metric_map.get("h1_break", ""),
                h4_bias=metric_map.get("h4_bias", ""),
                regime=metric_map.get("regime", ""),
                buy_count=buy,
                hold_count=hold,
                sell_count=sell,
                dominant=dominant,
            )

            self.records.append(record)
            self.save_history()
            logger.info(f"Ingested report: {record.timestamp} ${record.price} {dominant}")
            return record

        except Exception as e:
            logger.error(f"Failed to ingest truth report: {e}")
            return None

    def label_outcomes(self, price_data: List[Dict]):
        """
        Label outcomes for records that have subsequent price data.

        price_data: list of {"unix": int, "high": float, "low": float, "close": float}
        representing the NEXT hour's candle after each report.
        """
        price_map = {p["unix"]: p for p in price_data}
        labeled = 0

        for record in self.records:
            if record.outcome:
                continue  # Already labeled

            # Find the next hour's data
            next_unix = record.unix + 3600
            candle = price_map.get(next_unix)
            if not candle:
                continue

            entry = record.price
            high = candle["high"]
            low = candle["low"]
            close = candle["close"]

            # MFE/MAE relative to entry
            mfe_long = high - entry    # Best case for a long
            mae_long = entry - low     # Worst case for a long
            mfe_short = entry - low    # Best case for a short
            mae_short = high - entry   # Worst case for a short
            delta = close - entry

            # Store the larger excursions
            record.mfe = max(mfe_long, mfe_short)
            record.mae = max(mae_long, mae_short)
            record.delta = delta
            record.next_price = close

            # Label outcomes (AFTER the fact only)
            if mfe_long >= MFE_THRESHOLD and mae_long <= MAE_THRESHOLD:
                record.outcome = "SUCCESS_LONG"
            elif mfe_short >= MFE_THRESHOLD and mae_short <= MAE_THRESHOLD:
                record.outcome = "SUCCESS_SHORT"
            else:
                record.outcome = "NEUTRAL"

            labeled += 1

        if labeled > 0:
            self.save_history()
            logger.info(f"Labeled {labeled} new outcomes")

        return labeled

    def label_outcomes_from_sequential(self):
        """
        Label outcomes using the next record's price as a proxy.
        Less accurate than candle data, but works without external price feed.
        """
        labeled = 0
        sorted_records = sorted(self.records, key=lambda r: r.unix)

        for i in range(len(sorted_records) - 1):
            current = sorted_records[i]
            next_rec = sorted_records[i + 1]

            if current.outcome:
                continue
            if next_rec.unix - current.unix > 7200:
                continue  # Gap too large, skip

            entry = current.price
            next_price = next_rec.price
            delta = next_price - entry

            # Approximate MFE/MAE from delta (conservative)
            current.mfe = abs(delta) if delta != 0 else 0
            current.mae = abs(delta) * 0.3  # Rough approximation
            current.delta = delta
            current.next_price = next_price

            if delta >= MFE_THRESHOLD:
                current.outcome = "SUCCESS_LONG"
            elif delta <= -MFE_THRESHOLD:
                current.outcome = "SUCCESS_SHORT"
            else:
                current.outcome = "NEUTRAL"

            labeled += 1

        if labeled > 0:
            self.save_history()
            logger.info(f"Labeled {labeled} outcomes from sequential data")

        return labeled

    @staticmethod
    def _normalize_rsi(interp: str) -> str:
        interp = interp.upper()
        if "OVERSOLD" in interp and "LEANING" not in interp:
            return "OVERSOLD"
        if "LEANING" in interp and "OVERSOLD" in interp:
            return "LEANING_OVERSOLD"
        if "OVERBOUGHT" in interp and "LEANING" not in interp:
            return "OVERBOUGHT"
        if "LEANING" in interp and "OVERBOUGHT" in interp:
            return "LEANING_OVERBOUGHT"
        return "NEUTRAL"

    @staticmethod
    def _normalize_atr(interp: str) -> str:
        interp = interp.upper()
        if "EXPAND" in interp:
            return "EXPANDING"
        if "CONTRACT" in interp:
            return "CONTRACTING"
        return "STABLE"

    @staticmethod
    def _normalize_ichimoku(interp: str) -> str:
        interp = interp.upper()
        if "ABOVE" in interp:
            return "ABOVE_CLOUD"
        if "BELOW" in interp:
            return "BELOW_CLOUD"
        return "IN_CLOUD"

    @staticmethod
    def _normalize_h1_break(interp: str) -> str:
        interp = interp.upper()
        if "HIGH" in interp:
            return "BROKE_HIGH"
        if "LOW" in interp:
            return "BROKE_LOW"
        return "INSIDE_RANGE"


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 2 — COMBINATION ANALYSIS ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

class CombinationAnalyzer:
    """Analyzes 2/3/4-metric intersections for historical edge."""

    def __init__(self, records: List[TruthRecord]):
        # Only use labeled records
        self.records = [r for r in records if r.outcome and r.market_open]
        self.results: List[ComboResult] = []
        self.baseline = self._compute_baseline()

    def _compute_baseline(self) -> Dict:
        """Compute overall baseline win rates."""
        if not self.records:
            return {"long": 0, "short": 0, "neutral": 0, "total": 0}

        total = len(self.records)
        long_wins = sum(1 for r in self.records if r.outcome == "SUCCESS_LONG")
        short_wins = sum(1 for r in self.records if r.outcome == "SUCCESS_SHORT")
        neutrals = sum(1 for r in self.records if r.outcome == "NEUTRAL")

        return {
            "long": long_wins / total if total > 0 else 0,
            "short": short_wins / total if total > 0 else 0,
            "neutral": neutrals / total if total > 0 else 0,
            "total": total,
        }

    def analyze_all_combinations(self, max_depth: int = 4) -> List[ComboResult]:
        """Run analysis on all 2/3/4-metric combinations."""
        self.results = []

        for depth in range(2, max_depth + 1):
            for combo in combinations(METRIC_KEYS, depth):
                result = self._analyze_combination(list(combo))
                if result:
                    self.results.append(result)

        # Sort by edge over baseline (descending)
        self.results.sort(key=lambda r: r.edge_over_baseline, reverse=True)

        # Count stats
        valid = [r for r in self.results if not r.rejected]
        rejected = [r for r in self.results if r.rejected]
        logger.info(
            f"Analyzed {len(self.results)} combinations: "
            f"{len(valid)} valid, {len(rejected)} rejected"
        )

        return self.results

    def _analyze_combination(self, keys: List[str]) -> Optional[ComboResult]:
        """Analyze a specific metric combination."""
        # Group records by this combination's signature
        groups: Dict[str, List[TruthRecord]] = defaultdict(list)
        for record in self.records:
            sig = record.metric_signature(keys)
            groups[sig].append(record)

        # Find the signature with most samples
        best_sig = None
        best_records = []
        for sig, recs in groups.items():
            if len(recs) > len(best_records):
                best_sig = sig
                best_records = recs

        if not best_records:
            return None

        result = ComboResult(
            combo_keys=keys,
            signature=best_sig,
            sample_count=len(best_records),
        )

        # Apply minimum sample size filter
        if result.sample_count < MIN_SAMPLE_SIZE:
            result.rejected = True
            result.rejection_reason = f"Low sample size ({result.sample_count} < {MIN_SAMPLE_SIZE})"
            return result

        # Compute win rates
        result.success_long = sum(1 for r in best_records if r.outcome == "SUCCESS_LONG")
        result.success_short = sum(1 for r in best_records if r.outcome == "SUCCESS_SHORT")
        result.neutral = sum(1 for r in best_records if r.outcome == "NEUTRAL")
        n = result.sample_count
        result.win_rate_long = result.success_long / n
        result.win_rate_short = result.success_short / n

        # MFE/MAE statistics
        mfes = [r.mfe for r in best_records]
        maes = [r.mae for r in best_records]
        deltas = [r.delta for r in best_records]

        result.avg_mfe = sum(mfes) / n if n > 0 else 0
        result.avg_mae = sum(maes) / n if n > 0 else 0
        result.avg_delta = sum(deltas) / n if n > 0 else 0

        # Standard deviations
        if n > 1:
            result.mfe_std = math.sqrt(sum((x - result.avg_mfe) ** 2 for x in mfes) / (n - 1))
            result.mae_std = math.sqrt(sum((x - result.avg_mae) ** 2 for x in maes) / (n - 1))
        else:
            result.mfe_std = 0
            result.mae_std = 0

        # Variance coefficient
        if result.avg_mfe > 0:
            result.variance_coefficient = result.mfe_std / result.avg_mfe
        else:
            result.variance_coefficient = 999

        # Edge over baseline
        best_win = max(result.win_rate_long, result.win_rate_short)
        baseline_best = max(self.baseline["long"], self.baseline["short"])
        result.baseline_win_rate = baseline_best
        result.edge_over_baseline = best_win - baseline_best

        # Session breakdown
        result.session_breakdown = self._breakdown_by_field(best_records, "session")

        # Regime breakdown
        result.regime_breakdown = self._breakdown_by_field(best_records, "regime")

        # Apply rejection filters
        if result.variance_coefficient > MAX_VARIANCE_COEFFICIENT:
            result.rejected = True
            result.rejection_reason = f"High variance (CV={result.variance_coefficient:.2f})"
        elif result.edge_over_baseline < MIN_WIN_RATE_ABOVE_BASELINE:
            result.rejected = True
            result.rejection_reason = f"No edge over baseline ({result.edge_over_baseline:.1%})"
        elif self._has_regime_fragility(result):
            result.rejected = True
            result.rejection_reason = "Regime fragility detected"

        return result

    def _breakdown_by_field(self, records: List[TruthRecord], field: str) -> Dict[str, Dict]:
        """Break down outcomes by a specific field (session, regime, etc.)."""
        groups: Dict[str, List[TruthRecord]] = defaultdict(list)
        for r in records:
            val = getattr(r, field, "UNKNOWN")
            groups[val].append(r)

        breakdown = {}
        for val, recs in groups.items():
            n = len(recs)
            if n == 0:
                continue
            breakdown[val] = {
                "count": n,
                "success_long": sum(1 for r in recs if r.outcome == "SUCCESS_LONG"),
                "success_short": sum(1 for r in recs if r.outcome == "SUCCESS_SHORT"),
                "neutral": sum(1 for r in recs if r.outcome == "NEUTRAL"),
                "avg_delta": sum(r.delta for r in recs) / n,
            }

        return breakdown

    def _has_regime_fragility(self, result: ComboResult) -> bool:
        """Check if results degrade sharply across regimes."""
        regimes = result.regime_breakdown
        if len(regimes) < 2:
            return False

        win_rates = []
        for regime, data in regimes.items():
            n = data["count"]
            if n < 5:
                continue
            wins = data["success_long"] + data["success_short"]
            win_rates.append(wins / n)

        if len(win_rates) < 2:
            return False

        # If win rates differ by more than 30% across regimes, it's fragile
        return max(win_rates) - min(win_rates) > 0.30

    def get_top_combinations(self, n: int = 20) -> List[ComboResult]:
        """Get top N non-rejected combinations by edge."""
        valid = [r for r in self.results if not r.rejected]
        return valid[:n]

    def get_fragile_combinations(self) -> List[ComboResult]:
        """Get combinations that fail under regime change."""
        return [r for r in self.results if r.rejected and "fragility" in r.rejection_reason.lower()]

    def get_failed_combinations(self) -> List[ComboResult]:
        """Get all rejected combinations with reasons."""
        return [r for r in self.results if r.rejected]


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 3 — REPORTING (NO SIGNALS)
# ═══════════════════════════════════════════════════════════════════════════════

class TruthReporter:
    """Generates plain-language research reports from analysis."""

    def __init__(self, analyzer: CombinationAnalyzer, collector: TruthCollector):
        self.analyzer = analyzer
        self.collector = collector

    def generate_summary(self) -> Dict:
        """Generate a full analysis summary."""
        top = self.analyzer.get_top_combinations(20)
        fragile = self.analyzer.get_fragile_combinations()
        failed = self.analyzer.get_failed_combinations()
        baseline = self.analyzer.baseline

        report = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "disclaimer": (
                "This is a RESEARCH REPORT only. It does NOT generate signals, "
                "recommendations, or trading advice. All observations are "
                "historical and may not persist."
            ),
            "data_summary": {
                "total_records": len(self.collector.records),
                "labeled_records": len(self.analyzer.records),
                "date_range": self._get_date_range(),
                "baseline": {
                    "success_long_rate": round(baseline.get("long", 0) * 100, 1),
                    "success_short_rate": round(baseline.get("short", 0) * 100, 1),
                    "neutral_rate": round(baseline.get("neutral", 0) * 100, 1),
                },
            },
            "analysis_stats": {
                "total_combinations_tested": len(self.analyzer.results),
                "valid_combinations": len(top),
                "rejected_combinations": len(failed),
                "fragile_combinations": len(fragile),
            },
            "top_combinations": [self._format_combo(c) for c in top[:10]],
            "fragile_combinations": [self._format_combo(c) for c in fragile[:5]],
            "warnings": self._generate_warnings(top),
        }

        return report

    def generate_text_report(self) -> str:
        """Generate a human-readable text report."""
        summary = self.generate_summary()
        lines = []

        lines.append("=" * 60)
        lines.append("NEO TRUTH ANALYZER — RESEARCH REPORT")
        lines.append("=" * 60)
        lines.append(f"Generated: {summary['generated_at']}")
        lines.append("")
        lines.append("DISCLAIMER: " + summary["disclaimer"])
        lines.append("")

        # Data summary
        ds = summary["data_summary"]
        lines.append(f"--- DATA SUMMARY ---")
        lines.append(f"Total records: {ds['total_records']}")
        lines.append(f"Labeled records: {ds['labeled_records']}")
        lines.append(f"Date range: {ds['date_range']}")
        lines.append(f"Baseline rates: Long {ds['baseline']['success_long_rate']}% | "
                      f"Short {ds['baseline']['success_short_rate']}% | "
                      f"Neutral {ds['baseline']['neutral_rate']}%")
        lines.append("")

        # Analysis stats
        ast = summary["analysis_stats"]
        lines.append(f"--- ANALYSIS ---")
        lines.append(f"Combinations tested: {ast['total_combinations_tested']}")
        lines.append(f"Valid: {ast['valid_combinations']} | "
                      f"Rejected: {ast['rejected_combinations']} | "
                      f"Fragile: {ast['fragile_combinations']}")
        lines.append("")

        # Top combinations
        if summary["top_combinations"]:
            lines.append("--- NOTABLE METRIC COMBINATIONS ---")
            lines.append("(Sorted by historical edge over baseline)")
            lines.append("")

            for i, combo in enumerate(summary["top_combinations"], 1):
                lines.append(f"  #{i}: {combo['description']}")
                lines.append(f"      Samples: {combo['sample_count']} | "
                              f"Edge: +{combo['edge_pct']}% over baseline")
                lines.append(f"      Avg MFE: {combo['avg_mfe']:.1f} | "
                              f"Avg MAE: {combo['avg_mae']:.1f}")
                if combo.get("session_notes"):
                    lines.append(f"      Sessions: {combo['session_notes']}")
                lines.append("")
        else:
            lines.append("--- NO NOTABLE COMBINATIONS FOUND ---")
            lines.append("Insufficient data or no combinations exceeded baseline.")
            lines.append("")

        # Fragile combinations
        if summary["fragile_combinations"]:
            lines.append("--- FRAGILE COMBINATIONS (FAIL UNDER REGIME CHANGE) ---")
            for combo in summary["fragile_combinations"]:
                lines.append(f"  FRAGILE: {combo['description']}")
                lines.append(f"           Reason: {combo.get('rejection_reason', 'Regime fragility')}")
            lines.append("")

        # Warnings
        if summary["warnings"]:
            lines.append("--- WARNINGS ---")
            for w in summary["warnings"]:
                lines.append(f"  ! {w}")
            lines.append("")

        lines.append("=" * 60)
        lines.append("END OF REPORT — This is analysis, not advice.")
        lines.append("=" * 60)

        return "\n".join(lines)

    def _format_combo(self, combo: ComboResult) -> Dict:
        """Format a ComboResult for the report."""
        # Build human-readable description
        parts = combo.signature.split("|")
        conditions = []
        for part in parts:
            key, val = part.split("=", 1)
            name = METRIC_NAMES[METRIC_KEYS.index(key)] if key in METRIC_KEYS else key
            conditions.append(f"{name} = {val}")

        description = "When: " + " AND ".join(conditions)

        # Session notes
        session_notes = ""
        if combo.session_breakdown:
            best_session = max(combo.session_breakdown.items(),
                               key=lambda x: (x[1]["success_long"] + x[1]["success_short"]) / max(x[1]["count"], 1))
            worst_session = min(combo.session_breakdown.items(),
                                key=lambda x: (x[1]["success_long"] + x[1]["success_short"]) / max(x[1]["count"], 1))
            if best_session[0] != worst_session[0]:
                session_notes = f"Best in {best_session[0]}, degrades in {worst_session[0]}"

        return {
            "metrics": combo.combo_keys,
            "signature": combo.signature,
            "description": description,
            "sample_count": combo.sample_count,
            "win_rate_long": round(combo.win_rate_long * 100, 1),
            "win_rate_short": round(combo.win_rate_short * 100, 1),
            "edge_pct": round(combo.edge_over_baseline * 100, 1),
            "avg_mfe": round(combo.avg_mfe, 2),
            "avg_mae": round(combo.avg_mae, 2),
            "avg_delta": round(combo.avg_delta, 2),
            "variance_coefficient": round(combo.variance_coefficient, 3),
            "session_notes": session_notes,
            "rejected": combo.rejected,
            "rejection_reason": combo.rejection_reason,
        }

    def _get_date_range(self) -> str:
        if not self.collector.records:
            return "No data"
        sorted_recs = sorted(self.collector.records, key=lambda r: r.unix)
        return f"{sorted_recs[0].timestamp} → {sorted_recs[-1].timestamp}"

    def _generate_warnings(self, top: List[ComboResult]) -> List[str]:
        warnings = []

        total = len(self.analyzer.records)
        if total < 50:
            warnings.append(
                f"LOW DATA WARNING: Only {total} labeled records. "
                f"Results are statistically unreliable. Minimum 200+ recommended."
            )

        if total < MIN_SAMPLE_SIZE:
            warnings.append(
                "INSUFFICIENT DATA: Cannot produce meaningful analysis. "
                "Continue collecting data."
            )

        if top:
            best = top[0]
            if best.edge_over_baseline < 0.10:
                warnings.append(
                    f"WEAK EDGE: Best combination shows only "
                    f"+{best.edge_over_baseline:.1%} over baseline. "
                    f"This may be noise, not signal."
                )

        fragile_pct = len(self.analyzer.get_fragile_combinations()) / max(len(self.analyzer.results), 1)
        if fragile_pct > 0.3:
            warnings.append(
                f"HIGH FRAGILITY: {fragile_pct:.0%} of combinations are regime-fragile. "
                f"Market conditions may have shifted."
            )

        return warnings

    def save_report(self):
        """Save report to disk and MongoDB."""
        summary = self.generate_summary()
        text = self.generate_text_report()

        # Save JSON
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        ANALYSIS_FILE.write_text(json.dumps(summary, indent=2, default=str))

        # Save text report
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        date_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
        report_file = REPORTS_DIR / f"truth_analysis_{date_str}.txt"
        report_file.write_text(text)

        # MongoDB
        try:
            from pymongo import MongoClient
            client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=3000)
            db = client[MONGO_DB]
            col = db[MONGO_ANALYSIS_COL]
            col.insert_one(summary)
            client.close()
            logger.info("Report saved to MongoDB")
        except Exception as e:
            logger.warning(f"MongoDB save failed: {e}")

        logger.info(f"Report saved: {report_file}")
        return summary, text


# ═══════════════════════════════════════════════════════════════════════════════
# FASTAPI ROUTER (Mounts on NEO API port 8036)
# ═══════════════════════════════════════════════════════════════════════════════

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse

truth_analyzer_router = APIRouter(tags=["Truth Analyzer"])

# Singleton instances
_collector: Optional[TruthCollector] = None
_last_analysis: Optional[Dict] = None
_last_analysis_time: Optional[datetime] = None


def get_collector() -> TruthCollector:
    global _collector
    if _collector is None:
        _collector = TruthCollector()
    return _collector


@truth_analyzer_router.get("/api/neo/truth/status")
async def truth_status():
    """Current status of the truth analyzer."""
    collector = get_collector()
    total = len(collector.records)
    labeled = len([r for r in collector.records if r.outcome])
    unlabeled = total - labeled

    return {
        "status": "online",
        "role": "RESEARCH AUDITOR — analysis only, no signals",
        "total_records": total,
        "labeled_records": labeled,
        "unlabeled_records": unlabeled,
        "mfe_threshold": MFE_THRESHOLD,
        "mae_threshold": MAE_THRESHOLD,
        "min_sample_size": MIN_SAMPLE_SIZE,
        "last_analysis": _last_analysis_time.isoformat() if _last_analysis_time else None,
        "history_file": str(HISTORY_FILE),
    }


@truth_analyzer_router.get("/api/neo/truth/ingest")
async def truth_ingest():
    """
    Manually trigger ingestion of the latest truth report from CRELLA.
    The relay already caches it locally.
    """
    collector = get_collector()

    # Read from relay cache
    cache_file = DATA_DIR / "last_truth.json"
    if not cache_file.exists():
        raise HTTPException(status_code=404, detail="No truth data cached yet")

    try:
        data = json.loads(cache_file.read_text())
        record = collector.ingest_truth_report(data)
        if record:
            return {
                "status": "ingested",
                "timestamp": record.timestamp,
                "price": record.price,
                "dominant": record.dominant,
                "total_records": len(collector.records),
            }
        else:
            return {
                "status": "skipped",
                "reason": "duplicate or market closed",
                "total_records": len(collector.records),
            }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@truth_analyzer_router.post("/api/neo/truth/label-outcomes")
async def truth_label_outcomes():
    """Label outcomes using sequential price data."""
    collector = get_collector()
    labeled = collector.label_outcomes_from_sequential()
    return {
        "status": "labeled",
        "newly_labeled": labeled,
        "total_labeled": len([r for r in collector.records if r.outcome]),
        "total_records": len(collector.records),
    }


@truth_analyzer_router.post("/api/neo/truth/analyze")
async def truth_analyze():
    """Run full combination analysis. Returns summary report."""
    global _last_analysis, _last_analysis_time

    collector = get_collector()

    # Auto-label first
    collector.label_outcomes_from_sequential()

    labeled = [r for r in collector.records if r.outcome]
    if len(labeled) < 10:
        return {
            "status": "insufficient_data",
            "labeled_records": len(labeled),
            "message": f"Need at least 10 labeled records (have {len(labeled)}). Keep collecting.",
        }

    # Run analysis
    analyzer = CombinationAnalyzer(labeled)
    analyzer.analyze_all_combinations(max_depth=4)

    # Generate report
    reporter = TruthReporter(analyzer, collector)
    summary, text = reporter.save_report()

    _last_analysis = summary
    _last_analysis_time = datetime.now(timezone.utc)

    return summary


@truth_analyzer_router.get("/api/neo/truth/report", response_class=PlainTextResponse)
async def truth_report():
    """Get the latest analysis as a plain-text research report."""
    global _last_analysis, _last_analysis_time

    collector = get_collector()
    collector.label_outcomes_from_sequential()

    labeled = [r for r in collector.records if r.outcome]
    if len(labeled) < 5:
        return (
            "NEO TRUTH ANALYZER — INSUFFICIENT DATA\n\n"
            f"Records collected: {len(collector.records)}\n"
            f"Records labeled: {len(labeled)}\n\n"
            "Continue collecting H1 truth reports. Analysis requires\n"
            "at minimum 20+ labeled records for meaningful results.\n"
        )

    analyzer = CombinationAnalyzer(labeled)
    analyzer.analyze_all_combinations(max_depth=4)
    reporter = TruthReporter(analyzer, collector)
    _, text = reporter.save_report()

    _last_analysis = reporter.generate_summary()
    _last_analysis_time = datetime.now(timezone.utc)

    return text


@truth_analyzer_router.get("/api/neo/truth/history")
async def truth_history():
    """Get all collected truth records (latest 100)."""
    collector = get_collector()
    sorted_recs = sorted(collector.records, key=lambda r: r.unix, reverse=True)[:100]
    return {
        "total": len(collector.records),
        "showing": len(sorted_recs),
        "records": [asdict(r) for r in sorted_recs],
    }


@truth_analyzer_router.get("/api/neo/truth/baseline")
async def truth_baseline():
    """Get baseline statistics."""
    collector = get_collector()
    labeled = [r for r in collector.records if r.outcome]

    if not labeled:
        return {"status": "no_data", "message": "No labeled records yet"}

    total = len(labeled)
    return {
        "total_labeled": total,
        "success_long": sum(1 for r in labeled if r.outcome == "SUCCESS_LONG"),
        "success_short": sum(1 for r in labeled if r.outcome == "SUCCESS_SHORT"),
        "neutral": sum(1 for r in labeled if r.outcome == "NEUTRAL"),
        "success_long_rate": round(sum(1 for r in labeled if r.outcome == "SUCCESS_LONG") / total * 100, 1),
        "success_short_rate": round(sum(1 for r in labeled if r.outcome == "SUCCESS_SHORT") / total * 100, 1),
        "neutral_rate": round(sum(1 for r in labeled if r.outcome == "NEUTRAL") / total * 100, 1),
        "mfe_threshold": MFE_THRESHOLD,
        "mae_threshold": MAE_THRESHOLD,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# AUTO-INGESTION HOOK (called by the relay)
# ═══════════════════════════════════════════════════════════════════════════════

def auto_ingest_from_cache():
    """Called periodically to ingest new truth data from relay cache."""
    collector = get_collector()
    cache_file = DATA_DIR / "last_truth.json"
    if cache_file.exists():
        try:
            data = json.loads(cache_file.read_text())
            collector.ingest_truth_report(data)
        except:
            pass


async def start_truth_analyzer():
    """Startup hook — load collector."""
    logger.info("Truth Analyzer initialized — RESEARCH AUDITOR mode")
    get_collector()


async def stop_truth_analyzer():
    """Shutdown hook."""
    global _collector
    if _collector is not None:
        _collector.save_history()
    logger.info("Truth Analyzer stopped, history saved")
