"""CLI entry point for vibe-verify.

Usage:
    python -m tests.system_verify prepare --corpus tests/corpus/manifest.toml
    python -m tests.system_verify init-run [--corpus tests/corpus/manifest.toml]
    python -m tests.system_verify run --suite mvp --target-url http://127.0.0.1:8000
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

    # prepare
    p_prepare = sub.add_parser("prepare", help="Validate corpus manifest")
    p_prepare.add_argument(
        "--corpus", default=DEFAULT_CORPUS, help="Corpus manifest path"
    )
    p_prepare.add_argument("--config", default=None, help="Verify config TOML")

    # init-run
    p_init = sub.add_parser("init-run", help="Create an empty verification run")
    p_init.add_argument(
        "--corpus",
        default=None,
        help="Optional corpus manifest to validate and resolve",
    )
    p_init.add_argument("--run-id", default=None, help="Reuse an existing run ID")
    p_init.add_argument("--target-url", default=None, help="Backend base URL")
    p_init.add_argument("--config", default=None, help="Verify config TOML")

    # run
    p_run = sub.add_parser("run", help="Run verification scenarios")
    p_run.add_argument(
        "--suite", default="mvp", choices=["smoke", "mvp", "cache", "judge"]
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

    # report
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


def _cmd_prepare(args: argparse.Namespace) -> None:
    from .config import load_verify_config
    from .corpus import CorpusManager

    config = load_verify_config(args.config)
    cm = CorpusManager(config, args.corpus)
    ok = cm.validate()
    if ok:
        print("Corpus validation passed.")
        resolved = cm.resolve()
        print(f"Resolved manifest: {resolved}")
    else:
        for err in cm.validation_errors:
            print(err, file=sys.stderr)
        print("Corpus validation FAILED.", file=sys.stderr)
        sys.exit(1)


def _cmd_init_run(args: argparse.Namespace) -> None:
    from .config import load_verify_config
    from .metrics_collector import MetricsAggregator
    from .run import RunManager

    config = load_verify_config(args.config)
    if args.target_url:
        config.target.base_url = args.target_url

    mgr = RunManager(config, run_id=args.run_id)
    out_dir = mgr.start()
    print(f"Run ID: {mgr.run_id}")
    print(f"Output: {out_dir}")

    metrics = MetricsAggregator(mgr)
    if args.corpus:
        _prepare_corpus(mgr, config, args.corpus)

    _finalize_run(mgr, metrics)
    print(f"Manifest written to {out_dir / 'run_manifest.json'}")


def _cmd_run(args: argparse.Namespace) -> None:
    import asyncio
    from .config import load_verify_config
    from .data_lifecycle import DataDirError, prepare_run_data_dir
    from .metrics_collector import MetricsAggregator
    from .run import RunManager

    config = load_verify_config(args.config)
    if args.target_url:
        config.target.base_url = args.target_url
    if args.suite:
        config.run.suite = args.suite

    mgr = RunManager(config, run_id=args.run_id)
    out_dir = mgr.start()
    print(f"Run ID: {mgr.run_id}")
    print(f"Output: {out_dir}")

    metrics = MetricsAggregator(mgr)
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
        elif config.run.suite in ("smoke", "mvp"):
            asyncio.run(prepare_run_data_dir(config, mgr, phase="pre"))
            data_lifecycle["pre_reset"] = True
            print(f"Pre-run reset: {config.target.data_dir}")

            asyncio.run(_run_suite(mgr, config, metrics, args.corpus))
        else:
            print(f"Suite '{config.run.suite}' not yet implemented.", file=sys.stderr)
            run_failed = True
    except DataDirError as exc:
        print(f"Data directory error: {exc}", file=sys.stderr)
        run_failed = True
    except Exception as exc:
        print(f"Run failed: {exc}", file=sys.stderr)
        run_failed = True
    finally:
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
    from .corpus import CorpusManager

    corpus = CorpusManager(config, corpus_path)
    corpus.load()
    if not corpus.validate():
        errors = "\n".join(corpus.validation_errors)
        raise RuntimeError(f"Corpus validation failed:\n{errors}")

    resolved = corpus.resolve(mgr)
    print(f"Corpus resolved: {resolved}")
    return corpus


async def _run_suite(mgr, config, metrics, corpus_path: str) -> None:
    from .scenarios.s0_connectivity import run_s0
    from .scenarios.s1_import import run_s1

    corpus = _prepare_corpus(mgr, config, corpus_path)
    await run_s0(mgr, config, metrics)
    await run_s1(mgr, config, metrics, corpus)


def _finalize_run(mgr, metrics=None) -> None:
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


def _cmd_report(args: argparse.Namespace) -> None:
    print(f"Report generation for run {args.run_id} - not yet implemented.")


if __name__ == "__main__":
    main()
