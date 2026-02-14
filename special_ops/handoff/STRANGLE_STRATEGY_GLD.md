# GLD STRANGLE STRATEGY — Full Playbook
## Remote Operations Manual for Philippines Deployment
**Version:** 2.0 | **Date:** Feb 13, 2026 | **Author:** QUINN001 for CRELLA001

---

## 1. STRATEGY OVERVIEW

Buy a strangle on GLD (Gold ETF) using OTM calls at Fibonacci resistance
and OTM puts at Fibonacci support, with a 60/40 bullish lean.

**Core thesis:** Gold always beats its all-time high. Central banks are
accumulating. We don't predict direction — we straddle both sides and
take profit on whichever leg wins.

---

## 2. ENTRY RULES

### Strike Selection
| Leg | Fibonacci Zone | Description |
|-----|---------------|-------------|
| CALLS | 100-127.2% extension | At/above yesterday's high resistance |
| PUTS | 0-23.6% retracement | At/below yesterday's low support |

### Allocation
- **60% calls / 40% puts** (default bullish lean)
- Adjust to 50/50 if RSI > 70 (overbought, correction likely)
- Adjust to 70/30 calls if ADX > 25 AND gold trending up

### Timing
- Minimum **21 days to expiration**, ideal **28-35 DTE**
- Enter on **quiet/consolidation days** when IV is low (cheap premiums)
- After major data (CPI/NFP): **wait 30-60 min** for dust to settle
- Best entry: 10:00-11:00 AM EST after initial volatility settles

### IV Awareness
- Low IV (VIX < 18, GVZ < 15): Premium is cheap — **good entry**
- High IV (VIX > 25, GVZ > 20): Premium is expensive — **wait or reduce size**

---

## 3. PROFIT-TAKING RULES

| Condition | Action |
|-----------|--------|
| Winning leg +100% | **Close winner**, keep losing side as lottery ticket |
| Winning leg +150% | **Hard close** — never let a 150%+ winner turn into a loser |
| After closing winner | Re-enter at new Fibonacci levels (new strangle) |
| Both legs profitable | Close both — rare but possible during volatility expansion |

**Critical:** NEVER let a 100%+ winner turn into a loser.

---

## 4. RISK MANAGEMENT

| Rule | Limit |
|------|-------|
| Max risk per strangle | Total premium paid (defined risk) |
| Roll threshold | 10 DTE — roll or close to avoid theta decay |
| Max buying power usage | 50% of options buying power |
| PDT rule | Max 3 day trades per 5 rolling days (under $25K) |
| Position limit | Max 3 active strangles at any time |
| Daily loss limit | Stop trading if down 5% of account in a day |

---

## 5. CPI / DATA RELEASE PROTOCOL

```
TIME (EST)     ACTION
═══════════    ══════════════════════════════════════════
8:30 AM        Data drops — WATCH gold spot on MT5
8:30-9:30      Observe direction + magnitude of gold move
9:30 AM        Options market opens — premiums reprice
9:30-10:00     DO NOT TRADE — initial volatility/spread wide
10:00 AM+      Enter AFTER initial overreaction settles
```

**Key insight:** Both hot AND cool CPI can cause gold to sell off
short-term. Buy the overreaction, not the initial move.

---

## 6. ARCHITECTURE — REMOTE OPS

```
Philippines (You)
    |
    v   (HTTPS / Tailscale)
H100 Server (Quinn)
    |-- GLD Dashboard     (port 8080) — market data, Fib levels, setup
    |-- Signal Poller     — fetches MT5 signal every 30s
    |-- Alert Service     — Telegram alerts for profit/roll/kill
    |-- Ollama Analysis   — Dolphin3 AI recommendation
    |-- RH Executor       — Robinhood trade execution
    |
    v   (Tailscale VPN)
US Desktop (Always On)
    |-- MT5 Terminal + All EAs
    |-- Signal Relay      (port 8098) — serves signal file
    |-- Kill Switch       — Crellastein_Casper at 7% DD
    |-- Crash Freeze      — MarketMakerDCA freeze
```

---

## 7. DAILY ROUTINE (Philippines Time — +13 hours from EST)

| PH Time | EST Time | Action |
|---------|----------|--------|
| 9:00 PM | 8:00 AM | Check dashboard for overnight gold movement |
| 9:30 PM | 8:30 AM | CPI/NFP days: watch data release reaction |
| 10:30 PM | 9:30 AM | Market open: check premium repricing |
| 11:00 PM | 10:00 AM | Enter strangle if conditions met |
| 12:00 AM | 11:00 AM | Check positions, review AI analysis |
| 2:00 AM | 1:00 PM | Midday check — any profit targets hit? |
| 5:00 AM | 4:00 PM | Market close — review day's action |
| Sleep | After hours | Alert service monitors for kill switch/crash freeze |

---

## 8. EMERGENCY PROCEDURES

### Kill Switch Fires (7% Equity DD)
1. You'll receive a Telegram alert immediately
2. MT5 Casper EA closes all positions automatically
3. **DO NOT** override — let the system protect the account
4. Assess damage next market day before re-enabling

### Crash Freeze Activates
1. Telegram alert fires
2. MarketMakerDCA freezes all DCA operations
3. Options positions are NOT affected (Robinhood separate)
4. Wait for volatility to settle before unfreezing

### Desktop Goes Offline
1. Signal Poller logs "desktop unreachable" after 5 failures
2. Telegram alert fires
3. Dashboard falls back to cached signal
4. **Action:** Contact someone to check/restart the desktop
5. GLD dashboard still works (uses Yahoo Finance directly)

### Robinhood Auth Expires
1. RH monitor will log "login failed"
2. You'll need to re-authenticate (may need MFA)
3. Positions are safe — they're on Robinhood's side
4. Only NEW orders are blocked until re-auth

---

## 9. PM2 COMMANDS

```bash
# Start all services
pm2 start gld_dashboard/ecosystem.config.js

# Check status
pm2 status

# View logs
pm2 logs gld-strangle-dashboard
pm2 logs gld-signal-poller
pm2 logs gld-alert-service

# Restart a service
pm2 restart gld-strangle-dashboard

# Save + startup (survives server reboot)
pm2 save
pm2 startup
```

---

## 10. BEFORE DEPARTURE CHECKLIST

- [ ] Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in PM2 env
- [ ] Set ROBINHOOD_USER and ROBINHOOD_PASS on H100 (encrypted)
- [ ] Test dashboard access from PH network (or Tailscale)
- [ ] Verify signal relay works (desktop → H100)
- [ ] Send test Telegram alert
- [ ] Verify kill switch fires on MT5 demo at 7% DD
- [ ] Verify crash freeze activates on demo
- [ ] Start all PM2 services and run `pm2 save && pm2 startup`
- [ ] Paper trade one strangle end-to-end (entry → profit target → close)
- [ ] Bookmark dashboard URL on phone browser

---

*"Gold moves $100-200/day. The strangle costs $1,500-$2,500.*
*One big move pays for everything. We don't predict. We react."*

*— CRELLA001, Feb 2026*
