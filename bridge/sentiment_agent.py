#!/usr/bin/env python3
"""
SENTIMENT COMPRESSOR AGENT
==========================
Runs every 1-5 minutes. Compresses news/social into 3 fields.
Never sees account state. Never knows you're trading.

Output: aiiq_sentiment.json
{
  "sentiment_regime": "panic_bullish",
  "intensity": 0.82,
  "event_risk": "high"
}
"""

import json
import time
import os
import subprocess
import requests
from pathlib import Path
from datetime import datetime, timedelta

# Configuration
FILES_DIR = Path(os.environ.get("MT5_FILES_DIR", "/home/jbot/.wine/drive_c/Program Files/MetaTrader 5/MQL5/Files"))
SENTIMENT_FILE = FILES_DIR / "aiiq_sentiment.json"

OLLAMA_MODEL = os.environ.get("SENTIMENT_MODEL", "mistral:latest")
UPDATE_INTERVAL = 120  # seconds (2 minutes)

# News/data sources (add your API keys)
NEWS_API_KEY = os.environ.get("NEWS_API_KEY", "")
FINNHUB_KEY = os.environ.get("FINNHUB_KEY", "")

SYSTEM_PROMPT = """You are a market sentiment compressor for XAUUSD (gold).

Your job: Compress ALL information into exactly these fields:

- sentiment_regime: one of [neutral, mild_bullish, mild_bearish, panic_bullish, panic_bearish]
- intensity: number from 0.0 to 1.0
- event_risk: low | medium | high

Rules:
- "panic" only if narrative is widespread, urgent, or involves war/crisis
- "event_risk" is "high" if CPI, FOMC, NFP, war escalation, or surprise rates
- Gold rallies on fear, falls on risk-on
- Output ONLY valid JSON, no explanation

Examples:
- War headlines + gold surge → {"sentiment_regime":"panic_bullish","intensity":0.85,"event_risk":"high"}
- Quiet market, no news → {"sentiment_regime":"neutral","intensity":0.3,"event_risk":"low"}
- FOMC tomorrow, mild tension → {"sentiment_regime":"mild_bullish","intensity":0.55,"event_risk":"high"}"""


def safe_default():
    """Return neutral sentiment"""
    return {
        "sentiment_regime": "neutral",
        "intensity": 0.5,
        "event_risk": "low",
        "timestamp": datetime.now().isoformat(),
        "source": "default"
    }


def fetch_gold_news() -> list:
    """Fetch recent gold-related news headlines"""
    headlines = []
    
    # Method 1: Finnhub (free tier)
    if FINNHUB_KEY:
        try:
            url = f"https://finnhub.io/api/v1/news?category=general&token={FINNHUB_KEY}"
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                news = resp.json()
                gold_keywords = ['gold', 'xauusd', 'precious', 'fed', 'fomc', 'inflation', 
                                'rates', 'dollar', 'war', 'crisis', 'treasury']
                for item in news[:20]:
                    headline = item.get('headline', '').lower()
                    if any(kw in headline for kw in gold_keywords):
                        headlines.append(item.get('headline', ''))
        except Exception as e:
            print(f"Finnhub error: {e}")
    
    # Method 2: NewsAPI (if available)
    if NEWS_API_KEY and len(headlines) < 5:
        try:
            url = f"https://newsapi.org/v2/everything?q=gold+OR+XAUUSD+OR+federal+reserve&sortBy=publishedAt&apiKey={NEWS_API_KEY}"
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                articles = resp.json().get('articles', [])
                for art in articles[:10]:
                    headlines.append(art.get('title', ''))
        except Exception as e:
            print(f"NewsAPI error: {e}")
    
    # Method 3: Fallback - use market data as proxy
    if not headlines:
        try:
            import yfinance as yf
            gold = yf.Ticker("GC=F")
            info = gold.info
            
            price = info.get('regularMarketPrice', 0)
            prev = info.get('previousClose', 0)
            change_pct = ((price - prev) / prev * 100) if prev else 0
            
            # Derive pseudo-headlines from price action
            if abs(change_pct) > 2:
                direction = "surges" if change_pct > 0 else "plunges"
                headlines.append(f"Gold {direction} {abs(change_pct):.1f}% in volatile trading")
            if abs(change_pct) > 3:
                headlines.append("Precious metals see extreme volatility")
            if change_pct > 1.5:
                headlines.append("Safe haven demand lifts gold prices")
            elif change_pct < -1.5:
                headlines.append("Risk-on sentiment weighs on gold")
        except:
            pass
    
    return headlines[:10]


