"""Shared Bedrock Converse helper — eliminates repeated call/parse/error boilerplate."""
from __future__ import annotations

import json
import logging
import re
import threading
from typing import Optional

import boto3

from backend.config import Settings
from backend.constants import LLM_MODEL_ID

logger = logging.getLogger(__name__)

# Module-level cache for bedrock-runtime clients, keyed by region.
# boto3 clients are thread-safe once created, so caching avoids the overhead
# of credential resolution + HTTP session setup on every LLM call.
_bedrock_clients: dict = {}
_bedrock_clients_lock = threading.Lock()


def _get_bedrock_client(region: str):
    """Return a cached bedrock-runtime client for the given region."""
    if region not in _bedrock_clients:
        with _bedrock_clients_lock:
            # Double-check after acquiring lock
            if region not in _bedrock_clients:
                _bedrock_clients[region] = boto3.client(
                    "bedrock-runtime", region_name=region
                )
    return _bedrock_clients[region]


def call_bedrock(
    settings: Settings,
    system_prompt: str,
    user_message: str,
    max_tokens: int = 4000,
    temperature: float = 0.1,
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
