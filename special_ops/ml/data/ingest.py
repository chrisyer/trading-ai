"""
Data Ingest Pipeline — Collects market data from CRELLA truth endpoints into DuckDB.
Backfills from crella_trades_annotated.jsonl on first run.
Polls CRELLA /truth/latest every 60s for live data.
"""

import os
import sys
import json
import time
import signal
import logging
import duckdb
import requests
from pathlib import Path
from datetime import datetime, timezone

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [INGEST] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

ML_DIR = Path(__file__).resolve().parents[1]
BASE_DIR = Path(__file__).resolve().parents[3]  # /home/jbot/trading_ai
DB_PATH = ML_DIR / "data" / "trading.duckdb"
CRELLA_IP = os.environ.get("CRELLA_IP", "100.119.161.65")
CRELLA_PORT = os.environ.get("CRELLA_PORT", "8097")
CRELLA_BASE = f"http://{CRELLA_IP}:{CRELLA_PORT}"
POLL_INTERVAL = int(os.environ.get("INGEST_POLL_SECONDS", "60"))

TRAINING_DATA_DIR = BASE_DIR / "neo" / "training_data"
ANNOTATED_JSONL = TRAINING_DATA_DIR / "crella_trades_annotated.jsonl"
FULL_JSONL_PATTERN = "crella_full_*.jsonl"

_running = True


def _signal_handler(sig, frame):
    global _running
    log.info("Shutdown signal received")
    _running = False


signal.signal(signal.SIGINT, _signal_handler)
signal.signal(signal.SIGTERM, _signal_handler)


def init_db(con: duckdb.DuckDBPyConnection):
    con.execute("""
        CREATE TABLE IF NOT EXISTS market_ticks (
            ts TIMESTAMP,
            atr DOUBLE,
            rsi DOUBLE,
            ema_fast DOUBLE,
            ema_slow DOUBLE,
            vwap DOUBLE,
            price DOUBLE,
            spread DOUBLE,
            volume DOUBLE,
            session VARCHAR,
            ichimoku_tenkan DOUBLE,
            ichimoku_kijun DOUBLE,
            macd DOUBLE,
            macd_signal DOUBLE,
            momentum DOUBLE,
            PRIMARY KEY (ts)
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS basket_outcomes (
            basket_id INTEGER,
            ts TIMESTAMP,
            atr DOUBLE,
            atr_bucket VARCHAR,
            atr_pctl DOUBLE,
            layers INTEGER,
            direction INTEGER,
            spread_points DOUBLE,
            equity DOUBLE,
            net_lots DOUBLE,
            sentiment_regime VARCHAR,
            event_risk VARCHAR,
            intensity DOUBLE,
            control_disable_entries BOOLEAN,
            control_dca_mult DOUBLE,
            control_layer_cap INTEGER,
            control_risk_bias DOUBLE,
            pnl DOUBLE,
            dd DOUBLE,
            duration_min DOUBLE,
            max_layers INTEGER,
            PRIMARY KEY (basket_id, ts)
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS predictions (
            prediction_id VARCHAR,
            ts TIMESTAMP,
            current_price DOUBLE,
            predicted_direction VARCHAR,
            predicted_change_pips DOUBLE,
            confidence DOUBLE,
            ema_trend DOUBLE,
            rsi_val DOUBLE,
            macd_val DOUBLE,
            momentum DOUBLE,
            atr DOUBLE,
            session VARCHAR,
            status VARCHAR,
            PRIMARY KEY (prediction_id)
        )
    """)
    log.info("Database tables initialized")


