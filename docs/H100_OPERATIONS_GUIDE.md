# H100 NEO Operations Guide

**Last Updated:** February 5, 2026  
**Monthly Cost:** $5,000  
**Purpose:** XAUUSD signal generation, pattern recognition, market intelligence

---

## Critical Services

| Service | PM2 Name | Port | Purpose | Must Be Running |
|---------|----------|------|---------|-----------------|
| NEO API | `neo-ghost-api` | 8036 | Signal generation API | YES |
| Signal Generator | `neo-signal-generator` | - | Writes signals to files | YES |
| Health Monitor | `neo-health-monitor` | - | Alerts on failures | YES |

---

## How to Verify It's Working

### Quick Check (30 seconds)
```bash
# Check health endpoint
curl http://127.0.0.1:8036/api/neo/health | jq

# Expected response:
# {
#   "status": "healthy",
#   "signals_generated_24h": > 0,
#   "ghost_directives_age_seconds": < 120
# }
```

### Full Verification
```bash
# 1. Check all services are running
pm2 list

# 2. Check signal file was recently updated
ls -la /home/jbot/trading_ai/neo/signals/xauusd_fresh_signal.json

# 3. Check ghost_directives.txt was recently updated  
ls -la /home/jbot/trading_ai/intel/ghost_directives.txt

# 4. Check API responds
curl http://127.0.0.1:8036/api/unified/xauusd/signal | jq '.action, .confidence'
```

### Signs of Problems
- `ghost_directives_age_seconds` > 120: Signal generator may be stuck
- `status: "degraded"`: Some issues but still functioning
- `status: "critical"`: Major issues, needs immediate attention
- Any PM2 service showing "errored" or "stopped"

---

## What to Do If Something Breaks

### Step 1: Restart Services
```bash
# Restart all NEO services
pm2 restart neo-signal-generator
pm2 restart neo-ghost-api
pm2 restart neo-health-monitor

# Wait 30 seconds, then check
curl http://127.0.0.1:8036/api/neo/health | jq
```

### Step 2: Check Logs
```bash
# View signal generator logs
pm2 logs neo-signal-generator --lines 50

# View API logs
pm2 logs neo-ghost-api --lines 50

# View health monitor alerts
tail -50 /home/jbot/trading_ai/neo/logs/health_alerts.log
```

### Step 3: If Still Broken
```bash
# Check if port 8036 is in use by something else
netstat -tlnp | grep 8036

# Kill and restart everything
pm2 kill
pm2 start /home/jbot/trading_ai/neo/ghost_integration_api.py --name neo-ghost-api --interpreter python3
pm2 start /home/jbot/trading_ai/neo/signal_generator_service.py --name neo-signal-generator --interpreter python3
pm2 start /home/jbot/trading_ai/neo/health_monitor_critical.py --name neo-health-monitor --interpreter python3
pm2 save
```

---

## File Locations

| File | Purpose | Updated By |
|------|---------|------------|
| `/home/jbot/trading_ai/neo/signals/xauusd_fresh_signal.json` | Latest signal (JSON) | signal-generator |
| `/home/jbot/trading_ai/intel/ghost_directives.txt` | Signal for MT5 EAs | signal-generator |
| `/home/jbot/trading_ai/neo/logs/health_alerts.log` | Alert history | health-monitor |
| `/home/jbot/trading_ai/neo/logs/health_status.json` | Current health state | health-monitor |

---

## API Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/neo/health` | GET | Health check (MONITOR THIS) |
| `/api/unified/xauusd/signal` | GET | Get fresh signal |
| `/api/neo/status` | GET | Status of all NEO sources |
| `/api/neo/desktop/report` | POST | Receive Desktop NEO signals |
| `/api/neo/desktop/latest` | GET | Get latest Desktop signal |

---

## Setting Up Alerts

### Discord Alerts
1. Create a Discord webhook in your server
2. Set environment variable:
```bash
export DISCORD_ALERT_WEBHOOK="https://discord.com/api/webhooks/your-webhook-url"
pm2 restart neo-health-monitor
```

### Telegram Alerts
1. Create a Telegram bot via @BotFather
2. Get your chat ID
3. Set environment variables:
```bash
export TELEGRAM_BOT_TOKEN="your-bot-token"
export TELEGRAM_CHAT_ID="your-chat-id"
pm2 restart neo-health-monitor
```

---

## Training Pipeline (Use the H100 for ML)

The H100 should be doing more than just API serving. Training scripts:

```bash
# Pattern learning from historical trades
python /home/jbot/trading_ai/neo/learning/pattern_trainer.py

# Signal performance learning
python /home/jbot/trading_ai/neo/signal_learner.py

# Backtest strategies
python /home/jbot/trading_ai/backtesting/engine.py
```

---

## Contact / Escalation

If services are down for > 10 minutes:
1. Check logs first
2. Try restart procedure
3. If still failing, check H100 server status (memory, disk, network)

---

## Daily Checklist

- [ ] Health endpoint returns "healthy"
- [ ] `signals_generated_24h` > 1000 (should be ~1440 for 1/min)
- [ ] `ghost_directives_age_seconds` < 120
- [ ] No alerts in past 24h
- [ ] PM2 shows all 3 services "online"

---

*This server costs $5,000/month. It needs to work 24/7. No excuses.*
