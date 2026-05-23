"""Export agent interaction audit packets and Markdown reports."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .agent_audit_report import render_agent_audit_markdown, render_prompt_markdown
from .config import VerifyConfig
from .run import RunManager

logger = logging.getLogger(__name__)

_SECRET_LITERAL_PATTERNS = (
    re.compile(r"sk-[a-zA-Z0-9]{20,}"),
    re.compile(r"(?i)bearer\s+[a-z0-9._-]{10,}"),
)


def scan_for_secret_leaks(text: str) -> list[str]:
    findings: list[str] = []
    for pattern in _SECRET_LITERAL_PATTERNS:
        if pattern.search(text):
            findings.append(pattern.pattern)
    return findings


class AgentAuditExporter:
    """Fetch agent runs and write verify audit artifacts."""

    def __init__(self, run_manager: RunManager, config: VerifyConfig):
        self.run_manager = run_manager
        self.config = config
        self._trace_to_invocation: dict[str, str] = {}

    @property
    def trace_to_invocation(self) -> dict[str, str]:
        return dict(self._trace_to_invocation)

    def export_from_agent_runs(self, agent_runs: list[dict[str, Any]]) -> dict[str, int]:
        audit_cfg = self.config.audit
        if not audit_cfg.enabled or not audit_cfg.write_markdown_report:
            return {"agent_invocations": 0, "agent_reports": 0}

        packets: list[dict[str, Any]] = []
        for run in agent_runs:
            interaction = run.get("interaction")
            if not interaction:
                continue
            packet = dict(interaction)
            packet.setdefault("invocation_id", run.get("invocation_id") or "")
            packet.setdefault("run_id", self.run_manager.run_id)
            packet["llm_mode"] = self.config.llm.mode
            packet["stub_profile"] = (
                self.config.llm.stub_profile if not self.config.is_real_llm else None
            )
            usage = packet.get("usage") or {}
            usage["source"] = self.config.usage_source
            packet["usage"] = usage
            if run.get("trace_id"):
                self._trace_to_invocation[str(run["trace_id"])] = packet["invocation_id"]
            packets.append(packet)

        if not packets:
            logger.warning("agent audit export: no interaction packets available")
            return {"agent_invocations": 0, "agent_reports": 0}

        return self._write_artifacts(packets)

    def _write_artifacts(self, packets: list[dict[str, Any]]) -> dict[str, int]:
        audit_cfg = self.config.audit
        base = self.run_manager.base_dir / "audit"
        interactions_dir = base / "agent_interactions"
        reports_dir = base / "agent_reports"
        prompts_dir = base / "prompts"
        contexts_dir = base / "contexts"
        for directory in (interactions_dir, reports_dir, prompts_dir, contexts_dir):
            directory.mkdir(parents=True, exist_ok=True)

        index_records: list[dict[str, Any]] = []
        safety_records: list[dict[str, Any]] = []
        context_written: set[str] = set()

        for packet in packets:
            invocation_id = packet["invocation_id"]
            interaction_path = f"audit/agent_interactions/{invocation_id}.json"
            report_path = f"audit/agent_reports/{invocation_id}.md"
            packet["markdown_report_path"] = report_path

            interaction_file = interactions_dir / f"{invocation_id}.json"
            interaction_file.write_text(
                json.dumps(packet, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            markdown = render_agent_audit_markdown(packet)
            (reports_dir / f"{invocation_id}.md").write_text(markdown, encoding="utf-8")

            if audit_cfg.write_prompt_markdown:
                prompt_md = render_prompt_markdown(packet)
                (prompts_dir / f"{invocation_id}.prompt.md").write_text(
                    prompt_md, encoding="utf-8"
                )

            if audit_cfg.write_context_sidecars:
                context_hash = packet.get("context_hash") or ""
                if context_hash and context_hash not in context_written:
                    short = context_hash.replace("sha256:", "")[:12]
                    context_path = contexts_dir / f"context_{short}.json"
                    context_path.write_text(
                        json.dumps(packet.get("injected_context") or {}, ensure_ascii=False, indent=2)
                        + "\n",
                        encoding="utf-8",
                    )
                    context_written.add(context_hash)

            leaks = scan_for_secret_leaks(json.dumps(packet, ensure_ascii=False))
            safety_records.append(
                {
                    "invocation_id": invocation_id,
                    "trace_id": packet.get("trace_id"),
                    "secret_redaction_count": (packet.get("content_rendering") or {}).get(
                        "secret_redaction_count", 0
                    ),
                    "secret_leak_detected": bool(leaks),
                    "secret_leak_patterns": leaks,
                    "checked_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                }
            )

            index_records.append(
                {
                    "invocation_id": invocation_id,
                    "run_id": packet.get("run_id"),
                    "scenario_id": packet.get("scenario_id"),
                    "step_id": packet.get("step_id"),
                    "agent": packet.get("agent"),
                    "trace_id": packet.get("trace_id"),
                    "job_id": packet.get("job_id"),
                    "interaction_path": interaction_path,
                    "markdown_report_path": report_path,
                    "prompt_version": packet.get("prompt_version"),
                    "context_hash": packet.get("context_hash"),
                    "usage_source": (packet.get("usage") or {}).get("source"),
                    "input_tokens": (packet.get("usage") or {}).get("input_tokens"),
                    "output_tokens": (packet.get("usage") or {}).get("output_tokens"),
                    "total_ms": (packet.get("timing") or {}).get("total_ms"),
                    "created_at": packet.get("created_at"),
                }
            )

        invocations_path = base / "agent_invocations.ndjson"
        invocations_path.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in index_records),
            encoding="utf-8",
        )

        safety_path = base / "audit_safety_report.ndjson"
        safety_path.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in safety_records),
            encoding="utf-8",
        )

        index_md_lines = [
            "# Agent Audit Reports",
            "",
            f"run_id: {self.run_manager.run_id}",
            "",
            "| Invocation | Agent | Scenario | Input | Output | Total ms | Report |",
            "|---|---|---|---:|---:|---:|---|",
        ]
        for row in index_records:
            index_md_lines.append(
                f"| {row['invocation_id']} | {row.get('agent')} | {row.get('scenario_id')} | "
                f"{row.get('input_tokens')} | {row.get('output_tokens')} | "
                f"{row.get('total_ms')} | [{row['invocation_id']}]({row['markdown_report_path']}) |"
            )
        index_md_lines.append("")
        (reports_dir / "index.md").write_text("\n".join(index_md_lines), encoding="utf-8")

        return {
            "agent_invocations": len(index_records),
            "agent_reports": len(index_records),
            "agent_interactions": len(index_records),
            "audit_safety_records": len(safety_records),
        }


def _validate_single_invocation(run_dir: Path, row: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    invocation_id = row.get("invocation_id")
    interaction_rel = row.get("interaction_path")
    report_rel = row.get("markdown_report_path")

    if not interaction_rel or not (run_dir / interaction_rel).exists():
        failures.append(
            f"audit_interaction_missing: interaction file missing for {invocation_id}"
        )
        return failures

    if not report_rel or not (run_dir / report_rel).exists():
        failures.append(
            f"audit_markdown_report_missing: markdown report missing for {invocation_id}"
        )
        return failures

    report_text = (run_dir / report_rel).read_text(encoding="utf-8")
    if "## Usage / Timing" not in report_text:
        failures.append(
            f"audit_usage_timing_missing: Usage/Timing section missing for {invocation_id}"
        )
    if scan_for_secret_leaks(report_text):
        failures.append(f"audit_secret_leak_detected: report {invocation_id}")

    interaction = json.loads((run_dir / interaction_rel).read_text(encoding="utf-8"))
    if not interaction.get("prompt_messages"):
        failures.append(f"audit_prompt_missing: prompt_messages missing for {invocation_id}")
    if not interaction.get("injected_context"):
        failures.append(
            f"audit_injected_context_missing: injected_context missing for {invocation_id}"
        )
    if not interaction.get("llm_rounds"):
        failures.append(f"audit_interaction_missing: llm_rounds missing for {invocation_id}")

    tool_events = interaction.get("tool_events") or []
    llm_rounds = interaction.get("llm_rounds") or []
    has_tool_calls = any((r.get("response") or {}).get("tool_calls") for r in llm_rounds)
    if has_tool_calls and not tool_events:
        failures.append(f"audit_tool_result_missing: tool_events missing for {invocation_id}")

    return failures


def assert_agent_audit_artifacts(
    run_dir: Path,
    *,
    require_thinking_reason: bool = False,
) -> list[str]:
    """Return audit assertion failure messages (empty if all pass)."""
    audit_dir = run_dir / "audit"
    failures: list[str] = []

    invocations_path = audit_dir / "agent_invocations.ndjson"
    if not invocations_path.exists():
        failures.append("audit_interaction_missing: agent_invocations.ndjson missing")
        return failures

    invocations = [
        json.loads(line)
        for line in invocations_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not invocations:
        failures.append("audit_interaction_missing: agent_invocations.ndjson is empty")
        return failures

    for row in invocations:
        failures.extend(_validate_single_invocation(run_dir, row))

    if require_thinking_reason:
        for row in invocations:
            interaction_rel = row.get("interaction_path")
            if not interaction_rel or not (run_dir / interaction_rel).exists():
                continue
            interaction = json.loads((run_dir / interaction_rel).read_text(encoding="utf-8"))
            thinking_seen = any(
                ((round_item.get("response") or {}).get("thinking") or {}).get("available")
                or ((round_item.get("response") or {}).get("thinking") or {}).get("reason")
                for round_item in interaction.get("llm_rounds") or []
            )
            if not thinking_seen:
                failures.append(
                    f"audit_thinking_missing: thinking unavailable for {row.get('invocation_id')}"
                )

    return failures


def enrich_comment_records_with_agent_refs(
    comments_path: Path,
    trace_to_invocation: dict[str, str],
) -> int:
    if not comments_path.exists() or not trace_to_invocation:
        return 0

    updated: list[dict[str, Any]] = []
    changed = 0
    for line in comments_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        trace_id = record.get("trace_id") or ""
        invocation_id = trace_to_invocation.get(trace_id)
        if invocation_id:
            record["agent_invocation_id"] = invocation_id
            record["agent_interaction_path"] = f"audit/agent_interactions/{invocation_id}.json"
            record["agent_report_path"] = f"audit/agent_reports/{invocation_id}.md"
            changed += 1
        updated.append(record)

    comments_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in updated),
        encoding="utf-8",
    )
    return changed
