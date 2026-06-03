# Backend 测试

本目录只包含 backend 的单元测试与模块级测试，覆盖 service、配置、上下文窗口、LLM adapter、审计记录等后端内部行为。

旧的 backend 内置系统验证套件已经移除。系统级黑盒验证由 workspace 根目录的 `verify/` 工程维护；使用说明见 [../../verify/README.md](../../verify/README.md)。

## 运行

所有命令在 `backend/` 目录下通过 uv 执行：

```bash
cd backend
uv sync --extra dev
uv run pytest
```

按文件或单个测试收窄范围：

```bash
uv run pytest tests/test_comment_service.py
uv run pytest tests/test_window_service.py::test_window_not_recreated_when_latest_focus_already_covers_frontier
```

## 目录结构

```text
tests/
  README.md
  test_*.py        backend 单元测试与模块级测试
```

## 合并前检查

```bash
cd backend
uv run ruff check .
uv run pytest
```
