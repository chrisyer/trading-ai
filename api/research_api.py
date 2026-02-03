#!/usr/bin/env python3
"""
═══════════════════════════════════════════════════════════════════════════════
RESEARCH & FEEDBACK API
═══════════════════════════════════════════════════════════════════════════════

Provides API endpoints for:
1. Triggering nightly research
2. Submitting feedback on analyses
3. Getting daily briefs
4. Viewing agent performance
5. Fetching news sentiment

Port: 8891
"""

import asyncio
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Any
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import httpx
import logging

# Add paths for imports
sys.path.insert(0, '/home/jbot/trading_ai/cron')

from nightly_research import (
    run_nightly_research,
    gather_all_news,
    analyze_sentiment_with_ai,
    get_agent_accuracies
)
from daily_feedback import (
    collect_hourly_feedback,
    collect_close_feedback,
    submit_manual_feedback,
    submit_trade_to_journal
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ResearchAPI")

app = FastAPI(
    title="Research & Feedback API",
    description="Nightly research, daily feedback, and news sentiment",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

REPORTS_DIR = Path("/home/jbot/trading_ai/neo/reports")
DAILY_DATA_DIR = Path("/home/jbot/trading_ai/neo/daily_data")
KNOWLEDGE_API = "http://localhost:8890/knowledge"


# ═══════════════════════════════════════════════════════════════════════════════
# PYDANTIC MODELS
# ═══════════════════════════════════════════════════════════════════════════════

class FeedbackSubmission(BaseModel):
    analysis_id: str
    outcome: str  # "correct", "partially_correct", "incorrect"
    price_after: float = 0
    pnl: float = 0
    notes: str = ""


class TradeSubmission(BaseModel):
    action: str  # "BUY", "SELL", "SCALE_IN", "CLOSE"
    symbol: str = "XAUUSD"
    size: float = 0
    price: float = 0
    reason: str = ""
    agent: str = ""


class LessonSubmission(BaseModel):
    content: str
    lesson_type: str = "lesson"  # "lesson", "rule", "observation", "warning"
    agent: str = "all"
    tags: List[str] = []
    importance: int = 7
    permanent: bool = False


# ═══════════════════════════════════════════════════════════════════════════════
# RESEARCH ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "research-api",
        "timestamp": datetime.now().isoformat()
    }


@app.post("/research/run-nightly")
async def trigger_nightly_research(background_tasks: BackgroundTasks):
    """Trigger the nightly research job manually."""
    background_tasks.add_task(run_nightly_research)
    return {
        "status": "started",
        "message": "Nightly research running in background"
    }


@app.get("/research/news")
async def get_news_sentiment():
    """Get current news and sentiment analysis."""
    news = await gather_all_news()
    sentiment = await analyze_sentiment_with_ai(news)
    
    return {
        "news": news,
        "sentiment": sentiment,
        "timestamp": datetime.now().isoformat()
    }


@app.get("/research/daily-brief")
async def get_daily_brief(date: Optional[str] = None):
    """Get the daily brief for a specific date or today."""
    if not date:
        date = datetime.now().strftime("%Y%m%d")
    
    brief_file = REPORTS_DIR / f"DAILY_BRIEF_{date}.md"
    
    if not brief_file.exists():
        raise HTTPException(status_code=404, detail=f"No brief found for {date}")
    
    with open(brief_file) as f:
        content = f.read()
    
    return {
        "date": date,
        "content": content
    }


@app.get("/research/briefs")
async def list_daily_briefs(limit: int = 10):
    """List available daily briefs."""
    briefs = []
    
    for f in sorted(REPORTS_DIR.glob("DAILY_BRIEF_*.md"), reverse=True)[:limit]:
        date = f.stem.replace("DAILY_BRIEF_", "")
        briefs.append({
            "date": date,
            "filename": f.name,
            "size": f.stat().st_size
        })
    
    return {"briefs": briefs}


# ═══════════════════════════════════════════════════════════════════════════════
# FEEDBACK ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

@app.post("/feedback/collect")
async def trigger_feedback_collection(mode: str = "hourly"):
    """Trigger feedback collection."""
    if mode == "hourly":
        result = await collect_hourly_feedback()
        return {"status": "complete", "outcomes_recorded": result}
    elif mode == "close":
        result = await collect_close_feedback()
        return {"status": "complete", "summary": result}
    else:
        raise HTTPException(status_code=400, detail="Invalid mode. Use 'hourly' or 'close'")


@app.post("/feedback/submit")
async def submit_feedback(feedback: FeedbackSubmission):
    """Submit feedback on an analysis."""
    success = await submit_manual_feedback(
        feedback.analysis_id,
        feedback.outcome,
        feedback.price_after,
        feedback.pnl,
        feedback.notes
    )
    
    if success:
        return {"status": "recorded", "outcome": feedback.outcome}
    else:
        raise HTTPException(status_code=500, detail="Failed to record feedback")


@app.post("/feedback/trade")
async def submit_trade(trade: TradeSubmission):
    """Submit a trade to the journal."""
    success = await submit_trade_to_journal(
        trade.action,
        trade.symbol,
        trade.size,
        trade.price,
        trade.reason,
        trade.agent
    )
    
    if success:
        return {"status": "logged", "action": trade.action}
    else:
        raise HTTPException(status_code=500, detail="Failed to log trade")


@app.post("/feedback/lesson")
async def submit_lesson(lesson: LessonSubmission):
    """Submit a lesson learned to the knowledge base."""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{KNOWLEDGE_API}/feed/claudia",
                json={
                    "lesson_type": lesson.lesson_type,
                    "content": lesson.content,
                    "applies_to": [lesson.agent],
                    "tags": lesson.tags,
                    "importance": lesson.importance,
                    "permanent": lesson.permanent
                },
                timeout=30.0
            )
            
            if response.status_code == 200:
                return {"status": "added", "content": lesson.content[:50] + "..."}
            else:
                raise HTTPException(status_code=500, detail="Knowledge API error")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ═══════════════════════════════════════════════════════════════════════════════
