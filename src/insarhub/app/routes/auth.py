# -*- coding: utf-8 -*-
"""Authentication status and credential management endpoints."""

import asyncio
import json
import re

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

import insarhub.app.state as state
from insarhub.app.models import CredentialsBody

router = APIRouter()


# ---------------------------------------------------------------------------
# Auth check helpers
# ---------------------------------------------------------------------------

def _netrc_has(host: str) -> bool:
    if not state._NETRC.is_file():
        return False
    try:
        return f"machine {host}" in state._NETRC.read_text()
    except Exception:
        return False


def _check_cds_connected() -> bool:
    if not state._CDSAPIRC.is_file():
        return False
    key = None
    for line in state._CDSAPIRC.read_text().splitlines():
        if line.strip().startswith("key:"):
            key = line.split(":", 1)[1].strip()
            break
    if not key:
        return False
    try:
        import requests
        resp = requests.get(
            "https://cds.climate.copernicus.eu/api/retrieve/v1/jobs",
            headers={"PRIVATE-TOKEN": key},
            params={"limit": 1},
            timeout=5,
        )
        return resp.status_code == 200
    except Exception:
        return False


def _check_cdse_connected() -> bool:
    if not state._NETRC.is_file():
        return False
    try:
        import netrc as netrc_lib
        import requests
        creds = netrc_lib.netrc(str(state._NETRC)).authenticators("dataspace.copernicus.eu")
        if not creds:
            return False
        username, _, password = creds
        resp = requests.post(
            "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token",
            data={
                "grant_type": "password",
                "client_id": "cdse-public",
                "username": username,
                "password": password,
            },
            timeout=10,
        )
        return resp.status_code == 200
    except Exception:
        return False


def _read_credit_pool_pairs() -> list[tuple[str, str]]:
    if not state._CREDIT_POOL.is_file():
        return []
    try:
        pairs = []
        for line in state._CREDIT_POOL.read_text().splitlines():
            line = line.strip()
            if line and ':' in line:
                user, pwd = line.split(':', 1)
                pairs.append((user.strip(), pwd.strip()))
        return pairs
    except Exception:
        return []


def _check_hyp3_account(username: str | None = None, password: str | None = None) -> dict:
    try:
        from hyp3_sdk import HyP3
        kwargs: dict = {}
        if username:
            kwargs["username"] = username
        if password:
            kwargs["password"] = password
        hyp3    = HyP3(**kwargs)
        credits = hyp3.check_credits()
        display   = username
        per_month = None
        try:
            info = hyp3.my_info()
            if hasattr(info, "__dict__"):
                info = vars(info)
            display   = display or info.get("user_id") or info.get("username")
            per_month = info.get("credits_per_month")
        except Exception:
            pass
        return {
            "username":          display or "—",
            "credits_remaining": credits,
            "credits_per_month": per_month,
        }
    except Exception as e:
        return {"username": username or "—", "error": str(e)}


def _build_auth_status() -> dict:
    earthdata_connected = _netrc_has("urs.earthdata.nasa.gov")
    cdse_connected      = _check_cdse_connected()
    pool_pairs          = _read_credit_pool_pairs()
    hyp3_main           = _check_hyp3_account()
    credit_pool         = [_check_hyp3_account(u, p) for u, p in pool_pairs]
    cds_connected       = _check_cds_connected()
    return {
        "earthdata_connected": earthdata_connected,
        "cdse_connected":      cdse_connected,
        "cds_connected":       cds_connected,
        "hyp3":                hyp3_main,
        "credit_pool":         credit_pool,
        "credit_pool_exists":  state._CREDIT_POOL.is_file() and bool(pool_pairs),
    }


def _netrc_upsert(host: str, username: str, password: str) -> None:
    entry = f"machine {host}\n    login {username}\n    password {password}\n"
    if state._NETRC.is_file():
        text    = state._NETRC.read_text()
        pattern = re.compile(
            rf"machine\s+{re.escape(host)}\s+login\s+\S+\s+password\s+\S+\n?",
            re.MULTILINE,
        )
        if pattern.search(text):
            text = pattern.sub(entry, text)
        else:
            text = text.rstrip("\n") + "\n" + entry
        state._NETRC.write_text(text)
    else:
        state._NETRC.write_text(entry)
    state._NETRC.chmod(0o600)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/api/auth-status")
