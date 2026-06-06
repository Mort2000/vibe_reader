# 后端维测配置与观测指导

本文档用于指导 Vibe Reader Mini 后端的日常运维、测试验证和故障定位。它描述配置方式、日志读取路径、OTEL 接入方式、查询方法，以及典型场景下应关注的关键日志。

## 目标

后端可观测性遵循以下约定：

- 关键流程必须产生可读日志。HTTP 请求、书籍导入、阅读进度、AI 任务、上下文构建、聊天流、SSE 推送和启动配置都必须有明确的 `event`。
- 业务代码只通过标准 `logging` logger 记录文本或结构化上下文，不直接依赖具体消费端。
- 日志 sink 由配置动态路由，可同时输出到 console、rotating logfile 和 OTEL logs。
- traces 用于串联一次请求、一个后台 job 或一次 LLM 调用链路；metrics 用于趋势、容量和 SLO，不承载高基数字段。
- prompt、模型输出、书籍正文等敏感内容默认不进入 traces/metrics；审计包需要显式开启，并受脱敏配置控制。

## 配置入口

配置文件默认位于：

```text
~/.vibe_reader/config.toml
```

如设置 `VIBE_READER_DATA_DIR`，配置文件改为：

```text
$VIBE_READER_DATA_DIR/config.toml
```

环境变量覆盖配置文件中的同名观测项。常用环境变量如下：

| 环境变量 | 说明 |
|---|---|
| `VIBE_READER_DATA_DIR` | 数据目录，影响配置文件、数据库、书籍和日志默认位置 |
| `VIBE_READER_OBSERVABILITY_ENABLED` | 是否启用后端观测能力 |
| `VIBE_READER_ENVIRONMENT` | 环境名，例如 `local`、`test`、`staging`、`prod` |
| `VIBE_READER_LOG_LEVEL` | 日志等级，例如 `DEBUG`、`INFO`、`WARNING`、`ERROR` |
| `VIBE_READER_LOG_FORMAT` | `json` 或 `text` |
| `VIBE_READER_LOG_SINKS` | 逗号分隔 sink，例如 `console,file,otel` |
| `VIBE_READER_OTEL_ENABLED` | 是否初始化 OTEL runtime |
| `VIBE_READER_OTEL_ENDPOINT` | OTLP HTTP collector 地址，例如 `http://localhost:4318` |
| `VIBE_READER_OTEL_EXPORT_TRACES` | 是否导出 traces |
| `VIBE_READER_OTEL_EXPORT_METRICS` | 是否导出 metrics |
| `VIBE_READER_OTEL_EXPORT_LOGS` | 是否导出 OTEL logs |
| `VIBE_READER_OTEL_SAMPLE_RATIO` | trace 采样率，范围 `0.0` 到 `1.0` |
| `VIBE_READER_VERIFY_MODE` | 开启验证模式和验证上下文字段 |

启动后应首先看到 `observability.logging_configured` 日志。该日志会输出已生效的观测配置摘要，但不会打印 OTEL endpoint 明文，只显示是否配置。

## 日志配置

后端使用 Python 标准 `logging`。上层服务记录 `event`、`message` 和 `fields`，底层根据配置路由到不同 sink。console/file/otel 可以并存。

### 本地 console JSON

```toml
[observability]
enabled = true
service_name = "vibe-reader-backend"
environment = "local"
log_level = "INFO"
log_format = "json"
log_sinks = ["console"]

[observability.console]
enabled = true
stream = "stdout"
```

### console 加文件日志

```toml
[observability]
enabled = true
environment = "test"
log_level = "INFO"
log_format = "json"
log_sinks = ["console", "file"]

[observability.console]
enabled = true
stream = "stdout"

[observability.file]
enabled = true
path = "logs/backend.jsonl"
max_bytes = 10485760
backup_count = 5
```

`observability.file.path` 为空时默认写入 `$VIBE_READER_DATA_DIR/logs/backend.jsonl`。相对路径会拼到 `data_dir` 下；绝对路径和 `~` 会按系统路径解析。

### 可读文本日志

```toml
[observability]
enabled = true
log_level = "DEBUG"
log_format = "text"
log_sinks = ["console"]
```

