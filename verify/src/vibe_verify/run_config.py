"""Run configuration loading and conversion to executable run specs."""

from __future__ import annotations

import argparse
import os
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .provider import StubProfile
from .runner import Budget, Profile, RunSpec, UserModel


@dataclass(frozen=True)
class BackendSettings:
    command: str = ""
    cwd: str = ""
    config_file: Path | None = None
    ready_path: str = "/api/health"
    ready_timeout_s: float = 30.0
    env: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class RunSettings:
    suite: str = "real-happy-path"
    scenarios: tuple[str, ...] = ("R1_A4_full_flow",)
    profile_name: str = "r1_a4_stub"
    llm_mode: str = "stub"
    target_url: str = "http://127.0.0.1:8000"
    artifact_root: Path = Path("verify_runs")
    run_id: str | None = None
    corpus: Path | None = None
    audit: bool = False
    backend_agent_evidence: bool = False
    stub_profile: str = "r1_a4_stub"
    user: UserModel = field(default_factory=UserModel)
    budget: Budget = field(default_factory=Budget)
    params: dict[str, Any] = field(default_factory=dict)
    backend: BackendSettings = field(default_factory=BackendSettings)
    real_base_url: str = ""
    real_api_key: str = ""
    real_model: str = ""


def resolve_run_settings(args: argparse.Namespace) -> RunSettings:
    data = default_run_config()
    config_path = getattr(args, "config", None)
    if config_path:
        data = deep_merge(data, load_run_config(config_path))
    data = deep_merge(data, cli_run_config(args))
    return run_settings_from_mapping(data)


def build_run_spec(settings: RunSettings) -> RunSpec:
    profile = Profile(
        name=settings.profile_name,
        llm_mode=settings.llm_mode,
        user=settings.user,
        audit_enabled=settings.audit,
        backend_agent_evidence=settings.backend_agent_evidence,
        budget=settings.budget,
        stub=StubProfile(name=settings.stub_profile),
        real_base_url=settings.real_base_url
        or os.environ.get("VIBE_READER_LLM_BASE_URL", ""),
        real_api_key=settings.real_api_key
        or os.environ.get("VIBE_READER_LLM_API_KEY", ""),
        real_model=settings.real_model or os.environ.get("VIBE_READER_LLM_MODEL", ""),
    )
    spec_args: dict[str, Any] = {
        "suite": settings.suite,
        "profile": profile,
        "target_url": settings.target_url,
        "artifact_root": settings.artifact_root,
        "scenario_ids": settings.scenarios,
        "corpus_catalog_path": settings.corpus,
        "params": settings.params,
    }
    if settings.run_id:
        spec_args["run_id"] = settings.run_id
    return RunSpec(**spec_args)


def default_run_config() -> dict[str, Any]:
    return {
        "suite": "real-happy-path",
        "scenarios": ["R1_A4_full_flow"],
        "target_url": "http://127.0.0.1:8000",
        "artifact_root": "verify_runs",
        "corpus": None,
        "run_id": None,
        "profile": {
            "name": "r1_a4_stub",
            "llm_mode": "stub",
            "audit": False,
            "backend_agent_evidence": False,
            "stub": {"name": "r1_a4_stub"},
            "user": {
                "reading_paragraphs_per_second": 4.0,
                "page_delay_s": 0.25,
                "patience_s": 30.0,
                "poll_interval_s": 0.1,
            },
            "budget": {
                "max_calls": 20,
                "max_tokens": 1_000_000,
                "max_duration_s": 900.0,
                "max_cost_usd": 0.0,
            },
            "real": {
                "base_url": "",
                "api_key": "",
                "model": "",
            },
        },
        "params": {
            "read_batches": 8,
            "read_batch_size": 64,
            "min_comment_windows": 2,
            "post_compaction_comment_windows": 1,
            "min_chat_turns": 1,
            "max_wait_comment_s": 30.0,
            "max_wait_compaction_s": 60.0,
        },
        "backend": {
            "command": "",
            "cwd": "",
            "config_file": None,
            "ready_path": "/api/health",
            "ready_timeout_s": 30.0,
            "env": {},
        },
    }


def load_run_config(path: str | Path) -> dict[str, Any]:
    config_path = Path(path)
    raw = tomllib.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise TypeError(f"run config must be a TOML table: {config_path}")
    return raw


