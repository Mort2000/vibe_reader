"""V-16: Report generator — summary, metrics, and failure reports."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any


def generate_reports(run_dir: Path) -> dict[str, Path]:
    """Generate reports/summary.md, metrics.json, and failures.md."""
    run_dir = Path(run_dir)
    manifest = _read_json(run_dir / "run_manifest.json") or {}
    scenarios = _read_ndjson(run_dir / "scenario_results.ndjson")
    metrics = _read_ndjson(run_dir / "metrics.ndjson")

    reports_dir = run_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    metrics_json = _build_metrics_json(metrics, manifest)
    (reports_dir / "metrics.json").write_text(
        json.dumps(metrics_json, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    failures_md = _build_failures_md(scenarios, run_dir)
    (reports_dir / "failures.md").write_text(failures_md, encoding="utf-8")

    summary_md = _build_summary_md(manifest, scenarios, metrics_json, run_dir, metrics)
    (reports_dir / "summary.md").write_text(summary_md, encoding="utf-8")

    return {
        "summary": reports_dir / "summary.md",
        "metrics": reports_dir / "metrics.json",
        "failures": reports_dir / "failures.md",
    }


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _read_ndjson(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def _build_metrics_json(
    metrics: list[dict[str, Any]], manifest: dict[str, Any]
) -> dict[str, Any]:
    grouped: dict[str, list[float]] = {}
    for row in metrics:
        name = row.get("metric", "")
        value = row.get("value")
        if name and isinstance(value, (int, float)):
            grouped.setdefault(name, []).append(float(value))

    latency_rows = []
    for name, values in sorted(grouped.items()):
        if not any(
            token in name for token in ("latency", "duration", "ttft", "wait", "run_ms")
        ):
            continue
        values_sorted = sorted(values)
        latency_rows.append(
            {
                "metric": name,
                "count": len(values_sorted),
                "p50": _percentile(values_sorted, 0.5),
                "p90": _percentile(values_sorted, 0.9),
                "p95": _percentile(values_sorted, 0.95),
                "max": values_sorted[-1],
            }
        )

    token_metrics = {
        row.get("metric"): row.get("value")
        for row in metrics
        if str(row.get("metric", "")).startswith(
            ("tokens", "llm.ping.tokens", "comment.")
        )
    }

    return {
        "run_id": manifest.get("run_id"),
        "llm_mode": manifest.get("llm_mode"),
        "usage_source": manifest.get("usage_source"),
        "latency": latency_rows,
        "tokens_and_comment_metrics": token_metrics,
        "comment_density": {
            row.get("metric"): row.get("value")
            for row in metrics
            if str(row.get("metric", "")).startswith("comment.density.")
        },
        "real_llm": {
            "call_count": manifest.get("real_llm_call_count", 0),
            "input_tokens": manifest.get("real_llm_input_tokens", 0),
            "output_tokens": manifest.get("real_llm_output_tokens", 0),
            "phase_coverage": manifest.get("real_llm_phase_coverage", {}),
        },
    }


def _build_failures_md(scenarios: list[dict[str, Any]], run_dir: Path) -> str:
    lines = ["# Verification Failures", ""]
    failures = [s for s in scenarios if s.get("status") != "passed"]
    if not failures:
        lines.append("No scenario failures recorded.")
        return "\n".join(lines) + "\n"

    for scenario in failures:
        lines.append(f"## {scenario.get('scenario_id')}")
        lines.append("")
        lines.append(f"- status: {scenario.get('status')}")
        lines.append(f"- summary: {scenario.get('failure_summary')}")
        lines.append("")
        for step in scenario.get("steps") or []:
            if step.get("status") == "passed":
                continue
            lines.append(
                f"- step `{step.get('step_id')}`: {step.get('description')} "
                f"({step.get('status')}) trace={step.get('trace_id') or 'n/a'}"
            )
            for err in step.get("errors") or []:
                lines.append(f"  - {err.get('assertion')}: {err.get('message')}")
        lines.append("")

    trace_index = run_dir / "traces" / "trace_index.ndjson"
    if trace_index.exists():
        lines.extend(["## Trace Index", "", f"See `{trace_index}`", ""])
    return "\n".join(lines)


def _build_summary_md(
    manifest: dict[str, Any],
    scenarios: list[dict[str, Any]],
    metrics_json: dict[str, Any],
    run_dir: Path,
    raw_metrics: list[dict[str, Any]],
) -> str:
    passed = all(s.get("status") == "passed" for s in scenarios) if scenarios else True
    audit_comments = _count_ndjson_lines(run_dir / "audit" / "comments.ndjson")
    compaction_samples = _count_ndjson_lines(
        run_dir / "audit" / "compaction_summaries.ndjson"
    )
    real_comments = 0
    if audit_comments:
        for row in _read_ndjson(run_dir / "audit" / "comments.ndjson"):
            if row.get("llm_mode") == "real":
                real_comments += 1

    lines = [
        "# Vibe Reader Verify Summary",
        "",
        f"run_id: {manifest.get('run_id')}",
        f"git_commit: {manifest.get('git_commit')}",
        f"suite: {manifest.get('suite')}",
        f"llm_mode: {manifest.get('llm_mode')}",
        f"stub_profile: {manifest.get('stub_profile')}",
        f"real_llm_calls: {manifest.get('real_llm_call_count', 0)}",
        f"model: {manifest.get('model')}",
        f"corpus: {', '.join(manifest.get('corpus_sha256') or [])}",
        f"started_at: {manifest.get('started_at')}",
        f"ended_at: {manifest.get('ended_at')}",
        "",
        "## Result",
        "",
        "pass" if passed else "fail",
        "",
        "## Functional Checks",
        "",
        f"- import: {_scenario_status(scenarios, 'S1')}",
        f"- progress: {_scenario_status(scenarios, 'S1')}",
        f"- comment: {_scenario_status(scenarios, 'S2')}",
        f"- scroll_jump: {_scenario_status(scenarios, 'S3')}",
        f"- long_context: {_scenario_status(scenarios, 'S4')}",
        f"- compaction: {_scenario_status(scenarios, 'S4')}",
        f"- real_happy_path: {_scenario_status(scenarios, 'R1')}",
        "",
        "## Latency",
        "",
        "| metric | count | p50 | p90 | p95 | max |",
        "|---|---:|---:|---:|---:|---:|",
    ]

    for row in metrics_json.get("latency", []):
        lines.append(
            f"| {row['metric']} | {row['count']} | {row['p50']:.1f} | "
            f"{row['p90']:.1f} | {row['p95']:.1f} | {row['max']:.1f} |"
        )

    token_rows = _build_tokens_table(raw_metrics, manifest)
    lines.extend(
        [
            "",
            "## Tokens",
            "",
            "| agent | requests | input | output | total | max_input |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    if token_rows:
        for row in token_rows:
            lines.append(
                f"| {row['agent']} | {row['requests']} | {row['input']} | "
                f"{row['output']} | {row['total']} | {row['max_input']} |"
            )
    else:
        lines.append("| _no token metrics recorded_ | 0 | 0 | 0 | 0 | 0 |")

    phase = manifest.get("real_llm_phase_coverage") or {}
    if phase:
        lines.extend(
            [
                "",
                "## Real LLM Phase Coverage",
                "",
                f"- A2_comments: {phase.get('A2_comments', False)}",
                f"- A3_compaction: {phase.get('A3_compaction', False)}",
                f"- A4_full_flow: {phase.get('A4_full_flow', False)}",
            ]
        )

    density = metrics_json.get("comment_density") or {}
    lines.extend(
        [
            "",
            f"usage_source: {manifest.get('usage_source')}",
            "",
            "## Comment Density",
            "",
            f"- actual: {density.get('comment.density.actual')}",
            f"- soft_min: {density.get('comment.density.soft_min')}",
            f"- stat_start: {density.get('comment.density.stat_start_paragraph_idx')}",
            f"- stat_end: {density.get('comment.density.stat_end_paragraph_idx')}",
            "",
            "## Audit Samples",
            "",
            f"- comments: {audit_comments}",
            f"- compaction_summaries: {compaction_samples}",
            f"- real_comments: {real_comments}",
            f"- window_status: {_count_ndjson_lines(run_dir / 'audit' / 'window_status.ndjson')}",
            f"- agent_invocations: {_count_ndjson_lines(run_dir / 'audit' / 'agent_invocations.ndjson')}",
            f"- agent_reports: {_count_markdown_files(run_dir / 'audit' / 'agent_reports')}",
            f"- audit_safety: {_count_ndjson_lines(run_dir / 'audit' / 'audit_safety_report.ndjson')}",
            "",
            "## Failures",
            "",
            "See reports/failures.md",
            "",
        ]
    )
    return "\n".join(lines)


def _agent_from_metric(metric: str) -> str:
    if metric.startswith("llm.ping."):
        return "llm_ping"
    if metric.startswith("comment.") or metric.startswith("tokens_per_comment"):
        return "paragraph_comment"
    if metric.startswith("compaction."):
        return "context_compaction"
    if metric.startswith("chat."):
        return "chat"
    return "unknown"


def _build_tokens_table(
    metrics: list[dict[str, Any]], manifest: dict[str, Any]
) -> list[dict[str, int | str]]:
    by_agent: dict[str, dict[str, int]] = defaultdict(
        lambda: {
            "requests": 0,
            "input": 0,
            "output": 0,
            "max_input": 0,
        }
    )

    for row in metrics:
        metric = str(row.get("metric", ""))
        value = row.get("value")
        if not isinstance(value, (int, float)):
            continue

        tags = row.get("tags") or {}
        agent = str(tags.get("agent") or _agent_from_metric(metric))
        bucket = by_agent[agent]
        int_value = int(value)

        if metric.endswith(".tokens.input") or metric.endswith(".tokens.input_tokens"):
            bucket["input"] += int_value
            bucket["requests"] += 1
            bucket["max_input"] = max(bucket["max_input"], int_value)
        elif metric.endswith(".tokens.output") or metric.endswith(
            ".tokens.output_tokens"
        ):
            bucket["output"] += int_value
        elif metric == "tokens_per_comment_window":
            bucket["input"] += int_value
            bucket["requests"] += 1
            bucket["max_input"] = max(bucket["max_input"], int_value)

    if manifest.get("real_llm"):
        real = by_agent["real_llm_total"]
        real["requests"] = int(manifest.get("real_llm_call_count") or 0)
        real["input"] = int(manifest.get("real_llm_input_tokens") or 0)
        real["output"] = int(manifest.get("real_llm_output_tokens") or 0)
        real["max_input"] = int(manifest.get("real_llm_max_input_tokens_single") or 0)

    rows: list[dict[str, int | str]] = []
    for agent in sorted(by_agent):
        bucket = by_agent[agent]
        if not any(bucket.values()):
            continue
        rows.append(
            {
                "agent": agent,
                "requests": bucket["requests"],
                "input": bucket["input"],
                "output": bucket["output"],
                "total": bucket["input"] + bucket["output"],
                "max_input": bucket["max_input"],
            }
        )
    return rows


def _scenario_status(scenarios: list[dict[str, Any]], prefix: str) -> str:
    matched = [s for s in scenarios if str(s.get("scenario_id", "")).startswith(prefix)]
    if not matched:
        return "not_run"
    if all(s.get("status") == "passed" for s in matched):
        return "pass"
    return "fail"


def _count_ndjson_lines(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(
        1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    )


def _count_markdown_files(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for item in path.glob("*.md") if item.name != "index.md")


def _percentile(sorted_values: list[float], p: float) -> float:
    if not sorted_values:
        return 0.0
    k = (len(sorted_values) - 1) * p
    f = int(k)
    c = min(f + 1, len(sorted_values) - 1)
    if f == c:
        return sorted_values[f]
    return sorted_values[f] * (c - k) + sorted_values[c] * (k - f)
