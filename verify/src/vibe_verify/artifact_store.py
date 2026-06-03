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

    def write_llm_interaction_report(
        self, invocations: Iterable[AgentInvocation]
    ) -> Path:
        if not self.audit_enabled:
            raise RuntimeError(
                "full LLM interaction report requires audit_enabled=True"
            )
        text = render_llm_interaction_report(self.run_id, list(invocations))
        return self.write_text("audit/llm_interactions.md", text, immutable=True)

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
                "- `audit/llm_interactions.md`: human-readable full LLM "
                "interaction transcript when audit is enabled",
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
        "llm_interactions_report": "audit/llm_interactions.md",
    }
    return packet


def render_llm_interaction_report(
    run_id: str, invocations: list[AgentInvocation]
) -> str:
    """Render high-sensitivity Agent invocations for human audit."""
    lines = [
        "# LLM Interaction Audit Report",
        "",
        f"- run_id: `{run_id}`",
        f"- llm_call_count: `{len(invocations)}`",
        "- sensitivity: `high`",
        "- source: `AgentInvocation` evidence captured during this verify run",
        "",
    ]
    if not invocations:
        lines.append("No LLM invocations were recorded.")
        return "\n".join(lines) + "\n"

    for index, invocation in enumerate(invocations, start=1):
        lines.extend(render_invocation_section(index, invocation))
    return "\n".join(lines) + "\n"


def render_invocation_section(index: int, invocation: AgentInvocation) -> list[str]:
    packet = _audit_packet(invocation)
    usage = invocation.usage.to_dict()
    lines = [
        f"## {index}. `{invocation.id}` · {invocation.agent}",
        "",
    ]
    lines.extend(_render_audit_metadata(invocation, packet, usage))
    lines.extend(["", "### Prompt 内容", ""])
    lines.extend(_render_audit_prompt(invocation.prompt_messages))
    lines.extend(["", "### AI 思考", ""])
    lines.extend(_render_audit_thinking(invocation, packet))
    lines.extend(["", "### AI 答复", ""])
    lines.extend(_render_audit_reply(invocation, packet))
    lines.extend(["", "### 工具调用", ""])
    lines.extend(_render_audit_tool_calls(invocation))
    if invocation.error:
        lines.extend(["", "### 错误", "", _text_fence(str(invocation.error))])
    lines.extend(
        [
            "",
            f"- 完整记录：`audit/agent_interactions/{invocation.id}.json`",
        ]
    )
    return lines


def _audit_packet(invocation: AgentInvocation) -> dict[str, Any]:
    response = invocation.response
    if isinstance(response, dict):
        return response
    return {}


def _is_audit_packet(packet: dict[str, Any]) -> bool:
    return bool(
        packet.get("schema_version")
        or packet.get("llm_rounds")
        or packet.get("prompt_messages")
    )


def _render_audit_metadata(
    invocation: AgentInvocation,
    packet: dict[str, Any],
    usage: dict[str, Any],
) -> list[str]:
    correlation = jsonable(invocation.correlation)
    context_hash = str(
        packet.get("context_hash")
        or correlation.get("context_hash")
        or ""
    )
    packet_usage = packet.get("usage") if isinstance(packet.get("usage"), dict) else {}
    timing = packet.get("timing") if isinstance(packet.get("timing"), dict) else {}
    input_tokens = int(usage.get("input") or packet_usage.get("input_tokens") or 0)
    output_tokens = int(usage.get("output") or packet_usage.get("output_tokens") or 0)
    cached_tokens = int(
        usage.get("cached_input") or packet_usage.get("cached_input_tokens") or 0
    )
    total_tokens = int(
        usage.get("total") or input_tokens + output_tokens
    )
    duration_ms = invocation.duration_ms
    if duration_ms is None:
        duration_ms = timing.get("total_ms")
    ttft_ms = invocation.ttft_ms
    if ttft_ms is None:
        ttft_ms = timing.get("ttft_ms")
    lines = [
        f"- model: `{usage.get('model') or packet.get('model', '')}`",
        f"- usage_source: `{usage.get('source') or packet_usage.get('source', '')}`",
        f"- tokens_input: `{format_token_count(input_tokens)}`",
        f"- tokens_output: `{format_token_count(output_tokens)}`",
        f"- tokens_cached_input: `{format_token_count(cached_tokens)}`",
        f"- tokens_total: `{format_token_count(total_tokens)}`",
        f"- duration_s: `{format_duration_seconds(duration_ms)}`",
        f"- ttft_s: `{format_duration_seconds(ttft_ms)}`",
        f"- retries: `{invocation.retries or timing.get('retry_count', 0)}`",
    ]
    created_at = packet.get("created_at")
    if created_at:
        lines.append(f"- 答复时间: `{created_at}`")
    started_at = packet.get("started_at") or packet.get("request_started_at")
    if started_at:
        lines.append(f"- 请求时间: `{started_at}`")
    if context_hash:
        lines.append(f"- context_hash: `{context_hash}`")
    block_hashes = _collect_content_block_hashes(invocation.prompt_messages)
    for block_hash in block_hashes:
        lines.append(f"- block_hash: `{block_hash}`")
    return lines


