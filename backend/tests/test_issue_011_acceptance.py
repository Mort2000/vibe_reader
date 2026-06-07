from __future__ import annotations

import json
import stat
from itertools import product
from pathlib import Path

import httpx
import pytest
import toml
from fastapi import FastAPI

from app.config import MASKED_SECRET, Settings, load_settings
from app.errors import AppError, app_error_handler
from app.infrastructure.settings import SettingsProvider
from app.routers.config import router as config_router
from app.routers.health import router as health_router
from app.services.agent_base import (
    clear_agent_caches,
    get_chat_agent,
    get_comment_agent,
    get_compaction_agent,
    get_llm_model,
)
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

LLM_ENV_KEYS = [
    "VIBE_READER_LLM_API_KEY",
    "VIBE_READER_LLM_BASE_URL",
    "VIBE_READER_LLM_MODEL",
]


@pytest.fixture(autouse=True)
def clear_issue_011_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    clear_agent_caches()


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


def _catalog_toml() -> str:
    return """
[[models]]
id = "catalog"
provider = "openai_compatible"
url = "https://catalog.example/v1"
api_key = "catalog-secret"
model_name = "catalog-model"

[defaults]
global_model_id = "catalog"
chat_model_id = "catalog"
comment_model_id = "catalog"
"""


def _legacy_llm_toml() -> str:
    return """
[llm]
base_url = "https://legacy.example/v1"
api_key = "legacy-secret"
model = "legacy-model"
"""


@pytest.mark.parametrize(
    ("has_models", "has_legacy", "has_llm_env"),
    list(product((False, True), repeat=3)),
)
def test_legacy_and_env_llm_matrix(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    has_models: bool,
    has_legacy: bool,
    has_llm_env: bool,
) -> None:
    monkeypatch.setenv("VIBE_READER_DATA_DIR", str(tmp_path))
    if has_llm_env:
        monkeypatch.setenv("VIBE_READER_LLM_BASE_URL", "https://env.example/v1")
        monkeypatch.setenv("VIBE_READER_LLM_API_KEY", "env-secret")
        monkeypatch.setenv("VIBE_READER_LLM_MODEL", "env-model")

    config_path = tmp_path / "config.toml"
    if has_models or has_legacy:
        config_path.write_text(
            "\n".join(
                part
                for part in (
                    _catalog_toml() if has_models else "",
                    _legacy_llm_toml() if has_legacy else "",
                )
                if part
            ),
            encoding="utf-8",
        )

    settings = load_settings()

    if has_models:
        assert [model.id for model in settings.models] == ["catalog"]
        assert settings.effective_llm("global").model == "catalog-model"
        assert settings.read_only_env == {}
        if has_llm_env:
            assert settings.ignored_env["models"] == LLM_ENV_KEYS
        else:
            assert settings.ignored_env == {}
        if has_legacy:
            assert settings.migrations == ["legacy_llm_removed"]
            written = toml.load(config_path)
            assert "llm" not in written
            assert written["models"][0]["api_key"] == "catalog-secret"
            assert stat.S_IMODE(config_path.stat().st_mode) == 0o600
        assert "env-secret" not in config_path.read_text(encoding="utf-8")
        return

    if has_legacy:
        assert settings.migrations == ["legacy_llm_migrated"]
        assert [model.id for model in settings.models] == ["default"]
        assert settings.defaults.chat_model_id == "default"
        assert settings.defaults.comment_model_id == "default"
        assert settings.effective_llm("chat").model == "legacy-model"
        if has_llm_env:
            assert settings.ignored_env["models"] == LLM_ENV_KEYS
        else:
            assert settings.ignored_env == {}
        written = toml.load(config_path)
        assert "llm" not in written
        assert written["models"][0]["api_key"] == "legacy-secret"
        assert "env-secret" not in config_path.read_text(encoding="utf-8")
        assert stat.S_IMODE(config_path.stat().st_mode) == 0o600
        return

    assert settings.models == []
    assert settings.ignored_env == {}
    if has_llm_env:
        assert settings.llm.source == "env"
        assert settings.llm.model == "env-model"
        assert settings.read_only_env["llm"] == LLM_ENV_KEYS
    else:
        assert settings.llm.source == "default"
        assert settings.read_only_env == {}
    assert not config_path.exists()


