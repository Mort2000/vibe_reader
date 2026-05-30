"""S1: Real novel import scenario.

Imports an epub from the corpus through the API, validates
chapter/paragraph structure, and records import metrics.
"""

from __future__ import annotations

from typing import Any

from ..core.config import VerifyConfig
from ..corpus import CorpusManager
from ..metrics_collector import MetricsAggregator
from ..core.run_manager import RunManager
from ..core.scenario import ScenarioBuilder, ScenarioRunner
from ..flows import import_ as import_flow


SCENARIO_ID = "S1_book_import"


async def run_s1(
    run_manager: RunManager,
    config: VerifyConfig,
    metrics: MetricsAggregator,
    corpus: CorpusManager,
    suite_ctx: dict[str, Any] | None = None,
) -> None:
    """Execute S1 scenario."""
    builder = ScenarioBuilder(
        SCENARIO_ID, "Real novel import and structure validation"
    )
    builder.continue_on_failure = True

    builder.add_step(
        "import_book", "Import epub through API", _step_import, timeout_s=60.0
    )
    builder.add_step(
        "list_books", "Verify book appears in list", _step_list_books, timeout_s=10.0
    )
    builder.add_step(
        "book_detail", "Get book detail with stats", _step_book_detail, timeout_s=10.0
    )
    builder.add_step(
        "list_chapters",
        "List chapters and validate structure",
        _step_list_chapters,
        timeout_s=10.0,
    )
    builder.add_step(
        "list_paragraphs",
        "List paragraphs and validate numbering",
        _step_list_paragraphs,
        timeout_s=10.0,
    )
    builder.add_step(
        "validate_counts",
        "Validate chapter/paragraph counts against manifest",
        _step_validate_counts,
        timeout_s=5.0,
    )
    builder.add_step(
        "happy_path_probe",
        "Parse happy_path_current probe for real-happy-path readiness",
        _step_happy_path_probe,
        timeout_s=5.0,
    )
    builder.add_step(
        "paragraph_stability",
        "Verify paragraph idx continuity",
        _step_paragraph_stability,
        timeout_s=5.0,
    )
    builder.add_step(
        "reading_progress",
        "Save and restore reading progress via PUT then GET",
        _step_reading_progress,
        timeout_s=10.0,
    )
    builder.add_step(
        "import_idempotent",
        "P-04: Re-import same epub should return existing book (idempotent)",
        _step_import_idempotent,
        timeout_s=60.0,
    )
    builder.add_step(
        "progress_dedup_identical",
        "P-05: Identical progress PUT should be deduplicated on backend",
        _step_progress_dedup_identical,
        timeout_s=10.0,
    )
    builder.add_step(
        "progress_skip_trivial_scroll",
        "P-05: Trivial scroll_pct change within same paragraph should not persist",
        _step_progress_skip_trivial_scroll,
        timeout_s=10.0,
    )
    builder.add_step(
        "import_metrics", "Record import metrics", _step_import_metrics, timeout_s=5.0
    )

    runner = ScenarioRunner(run_manager, config)
    ctx = {
        "run_manager": run_manager,
        "config": config,
        "metrics": metrics,
        "corpus": corpus,
        "scenario_id": SCENARIO_ID,
    }
    if suite_ctx:
        for key in (
            "imported_book",
            "book_manifest",
            "chapters",
            "first_chapter_paragraphs",
            "import_stats",
        ):
            if key in suite_ctx:
                ctx[key] = suite_ctx[key]

    result = await runner.run(builder, context=ctx)

    if suite_ctx is not None:
        for key in (
            "imported_book",
            "book_manifest",
            "chapters",
            "first_chapter_paragraphs",
            "import_stats",
        ):
            if key in ctx:
                suite_ctx[key] = ctx[key]

    if result.status.value != "passed":
        raise RuntimeError(f"S1 failed: {result.failure_summary}")


async def _step_import(ctx: dict[str, Any]) -> None:
    await import_flow.import_book(ctx, scenario_id=SCENARIO_ID, step_id="import_book")


async def _step_list_books(ctx: dict[str, Any]) -> None:
    await import_flow.list_books_after_import(
        ctx, scenario_id=SCENARIO_ID, step_id="list_books"
    )


async def _step_book_detail(ctx: dict[str, Any]) -> None:
    await import_flow.fetch_book_detail(
        ctx, scenario_id=SCENARIO_ID, step_id="book_detail"
    )


async def _step_list_chapters(ctx: dict[str, Any]) -> None:
    await import_flow.list_chapters(
        ctx, scenario_id=SCENARIO_ID, step_id="list_chapters"
    )


async def _step_list_paragraphs(ctx: dict[str, Any]) -> None:
    await import_flow.list_import_paragraphs(
        ctx, scenario_id=SCENARIO_ID, step_id="list_paragraphs"
    )


async def _step_validate_counts(ctx: dict[str, Any]) -> None:
    await import_flow.validate_import_counts(
        ctx, scenario_id=SCENARIO_ID, step_id="validate_counts"
    )


async def _step_happy_path_probe(ctx: dict[str, Any]) -> None:
    await import_flow.resolve_happy_path_probe(
        ctx, scenario_id=SCENARIO_ID, step_id="happy_path_probe"
    )


async def _step_paragraph_stability(ctx: dict[str, Any]) -> None:
    await import_flow.assert_paragraph_stability(
        ctx, scenario_id=SCENARIO_ID, step_id="paragraph_stability"
    )


async def _step_reading_progress(ctx: dict[str, Any]) -> None:
    await import_flow.verify_reading_progress_roundtrip(
        ctx, scenario_id=SCENARIO_ID, step_id="reading_progress"
    )


async def _step_import_idempotent(ctx: dict[str, Any]) -> None:
    await import_flow.verify_import_idempotent(
        ctx, scenario_id=SCENARIO_ID, step_id="import_idempotent"
    )


async def _step_progress_dedup_identical(ctx: dict[str, Any]) -> None:
    await import_flow.verify_progress_dedup_identical(
        ctx, scenario_id=SCENARIO_ID, step_id="progress_dedup_identical"
    )


async def _step_progress_skip_trivial_scroll(ctx: dict[str, Any]) -> None:
    await import_flow.verify_progress_skip_trivial_scroll(
        ctx, scenario_id=SCENARIO_ID, step_id="progress_skip_trivial_scroll"
    )


async def _step_import_metrics(ctx: dict[str, Any]) -> None:
    await import_flow.record_import_metrics(
        ctx, scenario_id=SCENARIO_ID, step_id="import_metrics"
    )
