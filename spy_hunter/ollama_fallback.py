#!/usr/bin/env python3
"""
SPY HUNTER - LOCAL OLLAMA FALLBACK
===================================
Generates SPY signals using local Ollama when H100 is unavailable.
Runs every 15 minutes to keep fallback signals fresh.
"""

import json
import os
import time
import logging
from datetime import datetime
import yfinance as yf
import pandas as pd
import ta
import requests

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [OLLAMA_FALLBACK] %(levelname)s: %(message)s'
)
logger = logging.getLogger("OLLAMA_FALLBACK")

# Configuration
OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "mistral:latest"
SIGNAL_FILE = "/home/jbot/trading_ai/spy_hunter/ollama_signal.json"
MT5_SIGNAL_FILE = os.path.expanduser("~/.wine/drive_c/Program Files/MetaTrader 5/MQL5/Files/spy_ollama_signal.json")
CHECK_INTERVAL = 900  # 15 minutes


def get_spy_data() -> dict:
    """Fetch SPY data and calculate indicators"""
    try:
        spy = yf.Ticker("SPY")
        
        # Get current price from info
        info = spy.info
        current_price = info.get('regularMarketPrice', 0)
        prev_close = info.get('previousClose', 0)
        
        # Get historical data for indicators
        df = spy.history(period='5d', interval='5m')
        
        if df.empty:
            return None
        
        # Calculate indicators
        df['rsi'] = ta.momentum.rsi(df['Close'], window=14)
        df['rsi_2'] = ta.momentum.rsi(df['Close'], window=2)
        df['sma_20'] = ta.trend.sma_indicator(df['Close'], window=20)
        df['sma_50'] = ta.trend.sma_indicator(df['Close'], window=50)
        
        macd = ta.trend.MACD(df['Close'])
        df['macd'] = macd.macd()
        df['macd_signal'] = macd.macd_signal()
        
        bb = ta.volatility.BollingerBands(df['Close'])
        df['bb_upper'] = bb.bollinger_hband()
        df['bb_lower'] = bb.bollinger_lband()
        
        df['atr'] = ta.volatility.average_true_range(df['High'], df['Low'], df['Close'])
        
        # Get latest values
        latest = df.iloc[-1]
        
        # Determine trend
        trend = "BULLISH" if latest['Close'] > latest['sma_20'] > latest['sma_50'] else \
                "BEARISH" if latest['Close'] < latest['sma_20'] < latest['sma_50'] else "NEUTRAL"
        
        # BB position
        bb_pos = (latest['Close'] - latest['bb_lower']) / (latest['bb_upper'] - latest['bb_lower'])
        
        return {
            'price': current_price or float(latest['Close']),
            'prev_close': prev_close,
            'change_pct': ((current_price or latest['Close']) - prev_close) / prev_close * 100 if prev_close else 0,
            'rsi': float(latest['rsi']),
            'rsi_2': float(latest['rsi_2']),
            'macd': float(latest['macd']),
            'macd_signal': float(latest['macd_signal']),
            'sma_20': float(latest['sma_20']),
            'sma_50': float(latest['sma_50']),
            'bb_upper': float(latest['bb_upper']),
            'bb_lower': float(latest['bb_lower']),
            'bb_position': float(bb_pos),
            'atr': float(latest['atr']),
            'trend': trend,
            'volume': int(latest['Volume'])
        }
        
    except Exception as e:
        logger.error(f"Error fetching SPY data: {e}")
        return None


