# Vibe Reader Verify Summary

run_id: 20260524T070437Z_c9b436a7
git_commit: c5c8f32ed0d196bc288df1f67f68dc5bf04a3806
suite: smoke
llm_mode: stub
param_set: mvp
stub_profile: mvp_default
real_llm_calls: 0
model: deepseek-v4-flash
corpus: 5b8a4e552e42c7769da068ec8eec87e760a022a11bcfa34c155b55d81e0b11a4
started_at: 2026-05-24T07:04:37Z
ended_at: 2026-05-24T07:05:25Z

## Result

pass

## Functional Checks

- import: pass
- progress: pass
- comment: pass
- scroll_jump: pass
- long_context: pass
- compaction: pass
- real_happy_path: not_run

## Latency

| metric | count | p50 | p90 | p95 | max |
|---|---:|---:|---:|---:|---:|
| api.get.duration_ms | 13 | 3.2 | 8.7 | 8.8 | 8.8 |
| api.post.duration_ms | 2 | 1918.7 | 3412.5 | 3599.3 | 3786.0 |
| api.put.duration_ms | 505 | 10.6 | 14.5 | 24.9 | 56.0 |
| comment.agent_run_ms.max | 2 | 191.4 | 328.1 | 345.2 | 362.3 |
| comment.agent_run_ms.p50 | 2 | 187.5 | 327.3 | 344.8 | 362.3 |
| comment.agent_run_ms.p90 | 2 | 187.9 | 327.4 | 344.9 | 362.3 |
| comment.agent_run_ms.p95 | 2 | 191.4 | 328.1 | 345.2 | 362.3 |
| comment.job_queue_wait_ms | 1 | 0.0 | 0.0 | 0.0 | 0.0 |
| comment.job_run_ms | 1 | 0.0 | 0.0 | 0.0 | 0.0 |
| import.duration_ms | 2 | 3665.0 | 3665.0 | 3665.0 | 3665.0 |
| llm.ping.latency_ms | 1 | 48.0 | 48.0 | 48.0 | 48.0 |
| progress.update.duration_ms | 502 | 10.6 | 14.0 | 25.0 | 56.0 |

## Tokens

| agent | requests | input | output | total | max_input |
|---|---:|---:|---:|---:|---:|
| llm_ping | 1 | 4 | 1 | 5 | 4 |
| paragraph_comment | 2 | 18208 | 0 | 18208 | 13002 |

## Real LLM Phase Coverage

- A2_comments: False
- A3_compaction: False
- A4_full_flow: False

usage_source: estimate

## Comment Density

- actual: 0.0329
- soft_min: 0.05
- stat_start: 0.0
- stat_end: 521.0

## Audit Samples

- comments: 3
- compaction_summaries: 1
- real_comments: 0
- window_status: 0
- agent_invocations: 8
- agent_reports: 9
- audit_safety: 8

## Compaction Jobs

- `S4_long_context`: done=1 skipped=0 failed=0 (min_job_id=0)
- total: done=1 skipped=0 failed=0 other=0

## Failures

See reports/failures.md
