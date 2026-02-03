"""
CRITICAL SERVICES HEALTH MONITOR
Checks all trading services and alerts on failure
Runs every 5 minutes during market hours
"""

import asyncio
import httpx
import os
from datetime import datetime
import pytz

ET = pytz.timezone('America/New_York')

# Services to monitor
SERVICES = {
    "neo-gold-api": {"url": "http://localhost:8700/api/neo/gold-forex", "critical": True},
    "trading-agents": {"url": "http://localhost:8890/ops/status", "critical": True},
    "war-room-api": {"url": "http://localhost:3458/health", "critical": False},
}

# Telegram config
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8250652030:AAFd4x8NsTfdaB3O67lUnMhotT2XY61600s")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "6776619257")


async def send_alert(message: str):
    """Send critical alert to Telegram"""
    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                json={
                    "chat_id": TELEGRAM_CHAT_ID,
                    "text": message,
                    "parse_mode": "HTML"
                },
                timeout=30
            )
    except Exception as e:
        print(f"Failed to send alert: {e}")


async def check_service(name: str, config: dict) -> dict:
    """Check if a service is responding"""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(config["url"], timeout=10)
            if response.status_code == 200:
                return {"name": name, "status": "OK", "critical": config["critical"]}
            else:
                return {"name": name, "status": f"ERROR ({response.status_code})", "critical": config["critical"]}
    except Exception as e:
        return {"name": name, "status": f"DOWN ({str(e)[:50]})", "critical": config["critical"]}


async def check_neo_signals() -> dict:
    """Check if Neo is actually generating signals"""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get("http://localhost:8700/api/neo/gold-forex", timeout=10)
            if response.status_code == 200:
                data = response.json()
                gold_status = data.get("gold_status", {})
                if gold_status.get("price") and gold_status.get("direction"):
                    return {
                        "name": "neo-signals",
                        "status": f"OK - {gold_status['direction']} @ ${gold_status['price']}",
                        "critical": True
                    }
                else:
                    return {"name": "neo-signals", "status": "NO SIGNAL DATA", "critical": True}
            else:
                return {"name": "neo-signals", "status": "API ERROR", "critical": True}
    except Exception as e:
        return {"name": "neo-signals", "status": f"DOWN ({str(e)[:30]})", "critical": True}


async def check_data_freshness() -> dict:
    """
    CRITICAL: Verify data is FRESH, not stale cached garbage
    Compare Neo price against live yfinance price
    """
    try:
        import yfinance as yf
        
        # Get Neo's price
        async with httpx.AsyncClient() as client:
            response = await client.get("http://localhost:8700/api/neo/gold-forex", timeout=10)
            if response.status_code != 200:
                return {"name": "data-freshness", "status": "CANNOT CHECK - Neo API down", "critical": True}
            neo_price = response.json().get("gold_status", {}).get("price", 0)
        
        # Get LIVE price from yfinance (using info, NOT history)
        gold = yf.Ticker("GC=F")
        live_price = gold.info.get("regularMarketPrice", 0)
        
        if neo_price == 0 or live_price == 0:
            return {"name": "data-freshness", "status": "CANNOT VERIFY - missing price data", "critical": True}
        
        # Calculate difference
        diff = abs(neo_price - live_price)
        diff_pct = (diff / live_price) * 100
        
        # Allow 0.5% tolerance for timing differences
        if diff_pct > 0.5:
            return {
                "name": "data-freshness", 
                "status": f"STALE DATA! Neo=${neo_price:.2f} vs Live=${live_price:.2f} (diff: {diff_pct:.2f}%)",
                "critical": True
            }
        
        return {
            "name": "data-freshness",
            "status": f"OK - Neo=${neo_price:.2f} matches Live=${live_price:.2f}",
            "critical": True
        }
    except Exception as e:
        return {"name": "data-freshness", "status": f"CHECK FAILED ({str(e)[:40]})", "critical": True}


async def check_defcon() -> dict:
    """Check if DEFCON is blocking trades"""
    try:
        with open("/home/jbot/trading_ai/neo/signals/MT5_DEFCON_STATE.json") as f:
            import json
            state = json.load(f)
            
        ghost = state.get("ghost", {})
        if ghost.get("pause_entries", False):
            return {"name": "defcon-ghost", "status": "PAUSED - Ghost blocked!", "critical": True}
        if ghost.get("max_positions", 0) == 0:
            return {"name": "defcon-ghost", "status": "BLOCKED - max_positions=0", "critical": True}
        if ghost.get("min_confidence", 100) >= 100:
            return {"name": "defcon-ghost", "status": "BLOCKED - min_confidence=100", "critical": True}
            
        return {"name": "defcon-ghost", "status": f"OK - DEFCON {state.get('defcon', '?')}", "critical": True}
    except Exception as e:
        return {"name": "defcon-ghost", "status": f"ERROR ({str(e)[:30]})", "critical": True}


def is_market_hours() -> bool:
    """Check if we're in market hours (5 AM - 5 PM ET for forex/gold)"""
    now = datetime.now(ET)
    # Forex trades Sunday 5 PM to Friday 5 PM
    if now.weekday() == 5:  # Saturday
        return False
    if now.weekday() == 6 and now.hour < 17:  # Sunday before 5 PM
        return False
    if now.weekday() == 4 and now.hour >= 17:  # Friday after 5 PM
        return False
    return True


async def run_health_check():
    """Run full health check"""
    now = datetime.now(ET)
    print(f"\n{'='*60}")
    print(f"HEALTH CHECK - {now.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    print(f"Market Hours: {'YES' if is_market_hours() else 'NO'}")
    print(f"{'='*60}")
    
    results = []
    
    # Check all services
    for name, config in SERVICES.items():
        result = await check_service(name, config)
        results.append(result)
        
    # Check Neo signals specifically
    results.append(await check_neo_signals())
    
    # CRITICAL: Check data freshness - never serve stale data again
    results.append(await check_data_freshness())
    
    # Check DEFCON state
    results.append(await check_defcon())
    
    # Display results
    critical_failures = []
    for r in results:
        status_icon = "✅" if "OK" in r["status"] else "❌"
        crit_icon = "🔴" if r["critical"] else "🟡"
        print(f"  {status_icon} {crit_icon} {r['name']}: {r['status']}")
        
        if "OK" not in r["status"] and r["critical"]:
            critical_failures.append(r)
    
    # Alert on critical failures during market hours
    if critical_failures and is_market_hours():
        msg = f"🚨 <b>CRITICAL SERVICE FAILURE</b>\n\n"
        msg += f"Time: {now.strftime('%H:%M:%S ET')}\n\n"
        for f in critical_failures:
            msg += f"❌ <b>{f['name']}</b>: {f['status']}\n"
        msg += f"\n<i>Services need immediate attention!</i>"
        
        await send_alert(msg)
        print(f"\n⚠️  ALERT SENT - {len(critical_failures)} critical failures!")
    
    return results


async def monitor_loop(interval_minutes: int = 5):
    """Run continuous monitoring"""
    print(f"Starting health monitor (checking every {interval_minutes} minutes)")
    
    while True:
        try:
            await run_health_check()
        except Exception as e:
            print(f"Health check error: {e}")
        
        await asyncio.sleep(interval_minutes * 60)


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == "once":
        # Single check
        asyncio.run(run_health_check())
    else:
        # Continuous monitoring
        asyncio.run(monitor_loop(5))
