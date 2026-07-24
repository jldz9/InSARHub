# -*- coding: utf-8 -*-
"""
Shared in-memory state and metadata for the InSARHub FastAPI backend.

All route modules import from here to access the single shared state.
Mutable dicts (_jobs, _settings, etc.) are imported by reference — mutations
in any route module are immediately visible everywhere.
For reassignable values (_auth_cache), route modules must access via the module:
    import insarhub.app.state as state
    state._auth_cache = ...
"""

import dataclasses
import logging
import re
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

# ── Trigger auto-registration of all components ──────────────────────────────
import insarhub.downloader.s1_slc       # noqa: F401
import insarhub.processor.hyp3_s1    # noqa: F401
import insarhub.processor.isce_s1    # noqa: F401
import insarhub.analyzer.hyp3_sbas      # noqa: F401
import insarhub.analyzer.mintpy_base    # noqa: F401
import insarhub.analyzer.isce_sbas      # noqa: F401

from insarhub.core.registry import Downloader, Processor, Analyzer


# ---------------------------------------------------------------------------
# Metadata helpers
# ---------------------------------------------------------------------------

def _dataclass_defaults(cls) -> dict[str, Any]:
    """Return {field_name: default_value} for a dataclass, skipping fields with no default.

    String defaults of "auto" or "" are omitted so the GUI shows an empty field
    (the resolved actual path will be shown once the config is saved).
    """
    out: dict[str, Any] = {}
    for f in dataclasses.fields(cls):
        if f.default is not dataclasses.MISSING:
            val = f.default
            if isinstance(val, str) and val.strip().lower() in ("auto", ""):
                continue
            out[f.name] = val
        elif f.default_factory is not dataclasses.MISSING:   # type: ignore[misc]
            try:
                out[f.name] = f.default_factory()            # type: ignore[misc]
            except Exception:
                pass
    return out


def _build_ui_meta(cfg_cls) -> tuple[list[dict], list[dict]]:
    """Extract groups + UI field list from a config dataclass using its ClassVar metadata."""
    groups: list[dict] = list(getattr(cfg_cls, "_ui_groups", []))
    ui_field_meta: dict = getattr(cfg_cls, "_ui_fields", {})
    live = _dataclass_defaults(cfg_cls)
    fields: list[dict] = []
    for key, meta in ui_field_meta.items():
        entry: dict = {"key": key, "label": key, **meta}
        if key in live:
            entry["default"] = live[key]
        fields.append(entry)
    return groups, fields


def _build_registry_meta(registry) -> dict[str, Any]:
    """Dynamically build component metadata from every entry in a registry."""
    result: dict[str, Any] = {}
    for name in registry.available():
        cls = registry._registry[name]
        cfg_cls = getattr(cls, "default_config", None)
        if cfg_cls is None or not dataclasses.is_dataclass(cfg_cls):
            continue
        if not getattr(cfg_cls, "_ui_groups", None):
            continue
        groups, fields = _build_ui_meta(cfg_cls)
        import inspect
        # True for processors that take pairs as their own constructor arg
        # (ISCE_S1, GMTSAR_S1) rather than via a config field (Hyp3_S1) --
        # same check _run_folder_process does inline to decide dispatch;
        # exposed here so the frontend can tell local vs cloud processors
        # apart without hardcoding processor names (JobQueueDrawer.tsx).
        is_local = "pairs" in inspect.signature(cls.__init__).parameters
        result[name] = {
            "label":                 name,
            "description":           getattr(cls, "description", ""),
            "compatible_downloader": getattr(cls, "compatible_downloader", None),
            "compatible_processor":  getattr(cls, "compatible_processor", None),
            "is_local":              is_local,
            "groups":                groups,
            "fields":                fields,
        }
    return result


def _default_config_values(component_name: str, meta: dict[str, Any]) -> dict[str, Any]:
    """Return {key: default} for every UI field of a registered component."""
    entry = meta.get(component_name, {})
    return {f["key"]: f["default"] for f in entry.get("fields", []) if "default" in f}


def _safe_config_values(name: str, meta: dict[str, Any]) -> dict[str, Any]:
    """Like _default_config_values but excludes TopBar-owned fields (AOI, dates)."""
    return {k: v for k, v in _default_config_values(name, meta).items() if k not in _TOPBAR_FIELDS}


# ---------------------------------------------------------------------------
# Component metadata (built once at import time)
# ---------------------------------------------------------------------------

_DOWNLOADERS_META: dict[str, Any] = _build_registry_meta(Downloader)
_PROCESSORS_META:  dict[str, Any] = _build_registry_meta(Processor)
_ANALYZERS_META:   dict[str, Any] = _build_registry_meta(Analyzer)

_DEFAULT_DOWNLOADER = next(iter(_DOWNLOADERS_META), "")
_DEFAULT_PROCESSOR  = next(iter(_PROCESSORS_META),  "")
_DEFAULT_ANALYZER   = next(iter(_ANALYZERS_META),   "")

_TOPBAR_FIELDS = {"intersectsWith", "start", "end"}

# ---------------------------------------------------------------------------
# Filesystem constants
# ---------------------------------------------------------------------------

_NETRC       = Path.home() / ".netrc"
_CDSAPIRC    = Path.home() / ".cdsapirc"
_CREDIT_POOL = Path.home() / ".credit_pool"

# ---------------------------------------------------------------------------
# Persistent settings
# ---------------------------------------------------------------------------

