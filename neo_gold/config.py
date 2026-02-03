"""
NEO-GOLD Configuration
Gold Trading Specialist - XAUUSD Only
"""

# ═══════════════════════════════════════════════════════════════════════
# CORE SETTINGS
# ═══════════════════════════════════════════════════════════════════════

SYMBOL = "XAUUSD"
MIN_CONFIDENCE = 70  # 70%+ is profitable territory
MAX_SIGNALS_PER_DAY = 10  # Quality over quantity
LLM_MODEL = "qwen2.5:32b"  # Proven reasoning model

# ═══════════════════════════════════════════════════════════════════════
# TRADING SESSION TIMES (UTC)
# ═══════════════════════════════════════════════════════════════════════

SESSIONS = {
    "ASIA": {"start": "00:00", "end": "07:00"},
    "LONDON": {"start": "07:00", "end": "15:00"},
    "NEW_YORK": {"start": "12:00", "end": "21:00"},
    "OVERLAP_LONDON_NY": {"start": "12:00", "end": "15:00"},
    "DEAD_ZONE": {"start": "21:00", "end": "00:00"}  # Low liquidity
}

# Session-specific rules
NO_TRADE_TIMES = [
    ("07:00", "07:15"),  # First 15 min of London - wait for direction
    ("21:00", "00:00"),  # Low liquidity dead zone
]

# ═══════════════════════════════════════════════════════════════════════
# ROUND NUMBERS - Gold's Psychological Levels
# ═══════════════════════════════════════════════════════════════════════

# Major levels (big $100 increments) - strongest magnets
MAJOR_ROUND_LEVELS = [2600, 2700, 2800, 2900, 3000]

# Minor levels ($50 increments) - also significant
MINOR_ROUND_LEVELS = [2650, 2750, 2850, 2950]

# Current price range for dynamic calculation
ROUND_NUMBER_PROXIMITY_PIPS = 50  # Within $5 of round number = near magnet

# ═══════════════════════════════════════════════════════════════════════
# PATTERN DETECTION THRESHOLDS
# ═══════════════════════════════════════════════════════════════════════

ASIAN_RANGE_BREAKOUT_THRESHOLD = 100  # If Asian range < 100 pips, expect breakout
SWEEP_REVERSAL_CANDLES = 5  # Must reverse within 5 candles to confirm sweep
NEWS_FADE_DELAY_MINUTES = 5  # Wait 5 min after news spike
TRIPLE_TAP_TOLERANCE_PIPS = 20  # How close tests must be to same level

# ═══════════════════════════════════════════════════════════════════════
# RISK MANAGEMENT
# ═══════════════════════════════════════════════════════════════════════

MAX_POSITION_SIZE_PCT = 5  # 5% of equity max
MIN_RISK_REWARD = 1.5  # Must have at least 1:1.5 R:R
DEFAULT_STOP_LOSS_PIPS = 75  # ~$7.50 default SL
DEFAULT_TAKE_PROFIT_PIPS = 150  # ~$15.00 default TP

# Size reduction during low liquidity
LOW_LIQUIDITY_SIZE_MULTIPLIER = 0.5  # Half size during dead zone

# ═══════════════════════════════════════════════════════════════════════
# CORRELATION THRESHOLDS
# ═══════════════════════════════════════════════════════════════════════

DXY_CORRELATION_THRESHOLD = 0.7  # Strong inverse correlation expected
SUSPICIOUS_DIVERGENCE_PIPS = 30  # If DXY moves 30+ pips and Gold doesn't react

# ═══════════════════════════════════════════════════════════════════════
# FILE PATHS
# ═══════════════════════════════════════════════════════════════════════

import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
LOGS_DIR = os.path.join(BASE_DIR, "logs")
KNOWLEDGE_DIR = os.path.join(BASE_DIR, "knowledge")

SIGNAL_FILE = "/tmp/neo_gold_signal.json"
STATE_FILE = os.path.join(BASE_DIR, "neo_gold_state.json")

# Historical data
GOLD_DATA_DIR = "/home/jbot/trading_ai/data/gold"

# ═══════════════════════════════════════════════════════════════════════
# API ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════

MT5_API_URL = os.getenv("MT5_API_URL", "http://localhost:8085")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")

# External data sources
FRANKFURTER_URL = "https://api.frankfurter.app/latest"
TWELVE_DATA_API_KEY = os.getenv("TWELVE_DATA_API_KEY", "")

# ═══════════════════════════════════════════════════════════════════════
# LOGGING
# ═══════════════════════════════════════════════════════════════════════

import logging
from datetime import datetime

log_file = os.path.join(LOGS_DIR, f"neo_gold_{datetime.now().strftime('%Y%m%d')}.log")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [NEO-GOLD] %(levelname)s: %(message)s',
    handlers=[
        logging.FileHandler(log_file),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger("NEO-GOLD")