def backfill_annotated_trades(con: duckdb.DuckDBPyConnection):
    """Backfill basket outcomes from full CRELLA JSONL files (26K+ records, 23 baskets)."""
    existing = con.execute("SELECT COUNT(*) FROM basket_outcomes").fetchone()[0]
    if existing > 0:
        log.info(f"basket_outcomes already has {existing} rows, skipping backfill")
        return existing

    import glob
    full_files = sorted(glob.glob(str(TRAINING_DATA_DIR / FULL_JSONL_PATTERN)))
    source_file = full_files[-1] if full_files else ANNOTATED_JSONL

    if not Path(source_file).exists():
        log.warning(f"No training data found")
        return 0

    log.info(f"Backfilling from {source_file}...")
    count = 0
    seen_keys = set()

    with open(source_file) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue

            state = rec.get("state", {})
            control = rec.get("control", {})
            outcome = rec.get("outcome", {})
            sentiment = state.get("sentiment", {})
            regime = state.get("regime", {})

            basket_id = outcome.get("basket_id", 0)
            ts = rec.get("timestamp", "")
            key = (basket_id, ts)
            if key in seen_keys:
                continue
            seen_keys.add(key)

            try:
                con.execute("""
                    INSERT OR IGNORE INTO basket_outcomes VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    )
                """, [
                    basket_id, ts,
                    state.get("atr", 0), regime.get("atr_bucket", "unknown"),
                    regime.get("atr_pctl", 0), state.get("layers", 0),
                    state.get("dir", 0), state.get("spread_points", 0),
                    state.get("equity", 0), state.get("net_lots", 0),
                    sentiment.get("sentiment_regime", "unknown"),
                    sentiment.get("event_risk", "unknown"),
                    sentiment.get("intensity", 0),
                    control.get("disable_new_entries", False),
                    control.get("dca_step_multiplier", 1.0),
                    control.get("max_layers_cap", 3),
                    control.get("risk_bias", 0.0),
                    outcome.get("pnl", 0), outcome.get("dd", 0),
                    outcome.get("mins", 0), outcome.get("max_layers", 0),
                ])
                count += 1
            except Exception as e:
                if count < 5:
                    log.warning(f"Insert error: {e}")

    log.info(f"Backfilled {count} unique basket outcome records from {Path(source_file).name}")

    index_file = TRAINING_DATA_DIR / "crella_index.json"
    if index_file.exists():
        with open(index_file) as f:
            idx_data = json.load(f)
        idx_count = 0
        for rec in idx_data.get("index", []):
            if not rec.get("has_outcome"):
                continue
            basket_id = rec.get("basket_id", 0)
            ts = rec.get("ts", "")
            try:
                con.execute("""
                    INSERT OR IGNORE INTO basket_outcomes VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    )
                """, [
                    basket_id, ts,
                    rec.get("atr", 0), rec.get("atr_bucket", "unknown"),
                    rec.get("atr_pctl", 0), rec.get("layers", 0),
                    rec.get("dir", 0), 0, 0, 0,
                    rec.get("sentiment_regime", "unknown"),
                    rec.get("event_risk", "unknown"),
                    rec.get("intensity_bin", 0),
                    rec.get("control_disable_entries", False),
                    rec.get("control_dca_mult", 1.0),
                    rec.get("control_layer_cap", 3),
                    0.0,
                    rec.get("pnl", 0), rec.get("dd", 0),
                    rec.get("duration_min", 0), rec.get("max_layers_reached", 0),
                ])
                idx_count += 1
            except Exception:
                pass
        log.info(f"Backfilled {idx_count} basket records from crella_index.json")

    return count


def backfill_predictions(con: duckdb.DuckDBPyConnection):
    """Backfill from neo prediction_history.json."""
    pred_file = BASE_DIR / "neo" / "prediction_data" / "prediction_history.json"
    if not pred_file.exists():
        return 0

    existing = con.execute("SELECT COUNT(*) FROM predictions").fetchone()[0]
    if existing > 0:
        log.info(f"predictions already has {existing} rows, skipping backfill")
        return existing

    log.info(f"Backfilling predictions from {pred_file}...")
    with open(pred_file) as f:
        data = json.load(f)

    if isinstance(data, dict):
        data = data.get("predictions", [])

    count = 0
    for rec in data:
        if not isinstance(rec, dict):
            continue
        features = rec.get("features", {})
        if not isinstance(features, dict):
            features = {}

        def safe_float(val, default=0.0):
            if isinstance(val, (int, float)):
                return float(val)
            return default

        try:
            con.execute("""
                INSERT OR IGNORE INTO predictions VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
            """, [
                rec.get("prediction_id", ""),
                rec.get("timestamp", ""),
                safe_float(rec.get("current_price")),
                rec.get("predicted_direction", ""),
                safe_float(rec.get("predicted_change_pips")),
                safe_float(rec.get("confidence")),
                1.0 if features.get("ema_trend") == "BULLISH" else (-1.0 if features.get("ema_trend") == "BEARISH" else 0.0),
                safe_float(features.get("rsi_h1", features.get("rsi"))),
                safe_float(features.get("macd_value", features.get("macd"))),
                safe_float(features.get("momentum_4h", features.get("momentum"))),
                safe_float(features.get("atr")),
                str(features.get("session", "unknown")),
                rec.get("status", ""),
            ])
            count += 1
        except Exception:
            pass

    log.info(f"Backfilled {count} prediction records")
    return count