def _collect_content_block_hashes(messages: list[dict[str, Any]]) -> list[str]:
    hashes: list[str] = []
    seen: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            block_hash = value.get("content_hash") or value.get("hash")
            if isinstance(block_hash, str) and block_hash and block_hash not in seen:
                seen.add(block_hash)
                hashes.append(block_hash)
            for item in value.values():
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    for message in messages:
        visit(message.get("content"))
    return hashes


def _render_audit_prompt(messages: list[dict[str, Any]]) -> list[str]:
    if not messages:
        return ["（无 Prompt 记录）", ""]
    lines: list[str] = []
    for message in messages:
        role = str(message.get("role", "unknown"))
        lines.extend([f"#### {role}", ""])
        content = message.get("content", "")
        if isinstance(content, str):
            if content.strip():
                lines.extend([_text_fence(content), ""])
            continue
        if isinstance(content, list):
            for segment in content:
                if isinstance(segment, dict):
                    lines.extend(_render_prompt_segment(segment))
                elif segment:
                    lines.append(str(segment))
                    lines.append("")
    return lines


def _render_prompt_segment(segment: dict[str, Any]) -> list[str]:
    if segment.get("type") == "original_text_block":
        para_range = segment.get("paragraph_range") or [None, None]
        lines = [
            (
                f"**{segment.get('component', 'original_text_block')}** · "
                f"P{para_range[0]}–P{para_range[1]} · "
                f"{segment.get('paragraph_count', 0)} 段 · "
                f"{segment.get('char_count', 0)} 字 · "
                f"~{segment.get('token_estimate', 0)} tokens"
            ),
            f"- block_hash: `{segment.get('content_hash', '')}`",
            "",
        ]
        if segment.get("text_mode") == "full":
            for para in segment.get("paragraphs") or []:
                if not isinstance(para, dict):
                    continue
                lines.extend(
                    [
                        f"##### P{para.get('paragraph_idx')}",
                        "",
                        _text_fence(str(para.get("text", ""))),
                        "",
                    ]
                )
        else:
            first = segment.get("first_paragraph") or {}
            last = segment.get("last_paragraph") or {}
            if first.get("text"):
                lines.extend(
                    [
                        "##### 段首",
                        "",
                        _text_fence(str(first.get("text", ""))),
                        "",
                    ]
                )
            if last.get("text") and last.get("paragraph_idx") != first.get(
                "paragraph_idx"
            ):
                lines.extend(
                    [
                        "##### 段尾",
                        "",
                        _text_fence(str(last.get("text", ""))),
                        "",
                    ]
                )
        return lines
    text = str(segment.get("text", ""))
    if not text.strip():
        return []
    return [_text_fence(text), ""]


def _render_audit_thinking(
    invocation: AgentInvocation, packet: dict[str, Any]
) -> list[str]:
    chunks: list[str] = []
    if invocation.thinking:
        chunks.append(str(invocation.thinking))
    elif _is_audit_packet(packet):
        for round_index, round_item in enumerate(packet.get("llm_rounds") or [], 1):
            thinking = (round_item.get("response") or {}).get("thinking") or {}
            if thinking.get("available") and thinking.get("text"):
                multi_round = len(packet.get("llm_rounds") or []) > 1
                prefix = f"#### Round {round_index}" if multi_round else ""
                if prefix:
                    chunks.extend([prefix, ""])
                chunks.append(str(thinking["text"]))
                chunks.append("")
            elif thinking.get("reason"):
                chunks.append(f"_不可用: {thinking['reason']}_")
    if chunks:
        body = "\n".join(chunks).strip()
        return [_text_fence(redact_secrets_text(body)), ""]
    if invocation.thinking_unavailable_reason:
        return [f"_{invocation.thinking_unavailable_reason}_", ""]
    return ["_（未捕获思考内容）_", ""]


def _assistant_text_from_packet(packet: dict[str, Any]) -> str:
    final_result = packet.get("final_result") or {}
    ai_msg = final_result.get("ai_msg")
    if ai_msg:
        return str(ai_msg)
    for round_item in reversed(packet.get("llm_rounds") or []):
        content = (round_item.get("response") or {}).get("content") or ""
        if str(content).strip():
            return str(content)
    return ""


