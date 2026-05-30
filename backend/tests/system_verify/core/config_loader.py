"""TOML loading and param-set resolution for system verification."""

from __future__ import annotations

import os
import pathlib

import toml

from tests.system_verify.core.config import (
    READING_STOP_COMMENT_WINDOWS,
    AIMockConfig,
    AssertionParams,
    AuditConfig,
    BudgetParams,
    CommentDensityConfig,
    ContextConfig,
    LLMConfig,
    LLMStubConfig,
    LongFlowParams,
    MetricsConfig,
    ParamSet,
    ParamSetRegistryConfig,
    RealLLMConfig,
    RunConfig,
    TargetConfig,
    VerifyConfig,
)


def _env(key: str, default: str | None = None) -> str | None:
    return os.environ.get(key, default)


def _parse_long_flow(raw: dict) -> LongFlowParams:
    return LongFlowParams(
        require_compaction=raw.get("require_compaction", True),
        test_compaction_trigger_tokens=int(
            raw.get("test_compaction_trigger_tokens", 24000)
        ),
        test_compaction_min_source_tokens=int(
            raw.get("test_compaction_min_source_tokens", 16000)
        ),
        test_compaction_min_source_paragraphs=int(
            raw.get("test_compaction_min_source_paragraphs", 120)
        ),
        min_comment_windows=int(raw.get("min_comment_windows", 2)),
        min_chat_turns=int(raw.get("min_chat_turns", 1)),
        reading_stop_mode=raw.get(
            "reading_stop_mode", READING_STOP_COMMENT_WINDOWS
        ),
        post_compaction_comment_windows=int(
            raw.get("post_compaction_comment_windows", 3)
        ),
    )


def _parse_budget(raw: dict) -> BudgetParams:
    return BudgetParams(
        max_calls=int(raw.get("max_calls", 16)),
        max_input_tokens_per_call=int(raw.get("max_input_tokens_per_call", 64000)),
        max_output_tokens_per_call=int(raw.get("max_output_tokens_per_call", 1200)),
        max_total_cost_usd=float(raw.get("max_total_cost_usd", 3.00)),
        enforce=bool(raw.get("enforce", False)),
        track_usage=bool(raw.get("track_usage", False)),
    )


def _parse_assertions(raw: dict) -> AssertionParams:
    return AssertionParams(
        strict_done_without_comments=bool(
            raw.get("strict_done_without_comments", True)
        ),
        require_compaction_audit_real=bool(
            raw.get("require_compaction_audit_real", False)
        ),
        allow_probe_without_real_llm_flag=bool(
            raw.get("allow_probe_without_real_llm_flag", False)
        ),
    )


def parse_param_set(name: str, raw: dict) -> ParamSet:
    long_flow_raw = raw.get("long_flow", {})
    budget_raw = raw.get("budget", {})
    assertions_raw = raw.get("assertions", {})
    aimock_profile = raw.get("aimock_profile")
    if aimock_profile is not None:
        aimock_profile = str(aimock_profile)
    return ParamSet(
        name=name,
        llm_mode=str(raw.get("llm_mode", "stub")),
        aimock_profile=aimock_profile,
        progress_step_delay_ms=int(raw.get("progress_step_delay_ms", 0)),
        read_batch_size=int(raw.get("read_batch_size", 64)),
        compaction_advance_batch_size=int(
            raw.get("compaction_advance_batch_size", 64)
        ),
        max_wait_comment_window_s=int(raw.get("max_wait_comment_window_s", 180)),
        max_wait_compaction_s=int(raw.get("max_wait_compaction_s", 240)),
        max_wait_chat_s=int(raw.get("max_wait_chat_s", 120)),
        long_flow=_parse_long_flow(long_flow_raw),
        budget=_parse_budget(budget_raw),
        assertions=_parse_assertions(assertions_raw),
    )


