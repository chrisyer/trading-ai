#!/usr/bin/env python3
"""
═══════════════════════════════════════════════════════════════════════════════
NEO SESSION & TIME ANALYSIS
═══════════════════════════════════════════════════════════════════════════════

Analyzes which sessions and hours are most bullish/bearish.
Helps determine:
- When to SCALP (high probability moves)
- When to RISK OFF (corrections/reversals)
- Session-specific strategies

═══════════════════════════════════════════════════════════════════════════════
"""

import json
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Tuple
from pathlib import Path
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("SESSION_ANALYSIS")

DATA_DIR = Path("/home/jbot/trading_ai/data/neo")
ANALYSIS_DIR = DATA_DIR / "session_analysis"
ANALYSIS_DIR.mkdir(exist_ok=True)


# ═══════════════════════════════════════════════════════════════════════════════
# SESSION DEFINITIONS (UTC)
# ═══════════════════════════════════════════════════════════════════════════════

SESSIONS = {
    "ASIAN": {"start": 0, "end": 8, "name": "Asian Session (00:00-08:00 UTC)"},
    "LONDON": {"start": 8, "end": 16, "name": "London Session (08:00-16:00 UTC)"},
    "NEW_YORK": {"start": 13, "end": 21, "name": "New York Session (13:00-21:00 UTC)"},
    "OVERLAP": {"start": 13, "end": 16, "name": "London/NY Overlap (13:00-16:00 UTC)"},
    "LATE_NY": {"start": 17, "end": 21, "name": "Late NY (17:00-21:00 UTC)"},
}

# Key hours to analyze
# Key hours with both UTC and EST (EST = UTC - 5)
KEY_HOURS = {
    0: "Asian Open (19:00 EST)",
    3: "Asian Mid (22:00 EST)",
    7: "Pre-London (02:00 EST)",
    8: "London Open (03:00 EST)",
    9: "London Early (04:00 EST)",
    11: "London Mid (06:00 EST)",
    12: "London Mid/Pre-US (07:00 EST)",
    13: "NY Pre-Market (08:00 EST)",
    14: "NY Open (09:00 EST)",
    15: "NY Early (10:00 EST)",
    16: "London Close (11:00 EST)",
    17: "NY Afternoon (12:00 EST)",
    18: "NY Mid (13:00 EST)",
    20: "NY Late (15:00 EST)",
    21: "NY Close (16:00 EST)",
    23: "Pre-Asian (18:00 EST)"
}


# ═══════════════════════════════════════════════════════════════════════════════
# DATA FETCHING
# ═══════════════════════════════════════════════════════════════════════════════

def fetch_historical_data(symbol: str = "XAUUSD", days: int = 60) -> pd.DataFrame:
    """Fetch hourly data for analysis"""
    try:
        import yfinance as yf
        
        ticker_map = {"XAUUSD": "GC=F", "EURUSD": "EURUSD=X"}
        ticker = ticker_map.get(symbol, symbol)
        
        # Fetch data
        df = yf.Ticker(ticker).history(period=f"{days}d", interval="1h")
        
        if df.empty:
            logger.error("No data fetched")
            return pd.DataFrame()
        
        # Standardize columns
        df.columns = [c.lower() for c in df.columns]
        
        # Add time features
        df['hour'] = df.index.hour
        df['day_of_week'] = df.index.dayofweek  # 0=Monday
        df['date'] = df.index.date
        
        # Calculate hourly returns
        df['return'] = df['close'].pct_change() * 100
        df['move_pts'] = df['close'] - df['open']
        df['range'] = df['high'] - df['low']
        
        # Determine direction
        df['direction'] = np.where(df['close'] > df['open'], 'UP', 
                                   np.where(df['close'] < df['open'], 'DOWN', 'FLAT'))
        
        return df.dropna()
        
    except Exception as e:
        logger.error(f"Data fetch failed: {e}")
        return pd.DataFrame()


# ═══════════════════════════════════════════════════════════════════════════════
# HOURLY ANALYSIS
# ═══════════════════════════════════════════════════════════════════════════════

