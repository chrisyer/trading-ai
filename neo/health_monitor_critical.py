#!/usr/bin/env python3
"""
═══════════════════════════════════════════════════════════════════════════════
NEO CRITICAL HEALTH MONITOR
═══════════════════════════════════════════════════════════════════════════════

This monitor runs every minute and:
1. Checks /api/neo/health endpoint
2. Verifies signal generation is working
3. Sends alerts if anything is broken

If this monitor finds issues, it:
- Logs to file for evidence
- Attempts to restart broken services
- Sends Discord/Telegram alert

RUNS AS: pm2 start health_monitor_critical.py --name "neo-health-monitor"

═══════════════════════════════════════════════════════════════════════════════
"""

import os
import sys
import json
import time
import logging
import requests
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

# Configuration
HEALTH_ENDPOINT = "http://127.0.0.1:8036/api/neo/health"
CHECK_INTERVAL = 60  # seconds
MAX_FAILURES_BEFORE_RESTART = 3
MAX_FAILURES_BEFORE_ALERT = 5

# Paths
LOG_DIR = Path("/home/jbot/trading_ai/neo/logs")
LOG_DIR.mkdir(exist_ok=True)
ALERT_LOG = LOG_DIR / "health_alerts.log"
STATUS_FILE = LOG_DIR / "health_status.json"

# Discord webhook (set this to your webhook URL)
DISCORD_WEBHOOK = os.environ.get("DISCORD_ALERT_WEBHOOK", "")

# Telegram (optional)
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [HEALTH-MONITOR] %(levelname)s: %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(ALERT_LOG)
    ]
)
logger = logging.getLogger("HealthMonitor")

# State
consecutive_failures = 0
last_healthy_time = None
alerts_sent_today = 0


def check_health() -> dict:
    """Check the health endpoint."""
    try:
        response = requests.get(HEALTH_ENDPOINT, timeout=30)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        return {
            "status": "unreachable",
            "error": str(e),
            "issues": [f"Health endpoint unreachable: {str(e)[:100]}"]
        }


def restart_service(service_name: str):
    """Attempt to restart a PM2 service."""
    try:
        logger.warning(f"🔄 Attempting to restart {service_name}...")
        result = subprocess.run(
            ["pm2", "restart", service_name],
            capture_output=True,
            text=True,
            timeout=30
        )
        if result.returncode == 0:
            logger.info(f"✅ {service_name} restarted successfully")
            return True
        else:
            logger.error(f"❌ Failed to restart {service_name}: {result.stderr}")
            return False
    except Exception as e:
        logger.error(f"❌ Error restarting {service_name}: {e}")
        return False


def send_discord_alert(message: str, is_recovery: bool = False):
    """Send alert to Discord."""
    if not DISCORD_WEBHOOK:
        return
    
    try:
        color = 0x00FF00 if is_recovery else 0xFF0000
        embed = {
            "embeds": [{
                "title": "🟢 NEO Recovery" if is_recovery else "🚨 NEO ALERT",
                "description": message,
                "color": color,
                "timestamp": datetime.utcnow().isoformat(),
                "footer": {"text": "H100 Health Monitor"}
            }]
        }
        requests.post(DISCORD_WEBHOOK, json=embed, timeout=10)
        logger.info(f"Discord alert sent: {message[:50]}...")
    except Exception as e:
        logger.error(f"Failed to send Discord alert: {e}")


