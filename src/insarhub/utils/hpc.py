"""Shared HPC/sbatch resource configuration for the local/HPC processors.

Backend-neutral: ISCE2, ISCE3 and GMTSAR all read per-step sbatch resources from
one ``sbatch_options.json`` per workdir and share the manager-job conventions.
This used to live in ``processor/isce2_base.py`` (coupling every other backend to
ISCE2); it belongs in ``utils``. GMTSAR passes its own ``default_template`` (see
gmtsar_s1's ``_GMTSAR_SBATCH_DEFAULT_TEMPLATE``) into ``load_or_init_sbatch_options``.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from insarhub.utils.slurm_manager import sbatch_template_header

_SBATCH_DEFAULT_TEMPLATE: dict = {
    # Shared self-documenting header: every supported key, what it does and an
    # example, written into the file itself so editing resources never requires
    # looking anything up. Identical across ISCE2_S1, GMTSAR_S1 and ISCE3_Burst.
    **sbatch_template_header(),
    "_steps": {
        "01": "unpack_topo_reference",
        "02": "unpack_secondary_slc",
        "03": "average_baseline",
        "04": "extract_burst_overlaps",
        "05": "overlap_geo2rdr",
        "06": "overlap_resample",
        "07": "pairs_misreg",
        "08": "timeseries_misreg",
        "09": "fullBurst_geo2rdr",
        "10": "fullBurst_resample",
        "11": "extract_stack_valid_region",
        "12": "merge_reference_secondary_slc",
        "13": "generate_burst_igram",
        "14": "merge_burst_igram",
        "15": "filter_coherence",
        "16": "unwrap",
        "17": "SBAS",
        "manager": "job managers -- only 'partition' is used; cores/memory/walltime are fixed (1 idle core, partition max walltime)",
    },
    # Job managers are idle single-core babysitters that must outlive every
    # child they supervise, so they suit a long-walltime queue even when the
    # real work belongs elsewhere. Only "partition" is read here.
    "manager": {"partition": "all"},
    "default": {
        "time":          "02:00:00",
        "partition":     "all",
        "nodes":         1,
        "ntasks":        1,
        "cpus_per_task": 2,
        "mem":           "8G",
    },
    # 01/09/10 (topo + full-frame geo2rdr/resample) are full-frame,
    # multi-swath geometric work -- the single heaviest per-command cost in
    # the pipeline -- given more cores to match num_proc4topo/num_proc
    # (ISCE2_S1_Config), since neither side helps alone (found via a real
    # p100_f466 run: run_01 took 1h17m+ single-threaded on 3 swaths).
    "01": {"cpus_per_task": 6, "mem": "16G", "time": "03:00:00"},
    "02": {"cpus_per_task": 1, "mem": "4G"},
    "03": {"cpus_per_task": 1, "mem": "4G"},
    "04": {"cpus_per_task": 1, "mem": "4G"},
    "05": {"cpus_per_task": 2, "mem": "8G"},
    "06": {"cpus_per_task": 2, "mem": "8G"},
    "07": {"cpus_per_task": 2, "mem": "8G"},
    "08": {"cpus_per_task": 1, "mem": "4G"},
    "09": {"cpus_per_task": 4, "mem": "16G"},
    "10": {"cpus_per_task": 4, "mem": "16G"},
    "11": {"cpus_per_task": 1, "mem": "4G"},
    "12": {"cpus_per_task": 4, "mem": "16G"},
    "13": {"cpus_per_task": 4, "mem": "16G"},
    "14": {"cpus_per_task": 4, "mem": "16G"},
    "15": {"cpus_per_task": 4, "mem": "16G"},
    "16": {"time": "04:00:00", "cpus_per_task": 4, "mem": "32G"},
    "17": {"time": "24:00:00", "ntasks": 1, "cpus_per_task": 16, "mem": "128G"},
}


def _manager_partition(per_step: dict) -> str | None:
    """Partition for job-manager jobs, from sbatch_options.json's "manager".

    A manager's cores/memory are always fixed by slurm_manager (one idle
    core, 1G) since every manager does the identical bookkeeping job
    regardless of which step's children it supervises. Only ``partition``
    and ``time`` are honoured -- see _manager_time().
    """
    mgr = per_step.get("manager")
    if isinstance(mgr, dict):
        part = mgr.get("partition")
        if part:
            return str(part)
    return None


def _manager_time(per_step: dict) -> str | None:
    """Explicit walltime for job-manager jobs, from sbatch_options.json's
    "manager".time. None (the default) means "use the most the partition
    allows" -- see slurm_manager.manager_walltime().

    Worth setting explicitly on partitions with very generous limits: a
    manager only has to outlive the children it supervises, so inheriting a
    30-day cap just makes it look like a 30-day reservation to the scheduler
    and to anyone reading squeue, which can hurt its own queue priority.
    """
    mgr = per_step.get("manager")
    if isinstance(mgr, dict):
        t = mgr.get("time")
        if t:
            return str(t)
    return None


def _merge_sbatch_opts(per_step: dict, key: str) -> dict:
    """Merge the 'default' dict with the entry at ``key`` (key overrides default)."""
    default_cfg = per_step.get("default", {})
    if not isinstance(default_cfg, dict):
        default_cfg = {}
    step_cfg = per_step.get(key, {})
    if not isinstance(step_cfg, dict):
        step_cfg = {}
    return {**default_cfg, **step_cfg}


def load_or_init_sbatch_options(
    workdir: Path, step_key: str | None = None, step_label: str | None = None,
    default_template: dict = _SBATCH_DEFAULT_TEMPLATE,
) -> dict | None:
    """Load workdir/sbatch_options.json, creating or upgrading it as needed.

    One shared file for the whole workdir, but the content written for a
    *fresh* file is processor-specific: ISCE2_S1 callers use this function's
    default (ISCE's own "01".."17" template); GMTSAR_S1 passes its own
    ``default_template`` (see gmtsar_s1.py's ``_GMTSAR_SBATCH_DEFAULT_TEMPLATE``)
    so a workdir that only ever runs GMTSAR gets a GMTSAR-only file instead
    of ISCE's irrelevant numbered steps. If a workdir genuinely uses both
    processors (uncommon), whichever runs *second* just grows the existing
    file with its own missing keys via the per-key fallback below --
    nothing is ever removed or overwritten wholesale.

    Migrates the legacy ``srun_options.json`` filename if found.

    - Missing file: writes the full default_template and returns None —
      caller should stop and let the user review/edit before submitting.
    - ``step_key`` given, file exists but missing that key: adds the default
      entry for it (from ``default_template`` if present there, else empty),
      rewrites the file, prints a warning, and returns the loaded dict.
    - Otherwise: returns the loaded dict as-is.
    """
    workdir = Path(workdir).expanduser().resolve()
    sbatch_path = workdir / "sbatch_options.json"
    old_srun_path = workdir / "srun_options.json"
    if not sbatch_path.exists() and old_srun_path.exists():
        old_srun_path.rename(sbatch_path)
        print(f"[INFO] Migrated srun_options.json → sbatch_options.json")

    if not sbatch_path.exists():
        sbatch_path.write_text(json.dumps(default_template, indent=2))
        target = f'step "{step_key}" ({step_label})' if step_key else "every step"
        print(
            f"\n[INFO] No sbatch_options.json found — initialized default at:\n"
            f"       {sbatch_path}\n\n"
            f"  Edit the sbatch options for {target}, then rerun.\n"
        )
        return None

    try:
        per_step: dict = json.loads(sbatch_path.read_text())
    except Exception as e:
        print(f"[WARN] Could not read {sbatch_path}: {e}", file=sys.stderr)
        per_step = {}

    if step_key and step_key not in per_step:
        per_step[step_key] = dict(default_template.get(step_key, {}))
        per_step.setdefault("_steps", {})[step_key] = step_label
        sbatch_path.write_text(json.dumps(per_step, indent=2))
        print(
            f"[WARN] {sbatch_path} had no \"{step_key}\" ({step_label}) entry — "
            f"added default resource settings. Review before relying on them."
        )

    return per_step
