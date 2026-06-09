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


_PS_MODERN_FOLDER_SCRIPT = """\
$OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
[ComImport, ClassInterface(ClassInterfaceType.None), Guid("DC1C5A9C-E88A-4dde-A5A1-60F82A20AEF7")]
public class FileOpenDialog {}
[ComImport, Guid("42F85136-DB7E-439C-85F1-E4075D135FC8"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface IFileDialog {
    [PreserveSig] int Show(IntPtr h);
    [PreserveSig] int SetFileTypes(uint n, IntPtr p);
    [PreserveSig] int SetFileTypeIndex(uint i);
    [PreserveSig] int GetFileTypeIndex(out uint i);
    [PreserveSig] int Advise(IntPtr p, out uint c);
    [PreserveSig] int Unadvise(uint c);
    [PreserveSig] int SetOptions(uint f);
    [PreserveSig] int GetOptions(out uint f);
    [PreserveSig] int SetDefaultFolder(IntPtr p);
    [PreserveSig] int SetFolder(IntPtr p);
    [PreserveSig] int GetFolder(out IntPtr p);
    [PreserveSig] int GetCurrentSelection(out IntPtr p);
    [PreserveSig] int SetFileName([MarshalAs(UnmanagedType.LPWStr)] string n);
    [PreserveSig] int GetFileName([MarshalAs(UnmanagedType.LPWStr)] out string n);
    [PreserveSig] int SetTitle([MarshalAs(UnmanagedType.LPWStr)] string t);
    [PreserveSig] int SetOkButtonLabel([MarshalAs(UnmanagedType.LPWStr)] string t);
    [PreserveSig] int SetFileNameLabel([MarshalAs(UnmanagedType.LPWStr)] string t);
    [PreserveSig] int GetResult(out IntPtr p);
    [PreserveSig] int AddPlace(IntPtr p, uint f);
    [PreserveSig] int SetDefaultExtension([MarshalAs(UnmanagedType.LPWStr)] string e);
    [PreserveSig] int Close(int hr);
    [PreserveSig] int SetClientGuid(ref Guid g);
    [PreserveSig] int ClearClientData();
    [PreserveSig] int SetFilter(IntPtr p);
}
[ComImport, Guid("43826D1E-E718-42EE-BC55-A1E261C37BFE"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface IShellItem {
    [PreserveSig] int BindToHandler(IntPtr p, ref Guid b, ref Guid r, out IntPtr v);
    [PreserveSig] int GetParent(out IntPtr p);
    [PreserveSig] int GetDisplayName(uint s, [MarshalAs(UnmanagedType.LPWStr)] out string n);
    [PreserveSig] int GetAttributes(uint m, out uint a);
    [PreserveSig] int Compare(IntPtr p, uint h, out int o);
}
public static class FolderPicker {
    [DllImport("user32.dll")] static extern bool SetProcessDpiAwarenessContext(IntPtr v);
    [DllImport("shcore.dll")] static extern int SetProcessDpiAwareness(int v);
    static void _setDpi() {
        // DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4 (Windows 10 1703+)
        if (!SetProcessDpiAwarenessContext(new IntPtr(-4)))
            SetProcessDpiAwareness(2); // PROCESS_PER_MONITOR_DPI_AWARE fallback
    }
    public static string Pick(string title) {
        _setDpi();
        var d = (IFileDialog)new FileOpenDialog();
        uint o; d.GetOptions(out o);
        d.SetOptions(o | 0x20u | 0x800u); // FOS_PICKFOLDERS | FOS_PATHMUSTEXIST
        d.SetTitle(title);
        if (d.Show(IntPtr.Zero) != 0) return null;
        IntPtr p; d.GetResult(out p);
        var si = (IShellItem)Marshal.GetTypedObjectForIUnknown(p, typeof(IShellItem));
        string name; si.GetDisplayName(0x80058000u, out name);
        return name;
    }
}
"@ -Language CSharp
[FolderPicker]::Pick('Select work directory')
"""


def _run_ps_folder_picker(is_wsl: bool) -> str | None:
    """Write the modern folder picker script to a temp PS1 file and run it."""
    import os
    import tempfile
    with tempfile.NamedTemporaryFile(mode="w", suffix=".ps1", delete=False, encoding="utf-8") as f:
        f.write(_PS_MODERN_FOLDER_SCRIPT)
        tmp_path = f.name
    try:
        if is_wsl:
            wp = subprocess.run(["wslpath", "-w", tmp_path], capture_output=True, text=True)
            win_tmp = wp.stdout.strip()
            if wp.returncode != 0 or not win_tmp:
                return None
            res = subprocess.run(
                ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                 "-InputFormat", "None", "-File", win_tmp],
                capture_output=True, encoding="utf-8",
            )
        else:
            res = subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                 "-InputFormat", "None", "-File", tmp_path],
                capture_output=True, encoding="utf-8",
            )
        return res.stdout.strip() or None
    finally:
        os.unlink(tmp_path)


