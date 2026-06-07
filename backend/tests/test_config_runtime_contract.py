from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest
import toml

from app.config import (
    MASKED_SECRET,
    ActiveModelsConfig,
    ModelConfig,
    ModelDefaultsConfig,
    Settings,
    load_settings,
    merge_model_update,
    public_model_config,
)
from app.infrastructure.settings import SettingsProvider
from app.services.agent_base import (
    clear_agent_caches,
    get_chat_agent,
    get_comment_agent,
    get_compaction_agent,
    get_llm_model,
    prune_agent_caches,
)


APP_DIR = Path(__file__).resolve().parents[1] / "app"


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
def clear_contract_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def test_legacy_llm_migrates_to_catalog_and_removes_legacy_section(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("VIBE_READER_DATA_DIR", str(tmp_path))
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[llm]
base_url = "https://legacy.example/v1"
api_key = "legacy-secret"
model = "legacy-model"

[reader]
lookahead_paragraphs = 9
""",
        encoding="utf-8",
    )

    settings = load_settings()

    assert settings.migrations == ["legacy_llm_migrated"]
    assert len(settings.models) == 1
    model = settings.models[0]
    assert model.id == "default"
    assert model.url == "https://legacy.example/v1"
    assert model.api_key == "legacy-secret"
    assert model.model_name == "legacy-model"
    assert settings.defaults.chat_model_id == "default"
    assert settings.defaults.comment_model_id == "default"
    assert settings.effective_llm("chat").model == "legacy-model"

    written = toml.load(config_path)
    assert "llm" not in written
    assert written["reader"]["lookahead_paragraphs"] == 9
    assert written["models"][0]["api_key"] == "legacy-secret"
    assert stat.S_IMODE(config_path.stat().st_mode) == 0o600


def test_catalog_ignores_legacy_llm_and_llm_env_then_cleans_file(
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

[[models]]
id = "kept"
provider = "openai_compatible"
url = "https://catalog.example/v1"
api_key = "catalog-secret"
model_name = "catalog-model"

[defaults]
global_model_id = "kept"
chat_model_id = "kept"
comment_model_id = "kept"
""",
        encoding="utf-8",
    )

    settings = load_settings()

    assert settings.migrations == ["legacy_llm_removed"]
    assert [model.id for model in settings.models] == ["kept"]
    assert settings.llm.base_url == "https://catalog.example/v1"
    assert settings.llm.api_key == "catalog-secret"
    assert settings.llm.model == "catalog-model"
    assert settings.ignored_env["models"] == [
        "VIBE_READER_LLM_API_KEY",
        "VIBE_READER_LLM_BASE_URL",
        "VIBE_READER_LLM_MODEL",
    ]

    written = toml.load(config_path)
    assert "llm" not in written
    assert len(written["models"]) == 1
    assert written["models"][0]["model_name"] == "catalog-model"
    assert "env-secret" not in config_path.read_text(encoding="utf-8")


