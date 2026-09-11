# -*- coding: utf-8 -*-
"""
FastAPI backend for InSARHub.

Exposes the existing commands layer as REST endpoints.
React frontend calls these endpoints over HTTP.

Run with:
    uvicorn insarhub.app.api:app --reload --port 8080

Interactive API docs (test without any frontend):
    http://localhost:8080/docs
"""

import asyncio
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

import insarhub.app.state as state
from insarhub.app.models import JobStatus
from insarhub.app.routes import auth, settings, search, folders, processor, analyzer, render, quality

app = FastAPI(title="InSARHub API")

# The production build is served by this same app (see the StaticFiles mount at
# the bottom of this file), so the browser's requests are already same-origin
# and no CORS headers are needed. CORS is only required for `npm run dev`, where
# the Vite dev server on :5173 calls this API cross-origin.
#
# This used to be enabled unconditionally with allow_origins=["*"] +
# allow_credentials=True. Starlette answers that combination by reflecting the
# caller's Origin, so any website the user happened to have open could read from
# -- and post to -- the local API, which has no authentication of its own.
# Reaching the port *is* the authorization here, so dropping allow_credentials
# alone would not have helped: a bare "*" still lets any page read responses.
if os.getenv("INSARHUB_DEV"):
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

# ── Include all routers ──────────────────────────────────────────────────────
app.include_router(auth.router)
app.include_router(settings.router)
app.include_router(search.router)
app.include_router(folders.router)
app.include_router(processor.router)
app.include_router(analyzer.router)
app.include_router(render.router)
app.include_router(quality.router)


# ── Job polling / stop ───────────────────────────────────────────────────────

@app.get("/api/jobs/{job_id}", response_model=JobStatus)
async def get_job(job_id: str):
    """Poll this for background job status and progress."""
    if job_id not in state._jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    return state._jobs[job_id]


@app.post("/api/jobs/{job_id}/stop")
async def stop_job(job_id: str):
    """Signal a cancellable job (e.g. download) to stop."""
    event = state._stop_events.get(job_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Job not found or not cancellable")
    event.set()
    state._jobs[job_id]["message"] = "Stopping…"
    return {"ok": True}


# ── Startup ──────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def _startup_auth_check():
    """Silently populate auth cache in the background when the server starts."""
    from insarhub.app.routes.auth import _build_auth_status

    async def _run():
        state._auth_cache = await asyncio.to_thread(_build_auth_status)

    asyncio.create_task(_run())


# ── Serve React production build (only active after `npm run build`) ─────────

_frontend = Path(__file__).parent / "frontend" / "dist"
if _frontend.exists():
    app.mount("/", StaticFiles(directory=str(_frontend), html=True), name="frontend")
