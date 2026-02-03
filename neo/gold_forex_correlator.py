"""
Gold-Forex Correlation Trading Module
Generates forex signals based on Gold (XAUUSD) price action

When Gold moves, correlated forex pairs follow:
- AUD/USD: +0.80 correlation (Australia = #2 gold producer)
- NZD/USD: +0.65 correlation (follows AUD)
- EUR/USD: +0.50 correlation (anti-USD)
- USD/CHF: -0.70 correlation (CHF = safe haven)
- USD/JPY: -0.60 correlation (JPY = safe haven, risk-off)
- USD/CAD: -0.40 correlation (commodity currency)
"""

import yfinance as yf
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import json
from pathlib import Path

# Data directory
DATA_DIR = Path(__file__).parent.parent / "neo_gold"
DATA_DIR.mkdir(exist_ok=True)


class GoldForexCorrelator:
    """
    Generate forex signals based on Gold price action
    """
    
    def __init__(self):
        # Correlation matrix - empirical correlations with XAUUSD
        self.correlations = {
            'AUDUSD': {
                'correlation': 0.80,
                'direction': 'same',
                'strength': 5,
                'reason': 'Australia = #2 gold producer, commodity currency',
                'yf_symbol': 'AUDUSD=X'
            },
            'NZDUSD': {
                'correlation': 0.65,
                'direction': 'same',
                'strength': 4,
                'reason': 'Follows AUD, commodity currency',
                'yf_symbol': 'NZDUSD=X'
            },
            'EURUSD': {
                'correlation': 0.50,
                'direction': 'same',
                'strength': 3,
                'reason': 'Anti-USD correlation, risk sentiment',
                'yf_symbol': 'EURUSD=X'
            },
            'USDCHF': {
                'correlation': -0.70,
                'direction': 'inverse',
                'strength': 4,
                'reason': 'CHF = safe haven, moves with Gold',
                'yf_symbol': 'USDCHF=X'
            },
            'USDJPY': {
                'correlation': -0.60,
                'direction': 'inverse',
                'strength': 4,
                'reason': 'JPY = safe haven, risk-off correlation',
                'yf_symbol': 'USDJPY=X'
            },
            'USDCAD': {
                'correlation': -0.40,
                'direction': 'inverse',
                'strength': 3,
                'reason': 'CAD = commodity currency, oil correlation',
                'yf_symbol': 'USDCAD=X'
            },
        }
        
        # Thresholds
        self.gold_volatility_threshold = 0.3  # % move to trigger signals
        self.signal_confidence_threshold = 50  # Minimum confidence to signal
        
        # Key levels for Gold
        self.gold_key_levels = [4800, 4850, 4900, 4950, 5000, 5050, 5100]
        
    def get_gold_data(self, period: str = '5d') -> Dict:
        """Fetch current Gold (XAUUSD) data"""
        try:
            gold = yf.Ticker("GC=F")  # Gold futures
            
            # CRITICAL: Use info['regularMarketPrice'] for CURRENT price
            # history() can return stale data, especially over weekends/holidays
            info = gold.info
            current_price = info.get('regularMarketPrice', 0)
            
            if current_price == 0:
                # Fallback to fast_info
                current_price = gold.fast_info.last_price if hasattr(gold.fast_info, 'last_price') else 0
            
            # Get historical data for calculations (may be stale but OK for change calcs)
            hist = gold.history(period=period, interval='1h')
            
            if hist.empty:
                # Try alternative symbol
                gold = yf.Ticker("XAUUSD=X")
                hist = gold.history(period=period, interval='1h')
                if current_price == 0:
                    current_price = gold.info.get('regularMarketPrice', 0)
            
            if hist.empty and current_price == 0:
                return self._get_cached_gold_data()
            
            # Calculate changes
            if len(hist) >= 2:
                change_1h = ((current_price / hist['Close'].iloc[-2]) - 1) * 100
            else:
                change_1h = 0
                
            if len(hist) >= 5:
                change_4h = ((current_price / hist['Close'].iloc[-5]) - 1) * 100
            else:
                change_4h = change_1h
                
            if len(hist) >= 24:
                change_24h = ((current_price / hist['Close'].iloc[-24]) - 1) * 100
            else:
                change_24h = change_4h
            
            # Calculate RSI
            closes = hist['Close'].values
            if len(closes) >= 14:
                deltas = np.diff(closes)
                gains = np.where(deltas > 0, deltas, 0)
                losses = np.where(deltas < 0, -deltas, 0)
                avg_gain = np.mean(gains[-14:])
                avg_loss = np.mean(losses[-14:])
                rs = avg_gain / avg_loss if avg_loss > 0 else 100
                rsi = 100 - (100 / (1 + rs))
            else:
                rsi = 50
            
            # Calculate ATR
            if len(hist) >= 14:
                high = hist['High'].values
                low = hist['Low'].values
                close = hist['Close'].values
                tr = np.maximum(high[1:] - low[1:], 
                               np.maximum(abs(high[1:] - close[:-1]),
                                         abs(low[1:] - close[:-1])))
                atr = np.mean(tr[-14:])
            else:
                atr = 20  # Default
            
            # Determine volatility level
            atr_pct = (atr / current_price) * 100
            if atr_pct > 1.5:
                volatility = 'EXTREME'
            elif atr_pct > 1.0:
                volatility = 'HIGH'
            elif atr_pct > 0.5:
                volatility = 'MEDIUM'
            else:
                volatility = 'LOW'
            
            # Find nearest key level
            nearest_level = min(self.gold_key_levels, key=lambda x: abs(x - current_price))
            distance_to_level = abs(current_price - nearest_level)
            near_key_level = bool(distance_to_level < 20)  # Within $20
            
            data = {
                'price': round(current_price, 2),
                'change_1h': round(change_1h, 2),
                'change_4h': round(change_4h, 2),
                'change_24h': round(change_24h, 2),
                'rsi': round(rsi, 1),
                'atr': round(atr, 2),
                'atr_pips': int(atr * 10),  # Convert to pips (0.1 = 1 pip for gold)
                'volatility': volatility,
                'near_key_level': near_key_level,
                'key_level': nearest_level,
                'distance_to_level': round(distance_to_level, 2),
                'timestamp': datetime.now().isoformat()
            }
            
            # Cache the data
            self._cache_gold_data(data)
            
            return data
            
        except Exception as e:
            print(f"Error fetching Gold data: {e}")
            return self._get_cached_gold_data()
    
    def _cache_gold_data(self, data: Dict):
        """Cache Gold data to file"""
        cache_file = DATA_DIR / "gold_cache.json"
        # Convert numpy types to native Python types
        serializable_data = {}
        for key, value in data.items():
            if isinstance(value, (np.bool_, np.integer)):
                serializable_data[key] = int(value)
            elif isinstance(value, np.floating):
                serializable_data[key] = float(value)
            elif isinstance(value, np.ndarray):
                serializable_data[key] = value.tolist()
            else:
                serializable_data[key] = value
        
        with open(cache_file, 'w') as f:
            json.dump(serializable_data, f, indent=2)
    
    def _get_cached_gold_data(self) -> Dict:
        """Get cached Gold data"""
        cache_file = DATA_DIR / "gold_cache.json"
        if cache_file.exists():
            with open(cache_file, 'r') as f:
                return json.load(f)
        return {
            'price': 4980.0,
            'change_1h': 0.0,
            'change_4h': 0.0,
            'change_24h': 0.0,
            'rsi': 50,
            'atr': 20,
            'atr_pips': 200,
            'volatility': 'MEDIUM',
            'near_key_level': True,
            'key_level': 5000,
            'distance_to_level': 20,
            'timestamp': datetime.now().isoformat()
        }
    
    def get_gold_momentum(self) -> Dict:
        """
        Analyze current Gold momentum
        Returns: direction, strength, volatility, etc.
        """
        gold_data = self.get_gold_data()
        
        # Determine direction based on multiple factors
        bullish_score = 0
        bearish_score = 0
        
        # Price change factors
        if gold_data['change_1h'] > 0.3:
            bullish_score += 25
        elif gold_data['change_1h'] < -0.3:
            bearish_score += 25
        elif gold_data['change_1h'] > 0:
            bullish_score += 10
        elif gold_data['change_1h'] < 0:
            bearish_score += 10
            
        if gold_data['change_4h'] > 0.5:
            bullish_score += 30
        elif gold_data['change_4h'] < -0.5:
            bearish_score += 30
        elif gold_data['change_4h'] > 0:
            bullish_score += 15
        elif gold_data['change_4h'] < 0:
            bearish_score += 15
        
        # RSI factors
        if gold_data['rsi'] > 60:
            bullish_score += 20
        elif gold_data['rsi'] < 40:
            bearish_score += 20
        elif gold_data['rsi'] > 50:
            bullish_score += 10
        elif gold_data['rsi'] < 50:
            bearish_score += 10
        
        # Volatility bonus (stronger moves = stronger signals)
        if gold_data['volatility'] in ['HIGH', 'EXTREME']:
            bullish_score *= 1.2
            bearish_score *= 1.2
        
        # Determine direction
        total_score = bullish_score + bearish_score
        if total_score == 0:
            direction = 'NEUTRAL'
            strength = 50
        elif bullish_score > bearish_score:
            direction = 'BULLISH'
            strength = min(95, int((bullish_score / max(total_score, 1)) * 100))
        else:
            direction = 'BEARISH'
            strength = min(95, int((bearish_score / max(total_score, 1)) * 100))
        
        # Neutral if strength too low
        if strength < 55:
            direction = 'NEUTRAL'
        
        return {
            'direction': direction,
            'strength': strength,
            'price': gold_data['price'],
            'price_change_1h': gold_data['change_1h'],
            'price_change_4h': gold_data['change_4h'],
            'price_change_24h': gold_data['change_24h'],
            'rsi': gold_data['rsi'],
            'volatility': gold_data['volatility'],
            'atr_pips': gold_data['atr_pips'],
            'near_key_level': gold_data['near_key_level'],
            'key_level': gold_data['key_level'],
            'timestamp': gold_data['timestamp']
        }
    
    def get_forex_price(self, pair: str) -> Optional[float]:
        """Get current forex pair price"""
        try:
            config = self.correlations.get(pair)
            if not config:
                return None
            
            ticker = yf.Ticker(config['yf_symbol'])
            hist = ticker.history(period='1d')
            
            if not hist.empty:
                return round(hist['Close'].iloc[-1], 5)
            return None
        except:
            return None
    
    def _calculate_sl_tp(self, pair: str, gold_momentum: Dict, direction: str) -> Dict:
        """Calculate stop loss and take profit for a forex pair"""
        
        # Base pip values by pair type
        pip_values = {
            'AUDUSD': {'sl': 35, 'tp': 65, 'multiplier': 0.0001},
            'NZDUSD': {'sl': 35, 'tp': 65, 'multiplier': 0.0001},
            'EURUSD': {'sl': 30, 'tp': 50, 'multiplier': 0.0001},
            'USDCHF': {'sl': 35, 'tp': 60, 'multiplier': 0.0001},
            'USDJPY': {'sl': 50, 'tp': 100, 'multiplier': 0.01},
            'USDCAD': {'sl': 40, 'tp': 70, 'multiplier': 0.0001},
        }
        
        config = pip_values.get(pair, {'sl': 40, 'tp': 70, 'multiplier': 0.0001})
        
        # Adjust based on Gold volatility
        vol_multiplier = {
            'LOW': 0.7,
            'MEDIUM': 1.0,
            'HIGH': 1.3,
            'EXTREME': 1.6
        }.get(gold_momentum['volatility'], 1.0)
        
        sl_pips = int(config['sl'] * vol_multiplier)
        tp_pips = int(config['tp'] * vol_multiplier)
        
        # Get current price
        current_price = self.get_forex_price(pair)
        if current_price:
            if direction == 'BUY':
                sl_price = round(current_price - (sl_pips * config['multiplier']), 5)
                tp_price = round(current_price + (tp_pips * config['multiplier']), 5)
            else:  # SELL
                sl_price = round(current_price + (sl_pips * config['multiplier']), 5)
                tp_price = round(current_price - (tp_pips * config['multiplier']), 5)
        else:
            sl_price = None
            tp_price = None
        
        return {
            'sl_pips': sl_pips,
            'tp_pips': tp_pips,
            'sl_price': sl_price,
            'tp_price': tp_price,
            'entry': current_price,
            'risk_reward': round(tp_pips / sl_pips, 2) if sl_pips > 0 else 0
        }
    
    def _get_entry_timing(self, gold_momentum: Dict, config: Dict) -> str:
        """Determine optimal entry timing"""
        if gold_momentum['volatility'] == 'EXTREME':
            return 'WAIT_FOR_PULLBACK'
        elif gold_momentum['near_key_level']:
            return 'WAIT_FOR_BREAKOUT_CONFIRMATION'
        elif gold_momentum['strength'] > 80:
            return 'IMMEDIATE'
        else:
            return 'ON_PULLBACK'
    
    def generate_forex_signals(self, gold_momentum: Optional[Dict] = None) -> List[Dict]:
        """
        Generate signals for correlated pairs based on Gold momentum
        """
        if gold_momentum is None:
            gold_momentum = self.get_gold_momentum()
        
        signals = []
        
        # No signals if Gold is neutral
        if gold_momentum['direction'] == 'NEUTRAL':
            return [{
                'pair': 'NONE',
                'action': 'WAIT',
                'confidence': 0,
                'reason': 'Gold direction NEUTRAL - no correlated signals',
                'gold_status': gold_momentum
            }]
        
        for pair, config in self.correlations.items():
            # Calculate raw signal strength
            raw_strength = gold_momentum['strength'] * abs(config['correlation'])
            
            # Determine direction based on correlation type
            if config['direction'] == 'same':
                # Same direction as Gold
                direction = 'BUY' if gold_momentum['direction'] == 'BULLISH' else 'SELL'
            else:
                # Inverse direction (USD pairs)
                direction = 'SELL' if gold_momentum['direction'] == 'BULLISH' else 'BUY'
            
            # Only signal if confidence exceeds threshold
            confidence = int(raw_strength)
            if confidence >= self.signal_confidence_threshold:
                # Calculate SL/TP
                levels = self._calculate_sl_tp(pair, gold_momentum, direction)
                
                signals.append({
                    'pair': pair,
                    'action': direction,
                    'confidence': confidence,
                    'correlation': config['correlation'],
                    'correlation_strength': config['strength'],
                    'entry': levels['entry'],
                    'stop_loss': levels['sl_price'],
                    'take_profit': levels['tp_price'],
                    'sl_pips': levels['sl_pips'],
                    'tp_pips': levels['tp_pips'],
                    'risk_reward': levels['risk_reward'],
                    'reason': f"Gold {gold_momentum['direction']} ({gold_momentum['strength']}%) → {pair} {direction}",
                    'detailed_reason': config['reason'],
                    'entry_timing': self._get_entry_timing(gold_momentum, config),
                    'timestamp': datetime.now().isoformat()
                })
        
        # Sort by confidence (highest first)
        signals.sort(key=lambda x: x['confidence'], reverse=True)
        
        return signals
    
    def get_hedge_recommendations(self, gold_position: Dict) -> List[Dict]:
        """
        Recommend hedges for existing Gold position
        
        If LONG Gold:
            - SHORT USD/JPY (both benefit from risk-off)
            - LONG AUD/USD (amplify Gold exposure)
        
        If SHORT Gold:
            - LONG USD/JPY
            - SHORT AUD/USD
        """
        hedges = []
        direction = gold_position.get('direction', 'LONG').upper()
        
        if direction == 'LONG':
            hedges.append({
                'pair': 'USDJPY',
                'action': 'SELL',
                'size_ratio': 0.5,
                'type': 'HEDGE',
                'reason': 'Both Gold and JPY benefit from risk-off sentiment. If Gold drops, JPY weakness may offset losses.'
            })
            hedges.append({
                'pair': 'AUDUSD',
                'action': 'BUY',
                'size_ratio': 0.3,
                'type': 'AMPLIFY',
                'reason': 'AUD follows Gold (+0.80 correlation). Amplifies upside if Gold rallies.'
            })
            hedges.append({
                'pair': 'USDCHF',
                'action': 'SELL',
                'size_ratio': 0.3,
                'type': 'HEDGE',
                'reason': 'CHF is safe haven like Gold. Moves similarly in risk-off.'
            })
        
        elif direction == 'SHORT':
            hedges.append({
                'pair': 'USDJPY',
                'action': 'BUY',
                'size_ratio': 0.5,
                'type': 'HEDGE',
                'reason': 'If Gold rallies (against position), USD/JPY buy may offset.'
            })
            hedges.append({
                'pair': 'AUDUSD',
                'action': 'SELL',
                'size_ratio': 0.3,
                'type': 'AMPLIFY',
                'reason': 'AUD will fall with Gold. Amplifies downside profits.'
            })
        
        return hedges
    
    def detect_divergence(self) -> Dict:
        """
        Detect when correlated pairs diverge from Gold
        Divergence = potential reversal signal
        
        Example: Gold up but AUD/USD down = bearish divergence for Gold
        """
        gold_momentum = self.get_gold_momentum()
        divergences = []
        
        # Get recent changes for each pair
        for pair, config in self.correlations.items():
            try:
                ticker = yf.Ticker(config['yf_symbol'])
                hist = ticker.history(period='2d', interval='1h')
                
                if len(hist) >= 2:
                    pair_change = ((hist['Close'].iloc[-1] / hist['Close'].iloc[-5]) - 1) * 100
                    
                    # Check for divergence
                    gold_up = gold_momentum['price_change_4h'] > 0.3
                    gold_down = gold_momentum['price_change_4h'] < -0.3
                    pair_up = pair_change > 0.2
                    pair_down = pair_change < -0.2
                    
                    # Same direction pairs should move together
                    if config['direction'] == 'same':
                        if gold_up and pair_down:
                            divergences.append({
                                'pair': pair,
                                'type': 'BEARISH',
                                'gold_move': f"+{gold_momentum['price_change_4h']:.2f}%",
                                'pair_move': f"{pair_change:.2f}%",
                                'interpretation': f'{pair} not following Gold up - potential Gold reversal DOWN'
                            })
                        elif gold_down and pair_up:
                            divergences.append({
                                'pair': pair,
                                'type': 'BULLISH',
                                'gold_move': f"{gold_momentum['price_change_4h']:.2f}%",
                                'pair_move': f"+{pair_change:.2f}%",
                                'interpretation': f'{pair} not following Gold down - potential Gold reversal UP'
                            })
                    
                    # Inverse pairs should move opposite
                    else:
                        if gold_up and pair_up:
                            divergences.append({
                                'pair': pair,
                                'type': 'BEARISH',
                                'gold_move': f"+{gold_momentum['price_change_4h']:.2f}%",
                                'pair_move': f"+{pair_change:.2f}%",
                                'interpretation': f'{pair} rising WITH Gold (should be inverse) - unusual, watch for Gold reversal'
                            })
            except Exception as e:
                continue
        
        return {
            'divergence_detected': len(divergences) > 0,
            'count': len(divergences),
            'divergences': divergences,
            'gold_momentum': gold_momentum,
            'timestamp': datetime.now().isoformat()
        }
    
    def get_best_pair_for_gold_move(self, gold_direction: Optional[str] = None) -> Dict:
        """
        Return the single best forex pair to trade based on Gold direction
        """
        if gold_direction is None:
            momentum = self.get_gold_momentum()
            gold_direction = momentum['direction']
        
        if gold_direction == 'BULLISH':
            return {
                'pair': 'AUDUSD',
                'action': 'BUY',
                'correlation': 0.80,
                'reason': 'Highest correlation (+0.80). Australia = #2 gold producer. Will rise fastest with Gold.',
                'alternative': {
                    'pair': 'USDJPY',
                    'action': 'SELL',
                    'reason': 'Safe haven inverse play. JPY strengthens with Gold.'
                }
            }
        elif gold_direction == 'BEARISH':
            return {
                'pair': 'AUDUSD',
                'action': 'SELL',
                'correlation': 0.80,
                'reason': 'Highest correlation (+0.80). Will fall fastest when Gold drops.',
                'alternative': {
                    'pair': 'USDJPY',
                    'action': 'BUY',
                    'reason': 'Risk-on = JPY weakness = USD/JPY rises.'
                }
            }
        else:
            return {
                'pair': 'NONE',
                'action': 'WAIT',
                'reason': 'Gold direction unclear. Wait for momentum.',
                'alternative': None
            }
    
    def get_full_analysis(self) -> Dict:
        """
        Get complete Gold-Forex correlation analysis
        This is the main method for the API endpoint
        """
        # Get Gold momentum
        gold_momentum = self.get_gold_momentum()
        
        # Generate forex signals
        forex_signals = self.generate_forex_signals(gold_momentum)
        
        # Get hedge recommendations (assuming long gold position as default)
        hedge_recommendations = self.get_hedge_recommendations({'direction': 'LONG'})
        
        # Check for divergences
        divergence = self.detect_divergence()
        
        # Get best single trade
        best_trade = self.get_best_pair_for_gold_move(gold_momentum['direction'])
        
        # Build correlation heatmap
        heatmap = {}
        for pair, config in self.correlations.items():
            heatmap[f"XAUUSD_{pair}"] = config['correlation']
        
        return {
            'gold_status': {
                'price': gold_momentum['price'],
                'direction': gold_momentum['direction'],
                'strength': gold_momentum['strength'],
                'volatility': gold_momentum['volatility'],
                'change_1h': f"{gold_momentum['price_change_1h']:+.2f}%",
                'change_4h': f"{gold_momentum['price_change_4h']:+.2f}%",
                'change_24h': f"{gold_momentum['price_change_24h']:+.2f}%",
                'rsi': gold_momentum['rsi'],
                'near_key_level': gold_momentum['near_key_level'],
                'key_level': gold_momentum['key_level']
            },
            'forex_signals': forex_signals,
            'best_trade': best_trade,
            'hedge_recommendations': hedge_recommendations,
            'divergence_alert': divergence if divergence['divergence_detected'] else None,
            'correlation_heatmap': heatmap,
            'timestamp': datetime.now().isoformat()
        }


