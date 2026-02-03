# Go / No-Go Criteria for Scaling Position Size

## Overview
This document defines objective criteria for when it's safe to increase position sizes.
**Never scale based on recent profits alone.**

---

## Pre-Scale Checklist (ALL must be TRUE)

### 1. Operational Stability
- [ ] Bridge v3 running continuously for **7+ days** without crashes
- [ ] No manual interventions required during that period
- [ ] Audit log shows consistent behavior (no anomalies)
- [ ] Kill switch tested and confirmed working

### 2. Halt Effectiveness
Run `python analytics/attribution.py` and verify:
- [ ] **False Positive Rate < 25%** (rules aren't too conservative)
- [ ] **True Positive Rate > 70%** of halts avoided actual damage
- [ ] Halt reasons are distributed (not all from one trigger)

### 3. Tightening Value
From attribution report:
- [ ] **DD Reduction > $0** (tightening actually helps)
- [ ] Tightened baskets have **lower max DD** than normal
- [ ] No tightened basket had catastrophic loss

### 4. Data Quality
- [ ] **Stale sentiment rate < 10%** of cycles
- [ ] ATR history has **500+ samples** (no cold-start risk)
- [ ] Memory index has **50+ trade records**

### 5. Regression Tests
Run `python chaos/regression_suite.py` and verify:
- [ ] **100% pass rate** on all tests
- [ ] Hysteresis latch working (test 6 passed)
- [ ] Kill switch override working (test 5 passed)

### 6. Performance Baseline
- [ ] **Win rate > 45%** over 30+ baskets
- [ ] **Profit factor > 1.2** (gross profit / gross loss)
- [ ] **Max DD < 5%** of account in any single day
- [ ] No single basket lost > 2% of account

---

## Scaling Tiers

### Tier 0: Paper/Demo (Current)
- Any position size
- Used for system validation

### Tier 1: Micro Live
**Requirements:** All Pre-Scale criteria met
- Max position: **0.05 lots**
- Max daily exposure: **0.15 lots**
- Duration: **14 days minimum**

### Tier 2: Small Live
**Additional Requirements:**
- Tier 1 running 14+ days with all criteria still met
- **30+ live baskets** completed
- **Zero manual interventions**
- Max position: **0.10 lots**
- Max daily exposure: **0.30 lots**
- Duration: **30 days minimum**

### Tier 3: Normal Live
**Additional Requirements:**
- Tier 2 running 30+ days
- **100+ live baskets** completed
- Attribution report shows consistent value
- Regression suite still 100%
- Max position: **0.25 lots**
- Max daily exposure: **0.75 lots**

### Tier 4: Full Size
**Additional Requirements:**
- Tier 3 running 60+ days
- Board/stakeholder review (if applicable)
- Risk budget formally allocated
- Max position: Per risk policy

---

## Immediate Scale-Down Triggers (No-Go)

If ANY of these occur, **immediately reduce to Tier 0**:

### Critical (Halt Trading)
- [ ] Bridge crashes during market hours
- [ ] Kill switch fails to engage when tested
- [ ] Control.json corruption detected
- [ ] Any position opened when `disable_new_entries = true`

### Serious (Reduce by 1 Tier)
- [ ] Single basket loses > 3% of account
- [ ] 3 consecutive losing days
- [ ] False positive rate > 40%
- [ ] Stale data rate > 30%
- [ ] Manual intervention required

### Warning (Review within 24h)
- [ ] Regression test failure
- [ ] Unusual halt pattern (>50% of cycles halted)
- [ ] Memory index stops growing
- [ ] Sentiment agent down for >30 minutes

---

## Review Schedule

| Frequency | Action |
|-----------|--------|
| Daily | Check PM2 status, review audit log tail |
| Weekly | Run attribution report, review halt patterns |
| Monthly | Full regression suite, scale tier review |
| Quarterly | Architecture review, consider upgrades |

---

## Audit Trail Requirements

Before any tier change, document:
1. Current tier and duration at that tier
2. All pre-scale criteria status
3. Attribution report summary
4. Regression test results
5. Decision rationale
6. Rollback plan if issues arise

Store in: `bridge/logs/tier_changes.jsonl`

---

## Emergency Procedures

### If Bridge Crashes During Position
1. **Do NOT restart immediately**
2. Check MT5 - are positions still managed by EA?
3. EA has hard-coded safety limits - it continues without bridge
4. Manually review positions
5. Fix issue, restart bridge
6. Document incident

### If Kill Switch Needed
1. Create file: `touch bridge/kill_switch.txt`
2. Bridge will halt all entries within 2 seconds
3. EA continues managing existing positions (SL/TP)
4. Investigate root cause
5. Remove kill switch only after resolution: `rm bridge/kill_switch.txt`

---

## Metrics to Track (Dashboard)

Build or integrate tracking for:
- Daily PnL curve
- Rolling 7-day Sharpe ratio
- Halt frequency trend
- DD per basket distribution
- Time in market per day
- Sentiment regime distribution

---

*Last Updated: 2026-01-28*
*Version: 1.0*
