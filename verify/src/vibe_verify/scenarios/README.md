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
  runtime checks, job status, metrics, or backend-recorded Agent runs. Do not
  mix those calls into user-facing `app` actions.
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
