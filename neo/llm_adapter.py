#!/usr/bin/env python3
"""
LLM Adapter - Universal multi-provider LLM interface
Supports: ollama, openai, anthropic (claude), deepseek

Usage:
    from llm_adapter import call_llm

    # Uses provider from LLM_PROVIDER env var (default: ollama)
    response = call_llm("Analyze EURUSD market...")

    # Override provider per call
    response = call_llm("...", provider="deepseek")

Environment variables:
    LLM_PROVIDER   = ollama | openai | anthropic | deepseek  (default: ollama)
    LLM_API_KEY    = your API key (not needed for ollama)
    LLM_BASE_URL   = custom endpoint (optional override)
    OLLAMA_URL     = ollama endpoint (default: http://localhost:11434)
"""

import os
import json
import logging
import requests
from typing import Optional

logger = logging.getLogger("LLMAdapter")

# ---------------------------------------------------------------------------
# Provider → default model mapping
# ---------------------------------------------------------------------------
PROVIDER_MODELS = {
    "ollama": {
        "primary": os.getenv("OLLAMA_PRIMARY_MODEL", "qwen2.5:32b"),
        "backup":  os.getenv("OLLAMA_BACKUP_MODEL",  "qwen3:32b"),
    },
    "openai": {
        "primary": os.getenv("OPENAI_PRIMARY_MODEL", "gpt-4o"),
        "backup":  os.getenv("OPENAI_BACKUP_MODEL",  "gpt-4o-mini"),
    },
    "anthropic": {
        "primary": os.getenv("ANTHROPIC_PRIMARY_MODEL", "claude-sonnet-4-6"),
        "backup":  os.getenv("ANTHROPIC_BACKUP_MODEL",  "claude-haiku-4-5-20251001"),
    },
    "deepseek": {
        "primary": os.getenv("DEEPSEEK_PRIMARY_MODEL", "deepseek-reasoner"),
        "backup":  os.getenv("DEEPSEEK_BACKUP_MODEL",  "deepseek-chat"),
    },
}

# DeepSeek uses an OpenAI-compatible API at a different base URL
PROVIDER_BASE_URLS = {
    "openai":    "https://api.openai.com/v1",
    "deepseek":  "https://api.deepseek.com/v1",
    "anthropic": "https://api.anthropic.com",
    "ollama":    os.getenv("OLLAMA_URL", "http://localhost:11434"),
}


def _get_provider() -> str:
    return os.getenv("LLM_PROVIDER", "ollama").lower().strip()


def _get_api_key(provider: str) -> str:
    # Check provider-specific key first, then generic LLM_API_KEY
    key_map = {
        "openai":    "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "deepseek":  "DEEPSEEK_API_KEY",
    }
    env_name = key_map.get(provider, "LLM_API_KEY")
    return os.getenv(env_name) or os.getenv("LLM_API_KEY", "")


def _resolve_model(provider: str, model: Optional[str], use_backup: bool = False) -> str:
    if model:
        return model
    slot = "backup" if use_backup else "primary"
    return PROVIDER_MODELS.get(provider, PROVIDER_MODELS["ollama"])[slot]


# ---------------------------------------------------------------------------
# Provider-specific callers
# ---------------------------------------------------------------------------

