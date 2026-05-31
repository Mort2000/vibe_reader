# Corpus Parameters And Test Directory

This directory is for local corpus preparation. Only this `README.md` should be
tracked. Corpus manifests, generated files, and book files under this directory
are local test inputs and must not be committed.

## Corpus Manifest

Register every test book in a local manifest under `corpus/`:

- `alias`: stable book id used in reports.
- `path`: EPUB path, normally under `corpus/books/`.
- `license`: explicit authorization note for test use.
- `sha256`: expected file digest; validate it before every run.
- `language`: corpus language, for report readability.
- `min_chapters`, `min_paragraphs`, `min_chars`: coarse guards against using the
  wrong or truncated file.

Each scenario should resolve positions through a probe:

- `purposes`: scenario capability labels, for example `happy_path_current`.
- `start_chapter_idx`, `start_paragraph_idx`: where the scripted reader starts.
- `chapter_idx`, `paragraph_idx`: stable focus location used by the scenario.
- `requires_compaction`: set when the corpus is meant to exercise context
  compaction.
- `test_compaction_min_source_tokens` and
  `test_compaction_min_source_paragraphs`: minimum source material expected
  before compaction can be considered meaningful.
- `allow_real_llm`: keep false unless the text is approved for external model
  calls and the run config has a real cost budget.

Validate after every corpus or manifest change:

```bash
uv run vibe-verify validate-corpus corpus/<manifest>.toml
```

## Scenario Parameters

Tune scenario parameters in the run config, not in the user script.

- `read_batch_size`: paragraphs advanced by one user reading action. Use smaller
  values for dense or short chapters so comment windows are observable; use
  larger values for long chapters to reach compaction in reasonable time.
- `read_batches`: maximum number of read batches before waiting for compaction.
  Set it high enough to cover the distance from `start_paragraph_idx` to the
  expected compaction region.
- `min_comment_windows`: minimum distinct comment windows expected before
  compaction. It must not exceed the number of windows the selected corpus can
  naturally produce.
- `post_compaction_comment_windows`: unread windows expected after compaction.
  Ensure the probe leaves enough paragraphs after the compaction point.
- `min_chat_turns`: number of follow-up chat turns. It must not exceed the
  configured question list.
- `max_wait_comment_s`: backend wait budget for comment generation. Stub runs can
  be moderate; real LLM runs need room for provider latency.
- `max_wait_compaction_s`: backend wait budget for compaction. Increase for long
  corpora or real providers.
- `max_calls`, `max_tokens`, `max_duration_s`, `max_cost_usd`: verification
  safety limits. Stub runs usually need a call budget near expected Agent calls
  plus a margin; real runs must set a positive cost budget.

For a new corpus, start with conservative small read batches, run in stub mode,
inspect `run_manifest.json` and `evidence/agent_invocations.ndjson`, then adjust
only the parameters that caused false waits or budget pressure.

## Test Directory

Use an isolated backend data directory for each integration run family. Do not
reuse a developer or production backend data directory.

1. Copy a run config from `configs/`, for example:

   ```bash
   cp configs/r1_a4_stub.toml configs/local_r1_a4_stub.toml
   ```

2. In the copied config, set:

   ```toml
   corpus = "corpus/<local_manifest>.toml"

   [backend]
   config_file = "configs/backend_r1_a4_stub.toml"

   [backend.env]
   VIBE_READER_DATA_DIR = "/tmp/vibe_reader_verify_local"
   VIBE_READER_VERIFY_MODE = "1"
   VIBE_READER_OBSERVABILITY_ENABLED = "0"
   ```

3. Keep `backend.command` and `backend.cwd` when verify should launch the
   backend. When `backend.config_file` is set, verify copies that file to
   `VIBE_READER_DATA_DIR/config.toml` before backend startup. If a backend is
   already running, use a copied config with an empty `backend.command`, prepare
   its data directory yourself, and point `target_url` to that process.

4. Run from `workspace/verify`:

   ```bash
   uv run vibe-verify run --config configs/local_r1_a4_stub.toml
   ```

Artifacts are written under `artifact_root`, normally `verify_runs/`. The
backend data directory and artifact directory serve different purposes: the
first is product state, the second is verification evidence.
