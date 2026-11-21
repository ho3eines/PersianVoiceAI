"""The management panel is served locally, offline and with real controls.

The page must render RTL Persian, load only same-origin assets (no CDN, no
external font/script), and every control the doc advertises must call the API
that actually exists.  It is only reachable from loopback.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from managekit import build_app, local_client, make_settings
from server.webui import create_app

CONTROL_DIR = Path(__file__).resolve().parents[1] / "web" / "control"

EXPECTED_ENDPOINTS = (
    "/status",
    "/settings",
    "/providers",
    "/models",
    "/jobs",
    "/services",
)


def test_control_page_is_served_as_rtl_persian(tmp_path: Path) -> None:
    app, _manager, _models = build_app(tmp_path)
    with local_client(app) as client:
        response = client.get("/control")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        page = response.text
        slash = client.get("/control/")
        assert slash.status_code == 200
    assert 'dir="rtl"' in page
    assert 'lang="fa"' in page
    assert "راهنما" in page


def test_assets_are_served_with_the_right_types(tmp_path: Path) -> None:
    app, _manager, _models = build_app(tmp_path)
    with local_client(app) as client:
        script = client.get("/control/app.js")
        style = client.get("/control/styles.css")
        nested = client.get("/control/assets/app.js")
        missing = client.get("/control/nope.js")
    assert script.status_code == 200
    assert "javascript" in script.headers["content-type"]
    assert "/api/manage" in script.text
    assert style.status_code == 200
    assert "text/css" in style.headers["content-type"]
    assert nested.status_code == 200
    assert missing.status_code == 404


def test_panel_assets_are_not_cached(tmp_path: Path) -> None:
    """A stale cached asset hides a shipped fix from the operator."""
    app, _manager, _models = build_app(tmp_path)
    with local_client(app) as client:
        for path in ("/control", "/control/app.js", "/control/styles.css"):
            response = client.get(path)
            assert response.headers.get("cache-control") == "no-cache", path


def test_page_loads_only_same_origin_assets() -> None:
    page = (CONTROL_DIR / "index.html").read_text(encoding="utf-8")
    assert "/control/app.js" in page
    assert "/control/styles.css" in page
    external = re.findall(r'(?:src|href)="(https?://[^"]+)"', page)
    assert not external, external


def test_page_and_script_only_reference_real_endpoints() -> None:
    page = (CONTROL_DIR / "index.html").read_text(encoding="utf-8")
    script = (CONTROL_DIR / "app.js").read_text(encoding="utf-8")
    combined = page + script
    assert "/api/manage" in combined
    for endpoint in EXPECTED_ENDPOINTS:
        assert endpoint in combined, endpoint


def test_panel_is_reachable_only_from_loopback(tmp_path: Path) -> None:
    app = create_app(settings=make_settings(tmp_path), preload=False)
    with TestClient(
        app, client=("127.0.0.1", 5000), base_url="http://127.0.0.1"
    ) as loopback:
        assert loopback.get("/control").status_code == 200
        assert loopback.get("/api/manage/status").status_code == 200
    remote = TestClient(app, client=("10.0.0.9", 5000), base_url="http://127.0.0.1")
    with remote:
        assert remote.get("/control").status_code == 403
        assert remote.get("/api/manage/status").status_code == 403


@pytest.mark.parametrize("asset", ["index.html", "app.js", "styles.css"])
def test_panel_files_exist_and_are_not_empty(asset: str) -> None:
    path = CONTROL_DIR / asset
    assert path.is_file()
    assert path.stat().st_size > 0
