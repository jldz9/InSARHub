"""Shared helpers for reading/writing insarhub_config.json.

Kept outside insarhub.app so CLI and other non-GUI code can import
without pulling in FastAPI/uvicorn dependencies.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

_CONFIG_FILE         = "insarhub_config.json"
_LEGACY_WORKFLOW_FILE = "insarhub_workflow.json"


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


def write_insarhub_config(folder: Path, config: dict) -> None:
    """Write insarhub_config.json to folder, merging with any existing content."""
    path = Path(folder) / _CONFIG_FILE
    try:
        existing: dict = json.loads(path.read_text()) if path.exists() else {}
    except Exception:
        existing = {}
    existing.update(config)
    existing["updated_at"] = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    path.write_text(json.dumps(existing, indent=2, default=str))
