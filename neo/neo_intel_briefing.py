#!/usr/bin/env python3
"""
═══════════════════════════════════════════════════════════════════════════════
NEO INTELLIGENCE BRIEFING
═══════════════════════════════════════════════════════════════════════════════

NEO's job is INTEL, not trading. This generates the daily intelligence report.

CONTENTS:
1. Yesterday's Market Summary - What happened and why
2. Economic Calendar - What events could move gold today
3. News Analysis - Recent articles and their impact
4. Correlation Check - USD, yields, risk sentiment
5. Scenario Analysis - What could play out today
6. Key Levels to Watch

═══════════════════════════════════════════════════════════════════════════════
"""

import json
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from pathlib import Path
import logging
import requests
import os

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("NEO_INTEL")

# APIs
SERPER_API_KEY = os.getenv('SERPER_API_KEY', '2ae3bf9c33d9a3bb98176cae8d58795ea79eebf1')
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '8250652030:AAFd4x8NsTfdaB3O67lUnMhotT2XY61600s')
ADMIN_CHAT_ID = os.environ.get('ADMIN_CHAT_ID', '6776619257')

DATA_DIR = Path("/home/jbot/trading_ai/data/neo")
INTEL_DIR = DATA_DIR / "intel"
INTEL_DIR.mkdir(exist_ok=True)


# ═══════════════════════════════════════════════════════════════════════════════
# DATA FETCHING
# ═══════════════════════════════════════════════════════════════════════════════

def fetch_ohlcv(symbol: str = "XAUUSD", period: str = "5d", interval: str = "1h") -> pd.DataFrame:
    """Fetch OHLCV data"""
    try:
        import yfinance as yf
        ticker_map = {
            "XAUUSD": "GC=F",
            "DXY": "DX-Y.NYB",
            "US10Y": "^TNX",
            "SPY": "SPY",
            "VIX": "^VIX"
        }
        ticker = ticker_map.get(symbol, symbol)
        data = yf.Ticker(ticker).history(period=period, interval=interval)
        if not data.empty:
            data.columns = [c.lower() for c in data.columns]
        return data
    except Exception as e:
        logger.error(f"Failed to fetch {symbol}: {e}")
        return pd.DataFrame()


def search_news(query: str, days_back: int = 3, limit: int = 10) -> List[Dict]:
    """Search news - try Serper first, fallback to Yahoo Finance"""
    results = []
    
    # Try Serper API first
    try:
        url = "https://google.serper.dev/news"
        headers = {
            'X-API-KEY': SERPER_API_KEY,
            'Content-Type': 'application/json'
        }
        payload = {
            'q': query,
            'num': limit
        }
        
        response = requests.post(url, headers=headers, json=payload, timeout=15)
        response.raise_for_status()
        data = response.json()
        
        for item in data.get('news', [])[:limit]:
            sentiment = analyze_sentiment(item.get('title', '') + ' ' + item.get('snippet', ''))
            results.append({
                'title': item.get('title', ''),
                'snippet': item.get('snippet', ''),
                'source': item.get('source', ''),
                'date': item.get('date', ''),
                'link': item.get('link', ''),
                'sentiment': sentiment,
                'impact': estimate_impact(item.get('title', ''))
            })
        return results
    except Exception as e:
        logger.warning(f"Serper API failed: {e}, trying Yahoo Finance...")
    
    # Fallback: Yahoo Finance news
    try:
        import yfinance as yf
        
        # Get gold-related news from GC=F
        ticker = yf.Ticker("GC=F")
        news = ticker.news or []
        
        for item in news[:limit]:
            title = item.get('title', '')
            sentiment = analyze_sentiment(title)
            results.append({
                'title': title,
                'snippet': item.get('summary', '')[:200] if item.get('summary') else '',
                'source': item.get('publisher', 'Yahoo Finance'),
                'date': datetime.fromtimestamp(item.get('providerPublishTime', 0)).strftime('%Y-%m-%d') if item.get('providerPublishTime') else '',
                'link': item.get('link', ''),
                'sentiment': sentiment,
                'impact': estimate_impact(title)
            })
        
        logger.info(f"Got {len(results)} news from Yahoo Finance")
        return results
    except Exception as e:
        logger.error(f"Yahoo Finance news also failed: {e}")
        return []


