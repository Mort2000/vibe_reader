"""Verification configuration types and immutable param-set shapes."""

from __future__ import annotations

import os
import pathlib
from dataclasses import dataclass, field


def _env(key: str, default: str | None = None) -> str | None:
    return os.environ.get(key, default)


@dataclass
class TargetConfig:
    base_url: str = "http://127.0.0.1:8000"
    data_dir: str = "/tmp/vibe_reader_verify"


@dataclass
class LLMConfig:
    mode: str = "stub"
    stub_profile: str = "mvp_default"
    temperature: float = 0.4
    timeout_s: int = 120


@dataclass
class AIMockConfig:
    enabled: bool = True
    version: str = "1.27.1"
    host: str = "127.0.0.1"
    port: int = 4010
    strict: bool = True
    metrics: bool = True
    fixture_dir: str = "tests/system_verify/llm_stub/aimock/fixtures"
    profile_dir: str = "tests/system_verify/llm_stub/aimock/profiles"
    seed: int = 20260522
    startup_timeout_s: int = 20
    api_key: str = "aimock-test-key"
    model: str = "deepseek-v4-flash"


@dataclass
class LLMStubConfig:
    aimock: AIMockConfig = field(default_factory=AIMockConfig)


READING_STOP_CROSS_CHAPTER = "cross_chapter"
READING_STOP_COMMENT_WINDOWS = "comment_windows"
READING_STOP_MODES = frozenset(
    {READING_STOP_CROSS_CHAPTER, READING_STOP_COMMENT_WINDOWS}
)


@dataclass
class LongFlowParams:
    require_compaction: bool = True
    test_compaction_trigger_tokens: int = 24000
    test_compaction_min_source_tokens: int = 16000
    test_compaction_min_source_paragraphs: int = 120
    min_comment_windows: int = 2
    min_chat_turns: int = 1
    reading_stop_mode: str = READING_STOP_COMMENT_WINDOWS
    post_compaction_comment_windows: int = 3


@dataclass
class BudgetParams:
    max_calls: int = 16
    max_input_tokens_per_call: int = 64000
    max_output_tokens_per_call: int = 1200
    max_total_cost_usd: float = 3.00
    enforce: bool = False
    track_usage: bool = False


@dataclass
class AssertionParams:
    strict_done_without_comments: bool = True
    require_compaction_audit_real: bool = False
    allow_probe_without_real_llm_flag: bool = False


@dataclass
class ParamSet:
    name: str
    llm_mode: str = "stub"
    aimock_profile: str | None = "mvp_default"
    progress_step_delay_ms: int = 0
    read_batch_size: int = 64
    compaction_advance_batch_size: int = 64
    max_wait_comment_window_s: int = 180
    max_wait_compaction_s: int = 240
    max_wait_chat_s: int = 120
    long_flow: LongFlowParams = field(default_factory=LongFlowParams)
    budget: BudgetParams = field(default_factory=BudgetParams)
    assertions: AssertionParams = field(default_factory=AssertionParams)


@dataclass
class ParamSetRegistryConfig:
    default: str = "mvp"
    dir: str = "param_sets"
    suite_defaults: dict[str, str] = field(default_factory=dict)


@dataclass
class RealLLMConfig:
    base_url: str = ""
    api_key_env: str = "VIBE_READER_LLM_API_KEY"
    model: str = "deepseek-v4-flash"

    @property
    def api_key(self) -> str:
        return os.environ.get(self.api_key_env, "")


@dataclass
class RunConfig:
    suite: str = "mvp"
    seed: int = 20260522


@dataclass
class MetricsConfig:
    collect_otel: bool = True
    collect_logfire: bool = True
    collect_sse_events: bool = True
    collect_provider_usage: bool = False