def analyze_hourly_patterns(df: pd.DataFrame) -> Dict:
    """Analyze performance by hour"""
    
    hourly_stats = {}
    
    for hour in range(24):
        hour_data = df[df['hour'] == hour]
        
        if len(hour_data) < 10:
            continue
        
        up_candles = len(hour_data[hour_data['direction'] == 'UP'])
        down_candles = len(hour_data[hour_data['direction'] == 'DOWN'])
        total = len(hour_data)
        
        avg_move = hour_data['move_pts'].mean()
        avg_range = hour_data['range'].mean()
        avg_return = hour_data['return'].mean()
        
        # Win rate for bulls (what % of candles close up)
        bullish_rate = (up_candles / total) * 100
        
        # Volatility (average range)
        volatility = avg_range
        
        # Determine hour character
        if bullish_rate >= 60:
            character = "BULLISH"
            recommendation = "SCALP LONG"
        elif bullish_rate <= 40:
            character = "BEARISH"
            recommendation = "CAUTION / RISK OFF"
        else:
            character = "NEUTRAL"
            recommendation = "WAIT FOR SETUP"
        
        # High volatility hours
        if avg_range > df['range'].mean() * 1.3:
            volatility_level = "HIGH"
        elif avg_range < df['range'].mean() * 0.7:
            volatility_level = "LOW"
        else:
            volatility_level = "NORMAL"
        
        hourly_stats[hour] = {
            "hour_utc": hour,
            "hour_label": KEY_HOURS.get(hour, f"{hour}:00 UTC"),
            "total_candles": total,
            "up_candles": up_candles,
            "down_candles": down_candles,
            "bullish_rate": round(bullish_rate, 1),
            "avg_move_pts": round(avg_move, 2),
            "avg_range": round(avg_range, 2),
            "avg_return_pct": round(avg_return, 3),
            "character": character,
            "volatility": volatility_level,
            "recommendation": recommendation
        }
    
    return hourly_stats


def analyze_session_patterns(df: pd.DataFrame) -> Dict:
    """Analyze performance by trading session"""
    
    session_stats = {}
    
    for session_name, session_def in SESSIONS.items():
        start_hour = session_def["start"]
        end_hour = session_def["end"]
        
        # Handle sessions that span midnight
        if start_hour < end_hour:
            session_data = df[(df['hour'] >= start_hour) & (df['hour'] < end_hour)]
        else:
            session_data = df[(df['hour'] >= start_hour) | (df['hour'] < end_hour)]
        
        if len(session_data) < 20:
            continue
        
        up_candles = len(session_data[session_data['direction'] == 'UP'])
        down_candles = len(session_data[session_data['direction'] == 'DOWN'])
        total = len(session_data)
        
        bullish_rate = (up_candles / total) * 100
        avg_move = session_data['move_pts'].mean()
        avg_range = session_data['range'].mean()
        
        # Calculate session total move (start to end)
        # Group by date and get session performance
        session_moves = []
        for date in session_data['date'].unique():
            day_session = session_data[session_data['date'] == date]
            if len(day_session) > 0:
                session_open = day_session['open'].iloc[0]
                session_close = day_session['close'].iloc[-1]
                session_move = session_close - session_open
                session_moves.append(session_move)
        
        avg_session_move = np.mean(session_moves) if session_moves else 0
        session_win_rate = (sum(1 for m in session_moves if m > 0) / len(session_moves) * 100) if session_moves else 50
        
        # Character
        if bullish_rate >= 55:
            character = "BULLISH"
        elif bullish_rate <= 45:
            character = "BEARISH"
        else:
            character = "NEUTRAL"
        
        session_stats[session_name] = {
            "name": session_def["name"],
            "hours": f"{start_hour}:00-{end_hour}:00 UTC",
            "total_candles": total,
            "bullish_rate": round(bullish_rate, 1),
            "avg_hourly_move": round(avg_move, 2),
            "avg_hourly_range": round(avg_range, 2),
            "avg_session_move": round(avg_session_move, 2),
            "session_win_rate": round(session_win_rate, 1),
            "character": character
        }
    
    return session_stats


def analyze_day_of_week(df: pd.DataFrame) -> Dict:
    """Analyze performance by day of week"""
    
    day_names = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
    day_stats = {}
    
    for day in range(7):
        day_data = df[df['day_of_week'] == day]
        
        if len(day_data) < 20:
            continue
        
        up_candles = len(day_data[day_data['direction'] == 'UP'])
        total = len(day_data)
        
        bullish_rate = (up_candles / total) * 100
        avg_move = day_data['move_pts'].mean()
        avg_range = day_data['range'].mean()
        
        # Get daily total moves
        daily_moves = []
        for date in day_data['date'].unique():
            date_data = df[df['date'] == date]
            if len(date_data) > 0:
                daily_move = date_data['close'].iloc[-1] - date_data['open'].iloc[0]
                daily_moves.append(daily_move)
        
        avg_daily_move = np.mean(daily_moves) if daily_moves else 0
        daily_win_rate = (sum(1 for m in daily_moves if m > 0) / len(daily_moves) * 100) if daily_moves else 50
        
        day_stats[day_names[day]] = {
            "day": day_names[day],
            "bullish_rate": round(bullish_rate, 1),
            "avg_hourly_move": round(avg_move, 2),
            "avg_range": round(avg_range, 2),
            "avg_daily_move": round(avg_daily_move, 2),
            "daily_win_rate": round(daily_win_rate, 1)
        }
    
    return day_stats


