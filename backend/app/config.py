from __future__ import annotations

import os
import pathlib
from dataclasses import dataclass, field
from typing import Any

import toml


def _default_data_dir() -> pathlib.Path:
    return pathlib.Path.home() / ".vibe_reader"


def _env(key: str, default: str | None = None) -> str | None:
    return os.environ.get(key, default)


@dataclass
class LLMConfig:
    base_url: str = ""
    api_key: str = ""
    model: str = "deepseek-v4-flash"


@dataclass
class ReaderConfig:
    lookahead_paragraphs: int = 5
    progress_debounce_ms: int = 800


@dataclass
class ContextConfig:
    provider_context_limit_tokens: int = 1_000_000
    attention_target_input_tokens: int = 128_000
    normal_target_input_tokens: int = 112_000
    compression_target_input_tokens: int = 128_000
    emergency_input_cap_tokens: int = 160_000
    reserved_tokens: int = 12_000
    target_chapter_summary_tokens: int = 7_000
    max_chapter_summary_tokens: int = 10_000
    max_anchor_excerpts: int = 12
    max_anchor_excerpt_tokens: int = 120
    max_context_jump_chars: int = 24_000
    max_context_jump_tokens_estimate: int = 24_000


@dataclass
class ContextL2Config:
    target_chunk_tokens: int = 24_000
    min_chunk_tokens: int = 18_000
    max_chunk_tokens: int = 32_000
    max_chunk_chars: int = 8_000
    max_chunk_paragraphs: int = 180
    target_live_original_tokens: int = 96_000
    max_live_original_tokens: int = 112_000
    min_live_chunks_after_compaction: int = 2
    preferred_live_chunks_after_compaction: int = 3
    compaction_reclaim_chunk_count: int = 1


@dataclass
class WindowL1Config:
    focus_target_tokens: int = 6_000
    focus_max_tokens: int = 12_000
    min_focus_paragraphs: int = 8
    max_focus_paragraphs: int = 40
    overlap_paragraphs: int = 4
    trigger_advance_ratio: float = 0.70
    comment_density_soft_min: float = 0.25
    comment_density_stat_window_paragraphs: int = 80


@dataclass
class ContextL3Config:
    preflight_trigger_input_tokens: int = 112_000
    compression_trigger_input_tokens: int = 128_000
    max_completed_l2_chunks_before_compaction: int = 4
    min_completed_l2_chunks_before_compaction: int = 3
    compaction_reclaim_chunk_count: int = 1
    compaction_timeout_s: int = 180
    allow_emergency_overflow_once: bool = True


@dataclass
class EphemeralCommentsConfig:
    recent_focus_windows: int = 3
    nearby_paragraph_margin: int = 20
    max_tokens: int = 3_000
    compress: bool = False


@dataclass
class EphemeralChatConfig:
    recent_turns: int = 6
    max_tokens: int = 4_000
    compress: bool = False
    scope: str = "current_session"


@dataclass
class ObservabilityConfig:
    enabled: bool = True
    provider: str = "otel"
    log_json: bool = True
    log_level: str = "INFO"
    include_prompt_manifest: bool = True
    include_full_prompt: bool = False
    service_name: str = "vibe-reader-backend"
    otel_endpoint: str = ""


@dataclass
class TokenEstimationConfig:
    token_safety_margin: float = 1.10
    calibration_percentile: float = 0.95
    calibration_window_size: int = 50
    min_calibration_samples: int = 5
    default_bootstrap_calibration_ratio: float = 1.0


@dataclass
class Settings:
    data_dir: pathlib.Path = field(default_factory=_default_data_dir)
    llm: LLMConfig = field(default_factory=LLMConfig)
    reader: ReaderConfig = field(default_factory=ReaderConfig)
    context: ContextConfig = field(default_factory=ContextConfig)
    context_l2: ContextL2Config = field(default_factory=ContextL2Config)
    window_l1: WindowL1Config = field(default_factory=WindowL1Config)
    context_l3: ContextL3Config = field(default_factory=ContextL3Config)
    ephemeral_comments: EphemeralCommentsConfig = field(
        default_factory=EphemeralCommentsConfig
    )
    ephemeral_chat: EphemeralChatConfig = field(default_factory=EphemeralChatConfig)
    token_estimation: TokenEstimationConfig = field(
        default_factory=TokenEstimationConfig
    )
    observability: ObservabilityConfig = field(default_factory=ObservabilityConfig)
    verify_mode: bool = False
    _config_overlay_snapshot: dict[str, Any] | None = field(
        default=None,
        repr=False,
        compare=False,
    )

    @property
    def db_path(self) -> pathlib.Path:
        return self.data_dir / "vibe_reader.db"

    @property
    def books_dir(self) -> pathlib.Path:
        return self.data_dir / "books"

    @property
    def logs_dir(self) -> pathlib.Path:
        return self.data_dir / "logs"

    @property
    def config_path(self) -> pathlib.Path:
        return self.data_dir / "config.toml"


