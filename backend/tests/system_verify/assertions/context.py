"""Assertions for 128K tiered context and L3 chapter compaction (V-09 / A3)."""

from __future__ import annotations

import json
from typing import Any

from ..core.config import ContextConfig, VerifyConfig
from ..core.scenario import StepAssertionError, assert_that


def _component_by_name(
    injected_context: dict[str, Any], *names: str
) -> dict[str, Any] | None:
    for component in injected_context.get("components") or []:
        name = str(component.get("name") or "")
        if name in names:
            return component
    return None


def extract_l2_chunks(injected_context: dict[str, Any]) -> list[dict[str, Any]]:
    """Return active L2 chunk descriptors from an injected context sidecar."""
    for key in ("live_l2_chunks", "l2_chunks", "active_l2_chunks"):
        chunks = injected_context.get(key)
        if isinstance(chunks, list):
            return [c for c in chunks if isinstance(c, dict)]

    for name in (
        "live_l2_original_text",
        "live_original_text",
        "l2_original_text",
        "original_text_chunks",
    ):
        component = _component_by_name(injected_context, name)
        if not component:
            continue
        content = component.get("content") or {}
        if isinstance(content, dict):
            chunks = (
                content.get("chunks")
                or content.get("items")
                or content.get("live_l2_chunks")
            )
            if isinstance(chunks, list):
                return [c for c in chunks if isinstance(c, dict)]
    return []


def l2_chunk_signature(chunks: list[dict[str, Any]]) -> list[tuple[Any, Any, Any]]:
    """Stable tuple signature for chunk boundary comparison."""
    signature: list[tuple[Any, Any, Any]] = []
    for chunk in chunks:
        signature.append(
            (
                chunk.get("chunk_id") or chunk.get("id"),
                chunk.get("start_paragraph_idx"),
                chunk.get("end_paragraph_idx"),
            )
        )
    return sorted(signature, key=lambda item: (item[1] or 0, item[2] or 0))


def _parse_nested_compaction_payload(value: Any) -> dict[str, Any] | None:
    """Normalize compaction tool payload, including double-encoded raw JSON."""
    if not isinstance(value, dict):
        return None
    if str(value.get("summary") or "").strip():
        return value

    nested = value.get("payload")
    if isinstance(nested, dict):
        if str(nested.get("summary") or "").strip():
            return nested
        raw = nested.get("raw")
        if isinstance(raw, str) and raw.strip():
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                return None
            if isinstance(parsed, dict):
                inner = parsed.get("payload")
                if isinstance(inner, dict):
                    return inner if str(inner.get("summary") or "").strip() else None
                return parsed if str(parsed.get("summary") or "").strip() else None
    return None


def _summary_from_tool_events(tool_events: list[Any]) -> dict[str, Any] | None:
    for event in tool_events:
        if not isinstance(event, dict):
            continue
        arguments = event.get("arguments") or {}
        for candidate in (arguments.get("payload"), arguments):
            parsed = _parse_nested_compaction_payload(candidate)
            if parsed:
                return parsed
    return None


def _merge_summary_metadata(
    base: dict[str, Any], extra: dict[str, Any] | None
) -> dict[str, Any]:
    if not extra:
        return base
    merged = dict(extra)
    for key, value in base.items():
        if key not in merged or merged.get(key) in (None, "", []):
            merged[key] = value
    if base.get("summary"):
        merged["summary"] = base["summary"]
    if base.get("anchor_excerpts") is not None:
        merged["anchor_excerpts"] = base["anchor_excerpts"]
    if base.get("chapter_title"):
        merged["chapter_title"] = base["chapter_title"]
    if base.get("covered_start_paragraph_idx") is not None:
        merged["covered_start_paragraph_idx"] = base["covered_start_paragraph_idx"]
    elif base.get("covered_start") is not None:
        merged["covered_start_paragraph_idx"] = base["covered_start"]
    if base.get("covered_end_paragraph_idx") is not None:
        merged["covered_end_paragraph_idx"] = base["covered_end_paragraph_idx"]
    elif base.get("covered_end") is not None:
        merged["covered_end_paragraph_idx"] = base["covered_end"]
    return merged


