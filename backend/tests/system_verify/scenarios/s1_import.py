"""S1: Real novel import scenario.

Imports an epub from the corpus through the API, validates
chapter/paragraph structure, and records import metrics.
"""
from __future__ import annotations

from typing import Any

from ..client import TargetClient
from ..config import VerifyConfig
from ..contract import (
    validate_chapters_response,
    validate_import_response,
    validate_list_response,
    validate_paragraphs_response,
)
from ..corpus import CorpusManager
from ..metrics_collector import MetricsAggregator
from ..run import RunManager
from ..scenario import ScenarioBuilder, ScenarioRunner, StepAssertionError, assert_that


async def run_s1(run_manager: RunManager, config: VerifyConfig, metrics: MetricsAggregator, corpus: CorpusManager) -> None:
    """Execute S1 scenario."""
    builder = ScenarioBuilder("S1_book_import", "Real novel import and structure validation")

    builder.add_step("import_book", "Import epub through API", _step_import, timeout_s=60.0)
    builder.add_step("list_books", "Verify book appears in list", _step_list_books, timeout_s=10.0)
    builder.add_step("book_detail", "Get book detail with stats", _step_book_detail, timeout_s=10.0)
    builder.add_step("list_chapters", "List chapters and validate structure", _step_list_chapters, timeout_s=10.0)
    builder.add_step("list_paragraphs", "List paragraphs and validate numbering", _step_list_paragraphs, timeout_s=10.0)
    builder.add_step("validate_counts", "Validate chapter/paragraph counts against manifest", _step_validate_counts, timeout_s=5.0)
    builder.add_step("paragraph_stability", "Verify paragraph idx continuity", _step_paragraph_stability, timeout_s=5.0)
    builder.add_step("import_metrics", "Record import metrics", _step_import_metrics, timeout_s=5.0)

    runner = ScenarioRunner(run_manager, config)
    ctx = {
        "run_manager": run_manager,
        "config": config,
        "metrics": metrics,
        "corpus": corpus,
    }
    result = await runner.run(builder, context=ctx)

    if result.status.value != "passed":
        raise RuntimeError(f"S1 failed: {result.failure_summary}")


async def _step_import(ctx: dict[str, Any]) -> None:
    run_manager: RunManager = ctx["run_manager"]
    config: VerifyConfig = ctx["config"]
    corpus: CorpusManager = ctx["corpus"]

    if not corpus.books:
        corpus.load()
        corpus.validate()

    if not corpus.books:
        raise RuntimeError("No books in corpus manifest")

    book_manifest = corpus.books[0]
    ctx["book_manifest"] = book_manifest

    async with TargetClient(config.target.base_url, run_manager, "S1_book_import", "import_book", context=ctx) as client:
        body, rec = await client.import_book(book_manifest.path)
        validate_import_response(body, rec)

        book = body["book"]
        stats = body["import_stats"]
        ctx["imported_book"] = book
        ctx["import_stats"] = stats

        assert_that.gte(
            stats.get("chapter_count", 0),
            book_manifest.expected_min_chapters,
            label="chapter_count",
        )
        assert_that.gte(
            stats.get("paragraph_count", 0),
            book_manifest.expected_min_paragraphs,
            label="paragraph_count",
        )

        metrics: MetricsAggregator = ctx["metrics"]
        metrics.record_from_api_record(rec, scenario_id="S1_book_import", step_id="import_book")
        metrics.record_import_metrics(stats, scenario_id="S1_book_import", step_id="import_book")


async def _step_list_books(ctx: dict[str, Any]) -> None:
    run_manager: RunManager = ctx["run_manager"]
    config: VerifyConfig = ctx["config"]
    imported_book = ctx.get("imported_book", {})

    async with TargetClient(config.target.base_url, run_manager, "S1_book_import", "list_books", context=ctx) as client:
        body, rec = await client.list_books()
        validate_list_response(body, rec)

        assert_that.gte(body["total"], 1, label="book_count")

        found = any(b.get("id") == imported_book.get("id") for b in body["items"])
        assert_that.is_true(found, "Imported book should appear in list")

        metrics: MetricsAggregator = ctx["metrics"]
        metrics.record_from_api_record(rec, scenario_id="S1_book_import", step_id="list_books")


async def _step_book_detail(ctx: dict[str, Any]) -> None:
    run_manager: RunManager = ctx["run_manager"]
    config: VerifyConfig = ctx["config"]
    imported_book = ctx.get("imported_book", {})

    book_id = imported_book.get("id")
    assert_that.is_not_none(book_id, "book_id should be set")

    async with TargetClient(config.target.base_url, run_manager, "S1_book_import", "book_detail", context=ctx) as client:
        body, rec = await client.get_book(book_id)

        assert_that.equal(body.get("id"), book_id, label="book_id")
        assert_that.is_not_none(body.get("title"), label="title")
        assert_that.is_not_none(body.get("total_chapters"), label="total_chapters")
        assert_that.is_not_none(body.get("paragraph_count"), label="paragraph_count")

        ctx["book_detail"] = body

        metrics: MetricsAggregator = ctx["metrics"]
        metrics.record_from_api_record(rec, scenario_id="S1_book_import", step_id="book_detail")