def _snapshot_overlay_fields(settings: Settings) -> dict[str, Any]:
    return {
        "reader": {
            "lookahead_paragraphs": settings.reader.lookahead_paragraphs,
            "progress_debounce_ms": settings.reader.progress_debounce_ms,
        },
        "window_l1": {
            key: getattr(settings.window_l1, key)
            for key in (
                "focus_target_tokens",
                "focus_max_tokens",
                "min_focus_paragraphs",
                "max_focus_paragraphs",
                "overlap_paragraphs",
                "comment_density_stat_window_paragraphs",
                "trigger_advance_ratio",
                "comment_density_soft_min",
            )
        },
        "context": {
            "max_context_jump_tokens_estimate": settings.context.max_context_jump_tokens_estimate,
        },
        "context_l2": {
            key: getattr(settings.context_l2, key)
            for key in (
                "target_chunk_tokens",
                "min_chunk_tokens",
                "max_chunk_tokens",
                "max_chunk_chars",
                "max_chunk_paragraphs",
                "target_live_original_tokens",
                "max_live_original_tokens",
            )
        },
        "context_l3": {
            key: getattr(settings.context_l3, key)
            for key in (
                "preflight_trigger_input_tokens",
                "compression_trigger_input_tokens",
                "max_completed_l2_chunks_before_compaction",
                "min_completed_l2_chunks_before_compaction",
            )
        },
        "token_estimation": {
            key: getattr(settings.token_estimation, key)
            for key in (
                "token_safety_margin",
                "calibration_percentile",
                "calibration_window_size",
                "min_calibration_samples",
                "default_bootstrap_calibration_ratio",
            )
        },
    }


def _validate_overlay_values(raw: dict[str, Any]) -> None:
    l3_raw = raw.get("context_l3") or {}
    preflight = l3_raw.get("preflight_trigger_input_tokens")
    compression = l3_raw.get("compression_trigger_input_tokens")
    if preflight is not None and compression is not None and preflight > compression:
        raise ValueError(
            "preflight_trigger_input_tokens must be <= compression_trigger_input_tokens"
        )

    l2_raw = raw.get("context_l2") or {}
    min_chunk = l2_raw.get("min_chunk_tokens")
    target_chunk = l2_raw.get("target_chunk_tokens")
    max_chunk = l2_raw.get("max_chunk_tokens")
    if min_chunk is not None and target_chunk is not None and min_chunk > target_chunk:
        raise ValueError("min_chunk_tokens must be <= target_chunk_tokens")
    if target_chunk is not None and max_chunk is not None and target_chunk > max_chunk:
        raise ValueError("target_chunk_tokens must be <= max_chunk_tokens")


def restore_app_config_overlays(settings: Settings) -> dict[str, Any]:
    """Restore settings fields from the last overlay snapshot."""
    snapshot = settings._config_overlay_snapshot
    if not snapshot:
        return {"restored": False}

    reader_snap = snapshot.get("reader") or {}
    for key, value in reader_snap.items():
        setattr(settings.reader, key, value)

    win_snap = snapshot.get("window_l1") or {}
    for key, value in win_snap.items():
        setattr(settings.window_l1, key, value)

    ctx_snap = snapshot.get("context") or {}
    for key, value in ctx_snap.items():
        setattr(settings.context, key, value)

    l2_snap = snapshot.get("context_l2") or {}
    for key, value in l2_snap.items():
        setattr(settings.context_l2, key, value)

    l3_snap = snapshot.get("context_l3") or {}
    for key, value in l3_snap.items():
        setattr(settings.context_l3, key, value)

    te_snap = snapshot.get("token_estimation") or {}
    for key, value in te_snap.items():
        setattr(settings.token_estimation, key, value)

    settings._config_overlay_snapshot = None
    return {"restored": True, "snapshot": snapshot}


