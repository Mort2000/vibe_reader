"""Metrics and trace collection, aggregation, and export.

Reads metrics from the verify/metrics endpoint and SSE events,
aggregates latency/token/cache metrics, and writes ndjson output.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .config import VerifyConfig
from .run import RunManager


def merge_llm_tags(
    config: VerifyConfig | None, tags: dict[str, Any] | None = None
) -> dict[str, Any]:
    merged = dict(tags or {})
    if config is not None:
        merged.update(config.llm_metric_tags())
    return merged


@dataclass
class MetricPoint:
    """A single metric observation."""

    run_id: str
    scenario_id: str
    step_id: str
    metric: str
    value: float
    unit: str
    tags: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "scenario_id": self.scenario_id,
            "step_id": self.step_id,
            "metric": self.metric,
            "value": self.value,
            "unit": self.unit,
            "tags": self.tags,
            "created_at": self.created_at,
        }


@dataclass
class TraceIndexEntry:
    """A single trace index record linking scenario to trace_id."""

    run_id: str
    scenario_id: str
    step_id: str
    trace_id: str
    request_id: str
    book_id: int | None = None
    chapter_idx: int | None = None
    window_id: int | None = None
    agent: str = ""
    context_hash: str = ""
    prompt_version: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "scenario_id": self.scenario_id,
            "step_id": self.step_id,
            "trace_id": self.trace_id,
            "request_id": self.request_id,
            "book_id": self.book_id,
            "chapter_idx": self.chapter_idx,
            "window_id": self.window_id,
            "agent": self.agent,
            "context_hash": self.context_hash,
            "prompt_version": self.prompt_version,
        }


class MetricsAggregator:
    """Collects and aggregates metric points."""

    def __init__(self, run_manager: RunManager, config: VerifyConfig | None = None):
        self.run_manager = run_manager
        self.config = config or run_manager.config
        self._points: list[MetricPoint] = []
        self._traces: list[TraceIndexEntry] = []

    def record(
        self,
        metric: str,
        value: float,
        unit: str = "ms",
        scenario_id: str = "",
        step_id: str = "",
        tags: dict[str, Any] | None = None,
    ) -> None:
        pt = MetricPoint(
            run_id=self.run_manager.run_id,
            scenario_id=scenario_id,
            step_id=step_id,
            metric=metric,
            value=value,
            unit=unit,
            tags=merge_llm_tags(self.config, tags),
            created_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        self._points.append(pt)
        self.run_manager.write_ndjson("metrics.ndjson", [pt.to_dict()])

    def record_trace(
        self,
        trace_id: str,
        request_id: str,
        scenario_id: str = "",
        step_id: str = "",
        book_id: int | None = None,
        chapter_idx: int | None = None,
        window_id: int | None = None,
        agent: str = "",
        context_hash: str = "",
        prompt_version: str = "",
    ) -> None:
        entry = TraceIndexEntry(
            run_id=self.run_manager.run_id,
            scenario_id=scenario_id,
            step_id=step_id,
            trace_id=trace_id,
            request_id=request_id,
            book_id=book_id,
            chapter_idx=chapter_idx,
            window_id=window_id,
            agent=agent,
            context_hash=context_hash,
            prompt_version=prompt_version,
        )
        self._traces.append(entry)
        self.run_manager.write_ndjson("traces/trace_index.ndjson", [entry.to_dict()])

    def record_from_api_record(
        self, rec: Any, scenario_id: str = "", step_id: str = ""
    ) -> None:
        """Extract metrics from a TargetClient APIRecord."""
        if rec.duration_ms is not None:
            self.record(
                f"api.{rec.method.lower()}.duration_ms",
                rec.duration_ms,
                unit="ms",
                scenario_id=scenario_id,
                step_id=step_id,
                tags={"url": rec.url, "status_code": rec.status_code},
            )
        if rec.trace_id:
            self.record_trace(
                trace_id=rec.trace_id,
                request_id=rec.request_id,
                scenario_id=scenario_id,
                step_id=step_id,
            )

    def record_import_metrics(
        self, stats: dict, scenario_id: str = "", step_id: str = ""
    ) -> None:
        """Record import-related metrics from import_stats."""
        for key in (
            "duration_ms",
            "paragraph_count",
            "chapter_count",
            "char_count",
            "token_estimate",
        ):
            if key in stats:
                unit = "ms" if key == "duration_ms" else "count"
                self.record(
                    f"import.{key}",
                    stats[key],
                    unit=unit,
                    scenario_id=scenario_id,
                    step_id=step_id,
                )

    def record_sse_event_metrics(
        self, event: Any, scenario_id: str = "", step_id: str = ""
    ) -> None:
        """Record trace from SSE events."""
        if hasattr(event, "trace_id") and event.trace_id:
            self.record_trace(
                trace_id=event.trace_id,
                request_id="",
                scenario_id=scenario_id,
                step_id=step_id,
                book_id=event.book_id,
                chapter_idx=event.chapter_idx,
                window_id=event.window_id,
            )

    @property
    def points(self) -> list[MetricPoint]:
        return list(self._points)

    @property
    def traces(self) -> list[TraceIndexEntry]:
        return list(self._traces)

    def aggregate(self, metric: str) -> dict[str, float | int] | None:
        """Compute percentile aggregation for a named metric."""
        values = sorted(p.value for p in self._points if p.metric == metric)
        if not values:
            return None
        n = len(values)
        return {
            "count": n,
            "min": values[0],
            "p50": _percentile(values, 0.50),
            "p90": _percentile(values, 0.90),
            "p95": _percentile(values, 0.95),
            "p99": _percentile(values, 0.99),
            "max": values[-1],
            "mean": sum(values) / n,
            "stddev": _stddev(values),
        }

    def check_no_api_key_in_outputs(self) -> list[str]:
        """Scan all output files for potential API key leakage."""
        import re

        findings: list[str] = []
        api_key_pattern = re.compile(r"sk-[a-zA-Z0-9]{20,}")
        sensitive_words = ("api_key", "apikey", "secret_key")

        output_dir = self.run_manager.base_dir
        for path in output_dir.rglob("*.ndjson"):
            try:
                text = path.read_text()
                if api_key_pattern.search(text):
                    findings.append(f"Potential API key in {path}")
                for word in sensitive_words:
                    if word in text.lower() and "api_key_configured" not in text:
                        idx = text.lower().index(word)
                        context = text[max(0, idx - 30) : idx + 50]
                        findings.append(
                            f"Sensitive word '{word}' in {path}: ...{context}..."
                        )
            except Exception:
                pass
        return findings


def _percentile(sorted_values: list[float], p: float) -> float:
    if not sorted_values:
        return 0.0
    k = (len(sorted_values) - 1) * p
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return sorted_values[int(f)]
    d0 = sorted_values[int(f)] * (c - k)
    d1 = sorted_values[int(c)] * (k - f)
    return d0 + d1


def _stddev(values: list[float]) -> float:
    n = len(values)
    if n < 2:
        return 0.0
    mean = sum(values) / n
    variance = sum((v - mean) ** 2 for v in values) / (n - 1)
    return math.sqrt(variance)