_settings: dict[str, Any] = {
    "workdir":              str(Path.cwd()),
    "max_download_workers": 3,
    "downloader":           _DEFAULT_DOWNLOADER,
    "downloader_config":    _safe_config_values(_DEFAULT_DOWNLOADER, _DOWNLOADERS_META),
    "processor":            _DEFAULT_PROCESSOR,
    "processor_config":     _default_config_values(_DEFAULT_PROCESSOR, _PROCESSORS_META),
    "analyzer":             _DEFAULT_ANALYZER,
    "analyzer_configs":     {name: _default_config_values(name, _ANALYZERS_META) for name in _ANALYZERS_META},
}

# ---------------------------------------------------------------------------
# In-memory job / session stores
# ---------------------------------------------------------------------------

_jobs:        dict[str, dict[str, Any]] = {}
_stop_events: dict[str, Any]            = {}   # job_id → threading.Event
_sessions:    dict[str, Any]            = {}   # session_id → downloader instance
_auth_cache:  dict[str, Any] | None     = None  # populated at startup

# ---------------------------------------------------------------------------
# Progress helper
# ---------------------------------------------------------------------------

def _make_progress(job_id: str):
    def callback(message: str, percent: int):
        _jobs[job_id]["progress"] = percent
        _jobs[job_id]["message"]  = message
    return callback


def _make_download_progress(job_id: str):
    """Progress callback for download commands: extracts a leading '[N/M]'-style
    count from the message and shows it as 'Downloading N/M' — shared by every
    route that runs a DownloadScenesCommand."""
    def callback(message: str, percent: int):
        count = message.split(']')[0].lstrip('[') if ']' in message else ''
        _jobs[job_id]["message"]  = f"Downloading {count}" if count else message
        _jobs[job_id]["progress"] = percent
    return callback


@contextmanager
def stop_event(job_id: str):
    """Register a threading.Event as job_id's stop signal, yield it, and remove
    it from _stop_events on exit regardless of outcome — the register/cleanup
    pair repeated at the top/bottom of every stoppable job runner."""
    ev = threading.Event()
    _stop_events[job_id] = ev
    try:
        yield ev
    finally:
        _stop_events.pop(job_id, None)


def launch_job(background_tasks, runner, *args, start_message: str = "Starting…", **kwargs) -> dict:
    """Create a new job, schedule runner(job_id, *args, **kwargs) as a background
    task, and return {"job_id": job_id} — the 3-line skeleton repeated at the
    top of nearly every job-launching route."""
    job_id, _ = _new_job(start_message)
    background_tasks.add_task(runner, job_id, *args, **kwargs)
    return {"job_id": job_id}


# ---------------------------------------------------------------------------
# Unified insarhub_config.json helpers — implementations live in utils/config_io.py
# ---------------------------------------------------------------------------
from insarhub.utils.config_io import read_insarhub_config, write_insarhub_config  # noqa: F401


def _cfg_dict(cfg, *, exclude: tuple = ("workdir",)) -> dict[str, Any]:
    """dataclasses.asdict(cfg) with the given fields excluded — shared by every
    route that persists a resolved config back to insarhub_config.json."""
    return {k: v for k, v in dataclasses.asdict(cfg).items() if k not in exclude}


def _read_processor_type(folder: Path) -> str | None:
    """Return the processor type recorded in folder's insarhub_config.json, if any."""
    try:
        cfg = read_insarhub_config(folder)
        return cfg.get("processor", {}).get("type")
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Shared config utility
# ---------------------------------------------------------------------------

def _apply_config_from_dict(cfg, raw: dict, *, skip_keys: set = frozenset()) -> None:
    """Apply raw dict values to a config dataclass in-place.

    Skips None values, non-existent fields, and keys in skip_keys.
    Logs a warning (instead of silently passing) when setattr fails.
    """
    valid_fields = {f.name for f in dataclasses.fields(cfg)}
    for key, val in raw.items():
        if key not in valid_fields or key in skip_keys or val is None:
            continue
        try:
            setattr(cfg, key, val)
        except Exception as e:
            _log.warning("Config field %r skipped (%s: %s)", key, type(e).__name__, e)


# ---------------------------------------------------------------------------
# Error message sanitizer
# ---------------------------------------------------------------------------

_HOME = str(Path.home())

def _sanitize_error(msg: str) -> str:
    """Remove home directory paths from error messages before sending to client."""
    msg = msg.replace(_HOME, "~")
    # Remove long absolute paths (keep only the last two parts)
    msg = re.sub(r'(/[\w./\-_]+ ){0,}(/[\w./\-_]+){3,}',
                 lambda m: str(Path(m.group(0).strip()).parts[-1]), msg)
    return msg


# ---------------------------------------------------------------------------
# Job helpers
# ---------------------------------------------------------------------------

_JOB_TTL = 3600  # seconds — completed/errored jobs are pruned after this


def _new_job(message: str = "Starting…") -> tuple[str, dict]:
    """Create a new job entry, prune expired jobs, and return (job_id, job_dict)."""
    import uuid
    _prune_jobs()
    job_id = str(uuid.uuid4())
    job: dict[str, Any] = {
        "status": "running", "progress": 0, "message": message,
        "data": None, "_created_at": time.time(),
    }
    _jobs[job_id] = job
    return job_id, job


def _finish_job(job_id: str, *, status: str, message: str, progress: int = 100, data: Any = None) -> None:
    _jobs[job_id] = {
        "status": status, "progress": progress,
        "message": message if status != "error" else _sanitize_error(message),
        "data": data, "_created_at": _jobs[job_id].get("_created_at", time.time()),
    }


def _prune_jobs() -> None:
    """Remove completed/errored jobs older than _JOB_TTL seconds."""
    cutoff = time.time() - _JOB_TTL
    to_delete = [
        jid for jid, j in _jobs.items()
        if j.get("status") in ("done", "error")
        and j.get("_created_at", 0) < cutoff
    ]
    for jid in to_delete:
        _jobs.pop(jid, None)
        _stop_events.pop(jid, None)
