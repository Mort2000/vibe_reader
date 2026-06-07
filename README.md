# Vibe Reader Mini

本地单用户小说阅读器：epub 导入、章节阅读、阅读进度与 AI 伴读。

## 环境要求

- [uv](https://docs.astral.sh/uv/)（Python 依赖与虚拟环境管理）
- Python 3.11+（由 uv 自动解析）
- Node.js 18+（前端开发）

## 项目结构

```text
vibe_reader/
  backend/          FastAPI 后端（uv 项目根）
  frontend/         React + TypeScript + Vite 前端
  verify/           独立黑盒系统验证工程
```

本地数据目录默认为 `~/.vibe_reader/`（可通过 `VIBE_READER_DATA_DIR` 覆盖）：

```text
~/.vibe_reader/
  vibe_reader.db
  books/
  config.toml
  logs/
```

## 快速开始

### 1. 安装依赖

```bash
# 后端：editable 安装，注册 vibe-reader CLI
cd backend
uv sync --extra dev

# 前端
cd ../frontend
npm install
```

### 2. 启动后端

在 `backend/` 目录：

```bash
set -a && source .env && set +a
uv run vibe-reader
```

`vibe-reader` 以 reload 模式监听 `127.0.0.1:8000`。若不需要覆盖环境变量，日常开发可省略 `source` 步骤。

健康检查：

```bash
curl http://127.0.0.1:8000/api/health
curl http://127.0.0.1:8000/api/runtime
```

### 3. 启动前端（开发模式）

在 `frontend/` 目录：

```bash
npm run dev
```

Vite 会将 `/api` 代理到 `http://127.0.0.1:8000`。浏览器打开终端提示的地址（通常 `http://127.0.0.1:5173`）。

### 4. 一体化静态部署（可选）

```bash
cd frontend && npm run build
cd ../backend && uv run vibe-reader
```

访问 `http://127.0.0.1:8000/` 即可使用阅读界面。

## 常用环境变量

| 变量 | 说明 |
|---|---|
| `VIBE_READER_DATA_DIR` | 数据目录（默认 `~/.vibe_reader`） |
| `VIBE_READER_LLM_BASE_URL` | 兼容旧版的 LLM API base URL；仅在本地模型目录为空时作为只读运行时配置 |
| `VIBE_READER_LLM_API_KEY` | 兼容旧版的 LLM API key；不会被配置页自动持久化 |
| `VIBE_READER_LLM_MODEL` | 兼容旧版的模型名（默认 `deepseek-v4-flash`）；已有模型目录时会被忽略 |
| `VIBE_READER_VERIFY_MODE` | 设为 `1` 启用 `/api/verify/*` 诊断接口 |

完整列表见 [backend/.env.example](backend/.env.example)。

## 测试

后端单元/模块测试在 `backend/` 目录运行：

```bash
uv run pytest
```

系统验证由独立 `verify/` 工程提供，说明见 [verify/README.md](verify/README.md)。

## 配置说明

推荐通过浏览器里的“配置管理”页面维护后端 Settings 和模型目录。开发模式下打开 Vite 地址后进入“配置”，一体化部署时访问 `http://127.0.0.1:8000/config`。该页面支持创建多个模型、测试连接、为 Chat 与评论分别指定默认模型、切换当前生效模型、查看默认值和说明、以及单项/分组/常用重置。

`config.toml` 仍是本地持久化载体。多模型配置示例：

```toml
[[models]]
id = "default"
provider = "openai_compatible"
url = "https://api.example.com/v1"
model_name = "deepseek-v4-flash"
api_key = ""
think_effort = ""

[defaults]
global_model_id = "default"
chat_model_id = "default"
comment_model_id = "default"

[active]
global_model_id = ""
chat_model_id = ""
comment_model_id = ""

[reader]
lookahead_paragraphs = 5
progress_debounce_ms = 800

[window_l1]
focus_target_tokens = 6000
focus_max_tokens = 12000
```

旧版 `[llm]` 文件会在模型目录为空时自动迁移成 `[[models]]` 的默认条目，并清理 `[llm]`。一旦 `[[models]]` 非空，本地模型目录就是 LLM 配置的权威来源，旧 `[llm]` 和 `VIBE_READER_LLM_*` 不会混入目录或当前选择。非 LLM 的环境变量覆盖行为保持不变：配置页只读展示环境变量覆盖，不会写入环境变量。

Chat Agent 与 Comment Agent 可使用不同模型；Context Compaction Agent 与 Comment Agent 共用同一默认/当前模型。页面内切换当前模型后，新发起的 Chat 流、评论 job 和压缩任务使用新模型；已经开始的 Chat 流和 running 评论 job 沿用启动时的模型快照。

`api_key` 在配置 API、运行摘要、状态中心和配置页中只显示掩码。不要提交包含真实密钥的 `config.toml`。

更多验收和排障说明见 [配置管理验收说明](docs/configuration_management.md) 与 [后端维测配置与观测指导](docs/backend_observability_runbook.md)。