def test_env_only_llm_is_read_only_runtime_state_and_not_persisted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("VIBE_READER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("VIBE_READER_LLM_BASE_URL", "https://env.example/v1")
    monkeypatch.setenv("VIBE_READER_LLM_API_KEY", "env-secret")
    monkeypatch.setenv("VIBE_READER_LLM_MODEL", "env-model")

    settings = load_settings()

    assert settings.models == []
    assert settings.llm.source == "env"
    assert settings.llm.base_url == "https://env.example/v1"
    assert settings.llm.api_key == "env-secret"
    assert settings.llm.model == "env-model"
    assert settings.read_only_env["llm"] == [
        "VIBE_READER_LLM_API_KEY",
        "VIBE_READER_LLM_BASE_URL",
        "VIBE_READER_LLM_MODEL",
    ]
    assert not (tmp_path / "config.toml").exists()


def test_settings_metadata_includes_defaults_descriptions_and_env_markers(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("VIBE_READER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("VIBE_READER_LOG_LEVEL", "DEBUG")
    settings = load_settings(write_migrations=False)

    metadata = settings.ui_metadata()

    reader_field = metadata["groups"]["reader"]["fields"][
        "reader.lookahead_paragraphs"
    ]
    assert reader_field["label"] == "前瞻段落数"
    assert reader_field["default"] == 5
    assert reader_field["type"] == "integer"
    assert reader_field["constraints"]["min"] == 0
    assert reader_field["description"] != "lookahead_paragraphs"

    log_level = metadata["groups"]["observability"]["fields"][
        "observability.log_level"
    ]
    assert log_level["env_override"] == {
        "env_var": "VIBE_READER_LOG_LEVEL",
        "effective_value": "DEBUG",
    }
    assert log_level["read_only"] is True

    model_key = metadata["groups"]["models"]["fields"]["models[].api_key"]
    assert model_key["type"] == "secret"
    assert metadata["groups"]["models"]["secret_policy"]["masked_value"] == MASKED_SECRET


def test_config_module_is_facade_over_split_settings_modules() -> None:
    config_source = (APP_DIR / "config.py").read_text(encoding="utf-8")
    loader_source = (APP_DIR / "config_loader.py").read_text(encoding="utf-8")

    assert "GROUP_INFO = {" not in config_source
    assert "FIELD_INFO: dict" not in config_source
    assert "def load_settings" not in config_source
    assert "lookahead_paragraphs" not in loader_source
    assert "provider_context_limit_tokens" not in loader_source
    assert "coerce_dataclass_group" in loader_source


def test_non_config_routes_use_settings_service_instead_of_config_router() -> None:
    for route_name in ("books.py", "chat.py", "health.py", "progress.py", "verify.py"):
        route_source = (APP_DIR / "routers" / route_name).read_text(encoding="utf-8")
        assert "from .config import current_settings" not in route_source
        assert "from .config import current_settings, runtime_summary" not in route_source
        assert "from .config import current_settings, settings_summary" not in route_source


def test_secret_readback_and_unchanged_update_do_not_leak_plaintext() -> None:
    model = ModelConfig(
        id="main",
        url="https://provider.example/v1",
        model_name="model-a",
        api_key="plain-secret",
    )

    public = public_model_config(model)

    assert public["api_key"] == MASKED_SECRET
    assert public["api_key_configured"] is True
    assert "plain-secret" not in json.dumps(public)

    unchanged = merge_model_update(
        model,
        {"id": "main", "model_name": "model-b", "api_key": MASKED_SECRET},
    )
    assert unchanged.model_name == "model-b"
    assert unchanged.api_key == "plain-secret"

    replaced = merge_model_update(model, {"api_key": "new-secret"})
    assert replaced.api_key == "new-secret"

    cleared = merge_model_update(model, {"api_key": ""})
    assert cleared.api_key == ""


def test_effective_models_and_agent_caches_are_keyed_by_selection() -> None:
    settings = Settings(
        models=[
            ModelConfig(
                id="chat",
                url="https://chat.example/v1",
                model_name="chat-model",
                api_key="chat-key",
            ),
            ModelConfig(
                id="comment",
                url="https://comment.example/v1",
                model_name="comment-model",
                api_key="comment-key",
                think_effort="medium",
            ),
        ],
        defaults=ModelDefaultsConfig(
            global_model_id="chat",
            chat_model_id="chat",
            comment_model_id="comment",
        ),
    )
    clear_agent_caches()

    assert settings.effective_llm("chat").model == "chat-model"
    assert settings.effective_llm("comment").model == "comment-model"
    assert settings.effective_llm("ContextCompactionAgent").model == "comment-model"

    chat_model = get_llm_model(settings, "chat")
    comment_model = get_llm_model(settings, "comment")
    assert get_llm_model(settings, "chat") is chat_model
    assert chat_model is not comment_model
    assert chat_model.settings is None
    assert comment_model.settings == {"openai_reasoning_effort": "medium"}

    chat_agent = get_chat_agent(settings)
    comment_agent = get_comment_agent(settings)
    compaction_agent = get_compaction_agent(settings)
    assert get_chat_agent(settings) is chat_agent
    assert get_comment_agent(settings) is comment_agent
    assert get_compaction_agent(settings) is compaction_agent

    switched = Settings(
        models=settings.models,
        defaults=settings.defaults,
        active=ActiveModelsConfig(chat_model_id="comment"),
    )
    assert switched.effective_llm("chat").model == "comment-model"
    assert get_chat_agent(switched) is not chat_agent
    assert get_comment_agent(switched) is comment_agent


def test_agent_cache_prune_keeps_current_identities_and_drops_stale_entries() -> None:
    settings = Settings(
        models=[
            ModelConfig(id="chat", model_name="chat-model", api_key="chat-key"),
            ModelConfig(id="comment", model_name="comment-model", api_key="comment-key"),
        ],
        defaults=ModelDefaultsConfig(
            global_model_id="chat",
            chat_model_id="chat",
            comment_model_id="comment",
        ),
    )
    clear_agent_caches()

    old_chat_agent = get_chat_agent(settings)
    comment_agent = get_comment_agent(settings)
    compaction_agent = get_compaction_agent(settings)

    switched = Settings(
        models=settings.models,
        defaults=settings.defaults,
        active=ActiveModelsConfig(chat_model_id="comment"),
    )
    prune_agent_caches(switched)

    assert get_chat_agent(switched) is not old_chat_agent
    assert get_comment_agent(switched) is comment_agent
    assert get_compaction_agent(switched) is compaction_agent


def test_settings_provider_replaces_current_settings() -> None:
    first = Settings()
    second = Settings(
        models=[ModelConfig(id="m2", model_name="model-2")],
        defaults=ModelDefaultsConfig(global_model_id="m2"),
    )
    provider = SettingsProvider(first)

    assert provider.current() is first
    assert provider.replace(second) is second
    assert provider.current().effective_llm("global").model == "model-2"
