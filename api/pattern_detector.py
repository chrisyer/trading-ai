"""
Pattern Detector - Detects candlestick patterns for trading alerts.

Patterns detected:
- Bearish: Shooting star, Bearish engulfing, Evening star, Distribution volume
- Bullish: Hammer, Bullish engulfing, Morning star, V-recovery

Each pattern returns severity and DEFCON impact recommendation.
"""

from typing import List, Dict, Optional
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("PatternDetector")


class PatternDetector:
    """Detect candlestick and volume patterns."""
    
    def detect_all_patterns(self, candles: List[Dict], symbol: str = "XAUUSD") -> List[Dict]:
        """Run all pattern detections on candle data."""
        if len(candles) < 10:
            return []
        
        alerts = []
        
        # Get recent candles
        latest = candles[-1]
        prev = candles[-2]
        prev2 = candles[-3] if len(candles) > 2 else None
        
        # Calculate averages
        avg_volume = sum(c.get("volume", 0) for c in candles[-20:]) / 20 if len(candles) >= 20 else 1
        avg_body = sum(abs(c["close"] - c["open"]) for c in candles[-20:]) / 20 if len(candles) >= 20 else 1
        
        current_price = latest["close"]
        
        # === BEARISH PATTERNS ===
        
        # Shooting Star
        if self.is_shooting_star(latest, prev, avg_body):
            alerts.append({
                "pattern": "SHOOTING_STAR",
                "severity": "HIGH",
                "direction": "BEARISH",
                "defcon_impact": -2,
                "symbol": symbol,
                "price": current_price,
                "message": f"Shooting Star at ${latest['high']:.2f} - rejection from highs",
                "action": "Consider taking profits, tighten stops",
                "candle": latest,
            })
        
        # Bearish Engulfing
        if self.is_bearish_engulfing(latest, prev):
            alerts.append({
                "pattern": "BEARISH_ENGULFING",
                "severity": "HIGH",
                "direction": "BEARISH",
                "defcon_impact": -1,
                "symbol": symbol,
                "price": current_price,
                "message": f"Bearish Engulfing at ${current_price:.2f} - reversal signal",
                "action": "Reduce exposure, prepare for downside",
                "candle": latest,
            })
        
        # Evening Star (3-candle pattern)
        if prev2 and self.is_evening_star(prev2, prev, latest):
            alerts.append({
                "pattern": "EVENING_STAR",
                "severity": "HIGH",
                "direction": "BEARISH",
                "defcon_impact": -2,
                "symbol": symbol,
                "price": current_price,
                "message": f"Evening Star formation complete - classic reversal",
                "action": "URGENT: Close longs, activate defensive mode",
                "candle": latest,
            })
        
        # Distribution Volume (multiple red candles with rising volume)
        volume_alert = self.check_distribution_volume(candles[-5:], avg_volume, symbol, current_price)
        if volume_alert:
            alerts.append(volume_alert)
        
        # Volume Climax Rejection
        if avg_volume > 0 and latest.get("volume", 0) > avg_volume * 3:
            body = abs(latest["close"] - latest["open"])
            upper_wick = latest["high"] - max(latest["close"], latest["open"])
            
            if body > 0 and upper_wick > body * 2:  # Rejection with high volume
                alerts.append({
                    "pattern": "VOLUME_CLIMAX_REJECTION",
                    "severity": "CRITICAL",
                    "direction": "BEARISH",
                    "defcon_impact": -2,
                    "symbol": symbol,
                    "price": current_price,
                    "message": f"Volume climax ({latest.get('volume', 0)/avg_volume:.1f}x avg) with rejection at ${latest['high']:.2f}",
                    "action": "URGENT: Take profits immediately, activate SPY",
                    "candle": latest,
                })
        
        # Failed Breakout
        if len(candles) >= 10:
            failed_breakout = self.check_failed_breakout(candles[-10:], symbol, current_price)
            if failed_breakout:
                alerts.append(failed_breakout)
        
        # Hanging Man
        if self.is_hanging_man(latest, prev, avg_body):
            alerts.append({
                "pattern": "HANGING_MAN",
                "severity": "MEDIUM",
                "direction": "BEARISH",
                "defcon_impact": -1,
                "symbol": symbol,
                "price": current_price,
                "message": f"Hanging Man at ${current_price:.2f} - potential reversal",
                "action": "Watch for confirmation, tighten stops",
                "candle": latest,
            })
        
        # === BULLISH PATTERNS ===
        
        # Hammer
        if self.is_hammer(latest, prev, avg_body):
            alerts.append({
                "pattern": "HAMMER",
                "severity": "HIGH",
                "direction": "BULLISH",
                "defcon_impact": +1,
                "symbol": symbol,
                "price": current_price,
                "message": f"Hammer at ${latest['low']:.2f} - potential reversal",
                "action": "Watch for confirmation, prepare entry",
                "candle": latest,
            })
        
        # Bullish Engulfing
        if self.is_bullish_engulfing(latest, prev):
            alerts.append({
                "pattern": "BULLISH_ENGULFING",
                "severity": "HIGH",
                "direction": "BULLISH",
                "defcon_impact": +1,
                "symbol": symbol,
                "price": current_price,
                "message": f"Bullish Engulfing at ${current_price:.2f} - reversal signal",
                "action": "Consider entry, upgrade DEFCON",
                "candle": latest,
            })
        
        # Morning Star (3-candle pattern)
        if prev2 and self.is_morning_star(prev2, prev, latest):
            alerts.append({
                "pattern": "MORNING_STAR",
                "severity": "HIGH",
                "direction": "BULLISH",
                "defcon_impact": +2,
                "symbol": symbol,
                "price": current_price,
                "message": f"Morning Star formation complete - classic reversal",
                "action": "Strong buy signal, consider adding to longs",
                "candle": latest,
            })
        
        # V-Recovery
        v_recovery = self.check_v_recovery(candles[-6:], symbol, current_price)
        if v_recovery:
            alerts.append(v_recovery)
        
        # Bullish Divergence (RSI)
        # Note: This requires RSI calculation, simplified here
        if len(candles) >= 14:
            divergence = self.check_bullish_divergence(candles[-14:], symbol, current_price)
            if divergence:
                alerts.append(divergence)
        
        return alerts
    
    # === BEARISH PATTERN DETECTORS ===
    
    def is_shooting_star(self, candle: Dict, prev_candle: Dict, avg_body: float) -> bool:
        """Detect shooting star pattern."""
        body = abs(candle["close"] - candle["open"])
        upper_wick = candle["high"] - max(candle["close"], candle["open"])
        lower_wick = min(candle["close"], candle["open"]) - candle["low"]
        
        if body < avg_body * 0.1:  # Doji-like, skip
            return False
        
        return (
            upper_wick > body * 2 and
            lower_wick < body * 0.5 and
            candle["high"] > prev_candle["high"]  # At new high
        )
    
    def is_bearish_engulfing(self, candle: Dict, prev_candle: Dict) -> bool:
        """Detect bearish engulfing pattern."""
        curr_body = candle["close"] - candle["open"]  # Negative if red
        prev_body = prev_candle["close"] - prev_candle["open"]  # Positive if green
        
        return (
            curr_body < 0 and  # Current is red
            prev_body > 0 and  # Previous is green
            abs(curr_body) > prev_body * 1.2 and  # Red engulfs green significantly
            candle["open"] >= prev_candle["close"] and  # Opens at or above prior close
            candle["close"] <= prev_candle["open"]  # Close at or below prior open
        )
    
    def is_evening_star(self, candle1: Dict, candle2: Dict, candle3: Dict) -> bool:
        """Detect evening star (3-candle reversal)."""
        body1 = candle1["close"] - candle1["open"]  # Should be positive (green)
        body2 = abs(candle2["close"] - candle2["open"])  # Small body
        body3 = candle3["close"] - candle3["open"]  # Should be negative (red)
        
        avg_body = abs(body1) * 0.3  # Small body threshold
        
        return (
            body1 > 0 and  # First is green
            body2 < avg_body and  # Second is small
            body3 < 0 and  # Third is red
            abs(body3) > abs(body1) * 0.5 and  # Third is substantial
            candle2["close"] > candle1["close"] and  # Gap up
            candle3["close"] < candle1["close"]  # Third closes below first
        )
    
    def is_hanging_man(self, candle: Dict, prev_candle: Dict, avg_body: float) -> bool:
        """Detect hanging man pattern."""
        body = abs(candle["close"] - candle["open"])
        lower_wick = min(candle["close"], candle["open"]) - candle["low"]
        upper_wick = candle["high"] - max(candle["close"], candle["open"])
        
        if body < avg_body * 0.1:
            return False
        
        return (
            lower_wick > body * 2 and
            upper_wick < body * 0.3 and
            candle["close"] > prev_candle["close"]  # In uptrend
        )
    
    def check_distribution_volume(self, recent_candles: List[Dict], avg_volume: float, 
                                   symbol: str, current_price: float) -> Optional[Dict]:
        """Check for rising volume on consecutive red candles (distribution)."""
        if len(recent_candles) < 3:
            return None
        
        red_count = 0
        volumes = []
        
        for candle in recent_candles:
            is_red = candle["close"] < candle["open"]
            if is_red:
                red_count += 1
                volumes.append(candle.get("volume", 0))
            else:
                red_count = 0
                volumes = []
        
        if red_count < 2:
            return None
        
        # Check if volume is increasing
        volume_increasing = all(volumes[i] >= volumes[i-1] * 0.9 for i in range(1, len(volumes))) if len(volumes) > 1 else False
        
        # 3+ consecutive red candles with increasing volume
        if red_count >= 3 and volume_increasing and avg_volume > 0:
            return {
                "pattern": "DISTRIBUTION_VOLUME",
                "severity": "CRITICAL",
                "direction": "BEARISH",
                "defcon_impact": -2,
                "symbol": symbol,
                "price": current_price,
                "message": f"{red_count} consecutive red candles with RISING volume",
                "action": "URGENT: Major selling pressure - reduce exposure NOW",
            }
        
        # 2 red with elevated volume
        if red_count >= 2 and volumes and avg_volume > 0 and volumes[-1] > avg_volume * 1.5:
            return {
                "pattern": "HIGH_VOLUME_SELLING",
                "severity": "HIGH",
                "direction": "BEARISH",
                "defcon_impact": -1,
                "symbol": symbol,
                "price": current_price,
                "message": f"Back-to-back red candles with {volumes[-1]/avg_volume:.1f}x average volume",
                "action": "Warning: Selling pressure increasing",
            }
        
        return None
    
    def check_failed_breakout(self, candles: List[Dict], symbol: str, current_price: float) -> Optional[Dict]:
        """Detect failed breakout pattern."""
        if len(candles) < 5:
            return None
        
        # Find recent high (excluding last 2 candles)
        recent_high = max(c["high"] for c in candles[:-2])
        
        # Check if we broke above then closed back below
        broke_above = any(c["high"] > recent_high * 1.002 for c in candles[-3:-1])  # Broke above
        closed_below = candles[-1]["close"] < recent_high  # Closed back below
        
        if broke_above and closed_below:
            return {
                "pattern": "FAILED_BREAKOUT",
                "severity": "HIGH",
                "direction": "BEARISH",
                "defcon_impact": -1,
                "symbol": symbol,
                "price": current_price,
                "message": f"Failed breakout above ${recent_high:.2f} - bull trap",
                "action": "Breakout failed - consider reducing longs",
            }
        
        return None
    
    # === BULLISH PATTERN DETECTORS ===
    
    def is_hammer(self, candle: Dict, prev_candle: Dict, avg_body: float) -> bool:
        """Detect hammer pattern."""
        body = abs(candle["close"] - candle["open"])
        lower_wick = min(candle["close"], candle["open"]) - candle["low"]
        upper_wick = candle["high"] - max(candle["close"], candle["open"])
        
        if body < avg_body * 0.1:
            return False
        
        return (
            lower_wick > body * 2 and
            upper_wick < body * 0.5 and
            candle["low"] < prev_candle["low"]  # At new low
        )
    
    def is_bullish_engulfing(self, candle: Dict, prev_candle: Dict) -> bool:
        """Detect bullish engulfing pattern."""
        curr_body = candle["close"] - candle["open"]  # Positive if green
        prev_body = prev_candle["close"] - prev_candle["open"]  # Negative if red
        
        return (
            curr_body > 0 and
            prev_body < 0 and
            curr_body > abs(prev_body) * 1.2 and
            candle["open"] <= prev_candle["close"] and
            candle["close"] >= prev_candle["open"]
        )
    
    def is_morning_star(self, candle1: Dict, candle2: Dict, candle3: Dict) -> bool:
        """Detect morning star (3-candle reversal)."""
        body1 = candle1["close"] - candle1["open"]  # Should be negative (red)
        body2 = abs(candle2["close"] - candle2["open"])  # Small body
        body3 = candle3["close"] - candle3["open"]  # Should be positive (green)
        
        avg_body = abs(body1) * 0.3
        
        return (
            body1 < 0 and  # First is red
            body2 < avg_body and  # Second is small
            body3 > 0 and  # Third is green
            body3 > abs(body1) * 0.5 and  # Third is substantial
            candle2["close"] < candle1["close"] and  # Gap down
            candle3["close"] > candle1["close"]  # Third closes above first
        )
    
    def check_v_recovery(self, candles: List[Dict], symbol: str, current_price: float) -> Optional[Dict]:
        """Detect V-recovery pattern."""
        if len(candles) < 4:
            return None
        
        # Find the low point
        lows = [c["low"] for c in candles]
        low_idx = lows.index(min(lows))
        
        if low_idx < 1 or low_idx > len(candles) - 2:
            return None
        
        # Check for sharp drop then sharp recovery
        drop_before = candles[0]["close"] - candles[low_idx]["low"]
        rise_after = candles[-1]["close"] - candles[low_idx]["low"]
        
        # V-recovery: similar magnitude drop and rise
        if drop_before > 0 and rise_after > drop_before * 0.7:
            return {
                "pattern": "V_RECOVERY",
                "severity": "MEDIUM",
                "direction": "BULLISH",
                "defcon_impact": +1,
                "symbol": symbol,
                "price": current_price,
                "message": f"V-Recovery from ${candles[low_idx]['low']:.2f} - sharp reversal",
                "action": "Potential bottom, watch for follow-through",
            }
        
        return None
    
    def check_bullish_divergence(self, candles: List[Dict], symbol: str, current_price: float) -> Optional[Dict]:
        """Check for bullish divergence (price lower low, momentum higher low)."""
        if len(candles) < 10:
            return None
        
        # Simple RSI-like momentum calculation
        gains = []
        losses = []
        for i in range(1, len(candles)):
            change = candles[i]["close"] - candles[i-1]["close"]
            if change > 0:
                gains.append(change)
                losses.append(0)
            else:
                gains.append(0)
                losses.append(abs(change))
        
        # Calculate RSI for first half and second half
        half = len(gains) // 2
        
        avg_gain_1 = sum(gains[:half]) / half if half > 0 else 0
        avg_loss_1 = sum(losses[:half]) / half if half > 0 else 0.001
        rsi_1 = 100 - (100 / (1 + avg_gain_1 / avg_loss_1))
        
        avg_gain_2 = sum(gains[half:]) / (len(gains) - half) if len(gains) > half else 0
        avg_loss_2 = sum(losses[half:]) / (len(losses) - half) if len(losses) > half else 0.001
        rsi_2 = 100 - (100 / (1 + avg_gain_2 / avg_loss_2))
        
        # Price lower low
        price_ll = min(c["low"] for c in candles[half:]) < min(c["low"] for c in candles[:half])
        # RSI higher low
        rsi_hl = rsi_2 > rsi_1
        
        if price_ll and rsi_hl and rsi_2 < 40:  # Oversold with divergence
            return {
                "pattern": "BULLISH_DIVERGENCE",
                "severity": "MEDIUM",
                "direction": "BULLISH",
                "defcon_impact": +1,
                "symbol": symbol,
                "price": current_price,
                "message": f"Bullish RSI divergence detected - momentum building",
                "action": "Watch for reversal confirmation",
            }
        
        return None