def send_telegram_alert(message: str):
    """Send alert to Telegram."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        data = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": f"🚨 NEO ALERT\n\n{message}",
            "parse_mode": "HTML"
        }
        requests.post(url, data=data, timeout=10)
    except Exception as e:
        logger.error(f"Failed to send Telegram alert: {e}")


def send_alert(message: str, is_recovery: bool = False):
    """Send alert through all configured channels."""
    global alerts_sent_today
    
    # Log to file
    with open(ALERT_LOG, "a") as f:
        f.write(f"{datetime.utcnow().isoformat()} | {'RECOVERY' if is_recovery else 'ALERT'} | {message}\n")
    
    # Send to Discord
    send_discord_alert(message, is_recovery)
    
    # Send to Telegram
    if not is_recovery:
        send_telegram_alert(message)
    
    alerts_sent_today += 1


def save_status(health_data: dict):
    """Save current status to file."""
    status = {
        "last_check": datetime.utcnow().isoformat(),
        "health_data": health_data,
        "consecutive_failures": consecutive_failures,
        "last_healthy_time": last_healthy_time.isoformat() if last_healthy_time else None,
        "alerts_sent_today": alerts_sent_today
    }
    STATUS_FILE.write_text(json.dumps(status, indent=2))


def run_monitor():
    """Main monitoring loop."""
    global consecutive_failures, last_healthy_time, alerts_sent_today
    
    logger.info("="*70)
    logger.info("🏥 NEO CRITICAL HEALTH MONITOR STARTING")
    logger.info("="*70)
    logger.info(f"Health Endpoint: {HEALTH_ENDPOINT}")
    logger.info(f"Check Interval: {CHECK_INTERVAL}s")
    logger.info(f"Restart after: {MAX_FAILURES_BEFORE_RESTART} failures")
    logger.info(f"Alert after: {MAX_FAILURES_BEFORE_ALERT} failures")
    logger.info("="*70)
    
    last_healthy_time = datetime.utcnow()
    last_alert_day = datetime.utcnow().date()
    
    while True:
        try:
            # Reset daily alert counter
            if datetime.utcnow().date() != last_alert_day:
                alerts_sent_today = 0
                last_alert_day = datetime.utcnow().date()
            
            # Check health
            health = check_health()
            status = health.get("status", "unknown")
            
            if status == "healthy":
                if consecutive_failures > 0:
                    # Recovery!
                    logger.info(f"✅ RECOVERED after {consecutive_failures} failures")
                    send_alert(
                        f"NEO has recovered after {consecutive_failures} consecutive failures.\n"
                        f"Downtime: ~{consecutive_failures} minutes",
                        is_recovery=True
                    )
                
                consecutive_failures = 0
                last_healthy_time = datetime.utcnow()
                
                logger.info(f"✅ HEALTHY | Signals 24h: {health.get('signals_generated_24h', 0)} | "
                          f"Directives age: {health.get('ghost_directives_age_seconds', 0)}s")
                
            elif status in ["degraded", "critical", "unreachable"]:
                consecutive_failures += 1
                issues = health.get("issues", ["Unknown issue"])
                
                logger.warning(f"⚠️ {status.upper()} | Failures: {consecutive_failures} | Issues: {issues}")
                
                # Try to restart after threshold
                if consecutive_failures == MAX_FAILURES_BEFORE_RESTART:
                    logger.warning(f"🔄 Attempting automatic restart...")
                    restart_service("neo-signal-generator")
                    restart_service("neo-ghost-api")
                
                # Send alert after threshold
                if consecutive_failures >= MAX_FAILURES_BEFORE_ALERT:
                    downtime_mins = consecutive_failures
                    alert_msg = (
                        f"⚠️ NEO has been unhealthy for {downtime_mins} minutes!\n\n"
                        f"Status: {status.upper()}\n"
                        f"Issues:\n" + "\n".join(f"• {i}" for i in issues) + "\n\n"
                        f"Services restarted: {'Yes' if consecutive_failures >= MAX_FAILURES_BEFORE_RESTART else 'No'}\n"
                        f"Last healthy: {last_healthy_time.isoformat() if last_healthy_time else 'Unknown'}"
                    )
                    
                    # Only alert every 5 failures to avoid spam
                    if consecutive_failures % 5 == 0:
                        send_alert(alert_msg)
            
            # Save status
            save_status(health)
            
        except Exception as e:
            logger.error(f"❌ Monitor error: {e}")
            consecutive_failures += 1
        
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    run_monitor()