@router.get("/api/pick-folder")
async def pick_folder():
    """Open a native folder-picker dialog and return the selected path."""
    is_wsl = Path("/proc/version").exists() and \
             "microsoft" in Path("/proc/version").read_text().lower()

    if is_wsl:
        win_path = _run_ps_folder_picker(is_wsl=True)
        if not win_path:
            return {"path": None}
        conv = subprocess.run(["wslpath", "-u", win_path], capture_output=True, text=True)
        return {"path": conv.stdout.strip() or None}

    if sys.platform == "win32":
        return {"path": _run_ps_folder_picker(is_wsl=False)}

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


def _scan_folder_jobs(scan_dir: Path) -> list[dict]:
    """Return folder dicts for direct children of scan_dir.

    Only reads insarhub_config.json per folder (single file I/O) to derive
    workflow tags. All glob/stat/has_children checks removed for SSH speed.
    """
    items = []
    try:
        children = sorted(scan_dir.iterdir())
    except OSError:
        return items
    for child in children:
        if child.name.startswith('.') or not child.is_dir():
            continue
        cfg = read_insarhub_config(child)
        proc_type = cfg.get("processor", {}).get("type", "")
        az_type   = cfg.get("analyzer",  {}).get("type", "")
        tags: list[str] = []
        if proc_type.startswith("Hyp3"):
            tags.append("HyP3")
        if proc_type.startswith("ISCE"):
            tags.append("ISCE")
        if az_type:
            tags.append("MintPy")
        workflow = {
            "downloader": cfg.get("downloader", {}).get("type", ""),
            "processor":  cfg.get("processor",  {}).get("type", ""),
            "analyzer":   cfg.get("analyzer",   {}).get("type", ""),
        }
        items.append({
            "type": "folder",
            "name": child.name,
            "path": str(child),
            "tags": tags,
            "workflow": workflow,
            "has_children": True,
        })
    return items


@router.get("/api/job-folders")
async def get_job_folders():
    workdir = Path(state._settings["workdir"])
    if not workdir.exists():
        return {"jobs": []}
    return {"jobs": _scan_folder_jobs(workdir)}


@router.get("/api/browse-subfolders")
async def browse_subfolders(path: str):
    """Return job-folder entries for direct subfolders of any path."""
    folder = Path(path).expanduser().resolve()
    if not folder.exists() or not folder.is_dir():
        raise HTTPException(status_code=404, detail="Path not found")
    raw_wd = state._settings.get("workdir", "")
    if not raw_wd:
        raise HTTPException(status_code=400, detail="workdir not configured")
    workdir = Path(raw_wd).expanduser().resolve()
    # Prevent escaping above workdir
    try:
        folder.relative_to(workdir)
    except ValueError:
        raise HTTPException(status_code=403, detail="Path is outside workdir")
    return {"jobs": _scan_folder_jobs(folder), "path": str(folder), "workdir": str(workdir)}


@router.get("/api/folder-config")
async def get_folder_config(path: str):
    """Return insarhub_config.json for a folder, merged with in-memory defaults."""
    import dataclasses
    from insarhub.core.registry import Processor
    folder = Path(path).expanduser().resolve()
    cfg = read_insarhub_config(folder)
    # Fill missing sections with in-memory defaults so GUI always has full config
    if "downloader" not in cfg:
        dl = state._settings["downloader"]
        cfg["downloader"] = {"type": dl, "config": state._settings["downloader_config"]}
    if "processor" not in cfg:
        pr = state._settings["processor"]
        proc_cls = Processor._registry.get(pr)
        cfg_cls = getattr(proc_cls, "default_config", None) if proc_cls else None
        if cfg_cls and dataclasses.is_dataclass(cfg_cls):
            try:
                resolved = cfg_cls(workdir=folder)
                proc_config = {
                    k: str(v) if isinstance(v, Path) else v
                    for k, v in dataclasses.asdict(resolved).items()
                    if k not in ("workdir", "name")
                }
            except Exception:
                proc_config = state._settings["processor_config"]
        else:
            proc_config = state._settings["processor_config"]
        cfg["processor"] = {"type": pr, "config": proc_config}
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


@router.get("/api/processor-defaults")
async def get_processor_defaults(processor: str, workdir: str):
    """Return resolved config defaults for a given processor type and workdir."""
    import dataclasses
    from insarhub.core.registry import Processor
    folder = Path(workdir).expanduser().resolve()
    proc_cls = Processor._registry.get(processor)
    cfg_cls = getattr(proc_cls, "default_config", None) if proc_cls else None
    if cfg_cls is None or not dataclasses.is_dataclass(cfg_cls):
        raise HTTPException(status_code=404, detail=f"Unknown processor: {processor}")
    try:
        resolved = cfg_cls(workdir=folder)
        return {
            k: str(v) if isinstance(v, Path) else v
            for k, v in dataclasses.asdict(resolved).items()
            if k not in ("workdir", "name") and v is not None
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/api/job-folder")
async def delete_job_folder(path: str):
    folder  = Path(path).expanduser().resolve()
    workdir = Path(state._settings["workdir"]).expanduser().resolve()
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
