"""CLI entry point for vibe-verify.

Usage:
    python -m tests.system_verify prepare --corpus tests/corpus/manifest.toml
    python -m tests.system_verify init-run [--corpus tests/corpus/manifest.toml]
    python -m tests.system_verify run --suite mvp --target-url http://127.0.0.1:8000
    python -m tests.system_verify run --suite real-happy-path --llm-mode real
    python -m tests.system_verify run --dry-run
    python -m tests.system_verify report --run-id <run_id>

Backend should be started with an isolated data directory, for example:

    VIBE_READER_DATA_DIR=/tmp/vibe_reader_verify \\
    VIBE_READER_VERIFY_MODE=1 \\
    python3 -m app.main
"""

from __future__ import annotations

import argparse
import sys

from .env_file import load_project_dotenv

DEFAULT_CORPUS = "tests/corpus/manifest.toml"


def main(argv: list[str] | None = None) -> None:
    load_project_dotenv()
    parser = argparse.ArgumentParser(
        prog="vibe-verify", description="Vibe Reader system verification"
    )
    sub = parser.add_subparsers(dest="command")

    p_prepare = sub.add_parser("prepare", help="Validate corpus manifest")
    p_prepare.add_argument(
        "--corpus", default=DEFAULT_CORPUS, help="Corpus manifest path"
    )
    p_prepare.add_argument("--config", default=None, help="Verify config TOML")

    p_init = sub.add_parser("init-run", help="Create an empty verification run")
    p_init.add_argument(
        "--corpus",
        default=None,
        help="Optional corpus manifest to validate and resolve",
    )
    p_init.add_argument("--run-id", default=None, help="Reuse an existing run ID")
    p_init.add_argument("--target-url", default=None, help="Backend base URL")
    p_init.add_argument("--config", default=None, help="Verify config TOML")
    p_init.add_argument(
        "--llm-mode",
        choices=["stub", "real"],
        default=None,
        help="LLM mode for this run",
    )
    p_init.add_argument(
        "--param-set",
        default=None,
        help="Named verification param set (overrides suite default)",
    )

    p_run = sub.add_parser("run", help="Run verification scenarios")
    p_run.add_argument(
        "--suite",
        default="mvp",
        choices=["smoke", "mvp", "real-happy-path", "cache", "judge"],
    )
    p_run.add_argument(
        "--llm-mode",
        choices=["stub", "real"],
        default=None,
        help="LLM mode (must match param set when both are set)",
    )
    p_run.add_argument(
        "--param-set",
        default=None,
        help="Named verification param set (overrides suite default)",
    )
    p_run.add_argument(
        "--real-coverage",
        default="A2",
        help="real-happy-path coverage phase (A2, A3, A4)",
    )
    p_run.add_argument("--target-url", default=None, help="Backend base URL")
    p_run.add_argument("--run-id", default=None, help="Reuse an existing run ID")
    p_run.add_argument("--corpus", default=DEFAULT_CORPUS, help="Corpus manifest path")
    p_run.add_argument("--config", default=None, help="Verify config TOML")
    p_run.add_argument(
        "--dry-run",
        action="store_true",
        help="Prepare run output and corpus only; skip scenario execution",
    )
    p_run.add_argument(
        "--keep-data",
        action="store_true",
        help="Skip post-run backend data cleanup (for cache comparison runs)",
    )
    p_run.add_argument(
        "--spawn-backend",
        action="store_true",
        help="Spawn backend subprocess with stub LLM env (stub mode only)",
    )

    p_report = sub.add_parser("report", help="Generate report from a completed run")
    p_report.add_argument("--run-id", required=True, help="Run ID to report on")
    p_report.add_argument("--config", default=None, help="Verify config TOML")

    args = parser.parse_args(argv)

    if args.command == "prepare":
        _cmd_prepare(args)
    elif args.command == "init-run":
        _cmd_init_run(args)
    elif args.command == "run":
        _cmd_run(args)
    elif args.command == "report":
        _cmd_report(args)
    else:
        parser.print_help()
        sys.exit(1)


def _apply_llm_mode(config, llm_mode: str | None) -> None:
    """Deprecated: llm mode is driven by the active param set."""
    _ = (config, llm_mode)