def load_param_sets(
    registry: ParamSetRegistryConfig,
    *,
    config_path: pathlib.Path | None = None,
    inline_raw: dict | None = None,
) -> dict[str, ParamSet]:
    """Load named param sets from directory TOML files and optional inline blocks."""
    sets: dict[str, ParamSet] = {}

    param_dir = pathlib.Path(registry.dir)
    if not param_dir.is_absolute():
        if config_path is not None:
            candidate = config_path.parent / param_dir
            if candidate.is_dir():
                param_dir = candidate
            elif not param_dir.is_dir():
                param_dir = pathlib.Path(registry.dir)
    if param_dir.is_dir():
        for path in sorted(param_dir.glob("*.toml")):
            raw = toml.load(str(path))
            name = str(raw.get("name", path.stem))
            sets[name] = parse_param_set(name, raw)

    if inline_raw:
        for key, block in inline_raw.items():
            if not isinstance(block, dict):
                continue
            name = str(block.get("name", key))
            sets[name] = parse_param_set(name, block)

    return sets


def resolve_param_set_name(
    config: VerifyConfig,
    *,
    explicit: str | None = None,
    suite: str | None = None,
    coverage: str = "A2",
    llm_mode_hint: str | None = None,
) -> str:
    """Resolve active param set name from CLI/env/suite defaults."""
    if explicit:
        return explicit
    env_name = _env("VIBE_READER_VERIFY_PARAM_SET")
    if env_name:
        return env_name

    suite_name = suite or config.run.suite
    suite_defaults = config.param_set_registry.suite_defaults
    if suite_name in suite_defaults and suite_name != "real-happy-path":
        return suite_defaults[suite_name]

    if suite_name == "real-happy-path":
        mode = llm_mode_hint or config.llm.mode or "stub"
        phase = coverage.upper()
        candidate = f"r1_{phase.lower()}_{mode}"
        if candidate in config.param_sets:
            return candidate
        fallback = suite_defaults.get("real-happy-path")
        if fallback:
            return fallback

    if suite_name in suite_defaults:
        return suite_defaults[suite_name]

    return config.param_set_registry.default


def apply_param_set(
    config: VerifyConfig,
    name: str,
    *,
    llm_mode_override: str | None = None,
) -> None:
    """Activate a param set and sync llm/metrics fields derived from it."""
    if name not in config.param_sets:
        known = ", ".join(sorted(config.param_sets)) or "(none)"
        raise ValueError(f"Unknown param set {name!r}; known: {known}")

    params = config.param_sets[name]
    config._active_param_set_name = name
    config.llm.mode = params.llm_mode
    if params.llm_mode == "stub":
        if not params.aimock_profile:
            raise ValueError(
                f"Param set {name!r} requires aimock_profile when llm_mode=stub"
            )
        config.llm.stub_profile = params.aimock_profile
    config.metrics.collect_provider_usage = params.budget.track_usage

    if llm_mode_override and llm_mode_override != params.llm_mode:
        raise ValueError(
            f"--llm-mode {llm_mode_override!r} conflicts with param set "
            f"{name!r} (llm_mode={params.llm_mode!r})"
        )


def finalize_verify_config(
    config: VerifyConfig,
    *,
    param_set: str | None = None,
    suite: str | None = None,
    coverage: str = "A2",
    llm_mode_override: str | None = None,
) -> None:
    """Resolve and apply the active param set after base config load."""
    name = resolve_param_set_name(
        config,
        explicit=param_set,
        suite=suite,
        coverage=coverage,
        llm_mode_hint=llm_mode_override or config.llm.mode,
    )
    apply_param_set(config, name, llm_mode_override=llm_mode_override)


