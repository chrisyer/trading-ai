#!/usr/bin/env python3
"""
═══════════════════════════════════════════════════════════════════════════════
NEO YOUTUBE ALGO RESEARCH
═══════════════════════════════════════════════════════════════════════════════

Searches YouTube for recent algo trading videos (last 90 days).
Analyzes setups, strategies, and rates their potential for our bots.

Uses:
- Serper API for YouTube search
- Ollama for video content analysis

═══════════════════════════════════════════════════════════════════════════════
"""

import json
import requests
import subprocess
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from pathlib import Path
import logging
import os
import re

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("YOUTUBE_RESEARCH")

# APIs
SERPER_API_KEY = os.getenv('SERPER_API_KEY', '2ae3bf9c33d9a3bb98176cae8d58795ea79eebf1')

DATA_DIR = Path("/home/jbot/trading_ai/data/neo/youtube_research")
DATA_DIR.mkdir(parents=True, exist_ok=True)


# ═══════════════════════════════════════════════════════════════════════════════
# YOUTUBE SEARCH
# ═══════════════════════════════════════════════════════════════════════════════

def search_youtube_videos(query: str, max_results: int = 10) -> List[Dict]:
    """Search YouTube for videos using Serper API"""
    
    try:
        url = "https://google.serper.dev/videos"
        headers = {
            'X-API-KEY': SERPER_API_KEY,
            'Content-Type': 'application/json'
        }
        
        # Add time filter for recent videos
        payload = {
            'q': f'{query} site:youtube.com',
            'num': max_results
        }
        
        response = requests.post(url, headers=headers, json=payload, timeout=15)
        response.raise_for_status()
        data = response.json()
        
        videos = []
        for item in data.get('videos', []):
            # Parse date to check if within 90 days
            date_str = item.get('date', '')
            is_recent = check_if_recent(date_str, days=90)
            
            videos.append({
                'title': item.get('title', ''),
                'link': item.get('link', ''),
                'channel': item.get('channel', ''),
                'duration': item.get('duration', ''),
                'date': date_str,
                'snippet': item.get('snippet', ''),
                'thumbnail': item.get('thumbnail', ''),
                'is_recent': is_recent
            })
        
        # Filter to only recent videos
        recent_videos = [v for v in videos if v['is_recent']]
        
        return recent_videos
        
    except Exception as e:
        logger.error(f"YouTube search failed: {e}")
        return []


def check_if_recent(date_str: str, days: int = 90) -> bool:
    """Check if date string indicates video is within N days"""
    if not date_str:
        return False
    
    date_lower = date_str.lower()
    
    # Handle relative dates
    if 'hour' in date_lower or 'minute' in date_lower:
        return True
    if 'day' in date_lower:
        match = re.search(r'(\d+)\s*day', date_lower)
        if match:
            return int(match.group(1)) <= days
        return True
    if 'week' in date_lower:
        match = re.search(r'(\d+)\s*week', date_lower)
        if match:
            return int(match.group(1)) * 7 <= days
        return True
    if 'month' in date_lower:
        match = re.search(r'(\d+)\s*month', date_lower)
        if match:
            return int(match.group(1)) * 30 <= days
        return True
    if 'year' in date_lower:
        return False
    
    # Try parsing actual date
    try:
        for fmt in ['%b %d, %Y', '%Y-%m-%d', '%d %b %Y']:
            try:
                parsed = datetime.strptime(date_str, fmt)
                return (datetime.now() - parsed).days <= days
            except:
                continue
    except:
        pass
    
    # Default to including if we can't parse
    return True


# ═══════════════════════════════════════════════════════════════════════════════
# VIDEO ANALYSIS WITH OLLAMA
# ═══════════════════════════════════════════════════════════════════════════════

