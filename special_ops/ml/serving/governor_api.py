"""
LoRA Governor API — Fine-tuned risk governance, replaces Ollama.
Port 8043 (8042 taken by Chart Vision). Falls back to Ollama if model unavailable.
"""

import os
import json
import logging
import torch
import requests
from pathlib import Path
from datetime import datetime, timezone
from fastapi import FastAPI
from pydantic import BaseModel
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s [GOV-API] %(message)s")
log = logging.getLogger(__name__)

ML_DIR = Path(__file__).resolve().parents[1]
LORA_DIR = ML_DIR / "models" / "governor_lora"
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:7b-instruct")

app = FastAPI(title="LoRA Governor", version="1.0")

model = None
tokenizer = None
model_meta = {}

SYSTEM_PROMPT = (
    "You are a risk governor for an automated XAUUSD trading system. "
    "Given current market state, decide: disable_new_entries (true/false), "
    "dca_step_multiplier (0.5-2.0), max_layers_cap (1-5), risk_bias (-1.0 to 1.0). "
    "Prioritize capital preservation. Tighten risk during high volatility. "
    "Respond with JSON first, then brief reasoning."
)


class GovernRequest(BaseModel):
    atr: float
    atr_pctl: float = 0.5
    atr_bucket: str = "mid"
    layers: int = 0
    direction: int = 0
    spread_points: float = 0
    equity: float = 400000
    net_lots: float = 0
    sentiment_regime: str = "neutral"
    event_risk: str = "low"
    intensity: float = 0.3


class GovernResponse(BaseModel):
    disable_new_entries: bool
    dca_step_multiplier: float
    max_layers_cap: int
    risk_bias: float
    reasoning: str
    source: str
    timestamp: str


def load_model():
    global model, tokenizer, model_meta

    if not LORA_DIR.exists() or not (LORA_DIR / "adapter_config.json").exists():
        log.warning(f"No LoRA adapter at {LORA_DIR} — will use Ollama fallback")
        return False

    from transformers import AutoTokenizer, AutoModelForCausalLM, BitsAndBytesConfig
    from peft import PeftModel

    meta_path = LORA_DIR / "training_meta.json"
    if meta_path.exists():
        with open(meta_path) as f:
            model_meta = json.load(f)

    base_model_name = model_meta.get("base_model", "NousResearch/Meta-Llama-3-8B-Instruct")

    bnb_config = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=torch.bfloat16,
    )

    log.info(f"Loading base model {base_model_name}...")
    tokenizer = AutoTokenizer.from_pretrained(base_model_name, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    base = AutoModelForCausalLM.from_pretrained(
        base_model_name,
        quantization_config=bnb_config,
        device_map={"": 0},
        trust_remote_code=True,
    )

    log.info("Loading LoRA adapter...")
    model = PeftModel.from_pretrained(base, str(LORA_DIR))
    model.eval()

    log.info(f"Governor model loaded: {model_meta.get('n_examples', '?')} training examples")
    return True


def query_ollama(prompt: str) -> str:
    """Fallback to Ollama if LoRA model isn't loaded."""
    try:
        resp = requests.post(
            f"{OLLAMA_URL}/api/generate",
            json={
                "model": OLLAMA_MODEL,
                "prompt": prompt,
                "system": SYSTEM_PROMPT,
                "stream": False,
                "options": {"temperature": 0.3, "num_predict": 256},
            },
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json().get("response", "")
    except Exception as e:
        log.error(f"Ollama fallback failed: {e}")
        return ""


def parse_governor_response(text: str) -> dict:
    """Extract control parameters from model output."""
    defaults = {
        "disable_new_entries": False,
        "dca_step_multiplier": 1.0,
        "max_layers_cap": 3,
        "risk_bias": 0.0,
        "reasoning": text[:200] if text else "No response",
    }

    try:
        start = text.index("{")
        end = text.index("}", start) + 1
        parsed = json.loads(text[start:end])
        defaults.update({
            "disable_new_entries": bool(parsed.get("disable_new_entries", False)),
            "dca_step_multiplier": float(min(2.0, max(0.5, parsed.get("dca_step_multiplier", 1.0)))),
            "max_layers_cap": int(min(5, max(1, parsed.get("max_layers_cap", 3)))),
            "risk_bias": float(min(1.0, max(-1.0, parsed.get("risk_bias", 0.0)))),
        })
        reasoning_start = end
        if reasoning_start < len(text):
            reasoning = text[reasoning_start:].strip()
            if reasoning.startswith("Reasoning:"):
                reasoning = reasoning[len("Reasoning:"):].strip()
            if reasoning:
                defaults["reasoning"] = reasoning[:300]
    except (ValueError, json.JSONDecodeError):
        pass

    return defaults


def build_prompt(req: GovernRequest) -> str:
    return (
        f"Market State:\n"
        f"- ATR: {req.atr:.1f} (percentile: {req.atr_pctl:.2f}, bucket: {req.atr_bucket})\n"
        f"- Active layers: {req.layers}, Direction: {'LONG' if req.direction > 0 else 'SHORT'}\n"
        f"- Spread: {req.spread_points:.0f} points\n"
        f"- Sentiment: {req.sentiment_regime} (intensity: {req.intensity:.1f}, event_risk: {req.event_risk})\n"
        f"- Equity: ${req.equity:,.0f}, Net lots: {req.net_lots:.2f}\n"
        f"\nWhat risk controls should be applied?"
    )


@app.on_event("startup")
async def startup():
    load_model()
    log.info("LoRA Governor API ready on port 8043")


@app.get("/health")
async def health():
    return {
        "status": "loaded" if model is not None else "ollama_fallback",
        "lora_dir": str(LORA_DIR),
        "model_meta": model_meta,
        "ollama_model": OLLAMA_MODEL,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.post("/govern", response_model=GovernResponse)
async def govern(req: GovernRequest):
    prompt = build_prompt(req)
    source = "unknown"

    if model is not None and tokenizer is not None:
        full_prompt = f"<|system|>\n{SYSTEM_PROMPT}\n<|user|>\n{prompt}\n<|assistant|>\n"
        inputs = tokenizer(full_prompt, return_tensors="pt", truncation=True, max_length=512)
        inputs = {k: v.to(model.device) for k, v in inputs.items()}

        with torch.no_grad():
            outputs = model.generate(
                **inputs,
                max_new_tokens=256,
                temperature=0.3,
                do_sample=True,
                pad_token_id=tokenizer.eos_token_id,
            )
        response_text = tokenizer.decode(outputs[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True)
        source = "lora_governor"
    else:
        response_text = query_ollama(prompt)
        source = "ollama_fallback"

    parsed = parse_governor_response(response_text)

    return GovernResponse(
        disable_new_entries=parsed["disable_new_entries"],
        dca_step_multiplier=parsed["dca_step_multiplier"],
        max_layers_cap=parsed["max_layers_cap"],
        risk_bias=parsed["risk_bias"],
        reasoning=parsed["reasoning"],
        source=source,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


@app.post("/reload")
async def reload():
    success = load_model()
    return {"reloaded": success, "model_meta": model_meta, "source": "lora" if success else "ollama_fallback"}
