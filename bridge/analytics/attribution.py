#!/usr/bin/env python3
"""
PERFORMANCE ATTRIBUTION ANALYTICS
=================================
Phase 8: Measure before optimizing.

Three core analytics:
1. Halt Attribution - Why did we halt? What did it save?
2. Tightening Effectiveness - Did AI tightening reduce DD?
3. False Positive Rate - Are rules too conservative?

Run: python attribution.py
"""

import json
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict
from typing import List, Dict, Tuple
import statistics

BASE_DIR = Path(__file__).resolve().parents[1]
AUDIT_LOG = BASE_DIR / "logs" / "bridge_audit.jsonl"
TRADES_LOG = BASE_DIR / "memory" / "trades.jsonl"


def load_jsonl(path: Path) -> List[dict]:
    """Load JSONL file"""
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if line.strip():
            try:
                records.append(json.loads(line))
            except:
                pass
    return records


# ══════════════════════════════════════════════════════════════════════════════
# 1. HALT ATTRIBUTION
# ══════════════════════════════════════════════════════════════════════════════

def halt_attribution(audit_records: List[dict]) -> dict:
    """
    Analyze why halts occurred and their frequency.
    """
    total_cycles = len(audit_records)
    halt_cycles = 0
    reason_counts = defaultdict(int)
    
    # Track halt → resume transitions
    halt_durations = []
    current_halt_start = None
    
    for i, rec in enumerate(audit_records):
        rules = rec.get("rules", {})
        final = rec.get("final", {})
        reasons = rules.get("reasons", [])
        disabled = final.get("disable", False)
        
        if disabled:
            halt_cycles += 1
            for reason in reasons:
                reason_counts[reason] += 1
            
            if current_halt_start is None:
                current_halt_start = rec.get("ts")
        else:
            if current_halt_start:
                # Halt ended
                try:
                    start = datetime.fromisoformat(current_halt_start)
                    end = datetime.fromisoformat(rec.get("ts", ""))
                    duration = (end - start).total_seconds()
                    halt_durations.append(duration)
                except:
                    pass
                current_halt_start = None
    
    # Sort reasons by frequency
    sorted_reasons = sorted(reason_counts.items(), key=lambda x: x[1], reverse=True)
    
    return {
        "total_cycles": total_cycles,
        "halt_cycles": halt_cycles,
        "halt_rate": round(halt_cycles / total_cycles * 100, 2) if total_cycles else 0,
        "reason_breakdown": dict(sorted_reasons),
        "avg_halt_duration_sec": round(statistics.mean(halt_durations), 1) if halt_durations else 0,
        "max_halt_duration_sec": round(max(halt_durations), 1) if halt_durations else 0,
        "total_halts": len(halt_durations)
    }


# ══════════════════════════════════════════════════════════════════════════════
# 2. TIGHTENING EFFECTIVENESS
# ══════════════════════════════════════════════════════════════════════════════

def tightening_effectiveness(trades: List[dict], audit_records: List[dict]) -> dict:
    """
    Compare baskets with vs without AI tightening.
    """
    # Build timestamp -> control map from audit
    control_by_time = {}
    for rec in audit_records:
        ts = rec.get("ts", "")[:19]  # Truncate to second
        final = rec.get("final", {})
        control_by_time[ts] = {
            "dca": final.get("dca", 1.0),
            "layers": final.get("layers", 3),
            "disabled": final.get("disable", False)
        }
    
    # Categorize trades
    tightened_baskets = []  # DCA > 1.0 or layers < 3
    normal_baskets = []
    
    for trade in trades:
        outcome = trade.get("outcome", {})
        pnl = float(outcome.get("pnl", 0))
        dd = float(outcome.get("dd", 0))
        duration = float(outcome.get("mins", 0))
        
        # Check if this trade had tightening active
        ts = trade.get("timestamp", "")[:19]
        ctrl = trade.get("control", {})
        
        was_tightened = (
            float(ctrl.get("dca_step_multiplier", 1.0)) > 1.0 or
            int(ctrl.get("max_layers_cap", 3)) < 3
        )
        
        basket_stats = {"pnl": pnl, "dd": dd, "mins": duration}
        
        if was_tightened:
            tightened_baskets.append(basket_stats)
        else:
            normal_baskets.append(basket_stats)
    
    def summarize(baskets: List[dict]) -> dict:
        if not baskets:
            return {"count": 0, "avg_pnl": 0, "avg_dd": 0, "avg_mins": 0, "win_rate": 0}
        
        pnls = [b["pnl"] for b in baskets]
        dds = [b["dd"] for b in baskets]
        mins = [b["mins"] for b in baskets]
        wins = sum(1 for p in pnls if p > 0)
        
        return {
            "count": len(baskets),
            "avg_pnl": round(statistics.mean(pnls), 2),
            "avg_dd": round(statistics.mean(dds), 2),
            "max_dd": round(max(dds), 2) if dds else 0,
            "avg_mins": round(statistics.mean(mins), 1),
            "win_rate": round(wins / len(baskets) * 100, 1)
        }
    
    return {
        "tightened": summarize(tightened_baskets),
        "normal": summarize(normal_baskets),
        "dd_reduction": round(
            (summarize(normal_baskets)["avg_dd"] - summarize(tightened_baskets)["avg_dd"]),
            2
        ) if normal_baskets and tightened_baskets else 0
    }


