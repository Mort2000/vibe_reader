"""Corpus import, probe resolution, and chapter/paragraph loading."""

from __future__ import annotations

from typing import Any

from ..audit_exporter import CommentAuditExporter
from ..core.client_factory import TargetClient
from ..compaction_audit import CompactionAuditExporter
from ..core.config import VerifyConfig
from ..core.context import ScenarioContext
from ..assertions.api_contracts import validate_import_response
from ..corpus import BookManifest, CorpusManager, ProbeConfig
from ..core.run_manager import RunManager
from ..core.scenario import assert_that

def get_probe(corpus: CorpusManager, name: str = "early") -> ProbeConfig:
    if not corpus.books:
        corpus.load()
    book = corpus.books[0]
    for probe in book.probes:
        if probe.name == name:
            return probe
    if book.probes:
        return book.probes[0]
    return ProbeConfig(name="default", chapter_idx=1, paragraph_idx=20)

async def ensure_imported_book(
    ctx: ScenarioContext | dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    if isinstance(ctx, ScenarioContext):
        imported = ctx.imported_book
        if imported and imported.get("id"):
            return imported["id"], imported

        assert ctx.corpus is not None
        corpus = ctx.corpus
        if not corpus.books:
            corpus.load()
            corpus.validate()

        book_manifest: BookManifest = ctx.book_manifest or corpus.books[0]
        ctx.book_manifest = book_manifest

        async with TargetClient(
            ctx.config.target.base_url,
            ctx.run_manager,
            ctx.scenario_id or "setup",
            "ensure_import",
            context=ctx,
        ) as client:
            body, rec = await client.import_book(book_manifest.path)
            validate_import_response(body, rec)
            book = body["book"]
            ctx.imported_book = book
            ctx.import_stats = body.get("import_stats", {})
            return book["id"], book

    imported = ctx.get("imported_book")
    if imported and imported.get("id"):
        return imported["id"], imported

    run_manager: RunManager = ctx["run_manager"]
    config: VerifyConfig = ctx["config"]
    corpus: CorpusManager = ctx["corpus"]

    if not corpus.books:
        corpus.load()
        corpus.validate()

    book_manifest: BookManifest = ctx.get("book_manifest") or corpus.books[0]
    ctx["book_manifest"] = book_manifest

    async with TargetClient(
        config.target.base_url,
        run_manager,
        ctx.get("scenario_id", "setup"),
        "ensure_import",
        context=ctx,
    ) as client:
        body, rec = await client.import_book(book_manifest.path)
        validate_import_response(body, rec)
        book = body["book"]
        ctx["imported_book"] = book
        ctx["import_stats"] = body.get("import_stats", {})
        return book["id"], book

async def load_chapter_paragraphs(
    ctx: ScenarioContext | dict[str, Any],
    book_id: int,
    chapter_idx: int,
) -> list[dict[str, Any]]:
    cache_key = f"paragraphs_{book_id}_{chapter_idx}"
    if isinstance(ctx, ScenarioContext):
        if cache_key in ctx.extras:
            return ctx.extras[cache_key]

        async with TargetClient(
            ctx.config.target.base_url,
            ctx.run_manager,
            ctx.scenario_id or "setup",
            "load_paragraphs",
            context=ctx,
        ) as client:
            body, _ = await client.list_paragraphs(
                book_id, chapter_idx, params={"limit": 5000}
            )
            paragraphs = body.get("items", [])
            ctx.extras[cache_key] = paragraphs
            return paragraphs

    if cache_key in ctx:
        return ctx[cache_key]

    run_manager: RunManager = ctx["run_manager"]
    config: VerifyConfig = ctx["config"]

    async with TargetClient(
        config.target.base_url,
        run_manager,
        ctx.get("scenario_id", "setup"),
        "load_paragraphs",
        context=ctx,
    ) as client:
        body, _ = await client.list_paragraphs(
            book_id, chapter_idx, params={"limit": 5000}
        )
        paragraphs = body.get("items", [])
        ctx[cache_key] = paragraphs
        return paragraphs

def paragraph_text_map(paragraphs: list[dict[str, Any]]) -> dict[int, str]:
    return {p["paragraph_idx"]: p.get("text", "") for p in paragraphs}


def neighbor_paragraphs(
    paragraphs: list[dict[str, Any]],
    paragraph_idx: int,
    radius: int = 1,
) -> list[dict[str, Any]]:
    by_idx = paragraph_text_map(paragraphs)
    neighbors: list[dict[str, Any]] = []
    for offset in (-radius, radius):
        idx = paragraph_idx + offset
        if idx in by_idx and idx != paragraph_idx:
            neighbors.append({"paragraph_idx": idx, "text": by_idx[idx]})
    return sorted(neighbors, key=lambda item: item["paragraph_idx"])

def resolve_happy_path_start(
    probe: ProbeConfig, fallback: ProbeConfig
) -> tuple[int, int]:
    """Return the start chapter/paragraph for real happy-path reading."""
    chapter_idx = (
        probe.start_chapter_idx
        if probe.start_chapter_idx is not None
        else fallback.chapter_idx
    )
    paragraph_idx = (
        probe.start_paragraph_idx
        if probe.start_paragraph_idx is not None
        else fallback.paragraph_idx
    )
    return chapter_idx, paragraph_idx


async def load_chapters(
    ctx: ScenarioContext | dict[str, Any],
    book_id: int,
    *,
    client: TargetClient | None = None,
) -> list[dict[str, Any]]:
    if isinstance(ctx, ScenarioContext):
        if ctx.chapters:
            return ctx.chapters

        if client is None:
            async with TargetClient(
                ctx.config.target.base_url,
                ctx.run_manager,
                ctx.scenario_id or "setup",
                "load_chapters",
                context=ctx,
            ) as owned_client:
                body, _ = await owned_client.list_chapters(book_id)
                chapters = body.get("items") or []
        else:
            body, _ = await client.list_chapters(book_id)
            chapters = body.get("items") or []

        ctx.chapters = chapters
        return chapters

    if ctx.get("chapters"):
        return ctx["chapters"]

    run_manager: RunManager = ctx["run_manager"]
    config: VerifyConfig = ctx["config"]

    if client is None:
        async with TargetClient(
            config.target.base_url,
            run_manager,
            ctx.get("scenario_id", "setup"),
            "load_chapters",
            context=ctx,
        ) as owned_client:
            body, _ = await owned_client.list_chapters(book_id)
            chapters = body.get("items") or []
    else:
        body, _ = await client.list_chapters(book_id)
        chapters = body.get("items") or []

    ctx["chapters"] = chapters
    return chapters


def chapter_by_idx(
    chapters: list[dict[str, Any]], chapter_idx: int
) -> dict[str, Any] | None:
    for chapter in chapters:
        if chapter.get("idx") == chapter_idx:
            return chapter
    return None


def last_paragraph_idx(chapter: dict[str, Any]) -> int:
    count = int(chapter.get("paragraph_count") or 0)
    return max(0, count - 1)


def next_chapter_idx(chapters: list[dict[str, Any]], current_idx: int) -> int | None:
    ordered = sorted(int(ch["idx"]) for ch in chapters)
    try:
        pos = ordered.index(current_idx)
    except ValueError:
        return None
    if pos + 1 >= len(ordered):
        return None
    return ordered[pos + 1]


def assert_happy_path_corpus(corpus: CorpusManager) -> None:
    """Raise when the active corpus does not satisfy happy_path_current probes."""
    corpus_errors = corpus.validate_happy_path_probe()
    if corpus_errors:
        raise RuntimeError(
            "Corpus does not satisfy happy_path_current requirements: "
            + "; ".join(corpus_errors)
        )


async def setup_a2_reading_start(
    ctx: ScenarioContext,
    *,
    scenario_id: str,
    step_id: str = "setup",
) -> None:
    """Import book, resolve happy-path start probes, and load chapter metadata."""
    from .reading import ReadingCursor

    assert ctx.corpus is not None
    book_id, book = await ensure_imported_book(ctx)
    happy_probe = get_probe(ctx.corpus, "happy_path_current")
    early_probe = get_probe(ctx.corpus, "early")
    start_chapter, start_paragraph = resolve_happy_path_start(happy_probe, early_probe)

    ctx.book_id = book_id
    ctx.book = book
    ctx.probe = happy_probe
    ctx.start_chapter_idx = start_chapter
    ctx.start_paragraph_idx = start_paragraph
    ctx.chapter_idx = start_chapter

    async with TargetClient(
        ctx.config.target.base_url,
        ctx.run_manager,
        scenario_id,
        step_id,
        context=ctx,
    ) as client:
        chapters = await load_chapters(ctx, book_id, client=client)

    assert_that.is_true(len(chapters) >= 2, "Book must have at least two chapters")

    start_chapter_meta = next(
        (ch for ch in chapters if ch.get("idx") == start_chapter), None
    )
    assert_that.is_not_none(
        start_chapter_meta, "Start chapter must exist in book metadata"
    )
    assert start_chapter_meta is not None

    paragraphs = await load_chapter_paragraphs(ctx, book_id, start_chapter)
    assert_that.is_true(len(paragraphs) > 0, "Start chapter must contain paragraphs")
    assert_that.gte(
        paragraphs[-1]["paragraph_idx"],
        start_paragraph,
        label="start_paragraph_in_range",
    )

    ctx.chapters = chapters
    ctx.chapter_paragraphs = paragraphs
    ctx.cursor = ReadingCursor(start_chapter, start_paragraph)
    ctx.comment_audit_exporter = CommentAuditExporter(
        ctx.run_manager, ctx.config
    )


async def setup_a3_long_chapter(
    ctx: ScenarioContext,
    *,
    scenario_id: str,
    step_id: str = "setup",
) -> None:
    """Import book and start reading at the long-chapter probe (A3 compaction)."""
    from .reading import ReadingCursor

    assert ctx.corpus is not None
    book_id, book = await ensure_imported_book(ctx)
    probe = get_probe(ctx.corpus, "happy_path_current")

    target_chapter = probe.chapter_idx
    target_paragraph = 0

    ctx.book_id = book_id
    ctx.book = book
    ctx.probe = probe
    ctx.start_chapter_idx = target_chapter
    ctx.start_paragraph_idx = target_paragraph
    ctx.chapter_idx = target_chapter
    ctx.long_chapter_idx = target_chapter
    ctx.long_chapter_start_paragraph = target_paragraph

    async with TargetClient(
        ctx.config.target.base_url,
        ctx.run_manager,
        scenario_id,
        step_id,
        context=ctx,
    ) as client:
        chapters = await load_chapters(ctx, book_id, client=client)

    chapter_meta = next(
        (ch for ch in chapters if ch.get("idx") == target_chapter), None
    )
    assert_that.is_not_none(chapter_meta, "Long chapter must exist in book metadata")
    assert chapter_meta is not None

    paragraphs = await load_chapter_paragraphs(ctx, book_id, target_chapter)
    assert_that.is_true(len(paragraphs) > 0, "Long chapter must contain paragraphs")
    assert_that.gte(
        paragraphs[-1]["paragraph_idx"],
        probe.paragraph_idx,
        label="long_chapter_probe_in_range",
    )

    ctx.chapters = chapters
    ctx.chapter_paragraphs = paragraphs
    ctx.cursor = ReadingCursor(target_chapter, target_paragraph)
    ctx.compaction_audit_exporter = CompactionAuditExporter(
        ctx.run_manager, ctx.config
    )


async def setup_s2_continuous_reading(
    ctx: ScenarioContext,
    *,
    scenario_id: str,
    step_id: str = "setup_book",
) -> None:
    """Import corpus book, resolve early probe, and prepare S2 audit exporter."""
    book_id, book = await ensure_imported_book(ctx)

    assert ctx.corpus is not None
    probe = get_probe(ctx.corpus, "early")
    ctx.book_id = book_id
    ctx.book = book
    ctx.probe = probe
    ctx.chapter_idx = probe.chapter_idx

    paragraphs = await load_chapter_paragraphs(ctx, book_id, probe.chapter_idx)
    assert_that.is_true(len(paragraphs) > 0, "Chapter must contain paragraphs")
    ctx.chapter_paragraphs = paragraphs

    if ctx.comment_audit_exporter is None:
        ctx.comment_audit_exporter = CommentAuditExporter(
            ctx.run_manager, ctx.config
        )


async def setup_s3_fast_scroll(
    ctx: ScenarioContext,
    *,
    scenario_id: str,
    step_id: str = "setup_book",
) -> None:
    """Import corpus book and resolve early/middle probes for S3 fast scroll."""
    book_id, book = await ensure_imported_book(ctx)

    assert ctx.corpus is not None
    early = get_probe(ctx.corpus, "early")
    middle = get_probe(ctx.corpus, "middle")
    ctx.book_id = book_id
    ctx.book = book
    ctx.probe = early
    ctx.extras["middle_probe"] = middle
    ctx.chapter_idx = early.chapter_idx

    paragraphs = await load_chapter_paragraphs(ctx, book_id, early.chapter_idx)
    assert_that.is_true(len(paragraphs) > 0, "Chapter must contain paragraphs")
    ctx.chapter_paragraphs = paragraphs


async def setup_s5_direct_chat(
    ctx: ScenarioContext,
    *,
    scenario_id: str,
    step_id: str = "setup_book",
) -> None:
    """Import corpus book, resolve chat_live probe, and prepare chat audit exporters."""
    from ..audit_exporter import CommentAuditExporter, ensure_chat_audit_exporter
    from .reading import ReadingCursor

    book_id, book = await ensure_imported_book(ctx)

    assert ctx.corpus is not None
    probe = get_probe(ctx.corpus, "chat_live")
    ctx.book_id = book_id
    ctx.book = book
    ctx.probe = probe
    ctx.chapter_idx = probe.chapter_idx
    ctx.cursor = ReadingCursor(probe.chapter_idx, probe.paragraph_idx)

    paragraphs = await load_chapter_paragraphs(ctx, book_id, probe.chapter_idx)
    assert_that.is_true(len(paragraphs) > 0, "Chapter must contain paragraphs")
    ctx.chapter_paragraphs = paragraphs

    if ctx.chat_audit_exporter is None:
        ensure_chat_audit_exporter(ctx)
    if ctx.comment_audit_exporter is None:
        ctx.comment_audit_exporter = CommentAuditExporter(
            ctx.run_manager, ctx.config
        )


async def setup_s4_long_context(
    ctx: ScenarioContext,
    *,
    scenario_id: str,
    step_id: str = "setup_book",
) -> None:
    """Import corpus book, resolve long_context probe, and prepare compaction audit."""
    from .reading import ReadingCursor

    book_id, book = await ensure_imported_book(ctx)

    assert ctx.corpus is not None
    probe = get_probe(ctx.corpus, "long_context")
    min_context_tokens = probe.requires_context_tokens_gte or 0
    assert_that.gte(
        min_context_tokens,
        100_000,
        label="long_context_probe_requires_context_tokens_gte",
    )
    ctx.book_id = book_id
    ctx.book = book
    ctx.probe = probe
    ctx.chapter_idx = probe.chapter_idx

    async with TargetClient(
        ctx.config.target.base_url,
        ctx.run_manager,
        scenario_id,
        step_id,
        context=ctx,
    ) as client:
        chapters = await load_chapters(ctx, book_id, client=client)

    chapter = next((ch for ch in chapters if ch.get("idx") == probe.chapter_idx), None)
    assert_that.is_not_none(chapter, "long_context chapter must exist")
    assert chapter is not None

    paragraphs = await load_chapter_paragraphs(ctx, book_id, probe.chapter_idx)
    assert_that.is_true(len(paragraphs) > 0, "long_context chapter must contain paragraphs")
    assert_that.gte(
        paragraphs[-1]["paragraph_idx"],
        probe.paragraph_idx,
        label="long_context_probe_in_range",
    )

    ctx.chapters = chapters
    ctx.chapter_paragraphs = paragraphs
    ctx.cursor = ReadingCursor(probe.chapter_idx, probe.paragraph_idx)
    ctx.long_chapter_idx = probe.chapter_idx
    ctx.long_chapter_start_paragraph = probe.paragraph_idx
    if ctx.compaction_audit_exporter is None:
        ctx.compaction_audit_exporter = CompactionAuditExporter(
            ctx.run_manager, ctx.config
        )
