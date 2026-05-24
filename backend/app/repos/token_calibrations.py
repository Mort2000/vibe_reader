from __future__ import annotations

from typing import Any

import aiosqlite


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


async def get_or_create(
    db: aiosqlite.Connection,
    model: str,
    prompt_version: str,
    language_profile: str,
    *,
    bootstrap_ratio: float = 1.0,
    window_size: int = 50,
) -> dict[str, Any]:
    cur = await db.execute(
        "SELECT * FROM token_estimation_calibrations "
        "WHERE model = ? AND prompt_version = ? AND language_profile = ?",
        (model, prompt_version, language_profile),
    )
    row = await cur.fetchone()
    if row:
        return dict(row)

    now = _now()
    cur = await db.execute(
        """INSERT INTO token_estimation_calibrations
           (model, prompt_version, language_profile,
            bootstrap_calibration_ratio, rolling_p50_ratio, rolling_p95_ratio,
            sample_count, window_size, updated_at)
           VALUES (?, ?, ?, ?, 1.0, 1.0, 0, ?, ?)""",
        (model, prompt_version, language_profile, bootstrap_ratio, window_size, now),
    )
    await db.commit()
    return {
        "id": cur.lastrowid,
        "model": model,
        "prompt_version": prompt_version,
        "language_profile": language_profile,
        "bootstrap_calibration_ratio": bootstrap_ratio,
        "rolling_p50_ratio": 1.0,
        "rolling_p95_ratio": 1.0,
        "sample_count": 0,
        "window_size": window_size,
        "updated_at": now,
    }


async def update_calibration(
    db: aiosqlite.Connection,
    model: str,
    prompt_version: str,
    language_profile: str,
    *,
    rolling_p50_ratio: float,
    rolling_p95_ratio: float,
    sample_count: int,
    window_size: int,
) -> None:
    now = _now()
    await db.execute(
        """UPDATE token_estimation_calibrations
           SET rolling_p50_ratio = ?, rolling_p95_ratio = ?,
               sample_count = ?, window_size = ?, updated_at = ?
           WHERE model = ? AND prompt_version = ? AND language_profile = ?""",
        (
            rolling_p50_ratio,
            rolling_p95_ratio,
            sample_count,
            window_size,
            now,
            model,
            prompt_version,
            language_profile,
        ),
    )
    await db.commit()


async def list_calibrations(
    db: aiosqlite.Connection,
) -> list[dict[str, Any]]:
    cur = await db.execute(
        "SELECT * FROM token_estimation_calibrations ORDER BY model, prompt_version"
    )
    rows = await cur.fetchall()
    return [dict(r) for r in rows]
