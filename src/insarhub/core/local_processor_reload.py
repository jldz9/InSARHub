# -*- coding: utf-8 -*-
"""
Shared job-file discovery / saved-processor-reload logic for local
processors (ISCE_S1, GMTSAR_S1, ...), used by both the CLI (cli/main.py)
and the web GUI (app/routes/processor.py) so job-file naming conventions,
saved-config field filtering, and per-processor method-signature
differences are handled identically in both places instead of drifting
apart (which is exactly how GMTSAR_S1 ended up broken in the GUI while
already fixed on the CLI).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

_GROUP_KEY_RE = re.compile(r"p(\d+)_f(\d+)")


def _parse_group_key(key: str) -> tuple[int, int] | None:
    """Parse 'p100_f466' → (100, 466); return None if key doesn't match pattern."""
    m = _GROUP_KEY_RE.fullmatch(key)
    return (int(m.group(1)), int(m.group(2))) if m else None


def _read_config_json(cfg_path: Path) -> dict:
    """Read a JSON config file and return {field: value}, empty dict if missing or unreadable."""
    if not cfg_path.exists():
        return {}
    try:
        return json.loads(cfg_path.read_text())
    except Exception:
        return {}


# Identity/reference fields to strip when reading a saved role-config section —
# distinct from _SAVED_CFG_SKIP (per-invocation flags never persisted at all).
_ROLE_CONFIG_STRIP_FIELDS = {"workdir", "name", "saved_job_path"}

# Extra fields stripped when loading from a saved config file (runtime-only
# flags that must not carry over from a previous run's saved state). Mirrors
# cli/main.py's _SUBMIT_SKIP_FIELDS | {"hpc_mode", "dry_run"} | _RUNTIME_ONLY_FIELDS
# without importing from cli/main.py (this module is imported by the CLI, not
# the other way around).
_SAVED_CFG_SKIP = {
    "name", "workdir", "pairs", "saved_job_path",
    "earthdata_credentials_pool", "name_prefix", "sbatch_options_per_step",
    "hpc_mode", "dry_run", "container",
}


def _read_proc_config_from_folder(folder: Path) -> dict:
    """Read processor config from insarhub_config.json (or legacy formats), else fallback processor_config.json."""
    from insarhub.utils.config_io import read_insarhub_config
    data = read_insarhub_config(folder)
    cfg = data.get("processor", {}).get("config", {})
    if cfg:
        return {k: v for k, v in cfg.items() if k not in _ROLE_CONFIG_STRIP_FIELDS}
    raw = _read_config_json(folder / "processor_config.json")
    return {k: v for k, v in raw.items() if k not in _ROLE_CONFIG_STRIP_FIELDS}


def _find_subfolder_config(workdir: Path, filename: str) -> Path | None:
    """Return the config file path from the first p*_f* subfolder that has it."""
    if not workdir.is_dir():
        return None
    for subdir in sorted(workdir.iterdir()):
        if subdir.is_dir() and _parse_group_key(subdir.name):
            cfg = subdir / filename
            if cfg.exists():
                return cfg
    return None


def _find_jobs_file(workdir: Path, pattern: str, subdir: str | None = None) -> Path | None:
    """Return the first matching jobs JSON file.

    Search order:
      1. workdir/ directly
      2. workdir/<subdir>/      ← e.g. "isce" for ISCE_S1, "gmtsar" for
                                   GMTSAR_S1 (processor_cls.JOBS_SUBDIR)
      3. workdir/<p*_f*>/
      4. workdir/<p*_f*>/<subdir>/

    `subdir` generalizes what used to be a hardcoded ISCEPaths(...).isce_dir
    lookup -- found via a real gap: GMTSAR_S1's gmtsar_jobs.json lives under
    gmtsar/, which the old hardcoded isce/ lookup could never find.
    """
    for p in sorted(workdir.glob(pattern)):
        return p
    if subdir:
        sub = workdir / subdir
        if sub.is_dir():
            for p in sorted(sub.glob(pattern)):
                return p
    for d in sorted(workdir.iterdir()):
        if d.is_dir() and _parse_group_key(d.name):
            for p in sorted(d.glob(pattern)):
                return p
            if subdir:
                nested = d / subdir
                if nested.is_dir():
                    for p in sorted(nested.glob(pattern)):
                        return p
    return None


def _jobs_glob(processor_name: str) -> tuple[str, str | None]:
    """(pattern, subdir) for this processor's saved-job file, from its own
    JOBS_FILE/JOBS_SUBDIR class attributes -- see LocalProcessor in core/base.py."""
    from insarhub import Processor
    processor_cls = Processor._registry[processor_name]
    jobs_file = getattr(processor_cls, "JOBS_FILE", None) or "isce_jobs.json"
    pattern = f"{Path(jobs_file).stem}*.json"
    return pattern, getattr(processor_cls, "JOBS_SUBDIR", None)


def _load_local_processor(processor_name: str, workdir: Path, jobs_path: Path,
                           hpc_mode: bool = False, dry_run: bool = False,
                           container: str | None = None):
    """Instantiate a local processor from a saved jobs file without needing pairs."""
    import dataclasses
    from insarhub import Processor
    processor_cls = Processor._registry[processor_name]
    cfg_cls = getattr(processor_cls, "default_config", None)
    if cfg_cls is not None:
        saved_cfg = _read_proc_config_from_folder(workdir)
        overrides = {k: v for k, v in saved_cfg.items() if k not in _SAVED_CFG_SKIP}
        overrides["workdir"] = str(workdir)
        overrides["saved_job_path"] = str(jobs_path)
        overrides["hpc_mode"] = hpc_mode or bool(saved_cfg.get("hpc_mode", False))
        overrides["dry_run"] = dry_run
        overrides["container"] = container
        valid = {f.name for f in dataclasses.fields(cfg_cls)}
        cfg = cfg_cls(**{k: v for k, v in overrides.items() if k in valid})
    else:
        cfg = None
    saved = json.loads(jobs_path.read_text())
    # ISCE wraps its saved jobs as {"jobs": {...}, "workdir": ...}; other
    # local processors (e.g. GMTSAR_S1.save()) write the jobs dict directly
    # at the top level, no wrapper. Handle both shapes.
    jobs = (saved["jobs"] if "jobs" in saved else saved).values()
    # ISCE's saved jobs are keyed by step name (no real "pair" concept);
    # other local processors (e.g. GMTSAR_S1) store the real pair tuple
    # under "pair" -- prefer that when present instead of assuming ISCE's
    # step-based shape (found via a real gap: this used to always rebuild
    # (j["step"], j["step"]), which is meaningless for GMTSAR_S1's 4-tuple
    # pairs and would crash its pairs-arity validation).
    pairs = [tuple(j["pair"]) if "pair" in j else (j["step"], j["step"]) for j in jobs]
    return processor_cls(pairs=pairs or [("_", "_")], config=cfg)


def _call_if_supported(bound_method, **kwargs):
    """Call bound_method with only the kwargs its real signature accepts.

    Local processors don't all share the same refresh()/watch() shape --
    e.g. ISCE_Base.refresh(ls=...) shows per-command detail, a concept
    GMTSAR_S1 has no equivalent of. Rather than force every processor to
    accept every other processor's kwargs (or crash on TypeError), only
    pass what's actually supported; the rest are silently no-ops for
    processors that don't have that concept.
    """
    import inspect
    sig = inspect.signature(bound_method)
    supported = {k: v for k, v in kwargs.items() if k in sig.parameters}
    return bound_method(**supported)
