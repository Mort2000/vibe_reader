"""Markdown rendering for verify agent interaction audit reports."""

from __future__ import annotations

from typing import Any


def _fmt(value: Any, *, suffix: str = "") -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.1f}{suffix}"
    return f"{value}{suffix}"


def render_original_text_block(block: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    component = block.get("component", "original_text")
    para_range = block.get("paragraph_range") or [None, None]
    lines.append(
        f"**{component}** · P{para_range[0]}-P{para_range[1]} · "
        f"{block.get('paragraph_count', 0)} paragraphs · "
        f"{block.get('char_count', 0)} chars · "
        f"~{block.get('token_estimate', 0)} tokens"
    )
    lines.append(f"- hash: `{block.get('content_hash', '')}`")

    if block.get("text_mode") == "full":
        for para in block.get("paragraphs") or []:
            lines.append("")
            lines.append(f"#### P{para.get('paragraph_idx')}")
            lines.append("")
            lines.append(para.get("text") or "")
    else:
        first = block.get("first_paragraph") or {}
        last = block.get("last_paragraph") or {}
        if first:
            lines.extend(["", "#### First paragraph", "", first.get("text") or ""])
        if last and last.get("paragraph_idx") != first.get("paragraph_idx"):
            lines.extend(["", "#### Last paragraph", "", last.get("text") or ""])
    lines.append("")
    return lines


def render_prompt_segment(segment: dict[str, Any]) -> list[str]:
    if segment.get("type") == "original_text_block":
        return render_original_text_block(segment)
    text = segment.get("text") or ""
    if not text:
        return []
    return [text, ""]


def render_prompt_messages(messages: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = ["## Prompt", ""]
    for message in messages:
        role = message.get("role", "unknown")
        lines.append(f"### {role}")
        lines.append("")
        content = message.get("content") or []
        if isinstance(content, str):
            lines.extend([content, ""])
            continue
        for segment in content:
            lines.extend(render_prompt_segment(segment))
    return lines


def render_injected_context(context: dict[str, Any]) -> list[str]:
    lines = [
        "## Injected Context",
        "",
        f"- builder: {context.get('builder')} ({context.get('builder_version')})",
        f"- total_input_token_estimate: {context.get('total_input_token_estimate')}",
        f"- context_hash: `{context.get('context_hash', '')}`",
        "",
        "| Component | Source | Included | Tokens | Action |",
        "|---|---|---|---:|---|",
    ]
    for component in context.get("components") or []:
        lines.append(
            f"| {component.get('name')} | {component.get('source')} | "
            f"{component.get('included')} | {component.get('token_estimate', '')} | "
            f"{component.get('render_action') or component.get('drop_reason') or ''} |"
        )
    lines.append("")
    return lines


def render_usage_timing_table(packet: dict[str, Any]) -> list[str]:
    usage = packet.get("usage") or {}
    timing = packet.get("timing") or {}
    rounds = packet.get("llm_rounds") or []
    retry_count = timing.get("retry_count")
    if retry_count is None:
        retry_count = (
            max((r.get("timing") or {}).get("retry_index", 0) for r in rounds)
            if rounds
            else 0
        )

    lines = [
        "## Usage / Timing",
        "",
        "| Source | Input | Output | Cached Input | Rounds | Retries | TTFT | Total |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
        (
            f"| {usage.get('source', 'unknown')} | "
            f"{_fmt(usage.get('input_tokens'))} | "
            f"{_fmt(usage.get('output_tokens'))} | "
            f"{_fmt(usage.get('cached_input_tokens'))} | "
            f"{len(rounds)} | "
            f"{retry_count} | "
            f"{_fmt(timing.get('ttft_ms'), suffix=' ms')} | "
            f"{_fmt(timing.get('total_ms'), suffix=' ms')} |"
        ),
        "",
    ]
    return lines


def render_thinking_section(packet: dict[str, Any]) -> list[str]:
    lines = ["## Thinking / Reasoning", ""]
    chunks: list[str] = []
    for round_item in packet.get("llm_rounds") or []:
        thinking = (round_item.get("response") or {}).get("thinking") or {}
        if thinking.get("available"):
            text = thinking.get("text") or ""
            if text:
                chunks.append(text)
        elif thinking.get("reason"):
            chunks.append(f"_unavailable: {thinking.get('reason')}_")

    if chunks:
        lines.extend(chunks)
    else:
        lines.append("_No thinking content captured._")
    lines.append("")
    return lines


def render_tool_calls(packet: dict[str, Any]) -> list[str]:
    lines = ["## Tool Calls", ""]
    events = packet.get("tool_events") or []
    if not events:
        lines.extend(["_No tool calls recorded._", ""])
        return lines

    for event in events:
        lines.append(f"### `{event.get('tool_name')}` · {event.get('tool_call_id')}")
        lines.append("")
        args = (
            (event.get("arguments") or {}).get("payload")
            or event.get("arguments")
            or {}
        )
        lines.append(f"- arguments: `{args}`")
        lines.append(
            f"- schema: {(event.get('schema_validation') or {}).get('status')}"
        )
        business = event.get("business_validation") or {}
        lines.append(
            f"- business: {business.get('status')} {business.get('reason') or ''}".rstrip()
        )
        lines.append(f"- persistence: {(event.get('persistence') or {}).get('status')}")
        lines.append("")
    return lines


def render_final_result(packet: dict[str, Any]) -> list[str]:
    final_result = packet.get("final_result") or {}
    lines = ["## Final Result", "", f"- status: {final_result.get('status')}"]
    if final_result.get("no_call"):
        lines.append("- no_call: true")
    for comment in final_result.get("comments_created") or []:
        lines.append(
            f"- created p{comment.get('paragraph_idx')}: "
            f"{comment.get('comment_type')} · {comment.get('text')}"
        )
    for discarded in final_result.get("comments_discarded") or []:
        lines.append(
            f"- discarded p{discarded.get('paragraph_idx')}: {discarded.get('reason')}"
        )
    sample_refs = final_result.get("sample_refs") or []
    if sample_refs:
        lines.append("- sample_refs:")
        for ref in sample_refs:
            lines.append(f"  - {ref}")
    lines.append("")
    return lines


def render_agent_audit_markdown(packet: dict[str, Any]) -> str:
    invocation_id = packet.get("invocation_id", "unknown")
    agent = packet.get("agent", "Agent")
    window = packet.get("window") or {}

    header = [
        f"# {agent} · {invocation_id}",
        "",
        "| Field | Value |",
        "|---|---|",
        f"| Run | {packet.get('run_id')} |",
        f"| Scenario | {packet.get('scenario_id')} |",
        f"| Step | {packet.get('step_id')} |",
        f"| Model | {packet.get('model')} |",
        f"| LLM Mode | {packet.get('llm_mode')} |",
        f"| Trace | {packet.get('trace_id')} |",
        f"| Prompt Version | {packet.get('prompt_version')} |",
        "",
    ]

    reading = [
        "## Reading Position",
        "",
        f"- Book: {((packet.get('book') or {}).get('title'))} (id={(packet.get('book') or {}).get('id')})",
        f"- Chapter: {packet.get('chapter_idx')}",
        (
            f"- Window: P{window.get('start_paragraph_idx')}-"
            f"P{window.get('end_paragraph_idx')} (id={window.get('id')}, seq={window.get('seq')})"
        ),
        (
            f"- Focus: P{window.get('focus_start_paragraph_idx')}-"
            f"P{window.get('focus_end_paragraph_idx')}"
        ),
        "",
    ]

    links = [
        "## Related Artifacts",
        "",
        f"- interaction JSON: `audit/agent_interactions/{invocation_id}.json`",
        f"- prompt markdown: `audit/prompts/{invocation_id}.prompt.md`",
        f"- context sidecar: `audit/contexts/context_{packet.get('context_hash', '').replace('sha256:', '')[:12]}.json`",
        "",
    ]

    parts = header + render_usage_timing_table(packet) + reading
    parts.extend(render_prompt_messages(packet.get("prompt_messages") or []))
    parts.extend(render_injected_context(packet.get("injected_context") or {}))
    parts.extend(render_thinking_section(packet))
    parts.extend(render_tool_calls(packet))
    parts.extend(render_final_result(packet))
    parts.extend(links)
    return "\n".join(parts)


def render_prompt_markdown(packet: dict[str, Any]) -> str:
    lines = [
        f"# Prompt · {packet.get('invocation_id')}",
        "",
        f"- trace: {packet.get('trace_id')}",
        f"- model: {packet.get('model')}",
        "",
    ]
    lines.extend(render_prompt_messages(packet.get("prompt_messages") or []))
    return "\n".join(lines)