def analyze_sentiment(text: str) -> str:
    """Simple sentiment analysis"""
    text_lower = text.lower()
    
    bullish_words = ['surge', 'jump', 'rise', 'rally', 'soar', 'climb', 'gain', 'bullish', 
                    'record high', 'breakout', 'strong', 'safe haven', 'inflation fears',
                    'geopolitical', 'uncertainty', 'dovish', 'rate cut', 'buying']
    bearish_words = ['fall', 'drop', 'decline', 'plunge', 'bearish', 'selloff', 'retreat',
                    'hawkish', 'rate hike', 'strong dollar', 'yields rise', 'risk on',
                    'selling pressure', 'profit taking']
    
    bullish_count = sum(1 for w in bullish_words if w in text_lower)
    bearish_count = sum(1 for w in bearish_words if w in text_lower)
    
    if bullish_count > bearish_count:
        return 'BULLISH'
    elif bearish_count > bullish_count:
        return 'BEARISH'
    return 'NEUTRAL'


def estimate_impact(title: str) -> str:
    """Estimate news impact on gold"""
    title_lower = title.lower()
    
    high_impact = ['fed', 'fomc', 'interest rate', 'inflation', 'cpi', 'pce', 'powell',
                   'geopolitical', 'war', 'crisis', 'central bank', 'recession']
    medium_impact = ['dollar', 'yields', 'treasury', 'economic data', 'jobs', 'gdp',
                    'china', 'india', 'etf flows', 'demand']
    
    if any(w in title_lower for w in high_impact):
        return 'HIGH'
    elif any(w in title_lower for w in medium_impact):
        return 'MEDIUM'
    return 'LOW'


def get_economic_calendar() -> List[Dict]:
    """Get today's economic calendar events that affect gold"""
    # In production, this would hit an economic calendar API
    # For now, return known important events
    
    today = datetime.utcnow()
    day_of_week = today.strftime('%A')
    
    # Common event times (UTC)
    events = []
    
    # Standard high-impact events by day
    if day_of_week == 'Monday':
        events.append({
            'time': '14:00 UTC',
            'event': 'ISM Manufacturing (if first Monday)',
            'impact': 'MEDIUM',
            'expected_effect': 'Strong data = bearish gold (USD strength)'
        })
    elif day_of_week == 'Wednesday':
        events.append({
            'time': '14:00 UTC', 
            'event': 'FOMC Minutes/Decision (check calendar)',
            'impact': 'HIGH',
            'expected_effect': 'Hawkish = bearish gold, Dovish = bullish gold'
        })
    elif day_of_week == 'Thursday':
        events.append({
            'time': '13:30 UTC',
            'event': 'Weekly Jobless Claims',
            'impact': 'MEDIUM',
            'expected_effect': 'High claims = bullish gold (weak economy)'
        })
    elif day_of_week == 'Friday':
        events.append({
            'time': '13:30 UTC',
            'event': 'NFP/Jobs Report (first Friday of month)',
            'impact': 'HIGH',
            'expected_effect': 'Strong jobs = bearish gold, Weak = bullish gold'
        })
    
    # Check for specific dates (CPI, etc)
    day_of_month = today.day
    if 10 <= day_of_month <= 15:
        events.append({
            'time': '13:30 UTC',
            'event': 'CPI Report (if mid-month)',
            'impact': 'HIGH',
            'expected_effect': 'High CPI = initially bullish (inflation hedge), then bearish (rate hikes)'
        })
    
    return events


# ═══════════════════════════════════════════════════════════════════════════════
# ANALYSIS
# ═══════════════════════════════════════════════════════════════════════════════