@pytest.mark.asyncio
async def test_acceptance_first_setup_defaults_switching_and_status_summaries(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
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
                "think_effort": "medium",
            },
        ],
        "defaults": {
            "global_model_id": "chat",
            "chat_model_id": "chat",
            "comment_model_id": "comment",
        },
    }

    async with await _client(app) as client:
        saved_response = await client.put("/api/config", json=payload)

        assert saved_response.status_code == 200
        saved_text = saved_response.text
        assert "chat-secret" not in saved_text
        assert "comment-secret" not in saved_text
        saved = saved_response.json()
        assert saved["effective"]["chat"]["model_name"] == "chat-model"
        assert saved["effective"]["comment"]["model_name"] == "comment-model"
        assert saved["effective"]["compaction"]["model_name"] == "comment-model"
        assert saved["policy"]["in_flight_model_switch"].startswith("进行中的 Chat 流")

        settings_after_save = app.state.settings_provider.current()
        chat_agent = get_chat_agent(settings_after_save)
        comment_agent = get_comment_agent(settings_after_save)
        compaction_agent = get_compaction_agent(settings_after_save)
        assert settings_after_save.effective_model_identity("chat") == (
            "openai_compatible:chat:chat-model"
        )
        assert settings_after_save.effective_model_identity("comment") == (
            "openai_compatible:comment:comment-model"
        )
        assert settings_after_save.effective_model_identity("compaction") == (
            "openai_compatible:comment:comment-model"
        )
        assert get_llm_model(settings_after_save, "comment") is get_llm_model(
            settings_after_save, "compaction"
        )

        chat_switch = await client.post(
            "/api/config/active",
            json={"scope": "chat", "model_id": "comment"},
        )

        assert chat_switch.status_code == 200
        settings_after_chat_switch = app.state.settings_provider.current()
        assert settings_after_chat_switch.effective_llm("chat").model == "comment-model"
        assert get_chat_agent(settings_after_chat_switch) is not chat_agent
        assert get_comment_agent(settings_after_chat_switch) is comment_agent
        assert get_compaction_agent(settings_after_chat_switch) is compaction_agent

        comment_switch = await client.post(
            "/api/config/active",
            json={"scope": "comment", "model_id": "chat"},
        )

        assert comment_switch.status_code == 200
        settings_after_comment_switch = app.state.settings_provider.current()
        assert settings_after_comment_switch.effective_llm("comment").model == (
            "chat-model"
        )
        assert settings_after_comment_switch.effective_llm("compaction").model == (
            "chat-model"
        )
        assert get_comment_agent(settings_after_comment_switch) is not comment_agent
        assert get_compaction_agent(settings_after_comment_switch) is not compaction_agent

        config_doc = (await client.get("/api/config")).json()
        runtime = (await client.get("/api/runtime")).json()
        settings_summary = (await client.get("/api/settings")).json()

    written = toml.load(tmp_path / "config.toml")
    assert "llm" not in written
    assert written["models"][0]["api_key"] == "chat-secret"
    assert written["models"][1]["api_key"] == "comment-secret"
    assert written["active"] == {
        "global_model_id": "",
        "chat_model_id": "comment",
        "comment_model_id": "chat",
    }
    assert stat.S_IMODE((tmp_path / "config.toml").stat().st_mode) == 0o600

    expected_effective = {
        "global": "chat-model",
        "chat": "comment-model",
        "comment": "chat-model",
        "compaction": "chat-model",
    }
    for agent, model_name in expected_effective.items():
        assert config_doc["effective"][agent]["model_name"] == model_name
        assert runtime["models"]["effective"][agent]["model_name"] == model_name
        assert settings_summary["effective"][agent]["model_name"] == model_name
    assert runtime["models"]["catalog_count"] == 2
    assert settings_summary["models"][0]["api_key"] == MASKED_SECRET


@pytest.mark.asyncio
async def test_metadata_reset_env_override_and_secret_redaction_acceptance(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("VIBE_READER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("VIBE_READER_LOG_LEVEL", "DEBUG")
    app = _app(load_settings(write_migrations=False))

    async with await _client(app) as client:
        saved_response = await client.put(
            "/api/config",
            json={
                "models": [
                    {
                        "id": "main",
                        "url": "https://main.example/v1",
                        "model_name": "main-model",
                        "api_key": "plain-secret",
                    }
                ],
                "defaults": {
                    "global_model_id": "main",
                    "chat_model_id": "main",
                    "comment_model_id": "main",
                },
                "groups": {
                    "reader": {"lookahead_paragraphs": 13},
                    "observability": {"log_level": "ERROR"},
                },
            },
        )
        field_reset = await client.post(
            "/api/config/reset",
            json={"scope": "field", "path": "reader.lookahead_paragraphs"},
        )
        group_reset = await client.post(
            "/api/config/reset",
            json={"scope": "group", "group": "window_l1"},
        )
        preset_reset = await client.post(
            "/api/config/reset",
            json={"scope": "preset", "preset": "observability_common"},
        )
        config_doc = (await client.get("/api/config")).json()
        runtime_text = (await client.get("/api/runtime")).text
        settings_text = (await client.get("/api/settings")).text

    assert saved_response.status_code == 200
    assert field_reset.status_code == 200
    assert group_reset.status_code == 200
    assert preset_reset.status_code == 200
    assert "plain-secret" not in saved_response.text
    assert "plain-secret" not in json.dumps(config_doc, ensure_ascii=False)
    assert "plain-secret" not in runtime_text
    assert "plain-secret" not in settings_text

    metadata = config_doc["metadata"]
    required_groups = {
        "models",
        "reader",
        "window_l1",
        "context",
        "context_l2",
        "context_l3",
        "ephemeral_comments",
        "ephemeral_chat",
        "token_estimation",
        "observability",
    }
    assert required_groups <= set(metadata["groups"])
    for group_name in required_groups:
        group = metadata["groups"][group_name]
        assert group["label"]
        assert group["description"]
        assert group["fields"]
        for field in group["fields"].values():
            assert field["label"]
            assert field["description"]
            assert field["description"] != field["path"]
            assert "default" in field
            assert field["type"]

    log_level = metadata["groups"]["observability"]["fields"][
        "observability.log_level"
    ]
    assert log_level["read_only"] is True
    assert log_level["env_override"] == {
        "env_var": "VIBE_READER_LOG_LEVEL",
        "effective_value": "DEBUG",
    }
    assert preset_reset.json()["config"]["groups"]["observability"]["log_level"] == (
        "DEBUG"
    )

    audit_defaults = preset_reset.json()["config"]["groups"]["observability"]["audit"]
    assert audit_defaults["redact_secrets"] is True
    assert audit_defaults["include_model_response"] is False


def test_configuration_page_does_not_persist_or_log_config_document() -> None:
    page_path = (
        Path(__file__).resolve().parents[2]
        / "frontend"
        / "src"
        / "features"
        / "configuration"
        / "ConfigurationPage.tsx"
    )
    source = page_path.read_text(encoding="utf-8")

    forbidden_tokens = ("localStorage", "sessionStorage", "console.")
    assert not any(token in source for token in forbidden_tokens)
