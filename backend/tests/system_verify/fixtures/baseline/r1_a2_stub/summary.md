# Vibe Reader Verify Summary

run_id: 20260524T044338Z_d002f50e
git_commit: 036f1d64163544a9c4cb5f6d57fe75f69f08b3be
suite: real-happy-path
llm_mode: stub
param_set: r1_a2_stub
stub_profile: mvp_default
real_llm_calls: 0
model: deepseek-v4-flash
corpus: 5b8a4e552e42c7769da068ec8eec87e760a022a11bcfa34c155b55d81e0b11a4
started_at: 2026-05-24T04:43:38Z
ended_at: 2026-05-24T04:43:51Z

## Result

pass

## Functional Checks

- import: not_run
- progress: not_run
- comment: not_run
- scroll_jump: not_run
- long_context: not_run
- compaction: not_run
- real_happy_path: pass

## Latency

| metric | count | p50 | p90 | p95 | max |
|---|---:|---:|---:|---:|---:|
| api.get.duration_ms | 1 | 2.3 | 2.3 | 2.3 | 2.3 |
| api.put.duration_ms | 38 | 10.8 | 21.5 | 41.3 | 461.3 |
| comment.agent_run_ms.max | 1 | 417.5 | 417.5 | 417.5 | 417.5 |
| comment.agent_run_ms.p50 | 1 | 417.5 | 417.5 | 417.5 | 417.5 |
| comment.agent_run_ms.p90 | 1 | 417.5 | 417.5 | 417.5 | 417.5 |
| comment.agent_run_ms.p95 | 1 | 417.5 | 417.5 | 417.5 | 417.5 |
| comment.job_queue_wait_ms | 1 | 0.0 | 0.0 | 0.0 | 0.0 |
| comment.job_run_ms | 1 | 1000.0 | 1000.0 | 1000.0 | 1000.0 |
| progress.update.duration_ms | 38 | 10.8 | 21.5 | 41.3 | 461.3 |

## Tokens

| agent | requests | input | output | total | max_input |
|---|---:|---:|---:|---:|---:|
| paragraph_comment | 1 | 4050 | 0 | 4050 | 4050 |

## Real LLM Phase Coverage

- A2_comments: True
- A3_compaction: False
- A4_full_flow: False

usage_source: estimate

## Comment Density

- actual: 0.0
- soft_min: 0.05
- stat_start: 0.0
- stat_end: 80.0

## Audit Samples

- comments: 6
- compaction_summaries: 0
- real_comments: 0
- window_status: 0
- agent_invocations: 1
- agent_reports: 1
- audit_safety: 1

## Failures

See reports/failures.md
