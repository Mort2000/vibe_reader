# Vibe Reader Verify

Independent Python toolkit for black-box verification of the Vibe Reader
backend. It drives public HTTP / SSE APIs, records evidence, serves deterministic
OpenAI-compatible stub responses, and writes auditable run artifacts without
importing backend product code.

Run commands from this directory (`workspace/verify`). Paths in the bundled TOML
configs are resolved relative to the current working directory.

## Architecture

The framework architecture follows the code under `src/vibe_verify/`:

- `cli.py`: command entrypoint, backend launch wiring, stub/corpus commands.
- `run_config.py`: TOML run config loading, CLI override merge, RunSpec mapping.
- `runner.py`: run lifecycle, provider setup, scenario selection, budget guards.
- `provider.py`: stub sidecar and real-provider environment preparation.
- `driver.py`: product-facing app, book, user, HTTP, and SSE facades.
- `scenario.py` and `scenarios/`: scenario registry and built-in user scripts.
- `corpus.py`: authorized corpus manifest validation and probe resolution.
- `evidence.py` and `artifact_store.py`: evidence collection, summaries, reports.
- `assertions.py`: shared observable-result assertions.

Scenario authoring notes live in
[`src/vibe_verify/scenarios/README.md`](src/vibe_verify/scenarios/README.md).
Corpus and test-directory setup guidance lives in
[`corpus/README.md`](corpus/README.md).

## Preparation

```bash
uv sync --extra dev
uv run ruff check
uv run pytest
uv run vibe-verify validate-corpus corpus/<local_manifest>.toml
```

Corpus manifests and book files under `corpus/` are local test inputs and are
not tracked. Prepare them before running integration scenarios. Real model runs
must use a corpus probe with `allow_real_llm = true` and a positive cost budget.

## Running A Scenario

The default local stub integration config is
[`configs/r1_a4_stub.toml`](configs/r1_a4_stub.toml). It contains the scenario
selection, corpus manifest, backend command, backend environment, backend test
config template, scenario parameters, and verification budgets. Before launching
the backend, verify copies `backend.config_file` to
`VIBE_READER_DATA_DIR/config.toml`. Its `corpus` value points to a local
untracked manifest path; create that manifest first or copy the config and point
it to your own local manifest.

```bash
uv run vibe-verify run --config configs/r1_a4_stub.toml
```

Useful one-off overrides:

```bash
uv run vibe-verify run --config configs/r1_a4_stub.toml --run-id local_r1_a4
uv run vibe-verify run --config configs/r1_a4_stub.toml --max-calls 80
```

Use `audit = true` in a copied config only when full prompt and provider records
are needed. The default artifact output is sanitized: user messages, model text,
prompt bodies, OTEL attributes, and provider request bodies are represented by
hashes and summaries outside audit mode.

## Report Structure

Each run writes to `verify_runs/<run_id>/`:

- `run_manifest.json`: run identity, profile, corpus, budget, timing, token
  totals, and evidence gaps.
- `reports/summary.md`: human-readable pass/fail summary, key metrics, evidence
  gaps, and artifact index.
- `evidence/api.ndjson`: sanitized backend API calls.
- `evidence/sse.ndjson`: sanitized SSE stream events.
- `evidence/user_interactions.ndjson`: user script actions.
- `evidence/agent_invocations.ndjson`: sanitized LLM invocation summaries and
  token estimates.
- `stub/journal.ndjson`: sanitized stub-provider request / response summaries.
- `audit/`: full prompt and provider records when audit mode is enabled.
- `failure/snapshot.json`: failure context when a scenario fails.
