from __future__ import annotations

import pytest

from app.config import (
    Settings,
    apply_app_config_overlays,
    restore_app_config_overlays,
)


def test_apply_and_restore_app_config_overlays() -> None:
    settings = Settings()
    original_lookahead = settings.reader.lookahead_paragraphs
    original_preflight = settings.context_l3.preflight_trigger_input_tokens

    applied = apply_app_config_overlays(
        settings,
        {
            "reader": {"lookahead_paragraphs": 42},
            "context_l3": {"preflight_trigger_input_tokens": 16000},
            "context_l2": {"max_live_original_tokens": 32000},
        },
    )
    assert applied["reader"]["lookahead_paragraphs"] == 42
    assert settings.reader.lookahead_paragraphs == 42
    assert settings.context_l3.preflight_trigger_input_tokens == 16000
    assert settings.context_l2.max_live_original_tokens == 32000

    restore = restore_app_config_overlays(settings)
    assert restore["restored"] is True
    assert settings.reader.lookahead_paragraphs == original_lookahead
    assert settings.context_l3.preflight_trigger_input_tokens == original_preflight


def test_overlay_validation_rejects_invalid_l3_order() -> None:
    settings = Settings()
    with pytest.raises(ValueError, match="preflight_trigger_input_tokens"):
        apply_app_config_overlays(
            settings,
            {
                "context_l3": {
                    "preflight_trigger_input_tokens": 200000,
                    "compression_trigger_input_tokens": 128000,
                }
            },
        )
