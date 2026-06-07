from __future__ import annotations

import math
from collections import deque
from typing import Any

import aiosqlite

from ..config import TokenEstimationConfig
from ..repos import token_calibrations as cal_repo


def _local_estimate(text: str) -> int:
    cjk = sum(1 for c in text if "一" <= c <= "鿿")
    non_cjk = len(text) - cjk
    return int(cjk * 1.5 + non_cjk * 0.25)


class TokenEstimator:
    def __init__(self, config: TokenEstimationConfig) -> None:
        self._config = config
        self._ratios: dict[tuple[str, str, str], deque[float]] = {}

    def replace_config(self, config: TokenEstimationConfig) -> None:
        self._config = config

    def estimate(self, text: str) -> int:
        return _local_estimate(text)

    def _calibration_ratio(
        self,
        model: str,
        prompt_version: str = "",
        language_profile: str = "cjk_mixed",
    ) -> tuple[float, int]:
        key = (model, prompt_version, language_profile)
        ratios = self._ratios.get(key)
        bootstrap = self._config.default_bootstrap_calibration_ratio
        if ratios and len(ratios) >= self._config.min_calibration_samples:
            sorted_vals = sorted(ratios)
            idx = max(
                0,
                min(
                    len(sorted_vals) - 1,
                    int(self._config.calibration_percentile * (len(sorted_vals) - 1)),
                ),
            )
            p95 = sorted_vals[idx]
            calibration = max(bootstrap, p95)
        else:
            calibration = bootstrap
        return calibration, len(ratios) if ratios else 0

    def get_safe_estimate(
        self,
        text: str,
        model: str,
        prompt_version: str = "",
        language_profile: str = "cjk_mixed",
    ) -> int:
        raw = _local_estimate(text)
        calibration, _ = self._calibration_ratio(
            model, prompt_version, language_profile
        )
        return math.ceil(raw * calibration * self._config.token_safety_margin)

    def get_calibration_info(
        self,
        model: str,
        prompt_version: str = "",
        language_profile: str = "cjk_mixed",
    ) -> dict[str, Any]:
        calibration, sample_count = self._calibration_ratio(
            model, prompt_version, language_profile
        )
        return {
            "model": model,
            "version": "local_v1",
            "calibration_ratio": round(calibration, 4),
            "sample_count": sample_count,
        }

    async def load_calibrations(self, db: aiosqlite.Connection) -> None:
        rows = await cal_repo.list_calibrations(db)
        for row in rows:
            key = (row["model"], row["prompt_version"], row["language_profile"])
            sample_count = row.get("sample_count", 0)
            p50 = row.get("rolling_p50_ratio", 1.0)
            p95 = row.get("rolling_p95_ratio", 1.0)
            window_size = row.get("window_size", self._config.calibration_window_size)
            if sample_count > 0:
                d: deque[float] = deque(maxlen=window_size)
                n = min(sample_count, window_size)
                # Reconstruct a representative sample: fill with p50, place
                # p95 at the index that _calibration_ratio will pick so the
                # loaded state reproduces the persisted p95.  New real
                # observations will gradually replace these synthetic values.
                p95_idx = max(
                    0, min(n - 1, int(self._config.calibration_percentile * (n - 1)))
                )
                for i in range(n):
                    d.append(p95 if i == p95_idx else p50)
                self._ratios[key] = d

    async def record_observation(
        self,
        db: aiosqlite.Connection,
        model: str,
        prompt_version: str,
        language_profile: str,
        raw_estimate: int,
        actual_tokens: int,
    ) -> None:
        if raw_estimate <= 0 or actual_tokens <= 0:
            return

        ratio = actual_tokens / raw_estimate
        key = (model, prompt_version, language_profile)

        if key not in self._ratios:
            self._ratios[key] = deque(maxlen=self._config.calibration_window_size)
        self._ratios[key].append(ratio)

        ratios = self._ratios[key]
        sorted_vals = sorted(ratios)
        count = len(sorted_vals)

        p50_idx = max(0, min(count - 1, int(0.50 * (count - 1))))
        p95_idx = max(0, min(count - 1, int(0.95 * (count - 1))))

        rolling_p50 = round(sorted_vals[p50_idx], 6)
        rolling_p95 = round(sorted_vals[p95_idx], 6)

        bootstrap = self._config.default_bootstrap_calibration_ratio
        row = await cal_repo.get_or_create(
            db,
            model,
            prompt_version,
            language_profile,
            bootstrap_ratio=bootstrap,
            window_size=self._config.calibration_window_size,
        )
        existing_count = row.get("sample_count", 0)

        await cal_repo.update_calibration(
            db,
            model,
            prompt_version,
            language_profile,
            rolling_p50_ratio=rolling_p50,
            rolling_p95_ratio=rolling_p95,
            sample_count=existing_count + 1,
            window_size=self._config.calibration_window_size,
        )
