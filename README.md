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
| `VIBE_READER_LLM_BASE_URL` | LLM API base URL |
| `VIBE_READER_LLM_API_KEY` | LLM API key |
| `VIBE_READER_LLM_MODEL` | 模型名（默认 `deepseek-v4-flash`） |
| `VIBE_READER_VERIFY_MODE` | 设为 `1` 启用 `/api/verify/*` 诊断接口 |

完整列表见 [backend/.env.example](backend/.env.example)。

## 测试

后端单元/模块测试在 `backend/` 目录运行：

```bash
uv run pytest
```

系统验证由独立 `verify/` 工程提供，说明见 [verify/README.md](verify/README.md)。

## 配置说明

首次运行可在数据目录创建 `config.toml`，例如：

```toml
[llm]
base_url = ""
api_key = ""
model = "deepseek-v4-flash"

[reader]
lookahead_paragraphs = 5
progress_debounce_ms = 800

[window]
target_window_tokens = 6000
max_window_tokens = 12000
```

敏感项建议通过环境变量注入，不要提交到版本库。
