# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Vibe Reader Mini — a local single-user novel reader (epub import, chapter reading, progress tracking, AI companion reading). FastAPI backend (Python), React/TypeScript frontend (Vite). Chinese-language documentation and UI.

## Commands

All commands assume working from the indicated subdirectory.

### Backend (from `backend/`)

```bash
uv sync --extra dev                    # install deps (editable, registers CLI entry points)
uv run vibe-reader                     # start dev server (reload, 127.0.0.1:8000)
uv run ruff check .                    # lint
uv run ruff format .                   # format
uv run pytest                          # unit/module tests (no live backend)
```

Single test: `uv run pytest tests/path/to/test.py::test_name`

System verification lives in the sibling `verify/` project, not in backend
tests:

```bash
cd ../verify
uv sync --extra dev
uv run ruff check
uv run pytest
uv run vibe-verify validate-corpus corpus/hongloumeng_manifest.toml
uv run vibe-verify run --config configs/r1_a4_stub.toml
```

### Frontend (from `frontend/`)

```bash
npm install     # install deps
npm run dev     # dev server (proxies /api to backend at 127.0.0.1:8000)
npm run build   # TypeScript compile + Vite build (output to dist/)
npm run lint    # ESLint
```

### Unified deployment

Build frontend first, then backend serves static files from `frontend/dist/` at `/`:
```bash
cd frontend && npm run build
cd ../backend && set -a && source .env && set +a && uv run vibe-reader
```

## Architecture

### Backend (`backend/app/`)

Layered: **routers → services → repos**. No ORM — raw SQL via `aiosqlite`.

- `main.py` — `create_app()` factory, CLI entry point, startup DB init, static file serving
- `config.py` — settings from env vars + `~/.vibe_reader/config.toml` (env overrides TOML)
- `db.py` — SQLite schema (9 tables), migrations, WAL mode
- `routers/` — HTTP endpoints: health, books (import/CRUD), chapters, progress (GET/PUT with dedup), events (SSE)
- `services/` — business logic: `import_service.py` (epub parsing via ebooklib + BeautifulSoup, idempotent import), `llm_ping.py`
- `repos/` — data access: books, chapters, paragraphs, progress, windows, comments, jobs, chat
- `errors.py` — structured error handling (`AppError` + error map)
- `middleware.py` — request context (request_id, trace_id, verify IDs)
- `observability.py` — structured JSON logging, OpenTelemetry integration

Data directory: `~/.vibe_reader/` (configurable via `VIBE_READER_DATA_DIR`). Contains SQLite DB, book files, config.toml, logs.

### Frontend (`frontend/src/`)

Plain React SPA, no routing library or state management library. Two views (library/reader) managed by state in `App.tsx`.

- `App.tsx` — root component, view switching
- `BookList.tsx` — book listing + import
- `ImportDropZone.tsx` — drag-and-drop epub upload
- `ReaderView.tsx` — scrollable paragraph reader with debounced progress (800ms) and comment bubbles
- `ChapterNav.tsx` — chapter sidebar navigation
- `api/client.ts` — typed fetch wrapper for `/api/*`

Vite dev server proxies `/api` to `http://127.0.0.1:8000`.

### System Verification (`verify/`)

Independent black-box HTTP/SSE verification against a live backend. It does not
import product `app/` code.

- CLI: `vibe-verify` from `verify/pyproject.toml`
- Layout: `runner.py`, `driver.py`, `provider.py`, `evidence.py`,
  `artifact_store.py`, `corpus.py`, `assertions.py`, `scenario.py`, and
  `scenarios/`
- Current built-in scenario: `R1_A4_full_flow` for import, reading, comments,
  compaction, and post-compaction streaming chat
- Configs: `verify/configs/r1_a4_stub.toml` and backend config templates
- Output: `verify/verify_runs/<run_id>/` with manifest, evidence, stub journal,
  reports, audit files when enabled, and failure snapshots
- Corpus: epub files under `verify/corpus/books/`, declared in
  `verify/corpus/*_manifest.toml`
- Design docs: root `docs/verify/`

### Design docs

Specifications and plans in root `docs/`: `spec_mini.md`, `task_mini.md`,
`spec_interface.md`, `spec_telemetry.md`, and `docs/verify/`.

## Conventions

- Commit messages: conventional commits (`feat(slice1):`, `chore(backend):`, etc.)
- Python linting: ruff (line-length 88, target py311, max complexity 12, max statements 60)
- pytest: backend uses `asyncio_mode = "auto"`; verify framework tests run from
  `verify/` with normal pytest
- Backend manual runs can load `backend/.env` with
  `set -a && source .env && set +a`; shell env vars override `.env` values
