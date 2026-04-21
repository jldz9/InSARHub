# -*- coding: utf-8 -*-
"""Settings, health, workdir, and job-folder management endpoints."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException

import insarhub.app.state as state
from insarhub.app.models import SettingsUpdate, FolderConfigPatch
from insarhub.app.state import read_insarhub_config, write_insarhub_config

router = APIRouter()


@router.get("/api/health")
async def health():
    return {"status": "ok"}


@router.get("/api/workdir")
async def get_workdir():
    return {"workdir": state._settings["workdir"]}


@router.get("/api/pick-folder")
async def pick_folder():
    """Open a native folder-picker dialog and return the selected path."""
    is_wsl = Path("/proc/version").exists() and \
             "microsoft" in Path("/proc/version").read_text().lower()
    _PS_FOLDER_SCRIPT = (
        "Add-Type -AssemblyName System.Windows.Forms; "
        "$f = New-Object System.Windows.Forms.FolderBrowserDialog; "
        "$f.Description = 'Select work directory'; "
        "$f.UseDescriptionForTitle = $true; "
        "$f.AutoUpgradeEnabled = $true; "
        "$null = $f.ShowDialog(); "
        "Write-Output $f.SelectedPath"
    )

    if is_wsl:
        res = subprocess.run(["powershell.exe", "-NoProfile", "-Command", _PS_FOLDER_SCRIPT],
                             capture_output=True, text=True)
        win_path = res.stdout.strip()
        if not win_path:
            return {"path": None}
        conv = subprocess.run(["wslpath", "-u", win_path], capture_output=True, text=True)
        return {"path": conv.stdout.strip() or None}

    if sys.platform == "win32":
        res = subprocess.run(["powershell", "-NoProfile", "-Command", _PS_FOLDER_SCRIPT],
                             capture_output=True, text=True)
        return {"path": res.stdout.strip() or None}

    if sys.platform == "darwin":
        res = subprocess.run(
            ["osascript", "-e", "POSIX path of (choose folder with prompt \"Select work directory\")"],
            capture_output=True, text=True,
        )
        return {"path": res.stdout.strip().rstrip("/") or None}

    for cmd in [
        ["zenity", "--file-selection", "--directory", "--title=Select work directory"],
        ["kdialog", "--getexistingdirectory", "/"],
    ]:
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if res.returncode == 0 and res.stdout.strip():
                return {"path": res.stdout.strip()}
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue

    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk(); root.withdraw(); root.wm_attributes("-topmost", True)
        path = filedialog.askdirectory(title="Select work directory")
        root.destroy()
        return {"path": path or None}
    except Exception:
        pass

    return {"path": None}


@router.get("/api/job-folders")
async def get_job_folders():
    workdir = Path(state._settings["workdir"])
    if not workdir.exists():
        return {"jobs": []}
    jobs = []
    for subfolder in sorted(workdir.iterdir()):
        if not subfolder.is_dir():
            continue
        cfg = read_insarhub_config(subfolder)
        tags: list[str] = []
        if (subfolder / "hyp3_jobs.json").exists() or list(subfolder.glob("hyp3_retry_jobs_*.json")):
            tags.append("HyP3")
        if (subfolder / ".mintpy.cfg").exists() or cfg.get("analyzer"):
            tags.append("MintPy")
        if list(subfolder.glob("stack_p*_f*.json")):
            tags.append("SBAS")
        workflow = {
            "downloader": cfg.get("downloader", {}).get("type", ""),
            "processor":  cfg.get("processor",  {}).get("type", ""),
            "analyzer":   cfg.get("analyzer",   {}).get("type", ""),
        }
        jobs.append({"name": subfolder.name, "path": str(subfolder), "tags": tags, "workflow": workflow})
    return {"jobs": jobs}


@router.get("/api/folder-config")
async def get_folder_config(path: str):
    """Return insarhub_config.json for a folder, merged with in-memory defaults."""
    folder = Path(path).expanduser().resolve()
    cfg = read_insarhub_config(folder)
    # Fill missing sections with in-memory defaults so GUI always has full config
    if "downloader" not in cfg:
        dl = state._settings["downloader"]
        cfg["downloader"] = {"type": dl, "config": state._settings["downloader_config"]}
    if "processor" not in cfg:
        pr = state._settings["processor"]
        cfg["processor"] = {"type": pr, "config": state._settings["processor_config"]}
    if "analyzer" not in cfg:
        az = state._settings["analyzer"]
        cfg["analyzer"] = {"type": az, "config": state._settings["analyzer_configs"].get(az, {})}
    return cfg


@router.patch("/api/folder-config")
async def patch_folder_config(path: str, body: FolderConfigPatch):
    """Write analyzer config back to a folder's insarhub_config.json."""
    folder = Path(path).expanduser().resolve()
    if not folder.exists():
        raise HTTPException(status_code=404, detail="Folder not found")
    az_cfg = body.analyzer_config
    cfg = read_insarhub_config(folder)
    az_section = cfg.get("analyzer", {})
    existing = az_section.get("config", {})
    existing.update(az_cfg)
    az_section["config"] = existing
    write_insarhub_config(folder, {"analyzer": az_section})
    return {"ok": True}


