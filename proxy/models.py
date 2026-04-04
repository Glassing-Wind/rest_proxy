"""proxy_models.py — model aliasing and LM Studio model helpers."""

import json
from typing import Any, Dict, List

import httpx

from proxy.config import (
    ENABLE_MODEL_VALIDATION,
    FALLBACK_MODEL,
    LM_BASE,
    MODEL_ALIASES_ENV,
)
from proxy.logging import debug_log


def parse_model_aliases() -> Dict[str, str]:
    if not MODEL_ALIASES_ENV:
        return {}
    try:
        parsed = json.loads(MODEL_ALIASES_ENV)
    except Exception:
        debug_log("model_alias_parse_failed", raw_value=MODEL_ALIASES_ENV)
        return {}

    if not isinstance(parsed, dict):
        debug_log("model_alias_parse_failed", raw_value=MODEL_ALIASES_ENV)
        return {}

    aliases: Dict[str, str] = {}
    for key, value in parsed.items():
        if (
            isinstance(key, str)
            and isinstance(value, str)
            and key.strip()
            and value.strip()
        ):
            aliases[key.strip()] = value.strip()
    return aliases


def extract_model_keys(models_payload: Any) -> List[str]:
    if not isinstance(models_payload, dict):
        return []
    models = models_payload.get("models")
    if not isinstance(models, list):
        return []

    keys: List[str] = []
    for item in models:
        if not isinstance(item, dict):
            continue
        key = item.get("key")
        if isinstance(key, str) and key:
            keys.append(key)
    return keys


async def fetch_lmstudio_models() -> Dict[str, Any]:
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.get(f"{LM_BASE}/api/v1/models")
        r.raise_for_status()
        return r.json()


def resolve_model_name(requested_model: str, available_keys: List[str]) -> str:
    aliases = parse_model_aliases()
    if requested_model in aliases:
        mapped = aliases[requested_model]
        debug_log(
            "model_alias_applied",
            requested_model=requested_model,
            mapped_model=mapped,
        )
        requested_model = mapped

    if not ENABLE_MODEL_VALIDATION:
        return requested_model

    if requested_model in available_keys:
        return requested_model

    if FALLBACK_MODEL and FALLBACK_MODEL in available_keys:
        debug_log(
            "model_validation_fallback",
            requested_model=requested_model,
            fallback_model=FALLBACK_MODEL,
        )
        return FALLBACK_MODEL

    return requested_model


def build_local_llm_models(payload: Any) -> List[Dict[str, Any]]:
    raw_models = payload.get("models", []) if isinstance(payload, dict) else []

    models: List[Dict[str, Any]] = []
    for item in raw_models:
        if not isinstance(item, dict):
            continue
        if item.get("type") != "llm":
            continue

        quantization = item.get("quantization")
        quantization_name = None
        if isinstance(quantization, dict):
            name = quantization.get("name")
            if isinstance(name, str) and name:
                quantization_name = name

        capabilities = (
            item.get("capabilities")
            if isinstance(item.get("capabilities"), dict)
            else {}
        )
        display_name = item.get("display_name")
        key = item.get("key")

        if not isinstance(key, str) or not key:
            continue

        if not isinstance(display_name, str) or not display_name:
            display_name = key

        models.append(
            {
                "id": key,
                "name": display_name,
                "publisher": item.get("publisher")
                if isinstance(item.get("publisher"), str)
                else None,
                "params": item.get("params_string")
                if isinstance(item.get("params_string"), str)
                else None,
                "format": item.get("format")
                if isinstance(item.get("format"), str)
                else None,
                "quantization": quantization_name,
                "context_length": item.get("max_context_length"),
                "vision": bool(capabilities.get("vision", False)),
                "tool_use": bool(capabilities.get("trained_for_tool_use", False)),
                "loaded": bool(item.get("loaded_instances")),
            }
        )

    return models
