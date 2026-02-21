# QUINN MISSION: ML Training & Inference Pipeline
## H100 GPU-Accelerated Trading Intelligence
**Version:** 1.0 | **Date:** Feb 20, 2026 | **Author:** QUINN001

---

## OVERVIEW

Three ML services that turn the H100 into a real training and inference engine
for XAUUSD and forex trading. All three are additive to existing rules — if the
H100 is down, CRELLA falls back silently to rule-based logic.

| Port | Service | Model | Retrain |
|------|---------|-------|---------|
| **8040** | Direction Predictor | LSTM+Attention (PyTorch) | Nightly |
| **8041** | RL Position Sizer | PPO (Stable Baselines3) | Weekly |
| **8043** | LoRA Governor | Llama-3-8B + LoRA (4-bit) | Weekly |

> **Port Note:** 8042 is taken by Chart Vision API. LoRA Governor uses **8043**.

---

## SAFETY GUARANTEES

- **Aggregate lot cap:** 25 lots (hard limit, regardless of ML output)
- **Session gate:** 21:00–02:00 UTC (hard limit)
- **Circuit breaker:** ATR spike 1.6x threshold freezes all entries
- **Bridge max() overlay:** RL position sizer can only **tighten** risk, never loosen
- **Fallback chain:** LoRA Governor → Ollama → rule-based defaults
- **You cannot make things worse — only better**

---

## STEP 1: Install Dependencies

```bash
cd /home/jbot/trading_ai/special_ops/ml
pip install -r requirements.txt
python3 -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"
python3 -c "import duckdb; print('DuckDB:', duckdb.__version__)"
```

Expected: `True NVIDIA H100 80GB HBM3`

---

## STEP 2: Start Data Ingest (runs continuously)

```bash
export CRELLA_IP=100.119.161.65
cd /home/jbot/trading_ai/special_ops
python3 -m ml.data.ingest
```

**What it does:**
- Backfills ~26K annotated trade records from `crella_trades_annotated.jsonl`
- Backfills 8K+ predictions from `neo/prediction_data/prediction_history.json`
- Polls CRELLA `/truth/latest` every 60s for live market ticks
- Syncs basket outcomes from `/truth/training` every 30 polls
- Stores everything in DuckDB at `special_ops/ml/data/trading.duckdb`

Let this run for 2–3 hours to collect live data. It also does the full backfill
on first run (takes ~30 seconds).

---

## STEP 3: Train & Serve the Direction Predictor (port 8040)

### Train
```bash
cd /home/jbot/trading_ai/special_ops
python3 -m ml.training.train_direction
```

**Architecture:** Bidirectional LSTM (2 layers) + Multi-Head Attention (4 heads)
- Input: 32-step sequences of 13 features (ATR, layers, spread, sentiment, etc.)
- Output: UP/DOWN classification with confidence
- Training: AdamW + CosineAnnealing + early stopping (patience=10)
- Class-balanced loss for imbalanced direction data

### Serve
```bash
cd /home/jbot/trading_ai/special_ops
uvicorn ml.serving.direction_api:app --host 0.0.0.0 --port 8040
```

Ghost Commander on CRELLA calls `POST /predict` with current market state.

---

## STEP 4: Train & Serve the RL Position Sizer (port 8041)

### Train
```bash
cd /home/jbot/trading_ai/special_ops
python3 -m ml.training.train_rl
```

**Architecture:** PPO (Proximal Policy Optimization)
- State: 10-dim (ATR, layers, direction, spread, equity, sentiment, risk)
- Action: 2-dim continuous (dca_multiplier [0.5–2.0], lot_scale [0.3–1.5])
- Reward: risk-adjusted P&L (pnl / max(dd, 0.5)) with penalties for aggressive
  sizing during high volatility
- Trained on unique basket replays from DuckDB

### Serve
```bash
cd /home/jbot/trading_ai/special_ops
uvicorn ml.serving.sizing_api:app --host 0.0.0.0 --port 8041
```

Bridge governor on CRELLA calls `POST /predict` with `max()` overlay:
RL can only tighten risk, never loosen it.

---

## STEP 5: Train & Serve the LoRA Governor (port 8043)

### Prepare Dataset
```bash
cd /home/jbot/trading_ai/special_ops
python3 -m ml.finetune.prepare_dataset
```

Converts basket outcomes into instruction-tuning format (system/user/assistant).

### Fine-tune
```bash
cd /home/jbot/trading_ai/special_ops
python3 -m ml.finetune.train_lora
```

**Architecture:** Meta-Llama-3-8B-Instruct + LoRA
- 4-bit NF4 quantization (fits in ~6GB VRAM)
- LoRA r=16, alpha=32, targeting all projection layers
- Trains on actual CRELLA trade decisions with real P&L outcomes
- Replaces generic Ollama governor with a model that knows OUR patterns

### Serve
```bash
cd /home/jbot/trading_ai/special_ops
uvicorn ml.serving.governor_api:app --host 0.0.0.0 --port 8043
```

Falls back to Ollama (`qwen2.5:7b-instruct`) if LoRA adapter isn't loaded.

---

## STEP 6: systemd Services (persistent)