# PERFORMANCE ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/performance/agents")
async def get_agent_performance(days: int = 7):
    """Get performance stats for all agents."""
    accuracies = await get_agent_accuracies()
    
    return {
        "period_days": days,
        "agents": accuracies,
        "timestamp": datetime.now().isoformat()
    }


@app.get("/performance/summary")
async def get_performance_summary():
    """Get a summary of recent performance."""
    # Get recent summaries
    summaries = []
    for f in sorted(DAILY_DATA_DIR.glob("summary_*.json"), reverse=True)[:7]:
        try:
            with open(f) as file:
                summaries.append(json.load(file))
        except:
            pass
    
    # Calculate totals
    total_analyses = sum(s.get("analyses_count", 0) for s in summaries)
    total_pnl = sum(s.get("daily_pnl", 0) for s in summaries)
    
    return {
        "days_tracked": len(summaries),
        "total_analyses": total_analyses,
        "total_pnl": total_pnl,
        "daily_summaries": summaries
    }


# ═══════════════════════════════════════════════════════════════════════════════
# RECENT ANALYSES
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/analyses/pending")
async def get_pending_analyses():
    """Get analyses that need outcome feedback."""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{KNOWLEDGE_API}/recent_analyses",
                params={"hours": 48},
                timeout=30.0
            )
            if response.status_code == 200:
                data = response.json()
                # Filter to those without outcomes
                pending = [a for a in data.get("analyses", []) if not a.get("outcome")]
                return {"pending": pending, "count": len(pending)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/analyses/recent")
async def get_recent_analyses(hours: int = 24, agent: Optional[str] = None):
    """Get recent analyses."""
    try:
        params = {"hours": hours}
        if agent:
            params["agent"] = agent
        
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{KNOWLEDGE_API}/recent_analyses",
                params=params,
                timeout=30.0
            )
            if response.status_code == 200:
                return response.json()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ═══════════════════════════════════════════════════════════════════════════════
# SCHEDULED TASK RUNNER (for PM2)
# ═══════════════════════════════════════════════════════════════════════════════

async def scheduled_hourly():
    """Run hourly tasks."""
    while True:
        try:
            logger.info("Running hourly feedback collection...")
            await collect_hourly_feedback()
        except Exception as e:
            logger.error(f"Hourly task failed: {e}")
        
        # Sleep until next hour
        await asyncio.sleep(3600)


async def scheduled_nightly():
    """Run nightly research at 10 PM."""
    while True:
        now = datetime.now()
        
        # Calculate time until 10 PM
        target = now.replace(hour=22, minute=0, second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)
        
        sleep_seconds = (target - now).total_seconds()
        logger.info(f"Next nightly research in {sleep_seconds/3600:.1f} hours")
        
        await asyncio.sleep(sleep_seconds)
        
        try:
            logger.info("Running nightly research...")
            await run_nightly_research()
        except Exception as e:
            logger.error(f"Nightly research failed: {e}")


@app.on_event("startup")
async def startup_event():
    """Start scheduled tasks on startup."""
    asyncio.create_task(scheduled_hourly())
    asyncio.create_task(scheduled_nightly())
    logger.info("Scheduled tasks started")


if __name__ == "__main__":
    import uvicorn
    print("Starting Research API on port 8891...")
    uvicorn.run(app, host="0.0.0.0", port=8891)