def _apply_reader_overlay(
    settings: Settings, reader_raw: dict[str, Any]
) -> dict[str, Any]:
    for key in ("lookahead_paragraphs", "progress_debounce_ms"):
        if key in reader_raw:
            setattr(settings.reader, key, int(reader_raw[key]))
    return dict(reader_raw)


def _apply_window_l1_overlay(
    settings: Settings, win_raw: dict[str, Any]
) -> dict[str, Any]:
    wc = settings.window_l1
    int_keys = (
        "focus_target_tokens",
        "focus_max_tokens",
        "min_focus_paragraphs",
        "max_focus_paragraphs",
        "overlap_paragraphs",
        "comment_density_stat_window_paragraphs",
    )
    float_keys = ("trigger_advance_ratio", "comment_density_soft_min")
    for key in int_keys:
        if key in win_raw:
            setattr(wc, key, int(win_raw[key]))
    for key in float_keys:
        if key in win_raw:
            setattr(wc, key, float(win_raw[key]))
    return dict(win_raw)


def _apply_context_l2_overlay(
    settings: Settings, ctx_l2_raw: dict[str, Any]
) -> dict[str, Any]:
    l2 = settings.context_l2
    for key in (
        "target_chunk_tokens",
        "min_chunk_tokens",
        "max_chunk_tokens",
        "max_chunk_chars",
        "max_chunk_paragraphs",
        "target_live_original_tokens",
        "max_live_original_tokens",
        "min_live_chunks_after_compaction",
        "preferred_live_chunks_after_compaction",
    ):
        if key in ctx_l2_raw:
            setattr(l2, key, int(ctx_l2_raw[key]))
    return dict(ctx_l2_raw)


def _apply_context_l3_overlay(
    settings: Settings, ctx_l3_raw: dict[str, Any]
) -> dict[str, Any]:
    l3 = settings.context_l3
    for key in (
        "preflight_trigger_input_tokens",
        "compression_trigger_input_tokens",
        "max_completed_l2_chunks_before_compaction",
        "min_completed_l2_chunks_before_compaction",
    ):
        if key in ctx_l3_raw:
            setattr(l3, key, int(ctx_l3_raw[key]))
    return dict(ctx_l3_raw)


def _apply_context_overlay(
    settings: Settings, ctx_raw: dict[str, Any]
) -> dict[str, Any]:
    int_keys = ("max_context_jump_tokens_estimate",)
    for key in int_keys:
        if key in ctx_raw:
            setattr(settings.context, key, int(ctx_raw[key]))
    return dict(ctx_raw)


def _apply_token_estimation_overlay(
    settings: Settings, te_raw: dict[str, Any]
) -> dict[str, Any]:
    te = settings.token_estimation
    float_keys = (
        "token_safety_margin",
        "calibration_percentile",
        "default_bootstrap_calibration_ratio",
    )
    int_keys = ("calibration_window_size", "min_calibration_samples")
    for key in float_keys:
        if key in te_raw:
            setattr(te, key, float(te_raw[key]))
    for key in int_keys:
        if key in te_raw:
            setattr(te, key, int(te_raw[key]))
    return dict(te_raw)


def apply_app_config_overlays(settings: Settings, raw: dict) -> dict[str, Any]:
    """Apply ``reader`` / ``window_l1`` / ``context`` / ``context_l2`` / ``context_l3`` / ``token_estimation`` overlays."""
    _validate_overlay_values(raw)
    if settings._config_overlay_snapshot is None:
        settings._config_overlay_snapshot = _snapshot_overlay_fields(settings)

    applied: dict[str, Any] = {}

    reader_raw = raw.get("reader") or {}
    if reader_raw:
        applied["reader"] = _apply_reader_overlay(settings, reader_raw)

    win_raw = raw.get("window_l1") or {}
    if win_raw:
        applied["window_l1"] = _apply_window_l1_overlay(settings, win_raw)

    ctx_raw = raw.get("context") or {}
    if ctx_raw:
        applied["context"] = _apply_context_overlay(settings, ctx_raw)

    ctx_l2_raw = raw.get("context_l2") or {}
    if ctx_l2_raw:
        applied["context_l2"] = _apply_context_l2_overlay(settings, ctx_l2_raw)

    ctx_l3_raw = raw.get("context_l3") or {}
    if ctx_l3_raw:
        applied["context_l3"] = _apply_context_l3_overlay(settings, ctx_l3_raw)

    te_raw = raw.get("token_estimation") or {}
    if te_raw:
        applied["token_estimation"] = _apply_token_estimation_overlay(settings, te_raw)

    return applied


