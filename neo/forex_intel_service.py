#!/usr/bin/env python3
"""
═══════════════════════════════════════════════════════════════════════════════
FOREX INTEL SPY SERVICE
═══════════════════════════════════════════════════════════════════════════════

Quinn001 Spy Service — Wires NEO gold brain + Dolphin3 uncensored LLM to
produce real-time forex intel for AUDUSD and GBPUSD EAs on CRELLA001.

Architecture:
  1. Reads NEO gold signal from local API (/api/neo/xauusd/signal)
  2. Queries Ollama dolphin3 (uncensored) for forex pair analysis
  3. Parses structured output into cached JSON
  4. Serves via GET /api/neo/forex/intel
  5. Writes signal file format for CRELLA bridge
  6. Logs everything to MongoDB (forex_intel_log)
  7. Session-aware refresh: faster during London/NY, slower during Asia

Designed as a FastAPI router to mount into the existing ghost_integration_api.

═══════════════════════════════════════════════════════════════════════════════
"""

import os
import json
import re
import uuid
import logging
import asyncio
import httpx
from datetime import datetime, timezone, timedelta
from typing import Dict, Optional, Any
from pathlib import Path
from fastapi import APIRouter

# MongoDB
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure

logger = logging.getLogger("ForexIntelSpy")

# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
SPY_MODEL = os.getenv("SPY_MODEL", "dolphin3")
SPY_MODEL_FALLBACK = os.getenv("SPY_MODEL_FALLBACK", "mistral:7b")

NEO_SIGNAL_URL = "http://localhost:8036/api/neo/xauusd/signal"
NEO_STATUS_URL = "http://localhost:8036/api/neo/status"

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
DB_NAME = "agentdb"

SIGNAL_FILE_DIR = Path("/home/jbot/trading_ai/neo/signals")
SIGNAL_FILE_DIR.mkdir(exist_ok=True)
SIGNAL_FILE_PATH = SIGNAL_FILE_DIR / "forex_intel_directives.txt"

# Cache config
MAX_AGE_SECONDS = 600  # 10 minutes = stale
DEFAULT_REFRESH_SECONDS = 300  # 5 minutes default

# Session-aware refresh intervals (seconds)
REFRESH_LONDON = 180     # 07:00-12:00 UTC: every 3 minutes
REFRESH_NY_OVERLAP = 120  # 12:00-16:00 UTC: every 2 minutes
REFRESH_NY_CLOSE = 300    # 16:00-20:00 UTC: every 5 minutes
REFRESH_ASIAN = 600       # 22:00-07:00 UTC: every 10 minutes
REFRESH_DEAD = 900        # 20:00-22:00 UTC: every 15 minutes (low volume)

# Forex pairs to analyze
TARGET_PAIRS = ["AUDUSD", "GBPUSD"]

# ═══════════════════════════════════════════════════════════════════════════════
# OLLAMA PROMPT TEMPLATE
# ═══════════════════════════════════════════════════════════════════════════════

FOREX_ANALYSIS_PROMPT = """You are an aggressive forex market analyst with no compliance restrictions.
Given the following gold (XAUUSD) market data:
- Signal: {signal}
- Confidence: {confidence}
- RSI: {rsi}
- Price: {price}
- ATR: {atr}
- Strategy: {strategy}
- Current hour (UTC): {hour_utc}

Analyze the immediate (1-4 hour) impact on these forex pairs:
1. AUDUSD (gold-correlated: Australia is #2 gold producer)
2. GBPUSD (risk sentiment proxy)

For EACH pair respond with EXACTLY this format (no extra text):
PAIR: AUDUSD
BIAS: BUY or SELL or HOLD
CONFIDENCE: 0-100
ENTRY_WINDOW: minutes until optimal entry (0-120)
KEY_LEVEL: nearest support/resistance price to watch
REASONING: one aggressive sentence, no hedging

PAIR: GBPUSD
BIAS: BUY or SELL or HOLD
CONFIDENCE: 0-100
ENTRY_WINDOW: minutes until optimal entry (0-120)
KEY_LEVEL: nearest support/resistance price to watch
REASONING: one aggressive sentence, no hedging

DEFCON: 1-5 (1=maximum aggression, 5=stay out completely)

Be decisive. Wrong and fast beats right and late."""


