from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

import httpx
import pytest

from vibe_verify.artifact_store import ArtifactStore
from vibe_verify.cli import (
    BackendSettings,
    backend_preparer,
    build_parser,
    build_run_spec,
    main,
    prepare_backend_data_dir,
    resolve_run_settings,
    wait_for_backend_ready,
)
from vibe_verify.corpus import CorpusCatalog, CorpusRequirement
from vibe_verify.driver import (
    APIResponse,
    EventSubscriber,
    TargetClient,
    optional_int,
    parse_response_body,
    require_success,
    summarize_body,
    unwrap,
)
from vibe_verify.evidence import EvidenceHub, LLMView, normalize_usage
from vibe_verify.models import AgentInvocation, Correlation, TokenUsage
from vibe_verify.provider import (
    ProviderHarness,
    StubSidecar,
    estimate_usage,
    last_user_content,
    parse_comment_targets,
)
from vibe_verify.run_config import RunSettings
from vibe_verify.runner import Budget, BudgetExceeded, Profile, RunSpec, enforce_budget
from vibe_verify.scenario import ScenarioDefinition, ScenarioRegistry


def invocation(*, usage: TokenUsage | None = None) -> AgentInvocation:
    return AgentInvocation(
        id="inv",
        agent="A",
        prompt_messages=[{"role": "user", "content": "x"}],
        response={},
        usage=usage or TokenUsage(input=2, output=1),
        correlation=Correlation("run"),
    )


def test_artifact_mutable_json_failure_and_multiple_secret_patterns(tmp_path) -> None:
    store = ArtifactStore(tmp_path, "run")
    store.start()
    store.write_json("mutable.json", {"x": 1})
    store.write_json("mutable.json", {"x": 2})
    store.write_failure("broken", {"trace": "t"})
    store.write_text("evidence/auth.txt", "Authorization: Bearer top-secret")
    store.write_text("evidence/key.json", '{"api_key": "plain-secret"}')
    assert len(store.scan_secrets()) == 2


def test_evidence_filter_no_store_mixed_usage_and_verify_usage_shape() -> None:
    hub = EvidenceHub()
    hub.record_invocation(invocation())
    hub.record_invocation(
        invocation(usage=TokenUsage(input=4, output=2, source="provider"))
    )
    assert len(hub.calls()) == 2
    assert LLMView(hub).total_usage().source == "mixed"
    assert (
        normalize_usage(
            {"input": 3, "output": 2, "cached_input": 1}, source="framework"
        ).cached_input
        == 1
    )
    with pytest.raises(ValueError):
        TokenUsage(source="unknown")


def test_driver_body_helpers_and_error() -> None:
    assert parse_response_body(httpx.Response(200, text="plain")) == "plain"
    assert summarize_body(b"123") == {"bytes": 3}
    assert summarize_body("x" * 600) == "x" * 500
    assert summarize_body({"x": "y" * 600})["truncated"] is True
    assert unwrap({"data": {"x": 1}}) == {"x": 1}
    assert unwrap("value") == "value"
    assert optional_int(None) is None
    assert optional_int("3") == 3
    with pytest.raises(RuntimeError, match="backend HTTP 500"):
        require_success(APIResponse(500, {"error": "broken"}, {}, Correlation("r")))


async def test_target_client_owned_close_and_event_ingest() -> None:
    hub = EvidenceHub()
    client = TargetClient("http://backend", evidence=hub, correlation=Correlation("r"))
    await client.close()
    subscriber = EventSubscriber(hub, Correlation("r"))
    event = subscriber.ingest("done", {"trace_id": "t", "book_id": 1})
    assert event.correlation.trace_id == "t"
    assert event.correlation.book_id == 1


