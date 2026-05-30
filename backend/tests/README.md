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
# 方式 A：一键（自动起 AIMock + backend）
uv run vibe-verify run --suite smoke --spawn-backend
# 方式 B：手动起 backend 后运行（见上文步骤 3）
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

### Verify 7K L2 配置平面（A3 deliberate）

`verify.toml` 中 `[app.context_l2]` 使用 **7K 分块**（`target=7000 / min=5000 / max=9000`），刻意偏离 design 默认 24K，以便 ch1 语料切出 ≥3 个 L2 块并触发压缩。该平面仅用于 **机制验收**，不代表生产 24K 配置。

源块断言门槛以 `manifest.toml` 的 `happy_path_current` probe 为准（5000 tokens / 80 paragraphs）。`r1_a3_{stub|real}.toml` 的 `[long_flow]` 应与 probe 对齐；若 param set 与 probe 不一致，运行时以 **probe 优先**（`probe || long_flow`）。

修改 `[app.context_l2]` 后须完整重跑验证（框架 pre-run reset 会清 data_dir 并重 import），勿用 `--keep-data` 保留旧 chunk 布局。

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

pytest 同样支持 `--spawn-backend`（**仅 stub 模式**）。未使用该选项时，集成场景会检查 backend / verify mode / LLM 配置；前置不满足则 **SKIP**（加 `--require-integration` 改为 **FAIL**）。

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
set -a && source .env && set +a

# 校验语料（含 happy_path_current）
uv run vibe-verify prepare --corpus tests/corpus/manifest.toml

# stub smoke / mvp：S0–S4（参数集 mvp）
uv run vibe-verify run --suite smoke --spawn-backend
uv run vibe-verify run --suite mvp --spawn-backend

# R1 A2 stub 回归
uv run vibe-verify run --suite real-happy-path --param-set r1_a2_stub --spawn-backend

# 真实 LLM（须手动起 backend；--spawn-backend 仅 stub）
uv run vibe-verify run --suite real-happy-path --param-set r1_a2_real --llm-mode real --real-coverage A2
uv run vibe-verify run --suite real-happy-path --param-set r1_a3_real --llm-mode real --real-coverage A3

# 从已有 run 生成报告
uv run vibe-verify report --run-id <run_id>

# 失败时保留数据目录
uv run vibe-verify run --suite smoke --spawn-backend --keep-data
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
| `real-happy-path` | R1_A2_comments、R1_A3_compaction | 按 `--llm-mode` 选 `r1_a2_{stub\|real}` 等 |

| 场景 | 内容 | 验收阶段 |
|---|---|---|
| **S0_connectivity** | health / runtime / LLM 模式 / trace / llm_ping | A0 |
| **S1_book_import** | epub 导入、进度、happy_path_current probe | A1 |
| **S2_continuous_reading** | 连续阅读、评论/no-call 窗口、密度与校验指标、审计样本 | A2 |
| **S3_fast_scroll** | 快速滚动与跳读、窗口对齐、评论复用 | A2 |
| **S4_long_context** | 长上下文窗口、L2 分块与 compaction 前置 | A3 |
| **R1_A2_comments** | 长流程阅读、评论窗口、预算护栏 | A2 |
| **R1_A3_compaction** | compaction 触发、审计与 post-compaction 评论 | A3 |

## pytest

测试分三层，按依赖递增：

| 层级 | marker / 条件 | 需要 live backend |
|---|---|---|
| 单元测试 | `-m "not system and not real_llm"` | 否 |
| stub 集成 | `-m "system and not real_llm"` | 是（可用 `--spawn-backend` 自动起） |
| real LLM 集成 | `-m real_llm` + `--llm-mode real` + 匹配 `--param-set` | 是（须手动起 backend） |