@dataclass
class AuditConfig:
    enabled: bool = True
    level: str = "agent_interaction"
    include_agent_invocations: bool = True
    include_prompt_messages: bool = True
    include_injected_context: bool = True
    include_model_request: bool = True
    include_model_response: bool = True
    include_thinking: bool = True
    include_tool_calls: bool = True
    include_tool_results: bool = True
    include_validation_events: bool = True
    include_sse_summary: bool = True
    write_markdown_report: bool = True
    markdown_report_dir: str = "audit/agent_reports"
    include_usage_timing_summary: bool = True
    markdown_original_text_mode: str = "range_edge_excerpt"
    edge_paragraph_max_chars: int = 800
    paragraph_hash_algorithm: str = "sha256"
    redact_secrets: bool = True
    write_prompt_markdown: bool = True
    write_context_sidecars: bool = True
    sample_comments_per_window: int = 3
    sample_chat_turns_per_probe: int = 2
    include_prompt_manifest: bool = True
    include_full_prompt: bool = False
    include_original_excerpts: bool = True


@dataclass
class CommentDensityConfig:
    soft_min: float = 0.25
    stat_window_paragraphs: int = 80


@dataclass
class ContextConfig:
    provider_context_limit_tokens: int = 1_000_000
    attention_target_input_tokens: int = 128_000
    normal_target_input_tokens: int = 112_000
    compression_target_input_tokens: int = 128_000
    emergency_input_cap_tokens: int = 160_000
    target_l2_chunk_tokens: int = 24_000
    max_context_jump_chars: int = 24_000


@dataclass
class VerifyConfig:
    target: TargetConfig = field(default_factory=TargetConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    llm_stub: LLMStubConfig = field(default_factory=LLMStubConfig)
    real_llm: RealLLMConfig = field(default_factory=RealLLMConfig)
    run: RunConfig = field(default_factory=RunConfig)
    metrics: MetricsConfig = field(default_factory=MetricsConfig)
    audit: AuditConfig = field(default_factory=AuditConfig)
    comment_density: CommentDensityConfig = field(default_factory=CommentDensityConfig)
    context: ContextConfig = field(default_factory=ContextConfig)
    app_config: dict = field(default_factory=dict)
    param_set_registry: ParamSetRegistryConfig = field(
        default_factory=ParamSetRegistryConfig
    )
    param_sets: dict[str, ParamSet] = field(default_factory=dict)
    _active_param_set_name: str = field(default="mvp", repr=False)

    @property
    def params(self) -> ParamSet:
        """Read-only view of the active param set (set once via apply_param_set)."""
        try:
            return self.param_sets[self._active_param_set_name]
        except KeyError as exc:
            known = ", ".join(sorted(self.param_sets)) or "(none)"
            raise ValueError(
                f"Active param set {self._active_param_set_name!r} is not loaded; "
                f"known: {known}"
            ) from exc

    @property
    def target_data_dir(self) -> pathlib.Path:
        return pathlib.Path(self.target.data_dir)

    @property
    def is_real_llm(self) -> bool:
        return self.llm.mode == "real"

    @property
    def usage_source(self) -> str:
        if self.is_real_llm and self.metrics.collect_provider_usage:
            return "provider"
        return "estimate"

    def llm_metric_tags(self) -> dict[str, object]:
        return {
            "llm_mode": self.llm.mode,
            "param_set": self.params.name,
            "stub_profile": self.llm.stub_profile if not self.is_real_llm else None,
            "usage_source": self.usage_source,
            "real_llm": self.is_real_llm,
        }

    def effective_model(self) -> str | None:
        if self.is_real_llm:
            return _env("VIBE_READER_LLM_MODEL") or self.real_llm.model
        return self.llm_stub.aimock.model


def validate_real_llm_config(config: VerifyConfig) -> list[str]:
    """Return configuration errors when real LLM mode is requested."""
    if not config.is_real_llm:
        return []

    errors: list[str] = []
    if not config.real_llm.base_url:
        errors.append("real_llm.base_url is required when llm.mode=real")
    if not config.real_llm.api_key:
        errors.append(
            f"real LLM API key env {config.real_llm.api_key_env} is required "
            "when llm.mode=real"
        )
    if not config.real_llm.model:
        errors.append("real_llm.model is required when llm.mode=real")
    stop_mode = config.params.long_flow.reading_stop_mode
    if stop_mode not in READING_STOP_MODES:
        errors.append(
            "params.long_flow.reading_stop_mode must be one of "
            f"{sorted(READING_STOP_MODES)}; got {stop_mode!r}"
        )
    return errors