文本格式适合本地临时调试；长期采集建议使用 `json`，方便按字段查询。

### OTEL traces/metrics/logs

```toml
[observability]
enabled = true
service_name = "vibe-reader-backend"
environment = "staging"
log_level = "INFO"
log_format = "json"
log_sinks = ["console", "file", "otel"]

[observability.otel]
enabled = true
endpoint = "http://localhost:4318"
protocol = "otlp_http"
export_traces = true
export_metrics = true
export_logs = true
sample_ratio = 1.0

[observability.file]
enabled = true
path = "logs/backend.jsonl"
```

当前仅支持 OTLP HTTP。若 endpoint 配置为 `http://collector:4318`，后端会按信号自动使用：

```text
http://collector:4318/v1/traces
http://collector:4318/v1/metrics
http://collector:4318/v1/logs
```

### LLM 审计包

审计包用于验证和深度排查 LLM 输入输出链路。默认关闭；验证模式或显式配置可开启。

```toml
[observability.audit]
enabled = false
include_prompt_manifest = true
include_full_prompt = false
include_model_response = false
redact_secrets = true
```

审计文件写入：

```text
$VIBE_READER_DATA_DIR/verify_agent_interactions/<verify_run_id>/<invocation_id>.json
```

未处于验证 run 时，`verify_run_id` 为空会落到 `_unscoped` 目录。生产环境不要开启 `include_full_prompt` 或 `include_model_response`，除非已完成数据授权和留存评估。

## 日志读取路径

### 前台进程

开发环境常用启动方式：

```bash
cd backend
uv run vibe-reader
```

console sink 输出到当前终端。若 `stream = "stderr"`，日志进入 stderr；默认进入 stdout。

### 文件日志

默认路径：

```text
~/.vibe_reader/logs/backend.jsonl
```

设置 `VIBE_READER_DATA_DIR=/data/vibe_reader` 后默认路径为：

```text
/data/vibe_reader/logs/backend.jsonl
```

常用读取命令：

```bash
tail -f ~/.vibe_reader/logs/backend.jsonl
jq 'select(.level == "ERROR")' ~/.vibe_reader/logs/backend.jsonl
jq 'select(.trace_id == "trace_xxx")' ~/.vibe_reader/logs/backend.jsonl
jq -r 'select(.event == "job_runner.job_failed") | [.ts, .trace_id, .fields.job_id, .fields.job_type, .fields.error] | @tsv' ~/.vibe_reader/logs/backend.jsonl
```

### systemd 或容器

如果后端由 systemd 管理，console sink 通常进入 journal：

```bash
journalctl -u <service-name> -f
journalctl -u <service-name> --since "30 min ago"
```

如果后端在容器中运行：

```bash
docker logs -f <container-name>
```

实际 service/container 名由部署环境决定。

## 结构化日志字段

JSON 日志包含以下基础字段：

| 字段 | 说明 |
|---|---|
| `ts` | UTC 时间戳 |
| `level` | 日志等级 |
| `event` | 稳定事件名，查询时优先使用 |
| `message` | 人可读消息 |
| `logger` | Python logger 名 |
| `service` | 服务名，默认 `vibe-reader-backend` |
| `environment` | 环境名 |
| `request_id` | 单次 HTTP 请求 ID |
| `trace_id` | 应用级 trace ID，用于串联 HTTP、SSE、job、LLM |
| `span_id` | 当前 span ID，优先使用 OTEL span ID |
| `otel_trace_id` | OTEL 原生 trace ID |
| `otel_span_id` | OTEL 原生 span ID |
| `verify_run_id` | 验证 run ID |
| `verify_scenario_id` | 验证场景 ID |
| `verify_step_id` | 验证步骤 ID |
| `fields` | 业务字段 |
| `exception` | 异常类型、消息和堆栈，仅异常日志存在 |

HTTP 响应会回写：

```text
x-request-id
x-trace-id
```

前端、验证脚本或压测工具应保存这两个 header。排查时先按 `trace_id` 聚合，再按 `request_id` 缩小 HTTP 请求范围。

## OTEL 接入

### Collector 示例

最小调试 collector 配置：