```bash
cd backend
set -a && source .env && set +a

# 1. 单元测试（无需 backend，合并前首选回归）
uv run pytest -m "not system and not real_llm"

# 2. stub 集成（自动起 AIMock + backend；前置不满足则 FAIL）
uv run pytest tests/system_verify/test_scenarios.py \
  -m "system and not real_llm" --spawn-backend --require-integration

# 单场景示例
uv run pytest tests/system_verify/test_scenarios.py::test_mvp_suite -m system --spawn-backend
uv run pytest tests/system_verify/test_scenarios.py::test_r1_happy_path_a2_stub -m system --spawn-backend

# 3. real LLM（另开终端手动起 backend，加载 .env 中的真实 LLM 配置）
uv run python -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000

# A2 → reset → A3（同一 data_dir 时 A3 前须 reset）
uv run pytest tests/system_verify/test_scenarios.py::test_r1_happy_path_a2_real \
  -m real_llm --llm-mode real --param-set r1_a2_real --require-integration

uv run python -c "
import asyncio
from tests.system_verify.env_file import load_project_dotenv
load_project_dotenv()
from tests.system_verify.core.config_loader import load_verify_config
from tests.system_verify.core.run_manager import RunManager
from tests.system_verify.data_lifecycle import prepare_run_data_dir
async def main():
    c = load_verify_config('tests/corpus/verify.toml', param_set='r1_a3_real', llm_mode_override='real')
    mgr = RunManager(c); mgr.start()
    await prepare_run_data_dir(c, mgr, phase='pre'); mgr.finish()
asyncio.run(main())
"

uv run pytest tests/system_verify/test_scenarios.py::test_r1_happy_path_a3_real \
  -m real_llm --llm-mode real --param-set r1_a3_real --require-integration
```

`.env` 由 `tests/system_verify/conftest.py` 自动加载（shell 环境变量优先）。

- **stub 模式**：`--spawn-backend` 会自动启动 AIMock sidecar 与 backend 子进程；未使用时需手动起 backend 且 LLM env 须与 AIMock 一致。
- **real 模式**：pytest 不启动 backend；`--param-set` 必须与场景匹配（A2 用 `r1_a2_real`，A3 用 `r1_a3_real`）。
- 无 live backend 时 `test_scenarios.py` 会 **SKIP**；加 `--require-integration` 改为 **FAIL**（CI 联调推荐）。
- `test_scenario_compat.py` 保留旧 pytest 函数名别名，与 `test_r1_happy_path_*_real` 重复，日常不必单独跑。

## 目录结构

```text
tests/
  README.md
  test_*.py                 应用层单元测试（agent audit、config overlays 等）
  corpus/
    manifest.toml           验证语料声明（含 happy_path_current）
    verify.toml             基础配置（target、LLM 基础设施、套件默认）
    param_sets/             命名参数集（行为参数）
    books/                  epub 文件（gitignore）
  system_verify/
    __main__.py             vibe-verify CLI 入口
    conftest.py             pytest fixtures 与 marker 注册
    core/                   RunSpec、Orchestrator、ScenarioContext、config、run_manager
    flows/                  场景步骤实现（reading、comments、compaction、import…）
    assertions/             断言逻辑（与 flow 分离）
    modes/                  stub_aimock / real_llm 环境生命周期
    profiles/               VerificationProfile 与策略
    scenarios/              薄层场景入口 + registry.py
    fixtures/baseline/      Phase 0 冻结产物（test_characterization.py 回归护栏）
    llm_stub/
      aimock/               AIMock sidecar（server.mjs、profiles、fixtures）
      aimock_launcher.py    Python 启停与健康检查
      env.py                stub backend env 注入与 spawn
    report_generator.py     V-16 报告生成
    test_scenarios.py       pytest 集成场景入口
    test_characterization.py
    test_orchestrator.py
    …
```

## 合并前检查（workspace 联调）

```bash
cd backend
uv run ruff check .
uv run pytest -m "not system and not real_llm"
uv run pytest tests/system_verify/test_scenarios.py -m "system and not real_llm" --spawn-backend --require-integration
```
