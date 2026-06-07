from __future__ import annotations

from dataclasses import fields, is_dataclass
from typing import Any

from .config_dataclasses import read_path
from .config_schema import (
    MASKED_SECRET,
    SECRET_UNCHANGED_SENTINEL,
    DEFAULT_LLM_PROVIDER,
    ModelConfig,
    Settings,
    THINK_EFFORT_VALUES,
)


GROUP_INFO = {
    "models": ("模型管理", "维护可被 Chat、评论和压缩任务引用的 LLM 连接配置。"),
    "defaults": ("Agent 默认模型", "指定各 Agent 未临时切换时使用的模型目录条目。"),
    "active": ("当前生效模型", "记录运行时当前选择；空值表示沿用 Agent 默认或全局默认。"),
    "reader": ("阅读", "控制阅读位置推进、前瞻范围和进度写入节奏。"),
    "window_l1": ("窗口 L1", "控制当前阅读窗口大小、重叠和评论密度提示。"),
    "context": ("上下文", "控制供应商上下文上限、输入预算和锚点保留策略。"),
    "context_l2": ("上下文 L2", "控制原文 chunk 划分和活跃原文预算。"),
    "context_l3": ("上下文 L3 与压缩", "控制压缩触发阈值、回收策略和超时。"),
    "ephemeral_comments": ("评论临时上下文", "控制评论生成时附带的短期阅读状态。"),
    "ephemeral_chat": ("聊天临时上下文", "控制 Chat 请求附带的近期对话轮数和预算。"),
    "token_estimation": ("Token 估算", "控制本地 token 估算安全边际和校准窗口。"),
    "observability": ("可观测性", "控制日志、OTEL 和 Agent 审计输出。"),
}

