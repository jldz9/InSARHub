"""Shared helpers for reading/writing insarhub_config.json.

Kept outside insarhub.app so CLI and other non-GUI code can import
without pulling in FastAPI/uvicorn dependencies.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

_CONFIG_FILE         = "insarhub_config.json"
_LEGACY_WORKFLOW_FILE = "insarhub_workflow.json"

# ── RULE: never persist an environment-resolved value ────────────────────────
# These config fields are resolved from the RUNTIME environment (host vs
# container -- tool install paths discovered at construction time). They must be
# stored only as their "auto" sentinel, so each environment re-resolves them at
# load; persisting a container-resolved path into the shared workdir breaks a
# later host-side read (and vice-versa). This is enforced centrally in
# write_insarhub_config() so it holds for every writer (CLI, app, processor).
# Any new field whose value depends on where the process runs belongs here.
ENV_RESOLVED_FIELDS = frozenset({"gmtsar_root", "gmtsar_env_bin"})

# UI-only constants that must NOT be persisted to insarhub_config.json. These
# are fixed per-processor/analyzer class attributes (never a user choice), so
# persisting them just clutters the file and pins a stale value if the default
# image ever changes -- the frontend reads them from the schema endpoint
# (/api/*-schema -> _dataclass_defaults) instead. `container` (the user's actual
# run choice) IS persisted; only its sibling default constant is dropped.
UI_ONLY_FIELDS = frozenset({"container_default"})


def _sanitize_env_resolved(merged: dict) -> None:
    """Reset any ENV_RESOLVED_FIELDS under <section>.config to "auto", and drop
    UI_ONLY_FIELDS entirely, in place."""
    for section in merged.values():
        if isinstance(section, dict):
            cfg = section.get("config")
            if isinstance(cfg, dict):
                for k in ENV_RESOLVED_FIELDS:
                    if k in cfg and cfg[k] not in (None, "", "auto"):
                        cfg[k] = "auto"
                for k in UI_ONLY_FIELDS:
                    cfg.pop(k, None)


def read_insarhub_config(folder: Path) -> dict:
    """Read insarhub_config.json from folder, with fallback to legacy insarhub_workflow.json.

    A MISSING file returns {} -- that is the normal "nothing configured yet"
    case. A file that EXISTS but does not parse raises, because silently
    returning {} for it is actively dangerous: every caller then proceeds with
    an empty config as though the user had configured nothing. The downloader
    is the worst case -- with no relativeOrbit/frame/intersectsWith/date range
    it queries ASF for the entire Sentinel-1 archive, which does not fail, it
    just never returns. That is exactly how a single missing '{' in one
    workdir's insarhub_config.json turned into a downloader that "hung"
    forever with no error message anywhere.
    """
    path        = Path(folder) / _CONFIG_FILE
    legacy_path = Path(folder) / _LEGACY_WORKFLOW_FILE
    src = path if path.exists() else (legacy_path if legacy_path.exists() else None)
    if src is None:
        return {}
    try:
        data = json.loads(src.read_text())
    except json.JSONDecodeError as e:
        raise ValueError(
            f"{src} exists but is not valid JSON: {e}. "
            f"Fix or remove the file -- refusing to continue with an empty "
            f"config, which would run every step unconstrained."
        ) from e
    except OSError as e:
        raise ValueError(f"Could not read {src}: {e}") from e
    if not isinstance(data, dict):
        raise ValueError(
            f"{src} must contain a JSON object, got {type(data).__name__}.")

    for role in ("downloader", "processor", "analyzer"):
        val = data.get(role)
        if isinstance(val, str):
            data[role] = {"type": val}

    _RENAMES = {"Hyp3_InSAR": "Hyp3_S1", "ISCE_InSAR": "ISCE2_S1", "ISCE_S1": "ISCE2_S1"}
    for role in ("downloader", "processor", "analyzer"):
        section = data.get(role)
        if isinstance(section, dict) and section.get("type") in _RENAMES:
            section["type"] = _RENAMES[section["type"]]

    return data


def _atomic_write_json(path: Path, data: dict) -> None:
    """Write ``data`` to ``path`` atomically (temp file + ``os.replace``).

    ``write_text`` truncates the target before writing, so a concurrent reader
    (the GUI backend polling ``/api/folder-details`` while the container
    executor or a background job writes) sees a zero-length file and raises
    ``JSONDecodeError: Expecting value: line 1 column 1``. Writing to a temp
    file in the same directory and renaming over the target means a reader
    always sees either the old or the new complete document, never an empty one.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_insarhub_config(folder: Path, config: dict) -> None:
    """Write insarhub_config.json to folder, merging with any existing content."""
    path = Path(folder) / _CONFIG_FILE
    try:
        existing: dict = json.loads(path.read_text()) if path.exists() else {}
    except Exception:
        existing = {}
    existing.update(config)
    _sanitize_env_resolved(existing)   # never persist env-resolved paths (see rule above)
    existing["updated_at"] = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    _atomic_write_json(path, existing)
