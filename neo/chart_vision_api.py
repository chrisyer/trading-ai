#!/usr/bin/env python3
"""
═══════════════════════════════════════════════════════════════════════════════
NEO CHART VISION — Qwen3-VL Chart Analyzer
═══════════════════════════════════════════════════════════════════════════════

Upload any trading chart from phone or desktop → Qwen3-VL:32b analyzes it.

Identifies:
  - Candlestick patterns (engulfing, doji, hammer, shooting star, etc.)
  - Support / Resistance levels
  - Trend structure (HH/HL, LH/LL)
  - Indicator states (RSI, MACD, Bollinger, EMA, Ichimoku if visible)
  - Volume profile
  - Pattern classification (breakout, exhaustion, accumulation, etc.)

NOT a signal generator. Research observations only.

Port: 8042
URL:  http://<QUINN_IP>:8042

Author: QUINN001
Created: February 8, 2026
═══════════════════════════════════════════════════════════════════════════════
"""

import os
import json
import base64
import logging
import time
import requests as http_requests
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
import uvicorn

# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
VISION_MODEL = os.getenv("CHART_VISION_MODEL", "gemma3:27b")  # Fast, reliable vision
VISION_FALLBACK = "qwen3-vl:32b"  # Deeper analysis but slower (thinking mode)
VISION_TIMEOUT = 180  # seconds

PORT = int(os.getenv("CHART_VISION_PORT", "8042"))
DATA_DIR = Path("/home/jbot/trading_ai/neo/chart_vision_data")
DATA_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [CHART-VISION] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("chart_vision")

# ═══════════════════════════════════════════════════════════════════════════════
# VISION PROMPT — Comprehensive Chart Analysis
# ═══════════════════════════════════════════════════════════════════════════════

CHART_ANALYSIS_PROMPT = """You are a professional chart analyst examining a trading chart.
You do NOT give trade entries or exit signals. You ONLY describe what you observe.

Analyze the chart image and respond with ONLY valid JSON in this exact format:

{
  "symbol": "<detected symbol or UNKNOWN>",
  "timeframe": "<detected timeframe: M1/M5/M15/M30/H1/H4/D1 or UNKNOWN>",
  "price_current": "<current price if visible or null>",

  "trend": {
    "direction": "<BULLISH/BEARISH/SIDEWAYS>",
    "structure": "<describe HH/HL or LH/LL pattern>",
    "strength": "<STRONG/MODERATE/WEAK>"
  },

  "candlestick_patterns": [
    {"name": "<pattern name>", "location": "<where on chart>", "significance": "<HIGH/MEDIUM/LOW>"}
  ],

  "support_resistance": [
    {"type": "<SUPPORT/RESISTANCE>", "level": "<price level>", "strength": "<STRONG/MODERATE/WEAK>", "touches": "<number of touches if visible>"}
  ],

  "indicators": {
    "ema_crossover": "<BULLISH_CROSS/BEARISH_CROSS/NONE/NOT_VISIBLE>",
    "rsi": {"value": "<number or null>", "state": "<OVERBOUGHT/OVERSOLD/NEUTRAL/NOT_VISIBLE>", "divergence": "<BULLISH_DIV/BEARISH_DIV/NONE/NOT_VISIBLE>"},
    "macd": {"state": "<BULLISH/BEARISH/CROSSING/NOT_VISIBLE>", "histogram": "<GROWING/SHRINKING/FLAT/NOT_VISIBLE>"},
    "bollinger": {"position": "<UPPER/MIDDLE/LOWER/SQUEEZE/NOT_VISIBLE>", "width": "<EXPANDING/CONTRACTING/STABLE/NOT_VISIBLE>"},
    "ichimoku": {"cloud": "<ABOVE/BELOW/INSIDE/NOT_VISIBLE>", "color": "<BULLISH/BEARISH/NOT_VISIBLE>"},
    "volume": "<HIGH/LOW/AVERAGE/CLIMACTIC/NOT_VISIBLE>",
    "other": "<any other indicators you can identify>"
  },

  "pattern_classification": {
    "primary": "<breakout/breakdown/range/accumulation/distribution/exhaustion/parabolic/consolidation/pullback/reversal/none>",
    "confidence": "<HIGH/MEDIUM/LOW>",
    "description": "<1-2 sentence observation>"
  },

  "key_levels": {
    "nearest_resistance": "<price or null>",
    "nearest_support": "<price or null>",
    "pivot_point": "<price or null>"
  },

  "risk_assessment": {
    "reversion_risk": <0.0 to 1.0>,
    "continuation_probability": <0.0 to 1.0>,
    "volatility": "<HIGH/MODERATE/LOW>"
  },

  "observations": [
    "<observation 1>",
    "<observation 2>",
    "<observation 3>"
  ]
}

RULES:
- Describe ONLY what you SEE on the chart
- If an indicator is not visible, mark it NOT_VISIBLE
- Do NOT predict future price action
- Do NOT recommend BUY or SELL
- Focus on factual pattern identification
- Return ONLY valid JSON, no other text"""


