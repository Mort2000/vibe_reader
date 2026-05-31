"""Authorized corpus catalog and stable scenario probe resolution."""

from __future__ import annotations

import hashlib
import json
import re
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .models import optional_int

_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")


@dataclass(frozen=True)
class Probe:
    """Stable reading location and capability declaration."""

    name: str
    chapter_idx: int = 0
    paragraph_idx: int = 0
    start_chapter_idx: int | None = None
    start_paragraph_idx: int | None = None
    purposes: tuple[str, ...] = ()
    min_context_tokens: int = 0
    requires_compaction: bool = False
    test_compaction_trigger_tokens: int = 0
    test_compaction_min_source_tokens: int = 0
    test_compaction_min_source_paragraphs: int = 0
    max_real_input_tokens: int = 0
    allow_real_llm: bool = False
    allow_external_judge: bool = False


@dataclass(frozen=True)
class CorpusEntry:
    """One registered authorized corpus item."""

    alias: str
    path: Path
    license: str
    sha256: str
    language: str = "zh-CN"
    min_chapters: int = 1
    min_paragraphs: int = 0
    min_chars: int = 0
    probes: tuple[Probe, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class CorpusRequirement:
    """Scenario-level corpus capability requirement."""

    purpose: str
    min_context_tokens: int = 0
    real_llm: bool = False
    external_judge: bool = False


@dataclass(frozen=True)
class ResolvedCorpus:
    entry: CorpusEntry
    probe: Probe


class CorpusCatalog:
    """Load, validate, and resolve registered corpus files."""

    def __init__(self, manifest_path: str | Path):
        self.manifest_path = Path(manifest_path)
        self.entries: list[CorpusEntry] = []

    def load(self) -> list[CorpusEntry]:
        raw = tomllib.loads(self.manifest_path.read_text(encoding="utf-8"))
        base = self.manifest_path.parent
        entries: list[CorpusEntry] = []
        for item in raw.get("books", []):
            path = Path(item["path"])
            if not path.is_absolute():
                path = base / path
            probes = tuple(
                Probe(
                    name=probe["name"],
                    chapter_idx=int(probe.get("chapter_idx", 0)),
                    paragraph_idx=int(probe.get("paragraph_idx", 0)),
                    start_chapter_idx=optional_int(probe.get("start_chapter_idx")),
                    start_paragraph_idx=optional_int(probe.get("start_paragraph_idx")),
                    purposes=tuple(probe.get("purposes", (probe["name"],))),
                    min_context_tokens=int(
                        probe.get(
                            "min_context_tokens",
                            probe.get("requires_context_tokens_gte", 0),
                        )
                    ),
                    requires_compaction=parse_bool(
                        probe,
                        "requires_compaction",
                        context=f"{item.get('alias')}.{probe.get('name')}",
                    ),
                    test_compaction_trigger_tokens=int(
                        probe.get("test_compaction_trigger_tokens", 0)
                    ),
                    test_compaction_min_source_tokens=int(
                        probe.get("test_compaction_min_source_tokens", 0)
                    ),
                    test_compaction_min_source_paragraphs=int(
                        probe.get("test_compaction_min_source_paragraphs", 0)
                    ),
                    max_real_input_tokens=int(probe.get("max_real_input_tokens", 0)),
                    allow_real_llm=parse_bool(
                        probe,
                        "allow_real_llm",
                        legacy_key="real_llm",
                        context=f"{item.get('alias')}.{probe.get('name')}",
                    ),
                    allow_external_judge=parse_bool(
                        probe,
                        "allow_external_judge",
                        context=f"{item.get('alias')}.{probe.get('name')}",
                    ),
                )
                for probe in item.get("probes", [])
            )
            entries.append(
                CorpusEntry(
                    alias=item["alias"],
                    path=path,
                    license=item.get("license", ""),
                    sha256=item.get("sha256", ""),
                    language=item.get("language", "zh-CN"),
                    min_chapters=int(
                        item.get("min_chapters", item.get("expected_min_chapters", 1))
                    ),
                    min_paragraphs=int(
                        item.get(
                            "min_paragraphs",
                            item.get("expected_min_paragraphs", 0),
                        )
                    ),
                    min_chars=int(
                        item.get("min_chars", item.get("expected_min_chars", 0))
                    ),
                    probes=probes,
                )
            )
        self.entries = entries
        return list(entries)

    def validate(self) -> list[str]:
        if not self.entries:
            self.load()
        errors: list[str] = []
        aliases: set[str] = set()
        for entry in self.entries:
            if entry.alias in aliases:
                errors.append(f"[{entry.alias}] duplicate alias")
            aliases.add(entry.alias)
            if not entry.path.exists():
                errors.append(f"[{entry.alias}] file not found: {entry.path}")
                continue
            if not entry.license:
                errors.append(f"[{entry.alias}] license is required")
            if not entry.sha256:
                errors.append(f"[{entry.alias}] sha256 is required")
            elif not _SHA256.match(entry.sha256):
                errors.append(f"[{entry.alias}] sha256 must be 64 hex characters")
            elif file_sha256(entry.path) != entry.sha256.lower():
                errors.append(f"[{entry.alias}] sha256 mismatch")
            for name, value in (
                ("min_chapters", entry.min_chapters),
                ("min_paragraphs", entry.min_paragraphs),
                ("min_chars", entry.min_chars),
            ):
                if value < 0:
                    errors.append(f"[{entry.alias}] {name} must be non-negative")
            for probe in entry.probes:
                positions = (
                    probe.chapter_idx,
                    probe.paragraph_idx,
                    probe.start_chapter_idx or 0,
                    probe.start_paragraph_idx or 0,
                )
                if any(value < 0 for value in positions):
                    errors.append(
                        f"[{entry.alias}.{probe.name}] probe position must be "
                        "non-negative"
                    )
                for name, value in (
                    ("min_context_tokens", probe.min_context_tokens),
                    (
                        "test_compaction_trigger_tokens",
                        probe.test_compaction_trigger_tokens,
                    ),
                    (
                        "test_compaction_min_source_tokens",
                        probe.test_compaction_min_source_tokens,
                    ),
                    (
                        "test_compaction_min_source_paragraphs",
                        probe.test_compaction_min_source_paragraphs,
                    ),
                    ("max_real_input_tokens", probe.max_real_input_tokens),
                ):
                    if value < 0:
                        errors.append(
                            f"[{entry.alias}.{probe.name}] {name} must be non-negative"
                        )
                if probe.requires_compaction and (
                    probe.test_compaction_min_source_tokens <= 0
                    or probe.test_compaction_min_source_paragraphs <= 0
                ):
                    errors.append(
                        f"[{entry.alias}.{probe.name}] compaction probe must declare "
                        "positive source token and paragraph minimums"
                    )
        return errors

    def resolve(self, requirement: CorpusRequirement) -> ResolvedCorpus:
        errors = self.validate()
        if errors:
            raise ValueError("invalid corpus catalog: " + "; ".join(errors))
        for entry in self.entries:
            for probe in entry.probes:
                if requirement.purpose not in probe.purposes:
                    continue
                if probe.min_context_tokens < requirement.min_context_tokens:
                    continue
                if requirement.real_llm and not probe.allow_real_llm:
                    continue
                if requirement.external_judge and not probe.allow_external_judge:
                    continue
                return ResolvedCorpus(entry=entry, probe=probe)
        raise LookupError(f"no corpus probe satisfies {requirement}")

    def resolved_manifest(self, resolved: ResolvedCorpus) -> dict[str, Any]:
        entry = resolved.entry
        return {
            "alias": entry.alias,
            "path": str(entry.path),
            "license": entry.license,
            "language": entry.language,
            "sha256": file_sha256(entry.path),
            "probe": asdict(resolved.probe),
        }

    def export_resolved(self, path: str | Path, resolved: ResolvedCorpus) -> Path:
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(self.resolved_manifest(resolved), indent=2, ensure_ascii=False)
            + "\n",
            encoding="utf-8",
        )
        return output


def file_sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def parse_bool(
    raw: dict[str, Any],
    key: str,
    *,
    context: str,
    legacy_key: str | None = None,
) -> bool:
    value = raw.get(key, raw.get(legacy_key, False) if legacy_key else False)
    if isinstance(value, bool):
        return value
    raise TypeError(f"[{context}] {key} must be boolean, got {type(value).__name__}")