def _render_audit_reply(
    invocation: AgentInvocation, packet: dict[str, Any]
) -> list[str]:
    if _is_audit_packet(packet):
        lines: list[str] = []
        final_result = packet.get("final_result") or {}
        assistant_text = _assistant_text_from_packet(packet)
        if assistant_text.strip():
            lines.extend([_text_fence(redact_secrets_text(assistant_text)), ""])
        comments = final_result.get("comments_created") or []
        if comments:
            lines.append("**已创建评论**")
            lines.append("")
            for comment in comments:
                if not isinstance(comment, dict):
                    continue
                lines.append(
                    f"- P{comment.get('paragraph_idx')}: "
                    f"`{comment.get('comment_type')}` — {comment.get('text')}"
                )
            lines.append("")
        if final_result.get("no_call"):
            lines.append("- 模型未调用工具（no_call）")
            lines.append("")
        if not lines:
            lines.append("_（无自然语言答复；结果见工具调用）_")
            lines.append("")
        return lines
    response = invocation.response
    if isinstance(response, str) and response.strip():
        return [_text_fence(redact_secrets_text(response)), ""]
    if isinstance(response, dict):
        for key in ("content", "ai_msg", "text", "message"):
            value = response.get(key)
            if isinstance(value, str) and value.strip():
                return [_text_fence(redact_secrets_text(value)), ""]
        return [
            _json_fence(redact_secrets_text(json.dumps(response, ensure_ascii=False))),
            "",
        ]
    if response not in (None, ""):
        return [_text_fence(redact_secrets_text(str(response))), ""]
    return ["_（无答复记录）_", ""]


def _normalize_tool_arguments(arguments: dict[str, Any]) -> Any:
    payload = arguments.get("payload")
    if isinstance(payload, dict):
        raw = payload.get("raw")
        if isinstance(raw, str):
            parsed = _try_parse_embedded_json(raw)
            if parsed is not None:
                return parsed
    return arguments


def _render_audit_tool_calls(invocation: AgentInvocation) -> list[str]:
    if not invocation.tool_calls:
        return ["（无工具调用）", ""]
    lines: list[str] = []
    for index, call in enumerate(invocation.tool_calls, start=1):
        args = _normalize_tool_arguments(dict(call.arguments))
        payload = {
            "id": call.id,
            "name": call.name,
            "arguments": args,
        }
        lines.extend(
            [f"#### 调用 {index}: `{call.name}`", "", _json_fence(payload), ""]
        )
    return lines


def _text_fence(text: str) -> str:
    rendered = redact_secrets_text(text)
    fence = "```"
    while fence in rendered:
        fence += "`"
    return f"{fence}text\n{rendered}\n{fence}"


def _json_fence(value: Any) -> str:
    body = json.dumps(value, indent=2, ensure_ascii=False, default=str)
    body = redact_secrets_text(body)
    fence = "```"
    while fence in body:
        fence += "`"
    return f"{fence}json\n{body}\n{fence}"


def _try_parse_embedded_json(text: str) -> Any | None:
    stripped = text.strip()
    if not stripped or stripped[0] not in "{[":
        return None
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    if isinstance(parsed, (dict, list)):
        return parsed
    return None


def markdown_code_block(value: Any, info: str | None = None) -> str:
    if isinstance(value, str) and info in (None, "text"):
        return _text_fence(value)
    language = "json" if info == "json" else "text"
    if language == "json":
        return _json_fence(jsonable(value))
    if isinstance(value, str):
        return _text_fence(value)
    return _json_fence(jsonable(value))


def render_markdown_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(jsonable(value), indent=2, ensure_ascii=False, default=str)


def markdown_language(value: Any) -> str:
    return "text" if isinstance(value, str) else "json"


def format_optional(value: float | None) -> str:
    if value is None:
        return ""
    return str(round(float(value), 3))


def format_token_count(value: int | float | None) -> str:
    """Format token counts as plain integer, K, or M with three decimal places."""
    if value is None:
        return ""
    count = int(value)
    magnitude = abs(count)
    if magnitude < 1000:
        return str(count)
    if magnitude >= 1_000_000:
        return f"{count / 1_000_000:.3f} M"
    return f"{count / 1000:.3f} K"


def format_duration_seconds(value_ms: float | None) -> str:
    """Format millisecond durations as seconds with two decimal places."""
    if value_ms is None:
        return ""
    return f"{float(value_ms) / 1000:.2f} s"


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
