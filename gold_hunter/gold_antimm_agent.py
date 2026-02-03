"""
GOLD (XAUUSD) ANTI-MARKET MAKER AGENT
=====================================
Uses FinRL + Order Flow detection to hunt MM patterns on Gold

Key Features:
- Detects institutional order flow in gold
- Identifies MM accumulation/distribution
- Exploits predictable MM behavior patterns
- Trains continuously on H100 GPU
"""

import os
import numpy as np
import pandas as pd
import torch
import gymnasium as gym
from gymnasium import spaces
from datetime import datetime, timedelta
import yfinance as yf
import ta
from typing import Dict, List, Tuple, Optional
import json
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("GOLD_HUNTER")

# GPU Configuration
DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")
logger.info(f"Using device: {DEVICE}")


class GoldOrderFlowDetector:
    """
    Detect institutional/MM order flow patterns in Gold
    Gold-specific patterns: London fix, NY session, Asian accumulation
    """
    
    def __init__(self):
        self.volume_threshold_multiplier = 2.0
        self.delta_lookback = 20
        
    def detect_features(self, df: pd.DataFrame) -> Dict:
        """Extract order flow features from OHLCV data"""
        
        features = {}
        
        if len(df) < 5:
            return {
                'volume_ratio': 1.0,
                'volume_spike': False,
                'delta': 0.5,
                'cumulative_delta': 0,
                'absorption': False,
                'exhaustion': False,
                'stop_hunt': False,
                'liquidity_grab': False,
                'sweep': False
            }
        
        # Volume analysis
        avg_volume = df['Volume'].rolling(20).mean()
        vol_ratio = df['Volume'] / avg_volume
        features['volume_ratio'] = float(vol_ratio.iloc[-1]) if not pd.isna(vol_ratio.iloc[-1]) else 1.0
        features['volume_spike'] = features['volume_ratio'] > self.volume_threshold_multiplier
        
        # Delta approximation (buy vs sell pressure)
        df = df.copy()
        df['delta_proxy'] = (df['Close'] - df['Low']) / (df['High'] - df['Low'] + 0.0001)
        features['delta'] = float(df['delta_proxy'].iloc[-1])
        cum_delta = df['delta_proxy'].rolling(self.delta_lookback).sum()
        features['cumulative_delta'] = float(cum_delta.iloc[-1]) if not pd.isna(cum_delta.iloc[-1]) else 0
        
        # Absorption detection (high volume + small price move)
        price_range = (df['High'] - df['Low']).iloc[-1]
        avg_range = (df['High'] - df['Low']).rolling(20).mean().iloc[-1]
        features['absorption'] = (features['volume_ratio'] > 1.5) and (price_range < avg_range * 0.5)
        
        # Exhaustion detection
        features['exhaustion'] = self._detect_exhaustion(df)
        
        # Stop hunt detection (common in gold)
        features['stop_hunt'] = self._detect_stop_hunt(df)
        
        # Liquidity grab
        features['liquidity_grab'] = self._detect_liquidity_grab(df)
        
        # Liquidity sweep (gold-specific)
        features['sweep'] = self._detect_sweep(df)
        
        return features
    
    def _detect_exhaustion(self, df: pd.DataFrame) -> bool:
        """Detect exhaustion patterns"""
        if len(df) < 5:
            return False
            
        price_change = abs(df['Close'].iloc[-1] - df['Close'].iloc[-5]) / df['Close'].iloc[-5]
        volume_declining = df['Volume'].iloc[-1] < df['Volume'].iloc[-3]
        
        return price_change > 0.005 and volume_declining  # 0.5% move for gold
    
    def _detect_stop_hunt(self, df: pd.DataFrame) -> bool:
        """Detect stop hunt patterns - very common in gold"""
        if len(df) < 10:
            return False
            
        recent_high = df['High'].iloc[-10:-1].max()
        recent_low = df['Low'].iloc[-10:-1].min()
        
        current_high = df['High'].iloc[-1]
        current_low = df['Low'].iloc[-1]
        current_close = df['Close'].iloc[-1]
        
        high_hunt = (current_high > recent_high) and (current_close < recent_high)
        low_hunt = (current_low < recent_low) and (current_close > recent_low)
        
        return high_hunt or low_hunt
    
    def _detect_liquidity_grab(self, df: pd.DataFrame) -> bool:
        """Detect liquidity grab patterns"""
        if len(df) < 3:
            return False
            
        body = abs(df['Close'].iloc[-1] - df['Open'].iloc[-1])
        upper_wick = df['High'].iloc[-1] - max(df['Open'].iloc[-1], df['Close'].iloc[-1])
        lower_wick = min(df['Open'].iloc[-1], df['Close'].iloc[-1]) - df['Low'].iloc[-1]
        
        total_wick = upper_wick + lower_wick
        return total_wick > body * 2
    
    def _detect_sweep(self, df: pd.DataFrame) -> bool:
        """Detect liquidity sweep - price breaks level then reverses quickly"""
        if len(df) < 5:
            return False
        
        # Check for quick reversal after breaking recent high/low
        prev_high = df['High'].iloc[-5:-1].max()
        prev_low = df['Low'].iloc[-5:-1].min()
        
        # Broke high and reversed down
        swept_high = (df['High'].iloc[-1] > prev_high * 1.001) and \
                     (df['Close'].iloc[-1] < df['Open'].iloc[-1])
        
        # Broke low and reversed up
        swept_low = (df['Low'].iloc[-1] < prev_low * 0.999) and \
                    (df['Close'].iloc[-1] > df['Open'].iloc[-1])
        
        return swept_high or swept_low


