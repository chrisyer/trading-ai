# QUINN MISSION: OPS MONITOR
## Unified System Health + Daily Telegram Recap
**Version:** 1.0 | **Date:** Feb 20, 2026 | **Author:** CRELLA001 → QUINN001

---

## OVERVIEW

A single Python service on H100 that monitors the entire trading operation
and sends Telegram alerts when anything breaks + a daily recap at 8 AM EST.

**What it monitors:**
- CRELLA desktop health (equity, P&L, processes, MT5 files, bridge state, ghost signal)
- Local ML services (Direction Predictor 8040, RL Sizer 8041, LoRA Governor 8043)
- Kill switch / crash freeze status
- Equity drawdown (>3% triggers alert)

---

## CRELLA ENDPOINT

```
GET http://100.119.161.65:8097/ops/health
```

Returns full system health in one JSON call:
- `equity`, `balance`, `floating_pnl`
- `open_positions`
- `defcon` (level + color)
- `processes` (PM2 process status)
- `bridge` / `bridge_control` (disable_entries, dca_mult, layer_cap)
- `ghost` / `ghost_signal` (current direction + confidence)
- `mt5_files` / `file_freshness` (staleness check)
- `kill_switch` (triggered flag + reason)

---

## DEPLOYMENT

### Step 1: Test Telegram

```bash
cd /home/jbot/trading_ai/special_ops/services
python3 ops_monitor.py --test
```

You should receive a test message in Telegram. If not, check env vars:
```bash
echo $TELEGRAM_BOT_TOKEN
echo $TELEGRAM_CHAT_ID
```

### Step 2: Test Recap

```bash
python3 ops_monitor.py --recap
```

Sends the full daily recap immediately. Review the formatting in Telegram.

### Step 3: Deploy as PM2 Daemon

```bash
pm2 start ops_monitor.py --name ops-monitor --interpreter python3 \
  --cwd /home/jbot/trading_ai/special_ops/services -- --daemon
pm2 save
```

### Step 4: Verify

```bash
pm2 logs ops-monitor --nostream --lines 10
```

Should show health check results every 5 minutes.

---

## ALERT TYPES

| Alert | Trigger | Emoji | Cooldown |
|-------|---------|-------|----------|
| CRELLA unreachable | /ops/health fails | 🔴 | 30 min |
| Process down | PM2 process not running | ⚠️ | 30 min |
| Kill switch | 7% equity drawdown triggered | 🚨🚨 | 30 min |
| Equity drawdown | >3% since last check | 🟡 | 30 min |
| ML service down | Health endpoint fails | 🟠 | 30 min |

---

## DAILY RECAP

Sent at **8:00 AM EST (13:00 UTC)** on weekdays. Contains:
- CRELLA status (equity, P&L, positions)
- Bridge control state
- Ghost Commander signal
- MT5 file freshness
- All ML service health
- Overall trading posture

---

## ML API INTEGRATION (CRELLA SIDE)

Confirmed working endpoints on H100:

| Port | Service | Method | Path | Key Fields |
|------|---------|--------|------|------------|
| 8040 | Direction Predictor | POST | /predict | `atr`, `atr_pctl`, `layers`, `direction` → `direction`, `confidence` |
| 8041 | RL Position Sizer | POST | /predict | `atr`, `atr_pctl`, `layers`, `direction` → `dca_multiplier`, `lot_scale` |
| 8043 | LoRA Governor | POST | /govern | `atr`, `atr_pctl`, `atr_bucket`, `layers` → `disable_new_entries`, `dca_step_multiplier`, `max_layers_cap` |

All three have `GET /health` and `POST /reload`.

---

## CONFIGURATION

| Env Var | Default | Description |
|---------|---------|-------------|
| `TELEGRAM_BOT_TOKEN` | (hardcoded fallback) | Telegram bot token |
| `TELEGRAM_CHAT_ID` | (hardcoded fallback) | Telegram chat ID |
| `CRELLA_IP` | 100.119.161.65 | CRELLA Tailscale IP |
| `CRELLA_PORT` | 8097 | CRELLA truth server port |
| `OPS_POLL_SECONDS` | 300 | Poll interval (5 min) |

---

*One service to watch them all.*
