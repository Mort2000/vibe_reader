# 测试与系统验证

本目录包含 **系统验证框架**（`system_verify/`）与验证语料配置（`corpus/`）。验证通过公开 HTTP / SSE 接口访问后端，不导入产品内部 service 或数据库。

所有命令在 **`backend/`** 目录下通过 **uv** 执行。项目以 editable 模式安装（`uv sync --extra dev`），可直接使用 `vibe-verify` / `vibe-reader` 入口。

## 运行流程

```bash
# 1. 安装后端（editable，含 dev 依赖与 CLI 入口），并检查是否配置 .env
cd backend
uv sync --extra dev
ls .env

# 2. 校验语料 manifest（epub 需已放到 tests/corpus/books/）
uv run vibe-verify prepare --corpus tests/corpus/manifest.toml

# 3. 启动被测后端（另开终端，保持运行）
uv run vibe-reader

# 4. 确认 runtime（本终端）
curl -s http://127.0.0.1:8000/api/runtime | jq '.data_dir, .verify_mode'

# 5. 运行默认 stub 验证套件（不要求真实 LLM API key）
uv run vibe-verify run --suite smoke --target-url http://127.0.0.1:8000
```

输出目录：`backend/verify_runs/<run_id>/`。排查失败时可加 `--keep-data` 保留验证数据目录。

## 参数集（ParamSet）

行为参数（阅读 pacing、batch 大小、长流程停止条件、预算护栏、断言策略）不再在 `verify.toml` 中区分 stub/real 双轨字段，而是集中在 **命名参数集** 中：

```text
tests/corpus/param_sets/
  mvp.toml           S0–S4 默认 stub 回归
  r1_a2_stub.toml    R1 A2，AIMock 快速 batch
  r1_a2_real.toml    R1 A2，真实 LLM paced reading
  r1_a3_stub.toml    R1 A3 compaction stub 回归
  r1_a3_real.toml    R1 A3 compaction 真实 LLM
```

每个参数集声明所需 `llm_mode`（`stub` / `real`）。激活参数集后，框架同步 `llm.mode`、AIMock profile 与 `collect_provider_usage`。

注入优先级：`--param-set` > `VIBE_READER_VERIFY_PARAM_SET` > 套件默认 > `[param_set].default`。

`real-happy-path` 套件在未显式指定参数集时，按 `--llm-mode`（或默认 stub）自动选择 `r1_{A2|A3}_{stub|real}`。

## LLM 模式

| 模式 | 默认 | 外部网络 | 用途 |
|---|---|---|---|
| `stub` | 是 | 否 | 默认回归：接口、窗口、评论流程、R1 stub 参数集 |
| `real` | 否 | 是 | 真实 LLM：使用 `r1_*_real` 等参数集 |

基础设施配置见 `tests/corpus/verify.toml`；行为参数见 `tests/corpus/param_sets/`。

## 前置条件

### 环境变量（`.env`）

运行 **stub 默认套件** 时只需：

| 变量 | 示例 | 说明 |
|---|---|---|
| `VIBE_READER_DATA_DIR` | `/tmp/vibe_reader_verify` | 与后端、验证框架共用隔离目录 |
| `VIBE_READER_VERIFY_MODE` | `1` | 启用 `/api/verify/*` |
| `VIBE_READER_VERIFY_TARGET_URL` | `http://127.0.0.1:8000` | 被测后端地址 |
| `VIBE_READER_LLM_BASE_URL` | `http://127.0.0.1:4010/v1` | AIMock sidecar（`vibe-verify run` 会自动启动） |
| `VIBE_READER_LLM_API_KEY` | `aimock-test-key` | AIMock 占位 key，非真实凭据 |
| `VIBE_READER_LLM_MODEL` | `deepseek-v4-flash` | 与 `verify.toml` 中 `[llm_stub.aimock]` 一致 |

可选：`VIBE_READER_VERIFY_PARAM_SET` 指定参数集名称。

`vibe-verify run` 在 stub 模式下会自动启动 AIMock sidecar（需 Node.js ≥20，首次运行会在 `llm_stub/aimock/` 执行 `npm install`），并通过 `inject_stub_backend_env` 将 LLM env 注入 verify runner 进程。

后端仍是独立进程，需用相同 env 启动，或使用一键选项：

```bash
uv run vibe-verify run --suite smoke --spawn-backend
```

pytest 同样支持 `--spawn-backend`。未使用该选项时，runner 会在 S0 之前检查 `/api/verify/runtime` 的 LLM 配置，未就绪则 fail-fast。

运行 **real 参数集** 时额外需要：

| 变量 | 说明 |
|---|---|
| `VIBE_READER_LLM_BASE_URL` | 真实 provider 地址 |
| `VIBE_READER_LLM_API_KEY` | 真实 API key |