async def get_auth_status(refresh: bool = False):
    if refresh or state._auth_cache is None:
        state._auth_cache = await asyncio.to_thread(_build_auth_status)
    return state._auth_cache


@router.get("/api/auth-status/stream")
async def stream_auth_status():
    async def generate():
        earthdata  = _netrc_has("urs.earthdata.nasa.gov")
        cdse       = _check_cdse_connected()
        pool_pairs = _read_credit_pool_pairs()
        yield f"data: {json.dumps({'type': 'netrc', 'earthdata_connected': earthdata, 'cdse_connected': cdse, 'credit_pool_exists': bool(pool_pairs)})}\n\n"

        queue: asyncio.Queue[tuple[str, dict]] = asyncio.Queue()

        async def _check(kind: str, u: str | None = None, p: str | None = None) -> None:
            result = await asyncio.to_thread(_check_hyp3_account, u, p)
            await queue.put((kind, result))

        async def _check_cds() -> None:
            connected = await asyncio.to_thread(_check_cds_connected)
            await queue.put(("cds", {"connected": connected}))

        n_tasks = 1 + len(pool_pairs) + 1
        asyncio.create_task(_check("main"))
        for u, p in pool_pairs:
            asyncio.create_task(_check("pool", u, p))
        asyncio.create_task(_check_cds())

        for _ in range(n_tasks):
            kind, data = await queue.get()
            yield f"data: {json.dumps({'type': kind, 'data': data})}\n\n"

        yield f"data: {json.dumps({'type': 'done'})}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/api/credentials/earthdata")
async def save_earthdata_credentials(body: CredentialsBody):
    if not body.username or not body.password:
        raise HTTPException(status_code=400, detail="username and password required")
    await asyncio.to_thread(_netrc_upsert, "urs.earthdata.nasa.gov", body.username, body.password)
    await asyncio.to_thread(_netrc_upsert, "asf.alaska.edu", body.username, body.password)
    return {"ok": True}


@router.post("/api/credentials/cdse")
async def save_cdse_credentials(body: CredentialsBody):
    if not body.username or not body.password:
        raise HTTPException(status_code=400, detail="username and password required")
    await asyncio.to_thread(_netrc_upsert, "dataspace.copernicus.eu", body.username, body.password)
    return {"ok": True}


@router.post("/api/credentials/cds")
async def save_cds_credentials(body: CredentialsBody):
    if not body.token:
        raise HTTPException(status_code=400, detail="token required")
    def _write():
        state._CDSAPIRC.write_text(f"url: https://cds.climate.copernicus.eu/api\nkey: {body.token}\n")
    await asyncio.to_thread(_write)
    return {"ok": True}


@router.post("/api/credentials/credit-pool")
async def save_credit_pool_entry(body: CredentialsBody):
    if not body.username or not body.password:
        raise HTTPException(status_code=400, detail="username and password required")
    def _append():
        existing = state._CREDIT_POOL.read_text().splitlines() if state._CREDIT_POOL.is_file() else []
        lines = [l for l in existing if not l.startswith(f"{body.username}:")]
        lines.append(f"{body.username}:{body.password}")
        state._CREDIT_POOL.write_text("\n".join(lines) + "\n")
        state._CREDIT_POOL.chmod(0o600)
    await asyncio.to_thread(_append)
    return {"ok": True}


@router.delete("/api/credentials/credit-pool/{username}")
async def delete_credit_pool_entry(username: str):
    def _remove():
        if not state._CREDIT_POOL.is_file():
            return
        lines = [l for l in state._CREDIT_POOL.read_text().splitlines() if not l.startswith(f"{username}:")]
        state._CREDIT_POOL.write_text("\n".join(lines) + "\n")
    await asyncio.to_thread(_remove)
    return {"ok": True}