# ══════════════════════════════════════════════════════════════════════════════
# 3. FALSE POSITIVE RATE
# ══════════════════════════════════════════════════════════════════════════════

def false_positive_analysis(audit_records: List[dict]) -> dict:
    """
    Analyze how often halts occurred but market conditions were actually safe.
    A "false positive" is a halt followed by:
    - ATR bucket returning to low/mid within 10 cycles
    - No actual adverse move
    """
    false_positives = 0
    true_positives = 0
    
    i = 0
    while i < len(audit_records):
        rec = audit_records[i]
        final = rec.get("final", {})
        
        if final.get("disable", False):
            # Halt started - look ahead 10 cycles
            halt_start = i
            halt_atr = rec.get("state", {}).get("atr", 0)
            
            # Find when halt ended
            halt_end = i + 1
            while halt_end < len(audit_records):
                if not audit_records[halt_end].get("final", {}).get("disable", False):
                    break
                halt_end += 1
            
            halt_duration = halt_end - halt_start
            
            # Check what happened after halt
            if halt_end < len(audit_records):
                post_halt = audit_records[halt_end:halt_end + 10]
                
                # Was the halt "worth it"?
                # Check if ATR stayed elevated or spiked further
                post_atrs = [r.get("state", {}).get("atr", 0) for r in post_halt if r.get("state", {}).get("atr")]
                
                if post_atrs:
                    max_post_atr = max(post_atrs)
                    avg_post_atr = statistics.mean(post_atrs)
                    
                    # False positive: halt, but conditions normalized quickly
                    # and never got worse
                    if halt_duration <= 3 and max_post_atr <= halt_atr * 1.1:
                        false_positives += 1
                    else:
                        true_positives += 1
            
            i = halt_end
        else:
            i += 1
    
    total_halts = false_positives + true_positives
    
    return {
        "total_halts_analyzed": total_halts,
        "false_positives": false_positives,
        "true_positives": true_positives,
        "false_positive_rate": round(false_positives / total_halts * 100, 1) if total_halts else 0,
        "assessment": (
            "Rules are well-calibrated" if total_halts == 0 or false_positives / max(total_halts, 1) < 0.2
            else "Rules may be too conservative" if false_positives / max(total_halts, 1) < 0.4
            else "Consider relaxing halt triggers"
        )
    }


# ══════════════════════════════════════════════════════════════════════════════
# 4. SENTIMENT EFFECTIVENESS
# ══════════════════════════════════════════════════════════════════════════════

def sentiment_analysis(audit_records: List[dict]) -> dict:
    """
    Analyze how sentiment affected decisions.
    """
    sentiment_counts = defaultdict(int)
    sentiment_halt_correlation = defaultdict(lambda: {"total": 0, "halts": 0})
    stale_count = 0
    
    for rec in audit_records:
        sent = rec.get("sentiment", {})
        regime = sent.get("regime", "unknown")
        stale = sent.get("stale", False)
        disabled = rec.get("final", {}).get("disable", False)
        
        sentiment_counts[regime] += 1
        sentiment_halt_correlation[regime]["total"] += 1
        if disabled:
            sentiment_halt_correlation[regime]["halts"] += 1
        if stale:
            stale_count += 1
    
    # Calculate halt rates by sentiment
    halt_rates = {}
    for regime, data in sentiment_halt_correlation.items():
        if data["total"] > 0:
            halt_rates[regime] = round(data["halts"] / data["total"] * 100, 1)
    
    return {
        "sentiment_distribution": dict(sentiment_counts),
        "halt_rate_by_sentiment": halt_rates,
        "stale_sentiment_cycles": stale_count,
        "stale_rate": round(stale_count / len(audit_records) * 100, 1) if audit_records else 0
    }


# ══════════════════════════════════════════════════════════════════════════════
# 5. ATR REGIME ANALYSIS
# ══════════════════════════════════════════════════════════════════════════════

def atr_analysis(audit_records: List[dict]) -> dict:
    """
    Analyze ATR bucket distribution and effectiveness.
    """
    bucket_counts = defaultdict(int)
    bucket_halt_rates = defaultdict(lambda: {"total": 0, "halts": 0})
    
    for rec in audit_records:
        state = rec.get("state", {})
        final = rec.get("final", {})
        # ATR bucket might be in final or we derive from state
        bucket = final.get("atr_bucket") or "unknown"
        disabled = final.get("disable", False)
        
        bucket_counts[bucket] += 1
        bucket_halt_rates[bucket]["total"] += 1
        if disabled:
            bucket_halt_rates[bucket]["halts"] += 1
    
    halt_rates = {}
    for bucket, data in bucket_halt_rates.items():
        if data["total"] > 0:
            halt_rates[bucket] = round(data["halts"] / data["total"] * 100, 1)
    
    return {
        "bucket_distribution": dict(bucket_counts),
        "halt_rate_by_bucket": halt_rates
    }


