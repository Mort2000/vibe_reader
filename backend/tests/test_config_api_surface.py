from __future__ import annotations

import json
import stat
from typing import Any

import httpx
import pytest
import toml
from fastapi import FastAPI

from app.config import MASKED_SECRET, ModelConfig, ModelDefaultsConfig, Settings, load_settings
from app.errors import AppError, app_error_handler
from app.infrastructure.settings import SettingsProvider
from app.routers.config import router as config_router
from app.routers.health import router as health_router
from app.services.token_estimator import TokenEstimator


ENV_KEYS = [
    "VIBE_READER_DATA_DIR",
    "VIBE_READER_LLM_BASE_URL",
    "VIBE_READER_LLM_API_KEY",
    "VIBE_READER_LLM_MODEL",
    "VIBE_READER_LOG_LEVEL",
    "VIBE_READER_LOG_FORMAT",
    "VIBE_READER_LOG_SINKS",
    "VIBE_READER_OTEL_ENDPOINT",
    "VIBE_READER_OTEL_ENABLED",
    "VIBE_READER_ENVIRONMENT",
    "VIBE_READER_VERIFY_MODE",
]


@pytest.fixture(autouse=True)
def clear_config_api_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def _app(settings: Settings) -> FastAPI:
    app = FastAPI()
    app.state.settings = settings
    app.state.settings_provider = SettingsProvider(settings)
    app.state.token_estimator = TokenEstimator(settings.token_estimation)
    app.add_exception_handler(AppError, app_error_handler)  # type: ignore[arg-type]
    app.include_router(config_router, prefix="/api")
    app.include_router(health_router, prefix="/api")
    return app


async def _client(app: FastAPI) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


