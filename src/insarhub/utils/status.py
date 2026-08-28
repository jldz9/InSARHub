"""Shared run-files status model for the local/HPC processors.

This is backend-neutral infrastructure -- ISCE2, ISCE3, GMTSAR and the dolphin
analyzer all track per-step/per-stage status the same way -- so it lives in
``utils`` rather than under any one processor (it used to live in
``processor/isce2_base.py``, which several other backends imported from,
coupling them to ISCE2). Status is a ``<step>.status`` file per step plus
per-command ``cmd_*.done/.fail`` markers in ``<step>_logs/``.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

_PENDING   = "PENDING"
_RUNNING   = "RUNNING"
_SUCCEEDED = "SUCCEEDED"
_FAILED    = "FAILED"

_STEP_NUM_RE = re.compile(r"^run_(\d+)_")


def _resolve_step_names(
    requested: list[str], valid_names: list[str],
) -> tuple[set[str], list[str]]:
    """Resolve short step references to full run-script/job names.

    Accepts the full name ("run_03_average_baseline"), just the number
    ("03" or "3"), or a "run_03" prefix. Returns (resolved_names, unknown_inputs).
    """
    num_to_name: dict[str, str] = {}
    for name in valid_names:
        m = _STEP_NUM_RE.match(name)
        if m:
            digits = m.group(1)
            num_to_name[digits] = name                     # zero-padded, e.g. "03"
            num_to_name[digits.lstrip("0") or "0"] = name   # unpadded, e.g. "3"

    resolved: set[str] = set()
    unknown: list[str] = []
    for raw in requested:
        token = raw.strip()
        if token in valid_names:
            resolved.add(token)
            continue
        digits = token
        if digits.lower().startswith("run_"):
            digits = digits[4:]
        elif digits.lower().startswith("run"):
            digits = digits[3:]
        digits = digits.split("_")[0]
        key = digits.lstrip("0") or "0"
        if digits in num_to_name:
            resolved.add(num_to_name[digits])
        elif key in num_to_name:
            resolved.add(num_to_name[key])
        else:
            unknown.append(raw)
    return resolved, unknown


def _clear_step_markers(run_files_dir: Path, step: str) -> int:
    """Remove stale per-command cmd_*/task_* .done/.fail markers for a step
    being force-rerun via --step.

    Resetting a step's own .status file isn't enough to make it actually
    resubmit: the manager sbatch scripts' sliding-window logic checks these
    per-command marker files directly (independent of the step-level status)
    to decide which commands are "already done" and skip. Covers all three
    layouts a step's commands can live in:
      {step}_logs/   single-step-manager (LOG_DIR in the sbatch script)
      {step}_sbatch/ merged single-command-per-step group
      {step}_group/  merged multi-command group (only present when this step
                     is first in its group — see _group_steps())
    Returns the number of marker files removed.
    """
    removed = 0
    for suffix in ("_logs", "_sbatch", "_group"):
        d = run_files_dir / f"{step}{suffix}"
        if not d.exists():
            continue
        for pattern in ("cmd_*.done", "cmd_*.fail", "task_*.done", "task_*.fail"):
            for f in d.glob(pattern):
                f.unlink()
                removed += 1
    return removed


def _status_file(run_files_dir: Path, step_name: str) -> Path:
    return run_files_dir / f"{step_name}.status"


def _read_status(run_files_dir: Path, step_name: str) -> tuple[str, str]:
    sf = _status_file(run_files_dir, step_name)
    if not sf.exists():
        return _PENDING, ""
    raw = sf.read_text().strip()
    if raw.startswith(_RUNNING):
        parts = raw.split(":", 1)
        if len(parts) == 2:
            try:
                os.kill(int(parts[1]), 0)
                return _RUNNING, parts[1]
            except (OSError, ValueError):
                return _FAILED, "process died unexpectedly"
        return _RUNNING, ""
    if raw.startswith(_FAILED):
        return _FAILED, raw[len(_FAILED):].lstrip(":").strip()
    if raw == _SUCCEEDED:
        return _SUCCEEDED, ""
    return _PENDING, ""


def _write_status(run_files_dir: Path, step_name: str, status: str, detail: str = "") -> None:
    run_files_dir.mkdir(parents=True, exist_ok=True)
    sf = _status_file(run_files_dir, step_name)
    sf.write_text(f"{status}:{detail}" if detail else status)