def analyze_yesterday(symbol: str = "XAUUSD") -> Dict:
    """Analyze yesterday's trading session"""
    df = fetch_ohlcv(symbol, "5d", "1h")
    if df.empty:
        return {"error": "No data"}
    
    # Get yesterday's data
    df['date'] = df.index.date
    dates = df['date'].unique()
    
    if len(dates) < 2:
        return {"error": "Not enough history"}
    
    yesterday = dates[-2]
    yesterday_data = df[df['date'] == yesterday]
    
    if yesterday_data.empty:
        return {"error": "No yesterday data"}
    
    open_price = float(yesterday_data['open'].iloc[0])
    close_price = float(yesterday_data['close'].iloc[-1])
    high = float(yesterday_data['high'].max())
    low = float(yesterday_data['low'].min())
    
    move = close_price - open_price
    range_pts = high - low
    
    # Determine session character
    if move > range_pts * 0.3:
        character = "BULLISH TREND DAY"
        description = "Strong buyers, consistent higher lows"
    elif move < -range_pts * 0.3:
        character = "BEARISH TREND DAY"
        description = "Strong sellers, consistent lower highs"
    elif range_pts < 30:
        character = "CONSOLIDATION"
        description = "Tight range, coiling for move"
    else:
        character = "ROTATION DAY"
        description = "Up and down, no clear winner"
    
    # Key observations
    observations = []
    
    # Did we make new highs/lows?
    week_high = df['high'].max()
    week_low = df['low'].min()
    
    if high >= week_high * 0.999:
        observations.append("🔥 Made new weekly high - bulls in control")
    if low <= week_low * 1.001:
        observations.append("⚠️ Made new weekly low - bears attacking")
    
    # Closing position in range
    close_pct = (close_price - low) / range_pts if range_pts > 0 else 0.5
    if close_pct > 0.7:
        observations.append("📈 Closed near highs - bullish sentiment")
    elif close_pct < 0.3:
        observations.append("📉 Closed near lows - bearish sentiment")
    else:
        observations.append("⚖️ Closed mid-range - indecision")
    
    # Volume analysis (if available)
    if 'volume' in yesterday_data.columns:
        avg_vol = df['volume'].mean()
        yesterday_vol = yesterday_data['volume'].mean()
        if yesterday_vol > avg_vol * 1.3:
            observations.append("📊 High volume day - significant activity")
        elif yesterday_vol < avg_vol * 0.7:
            observations.append("📊 Low volume day - lack of conviction")
    
    return {
        "date": str(yesterday),
        "open": open_price,
        "high": high,
        "low": low,
        "close": close_price,
        "move": move,
        "move_pct": (move / open_price) * 100,
        "range": range_pts,
        "character": character,
        "description": description,
        "observations": observations,
        "close_position_in_range": close_pct
    }


def check_correlations() -> Dict:
    """Check key correlations for gold"""
    correlations = {}
    
    # DXY (Dollar Index) - inverse correlation
    dxy = fetch_ohlcv("DXY", "5d", "1h")
    if not dxy.empty:
        dxy_current = float(dxy['close'].iloc[-1])
        dxy_yesterday = float(dxy['close'].iloc[-24]) if len(dxy) >= 24 else dxy_current
        dxy_change = ((dxy_current - dxy_yesterday) / dxy_yesterday) * 100
        
        if dxy_change > 0.3:
            signal = "BEARISH for Gold (Dollar strength)"
        elif dxy_change < -0.3:
            signal = "BULLISH for Gold (Dollar weakness)"
        else:
            signal = "NEUTRAL"
        
        correlations["DXY"] = {
            "current": dxy_current,
            "change_pct": dxy_change,
            "gold_impact": signal
        }
    
    # US 10Y Yields - usually inverse
    yields = fetch_ohlcv("US10Y", "5d", "1d")
    if not yields.empty:
        yield_current = float(yields['close'].iloc[-1])
        yield_yesterday = float(yields['close'].iloc[-2]) if len(yields) >= 2 else yield_current
        yield_change = yield_current - yield_yesterday
        
        if yield_change > 0.02:
            signal = "BEARISH for Gold (yields rising, opportunity cost higher)"
        elif yield_change < -0.02:
            signal = "BULLISH for Gold (yields falling, gold more attractive)"
        else:
            signal = "NEUTRAL"
        
        correlations["US10Y"] = {
            "current": yield_current,
            "change_bps": yield_change * 100,
            "gold_impact": signal
        }
    
    # VIX (Fear) - positive correlation
    vix = fetch_ohlcv("VIX", "5d", "1d")
    if not vix.empty:
        vix_current = float(vix['close'].iloc[-1])
        vix_yesterday = float(vix['close'].iloc[-2]) if len(vix) >= 2 else vix_current
        vix_change = ((vix_current - vix_yesterday) / vix_yesterday) * 100
        
        if vix_current > 25:
            signal = "BULLISH for Gold (high fear, safe haven demand)"
        elif vix_current < 15:
            signal = "BEARISH for Gold (low fear, risk-on sentiment)"
        else:
            signal = "NEUTRAL"
        
        correlations["VIX"] = {
            "current": vix_current,
            "change_pct": vix_change,
            "gold_impact": signal
        }
    
    # Overall correlation signal
    bullish_count = sum(1 for c in correlations.values() if "BULLISH" in c.get("gold_impact", ""))
    bearish_count = sum(1 for c in correlations.values() if "BEARISH" in c.get("gold_impact", ""))
    
    if bullish_count > bearish_count:
        correlations["overall"] = "BULLISH ALIGNMENT"
    elif bearish_count > bullish_count:
        correlations["overall"] = "BEARISH ALIGNMENT"
    else:
        correlations["overall"] = "MIXED SIGNALS"
    
    return correlations


