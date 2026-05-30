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
uv run pytest -m "not system and not real_llm"   # unit tests (no live backend)
```

Single test: `uv run pytest tests/path/to/test.py::test_name`

System verification (see `tests/README.md` for full guide):

```bash
# CLI
uv run vibe-verify prepare --corpus tests/corpus/manifest.toml
uv run vibe-verify run --suite mvp --spawn-backend              # stub S0–S4
uv run vibe-verify run --suite real-happy-path --param-set r1_a2_real --llm-mode real --real-coverage A2

# pytest — stub integration (auto-starts AIMock + backend)
uv run pytest tests/system_verify/test_scenarios.py -m "system and not real_llm" --spawn-backend --require-integration

# pytest — real LLM (start backend manually with .env first)
uv run pytest tests/system_verify/test_scenarios.py::test_r1_happy_path_a2_real \
  -m real_llm --llm-mode real --param-set r1_a2_real --require-integration
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
cd ../backend && set -a && source ../.env && set +a && uv run vibe-reader
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

### System Verification (`backend/tests/system_verify/`)

Black-box HTTP/SSE testing against a live backend. Does not import product `app/` code.

- CLI: `vibe-verify` (registered in `pyproject.toml` scripts)
- Layered layout: `core/` (RunSpec, Orchestrator, ScenarioContext), `flows/` (step logic), `assertions/`, `modes/` (stub AIMock / real LLM), `profiles/`, `scenarios/` (thin entries + `registry.py`)
- Scenarios: S0–S4 (MVP stub suite), R1_A2_comments, R1_A3_compaction (real-happy-path)
- Param sets: `tests/corpus/param_sets/` (`mvp`, `r1_a2_{stub|real}`, `r1_a3_{stub|real}`)
- Output: `backend/verify_runs/<run_id>/` with JSON/NDJSON manifests and V-16 reports
- Corpus: epub files in `tests/corpus/books/` (gitignored), declared in `tests/corpus/manifest.toml`
- Baseline: `fixtures/baseline/` + `test_characterization.py` guard refactor regressions
- Security: API key leak scanning in output files

### Design docs

Specifications in sibling repo `vibe_reader_doc/`: `spec_mini.md`, `task_mini.md`, `spec_interface.md`, `spec_telemetry.md`.

## Conventions

- Commit messages: conventional commits (`feat(slice1):`, `chore(backend):`, etc.)
- Python linting: ruff (line-length 88, target py311, max complexity 12, max statements 60)
- pytest: `asyncio_mode = "auto"`, markers `system_verify` / `system` (stub integration) / `real_llm`
- `.env` is auto-loaded by `conftest.py`; shell env vars override `.env` values
- Backend uses `set -a && source ../.env && set +a` pattern for env loading in manual runs