# ═══════════════════════════════════════════════════════════════════════════════
# OLLAMA VISION API
# ═══════════════════════════════════════════════════════════════════════════════

def get_live_prices() -> Dict:
    """Fetch live prices from NEO API to ground the vision model."""
    try:
        resp = http_requests.get("http://localhost:8036/api/neo/xauusd/signal", timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            price = data.get("current_price") or data.get("price")
            if price:
                return {"XAUUSD": float(price)}
    except:
        pass
    return {}


def query_vision(image_b64: str, context: str = "") -> Dict:
    """Send image to Qwen3-VL via Ollama and get structured analysis."""

    prompt = CHART_ANALYSIS_PROMPT

    # Inject live prices to prevent hallucination of old training data prices
    live = get_live_prices()
    if live:
        price_lines = ", ".join(f"{sym}: ${p:.2f}" for sym, p in live.items())
        prompt += f"\n\nIMPORTANT LIVE PRICE DATA (use these as reference, do NOT guess prices from memory):\n{price_lines}"
        prompt += "\nIf the chart shows XAUUSD/Gold, the current price is in the $4,800-$5,200 range (2026), NOT $1,800-$2,100 (that was 2023-2024 data)."

    if context:
        prompt += f"\n\nAdditional context from uploader: {context}"

    for model in [VISION_MODEL, VISION_FALLBACK]:
        try:
            logger.info(f"Querying {model}...")
            start = time.time()

            resp = http_requests.post(
                f"{OLLAMA_URL}/api/chat",
                json={
                    "model": model,
                    "messages": [
                        {
                            "role": "user",
                            "content": prompt,
                            "images": [image_b64]
                        }
                    ],
                    "stream": False,
                    "options": {
                        "temperature": 0.2,
                        "num_predict": 16384,
                    }
                },
                timeout=VISION_TIMEOUT,
            )

            elapsed = time.time() - start

            if resp.status_code != 200:
                logger.warning(f"{model} returned {resp.status_code}: {resp.text[:200]}")
                continue

            msg = resp.json().get("message", {})
            raw = msg.get("content", "").strip()

            # Qwen3-VL uses thinking mode — check thinking field if content is empty
            if not raw:
                thinking = msg.get("thinking", "").strip()
                if thinking:
                    logger.info(f"{model} responded in thinking mode ({len(thinking)} chars thinking)")
                    raw = thinking
                else:
                    logger.warning(f"{model} returned empty response (no content or thinking)")
                    continue

            logger.info(f"{model} responded in {elapsed:.1f}s ({len(raw)} chars)")

            # Extract JSON from response
            result = extract_json(raw)

            # If thinking mode produced no JSON, try a follow-up without thinking
            if "error" in result and "Could not parse" in result.get("error", ""):
                logger.info(f"Retrying {model} with /think disabled...")
                resp2 = http_requests.post(
                    f"{OLLAMA_URL}/api/chat",
                    json={
                        "model": model,
                        "messages": [
                            {
                                "role": "user",
                                "content": "/no_think\n" + prompt,
                                "images": [image_b64]
                            }
                        ],
                        "stream": False,
                        "options": {
                            "temperature": 0.2,
                            "num_predict": 16384,
                        }
                    },
                    timeout=VISION_TIMEOUT,
                )
                if resp2.status_code == 200:
                    msg2 = resp2.json().get("message", {})
                    raw2 = msg2.get("content", "").strip()
                    if raw2:
                        result = extract_json(raw2)
                        elapsed = time.time() - start

            result["_meta"] = {
                "model": model,
                "inference_time_s": round(elapsed, 1),
                "analyzed_at": datetime.now(timezone.utc).isoformat(),
            }
            return result

        except http_requests.Timeout:
            logger.warning(f"{model} timed out after {VISION_TIMEOUT}s")
        except Exception as e:
            logger.error(f"{model} failed: {e}")

    return {"error": "All vision models failed", "models_tried": [VISION_MODEL, VISION_FALLBACK]}


def extract_json(raw: str) -> dict:
    """Extract JSON from model response, handling markdown fences and common LLM errors."""
    import re

    # Strip markdown code fences
    text = raw.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines)

    start = text.find("{")
    end = text.rfind("}")

    if start == -1 or end == -1 or end <= start:
        return {"error": "Could not parse JSON from model response", "raw_response": raw[:500]}

    json_str = text[start:end + 1]

    # Try direct parse first
    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        pass

    # Fix common LLM JSON errors:
    # 1. Trailing commas before } or ]
    json_str = re.sub(r',\s*([}\]])', r'\1', json_str)
    # 2. Single quotes -> double quotes
    json_str = json_str.replace("'", '"')
    # 3. Unquoted keys (word: -> "word":)
    json_str = re.sub(r'(?<=[{,\n])\s*(\w+)\s*:', r' "\1":', json_str)
    # 4. NaN/Infinity -> null
    json_str = re.sub(r'\bNaN\b', 'null', json_str)
    json_str = re.sub(r'\bInfinity\b', 'null', json_str)
    # 5. Truncated JSON — try to close open braces/brackets
    open_braces = json_str.count('{') - json_str.count('}')
    open_brackets = json_str.count('[') - json_str.count(']')
    if open_brackets > 0:
        json_str += ']' * open_brackets
    if open_braces > 0:
        json_str += '}' * open_braces

    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        pass

    # Last resort: find the largest valid JSON substring
    for trim in range(len(json_str), max(100, len(json_str) - 2000), -50):
        candidate = json_str[:trim]
        # Close open structures
        ob = candidate.count('{') - candidate.count('}')
        ol = candidate.count('[') - candidate.count(']')
        candidate += ']' * max(0, ol) + '}' * max(0, ob)
        try:
            return json.loads(candidate)
        except:
            continue

    return {"error": "Could not parse JSON after repair attempts", "raw_response": raw[:800]}