def _summary_from_final_result(final_result: dict[str, Any]) -> dict[str, Any] | None:
    for key in (
        "chapter_compressed_summary",
        "summary_payload",
        "compaction_result",
    ):
        value = final_result.get(key)
        if isinstance(value, dict):
            return value
    if final_result.get("summary") and (
        final_result.get("anchor_excerpts") is not None
        or final_result.get("covered_end_paragraph_idx") is not None
    ):
        return final_result
    return None


def _summary_from_injected_context(payload: dict[str, Any]) -> dict[str, Any] | None:
    injected = payload.get("injected_context") or {}
    component = _component_by_name(
        injected,
        "chapter_compressed_summary",
        "chapter_summary",
        "rolling_summary",
    )
    if not component:
        return None
    content = component.get("content")
    if not isinstance(content, dict):
        return None
    if str(content.get("summary") or "").strip():
        return content
    tool_summary = _summary_from_tool_events(payload.get("tool_events") or [])
    if tool_summary:
        return _merge_summary_metadata(tool_summary, content)
    return None


def _summary_from_next_summary(payload: dict[str, Any]) -> dict[str, Any] | None:
    next_summary = payload.get("next_summary")
    if not isinstance(next_summary, dict) or next_summary.get("id") is None:
        return None
    if str(next_summary.get("summary") or "").strip():
        return next_summary
    tool_summary = _summary_from_tool_events(payload.get("tool_events") or [])
    if tool_summary:
        return _merge_summary_metadata(tool_summary, next_summary)
    return next_summary