def _load_run_config(args: argparse.Namespace):
    from .core.config import validate_real_llm_config
    from .core.config_loader import load_verify_config

    return load_verify_config(
        args.config,
        param_set=getattr(args, "param_set", None),
        suite=getattr(args, "suite", None),
        coverage=getattr(args, "real_coverage", "A2"),
        llm_mode_override=getattr(args, "llm_mode", None),
    ), validate_real_llm_config


def _cmd_prepare(args: argparse.Namespace) -> None:
    from .corpus import CorpusManager

    config, _ = _load_run_config(args)
    cm = CorpusManager(config, args.corpus)
    ok = cm.validate()
    happy_errors = cm.validate_happy_path_probe()
    if ok:
        print("Corpus validation passed.")
        resolved = cm.resolve()
        print(f"Resolved manifest: {resolved}")
        if happy_errors:
            print("happy_path_current warnings:")
            for err in happy_errors:
                print(f"  - {err}")
    else:
        for err in cm.validation_errors:
            print(err, file=sys.stderr)
        print("Corpus validation FAILED.", file=sys.stderr)
        sys.exit(1)


def _cmd_init_run(args: argparse.Namespace) -> None:
    from .metrics_collector import MetricsAggregator
    from .core.run_manager import RunManager

    config, _ = _load_run_config(args)
    if args.target_url:
        config.target.base_url = args.target_url

    mgr = RunManager(config, run_id=args.run_id)
    out_dir = mgr.start()
    print(f"Run ID: {mgr.run_id}")
    print(f"Output: {out_dir}")

    metrics = MetricsAggregator(mgr, config)
    if args.corpus:
        _prepare_corpus(mgr, config, args.corpus)

    _finalize_run(mgr, metrics)
    print(f"Manifest written to {out_dir / 'run_manifest.json'}")


def _cmd_run(args: argparse.Namespace) -> None:
    from .core.run_spec import (
        build_verify_config_from_run_spec,
        resolve_profile_for_run_spec,
        resolve_run_spec,
    )
    from .metrics_collector import MetricsAggregator
    from .modes.base import (
        cleanup_mode,
        prepare_mode,
        resolve_mode_environment,
        validate_mode_prerequisites,
    )
    from .core.run_manager import RunManager

    spec = resolve_run_spec(
        suite=args.suite,
        coverage=args.real_coverage,
        param_set=args.param_set,
        llm_mode_override=args.llm_mode,
        target_url=args.target_url,
        corpus_path=args.corpus,
        config_path=args.config,
        run_id=args.run_id,
        spawn_backend=bool(args.spawn_backend),
        dry_run=bool(args.dry_run),
        keep_data=bool(args.keep_data),
    )
    config = build_verify_config_from_run_spec(spec, config_path=args.config)
    if args.target_url:
        config.target.base_url = args.target_url

    profile = resolve_profile_for_run_spec(spec)
    env = resolve_mode_environment(spec)
    if config.is_real_llm:
        real_handle = prepare_mode(env, spec, profile, config=config)
        errors = validate_mode_prerequisites(env, real_handle)
        cleanup_mode(env, real_handle)
        if errors:
            print("Real LLM config invalid: " + "; ".join(errors), file=sys.stderr)
            sys.exit(1)

    mgr = RunManager(config, run_id=args.run_id)
    out_dir = mgr.start()
    print(f"Run ID: {mgr.run_id}")
    print(f"Output: {out_dir}")
    print(f"LLM mode: {config.llm.mode}")
    print(f"Param set: {config.params.name}")

    metrics = MetricsAggregator(mgr, config)
    mode_handle = prepare_mode(
        env,
        spec,
        profile,
        config=config,
        spawn_backend=bool(args.spawn_backend),
        dry_run=bool(args.dry_run),
        assert_backend_ready=not args.dry_run and not args.spawn_backend,
        fail_on_launch_error=True,
        fail_on_backend_spawn_error=True,
        fail_on_backend_ready_error=True,
    )
    if mode_handle.manifest_info.get("provider") == "aimock":
        mgr.set_aimock_info(mode_handle.manifest_info)
    try:
        run_failed, data_lifecycle = _execute_run(args, config, mgr, metrics)
        _finish_run(args, config, mgr, metrics, data_lifecycle, run_failed, out_dir)
    finally:
        cleanup_mode(env, mode_handle)