FIELD_INFO: dict[str, dict[str, Any]] = {
    "models[].id": {
        "label": "模型 ID",
        "description": "模型目录中的唯一标识，用于默认模型和当前模型引用。",
        "type": "string",
        "constraints": {"required": True, "pattern": "非空唯一字符串"},
    },
    "models[].provider": {
        "label": "提供方",
        "description": "LLM 提供方类型；当前运行时使用 OpenAI 兼容协议，并预留扩展值。",
        "type": "enum",
        "constraints": {"default": DEFAULT_LLM_PROVIDER},
    },
    "models[].url": {
        "label": "API Base URL",
        "description": "OpenAI 兼容接口的 base URL，通常以 /v1 结尾。",
        "type": "string",
        "constraints": {"format": "url"},
    },
    "models[].model_name": {
        "label": "模型名称",
        "description": "发送给 provider 的模型名称。",
        "type": "string",
        "constraints": {"required": True},
    },
    "models[].api_key": {
        "label": "API Key",
        "description": "访问密钥；读取时只返回掩码，未修改保存时应保留原值。",
        "type": "secret",
        "constraints": {"masked_readback": MASKED_SECRET},
    },
    "models[].think_effort": {
        "label": "思考力度",
        "description": "供支持 reasoning/thinking 的模型使用；不支持时可留空。",
        "type": "enum",
        "constraints": {"values": sorted(THINK_EFFORT_VALUES)},
    },
    "defaults.global_model_id": {
        "label": "全局默认模型",
        "description": "未设置 Agent 默认时使用的模型目录引用。",
        "type": "model_ref",
    },
    "defaults.chat_model_id": {
        "label": "Chat 默认模型",
        "description": "ReadingChatAgent 默认使用的模型目录引用。",
        "type": "model_ref",
    },
    "defaults.comment_model_id": {
        "label": "评论默认模型",
        "description": "ParagraphCommentAgent 默认使用的模型；ContextCompactionAgent 与它共用。",
        "type": "model_ref",
    },
    "active.global_model_id": {
        "label": "全局当前模型",
        "description": "运行时当前全局模型；空值表示使用全局默认。",
        "type": "model_ref",
    },
    "active.chat_model_id": {
        "label": "Chat 当前模型",
        "description": "Chat 的运行时临时模型；空值表示使用 Chat 默认。",
        "type": "model_ref",
    },
    "active.comment_model_id": {
        "label": "评论当前模型",
        "description": "评论与压缩的运行时临时模型；空值表示使用评论默认。",
        "type": "model_ref",
    },
    "reader.lookahead_paragraphs": {
        "label": "前瞻段落数",
        "description": "阅读位置前方纳入助手处理范围的段落数量。",
        "constraints": {"min": 0},
    },
    "reader.progress_debounce_ms": {
        "label": "进度防抖毫秒",
        "description": "阅读进度写入和后台处理触发的防抖时间。",
        "constraints": {"min": 0},
    },
    "window_l1.focus_target_tokens": {
        "label": "焦点目标 token",
        "description": "当前阅读焦点窗口的目标 token 数。",
        "constraints": {"min": 1},
    },
    "window_l1.focus_max_tokens": {
        "label": "焦点最大 token",
        "description": "当前阅读焦点窗口允许的最大 token 数。",
        "constraints": {"min": 1},
    },
    "window_l1.min_focus_paragraphs": {
        "label": "最小焦点段落",
        "description": "焦点窗口至少保留的段落数。",
        "constraints": {"min": 1},
    },
    "window_l1.max_focus_paragraphs": {
        "label": "最大焦点段落",
        "description": "焦点窗口最多保留的段落数。",
        "constraints": {"min": 1},
    },
    "window_l1.overlap_paragraphs": {
        "label": "窗口重叠段落",
        "description": "相邻阅读窗口之间保留的重叠段落数。",
        "constraints": {"min": 0},
    },
    "window_l1.trigger_advance_ratio": {
        "label": "推进触发比例",
        "description": "阅读超过窗口比例后触发下一轮窗口推进。",
        "constraints": {"min": 0, "max": 1},
    },
    "window_l1.comment_density_soft_min": {
        "label": "评论软密度",
        "description": "用于提示评论 Agent 补足评论数量的软目标密度。",
        "constraints": {"min": 0, "max": 1},
    },
    "window_l1.comment_density_stat_window_paragraphs": {
        "label": "密度统计段落",
        "description": "计算近期评论密度时使用的段落窗口大小。",
        "constraints": {"min": 1},
    },
    "context.provider_context_limit_tokens": {
        "label": "供应商上下文上限",
        "description": "目标模型可接受的最大上下文 token 预算。",
        "constraints": {"min": 1},
    },
    "context.attention_target_input_tokens": {
        "label": "注意力目标输入",
        "description": "希望模型重点处理的输入 token 目标。",
        "constraints": {"min": 1},
    },
    "context.normal_target_input_tokens": {
        "label": "常规输入目标",
        "description": "正常请求构建上下文时的输入 token 目标。",
        "constraints": {"min": 1},
    },
    "context.compression_target_input_tokens": {
        "label": "压缩输入目标",
        "description": "压缩任务构建上下文时的输入 token 目标。",
        "constraints": {"min": 1},
    },
    "context.emergency_input_cap_tokens": {
        "label": "紧急输入上限",
        "description": "上下文退化时允许的硬输入上限。",
        "constraints": {"min": 1},
    },
    "context.reserved_tokens": {
        "label": "保留输出 token",
        "description": "为模型输出和工具调用预留的 token 预算。",
        "constraints": {"min": 0},
    },
    "context.target_chapter_summary_tokens": {
        "label": "章节摘要目标 token",
        "description": "压缩后章节摘要的目标长度。",
        "constraints": {"min": 1},
    },
    "context.max_chapter_summary_tokens": {
        "label": "章节摘要最大 token",
        "description": "压缩后章节摘要允许的最大长度。",
        "constraints": {"min": 1},
    },
    "context.max_anchor_excerpts": {
        "label": "最大锚点摘录数",
        "description": "压缩摘要中保留的关键原文锚点数量上限。",
        "constraints": {"min": 0},
    },
    "context.max_anchor_excerpt_tokens": {
        "label": "单条锚点 token",
        "description": "每条锚点摘录允许的最大 token 数。",
        "constraints": {"min": 1},
    },
    "context.max_context_jump_chars": {
        "label": "最大跳读字符",
        "description": "允许一次前向跳读补处理的最大字符数。",
        "constraints": {"min": 0},
    },
    "context.max_context_jump_tokens_estimate": {
        "label": "最大跳读 token",
        "description": "允许一次前向跳读补处理的最大估算 token 数。",
        "constraints": {"min": 0},
    },
    "context_l2.target_chunk_tokens": {
        "label": "Chunk 目标 token",
        "description": "L2 原文 chunk 的目标 token 数。",
        "constraints": {"min": 1},
    },
    "context_l2.min_chunk_tokens": {
        "label": "Chunk 最小 token",
        "description": "L2 原文 chunk 的最小 token 数。",
        "constraints": {"min": 1},
    },
    "context_l2.max_chunk_tokens": {
        "label": "Chunk 最大 token",
        "description": "L2 原文 chunk 的最大 token 数。",
        "constraints": {"min": 1},
    },
    "context_l2.max_chunk_chars": {
        "label": "Chunk 最大字符",
        "description": "L2 原文 chunk 的最大字符数。",
        "constraints": {"min": 1},
    },
    "context_l2.max_chunk_paragraphs": {
        "label": "Chunk 最大段落",
        "description": "L2 原文 chunk 的最大段落数。",
        "constraints": {"min": 1},
    },
    "context_l2.target_live_original_tokens": {
        "label": "活跃原文目标 token",
        "description": "未压缩活跃原文保留的目标 token 数。",
        "constraints": {"min": 1},
    },
    "context_l2.max_live_original_tokens": {
        "label": "活跃原文最大 token",
        "description": "未压缩活跃原文保留的最大 token 数。",
        "constraints": {"min": 1},
    },
    "context_l2.min_live_chunks_after_compaction": {
        "label": "压缩后最少活跃 chunk",
        "description": "每轮压缩后仍需保留的活跃原文 chunk 数。",
        "constraints": {"min": 0},
    },
    "context_l2.compaction_reclaim_chunk_count": {
        "label": "L2 回收 chunk 数",
        "description": "压缩时计划回收的已完成 chunk 数。",
        "constraints": {"min": 1},
    },
    "context_l3.preflight_trigger_input_tokens": {
        "label": "预检触发 token",
        "description": "进入压缩预检流程的输入 token 阈值。",
        "constraints": {"min": 1},
    },
    "context_l3.compression_trigger_input_tokens": {
        "label": "压缩触发 token",
        "description": "真正触发 L3 压缩任务的输入 token 阈值。",
        "constraints": {"min": 1},
    },
    "context_l3.max_completed_l2_chunks_before_compaction": {
        "label": "压缩前最大完成 chunk",
        "description": "超过该完成 chunk 数后优先触发压缩。",
        "constraints": {"min": 1},
    },
    "context_l3.min_completed_l2_chunks_before_compaction": {
        "label": "压缩前最小完成 chunk",
        "description": "未达到该完成 chunk 数时避免过早压缩。",
        "constraints": {"min": 1},
    },
    "context_l3.compaction_reclaim_chunk_count": {
        "label": "L3 回收 chunk 数",
        "description": "每次 L3 压缩尝试回收的 chunk 数。",
        "constraints": {"min": 1},
    },
    "context_l3.compaction_timeout_s": {
        "label": "压缩超时秒",
        "description": "单次压缩 Agent 调用的超时时间。",
        "constraints": {"min": 1},
    },
    "context_l3.allow_emergency_overflow_once": {
        "label": "允许一次紧急溢出",
        "description": "压缩未及时完成时是否允许一次临时超过目标预算。",
    },
    "ephemeral_comments.recent_focus_windows": {
        "label": "近期焦点窗口数",
        "description": "评论上下文中保留的近期焦点窗口数量。",
        "constraints": {"min": 0},
    },
    "ephemeral_comments.nearby_paragraph_margin": {
        "label": "附近段落边距",
        "description": "评论临时上下文纳入目标附近段落的范围。",
        "constraints": {"min": 0},
    },
    "ephemeral_comments.max_tokens": {
        "label": "评论临时 token",
        "description": "评论临时上下文的最大 token 预算。",
        "constraints": {"min": 0},
    },
    "ephemeral_comments.compress": {
        "label": "压缩评论临时上下文",
        "description": "是否压缩评论任务的临时上下文。",
    },
    "ephemeral_chat.recent_turns": {
        "label": "近期对话轮数",
        "description": "Chat 上下文中保留的最近完成对话轮数。",
        "constraints": {"min": 0},
    },
    "ephemeral_chat.max_tokens": {
        "label": "聊天临时 token",
        "description": "近期对话历史的最大 token 预算。",
        "constraints": {"min": 0},
    },
    "ephemeral_chat.compress": {
        "label": "压缩聊天临时上下文",
        "description": "是否压缩 Chat 临时上下文。",
    },
    "ephemeral_chat.scope": {
        "label": "聊天历史范围",
        "description": "选择 Chat 临时上下文引用的会话范围。",
        "type": "enum",
        "constraints": {"values": ["current_session"]},
    },
    "token_estimation.token_safety_margin": {
        "label": "Token 安全边际",
        "description": "本地估算值乘以该系数后作为安全估算。",
        "constraints": {"min": 1},
    },
    "token_estimation.calibration_percentile": {
        "label": "校准分位数",
        "description": "使用观测样本的该分位数作为校准依据。",
        "constraints": {"min": 0, "max": 1},
    },
    "token_estimation.calibration_window_size": {
        "label": "校准窗口大小",
        "description": "每个模型和 prompt 版本保留的校准样本数量。",
        "constraints": {"min": 1},
    },
    "token_estimation.min_calibration_samples": {
        "label": "最少校准样本",
        "description": "达到该样本数后才启用滚动校准。",
        "constraints": {"min": 1},
    },
    "token_estimation.default_bootstrap_calibration_ratio": {
        "label": "默认启动校准比",
        "description": "样本不足时使用的默认校准倍率。",
        "constraints": {"min": 0},
    },
    "observability.enabled": {
        "label": "启用可观测性",
        "description": "控制日志和遥测基础能力是否启用。",
    },
    "observability.provider": {
        "label": "可观测性提供方",
        "description": "当前可观测性后端类型。",
        "type": "enum",
        "constraints": {"values": ["otel"]},
    },
    "observability.log_json": {
        "label": "JSON 日志兼容开关",
        "description": "旧配置兼容项；实际输出格式以日志格式为准。",
    },
    "observability.log_format": {
        "label": "日志格式",
        "description": "控制控制台和文件日志的输出格式。",
        "type": "enum",
        "constraints": {"values": ["json", "text"]},
    },
    "observability.log_sinks": {
        "label": "日志输出",
        "description": "选择启用的日志 sink，例如 console、file、otel。",
        "type": "string_list",
    },
    "observability.log_level": {
        "label": "日志级别",
        "description": "后端日志最小输出级别。",
        "type": "enum",
        "constraints": {"values": ["DEBUG", "INFO", "WARNING", "ERROR"]},
    },
    "observability.environment": {
        "label": "运行环境",
        "description": "写入日志和遥测资源的环境名称。",
    },
    "observability.include_prompt_manifest": {
        "label": "审计 prompt 清单",
        "description": "旧配置兼容项；实际审计设置位于 observability.audit。",
    },
    "observability.include_full_prompt": {
        "label": "审计完整 prompt",
        "description": "旧配置兼容项；生产环境通常应关闭。",
    },
    "observability.service_name": {
        "label": "服务名",
        "description": "日志和遥测资源中的服务名称。",
    },
    "observability.otel_endpoint": {
        "label": "OTEL endpoint 兼容项",
        "description": "旧配置兼容字段；实际 endpoint 位于 observability.otel。",
        "constraints": {"format": "url_or_empty"},
    },
    "observability.console.enabled": {
        "label": "控制台日志",
        "description": "是否启用控制台日志输出。",
    },
    "observability.console.stream": {
        "label": "控制台流",
        "description": "控制台日志写入 stdout 或 stderr。",
        "type": "enum",
        "constraints": {"values": ["stdout", "stderr"]},
    },
    "observability.file.enabled": {
        "label": "文件日志",
        "description": "是否启用滚动文件日志。",
    },
    "observability.file.path": {
        "label": "日志文件路径",
        "description": "文件日志路径；相对路径按 data_dir 解析。",
    },
    "observability.file.max_bytes": {
        "label": "日志文件大小",
        "description": "单个日志文件滚动前的最大字节数。",
        "constraints": {"min": 1},
    },
    "observability.file.backup_count": {
        "label": "日志备份数量",
        "description": "滚动文件日志保留的备份文件数量。",
        "constraints": {"min": 0},
    },
    "observability.otel.enabled": {
        "label": "启用 OTEL",
        "description": "是否启用 OpenTelemetry 导出。",
    },
    "observability.otel.endpoint": {
        "label": "OTEL Endpoint",
        "description": "OTEL collector 的 HTTP base URL。",
        "constraints": {"format": "url_or_empty"},
    },
    "observability.otel.protocol": {
        "label": "OTEL 协议",
        "description": "遥测导出协议。",
        "type": "enum",
        "constraints": {"values": ["otlp_http"]},
    },
    "observability.otel.export_traces": {
        "label": "导出 traces",
        "description": "是否导出调用链追踪。",
    },
    "observability.otel.export_metrics": {
        "label": "导出 metrics",
        "description": "是否导出指标。",
    },
    "observability.otel.export_logs": {
        "label": "导出 logs",
        "description": "是否通过 OTEL 导出日志。",
    },
    "observability.otel.sample_ratio": {
        "label": "采样比例",
        "description": "trace 采样比例。",
        "constraints": {"min": 0, "max": 1},
    },
    "observability.audit.enabled": {
        "label": "启用 Agent 审计",
        "description": "是否将 Agent 运行摘要写入审计存储。",
    },
    "observability.audit.include_prompt_manifest": {
        "label": "包含 prompt 清单",
        "description": "审计包是否包含 prompt manifest。",
    },
    "observability.audit.include_full_prompt": {
        "label": "包含完整 prompt",
        "description": "审计包是否包含完整 prompt；可能包含正文，应谨慎开启。",
    },
    "observability.audit.include_model_response": {
        "label": "包含模型响应",
        "description": "审计包是否包含模型输出；默认关闭以降低泄露风险。",
    },
    "observability.audit.redact_secrets": {
        "label": "脱敏密钥",
        "description": "审计输出是否脱敏 api_key、authorization 等敏感字段。",
    },
}


