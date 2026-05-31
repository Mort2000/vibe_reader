"""Independent CLI for stub serving, corpus validation, and scenario runs."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shlex
import subprocess
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .corpus import CorpusCatalog
from .provider import GenericStubRouter, StubProfile, StubSidecar
from .run_config import (
    BackendSettings,
    RunSettings,
    build_run_spec,
    resolve_run_settings,
)
from .runner import RunEngine, RunSpec
from .scenarios import build_registry


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="vibe-verify")
    commands = parser.add_subparsers(dest="command", required=True)
    stub = commands.add_parser("stub", help="serve the deterministic model sidecar")
    stub.add_argument("--host", default="127.0.0.1")
    stub.add_argument("--port", type=int, default=4010)
    stub.add_argument("--profile", default="mvp_default")
    stub.add_argument("--fault", default="")
    corpus = commands.add_parser("validate-corpus", help="validate a corpus manifest")
    corpus.add_argument("manifest")
    run = commands.add_parser("run", help="run built-in verification scenarios")
    run.add_argument(
        "--config",
        default=None,
        help="TOML run configuration; CLI flags override config values",
    )
    run.add_argument("--suite", default=argparse.SUPPRESS)
    run.add_argument("--scenario", default=argparse.SUPPRESS)
    run.add_argument("--profile", default=argparse.SUPPRESS)
    run.add_argument(
        "--llm-mode",
        choices=["stub", "real"],
        default=argparse.SUPPRESS,
    )
    run.add_argument("--target-url", default=argparse.SUPPRESS)
    run.add_argument("--artifact-root", default=argparse.SUPPRESS)
    run.add_argument("--run-id", default=argparse.SUPPRESS)
    run.add_argument("--corpus", default=argparse.SUPPRESS)
    run.add_argument(
        "--audit",
        action=argparse.BooleanOptionalAction,
        default=argparse.SUPPRESS,
    )
    run.add_argument("--stub-profile", default=argparse.SUPPRESS)
    run.add_argument("--read-batches", type=int, default=argparse.SUPPRESS)
    run.add_argument("--read-batch-size", type=int, default=argparse.SUPPRESS)
    run.add_argument("--min-comment-windows", type=int, default=argparse.SUPPRESS)
    run.add_argument(
        "--post-compaction-comment-windows",
        type=int,
        default=argparse.SUPPRESS,
    )
    run.add_argument("--min-chat-turns", type=int, default=argparse.SUPPRESS)
    run.add_argument("--max-wait-comment-s", type=float, default=argparse.SUPPRESS)
    run.add_argument(
        "--max-wait-compaction-s",
        type=float,
        default=argparse.SUPPRESS,
    )
    run.add_argument("--max-calls", type=int, default=argparse.SUPPRESS)
    run.add_argument("--max-tokens", type=int, default=argparse.SUPPRESS)
    run.add_argument("--max-duration-s", type=float, default=argparse.SUPPRESS)
    run.add_argument("--max-cost-usd", type=float, default=argparse.SUPPRESS)
    run.add_argument(
        "--backend-command",
        default=argparse.SUPPRESS,
        help="optional command to spawn backend after stub env is prepared",
    )
    run.add_argument("--backend-cwd", default=argparse.SUPPRESS)
    run.add_argument("--backend-config-file", default=argparse.SUPPRESS)
    run.add_argument("--backend-ready-path", default=argparse.SUPPRESS)
    run.add_argument(
        "--backend-ready-timeout-s",
        type=float,
        default=argparse.SUPPRESS,
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "validate-corpus":
        errors = CorpusCatalog(args.manifest).validate()
        print(json.dumps({"ok": not errors, "errors": errors}, ensure_ascii=False))
        return 1 if errors else 0
    if args.command == "run":
        return run_scenarios(args)
    profile = StubProfile(name=args.profile, fault=args.fault)
    with StubSidecar(
        GenericStubRouter(profile), host=args.host, port=args.port
    ) as sidecar:
        print(f"Vibe Reader verify stub listening at {sidecar.base_url}", flush=True)
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            return 0


def run_scenarios(args: argparse.Namespace) -> int:
    settings = resolve_run_settings(args)
    spec = build_run_spec(settings)
    target_preparer = None
    target_cleanup = None
    if settings.backend.command:
        target_preparer = backend_preparer(settings)
        target_cleanup = cleanup_backend
    result = asyncio.run(
        RunEngine(
            build_registry(),
            target_preparer=target_preparer,
            target_cleanup=target_cleanup,
        ).run(spec)
    )
    print(
        json.dumps(
            {
                "run_id": result.run_id,
                "status": result.status,
                "artifact_dir": str(result.artifact_dir),
                "error": result.error,
                "scenarios": result.scenarios,
            },
            ensure_ascii=False,
        )
    )
    return 0 if result.status == "passed" else 1


def backend_preparer(settings: RunSettings):
    def prepare(_spec: RunSpec, _session: Any) -> subprocess.Popen:
        env = os.environ.copy()
        env.update(settings.backend.env)
        env.setdefault("VIBE_READER_VERIFY_MODE", "1")
        prepare_backend_data_dir(settings.backend, env)
        argv = shlex.split(settings.backend.command)
        process = subprocess.Popen(
            argv,
            cwd=settings.backend.cwd or None,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            wait_for_backend_ready(
                settings.target_url,
                settings.backend.ready_path,
                timeout_s=settings.backend.ready_timeout_s,
                process=process,
            )
        except Exception:
            cleanup_backend(process)
            raise
        return process

    return prepare


def prepare_backend_data_dir(
    backend: BackendSettings,
    env: Mapping[str, str] | None = None,
) -> None:
    if backend.config_file is None:
        return
    data_dir = backend.env.get("VIBE_READER_DATA_DIR")
    if data_dir is None and env is not None:
        data_dir = env.get("VIBE_READER_DATA_DIR")
    if not data_dir:
        raise ValueError("backend.config_file requires VIBE_READER_DATA_DIR")
    source = backend.config_file
    target = Path(data_dir) / "config.toml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

def wait_for_backend_ready(
    target_url: str,
    path: str,
    *,
    timeout_s: float,
    process: subprocess.Popen | None = None,
) -> None:
    import httpx

    url = target_url.rstrip("/") + "/" + path.lstrip("/")
    deadline = time.monotonic() + timeout_s
    last_error = ""
    while time.monotonic() < deadline:
        if process is not None and process.poll() is not None:
            raise RuntimeError(f"backend command exited early: {process.returncode}")
        try:
            response = httpx.get(url, timeout=1.0)
            if 200 <= response.status_code < 300:
                return
            last_error = f"HTTP {response.status_code}"
        except Exception as exc:
            last_error = str(exc)
        time.sleep(0.1)
    raise TimeoutError(f"backend not ready at {url}: {last_error}")


def cleanup_backend(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
