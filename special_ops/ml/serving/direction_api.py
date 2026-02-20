"""
Direction Predictor API — Serves XAUUSD price direction predictions.
Port 8040. Called by Ghost Commander on CRELLA.
"""

import os
import logging
import numpy as np
import torch
from pathlib import Path
from datetime import datetime, timezone
from fastapi import FastAPI
from pydantic import BaseModel
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s [DIR-API] %(message)s")
log = logging.getLogger(__name__)

ML_DIR = Path(__file__).resolve().parents[1]
MODEL_PATH = ML_DIR / "models" / "direction_predictor.pt"

app = FastAPI(title="XAUUSD Direction Predictor", version="1.0")

model = None
scaler_mean = None
scaler_scale = None
model_meta = {}
sequence_buffer = []
SEQUENCE_LEN = 32


class PredictRequest(BaseModel):
    atr: float
    atr_pctl: float = 0.5
    layers: int = 0
    direction: int = 0
    spread_points: float = 0
    equity: float = 400000
    net_lots: float = 0
    intensity: float = 0.3
    dca_mult: float = 1.0
    layer_cap: int = 3
    pnl: float = 0
    dd: float = 0
    duration_min: float = 0


class PredictResponse(BaseModel):
    direction: str
    confidence: float
    raw_probs: dict
    model_version: str
    timestamp: str
    sequence_len: int


def load_model():
    global model, scaler_mean, scaler_scale, model_meta

    if not MODEL_PATH.exists():
        log.warning(f"No model at {MODEL_PATH} — serving random until trained")
        return False

    import sys
    if str(ML_DIR) not in sys.path:
        sys.path.insert(0, str(ML_DIR))
    from models_arch import DirectionPredictor

    checkpoint = torch.load(MODEL_PATH, map_location="cuda" if torch.cuda.is_available() else "cpu",
                            weights_only=False)

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = DirectionPredictor(
        input_dim=checkpoint.get("num_features", 13),
        hidden_dim=checkpoint.get("hidden_dim", 128),
    ).to(device)
    model.load_state_dict(checkpoint["model_state_dict"])
    model.eval()

    scaler_mean = np.array(checkpoint["scaler_mean"])
    scaler_scale = np.array(checkpoint["scaler_scale"])
    model_meta = {
        "best_val_acc": checkpoint.get("best_val_acc", 0),
        "epoch": checkpoint.get("epoch", 0),
        "trained_at": checkpoint.get("trained_at", "unknown"),
    }

    log.info(f"Model loaded: val_acc={model_meta['best_val_acc']:.3f}, epoch={model_meta['epoch']}")
    return True


@app.on_event("startup")
async def startup():
    load_model()
    log.info("Direction Predictor API ready on port 8040")


@app.get("/health")
async def health():
    return {
        "status": "loaded" if model is not None else "no_model",
        "model_path": str(MODEL_PATH),
        "model_meta": model_meta,
        "sequence_buffer_len": len(sequence_buffer),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.post("/predict", response_model=PredictResponse)
async def predict(req: PredictRequest):
    features = np.array([
        req.atr, req.atr_pctl, req.layers, req.direction, req.spread_points,
        req.equity / 100000.0, req.net_lots, req.intensity, req.dca_mult,
        req.layer_cap, req.pnl / 10000.0, req.dd, req.duration_min / 60.0,
    ], dtype=np.float32)

    sequence_buffer.append(features)
    if len(sequence_buffer) > SEQUENCE_LEN * 2:
        del sequence_buffer[:len(sequence_buffer) - SEQUENCE_LEN]

    if model is None or scaler_mean is None:
        return PredictResponse(
            direction="HOLD",
            confidence=0.5,
            raw_probs={"DOWN": 0.5, "UP": 0.5},
            model_version="no_model",
            timestamp=datetime.now(timezone.utc).isoformat(),
            sequence_len=len(sequence_buffer),
        )

    if len(sequence_buffer) < SEQUENCE_LEN:
        padded = [np.zeros_like(features)] * (SEQUENCE_LEN - len(sequence_buffer)) + list(sequence_buffer)
    else:
        padded = list(sequence_buffer[-SEQUENCE_LEN:])

    seq = np.array(padded)
    seq = (seq - scaler_mean) / np.clip(scaler_scale, 1e-8, None)

    device = next(model.parameters()).device
    with torch.no_grad():
        x = torch.FloatTensor(seq).unsqueeze(0).to(device)
        logits = model(x)
        probs = torch.softmax(logits, dim=1).cpu().numpy()[0]

    direction = "UP" if probs[1] > probs[0] else "DOWN"
    confidence = float(max(probs))

    return PredictResponse(
        direction=direction,
        confidence=round(confidence, 4),
        raw_probs={"DOWN": round(float(probs[0]), 4), "UP": round(float(probs[1]), 4)},
        model_version=model_meta.get("trained_at", "unknown"),
        timestamp=datetime.now(timezone.utc).isoformat(),
        sequence_len=len(sequence_buffer),
    )


@app.post("/reload")
async def reload():
    success = load_model()
    return {"reloaded": success, "model_meta": model_meta}
