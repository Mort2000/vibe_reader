# Verification Scenarios

This directory contains built-in verification scenarios. A scenario is a user
script: it describes how a reader uses the product through public backend APIs
and asserts the externally visible outcome from collected evidence.

## Writing Scenarios

- Register each scenario from `__init__.py` with a stable id, suite, profile,
  corpus purpose, and a short description.
- Keep the script readable as a behavior flow. Prefer `context.user`,
  `context.app`, and `BookFacade` helpers over low-level HTTP calls.
- Use `context.observability` only for read-only verify-mode evidence such as
  formal runtime checks, the temporary S0 LLM ping compatibility probe, or
  backend-recorded Agent runs in `backend_agent_evidence=true` real/audit
  profiles. Prefer standard runner evidence for assertions when available, and
  do not mix observability calls into user-facing `app` actions.
- Put scenario knobs in a small policy object and map CLI / run params into that
  policy at the script boundary.
- Resolve corpus-dependent positions through manifest probes rather than
  hard-coding book-specific assumptions in the script body.
- Keep assertions focused on observable API, SSE, artifact, and LLM invocation
  evidence. Shared assertion logic belongs outside this directory.

## Testing Policy

Do not write unit tests for scenario files in this directory. Scenarios are
black-box integration specifications; validate them with automated scenario runs
against a stub or real backend and the generated pass/fail artifacts.

This policy is about user-script files, not shared framework code. Registration,
driver behavior, provider behavior, evidence recording, corpus validation, and
generic assertion helpers should continue to have normal unit tests outside this
directory. Do not mock backend internals or duplicate backend algorithms to make
a scenario unit test pass.

## Built-in Scenario IDs

- `S0_environment_connectivity`
- `S1_import_book`
- `S2_continuous_reading_comments`
- `S3_fast_scroll`
- `S4_context_compaction`
- `S5_direct_chat`
- `S6_followup_chat`
- `R1_A4_full_flow`

## Shared Helpers

S0-S6 each live in their own `sN_*.py` script. Shared policy parsing, probe
opening, chat-turn helpers, progress checks, and compaction waiting helpers live
in `common.py`; pure reusable assertions stay in `vibe_verify.assertions`.