def generate_scenarios(yesterday: Dict, correlations: Dict, news: List[Dict]) -> List[Dict]:
    """Generate possible scenarios for today"""
    
    scenarios = []
    
    # Get current bias from correlations
    overall_bias = correlations.get("overall", "MIXED")
    
    # Scenario 1: Trend Continuation
    if yesterday.get("character") in ["BULLISH TREND DAY"]:
        scenarios.append({
            "name": "BULLISH CONTINUATION",
            "probability": 60 if "BULLISH" in overall_bias else 45,
            "description": "Yesterday's bulls continue, price makes new highs",
            "trigger": f"Price holds above ${yesterday.get('low', 0):.0f} (yesterday's low)",
            "target": f"${yesterday.get('high', 0) + 30:.0f} (extension)",
            "invalidation": f"Close below ${yesterday.get('low', 0):.0f}",
            "action": "BUY dips to yesterday's range"
        })
    elif yesterday.get("character") in ["BEARISH TREND DAY"]:
        scenarios.append({
            "name": "BEARISH CONTINUATION", 
            "probability": 55 if "BEARISH" in overall_bias else 40,
            "description": "Yesterday's sellers continue, price makes new lows",
            "trigger": f"Price holds below ${yesterday.get('high', 0):.0f} (yesterday's high)",
            "target": f"${yesterday.get('low', 0) - 30:.0f} (extension)",
            "invalidation": f"Close above ${yesterday.get('high', 0):.0f}",
            "action": "SELL rallies to yesterday's range"
        })
    
    # Scenario 2: Mean Reversion
    if yesterday.get("move", 0) > 40:
        scenarios.append({
            "name": "PULLBACK/MEAN REVERSION",
            "probability": 35 if "BEARISH" in overall_bias else 25,
            "description": "After strong up move, profit taking drags price down",
            "trigger": f"Failure to break ${yesterday.get('high', 0):.0f}",
            "target": f"${(yesterday.get('high', 0) + yesterday.get('low', 0)) / 2:.0f} (mid-range)",
            "invalidation": f"New high above ${yesterday.get('high', 0) + 20:.0f}",
            "action": "Wait for reversal signals before shorting"
        })
    elif yesterday.get("move", 0) < -40:
        scenarios.append({
            "name": "BOUNCE/RELIEF RALLY",
            "probability": 35 if "BULLISH" in overall_bias else 25,
            "description": "After strong down move, bargain hunters step in",
            "trigger": f"Price holds ${yesterday.get('low', 0):.0f} twice",
            "target": f"${(yesterday.get('high', 0) + yesterday.get('low', 0)) / 2:.0f} (mid-range)",
            "invalidation": f"New low below ${yesterday.get('low', 0) - 20:.0f}",
            "action": "Wait for reversal signals before buying"
        })
    
    # Scenario 3: News-Driven Move
    high_impact_news = [n for n in news if n.get('impact') == 'HIGH']
    if high_impact_news:
        bullish_news = [n for n in high_impact_news if n.get('sentiment') == 'BULLISH']
        bearish_news = [n for n in high_impact_news if n.get('sentiment') == 'BEARISH']
        
        if bullish_news:
            scenarios.append({
                "name": "NEWS-DRIVEN RALLY",
                "probability": 50,
                "description": f"Recent bullish headlines could drive buying: {bullish_news[0].get('title', '')[:50]}...",
                "trigger": "Opening gap up or strong buying in first hour",
                "target": "New highs",
                "invalidation": "Quick reversal of gap",
                "action": "BUY on confirmed momentum"
            })
        
        if bearish_news:
            scenarios.append({
                "name": "NEWS-DRIVEN SELLOFF",
                "probability": 45,
                "description": f"Recent bearish headlines could pressure: {bearish_news[0].get('title', '')[:50]}...",
                "trigger": "Opening gap down or strong selling in first hour",
                "target": "New lows",
                "invalidation": "Quick reversal of gap",
                "action": "SELL on confirmed weakness"
            })
    
    # Scenario 4: Range Day
    if yesterday.get("character") in ["CONSOLIDATION", "ROTATION DAY"]:
        scenarios.append({
            "name": "RANGE CONTINUATION",
            "probability": 40,
            "description": "Price chops between yesterday's high and low",
            "trigger": f"Rejection at ${yesterday.get('high', 0):.0f} or ${yesterday.get('low', 0):.0f}",
            "target": "Opposite end of range",
            "invalidation": "Breakout above/below range with volume",
            "action": "FADE extremes, take quick profits"
        })
    
    # Sort by probability
    scenarios.sort(key=lambda x: x['probability'], reverse=True)
    
    return scenarios


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN INTEL BRIEFING
# ═══════════════════════════════════════════════════════════════════════════════

