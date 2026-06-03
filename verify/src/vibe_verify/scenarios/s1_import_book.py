"""S1: import an authorized book and validate probe chapter structure."""

from __future__ import annotations

from vibe_verify.assertions import check_available_count, check_paragraphs
from vibe_verify.scenario import ScenarioContext, ScenarioDefinition

from .common import (
    CORE_SUITES,
    DEFAULT_CORPUS_PURPOSE,
    S1_SCENARIO_ID,
    ensure_corpus,
    open_probe_chapter,
)


def s1_import_book() -> ScenarioDefinition:
    return ScenarioDefinition(
        id=S1_SCENARIO_ID,
        script=run_s1_import_book,
        suites=CORE_SUITES,
        corpus_purpose=DEFAULT_CORPUS_PURPOSE,
        description="S1: import an authorized book through the public API",
    )


async def run_s1_import_book(context: ScenarioContext) -> None:
    ensure_corpus(context, S1_SCENARIO_ID)
    async with context.app.import_epub(context.params.corpus) as book:
        target = await open_probe_chapter(context, book)
        check_paragraphs(
            book.paragraphs,
            minimum=target + 1,
            expected_start=0,
            require_text=True,
        )
        check_available_count(
            "paragraphs",
            requested=target + 1,
            available=len(book.paragraphs),
        )
