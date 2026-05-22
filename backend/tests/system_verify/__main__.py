"""CLI entry point for vibe-verify.

Usage:
    python -m tests.system_verify prepare --corpus tests/corpus/manifest.toml
    python -m tests.system_verify run --suite mvp --target-url http://127.0.0.1:8000
    python -m tests.system_verify report --run-id <run_id>
"""
from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="vibe-verify", description="Vibe Reader system verification")
    sub = parser.add_subparsers(dest="command")

    # prepare
    p_prepare = sub.add_parser("prepare", help="Validate corpus manifest")
    p_prepare.add_argument("--corpus", default="tests/corpus/manifest.toml", help="Corpus manifest path")
    p_prepare.add_argument("--config", default=None, help="Verify config TOML")

    # run
    p_run = sub.add_parser("run", help="Run verification scenarios")
    p_run.add_argument("--suite", default="mvp", choices=["smoke", "mvp", "cache", "judge"])
    p_run.add_argument("--target-url", default=None, help="Backend base URL")
    p_run.add_argument("--run-id", default=None, help="Reuse an existing run ID")
    p_run.add_argument("--config", default=None, help="Verify config TOML")

    # report
    p_report = sub.add_parser("report", help="Generate report from a completed run")
    p_report.add_argument("--run-id", required=True, help="Run ID to report on")
    p_report.add_argument("--config", default=None, help="Verify config TOML")

    args = parser.parse_args(argv)

    if args.command == "prepare":
        _cmd_prepare(args)
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
        print("Corpus validation FAILED.", file=sys.stderr)
        sys.exit(1)


def _cmd_run(args: argparse.Namespace) -> None:
    import asyncio
    from .config import load_verify_config
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

    try:
        if config.run.suite == "smoke":
            from .scenarios.s0_connectivity import run_s0
            from .scenarios.s1_import import run_s1
            asyncio.run(_run_smoke(mgr, config))
        elif config.run.suite == "mvp":
            asyncio.run(_run_mvp(mgr, config))
        else:
            print(f"Suite '{config.run.suite}' not yet implemented.", file=sys.stderr)
    except Exception as exc:
        print(f"Run failed: {exc}", file=sys.stderr)
    finally:
        mgr.finish()
        mgr.write_manifest()
        print(f"Manifest written to {out_dir / 'run_manifest.json'}")


async def _run_smoke(mgr: RunManager, config) -> None:
    from .corpus import CorpusManager
    from .metrics_collector import MetricsAggregator
    from .scenarios.s0_connectivity import run_s0
    from .scenarios.s1_import import run_s1

    metrics = MetricsAggregator(mgr)
    corpus = CorpusManager(config, "tests/corpus/manifest.toml")
    corpus.load()

    await run_s0(mgr, config, metrics)
    await run_s1(mgr, config, metrics, corpus)


async def _run_mvp(mgr: RunManager, config) -> None:
    from .corpus import CorpusManager
    from .metrics_collector import MetricsAggregator
    from .scenarios.s0_connectivity import run_s0
    from .scenarios.s1_import import run_s1

    metrics = MetricsAggregator(mgr)
    corpus = CorpusManager(config, "tests/corpus/manifest.toml")
    corpus.load()

    await run_s0(mgr, config, metrics)
    await run_s1(mgr, config, metrics, corpus)


def _cmd_report(args: argparse.Namespace) -> None:
    print(f"Report generation for run {args.run_id} - not yet implemented.")


if __name__ == "__main__":
    main()
