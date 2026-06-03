from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.errors import AppError
from app.routers.verify import _require_verify, router


def test_verify_router_exposes_only_temporary_compatibility_routes() -> None:
    route_methods = {
        (method, route.path)
        for route in router.routes
        for method in getattr(route, "methods", set())
    }

    assert route_methods == {
        ("GET", "/verify/agent-runs"),
        ("POST", "/verify/llm-ping"),
    }


def test_require_verify_rejects_disabled_mode() -> None:
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(settings=SimpleNamespace(verify_mode=False))
        )
    )

    with pytest.raises(AppError) as exc_info:
        _require_verify(request)

    assert exc_info.value.code == "verify_mode_required"
    assert exc_info.value.status == 404


def test_require_verify_allows_enabled_mode() -> None:
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(settings=SimpleNamespace(verify_mode=True))
        )
    )

    _require_verify(request)