# ═══════════════════════════════════════════════════════════════════════════════
# RECOMMENDATIONS
# ═══════════════════════════════════════════════════════════════════════════════

def generate_trading_windows(hourly_stats: Dict, session_stats: Dict) -> Dict:
    """Generate trading window recommendations"""
    
    # Find best hours for scalping longs
    best_long_hours = []
    for hour, stats in sorted(hourly_stats.items()):
        if stats['bullish_rate'] >= 58 and stats['volatility'] != 'LOW':
            best_long_hours.append({
                "hour": hour,
                "label": stats['hour_label'],
                "bullish_rate": stats['bullish_rate'],
                "avg_move": stats['avg_move_pts']
            })
    
    # Find hours to avoid (corrections)
    risk_off_hours = []
    for hour, stats in sorted(hourly_stats.items()):
        if stats['bullish_rate'] <= 42 or stats['volatility'] == 'LOW':
            risk_off_hours.append({
                "hour": hour,
                "label": stats['hour_label'],
                "bullish_rate": stats['bullish_rate'],
                "reason": "Correction likely" if stats['bullish_rate'] <= 42 else "Low volatility"
            })
    
    # Find best sessions
    best_sessions = []
    for session, stats in session_stats.items():
        if stats['bullish_rate'] >= 53 and stats['session_win_rate'] >= 55:
            best_sessions.append({
                "session": session,
                "name": stats['name'],
                "bullish_rate": stats['bullish_rate'],
                "session_win_rate": stats['session_win_rate']
            })
    
    return {
        "best_long_hours": sorted(best_long_hours, key=lambda x: x['bullish_rate'], reverse=True)[:5],
        "risk_off_hours": sorted(risk_off_hours, key=lambda x: x['bullish_rate'])[:5],
        "best_sessions": best_sessions
    }


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN ANALYSIS
# ═══════════════════════════════════════════════════════════════════════════════

def run_full_analysis(symbol: str = "XAUUSD", days: int = 60) -> Dict:
    """Run complete session analysis"""
    
    logger.info("="*70)
    logger.info(f"📊 SESSION ANALYSIS - {symbol} ({days} days)")
    logger.info("="*70)
    
    # Fetch data
    logger.info("\n📥 Fetching historical data...")
    df = fetch_historical_data(symbol, days)
    
    if df.empty:
        return {"error": "No data available"}
    
    logger.info(f"   Got {len(df)} hourly candles")
    
    # Analysis
    logger.info("\n⏰ Analyzing hourly patterns...")
    hourly = analyze_hourly_patterns(df)
    
    logger.info("\n🌍 Analyzing session patterns...")
    sessions = analyze_session_patterns(df)
    
    logger.info("\n📅 Analyzing day of week patterns...")
    days_analysis = analyze_day_of_week(df)
    
    logger.info("\n🎯 Generating recommendations...")
    recommendations = generate_trading_windows(hourly, sessions)
    
    # Build result
    result = {
        "timestamp": datetime.utcnow().isoformat(),
        "symbol": symbol,
        "period_days": days,
        "total_candles": len(df),
        "hourly_patterns": hourly,
        "session_patterns": sessions,
        "day_of_week": days_analysis,
        "recommendations": recommendations
    }
    
    # Save
    save_path = ANALYSIS_DIR / f"session_analysis_{symbol}_{datetime.now().strftime('%Y%m%d')}.json"
    save_path.write_text(json.dumps(result, indent=2))
    logger.info(f"\n📁 Saved to {save_path}")
    
    return result


