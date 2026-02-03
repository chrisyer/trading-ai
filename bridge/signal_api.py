#!/usr/bin/env python3
"""
SIGNAL API FOR CRELLA
======================
Serves signal files over HTTP so Crella can pull them automatically.

Endpoints:
  GET /signal       → ea_signal.json
  GET /defcon       → defcon_state.json
  GET /control      → aiiq_control.json
  GET /health       → API health check

Usage from Crella:
  curl http://100.91.17.86:8095/signal > MQL5/Files/ea_signal.json
"""

from flask import Flask, send_file, jsonify
from pathlib import Path
from datetime import datetime

app = Flask(__name__)

# Signal files location
SIGNAL_DIR = Path("/home/jbot/trading_ai/crella_signals")
CONTROL_FILE = Path("/home/jbot/.wine/drive_c/Program Files/MetaTrader 5/MQL5/Files/aiiq_control.json")


@app.route('/signal')
def get_signal():
    """Return ea_signal.json"""
    signal_file = SIGNAL_DIR / "ea_signal.json"
    if signal_file.exists():
        return send_file(signal_file, mimetype='application/json')
    return jsonify({"error": "Signal file not found"}), 404


@app.route('/defcon')
def get_defcon():
    """Return defcon_state.json"""
    defcon_file = SIGNAL_DIR / "defcon_state.json"
    if defcon_file.exists():
        return send_file(defcon_file, mimetype='application/json')
    return jsonify({"error": "DEFCON file not found"}), 404


@app.route('/control')
def get_control():
    """Return aiiq_control.json (raw bridge output)"""
    if CONTROL_FILE.exists():
        return send_file(CONTROL_FILE, mimetype='application/json')
    return jsonify({"error": "Control file not found"}), 404


@app.route('/health')
def health():
    """Health check"""
    signal_file = SIGNAL_DIR / "ea_signal.json"
    return jsonify({
        "status": "ok",
        "timestamp": datetime.now().isoformat(),
        "signal_exists": signal_file.exists(),
        "signal_age_seconds": (datetime.now() - datetime.fromtimestamp(signal_file.stat().st_mtime)).total_seconds() if signal_file.exists() else None
    })


@app.route('/')
def index():
    """API info"""
    return jsonify({
        "name": "Quinn Signal API",
        "version": "1.0",
        "endpoints": {
            "/signal": "GET ea_signal.json for SHARP/Crella bots",
            "/defcon": "GET defcon_state.json",
            "/control": "GET raw aiiq_control.json from bridge",
            "/health": "API health check"
        },
        "usage": "curl http://100.91.17.86:8096/signal > MQL5/Files/ea_signal.json"
    })


if __name__ == "__main__":
    print("=" * 60)
    print("📡 QUINN SIGNAL API")
    print("=" * 60)
    print("Endpoints:")
    print("  GET /signal  → ea_signal.json")
    print("  GET /defcon  → defcon_state.json")
    print("  GET /control → aiiq_control.json")
    print("  GET /health  → Health check")
    print("=" * 60)
    print()
    print("Crella usage:")
    print("  curl http://100.91.17.86:8096/signal > MQL5/Files/ea_signal.json")
    print()
    app.run(host='0.0.0.0', port=8096)