def test_provider_helpers_and_sidecar_404_lifecycle() -> None:
    assert parse_comment_targets("nothing") == []
    assert last_user_content({"messages": [{"role": "system", "content": "x"}]}) == ""
    usage = estimate_usage({"messages": []}, "")
    assert usage["total_tokens"] == 2
    sidecar = StubSidecar()
    with pytest.raises(RuntimeError, match="not started"):
        _ = sidecar.base_url
    sidecar.stop()
    with sidecar:
        root = sidecar.base_url.removesuffix("/v1")
        assert httpx.get(root + "/missing").status_code == 404
        assert httpx.post(root + "/missing", json={}).status_code == 404


def test_provider_real_cleanup_is_noop() -> None:
    harness = ProviderHarness()
    real = harness.prepare(
        mode="real",
        real_base_url="https://example.invalid/v1",
        real_api_key="secret",
        real_model="model",
        real_budget=Budget(max_cost_usd=1),
    )
    harness.cleanup(real)


def test_budget_call_token_and_duration_guards() -> None:
    hub = EvidenceHub()
    hub.record_invocation(invocation())
    with pytest.raises(BudgetExceeded, match="call"):
        enforce_budget(Budget(max_calls=0), hub, time.monotonic())
    with pytest.raises(BudgetExceeded, match="token"):
        enforce_budget(Budget(max_tokens=0), hub, time.monotonic())
    with pytest.raises(BudgetExceeded, match="duration"):
        enforce_budget(Budget(max_duration_s=-1), EvidenceHub(), time.monotonic())


def test_run_spec_digest_and_registry_explicit_selection(tmp_path) -> None:
    spec = RunSpec(
        suite="core",
        profile=Profile(),
        target_url="http://backend",
        artifact_root=tmp_path,
    )
    assert spec.digest.startswith("sha256:")
    same_config = RunSpec(
        suite="core",
        profile=Profile(),
        target_url="http://backend",
        artifact_root=tmp_path / "other",
        run_id="other",
    )
    assert spec.digest == same_config.digest
    registry = ScenarioRegistry()

    async def script(_context) -> None:
        return None

    registry.register(ScenarioDefinition("one", script, suites=frozenset({"core"})))
    registry.register(ScenarioDefinition("two", script, suites=frozenset({"other"})))
    assert [
        item.id
        for item in registry.select(
            suite="core", profile="mvp_stub", scenario_ids=("one",)
        )
    ] == ["one"]