### Direction Predictor
```ini
[Unit]
Description=ML Direction Predictor API
After=network.target

[Service]
Type=simple
User=jbot
WorkingDirectory=/home/jbot/trading_ai/special_ops
ExecStart=/home/jbot/.local/bin/uvicorn ml.serving.direction_api:app --host 0.0.0.0 --port 8040
Restart=always
RestartSec=5
Environment=CUDA_VISIBLE_DEVICES=0

[Install]
WantedBy=multi-user.target
```
Save as `/etc/systemd/system/ml-direction.service`

### RL Position Sizer
```ini
[Unit]
Description=ML RL Position Sizer API
After=network.target

[Service]
Type=simple
User=jbot
WorkingDirectory=/home/jbot/trading_ai/special_ops
ExecStart=/home/jbot/.local/bin/uvicorn ml.serving.sizing_api:app --host 0.0.0.0 --port 8041
Restart=always
RestartSec=5
Environment=CUDA_VISIBLE_DEVICES=0

[Install]
WantedBy=multi-user.target
```
Save as `/etc/systemd/system/ml-sizing.service`

### LoRA Governor
```ini
[Unit]
Description=ML LoRA Governor API
After=network.target

[Service]
Type=simple
User=jbot
WorkingDirectory=/home/jbot/trading_ai/special_ops
ExecStart=/home/jbot/.local/bin/uvicorn ml.serving.governor_api:app --host 0.0.0.0 --port 8043
Restart=always
RestartSec=5
Environment=CUDA_VISIBLE_DEVICES=0
Environment=OLLAMA_URL=http://localhost:11434

[Install]
WantedBy=multi-user.target
```
Save as `/etc/systemd/system/ml-governor.service`

### Data Ingest
```ini
[Unit]
Description=ML Data Ingest Pipeline
After=network.target

[Service]
Type=simple
User=jbot
WorkingDirectory=/home/jbot/trading_ai/special_ops
ExecStart=/usr/bin/python3 -m ml.data.ingest
Restart=always
RestartSec=10
Environment=CRELLA_IP=100.119.161.65

[Install]
WantedBy=multi-user.target
```
Save as `/etc/systemd/system/ml-ingest.service`

### Enable all
```bash
sudo systemctl daemon-reload
sudo systemctl enable ml-direction ml-sizing ml-governor ml-ingest
sudo systemctl start ml-direction ml-sizing ml-governor ml-ingest
```

---

## STEP 7: Verify

```bash
curl http://localhost:8040/health
curl http://localhost:8041/health
curl http://localhost:8043/health
```

All should return `"status": "loaded"` (or `"ollama_fallback"` for governor
before LoRA is trained).

### Test predictions
```bash
# Direction
curl -X POST http://localhost:8040/predict \
  -H "Content-Type: application/json" \
  -d '{"atr": 65.0, "atr_pctl": 0.7, "layers": 2, "direction": 1}'

# Position sizing
curl -X POST http://localhost:8041/predict \
  -H "Content-Type: application/json" \
  -d '{"atr": 65.0, "atr_pctl": 0.7, "layers": 2, "direction": 1}'

# Governor
curl -X POST http://localhost:8043/govern \
  -H "Content-Type: application/json" \
  -d '{"atr": 65.0, "atr_pctl": 0.7, "layers": 2, "direction": 1}'
```

---

## RETRAINING SCHEDULE

| Model | Frequency | Command |
|-------|-----------|---------|
| Direction Predictor | **Nightly** | `python3 -m ml.training.train_direction` |
| RL Position Sizer | **Weekly** | `python3 -m ml.training.train_rl` |
| LoRA Governor | **Weekly** | `python3 -m ml.finetune.prepare_dataset && python3 -m ml.finetune.train_lora` |

After retraining, hot-reload weights without restarting:
```bash
curl -X POST http://localhost:8040/reload
curl -X POST http://localhost:8041/reload
curl -X POST http://localhost:8043/reload
```

---

## PORT MAP (full system)

| Port | Service |
|------|---------|
| 8036 | NEO Ghost Integration API |
| 8037 | BTC Miners / Consensus Engine |
| 8040 | **ML Direction Predictor** |
| 8041 | **ML RL Position Sizer** |
| 8042 | Chart Vision API |
| 8043 | **ML LoRA Governor** |
| 8088 | GLD Strangle Dashboard |
| 8096 | Bridge Signal API |
| 8097 | CRELLA Truth Server (remote) |
| 8890 | Trading Agents API |
| 8897 | NEO Training API |

---

## DATA FLOW

```
CRELLA (100.119.161.65:8097)
    |
    v  /truth/latest (every 60s)
Data Ingest → DuckDB (trading.duckdb)
    |
    v  Training data
    ├── train_direction → direction_predictor.pt → Direction API (8040)
    ├── train_rl → rl_position_sizer.zip → Sizing API (8041)
    └── prepare_dataset + train_lora → governor_lora/ → Governor API (8043)
                                                           |
Ghost Commander ← Direction API                            |
Bridge Governor ← Sizing API (max overlay)                 |
Bridge Governor ← Governor API (replaces Ollama) ←────────┘
```

---

*This is what the H100 was built for. Real ML on real trading data.*
