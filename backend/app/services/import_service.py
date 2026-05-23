from __future__ import annotations

import hashlib
import logging
import re
import shutil
import time
import warnings
from pathlib import Path
from typing import Any

from bs4 import XMLParsedAsHTMLWarning

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

import aiosqlite
from ebooklib import epub

from ..repos import books as book_repo
from ..repos import chapters as chapter_repo
from ..repos import paragraphs as paragraph_repo
from ..repos import progress as progress_repo

logger = logging.getLogger(__name__)

_CHINESE_PUNCT = re.compile(r"[　-〿＀-￯]")


def _estimate_tokens(text: str) -> int:
    chars = len(text)
    cjk = sum(1 for c in text if "一" <= c <= "鿿" or "㐀" <= c <= "䶿")
    non_cjk = chars - cjk
    return int(cjk * 1.5 + non_cjk * 0.25)


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _is_valid_paragraph(text: str) -> bool:
    cleaned = text.strip()
    if not cleaned:
        return False
    if len(cleaned) < 2:
        return False
    return True


def _extract_cover(book: epub.EpubBook, dest_dir: Path) -> str | None:
    for item in book.get_items_of_type(epub.EpubImage):
        if item.get_name().lower().find("cover") >= 0:
            cover_path = dest_dir / f"cover_{item.get_name().split('/')[-1]}"
            cover_path.write_bytes(item.get_content())
            return str(cover_path)
    return None


def _parse_chapters(book: epub.EpubBook) -> list[dict[str, Any]]:
    from bs4 import BeautifulSoup

    items = [item for item in book.get_items_of_type(9) if isinstance(item, epub.EpubHtml)]

    if not items:
        return []

    spine_ids = [sid for sid, _linear in book.spine]
    ordered = []
    for sid in spine_ids:
        for item in items:
            if item.get_id() == sid:
                ordered.append(item)
                break
    if not ordered:
        ordered = items

    chapters = []
    for idx, item in enumerate(ordered):
        soup = BeautifulSoup(item.get_content(), "xml")

        title_tag = soup.find(["h1", "h2", "h3"])
        title = title_tag.get_text(strip=True) if title_tag else f"Chapter {idx + 1}"

        body = soup.find("body")
        if not body:
            continue

        paragraphs = []
        p_idx = 0
        for tag in body.find_all(["p", "div"]):
            text = tag.get_text(separator="", strip=True)
            if not _is_valid_paragraph(text):
                continue
            paragraphs.append({
                "paragraph_idx": p_idx,
                "text": text,
                "text_hash": _text_hash(text),
                "char_count": len(text),
                "token_estimate": _estimate_tokens(text),
            })
            p_idx += 1

        if not paragraphs:
            continue

        total_tokens = sum(p["token_estimate"] for p in paragraphs)
        chapters.append({
            "idx": len(chapters),
            "title": title,
            "paragraphs": paragraphs,
            "paragraph_count": len(paragraphs),
            "token_estimate": total_tokens,
        })

    return chapters


async def import_epub(
    db: aiosqlite.Connection,
    epub_path: str,
    books_dir: Path,
) -> dict[str, Any]:
    start_ms = time.time() * 1000

    book = epub.read_epub(epub_path)

    title = book.get_metadata("DC", "title")
    title_str = title[0][0] if title else Path(epub_path).stem

    author = book.get_metadata("DC", "creator")
    author_str = author[0][0] if author else None

    file_hash = hashlib.sha256(Path(epub_path).read_bytes()).hexdigest()[:16]

    existing = await book_repo.get_book_by_hash(db, file_hash)
    if existing is not None:
        logger.info("book.import.duplicate", extra={"event": "book.import.duplicate", "fields": {"book_id": existing["id"], "file_hash": file_hash}})
        prog = await progress_repo.get_progress(db, existing["id"])
        lp = prog if prog.get("updated_at") else None
        return {
            "book": {
                "id": existing["id"],
                "title": existing["title"],
                "author": existing.get("author"),
                "cover_url": f"/api/books/{existing['id']}/cover" if existing.get("cover_path") else None,
                "total_chapters": existing["total_chapters"],
                "imported_at": existing.get("imported_at", ""),
                "updated_at": existing["updated_at"],
                "last_progress": lp,
            },
            "first_chapter": None,
            "import_stats": {
                "duplicate": True,
                "existing_book_id": existing["id"],
                "chapter_count": existing["total_chapters"],
                "paragraph_count": 0,
                "char_count": 0,
                "token_estimate": 0,
                "duration_ms": 0,
            },
        }

    dest_dir = books_dir / file_hash
    dest_dir.mkdir(parents=True, exist_ok=True)

    dest_file = dest_dir / Path(epub_path).name
    if not dest_file.exists():
        shutil.copy2(epub_path, dest_file)

    cover_path = _extract_cover(book, dest_dir)

    parsed_chapters = _parse_chapters(book)
    if not parsed_chapters:
        from ..errors import AppError
        raise AppError("invalid_epub", "No readable chapters found in epub")

    book_record = await book_repo.create_book(
        db,
        title=title_str,
        author=author_str,
        file_hash=file_hash,
        file_path=str(dest_file),
        cover_path=cover_path,
        total_chapters=len(parsed_chapters),
    )
    book_id = book_record["id"]

    total_paragraphs = 0
    total_chars = 0
    total_tokens = 0

    for ch in parsed_chapters:
        raw_text = "\n".join(p["text"] for p in ch["paragraphs"])
        await chapter_repo.create_chapter(
            db,
            book_id=book_id,
            idx=ch["idx"],
            title=ch["title"],
            raw_text=raw_text,
            paragraph_count=ch["paragraph_count"],
            token_estimate=ch["token_estimate"],
        )
        await paragraph_repo.bulk_insert_paragraphs(
            db, book_id, ch["idx"], ch["paragraphs"]
        )
        total_paragraphs += ch["paragraph_count"]
        total_chars += sum(p["char_count"] for p in ch["paragraphs"])
        total_tokens += ch["token_estimate"]

    duration_ms = time.time() * 1000 - start_ms

    logger.info(
        "book.import.done",
        extra={
            "event": "book.import.done",
            "fields": {
                "book_id": book_id,
                "chapters": len(parsed_chapters),
                "paragraphs": total_paragraphs,
                "tokens": total_tokens,
                "duration_ms": int(duration_ms),
            },
        },
    )

    first_ch = parsed_chapters[0] if parsed_chapters else None
    return {
        "book": {
            "id": book_id,
            "title": title_str,
            "author": author_str,
            "cover_url": f"/api/books/{book_id}/cover" if cover_path else None,
            "total_chapters": len(parsed_chapters),
            "imported_at": book_record["imported_at"],
            "updated_at": book_record["updated_at"],
            "last_progress": None,
        },
        "first_chapter": {
            "book_id": book_id,
            "idx": first_ch["idx"],
            "title": first_ch["title"],
            "paragraph_count": first_ch["paragraph_count"],
            "token_estimate": first_ch["token_estimate"],
        } if first_ch else None,
        "import_stats": {
            "chapter_count": len(parsed_chapters),
            "paragraph_count": total_paragraphs,
            "char_count": total_chars,
            "token_estimate": total_tokens,
            "duration_ms": int(duration_ms),
        },
    }