```yaml
receivers:
  otlp:
    protocols:
      http:
        endpoint: 0.0.0.0:4318

exporters:
  debug: {}

service:
  pipelines:
    traces:
      receivers: [otlp]
      exporters: [debug]
    metrics:
      receivers: [otlp]
      exporters: [debug]
    logs:
      receivers: [otlp]
      exporters: [debug]
```

生产环境通常将 traces 送到 Jaeger/Tempo，metrics 送到 Prometheus remote write/Mimir，logs 送到 Loki 或其它日志平台。

### 自动与手工 instrumentation

后端启动时会初始化：

- FastAPI instrumentation：自动生成 HTTP server span，并写入 `request.id`、`app.trace_id` 和验证字段。
- PydanticAI instrumentation：自动生成 LLM 相关 span，但禁用 prompt/content 采集。
- 手工 span：覆盖后台 job、上下文构建、评论 Agent、压缩 Agent、聊天 Agent 和聊天持久化。
- 手工 metrics：覆盖 job、context build、agent、chat stream 和 SSE event。

核心 span 名：

| span | 场景 |
|---|---|
| `job.<job_type>` | 后台 AI job 执行 |
| `service.context.build` | LLM 上下文构建 |
| `ai.ParagraphCommentAgent.run` | 段落评论 Agent |
| `ai.ContextCompactionAgent.run` | 上下文压缩 Agent |
| `ai.ReadingChatAgent.run` | 阅读聊天 Agent |
| `service.chat.persist` | 聊天结果落库 |

常用 trace 属性：

| 属性 | 说明 |
|---|---|
| `service.name` | `vibe-reader-backend` |
| `deployment.environment` | 环境名 |
| `request.id` | HTTP 请求 ID |
| `app.trace_id` | 应用级 trace ID |
| `verify.run_id` | 验证 run ID |
| `job.id` | 后台 job ID |
| `job.type` | job 类型 |
| `book.id` | 书籍 ID |
| `chapter.idx` | 章节序号 |
| `window.id` | 阅读窗口 ID |
| `ai.agent` | Agent 名 |
| `ai.model` | 模型名 |

### Metrics

当前导出的业务 metrics：

| metric | 类型 | 说明 |
|---|---|---|
| `vibe_reader_jobs_total` | counter | job 数量，按 `job_type`、`status` 统计 |
| `vibe_reader_job_duration_ms` | histogram | job 执行耗时 |
| `vibe_reader_context_builds_total` | counter | 上下文构建次数 |
| `vibe_reader_context_build_duration_ms` | histogram | 上下文构建耗时 |
| `vibe_reader_context_tokens` | histogram | 上下文 token 估算 |
| `vibe_reader_agent_runs_total` | counter | Agent 调用次数 |
| `vibe_reader_agent_duration_ms` | histogram | Agent 调用耗时 |
| `vibe_reader_agent_tokens` | histogram | Agent token 用量 |
| `vibe_reader_chat_streams_total` | counter | 聊天流次数 |
| `vibe_reader_chat_duration_ms` | histogram | 聊天总耗时 |
| `vibe_reader_chat_ttft_ms` | histogram | 聊天首 token 时间 |
| `vibe_reader_chat_tokens` | histogram | 聊天 token 用量 |
| `vibe_reader_sse_events_total` | counter | SSE 事件数量 |

metric 标签只允许低基数字段，例如 `status`、`job_type`、`task_type`、`agent`、`model`、`token_type`、`event`。不要把 `trace_id`、`request_id`、`job_id`、用户文本或书籍正文放入 metric 标签。

PromQL 示例：

```promql
sum by (status, job_type) (increase(vibe_reader_jobs_total[5m]))
histogram_quantile(0.95, sum by (le, agent) (rate(vibe_reader_agent_duration_ms_bucket[5m])))
sum by (agent, token_type) (rate(vibe_reader_agent_tokens_sum[5m]))
sum by (status) (increase(vibe_reader_chat_streams_total[5m]))
histogram_quantile(0.95, sum by (le) (rate(vibe_reader_chat_ttft_ms_bucket[5m])))
sum by (event, status) (increase(vibe_reader_sse_events_total[5m]))
```

不同 OTEL 后端对 histogram 的 `_bucket`、`_sum`、`_count` 暴露方式可能不同，应以实际后端为准。

