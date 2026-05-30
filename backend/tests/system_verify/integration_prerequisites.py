"""Integration test prerequisites for pytest system_verify scenarios.

Pytest does not spawn the backend by default. When health, verify mode, LLM
stub wiring, or corpus files are missing, scenarios should skip instead of
running for minutes and failing with opaque RuntimeError messages.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from .core.config import VerifyConfig, validate_real_llm_config
from .corpus import CorpusManager
from .data_lifecycle import assert_isolated_data_dir
from .llm_stub.env import validate_backend_stub_llm


def _fetch_json(url: str, *, timeout_s: float = 3.0) -> tuple[dict[str, Any] | None, int]:
    try:
        with urllib.request.urlopen(url, timeout=timeout_s) as resp:
            body = resp.read()
            status = getattr(resp, "status", 200)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else {}
        except json.JSONDecodeError:
            payload = {}
        return payload if isinstance(payload, dict) else None, exc.code
    except (urllib.error.URLError, TimeoutError, OSError):
        return None, 0

    if not body:
        return {}, status
    try:
        payload = json.loads(body.decode("utf-8"))
    except json.JSONDecodeError:
        return None, status
    return payload if isinstance(payload, dict) else None, status


def _check_backend_health(base: str) -> list[str]:
    health, health_status = _fetch_json(f"{base}/api/health")
    if health_status == 200 and health and health.get("status") == "ok":
        return []
    return [
        f"backend unreachable or unhealthy at {base}/api/health "
        f"(start backend or pass pytest --spawn-backend)"
    ]


def _check_runtime_isolation(config: VerifyConfig, base: str) -> list[str]:
    runtime, runtime_status = _fetch_json(f"{base}/api/runtime")
    if runtime_status != 200 or not runtime:
        return [f"GET /api/runtime failed (HTTP {runtime_status})"]
    try:
        assert_isolated_data_dir(config.target_data_dir, runtime.get("data_dir", ""))
    except Exception as exc:
        return [str(exc)]
    return []


def _check_verify_mode(base: str) -> list[str]:
    verify_runtime, verify_status = _fetch_json(f"{base}/api/verify/runtime")
    if verify_status == 404:
        return ["verify endpoints unavailable (set VIBE_READER_VERIFY_MODE=1 on backend)"]
    if verify_status != 200 or not verify_runtime:
        return [f"GET /api/verify/runtime failed (HTTP {verify_status})"]
    if not verify_runtime.get("verify_mode"):
        return ["backend verify_mode is false"]
    return []


def _check_llm_prerequisites(
    config: VerifyConfig,
    *,
    aimock_session: Any | None,
) -> list[str]:
    if config.is_real_llm:
        return validate_real_llm_config(config)
    if aimock_session is None or not config.llm_stub.aimock.enabled:
        return []
    llm_errors = validate_backend_stub_llm(config, aimock_session)
    if not llm_errors:
        return []
    return [
        "stub LLM not wired on backend: "
        + "; ".join(llm_errors)
        + " (restart backend with AIMock env or use --spawn-backend)"
    ]


def _check_corpus(corpus: CorpusManager | None) -> list[str]:
    if corpus is None:
        return []
    if not corpus.books:
        corpus.load()
    if corpus.validate():
        return []
    return ["corpus invalid: " + "; ".join(corpus.validation_errors)]


def check_integration_prerequisites(
    config: VerifyConfig,
    corpus: CorpusManager | None = None,
    *,
    aimock_session: Any | None = None,
) -> list[str]:
    """Return human-readable skip reasons; empty list means ready."""
    base = config.target.base_url.rstrip("/")

    for checker in (
        lambda: _check_backend_health(base),
        lambda: _check_runtime_isolation(config, base),
        lambda: _check_verify_mode(base),
        lambda: _check_llm_prerequisites(config, aimock_session=aimock_session),
        lambda: _check_corpus(corpus),
    ):
        issues = checker()
        if issues:
            return issues
    return []