def poll_crella_latest(con: duckdb.DuckDBPyConnection):
    """Fetch latest market metrics from CRELLA truth endpoint."""
    try:
        resp = requests.get(f"{CRELLA_BASE}/truth/latest", timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        log.debug(f"CRELLA poll failed: {e}")
        return False

    metrics = data.get("metrics", data)
    ts = data.get("timestamp", datetime.now(timezone.utc).isoformat())

    try:
        con.execute("""
            INSERT OR REPLACE INTO market_ticks VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
        """, [
            ts,
            metrics.get("atr", 0),
            metrics.get("rsi", 0),
            metrics.get("ema_fast", metrics.get("ema_21", 0)),
            metrics.get("ema_slow", metrics.get("ema_55", 0)),
            metrics.get("vwap", 0),
            metrics.get("price", metrics.get("bid", 0)),
            metrics.get("spread", metrics.get("spread_points", 0)),
            metrics.get("volume", 0),
            metrics.get("session", "unknown"),
            metrics.get("ichimoku_tenkan", 0),
            metrics.get("ichimoku_kijun", 0),
            metrics.get("macd", 0),
            metrics.get("macd_signal", 0),
            metrics.get("momentum", 0),
        ])
        return True
    except Exception as e:
        log.debug(f"Insert tick failed: {e}")
        return False


def poll_crella_training(con: duckdb.DuckDBPyConnection):
    """Sync basket outcomes from CRELLA training endpoint."""
    try:
        resp = requests.get(f"{CRELLA_BASE}/truth/training", timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        log.debug(f"CRELLA training poll failed: {e}")
        return 0

    index = data.get("index", [])
    new_count = 0
    for rec in index:
        if not rec.get("has_outcome"):
            continue
        try:
            con.execute("""
                INSERT OR IGNORE INTO basket_outcomes VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
            """, [
                rec.get("basket_id", 0), rec.get("ts", ""),
                rec.get("atr", 0), rec.get("atr_bucket", "unknown"),
                rec.get("atr_pctl", 0), rec.get("layers", 0),
                rec.get("dir", 0), 0, 0, 0,
                rec.get("sentiment_regime", "unknown"),
                rec.get("event_risk", "unknown"),
                rec.get("intensity_bin", 0),
                rec.get("control_disable_entries", False),
                rec.get("control_dca_mult", 1.0),
                rec.get("control_layer_cap", 3),
                0.0,
                rec.get("pnl", 0), rec.get("dd", 0),
                rec.get("duration_min", 0), rec.get("max_layers_reached", 0),
            ])
            new_count += 1
        except Exception:
            pass

    if new_count > 0:
        log.info(f"Synced {new_count} new basket outcomes from CRELLA")
    return new_count


def main():
    log.info("=" * 60)
    log.info("ML Data Ingest Pipeline — Starting")
    log.info(f"  DuckDB:    {DB_PATH}")
    log.info(f"  CRELLA:    {CRELLA_BASE}")
    log.info(f"  Poll:      every {POLL_INTERVAL}s")
    log.info("=" * 60)

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(DB_PATH))
    init_db(con)

    backfill_annotated_trades(con)
    backfill_predictions(con)

    stats = con.execute("""
        SELECT
            (SELECT COUNT(*) FROM market_ticks) as ticks,
            (SELECT COUNT(*) FROM basket_outcomes) as baskets,
            (SELECT COUNT(*) FROM predictions) as preds
    """).fetchone()
    log.info(f"DB state: {stats[0]} ticks, {stats[1]} baskets, {stats[2]} predictions")

    poll_count = 0
    training_poll_count = 0

    while _running:
        success = poll_crella_latest(con)
        poll_count += 1

        if poll_count % 30 == 0:
            poll_crella_training(con)
            training_poll_count += 1

        if poll_count % 60 == 0:
            stats = con.execute("""
                SELECT
                    (SELECT COUNT(*) FROM market_ticks) as ticks,
                    (SELECT COUNT(*) FROM basket_outcomes) as baskets
            """).fetchone()
            log.info(f"[Poll #{poll_count}] Ticks: {stats[0]}, Baskets: {stats[1]}")

        for _ in range(POLL_INTERVAL):
            if not _running:
                break
            time.sleep(1)

    con.close()
    log.info("Ingest pipeline shut down cleanly")


if __name__ == "__main__":
    main()