def _call_ollama(prompt: str, model: str, temperature: float,
                 max_tokens: int, system_prompt: Optional[str],
                 timeout: int) -> str:
    base_url = os.getenv("OLLAMA_URL", "http://localhost:11434")
    full_prompt = f"{system_prompt}\n\n{prompt}" if system_prompt else prompt
    resp = requests.post(
        f"{base_url}/api/generate",
        json={
            "model": model,
            "prompt": full_prompt,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json().get("response", "")


def _call_openai_compatible(prompt: str, model: str, temperature: float,
                             max_tokens: int, system_prompt: Optional[str],
                             api_key: str, base_url: str, timeout: int) -> str:
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    resp = requests.post(
        f"{base_url}/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def _call_anthropic(prompt: str, model: str, temperature: float,
                    max_tokens: int, system_prompt: Optional[str],
                    api_key: str, timeout: int) -> str:
    body: dict = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": [{"role": "user", "content": prompt}],
    }
    if system_prompt:
        body["system"] = system_prompt

    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        },
        json=body,
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json()["content"][0]["text"]


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def call_llm(
    prompt: str,
    model: Optional[str] = None,
    temperature: float = 0.2,
    max_tokens: int = 2000,
    system_prompt: Optional[str] = None,
    provider: Optional[str] = None,
    timeout: int = 120,
) -> str:
    """
    Call LLM with the configured or specified provider.

    Args:
        prompt:        User message / trading analysis request
        model:         Model name override (uses provider default if None)
        temperature:   Sampling temperature (low = deterministic)
        max_tokens:    Max response tokens
        system_prompt: System/context prompt (optional)
        provider:      Override provider for this call only
        timeout:       Request timeout in seconds

    Returns:
        LLM response text, or "" on failure
    """
    active_provider = (provider or _get_provider()).lower()
    resolved_model = _resolve_model(active_provider, model)
    api_key = _get_api_key(active_provider)

    logger.debug(f"LLM call: provider={active_provider} model={resolved_model}")

    try:
        if active_provider == "ollama":
            return _call_ollama(prompt, resolved_model, temperature,
                                max_tokens, system_prompt, timeout)

        elif active_provider == "anthropic":
            if not api_key:
                raise ValueError("ANTHROPIC_API_KEY not set")
            return _call_anthropic(prompt, resolved_model, temperature,
                                   max_tokens, system_prompt, api_key, timeout)

        elif active_provider in ("openai", "deepseek"):
            if not api_key:
                raise ValueError(f"{active_provider.upper()}_API_KEY not set")
            base_url = os.getenv("LLM_BASE_URL", PROVIDER_BASE_URLS[active_provider])
            return _call_openai_compatible(prompt, resolved_model, temperature,
                                           max_tokens, system_prompt,
                                           api_key, base_url, timeout)
        else:
            raise ValueError(f"Unknown LLM provider: {active_provider}")

    except Exception as e:
        logger.warning(f"[{active_provider}/{resolved_model}] failed: {e}")

        # Fallback: try backup model on same provider
        backup_model = _resolve_model(active_provider, None, use_backup=True)
        if backup_model != resolved_model:
            logger.info(f"Retrying with backup model: {backup_model}")
            try:
                if active_provider == "ollama":
                    return _call_ollama(prompt, backup_model, temperature,
                                        max_tokens, system_prompt, timeout)
                elif active_provider == "anthropic":
                    return _call_anthropic(prompt, backup_model, temperature,
                                           max_tokens, system_prompt, api_key, timeout)
                elif active_provider in ("openai", "deepseek"):
                    base_url = os.getenv("LLM_BASE_URL", PROVIDER_BASE_URLS[active_provider])
                    return _call_openai_compatible(prompt, backup_model, temperature,
                                                   max_tokens, system_prompt,
                                                   api_key, base_url, timeout)
            except Exception as e2:
                logger.error(f"Backup model also failed: {e2}")

        return ""


def get_active_provider() -> str:
    """Return the currently configured provider name."""
    return _get_provider()


def get_active_model(use_backup: bool = False) -> str:
    """Return the primary (or backup) model name for the active provider."""
    provider = _get_provider()
    return _resolve_model(provider, None, use_backup)


def health_check() -> dict:
    """Check connectivity for the active provider."""
    provider = _get_provider()
    model = _resolve_model(provider, None)
    api_key = _get_api_key(provider)

    result = {"provider": provider, "model": model, "status": "unknown", "error": None}

    try:
        if provider == "ollama":
            base_url = os.getenv("OLLAMA_URL", "http://localhost:11434")
            resp = requests.get(f"{base_url}/api/tags", timeout=5)
            result["status"] = "ok" if resp.status_code == 200 else "error"
        elif provider in ("openai", "deepseek"):
            if not api_key:
                result["status"] = "error"
                result["error"] = "API key not set"
            else:
                base_url = os.getenv("LLM_BASE_URL", PROVIDER_BASE_URLS[provider])
                resp = requests.get(f"{base_url}/models",
                                    headers={"Authorization": f"Bearer {api_key}"},
                                    timeout=10)
                result["status"] = "ok" if resp.status_code == 200 else "error"
        elif provider == "anthropic":
            if not api_key:
                result["status"] = "error"
                result["error"] = "API key not set"
            else:
                result["status"] = "ok"  # No simple health endpoint for Anthropic
    except Exception as e:
        result["status"] = "error"
        result["error"] = str(e)

    return result


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    print("=== LLM Adapter Health Check ===")
    h = health_check()
    print(json.dumps(h, indent=2))

    if "--test" in sys.argv:
        print("\n=== Test Call ===")
        response = call_llm(
            "Reply with exactly: OK",
            max_tokens=10,
            temperature=0,
        )
        print(f"Response: {repr(response)}")