def analyze_video_with_ollama(video: Dict) -> Dict:
    """Use Ollama to analyze video content based on title/description"""
    
    prompt = f"""You are an expert algo trader analyzing a YouTube video about trading strategies.

VIDEO INFO:
Title: {video.get('title', '')}
Channel: {video.get('channel', '')}
Description/Snippet: {video.get('snippet', '')}
Duration: {video.get('duration', '')}

Based on this video's title and description, analyze:

1. STRATEGY TYPE: What kind of trading strategy is this? (scalping, swing, trend following, mean reversion, etc.)

2. KEY INDICATORS: What indicators or tools does it likely discuss? (RSI, MACD, EMA, volume, etc.)

3. TIMEFRAME: What timeframe is this strategy for? (M1, M5, M15, H1, H4, D1)

4. ASSET CLASS: What market is this for? (Forex, Gold, Stocks, Crypto, Futures)

5. SETUP DETAILS: Based on the title, what's the core setup/entry logic?

6. POTENTIAL VALUE: Rate 1-10 how useful this could be for our gold/forex trading bots.

7. IMPLEMENTATION DIFFICULTY: Rate 1-10 how hard this would be to code.

8. RECOMMENDATION: Should we watch this video? (YES/MAYBE/NO)

Respond in JSON format:
{{
    "strategy_type": "...",
    "indicators": ["..."],
    "timeframe": "...",
    "asset_class": "...",
    "setup_summary": "...",
    "potential_value": 7,
    "implementation_difficulty": 5,
    "recommendation": "YES/MAYBE/NO",
    "key_takeaway": "One sentence summary of what we could learn"
}}"""

    try:
        result = subprocess.run(
            ["ollama", "run", "llama3.1:8b"],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=60
        )
        
        response = result.stdout.strip()
        
        # Extract JSON from response
        start = response.find('{')
        end = response.rfind('}') + 1
        if start >= 0 and end > start:
            analysis = json.loads(response[start:end])
            return analysis
        
        return {"error": "Could not parse response", "raw": response[:500]}
        
    except subprocess.TimeoutExpired:
        return {"error": "Ollama timeout"}
    except Exception as e:
        return {"error": str(e)}


# ═══════════════════════════════════════════════════════════════════════════════
# RESEARCH QUERIES
# ═══════════════════════════════════════════════════════════════════════════════

RESEARCH_QUERIES = [
    "gold trading strategy 2026 algorithm",
    "XAUUSD scalping strategy profitable",
    "forex algorithm trading setup 2026",
    "RSI trading strategy backtest results",
    "EMA crossover gold trading",
    "price action trading algorithm",
    "smart money concept trading algo",
    "order block trading strategy",
    "liquidity sweep trading setup",
    "mean reversion trading algorithm",
    "trend following algo trading",
    "supply demand zone trading",
    "breakout trading strategy algorithm",
    "trading bot MQL5 strategy",
    "automated trading strategy profitable"
]


def run_full_research(queries: List[str] = None, max_videos_per_query: int = 5) -> Dict:
    """Run full YouTube research across multiple queries"""
    
    if queries is None:
        queries = RESEARCH_QUERIES[:8]  # Default to first 8
    
    logger.info("="*70)
    logger.info("📺 YOUTUBE ALGO RESEARCH")
    logger.info("="*70)
    
    all_videos = []
    seen_links = set()
    
    for query in queries:
        logger.info(f"\n🔍 Searching: {query}")
        videos = search_youtube_videos(query, max_videos_per_query)
        
        for video in videos:
            if video['link'] not in seen_links:
                seen_links.add(video['link'])
                all_videos.append(video)
                logger.info(f"   📹 {video['title'][:50]}... ({video['date']})")
    
    logger.info(f"\n📊 Found {len(all_videos)} unique recent videos")
    
    # Analyze top videos with Ollama
    logger.info("\n🤖 Analyzing videos with AI...")
    analyzed = []
    
    for i, video in enumerate(all_videos[:15]):  # Analyze top 15
        logger.info(f"\n[{i+1}/{min(15, len(all_videos))}] Analyzing: {video['title'][:40]}...")
        
        analysis = analyze_video_with_ollama(video)
        
        analyzed.append({
            **video,
            "analysis": analysis
        })
        
        if "error" not in analysis:
            value = analysis.get('potential_value', 0)
            rec = analysis.get('recommendation', 'N/A')
            logger.info(f"   Value: {value}/10 | Recommendation: {rec}")
    
    # Sort by potential value
    analyzed.sort(key=lambda x: x.get('analysis', {}).get('potential_value', 0), reverse=True)
    
    # Build report
    report = {
        "timestamp": datetime.utcnow().isoformat(),
        "queries_used": queries,
        "total_videos_found": len(all_videos),
        "videos_analyzed": len(analyzed),
        "top_recommendations": [
            v for v in analyzed 
            if v.get('analysis', {}).get('recommendation') == 'YES'
        ][:5],
        "all_analyzed": analyzed
    }
    
    # Save report
    save_path = DATA_DIR / f"research_{datetime.now().strftime('%Y%m%d_%H%M')}.json"
    save_path.write_text(json.dumps(report, indent=2))
    logger.info(f"\n📁 Saved to {save_path}")
    
    return report


