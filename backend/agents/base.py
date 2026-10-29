"""
agents/base.py — Shared LLM client and base agent utilities.

All agents receive only the context they need (not the full conversation history).
Secrets are never placed in agent payloads or logs.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from openai import AsyncOpenAI

from ..config import Settings

logger = logging.getLogger(__name__)


import httpx

def get_llm_client(settings: Settings) -> AsyncOpenAI:
    key = settings.gemini_api_key or settings.openai_api_key or "placeholder_key"
    client = AsyncOpenAI(
        api_key=key,
        base_url=settings.openai_base_url,
    )
    if settings.gemini_api_key:
        client._gemini_api_key = settings.gemini_api_key
    return client


def get_model(settings: Settings) -> str:
    if settings.gemini_api_key:
        return settings.gemini_model
    return settings.openai_model


async def call_gemini_rest(
    api_key: str,
    model: str,
    system_prompt: str,
    user_message: str,
    response_format: str = "text",
) -> str:
    """Direct Google Gemini REST API call using httpx."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    payload: dict[str, Any] = {
        "contents": [{"role": "user", "parts": [{"text": user_message}]}],
        "generationConfig": {"temperature": 0.1, "maxOutputTokens": 2048},
    }
    if system_prompt:
        payload["system_instruction"] = {"parts": [{"text": system_prompt}]}
    if response_format == "json":
        payload["generationConfig"]["responseMimeType"] = "application/json"

    logger.debug("Gemini REST call: model=%s", model)
    async with httpx.AsyncClient(timeout=45.0) as http_client:
        resp = await http_client.post(url, json=payload)
        resp.raise_for_status()
        data = resp.json()
        candidates = data.get("candidates", [])
        if not candidates:
            raise ValueError(f"Gemini returned no candidates: {data}")
        parts = candidates[0].get("content", {}).get("parts", [])
        if not parts:
            raise ValueError(f"Gemini returned empty content parts: {data}")
        return parts[0].get("text", "").strip()


async def call_llm(
    client: AsyncOpenAI,
    model: str,
    system_prompt: str,
    user_message: str,
    response_format: str = "text",
) -> str:
    """
    Single LLM call. Routes to Gemini REST when gemini_api_key is present,
    or OpenAI chat completion otherwise.
    Never logs full prompts at INFO level to avoid leaking sensitive schema/data.
    """
    gemini_key = getattr(client, "_gemini_api_key", None)
    if gemini_key:
        return await call_gemini_rest(
            api_key=gemini_key,
            model=model,
            system_prompt=system_prompt,
            user_message=user_message,
            response_format=response_format,
        )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": 0.1,
        "max_tokens": 2048,
    }
    if response_format == "json":
        kwargs["response_format"] = {"type": "json_object"}

    logger.debug("LLM call: model=%s", model)
    response = await client.chat.completions.create(**kwargs)
    content = response.choices[0].message.content or ""
    return content.strip()


def safe_parse_json(raw: str) -> dict:
    """Parse JSON from LLM response, stripping markdown fences if present."""
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        cleaned = "\n".join(lines[1:-1]) if len(lines) > 2 else cleaned
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Agent returned invalid JSON: {exc}\nRaw: {cleaned[:200]}") from exc