# ══════════════════════════════════════════════════════════════════════════════
# MAIN REPORT
# ══════════════════════════════════════════════════════════════════════════════

def generate_report():
    """Generate full attribution report"""
    print("="*70)
    print("📊 PERFORMANCE ATTRIBUTION REPORT")
    print("="*70)
    print(f"Generated: {datetime.now().isoformat()}")
    print()
    
    # Load data
    audit = load_jsonl(AUDIT_LOG)
    trades = load_jsonl(TRADES_LOG)
    
    print(f"Audit records: {len(audit)}")
    print(f"Trade records: {len(trades)}")
    print()
    
    if not audit:
        print("❌ No audit data available. Run the bridge first.")
        return
    
    # 1. Halt Attribution
    print("─"*70)
    print("1️⃣ HALT ATTRIBUTION")
    print("─"*70)
    halt = halt_attribution(audit)
    print(f"   Total cycles:        {halt['total_cycles']}")
    print(f"   Halt cycles:         {halt['halt_cycles']} ({halt['halt_rate']}%)")
    print(f"   Distinct halts:      {halt['total_halts']}")
    print(f"   Avg halt duration:   {halt['avg_halt_duration_sec']}s")
    print(f"   Max halt duration:   {halt['max_halt_duration_sec']}s")
    print()
    print("   Reasons:")
    for reason, count in halt['reason_breakdown'].items():
        pct = count / halt['halt_cycles'] * 100 if halt['halt_cycles'] else 0
        print(f"      {reason:25s} {count:5d} ({pct:5.1f}%)")
    print()
    
    # 2. Tightening Effectiveness
    print("─"*70)
    print("2️⃣ TIGHTENING EFFECTIVENESS")
    print("─"*70)
    tight = tightening_effectiveness(trades, audit)
    print("   Normal baskets (no tightening):")
    print(f"      Count:    {tight['normal']['count']}")
    print(f"      Avg PnL:  ${tight['normal']['avg_pnl']:.2f}")
    print(f"      Avg DD:   ${tight['normal']['avg_dd']:.2f}")
    print(f"      Max DD:   ${tight['normal']['max_dd']:.2f}")
    print(f"      Win rate: {tight['normal']['win_rate']}%")
    print()
    print("   Tightened baskets (DCA>1 or layers<3):")
    print(f"      Count:    {tight['tightened']['count']}")
    print(f"      Avg PnL:  ${tight['tightened']['avg_pnl']:.2f}")
    print(f"      Avg DD:   ${tight['tightened']['avg_dd']:.2f}")
    print(f"      Max DD:   ${tight['tightened']['max_dd']:.2f}")
    print(f"      Win rate: {tight['tightened']['win_rate']}%")
    print()
    print(f"   DD Reduction from tightening: ${tight['dd_reduction']:.2f}")
    print()
    
    # 3. False Positive Analysis
    print("─"*70)
    print("3️⃣ FALSE POSITIVE ANALYSIS")
    print("─"*70)
    fp = false_positive_analysis(audit)
    print(f"   Halts analyzed:      {fp['total_halts_analyzed']}")
    print(f"   True positives:      {fp['true_positives']}")
    print(f"   False positives:     {fp['false_positives']}")
    print(f"   False positive rate: {fp['false_positive_rate']}%")
    print(f"   Assessment:          {fp['assessment']}")
    print()
    
    # 4. Sentiment Analysis
    print("─"*70)
    print("4️⃣ SENTIMENT ANALYSIS")
    print("─"*70)
    sent = sentiment_analysis(audit)
    print("   Distribution:")
    for regime, count in sent['sentiment_distribution'].items():
        pct = count / len(audit) * 100
        halt_rate = sent['halt_rate_by_sentiment'].get(regime, 0)
        print(f"      {regime:20s} {count:5d} ({pct:5.1f}%)  halt rate: {halt_rate:5.1f}%")
    print()
    print(f"   Stale sentiment cycles: {sent['stale_sentiment_cycles']} ({sent['stale_rate']}%)")
    print()
    
    # 5. ATR Analysis
    print("─"*70)
    print("5️⃣ ATR REGIME ANALYSIS")
    print("─"*70)
    atr = atr_analysis(audit)
    print("   Distribution:")
    for bucket, count in atr['bucket_distribution'].items():
        pct = count / len(audit) * 100
        halt_rate = atr['halt_rate_by_bucket'].get(bucket, 0)
        print(f"      {bucket:10s} {count:5d} ({pct:5.1f}%)  halt rate: {halt_rate:5.1f}%")
    print()
    
    print("="*70)
    print("END OF REPORT")
    print("="*70)
    
    # Save report as JSON
    report = {
        "generated": datetime.now().isoformat(),
        "audit_records": len(audit),
        "trade_records": len(trades),
        "halt_attribution": halt,
        "tightening_effectiveness": tight,
        "false_positive_analysis": fp,
        "sentiment_analysis": sent,
        "atr_analysis": atr
    }
    
    report_path = BASE_DIR / "logs" / "attribution_report.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\n📁 Report saved: {report_path}")


if __name__ == "__main__":
    generate_report()
