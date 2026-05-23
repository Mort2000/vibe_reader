"""Minimal OpenAI-compatible LLM connectivity probe for system verification."""

from __future__ import annotations

import time
from typing import Any

import httpx

from ..config import LLMConfig
from ..errors import AppError
from ..observability import get_trace_id


async def ping_llm(llm: LLMConfig, timeout_s: float = 60.0) -> dict[str, Any]:
    """Send a minimal chat completion request and return usage summary."""
    if not llm.base_url:
        raise AppError(
            "llm_not_configured", "LLM base_url is not configured", status=400
        )
    if not llm.api_key:
        raise AppError(
            "llm_not_configured", "LLM api_key is not configured", status=400
        )

    url = f"{llm.base_url.rstrip('/')}/chat/completions"
    payload = {
        "model": llm.model,
        "messages": [{"role": "user", "content": "Reply with exactly: ok"}],
        "max_tokens": 8,
        "temperature": 0,
    }
    headers = {
        "Authorization": f"Bearer {llm.api_key}",
        "Content-Type": "application/json",
    }

    start = time.monotonic()
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_s, connect=10.0)
        ) as client:
            resp = await client.post(url, headers=headers, json=payload)
    except httpx.TimeoutException as exc:
        raise AppError(
            "llm_timeout", f"LLM ping timed out after {timeout_s}s", status=504
        ) from exc
    except httpx.HTTPError as exc:
        raise AppError(
            "llm_provider_error", f"LLM ping request failed: {exc}", status=502
        ) from exc

    elapsed_ms = (time.monotonic() - start) * 1000

    if resp.status_code >= 400:
        detail = resp.text[:300] if resp.text else resp.reason_phrase
        raise AppError(
            "llm_provider_error",
            f"LLM provider returned HTTP {resp.status_code}",
            status=502,
            details={
                "provider_status": resp.status_code,
                "provider_body_excerpt": detail,
            },
        )

    try:
        body = resp.json()
    except ValueError as exc:
        raise AppError(
            "llm_provider_error", "LLM provider returned non-JSON response", status=502
        ) from exc

    usage = body.get("usage") or {}
    choice = (body.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    content = (message.get("content") or "").strip()

    tokens: dict[str, int | None] = {
        "input": usage.get("prompt_tokens"),
        "output": usage.get("completion_tokens"),
        "cached_input": usage.get("prompt_cache_hit_tokens")
        or usage.get("cached_tokens"),
    }
    if tokens["input"] is None and tokens["output"] is None:
        tokens["input"] = _estimate_tokens(payload["messages"][0]["content"])
        tokens["output"] = _estimate_tokens(content) if content else 0

    return {
        "ok": True,
        "model": body.get("model") or llm.model,
        "trace_id": get_trace_id(),
        "latency_ms": round(elapsed_ms, 2),
        "reply_excerpt": content[:80],
        "tokens": tokens,
        "usage_estimate": tokens["input"] is not None or tokens["output"] is not None,
    }


def _estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, len(text) // 2)