class GoldTradingEnv(gym.Env):
    """
    Custom Gymnasium environment for Gold/XAUUSD trading
    Incorporates order flow features
    """
    
    def __init__(self, df: pd.DataFrame, initial_balance: float = 100000):
        super().__init__()
        
        self.df = df.reset_index(drop=True)
        self.initial_balance = initial_balance
        self.order_flow = GoldOrderFlowDetector()
        
        # Technical indicators
        self._add_indicators()
        
        # State: OHLCV + indicators + order flow features
        self.observation_space = spaces.Box(
            low=-np.inf, high=np.inf, shape=(26,), dtype=np.float32
        )
        
        # Actions: 0=Hold, 1=Buy, 2=Sell
        self.action_space = spaces.Discrete(3)
        
        self.reset()
    
    def _add_indicators(self):
        """Add technical indicators optimized for gold"""
        df = self.df
        
        # Trend
        df['sma_20'] = ta.trend.sma_indicator(df['Close'], window=20)
        df['sma_50'] = ta.trend.sma_indicator(df['Close'], window=50)
        df['sma_200'] = ta.trend.sma_indicator(df['Close'], window=200)
        df['ema_9'] = ta.trend.ema_indicator(df['Close'], window=9)
        
        # Momentum
        df['rsi'] = ta.momentum.rsi(df['Close'], window=14)
        df['rsi_2'] = ta.momentum.rsi(df['Close'], window=2)
        macd = ta.trend.MACD(df['Close'])
        df['macd'] = macd.macd()
        df['macd_signal'] = macd.macd_signal()
        
        # Volatility - important for gold
        bb = ta.volatility.BollingerBands(df['Close'])
        df['bb_upper'] = bb.bollinger_hband()
        df['bb_lower'] = bb.bollinger_lband()
        df['bb_width'] = (df['bb_upper'] - df['bb_lower']) / df['Close']
        df['atr'] = ta.volatility.average_true_range(df['High'], df['Low'], df['Close'])
        
        # Volume
        df['volume_sma'] = df['Volume'].rolling(20).mean()
        df['volume_ratio'] = df['Volume'] / df['volume_sma']
        
        # Fill NaN
        df.fillna(method='bfill', inplace=True)
        df.fillna(0, inplace=True)
        
        self.df = df
    
    def _get_observation(self) -> np.ndarray:
        """Get current state observation"""
        row = self.df.iloc[self.current_step]
        
        # Order flow features
        lookback_df = self.df.iloc[max(0, self.current_step-20):self.current_step+1]
        of_features = self.order_flow.detect_features(lookback_df)
        
        obs = np.array([
            # Price features (normalized)
            row['Close'] / row['sma_20'] - 1,
            row['Close'] / row['sma_50'] - 1,
            row['Close'] / row['sma_200'] - 1 if row['sma_200'] > 0 else 0,
            row['Close'] / row['ema_9'] - 1,
            (row['Close'] - row['bb_lower']) / (row['bb_upper'] - row['bb_lower'] + 0.001),
            
            # Momentum
            row['rsi'] / 100,
            row['rsi_2'] / 100,
            row['macd'] / row['Close'] * 100,
            
            # Volatility (gold-specific importance)
            row['atr'] / row['Close'],
            row['bb_width'],
            
            # Volume
            row['volume_ratio'] if not pd.isna(row['volume_ratio']) else 1.0,
            
            # Order flow features
            of_features.get('volume_ratio', 1.0),
            of_features.get('delta', 0.5),
            of_features.get('cumulative_delta', 0.0),
            float(of_features.get('absorption', False)),
            float(of_features.get('exhaustion', False)),
            float(of_features.get('stop_hunt', False)),
            float(of_features.get('liquidity_grab', False)),
            float(of_features.get('sweep', False)),
            
            # Position info
            float(self.position),
            self.entry_price / row['Close'] if self.entry_price > 0 else 1.0,
            self.unrealized_pnl / self.initial_balance,
            self.balance / self.initial_balance,
            
            # Recent returns
            row['Close'] / self.df.iloc[max(0, self.current_step-5)]['Close'] - 1,
            row['Close'] / self.df.iloc[max(0, self.current_step-20)]['Close'] - 1,
            
            # Spread proxy
            (row['High'] - row['Low']) / row['Close'],
        ], dtype=np.float32)
        
        return obs
    
    def reset(self, seed=None):
        super().reset(seed=seed)
        
        self.current_step = 200  # Start after indicators are valid (need 200 for SMA200)
        self.balance = self.initial_balance
        self.position = 0
        self.entry_price = 0
        self.units = 0  # Gold uses units/oz
        self.unrealized_pnl = 0
        self.total_trades = 0
        self.winning_trades = 0
        
        return self._get_observation(), {}
    
    def step(self, action: int) -> Tuple[np.ndarray, float, bool, bool, Dict]:
        """Execute one step"""
        
        current_price = self.df.iloc[self.current_step]['Close']
        reward = 0
        
        # Gold position sizing: 1 lot = 100 oz, pip value varies
        position_value = self.balance * 0.10  # 10% of balance per trade
        units = position_value / current_price * 100  # Convert to oz
        
        if action == 1 and self.position <= 0:  # Buy
            if self.position == -1:  # Close short
                pnl = (self.entry_price - current_price) * self.units
                self.balance += pnl
                reward = pnl / self.initial_balance * 100
                if pnl > 0:
                    self.winning_trades += 1
                self.total_trades += 1
            
            self.units = units
            self.entry_price = current_price
            self.position = 1
            
        elif action == 2 and self.position >= 0:  # Sell
            if self.position == 1:  # Close long
                pnl = (current_price - self.entry_price) * self.units
                self.balance += pnl
                reward = pnl / self.initial_balance * 100
                if pnl > 0:
                    self.winning_trades += 1
                self.total_trades += 1
            
            self.units = units
            self.entry_price = current_price
            self.position = -1
        
        # Calculate unrealized PnL
        if self.position == 1:
            self.unrealized_pnl = (current_price - self.entry_price) * self.units
        elif self.position == -1:
            self.unrealized_pnl = (self.entry_price - current_price) * self.units
        else:
            self.unrealized_pnl = 0
        
        self.current_step += 1
        done = self.current_step >= len(self.df) - 1
        truncated = False
        
        if self.unrealized_pnl > 0:
            reward += 0.01
        
        obs = self._get_observation()
        
        info = {
            'balance': self.balance,
            'position': self.position,
            'unrealized_pnl': self.unrealized_pnl,
            'total_value': self.balance + self.unrealized_pnl,
            'total_trades': self.total_trades,
            'win_rate': self.winning_trades / max(1, self.total_trades)
        }
        
        return obs, reward, done, truncated, info