@router.delete("/api/job-folder")
async def delete_job_folder(path: str):
    folder  = Path(path).expanduser().resolve()
    workdir = Path(state._settings["workdir"])
    if not folder.exists():
        raise HTTPException(status_code=404, detail="Folder not found")
    try:
        folder.relative_to(workdir)
    except ValueError:
        raise HTTPException(status_code=403, detail="Folder is not inside workdir")
    if not folder.is_dir():
        raise HTTPException(status_code=400, detail="Path is not a directory")
    shutil.rmtree(folder)
    return {"ok": True}


@router.get("/api/settings")
async def get_settings():
    cur_analyzer    = state._settings["analyzer"]
    analyzer_configs: dict = state._settings.get("analyzer_configs", {})
    return {
        "workdir":              state._settings["workdir"],
        "max_download_workers": state._settings["max_download_workers"],
        "downloader":           state._settings["downloader"],
        "downloader_config":    state._settings["downloader_config"],
        "processor":            state._settings["processor"],
        "processor_config":     state._settings["processor_config"],
        "analyzer":             cur_analyzer,
        "analyzer_configs":     analyzer_configs,
    }


@router.patch("/api/settings")
async def update_settings(req: SettingsUpdate):
    if req.workdir is not None:
        p = Path(req.workdir).expanduser().resolve()
        p.mkdir(parents=True, exist_ok=True)
        state._settings["workdir"] = str(p)
    if req.max_download_workers is not None:
        state._settings["max_download_workers"] = max(1, min(99, req.max_download_workers))
    if req.downloader is not None:
        state._settings["downloader"] = req.downloader
        state._settings["downloader_config"] = state._safe_config_values(req.downloader, state._DOWNLOADERS_META)
    if req.downloader_config is not None:
        cfg = {k: v for k, v in req.downloader_config.items() if k not in state._TOPBAR_FIELDS}
        state._settings["downloader_config"].update(cfg)
    if req.processor is not None:
        state._settings["processor"] = req.processor
        state._settings["processor_config"] = state._default_config_values(req.processor, state._PROCESSORS_META)
    if req.processor_config is not None:
        state._settings["processor_config"].update(req.processor_config)
    if req.analyzer is not None:
        state._settings["analyzer"] = req.analyzer
        if req.analyzer not in state._settings["analyzer_configs"]:
            state._settings["analyzer_configs"][req.analyzer] = state._default_config_values(req.analyzer, state._ANALYZERS_META)
    if req.analyzer_config is not None:
        target = req.analyzer if req.analyzer is not None else state._settings["analyzer"]
        if target not in state._settings["analyzer_configs"]:
            state._settings["analyzer_configs"][target] = state._default_config_values(target, state._ANALYZERS_META)
        state._settings["analyzer_configs"][target].update(req.analyzer_config)
    return await get_settings()


@router.get("/api/workflows")
async def get_workflows():
    return {
        "downloaders": state._DOWNLOADERS_META,
        "processors":  state._PROCESSORS_META,
        "analyzers":   state._ANALYZERS_META,
    }