def format_report(report: Dict) -> str:
    """Format report for display"""
    
    lines = [
        "═══════════════════════════════════════════════════════════════════════════════",
        "📺 YOUTUBE ALGO RESEARCH REPORT",
        f"   Generated: {report.get('timestamp', 'N/A')}",
        f"   Videos Found: {report.get('total_videos_found', 0)}",
        f"   Videos Analyzed: {report.get('videos_analyzed', 0)}",
        "═══════════════════════════════════════════════════════════════════════════════",
        "",
        "🏆 TOP RECOMMENDATIONS (Watch These!):",
        "─────────────────────────────────────────────────────────────────────────────",
    ]
    
    top_recs = report.get('top_recommendations', [])
    if not top_recs:
        lines.append("   No strong recommendations found.")
    else:
        for i, video in enumerate(top_recs, 1):
            analysis = video.get('analysis', {})
            lines.extend([
                f"",
                f"#{i}. {video.get('title', 'N/A')[:60]}...",
                f"   Channel: {video.get('channel', 'N/A')}",
                f"   Date: {video.get('date', 'N/A')} | Duration: {video.get('duration', 'N/A')}",
                f"   Link: {video.get('link', 'N/A')}",
                f"",
                f"   📊 Analysis:",
                f"   Strategy: {analysis.get('strategy_type', 'N/A')}",
                f"   Indicators: {', '.join(analysis.get('indicators', ['N/A']))}",
                f"   Timeframe: {analysis.get('timeframe', 'N/A')}",
                f"   Asset: {analysis.get('asset_class', 'N/A')}",
                f"   Setup: {analysis.get('setup_summary', 'N/A')[:80]}...",
                f"",
                f"   ⭐ Value: {analysis.get('potential_value', 0)}/10",
                f"   🔧 Difficulty: {analysis.get('implementation_difficulty', 0)}/10",
                f"   💡 Takeaway: {analysis.get('key_takeaway', 'N/A')[:70]}...",
                f"",
            ])
    
    lines.extend([
        "",
        "═══════════════════════════════════════════════════════════════════════════════",
        "📋 ALL ANALYZED VIDEOS (sorted by value):",
        "═══════════════════════════════════════════════════════════════════════════════",
    ])
    
    for video in report.get('all_analyzed', [])[:10]:
        analysis = video.get('analysis', {})
        value = analysis.get('potential_value', 0)
        rec = analysis.get('recommendation', 'N/A')
        rec_emoji = "🟢" if rec == "YES" else "🟡" if rec == "MAYBE" else "🔴"
        
        lines.append(f"")
        lines.append(f"  {rec_emoji} [{value}/10] {video.get('title', 'N/A')[:55]}...")
        lines.append(f"     {video.get('channel', '')} | {video.get('date', '')}")
        
        if 'error' not in analysis:
            lines.append(f"     Strategy: {analysis.get('strategy_type', 'N/A')} | {analysis.get('timeframe', 'N/A')}")
    
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    
    # Custom query if provided
    if len(sys.argv) > 1 and not sys.argv[1].startswith("-"):
        custom_query = " ".join([a for a in sys.argv[1:] if not a.startswith("-")])
        queries = [custom_query]
    else:
        queries = RESEARCH_QUERIES[:6]
    
    report = run_full_research(queries)
    print(format_report(report))