def extract_chapter_summary(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Extract ChapterCompressedSummary-like payload from agent interaction data."""
    for key in ("chapter_compressed_summary", "chapter_summary", "summary_payload"):
        value = payload.get(key)
        if isinstance(value, dict):
            return value

    final_result = payload.get("final_result") or {}
    if isinstance(final_result, dict):
        summary = _summary_from_final_result(final_result)
        if summary:
            return summary

    tool_summary = _summary_from_tool_events(payload.get("tool_events") or [])
    if tool_summary:
        return _merge_summary_metadata(tool_summary, payload.get("next_summary"))

    summary = _summary_from_injected_context(payload)
    if summary:
        return summary

    return _summary_from_next_summary(payload)


def _compaction_run_has_evidence(run: dict[str, Any]) -> bool:
    interaction = run.get("interaction") or run
    usage = interaction.get("usage") or {}
    has_llm_work = int(
        run.get("input_tokens")
        or interaction.get("input_tokens")
        or usage.get("input_tokens")
        or 0
    ) > 0
    has_summary = bool(
        interaction.get("summary_id")
        or interaction.get("next_summary")
        or extract_chapter_summary(interaction)
    )
    return has_llm_work or has_summary


def assert_token_budget(
    injected_context: dict[str, Any],
    config: VerifyConfig,
) -> None:
    estimate = injected_context.get("total_input_token_estimate")
    if estimate is None:
        return

    cap = config.context.emergency_input_cap_tokens
    assert_that.lte(
        int(estimate),
        cap,
        label="context.input_token_estimate_within_emergency_cap",
    )


def assert_s4_context_evidence(
    *,
    config: VerifyConfig,
    injected_contexts: list[dict[str, Any]],
    compaction_jobs: list[dict[str, Any]],
    compaction_runs: list[dict[str, Any]],
    completed_compactions: list[dict[str, Any]] | None = None,
) -> None:
    """Validate S4 token budgets, compaction observation, and reclaimed L2 chunks."""
    done_compaction_jobs = [
        job
        for job in compaction_jobs
        if job.get("job_type") == "compact_context" and job.get("status") == "done"
    ]
    compaction_observed = len(compaction_runs) > 0 or len(done_compaction_jobs) > 0
    assert_that.is_true(
        compaction_observed,
        "compaction_observed: expected compaction job, agent run, or SSE signal",
    )

    for injected in injected_contexts:
        assert_token_budget(injected, config)

    if compaction_runs:
        interaction = compaction_runs[-1].get("interaction") or compaction_runs[-1]
        assert_token_budget(interaction.get("injected_context") or {}, config)

    assert_reclaimed_l2_chunk_present(
        injected_contexts=injected_contexts,
        compaction_jobs=compaction_jobs,
        compaction_runs=compaction_runs,
        completed_compactions=completed_compactions,
    )


def assert_l2_chunk_boundaries_stable(
    before: list[dict[str, Any]],
    after: list[dict[str, Any]],
) -> None:
    if not before or not after:
        return

    before_active = [c for c in before if c.get("status", "active") != "reclaimed"]
    after_active = [c for c in after if c.get("status", "active") != "reclaimed"]
    if not before_active or not after_active:
        return

    after_by_id = {
        chunk.get("chunk_id") or chunk.get("id"): chunk for chunk in after_active
    }
    for chunk in before_active:
        chunk_id = chunk.get("chunk_id") or chunk.get("id")
        if chunk_id is None:
            continue
        matched = after_by_id.get(chunk_id)
        if matched is None:
            continue
        assert_that.equal(
            matched.get("start_paragraph_idx"),
            chunk.get("start_paragraph_idx"),
            label=f"l2_chunk_{chunk_id}_start_stable",
        )
        assert_that.equal(
            matched.get("end_paragraph_idx"),
            chunk.get("end_paragraph_idx"),
            label=f"l2_chunk_{chunk_id}_end_stable",
        )


SUMMARY_COMPONENT_NAMES = (
    "chapter_compressed_summary",
    "chapter_summary",
    "rolling_summary",
)


def find_chapter_summary_component(
    injected_context: dict[str, Any],
) -> dict[str, Any] | None:
    return _component_by_name(injected_context, *SUMMARY_COMPONENT_NAMES)


def assert_comment_activity_observable(
    *,
    comment_runs: list[dict[str, Any]],
    trace: Any,
) -> None:
    """Assert comment windows or ParagraphCommentAgent runs were observed."""
    comment_observed = (
        len(comment_runs) > 0
        or getattr(trace, "comment_created_count", 0) > 0
        or getattr(trace, "window_done_count", 0) > 0
    )
    assert_that.is_true(
        comment_observed,
        "Expected comment agent activity during long-context reading",
    )


def assert_compaction_failure_does_not_block_comments(
    *,
    comment_runs: list[dict[str, Any]],
    trace: Any,
    failed_job: dict[str, Any] | None,
) -> None:
    """Explicit S4 check: comment path stays observable even if compaction failed."""
    assert_comment_activity_observable(comment_runs=comment_runs, trace=trace)
    if failed_job:
        assert_that.gte(
            len(comment_runs) + getattr(trace, "window_done_count", 0),
            1,
            label="comments_observable_despite_compaction_failure",
        )


def assert_chapter_summary_in_subsequent_context(
    injected_context: dict[str, Any],
    *,
    compaction_run: dict[str, Any] | None = None,
) -> None:
    """Assert a post-compaction context build includes ChapterCompressedSummary."""
    component = find_chapter_summary_component(injected_context)
    if not component:
        component_names = [
            str(c.get("name") or "") for c in (injected_context.get("components") or [])
        ]
        raise StepAssertionError(
            assertion="chapter_summary_in_subsequent_context",
            message="Post-compaction injected context must include chapter summary component",
            expected=list(SUMMARY_COMPONENT_NAMES),
            actual={"component_names": component_names},
        )

    if component.get("included") is False:
        raise StepAssertionError(
            assertion="chapter_summary_in_subsequent_context",
            message="Chapter summary component must be included in subsequent context",
            expected={"included": True},
            actual={"included": False, "component": component.get("name")},
        )

    content = component.get("content")
    if isinstance(content, dict):
        has_summary_text = bool(str(content.get("summary") or "").strip())
        has_summary_id = content.get("summary_id") or content.get("id")
        if not has_summary_text and not has_summary_id:
            raise StepAssertionError(
                assertion="chapter_summary_in_subsequent_context",
                message="Chapter summary component content must include summary text or id",
                actual=content,
            )
    elif not component.get("token_estimate"):
        raise StepAssertionError(
            assertion="chapter_summary_in_subsequent_context",
            message="Chapter summary component must carry content or token estimate",
            actual=component,
        )

    if compaction_run:
        compaction_summary = extract_chapter_summary(
            compaction_run.get("interaction") or compaction_run
        )
        if compaction_summary and isinstance(content, dict):
            expected_id = compaction_summary.get("id") or compaction_summary.get(
                "summary_id"
            )
            actual_id = content.get("summary_id") or content.get("id")
            if expected_id and actual_id:
                assert_that.equal(
                    actual_id,
                    expected_id,
                    label="chapter_summary_id_matches_compaction",
                )


def _agent_run_job_id(run: dict[str, Any]) -> int:
    interaction = run.get("interaction") or run
    job_id = run.get("job_id") or interaction.get("job_id")
    return int(job_id) if job_id is not None else 0


def select_post_compaction_comment_runs(
    comment_runs: list[dict[str, Any]],
    *,
    compaction_job_id: int | None,
    compaction_trace_ids: set[str],
    compaction_chapter_idx: int | None = None,
) -> list[dict[str, Any]]:
    """Return comment agent runs that likely occurred after compaction."""
    if compaction_job_id:
        post = [
            run for run in comment_runs if _agent_run_job_id(run) > compaction_job_id
        ]
        if post:
            if compaction_chapter_idx is not None:
                same_chapter = [
                    run
                    for run in post
                    if int(run.get("chapter_idx") or 0) == compaction_chapter_idx
                ]
                if same_chapter:
                    return same_chapter
            return post

    post = [
        run
        for run in comment_runs
        if str(run.get("trace_id") or "") not in compaction_trace_ids
    ]
    if compaction_chapter_idx is not None and post:
        same_chapter = [
            run
            for run in post
            if int(run.get("chapter_idx") or 0) == compaction_chapter_idx
        ]
        if same_chapter:
            return same_chapter
    return post or comment_runs[-1:]


def assert_chapter_summary_structure(summary: dict[str, Any]) -> None:
    assert_that.is_true(
        bool(str(summary.get("summary") or "").strip()),
        "ChapterCompressedSummary.summary must be non-empty",
    )
    assert_that.is_not_none(
        summary.get("anchor_excerpts"),
        "ChapterCompressedSummary.anchor_excerpts must be present",
    )
    for forbidden in ("comment_digest", "chat_digest"):
        assert_that.not_contains(
            summary,
            forbidden,
            label=f"chapter_summary_must_not_contain_{forbidden}",
        )


def _extract_compaction_source_scale(
    compaction_run: dict[str, Any],
) -> tuple[dict[str, Any], int | None, int | None]:
    interaction = compaction_run.get("interaction") or compaction_run
    source = (
        interaction.get("compaction_source")
        or interaction.get("source_chunk")
        or (interaction.get("final_result") or {}).get("source_chunk")
        or {}
    )
    if not isinstance(source, dict):
        source = {}

    source_tokens = (
        source.get("token_estimate")
        or source.get("source_chunk_tokens")
        or interaction.get("source_chunk_tokens")
        or compaction_run.get("source_chunk_tokens")
        or interaction.get("input_tokens")
        or compaction_run.get("input_tokens")
    )
    paragraph_count = (
        source.get("paragraph_count")
        or source.get("source_paragraph_count")
        or interaction.get("source_paragraph_count")
        or compaction_run.get("source_paragraph_count")
    )
    if source_tokens is None and paragraph_count is None:
        injected = interaction.get("injected_context") or {}
        for component in injected.get("components") or []:
            content = component.get("content") or {}
            if not isinstance(content, dict):
                continue
            if content.get("token_estimate") is not None and source_tokens is None:
                source_tokens = content.get("token_estimate")
            start = content.get("start_paragraph_idx")
            end = content.get("end_paragraph_idx")
            if start is not None and end is not None and paragraph_count is None:
                paragraph_count = int(end) - int(start) + 1

    return interaction, source_tokens, paragraph_count


def assert_compaction_source_scale(
    compaction_run: dict[str, Any],
    *,
    min_source_tokens: int,
    min_source_paragraphs: int,
) -> None:
    interaction, source_tokens, paragraph_count = _extract_compaction_source_scale(
        compaction_run
    )

    if source_tokens is not None:
        assert_that.gte(
            int(source_tokens),
            min_source_tokens,
            label="compaction_source_tokens",
        )
    if paragraph_count is not None:
        assert_that.gte(
            int(paragraph_count),
            min_source_paragraphs,
            label="compaction_source_paragraphs",
        )

    missing: list[str] = []
    if source_tokens is None:
        missing.append("source_tokens")
    if paragraph_count is None:
        missing.append("paragraph_count")
    if missing:
        if (
            missing == ["paragraph_count"]
            and source_tokens is not None
            and int(source_tokens) >= min_source_tokens
        ):
            return
        raise StepAssertionError(
            assertion="compaction_source_scale_unavailable",
            message=(
                "Compaction source scale data unavailable: missing "
                + ", ".join(missing)
            ),
            expected={
                "min_source_tokens": min_source_tokens,
                "min_source_paragraphs": min_source_paragraphs,
            },
            actual={
                "source_tokens": source_tokens,
                "paragraph_count": paragraph_count,
                "compaction_run_keys": sorted(interaction.keys()),
            },
        )


def _is_compaction_agent_run(run: dict[str, Any]) -> bool:
    return (
        str(run.get("agent") or run.get("agent_name") or "") == "ContextCompactionAgent"
        or (run.get("interaction") or {}).get("agent") == "ContextCompactionAgent"
    )


def _is_comment_agent_run(run: dict[str, Any]) -> bool:
    return (
        str(run.get("agent") or run.get("agent_name") or "") == "ParagraphCommentAgent"
        or (run.get("interaction") or {}).get("agent") == "ParagraphCommentAgent"
    )


def _is_chat_agent_run(run: dict[str, Any]) -> bool:
    return (
        str(run.get("agent") or run.get("agent_name") or "") == "ReadingChatAgent"
        or (run.get("interaction") or {}).get("agent") == "ReadingChatAgent"
    )


def find_compaction_agent_runs(
    agent_runs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [run for run in agent_runs if _is_compaction_agent_run(run)]


def find_comment_agent_runs(agent_runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [run for run in agent_runs if _is_comment_agent_run(run)]


def find_chat_agent_runs(agent_runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [run for run in agent_runs if _is_chat_agent_run(run)]


def has_reclaimed_l2_chunk(chunks: list[dict[str, Any]]) -> bool:
    return any(chunk.get("status") == "reclaimed" for chunk in chunks)


def assert_reclaimed_l2_chunk_present(
    *,
    injected_contexts: list[dict[str, Any]],
    compaction_jobs: list[dict[str, Any]] | None = None,
    compaction_runs: list[dict[str, Any]] | None = None,
    completed_compactions: list[dict[str, Any]] | None = None,
) -> None:
    """Assert at least one L2 chunk was reclaimed after compaction."""
    all_chunks: list[dict[str, Any]] = []
    for injected in injected_contexts:
        all_chunks.extend(extract_l2_chunks(injected))
    if has_reclaimed_l2_chunk(all_chunks):
        return

    for job in compaction_jobs or []:
        if job.get("reclaimed_chunk_id") or job.get("reclaimed_chunk_ids"):
            return
        metadata = job.get("metadata") or {}
        if metadata.get("reclaimed_chunk_id") or metadata.get("reclaimed_chunk_ids"):
            return

    for run in compaction_runs or []:
        if _is_compaction_agent_run(run):
            return
        interaction = run.get("interaction") or run
        final_result = interaction.get("final_result") or {}
        if final_result.get("reclaimed_chunk_id") or final_result.get(
            "reclaimed_chunk_ids"
        ):
            return
        source = (
            interaction.get("compaction_source")
            or interaction.get("source_chunk")
            or final_result.get("source_chunk")
            or {}
        )
        if isinstance(source, dict) and source.get("status") == "reclaimed":
            return

    for event in completed_compactions or []:
        if event.get("reclaimed_chunk_id") or event.get("reclaimed_chunk_ids"):
            return

    raise StepAssertionError(
        assertion="reclaimed_l2_chunk_present",
        message="Expected at least one reclaimed L2 chunk after compaction",
        expected="reclaimed L2 chunk or compaction metadata",
        actual={
            "chunk_statuses": [chunk.get("status") for chunk in all_chunks[:10]],
            "compaction_jobs": len(compaction_jobs or []),
            "compaction_runs": len(compaction_runs or []),
        },
    )


def assert_compaction_completed(
    *,
    compaction_jobs: list[dict[str, Any]],
    compaction_runs: list[dict[str, Any]],
    require_real: bool = False,
    require_agent_run: bool = False,
) -> None:
    done_jobs = [job for job in compaction_jobs if job.get("status") == "done"]
    skipped_jobs = [job for job in compaction_jobs if job.get("status") == "skipped"]

    if require_agent_run:
        if not compaction_runs:
            raise StepAssertionError(
                assertion="compaction_agent_run",
                message=(
                    "Expected ContextCompactionAgent run with reclaimed chunk; "
                    "instant no-op jobs do not count"
                ),
                expected="compaction agent run",
                actual={
                    "done_jobs": len(done_jobs),
                    "skipped_jobs": len(skipped_jobs),
                    "agent_runs": len(compaction_runs),
                },
            )
        interaction = compaction_runs[-1].get("interaction") or compaction_runs[-1]
        if not _compaction_run_has_evidence(compaction_runs[-1]):
            raise StepAssertionError(
                assertion="compaction_agent_run",
                message="Compaction agent run missing summary or token usage",
                expected="summary_id or input_tokens > 0",
                actual={
                    "input_tokens": compaction_runs[-1].get("input_tokens"),
                    "interaction_input_tokens": interaction.get("input_tokens"),
                    "usage": interaction.get("usage"),
                    "summary_id": interaction.get("summary_id"),
                    "next_summary": interaction.get("next_summary"),
                },
            )
    elif not done_jobs and not compaction_runs:
        raise StepAssertionError(
            assertion="compaction_completed",
            message="Expected at least one completed compact_context job or compaction agent run",
            expected="compaction",
            actual={
                "jobs": compaction_jobs,
                "agent_runs": len(compaction_runs),
            },
        )

    if compaction_runs:
        interaction = compaction_runs[-1].get("interaction") or compaction_runs[-1]
        summary = extract_chapter_summary(interaction)
        if summary and str(summary.get("summary") or "").strip():
            assert_chapter_summary_structure(summary)

    if require_real:
        for run in compaction_runs:
            interaction = run.get("interaction") or run
            llm_mode = interaction.get("llm_mode") or run.get("llm_mode")
            if llm_mode and llm_mode != "real":
                raise StepAssertionError(
                    assertion="real_compaction_agent_run",
                    message="Expected ContextCompactionAgent audit with llm_mode=real",
                    actual={"llm_mode": llm_mode},
                )


def record_context_metrics_from_verify(
    metrics,
    verify_metrics: dict[str, Any],
    *,
    scenario_id: str,
    step_id: str,
) -> None:
    mapping = {
        "context.build_ms": (verify_metrics.get("latency") or {}).get(
            "context.build_ms"
        ),
        "context.input_token_estimate": verify_metrics.get("context", {}).get(
            "input_token_estimate"
        ),
        "context.live_l2_tokens": verify_metrics.get("context", {}).get(
            "live_l2_tokens"
        ),
        "context.chapter_summary_tokens": verify_metrics.get("context", {}).get(
            "chapter_summary_tokens"
        ),
        "context.l2_chunk_count": verify_metrics.get("context", {}).get(
            "l2_chunk_count"
        ),
        "context.compaction_epoch": verify_metrics.get("context", {}).get(
            "compaction_epoch"
        ),
        "compaction.e2e_latency_ms": (verify_metrics.get("latency") or {}).get(
            "compaction.e2e_latency_ms"
        ),
    }
    tokens = verify_metrics.get("tokens") or {}
    compaction_tokens = tokens.get("ContextCompactionAgent") or {}
    if compaction_tokens:
        mapping["compaction.tokens.input"] = compaction_tokens.get("input")
        mapping["compaction.tokens.output"] = compaction_tokens.get("output")

    for metric_name, payload in mapping.items():
        if payload is None:
            continue
        if isinstance(payload, dict):
            for stat in ("p50", "max", "count"):
                value = payload.get(stat)
                if value is not None:
                    metrics.record(
                        f"{metric_name}.{stat}" if stat != "count" else metric_name,
                        float(value),
                        unit="ms" if metric_name.endswith("_ms") else "count",
                        scenario_id=scenario_id,
                        step_id=step_id,
                    )
        else:
            metrics.record(
                metric_name,
                float(payload),
                unit="count",
                scenario_id=scenario_id,
                step_id=step_id,
            )


def context_config_snapshot(config: ContextConfig) -> dict[str, int]:
    return {
        "attention_target_input_tokens": config.attention_target_input_tokens,
        "emergency_input_cap_tokens": config.emergency_input_cap_tokens,
        "target_l2_chunk_tokens": config.target_l2_chunk_tokens,
    }
