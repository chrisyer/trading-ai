#!/usr/bin/env python3
"""
GOLD HUNTER - LOCAL OLLAMA FALLBACK
====================================
Generates XAUUSD signals using local Ollama when H100 is unavailable.
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

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [GOLD_OLLAMA] %(levelname)s: %(message)s'
)
logger = logging.getLogger("GOLD_OLLAMA")

OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "mistral:latest"
SIGNAL_FILE = "/home/jbot/trading_ai/gold_hunter/ollama_signal.json"
MT5_SIGNAL_FILE = os.path.expanduser("~/.wine/drive_c/Program Files/MetaTrader 5/MQL5/Files/gold_ollama_signal.json")
CHECK_INTERVAL = 900  # 15 minutes


def get_gold_data() -> dict:
    """Fetch Gold data and calculate indicators"""
    try:
        gold = yf.Ticker("GC=F")
        
        info = gold.info
        current_price = info.get('regularMarketPrice', 0)
        prev_close = info.get('previousClose', 0)
        
        df = gold.history(period='5d', interval='5m')
        
        if df.empty:
            return None
        
        # Filter trading hours
        df = df[df['Volume'] > 0]
        
        if len(df) < 50:
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
        
        latest = df.iloc[-1]
        
        trend = "BULLISH" if latest['Close'] > latest['sma_20'] > latest['sma_50'] else \
                "BEARISH" if latest['Close'] < latest['sma_20'] < latest['sma_50'] else "NEUTRAL"
        
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
        logger.error(f"Error fetching Gold data: {e}")
        return None


def get_ollama_signal(data: dict) -> dict:
    """Query Ollama for Gold trading signal"""
    
    prompt = f"""You are a professional Gold/XAUUSD trader. Analyze this data and give ONE trading signal.

GOLD (XAUUSD) CURRENT DATA:
- Price: ${data['price']:.2f}
- Daily Change: {data['change_pct']:.2f}%
- Trend: {data['trend']}

TECHNICAL INDICATORS:
- RSI(14): {data['rsi']:.1f} (>70=overbought, <30=oversold)
- RSI(2): {data['rsi_2']:.1f} (mean reversion)
- MACD: {data['macd']:.3f}, Signal: {data['macd_signal']:.3f}
- SMA20: ${data['sma_20']:.2f}, SMA50: ${data['sma_50']:.2f}
- BB Position: {data['bb_position']:.2f} (0=lower band, 1=upper band)
- ATR: ${data['atr']:.2f} (volatility)

GOLD-SPECIFIC RULES:
1. Gold tends to mean-revert - watch RSI extremes
2. BUY when: RSI2<10, price at BB lower, bullish divergence
3. SELL when: RSI2>90, price at BB upper, bearish divergence
4. HOLD when: conflicting signals or mid-range RSI
5. Gold is volatile - be confident in your call

Respond with ONLY: ACTION CONFIDENCE
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
        
        logger.warning(f"Could not parse Ollama response: {text}")
        return {'action': 'HOLD', 'confidence': 40}
        
    except Exception as e:
        logger.error(f"Ollama request failed: {e}")
        return None


def save_signal(signal: dict, data: dict):
    """Save signal to file for MT5"""
    
    signal_data = {
        "symbol": "XAUUSD",
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
            "bb_position": data['bb_position'],
            "atr": data['atr']
        }
    }
    
    with open(SIGNAL_FILE, 'w') as f:
        json.dump(signal_data, f, indent=2)
    
    try:
        os.makedirs(os.path.dirname(MT5_SIGNAL_FILE), exist_ok=True)
        with open(MT5_SIGNAL_FILE, 'w') as f:
            json.dump(signal_data, f, indent=2)
    except Exception as e:
        logger.debug(f"Could not write to MT5 folder: {e}")
    
    logger.info(f"Gold signal saved: {signal['action']} @ ${data['price']:.2f} ({signal['confidence']}%)")


def check_h100_status() -> bool:
    """Check if H100 Gold API is responding"""
    try:
        response = requests.get("http://146.190.188.208:8894/gold/signal", timeout=5)
        return response.status_code == 200
    except:
        return False


def main():
    """Main loop"""
    logger.info("════════════════════════════════════════════════════════════")
    logger.info("🥇 GOLD HUNTER - OLLAMA FALLBACK STARTED")
    logger.info(f"   Model: {OLLAMA_MODEL}")
    logger.info(f"   Interval: {CHECK_INTERVAL}s")
    logger.info("════════════════════════════════════════════════════════════")
    
    while True:
        try:
            h100_up = check_h100_status()
            status = "🟢 H100 Gold online (backup mode)" if h100_up else "🔴 H100 Gold down (active mode)"
            logger.info(status)
            
            data = get_gold_data()
            
            if data:
                logger.info(f"Gold: ${data['price']:.2f} | RSI2: {data['rsi_2']:.1f} | Trend: {data['trend']}")
                
                signal = get_ollama_signal(data)
                
                if signal:
                    save_signal(signal, data)
                    logger.info(f"📤 {signal['action']} Gold signal ready ({signal['confidence']}%)")
                else:
                    logger.warning("Failed to get Ollama Gold signal")
            else:
                logger.error("Failed to fetch Gold data")
            
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