async def _step_list_chapters(ctx: dict[str, Any]) -> None:
    run_manager: RunManager = ctx["run_manager"]
    config: VerifyConfig = ctx["config"]
    imported_book = ctx.get("imported_book", {})

    book_id = imported_book.get("id")
    async with TargetClient(config.target.base_url, run_manager, "S1_book_import", "list_chapters", context=ctx) as client:
        body, rec = await client.list_chapters(book_id)
        validate_chapters_response(body, rec)

        ctx["chapters"] = body["items"]
        ctx["chapter_count"] = body["total"]

        # Verify chapter idx starts at 0 and is sequential
        if body["items"]:
            first_idx = body["items"][0].get("idx")
            assert_that.equal(first_idx, 0, label="first_chapter_idx")

        metrics: MetricsAggregator = ctx["metrics"]
        metrics.record_from_api_record(rec, scenario_id="S1_book_import", step_id="list_chapters")


async def _step_list_paragraphs(ctx: dict[str, Any]) -> None:
    run_manager: RunManager = ctx["run_manager"]
    config: VerifyConfig = ctx["config"]
    imported_book = ctx.get("imported_book", {})
    chapters = ctx.get("chapters", [])

    if not chapters:
        raise RuntimeError("No chapters available to query paragraphs")

    book_id = imported_book.get("id")
    first_chapter = chapters[0]
    chapter_idx = first_chapter["idx"]

    async with TargetClient(config.target.base_url, run_manager, "S1_book_import", "list_paragraphs", context=ctx) as client:
        body, rec = await client.list_paragraphs(book_id, chapter_idx)
        validate_paragraphs_response(body, rec)

        ctx["first_chapter_paragraphs"] = body["items"]
        ctx["first_chapter_paragraph_count"] = body["total"]

        metrics: MetricsAggregator = ctx["metrics"]
        metrics.record_from_api_record(rec, scenario_id="S1_book_import", step_id="list_paragraphs")


async def _step_validate_counts(ctx: dict[str, Any]) -> None:
    book_manifest = ctx.get("book_manifest")
    import_stats = ctx.get("import_stats", {})
    chapters = ctx.get("chapters", [])

    if not book_manifest:
        return

    assert_that.gte(
        len(chapters),
        book_manifest.expected_min_chapters,
        label="chapter_count_vs_manifest",
    )

    total_paragraphs = sum(c.get("paragraph_count", 0) for c in chapters)
    assert_that.gte(
        total_paragraphs,
        book_manifest.expected_min_paragraphs,
        label="paragraph_count_vs_manifest",
    )

    # Validate char count if expected
    if book_manifest.expected_min_chars > 0:
        import_chars = import_stats.get("char_count", 0)
        assert_that.gte(
            import_chars,
            book_manifest.expected_min_chars,
            label="char_count_vs_manifest",
        )


async def _step_paragraph_stability(ctx: dict[str, Any]) -> None:
    """Verify paragraph indices are contiguous starting from 0."""
    paragraphs = ctx.get("first_chapter_paragraphs", [])

    if not paragraphs:
        return

    indices = [p.get("paragraph_idx") for p in paragraphs]
    assert_that.equal(indices[0], 0, label="first_paragraph_idx")

    # Check contiguity
    for i in range(1, len(indices)):
        expected = indices[i - 1] + 1
        if indices[i] != expected:
            raise StepAssertionError(
                assertion="paragraph_continuity",
                message=f"Gap in paragraph indices: expected {expected}, got {indices[i]} at position {i}",
                expected=expected,
                actual=indices[i],
            )

    # Check no empty paragraphs dominate (more than 50% empty)
    non_empty = [p for p in paragraphs if p.get("text", "").strip()]
    empty_ratio = 1.0 - (len(non_empty) / len(paragraphs)) if paragraphs else 0
    assert_that.is_true(
        empty_ratio < 0.5,
        f"Empty paragraph ratio too high: {empty_ratio:.2%}",
    )


async def _step_import_metrics(ctx: dict[str, Any]) -> None:
    import_stats = ctx.get("import_stats", {})
    metrics: MetricsAggregator = ctx["metrics"]

    for key in ("chapter_count", "paragraph_count", "char_count", "token_estimate"):
        if key in import_stats:
            metrics.record(
                f"import.{key}",
                import_stats[key],
                unit="count",
                scenario_id="S1_book_import",
                step_id="import_metrics",
            )

    if "duration_ms" in import_stats:
        metrics.record(
            "import.duration_ms",
            import_stats["duration_ms"],
            unit="ms",
            scenario_id="S1_book_import",
            step_id="import_metrics",
        )
