"""AIMock LLM stub sidecar launcher for system verification."""

from __future__ import annotations

import hashlib
import json
import pathlib
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from ..config import VerifyConfig


class AIMockLaunchError(RuntimeError):
    """Raised when AIMock sidecar fails to start or pass health checks."""


@dataclass
class AIMockSession:
    """Running AIMock sidecar session metadata."""

    base_url: str
    host: str
    port: int
    profile: str
    version: str
    strict: bool
    fixture_hash: str
    profile_hash: str
    api_key: str
    model: str
    _process: subprocess.Popen[str] | None = field(default=None, repr=False)
    _aimock_dir: pathlib.Path = field(default_factory=pathlib.Path, repr=False)

    def backend_env(self) -> dict[str, str]:
        return {
            "VIBE_READER_VERIFY_MODE": "1",
            "VIBE_READER_LLM_BASE_URL": self.base_url,
            "VIBE_READER_LLM_API_KEY": self.api_key,
            "VIBE_READER_LLM_MODEL": self.model,
        }

    def stop(self) -> None:
        proc = self._process
        if proc is None or proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


def _sha256_dir(path: pathlib.Path) -> str:
    if not path.exists():
        return "sha256:" + hashlib.sha256(b"").hexdigest()
    digest = hashlib.sha256()
    for file_path in sorted(path.rglob("*")):
        if not file_path.is_file():
            continue
        rel = str(file_path.relative_to(path)).encode()
        digest.update(rel)
        digest.update(file_path.read_bytes())
    return "sha256:" + digest.hexdigest()


def _backend_root() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parents[3]


def _resolve_aimock_dir() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parent / "aimock"


def _ensure_node_deps(aimock_dir: pathlib.Path) -> None:
    node_modules = aimock_dir / "node_modules"
    if (node_modules / "@copilotkit/aimock").exists():
        return
    npm = shutil.which("npm")
    if npm is None:
        raise AIMockLaunchError(
            "Node.js/npm is required to run AIMock stub. Install Node.js or "
            "run `npm install` in tests/system_verify/llm_stub/aimock/"
        )
    subprocess.run(
        [npm, "install", "--no-fund", "--no-audit"],
        cwd=aimock_dir,
        check=True,
        capture_output=True,
        text=True,
    )


def _wait_health(host: str, port: int, timeout_s: float) -> None:
    deadline = time.monotonic() + timeout_s
    url = f"http://{host}:{port}/health"
    last_error = ""
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if resp.status == 200:
                    return
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = str(exc)
        time.sleep(0.25)
    raise AIMockLaunchError(
        f"AIMock health check failed at {url} within {timeout_s}s: {last_error}"
    )


def start_aimock(
    config: VerifyConfig,
    *,
    profile: str | None = None,
) -> AIMockSession:
    """Start AIMock sidecar for stub verification runs."""
    aimock_cfg = config.llm_stub.aimock
    if not aimock_cfg.enabled:
        raise AIMockLaunchError("llm_stub.aimock.enabled is false in stub mode")

    aimock_dir = _resolve_aimock_dir()
    server_script = aimock_dir / "server.mjs"
    if not server_script.exists():
        raise AIMockLaunchError(f"AIMock server script not found: {server_script}")

    _ensure_node_deps(aimock_dir)

    profile_name = profile or config.llm.stub_profile
    profile_path = aimock_dir / "profiles" / f"{profile_name}.json"
    if not profile_path.exists():
        raise AIMockLaunchError(f"AIMock profile not found: {profile_path}")

    node = shutil.which("node")
    if node is None:
        raise AIMockLaunchError("Node.js is required to run AIMock stub sidecar")

    cmd = [
        node,
        str(server_script),
        "--profile",
        profile_name,
        "--port",
        str(aimock_cfg.port),
        "--host",
        aimock_cfg.host,
        "--seed",
        str(aimock_cfg.seed),
    ]
    if aimock_cfg.strict:
        cmd.append("--strict")

    proc = subprocess.Popen(
        cmd,
        cwd=aimock_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    try:
        _wait_health(
            aimock_cfg.host, aimock_cfg.port, float(aimock_cfg.startup_timeout_s)
        )
    except AIMockLaunchError:
        proc.terminate()
        stdout = proc.stdout.read() if proc.stdout else ""
        raise AIMockLaunchError(
            f"AIMock failed to start (profile={profile_name}): {stdout.strip()}"
        ) from None

    fixture_dir = pathlib.Path(aimock_cfg.fixture_dir)
    if not fixture_dir.is_absolute():
        fixture_dir = _backend_root() / fixture_dir
    profile_dir = pathlib.Path(aimock_cfg.profile_dir)
    if not profile_dir.is_absolute():
        profile_dir = _backend_root() / profile_dir

    base_url = f"http://{aimock_cfg.host}:{aimock_cfg.port}/v1"
    return AIMockSession(
        base_url=base_url,
        host=aimock_cfg.host,
        port=aimock_cfg.port,
        profile=profile_name,
        version=aimock_cfg.version,
        strict=aimock_cfg.strict,
        fixture_hash=_sha256_dir(fixture_dir),
        profile_hash=_sha256_dir(profile_dir),
        api_key=aimock_cfg.api_key,
        model=aimock_cfg.model,
        _process=proc,
        _aimock_dir=aimock_dir,
    )


def ping_s0_fixture(session: AIMockSession, timeout_s: float = 10.0) -> None:
    """Verify S0 ping fixture is reachable on the running sidecar."""
    url = f"{session.base_url.rstrip('/')}/chat/completions"
    payload = json.dumps(
        {
            "model": session.model,
            "messages": [{"role": "user", "content": "Reply with exactly: ok"}],
            "max_tokens": 8,
            "temperature": 0,
        }
    ).encode()
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Authorization": f"Bearer {session.api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            body = json.loads(resp.read().decode())
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise AIMockLaunchError(f"S0 ping fixture probe failed: {exc}") from exc

    choices = body.get("choices") or [{}]
    message = (choices[0].get("message") or {}).get("content", "")
    if str(message).strip() != "ok":
        raise AIMockLaunchError(
            f"S0 ping fixture returned unexpected content: {message!r}"
        )


class AIMockSidecar:
    """Context manager wrapping AIMock startup and shutdown."""

    def __init__(self, config: VerifyConfig):
        self.config = config
        self.session: AIMockSession | None = None

    def __enter__(self) -> AIMockSession:
        if self.config.is_real_llm:
            raise AIMockLaunchError("AIMock sidecar must not start in real LLM mode")
        self.session = start_aimock(self.config)
        ping_s0_fixture(self.session)
        return self.session

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.session is not None:
            self.session.stop()
            self.session = None