def load_settings() -> Settings:
    data_dir = pathlib.Path(_env("VIBE_READER_DATA_DIR") or str(_default_data_dir()))
    config_path = data_dir / "config.toml"

    raw: dict = {}
    if config_path.exists():
        raw = toml.load(config_path)

    llm_raw = raw.get("llm", {})
    llm = LLMConfig(
        base_url=_env("VIBE_READER_LLM_BASE_URL") or llm_raw.get("base_url", ""),
        api_key=_env("VIBE_READER_LLM_API_KEY") or llm_raw.get("api_key", ""),
        model=_env("VIBE_READER_LLM_MODEL")
        or llm_raw.get("model", "deepseek-v4-flash"),
    )

    reader_raw = raw.get("reader", {})
    reader = ReaderConfig(
        lookahead_paragraphs=reader_raw.get("lookahead_paragraphs", 5),
        progress_debounce_ms=reader_raw.get("progress_debounce_ms", 800),
    )

    ctx_raw = raw.get("context", {})
    context = ContextConfig(
        provider_context_limit_tokens=ctx_raw.get(
            "provider_context_limit_tokens", 1_000_000
        ),
        attention_target_input_tokens=ctx_raw.get(
            "attention_target_input_tokens", 128_000
        ),
        normal_target_input_tokens=ctx_raw.get("normal_target_input_tokens", 112_000),
        compression_target_input_tokens=ctx_raw.get(
            "compression_target_input_tokens", 128_000
        ),
        emergency_input_cap_tokens=ctx_raw.get("emergency_input_cap_tokens", 160_000),
        reserved_tokens=ctx_raw.get("reserved_tokens", 12_000),
        target_chapter_summary_tokens=ctx_raw.get(
            "target_chapter_summary_tokens", 7_000
        ),
        max_chapter_summary_tokens=ctx_raw.get("max_chapter_summary_tokens", 10_000),
        max_anchor_excerpts=ctx_raw.get("max_anchor_excerpts", 12),
        max_anchor_excerpt_tokens=ctx_raw.get("max_anchor_excerpt_tokens", 120),
        max_context_jump_chars=ctx_raw.get("max_context_jump_chars", 24_000),
        max_context_jump_tokens_estimate=ctx_raw.get(
            "max_context_jump_tokens_estimate", 24_000
        ),
    )

    ctx_l2_raw = raw.get("context_l2", {})
    context_l2 = ContextL2Config(
        target_chunk_tokens=ctx_l2_raw.get("target_chunk_tokens", 24_000),
        min_chunk_tokens=ctx_l2_raw.get("min_chunk_tokens", 18_000),
        max_chunk_tokens=ctx_l2_raw.get("max_chunk_tokens", 32_000),
        max_chunk_chars=ctx_l2_raw.get("max_chunk_chars", 8_000),
        max_chunk_paragraphs=ctx_l2_raw.get("max_chunk_paragraphs", 180),
        target_live_original_tokens=ctx_l2_raw.get(
            "target_live_original_tokens", 96_000
        ),
        max_live_original_tokens=ctx_l2_raw.get("max_live_original_tokens", 112_000),
        min_live_chunks_after_compaction=ctx_l2_raw.get(
            "min_live_chunks_after_compaction", 2
        ),
        preferred_live_chunks_after_compaction=ctx_l2_raw.get(
            "preferred_live_chunks_after_compaction", 3
        ),
        compaction_reclaim_chunk_count=ctx_l2_raw.get(
            "compaction_reclaim_chunk_count", 1
        ),
    )

    win_raw = raw.get("window_l1", {})
    window_l1 = WindowL1Config(
        focus_target_tokens=win_raw.get("focus_target_tokens", 6_000),
        focus_max_tokens=win_raw.get("focus_max_tokens", 12_000),
        min_focus_paragraphs=win_raw.get("min_focus_paragraphs", 8),
        max_focus_paragraphs=win_raw.get("max_focus_paragraphs", 40),
        overlap_paragraphs=win_raw.get("overlap_paragraphs", 4),
        trigger_advance_ratio=win_raw.get("trigger_advance_ratio", 0.70),
        comment_density_soft_min=win_raw.get("comment_density_soft_min", 0.25),
        comment_density_stat_window_paragraphs=win_raw.get(
            "comment_density_stat_window_paragraphs", 80
        ),
    )

    ctx_l3_raw = raw.get("context_l3", {})
    context_l3 = ContextL3Config(
        preflight_trigger_input_tokens=ctx_l3_raw.get(
            "preflight_trigger_input_tokens", 112_000
        ),
        compression_trigger_input_tokens=ctx_l3_raw.get(
            "compression_trigger_input_tokens", 128_000
        ),
        max_completed_l2_chunks_before_compaction=ctx_l3_raw.get(
            "max_completed_l2_chunks_before_compaction", 4
        ),
        min_completed_l2_chunks_before_compaction=ctx_l3_raw.get(
            "min_completed_l2_chunks_before_compaction", 3
        ),
        compaction_reclaim_chunk_count=ctx_l3_raw.get(
            "compaction_reclaim_chunk_count", 1
        ),
        compaction_timeout_s=ctx_l3_raw.get("compaction_timeout_s", 180),
        allow_emergency_overflow_once=ctx_l3_raw.get(
            "allow_emergency_overflow_once", True
        ),
    )

    eph_comments_raw = raw.get("ephemeral_comments", {})
    ephemeral_comments = EphemeralCommentsConfig(
        recent_focus_windows=eph_comments_raw.get("recent_focus_windows", 3),
        nearby_paragraph_margin=eph_comments_raw.get("nearby_paragraph_margin", 20),
        max_tokens=eph_comments_raw.get("max_tokens", 3_000),
        compress=eph_comments_raw.get("compress", False),
    )

    eph_chat_raw = raw.get("ephemeral_chat", {})
    ephemeral_chat = EphemeralChatConfig(
        recent_turns=eph_chat_raw.get("recent_turns", 6),
        max_tokens=eph_chat_raw.get("max_tokens", 4_000),
        compress=eph_chat_raw.get("compress", False),
        scope=eph_chat_raw.get("scope", "current_session"),
    )

    te_raw = raw.get("token_estimation", {})
    token_estimation = TokenEstimationConfig(
        token_safety_margin=te_raw.get("token_safety_margin", 1.10),
        calibration_percentile=te_raw.get("calibration_percentile", 0.95),
        calibration_window_size=te_raw.get("calibration_window_size", 50),
        min_calibration_samples=te_raw.get("min_calibration_samples", 5),
        default_bootstrap_calibration_ratio=te_raw.get(
            "default_bootstrap_calibration_ratio", 1.0
        ),
    )

    obs_raw = raw.get("observability", {})
    observability = ObservabilityConfig(
        enabled=_env("VIBE_READER_OBSERVABILITY_ENABLED") not in ("0", "false", "")
        if _env("VIBE_READER_OBSERVABILITY_ENABLED") is not None
        else obs_raw.get("enabled", True),
        provider=obs_raw.get("provider", "otel"),
        log_json=obs_raw.get("log_json", True),
        log_level=_env("VIBE_READER_LOG_LEVEL") or obs_raw.get("log_level", "INFO"),
        include_prompt_manifest=obs_raw.get("include_prompt_manifest", True),
        include_full_prompt=obs_raw.get("include_full_prompt", False),
        service_name=obs_raw.get("service_name", "vibe-reader-backend")
        if isinstance(obs_raw.get("service_name"), str)
        else "vibe-reader-backend",
        otel_endpoint=_env("VIBE_READER_OTEL_ENDPOINT") or obs_raw.get("endpoint", ""),
    )

    verify_mode = _env("VIBE_READER_VERIFY_MODE") in ("1", "true")

    return Settings(
        data_dir=data_dir,
        llm=llm,
        reader=reader,
        context=context,
        context_l2=context_l2,
        window_l1=window_l1,
        context_l3=context_l3,
        ephemeral_comments=ephemeral_comments,
        ephemeral_chat=ephemeral_chat,
        token_estimation=token_estimation,
        observability=observability,
        verify_mode=verify_mode,
    )
