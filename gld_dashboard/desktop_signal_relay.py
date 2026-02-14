#!/usr/bin/env python3
"""
DESKTOP SIGNAL RELAY — US Desktop Side (Option C)
══════════════════════════════════════════════════
Lightweight Flask server running on the US desktop.
Reads the MT5 GLD_Options_Advisor signal file and serves it via HTTP.
H100 polls this endpoint every 30-60 seconds.

Deploy on Windows desktop via:
  python desktop_signal_relay.py

Or as a Windows scheduled task / startup script.

Port: 8098 (configurable)
"""

import os
import json
import time
from datetime import datetime
from pathlib import Path
from flask import Flask, jsonify

app = Flask(__name__)

# MT5 signal file location (Windows path)
# GLD_Options_Advisor.mq5 writes to MT5's Common/Files directory
SIGNAL_FILE = Path(os.environ.get(
    "MT5_GLD_SIGNAL",
    r"C:\Users\Gringot\AppData\Roaming\MetaQuotes\Terminal\Common\Files\gld_options_signal.txt"
))

# Alternative: check multiple locations
SIGNAL_PATHS = [
    SIGNAL_FILE,
    Path(r"C:\Users\Gringot\AppData\Roaming\MetaQuotes\Terminal\Common\Files\gld_signal.json"),
    Path(r"C:\Users\Gringot\Documents\gld_signal.json"),
]

PORT = int(os.environ.get("RELAY_PORT", 8098))


def read_signal_file() -> dict:
    """Read the MT5 signal file from disk."""
    for path in SIGNAL_PATHS:
        if path.exists():
            try:
                content = path.read_text(encoding="utf-8").strip()
                # Try JSON first
                try:
                    data = json.loads(content)
                    data["_file"] = str(path)
                    data["_read_at"] = datetime.utcnow().isoformat()
                    return data
                except json.JSONDecodeError:
                    pass

                # Parse text format (key=value per line)
                data = {"_format": "text", "_file": str(path)}
                for line in content.split("\n"):
                    line = line.strip()
                    if "=" in line:
                        key, val = line.split("=", 1)
                        key = key.strip().lower().replace(" ", "_")
                        val = val.strip()
                        # Try numeric conversion
                        try:
                            val = float(val)
                            if val == int(val):
                                val = int(val)
                        except ValueError:
                            pass
                        data[key] = val
                data["_read_at"] = datetime.utcnow().isoformat()
                return data

            except Exception as e:
                return {"error": str(e), "path": str(path)}

    return {
        "status": "no_file",
        "message": "No signal file found",
        "searched": [str(p) for p in SIGNAL_PATHS],
    }


@app.route("/signal/gld")
def get_gld_signal():
    """Serve the current GLD signal."""
    return jsonify(read_signal_file())


@app.route("/signal/all")
def get_all_signals():
    """Serve all available signal files from MT5 Common/Files."""
    common_dir = Path(r"C:\Users\Gringot\AppData\Roaming\MetaQuotes\Terminal\Common\Files")
    signals = {}
    if common_dir.exists():
        for f in common_dir.glob("*.txt"):
            try:
                signals[f.name] = f.read_text(encoding="utf-8")[:1000]
            except Exception:
                pass
        for f in common_dir.glob("*.json"):
            try:
                signals[f.name] = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                pass
    return jsonify(signals)


@app.route("/health")
def health():
    """Health check."""
    return jsonify({
        "status": "ok",
        "service": "desktop-signal-relay",
        "hostname": os.environ.get("COMPUTERNAME", "unknown"),
        "timestamp": datetime.utcnow().isoformat(),
        "signal_file_exists": any(p.exists() for p in SIGNAL_PATHS),
    })


if __name__ == "__main__":
    print(f"Desktop Signal Relay starting on port {PORT}")
    print(f"Signal file: {SIGNAL_FILE}")
    print(f"Access: http://localhost:{PORT}/signal/gld")
    app.run(host="0.0.0.0", port=PORT, debug=False)