@pytest.mark.asyncio
async def test_read_config_migrates_legacy_llm_and_masks_secrets(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("VIBE_READER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("VIBE_READER_LLM_BASE_URL", "https://env.example/v1")
    monkeypatch.setenv("VIBE_READER_LLM_API_KEY", "env-secret")
    monkeypatch.setenv("VIBE_READER_LLM_MODEL", "env-model")
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[llm]
base_url = "https://legacy.example/v1"
api_key = "legacy-secret"
model = "legacy-model"
""",
        encoding="utf-8",
    )

    app = _app(load_settings(write_migrations=False))
    async with await _client(app) as client:
        response = await client.get("/api/config")

    assert response.status_code == 200
    body = response.json()
    assert body["models"][0]["api_key"] == MASKED_SECRET
    assert body["models"][0]["api_key_configured"] is True
    assert body["effective"]["chat"]["model_name"] == "legacy-model"
    assert body["metadata"]["ignored_env"]["models"] == [
        "VIBE_READER_LLM_API_KEY",
        "VIBE_READER_LLM_BASE_URL",
        "VIBE_READER_LLM_MODEL",
    ]
    assert "legacy-secret" not in response.text
    assert "env-secret" not in response.text

    written = toml.load(config_path)
    assert "llm" not in written
    assert written["models"][0]["api_key"] == "legacy-secret"
    assert stat.S_IMODE(config_path.stat().st_mode) == 0o600


@pytest.mark.asyncio
async def test_save_config_writes_catalog_and_keeps_responses_redacted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("VIBE_READER_DATA_DIR", str(tmp_path))
    app = _app(load_settings(write_migrations=False))
    payload = {
        "models": [
            {
                "id": "chat",
                "provider": "openai_compatible",
                "url": "https://chat.example/v1",
                "model_name": "chat-model",
                "api_key": "chat-secret",
            },
            {
                "id": "comment",
                "provider": "openai_compatible",
                "url": "https://comment.example/v1",
                "model_name": "comment-model",
                "api_key": "comment-secret",
            },
        ],
        "defaults": {
            "global_model_id": "chat",
            "chat_model_id": "chat",
            "comment_model_id": "comment",
        },
        "active": {"chat_model_id": "comment"},
        "groups": {"reader": {"lookahead_paragraphs": 12}},
    }

    async with await _client(app) as client:
        response = await client.put("/api/config", json=payload)
        runtime = await client.get("/api/runtime")
        settings_summary = await client.get("/api/settings")

    assert response.status_code == 200
    assert "chat-secret" not in response.text
    assert "comment-secret" not in response.text
    body = response.json()
    assert body["effective"]["chat"]["model_name"] == "comment-model"
    assert body["effective"]["comment"]["model_name"] == "comment-model"
    assert app.state.settings_provider.current().effective_llm("chat").model == (
        "comment-model"
    )

    assert runtime.json()["models"]["effective"]["chat"]["model_name"] == (
        "comment-model"
    )
    assert "font_size" not in settings_summary.json()["reader"]
    assert settings_summary.json()["effective"]["chat"]["model_name"] == "comment-model"

    written = toml.load(tmp_path / "config.toml")
    assert "llm" not in written
    assert written["models"][0]["api_key"] == "chat-secret"
    assert written["models"][1]["api_key"] == "comment-secret"
    assert written["reader"]["lookahead_paragraphs"] == 12


@pytest.mark.asyncio
async def test_save_preserves_env_overridden_persisted_value_and_reset_defaults_it(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("VIBE_READER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("VIBE_READER_LOG_LEVEL", "DEBUG")
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[observability]
log_level = "WARNING"
log_format = "json"
""",
        encoding="utf-8",
    )
    app = _app(load_settings(write_migrations=False))

    async with await _client(app) as client:
        save_response = await client.put(
            "/api/config",
            json={"groups": {"observability": {"log_level": "ERROR", "log_format": "text"}}},
        )
        written_after_save = toml.load(config_path)
        reset_response = await client.post(
            "/api/config/reset",
            json={"scope": "field", "path": "observability.log_level"},
        )

    assert save_response.status_code == 200
    assert written_after_save["observability"]["log_level"] == "WARNING"
    assert written_after_save["observability"]["log_format"] == "text"
    assert save_response.json()["metadata"]["env_overrides"][
        "observability.log_level"
    ] == "VIBE_READER_LOG_LEVEL"

    assert reset_response.status_code == 200
    written_after_reset = toml.load(config_path)
    assert written_after_reset["observability"]["log_level"] == "INFO"
    assert reset_response.json()["config"]["groups"]["observability"]["log_level"] == (
        "DEBUG"
    )


@pytest.mark.asyncio
async def test_model_crud_rejects_deleting_referenced_models_and_switches_active(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("VIBE_READER_DATA_DIR", str(tmp_path))
    settings = Settings(
        data_dir=tmp_path,
        models=[
            ModelConfig(id="chat", model_name="chat-model", api_key="chat-secret"),
            ModelConfig(
                id="comment",
                model_name="comment-model",
                api_key="comment-secret",
            ),
        ],
        defaults=ModelDefaultsConfig(
            global_model_id="chat",
            chat_model_id="chat",
            comment_model_id="comment",
        ),
    )
    app = _app(settings)

    async with await _client(app) as client:
        create_response = await client.post(
            "/api/config/models",
            json={
                "id": "draft",
                "url": "https://draft.example/v1",
                "model_name": "draft-model",
                "api_key": "draft-secret",
            },
        )
        update_response = await client.put(
            "/api/config/models/draft",
            json={"model_name": "draft-v2", "api_key": MASKED_SECRET},
        )
        delete_draft_response = await client.delete("/api/config/models/draft")
        delete_response = await client.delete("/api/config/models/comment")
        switch_response = await client.post(
            "/api/config/active",
            json={"scope": "comment", "model_id": "chat"},
        )

    assert create_response.status_code == 200
    assert "draft-secret" not in create_response.text
    assert create_response.json()["effective"]["global"]["model_name"] == "chat-model"
    assert update_response.status_code == 200
    draft = next(
        model for model in update_response.json()["models"] if model["id"] == "draft"
    )
    assert draft["model_name"] == "draft-v2"
    assert draft["api_key"] == MASKED_SECRET
    assert delete_draft_response.status_code == 200
    assert all(
        model["id"] != "draft" for model in delete_draft_response.json()["models"]
    )
    assert delete_response.status_code == 409
    assert delete_response.json()["error"]["details"]["fields"][0]["path"] == (
        "defaults.comment_model_id"
    )
    assert switch_response.status_code == 200
    switched = switch_response.json()
    assert switched["effective"]["comment"]["model_name"] == "chat-model"
    assert switched["effective"]["compaction"]["model_name"] == "chat-model"
    assert toml.load(tmp_path / "config.toml")["active"]["comment_model_id"] == "chat"


@pytest.mark.asyncio
async def test_reset_group_and_common_preset(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("VIBE_READER_DATA_DIR", str(tmp_path))
    app = _app(load_settings(write_migrations=False))

    async with await _client(app) as client:
        save_response = await client.put(
            "/api/config",
            json={
                "groups": {
                    "reader": {"lookahead_paragraphs": 33},
                    "context": {"normal_target_input_tokens": 999},
                }
            },
        )
        group_reset = await client.post(
            "/api/config/reset",
            json={"scope": "group", "group": "reader"},
        )
        preset_reset = await client.post(
            "/api/config/reset",
            json={"scope": "preset", "preset": "context_budget"},
        )

    assert save_response.status_code == 200
    assert group_reset.status_code == 200
    assert group_reset.json()["config"]["groups"]["reader"][
        "lookahead_paragraphs"
    ] == 5
    assert preset_reset.status_code == 200
    assert preset_reset.json()["config"]["groups"]["context"][
        "normal_target_input_tokens"
    ] == 112_000


@pytest.mark.asyncio
async def test_model_ping_works_outside_verify_and_preserves_masked_secret(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("VIBE_READER_DATA_DIR", str(tmp_path))
    settings = Settings(
        data_dir=tmp_path,
        models=[
            ModelConfig(
                id="main",
                url="https://provider.example/v1",
                model_name="saved-model",
                api_key="saved-secret",
            )
        ],
        defaults=ModelDefaultsConfig(global_model_id="main"),
    )
    app = _app(settings)
    captured: dict[str, Any] = {}

    async def fake_ping(model: ModelConfig, timeout_s: float = 60.0) -> dict[str, Any]:
        captured["model"] = model
        captured["timeout_s"] = timeout_s
        return {
            "ok": True,
            "model": model.model_name,
            "latency_ms": 1.2,
            "tokens": {"input": 1, "output": 1, "cached_input": None},
        }

    monkeypatch.setattr("app.routers.config.ping_llm", fake_ping)

    async with await _client(app) as client:
        response = await client.post(
            "/api/config/models/ping",
            json={
                "model_id": "main",
                "model": {
                    "id": "main",
                    "model_name": "draft-model",
                    "api_key": MASKED_SECRET,
                },
            },
        )

    assert response.status_code == 200
    assert response.json()["model"] == "draft-model"
    assert response.json()["model_id"] == "main"
    assert captured["model"].api_key == "saved-secret"
    assert "saved-secret" not in json.dumps(response.json())


@pytest.mark.asyncio
async def test_save_config_returns_chinese_field_errors(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("VIBE_READER_DATA_DIR", str(tmp_path))
    app = _app(load_settings(write_migrations=False))

    async with await _client(app) as client:
        response = await client.put(
            "/api/config",
            json={
                "models": [{"id": "main", "model_name": "model"}],
                "defaults": {"chat_model_id": "missing"},
            },
        )

    assert response.status_code == 422
    field_errors = response.json()["error"]["details"]["fields"]
    assert field_errors == [
        {"path": "defaults.chat_model_id", "message": "引用的模型不存在"}
    ]
