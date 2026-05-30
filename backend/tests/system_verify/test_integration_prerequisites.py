"""Unit tests for integration prerequisite checks."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from tests.system_verify.core.config import VerifyConfig
from tests.system_verify.corpus import CorpusManager
from tests.system_verify.integration_prerequisites import check_integration_prerequisites


def test_check_integration_prerequisites_backend_unreachable() -> None:
    config = VerifyConfig()
    with patch(
        "tests.system_verify.integration_prerequisites._fetch_json",
        return_value=(None, 0),
    ):
        issues = check_integration_prerequisites(config, None)
    assert len(issues) == 1
    assert "backend unreachable" in issues[0]


def test_check_integration_prerequisites_verify_mode_required() -> None:
    config = VerifyConfig()
    with patch(
        "tests.system_verify.integration_prerequisites._fetch_json",
        side_effect=[
            ({"status": "ok"}, 200),
            ({"data_dir": str(config.target_data_dir), "llm": {}}, 200),
            (None, 404),
        ],
    ):
        issues = check_integration_prerequisites(config, None)
    assert any("verify endpoints unavailable" in issue for issue in issues)


def test_check_integration_prerequisites_corpus_invalid(tmp_path) -> None:
    config = VerifyConfig()
    manifest = tmp_path / "manifest.toml"
    manifest.write_text('[[books]]\nalias = "missing"\npath = "nope.epub"\n', encoding="utf-8")
    corpus = CorpusManager(config, manifest)
    corpus.load()
    with patch(
        "tests.system_verify.integration_prerequisites._fetch_json",
        side_effect=[
            ({"status": "ok"}, 200),
            ({"data_dir": str(config.target_data_dir), "llm": {}}, 200),
            ({"verify_mode": True, "llm": {}}, 200),
        ],
    ), patch(
        "tests.system_verify.integration_prerequisites.validate_backend_stub_llm",
        return_value=[],
    ):
        issues = check_integration_prerequisites(config, corpus, aimock_session=MagicMock())
    assert any("corpus invalid" in issue for issue in issues)