def format_report(analysis: Dict) -> str:
    """Format analysis for display"""
    
    hourly = analysis.get("hourly_patterns", {})
    sessions = analysis.get("session_patterns", {})
    days = analysis.get("day_of_week", {})
    recs = analysis.get("recommendations", {})
    
    lines = [
        f"═══════════════════════════════════════════════════════════════════════════════",
        f"📊 SESSION & TIME ANALYSIS - {analysis.get('symbol', 'XAUUSD')}",
        f"   Period: Last {analysis.get('period_days', 60)} days ({analysis.get('total_candles', 0)} candles)",
        f"═══════════════════════════════════════════════════════════════════════════════",
        f"",
        f"🎯 SCALP LONG - BEST HOURS (highest bullish rate):",
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
    ]
    
    for h in recs.get("best_long_hours", [])[:5]:
        lines.append(f"   🟢 {h['label']:20s} | Bullish: {h['bullish_rate']:5.1f}% | Avg Move: {h['avg_move']:+.2f} pts")
    
    lines.extend([
        f"",
        f"⚠️ RISK OFF - CORRECTION HOURS (low bullish rate or low volatility):",
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
    ])
    
    for h in recs.get("risk_off_hours", [])[:5]:
        lines.append(f"   🔴 {h['label']:20s} | Bullish: {h['bullish_rate']:5.1f}% | {h['reason']}")
    
    lines.extend([
        f"",
        f"🌍 SESSION BREAKDOWN:",
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
    ])
    
    for session_name, stats in sessions.items():
        char_emoji = "🟢" if stats['character'] == "BULLISH" else "🔴" if stats['character'] == "BEARISH" else "⚪"
        lines.append(f"   {char_emoji} {stats['name']:35s}")
        lines.append(f"      Bullish Rate: {stats['bullish_rate']:.1f}% | Session Win: {stats['session_win_rate']:.1f}% | Avg Move: {stats['avg_session_move']:+.2f} pts")
        lines.append(f"")
    
    lines.extend([
        f"📅 DAY OF WEEK:",
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
    ])
    
    for day_name, stats in days.items():
        char_emoji = "🟢" if stats['bullish_rate'] >= 55 else "🔴" if stats['bullish_rate'] <= 45 else "⚪"
        lines.append(f"   {char_emoji} {day_name:12s} | Bullish: {stats['bullish_rate']:5.1f}% | Daily Win: {stats['daily_win_rate']:5.1f}% | Avg Daily: {stats['avg_daily_move']:+.2f} pts")
    
    lines.extend([
        f"",
        f"═══════════════════════════════════════════════════════════════════════════════",
        f"📋 HOURLY BREAKDOWN (UTC):",
        f"═══════════════════════════════════════════════════════════════════════════════",
        f"",
        f"Hour  | Bullish% | Avg Move | Range   | Character   | Recommendation",
        f"─────────────────────────────────────────────────────────────────────────────",
    ])
    
    for hour in sorted(hourly.keys()):
        stats = hourly[hour]
        char_color = "🟢" if stats['character'] == "BULLISH" else "🔴" if stats['character'] == "BEARISH" else "⚪"
        lines.append(f"{hour:02d}:00 | {stats['bullish_rate']:6.1f}%  | {stats['avg_move_pts']:+7.2f} | ${stats['avg_range']:6.2f} | {char_color} {stats['character']:10s} | {stats['recommendation']}")
    
    return "\n".join(lines)


def format_telegram_summary(analysis: Dict) -> str:
    """Format summary for Telegram"""
    
    recs = analysis.get("recommendations", {})
    sessions = analysis.get("session_patterns", {})
    
    lines = [
        f"📊 <b>SESSION ANALYSIS - XAUUSD</b>",
        f"📅 Last {analysis.get('period_days', 60)} days",
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"",
        f"🎯 <b>BEST HOURS TO SCALP LONG:</b>",
    ]
    
    for h in recs.get("best_long_hours", [])[:4]:
        lines.append(f"  🟢 {h['label']}: {h['bullish_rate']:.0f}% bullish")
    
    lines.extend([
        f"",
        f"⚠️ <b>RISK OFF HOURS:</b>",
    ])
    
    for h in recs.get("risk_off_hours", [])[:4]:
        lines.append(f"  🔴 {h['label']}: {h['bullish_rate']:.0f}% bullish")
    
    lines.extend([
        f"",
        f"🌍 <b>SESSION CHARACTER:</b>",
    ])
    
    for session_name, stats in sessions.items():
        char = stats.get('character', 'NEUTRAL')
        emoji = "🟢" if char == "BULLISH" else "🔴" if char == "BEARISH" else "⚪"
        lines.append(f"  {emoji} {session_name}: {stats['bullish_rate']:.0f}% bullish, {stats['session_win_rate']:.0f}% win")
    
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    
    days = 60
    symbol = "XAUUSD"
    
    for arg in sys.argv[1:]:
        if arg.isdigit():
            days = int(arg)
        elif not arg.startswith("-"):
            symbol = arg.upper()
    
    analysis = run_full_analysis(symbol, days)
    
    if "error" not in analysis:
        print(format_report(analysis))
        
        if "--send" in sys.argv or "-s" in sys.argv:
            import requests
            TELEGRAM_BOT_TOKEN = '8250652030:AAFd4x8NsTfdaB3O67lUnMhotT2XY61600s'
            ADMIN_CHAT_ID = '6776619257'
            
            msg = format_telegram_summary(analysis)
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            payload = {"chat_id": ADMIN_CHAT_ID, "text": msg, "parse_mode": "HTML"}
            resp = requests.post(url, json=payload, timeout=10)
            print(f"\n📤 Telegram: {'✅ Sent' if resp.ok else '❌ Failed'}")
