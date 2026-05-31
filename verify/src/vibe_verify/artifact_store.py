"""Run artifact persistence, reporting, and secret safety checks."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import AgentInvocation, jsonable

_SLUG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(
        r"(?i)\b(?:authorization|proxy-authorization)\s*[:=]\s*"
        r"(?:bearer|basic)\s+\S+"
    ),
    re.compile(
        r"(?im)^\s*(?:cookie|set-cookie|x-api-key|api-key|"
        r"openai-api-key|anthropic-api-key)\s*[:=]\s*(?!\*{3})\S+"
    ),
    re.compile(
        r'(?i)"(?:api[_-]?key|secret[_-]?key|access[_-]?token|'
        r"refresh[_-]?token|id[_-]?token|openai[_-]?api[_-]?key|"
        r"anthropic[_-]?api[_-]?key|authorization|proxy-authorization|"
        r'cookie|set-cookie)"\s*:\s*"(?!\*{3})[^"]+"'
    ),
    re.compile(
        r"(?im)^\s*(?:OPENAI_API_KEY|ANTHROPIC_API_KEY|API_KEY|"
        r"SECRET_KEY|ACCESS_TOKEN|REFRESH_TOKEN|ID_TOKEN|COOKIE|"
        r"SET_COOKIE|X_API_KEY)\s*[:=]\s*(?!\*{3})\S+"
    ),
)
_SENSITIVE_HEADER_NAMES = {
    "authorization",
    "proxy-authorization",
    "cookie",
    "set-cookie",
    "x-api-key",
    "api-key",
    "openai-api-key",
    "anthropic-api-key",
}


@dataclass(frozen=True)
class SafetyFinding:
    """Potential secret leak in a persisted artifact."""

    path: str
    pattern: str


class ArtifactStore:
    """Own one run directory and all persisted verification evidence."""

    def __init__(self, root: str | Path, run_id: str, *, audit_enabled: bool = False):
        validate_slug(run_id, "run_id")
        self.root = Path(root)
        self.run_id = run_id
        self.run_dir = self.root / run_id
        self.audit_enabled = audit_enabled

    def start(self) -> Path:
        if self.run_dir.exists() and any(self.run_dir.iterdir()):
            raise FileExistsError(
                f"run artifact directory already exists: {self.run_dir}"
            )
        for relative in (
            "",
            "evidence",
            "traces",
            "audit/agent_interactions",
            "audit/prompts",
            "stub",
            "reports",
            "failure",
        ):
            (self.run_dir / relative).mkdir(parents=True, exist_ok=True)
        return self.run_dir

    def append_ndjson(self, relative: str, records: Iterable[Any]) -> Path:
        path = self._path(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as stream:
            for record in records:
                stream.write(json.dumps(jsonable(record), ensure_ascii=False) + "\n")
        return path

    def write_json(self, relative: str, value: Any, *, immutable: bool = False) -> Path:
        path = self._path(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        if immutable and path.exists():
            raise FileExistsError(f"artifact is immutable: {path}")
        path.write_text(
            json.dumps(jsonable(value), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return path

    def write_text(self, relative: str, value: str, *, immutable: bool = False) -> Path:
        path = self._path(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        if immutable and path.exists():
            raise FileExistsError(f"artifact is immutable: {path}")
        path.write_text(value, encoding="utf-8")
        return path

    def write_manifest(self, manifest: dict[str, Any]) -> Path:
        return self.write_json("run_manifest.json", manifest, immutable=True)

    def write_audit_packet(self, invocation: AgentInvocation) -> Path:
        if not self.audit_enabled:
            raise RuntimeError("full Agent audit requires audit_enabled=True")
        invocation_id = validate_slug(invocation.id, "agent invocation id")
        packet = invocation_to_packet(invocation)
        path = self.write_json(
            f"audit/agent_interactions/{invocation_id}.json",
            packet,
            immutable=True,
        )
        prompt = "\n\n".join(
            f"## {message.get('role', 'unknown')}\n\n{message.get('content', '')}"
            for message in invocation.prompt_messages
        )
        self.write_text(
            f"audit/prompts/{invocation_id}.md",
            prompt + "\n",
            immutable=True,
        )
        return path

    def write_failure(
        self, summary: str, context: dict[str, Any] | None = None
    ) -> Path:
        return self.write_json(
            "failure/snapshot.json",
            {"summary": summary, "context": context or {}},
        )

    def scan_secrets(self) -> list[SafetyFinding]:
        findings: list[SafetyFinding] = []
        for path in self.run_dir.rglob("*"):
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            findings.extend(
                scan_text_for_secrets(str(path.relative_to(self.run_dir)), text)
            )
        return findings

    def scan_text(self, relative: str, text: str) -> list[SafetyFinding]:
        return scan_text_for_secrets(relative, text)

    def write_summary(
        self,
        *,
        status: str,
        scenarios: list[dict[str, Any]],
        findings: list[SafetyFinding],
        manifest: dict[str, Any] | None = None,
        evidence_gaps: list[str] | None = None,
    ) -> Path:
        manifest = manifest or {}
        evidence_gaps = evidence_gaps or []
        text = self.render_summary(
            status=status,
            scenarios=scenarios,
            findings=findings,
            manifest=manifest,
            evidence_gaps=evidence_gaps,
        )
        return self.write_text("reports/summary.md", text)

    def render_summary(
        self,
        *,
        status: str,
        scenarios: list[dict[str, Any]],
        findings: list[SafetyFinding],
        manifest: dict[str, Any] | None = None,
        evidence_gaps: list[str] | None = None,
    ) -> str:
        manifest = manifest or {}
        evidence_gaps = evidence_gaps or []
        lines = [
            "# Vibe Reader Verify Summary",
            "",
            f"- run_id: `{self.run_id}`",
            f"- status: `{status}`",
            f"- scenarios: `{len(scenarios)}`",
            f"- safety_findings: `{len(findings)}`",
            f"- llm_call_count: `{manifest.get('llm_call_count', 0)}`",
            f"- token_total: `{manifest.get('token_total', 0)}`",
            f"- duration_ms: `{round(float(manifest.get('duration_ms', 0)), 1)}`",
            "",
            "## Scenarios",
            "",
        ]
        if scenarios:
            for item in scenarios:
                line = f"- `{item.get('id', '')}`: `{item.get('status', '')}`"
                if item.get("error"):
                    error = redact_secrets_text(str(item.get("error", "")))[:300]
                    line += f" - {item.get('error_type', 'Error')}: {error}"
                lines.append(line)
        else:
            lines.append("- No scenarios executed.")
        if evidence_gaps:
            lines.extend(["", "## Evidence Gaps", ""])
            lines.extend(f"- {item}" for item in evidence_gaps)
        if findings:
            lines.extend(["", "## Safety Findings", ""])
            lines.extend(
                f"- `{item.path}` matched `{item.pattern}`" for item in findings
            )
        lines.extend(
            [
                "",
                "## Artifact Index",
                "",
                "- `run_manifest.json`: run identity, profile, budget, corpus, "
                "and totals",
                "- `evidence/api.ndjson`: sanitized backend API interactions",
                "- `evidence/sse.ndjson`: sanitized stream events",
                "- `evidence/user_interactions.ndjson`: user script actions",
                "- `evidence/agent_invocations.ndjson`: sanitized LLM invocation "
                "summaries",
                "- `stub/journal.ndjson`: sanitized stub provider journal",
                "- `audit/`: full prompts and provider records when audit is enabled",
                "- `failure/snapshot.json`: failure context when present",
            ]
        )
        return "\n".join(lines) + "\n"

    def _path(self, relative: str) -> Path:
        path = Path(relative)
        if path.is_absolute() or not path.parts or ".." in path.parts:
            raise ValueError(
                f"artifact path must stay within run directory: {relative}"
            )
        root = self.run_dir.resolve()
        resolved = (self.run_dir / path).resolve()
        if not resolved.is_relative_to(root):
            raise ValueError(
                f"artifact path must stay within run directory: {relative}"
            )
        return resolved


def invocation_to_packet(invocation: AgentInvocation) -> dict[str, Any]:
    """Build the explicit high-sensitivity audit representation."""
    packet = jsonable(invocation)
    packet["usage"] = invocation.usage.to_dict()
    packet["evidence_refs"] = {
        "api": "evidence/api.ndjson",
        "sse": "evidence/sse.ndjson",
        "user_interactions": "evidence/user_interactions.ndjson",
        "agent_invocations": "evidence/agent_invocations.ndjson",
        "stub_journal": "stub/journal.ndjson",
        "audit_prompt": f"audit/prompts/{invocation.id}.md",
    }
    return packet


def redact_headers(headers: dict[str, str]) -> dict[str, str]:
    """Redact credentials before ordinary request evidence is persisted."""
    return {
        key: "***REDACTED***" if key.lower() in _SENSITIVE_HEADER_NAMES else value
        for key, value in headers.items()
    }


def scan_text_for_secrets(path: str, text: str) -> list[SafetyFinding]:
    findings: list[SafetyFinding] = []
    for pattern in _SECRET_PATTERNS:
        if pattern.search(text):
            findings.append(SafetyFinding(path=path, pattern=pattern.pattern))
    return findings


def redact_secrets_text(text: str) -> str:
    redacted = text
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub("***REDACTED***", redacted)
    return redacted


def validate_slug(value: str, label: str) -> str:
    if not _SLUG.fullmatch(value):
        raise ValueError(f"{label} must match {_SLUG.pattern!r}; got {value!r}")
    return value
