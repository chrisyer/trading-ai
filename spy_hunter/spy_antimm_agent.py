"""
SPY ANTI-MARKET MAKER AGENT
===========================
Uses FinRL + Order Flow detection to hunt MM patterns on SPY

Key Features:
- Detects institutional order flow
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
logger = logging.getLogger("SPY_HUNTER")

# GPU Configuration
DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")
logger.info(f"Using device: {DEVICE}")


class OrderFlowDetector:
    """
    Detect institutional/MM order flow patterns
    Based on ICT methodology + volume analysis
    """
    
    def __init__(self):
        self.volume_threshold_multiplier = 2.0
        self.delta_lookback = 20
        
    def detect_features(self, df: pd.DataFrame) -> Dict:
        """Extract order flow features from OHLCV data"""
        
        features = {}
        
        # Volume analysis
        avg_volume = df['Volume'].rolling(20).mean()
        features['volume_ratio'] = (df['Volume'] / avg_volume).iloc[-1]
        features['volume_spike'] = features['volume_ratio'] > self.volume_threshold_multiplier
        
        # Delta approximation (buy vs sell pressure)
        # Using close position within bar as proxy
        df['delta_proxy'] = (df['Close'] - df['Low']) / (df['High'] - df['Low'] + 0.0001)
        features['delta'] = df['delta_proxy'].iloc[-1]
        features['cumulative_delta'] = df['delta_proxy'].rolling(self.delta_lookback).sum().iloc[-1]
        
        # Absorption detection (high volume + small price move)
        price_range = (df['High'] - df['Low']).iloc[-1]
        avg_range = (df['High'] - df['Low']).rolling(20).mean().iloc[-1]
        features['absorption'] = (features['volume_ratio'] > 1.5) and (price_range < avg_range * 0.5)
        
        # Exhaustion detection (extreme move + declining volume)
        features['exhaustion'] = self._detect_exhaustion(df)
        
        # Stop hunt detection
        features['stop_hunt'] = self._detect_stop_hunt(df)
        
        # Liquidity grab
        features['liquidity_grab'] = self._detect_liquidity_grab(df)
        
        return features
    
    def _detect_exhaustion(self, df: pd.DataFrame) -> bool:
        """Detect exhaustion patterns (capitulation)"""
        if len(df) < 5:
            return False
            
        # Large move with declining volume
        price_change = abs(df['Close'].iloc[-1] - df['Close'].iloc[-5]) / df['Close'].iloc[-5]
        volume_declining = df['Volume'].iloc[-1] < df['Volume'].iloc[-3]
        
        return price_change > 0.01 and volume_declining
    
    def _detect_stop_hunt(self, df: pd.DataFrame) -> bool:
        """Detect stop hunt patterns"""
        if len(df) < 10:
            return False
            
        # Price breaks recent high/low then reverses
        recent_high = df['High'].iloc[-10:-1].max()
        recent_low = df['Low'].iloc[-10:-1].min()
        
        current_high = df['High'].iloc[-1]
        current_low = df['Low'].iloc[-1]
        current_close = df['Close'].iloc[-1]
        
        # Broke high but closed below
        high_hunt = (current_high > recent_high) and (current_close < recent_high)
        # Broke low but closed above
        low_hunt = (current_low < recent_low) and (current_close > recent_low)
        
        return high_hunt or low_hunt
    
    def _detect_liquidity_grab(self, df: pd.DataFrame) -> bool:
        """Detect liquidity grab patterns"""
        if len(df) < 3:
            return False
            
        # Large wick relative to body
        body = abs(df['Close'].iloc[-1] - df['Open'].iloc[-1])
        upper_wick = df['High'].iloc[-1] - max(df['Open'].iloc[-1], df['Close'].iloc[-1])
        lower_wick = min(df['Open'].iloc[-1], df['Close'].iloc[-1]) - df['Low'].iloc[-1]
        
        total_wick = upper_wick + lower_wick
        return total_wick > body * 2


class SPYTradingEnv(gym.Env):
    """
    Custom Gymnasium environment for SPY trading
    Incorporates order flow features
    """
    
    def __init__(self, df: pd.DataFrame, initial_balance: float = 100000):
        super().__init__()
        
        self.df = df.reset_index(drop=True)
        self.initial_balance = initial_balance
        self.order_flow = OrderFlowDetector()
        
        # Technical indicators
        self._add_indicators()
        
        # State: OHLCV + indicators + order flow features
        self.observation_space = spaces.Box(
            low=-np.inf, high=np.inf, shape=(25,), dtype=np.float32
        )
        
        # Actions: 0=Hold, 1=Buy, 2=Sell
        self.action_space = spaces.Discrete(3)
        
        self.reset()
    
    def _add_indicators(self):
        """Add technical indicators"""
        df = self.df
        
        # Trend
        df['sma_20'] = ta.trend.sma_indicator(df['Close'], window=20)
        df['sma_50'] = ta.trend.sma_indicator(df['Close'], window=50)
        df['ema_9'] = ta.trend.ema_indicator(df['Close'], window=9)
        
        # Momentum
        df['rsi'] = ta.momentum.rsi(df['Close'], window=14)
        df['rsi_2'] = ta.momentum.rsi(df['Close'], window=2)
        macd = ta.trend.MACD(df['Close'])
        df['macd'] = macd.macd()
        df['macd_signal'] = macd.macd_signal()
        
        # Volatility
        bb = ta.volatility.BollingerBands(df['Close'])
        df['bb_upper'] = bb.bollinger_hband()
        df['bb_lower'] = bb.bollinger_lband()
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
            row['Close'] / row['ema_9'] - 1,
            (row['Close'] - row['bb_lower']) / (row['bb_upper'] - row['bb_lower'] + 0.001),
            
            # Momentum
            row['rsi'] / 100,
            row['rsi_2'] / 100,
            row['macd'] / row['Close'] * 100,
            
            # Volatility
            row['atr'] / row['Close'],
            
            # Volume
            row['volume_ratio'],
            
            # Order flow features
            of_features.get('volume_ratio', 1.0),
            of_features.get('delta', 0.5),
            of_features.get('cumulative_delta', 0.0),
            float(of_features.get('absorption', False)),
            float(of_features.get('exhaustion', False)),
            float(of_features.get('stop_hunt', False)),
            float(of_features.get('liquidity_grab', False)),
            
            # Position info
            float(self.position),
            self.entry_price / row['Close'] if self.entry_price > 0 else 1.0,
            self.unrealized_pnl / self.initial_balance,
            self.balance / self.initial_balance,
            
            # Time features
            row.name % 390 / 390,  # Intraday position (390 min in trading day)
            
            # Recent returns
            row['Close'] / self.df.iloc[max(0, self.current_step-5)]['Close'] - 1,
            row['Close'] / self.df.iloc[max(0, self.current_step-10)]['Close'] - 1,
            row['Close'] / self.df.iloc[max(0, self.current_step-20)]['Close'] - 1,
            
            # Spread proxy
            (row['High'] - row['Low']) / row['Close'],
        ], dtype=np.float32)
        
        return obs
    
    def reset(self, seed=None):
        super().reset(seed=seed)
        
        self.current_step = 50  # Start after indicators are valid
        self.balance = self.initial_balance
        self.position = 0  # -1=short, 0=flat, 1=long
        self.entry_price = 0
        self.shares = 0
        self.unrealized_pnl = 0
        self.total_trades = 0
        self.winning_trades = 0
        
        return self._get_observation(), {}
    
    def step(self, action: int) -> Tuple[np.ndarray, float, bool, bool, Dict]:
        """Execute one step"""
        
        current_price = self.df.iloc[self.current_step]['Close']
        reward = 0
        
        # Execute action
        if action == 1 and self.position <= 0:  # Buy
            if self.position == -1:  # Close short
                pnl = (self.entry_price - current_price) * self.shares
                self.balance += pnl
                reward = pnl / self.initial_balance * 100
                if pnl > 0:
                    self.winning_trades += 1
                self.total_trades += 1
            
            # Open long
            self.shares = int(self.balance * 0.95 / current_price)
            self.entry_price = current_price
            self.position = 1
            
        elif action == 2 and self.position >= 0:  # Sell
            if self.position == 1:  # Close long
                pnl = (current_price - self.entry_price) * self.shares
                self.balance += pnl
                reward = pnl / self.initial_balance * 100
                if pnl > 0:
                    self.winning_trades += 1
                self.total_trades += 1
            
            # Open short
            self.shares = int(self.balance * 0.95 / current_price)
            self.entry_price = current_price
            self.position = -1
        
        # Calculate unrealized PnL
        if self.position == 1:
            self.unrealized_pnl = (current_price - self.entry_price) * self.shares
        elif self.position == -1:
            self.unrealized_pnl = (self.entry_price - current_price) * self.shares
        else:
            self.unrealized_pnl = 0
        
        # Move to next step
        self.current_step += 1
        
        # Check if done
        done = self.current_step >= len(self.df) - 1
        truncated = False
        
        # Small reward for holding profitable position
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


class SPYHunterTrainer:
    """
    Train the SPY Anti-MM agent using PPO on H100
    """
    
    def __init__(self, model_dir: str = "/home/jbot/trading_ai/spy_hunter/models"):
        self.model_dir = model_dir
        os.makedirs(model_dir, exist_ok=True)
        
        self.device = DEVICE
        logger.info(f"Trainer initialized on {self.device}")
        
    def fetch_spy_data(self, days: int = 60) -> pd.DataFrame:
        """Fetch SPY data for training"""
        end = datetime.now()
        start = end - timedelta(days=days)
        
        spy = yf.Ticker("SPY")
        df = spy.history(start=start, end=end, interval="5m")
        
        df = df.reset_index()
        # Keep only OHLCV columns
        df = df[['Open', 'High', 'Low', 'Close', 'Volume']].copy()
        df = df.reset_index(drop=True)
        
        logger.info(f"Fetched {len(df)} bars of SPY 5m data")
        return df
    
    def train(self, total_timesteps: int = 100000):
        """Train the agent"""
        from stable_baselines3 import PPO
        from stable_baselines3.common.vec_env import DummyVecEnv
        
        # Fetch data
        df = self.fetch_spy_data(days=30)
        
        # Create environment
        env = DummyVecEnv([lambda: SPYTradingEnv(df)])
        
        # Create model with GPU - RAMPED UP FOR H100
        model = PPO(
            "MlpPolicy",
            env,
            verbose=1,
            learning_rate=3e-4,
            n_steps=4096,      # 2x larger rollout buffer
            batch_size=256,    # 4x larger batches for H100
            n_epochs=20,       # 2x more epochs per update
            gamma=0.99,
            gae_lambda=0.95,
            clip_range=0.2,
            ent_coef=0.01,     # Encourage exploration
            device=self.device,
            tensorboard_log=f"{self.model_dir}/logs"
        )
        
        # Train
        logger.info(f"Starting training for {total_timesteps} timesteps...")
        model.learn(total_timesteps=total_timesteps)
        
        # Save model
        model_path = f"{self.model_dir}/spy_hunter_ppo"
        model.save(model_path)
        logger.info(f"Model saved to {model_path}")
        
        return model
    
    def continuous_train(self, interval_hours: int = 4):
        """Continuous training loop"""
        from stable_baselines3 import PPO
        
        model_path = f"{self.model_dir}/spy_hunter_ppo"
        
        while True:
            logger.info("="*60)
            logger.info(f"CONTINUOUS TRAINING CYCLE - {datetime.now()}")
            logger.info("="*60)
            
            # Fetch fresh data
            df = self.fetch_spy_data(days=14)
            
            # Create environment
            env = SPYTradingEnv(df)
            
            # Load or create model
            if os.path.exists(f"{model_path}.zip"):
                model = PPO.load(model_path, env=env, device=self.device)
                logger.info("Loaded existing model")
            else:
                model = PPO(
                    "MlpPolicy", env, verbose=1,
                    device=self.device,
                    tensorboard_log=f"{self.model_dir}/logs"
                )
                logger.info("Created new model")
            
            # Train
            model.learn(total_timesteps=50000)
            model.save(model_path)
            
            # Evaluate
            self._evaluate(model, df)
            
            # Export signal for MT5
            self._export_signal(model, df)
            
            # Wait
            logger.info(f"Sleeping {interval_hours} hours until next training cycle...")
            import time
            time.sleep(interval_hours * 3600)
    
    def _evaluate(self, model, df: pd.DataFrame):
        """Evaluate model performance"""
        env = SPYTradingEnv(df)
        obs, _ = env.reset()
        
        total_reward = 0
        done = False
        
        while not done:
            action, _ = model.predict(obs, deterministic=True)
            obs, reward, done, _, info = env.step(action)
            total_reward += reward
        
        logger.info(f"Evaluation Results:")
        logger.info(f"  Total Reward: {total_reward:.2f}")
        logger.info(f"  Final Balance: ${info['total_value']:,.2f}")
        logger.info(f"  Total Trades: {info['total_trades']}")
        logger.info(f"  Win Rate: {info['win_rate']*100:.1f}%")
    
    def _export_signal(self, model, df: pd.DataFrame):
        """Export current signal for MT5"""
        env = SPYTradingEnv(df)
        obs = env._get_observation()
        
        action, _ = model.predict(obs, deterministic=True)
        
        signal = {
            "timestamp": datetime.now().isoformat(),
            "symbol": "SPY",
            "action": ["HOLD", "BUY", "SELL"][action],
            "price": float(df.iloc[-1]['Close']),
            "confidence": 75,  # TODO: Calculate from model
            "source": "SPY_HUNTER_RL"
        }
        
        signal_path = "/home/jbot/trading_ai/spy_hunter/current_signal.json"
        with open(signal_path, 'w') as f:
            json.dump(signal, f, indent=2)
        
        logger.info(f"Signal exported: {signal['action']} @ ${signal['price']:.2f}")


if __name__ == "__main__":
    import sys
    
    trainer = SPYHunterTrainer()
    
    if len(sys.argv) > 1 and sys.argv[1] == "continuous":
        trainer.continuous_train()
    else:
        # Single training run
        trainer.train(total_timesteps=100000)
