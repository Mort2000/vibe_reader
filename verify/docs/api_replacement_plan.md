# API Replacement Plan

This plan keeps Verify aligned with `docs/verify/purpose.md`: user behavior is
driven through formal product APIs, gray-box access is read-only, and backend
test-only endpoints are either removed from the default path or isolated as
temporary compatibility.

## Phase 1: Runtime Probe

- Replace `GET /api/verify/runtime` with the formal `GET /api/runtime`.
- Keep validation limited to runtime readiness and model configuration.
- Acceptance: no verify runtime endpoint is used by `workspace/verify`.

## Phase 2: Async Job Observation

- Replace `GET /api/verify/jobs` compaction polling with the formal
  `GET /api/events` SSE stream.
- User scripts should observe `context.compacted` / `context.failed` events and
  product-visible state such as windows, comments, and chat responses.
- Acceptance: R1 full flow no longer calls the verify jobs endpoint.

## Phase 3: Agent Evidence Boundary

- Remove unused verify metrics access from the Verify codebase.
- Use local stub evidence as the default source for stub-mode Agent calls.
- Keep backend Agent-run import only as an explicit compatibility path for
  real/audit evidence until a standard OTEL or audit sink replaces it. Stub mode
  must use local stub evidence by default.
- Acceptance: default stub runs do not depend on `/api/verify/agent-runs`; the
  remaining verify endpoint dependency is isolated and documented.
