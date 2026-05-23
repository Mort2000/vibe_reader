"""Filesystem storage for verify agent interaction packets.

Full interaction JSON is not kept in SQLite; only ``interaction_path`` is indexed
in ``verify_agent_runs`` and loaded on demand.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any


def interaction_file_path(
    data_dir: pathlib.Path,
    verify_run_id: str,
    invocation_id: str,
) -> pathlib.Path:
    run_scope = verify_run_id or "_unscoped"
    return data_dir / "verify_agent_interactions" / run_scope / f"{invocation_id}.json"


def persist_interaction_packet(
    data_dir: pathlib.Path,
    *,
    verify_run_id: str,
    invocation_id: str,
    packet: dict[str, Any],
) -> str:
    """Write packet to disk; return path relative to ``data_dir``."""
    path = interaction_file_path(data_dir, verify_run_id, invocation_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(packet, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return str(path.relative_to(data_dir))


def load_interaction_packet(
    data_dir: pathlib.Path,
    interaction_path: str,
) -> dict[str, Any] | None:
    if not interaction_path:
        return None
    path = data_dir / interaction_path
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
