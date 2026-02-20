"""
RL Position Sizer — PPO agent that learns optimal DCA spacing and lot sizing.
Trained on CRELLA basket replay history.
State: market conditions (ATR, spread, layers, sentiment, equity, direction)
Action: dca_multiplier (continuous 0.5-2.0), lot_scale (continuous 0.3-1.5)
Reward: risk-adjusted P&L (Sharpe-like: pnl / max(dd, 0.5))
"""

import os
import sys
import json
import logging
import numpy as np
import gymnasium as gym
from gymnasium import spaces
from pathlib import Path
from datetime import datetime
from stable_baselines3 import PPO
from stable_baselines3.common.vec_env import DummyVecEnv
from stable_baselines3.common.callbacks import EvalCallback
import duckdb

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [TRAIN-RL] %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

ML_DIR = Path(__file__).resolve().parents[1]
DB_PATH = ML_DIR / "data" / "trading.duckdb"
MODEL_DIR = ML_DIR / "models"

DEVICE = "cuda"


class BasketReplayEnv(gym.Env):
    """
    Replays basket outcomes from historical data.
    Agent decides DCA multiplier and lot scaling for each basket.
    Reward is risk-adjusted: pnl / max(drawdown, 0.5) with penalties.
    """

    metadata = {"render_modes": []}

    def __init__(self, baskets):
        super().__init__()
        self.baskets = baskets
        self.idx = 0

        self.observation_space = spaces.Box(
            low=-5.0, high=5.0, shape=(10,), dtype=np.float32
        )
        # [dca_multiplier, lot_scale]
        self.action_space = spaces.Box(
            low=np.array([0.5, 0.3]),
            high=np.array([2.0, 1.5]),
            dtype=np.float32,
        )

    def _get_obs(self):
        b = self.baskets[self.idx]
        return np.array([
            b["atr"] / 100.0,
            b["atr_pctl"],
            b["layers"] / 5.0,
            b["direction"],
            b["spread_points"] / 50.0,
            b["equity_norm"],
            b["net_lots"] / 5.0,
            b["intensity"],
            b["sentiment_code"],
            b["event_risk_code"],
        ], dtype=np.float32)

    def reset(self, seed=None, options=None):
        super().reset(seed=seed)
        self.idx = np.random.randint(0, len(self.baskets))
        return self._get_obs(), {}

    def step(self, action):
        dca_mult = float(np.clip(action[0], 0.5, 2.0))
        lot_scale = float(np.clip(action[1], 0.3, 1.5))

        b = self.baskets[self.idx]
        base_pnl = b["pnl"]
        base_dd = b["dd"]

        adjusted_pnl = base_pnl * lot_scale
        adjusted_dd = base_dd * lot_scale

        risk_denom = max(adjusted_dd, 0.5)
        reward = adjusted_pnl / risk_denom / 1000.0

        if dca_mult > 1.5 and base_dd > 2.0:
            reward -= 0.5
        if lot_scale > 1.0 and b["atr_pctl"] > 0.8:
            reward -= 0.3

        if adjusted_pnl > 0 and adjusted_dd < 1.0:
            reward += 0.2

        self.idx = (self.idx + 1) % len(self.baskets)
        done = self.idx == 0
        truncated = False

        return self._get_obs(), reward, done, truncated, {
            "dca_mult": dca_mult,
            "lot_scale": lot_scale,
            "pnl": adjusted_pnl,
            "dd": adjusted_dd,
        }


SENTIMENT_MAP = {"neutral": 0, "mild_bullish": 1, "mild_bearish": -1,
                 "panic_bullish": 2, "panic_bearish": -2, "unknown": 0}
RISK_MAP = {"low": 0, "medium": 1, "high": 2, "unknown": 0}


def load_baskets():
    """Load basket outcomes from DuckDB."""
    if not DB_PATH.exists():
        log.error(f"DuckDB not found. Run data ingest first.")
        sys.exit(1)

    con = duckdb.connect(str(DB_PATH), read_only=True)
    rows = con.execute("""
        SELECT
            basket_id, atr, atr_pctl, layers, direction, spread_points,
            equity / 100000.0 as equity_norm, net_lots, intensity,
            sentiment_regime, event_risk,
            control_dca_mult, control_layer_cap,
            pnl, dd, duration_min, max_layers
        FROM basket_outcomes
        WHERE pnl IS NOT NULL AND basket_id > 0
        ORDER BY ts ASC
    """).fetchall()
    con.close()

    baskets = []
    for r in rows:
        baskets.append({
            "basket_id": r[0], "atr": r[1], "atr_pctl": r[2],
            "layers": r[3], "direction": r[4], "spread_points": r[5],
            "equity_norm": r[6], "net_lots": r[7], "intensity": r[8],
            "sentiment_code": SENTIMENT_MAP.get(r[9], 0),
            "event_risk_code": RISK_MAP.get(r[10], 0),
            "orig_dca_mult": r[11], "orig_layer_cap": r[12],
            "pnl": r[13], "dd": r[14], "duration_min": r[15],
            "max_layers": r[16],
        })

    log.info(f"Loaded {len(baskets)} unique baskets for RL training")
    return baskets


def train():
    log.info("=" * 60)
    log.info("RL Position Sizer Training — PPO")
    log.info(f"  Device:  {DEVICE}")
    log.info("=" * 60)

    baskets = load_baskets()
    if len(baskets) < 10:
        log.warning(f"Only {len(baskets)} baskets — RL training may be unreliable")

    split = int(len(baskets) * 0.8)
    train_baskets = baskets[:split]
    eval_baskets = baskets[split:] if split < len(baskets) else baskets

    env = DummyVecEnv([lambda: BasketReplayEnv(train_baskets)])
    eval_env = DummyVecEnv([lambda: BasketReplayEnv(eval_baskets)])

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    model_path = MODEL_DIR / "rl_position_sizer"

    model = PPO(
        "MlpPolicy", env,
        learning_rate=3e-4,
        n_steps=2048,
        batch_size=64,
        n_epochs=10,
        gamma=0.99,
        gae_lambda=0.95,
        clip_range=0.2,
        ent_coef=0.01,
        verbose=1,
        device=DEVICE,
        tensorboard_log=str(ML_DIR / "checkpoints" / "rl_logs"),
    )

    total_timesteps = max(50000, len(train_baskets) * 100)
    log.info(f"Training for {total_timesteps} timesteps on {len(train_baskets)} baskets")

    eval_callback = EvalCallback(
        eval_env,
        best_model_save_path=str(MODEL_DIR),
        eval_freq=5000,
        n_eval_episodes=min(20, len(eval_baskets)),
        deterministic=True,
    )

    model.learn(total_timesteps=total_timesteps, callback=eval_callback)

    model.save(str(model_path))
    log.info(f"Model saved to {model_path}.zip")

    meta = {
        "trained_at": datetime.utcnow().isoformat(),
        "total_timesteps": total_timesteps,
        "n_baskets": len(baskets),
        "device": DEVICE,
        "action_space": {"dca_mult": [0.5, 2.0], "lot_scale": [0.3, 1.5]},
    }
    with open(MODEL_DIR / "rl_position_sizer_meta.json", "w") as f:
        json.dump(meta, f, indent=2)

    log.info("RL training complete")


if __name__ == "__main__":
    train()
