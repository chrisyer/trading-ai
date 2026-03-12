#!/usr/bin/env python3
"""
NEO Multi-Model Consensus System

For high-stakes decisions, consult multiple LLMs.
Agreement = higher confidence
Disagreement = caution / WAIT

Like having a board of directors vote on major decisions.
"""

import json
import time
from datetime import datetime
from typing import Dict, List, Any, Optional
import sys
sys.path.append('..')
from llm_adapter import call_llm, get_active_provider, PROVIDER_MODELS

# Models to consult (in order of preference).
# When using a cloud provider the model names are resolved automatically
# from PROVIDER_MODELS; when using ollama the ollama names are used.
_OLLAMA_CONSENSUS_MODELS = [
    {"name": "deepseek-r1:70b", "weight": 1.5, "timeout": 120},
    {"name": "qwen3:32b",       "weight": 1.0, "timeout": 90},
    {"name": "llama3.1:70b",    "weight": 1.2, "timeout": 120},
]


def _get_consensus_models() -> List[Dict]:
    """Return consensus model list for the active provider."""
    provider = get_active_provider()
    if provider == "ollama":
        return _OLLAMA_CONSENSUS_MODELS
    # For cloud providers use primary + backup (2-model consensus)
    models = PROVIDER_MODELS.get(provider, PROVIDER_MODELS["openai"])
    return [
        {"name": models["primary"], "weight": 1.5, "timeout": 120},
        {"name": models["backup"],  "weight": 1.0, "timeout": 90},
    ]


CONSENSUS_MODELS = _OLLAMA_CONSENSUS_MODELS  # kept for backwards-compat imports


def call_model(model: str, prompt: str, timeout: int = 120) -> Optional[str]:
    """Call a single model via the unified LLM adapter."""
    try:
        result = call_llm(prompt=prompt, model=model, timeout=timeout)
        return result if result else None
    except Exception as e:
        print(f"  ❌ {model} error: {e}")
        return None


def parse_decision(response: str) -> Optional[Dict]:
    """Extract JSON decision from model response."""
    if not response:
        return None
    
    try:
        # Find JSON block
        start = response.find('{')
        end = response.rfind('}') + 1
        if start >= 0 and end > start:
            return json.loads(response[start:end])
    except json.JSONDecodeError:
        pass
    
    # Try to extract action from text
    response_upper = response.upper()
    if "WAIT" in response_upper:
        return {"decision": {"action": "WAIT"}}
    elif "BUY" in response_upper:
        return {"decision": {"action": "BUY"}}
    elif "SELL" in response_upper:
        return {"decision": {"action": "SELL"}}
    
    return None


def get_consensus(prompt: str, min_models: int = 2) -> Dict[str, Any]:
    """
    Query multiple models and determine consensus.
    
    Returns:
        {
            "consensus": True/False,
            "action": "BUY" | "SELL" | "WAIT",
            "confidence_adjustment": int (+10, 0, -20),
            "models_agreed": ["model1", "model2"],
            "models_disagreed": ["model3"],
            "note": "Explanation"
        }
    """
    provider = get_active_provider()
    consensus_models = _get_consensus_models()
    print(f"🧠 Consulting multiple models for consensus (provider={provider})...")

    decisions = []

    for model_config in consensus_models:
        model_name = model_config["name"]
        timeout = model_config["timeout"]
        weight = model_config["weight"]
        
        print(f"  📡 Asking {model_name}...")
        start_time = time.time()
        
        response = call_model(model_name, prompt, timeout)
        elapsed = time.time() - start_time
        
        if response:
            decision = parse_decision(response)
            if decision:
                action = decision.get("decision", {}).get("action", "UNKNOWN")
                confidence = decision.get("decision", {}).get("confidence", 50)
                
                decisions.append({
                    "model": model_name,
                    "action": action,
                    "confidence": confidence,
                    "weight": weight,
                    "time_seconds": elapsed
                })
                print(f"    ✅ {model_name}: {action} (conf: {confidence}) in {elapsed:.1f}s")
            else:
                print(f"    ⚠️ {model_name}: Could not parse decision")
        else:
            print(f"    ❌ {model_name}: No response")
    
    # Analyze consensus
    if len(decisions) < min_models:
        return {
            "consensus": False,
            "action": "WAIT",
            "confidence_adjustment": -30,
            "models_agreed": [],
            "models_disagreed": [],
            "note": f"Not enough models responded ({len(decisions)}/{min_models})"
        }
    
    # Count actions with weights
    action_weights = {}
    for d in decisions:
        action = d["action"]
        action_weights[action] = action_weights.get(action, 0) + d["weight"]
    
    # Find majority action
    majority_action = max(action_weights.keys(), key=lambda k: action_weights[k])
    total_weight = sum(action_weights.values())
    majority_pct = action_weights[majority_action] / total_weight * 100
    
    models_agreed = [d["model"] for d in decisions if d["action"] == majority_action]
    models_disagreed = [d["model"] for d in decisions if d["action"] != majority_action]
    
    # Determine consensus level
    if len(models_agreed) == len(decisions):
        # Full agreement
        return {
            "consensus": True,
            "action": majority_action,
            "confidence_adjustment": +10,
            "models_agreed": models_agreed,
            "models_disagreed": [],
            "agreement_pct": 100,
            "note": f"🟢 FULL CONSENSUS: All {len(decisions)} models agree on {majority_action}"
        }
    elif majority_pct >= 66:
        # Strong majority
        return {
            "consensus": True,
            "action": majority_action,
            "confidence_adjustment": +5,
            "models_agreed": models_agreed,
            "models_disagreed": models_disagreed,
            "agreement_pct": majority_pct,
            "note": f"🟡 MAJORITY CONSENSUS: {len(models_agreed)}/{len(decisions)} models agree on {majority_action}"
        }
    else:
        # No consensus
        return {
            "consensus": False,
            "action": "WAIT",
            "confidence_adjustment": -20,
            "models_agreed": models_agreed,
            "models_disagreed": models_disagreed,
            "agreement_pct": majority_pct,
            "note": f"🔴 NO CONSENSUS: Models disagree - defaulting to WAIT"
        }


def test_consensus():
    """Test the consensus system."""
    print("=" * 60)
    print("NEO MULTI-MODEL CONSENSUS TEST")
    print("=" * 60)
    
    test_prompt = """
    You are NEO, analyzing the forex market.
    
    Current market state:
    - EURUSD: 1.0850
    - Regime: Ranging (ADX ~18)
    - RSI(2): 8.5 (extremely oversold)
    - Price above 200 SMA: Yes
    
    Based on RSI(2) strategy (Connors research, 88% win rate):
    - RSI(2) < 10 = oversold
    - Price > 200 SMA = uptrend filter passed
    
    What is your decision? Output JSON:
    {
      "decision": {
        "action": "BUY" | "SELL" | "WAIT",
        "symbol": "EURUSD",
        "confidence": 0-100
      },
      "reasoning": "brief explanation"
    }
    """
    
    result = get_consensus(test_prompt)
    
    print("")
    print("=" * 60)
    print("CONSENSUS RESULT:")
    print(json.dumps(result, indent=2))
    print("=" * 60)
    
    return result


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--test", action="store_true", help="Run test")
    args = parser.parse_args()
    
    if args.test:
        test_consensus()
    else:
        print("Usage: python3 multi_model.py --test")
        print("Or import and use get_consensus(prompt) function")
