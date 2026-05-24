"""S1: Real novel import scenario.

Imports an epub from the corpus through the API, validates
chapter/paragraph structure, and records import metrics.
"""

from __future__ import annotations

import asyncio
from typing import Any

from ..client import TargetClient
from ..config import VerifyConfig
from ..contract import (
    validate_chapters_response,
    validate_import_response,
    validate_list_response,
    validate_paragraphs_response,
    validate_progress_response,
    validate_reading_progress,
)
from ..corpus import CorpusManager
from ..metrics_collector import MetricsAggregator
from ..run import RunManager
from ..scenario import ScenarioBuilder, ScenarioRunner, StepAssertionError, assert_that


CONTENT_CHAPTER_IDX = 1


def _pick_progress_paragraph(
    ctx: dict[str, Any], *, prefer: int = 20
) -> tuple[int, int]:
    """Return (chapter_idx, paragraph_idx) within chapter 1 bounds."""
    paragraphs = ctx.get("content_chapter_paragraphs", [])
    if not paragraphs:
        raise RuntimeError("No chapter 1 paragraphs available for progress checks")
    last_idx = paragraphs[-1].get("paragraph_idx", 0)
    return CONTENT_CHAPTER_IDX, min(prefer, last_idx)


async def run_s1(
    run_manager: RunManager,
    config: VerifyConfig,
    metrics: MetricsAggregator,
    corpus: CorpusManager,
    suite_ctx: dict[str, Any] | None = None,
) -> None:
    """Execute S1 scenario."""
    builder = ScenarioBuilder(
        "S1_book_import", "Real novel import and structure validation"
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

    async with TargetClient(
        config.target.base_url,
        run_manager,
        "S1_book_import",
        "import_book",
        context=ctx,
    ) as client:
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
        metrics.record_from_api_record(
            rec, scenario_id="S1_book_import", step_id="import_book"
        )
        metrics.record_import_metrics(
            stats, scenario_id="S1_book_import", step_id="import_book"
        )


async def _step_list_books(ctx: dict[str, Any]) -> None:
    run_manager: RunManager = ctx["run_manager"]
    config: VerifyConfig = ctx["config"]
    imported_book = ctx.get("imported_book", {})

    async with TargetClient(
        config.target.base_url, run_manager, "S1_book_import", "list_books", context=ctx
    ) as client:
        body, rec = await client.list_books()
        validate_list_response(body, rec)

        assert_that.gte(body["total"], 1, label="book_count")

        found = any(b.get("id") == imported_book.get("id") for b in body["items"])
        assert_that.is_true(found, "Imported book should appear in list")

        metrics: MetricsAggregator = ctx["metrics"]
        metrics.record_from_api_record(
            rec, scenario_id="S1_book_import", step_id="list_books"
        )


async def _step_book_detail(ctx: dict[str, Any]) -> None:
    run_manager: RunManager = ctx["run_manager"]
    config: VerifyConfig = ctx["config"]
    imported_book = ctx.get("imported_book", {})

    book_id = imported_book.get("id")
    assert_that.is_not_none(book_id, "book_id should be set")

    async with TargetClient(
        config.target.base_url,
        run_manager,
        "S1_book_import",
        "book_detail",
        context=ctx,
    ) as client:
        body, rec = await client.get_book(book_id)

        assert_that.equal(body.get("id"), book_id, label="book_id")
        assert_that.is_not_none(body.get("title"), label="title")
        assert_that.is_not_none(body.get("total_chapters"), label="total_chapters")
        assert_that.is_not_none(body.get("paragraph_count"), label="paragraph_count")

        ctx["book_detail"] = body

        metrics: MetricsAggregator = ctx["metrics"]
        metrics.record_from_api_record(
            rec, scenario_id="S1_book_import", step_id="book_detail"
        )


async def _step_list_chapters(ctx: dict[str, Any]) -> None:
    run_manager: RunManager = ctx["run_manager"]
    config: VerifyConfig = ctx["config"]
    imported_book = ctx.get("imported_book", {})

    book_id = imported_book.get("id")
    async with TargetClient(
        config.target.base_url,
        run_manager,
        "S1_book_import",
        "list_chapters",
        context=ctx,
    ) as client:
        body, rec = await client.list_chapters(book_id)
        validate_chapters_response(body, rec)

        ctx["chapters"] = body["items"]
        ctx["chapter_count"] = body["total"]

        # Verify chapter idx starts at 0 and is sequential
        if body["items"]:
            first_idx = body["items"][0].get("idx")
            assert_that.equal(first_idx, 0, label="first_chapter_idx")

        metrics: MetricsAggregator = ctx["metrics"]
        metrics.record_from_api_record(
            rec, scenario_id="S1_book_import", step_id="list_chapters"
        )


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

    async with TargetClient(
        config.target.base_url,
        run_manager,
        "S1_book_import",
        "list_paragraphs",
        context=ctx,
    ) as client:
        body, rec = await client.list_paragraphs(book_id, chapter_idx)
        validate_paragraphs_response(body, rec)

        ctx["first_chapter_paragraphs"] = body["items"]
        ctx["first_chapter_paragraph_count"] = body["total"]

        content_body, content_rec = await client.list_paragraphs(
            book_id, CONTENT_CHAPTER_IDX
        )
        validate_paragraphs_response(content_body, content_rec)
        ctx["content_chapter_paragraphs"] = content_body["items"]
        ctx["content_chapter_idx"] = CONTENT_CHAPTER_IDX

        metrics: MetricsAggregator = ctx["metrics"]
        metrics.record_from_api_record(
            rec, scenario_id="S1_book_import", step_id="list_paragraphs"
        )
        metrics.record_from_api_record(
            content_rec, scenario_id="S1_book_import", step_id="list_paragraphs"
        )


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

    if book_manifest.expected_min_chars > 0:
        import_chars = import_stats.get("char_count", 0)
        assert_that.gte(
            import_chars,
            book_manifest.expected_min_chars,
            label="char_count_vs_manifest",
        )


async def _step_happy_path_probe(ctx: dict[str, Any]) -> None:
    corpus: CorpusManager = ctx["corpus"]
    book_manifest = ctx.get("book_manifest")
    if not book_manifest:
        return

    probe = corpus.get_probe(book_manifest.alias, "happy_path_current")
    assert_that.is_not_none(
        probe,
        "Corpus must declare happy_path_current probe",
    )
    assert probe is not None
    ctx["happy_path_probe"] = probe

    errors = corpus.validate_happy_path_probe(book_manifest.alias)
    assert_that.is_true(
        len(errors) == 0,
        f"happy_path_current validation failed: {'; '.join(errors)}",
    )

    metrics: MetricsAggregator = ctx["metrics"]
    metrics.record(
        "corpus.happy_path_probe.resolved",
        1,
        unit="count",
        scenario_id="S1_book_import",
        step_id="happy_path_probe",
        tags={
            "chapter_idx": probe.chapter_idx,
            "paragraph_idx": probe.paragraph_idx,
        },
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


async def _step_reading_progress(ctx: dict[str, Any]) -> None:
    """PUT a reading position, then GET and assert fields match (A1 smoke).

    Probes are unified on chapter 1; the position-switch check jumps from the
    early paragraph (20) back to paragraph 0 within the same chapter instead
    of crossing into chapter 2.
    """
    run_manager: RunManager = ctx["run_manager"]
    config: VerifyConfig = ctx["config"]
    imported_book = ctx.get("imported_book", {})
    content_paragraphs = ctx.get("content_chapter_paragraphs", [])

    book_id = imported_book.get("id")
    assert_that.is_not_none(book_id, "book_id should be set")

    if not content_paragraphs:
        raise RuntimeError("No chapter 1 paragraphs available to set reading progress")

    target_chapter = CONTENT_CHAPTER_IDX
    target_paragraph = min(20, content_paragraphs[-1].get("paragraph_idx", 0))
    target_scroll = 0.42

    async with TargetClient(
        config.target.base_url,
        run_manager,
        "S1_book_import",
        "reading_progress",
        context=ctx,
    ) as client:
        initial, rec = await client.get_progress(book_id)
        validate_reading_progress(initial, rec)
        assert_that.equal(initial.get("updated_at"), None, label="initial_updated_at")

        put_body, rec = await client.update_progress(
            book_id,
            target_chapter,
            target_paragraph,
            target_scroll,
        )
        validate_progress_response(put_body, rec)

        saved = put_body["progress"]
        assert_that.equal(saved["book_id"], book_id, label="put_book_id")
        assert_that.equal(saved["chapter_idx"], target_chapter, label="put_chapter_idx")
        assert_that.equal(
            saved["paragraph_idx"], target_paragraph, label="put_paragraph_idx"
        )
        assert_that.equal(saved["scroll_pct"], target_scroll, label="put_scroll_pct")

        frontier = put_body.get("assistant_frontier_paragraph_idx")
        assert_that.is_not_none(frontier, label="assistant_frontier_paragraph_idx")
        assert_that.gte(
            frontier, target_paragraph, label="assistant_frontier_gte_reading"
        )

        restored, rec = await client.get_progress(book_id)
        validate_reading_progress(restored, rec)
        assert_that.equal(restored["book_id"], book_id, label="get_book_id")
        assert_that.equal(
            restored["chapter_idx"], target_chapter, label="get_chapter_idx"
        )
        assert_that.equal(
            restored["paragraph_idx"], target_paragraph, label="get_paragraph_idx"
        )
        assert_that.equal(restored["scroll_pct"], target_scroll, label="get_scroll_pct")
        assert_that.is_not_none(restored.get("updated_at"), label="restored_updated_at")

        await client.update_progress(book_id, target_chapter, 0, 0.0)
        switched, rec = await client.get_progress(book_id)
        validate_reading_progress(switched, rec)
        assert_that.equal(
            switched["chapter_idx"],
            target_chapter,
            label="chapter_switch_chapter_idx",
        )
        assert_that.equal(
            switched["paragraph_idx"],
            0,
            label="chapter_switch_paragraph_idx",
        )

        metrics: MetricsAggregator = ctx["metrics"]
        metrics.record_from_api_record(
            rec, scenario_id="S1_book_import", step_id="reading_progress"
        )


async def _step_import_idempotent(ctx: dict[str, Any]) -> None:
    """P-04: same file hash re-import must not create a duplicate book record."""
    run_manager: RunManager = ctx["run_manager"]
    config: VerifyConfig = ctx["config"]
    book_manifest = ctx.get("book_manifest")
    imported_book = ctx.get("imported_book", {})

    if not book_manifest:
        raise RuntimeError("book_manifest missing for idempotent import check")

    original_id = imported_book.get("id")
    assert_that.is_not_none(original_id, "original book_id should be set")

    async with TargetClient(
        config.target.base_url,
        run_manager,
        "S1_book_import",
        "import_idempotent",
        context=ctx,
    ) as client:
        before, rec = await client.list_books()
        validate_list_response(before, rec)
        count_before = before["total"]

        body, rec = await client.import_book(book_manifest.path)
        validate_import_response(body, rec)

        reimported_id = body["book"]["id"]
        assert_that.equal(
            reimported_id,
            original_id,
            label="reimport_book_id",
        )

        after, rec = await client.list_books()
        validate_list_response(after, rec)
        assert_that.equal(
            after["total"],
            count_before,
            label="book_count_after_reimport",
        )

        metrics: MetricsAggregator = ctx["metrics"]
        metrics.record_from_api_record(
            rec, scenario_id="S1_book_import", step_id="import_idempotent"
        )


async def _step_progress_dedup_identical(ctx: dict[str, Any]) -> None:
    """P-05: unchanged progress should not rewrite updated_at on every PUT."""
    run_manager: RunManager = ctx["run_manager"]
    config: VerifyConfig = ctx["config"]
    imported_book = ctx.get("imported_book", {})

    book_id = imported_book.get("id")
    assert_that.is_not_none(book_id, "book_id should be set")

    chapter_idx, paragraph_idx = _pick_progress_paragraph(ctx)
    scroll_pct = 0.42

    async with TargetClient(
        config.target.base_url,
        run_manager,
        "S1_book_import",
        "progress_dedup_identical",
        context=ctx,
    ) as client:
        first, rec = await client.update_progress(
            book_id,
            chapter_idx,
            paragraph_idx,
            scroll_pct,
        )
        validate_progress_response(first, rec)
        assert_that.equal(
            first["progress"]["paragraph_idx"],
            paragraph_idx,
            label="first_paragraph_idx",
        )
        first_updated_at = first["progress"]["updated_at"]
        assert_that.is_not_none(first_updated_at, label="first_updated_at")

        await asyncio.sleep(1.1)

        second, rec = await client.update_progress(
            book_id,
            chapter_idx,
            paragraph_idx,
            scroll_pct,
        )
        validate_progress_response(second, rec)
        second_updated_at = second["progress"]["updated_at"]

        assert_that.equal(
            second_updated_at,
            first_updated_at,
            label="identical_progress_updated_at",
        )

        metrics: MetricsAggregator = ctx["metrics"]
        metrics.record_from_api_record(
            rec, scenario_id="S1_book_import", step_id="progress_dedup_identical"
        )


async def _step_progress_skip_trivial_scroll(ctx: dict[str, Any]) -> None:
    """P-05: micro scroll_pct drift within one paragraph should be treated as no-op."""
    run_manager: RunManager = ctx["run_manager"]
    config: VerifyConfig = ctx["config"]
    imported_book = ctx.get("imported_book", {})

    book_id = imported_book.get("id")
    assert_that.is_not_none(book_id, "book_id should be set")

    chapter_idx, paragraph_idx = _pick_progress_paragraph(ctx)
    base_scroll = 0.42
    trivial_scroll = 0.4205

    async with TargetClient(
        config.target.base_url,
        run_manager,
        "S1_book_import",
        "progress_skip_trivial_scroll",
        context=ctx,
    ) as client:
        base, rec = await client.update_progress(
            book_id,
            chapter_idx,
            paragraph_idx,
            base_scroll,
        )
        validate_progress_response(base, rec)
        assert_that.equal(
            base["progress"]["paragraph_idx"], paragraph_idx, label="base_paragraph_idx"
        )
        base_updated_at = base["progress"]["updated_at"]
        assert_that.is_not_none(base_updated_at, label="base_updated_at")

        await asyncio.sleep(1.1)

        trivial, rec = await client.update_progress(
            book_id,
            chapter_idx,
            paragraph_idx,
            trivial_scroll,
        )
        validate_progress_response(trivial, rec)
        trivial_updated_at = trivial["progress"]["updated_at"]

        assert_that.equal(
            trivial_updated_at,
            base_updated_at,
            label="trivial_scroll_updated_at",
        )

        metrics: MetricsAggregator = ctx["metrics"]
        metrics.record_from_api_record(
            rec, scenario_id="S1_book_import", step_id="progress_skip_trivial_scroll"
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
