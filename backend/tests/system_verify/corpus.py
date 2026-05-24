"""Corpus manager: parses and validates epub corpus manifest.

Reads a TOML manifest, validates file existence, sha256, and metadata,
and writes a resolved manifest with actual statistics.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import toml

from .config import VerifyConfig
from .run import RunManager


@dataclass
class ProbeConfig:
    name: str
    chapter_idx: int = 0
    paragraph_idx: int = 0
    start_chapter_idx: int | None = None
    start_paragraph_idx: int | None = None
    requires_context_tokens_gte: int | None = None
    real_llm: bool = False
    requires_compaction: bool = False
    test_compaction_trigger_tokens: int | None = None
    test_compaction_min_source_tokens: int | None = None
    test_compaction_min_source_paragraphs: int | None = None
    max_real_input_tokens: int | None = None


@dataclass
class BookManifest:
    alias: str
    path: str
    language: str = "zh-CN"
    license: str = ""
    sha256: str = ""
    expected_min_chapters: int = 1
    expected_min_paragraphs: int = 0
    expected_min_chars: int = 0
    probes: list[ProbeConfig] = field(default_factory=list)


class CorpusManager:
    """Manages verification corpus from a TOML manifest."""

    def __init__(self, config: VerifyConfig, manifest_path: str | pathlib.Path):
        self.config = config
        self.manifest_path = pathlib.Path(manifest_path)
        self.books: list[BookManifest] = []
        self._validation_errors: list[str] = []
        self._resolved: dict[str, Any] | None = None

    def load(self) -> list[BookManifest]:
        """Parse the corpus manifest TOML file."""
        if not self.manifest_path.exists():
            self._validation_errors.append(f"Manifest not found: {self.manifest_path}")
            return []

        raw = toml.load(str(self.manifest_path))
        self.books = []

        for book_raw in raw.get("books", []):
            probes = [
                ProbeConfig(
                    name=p.get("name", "unnamed"),
                    chapter_idx=p.get("chapter_idx", 0),
                    paragraph_idx=p.get("paragraph_idx", 0),
                    start_chapter_idx=p.get("start_chapter_idx"),
                    start_paragraph_idx=p.get("start_paragraph_idx"),
                    requires_context_tokens_gte=p.get("requires_context_tokens_gte"),
                    real_llm=bool(p.get("real_llm", False)),
                    requires_compaction=bool(p.get("requires_compaction", False)),
                    test_compaction_trigger_tokens=p.get(
                        "test_compaction_trigger_tokens"
                    ),
                    test_compaction_min_source_tokens=p.get(
                        "test_compaction_min_source_tokens"
                    ),
                    test_compaction_min_source_paragraphs=p.get(
                        "test_compaction_min_source_paragraphs"
                    ),
                    max_real_input_tokens=p.get("max_real_input_tokens"),
                )
                for p in book_raw.get("probes", [])
            ]

            book = BookManifest(
                alias=book_raw.get("alias", ""),
                path=book_raw.get("path", ""),
                language=book_raw.get("language", "zh-CN"),
                license=book_raw.get("license", ""),
                sha256=book_raw.get("sha256", ""),
                expected_min_chapters=book_raw.get("expected_min_chapters", 1),
                expected_min_paragraphs=book_raw.get("expected_min_paragraphs", 0),
                expected_min_chars=book_raw.get("expected_min_chars", 0),
                probes=probes,
            )
            self.books.append(book)

        return self.books

    def validate(self) -> bool:
        """Validate all books in the manifest. Returns True if all pass."""
        if not self.books:
            self.load()

        self._validation_errors = []
        all_ok = True

        for book in self.books:
            book_path = pathlib.Path(book.path)
            if not book_path.exists():
                alt_path = self.manifest_path.parent / book.path
                if alt_path.exists():
                    book.path = str(alt_path)
                    book_path = alt_path
                else:
                    self._validation_errors.append(
                        f"[{book.alias}] File not found: {book.path}"
                    )
                    all_ok = False
                    continue

            if book.sha256:
                actual = _file_sha256(book_path)
                if actual != book.sha256:
                    self._validation_errors.append(
                        f"[{book.alias}] sha256 mismatch: expected {book.sha256}, got {actual}"
                    )
                    all_ok = False

            if not book.license:
                self._validation_errors.append(f"[{book.alias}] No license declared")
                all_ok = False

            for probe in book.probes:
                if probe.chapter_idx < 0:
                    self._validation_errors.append(
                        f"[{book.alias}] probe '{probe.name}' has negative chapter_idx"
                    )
                    all_ok = False

        return all_ok

    def validate_happy_path_probe(self, book_alias: str | None = None) -> list[str]:
        """Validate ``happy_path_current`` probe for real-happy-path readiness."""
        if not self.books:
            self.load()

        errors: list[str] = []
        book = self.get_book(book_alias) if book_alias else (self.books[0] if self.books else None)
        if book is None:
            return ["No books in corpus manifest"]

        probe = self.get_probe(book.alias, "happy_path_current")
        if probe is None:
            errors.append(
                f"[{book.alias}] missing happy_path_current probe required for real-happy-path"
            )
            return errors

        if not probe.real_llm and not self.config.params.assertions.allow_probe_without_real_llm_flag:
            errors.append(
                f"[{book.alias}] happy_path_current must set real_llm = true "
                f"for param set {self.config.params.name!r}"
            )

        long_flow = self.config.params.long_flow
        min_tokens = (
            probe.test_compaction_min_source_tokens
            or long_flow.test_compaction_min_source_tokens
        )
        min_paragraphs = (
            probe.test_compaction_min_source_paragraphs
            or long_flow.test_compaction_min_source_paragraphs
        )
        min_context = probe.requires_context_tokens_gte or 0

        if book.expected_min_paragraphs < min_paragraphs:
            errors.append(
                f"[{book.alias}] expected_min_paragraphs={book.expected_min_paragraphs} "
                f"< happy_path min paragraphs {min_paragraphs}"
            )
        if min_context and book.expected_min_chars < min_context // 4:
            errors.append(
                f"[{book.alias}] corpus may be too small for "
                f"requires_context_tokens_gte={min_context}"
            )
        if min_tokens <= 0 or min_paragraphs <= 0:
            errors.append(
                f"[{book.alias}] happy_path_current missing compaction min constraints"
            )

        return errors

    def resolve(self, run_manager: RunManager | None = None) -> pathlib.Path | None:
        """Write corpus_manifest.resolved.json with validated info."""
        if not self.books:
            self.load()

        resolved_books = []
        for book in self.books:
            book_path = pathlib.Path(book.path)
            entry: dict[str, Any] = {
                "alias": book.alias,
                "path": str(book_path),
                "language": book.language,
                "license": book.license,
                "sha256": _file_sha256(book_path) if book_path.exists() else None,
                "file_size": book_path.stat().st_size if book_path.exists() else None,
                "expected_min_chapters": book.expected_min_chapters,
                "expected_min_paragraphs": book.expected_min_paragraphs,
                "expected_min_chars": book.expected_min_chars,
                "probes": [_probe_dict(p) for p in book.probes],
                "validation_status": "ok"
                if not self._has_errors_for(book.alias)
                else "failed",
            }
            resolved_books.append(entry)

        manifest = {
            "resolved_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "manifest_source": str(self.manifest_path),
            "books": resolved_books,
            "validation_errors": self._validation_errors,
            "happy_path_validation_errors": self.validate_happy_path_probe(),
        }

        self._resolved = manifest

        if run_manager:
            out_path = run_manager.base_dir / "corpus_manifest.resolved.json"
            out_path.write_text(
                json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"
            )
            sha_list = [b["sha256"] for b in resolved_books if b.get("sha256")]
            run_manager.set_corpus_sha256(sha_list)
            return out_path

        return None

    @property
    def validation_errors(self) -> list[str]:
        return list(self._validation_errors)

    def _has_errors_for(self, alias: str) -> bool:
        return any(f"[{alias}]" in e for e in self._validation_errors)

    def get_book(self, alias: str) -> BookManifest | None:
        for b in self.books:
            if b.alias == alias:
                return b
        return None

    def get_probe(self, book_alias: str, probe_name: str) -> ProbeConfig | None:
        book = self.get_book(book_alias)
        if not book:
            return None
        for p in book.probes:
            if p.name == probe_name:
                return p
        return None


def _probe_dict(probe: ProbeConfig) -> dict[str, Any]:
    data: dict[str, Any] = {
        "name": probe.name,
        "chapter_idx": probe.chapter_idx,
        "paragraph_idx": probe.paragraph_idx,
        "requires_context_tokens_gte": probe.requires_context_tokens_gte,
    }
    if probe.start_chapter_idx is not None:
        data["start_chapter_idx"] = probe.start_chapter_idx
    if probe.start_paragraph_idx is not None:
        data["start_paragraph_idx"] = probe.start_paragraph_idx
    if probe.real_llm:
        data["real_llm"] = True
    if probe.requires_compaction:
        data["requires_compaction"] = True
    for key in (
        "test_compaction_trigger_tokens",
        "test_compaction_min_source_tokens",
        "test_compaction_min_source_paragraphs",
        "max_real_input_tokens",
    ):
        value = getattr(probe, key)
        if value is not None:
            data[key] = value
    return data


def _file_sha256(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()
