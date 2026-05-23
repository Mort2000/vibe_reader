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

# 5. 运行验证套件
uv run vibe-verify run --suite smoke --target-url http://127.0.0.1:8000
```

输出目录：`backend/verify_runs/<run_id>/`。排查失败时可加 `--keep-data` 保留验证数据目录。

## 前置条件

### 环境变量（`.env`）

运行系统验证时建议配置：

| 变量 | 示例 | 说明 |
|---|---|---|
| `VIBE_READER_DATA_DIR` | `/tmp/vibe_reader_verify` | 与后端、验证框架共用隔离目录 |
| `VIBE_READER_VERIFY_MODE` | `1` | 启用 `/api/verify/*` |
| `VIBE_READER_LLM_BASE_URL` | （你的 API 地址） | S0 `llm_ping` 需要 |
| `VIBE_READER_LLM_API_KEY` | （你的 key） | 同上 |
| `VIBE_READER_VERIFY_TARGET_URL` | `http://127.0.0.1:8000` | 被测后端地址 |

Shell 中已设置的变量优先于 `.env`。

### 语料

将 `corpus/manifest.toml` 中声明的 epub 放到 `corpus/books/`（该目录 gitignore），再执行 `vibe-verify prepare`。

## CLI 参考

主入口：`uv run vibe-verify`（注册于 `pyproject.toml` 的 `[project.scripts]`）。

```bash
cd backend
set -a && source ../.env && set +a

# 校验语料
uv run vibe-verify prepare --corpus tests/corpus/manifest.toml

# smoke：S0 连通性 + S1 导入与阅读
uv run vibe-verify run --suite smoke --target-url http://127.0.0.1:8000

# 与 smoke 相同场景集合
uv run vibe-verify run --suite mvp --target-url http://127.0.0.1:8000

# 仅创建 run 目录与 manifest
uv run vibe-verify init-run --corpus tests/corpus/manifest.toml

# 准备语料 + run 骨架，不执行场景
uv run vibe-verify run --dry-run --corpus tests/corpus/manifest.toml

# 失败时保留数据目录
uv run vibe-verify run --suite smoke --keep-data
```

被测后端（另开终端）：

```bash
cd backend
uv run vibe-reader
```

## 输出目录

每次 run 写入 `backend/verify_runs/<run_id>/`：

| 文件 | 说明 |
|---|---|
| `run_manifest.json` | run 元数据与安全检查结果 |
| `scenario_results.ndjson` | 各场景逐步结果 |
| `api_requests.ndjson` | HTTP 请求/响应摘要 |
| `metrics.ndjson` | 指标采样 |
| `traces/trace_index.ndjson` | trace 索引 |
| `corpus_manifest.resolved.json` | 解析后的语料 manifest |

## 场景与套件

| 套件 | 包含场景 |
|---|---|
| `smoke` / `mvp` | S0、S1 |

| 场景 | 内容 |
|---|---|
| **S0_connectivity** | health / runtime / settings / verify 端点、trace 头、`llm_ping` |
| **S1_book_import** | epub 导入、章节段落结构、计数与编号连续性、阅读进度 PUT→GET |

S1 另含导入幂等与进度去重相关断言步骤（`import_idempotent`、`progress_dedup_identical`、`progress_skip_trivial_scroll`）。场景在 `continue_on_failure` 下会执行全部步骤并在 `scenario_results.ndjson` 中汇总结果。

## pytest

`pyproject.toml` 已注册 marker `system_verify` / `system_llm`：

```bash
cd backend
set -a && source ../.env && set +a
uv run pytest tests/ -m system_verify
```

`.env` 由 `tests/system_verify/conftest.py` 自动加载。pytest **不**负责启动或停止后端进程。

## 目录结构

```text
tests/
  README.md                 本文件
  corpus/
    manifest.toml           验证语料声明
    books/                  epub 文件（gitignore，需自行放置）
  system_verify/            验证框架（vibe-verify CLI 实现）
    scenarios/              场景实现
    client.py               Target HTTP client
    contract.py             接口合同校验
    run.py                  run 目录与 manifest
    …
```

## 相关规格

- 任务与验收：`vibe_reader_doc/task_mini.md`
- 接口合同：`vibe_reader_doc/spec_interface.md`
- 可观测：`vibe_reader_doc/spec_telemetry.md`