class GoldHunterTrainer:
    """
    Train the Gold Anti-MM agent using PPO on H100
    """
    
    def __init__(self, model_dir: str = "/home/jbot/trading_ai/gold_hunter/models"):
        self.model_dir = model_dir
        os.makedirs(model_dir, exist_ok=True)
        
        self.device = DEVICE
        logger.info(f"Gold Trainer initialized on {self.device}")
        
    def fetch_gold_data(self, days: int = 60) -> pd.DataFrame:
        """Fetch Gold (GC=F) data for training"""
        end = datetime.now()
        start = end - timedelta(days=days)
        
        gold = yf.Ticker("GC=F")
        df = gold.history(start=start, end=end, interval="5m")
        
        df = df.reset_index()
        df = df[['Open', 'High', 'Low', 'Close', 'Volume']].copy()
        df = df.reset_index(drop=True)
        
        # Filter out zero volume (non-trading hours)
        df = df[df['Volume'] > 0].reset_index(drop=True)
        
        logger.info(f"Fetched {len(df)} bars of Gold 5m data")
        return df
    
    def train(self, total_timesteps: int = 100000):
        """Train the agent"""
        from stable_baselines3 import PPO
        from stable_baselines3.common.vec_env import DummyVecEnv
        
        df = self.fetch_gold_data(days=30)
        
        if len(df) < 500:
            logger.error(f"Insufficient data: {len(df)} bars")
            return None
        
        env = DummyVecEnv([lambda: GoldTradingEnv(df)])
        
        model = PPO(
            "MlpPolicy",
            env,
            verbose=1,
            learning_rate=3e-4,
            n_steps=4096,
            batch_size=256,
            n_epochs=20,
            gamma=0.99,
            gae_lambda=0.95,
            clip_range=0.2,
            ent_coef=0.01,
            device=self.device,
            tensorboard_log=f"{self.model_dir}/logs"
        )
        
        logger.info(f"Starting training for {total_timesteps} timesteps...")
        model.learn(total_timesteps=total_timesteps)
        
        model_path = f"{self.model_dir}/gold_hunter_ppo"
        model.save(model_path)
        logger.info(f"Model saved to {model_path}")
        
        return model


if __name__ == "__main__":
    import sys
    
    trainer = GoldHunterTrainer()
    trainer.train(total_timesteps=100000)