def get_ollama_signal(data: dict) -> dict:
    """Query Ollama for trading signal"""
    
    prompt = f"""You are a professional SPY day trader. Analyze this data and give ONE trading signal.

SPY CURRENT DATA:
- Price: ${data['price']:.2f}
- Daily Change: {data['change_pct']:.2f}%
- Trend: {data['trend']}

TECHNICAL INDICATORS:
- RSI(14): {data['rsi']:.1f} (>70=overbought, <30=oversold)
- RSI(2): {data['rsi_2']:.1f} (mean reversion signal)
- MACD: {data['macd']:.3f}, Signal: {data['macd_signal']:.3f}
- SMA20: ${data['sma_20']:.2f}, SMA50: ${data['sma_50']:.2f}
- BB Position: {data['bb_position']:.2f} (0=lower, 0.5=middle, 1=upper)
- ATR: {data['atr']:.2f}

RULES:
1. BUY when: RSI2<10 (oversold), price at BB lower, bullish MACD cross
2. SELL when: RSI2>90 (overbought), price at BB upper, bearish MACD cross
3. HOLD when: no clear setup or conflicting signals

Respond with ONLY this format (no explanation):
ACTION CONFIDENCE
Example: BUY 75 or SELL 80 or HOLD 50"""

    try:
        response = requests.post(
            OLLAMA_URL,
            json={
                "model": OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": 0.3,
                    "num_predict": 20
                }
            },
            timeout=30
        )
        
        if response.status_code != 200:
            logger.error(f"Ollama error: {response.status_code}")
            return None
        
        result = response.json()
        text = result.get('response', '').strip().upper()
        
        # Parse response
        parts = text.split()
        if len(parts) >= 2:
            action = parts[0]
            try:
                confidence = int(parts[1].replace('%', ''))
            except:
                confidence = 50
            
            if action in ['BUY', 'SELL', 'HOLD']:
                return {
                    'action': action,
                    'confidence': min(95, max(30, confidence))
                }
        
        # Default to HOLD if can't parse
        logger.warning(f"Could not parse Ollama response: {text}")
        return {'action': 'HOLD', 'confidence': 40}
        
    except Exception as e:
        logger.error(f"Ollama request failed: {e}")
        return None


def save_signal(signal: dict, data: dict):
    """Save signal to file for MT5"""
    
    signal_data = {
        "symbol": "SPY",
        "action": signal['action'],
        "price": data['price'],
        "confidence": signal['confidence'],
        "timestamp": datetime.now().isoformat(),
        "source": "OLLAMA_LOCAL",
        "model": OLLAMA_MODEL,
        "indicators": {
            "rsi": data['rsi'],
            "rsi_2": data['rsi_2'],
            "trend": data['trend'],
            "bb_position": data['bb_position']
        }
    }
    
    # Save to main location
    with open(SIGNAL_FILE, 'w') as f:
        json.dump(signal_data, f, indent=2)
    
    # Try to save to MT5 Files folder
    try:
        os.makedirs(os.path.dirname(MT5_SIGNAL_FILE), exist_ok=True)
        with open(MT5_SIGNAL_FILE, 'w') as f:
            json.dump(signal_data, f, indent=2)
    except Exception as e:
        logger.debug(f"Could not write to MT5 folder: {e}")
    
    logger.info(f"Signal saved: {signal['action']} @ ${data['price']:.2f} ({signal['confidence']}%)")


def check_h100_status() -> bool:
    """Check if H100 API is responding"""
    try:
        response = requests.get("http://146.190.188.208:8893/spy/signal", timeout=5)
        return response.status_code == 200
    except:
        return False


def main():
    """Main loop"""
    logger.info("════════════════════════════════════════════════════════════")
    logger.info("🦙 SPY HUNTER - OLLAMA FALLBACK STARTED")
    logger.info(f"   Model: {OLLAMA_MODEL}")
    logger.info(f"   Interval: {CHECK_INTERVAL}s")
    logger.info("════════════════════════════════════════════════════════════")
    
    while True:
        try:
            # Check if H100 is up (if so, we're just backup)
            h100_up = check_h100_status()
            status = "🟢 H100 online (backup mode)" if h100_up else "🔴 H100 down (active mode)"
            logger.info(status)
            
            # Always generate signal (to have fresh backup ready)
            data = get_spy_data()
            
            if data:
                logger.info(f"SPY: ${data['price']:.2f} | RSI2: {data['rsi_2']:.1f} | Trend: {data['trend']}")
                
                signal = get_ollama_signal(data)
                
                if signal:
                    save_signal(signal, data)
                    logger.info(f"📤 {signal['action']} signal ready (confidence: {signal['confidence']}%)")
                else:
                    logger.warning("Failed to get Ollama signal")
            else:
                logger.error("Failed to fetch SPY data")
            
            logger.info(f"Next check in {CHECK_INTERVAL}s...")
            time.sleep(CHECK_INTERVAL)
            
        except KeyboardInterrupt:
            logger.info("Stopped by user")
            break
        except Exception as e:
            logger.error(f"Error: {e}")
            time.sleep(60)


if __name__ == "__main__":
    main()
