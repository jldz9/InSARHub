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

    _migrate_dolphin_analyzer(data)

    return data


# ── dolphin PL: pre-split configs need the upstream to disambiguate ──────────
# Until the dolphin analyzer was split per sensor, ONE analyzer
# ("ISCE3_Dolphin_PL") served both ISCE3_Burst and ISCE3_NISAR, carrying one
# config whose wavelength was the Sentinel-1 C-band constant. A NISAR workdir
# configured back then therefore has BOTH a sensor-ambiguous analyzer type AND a
# persisted C-band wavelength -- L-band is ~0.24 m, so that value scales every
# displacement by roughly 4.3x and nothing downstream flags it.
#
# The registry alias for the old name resolves to the Sentinel-1 analyzer (it
# has to: for an ISCE3_Burst workdir that IS the correct target). The alias
# alone cannot tell the two apart, because the sensor is not in the name -- but
# the saved processor sitting next to it in the same file says exactly which
# upstream this workdir is, so the migration happens here where both are in hand.

#: Pre-split analyzer names that do not say which sensor they meant.
_AMBIGUOUS_DOLPHIN = frozenset({
    "ISCE3_Dolphin_PL", "ISCE3_Dolphin_TS", "Dolphin_TS", "Dolphin_SBAS",
})

#: The Sentinel-1 C-band wavelength the pre-split config persisted as its
#: default. Only THIS exact value is dropped when retargeting to NISAR: any
#: other number was typed by the user and is left alone.
_S1_C_BAND = 0.055465764662349676

#: Old config-class labels -> current ones (the ``name`` field is a label only,
#: but a stale one is actively misleading once the class beside it changed).
_DOLPHIN_CONFIG_NAMES = {
    "ISCE3_Dolphin_PL_Config":       "ISCE3_Dolphin_S1_PL_Config",
    "ISCE3_Dolphin_PL_S1_Config":    "ISCE3_Dolphin_S1_PL_Config",
    "ISCE3_Dolphin_PL_NISAR_Config": "ISCE3_Dolphin_NISAR_PL_Config",
}


def _migrate_dolphin_analyzer(data: dict) -> None:
    """Retarget a pre-split dolphin analyzer section in place, using the saved
    processor to decide which sensor it meant.

    Idempotent, and a no-op for every non-dolphin config. Nothing is written
    back to disk -- like ``_RENAMES`` above, this runs on each read so a workdir
    is migrated wherever it is opened, without rewriting files the user may be
    sharing with an older InSARHub.
    """
    az = data.get("analyzer")
    if not isinstance(az, dict):
        return
    cfg = az.get("config")
    cfg = cfg if isinstance(cfg, dict) else None

    if az.get("type") in _AMBIGUOUS_DOLPHIN:
        proc = data.get("processor")
        upstream = proc.get("type") if isinstance(proc, dict) else None

        if upstream == "ISCE3_NISAR":
            az["type"] = "ISCE3_Dolphin_NISAR_PL"
            if cfg is not None:
                # The persisted C-band default would survive as an explicit
                # override (_apply_config_from_dict sets every matching field),
                # pinning the wrong wavelength on an L-band stack. Dropping the
                # key restores "derive it from the GSLC metadata".
                if cfg.get("wavelength") == _S1_C_BAND:
                    cfg.pop("wavelength", None)
        elif upstream == "ISCE3_Burst":
            # Same class the alias would have reached; naming it explicitly
            # stops the file from staying ambiguous forever.
            az["type"] = "ISCE3_Dolphin_S1_PL"
        # Upstream unknown/absent: leave it to the registry alias, which keeps
        # the historical Sentinel-1 behaviour rather than guessing.

    if cfg is not None and cfg.get("name") in _DOLPHIN_CONFIG_NAMES:
        cfg["name"] = _DOLPHIN_CONFIG_NAMES[cfg["name"]]

    # A NISAR analyzer must never carry the S1 config label, however it got here.
    if cfg is not None and az.get("type") == "ISCE3_Dolphin_NISAR_PL" \
            and cfg.get("name") == "ISCE3_Dolphin_S1_PL_Config":
        cfg["name"] = "ISCE3_Dolphin_NISAR_PL_Config"


def resolve_legacy_analyzer_name(name: str, folder) -> str:
    """Map a pre-split dolphin analyzer name onto the per-sensor one for *folder*.

    The registry resolves ``ISCE3_Dolphin_PL`` through an alias, but an alias is
    a fixed mapping and the old name does not say which sensor it meant -- so it
    lands on the Sentinel-1 analyzer even for a NISAR workdir, which is how a
    C-band wavelength ends up on an L-band stack.

    :func:`read_insarhub_config` already fixes that for a SAVED analyzer section,
    but the CLI never reads one: ``insarhub analyzer -N <name>`` builds the
    config from the class default plus command-line flags. So an old command line
    re-run verbatim needs the same disambiguation, which is what this gives it.

    Returns *name* unchanged for anything that is not an ambiguous dolphin name,
    and whenever the workdir does not say which processor produced it.
    """
    if name not in _AMBIGUOUS_DOLPHIN:
        return name
    try:
        data = read_insarhub_config(Path(folder))
    except Exception:                                            # noqa: BLE001
        return name          # unreadable/malformed: leave it to the alias
    proc = data.get("processor")
    upstream = proc.get("type") if isinstance(proc, dict) else None
    if upstream == "ISCE3_NISAR":
        return "ISCE3_Dolphin_NISAR_PL"
    if upstream == "ISCE3_Burst":
        return "ISCE3_Dolphin_S1_PL"
    return name


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
