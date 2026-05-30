# Vibe Reader Verify Summary

run_id: 20260524T044442Z_f184b883
git_commit: 036f1d64163544a9c4cb5f6d57fe75f69f08b3be
suite: real-happy-path
llm_mode: stub
param_set: r1_a3_stub
stub_profile: mvp_default
real_llm_calls: 0
model: deepseek-v4-flash
corpus: 5b8a4e552e42c7769da068ec8eec87e760a022a11bcfa34c155b55d81e0b11a4
started_at: 2026-05-24T04:44:42Z
ended_at: 2026-05-24T04:45:13Z

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
| api.get.duration_ms | 1 | 2.0 | 2.0 | 2.0 | 2.0 |
| api.put.duration_ms | 14 | 23.3 | 44.0 | 48.2 | 53.9 |
| comment.agent_run_ms.max | 1 | 345.7 | 345.7 | 345.7 | 345.7 |
| comment.agent_run_ms.p50 | 1 | 10.8 | 10.8 | 10.8 | 10.8 |
| comment.agent_run_ms.p90 | 1 | 345.7 | 345.7 | 345.7 | 345.7 |
| comment.agent_run_ms.p95 | 1 | 345.7 | 345.7 | 345.7 | 345.7 |
| progress.update.duration_ms | 14 | 23.3 | 44.0 | 48.2 | 53.9 |

## Tokens

| agent | requests | input | output | total | max_input |
|---|---:|---:|---:|---:|---:|
| paragraph_comment | 1 | 12939 | 0 | 12939 | 12939 |

## Real LLM Phase Coverage

- A2_comments: False
- A3_compaction: True
- A4_full_flow: False

usage_source: estimate

## Comment Density

- actual: 0.0125
- soft_min: 0.05
- stat_start: 0.0
- stat_end: 521.0

## Audit Samples

- comments: 0
- compaction_summaries: 1
- real_comments: 0
- window_status: 0
- agent_invocations: 6
- agent_reports: 6
- audit_safety: 6

## Failures

See reports/failures.md
