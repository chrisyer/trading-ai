"""
RL Position Sizer API — Serves DCA multiplier and lot scaling decisions.
Port 8041. Called by bridge governor on CRELLA with max() overlay.
"""

import os
import logging
import numpy as np
from pathlib import Path
from datetime import datetime, timezone
from fastapi import FastAPI
from pydantic import BaseModel
from stable_baselines3 import PPO

logging.basicConfig(level=logging.INFO, format="%(asctime)s [SIZE-API] %(message)s")
log = logging.getLogger(__name__)

ML_DIR = Path(__file__).resolve().parents[1]
MODEL_PATH = ML_DIR / "models" / "rl_position_sizer.zip"

app = FastAPI(title="RL Position Sizer", version="1.0")

model = None
model_meta = {}

SENTIMENT_MAP = {"neutral": 0, "mild_bullish": 1, "mild_bearish": -1,
                 "panic_bullish": 2, "panic_bearish": -2, "unknown": 0}
RISK_MAP = {"low": 0, "medium": 1, "high": 2, "unknown": 0}


class SizeRequest(BaseModel):
    atr: float
    atr_pctl: float = 0.5
    layers: int = 0
    direction: int = 0
    spread_points: float = 0
    equity: float = 400000
    net_lots: float = 0
    intensity: float = 0.3
    sentiment_regime: str = "neutral"
    event_risk: str = "low"


class SizeResponse(BaseModel):
    dca_multiplier: float
    lot_scale: float
    confidence: str
    model_version: str
    timestamp: str


def load_model():
    global model, model_meta

    if not MODEL_PATH.exists():
        log.warning(f"No model at {MODEL_PATH} — serving defaults until trained")
        return False

    model = PPO.load(str(MODEL_PATH), device="cuda" if __import__("torch").cuda.is_available() else "cpu")

    import json
    meta_path = ML_DIR / "models" / "rl_position_sizer_meta.json"
    if meta_path.exists():
        with open(meta_path) as f:
            model_meta = json.load(f)

    log.info(f"RL model loaded: {model_meta.get('n_baskets', '?')} baskets, {model_meta.get('total_timesteps', '?')} steps")
    return True


@app.on_event("startup")
async def startup():
    load_model()
    log.info("RL Position Sizer API ready on port 8041")


@app.get("/health")
async def health():
    return {
        "status": "loaded" if model is not None else "no_model",
        "model_path": str(MODEL_PATH),
        "model_meta": model_meta,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.post("/predict", response_model=SizeResponse)
async def predict(req: SizeRequest):
    obs = np.array([
        req.atr / 100.0,
        req.atr_pctl,
        req.layers / 5.0,
        req.direction,
        req.spread_points / 50.0,
        req.equity / 100000.0,
        req.net_lots / 5.0,
        req.intensity,
        SENTIMENT_MAP.get(req.sentiment_regime, 0),
        RISK_MAP.get(req.event_risk, 0),
    ], dtype=np.float32)

    if model is None:
        return SizeResponse(
            dca_multiplier=1.0,
            lot_scale=1.0,
            confidence="no_model",
            model_version="default",
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    action, _ = model.predict(obs, deterministic=True)
    dca_mult = float(np.clip(action[0], 0.5, 2.0))
    lot_scale = float(np.clip(action[1], 0.3, 1.5))

    confidence = "high" if abs(action[0] - 1.0) > 0.3 else "medium"

    return SizeResponse(
        dca_multiplier=round(dca_mult, 3),
        lot_scale=round(lot_scale, 3),
        confidence=confidence,
        model_version=model_meta.get("trained_at", "default"),
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


@app.post("/reload")
async def reload():
    success = load_model()
    return {"reloaded": success, "model_meta": model_meta}