def generate_intel_briefing(symbol: str = "XAUUSD") -> Dict:
    """Generate comprehensive daily intel briefing"""
    
    logger.info("="*70)
    logger.info("🔍 NEO INTELLIGENCE BRIEFING")
    logger.info("="*70)
    
    # 1. Yesterday's Analysis
    logger.info("\n📊 Analyzing yesterday's session...")
    yesterday = analyze_yesterday(symbol)
    
    # 2. Correlation Check
    logger.info("\n🔗 Checking correlations...")
    correlations = check_correlations()
    
    # 3. News Search
    logger.info("\n📰 Searching for news...")
    gold_news = search_news("gold price XAUUSD forecast", days_back=3, limit=8)
    macro_news = search_news("Fed interest rates inflation economy", days_back=2, limit=5)
    all_news = gold_news + macro_news
    
    # 4. Economic Calendar
    logger.info("\n📅 Checking economic calendar...")
    calendar = get_economic_calendar()
    
    # 5. Scenario Analysis
    logger.info("\n🎯 Generating scenarios...")
    scenarios = generate_scenarios(yesterday, correlations, all_news)
    
    # 6. Build briefing
    briefing = {
        "timestamp": datetime.utcnow().isoformat(),
        "symbol": symbol,
        "yesterday": yesterday,
        "correlations": correlations,
        "news": all_news,
        "calendar": calendar,
        "scenarios": scenarios,
        "key_levels": {
            "yesterday_high": yesterday.get("high", 0),
            "yesterday_low": yesterday.get("low", 0),
            "yesterday_close": yesterday.get("close", 0),
            "watch_above": yesterday.get("high", 0) + 20,
            "watch_below": yesterday.get("low", 0) - 20
        }
    }
    
    # Save briefing
    briefing_file = INTEL_DIR / f"briefing_{datetime.utcnow().strftime('%Y%m%d')}.json"
    briefing_file.write_text(json.dumps(briefing, indent=2))
    logger.info(f"\n📁 Saved to {briefing_file}")
    
    return briefing