## 查询指导

### 从日志跳到 trace

1. 从 HTTP 响应 header 或错误日志取得 `trace_id`。
2. 在文件日志或日志平台中查询同一 `trace_id`。
3. 在 Jaeger/Tempo 中按 `app.trace_id = <trace_id>` 或 `request.id = <request_id>` 查询 trace。
4. 找到最慢或错误 span，再回到日志中按 `trace_id` 和 `event` 查看业务上下文。

本地日志示例：

```bash
jq 'select(.trace_id == "trace_xxx") | {ts, level, event, fields}' ~/.vibe_reader/logs/backend.jsonl
```

### Trace 平台

在 Jaeger 或 Tempo 中优先使用：

```text
service.name = vibe-reader-backend
deployment.environment = <env>
app.trace_id = trace_xxx
request.id = req_xxx
job.type = comment_window
ai.agent = ParagraphCommentAgent
```

如果只有 OTEL 原生 trace ID，可使用日志里的 `otel_trace_id` 反查。

### 日志平台

如果日志进入 Loki，查询语法取决于 collector label 映射。常见查询形态：

```logql
{service_name="vibe-reader-backend"} | json | event="http.request.failed"
{service_name="vibe-reader-backend"} | json | trace_id="trace_xxx"
{service_name="vibe-reader-backend"} | json | event="sse.queue_full"
```

若平台未将 `service` 映射为 label，可先按容器、namespace 或 app label 缩小范围，再用 `| json` 过滤字段。

## 典型场景关键日志

### 启动与配置

| event | 等级 | 关注字段 | 说明 |
|---|---|---|---|
| `observability.logging_configured` | INFO | `fields.log_sinks`、`fields.log_format`、`fields.otel` | 确认日志和 OTEL 配置已生效 |
| `observability.file_sink_failed` | WARNING | `fields.error` | 文件日志初始化失败，通常是路径或权限问题 |
| `observability.otel_endpoint_missing` | WARNING | `fields.export_traces`、`fields.export_metrics` | 开启 OTEL 导出但未配置 endpoint |
| `observability.otel_log_sink_unavailable` | WARNING | `fields.reason` | 配置了 `otel` sink，但 log exporter 未初始化 |
| `startup.chunk_backfill` | INFO | backfill 数量相关字段 | 启动时补齐历史 chunk 状态 |

### HTTP 请求

| event | 等级 | 关注字段 | 说明 |
|---|---|---|---|
| `http.request.completed` | INFO | `method`、`path`、`status_code`、`duration_ms` | 请求完成，包括流式响应的真实完成时间 |
| `http.request.failed` | ERROR | `method`、`path`、`duration_ms`、`exception` | 请求异常，按 `trace_id` 查同链路日志和 trace |

### 书籍导入

| event | 等级 | 关注字段 | 说明 |
|---|---|---|---|
| `book.import.duplicate` | INFO | `book_id`、`source` | 重复导入，复用已有书籍 |
| `book.import.done` | INFO | `book_id`、章节/段落统计 | 导入完成，可继续观察窗口创建和评论任务 |

### 阅读进度与窗口

| event | 等级 | 关注字段 | 说明 |
|---|---|---|---|
| `progress.update.accepted` | INFO | `book_id`、`chapter_idx`、`paragraph_idx` | 进度更新被接受 |
| `progress.update.deduped` | INFO | 进度字段 | 重复进度被去重 |
| `progress.update.backward_jump` | INFO | 当前和目标进度 | 检测到回跳 |
| `progress.update.agent_busy` | INFO | `job_id`、窗口字段 | Agent 忙时策略生效 |
| `progress.update.agent_busy.pending_preserved` | INFO | pending 进度 | Agent 忙时保留 pending |
| `job_runner.pending_processed` | INFO | pending 进度 | pending 进度被后台处理 |
| `job_runner.pending_discarded` | INFO | pending 进度 | pending 进度过期或不再适用 |
| `window.created` | INFO | `window_id`、`book_id`、`chapter_idx` | 阅读窗口创建 |

### 后台 job

