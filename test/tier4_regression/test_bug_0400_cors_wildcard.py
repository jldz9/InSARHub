"""Any website could read from — and post to — the local InSARHub API.

Fixed in:   0.4.0
Changelog:  "Fixed the web API accepting cross-origin requests from any website."
Symptom:    While `insarhub-app` was running, any page open in the same browser
            could call the API and read the responses: Earthdata/CDSE/CDS
            connection state and HyP3 usernames from /api/auth-status, folder
            and product listings, job submission, DELETE /api/job-folder, and
            POST /api/credentials/* which writes credentials to disk.
Root cause: CORS was enabled unconditionally with `allow_origins=["*"]` and
            `allow_credentials=True`. Starlette answers that combination by
            reflecting the caller's Origin, so every origin was allowed.

The API has no authentication of its own -- reaching the port *is* the
authorization -- which is why the obvious partial fix does not work: dropping
`allow_credentials` while leaving a bare "*" still permits cross-origin reads.
`test_dropping_allow_credentials_alone_is_not_enough` pins that, because it is
the change someone will reach for first.
"""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

BUG = {
    "id": "0400-cors-wildcard",
    "fixed_in": "0.4.0",
    "area": "app/security",
}

HOSTILE = "https://evil.example.com"


def _header_names(response) -> set[str]:
    return {k.lower() for k in response.headers}


# ── The fix ──────────────────────────────────────────────────────────────────

def test_hostile_origin_gets_no_cors_headers(api_client):
    response = api_client.get("/api/health", headers={"Origin": HOSTILE})
    assert response.status_code == 200, "same-origin behaviour must be unchanged"
    assert "access-control-allow-origin" not in _header_names(response)


def test_auth_status_is_not_readable_cross_origin(api_client):
    """The endpoint that leaked account names and the HyP3 credit pool."""
    response = api_client.get("/api/auth-status", headers={"Origin": HOSTILE})
    assert "access-control-allow-origin" not in _header_names(response)


def test_credential_write_preflight_is_refused(api_client):
    """The preflight used to come back approving POST from any origin."""
    response = api_client.options(
        "/api/credentials/earthdata",
        headers={
            "Origin": HOSTILE,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    headers = _header_names(response)
    assert "access-control-allow-origin" not in headers
    assert "access-control-allow-methods" not in headers


@pytest.mark.parametrize(
    "path",
    ["/api/health", "/api/workdir", "/api/settings", "/api/job-folders", "/api/workflows"],
)
def test_no_endpoint_leaks_cors_headers(api_client, path):
    response = api_client.get(path, headers={"Origin": HOSTILE})
    assert "access-control-allow-origin" not in _header_names(response)


# ── The partial fix that does not work ───────────────────────────────────────

def test_dropping_allow_credentials_alone_is_not_enough():
    """Documents *why* the fix is the origin list, not the credentials flag.

    With allow_origins=["*"] and allow_credentials=False, Starlette returns a
    bare `Access-Control-Allow-Origin: *`, which still lets any page read the
    response -- and since nothing here authenticates with cookies, losing the
    cookies costs an attacker nothing.

    Asserted against a throwaway app so it describes the CORS semantics without
    depending on InSARHub being misconfigured.
    """
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware

    app = FastAPI()
    app.add_middleware(
        CORSMiddleware, allow_origins=["*"], allow_credentials=False, allow_methods=["*"]
    )

    @app.get("/probe")
    def probe():
        return {"secret": "readable"}

    with TestClient(app) as client:
        response = client.get("/probe", headers={"Origin": HOSTILE})

    assert response.headers.get("access-control-allow-origin") == "*", (
        "a bare '*' is still a cross-origin read grant -- this is why the fix "
        "had to restrict allow_origins, not just disable allow_credentials"
    )


# ── Dev mode still works ─────────────────────────────────────────────────────

def test_vite_dev_server_is_still_allowed_under_insarhub_dev(monkeypatch):
    """If `npm run dev` breaks, someone will restore the wildcard.

    The middleware stack is assembled at import time from the env var, so the
    module is reloaded here and restored afterwards.
    """
    monkeypatch.setenv("INSARHUB_DEV", "1")
    import insarhub.app.api as api_module

    reloaded = importlib.reload(api_module)
    try:
        with TestClient(reloaded.app) as client:
            allowed = client.get(
                "/api/health", headers={"Origin": "http://localhost:5173"}
            )
            assert allowed.headers.get("access-control-allow-origin") == "http://localhost:5173"

            denied = client.get("/api/health", headers={"Origin": HOSTILE})
            assert "access-control-allow-origin" not in _header_names(denied)
    finally:
        monkeypatch.delenv("INSARHUB_DEV", raising=False)
        importlib.reload(api_module)
