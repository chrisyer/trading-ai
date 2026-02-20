"""
Direction Predictor — LSTM+Attention model for XAUUSD 1-hour price direction.
Trains on CRELLA basket outcomes and NEO prediction history from DuckDB.
Outputs: direction (UP/DOWN) + confidence score.
"""

import os
import sys
import json
import logging
import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import Dataset, DataLoader
from pathlib import Path
from datetime import datetime
from sklearn.preprocessing import StandardScaler
import duckdb

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [TRAIN-DIR] %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

ML_DIR = Path(__file__).resolve().parents[1]
DB_PATH = ML_DIR / "data" / "trading.duckdb"
MODEL_DIR = ML_DIR / "models"
CHECKPOINT_DIR = ML_DIR / "checkpoints"

sys.path.insert(0, str(ML_DIR))
from models_arch import DirectionPredictor, SEQUENCE_LEN, NUM_FEATURES, HIDDEN_DIM

DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")

FEATURES = [
    "atr", "atr_pctl", "layers", "direction", "spread_points",
    "equity_norm", "net_lots", "intensity", "dca_mult",
    "layer_cap", "pnl_norm", "dd", "duration_norm",
]
LEARNING_RATE = 1e-3
EPOCHS = 50
BATCH_SIZE = 64


class TradeSequenceDataset(Dataset):
    def __init__(self, sequences, labels):
        self.sequences = torch.FloatTensor(sequences)
        self.labels = torch.LongTensor(labels)

    def __len__(self):
        return len(self.labels)

    def __getitem__(self, idx):
        return self.sequences[idx], self.labels[idx]


def load_training_data():
    """Load and prepare sequences from DuckDB basket outcomes."""
    if not DB_PATH.exists():
        log.error(f"DuckDB not found at {DB_PATH}. Run data ingest first.")
        sys.exit(1)

    con = duckdb.connect(str(DB_PATH), read_only=True)

    df = con.execute("""
        SELECT
            atr, atr_pctl, layers, direction, spread_points,
            equity / 100000.0 as equity_norm,
            net_lots,
            intensity,
            control_dca_mult as dca_mult,
            control_layer_cap as layer_cap,
            pnl / 10000.0 as pnl_norm,
            dd,
            duration_min / 60.0 as duration_norm
        FROM basket_outcomes
        WHERE pnl IS NOT NULL
        ORDER BY ts ASC
    """).fetchnumpy()
    con.close()

    features = np.column_stack([df[k] for k in [
        "atr", "atr_pctl", "layers", "direction", "spread_points",
        "equity_norm", "net_lots", "intensity", "dca_mult",
        "layer_cap", "pnl_norm", "dd", "duration_norm",
    ]])

    labels_raw = (df["pnl_norm"] > 0).astype(int)

    features = np.nan_to_num(features, nan=0.0)

    scaler = StandardScaler()
    features = scaler.fit_transform(features)

    sequences = []
    labels = []
    for i in range(len(features) - SEQUENCE_LEN):
        sequences.append(features[i:i + SEQUENCE_LEN])
        labels.append(labels_raw[i + SEQUENCE_LEN])

    log.info(f"Created {len(sequences)} sequences from {len(features)} records")
    log.info(f"Label distribution: UP={sum(labels)}, DOWN={len(labels) - sum(labels)}")

    return np.array(sequences), np.array(labels), scaler


def train():
    log.info("=" * 60)
    log.info("Direction Predictor Training — LSTM+Attention")
    log.info(f"  Device:     {DEVICE}")
    log.info(f"  Sequence:   {SEQUENCE_LEN} steps")
    log.info(f"  Features:   {NUM_FEATURES}")
    log.info(f"  Hidden:     {HIDDEN_DIM}")
    log.info(f"  Epochs:     {EPOCHS}")
    log.info("=" * 60)

    sequences, labels, scaler = load_training_data()

    if len(sequences) < 100:
        log.warning(f"Only {len(sequences)} sequences — training may be unreliable")

    split = int(len(sequences) * 0.8)
    train_ds = TradeSequenceDataset(sequences[:split], labels[:split])
    val_ds = TradeSequenceDataset(sequences[split:], labels[split:])

    train_loader = DataLoader(train_ds, batch_size=BATCH_SIZE, shuffle=True)
    val_loader = DataLoader(val_ds, batch_size=BATCH_SIZE)

    model = DirectionPredictor().to(DEVICE)
    optimizer = torch.optim.AdamW(model.parameters(), lr=LEARNING_RATE, weight_decay=1e-4)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=EPOCHS)

    class_counts = np.bincount(labels)
    weights = torch.FloatTensor([1.0 / max(c, 1) for c in class_counts]).to(DEVICE)
    criterion = nn.CrossEntropyLoss(weight=weights)

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)

    best_val_acc = 0
    patience = 10
    no_improve = 0

    for epoch in range(EPOCHS):
        model.train()
        train_loss = 0
        correct = 0
        total = 0

        for X, y in train_loader:
            X, y = X.to(DEVICE), y.to(DEVICE)
            optimizer.zero_grad()
            logits = model(X)
            loss = criterion(logits, y)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()

            train_loss += loss.item()
            correct += (logits.argmax(1) == y).sum().item()
            total += len(y)

        scheduler.step()

        model.eval()
        val_correct = 0
        val_total = 0
        with torch.no_grad():
            for X, y in val_loader:
                X, y = X.to(DEVICE), y.to(DEVICE)
                logits = model(X)
                val_correct += (logits.argmax(1) == y).sum().item()
                val_total += len(y)

        train_acc = correct / max(total, 1)
        val_acc = val_correct / max(val_total, 1)

        if (epoch + 1) % 5 == 0 or epoch == 0:
            log.info(
                f"Epoch {epoch+1:3d}/{EPOCHS} | "
                f"Loss: {train_loss/len(train_loader):.4f} | "
                f"Train Acc: {train_acc:.3f} | Val Acc: {val_acc:.3f}"
            )

        if val_acc > best_val_acc:
            best_val_acc = val_acc
            no_improve = 0
            torch.save({
                "model_state_dict": model.state_dict(),
                "scaler_mean": scaler.mean_.tolist(),
                "scaler_scale": scaler.scale_.tolist(),
                "features": FEATURES,
                "sequence_len": SEQUENCE_LEN,
                "hidden_dim": HIDDEN_DIM,
                "num_features": NUM_FEATURES,
                "best_val_acc": best_val_acc,
                "epoch": epoch + 1,
                "trained_at": datetime.utcnow().isoformat(),
            }, MODEL_DIR / "direction_predictor.pt")
        else:
            no_improve += 1
            if no_improve >= patience:
                log.info(f"Early stopping at epoch {epoch+1} (best val_acc: {best_val_acc:.3f})")
                break

    log.info(f"Training complete. Best validation accuracy: {best_val_acc:.3f}")
    log.info(f"Model saved to {MODEL_DIR / 'direction_predictor.pt'}")
    return best_val_acc


if __name__ == "__main__":
    train()