# Test
if __name__ == "__main__":
    correlator = GoldForexCorrelator()
    
    print("=" * 70)
    print("🥇 GOLD-FOREX CORRELATION ANALYSIS")
    print("=" * 70)
    
    analysis = correlator.get_full_analysis()
    
    print(f"""
GOLD STATUS:
  Price: ${analysis['gold_status']['price']}
  Direction: {analysis['gold_status']['direction']}
  Strength: {analysis['gold_status']['strength']}%
  Volatility: {analysis['gold_status']['volatility']}
  1H Change: {analysis['gold_status']['change_1h']}
  4H Change: {analysis['gold_status']['change_4h']}
  RSI: {analysis['gold_status']['rsi']}
  Near Key Level: {analysis['gold_status']['near_key_level']} (${analysis['gold_status']['key_level']})

BEST SINGLE TRADE:
  {analysis['best_trade']['pair']} → {analysis['best_trade']['action']}
  Reason: {analysis['best_trade']['reason']}

FOREX SIGNALS:
""")
    
    for signal in analysis['forex_signals'][:5]:
        if signal['pair'] != 'NONE':
            print(f"  {signal['pair']}: {signal['action']} ({signal['confidence']}%)")
            print(f"    Entry: {signal.get('entry', 'N/A')}")
            print(f"    SL: {signal.get('stop_loss', 'N/A')} ({signal.get('sl_pips', 0)} pips)")
            print(f"    TP: {signal.get('take_profit', 'N/A')} ({signal.get('tp_pips', 0)} pips)")
            print(f"    R:R = {signal.get('risk_reward', 'N/A')}")
            print(f"    Timing: {signal.get('entry_timing', 'N/A')}")
            print()
    
    print("HEDGE RECOMMENDATIONS (if LONG Gold):")
    for hedge in analysis['hedge_recommendations']:
        print(f"  {hedge['pair']} → {hedge['action']} ({hedge['size_ratio']*100:.0f}% size)")
        print(f"    Type: {hedge['type']}")
        print(f"    Reason: {hedge['reason']}")
        print()
    
    if analysis['divergence_alert']:
        print("⚠️ DIVERGENCE ALERT:")
        for div in analysis['divergence_alert']['divergences']:
            print(f"  {div['pair']}: {div['type']}")
            print(f"    {div['interpretation']}")
    else:
        print("✅ No divergences detected")