def _value_type(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "float"
    if isinstance(value, list):
        return "string_list"
    return "string"


def _field_metadata(
    path: str,
    value: Any,
    default: Any,
    settings: Settings | None,
) -> dict[str, Any]:
    info = FIELD_INFO.get(path, {})
    metadata = {
        "path": path,
        "label": info.get("label", path.rsplit(".", 1)[-1]),
        "description": info.get("description", "该配置会影响后端运行行为。"),
        "type": info.get("type", _value_type(default)),
        "default": default,
        "constraints": info.get("constraints", {}),
    }
    if settings is not None and path in settings.env_overrides:
        metadata["env_override"] = {
            "env_var": settings.env_overrides[path],
            "effective_value": value,
        }
        metadata["read_only"] = True
    return metadata


def _collect_dataclass_fields(
    group_name: str,
    obj: Any,
    default_obj: Any,
    settings: Settings | None,
    prefix: str = "",
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if not is_dataclass(obj):
        return result
    for item in fields(obj):
        value = getattr(obj, item.name)
        default = getattr(default_obj, item.name, None)
        path = f"{group_name}.{prefix}{item.name}"
        if is_dataclass(value):
            nested_default = default if is_dataclass(default) else value.__class__()
            nested = _collect_dataclass_fields(
                group_name,
                value,
                nested_default,
                settings,
                prefix=f"{prefix}{item.name}.",
            )
            result.update(nested)
            continue
        result[path] = _field_metadata(path, value, default, settings)
    return result


def build_settings_metadata(settings: Settings | None = None) -> dict[str, Any]:
    current = settings or Settings()
    defaults = Settings()
    groups: dict[str, Any] = {}

    for group_name in (
        "models",
        "defaults",
        "active",
        "reader",
        "window_l1",
        "context",
        "context_l2",
        "context_l3",
        "ephemeral_comments",
        "ephemeral_chat",
        "token_estimation",
        "observability",
    ):
        label, description = GROUP_INFO[group_name]
        if group_name == "models":
            fields_meta = {
                key: _field_metadata(
                    key,
                    None,
                    read_path(ModelConfig(), key.removeprefix("models[].")),
                    current,
                )
                for key in FIELD_INFO
                if key.startswith("models[].")
            }
        else:
            fields_meta = _collect_dataclass_fields(
                group_name,
                getattr(current, group_name),
                getattr(defaults, group_name),
                current,
            )
        groups[group_name] = {
            "label": label,
            "description": description,
            "fields": fields_meta,
        }

    groups["models"]["secret_policy"] = {
        "masked_value": MASKED_SECRET,
        "unchanged_sentinel": SECRET_UNCHANGED_SENTINEL,
        "readback": "api_key is never returned in plaintext",
    }
    groups["models"]["ignored_env"] = current.ignored_env.get("models", [])
    groups["models"]["read_only_env"] = current.read_only_env.get("llm", [])
    return {
        "groups": groups,
        "env_overrides": current.env_overrides,
        "ignored_env": current.ignored_env,
        "read_only_env": current.read_only_env,
        "migrations": current.migrations,
    }
