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
    """Read insarhub_config.json from folder, with fallback to legacy insarhub_workflow.json."""
    path        = Path(folder) / _CONFIG_FILE
    legacy_path = Path(folder) / _LEGACY_WORKFLOW_FILE
    try:
        if path.exists():
            data = json.loads(path.read_text())
        elif legacy_path.exists():
            data = json.loads(legacy_path.read_text())
        else:
            return {}
    except Exception:
        return {}

    for role in ("downloader", "processor", "analyzer"):
        val = data.get(role)
        if isinstance(val, str):
            data[role] = {"type": val}

    _RENAMES = {"Hyp3_InSAR": "Hyp3_S1", "ISCE_InSAR": "ISCE_S1"}
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