def format_telegram_briefing(briefing: Dict) -> str:
    """Format briefing for Telegram"""
    
    yesterday = briefing.get("yesterday", {})
    correlations = briefing.get("correlations", {})
    news = briefing.get("news", [])
    calendar = briefing.get("calendar", [])
    scenarios = briefing.get("scenarios", [])
    levels = briefing.get("key_levels", {})
    
    today = datetime.utcnow().strftime("%A, %B %d")
    
    lines = [
        f"🔍 <b>NEO INTEL BRIEFING - XAUUSD</b>",
        f"📅 {today}",
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"",
        f"📊 <b>YESTERDAY'S SESSION:</b>",
        f"",
        f"  {yesterday.get('character', 'N/A')}",
        f"  {yesterday.get('description', '')}",
        f"",
        f"  Open: ${yesterday.get('open', 0):.2f}",
        f"  High: ${yesterday.get('high', 0):.2f}",
        f"  Low: ${yesterday.get('low', 0):.2f}",
        f"  Close: ${yesterday.get('close', 0):.2f}",
        f"  Move: {yesterday.get('move', 0):+.0f} pts ({yesterday.get('move_pct', 0):+.1f}%)",
        f"",
        f"<b>Observations:</b>",
    ]
    
    for obs in yesterday.get("observations", []):
        lines.append(f"  {obs}")
    
    lines.extend([
        f"",
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"🔗 <b>CORRELATIONS:</b>",
        f"",
    ])
    
    for key, val in correlations.items():
        if key == "overall":
            lines.append(f"<b>Overall: {val}</b>")
        elif isinstance(val, dict):
            impact = val.get("gold_impact", "N/A")
            emoji = "🟢" if "BULLISH" in impact else "🔴" if "BEARISH" in impact else "⚪"
            lines.append(f"  {emoji} {key}: {impact}")
    
    # News section
    bullish_news = [n for n in news if n.get('sentiment') == 'BULLISH'][:3]
    bearish_news = [n for n in news if n.get('sentiment') == 'BEARISH'][:3]
    
    if bullish_news or bearish_news:
        lines.extend([
            f"",
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"📰 <b>RECENT NEWS:</b>",
            f"",
        ])
        
        if bullish_news:
            lines.append("<b>🟢 BULLISH Headlines:</b>")
            for n in bullish_news:
                impact = f"[{n.get('impact', 'LOW')}]" if n.get('impact') == 'HIGH' else ""
                lines.append(f"  • {n.get('title', '')[:55]}... {impact}")
        
        if bearish_news:
            lines.append("")
            lines.append("<b>🔴 BEARISH Headlines:</b>")
            for n in bearish_news:
                impact = f"[{n.get('impact', 'LOW')}]" if n.get('impact') == 'HIGH' else ""
                lines.append(f"  • {n.get('title', '')[:55]}... {impact}")
    
    # Calendar
    if calendar:
        lines.extend([
            f"",
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"📅 <b>TODAY'S EVENTS:</b>",
            f"",
        ])
        for event in calendar:
            impact_emoji = "🔴" if event.get('impact') == 'HIGH' else "🟡"
            lines.append(f"  {impact_emoji} {event.get('time', 'TBD')}: {event.get('event', 'N/A')}")
            lines.append(f"     → {event.get('expected_effect', '')[:50]}")
    
    # Scenarios
    lines.extend([
        f"",
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"🎯 <b>SCENARIOS:</b>",
        f"",
    ])
    
    for i, scenario in enumerate(scenarios[:3]):
        prob = scenario.get('probability', 0)
        emoji = "🥇" if i == 0 else "🥈" if i == 1 else "🥉"
        lines.append(f"{emoji} <b>{scenario.get('name', 'N/A')}</b> ({prob}%)")
        lines.append(f"   {scenario.get('description', '')[:60]}")
        lines.append(f"   <i>Action: {scenario.get('action', 'N/A')}</i>")
        lines.append(f"")
    
    # Key Levels
    lines.extend([
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"📍 <b>KEY LEVELS:</b>",
        f"",
        f"  🔼 Watch Above: ${levels.get('watch_above', 0):.0f}",
        f"  📊 Yesterday High: ${levels.get('yesterday_high', 0):.0f}",
        f"  📊 Yesterday Close: ${levels.get('yesterday_close', 0):.0f}",
        f"  📊 Yesterday Low: ${levels.get('yesterday_low', 0):.0f}",
        f"  🔽 Watch Below: ${levels.get('watch_below', 0):.0f}",
    ])
    
    return "\n".join(lines)


def send_telegram(message: str) -> bool:
    """Send to Telegram"""
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": ADMIN_CHAT_ID,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        }
        response = requests.post(url, json=payload, timeout=10)
        return response.status_code == 200
    except Exception as e:
        logger.error(f"Telegram failed: {e}")
        return False


def send_intel_briefing(symbol: str = "XAUUSD"):
    """Generate and send intel briefing"""
    briefing = generate_intel_briefing(symbol)
    message = format_telegram_briefing(briefing)
    
    success = send_telegram(message)
    logger.info(f"Telegram: {'✅ Sent' if success else '❌ Failed'}")
    
    return briefing


# ═══════════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    import re
    
    send = "--send" in sys.argv or "-s" in sys.argv
    
    briefing = generate_intel_briefing("XAUUSD")
    
    # Print formatted (remove HTML tags for console)
    message = format_telegram_briefing(briefing)
    plain = re.sub(r'<[^>]+>', '', message)
    print(plain)
    
    if send:
        print("\n📤 Sending to Telegram...")
        success = send_telegram(message)
        print("✅ Sent!" if success else "❌ Failed")
