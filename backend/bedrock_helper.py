"""Shared LLM helper — routes to Claude (Anthropic SDK) or Bedrock Converse.

All callers use call_bedrock(...) for backward compatibility; it dispatches on
settings.llm_provider ("anthropic" -> Claude, anything else -> Bedrock) and, in
both cases, strips fences/thinking tags and returns the parsed JSON object.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
from typing import Optional

from backend.config import Settings
from backend.constants import LLM_MODEL_ID

logger = logging.getLogger(__name__)

# Module-level cache for bedrock-runtime clients, keyed by region.
# boto3 clients are thread-safe once created, so caching avoids the overhead
# of credential resolution + HTTP session setup on every LLM call.
_bedrock_clients: dict = {}
_bedrock_clients_lock = threading.Lock()

# Single cached Anthropic SDK client (thread-safe once constructed).
_anthropic_client = None
_anthropic_lock = threading.Lock()


def _get_bedrock_client(region: str):
    """Return a cached bedrock-runtime client for the given region."""
    import boto3
    if region not in _bedrock_clients:
        with _bedrock_clients_lock:
            # Double-check after acquiring lock
            if region not in _bedrock_clients:
                _bedrock_clients[region] = boto3.client(
                    "bedrock-runtime", region_name=region
                )
    return _bedrock_clients[region]


def _resolve_anthropic_key(settings: Settings) -> Optional[str]:
    """Resolve the Anthropic key: ANTHROPIC_API_KEY env (dev) else Secrets Manager."""
    key = os.getenv("ANTHROPIC_API_KEY")
    if key:
        return key
    secret_id = getattr(settings, "anthropic_secret", "")
    if not secret_id:
        return None  # let the SDK's own resolution run (and error if truly unset)
    import boto3
    sm = boto3.client("secretsmanager", region_name=settings.region)
    return sm.get_secret_value(SecretId=secret_id)["SecretString"].strip()


def _get_anthropic_client(settings: Settings):
    """Return a cached Anthropic client, keyed from env or Secrets Manager."""
    global _anthropic_client
    if _anthropic_client is None:
        with _anthropic_lock:
            if _anthropic_client is None:
                import anthropic
                _anthropic_client = anthropic.Anthropic(
                    api_key=_resolve_anthropic_key(settings)
                )
    return _anthropic_client


def call_bedrock(
    settings: Settings,
    system_prompt: str,
    user_message: str,
    max_tokens: int = 4000,
    temperature: float = 0.1,
) -> dict:
    """Dispatch to Claude or Bedrock, then parse the JSON response. Name kept for compat.

    Retries on a JSON parse failure: LLMs occasionally emit slightly-malformed JSON
    on large nested outputs, and a fresh sample almost always parses cleanly.
    """
    anthropic_provider = getattr(settings, "llm_provider", "anthropic") == "anthropic"
    last_err: Optional[Exception] = None
    for attempt in range(3):
        try:
            if anthropic_provider:
                return _call_anthropic(settings, system_prompt, user_message, max_tokens)
            return _call_bedrock_converse(settings, system_prompt, user_message, max_tokens, temperature)
        except (json.JSONDecodeError, ValueError) as exc:
            last_err = exc
            logger.warning("LLM JSON parse failed (attempt %d/3): %s", attempt + 1, exc)
    raise last_err  # exhausted retries


def _call_anthropic(
    settings: Settings, system_prompt: str, user_message: str, max_tokens: int
) -> dict:
    """Call Claude via the Anthropic SDK. No temperature — removed on Opus 4.8."""
    client = _get_anthropic_client(settings)
    model = getattr(settings, "anthropic_model", "claude-sonnet-4-6")
    message = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
    )
    raw = "".join(b.text for b in message.content if b.type == "text").strip()
    return parse_llm_json(raw)


def _call_bedrock_converse(
    settings: Settings, system_prompt: str, user_message: str,
    max_tokens: int, temperature: float,
) -> dict:
    """Call Bedrock Converse API, strip fences/thinking tags, parse JSON response."""
    client = _get_bedrock_client(settings.bedrock_region)
    response = client.converse(
        modelId=getattr(settings, "llm_model", LLM_MODEL_ID),
        system=[{"text": system_prompt}],
        messages=[{"role": "user", "content": [{"text": user_message}]}],
        inferenceConfig={"maxTokens": max_tokens, "temperature": temperature},
    )
    raw = response["output"]["message"]["content"][0]["text"].strip()
    return parse_llm_json(raw)


def parse_llm_json(raw: str) -> dict:
    """Strip thinking tags, markdown fences, and parse the JSON object from LLM output."""
    # Strip <think>...</think> tags (Qwen3 reasoning wrapper)
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
    # Strip markdown fencing
    raw = re.sub(r"```(?:json)?", "", raw).strip().rstrip("`")
    # Find the JSON object boundaries
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        return json.loads(raw[start : end + 1])
    # Fallback: try parsing the whole thing
    return json.loads(raw)