def fetch_economic_calendar() -> list:
    """Check for high-impact events"""
    events = []
    
    # Simplified: Check for known high-impact events
    # In production, use an economic calendar API
    now = datetime.now()
    day = now.strftime("%A")
    
    # FOMC typically Wed, NFP first Friday, CPI mid-month
    if day == "Wednesday":
        events.append("Potential FOMC day - monitor for rate decision")
    if day == "Friday" and now.day <= 7:
        events.append("Potential NFP release day")
    if 10 <= now.day <= 15:
        events.append("CPI release window")
    
    return events


def ollama_compress(headlines: list, events: list) -> dict:
    """Query Ollama to compress sentiment"""
    
    input_data = {
        "headlines": headlines if headlines else ["No significant gold news"],
        "economic_events": events if events else ["No major events scheduled"],
        "timestamp": datetime.now().isoformat()
    }
    
    prompt = f"""{SYSTEM_PROMPT}

INPUT DATA:
{json.dumps(input_data, indent=2)}

Compress to sentiment JSON now:"""

    try:
        result = subprocess.run(
            ["ollama", "run", OLLAMA_MODEL],
            input=prompt.encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30
        )
        
        if result.returncode != 0:
            print(f"Ollama error: {result.stderr.decode()[:100]}")
            return {}
        
        response = result.stdout.decode("utf-8", errors="ignore").strip()
        
        # Extract JSON
        start = response.find("{")
        end = response.rfind("}")
        if start >= 0 and end > start:
            return json.loads(response[start:end+1])
        
    except subprocess.TimeoutExpired:
        print("Ollama timeout")
    except json.JSONDecodeError as e:
        print(f"JSON parse error: {e}")
    except Exception as e:
        print(f"Error: {e}")
    
    return {}


def validate_sentiment(raw: dict) -> dict:
    """Validate and clamp sentiment values"""
    out = safe_default()
    
    # Validate sentiment_regime
    valid_regimes = ["neutral", "mild_bullish", "mild_bearish", "panic_bullish", "panic_bearish"]
    regime = str(raw.get("sentiment_regime", "neutral")).lower().replace(" ", "_")
    if regime in valid_regimes:
        out["sentiment_regime"] = regime
    
    # Validate intensity (0-1)
    try:
        intensity = float(raw.get("intensity", 0.5))
        out["intensity"] = max(0.0, min(1.0, intensity))
    except:
        pass
    
    # Validate event_risk
    risk = str(raw.get("event_risk", "low")).lower()
    if risk in ["low", "medium", "high"]:
        out["event_risk"] = risk
    
    out["timestamp"] = datetime.now().isoformat()
    out["source"] = "ollama"
    
    return out


def main():
    """Main sentiment loop"""
    print("="*60)
    print("📰 SENTIMENT COMPRESSOR AGENT")
    print("="*60)
    print(f"Model: {OLLAMA_MODEL}")
    print(f"Output: {SENTIMENT_FILE}")
    print(f"Interval: {UPDATE_INTERVAL}s")
    print("="*60)
    
    # Ensure directory exists
    FILES_DIR.mkdir(parents=True, exist_ok=True)
    
    # Write initial neutral
    SENTIMENT_FILE.write_text(json.dumps(safe_default(), indent=2))
    print("Initial sentiment: neutral")
    
    while True:
        try:
            print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Fetching sentiment...")
            
            # Gather inputs
            headlines = fetch_gold_news()
            events = fetch_economic_calendar()
            
            print(f"  Headlines: {len(headlines)}")
            print(f"  Events: {len(events)}")
            
            # Compress with Ollama
            raw = ollama_compress(headlines, events)
            
            if raw:
                sentiment = validate_sentiment(raw)
            else:
                sentiment = safe_default()
            
            # Write output
            SENTIMENT_FILE.write_text(json.dumps(sentiment, indent=2))
            
            print(f"  → {sentiment['sentiment_regime']} ({sentiment['intensity']:.2f}) risk={sentiment['event_risk']}")
            
        except KeyboardInterrupt:
            print("\nStopped")
            break
        except Exception as e:
            print(f"Error: {e}")
            # Write safe default on error
            SENTIMENT_FILE.write_text(json.dumps(safe_default(), indent=2))
        
        time.sleep(UPDATE_INTERVAL)


if __name__ == "__main__":
    main()