Shell 中已设置的变量优先于 `.env`。`--llm-mode` 必须与参数集声明的 `llm_mode` 一致。

### 语料

将 `corpus/manifest.toml` 中声明的 epub 放到 `corpus/books/`（该目录 gitignore），再执行 `vibe-verify prepare`。

manifest 现包含 `happy_path_current` probe，供 R1 长流程定位。

## CLI 参考

```bash
cd backend
set -a && source ../.env && set +a

# 校验语料（含 happy_path_current）
uv run vibe-verify prepare --corpus tests/corpus/manifest.toml

# stub smoke / mvp：S0–S4（参数集 mvp）
uv run vibe-verify run --suite smoke --target-url http://127.0.0.1:8000
uv run vibe-verify run --suite mvp --target-url http://127.0.0.1:8000

# R1 A2 stub 回归（同一 R1 场景，AIMock 参数集）
uv run vibe-verify run --suite real-happy-path --param-set r1_a2_stub

# 真实 LLM A2（参数集 r1_a2_real）
uv run vibe-verify run --suite real-happy-path --param-set r1_a2_real --llm-mode real --real-coverage A2

# 从已有 run 生成报告
uv run vibe-verify report --run-id <run_id>

# 失败时保留数据目录
uv run vibe-verify run --suite smoke --keep-data
```

## 输出目录

每次 run 写入 `backend/verify_runs/<run_id>/`：

| 文件 | 说明 |
|---|---|
| `run_manifest.json` | run 元数据（含 `param_set`、`llm_mode`、`stub_profile`、`real_llm_*`） |
| `scenario_results.ndjson` | 各场景逐步结果 |
| `api_requests.ndjson` | HTTP 请求/响应摘要 |
| `metrics.ndjson` | 指标采样（含 `llm_mode` / `usage_source` 维度） |
| `traces/trace_index.ndjson` | trace 索引 |
| `sse_events.ndjson` | SSE 事件（S2/S3 窗口与评论） |
| `audit/comments.ndjson` | 评论审计样本 |
| `audit/window_status.ndjson` | no-call 窗口状态 |
| `reports/summary.md` | V-16 摘要报告 |
| `reports/metrics.json` | 指标聚合 |
| `reports/failures.md` | 失败分类 |

## 场景与套件

| 套件 | 包含场景 | 默认参数集 |
|---|---|---|
| `smoke` / `mvp` | S0、S1、S2、S3、S4 | `mvp` |
| `real-happy-path` | R1（A2/A3） | 按 `--llm-mode` 选 `r1_a2_{stub\|real}` 等 |

| 场景 | 内容 | 验收阶段 |
|---|---|---|
| **S0_connectivity** | health / runtime / LLM 模式 / trace / llm_ping | A0 |
| **S1_book_import** | epub 导入、进度、happy_path_current probe | A1 |
| **S2_continuous_reading** | 连续阅读、评论/no-call 窗口、密度与校验指标、审计样本 | A2 |
| **S3_fast_scroll** | 快速滚动与跳读、窗口对齐、评论复用 | A2 |
| **R1_real_happy_path** | 长流程阅读、评论窗口、可选 compaction、预算护栏 | A2/A3 |

## pytest

```bash
cd backend
set -a && source ../.env && set +a

# 默认 stub 回归（含 R1 stub）
uv run pytest tests/system_verify/ -m "system_verify and system and not real_llm"

# 完整 stub 套件
uv run pytest tests/system_verify/test_scenarios.py::test_mvp_suite -m system

# R1 A2 stub（默认 CI 可纳入）
uv run pytest tests/system_verify/test_scenarios.py::test_r1_happy_path_a2_stub -m system

# 真实 LLM Happy Path（需 API key）
uv run pytest tests/system_verify/test_scenarios.py::test_r1_happy_path_a2_real -m real_llm --llm-mode real --param-set r1_a2_real
```

`.env` 由 `tests/system_verify/conftest.py` 自动加载。pytest **不**负责启动或停止后端进程。

## 目录结构

```text
tests/
  README.md
  corpus/
    manifest.toml           验证语料声明（含 happy_path_current）
    verify.toml             基础配置（target、LLM 基础设施、套件默认）
    param_sets/             命名参数集（行为参数）
    books/                  epub 文件（gitignore）
  system_verify/
    llm_stub/
      aimock/                 AIMock sidecar（server.mjs、profiles、fixtures）
      aimock_launcher.py      Python 启停与健康检查
    scenarios/              S0–S4、R1
    report_generator.py     V-16 报告生成
    suite.py                套件编排
    test_scenarios.py       pytest 入口
    …
```
