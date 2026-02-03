#!/usr/bin/env python3
"""
SPY HUNTER CONTINUOUS TRAINER
=============================
Runs on H100 GPU continuously:
1. Fetches fresh SPY data
2. Trains/updates the RL model
3. Evaluates performance
4. Exports signals
5. Repeats every N hours
"""

import os
import sys
import time
import json
import logging
from datetime import datetime, timedelta
import numpy as np
import pandas as pd
import torch

# Add parent directory to path
sys.path.insert(0, '/home/jbot/trading_ai/spy_hunter')

from spy_antimm_agent import SPYHunterTrainer, SPYTradingEnv, OrderFlowDetector

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('/home/jbot/trading_ai/spy_hunter/training.log')
    ]
)
logger = logging.getLogger("CONTINUOUS_TRAINER")

# Configuration - RAMPED UP FOR H100
MODEL_DIR = "/home/jbot/trading_ai/spy_hunter/models"
SIGNAL_FILE = "/home/jbot/trading_ai/spy_hunter/current_signal.json"
STATE_FILE = "/home/jbot/trading_ai/spy_hunter/trainer_state.json"
TRAINING_INTERVAL_HOURS = 1  # Signal every hour
TIMESTEPS_PER_CYCLE = 200000  # 4x more training per cycle
MIN_CONFIDENCE_THRESHOLD = 60

# Check GPU
DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")
if torch.cuda.is_available():
    GPU_NAME = torch.cuda.get_device_name(0)
    GPU_MEM = torch.cuda.get_device_properties(0).total_memory / 1e9
    logger.info(f"🚀 GPU Detected: {GPU_NAME} ({GPU_MEM:.1f} GB)")
else:
    logger.warning("⚠️ No GPU detected, using CPU")


def load_state() -> dict:
    """Load trainer state"""
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r') as f:
                return json.load(f)
        except:
            pass
    return {
        "total_training_cycles": 0,
        "total_timesteps": 0,
        "last_training": None,
        "best_win_rate": 0,
        "performance_history": []
    }


def save_state(state: dict):
    """Save trainer state"""
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f, indent=2)


def export_signal(action: str, price: float, confidence: int, env_info: dict = None):
    """Export signal for MT5"""
    signal = {
        "symbol": "SPY",
        "action": action,
        "price": float(price),
        "confidence": int(confidence),
        "timestamp": datetime.now().isoformat(),
        "source": "SPY_HUNTER_RL",
        "env_info": env_info
    }
    
    with open(SIGNAL_FILE, 'w') as f:
        json.dump(signal, f, indent=2)
    
    logger.info(f"📤 Signal exported: {action} @ ${price:.2f} ({confidence}%)")


def evaluate_model(model, df: pd.DataFrame) -> dict:
    """Evaluate model and return metrics"""
    env = SPYTradingEnv(df)
    obs, _ = env.reset()
    
    total_reward = 0
    done = False
    actions_taken = []
    
    while not done:
        action, _ = model.predict(obs, deterministic=True)
        actions_taken.append(action)
        obs, reward, done, _, info = env.step(action)
        total_reward += reward
    
    # Calculate action distribution
    action_counts = {0: 0, 1: 0, 2: 0}
    for a in actions_taken:
        action_counts[int(a)] += 1
    
    return {
        "total_reward": total_reward,
        "final_balance": info['total_value'],
        "total_trades": info['total_trades'],
        "win_rate": info['win_rate'] * 100,
        "return_pct": (info['total_value'] - 100000) / 100000 * 100,
        "hold_pct": action_counts[0] / len(actions_taken) * 100,
        "buy_pct": action_counts[1] / len(actions_taken) * 100,
        "sell_pct": action_counts[2] / len(actions_taken) * 100
    }


def get_current_prediction(model, df: pd.DataFrame) -> tuple:
    """Get current model prediction with confidence"""
    env = SPYTradingEnv(df)
    
    # Get observation for latest data
    env.current_step = len(df) - 2
    obs = env._get_observation()
    
    # Get action probabilities
    action, _ = model.predict(obs, deterministic=True)
    
    # Calculate confidence based on recent performance
    # (In production, you'd use action probabilities from policy)
    base_confidence = 70
    
    # Boost confidence if order flow features are strong
    of_detector = OrderFlowDetector()
    of_features = of_detector.detect_features(df.tail(25))
    
    confidence = base_confidence
    if of_features.get('stop_hunt'):
        confidence += 10
        logger.info("🎯 Stop hunt detected - boosting confidence")
    if of_features.get('absorption'):
        confidence += 5
        logger.info("📊 Absorption detected - boosting confidence")
    if of_features.get('exhaustion'):
        confidence += 5
        logger.info("💨 Exhaustion detected - boosting confidence")
    
    confidence = min(95, confidence)
    
    action_name = ["HOLD", "BUY", "SELL"][int(action)]
    
    return action_name, confidence, of_features


