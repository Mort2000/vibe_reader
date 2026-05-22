"""Reset verification data directory for system testing."""
from __future__ import annotations

import pathlib
import shutil
from typing import Any

import aiosqlite
from fastapi import Request

from ..config import Settings, _default_data_dir
from ..db import init_db
from ..errors import AppError


def _default_user_data_dir() -> pathlib.Path:
    return _default_data_dir()


def _normalize_data_dir(path: str | pathlib.Path) -> pathlib.Path:
    return pathlib.Path(path).expanduser().resolve()


def _validate_reset_target(settings: Settings, confirm_data_dir: str) -> pathlib.Path:
    expected = settings.data_dir.resolve()
    confirmed = _normalize_data_dir(confirm_data_dir)

    if confirmed != expected:
        raise AppError(
            "data_dir_mismatch",
            "confirm_data_dir does not match backend data_dir",
            status=400,
            details={
                "confirm_data_dir": str(confirmed),
                "backend_data_dir": str(expected),
            },
        )

    default_user = _default_user_data_dir().resolve()
    if confirmed == default_user and not settings.verify_mode:
        raise AppError(
            "unsafe_reset_target",
            "Refusing to reset default user data directory without verify mode",
            status=403,
            details={"data_dir": str(confirmed)},
        )

    return confirmed


def _clear_directory_contents(directory: pathlib.Path) -> None:
    if not directory.exists():
        directory.mkdir(parents=True, exist_ok=True)
        return
    for item in directory.iterdir():
        if item.is_dir():
            shutil.rmtree(item)
        else:
            item.unlink()


async def reset_verify_data(request: Request, confirm_data_dir: str) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    if not settings.verify_mode:
        raise AppError(
            "verify_mode_required",
            "Verify endpoints require VIBE_READER_VERIFY_MODE=1",
            status=404,
        )

    data_dir = _validate_reset_target(settings, confirm_data_dir)

    db: aiosqlite.Connection | None = getattr(request.app.state, "db", None)
    if db is not None:
        await db.close()
        request.app.state.db = None

    for suffix in ("", "-wal", "-shm"):
        db_file = settings.db_path.with_name(settings.db_path.name + suffix)
        if db_file.exists():
            db_file.unlink()

    config_path = settings.config_path
    if config_path.exists():
        config_path.unlink()

    _clear_directory_contents(settings.books_dir)
    _clear_directory_contents(settings.logs_dir)

    request.app.state.db = await init_db(settings.db_path)

    return {
        "reset": True,
        "data_dir": str(data_dir),
    }