# ═══════════════════════════════════════════════════════════════════════════════
# MONGODB LOGGING
# ═══════════════════════════════════════════════════════════════════════════════

def log_analysis(result: Dict, context: str = ""):
    """Log analysis to MongoDB and disk."""
    try:
        from pymongo import MongoClient
        client = MongoClient("mongodb://localhost:27017", serverSelectionTimeoutMS=3000)
        db = client["quinn_trading"]
        col = db["chart_analyses"]
        doc = {
            "analyzed_at": datetime.now(timezone.utc),
            "context": context,
            "result": result,
        }
        col.insert_one(doc)
        client.close()
    except Exception as e:
        logger.warning(f"MongoDB log failed: {e}")

    # Also save to disk
    try:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        out_file = DATA_DIR / f"analysis_{ts}.json"
        out_file.write_text(json.dumps(result, indent=2, default=str))
    except Exception as e:
        logger.warning(f"Disk log failed: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# FASTAPI APP
# ═══════════════════════════════════════════════════════════════════════════════

app = FastAPI(
    title="NEO Chart Vision",
    description="Upload any chart → Qwen3-VL analyzes patterns, indicators, S/R",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ═══════════════════════════════════════════════════════════════════════════════
# HTML UPLOAD PAGE — Mobile-First
# ═══════════════════════════════════════════════════════════════════════════════

UPLOAD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<title>NEO Chart Vision</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }

  body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: #0a0e17;
    color: #e0e6f0;
    min-height: 100vh;
    padding: 16px;
  }

  .header {
    text-align: center;
    padding: 20px 0 16px;
  }

  .header h1 {
    font-size: 1.6rem;
    background: linear-gradient(135deg, #00d4ff, #7b2ff7);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    margin-bottom: 4px;
  }

  .header .sub {
    font-size: 0.8rem;
    color: #6b7280;
  }

  .model-badge {
    display: inline-block;
    background: rgba(123, 47, 247, 0.15);
    border: 1px solid rgba(123, 47, 247, 0.3);
    border-radius: 12px;
    padding: 2px 10px;
    font-size: 0.7rem;
    color: #a78bfa;
    margin-top: 6px;
  }

  .upload-zone {
    border: 2px dashed #2d3748;
    border-radius: 16px;
    padding: 40px 20px;
    text-align: center;
    cursor: pointer;
    transition: all 0.3s;
    margin: 16px 0;
    background: rgba(255,255,255,0.02);
    position: relative;
    overflow: hidden;
  }

  .upload-zone:hover, .upload-zone.dragover {
    border-color: #7b2ff7;
    background: rgba(123, 47, 247, 0.05);
  }

  .upload-zone .icon { font-size: 3rem; margin-bottom: 12px; }
  .upload-zone .text { font-size: 0.95rem; color: #9ca3af; }
  .upload-zone .hint { font-size: 0.75rem; color: #4b5563; margin-top: 8px; }

  .upload-zone img.preview {
    max-width: 100%;
    max-height: 300px;
    border-radius: 8px;
    object-fit: contain;
  }

  .context-input {
    width: 100%;
    background: #111827;
    border: 1px solid #2d3748;
    border-radius: 10px;
    padding: 12px 14px;
    color: #e0e6f0;
    font-size: 0.9rem;
    margin-bottom: 12px;
    outline: none;
  }

  .context-input:focus { border-color: #7b2ff7; }

  .btn-row { display: flex; gap: 10px; margin-bottom: 16px; }

  .btn {
    flex: 1;
    padding: 14px;
    border: none;
    border-radius: 12px;
    font-size: 0.95rem;
    font-weight: 600;
    cursor: pointer;
    transition: all 0.2s;
  }

  .btn-primary {
    background: linear-gradient(135deg, #7b2ff7, #00d4ff);
    color: white;
  }

  .btn-primary:hover { transform: translateY(-1px); box-shadow: 0 4px 20px rgba(123,47,247,0.3); }
  .btn-primary:disabled { opacity: 0.5; cursor: not-allowed; transform: none; }

  .btn-camera {
    background: #1f2937;
    color: #9ca3af;
    flex: 0 0 auto;
    width: 56px;
    font-size: 1.4rem;
    padding: 14px 0;
  }

  .btn-camera:hover { background: #374151; }

  .spinner {
    display: none;
    text-align: center;
    padding: 30px;
  }

  .spinner.active { display: block; }

  .spinner .dot {
    display: inline-block;
    width: 10px; height: 10px;
    border-radius: 50%;
    background: #7b2ff7;
    margin: 0 4px;
    animation: bounce 1.4s infinite ease-in-out both;
  }

  .spinner .dot:nth-child(1) { animation-delay: -0.32s; }
  .spinner .dot:nth-child(2) { animation-delay: -0.16s; }

  @keyframes bounce {
    0%, 80%, 100% { transform: scale(0); }
    40% { transform: scale(1.0); }
  }

  .spinner .msg { margin-top: 12px; font-size: 0.85rem; color: #6b7280; }

  /* Results */
  .results { display: none; margin-top: 16px; }
  .results.active { display: block; }

  .result-card {
    background: #111827;
    border: 1px solid #1f2937;
    border-radius: 14px;
    padding: 16px;
    margin-bottom: 12px;
  }

  .result-card h3 {
    font-size: 0.85rem;
    color: #6b7280;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    margin-bottom: 10px;
  }

  .result-row {
    display: flex;
    justify-content: space-between;
    padding: 6px 0;
    border-bottom: 1px solid rgba(255,255,255,0.04);
    font-size: 0.88rem;
  }

  .result-row:last-child { border-bottom: none; }
  .result-row .label { color: #9ca3af; }

  .result-row .value { font-weight: 600; }
  .result-row .value.bullish { color: #10b981; }
  .result-row .value.bearish { color: #ef4444; }
  .result-row .value.neutral { color: #f59e0b; }

  .tag {
    display: inline-block;
    padding: 2px 8px;
    border-radius: 6px;
    font-size: 0.75rem;
    font-weight: 600;
    margin: 2px;
  }

  .tag-high { background: rgba(239,68,68,0.15); color: #ef4444; }
  .tag-medium { background: rgba(245,158,11,0.15); color: #f59e0b; }
  .tag-low { background: rgba(107,114,128,0.15); color: #9ca3af; }
  .tag-bullish { background: rgba(16,185,129,0.15); color: #10b981; }
  .tag-bearish { background: rgba(239,68,68,0.15); color: #ef4444; }

  .obs-list { list-style: none; padding: 0; }

  .obs-list li {
    padding: 8px 0;
    border-bottom: 1px solid rgba(255,255,255,0.04);
    font-size: 0.85rem;
    line-height: 1.4;
  }

  .obs-list li::before { content: "→ "; color: #7b2ff7; }
  .obs-list li:last-child { border-bottom: none; }

  .meta-line {
    text-align: center;
    font-size: 0.7rem;
    color: #374151;
    margin-top: 8px;
  }

  .disclaimer {
    text-align: center;
    font-size: 0.7rem;
    color: #4b5563;
    padding: 16px 0;
    border-top: 1px solid #1f2937;
    margin-top: 20px;
  }

  .json-toggle {
    text-align: center;
    margin-top: 8px;
  }

  .json-toggle button {
    background: none;
    border: 1px solid #2d3748;
    color: #6b7280;
    padding: 6px 14px;
    border-radius: 8px;
    font-size: 0.75rem;
    cursor: pointer;
  }

  .json-raw {
    display: none;
    background: #0d1117;
    border: 1px solid #1f2937;
    border-radius: 10px;
    padding: 12px;
    margin-top: 8px;
    font-family: 'SF Mono', Monaco, monospace;
    font-size: 0.72rem;
    color: #8b949e;
    overflow-x: auto;
    white-space: pre-wrap;
    word-break: break-all;
    max-height: 400px;
    overflow-y: auto;
  }

  .json-raw.active { display: block; }

  input[type="file"] { display: none; }
</style>
</head>
<body>

<div class="header">
  <h1>NEO Chart Vision</h1>
  <div class="sub">Upload any chart. Get instant analysis.</div>
  <div class="model-badge">Qwen3-VL:32b on H100</div>
</div>

<div class="upload-zone" id="dropZone" onclick="document.getElementById('fileInput').click()">
  <div id="uploadContent">
    <div class="icon">📊</div>
    <div class="text">Tap to upload or drag a chart</div>
    <div class="hint">PNG, JPG, WebP — any timeframe, any symbol</div>
  </div>
</div>

<input type="file" id="fileInput" accept="image/*" capture="environment">
<input type="file" id="cameraInput" accept="image/*" capture="environment" style="display:none">

<input type="text" class="context-input" id="contextInput"
  placeholder="Optional: symbol, timeframe, what to focus on...">

<div class="btn-row">
  <button class="btn btn-camera" onclick="document.getElementById('cameraInput').click()" title="Camera">📷</button>
  <button class="btn btn-primary" id="analyzeBtn" onclick="analyze()" disabled>Analyze Chart</button>
</div>

<div class="spinner" id="spinner">
  <div><span class="dot"></span><span class="dot"></span><span class="dot"></span></div>
  <div class="msg">Qwen3-VL is reading your chart...</div>
</div>

<div class="results" id="results"></div>

<div class="disclaimer">
  Research observations only. Not financial advice. Not a trading signal.
</div>

<script>
let selectedFile = null;
const dropZone = document.getElementById('dropZone');
const fileInput = document.getElementById('fileInput');
const cameraInput = document.getElementById('cameraInput');
const analyzeBtn = document.getElementById('analyzeBtn');

// File selection
fileInput.onchange = (e) => handleFile(e.target.files[0]);
cameraInput.onchange = (e) => handleFile(e.target.files[0]);

// Drag and drop
dropZone.ondragover = (e) => { e.preventDefault(); dropZone.classList.add('dragover'); };
dropZone.ondragleave = () => dropZone.classList.remove('dragover');
dropZone.ondrop = (e) => {
  e.preventDefault();
  dropZone.classList.remove('dragover');
  if (e.dataTransfer.files.length) handleFile(e.dataTransfer.files[0]);
};

// Paste from clipboard
document.onpaste = (e) => {
  const items = e.clipboardData?.items;
  if (!items) return;
  for (let item of items) {
    if (item.type.startsWith('image/')) {
      handleFile(item.getAsFile());
      break;
    }
  }
};

function handleFile(file) {
  if (!file || !file.type.startsWith('image/')) return;
  selectedFile = file;
  analyzeBtn.disabled = false;

  const reader = new FileReader();
  reader.onload = (e) => {
    document.getElementById('uploadContent').innerHTML =
      '<img class="preview" src="' + e.target.result + '">';
  };
  reader.readAsDataURL(file);
}

async function analyze() {
  if (!selectedFile) return;

  analyzeBtn.disabled = true;
  document.getElementById('spinner').classList.add('active');
  document.getElementById('results').classList.remove('active');

  const formData = new FormData();
  formData.append('file', selectedFile);
  formData.append('context', document.getElementById('contextInput').value);

  try {
    const resp = await fetch('/analyze', { method: 'POST', body: formData });
    const data = await resp.json();
    renderResults(data);
  } catch (err) {
    document.getElementById('results').innerHTML =
      '<div class="result-card"><h3>Error</h3><p>' + err.message + '</p></div>';
    document.getElementById('results').classList.add('active');
  }

  document.getElementById('spinner').classList.remove('active');
  analyzeBtn.disabled = false;
}

function trendColor(dir) {
  if (!dir) return 'neutral';
  const d = dir.toUpperCase();
  if (d.includes('BULL')) return 'bullish';
  if (d.includes('BEAR')) return 'bearish';
  return 'neutral';
}

function tagClass(level) {
  if (!level) return 'tag-low';
  const l = level.toUpperCase();
  if (l === 'HIGH' || l === 'STRONG') return 'tag-high';
  if (l === 'MEDIUM' || l === 'MODERATE') return 'tag-medium';
  return 'tag-low';
}

function renderResults(data) {
  if (data.error) {
    document.getElementById('results').innerHTML =
      '<div class="result-card"><h3>Analysis Error</h3><p style="color:#ef4444">' + data.error + '</p>' +
      (data.raw_response ? '<pre style="font-size:0.7rem;color:#6b7280;margin-top:8px;white-space:pre-wrap">' + data.raw_response + '</pre>' : '') +
      '</div>';
    document.getElementById('results').classList.add('active');
    return;
  }

  let html = '';
  const t = data.trend || {};
  const p = data.pattern_classification || {};
  const r = data.risk_assessment || {};
  const ind = data.indicators || {};
  const meta = data._meta || {};

  // Overview card
  html += '<div class="result-card">';
  html += '<h3>📊 Overview</h3>';
  html += row('Symbol', data.symbol || 'UNKNOWN');
  html += row('Timeframe', data.timeframe || 'UNKNOWN');
  if (data.price_current) html += row('Price', data.price_current);
  html += row('Trend', t.direction || '—', trendColor(t.direction));
  html += row('Strength', t.strength || '—');
  if (t.structure) html += row('Structure', t.structure);
  html += '</div>';

  // Pattern card
  html += '<div class="result-card">';
  html += '<h3>🔍 Pattern Classification</h3>';
  html += row('Pattern', (p.primary || 'none').toUpperCase());
  html += row('Confidence', '<span class="tag ' + tagClass(p.confidence) + '">' + (p.confidence || '—') + '</span>');
  if (p.description) html += '<div style="padding:8px 0;font-size:0.85rem;color:#d1d5db;line-height:1.4">' + p.description + '</div>';
  html += '</div>';

  // Candlestick patterns
  const candles = data.candlestick_patterns || [];
  if (candles.length > 0) {
    html += '<div class="result-card">';
    html += '<h3>🕯️ Candlestick Patterns</h3>';
    candles.forEach(c => {
      html += '<div style="padding:6px 0;border-bottom:1px solid rgba(255,255,255,0.04)">';
      html += '<span style="font-weight:600">' + c.name + '</span>';
      html += ' <span class="tag ' + tagClass(c.significance) + '">' + (c.significance || '') + '</span>';
      if (c.location) html += '<div style="font-size:0.75rem;color:#6b7280">' + c.location + '</div>';
      html += '</div>';
    });
    html += '</div>';
  }

  // Support / Resistance
  const sr = data.support_resistance || [];
  if (sr.length > 0) {
    html += '<div class="result-card">';
    html += '<h3>📏 Support & Resistance</h3>';
    sr.forEach(l => {
      const color = l.type === 'SUPPORT' ? 'bullish' : 'bearish';
      html += row(l.type, l.level + ' <span class="tag ' + tagClass(l.strength) + '">' + (l.strength || '') + '</span>', color);
    });
    html += '</div>';
  }

  // Indicators
  html += '<div class="result-card">';
  html += '<h3>📈 Indicators</h3>';
  if (ind.ema_crossover && ind.ema_crossover !== 'NOT_VISIBLE')
    html += row('EMA Crossover', ind.ema_crossover, trendColor(ind.ema_crossover));
  if (ind.rsi && ind.rsi.state !== 'NOT_VISIBLE') {
    let rsiText = ind.rsi.state || '—';
    if (ind.rsi.value) rsiText = ind.rsi.value + ' (' + rsiText + ')';
    if (ind.rsi.divergence && ind.rsi.divergence !== 'NONE' && ind.rsi.divergence !== 'NOT_VISIBLE')
      rsiText += ' ⚠️ ' + ind.rsi.divergence;
    html += row('RSI', rsiText);
  }
  if (ind.macd && ind.macd.state !== 'NOT_VISIBLE') {
    let macdText = ind.macd.state || '—';
    if (ind.macd.histogram && ind.macd.histogram !== 'NOT_VISIBLE') macdText += ' / Hist: ' + ind.macd.histogram;
    html += row('MACD', macdText, trendColor(ind.macd.state));
  }
  if (ind.bollinger && ind.bollinger.position !== 'NOT_VISIBLE')
    html += row('Bollinger', (ind.bollinger.position || '—') + ' / ' + (ind.bollinger.width || '—'));
  if (ind.ichimoku && ind.ichimoku.cloud !== 'NOT_VISIBLE')
    html += row('Ichimoku', 'Cloud: ' + (ind.ichimoku.cloud || '—'), trendColor(ind.ichimoku.color));
  if (ind.volume && ind.volume !== 'NOT_VISIBLE')
    html += row('Volume', ind.volume);
  if (ind.other) html += row('Other', ind.other);
  html += '</div>';

  // Risk Assessment
  html += '<div class="result-card">';
  html += '<h3>⚠️ Risk Assessment</h3>';
  html += row('Reversion Risk', bar(r.reversion_risk));
  html += row('Continuation', bar(r.continuation_probability));
  html += row('Volatility', r.volatility || '—');
  html += '</div>';

  // Key Levels
  const kl = data.key_levels || {};
  if (kl.nearest_resistance || kl.nearest_support) {
    html += '<div class="result-card">';
    html += '<h3>🎯 Key Levels</h3>';
    if (kl.nearest_resistance) html += row('Resistance', kl.nearest_resistance, 'bearish');
    if (kl.nearest_support) html += row('Support', kl.nearest_support, 'bullish');
    if (kl.pivot_point) html += row('Pivot', kl.pivot_point);
    html += '</div>';
  }

  // Observations
  const obs = data.observations || [];
  if (obs.length > 0) {
    html += '<div class="result-card">';
    html += '<h3>🧠 Observations</h3>';
    html += '<ul class="obs-list">';
    obs.forEach(o => { html += '<li>' + o + '</li>'; });
    html += '</ul>';
    html += '</div>';
  }

  // Meta
  if (meta.model) {
    html += '<div class="meta-line">' + meta.model + ' · ' + (meta.inference_time_s || '?') + 's · ' + (meta.analyzed_at || '') + '</div>';
  }

  // Raw JSON toggle
  html += '<div class="json-toggle"><button onclick="toggleJson()">Show Raw JSON</button></div>';
  html += '<pre class="json-raw" id="jsonRaw">' + JSON.stringify(data, null, 2) + '</pre>';

  document.getElementById('results').innerHTML = html;
  document.getElementById('results').classList.add('active');
  document.getElementById('results').scrollIntoView({ behavior: 'smooth' });
}

function row(label, value, colorClass) {
  return '<div class="result-row"><span class="label">' + label + '</span><span class="value' +
    (colorClass ? ' ' + colorClass : '') + '">' + value + '</span></div>';
}

function bar(val) {
  if (val === undefined || val === null) return '—';
  const pct = Math.round(val * 100);
  const color = pct > 70 ? '#ef4444' : pct > 40 ? '#f59e0b' : '#10b981';
  return '<div style="display:inline-flex;align-items:center;gap:8px">' +
    '<div style="width:80px;height:6px;background:#1f2937;border-radius:3px;overflow:hidden">' +
    '<div style="width:' + pct + '%;height:100%;background:' + color + ';border-radius:3px"></div></div>' +
    '<span>' + pct + '%</span></div>';
}

function toggleJson() {
  document.getElementById('jsonRaw').classList.toggle('active');
}
</script>
</body>
</html>"""


# ═══════════════════════════════════════════════════════════════════════════════
# ROUTES
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/", response_class=HTMLResponse)
async def upload_page():
    """Mobile-friendly chart upload page."""
    return UPLOAD_HTML


@app.get("/health")
async def health():
    return {
        "status": "online",
        "service": "NEO Chart Vision",
        "model": VISION_MODEL,
        "port": PORT,
    }


@app.post("/analyze")
async def analyze_chart(
    file: UploadFile = File(None),
    image_base64: str = Form(None),
    context: str = Form(""),
):
    """
    Analyze a chart image using Qwen3-VL.
    Accepts file upload OR base64-encoded image.
    Returns structured JSON analysis.
    """
    # Get image as base64
    if file:
        image_data = await file.read()
        if len(image_data) > 20 * 1024 * 1024:  # 20MB limit
            raise HTTPException(400, "Image too large (max 20MB)")
        img_b64 = base64.b64encode(image_data).decode("utf-8")
        logger.info(f"Received file upload: {file.filename} ({len(image_data)} bytes)")
    elif image_base64:
        # Strip data URL prefix if present
        if image_base64.startswith("data:"):
            img_b64 = image_base64.split(",", 1)[1] if "," in image_base64 else image_base64
        else:
            img_b64 = image_base64
        logger.info(f"Received base64 image ({len(img_b64)} chars)")
    else:
        raise HTTPException(400, "No image provided. Upload a file or send image_base64.")

    # Query vision model
    result = query_vision(img_b64, context)

    # Log analysis
    log_analysis(result, context)

    return JSONResponse(content=result)


@app.get("/history")
async def analysis_history(limit: int = 20):
    """Get recent chart analyses from disk."""
    files = sorted(DATA_DIR.glob("analysis_*.json"), reverse=True)[:limit]
    history = []
    for f in files:
        try:
            data = json.loads(f.read_text())
            history.append({
                "file": f.name,
                "symbol": data.get("symbol", "?"),
                "timeframe": data.get("timeframe", "?"),
                "pattern": data.get("pattern_classification", {}).get("primary", "?"),
                "trend": data.get("trend", {}).get("direction", "?"),
                "analyzed_at": data.get("_meta", {}).get("analyzed_at", "?"),
            })
        except:
            pass
    return {"count": len(history), "analyses": history}


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    logger.info(f"Starting NEO Chart Vision on port {PORT}")
    logger.info(f"Model: {VISION_MODEL} | Fallback: {VISION_FALLBACK}")
    logger.info(f"Open http://0.0.0.0:{PORT} to upload charts")
    uvicorn.run(app, host="0.0.0.0", port=PORT)