def load_verify_config(
    path: str | pathlib.Path | None = None,
    *,
    param_set: str | None = None,
    suite: str | None = None,
    coverage: str = "A2",
    llm_mode_override: str | None = None,
) -> VerifyConfig:
    """Load verification config from TOML file with env var overrides."""
    config_path = pathlib.Path(path) if path else None

    if config_path is None:
        env_path = _env("VIBE_READER_VERIFY_CONFIG")
        if env_path:
            config_path = pathlib.Path(env_path)
        else:
            default = pathlib.Path("tests/corpus/verify.toml")
            if default.exists():
                config_path = default

    raw: dict = {}
    if config_path and config_path.exists():
        raw = toml.load(str(config_path))

    target_raw = raw.get("target", {})
    target = TargetConfig(
        base_url=_env("VIBE_READER_VERIFY_TARGET_URL")
        or target_raw.get("base_url", "http://127.0.0.1:8000"),
        data_dir=_env("VIBE_READER_VERIFY_DATA_DIR")
        or target_raw.get("data_dir", "/tmp/vibe_reader_verify"),
    )

    llm_raw = raw.get("llm", {})
    llm_mode = (
        llm_mode_override
        or _env("VIBE_READER_VERIFY_LLM_MODE")
        or llm_raw.get("mode", "stub")
    )
    llm = LLMConfig(
        mode=llm_mode,
        stub_profile=llm_raw.get("stub_profile", "mvp_default"),
        temperature=llm_raw.get("temperature", 0.4),
        timeout_s=llm_raw.get("timeout_s", 120),
    )

    aimock_raw = raw.get("llm_stub", {}).get("aimock", {})
    llm_stub = LLMStubConfig(
        aimock=AIMockConfig(
            enabled=aimock_raw.get("enabled", True),
            version=aimock_raw.get("version", "1.27.1"),
            host=aimock_raw.get("host", "127.0.0.1"),
            port=int(aimock_raw.get("port", 4010)),
            strict=aimock_raw.get("strict", True),
            metrics=aimock_raw.get("metrics", True),
            fixture_dir=aimock_raw.get(
                "fixture_dir", "tests/system_verify/llm_stub/aimock/fixtures"
            ),
            profile_dir=aimock_raw.get(
                "profile_dir", "tests/system_verify/llm_stub/aimock/profiles"
            ),
            seed=int(aimock_raw.get("seed", raw.get("run", {}).get("seed", 20260522))),
            startup_timeout_s=int(aimock_raw.get("startup_timeout_s", 20)),
            api_key=aimock_raw.get("api_key", "aimock-test-key"),
            model=aimock_raw.get("model", "deepseek-v4-flash"),
        )
    )

    real_raw = raw.get("real_llm", {})
    real_llm = RealLLMConfig(
        base_url=_env("VIBE_READER_LLM_BASE_URL") or real_raw.get("base_url", ""),
        api_key_env=real_raw.get("api_key_env", "VIBE_READER_LLM_API_KEY"),
        model=_env("VIBE_READER_LLM_MODEL")
        or real_raw.get("model", "deepseek-v4-flash"),
    )

    run_raw = raw.get("run", {})
    run = RunConfig(
        suite=_env("VIBE_READER_VERIFY_SUITE") or run_raw.get("suite", "mvp"),
        seed=run_raw.get("seed", 20260522),
    )

    metrics_raw = raw.get("metrics", {})
    metrics = MetricsConfig(
        collect_otel=metrics_raw.get("collect_otel", True),
        collect_logfire=metrics_raw.get("collect_logfire", True),
        collect_sse_events=metrics_raw.get("collect_sse_events", True),
        collect_provider_usage=metrics_raw.get("collect_provider_usage", False),
    )

    audit_raw = raw.get("audit", {})
    audit = AuditConfig(
        enabled=audit_raw.get("enabled", True),
        level=audit_raw.get("level", "agent_interaction"),
        include_agent_invocations=audit_raw.get("include_agent_invocations", True),
        include_prompt_messages=audit_raw.get("include_prompt_messages", True),
        include_injected_context=audit_raw.get("include_injected_context", True),
        include_model_request=audit_raw.get("include_model_request", True),
        include_model_response=audit_raw.get("include_model_response", True),
        include_thinking=audit_raw.get("include_thinking", True),
        include_tool_calls=audit_raw.get("include_tool_calls", True),
        include_tool_results=audit_raw.get("include_tool_results", True),
        include_validation_events=audit_raw.get("include_validation_events", True),
        include_sse_summary=audit_raw.get("include_sse_summary", True),
        write_markdown_report=audit_raw.get("write_markdown_report", True),
        markdown_report_dir=audit_raw.get("markdown_report_dir", "audit/agent_reports"),
        include_usage_timing_summary=audit_raw.get(
            "include_usage_timing_summary", True
        ),
        markdown_original_text_mode=audit_raw.get(
            "markdown_original_text_mode", "range_edge_excerpt"
        ),
        edge_paragraph_max_chars=int(audit_raw.get("edge_paragraph_max_chars", 800)),
        paragraph_hash_algorithm=audit_raw.get("paragraph_hash_algorithm", "sha256"),
        redact_secrets=audit_raw.get("redact_secrets", True),
        write_prompt_markdown=audit_raw.get("write_prompt_markdown", True),
        write_context_sidecars=audit_raw.get("write_context_sidecars", True),
        sample_comments_per_window=audit_raw.get("sample_comments_per_window", 3),
        sample_chat_turns_per_probe=audit_raw.get("sample_chat_turns_per_probe", 2),
        include_prompt_manifest=audit_raw.get("include_prompt_manifest", True),
        include_full_prompt=audit_raw.get("include_full_prompt", False),
        include_original_excerpts=audit_raw.get("include_original_excerpts", True),
    )

    density_raw = raw.get("comment_density", {})
    comment_density = CommentDensityConfig(
        soft_min=density_raw.get("soft_min", 0.25),
        stat_window_paragraphs=density_raw.get("stat_window_paragraphs", 80),
    )

    context_raw = raw.get("context", {})
    context = ContextConfig(
        provider_context_limit_tokens=context_raw.get(
            "provider_context_limit_tokens", 1_000_000
        ),
        attention_target_input_tokens=context_raw.get(
            "attention_target_input_tokens", 128_000
        ),
        normal_target_input_tokens=context_raw.get(
            "normal_target_input_tokens", 112_000
        ),
        compression_target_input_tokens=context_raw.get(
            "compression_target_input_tokens", 128_000
        ),
        emergency_input_cap_tokens=context_raw.get(
            "emergency_input_cap_tokens", 160_000
        ),
        target_l2_chunk_tokens=context_raw.get("target_l2_chunk_tokens", 24_000),
        max_context_jump_chars=context_raw.get("max_context_jump_chars", 24_000),
    )

    param_set_raw = raw.get("param_set", {})
    suite_defaults_raw = raw.get("suite_defaults", {})
    param_set_registry = ParamSetRegistryConfig(
        default=str(param_set_raw.get("default", "mvp")),
        dir=str(param_set_raw.get("dir", "param_sets")),
        suite_defaults={str(k): str(v) for k, v in suite_defaults_raw.items()},
    )

    inline_param_sets = raw.get("param_sets", {})
    param_sets = load_param_sets(
        param_set_registry,
        config_path=config_path,
        inline_raw=inline_param_sets if inline_param_sets else None,
    )
    if not param_sets:
        raise ValueError(
            "No param sets loaded; add tests/corpus/param_sets/*.toml or "
            "[param_sets] inline blocks in verify.toml"
        )

    config = VerifyConfig(
        target=target,
        llm=llm,
        llm_stub=llm_stub,
        real_llm=real_llm,
        run=run,
        metrics=metrics,
        audit=audit,
        comment_density=comment_density,
        context=context,
        app_config=dict(raw.get("app", {})),
        param_set_registry=param_set_registry,
        param_sets=param_sets,
    )

    effective_suite = suite or config.run.suite
    if suite:
        config.run.suite = suite
    finalize_verify_config(
        config,
        param_set=param_set,
        suite=effective_suite,
        coverage=coverage,
        llm_mode_override=llm_mode_override,
    )
    return config