def cli_run_config(args: argparse.Namespace) -> dict[str, Any]:
    data: dict[str, Any] = {}
    set_if_present(data, args, "suite", ("suite",))
    if hasattr(args, "scenario"):
        data["scenarios"] = [args.scenario] if args.scenario else []
    set_if_present(data, args, "target_url", ("target_url",))
    set_if_present(data, args, "artifact_root", ("artifact_root",))
    set_if_present(data, args, "run_id", ("run_id",))
    set_if_present(data, args, "corpus", ("corpus",))
    set_if_present(data, args, "profile", ("profile", "name"))
    set_if_present(data, args, "llm_mode", ("profile", "llm_mode"))
    set_if_present(data, args, "audit", ("profile", "audit"))
    set_if_present(data, args, "stub_profile", ("profile", "stub", "name"))
    for attr in (
        "read_batches",
        "read_batch_size",
        "min_comment_windows",
        "post_compaction_comment_windows",
        "min_chat_turns",
        "max_wait_comment_s",
        "max_wait_compaction_s",
    ):
        set_if_present(data, args, attr, ("params", attr))
    for attr in ("max_calls", "max_tokens", "max_duration_s", "max_cost_usd"):
        set_if_present(data, args, attr, ("profile", "budget", attr))
    set_if_present(data, args, "backend_command", ("backend", "command"))
    set_if_present(data, args, "backend_cwd", ("backend", "cwd"))
    set_if_present(data, args, "backend_config_file", ("backend", "config_file"))
    set_if_present(data, args, "backend_ready_path", ("backend", "ready_path"))
    set_if_present(
        data,
        args,
        "backend_ready_timeout_s",
        ("backend", "ready_timeout_s"),
    )
    return data


def set_if_present(
    data: dict[str, Any],
    args: argparse.Namespace,
    attr: str,
    path: tuple[str, ...],
) -> None:
    if not hasattr(args, attr):
        return
    value = getattr(args, attr)
    target = data
    for part in path[:-1]:
        nested = target.setdefault(part, {})
        if not isinstance(nested, dict):
            raise TypeError(f"CLI override conflicts with non-table key: {part}")
        target = nested
    target[path[-1]] = value


def deep_merge(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = dict(base)
    for key, value in override.items():
        if isinstance(result.get(key), Mapping) and isinstance(value, Mapping):
            result[key] = deep_merge(result[key], value)
            continue
        result[key] = value
    return result


def run_settings_from_mapping(data: Mapping[str, Any]) -> RunSettings:
    profile = mapping_at(data, "profile")
    user = mapping_at(profile, "profile.user")
    budget = mapping_at(profile, "profile.budget")
    stub = mapping_at(profile, "profile.stub")
    real = mapping_at(profile, "profile.real")
    backend = mapping_at(data, "backend")
    backend_env = mapping_at(backend, "backend.env")
    params = mapping_at(data, "params")
    corpus = data.get("corpus")
    backend_config_file = backend.get("config_file")
    return RunSettings(
        suite=str(data["suite"]),
        scenarios=scenario_tuple(data.get("scenarios", data.get("scenario", ()))),
        profile_name=str(profile["name"]),
        llm_mode=str(profile["llm_mode"]),
        target_url=str(data["target_url"]),
        artifact_root=Path(str(data["artifact_root"])),
        run_id=optional_str(data.get("run_id")),
        corpus=Path(str(corpus)) if corpus else None,
        audit=strict_bool(profile.get("audit", False), "profile.audit"),
        backend_agent_evidence=strict_bool(
            profile.get("backend_agent_evidence", False),
            "profile.backend_agent_evidence",
        ),
        stub_profile=str(stub["name"]),
        user=UserModel(
            reading_paragraphs_per_second=float(
                user["reading_paragraphs_per_second"]
            ),
            page_delay_s=float(user["page_delay_s"]),
            patience_s=float(user["patience_s"]),
            poll_interval_s=float(user["poll_interval_s"]),
        ),
        budget=Budget(
            max_calls=int(budget["max_calls"]),
            max_tokens=int(budget["max_tokens"]),
            max_duration_s=float(budget["max_duration_s"]),
            max_cost_usd=float(budget["max_cost_usd"]),
        ),
        params=dict(params),
        backend=BackendSettings(
            command=str(backend.get("command", "")),
            cwd=str(backend.get("cwd", "")),
            config_file=Path(str(backend_config_file))
            if backend_config_file
            else None,
            ready_path=str(backend.get("ready_path", "/api/health")),
            ready_timeout_s=float(backend.get("ready_timeout_s", 30.0)),
            env={str(key): env_value(value) for key, value in backend_env.items()},
        ),
        real_base_url=str(real.get("base_url", "")),
        real_api_key=str(real.get("api_key", "")),
        real_model=str(real.get("model", "")),
    )


def mapping_at(data: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    lookup_key = key.rsplit(".", 1)[-1]
    value = data.get(lookup_key, {})
    if not isinstance(value, Mapping):
        raise TypeError(f"{key} must be a TOML table")
    return value


def strict_bool(value: Any, key: str) -> bool:
    if isinstance(value, bool):
        return value
    raise TypeError(f"{key} must be boolean, got {type(value).__name__}")


def scenario_tuple(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,) if value else ()
    if not isinstance(value, Sequence):
        raise TypeError("scenarios must be a string or list of strings")
    if not all(isinstance(item, str) for item in value):
        raise TypeError("scenarios must be a string or list of strings")
    result = tuple(value)
    if not all(result):
        raise ValueError("scenarios must not contain empty ids")
    return result


def optional_str(value: Any) -> str | None:
    if value is None or value == "":
        return None
    return str(value)


def env_value(value: Any) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    return str(value)