| event | 等级 | 关注字段 | 说明 |
|---|---|---|---|
| `job_runner.started` | INFO | 无 | job runner 已启动 |
| `job_runner.recovered` | INFO | `job_id`、数量 | 启动时恢复 queued/running job |
| `job_runner.job_done` | INFO | `job_id`、`job_type`、`duration_ms` | job 成功完成 |
| `job_runner.job_failed` | ERROR | `job_id`、`job_type`、`error` | job 失败，需查询同一 `trace_id` 的 span 和 Agent 日志 |
| `job_runner.task_failed` | ERROR | `job_id`、异常 | runner 内部 task 异常 |
| `job_runner.no_handler` | ERROR | `job_type` | 未注册 job handler |
| `job_runner.compaction_skipped` | INFO | `reason` | 压缩任务被跳过 |
| `job_runner.stopped` | INFO | 无 | job runner 已停止 |

### 上下文构建与评论 Agent

| event | 等级 | 关注字段 | 说明 |
|---|---|---|---|
| `comment_task.preflight_compaction` | INFO | token 估算和触发原因 | 评论前预检查触发压缩 |
| `comment_task.context_degraded` | WARNING | `reason`、token 字段 | 上下文降级，需要结合 `service.context.build` span 排查 |
| `context_builder.overflow_without_live_drop` | WARNING | token 字段 | 上下文超预算且无法继续丢弃 live 内容 |
| `comment_task.no_evidence_no_call` | WARNING | `window_id` | 证据不足，跳过 LLM 调用 |
| `comment_task.partial_evidence` | WARNING | `window_id`、缺失证据数量 | 部分证据缺失 |
| `comment_task.completed` | INFO | `job_id`、`window_id`、评论数、token 和耗时 | 评论任务成功完成 |
| `comment_task.discarded_comments` | WARNING | 丢弃数量和原因 | 模型返回评论被校验丢弃 |

相关 trace span：

```text
job.comment_window
service.context.build
ai.ParagraphCommentAgent.run
```

相关 metrics：

```text
vibe_reader_context_builds_total
vibe_reader_context_build_duration_ms
vibe_reader_context_tokens
vibe_reader_agent_runs_total
vibe_reader_agent_duration_ms
vibe_reader_agent_tokens
```

### 上下文压缩

| event | 等级 | 关注字段 | 说明 |
|---|---|---|---|
| `compaction.enqueue_skipped` | INFO | `reason` | 压缩任务未入队 |
| `compaction.no_chunk` | INFO | `book_id`、`chapter_idx` | 没有可压缩 chunk |
| `compaction.completed` | INFO | `job_id`、chunk 和 token 字段 | 压缩完成 |

相关 trace span：

```text
job.compact_context
ai.ContextCompactionAgent.run
```

### 聊天流

| event | 等级 | 关注字段 | 说明 |
|---|---|---|---|
| `chat.started` | SSE | `trace_id`、`request_id` | 聊天流开始事件 |
| `chat.first_delta` | SSE | `trace_id`、`request_id` | 首个增量，定位 TTFT |
| `chat.delta` | SSE | 增量文本 | 流式内容片段 |
| `chat.done` | SSE | token 和 turn 字段 | 聊天流结束 |
| `chat.error` | SSE | `error` | 聊天流错误事件 |
| `chat.stream_done` | INFO | `duration_ms`、`ttft_ms`、token 字段 | 后端完成聊天流和落库 |
| `chat.stream_error` | ERROR | `exception`、请求字段 | 聊天流异常 |
| `chat.recorder_failed` | ERROR | 审计/记录异常 | 聊天审计或记录失败，但需确认业务是否已返回 |

相关 trace span：

```text
ai.ReadingChatAgent.run
service.chat.persist
```

相关 metrics：

```text
vibe_reader_chat_streams_total
vibe_reader_chat_duration_ms
vibe_reader_chat_ttft_ms
vibe_reader_chat_tokens
```

### SSE 推送

| event | 等级 | 关注字段 | 说明 |
|---|---|---|---|
| `sse.queue_full` | WARNING | `sse_event`、`dropped_count` | 订阅端消费过慢或断开，事件被丢弃 |

所有 SSE payload 会尽量带上当前 `trace_id`、`request_id` 和验证字段。排查 UI 未更新时，先看后端是否发布了对应 SSE，再看 `sse.queue_full` 是否增加。

## 排查流程

