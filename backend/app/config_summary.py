from __future__ import annotations

from dataclasses import asdict
from typing import Any

from .config_schema import LLMConfig, PERSISTED_SETTINGS_GROUPS, Settings


def effective_model_summary(settings: Settings, agent: str) -> dict[str, Any]:
    llm = settings.effective_llm(agent)
    model = settings.effective_model(agent)
    return {
        "agent": agent,
        "model_id": model.id if model is not None else llm.model_id,
        "provider": llm.provider,
        "model_name": llm.model,
        "think_effort": llm.think_effort,
        "source": llm.source,
        "base_url_configured": bool(llm.base_url),
        "api_key_configured": bool(llm.api_key),
    }


def effective_models_summary(settings: Settings) -> dict[str, Any]:
    return {
        "global": effective_model_summary(settings, "global"),
        "chat": effective_model_summary(settings, "chat"),
        "comment": effective_model_summary(settings, "comment"),
        "compaction": effective_model_summary(settings, "compaction"),
    }


def llm_summary(llm: LLMConfig) -> dict[str, Any]:
    return {
        "base_url_configured": bool(llm.base_url),
        "api_key_configured": bool(llm.api_key),
        "model": llm.model,
        "model_name": llm.model,
        "provider": llm.provider,
        "source": llm.source,
    }


def model_catalog_document(settings: Settings) -> dict[str, Any]:
    return {
        "models": settings.public_models(),
        "defaults": asdict(settings.defaults),
        "active": asdict(settings.active),
    }


def config_groups(settings: Settings) -> dict[str, Any]:
    return {
        group_name: asdict(getattr(settings, group_name))
        for group_name in PERSISTED_SETTINGS_GROUPS
    }


def runtime_summary(settings: Settings) -> dict[str, Any]:
    return {
        "app": "vibe-reader-mini",
        "version": "0.1.0",
        "data_dir": str(settings.data_dir),
        "verify_mode": settings.verify_mode,
        "llm": llm_summary(settings.effective_llm("global")),
        "models": {
            "catalog_count": len(settings.models),
            "effective": effective_models_summary(settings),
        },
        "observability": {
            "enabled": settings.observability.enabled,
            "provider": settings.observability.provider,
        },
    }


def settings_summary(settings: Settings) -> dict[str, Any]:
    return {
        **model_catalog_document(settings),
        "effective": effective_models_summary(settings),
        "llm": llm_summary(settings.effective_llm("global")),
        "reader": asdict(settings.reader),
        "context": {
            **asdict(settings.context),
            "effective_input_budget": settings.context.normal_target_input_tokens,
            "hard_input_cap": settings.context.emergency_input_cap_tokens,
        },
        "window_l1": {
            "lookahead_paragraphs": settings.reader.lookahead_paragraphs,
            **asdict(settings.window_l1),
        },
        "env": {
            "overrides": settings.env_overrides,
            "ignored": settings.ignored_env,
            "read_only": settings.read_only_env,
        },
    }


def config_document(settings: Settings) -> dict[str, Any]:
    catalog = model_catalog_document(settings)
    return {
        "config": {
            **catalog,
            "groups": config_groups(settings),
        },
        **catalog,
        "effective": effective_models_summary(settings),
        "metadata": settings.ui_metadata(),
        "runtime": runtime_summary(settings),
        "policy": {
            "in_flight_model_switch": (
                "进行中的 Chat 流和 running 评论任务沿用启动时模型；新请求和新任务使用更新后的当前配置。"
            ),
            "compaction_model": "Context Compaction Agent 与 Comment Agent 共用模型。",
        },
    }