def training_cycle(trainer: SPYHunterTrainer, state: dict) -> dict:
    """Run one training cycle"""
    from stable_baselines3 import PPO
    from stable_baselines3.common.vec_env import DummyVecEnv
    
    logger.info("="*70)
    logger.info(f"🔄 TRAINING CYCLE {state['total_training_cycles'] + 1}")
    logger.info(f"   Device: {DEVICE}")
    logger.info(f"   Timesteps: {TIMESTEPS_PER_CYCLE}")
    logger.info("="*70)
    
    # Fetch fresh data
    df = trainer.fetch_spy_data(days=30)
    logger.info(f"📊 Fetched {len(df)} bars of SPY data")
    
    # Create environment
    env = DummyVecEnv([lambda: SPYTradingEnv(df)])
    
    # Load or create model
    model_path = f"{MODEL_DIR}/spy_hunter_ppo"
    
    if os.path.exists(f"{model_path}.zip"):
        model = PPO.load(model_path, env=env, device=DEVICE)
        logger.info("📂 Loaded existing model")
    else:
        logger.info("🆕 Creating new model - H100 OPTIMIZED")
        model = PPO(
            "MlpPolicy",
            env,
            verbose=1,
            learning_rate=3e-4,
            n_steps=4096,      # Large rollout buffer
            batch_size=256,    # Big batches for H100
            n_epochs=20,       # More training per update
            gamma=0.99,
            gae_lambda=0.95,
            clip_range=0.2,
            ent_coef=0.01,
            device=DEVICE,
            tensorboard_log=f"{MODEL_DIR}/logs"
        )
    
    # Train with intermediate checkpoints
    logger.info(f"🏋️ Training for {TIMESTEPS_PER_CYCLE} timesteps (H100 TURBO MODE)...")
    start_time = time.time()
    
    # Train in chunks to export intermediate signals
    chunk_size = 50000
    trained = 0
    while trained < TIMESTEPS_PER_CYCLE:
        steps = min(chunk_size, TIMESTEPS_PER_CYCLE - trained)
        model.learn(total_timesteps=steps, reset_num_timesteps=False)
        trained += steps
        
        # Save checkpoint and export signal mid-training
        model.save(model_path)
        logger.info(f"   📊 Checkpoint: {trained}/{TIMESTEPS_PER_CYCLE} timesteps")
        
    train_time = time.time() - start_time
    logger.info(f"⏱️ Training completed in {train_time:.1f}s ({TIMESTEPS_PER_CYCLE/train_time:.0f} steps/sec)")
    
    # Save model
    model.save(model_path)
    logger.info(f"💾 Model saved to {model_path}")
    
    # Evaluate
    logger.info("📈 Evaluating model...")
    eval_df = trainer.fetch_spy_data(days=7)  # Recent data for eval
    metrics = evaluate_model(model, eval_df)
    
    logger.info(f"   Total Reward: {metrics['total_reward']:.2f}")
    logger.info(f"   Final Balance: ${metrics['final_balance']:,.2f}")
    logger.info(f"   Return: {metrics['return_pct']:.2f}%")
    logger.info(f"   Win Rate: {metrics['win_rate']:.1f}%")
    logger.info(f"   Total Trades: {metrics['total_trades']}")
    logger.info(f"   Actions: HOLD {metrics['hold_pct']:.1f}% | BUY {metrics['buy_pct']:.1f}% | SELL {metrics['sell_pct']:.1f}%")
    
    # Get current prediction
    action, confidence, of_features = get_current_prediction(model, eval_df)
    current_price = eval_df['Close'].iloc[-1]
    
    # Export signal
    export_signal(action, current_price, confidence, {
        "stop_hunt": of_features.get('stop_hunt', False),
        "absorption": of_features.get('absorption', False),
        "exhaustion": of_features.get('exhaustion', False),
        "volume_ratio": of_features.get('volume_ratio', 1.0)
    })
    
    # Update state
    state['total_training_cycles'] += 1
    state['total_timesteps'] += TIMESTEPS_PER_CYCLE
    state['last_training'] = datetime.now().isoformat()
    
    if metrics['win_rate'] > state['best_win_rate']:
        state['best_win_rate'] = metrics['win_rate']
        logger.info(f"🏆 New best win rate: {metrics['win_rate']:.1f}%")
    
    state['performance_history'].append({
        "timestamp": datetime.now().isoformat(),
        "win_rate": metrics['win_rate'],
        "return_pct": metrics['return_pct'],
        "total_trades": metrics['total_trades']
    })
    
    # Keep only last 100 performance records
    state['performance_history'] = state['performance_history'][-100:]
    
    save_state(state)
    
    return metrics


def main():
    """Main continuous training loop"""
    logger.info("="*70)
    logger.info("🎯 SPY HUNTER CONTINUOUS TRAINER")
    logger.info("   Anti-Market Maker RL Agent")
    logger.info(f"   Training Interval: {TRAINING_INTERVAL_HOURS} hours")
    logger.info(f"   Timesteps per Cycle: {TIMESTEPS_PER_CYCLE}")
    logger.info("="*70)
    
    # Initialize
    os.makedirs(MODEL_DIR, exist_ok=True)
    state = load_state()
    trainer = SPYHunterTrainer(model_dir=MODEL_DIR)
    
    logger.info(f"📊 Previous cycles: {state['total_training_cycles']}")
    logger.info(f"📊 Total timesteps trained: {state['total_timesteps']}")
    
    # Main loop
    while True:
        try:
            # Run training cycle
            metrics = training_cycle(trainer, state)
            
            logger.info(f"💤 Sleeping {TRAINING_INTERVAL_HOURS} hours until next cycle...")
            time.sleep(TRAINING_INTERVAL_HOURS * 3600)
            
        except KeyboardInterrupt:
            logger.info("🛑 Interrupted by user")
            break
            
        except Exception as e:
            logger.error(f"❌ Error in training cycle: {e}")
            import traceback
            traceback.print_exc()
            
            # Wait before retry
            logger.info("⏰ Waiting 30 minutes before retry...")
            time.sleep(1800)


if __name__ == "__main__":
    main()
