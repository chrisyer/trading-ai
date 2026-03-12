#!/usr/bin/env python3
"""
NEO Configuration - Autonomous LLM Trader
All settings in one place. NO RANDOM DATA.
"""

import os
from pathlib import Path

# Base directories
NEO_DIR = Path(os.path.dirname(os.path.abspath(__file__)))
SIGNALS_DIR = NEO_DIR / "signals"
LOGS_DIR = NEO_DIR / "logs"

# Ensure directories exist
SIGNALS_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)

# Database
DATABASE_PATH = NEO_DIR / "neo_memory.db"

# Signal output (Ghost Commander reads this)
SIGNAL_FILE = Path("/tmp/neo_signal.json")
SIGNAL_HISTORY_DIR = SIGNALS_DIR

# Account settings
ACCOUNT_SIZE = 88000  # USD
MAX_POSITION_PCT = 5.0  # Max 5% per position ($4,400)
MAX_DAILY_LOSS_PCT = 3.0  # Kill switch at 3% daily loss ($2,640)
MAX_OPEN_POSITIONS = 4  # Max concurrent positions

# Calculated limits
MAX_POSITION_DOLLARS = ACCOUNT_SIZE * (MAX_POSITION_PCT / 100)
MAX_DAILY_LOSS_DOLLARS = ACCOUNT_SIZE * (MAX_DAILY_LOSS_PCT / 100)

# Data sources (no API keys needed)
MT5_API_URL = "http://localhost:8085"  # Local MT5 trades API
COINGECKO_URL = "https://api.coingecko.com/api/v3"
FRANKFURTER_URL = "https://api.frankfurter.app"

# Trading pairs to monitor
FOREX_PAIRS = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF"]
CRYPTO_PAIRS = ["BTC", "ETH", "SOL"]  # Tracked for correlation/sentiment

# LLM Configuration - Updated Jan 22, 2026
# Switched to qwen2.5:32b (faster, newer, better at trading)
LLM_CONFIG = {
    "primary": {
        "model": "qwen2.5:32b",  # Newest model (7 days old), fast, good at trading
        "timeout": 60,
        "temperature": 0.2  # Low for consistency
    },
    "backup": {
        "model": "qwen3:32b",  # Good analysis fallback
        "timeout": 90,
        "temperature": 0.3
    },
    "fast": {
        "model": "llama3.1:8b",  # Quick decisions
        "timeout": 30,
        "temperature": 0.2
    },
    "consensus": [
        "qwen2.5:32b",
        "qwen3:32b",
        "gemma3:27b"
    ]
}

# NEO loop timing
THINK_INTERVAL_SECONDS = 60  # How often NEO analyzes
LEARN_INTERVAL_SECONDS = 300  # How often NEO reviews outcomes
HEARTBEAT_INTERVAL_SECONDS = 10  # Health check

# Safety settings
KILL_SWITCH_ENABLED = True
LOG_ALL_DECISIONS = True
REQUIRE_REASONING = True  # Must explain every decision

# Strategy parameters (from proven strategies database)
PROVEN_PARAMETERS = {
    "rsi_period": 2,  # Connors RSI(2)
    "rsi_oversold": 10,
    "rsi_overbought": 70,
    "atr_period": 20,  # Turtle
    "atr_stop_multiplier": 2.0,
    "trend_sma_period": 200,
    "exit_sma_period": 5
}

# Ollama endpoint
OLLAMA_URL = "http://localhost:11434"

# ---------------------------------------------------------------------------
# LLM Provider Configuration
# Set LLM_PROVIDER env var to switch between providers:
#   ollama    - local Ollama (default, free)
#   deepseek  - DeepSeek API (cheapest cloud, $0.14/M tokens)
#   anthropic - Claude API (best analysis quality)
#   openai    - OpenAI GPT-4o
#
# Example:
#   export LLM_PROVIDER=deepseek
#   export DEEPSEEK_API_KEY=sk-xxx
# ---------------------------------------------------------------------------
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "ollama")

# API keys (set via environment variables, never hardcode)
OPENAI_API_KEY    = os.getenv("OPENAI_API_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
DEEPSEEK_API_KEY  = os.getenv("DEEPSEEK_API_KEY", "")

# Per-provider model defaults (override via env vars)
PROVIDER_MODELS = {
    "ollama": {
        "primary": os.getenv("OLLAMA_PRIMARY_MODEL", "qwen2.5:32b"),
        "backup":  os.getenv("OLLAMA_BACKUP_MODEL",  "qwen3:32b"),
        "fast":    os.getenv("OLLAMA_FAST_MODEL",    "llama3.1:8b"),
    },
    "openai": {
        "primary": os.getenv("OPENAI_PRIMARY_MODEL", "gpt-4o"),
        "backup":  os.getenv("OPENAI_BACKUP_MODEL",  "gpt-4o-mini"),
        "fast":    os.getenv("OPENAI_FAST_MODEL",    "gpt-4o-mini"),
    },
    "anthropic": {
        "primary": os.getenv("ANTHROPIC_PRIMARY_MODEL", "claude-sonnet-4-6"),
        "backup":  os.getenv("ANTHROPIC_BACKUP_MODEL",  "claude-haiku-4-5-20251001"),
        "fast":    os.getenv("ANTHROPIC_FAST_MODEL",    "claude-haiku-4-5-20251001"),
    },
    "deepseek": {
        "primary": os.getenv("DEEPSEEK_PRIMARY_MODEL", "deepseek-reasoner"),
        "backup":  os.getenv("DEEPSEEK_BACKUP_MODEL",  "deepseek-chat"),
        "fast":    os.getenv("DEEPSEEK_FAST_MODEL",    "deepseek-chat"),
    },
}

# User agent for web requests
USER_AGENT = "Mozilla/5.0 (compatible; NEO-Trader/1.0)"
