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

CREATE TABLE IF NOT EXISTS original_text_chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    chapter_idx INTEGER NOT NULL,
    chunk_seq INTEGER NOT NULL,
    start_paragraph_idx INTEGER NOT NULL,
    end_paragraph_idx INTEGER NOT NULL,
    token_estimate INTEGER NOT NULL DEFAULT 0,
    char_count INTEGER NOT NULL DEFAULT 0,
    text_hash TEXT NOT NULL DEFAULT '',
    rendered_hash TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    reclaimed_by_summary_id INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(book_id, chapter_idx, chunk_seq)
);

CREATE TABLE IF NOT EXISTS chapter_compressed_summaries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    chapter_idx INTEGER NOT NULL,
    covered_start_paragraph_idx INTEGER NOT NULL,
    covered_end_paragraph_idx INTEGER NOT NULL,
    source_chunk_ids_json TEXT NOT NULL DEFAULT '[]',
    source_text_hash TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    anchor_excerpts_json TEXT NOT NULL DEFAULT '[]',
    token_estimate INTEGER NOT NULL DEFAULT 0,
    context_version INTEGER NOT NULL DEFAULT 1,
    compaction_epoch INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS book_context_states (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL UNIQUE REFERENCES books(id) ON DELETE CASCADE,
    active_chapter_idx INTEGER NOT NULL DEFAULT 0,
    reading_paragraph_idx INTEGER NOT NULL DEFAULT 0,
    assistant_frontier_chapter_idx INTEGER NOT NULL DEFAULT 0,
    assistant_frontier_paragraph_idx INTEGER NOT NULL DEFAULT 0,
    context_frontier_chapter_idx INTEGER NOT NULL DEFAULT 0,
    context_frontier_paragraph_idx INTEGER NOT NULL DEFAULT 0,
    latest_summary_id INTEGER,
    live_l2_chunk_ids_json TEXT,
    compaction_epoch INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'idle',
    running_job_id INTEGER,
    pending_chapter_idx INTEGER,
    pending_paragraph_idx INTEGER,
    pending_scroll_pct REAL,
    pending_assistant_frontier_chapter_idx INTEGER,
    pending_assistant_frontier_paragraph_idx INTEGER,
    pending_context_jump_chars INTEGER,
    pending_updated_at TEXT,
    emergency_overflow_used INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
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

CREATE INDEX IF NOT EXISTS idx_original_text_chunks_book_chapter
    ON original_text_chunks(book_id, chapter_idx);

CREATE INDEX IF NOT EXISTS idx_chapter_summaries_book_chapter
    ON chapter_compressed_summaries(book_id, chapter_idx, covered_end_paragraph_idx);

CREATE INDEX IF NOT EXISTS idx_book_context_states_book
    ON book_context_states(book_id);

CREATE INDEX IF NOT EXISTS idx_chat_sessions_book_chapter
    ON chat_sessions(book_id, chapter_idx);

CREATE INDEX IF NOT EXISTS idx_chat_turns_session
    ON chat_turns(session_id);

CREATE INDEX IF NOT EXISTS idx_ai_jobs_status
    ON ai_jobs(status);

CREATE INDEX IF NOT EXISTS idx_ai_jobs_book_chapter
    ON ai_jobs(book_id, chapter_idx);

CREATE INDEX IF NOT EXISTS idx_ai_jobs_trace_id
    ON ai_jobs(trace_id);

CREATE INDEX IF NOT EXISTS idx_token_calibrations_lookup
    ON token_estimation_calibrations(model, prompt_version, language_profile);

CREATE TABLE IF NOT EXISTS verify_agent_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_id TEXT NOT NULL UNIQUE,
    request_id TEXT NOT NULL DEFAULT '',
    verify_run_id TEXT NOT NULL DEFAULT '',
    verify_scenario_id TEXT NOT NULL DEFAULT '',
    verify_step_id TEXT NOT NULL DEFAULT '',
    job_id INTEGER,
    window_id INTEGER,
    book_id INTEGER,
    chapter_idx INTEGER,
    agent_name TEXT NOT NULL,
    duration_ms REAL NOT NULL DEFAULT 0,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cached_input_tokens INTEGER,
    no_call INTEGER NOT NULL DEFAULT 0,
    tool_call_count INTEGER NOT NULL DEFAULT 0,
    valid_count INTEGER NOT NULL DEFAULT 0,
    validation_failed_count INTEGER NOT NULL DEFAULT 0,
    discarded_count INTEGER NOT NULL DEFAULT 0,
    discarded_by_reason_json TEXT NOT NULL DEFAULT '{}',
    candidate_lookup_count INTEGER,
    prompt_version TEXT NOT NULL DEFAULT '',
    context_hash TEXT NOT NULL DEFAULT '',
    comment_density_actual REAL,
    comment_density_soft_min REAL,
    density_stat_start INTEGER,
    density_stat_end INTEGER,
    status TEXT NOT NULL DEFAULT 'ok',
    error TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS token_estimation_calibrations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    language_profile TEXT NOT NULL,
    bootstrap_calibration_ratio REAL NOT NULL DEFAULT 1.0,
    rolling_p50_ratio REAL NOT NULL DEFAULT 1.0,
    rolling_p95_ratio REAL NOT NULL DEFAULT 1.0,
    sample_count INTEGER NOT NULL DEFAULT 0,
    window_size INTEGER NOT NULL DEFAULT 50,
    updated_at TEXT NOT NULL,
    UNIQUE(model, prompt_version, language_profile)
);

CREATE INDEX IF NOT EXISTS idx_verify_agent_runs_run_id
    ON verify_agent_runs(verify_run_id);

CREATE INDEX IF NOT EXISTS idx_verify_agent_runs_scenario
    ON verify_agent_runs(verify_run_id, verify_scenario_id);
"""


_MIGRATIONS = [
    ("books", "author", "TEXT"),
    ("chapters", "token_estimate", "INTEGER NOT NULL DEFAULT 0"),
    ("verify_agent_runs", "invocation_id", "TEXT NOT NULL DEFAULT ''"),
    ("verify_agent_runs", "interaction_json", "TEXT NOT NULL DEFAULT ''"),
    ("verify_agent_runs", "interaction_path", "TEXT NOT NULL DEFAULT ''"),
    ("book_context_states", "emergency_overflow_used", "INTEGER NOT NULL DEFAULT 0"),
    ("original_text_chunks", "raw_token_estimate", "INTEGER NOT NULL DEFAULT 0"),
    ("original_text_chunks", "estimator_model", "TEXT NOT NULL DEFAULT ''"),
    ("original_text_chunks", "estimator_version", "TEXT NOT NULL DEFAULT ''"),
    (
        "original_text_chunks",
        "estimator_calibration_ratio",
        "REAL NOT NULL DEFAULT 1.0",
    ),
    ("original_text_chunks", "chunking_version", "TEXT NOT NULL DEFAULT ''"),
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
