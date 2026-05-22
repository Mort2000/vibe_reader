from __future__ import annotations

import pathlib

import aiosqlite

_SCHEMA = """
CREATE TABLE IF NOT EXISTS books (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    author TEXT,
    file_hash TEXT NOT NULL,
    file_path TEXT NOT NULL,
    cover_path TEXT,
    total_chapters INTEGER NOT NULL DEFAULT 0,
    imported_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chapters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    idx INTEGER NOT NULL,
    title TEXT NOT NULL,
    raw_text TEXT NOT NULL DEFAULT '',
    paragraph_count INTEGER NOT NULL DEFAULT 0,
    token_estimate INTEGER NOT NULL DEFAULT 0,
    analysis_status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(book_id, idx)
);

CREATE TABLE IF NOT EXISTS paragraphs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    chapter_idx INTEGER NOT NULL,
    paragraph_idx INTEGER NOT NULL,
    text TEXT NOT NULL,
    text_hash TEXT NOT NULL,
    char_count INTEGER NOT NULL DEFAULT 0,
    token_estimate INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    UNIQUE(book_id, chapter_idx, paragraph_idx)
);

CREATE TABLE IF NOT EXISTS reading_progress (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL UNIQUE REFERENCES books(id) ON DELETE CASCADE,
    chapter_idx INTEGER NOT NULL DEFAULT 0,
    paragraph_idx INTEGER NOT NULL DEFAULT 0,
    scroll_pct REAL NOT NULL DEFAULT 0.0,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS reading_windows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    chapter_idx INTEGER NOT NULL,
    window_seq INTEGER NOT NULL,
    start_paragraph_idx INTEGER NOT NULL,
    end_paragraph_idx INTEGER NOT NULL,
    focus_start_paragraph_idx INTEGER NOT NULL,
    focus_end_paragraph_idx INTEGER NOT NULL,
    assistant_frontier_paragraph_idx INTEGER NOT NULL,
    text_hash TEXT NOT NULL DEFAULT '',
    context_hash TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    UNIQUE(book_id, chapter_idx, window_seq)
);

CREATE TABLE IF NOT EXISTS paragraph_comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    chapter_idx INTEGER NOT NULL,
    paragraph_idx INTEGER NOT NULL,
    window_id INTEGER NOT NULL REFERENCES reading_windows(id) ON DELETE CASCADE,
    comment TEXT NOT NULL,
    comment_type TEXT NOT NULL DEFAULT 'observation',
    status TEXT NOT NULL DEFAULT 'active',
    trace_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rolling_context_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    chapter_idx INTEGER NOT NULL,
    up_to_paragraph_idx INTEGER NOT NULL,
    source_window_id INTEGER REFERENCES reading_windows(id),
    summary TEXT NOT NULL DEFAULT '',
    comment_digest TEXT NOT NULL DEFAULT '',
    chat_digest TEXT NOT NULL DEFAULT '',
    anchor_excerpts_json TEXT NOT NULL DEFAULT '[]',
    open_questions_json TEXT NOT NULL DEFAULT '[]',
    token_estimate INTEGER NOT NULL DEFAULT 0,
    context_version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chat_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    chapter_idx INTEGER NOT NULL,
    title TEXT,
    last_paragraph_idx INTEGER NOT NULL DEFAULT 0,
    message_history_json TEXT,
    message_history_updated_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chat_turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    chapter_idx INTEGER NOT NULL,
    paragraph_idx INTEGER NOT NULL,
    user_msg TEXT NOT NULL,
    ai_msg TEXT,
    status TEXT NOT NULL DEFAULT 'streaming',
    tokens_in INTEGER,
    tokens_out INTEGER,
    trace_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ai_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_type TEXT NOT NULL,
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    chapter_idx INTEGER NOT NULL,
    window_id INTEGER REFERENCES reading_windows(id),
    status TEXT NOT NULL DEFAULT 'pending',
    attempt_count INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    trace_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_paragraphs_book_chapter
    ON paragraphs(book_id, chapter_idx);

CREATE INDEX IF NOT EXISTS idx_paragraph_comments_book_chapter
    ON paragraph_comments(book_id, chapter_idx, paragraph_idx);

CREATE INDEX IF NOT EXISTS idx_reading_windows_book_chapter
    ON reading_windows(book_id, chapter_idx);

CREATE INDEX IF NOT EXISTS idx_rolling_snapshots_book_chapter
    ON rolling_context_snapshots(book_id, chapter_idx, up_to_paragraph_idx);

CREATE INDEX IF NOT EXISTS idx_chat_sessions_book_chapter
    ON chat_sessions(book_id, chapter_idx);

CREATE INDEX IF NOT EXISTS idx_chat_turns_session
    ON chat_turns(session_id);

CREATE INDEX IF NOT EXISTS idx_ai_jobs_status
    ON ai_jobs(status);

CREATE INDEX IF NOT EXISTS idx_ai_jobs_book_chapter
    ON ai_jobs(book_id, chapter_idx);
"""


_MIGRATIONS = [
    ("books", "author", "TEXT"),
    ("chapters", "token_estimate", "INTEGER NOT NULL DEFAULT 0"),
]


async def _run_migrations(db: aiosqlite.Connection) -> None:
    for table, column, col_type in _MIGRATIONS:
        try:
            cur = await db.execute(f"SELECT {column} FROM {table} LIMIT 0")
            await cur.close()
        except Exception:
            await db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
    await db.commit()


async def init_db(db_path: pathlib.Path) -> aiosqlite.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = await aiosqlite.connect(str(db_path))
    db.row_factory = aiosqlite.Row
    await db.executescript(_SCHEMA)
    await _run_migrations(db)
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA foreign_keys=ON")
    await db.commit()
    return db
