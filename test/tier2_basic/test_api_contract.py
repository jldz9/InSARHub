"""Tier 2 -- the GUI's HTTP surface does what it promises.

The React frontend has no tests of its own, so this file is the only thing
standing between an endpoint rename and a silently broken panel. It checks the
contract from both sides:

  * the routes the backend serves
  * the routes `frontend/src/*.ts(x)` actually calls

A path in one and not the other is a bug in whichever moved last.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from insarhub.app.api import app

FRONTEND = Path(__file__).resolve().parents[2] / "src" / "insarhub" / "app" / "frontend" / "src"


# ── Route inventory ──────────────────────────────────────────────────────────

def _served_routes() -> set[str]:
    out = set()
    for route in app.routes:
        path = getattr(route, "path", None)
        if path and path.startswith("/api/"):
            out.add(path)
    for router in (
        "auth", "settings", "search", "folders",
        "processor", "analyzer", "render", "quality",
    ):
        module = __import__(f"insarhub.app.routes.{router}", fromlist=["router"])
        for route in module.router.routes:
            path = getattr(route, "path", None)
            if path and path.startswith("/api/"):
                out.add(path)
    return out


SERVED = _served_routes()

# Endpoints the GUI depends on. Pinned so a rename is a failure, not a 404 the
# user discovers by clicking.
CORE_ENDPOINTS = {
    "/api/health", "/api/workdir", "/api/settings", "/api/workflows",
    "/api/auth-status", "/api/job-folders", "/api/folder-config",
    "/api/search", "/api/processor-steps", "/api/analyzer-steps",
    "/api/folder-details", "/api/folder-pairs",
}


def test_route_inventory_is_not_empty():
    assert len(SERVED) > 40, f"only found {len(SERVED)} /api routes"


@pytest.mark.parametrize("path", sorted(CORE_ENDPOINTS))
def test_core_endpoint_is_served(path):
    assert path in SERVED, f"{path} is no longer served -- the GUI calls it"


def test_no_duplicate_route_paths():
    """Two handlers on one path: the second silently wins."""
    seen, dupes = set(), []
    for router in (
        "auth", "settings", "search", "folders",
        "processor", "analyzer", "render", "quality",
    ):
        module = __import__(f"insarhub.app.routes.{router}", fromlist=["router"])
        for route in module.router.routes:
            path = getattr(route, "path", None)
            methods = getattr(route, "methods", set()) or set()
            for method in methods:
                key = (method, path)
                if key in seen:
                    dupes.append(key)
                seen.add(key)
    assert not dupes, f"duplicate route definitions: {dupes}"


# ── Frontend/backend agreement ───────────────────────────────────────────────

def _frontend_api_paths() -> set[str]:
    """Every /api/... literal the frontend sources reference."""
    if not FRONTEND.is_dir():
        pytest.skip("frontend sources not present")
    found = set()
    pattern = re.compile(r"[\"'`](/api/[A-Za-z0-9_\-/]+)")
    for path in list(FRONTEND.rglob("*.ts")) + list(FRONTEND.rglob("*.tsx")):
        for match in pattern.findall(path.read_text(encoding="utf-8")):
            found.add(match.rstrip("/"))
    return found


def test_every_endpoint_the_frontend_calls_exists():
    """The check that actually catches GUI breakage.

    Template segments (/api/jobs/{job_id}) are compared by prefix, since the
    frontend interpolates the id.
    """
    called = _frontend_api_paths()
    assert called, "found no /api/ references in the frontend sources"

    templated = {p.split("{")[0].rstrip("/") for p in SERVED}
    missing = sorted(
        path for path in called
        if path not in SERVED and path.rsplit("/", 1)[0] not in templated and path not in templated
    )
    assert not missing, (
        f"the frontend calls endpoints the backend does not serve: {missing}"
    )


# ── Live behaviour ───────────────────────────────────────────────────────────

def test_health(api_client):
    response = api_client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


@pytest.mark.parametrize(
    "path", ["/api/health", "/api/workdir", "/api/settings", "/api/workflows"]
)
def test_read_only_endpoints_respond(api_client, path):
    """GETs that the GUI issues on load must not 500 on a bare install."""
    response = api_client.get(path)
    assert response.status_code < 500, f"{path} -> {response.status_code}"


def test_openapi_schema_builds(api_client):
    """A malformed response model breaks /docs and every generated client."""
    response = api_client.get("/openapi.json")
    assert response.status_code == 200
    assert response.json()["paths"]


def test_unknown_api_path_is_404(api_client):
    assert api_client.get("/api/definitely-not-a-route").status_code == 404


# ── Security contract ────────────────────────────────────────────────────────
#
# The API is unauthenticated -- reaching the port is the authorization -- so it
# must never be readable cross-origin. The full regression suite for the 0.4.0
# wildcard-CORS bug lives in tier4_regression/test_bug_0400_cors_wildcard.py;
# this is the one-line standing check that belongs with the rest of the GUI
# contract.

def test_no_cors_headers_by_default(api_client):
    response = api_client.get(
        "/api/health", headers={"Origin": "https://evil.example.com"}
    )
    assert "access-control-allow-origin" not in {k.lower() for k in response.headers}
