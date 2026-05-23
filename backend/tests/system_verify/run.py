"""Run manager: creates run directories, generates run_id and run_manifest.json."""

from __future__ import annotations

import hashlib
import json
import pathlib
import subprocess
import uuid
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

        manifest: dict[str, Any] = {
            "run_id": self.run_id,
            "started_at": _fmt_ts(self.started_at),
            "ended_at": _fmt_ts(self.ended_at),
            "git_commit": git_commit,
            "git_dirty": git_dirty,
            "suite": self.config.run.suite,
            "target_url": self.config.target.base_url,
            "backend_version": resolved_backend_version,
            "model": self.config.llm.model,
            "llm_base_url_hash": hash_string(self.config.llm.base_url)
            if self.config.llm.base_url
            else None,
            "corpus_sha256": self._corpus_sha256,
            "config_hash": config_hash,
            "security_checks": self._security_checks,
            "target_data_dir": str(self.config.target.data_dir),
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
