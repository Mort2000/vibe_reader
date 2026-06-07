from __future__ import annotations

import pytest
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.main import SpaStaticFiles


def _scope(path: str, method: str = "GET") -> dict[str, object]:
    return {
        "type": "http",
        "method": method,
        "path": path,
        "headers": [],
    }


@pytest.mark.asyncio
async def test_spa_static_files_falls_back_to_index_for_frontend_route(
    tmp_path,
) -> None:
    (tmp_path / "index.html").write_text("<main>app</main>", encoding="utf-8")
    files = SpaStaticFiles(directory=tmp_path, html=True)

    response = await files.get_response(
        "books/1/chapters/2", _scope("/books/1/chapters/2")
    )

    assert response.status_code == 200
    assert response.media_type == "text/html"


@pytest.mark.asyncio
async def test_spa_static_files_does_not_fallback_for_api_route(tmp_path) -> None:
    (tmp_path / "index.html").write_text("<main>app</main>", encoding="utf-8")
    files = SpaStaticFiles(directory=tmp_path, html=True)

    with pytest.raises(StarletteHTTPException) as exc_info:
        await files.get_response("api/missing", _scope("/api/missing"))

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("asset_path", ["assets/missing.js", "assets/missing"])
async def test_spa_static_files_does_not_fallback_for_missing_asset(
    tmp_path,
    asset_path: str,
) -> None:
    (tmp_path / "index.html").write_text("<main>app</main>", encoding="utf-8")
    files = SpaStaticFiles(directory=tmp_path, html=True)

    with pytest.raises(StarletteHTTPException) as exc_info:
        await files.get_response(asset_path, _scope(f"/{asset_path}"))

    assert exc_info.value.status_code == 404
