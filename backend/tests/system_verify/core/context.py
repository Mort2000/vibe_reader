"""Typed scenario runtime context for system verification.

``ScenarioContext.extras`` holds step-local scratch (exporters, audit tallies)
not yet promoted to typed fields. S4 compaction evidence and shared corpus state
live on typed attributes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from tests.system_verify.core.config import VerifyConfig
from tests.system_verify.core.run_spec import RunSpec
from tests.system_verify.corpus import CorpusManager
from tests.system_verify.metrics_collector import MetricsAggregator
from tests.system_verify.profiles.registry import VerificationProfile, profile_from_param_set
from tests.system_verify.core.run_manager import RunManager

if TYPE_CHECKING:
    from tests.system_verify.flows.reading import ReadingSession, ReadingTrace

# Keys mirrored between typed fields and legacy dict during migration.
_LEGACY_TYPED_KEYS: frozenset[str] = frozenset(
    {
        "run_manager",
        "config",
        "metrics",
        "corpus",
        "scenario_id",
        "spec",
        "profile",
        "book",
        "book_id",
        "chapter_idx",
        "cursor",
        "reading_trace",
        "reading_session",
        "comments",
        "completed_windows",
        "compaction_job",
        "compaction_jobs",
        "last_api_record",
        "probe",
        "chapter_paragraphs",
        "final_paragraph_idx",
        "completed_window",
        "imported_book",
        "book_manifest",
        "import_stats",
        "chapters",
        "start_chapter_idx",
        "start_paragraph_idx",
        "long_chapter_idx",
        "long_chapter_start_paragraph",
        "compaction_chapter_idx",
        "compaction_failed_job",
        "compaction_agent_runs",
        "comment_agent_runs",
        "injected_contexts",
        "post_compaction_comment_windows_completed",
        "post_compaction_comment_run",
        "comment_audit_exporter",
        "compaction_audit_exporter",
        "chat_audit_exporter",
        "chat_turns",
        "chat_session_id",
        "chat_agent_runs",
        "verify_jobs",
        "verify_runtime",
        "last_progress_response",
        "comments_before_jump_back",
        "comment_event_count_before_jump_back",
    }
)

_SUITE_CTX_KEYS: tuple[str, ...] = (
    "imported_book",
    "book_manifest",
    "chapters",
    "first_chapter_paragraphs",
    "import_stats",
)

_PUBLISH_SUITE_CTX_KEYS: tuple[str, ...] = _SUITE_CTX_KEYS + (
    "comment_audit_exporter",
    "reading_trace",
)


def _new_reading_trace() -> ReadingTrace:
    from tests.system_verify.flows.reading import ReadingTrace

    return ReadingTrace()


@dataclass
class ScenarioContext:
    """Explicit cross-step state for a single scenario run."""

    config: VerifyConfig
    run_manager: RunManager
    metrics: MetricsAggregator
    scenario_id: str
    corpus: CorpusManager | None = None
    spec: RunSpec | None = None
    profile: VerificationProfile | None = None

    book: dict[str, Any] | None = None
    book_id: int | None = None
    chapter_idx: int | None = None
    cursor: Any | None = None
    reading_trace: ReadingTrace = field(default_factory=_new_reading_trace)
    reading_session: ReadingSession | None = None

    comments: list[dict[str, Any]] = field(default_factory=list)
    completed_windows: list[dict[str, Any]] = field(default_factory=list)
    compaction_job: dict[str, Any] | None = None
    compaction_jobs: list[dict[str, Any]] = field(default_factory=list)

    probe: Any | None = None
    chapter_paragraphs: list[dict[str, Any]] = field(default_factory=list)
    final_paragraph_idx: int | None = None
    completed_window: dict[str, Any] | None = None

    imported_book: dict[str, Any] | None = None
    book_manifest: Any | None = None
    import_stats: dict[str, Any] = field(default_factory=dict)
    chapters: list[dict[str, Any]] = field(default_factory=list)
    start_chapter_idx: int | None = None
    start_paragraph_idx: int | None = None
    long_chapter_idx: int | None = None
    long_chapter_start_paragraph: int | None = None

    compaction_chapter_idx: int | None = None
    compaction_failed_job: dict[str, Any] | None = None
    compaction_agent_runs: list[dict[str, Any]] = field(default_factory=list)
    comment_agent_runs: list[dict[str, Any]] = field(default_factory=list)
    injected_contexts: list[dict[str, Any]] = field(default_factory=list)
    post_compaction_comment_windows_completed: int = 0
    post_compaction_comment_run: dict[str, Any] | None = None

    comment_audit_exporter: Any | None = None
    compaction_audit_exporter: Any | None = None
    chat_audit_exporter: Any | None = None
    chat_turns: list[Any] = field(default_factory=list)
    chat_session_id: int | None = None
    chat_agent_runs: list[dict[str, Any]] = field(default_factory=list)
    verify_jobs: list[dict[str, Any]] = field(default_factory=list)
    verify_runtime: dict[str, Any] | None = None
    last_progress_response: dict[str, Any] | None = None
    comments_before_jump_back: dict[int, int] = field(default_factory=dict)
    comment_event_count_before_jump_back: int = 0

    last_api_record: Any | None = None
    extras: dict[str, Any] = field(default_factory=dict)


def create_scenario_context(
    *,
    run_manager: RunManager,
    config: VerifyConfig,
    metrics: MetricsAggregator,
    scenario_id: str,
    corpus: CorpusManager | None = None,
    spec: RunSpec | None = None,
    profile: VerificationProfile | None = None,
    suite_ctx: dict[str, Any] | None = None,
    reading_trace: ReadingTrace | None = None,
) -> ScenarioContext:
    """Build a typed context and merge suite-level shared state."""
    ctx = ScenarioContext(
        config=config,
        run_manager=run_manager,
        metrics=metrics,
        scenario_id=scenario_id,
        corpus=corpus,
        spec=spec,
        profile=profile or profile_from_param_set(config.params),
        reading_trace=reading_trace or _new_reading_trace(),
    )
    merge_suite_ctx(ctx, suite_ctx)
    return ctx


def merge_suite_ctx(ctx: ScenarioContext, suite_ctx: dict[str, Any] | None) -> None:
    """Copy known suite-level cache entries into the scenario context."""
    if not suite_ctx:
        return
    legacy = as_legacy_dict(ctx)
    for key in _SUITE_CTX_KEYS:
        if key in suite_ctx and key not in legacy:
            _set_legacy_value(ctx, key, suite_ctx[key])


def publish_suite_ctx(ctx: ScenarioContext, suite_ctx: dict[str, Any] | None) -> None:
    """Publish scenario outputs back to suite-level shared state."""
    if not suite_ctx:
        return
    legacy = as_legacy_dict(ctx)
    for key in _PUBLISH_SUITE_CTX_KEYS:
        if key in legacy:
            suite_ctx[key] = legacy[key]


def as_legacy_dict(ctx: ScenarioContext) -> dict[str, Any]:
    """Adapt typed context to the legacy dict shape for TargetClient helpers."""
    legacy: dict[str, Any] = {
        "run_manager": ctx.run_manager,
        "config": ctx.config,
        "metrics": ctx.metrics,
        "corpus": ctx.corpus,
        "scenario_id": ctx.scenario_id,
        "reading_trace": ctx.reading_trace,
    }
    optional_fields: tuple[tuple[str, Any], ...] = (
        ("spec", ctx.spec),
        ("profile", ctx.profile),
        ("book", ctx.book),
        ("book_id", ctx.book_id),
        ("chapter_idx", ctx.chapter_idx),
        ("cursor", ctx.cursor),
        ("reading_session", ctx.reading_session),
        ("comments", ctx.comments),
        ("completed_windows", ctx.completed_windows),
        ("compaction_job", ctx.compaction_job),
        ("compaction_jobs", ctx.compaction_jobs),
        ("last_api_record", ctx.last_api_record),
        ("probe", ctx.probe),
        ("chapter_paragraphs", ctx.chapter_paragraphs),
        ("final_paragraph_idx", ctx.final_paragraph_idx),
        ("completed_window", ctx.completed_window),
        ("imported_book", ctx.imported_book),
        ("book_manifest", ctx.book_manifest),
        ("import_stats", ctx.import_stats),
        ("chapters", ctx.chapters),
        ("start_chapter_idx", ctx.start_chapter_idx),
        ("start_paragraph_idx", ctx.start_paragraph_idx),
        ("long_chapter_idx", ctx.long_chapter_idx),
        ("long_chapter_start_paragraph", ctx.long_chapter_start_paragraph),
        ("compaction_chapter_idx", ctx.compaction_chapter_idx),
        ("compaction_failed_job", ctx.compaction_failed_job),
        ("compaction_agent_runs", ctx.compaction_agent_runs),
        ("comment_agent_runs", ctx.comment_agent_runs),
        ("injected_contexts", ctx.injected_contexts),
        (
            "post_compaction_comment_windows_completed",
            ctx.post_compaction_comment_windows_completed,
        ),
        ("post_compaction_comment_run", ctx.post_compaction_comment_run),
        ("comment_audit_exporter", ctx.comment_audit_exporter),
        ("compaction_audit_exporter", ctx.compaction_audit_exporter),
        ("chat_audit_exporter", ctx.chat_audit_exporter),
        ("chat_turns", ctx.chat_turns),
        ("chat_session_id", ctx.chat_session_id),
        ("chat_agent_runs", ctx.chat_agent_runs),
        ("verify_jobs", ctx.verify_jobs),
        ("verify_runtime", ctx.verify_runtime),
        ("last_progress_response", ctx.last_progress_response),
        ("comments_before_jump_back", ctx.comments_before_jump_back),
        (
            "comment_event_count_before_jump_back",
            ctx.comment_event_count_before_jump_back,
        ),
    )
    for key, value in optional_fields:
        if value is None:
            continue
        if key in (
            "comments",
            "completed_windows",
            "compaction_jobs",
            "chapter_paragraphs",
            "chapters",
            "compaction_agent_runs",
            "comment_agent_runs",
            "injected_contexts",
            "verify_jobs",
            "comments_before_jump_back",
            "chat_turns",
            "chat_agent_runs",
        ) and not value:
            continue
        if key == "import_stats" and not value:
            continue
        if key == "post_compaction_comment_windows_completed" and value == 0:
            continue
        if key == "comment_event_count_before_jump_back" and value == 0:
            continue
        legacy[key] = value
    if ctx.cursor is not None:
        legacy["reading_cursor"] = ctx.cursor
    legacy.update(ctx.extras)
    return legacy


def sync_from_legacy_dict(ctx: ScenarioContext, legacy: dict[str, Any]) -> None:
    """Apply mutations from a legacy dict back onto typed fields."""
    field_map: tuple[tuple[str, str], ...] = (
        ("book", "book"),
        ("book_id", "book_id"),
        ("chapter_idx", "chapter_idx"),
        ("reading_session", "reading_session"),
        ("comments", "comments"),
        ("completed_windows", "completed_windows"),
        ("compaction_job", "compaction_job"),
        ("compaction_jobs", "compaction_jobs"),
        ("last_api_record", "last_api_record"),
        ("probe", "probe"),
        ("chapter_paragraphs", "chapter_paragraphs"),
        ("final_paragraph_idx", "final_paragraph_idx"),
        ("completed_window", "completed_window"),
        ("imported_book", "imported_book"),
        ("book_manifest", "book_manifest"),
        ("import_stats", "import_stats"),
        ("chapters", "chapters"),
        ("start_chapter_idx", "start_chapter_idx"),
        ("start_paragraph_idx", "start_paragraph_idx"),
        ("long_chapter_idx", "long_chapter_idx"),
        ("long_chapter_start_paragraph", "long_chapter_start_paragraph"),
        ("compaction_chapter_idx", "compaction_chapter_idx"),
        ("compaction_failed_job", "compaction_failed_job"),
        ("compaction_agent_runs", "compaction_agent_runs"),
        ("comment_agent_runs", "comment_agent_runs"),
        ("injected_contexts", "injected_contexts"),
        (
            "post_compaction_comment_windows_completed",
            "post_compaction_comment_windows_completed",
        ),
        ("post_compaction_comment_run", "post_compaction_comment_run"),
        ("comment_audit_exporter", "comment_audit_exporter"),
        ("compaction_audit_exporter", "compaction_audit_exporter"),
        ("chat_audit_exporter", "chat_audit_exporter"),
        ("chat_turns", "chat_turns"),
        ("chat_session_id", "chat_session_id"),
        ("chat_agent_runs", "chat_agent_runs"),
        ("verify_jobs", "verify_jobs"),
        ("verify_runtime", "verify_runtime"),
        ("last_progress_response", "last_progress_response"),
        ("comments_before_jump_back", "comments_before_jump_back"),
        (
            "comment_event_count_before_jump_back",
            "comment_event_count_before_jump_back",
        ),
    )
    for legacy_key, attr in field_map:
        if legacy_key not in legacy:
            continue
        value = legacy.get(legacy_key)
        if legacy_key in (
            "comments",
            "completed_windows",
            "compaction_jobs",
            "chapter_paragraphs",
            "chapters",
            "compaction_agent_runs",
            "comment_agent_runs",
            "injected_contexts",
            "verify_jobs",
            "chat_turns",
            "chat_agent_runs",
        ):
            setattr(ctx, attr, list(value or []))
        elif legacy_key == "comments_before_jump_back":
            ctx.comments_before_jump_back = dict(value or {})
        elif legacy_key == "import_stats":
            ctx.import_stats = dict(value or {})
        elif legacy_key == "post_compaction_comment_windows_completed":
            ctx.post_compaction_comment_windows_completed = int(value or 0)
        elif legacy_key == "comment_event_count_before_jump_back":
            ctx.comment_event_count_before_jump_back = int(value or 0)
        else:
            setattr(ctx, attr, value)

    cursor = legacy.get("reading_cursor", legacy.get("cursor"))
    if cursor is not None:
        ctx.cursor = cursor
    if "reading_trace" in legacy and legacy["reading_trace"] is not ctx.reading_trace:
        ctx.reading_trace = legacy["reading_trace"]

    for key, value in legacy.items():
        if key in _LEGACY_TYPED_KEYS or key == "reading_cursor":
            continue
        ctx.extras[key] = value


def _set_legacy_value(ctx: ScenarioContext, key: str, value: Any) -> None:
    legacy = {key: value}
    sync_from_legacy_dict(ctx, legacy)


def ensure_scenario_context(ctx: ScenarioContext | dict[str, Any]) -> ScenarioContext:
    """Normalize flow-layer input to typed ScenarioContext."""
    if isinstance(ctx, ScenarioContext):
        return ctx
    return scenario_context_from_legacy_dict(ctx)


def coerce_scenario_context(
    context: ScenarioContext | dict[str, Any] | None,
    *,
    run_manager: RunManager | None = None,
    config: VerifyConfig | None = None,
) -> ScenarioContext:
    """Normalize runner input to ScenarioContext."""
    _ = run_manager, config  # reserved; ScenarioRunner rejects None before calling
    if isinstance(context, ScenarioContext):
        return context
    if context is None:
        raise TypeError("ScenarioRunner.run requires a scenario context")
    return scenario_context_from_legacy_dict(context)


def scenario_context_from_legacy_dict(legacy: dict[str, Any]) -> ScenarioContext:
    """Construct typed context from an existing legacy dict."""
    ctx = ScenarioContext(
        config=legacy["config"],
        run_manager=legacy["run_manager"],
        metrics=legacy["metrics"],
        scenario_id=str(legacy.get("scenario_id", "")),
        corpus=legacy.get("corpus"),
        spec=legacy.get("spec"),
        profile=legacy.get("profile"),
        reading_trace=legacy.get("reading_trace") or _new_reading_trace(),
    )
    sync_from_legacy_dict(ctx, legacy)
    return ctx