def _execute_run(args, config, mgr, metrics) -> tuple[bool, dict]:
    import asyncio

    from .data_lifecycle import DataDirError, prepare_run_data_dir

    run_failed = False
    data_lifecycle = {
        "pre_reset": False,
        "post_reset": False,
        "preserved_on_failure": False,
        "keep_data": bool(args.keep_data),
    }

    try:
        if args.dry_run:
            _prepare_corpus(mgr, config, args.corpus)
            print("Dry run: scenarios skipped.")
        elif config.run.suite in ("smoke", "mvp", "real-happy-path"):
            asyncio.run(prepare_run_data_dir(config, mgr, phase="pre"))
            data_lifecycle["pre_reset"] = True
            print(f"Pre-run reset: {config.target.data_dir}")
            asyncio.run(
                _run_orchestrated_suite(
                    mgr,
                    config,
                    metrics,
                    args.corpus,
                    coverage=args.real_coverage,
                )
            )
        else:
            print(f"Suite '{config.run.suite}' not yet implemented.", file=sys.stderr)
            run_failed = True
    except DataDirError as exc:
        print(f"Data directory error: {exc}", file=sys.stderr)
        run_failed = True
    except Exception as exc:
        print(f"Run failed: {exc}", file=sys.stderr)
        run_failed = True

    return run_failed, data_lifecycle


def _finish_run(
    args, config, mgr, metrics, data_lifecycle, run_failed, out_dir
) -> None:
    import asyncio

    from .data_lifecycle import DataDirError, prepare_run_data_dir

    if data_lifecycle["pre_reset"] and (run_failed or args.keep_data):
        data_lifecycle["preserved_on_failure"] = run_failed
        print(f"Data dir preserved: {config.target.data_dir}")
    elif data_lifecycle["pre_reset"] and not run_failed:
        try:
            asyncio.run(prepare_run_data_dir(config, mgr, phase="post"))
            data_lifecycle["post_reset"] = True
            print(f"Post-run reset: {config.target.data_dir}")
        except DataDirError as exc:
            print(f"Post-run reset failed: {exc}", file=sys.stderr)
            run_failed = True

    mgr.set_data_lifecycle(data_lifecycle)
    _finalize_run(mgr, metrics)
    print(f"Manifest written to {out_dir / 'run_manifest.json'}")
    if run_failed:
        sys.exit(1)


def _prepare_corpus(mgr, config, corpus_path: str):
    from .core.orchestrator import prepare_corpus

    return prepare_corpus(mgr, config, corpus_path)


async def _run_orchestrated_suite(
    mgr, config, metrics, corpus_path: str, *, coverage: str
) -> None:
    from .core.orchestrator import run_suite_scenarios

    await run_suite_scenarios(
        mgr,
        config,
        metrics,
        corpus_path,
        suite=config.run.suite,
        coverage=coverage if config.run.suite == "real-happy-path" else None,
    )


def _finalize_run(mgr, metrics=None) -> None:
    from .core.orchestrator import finalize_reports

    findings: list[str] = []
    if metrics is not None:
        findings = metrics.check_no_api_key_in_outputs()

    mgr.set_security_checks(
        {
            "api_key_leak_scan": {
                "passed": len(findings) == 0,
                "findings_count": len(findings),
                "findings": findings,
            },
        }
    )
    mgr.finish()
    mgr.write_manifest()
    finalize_reports(mgr)


def _cmd_report(args: argparse.Namespace) -> None:
    from pathlib import Path

    from .report_generator import generate_reports

    run_dir = Path("verify_runs") / args.run_id
    if not run_dir.exists():
        print(f"Run directory not found: {run_dir}", file=sys.stderr)
        sys.exit(1)
    paths = generate_reports(run_dir)
    for name, path in paths.items():
        print(f"{name}: {path}")


if __name__ == "__main__":
    main()
