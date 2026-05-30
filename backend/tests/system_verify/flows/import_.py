"""Book import, structure validation, reading progress, and import metrics."""

from __future__ import annotations

import asyncio
from typing import Any

from ..core.client_factory import TargetClient
from ..core.config import VerifyConfig
from ..assertions.api_contracts import (
    validate_chapters_response,
    validate_import_response,
    validate_list_response,
    validate_paragraphs_response,
    validate_progress_response,
    validate_reading_progress,
)
from ..corpus import CorpusManager
from ..metrics_collector import MetricsAggregator
from ..core.run_manager import RunManager
from ..core.scenario import StepAssertionError, assert_that

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


async def import_book(
    ctx: dict[str, Any],
    *,
    scenario_id: str,
    step_id: str = "import_book",
) -> None:
    run_manager: RunManager = ctx["run_manager"]
    config: VerifyConfig = ctx["config"]
    corpus: CorpusManager = ctx["corpus"]
    metrics: MetricsAggregator = ctx["metrics"]

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
        scenario_id,
        step_id,
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

        metrics.record_from_api_record(rec, scenario_id=scenario_id, step_id=step_id)
        metrics.record_import_metrics(stats, scenario_id=scenario_id, step_id=step_id)


async def list_books_after_import(
    ctx: dict[str, Any],
    *,
    scenario_id: str,
    step_id: str = "list_books",
) -> None:
    run_manager: RunManager = ctx["run_manager"]
    config: VerifyConfig = ctx["config"]
    metrics: MetricsAggregator = ctx["metrics"]
    imported_book = ctx.get("imported_book", {})

    async with TargetClient(
        config.target.base_url,
        run_manager,
        scenario_id,
        step_id,
        context=ctx,
    ) as client:
        body, rec = await client.list_books()
        validate_list_response(body, rec)

        assert_that.gte(body["total"], 1, label="book_count")

        found = any(b.get("id") == imported_book.get("id") for b in body["items"])
        assert_that.is_true(found, "Imported book should appear in list")

        metrics.record_from_api_record(rec, scenario_id=scenario_id, step_id=step_id)


async def fetch_book_detail(
    ctx: dict[str, Any],
    *,
    scenario_id: str,
    step_id: str = "book_detail",
) -> None:
    run_manager: RunManager = ctx["run_manager"]
    config: VerifyConfig = ctx["config"]
    metrics: MetricsAggregator = ctx["metrics"]
    imported_book = ctx.get("imported_book", {})

    book_id = imported_book.get("id")
    assert_that.is_not_none(book_id, "book_id should be set")

    async with TargetClient(
        config.target.base_url,
        run_manager,
        scenario_id,
        step_id,
        context=ctx,
    ) as client:
        body, rec = await client.get_book(book_id)

        assert_that.equal(body.get("id"), book_id, label="book_id")
        assert_that.is_not_none(body.get("title"), label="title")
        assert_that.is_not_none(body.get("total_chapters"), label="total_chapters")
        assert_that.is_not_none(body.get("paragraph_count"), label="paragraph_count")

        ctx["book_detail"] = body
        metrics.record_from_api_record(rec, scenario_id=scenario_id, step_id=step_id)


async def list_chapters(
    ctx: dict[str, Any],
    *,
    scenario_id: str,
    step_id: str = "list_chapters",
) -> None:
    run_manager: RunManager = ctx["run_manager"]
    config: VerifyConfig = ctx["config"]
    metrics: MetricsAggregator = ctx["metrics"]
    imported_book = ctx.get("imported_book", {})

    book_id = imported_book.get("id")
    async with TargetClient(
        config.target.base_url,
        run_manager,
        scenario_id,
        step_id,
        context=ctx,
    ) as client:
        body, rec = await client.list_chapters(book_id)
        validate_chapters_response(body, rec)

        ctx["chapters"] = body["items"]
        ctx["chapter_count"] = body["total"]

        if body["items"]:
            first_idx = body["items"][0].get("idx")
            assert_that.equal(first_idx, 0, label="first_chapter_idx")

        metrics.record_from_api_record(rec, scenario_id=scenario_id, step_id=step_id)


async def list_import_paragraphs(
    ctx: dict[str, Any],
    *,
    scenario_id: str,
    step_id: str = "list_paragraphs",
) -> None:
    run_manager: RunManager = ctx["run_manager"]
    config: VerifyConfig = ctx["config"]
    metrics: MetricsAggregator = ctx["metrics"]
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
        scenario_id,
        step_id,
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

        metrics.record_from_api_record(rec, scenario_id=scenario_id, step_id=step_id)
        metrics.record_from_api_record(
            content_rec, scenario_id=scenario_id, step_id=step_id
        )


async def validate_import_counts(
    ctx: dict[str, Any],
    *,
    scenario_id: str,
    step_id: str = "validate_counts",
) -> None:
    _ = scenario_id, step_id
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


