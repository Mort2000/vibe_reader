"""Load project .env for verification tests and CLI."""
from __future__ import annotations

import pathlib


def load_project_dotenv() -> pathlib.Path | None:
    """Load the first .env found under cwd, backend root, or repo root."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return None

    here = pathlib.Path(__file__).resolve()
    backend_root = here.parents[2]
    repo_root = here.parents[3]

    seen: set[pathlib.Path] = set()
    for base in (pathlib.Path.cwd(), backend_root, repo_root):
        candidate = base.resolve() / ".env"
        if candidate in seen:
            continue
        seen.add(candidate)
        if candidate.is_file():
            load_dotenv(candidate, override=False)
            return candidate
    return None
