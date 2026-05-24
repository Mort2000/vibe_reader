"""Run manager: creates run directories, generates run_id and run_manifest.json."""

from __future__ import annotations

import hashlib
import json
import pathlib
import subprocess
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .config import VerifyConfig


def generate_run_id() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    short = uuid.uuid4().hex[:8]
    return f"{ts}_{short}"


def get_git_info() -> tuple[str, bool]:
    """Return (commit_hash, is_dirty) from git."""
    try:
        commit = (
            subprocess.check_output(
                ["git", "rev-parse", "HEAD"],
                stderr=subprocess.DEVNULL,
            )
            .decode()
            .strip()
        )
        dirty = bool(
            subprocess.check_output(
                ["git", "status", "--porcelain"],
                stderr=subprocess.DEVNULL,
            )
            .decode()
            .strip()
        )
        return commit, dirty
    except Exception:
        return "", True


def hash_string(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()[:16]


@dataclass
class RealLLMCallTracker:
    """Tracks real LLM usage for budget guardrails in R1."""

    call_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    max_input_tokens_single: int = 0
    max_output_tokens_single: int = 0
    total_cost_usd: float = 0.0
    cost_reported: bool = False
    cost_guardrail_status: str = "not_checked"
    budget_exceeded: bool = False
    budget_reason: str = ""
    phase_coverage: dict[str, bool] = field(
        default_factory=lambda: {
            "A2_comments": False,
            "A3_compaction": False,
            "A4_full_flow": False,
        }
    )

    def record_call(
        self,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cost_usd: float | None = None,
        config: VerifyConfig | None = None,
    ) -> None:
        self.call_count += 1
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.max_input_tokens_single = max(self.max_input_tokens_single, input_tokens)
        self.max_output_tokens_single = max(
            self.max_output_tokens_single, output_tokens
        )
        if cost_usd is not None:
            self.total_cost_usd += cost_usd
            self.cost_reported = True

        if config is not None:
            self._check_per_call_limits(config, input_tokens, output_tokens)

    def _check_per_call_limits(
        self, config: VerifyConfig, input_tokens: int, output_tokens: int
    ) -> None:
        if self.budget_exceeded:
            return
        limits = config.real_llm
        if input_tokens > limits.max_input_tokens_per_call:
            self.budget_exceeded = True
            self.budget_reason = "real_llm_budget_exceeded:input_tokens_per_call"
            return
        if output_tokens > limits.max_output_tokens_per_call:
            self.budget_exceeded = True
            self.budget_reason = "real_llm_budget_exceeded:output_tokens_per_call"

    def check_budget(self, config: VerifyConfig) -> None:
        if self.budget_exceeded:
            return

        limits = config.real_llm
        if self.call_count > limits.max_calls:
            self.budget_exceeded = True
            self.budget_reason = "real_llm_budget_exceeded:max_calls"
            return
        if self.max_input_tokens_single > limits.max_input_tokens_per_call:
            self.budget_exceeded = True
            self.budget_reason = "real_llm_budget_exceeded:input_tokens_per_call"
            return
        if self.max_output_tokens_single > limits.max_output_tokens_per_call:
            self.budget_exceeded = True
            self.budget_reason = "real_llm_budget_exceeded:output_tokens_per_call"
            return

        if limits.max_total_cost_usd > 0:
            if self.cost_reported:
                self.cost_guardrail_status = "enforced"
                if self.total_cost_usd > limits.max_total_cost_usd:
                    self.budget_exceeded = True
                    self.budget_reason = "real_llm_budget_exceeded:total_cost_usd"
            else:
                # Provider cost is not available yet; enforce once usage/cost is recorded.
                self.cost_guardrail_status = "skipped_no_cost_data"


class RunManager:
    """Manages a single verification run's output directory and manifest."""

    def __init__(self, config: VerifyConfig, run_id: str | None = None):
        self.config = config
        self.run_id = run_id or _env_run_id() or generate_run_id()
        self.base_dir = pathlib.Path("verify_runs") / self.run_id
        self.started_at: datetime | None = None
        self.ended_at: datetime | None = None
        self._corpus_sha256: list[str] = []
        self._security_checks: dict[str, Any] = {}
        self._backend_version: str | None = None
        self._data_lifecycle: dict[str, Any] = {}
        self._aimock_info: dict[str, Any] | None = None
        self.real_llm_tracker = RealLLMCallTracker()

    def set_aimock_info(self, info: dict[str, Any] | None) -> None:
        self._aimock_info = info

    def set_security_checks(self, checks: dict[str, Any]) -> None:
        self._security_checks = checks

    def set_data_lifecycle(self, lifecycle: dict[str, Any]) -> None:
        self._data_lifecycle = lifecycle

    def set_backend_version(self, version: str | None) -> None:
        self._backend_version = version

    def start(self) -> pathlib.Path:
        """Create output directories and record start time."""
        self.started_at = datetime.now(timezone.utc)
        dirs = [
            self.base_dir,
            self.base_dir / "traces",
            self.base_dir / "audit" / "samples",
            self.base_dir / "audit" / "agent_interactions",
            self.base_dir / "audit" / "agent_reports",
            self.base_dir / "audit" / "prompts",
            self.base_dir / "audit" / "contexts",
            self.base_dir / "judge",
            self.base_dir / "reports",
        ]
        for d in dirs:
            d.mkdir(parents=True, exist_ok=True)
        return self.base_dir

    def finish(self) -> None:
        self.ended_at = datetime.now(timezone.utc)

    def set_corpus_sha256(self, sha_list: list[str]) -> None:
        self._corpus_sha256 = sha_list

    def write_manifest(self, backend_version: str | None = None) -> pathlib.Path:
        """Write run_manifest.json."""
        git_commit, git_dirty = get_git_info()
        config_hash = hash_string(repr(self.config))
        resolved_backend_version = backend_version or self._backend_version
        cfg = self.config
        tracker = self.real_llm_tracker

        base_url = cfg.real_llm.base_url if cfg.is_real_llm else ""
        aimock = self._aimock_info or {}
        manifest: dict[str, Any] = {
            "run_id": self.run_id,
            "started_at": _fmt_ts(self.started_at),
            "ended_at": _fmt_ts(self.ended_at),
            "git_commit": git_commit,
            "git_dirty": git_dirty,
            "suite": cfg.run.suite,
            "target_url": cfg.target.base_url,
            "backend_version": resolved_backend_version,
            "llm_mode": cfg.llm.mode,
            "stub_profile": cfg.llm.stub_profile if not cfg.is_real_llm else None,
            "llm_stub_provider": aimock.get("provider")
            if not cfg.is_real_llm
            else None,
            "aimock_version": aimock.get("version") if not cfg.is_real_llm else None,
            "aimock_base_url": aimock.get("base_url") if not cfg.is_real_llm else None,
            "aimock_fixture_hash": aimock.get("fixture_hash")
            if not cfg.is_real_llm
            else None,
            "aimock_profile_hash": aimock.get("profile_hash")
            if not cfg.is_real_llm
            else None,
            "real_llm": cfg.is_real_llm,
            "model": cfg.effective_model(),
            "llm_base_url_hash": hash_string(base_url) if base_url else None,
            "real_llm_call_count": tracker.call_count if cfg.is_real_llm else 0,
            "real_llm_input_tokens": tracker.input_tokens if cfg.is_real_llm else 0,
            "real_llm_output_tokens": tracker.output_tokens if cfg.is_real_llm else 0,
            "real_llm_max_input_tokens_single": (
                tracker.max_input_tokens_single if cfg.is_real_llm else 0
            ),
            "real_llm_max_output_tokens_single": (
                tracker.max_output_tokens_single if cfg.is_real_llm else 0
            ),
            "real_llm_total_cost_usd": tracker.total_cost_usd
            if cfg.is_real_llm
            else 0.0,
            "real_llm_cost_guardrail_status": (
                tracker.cost_guardrail_status if cfg.is_real_llm else None
            ),
            "real_llm_budget_exceeded": tracker.budget_exceeded,
            "real_llm_budget_reason": tracker.budget_reason or None,
            "real_llm_phase_coverage": tracker.phase_coverage,
            "usage_source": cfg.usage_source,
            "corpus_sha256": self._corpus_sha256,
            "config_hash": config_hash,
            "security_checks": self._security_checks,
            "target_data_dir": str(cfg.target.data_dir),
            "data_lifecycle": self._data_lifecycle,
        }

        path = self.base_dir / "run_manifest.json"
        path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
        return path

    def write_ndjson(self, filename: str, records: list[dict]) -> pathlib.Path:
        """Append records to an NDJSON file in the run directory."""
        path = self.base_dir / filename
        with open(path, "a", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return path


def _env_run_id() -> str | None:
    import os

    return os.environ.get("VIBE_READER_VERIFY_RUN_ID")


def _fmt_ts(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
