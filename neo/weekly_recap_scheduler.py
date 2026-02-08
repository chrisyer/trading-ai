#!/usr/bin/env python3
"""
═══════════════════════════════════════════════════════════════════════════════
WEEKLY RESEARCH RECAP — Weekend Scheduler (PM2 Managed)
═══════════════════════════════════════════════════════════════════════════════

Runs as a lightweight PM2 process. Sleeps most of the time.
Wakes up every 30 minutes to check if it's the weekend.
When it IS the weekend and the recap hasn't run yet → generates and sends.

This NEVER runs during live market hours.
This NEVER blocks hourly truth reports.
This NEVER generates trading signals.

PM2 Registration:
  pm2 start weekly_recap_scheduler.py --name weekly-recap --interpreter python3
  pm2 save

Author: QUINN001
Created: February 8, 2026
═══════════════════════════════════════════════════════════════════════════════
"""

import sys
import time
import logging

sys.path.insert(0, '/home/jbot/trading_ai/neo')

from weekly_research_recap import (
    is_weekend,
    already_ran_this_week,
    generate_weekly_recap,
    get_week_end_date,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [WEEKLY-SCHED] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("weekly_scheduler")

# Check every 30 minutes
CHECK_INTERVAL = 1800  # 30 minutes in seconds

# Prefer Saturday 10:00 UTC (well after Friday close)
PREFERRED_HOUR_UTC = 10


def main():
    logger.info("Weekly Research Recap Scheduler started")
    logger.info(f"Check interval: {CHECK_INTERVAL}s | Preferred hour: {PREFERRED_HOUR_UTC}:00 UTC")

    while True:
        try:
            if is_weekend():
                if already_ran_this_week():
                    logger.info(f"Week ending {get_week_end_date()} — already generated. Sleeping.")
                else:
                    from datetime import datetime, timezone
                    now = datetime.now(timezone.utc)
                    # Saturday (5) at preferred hour or later, or any time Sunday (6)
                    if (now.weekday() == 5 and now.hour >= PREFERRED_HOUR_UTC) or now.weekday() == 6:
                        logger.info(f"Weekend trigger! Generating weekly recap for {get_week_end_date()}")
                        ok, msg = generate_weekly_recap(force=False)
                        if ok:
                            logger.info(f"Weekly recap generated successfully ({len(msg)} chars)")
                        else:
                            logger.warning(f"Weekly recap generation issue: {msg[:200]}")
                    else:
                        logger.info(f"Saturday before {PREFERRED_HOUR_UTC}:00 UTC — waiting for preferred window")
            else:
                logger.info("Weekday — sleeping until weekend")

        except Exception as e:
            logger.error(f"Scheduler error: {e}")

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