def test_cli_validate_corpus_success_and_failure(tmp_path, capsys) -> None:
    parser = build_parser()
    assert parser.parse_args(["stub"]).command == "stub"
    book = tmp_path / "book.epub"
    book.write_bytes(b"epub")
    digest = hashlib.sha256(b"epub").hexdigest()
    manifest = tmp_path / "manifest.toml"
    manifest.write_text(
        f'[[books]]\nalias = "book"\npath = "book.epub"\n'
        f'license = "public-domain"\nsha256 = "{digest}"\n',
        encoding="utf-8",
    )
    assert main(["validate-corpus", str(manifest)]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True
    book.unlink()
    assert main(["validate-corpus", str(manifest)]) == 1
    assert json.loads(capsys.readouterr().out)["ok"] is False


def test_cli_run_config_resolves_file_and_overrides(tmp_path) -> None:
    config = tmp_path / "run.toml"
    config.write_text(
        """
suite = "configured-suite"
scenarios = ["configured-scenario"]
target_url = "http://configured"
artifact_root = "configured-runs"
corpus = "corpus/manifest.toml"

[profile]
name = "configured-profile"

[profile.budget]
max_calls = 3

[params]
read_batches = 2
read_batch_size = 5

[backend]
command = "uv run app"
cwd = "../backend"
config_file = "configs/backend.toml"

[backend.env]
VIBE_READER_OBSERVABILITY_ENABLED = false
""",
        encoding="utf-8",
    )

    args = build_parser().parse_args(
        [
            "run",
            "--config",
            str(config),
            "--scenario",
            "override-scenario",
            "--run-id",
            "manual",
            "--max-calls",
            "7",
        ]
    )
    settings = resolve_run_settings(args)
    spec = build_run_spec(settings)

    assert settings.suite == "configured-suite"
    assert settings.scenarios == ("override-scenario",)
    assert settings.backend.env["VIBE_READER_OBSERVABILITY_ENABLED"] == "0"
    assert settings.backend.config_file == Path("configs/backend.toml")
    assert spec.run_id == "manual"
    assert spec.profile.name == "configured-profile"
    assert spec.profile.budget.max_calls == 7
    assert spec.params["read_batch_size"] == 5
    assert spec.corpus_catalog_path == Path("corpus/manifest.toml")


def test_cli_run_config_rejects_string_booleans_and_bad_scenarios(
    tmp_path,
) -> None:
    config = tmp_path / "bad.toml"
    config.write_text(
        """
[profile]
audit = "false"
""",
        encoding="utf-8",
    )
    args = build_parser().parse_args(["run", "--config", str(config)])
    with pytest.raises(TypeError, match="profile.audit must be boolean"):
        resolve_run_settings(args)

    config.write_text("scenarios = 1\n", encoding="utf-8")
    args = build_parser().parse_args(["run", "--config", str(config)])
    with pytest.raises(TypeError, match="scenarios must be"):
        resolve_run_settings(args)

    config.write_text("scenarios = [1]\n", encoding="utf-8")
    args = build_parser().parse_args(["run", "--config", str(config)])
    with pytest.raises(TypeError, match="scenarios must be"):
        resolve_run_settings(args)


def test_cli_no_audit_overrides_config_true(tmp_path) -> None:
    config = tmp_path / "run.toml"
    config.write_text(
        """
[profile]
audit = true
""",
        encoding="utf-8",
    )

    args = build_parser().parse_args(["run", "--config", str(config), "--no-audit"])
    assert resolve_run_settings(args).audit is False


def test_backend_data_dir_preparation_writes_backend_config(tmp_path) -> None:
    source = tmp_path / "backend.toml"
    source.write_text("[reader]\nlookahead_paragraphs = 4\n", encoding="utf-8")
    data_dir = tmp_path / "data"

    prepare_backend_data_dir(
        BackendSettings(
            config_file=source,
            env={"VIBE_READER_DATA_DIR": str(data_dir)},
        )
    )

    assert (data_dir / "config.toml").read_text(encoding="utf-8") == (
        "[reader]\nlookahead_paragraphs = 4\n"
    )


def test_backend_data_dir_preparation_can_reset_verify_tmp_dir(tmp_path) -> None:
    source = tmp_path / "backend.toml"
    source.write_text("[reader]\nlookahead_paragraphs = 4\n", encoding="utf-8")
    data_dir = tmp_path / "data"
    (data_dir / "stale").mkdir(parents=True)
    (data_dir / "stale/file.txt").write_text("old", encoding="utf-8")

    prepare_backend_data_dir(
        BackendSettings(
            config_file=source,
            reset_data_dir=True,
            env={
                "VIBE_READER_DATA_DIR": str(data_dir),
                "VIBE_READER_VERIFY_MODE": "1",
            },
        )
    )

    assert not (data_dir / "stale").exists()
    assert (data_dir / "config.toml").exists()


def test_backend_data_dir_reset_requires_verify_mode(tmp_path) -> None:
    source = tmp_path / "backend.toml"
    source.write_text("[reader]\nlookahead_paragraphs = 4\n", encoding="utf-8")

    with pytest.raises(ValueError, match="VERIFY_MODE"):
        prepare_backend_data_dir(
            BackendSettings(
                config_file=source,
                reset_data_dir=True,
                env={"VIBE_READER_DATA_DIR": str(tmp_path / "data")},
            )
        )


def test_backend_preparer_cleans_process_when_ready_wait_fails(
    tmp_path,
    monkeypatch,
) -> None:
    config = tmp_path / "backend.toml"
    config.write_text("[reader]\nlookahead_paragraphs = 4\n", encoding="utf-8")
    cleaned: list[int | None] = []

    def fail_ready(*_args, **_kwargs) -> None:
        raise TimeoutError("not ready")

    def cleanup(process) -> None:
        cleaned.append(process.pid)
        process.terminate()
        process.wait(timeout=5)

    monkeypatch.setattr("vibe_verify.cli.wait_for_backend_ready", fail_ready)
    monkeypatch.setattr("vibe_verify.cli.cleanup_backend", cleanup)

    settings = RunSettings(
        target_url="http://backend",
        backend=BackendSettings(
            command=f'{sys.executable} -c "import time; time.sleep(60)"',
            config_file=config,
            env={"VIBE_READER_DATA_DIR": str(tmp_path / "data")},
        ),
    )
    with pytest.raises(TimeoutError, match="not ready"):
        backend_preparer(settings)(None, None)  # type: ignore[arg-type]

    assert cleaned


def test_wait_for_backend_ready_does_not_accept_404(monkeypatch) -> None:
    def not_found(_url, timeout):
        return httpx.Response(404, json={"error": "missing"})

    monkeypatch.setattr(httpx, "get", not_found)
    with pytest.raises(TimeoutError, match="HTTP 404"):
        wait_for_backend_ready(
            "http://backend",
            "/missing",
            timeout_s=0.001,
        )


def test_corpus_rejects_missing_file_duplicate_and_restricted_probe(tmp_path) -> None:
    manifest = tmp_path / "manifest.toml"
    manifest.write_text(
        """
[[books]]
alias = "duplicate"
path = "missing.epub"
license = "public-domain"
sha256 = "0000000000000000000000000000000000000000000000000000000000000000"

[[books]]
alias = "duplicate"
path = "missing-too.epub"
license = "public-domain"
sha256 = "0000000000000000000000000000000000000000000000000000000000000000"
""",
        encoding="utf-8",
    )
    errors = CorpusCatalog(manifest).validate()
    assert any("duplicate alias" in error for error in errors)
    assert sum("file not found" in error for error in errors) == 2

    book = tmp_path / "book.epub"
    book.write_bytes(b"epub")
    digest = hashlib.sha256(b"epub").hexdigest()
    manifest.write_text(
        f"""
[[books]]
alias = "book"
path = "book.epub"
license = "public-domain"
sha256 = "{digest}"

[[books.probes]]
name = "core"
purposes = ["core"]
min_context_tokens = 10
""",
        encoding="utf-8",
    )
    with pytest.raises(LookupError):
        CorpusCatalog(manifest).resolve(
            CorpusRequirement("core", min_context_tokens=11)
        )
    with pytest.raises(LookupError):
        CorpusCatalog(manifest).resolve(CorpusRequirement("core", real_llm=True))


def test_corpus_requires_hash_and_strict_boolean_authorization(tmp_path) -> None:
    book = tmp_path / "book.epub"
    book.write_bytes(b"epub")
    manifest = tmp_path / "manifest.toml"
    manifest.write_text(
        """
[[books]]
alias = "book"
path = "book.epub"
license = "public-domain"
sha256 = "not-a-hash"

[[books.probes]]
name = "core"
purposes = ["core"]
allow_external_judge = "false"
""",
        encoding="utf-8",
    )
    with pytest.raises(TypeError, match="allow_external_judge must be boolean"):
        CorpusCatalog(manifest).validate()

    manifest.write_text(
        """
[[books]]
alias = "book"
path = "book.epub"
license = "public-domain"
sha256 = "not-a-hash"
""",
        encoding="utf-8",
    )
    assert any("64 hex" in error for error in CorpusCatalog(manifest).validate())

    manifest.write_text(
        """
[[books]]
alias = "book"
path = "book.epub"
license = "public-domain"
""",
        encoding="utf-8",
    )
    assert any(
        "sha256 is required" in error for error in CorpusCatalog(manifest).validate()
    )