async def resolve_happy_path_probe(
    ctx: dict[str, Any],
    *,
    scenario_id: str,
    step_id: str = "happy_path_probe",
) -> None:
    corpus: CorpusManager = ctx["corpus"]
    metrics: MetricsAggregator = ctx["metrics"]
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

    metrics.record(
        "corpus.happy_path_probe.resolved",
        1,
        unit="count",
        scenario_id=scenario_id,
        step_id=step_id,
        tags={
            "chapter_idx": probe.chapter_idx,
            "paragraph_idx": probe.paragraph_idx,
        },
    )


async def assert_paragraph_stability(
    ctx: dict[str, Any],
    *,
    scenario_id: str,
    step_id: str = "paragraph_stability",
) -> None:
    _ = scenario_id, step_id
    paragraphs = ctx.get("first_chapter_paragraphs", [])

    if not paragraphs:
        return

    indices = [p.get("paragraph_idx") for p in paragraphs]
    assert_that.equal(indices[0], 0, label="first_paragraph_idx")

    for i in range(1, len(indices)):
        expected = indices[i - 1] + 1
        if indices[i] != expected:
            raise StepAssertionError(
                assertion="paragraph_continuity",
                message=(
                    f"Gap in paragraph indices: expected {expected}, "
                    f"got {indices[i]} at position {i}"
                ),
                expected=expected,
                actual=indices[i],
            )

    non_empty = [p for p in paragraphs if p.get("text", "").strip()]
    empty_ratio = 1.0 - (len(non_empty) / len(paragraphs)) if paragraphs else 0
    assert_that.is_true(
        empty_ratio < 0.5,
        f"Empty paragraph ratio too high: {empty_ratio:.2%}",
    )


async def verify_reading_progress_roundtrip(
    ctx: dict[str, Any],
    *,
    scenario_id: str,
    step_id: str = "reading_progress",
) -> None:
    run_manager: RunManager = ctx["run_manager"]
    config: VerifyConfig = ctx["config"]
    metrics: MetricsAggregator = ctx["metrics"]
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
        scenario_id,
        step_id,
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

        metrics.record_from_api_record(rec, scenario_id=scenario_id, step_id=step_id)


async def verify_import_idempotent(
    ctx: dict[str, Any],
    *,
    scenario_id: str,
    step_id: str = "import_idempotent",
) -> None:
    run_manager: RunManager = ctx["run_manager"]
    config: VerifyConfig = ctx["config"]
    metrics: MetricsAggregator = ctx["metrics"]
    book_manifest = ctx.get("book_manifest")
    imported_book = ctx.get("imported_book", {})

    if not book_manifest:
        raise RuntimeError("book_manifest missing for idempotent import check")

    original_id = imported_book.get("id")
    assert_that.is_not_none(original_id, "original book_id should be set")

    async with TargetClient(
        config.target.base_url,
        run_manager,
        scenario_id,
        step_id,
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

        metrics.record_from_api_record(rec, scenario_id=scenario_id, step_id=step_id)


async def verify_progress_dedup_identical(
    ctx: dict[str, Any],
    *,
    scenario_id: str,
    step_id: str = "progress_dedup_identical",
) -> None:
    run_manager: RunManager = ctx["run_manager"]
    config: VerifyConfig = ctx["config"]
    metrics: MetricsAggregator = ctx["metrics"]
    imported_book = ctx.get("imported_book", {})

    book_id = imported_book.get("id")
    assert_that.is_not_none(book_id, "book_id should be set")

    chapter_idx, paragraph_idx = _pick_progress_paragraph(ctx)
    scroll_pct = 0.42

    async with TargetClient(
        config.target.base_url,
        run_manager,
        scenario_id,
        step_id,
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

        metrics.record_from_api_record(rec, scenario_id=scenario_id, step_id=step_id)


async def verify_progress_skip_trivial_scroll(
    ctx: dict[str, Any],
    *,
    scenario_id: str,
    step_id: str = "progress_skip_trivial_scroll",
) -> None:
    run_manager: RunManager = ctx["run_manager"]
    config: VerifyConfig = ctx["config"]
    metrics: MetricsAggregator = ctx["metrics"]
    imported_book = ctx.get("imported_book", {})

    book_id = imported_book.get("id")
    assert_that.is_not_none(book_id, "book_id should be set")

    chapter_idx, paragraph_idx = _pick_progress_paragraph(ctx)
    base_scroll = 0.42
    trivial_scroll = 0.4205

    async with TargetClient(
        config.target.base_url,
        run_manager,
        scenario_id,
        step_id,
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

        metrics.record_from_api_record(rec, scenario_id=scenario_id, step_id=step_id)


async def record_import_metrics(
    ctx: dict[str, Any],
    *,
    scenario_id: str,
    step_id: str = "import_metrics",
) -> None:
    import_stats = ctx.get("import_stats", {})
    metrics: MetricsAggregator = ctx["metrics"]

    for key in ("chapter_count", "paragraph_count", "char_count", "token_estimate"):
        if key in import_stats:
            metrics.record(
                f"import.{key}",
                import_stats[key],
                unit="count",
                scenario_id=scenario_id,
                step_id=step_id,
            )

    if "duration_ms" in import_stats:
        metrics.record(
            "import.duration_ms",
            import_stats["duration_ms"],
            unit="ms",
            scenario_id=scenario_id,
            step_id=step_id,
        )
