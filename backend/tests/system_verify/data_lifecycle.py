"""Backend data directory lifecycle for system verification runs."""

from __future__ import annotations

import pathlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .client import TargetClient
    from .config import VerifyConfig
    from .run import RunManager

DEFAULT_USER_DATA_DIR = pathlib.Path.home() / ".vibe_reader"


class DataDirError(RuntimeError):
    """Raised when backend data_dir is not a safe isolated verification target."""


def assert_isolated_data_dir(expected: pathlib.Path | str, actual: str) -> None:
    """Ensure backend uses the configured isolated data directory."""
    expected_path = pathlib.Path(expected).expanduser().resolve()
    actual_path = pathlib.Path(actual).expanduser().resolve()

    if expected_path == DEFAULT_USER_DATA_DIR.resolve():
        raise DataDirError(
            f"Verify config target.data_dir must not be the default user directory: {expected_path}"
        )

    if actual_path == DEFAULT_USER_DATA_DIR.resolve():
        raise DataDirError(
            "Backend is using the default user data directory (~/.vibe_reader). "
            "Start the backend with VIBE_READER_DATA_DIR set to an isolated path."
        )

    if actual_path != expected_path:
        raise DataDirError(
            f"Backend data_dir mismatch: expected {expected_path}, got {actual_path}"
        )


async def reset_backend_data(client: TargetClient, data_dir: str) -> dict[str, Any]:
    """Call POST /api/verify/reset to clear verification data."""
    body, rec = await client.verify_reset(data_dir)

    if rec.status_code == 404:
        err = body.get("error", {}) if isinstance(body, dict) else {}
        code = err.get("code", "") if isinstance(err, dict) else ""
        if code == "verify_mode_required":
            raise DataDirError(
                "Verify reset requires VIBE_READER_VERIFY_MODE=1 on the backend. "
                f"Start with: VIBE_READER_DATA_DIR={data_dir} VIBE_READER_VERIFY_MODE=1 ..."
            )
        raise DataDirError(f"Verify reset endpoint unavailable (HTTP 404): {body}")

    if rec.status_code >= 400:
        err = body.get("error", {}) if isinstance(body, dict) else {}
        message = err.get("message", str(body)) if isinstance(err, dict) else str(body)
        raise DataDirError(f"Verify reset failed (HTTP {rec.status_code}): {message}")

    if not body.get("reset"):
        raise DataDirError(f"Verify reset did not confirm reset: {body}")

    return body


async def prepare_run_data_dir(
    config: VerifyConfig,
    run_manager: RunManager,
    *,
    phase: str,
) -> dict[str, Any]:
    """Validate backend data_dir and reset verification data.

    phase: 'pre' before scenarios, 'post' after successful run.
    """
    from .client import TargetClient

    scenario_id = "data_lifecycle"
    step_id = f"{phase}_reset"

    async with TargetClient(
        config.target.base_url,
        run_manager,
        verify_scenario_id=scenario_id,
        verify_step_id=step_id,
    ) as client:
        runtime_body, rec = await client.runtime()
        if rec.status_code >= 400:
            raise DataDirError(
                f"Failed to read backend runtime (HTTP {rec.status_code})"
            )

        actual_data_dir = runtime_body.get("data_dir", "")
        assert_isolated_data_dir(config.target_data_dir, actual_data_dir)

        return await reset_backend_data(client, actual_data_dir)