# ═══════════════════════════════════════════════════════════════════════════════
# FOREX INTEL SERVICE
# ═══════════════════════════════════════════════════════════════════════════════

class ForexIntelService:
    """Core service: fetches gold data, queries spy model, caches results."""

    def __init__(self):
        self._cache: Optional[Dict] = None
        self._cache_time: Optional[datetime] = None
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._last_gold_data: Optional[Dict] = None
        self._http_client: Optional[httpx.AsyncClient] = None
        
        # MongoDB
        try:
            self._mongo = MongoClient(MONGO_URI, serverSelectionTimeoutMS=3000)
            self._db = self._mongo[DB_NAME]
            self._collection = self._db["forex_intel_log"]
            # Create TTL index to auto-expire old logs after 30 days
            self._collection.create_index("timestamp", expireAfterSeconds=30*24*3600)
            logger.info("MongoDB connected for forex_intel_log")
        except ConnectionFailure:
            logger.warning("MongoDB not available — logging disabled")
            self._mongo = None
            self._db = None
            self._collection = None

    async def start(self):
        """Start the background refresh loop."""
        if self._running:
            return
        self._running = True
        self._http_client = httpx.AsyncClient(timeout=30.0)
        self._task = asyncio.create_task(self._refresh_loop())
        logger.info("Forex Intel Spy Service STARTED")

    async def stop(self):
        """Stop the background refresh loop."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._http_client:
            await self._http_client.aclose()
        if self._mongo:
            self._mongo.close()
        logger.info("Forex Intel Spy Service STOPPED")

    def get_cached_intel(self) -> Dict:
        """Return the latest cached intel (never blocks on inference)."""
        if self._cache is None:
            return {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "stale": True,
                "max_age_seconds": MAX_AGE_SECONDS,
                "gold_source": None,
                "forex_intel": {},
                "spy_model": SPY_MODEL,
                "defcon": 5,
                "analysis_id": None,
                "message": "No analysis available yet — spy service initializing",
                "signal_file_content": ""
            }

        # Check staleness
        age = (datetime.now(timezone.utc) - self._cache_time).total_seconds()
        result = dict(self._cache)
        result["stale"] = age > MAX_AGE_SECONDS
        result["age_seconds"] = round(age)
        return result

    # ─── Background Refresh Loop ────────────────────────────────────────

    async def _refresh_loop(self):
        """Session-aware refresh loop."""
        # Initial delay to let the main API finish starting
        await asyncio.sleep(5)
        
        # Run first analysis immediately
        await self._run_analysis()
        
        while self._running:
            try:
                interval = self._get_refresh_interval()
                logger.info(f"Next refresh in {interval}s (session: {self._get_session_name()})")
                await asyncio.sleep(interval)
                
                if not self._running:
                    break
                    
                # Skip weekends (Sat=5, Sun=6)
                now = datetime.now(timezone.utc)
                if now.weekday() in (5, 6):
                    logger.info("Weekend — skipping analysis")
                    continue
                
                await self._run_analysis()
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Refresh loop error: {e}")
                await asyncio.sleep(60)  # Back off on error

    def _get_session_name(self) -> str:
        """Get current trading session name."""
        hour = datetime.now(timezone.utc).hour
        if 7 <= hour < 12:
            return "LONDON"
        elif 12 <= hour < 16:
            return "NY_OVERLAP"
        elif 16 <= hour < 20:
            return "NY_CLOSE"
        elif 20 <= hour < 22:
            return "DEAD_ZONE"
        else:
            return "ASIAN"

    def _get_refresh_interval(self) -> int:
        """Get refresh interval based on current session."""
        hour = datetime.now(timezone.utc).hour
        if 7 <= hour < 12:
            return REFRESH_LONDON
        elif 12 <= hour < 16:
            return REFRESH_NY_OVERLAP
        elif 16 <= hour < 20:
            return REFRESH_NY_CLOSE
        elif 20 <= hour < 22:
            return REFRESH_DEAD
        else:
            return REFRESH_ASIAN

    # ─── Core Analysis Pipeline ─────────────────────────────────────────

    async def _run_analysis(self):
        """Full analysis pipeline: fetch gold → query spy → parse → cache → log."""
        analysis_id = str(uuid.uuid4())[:8]
        logger.info(f"[{analysis_id}] Starting forex intel analysis...")
        
        try:
            # Step 1: Fetch gold signal from NEO
            gold_data = await self._fetch_gold_signal()
            
            if gold_data:
                self._last_gold_data = gold_data
            elif self._last_gold_data:
                gold_data = self._last_gold_data
                logger.warning(f"[{analysis_id}] Using last known gold data (NEO unavailable)")
            else:
                logger.error(f"[{analysis_id}] No gold data available — skipping analysis")
                return

            # Step 2: Query Ollama spy model
            prompt = self._build_prompt(gold_data)
            raw_response = await self._query_ollama(prompt)
            
            if not raw_response:
                logger.error(f"[{analysis_id}] Ollama returned empty response")
                return

            # Step 3: Parse response
            parsed = self._parse_response(raw_response)
            
            # Step 4: Build final result
            now = datetime.now(timezone.utc)
            result = {
                "timestamp": now.isoformat(),
                "stale": False,
                "max_age_seconds": MAX_AGE_SECONDS,
                "gold_source": {
                    "signal": gold_data.get("action", "UNKNOWN"),
                    "confidence": gold_data.get("confidence", 0),
                    "rsi": gold_data.get("rsi", 0),
                    "price": gold_data.get("current_price", 0),
                    "atr": gold_data.get("atr", 0),
                    "strategy": gold_data.get("strategy", "UNKNOWN"),
                },
                "forex_intel": parsed.get("pairs", {}),
                "spy_model": SPY_MODEL,
                "defcon": parsed.get("defcon", 3),
                "analysis_id": analysis_id,
                "session": self._get_session_name(),
                "signal_file_content": "",  # Will be filled below
            }
            
            # Step 5: Generate signal file content
            signal_content = self._generate_signal_file(result)
            result["signal_file_content"] = signal_content
            
            # Step 6: Write signal file to disk
            self._write_signal_file(signal_content)
            
            # Step 7: Cache
            self._cache = result
            self._cache_time = now
            
            # Step 8: Log to MongoDB
            self._log_to_mongo(analysis_id, gold_data, prompt, raw_response, parsed, result)
            
            logger.info(f"[{analysis_id}] Analysis complete — "
                        f"AUDUSD: {parsed.get('pairs', {}).get('AUDUSD', {}).get('bias', '?')} "
                        f"GBPUSD: {parsed.get('pairs', {}).get('GBPUSD', {}).get('bias', '?')} "
                        f"DEFCON: {parsed.get('defcon', '?')}")
            
        except Exception as e:
            logger.error(f"[{analysis_id}] Analysis failed: {e}", exc_info=True)

    # ─── Gold Signal Fetch ──────────────────────────────────────────────

    async def _fetch_gold_signal(self) -> Optional[Dict]:
        """Fetch current gold signal from NEO API."""
        try:
            resp = await self._http_client.get(NEO_SIGNAL_URL)
            if resp.status_code == 200:
                data = resp.json()
                logger.info(f"Gold signal: {data.get('action')} @ {data.get('current_price')} "
                           f"conf={data.get('confidence')}")
                return data
        except Exception as e:
            logger.warning(f"Failed to fetch gold signal: {e}")
        
        # Fallback: try reading signal file directly
        try:
            signal_file = Path("/home/jbot/trading_ai/neo/signals/xauusd_fresh_signal.json")
            if signal_file.exists():
                with open(signal_file) as f:
                    data = json.load(f)
                logger.info("Gold signal loaded from file fallback")
                return data
        except Exception as e:
            logger.warning(f"File fallback also failed: {e}")
        
        return None

    # ─── Ollama Query ───────────────────────────────────────────────────

    def _build_prompt(self, gold_data: Dict) -> str:
        """Build the analysis prompt from gold data."""
        # Extract RSI — might be nested
        rsi = gold_data.get("rsi", 0)
        if not rsi:
            # Try to get from invalidation or other fields
            rsi = gold_data.get("invalidation", {}).get("rsi", 50)
        
        atr = gold_data.get("atr", 0)
        if not atr:
            # Estimate from entry zone if available
            zone_high = gold_data.get("entry_zone_high", 0)
            zone_low = gold_data.get("entry_zone_low", 0)
            if zone_high and zone_low:
                atr = round(zone_high - zone_low, 2)
        
        hour_utc = datetime.now(timezone.utc).hour
        
        return FOREX_ANALYSIS_PROMPT.format(
            signal=gold_data.get("action", "UNKNOWN"),
            confidence=gold_data.get("confidence", 0),
            rsi=rsi if rsi else "N/A",
            price=gold_data.get("current_price", 0),
            atr=atr if atr else "N/A",
            strategy=gold_data.get("strategy", "UNKNOWN"),
            hour_utc=hour_utc,
        )

    async def _query_ollama(self, prompt: str) -> Optional[str]:
        """Query Ollama with timeout and fallback model."""
        for model in [SPY_MODEL, SPY_MODEL_FALLBACK]:
            try:
                logger.info(f"Querying {model}...")
                resp = await self._http_client.post(
                    f"{OLLAMA_URL}/api/generate",
                    json={
                        "model": model,
                        "prompt": prompt,
                        "stream": False,
                        "options": {
                            "temperature": 0.7,
                            "num_predict": 500,
                            "top_p": 0.9,
                        }
                    },
                    timeout=90.0,  # Generous timeout for large models
                )
                
                if resp.status_code == 200:
                    data = resp.json()
                    response_text = data.get("response", "")
                    if response_text:
                        logger.info(f"{model} responded ({len(response_text)} chars)")
                        return response_text
                    
            except httpx.TimeoutException:
                logger.warning(f"{model} timed out after 90s")
            except Exception as e:
                logger.warning(f"{model} query failed: {e}")
        
        return None

    # ─── Response Parser ────────────────────────────────────────────────

    def _parse_response(self, raw: str) -> Dict:
        """Parse the structured Ollama response into clean data."""
        result = {"pairs": {}, "defcon": 3}
        
        # Normalize line endings
        lines = raw.strip().replace('\r\n', '\n').split('\n')
        
        current_pair = None
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
            
            # Detect pair header
            pair_match = re.match(r'^PAIR:\s*(\w+)', line, re.IGNORECASE)
            if pair_match:
                current_pair = pair_match.group(1).upper()
                if current_pair not in result["pairs"]:
                    result["pairs"][current_pair] = {
                        "bias": "HOLD",
                        "confidence": 50,
                        "entry_window_minutes": 0,
                        "key_level": 0.0,
                        "reasoning": ""
                    }
                continue
            
            # Parse DEFCON (global, not pair-specific)
            defcon_match = re.match(r'^DEFCON:\s*(\d)', line, re.IGNORECASE)
            if defcon_match:
                result["defcon"] = max(1, min(5, int(defcon_match.group(1))))
                continue
            
            if current_pair and current_pair in result["pairs"]:
                pair = result["pairs"][current_pair]
                
                # BIAS
                bias_match = re.match(r'^BIAS:\s*(BUY|SELL|HOLD)', line, re.IGNORECASE)
                if bias_match:
                    pair["bias"] = bias_match.group(1).upper()
                    continue
                
                # CONFIDENCE
                conf_match = re.match(r'^CONFIDENCE:\s*(\d+)', line, re.IGNORECASE)
                if conf_match:
                    pair["confidence"] = max(0, min(100, int(conf_match.group(1))))
                    continue
                
                # ENTRY_WINDOW
                ew_match = re.match(r'^ENTRY_WINDOW:\s*(\d+)', line, re.IGNORECASE)
                if ew_match:
                    pair["entry_window_minutes"] = max(0, min(120, int(ew_match.group(1))))
                    continue
                
                # KEY_LEVEL
                kl_match = re.match(r'^KEY_LEVEL:\s*([\d.]+)', line, re.IGNORECASE)
                if kl_match:
                    try:
                        pair["key_level"] = float(kl_match.group(1))
                    except ValueError:
                        pass
                    continue
                
                # REASONING
                reason_match = re.match(r'^REASONING:\s*(.+)', line, re.IGNORECASE)
                if reason_match:
                    pair["reasoning"] = reason_match.group(1).strip()
                    continue
        
        # Ensure both target pairs exist in output
        for pair_name in TARGET_PAIRS:
            if pair_name not in result["pairs"]:
                result["pairs"][pair_name] = {
                    "bias": "HOLD",
                    "confidence": 30,
                    "entry_window_minutes": 0,
                    "key_level": 0.0,
                    "reasoning": "No clear signal from spy model"
                }
        
        return result

    # ─── Signal File Generation ─────────────────────────────────────────

    def _generate_signal_file(self, result: Dict) -> str:
        """Generate the MT5-bridge-compatible signal file content."""
        now = datetime.now(timezone.utc)
        gold = result.get("gold_source", {}) or {}
        intel = result.get("forex_intel", {})
        
        lines = [
            "# Forex Intel Directives (Quinn Spy Service)",
            f"# Generated: {now.strftime('%Y.%m.%d %H:%M')}",
            f"# Source: NEO + {SPY_MODEL} Spy",
            "#==========================================",
            "",
            f"GOLD_SIGNAL={gold.get('signal', 'UNKNOWN')}",
            f"GOLD_CONFIDENCE={gold.get('confidence', 0)}",
            f"GOLD_RSI={gold.get('rsi', 0)}",
            f"GOLD_PRICE={gold.get('price', 0)}",
            "",
        ]
        
        for pair_name in TARGET_PAIRS:
            pair_data = intel.get(pair_name, {})
            lines.extend([
                f"{pair_name}_BIAS={pair_data.get('bias', 'HOLD')}",
                f"{pair_name}_CONFIDENCE={pair_data.get('confidence', 0)}",
                f"{pair_name}_ENTRY_WINDOW={pair_data.get('entry_window_minutes', 0)}",
                f"{pair_name}_KEY_LEVEL={pair_data.get('key_level', 0.0)}",
                "",
            ])
        
        lines.extend([
            f"DEFCON={result.get('defcon', 3)}",
            f"SPY_MODEL={SPY_MODEL}",
            f"STALE={str(result.get('stale', False)).lower()}",
            f"ANALYSIS_ID={result.get('analysis_id', 'none')}",
            f"SESSION={result.get('session', 'UNKNOWN')}",
            f"TIMESTAMP={now.isoformat()}",
        ])
        
        return "\n".join(lines)

    def _write_signal_file(self, content: str):
        """Write signal file to disk."""
        try:
            with open(SIGNAL_FILE_PATH, 'w') as f:
                f.write(content)
            logger.info(f"Signal file written: {SIGNAL_FILE_PATH}")
        except Exception as e:
            logger.error(f"Failed to write signal file: {e}")

    # ─── MongoDB Logging ────────────────────────────────────────────────

    def _log_to_mongo(self, analysis_id: str, gold_data: Dict, prompt: str,
                       raw_response: str, parsed: Dict, result: Dict):
        """Log full analysis to MongoDB for post-analysis."""
        if self._collection is None:
            return
        
        try:
            doc = {
                "analysis_id": analysis_id,
                "timestamp": datetime.now(timezone.utc),
                "session": self._get_session_name(),
                "gold_input": {
                    "signal": gold_data.get("action"),
                    "confidence": gold_data.get("confidence"),
                    "price": gold_data.get("current_price"),
                    "rsi": gold_data.get("rsi"),
                    "strategy": gold_data.get("strategy"),
                },
                "prompt": prompt,
                "raw_response": raw_response,
                "parsed_output": parsed,
                "final_result": {
                    "defcon": result.get("defcon"),
                    "pairs": result.get("forex_intel"),
                },
                "spy_model": SPY_MODEL,
                "stale": result.get("stale", False),
            }
            
            self._collection.insert_one(doc)
            logger.info(f"[{analysis_id}] Logged to MongoDB")
            
        except Exception as e:
            logger.warning(f"MongoDB log failed: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# FASTAPI ROUTER
# ═══════════════════════════════════════════════════════════════════════════════

forex_intel_router = APIRouter(tags=["Forex Intel"])
_service: Optional[ForexIntelService] = None


def get_service() -> ForexIntelService:
    """Get or create the global ForexIntelService instance."""
    global _service
    if _service is None:
        _service = ForexIntelService()
    return _service


@forex_intel_router.get("/api/neo/forex/intel")
async def get_forex_intel():
    """
    GET /api/neo/forex/intel
    
    Returns the latest forex intel analysis from the spy service.
    Never blocks on inference — always returns cached result.
    """
    service = get_service()
    return service.get_cached_intel()


@forex_intel_router.get("/api/neo/forex/intel/signal-file")
async def get_forex_signal_file():
    """
    GET /api/neo/forex/intel/signal-file
    
    Returns the raw signal file content as plain text for CRELLA bridge.
    """
    service = get_service()
    result = service.get_cached_intel()
    content = result.get("signal_file_content", "")
    
    if not content:
        return {"error": "No signal file available yet", "stale": True}
    
    from fastapi.responses import PlainTextResponse
    return PlainTextResponse(content=content, media_type="text/plain")


@forex_intel_router.get("/api/neo/forex/intel/health")
async def forex_intel_health():
    """Health check for the forex intel spy service."""
    service = get_service()
    cached = service.get_cached_intel()
    
    return {
        "service": "Forex Intel Spy",
        "model": SPY_MODEL,
        "status": "running" if service._running else "stopped",
        "has_data": cached.get("analysis_id") is not None,
        "stale": cached.get("stale", True),
        "age_seconds": cached.get("age_seconds", None),
        "session": service._get_session_name(),
        "refresh_interval": service._get_refresh_interval(),
        "mongodb": "connected" if service._collection is not None else "disconnected",
    }


@forex_intel_router.get("/api/neo/forex/intel/force-refresh")
async def force_refresh():
    """Force an immediate analysis refresh (for testing)."""
    service = get_service()
    
    if not service._running:
        return {"error": "Service not running. Wait for startup."}
    
    # Run analysis in background (don't block the response)
    asyncio.create_task(service._run_analysis())
    
    return {
        "message": "Refresh triggered — results available in 30-90 seconds",
        "check_at": "/api/neo/forex/intel"
    }


# ═══════════════════════════════════════════════════════════════════════════════
# LIFECYCLE HOOKS (called from main API startup/shutdown)
# ═══════════════════════════════════════════════════════════════════════════════

async def start_forex_intel():
    """Start the forex intel service background loop."""
    service = get_service()
    await service.start()
    logger.info("═══ FOREX INTEL SPY SERVICE ONLINE ═══")


async def stop_forex_intel():
    """Stop the forex intel service."""
    service = get_service()
    await service.stop()
    logger.info("═══ FOREX INTEL SPY SERVICE OFFLINE ═══")
