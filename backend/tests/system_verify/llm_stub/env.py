"""Stub LLM env injection and optional backend process helpers.

AIMock runs in the verify runner process tree. The FastAPI backend is normally
started separately; ``inject_stub_backend_env`` publishes the required LLM env
into ``os.environ`` so subprocess spawns and operator copy/paste stay aligned.

Design §4.2 "runner injects backend LLM env" is satisfied when:
- ``inject_stub_backend_env`` runs after AIMock starts (pytest + CLI), and
- the backend process is (re)started with the same values, or
- ``--spawn-backend`` is passed to ``vibe-verify run``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from ..config import VerifyConfig
from .aimock_launcher import AIMockSession


@dataclass
class BackendProcess:
    """Optional backend subprocess started with stub LLM env."""

    _proc: subprocess.Popen[bytes] | None = field(default=None, repr=False)

    def stop(self) -> None:
        proc = self._proc
        if proc is None or proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
        self._proc = None


def stub_backend_env(session: AIMockSession, config: VerifyConfig) -> dict[str, str]:
    """Full env dict the backend process needs for stub verification."""
    env = session.backend_env()
    env["VIBE_READER_DATA_DIR"] = str(config.target.data_dir)
    return env


def inject_stub_backend_env(session: AIMockSession, config: VerifyConfig) -> dict[str, str]:
    """Publish stub backend env into the current process (verify runner / pytest)."""
    env = stub_backend_env(session, config)
    os.environ.update(env)
    return env


def format_backend_env_lines(env: dict[str, str]) -> list[str]:
    return [f"{key}={value}" for key, value in env.items()]


def print_stub_backend_env_notice(session: AIMockSession, config: VerifyConfig) -> None:
    env = stub_backend_env(session, config)
    print("AIMock sidecar started:")
    print(f"  base_url: {session.base_url}")
    print(f"  profile:  {session.profile}")
    print("Stub backend env applied to verify runner; backend process must use:")
    for line in format_backend_env_lines(env):
        print(f"  {line}")
    print("Restart the backend with the above vars, or pass --spawn-backend to vibe-verify run.")


def fetch_verify_runtime_llm(target_url: str, timeout_s: float = 5.0) -> dict:
    url = f"{target_url.rstrip('/')}/api/verify/runtime"
    try:
        with urllib.request.urlopen(url, timeout=timeout_s) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return {}
        raise
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Cannot reach backend verify runtime at {url}: {exc}") from exc


def validate_backend_stub_llm(
    config: VerifyConfig, session: AIMockSession, *, target_url: str | None = None
) -> list[str]:
    """Return human-readable errors when backend is not configured for AIMock."""
    base = target_url or config.target.base_url
    try:
        body = fetch_verify_runtime_llm(base)
    except RuntimeError as exc:
        return [str(exc)]

    if not body:
        return [
            "Backend verify runtime unavailable (404). Start backend with "
            "VIBE_READER_VERIFY_MODE=1."
        ]

    llm = body.get("llm") or {}
    errors: list[str] = []
    if not llm.get("api_key_configured"):
        errors.append("backend llm.api_key_configured is false")
    if not llm.get("base_url_configured"):
        errors.append("backend llm.base_url_configured is false")
    expected_model = session.model
    backend_model = llm.get("model")
    if backend_model and backend_model != expected_model:
        errors.append(
            f"backend llm.model={backend_model!r} != expected AIMock model "
            f"{expected_model!r}"
        )
    return errors


def assert_backend_stub_llm_ready(
    config: VerifyConfig,
    session: AIMockSession,
    *,
    target_url: str | None = None,
) -> None:
    errors = validate_backend_stub_llm(config, session, target_url=target_url)
    if not errors:
        return
    env = stub_backend_env(session, config)
    lines = "\n".join(f"  {line}" for line in format_backend_env_lines(env))
    detail = "; ".join(errors)
    raise RuntimeError(
        f"Backend not ready for stub LLM ({detail}). Required env:\n{lines}\n"
        "Restart backend with these vars or use vibe-verify run --spawn-backend."
    )


def _parse_target_host_port(target_url: str) -> tuple[str, int]:
    from urllib.parse import urlparse

    parsed = urlparse(target_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return host, port


def _wait_backend_health(target_url: str, timeout_s: float = 30.0) -> None:
    url = f"{target_url.rstrip('/')}/api/health"
    deadline = time.monotonic() + timeout_s
    last_error = ""
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if resp.status == 200:
                    return
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = str(exc)
        time.sleep(0.3)
    raise RuntimeError(f"Backend health check failed at {url}: {last_error}")


def spawn_backend(config: VerifyConfig, session: AIMockSession) -> BackendProcess:
    """Start backend subprocess with stub LLM env (no reload)."""
    host, port = _parse_target_host_port(config.target.base_url)
    env = {**os.environ, **stub_backend_env(session, config)}

    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:create_app",
            "--factory",
            "--host",
            host,
            "--port",
            str(port),
        ],
        cwd=_backend_root(),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_backend_health(config.target.base_url)
    except RuntimeError as exc:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        raise RuntimeError(f"Failed to spawn backend: {exc}") from None

    assert_backend_stub_llm_ready(config, session)
    return BackendProcess(_proc=proc)


def _backend_root() -> str:
    from pathlib import Path

    return str(Path(__file__).resolve().parents[3])