### 请求失败

1. 从客户端响应 header 或错误日志取得 `x-trace-id`。
2. 查 `http.request.failed`：

```bash
jq 'select(.event == "http.request.failed" and .trace_id == "trace_xxx")' ~/.vibe_reader/logs/backend.jsonl
```

3. 查同一 trace 的全部日志：

```bash
jq 'select(.trace_id == "trace_xxx") | {ts, level, event, fields, exception}' ~/.vibe_reader/logs/backend.jsonl
```

4. 在 trace 平台按 `app.trace_id` 定位最慢或错误 span。

### 评论任务失败

1. 查 `job_runner.job_failed`，记录 `job_id`、`job_type`、`trace_id`。
2. 在日志中过滤同一 `trace_id`，重点看 `comment_task.context_degraded`、`comment_task.discarded_comments`、Agent 异常和 `exception`。
3. 在 trace 平台查看 `job.comment_window`、`service.context.build`、`ai.ParagraphCommentAgent.run`。
4. 在 metrics 中查看 `vibe_reader_agent_runs_total{status="error"}` 和 `vibe_reader_context_builds_total{status="error"}` 是否升高。

### 聊天首 token 慢

1. 查 `chat.stream_done` 的 `ttft_ms`。
2. 查 trace 中 `ai.ReadingChatAgent.run` span 耗时。
3. 用 PromQL 看 P95：

```promql
histogram_quantile(0.95, sum by (le) (rate(vibe_reader_chat_ttft_ms_bucket[5m])))
```

4. 对比 `vibe_reader_chat_tokens` 和 `vibe_reader_agent_tokens`，判断是否由上下文过大导致。

### 上下文超预算或降级

1. 查 `comment_task.context_degraded` 和 `context_builder.overflow_without_live_drop`。
2. 查 `service.context.build` span 属性中的 task 类型、token 估算和降级标记。
3. 查 metrics：

```promql
sum by (status, task_type, context_degraded) (increase(vibe_reader_context_builds_total[10m]))
histogram_quantile(0.95, sum by (le, task_type) (rate(vibe_reader_context_tokens_bucket[10m])))
```

4. 如果频繁降级，优先检查 context 配置、章节 chunk 状态和压缩任务是否正常完成。

### SSE 丢事件

1. 查 `sse.queue_full`。
2. 看 `fields.sse_event` 和 `fields.dropped_count`。
3. 查 metrics：

```promql
sum by (event, status) (increase(vibe_reader_sse_events_total[5m]))
```

4. 若 `dropped` 持续升高，检查客户端是否长期不消费 event stream，或服务端队列容量是否不足。

## 验证清单

启用新环境或调整观测配置后，至少验证：

- 启动日志中存在 `observability.logging_configured`。
- `fields.log_sinks` 与预期一致。
- 若开启文件日志，`$VIBE_READER_DATA_DIR/logs/backend.jsonl` 或自定义路径可写且持续增长。
- 任意 `/api/health` 或 `/api/runtime` 响应包含 `x-request-id` 和 `x-trace-id`。
- 按响应 `x-trace-id` 能在日志中查到 `http.request.completed`。
- 若开启 OTEL traces，collector 能收到 `service.name = vibe-reader-backend` 的 HTTP span。
- 若开启 OTEL metrics，collector 或指标后端能看到 `vibe_reader_*` 指标。
- 若开启 OTEL logs，日志平台能按 `event`、`trace_id` 查询。
- 若开启审计包，确认文件落到 `verify_agent_interactions/`，且 prompt 和模型输出字段符合脱敏预期。

## 安全与保留

- 默认不会将 prompt、书籍正文、模型输出写入 OTEL traces 或 metrics。
- PydanticAI instrumentation 禁用 content 和 binary content 采集。
- 异常 span 只记录异常类型和安全摘要，不主动把异常消息作为 span event 全量写入。
- JSON 日志的 `exception.message` 可能包含异常文本，生产环境应避免在异常消息中拼接敏感输入。
- 文件日志是 rotating logfile，轮转参数由 `max_bytes` 和 `backup_count` 控制。
- 审计包是排查工具，不是长期业务数据源；生产启用前必须明确访问权限、保留周期和脱敏策略。
