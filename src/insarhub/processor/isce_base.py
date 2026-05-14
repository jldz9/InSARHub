# -*- coding: utf-8 -*-
"""
ISCE_Base — shared infrastructure for ISCE2-backed local processors.

Handles ISCE2 discovery, run-file status tracking, sequential step execution
(with per-step parallelism), job persistence, and monitoring.  Concrete
subclasses (e.g. ISCE_S1) supply submit() and any sensor-specific helpers.
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
import signal
import subprocess
import sys
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import redirect_stdout
from pathlib import Path

from colorama import Fore, Style

from insarhub.core import LocalProcessor

logger = logging.getLogger(__name__)

_PENDING   = "PENDING"
_RUNNING   = "RUNNING"
_SUCCEEDED = "SUCCEEDED"
_FAILED    = "FAILED"

JOBS_FILE = "isce_jobs.json"

_SBATCH_DEFAULT_TEMPLATE: dict = {
    "_comment": (
        "Slurmjob_Config fields per step. 'default' applies to any unlisted step. "
        "Step-specific keys override the default. "
        "Supported keys: time, partition, nodes, ntasks, cpus_per_task, mem, "
        "account, qos, nodelist, gpus, mail_user, mail_type."
    ),
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
    },
    "default": {
        "time":          "02:00:00",
        "partition":     "all",
        "nodes":         1,
        "ntasks":        1,
        "cpus_per_task": 2,
        "mem":           "8G",
    },
    "01": {"cpus_per_task": 1, "mem": "4G"},
    "02": {"cpus_per_task": 1, "mem": "4G"},
    "03": {"cpus_per_task": 1, "mem": "4G"},
    "04": {"cpus_per_task": 1, "mem": "4G"},
    "05": {"cpus_per_task": 2, "mem": "8G"},
    "06": {"cpus_per_task": 2, "mem": "8G"},
    "07": {"cpus_per_task": 2, "mem": "8G"},
    "08": {"cpus_per_task": 2, "mem": "8G"},
    "09": {"cpus_per_task": 2, "mem": "8G"},
    "10": {"cpus_per_task": 4, "mem": "16G"},
    "11": {"cpus_per_task": 1, "mem": "4G"},
    "12": {"cpus_per_task": 4, "mem": "16G"},
    "13": {"cpus_per_task": 4, "mem": "16G"},
    "14": {"cpus_per_task": 4, "mem": "16G"},
    "15": {"cpus_per_task": 4, "mem": "16G"},
    "16": {"time": "04:00:00", "cpus_per_task": 2, "mem": "32G"},
}


# ── ISCE2 discovery ───────────────────────────────────────────────────────────

def _check_isce2(isce_home: Path | None) -> Path:
    """Return the resolved path to topsApp.py, raising if ISCE2 is missing."""
    if isce_home:
        for c in [isce_home / "applications" / "topsApp.py",
                  isce_home / "topsApp.py"]:
            if c.exists():
                return c
        raise EnvironmentError(
            f"ISCE2 not found under isce_home='{isce_home}'. "
            "Check the path or set $ISCE_HOME."
        )
    env_home = os.environ.get("ISCE_HOME")
    if env_home:
        return _check_isce2(Path(env_home))
    import shutil
    which = shutil.which("topsApp.py")
    if which:
        return Path(which)
    raise EnvironmentError(
        "ISCE2 is not installed or not findable. "
        "Install ISCE2 and either set $ISCE_HOME or add its applications/ "
        "directory to $PATH, or pass isce_home= to the config."
    )


def _find_topsstack(isce_home: Path | None) -> tuple[Path, Path]:
    """Return (stackSentinel.py path, PYTHONPATH directory to add).

    The returned pythonpath entry is the *parent* of the topsStack package dir
    so that ``from topsStack.Stack import …`` resolves in subprocesses.
    """
    def _search(base: Path) -> Path | None:
        for rel in [
            "share/isce2/topsStack/stackSentinel.py",
            "contrib/stack/topsStack/stackSentinel.py",
            "components/contrib/stack/topsStack/stackSentinel.py",
        ]:
            p = base / rel
            if p.exists():
                return p
        return None

    candidates: list[Path] = []
    if isce_home:
        candidates.append(Path(isce_home))
    env_home = os.environ.get("ISCE_HOME")
    if env_home:
        candidates.append(Path(env_home))
    try:
        import isce as _isce
        # <env>/lib/pythonX.Y/site-packages/isce → go up 4 levels to env root
        candidates.append(Path(_isce.__file__).parent.parent.parent.parent)
    except ImportError:
        pass
    import shutil
    tops = shutil.which("topsApp.py")
    if tops:
        candidates.append(Path(tops).parent.parent)

    for base in candidates:
        s = _search(base)
        if s:
            return s, s.parent.parent   # topsStack/../ = share/isce2 or contrib/stack

    if not candidates:
        raise EnvironmentError(
            "ISCE2 not found and no base path available to download topsStack into. "
            "Set $ISCE_HOME or pass isce_home= to the config."
        )
    from insarhub.utils.tool import _download_isce_stacktool
    s = _download_isce_stacktool(candidates[0])
    return s, s.parent.parent


# ── Step status helpers ───────────────────────────────────────────────────────

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


# ── Base class ────────────────────────────────────────────────────────────────

class ISCE_Base(LocalProcessor):
    """Shared infrastructure for ISCE2-backed local processors.

    Subclasses must implement ``submit()``.  All monitoring, persistence, and
    step-execution machinery lives here.
    """

    def __init__(self, config):
        super().__init__(config)
        _isce_home = Path(self.config.isce_home) if self.config.isce_home else None
        self._stack_bin, self._pythonpath_add = _find_topsstack(_isce_home)
        self._isce_app_bin = _check_isce2(_isce_home)

        self.workdir: Path = Path(self.config.workdir).expanduser().resolve()
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.isce_dir: Path = self.workdir / "isce"
        self.isce_dir.mkdir(parents=True, exist_ok=True)
        self._run_files_dir = self.isce_dir / "run_files"

        self.jobs: dict[str, dict] = {}
        self._executor_thread: threading.Thread | None = None

        if self.config.saved_job_path:
            self._load(Path(self.config.saved_job_path))

    # ── Persistence ───────────────────────────────────────────────────────────

    def _load(self, path: Path) -> None:
        if not path.exists():
            raise FileNotFoundError(f"Job file not found: {path}")
        self.jobs = json.loads(path.read_text()).get("jobs", {})

    def save(self, save_path: Path | str | None = None, silent: bool = False) -> Path:
        if not self.jobs:
            raise ValueError("No jobs to save. Call submit() first.")
        path = (Path(save_path).expanduser().resolve() if save_path
                else self.isce_dir / JOBS_FILE)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(
            {"jobs": self.jobs, "workdir": str(self.workdir)}, indent=2
        ))
        if not silent:
            print(f"{Fore.GREEN}Job file saved to {path}.{Style.RESET_ALL}")
        return path

    # ── Sequential step executor ──────────────────────────────────────────────

    def _build_cmd_sbatch_script(
        self, step: str, cmd: str, cmd_idx: int, log_dir: Path, step_cfg: dict,
        sbatch_dir: Path | None = None,
    ) -> Path:
        """Generate a single-command sbatch script for one line of a step's run file."""
        import dataclasses
        from insarhub.utils.tool import Slurmjob_Config

        env = os.environ.copy()
        pythonpath = str(self._pythonpath_add) + os.pathsep + env.get("PYTHONPATH", "")
        path = (str(self._stack_bin.parent) + os.pathsep
                + str(self._isce_app_bin.parent) + os.pathsep
                + env.get("PATH", ""))

        done_file = log_dir / f"cmd_{cmd_idx:04d}.done"
        fail_file = log_dir / f"cmd_{cmd_idx:04d}.fail"
        log_file  = log_dir / f"cmd_{cmd_idx:04d}.log"

        _slurm_fields = {f.name for f in dataclasses.fields(Slurmjob_Config)}
        _skip = {"job_name", "output_file", "error_file", "dependency",
                 "command", "modules", "conda_env", "export_env", "array"}
        slurm_kwargs = {k: v for k, v in step_cfg.items()
                        if k in _slurm_fields and k not in _skip}
        slurm_cfg = Slurmjob_Config(
            job_name=f"isce_{step}_{cmd_idx:04d}",
            output_file=str(log_dir / f"{step}_{cmd_idx:04d}_slurm_%j.out"),
            error_file=str(log_dir / f"{step}_{cmd_idx:04d}_slurm_%j.err"),
            **slurm_kwargs,
        )

        lines = ["#!/bin/bash"]
        lines += slurm_cfg.to_header_lines()
        lines += [
            "",
            f"export PYTHONPATH={pythonpath!r}",
            f"export PATH={path!r}",
            "",
            f'if [[ -f {done_file} ]]; then echo "cmd_{cmd_idx:04d} already done, skipping."; exit 0; fi',
            "",
            f'{cmd} > {log_file} 2>&1',
            f'_rc=$?',
            f'if [[ $_rc -eq 0 ]]; then',
            f'  touch {done_file}',
            f'  rm -f {fail_file}',
            f'else',
            f'  echo $_rc > {fail_file}',
            f'  exit $_rc',
            f'fi',
        ]

        out_dir = sbatch_dir if sbatch_dir is not None else log_dir
        sbatch_script = out_dir / f"{step}_{cmd_idx:04d}.sbatch"
        sbatch_script.write_text("\n".join(lines) + "\n")
        sbatch_script.chmod(0o755)
        return sbatch_script

    @staticmethod
    def _parse_time_secs(t: str) -> int:
        """Parse SLURM time string (HH:MM:SS, MM:SS, or integer minutes) → seconds."""
        t = str(t).strip()
        parts = t.split(":")
        try:
            if len(parts) == 3:
                return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
            if len(parts) == 2:
                return int(parts[0]) * 60 + int(parts[1])
            return int(parts[0]) * 60
        except ValueError:
            return 0

    @staticmethod
    def _fmt_secs(s: int) -> str:
        h, rem = divmod(s, 3600)
        m, sec = divmod(rem, 60)
        return f"{h:02d}:{m:02d}:{sec:02d}"

    def _dry_run_path_checks(self) -> bool:
        """Print path validation table. Returns True if all required paths OK."""
        cfg = self.config
        W, G, R, B, E = Fore.YELLOW, Fore.GREEN, Fore.RED, Style.BRIGHT, Style.RESET_ALL
        print(f"\n{B}{'─'*60}{E}")
        print(f"{B}  DRY-RUN VALIDATION{E}")
        print(f"{'─'*60}")
        checks = [
            ("workdir",   self.workdir,                                           True),
            ("isce_dir",  self.isce_dir,                                          True),
            ("run_files", self._run_files_dir,                                    True),
            ("slc_dir",   Path(str(cfg.slc_dir))   if cfg.slc_dir   else None,   True),
            ("orbit_dir", Path(str(cfg.orbit_dir)) if cfg.orbit_dir else None,   False),
            ("dem_path",  Path(str(cfg.dem_path))  if cfg.dem_path  else None,   False),
        ]
        all_ok = True
        for label, p, required in checks:
            if p is None:
                print(f"  {W}{'?' if required else '-'}{E}  {label:<12}  not set")
                if required:
                    all_ok = False
                continue
            exists = p.exists()
            sym = (G + "✓" + E) if exists else (R + "✗" + E)
            note = ""
            if exists and label == "slc_dir":
                n = len(list(p.glob("*.SAFE"))) + len(list(p.glob("*.zip")))
                note = f"  ({n} SLC file(s))"
            elif exists and label == "orbit_dir":
                note = f"  ({len(list(p.glob('*.EOF')))} orbit file(s))"
            elif not exists and required:
                all_ok = False
            print(f"  {sym}  {label:<12}  {p}{note}")
        return all_ok

    def _hpc_dry_run_summary(self, pending_steps: list[str]) -> None:
        """Validate config paths and print a per-step HPC submission summary."""
        W, G, R, B, E = Fore.YELLOW, Fore.GREEN, Fore.RED, Style.BRIGHT, Style.RESET_ALL

        all_ok = self._dry_run_path_checks()

        # ── Per-step table ────────────────────────────────────────────────────
        print(f"\n{B}{'─'*60}{E}")
        print(f"{B}  {'STEP':<44} {'JOBS':>5}  {'TIME':>9}  {'CPUS':>5}  {'MEM':>6}{E}")
        print(f"{'─'*60}")

        total_jobs = 0
        total_secs = 0
        issues: list[str] = []

        for step in pending_steps:
            script = Path(self.jobs[step]["script"])
            commands = [
                l.strip() for l in script.read_text().splitlines()
                if l.strip() and not l.strip().startswith("#")
            ]
            n_cmds = len(commands)
            step_cfg = self._sbatch_opts_for_step(step)
            t_str  = step_cfg.get("time",          "??:??:??")
            cpus   = step_cfg.get("cpus_per_task", "?")
            mem    = step_cfg.get("mem",            "?")

            secs = self._parse_time_secs(t_str)
            total_secs += secs
            total_jobs += n_cmds

            # flag suspicious values
            step_issues = []
            if secs == 0:
                step_issues.append("time=0?")
                issues.append(f"{step}: time invalid ({t_str})")
            try:
                if int(str(cpus)) < 1:
                    step_issues.append("cpus<1?")
            except ValueError:
                pass

            flag = f"  {W}⚠ {', '.join(step_issues)}{E}" if step_issues else ""
            print(f"  {step:<44} {n_cmds:>5}  {t_str:>9}  {str(cpus):>5}  {str(mem):>6}{flag}")

        # ── Totals ────────────────────────────────────────────────────────────
        print(f"{'─'*60}")
        print(f"  {'TOTAL':<44} {total_jobs:>5}  {self._fmt_secs(total_secs):>9}")
        print(f"\n  Steps run sequentially; commands within each step run in parallel.")
        print(f"  {B}Estimated wall time ≈ {self._fmt_secs(total_secs)}{E}  "
              f"(sum of per-step time limits)")

        if issues:
            print(f"\n{W}  Warnings:{E}")
            for iss in issues:
                print(f"    {W}⚠{E}  {iss}")

        verdict = (f"{G}  ✓ All paths OK — ready to submit.{E}"
                   if all_ok else
                   f"{R}  ✗ Fix missing required paths before submitting.{E}")
        print(f"\n{verdict}")
        print(f"  Run without --dry-run to submit jobs.\n")

    def _step_executor_hpc(self, pending_steps: list[str]) -> None:
        """Submit one sbatch job per command; steps chain via --dependency."""
        dry_run = getattr(self.config, "dry_run", False)
        if dry_run:
            self._hpc_dry_run_summary(pending_steps)
            return

        prev_job_ids: list[str] = []

        for step in pending_steps:
            script  = Path(self.jobs[step]["script"])
            log_dir = Path(self.jobs[step]["log_dir"])
            log_dir.mkdir(parents=True, exist_ok=True)

            commands = [
                self._fix_cmd(line.strip())
                for line in script.read_text().splitlines()
                if line.strip() and not line.strip().startswith("#")
            ]

            if not commands:
                _write_status(self._run_files_dir, step, _SUCCEEDED)
                self.jobs[step]["status"] = _SUCCEEDED
                self.jobs[step]["slurm_job_ids"] = []
                continue

            step_cfg   = self._sbatch_opts_for_step(step)
            dep_flag   = ("--dependency=afterok:" + ":".join(prev_job_ids)) if prev_job_ids else ""
            sbatch_dir = self._run_files_dir / f"{step}_sbatch"
            sbatch_dir.mkdir(parents=True, exist_ok=True)

            step_job_ids: list[str] = []
            submit_failed = False
            all_done = True

            for i, cmd in enumerate(commands):
                done_file = log_dir / f"cmd_{i:04d}.done"
                sbatch_script = self._build_cmd_sbatch_script(step, cmd, i, log_dir, step_cfg, sbatch_dir)

                if done_file.exists():
                    print(f"      cmd_{i:04d}  {Fore.YELLOW}SKIPPED (already done){Style.RESET_ALL}")
                    continue

                all_done = False
                sbatch_cmd = " ".join(filter(None, ["sbatch", dep_flag, str(sbatch_script)]))
                result = subprocess.run(sbatch_cmd, shell=True, capture_output=True, text=True)
                if result.returncode != 0:
                    print(f"  {Fore.RED}sbatch failed for {step}[{i}]: {result.stderr.strip()}{Style.RESET_ALL}")
                    _write_status(self._run_files_dir, step, _FAILED, "sbatch submission failed")
                    self.jobs[step]["status"] = _FAILED
                    self.save(silent=True)
                    submit_failed = True
                    break
                m = re.search(r"\d+", result.stdout)
                step_job_ids.append(m.group() if m else "unknown")

            if submit_failed:
                return

            if all_done and not step_job_ids:
                _write_status(self._run_files_dir, step, _SUCCEEDED)
                self.jobs[step]["status"] = _SUCCEEDED
                self.jobs[step]["slurm_job_ids"] = []
                print(f"  {Fore.GREEN}  ✓ {step}  (all commands already done){Style.RESET_ALL}")
                continue

            self.jobs[step]["slurm_job_ids"] = step_job_ids
            self.jobs[step].pop("slurm_job_id", None)
            self.jobs[step]["status"] = _PENDING
            _write_status(self._run_files_dir, step, _PENDING)
            prev_job_ids = step_job_ids
            ids_preview = (step_job_ids[0] if len(step_job_ids) == 1
                           else f"{step_job_ids[0]}…{step_job_ids[-1]}")
            print(f"  {Fore.CYAN}  ▶ {step}  →  {len(step_job_ids)} job(s) [{ids_preview}]{Style.RESET_ALL}")

        self.save(silent=True)
        print(f"\n{Fore.GREEN}All steps queued. "
              f"SSH session can now be closed — use 'refresh' to check status.{Style.RESET_ALL}")

    def _step_executor(self, pending_steps: list[str]) -> None:
        """Run steps in order; parallelise independent commands within each step."""
        if getattr(self.config, "hpc_mode", False):
            self._step_executor_hpc(pending_steps)
            return

        dry_run = getattr(self.config, "dry_run", False)

        if dry_run:
            W, G, B, E = Fore.YELLOW, Fore.GREEN, Style.BRIGHT, Style.RESET_ALL
            all_ok = self._dry_run_path_checks()
            # ── Step summary table ────────────────────────────────────────────
            print(f"\n{B}{'─'*60}{E}")
            print(f"{B}  {'STEP':<44} {'CMDS':>5}  {'DONE':>5}{E}")
            print(f"{'─'*60}")
            total_cmds = total_done = 0
            for step in pending_steps:
                script  = Path(self.jobs[step]["script"])
                log_dir = Path(self.jobs[step]["log_dir"])
                cmds = [l.strip() for l in script.read_text().splitlines()
                        if l.strip() and not l.strip().startswith("#")]
                done = sum(1 for i in range(len(cmds))
                           if (log_dir / f"cmd_{i:04d}.done").exists())
                total_cmds += len(cmds)
                total_done += done
                done_tag = f"  {G}(all done){E}" if done == len(cmds) else (
                           f"  {W}({done}/{len(cmds)} done){E}" if done else "")
                print(f"  {step:<44} {len(cmds):>5}  {done:>5}{done_tag}")
            print(f"{'─'*60}")
            print(f"  {'TOTAL':<44} {total_cmds:>5}  {total_done:>5}")
            # ── Command listing ───────────────────────────────────────────────
            print(f"\n{B}  COMMANDS{E}")
            for step in pending_steps:
                script  = Path(self.jobs[step]["script"])
                log_dir = Path(self.jobs[step]["log_dir"])
                cmds = [self._fix_cmd(l.strip()) for l in script.read_text().splitlines()
                        if l.strip() and not l.strip().startswith("#")]
                print(f"\n{Fore.CYAN}  ▶ {step}{E}  ({len(cmds)} command(s))")
                for i, cmd in enumerate(cmds):
                    done_file = log_dir / f"cmd_{i:04d}.done"
                    tag = f"  {W}(done){E}" if done_file.exists() else ""
                    print(f"      cmd_{i:04d}  {cmd[:120]}{tag}")
            verdict = (f"{G}  ✓ All paths OK — ready to run.{E}" if all_ok
                       else f"{Fore.RED}  ✗ Fix missing required paths before running.{E}")
            print(f"\n{verdict}")
            print(f"  Run without --dry-run to execute.\n")
            self.save(silent=True)
            return

        for step in pending_steps:
            status, _ = _read_status(self._run_files_dir, step)
            if status == _SUCCEEDED and self.config.skip_existing:
                continue

            script  = Path(self.jobs[step]["script"])
            log_dir = Path(self.jobs[step]["log_dir"])
            commands = [
                self._fix_cmd(line.strip())
                for line in script.read_text().splitlines()
                if line.strip() and not line.strip().startswith("#")
            ]

            print(f"\n{Fore.CYAN}  ▶ {step}{Style.RESET_ALL}  ({len(commands)} command(s))")
            _write_status(self._run_files_dir, step, _RUNNING, str(os.getpid()))
            self.jobs[step]["status"] = _RUNNING

            success = self._run_step(script, log_dir)

            if success:
                _write_status(self._run_files_dir, step, _SUCCEEDED)
                self.jobs[step]["status"] = _SUCCEEDED
                print(f"  {Fore.GREEN}✓ {step}{Style.RESET_ALL}")
            else:
                _write_status(self._run_files_dir, step, _FAILED)
                self.jobs[step]["status"] = _FAILED
                print(f"  {Fore.RED}✗ {step} FAILED — logs: {log_dir}{Style.RESET_ALL}")
                break

        self.save(silent=True)

    def _fix_cmd(self, cmd: str) -> str:
        """Resolve bare .py script names to absolute paths and prefix with sys.executable."""
        import sys
        parts = cmd.split(None, 1)
        if not parts or not parts[0].endswith(".py"):
            return cmd
        script_name = parts[0]
        rest = parts[1] if len(parts) > 1 else ""
        # If already an absolute path, just ensure it runs under sys.executable
        if os.path.isabs(script_name):
            return f"{sys.executable} {cmd}"
        # Resolve: topsStack dir → ISCE2 applications dir → PATH
        for search_dir in (self._stack_bin.parent, self._isce_app_bin.parent):
            candidate = search_dir / script_name
            if candidate.exists():
                resolved = str(candidate)
                break
        else:
            import shutil
            found = shutil.which(script_name)
            resolved = found if found else script_name
        return f"{sys.executable} {resolved} {rest}".strip()

    def _sbatch_opts_for_step(self, step_name: str) -> dict:
        """Return merged Slurmjob_Config kwargs for step_name from sbatch_options_per_step.

        Merges 'default' dict with the step-specific dict (step overrides default).
        Falls back gracefully if values are missing or still in old string format.
        """
        per_step: dict = getattr(self.config, "sbatch_options_per_step", {}) or {}
        default_cfg = per_step.get("default", {})
        if not isinstance(default_cfg, dict):
            default_cfg = {}
        m = re.match(r"run_(\d+)", step_name)
        step_cfg: dict = {}
        if m:
            v = per_step.get(m.group(1), {})
            if isinstance(v, dict):
                step_cfg = v
        return {**default_cfg, **step_cfg}

    def _run_step(self, script: Path, log_dir: Path) -> bool:
        """Execute all commands in a run script in parallel, return True if all pass."""
        commands = [
            self._fix_cmd(line.strip())
            for line in script.read_text().splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        if not commands:
            return True

        n = len(commands)
        w = self.config.max_workers
        print(f"    {n} command(s), max_workers={w}")

        env = os.environ.copy()
        env["PYTHONPATH"] = (str(self._pythonpath_add)
                             + os.pathsep + env.get("PYTHONPATH", ""))
        # Both topsStack scripts (SentinelWrapper.py etc.) and ISCE2 application
        # scripts (looks.py etc.) are called by name in the generated run files.
        env["PATH"] = (str(self._stack_bin.parent)
                       + os.pathsep + str(self._isce_app_bin.parent)
                       + os.pathsep + env.get("PATH", ""))

        failed = 0
        with ThreadPoolExecutor(max_workers=w) as pool:
            futures: dict = {}
            for i, cmd in enumerate(commands):
                done_file = log_dir / f"cmd_{i:04d}.done"
                if done_file.exists():
                    print(f"      cmd_{i:04d}  {Fore.YELLOW}SKIPPED  {Style.RESET_ALL}")
                    continue
                futures[pool.submit(self._run_cmd, cmd, log_dir, i, env)] = i

            for fut in as_completed(futures):
                i = futures[fut]
                rc = fut.result()
                if rc == 0:
                    print(f"      cmd_{i:04d}  {Fore.GREEN}SUCCEEDED{Style.RESET_ALL}")
                else:
                    failed += 1
                    print(f"      cmd_{i:04d}  {Fore.RED}FAILED    (rc={rc}){Style.RESET_ALL}")

        return failed == 0

    def _run_cmd(self, cmd: str, log_dir: Path, idx: int, env: dict) -> int:
        log_file  = log_dir / f"cmd_{idx:04d}.log"
        done_file = log_dir / f"cmd_{idx:04d}.done"
        with open(log_file, "w") as lf:
            result = subprocess.run(
                cmd, shell=True,
                cwd=str(self.isce_dir),
                stdout=lf, stderr=subprocess.STDOUT,
                env=env,
            )
        if result.returncode == 0:
            done_file.touch()
            (log_dir / f"cmd_{idx:04d}.fail").unlink(missing_ok=True)
        else:
            (log_dir / f"cmd_{idx:04d}.fail").write_text(str(result.returncode))
        return result.returncode

    # ── Refresh ───────────────────────────────────────────────────────────────

    _SLURM_DEAD_STATES = frozenset({
        "FAILED", "CANCELLED", "TIMEOUT", "NODE_FAIL", "BOOT_FAIL", "OUT_OF_MEMORY",
    })

    @staticmethod
    def _slurm_active_jobs() -> dict[str, str]:
        """Return {job_id: squeue_state} for all active jobs (R, PD, CG, etc.)."""
        try:
            r = subprocess.run(
                ["squeue", "--noheader", "--format=%i %T", "--me"],
                capture_output=True, text=True, timeout=10,
            )
            result: dict[str, str] = {}
            for line in r.stdout.splitlines():
                parts = line.strip().split()
                if len(parts) >= 2:
                    result[parts[0]] = parts[1]
            return result
        except Exception:
            return {}

    @staticmethod
    def _slurm_job_states(job_ids: list[str]) -> dict[str, str]:
        """Query sacct for the terminal state of specific job IDs.

        Returns {job_id: state} only for jobs that have ended.
        Job IDs still running or not yet started are absent from the result.
        """
        if not job_ids:
            return {}
        try:
            r = subprocess.run(
                ["sacct", "--noheader", "--parsable2",
                 "--format=JobID,State",
                 "--jobs=" + ",".join(job_ids)],
                capture_output=True, text=True, timeout=15,
            )
            result: dict[str, str] = {}
            for line in r.stdout.splitlines():
                parts = line.strip().split("|")
                if len(parts) < 2:
                    continue
                jid = parts[0].strip().split(".")[0]  # strip .batch / array suffixes
                state = parts[1].strip().split(" ")[0]
                # Keep the "worst" state if a job appears multiple times (array steps)
                if jid not in result or state in ISCE_Base._SLURM_DEAD_STATES:
                    result[jid] = state
            return result
        except Exception:
            return {}

    def refresh(self) -> dict[str, dict]:
        """Read file-based status for all steps and print a coloured table."""
        if not self.jobs:
            raise ValueError("No jobs loaded. Call submit() or load a saved job file.")

        _has_slurm_ids = any(
            meta.get("slurm_job_ids") or meta.get("slurm_job_id")
            for meta in self.jobs.values()
        )
        hpc = getattr(self.config, "hpc_mode", False) or _has_slurm_ids
        active_slurm: dict[str, str] = {}
        sacct_states: dict[str, str] = {}
        if hpc:
            active_slurm = self._slurm_active_jobs()
            pending_ids: list[str] = []
            for meta in self.jobs.values():
                if meta.get("status") == _PENDING:
                    # support both old single-id and new list format
                    ids = meta.get("slurm_job_ids") or (
                        [meta["slurm_job_id"]] if meta.get("slurm_job_id") else []
                    )
                    pending_ids.extend(ids)
            if pending_ids:
                sacct_states = self._slurm_job_states(pending_ids)

        counts: dict[str, int] = defaultdict(int)
        color_map = {
            _SUCCEEDED: Fore.GREEN,
            _FAILED:    Fore.RED,
            _RUNNING:   Fore.CYAN,
            _PENDING:   Fore.YELLOW,
        }

        print(f"\n{Style.BRIGHT}{'  ':<3} {'STEP':<45} {'STATUS'}{Style.RESET_ALL}")
        print("-" * 65)

        for step, meta in sorted(self.jobs.items()):
            status, detail = _read_status(self._run_files_dir, step)
            if hpc and status == _PENDING:
                job_ids: list[str] = meta.get("slurm_job_ids") or (
                    [meta["slurm_job_id"]] if meta.get("slurm_job_id") else []
                )
                if job_ids:
                    if any(active_slurm.get(jid) == "RUNNING" for jid in job_ids):
                        status = _RUNNING
                    elif any(jid in active_slurm for jid in job_ids):
                        status = _PENDING  # queued in SLURM but not yet running
                    else:
                        dead = [jid for jid in job_ids
                                if jid in sacct_states
                                and sacct_states[jid] in self._SLURM_DEAD_STATES]
                        if dead:
                            detail = f"SLURM {sacct_states[dead[0]]} (job {dead[0]})"
                            status = _FAILED
                            _write_status(self._run_files_dir, step, _FAILED, detail)
                        else:
                            log_dir_p = Path(meta["log_dir"])
                            fail_count = len(list(log_dir_p.glob("cmd_*.fail")))
                            done_count = len(list(log_dir_p.glob("cmd_*.done")))
                            if fail_count > 0:
                                detail = f"{fail_count} command(s) failed"
                                status = _FAILED
                                _write_status(self._run_files_dir, step, _FAILED, detail)
                            elif done_count >= len(job_ids):
                                status = _SUCCEEDED
                                _write_status(self._run_files_dir, step, _SUCCEEDED)
            meta["status"] = status
            counts[status] += 1
            color  = color_map.get(status, "")
            suffix = f"  ({detail})" if detail and status == _FAILED else ""
            if hpc:
                job_ids_d: list[str] = meta.get("slurm_job_ids") or (
                    [meta["slurm_job_id"]] if meta.get("slurm_job_id") else []
                )
                if len(job_ids_d) == 1:
                    job_id_tag = f"  [job {job_ids_d[0]}]"
                elif len(job_ids_d) > 1:
                    job_id_tag = f"  [{len(job_ids_d)} jobs: {job_ids_d[0]}…{job_ids_d[-1]}]"
                else:
                    job_id_tag = ""
            else:
                job_id_tag = ""
            print(f"  - {step:<43}  {color}{status}{suffix}{Style.RESET_ALL}{job_id_tag}")

            # Per-command lines for multi-command steps (HPC new/old format, and local)
            log_dir_p = Path(meta.get("log_dir", ""))
            if log_dir_p.exists():
                cmd_job_ids: list[str] = meta.get("slurm_job_ids") or []
                if len(cmd_job_ids) > 1:
                    # New HPC format: one job per command — show job IDs
                    for i, jid in enumerate(cmd_job_ids):
                        done_f = log_dir_p / f"cmd_{i:04d}.done"
                        fail_f = log_dir_p / f"cmd_{i:04d}.fail"
                        if done_f.exists():
                            cmd_st, cmd_color = _SUCCEEDED, Fore.GREEN
                        elif fail_f.exists():
                            cmd_st, cmd_color = _FAILED, Fore.RED
                        elif active_slurm.get(jid) == "RUNNING":
                            cmd_st, cmd_color = _RUNNING, Fore.CYAN
                        elif jid in active_slurm:
                            cmd_st, cmd_color = _PENDING, Fore.YELLOW  # SLURM PD
                        else:
                            cmd_st, cmd_color = _PENDING, Fore.YELLOW
                        print(f"      cmd_{i:04d}  {cmd_color}{cmd_st:<9}{Style.RESET_ALL}  [job {jid}]")
                else:
                    # Old HPC format or local mode: scan done/fail files
                    def _idx(f):
                        try: return int(f.stem.split("_", 1)[1])
                        except (ValueError, IndexError): return None
                    indices = sorted(
                        i for i in (
                            {_idx(f) for f in log_dir_p.glob("cmd_????.done")}
                            | {_idx(f) for f in log_dir_p.glob("cmd_????.fail")}
                        ) if i is not None
                    )
                    if len(indices) > 1:
                        for i in indices:
                            done_f = log_dir_p / f"cmd_{i:04d}.done"
                            fail_f = log_dir_p / f"cmd_{i:04d}.fail"
                            if done_f.exists():
                                cmd_st, cmd_color = _SUCCEEDED, Fore.GREEN
                            elif fail_f.exists():
                                cmd_st, cmd_color = _FAILED, Fore.RED
                            else:
                                cmd_st, cmd_color = _PENDING, Fore.YELLOW
                            print(f"      cmd_{i:04d}  {cmd_color}{cmd_st:<9}{Style.RESET_ALL}")

        print()
        print(f"  {Fore.GREEN}Succeeded : {counts[_SUCCEEDED]}{Style.RESET_ALL}  "
              f"{Fore.CYAN}Running : {counts[_RUNNING]}{Style.RESET_ALL}  "
              f"{Fore.YELLOW}Pending : {counts[_PENDING]}{Style.RESET_ALL}  "
              f"{Fore.RED}Failed : {counts[_FAILED]}{Style.RESET_ALL}")

        self.save(silent=True)
        return self.jobs

    # ── Retry ─────────────────────────────────────────────────────────────────

    def retry(self) -> dict:
        """Re-run the first failed step and all subsequent steps."""
        failed = [n for n, m in sorted(self.jobs.items()) if m["status"] == _FAILED]
        if not failed:
            self.refresh()
            failed = [n for n, m in sorted(self.jobs.items()) if m["status"] == _FAILED]
        if not failed:
            print(f"{Fore.GREEN}No failed steps.{Style.RESET_ALL}")
            return {}

        first_failed = failed[0]
        to_retry = sorted(n for n in self.jobs if n >= first_failed)
        for step in to_retry:
            _write_status(self._run_files_dir, step, _PENDING)
            self.jobs[step]["status"] = _PENDING

        print(f"{Fore.YELLOW}Retrying {len(to_retry)} step(s) "
              f"from {first_failed}…{Style.RESET_ALL}")

        hpc_mode = getattr(self.config, "hpc_mode", False)
        dry_run  = getattr(self.config, "dry_run", False)
        if hpc_mode or dry_run:
            self._step_executor(to_retry)
        else:
            self._start_local_background(to_retry)
        return self.jobs

    # ── Background local execution ────────────────────────────────────────────

    def _start_local_background(self, pending_steps: list[str]) -> None:
        """Fork a detached process to run steps; parent returns immediately."""
        pid_file = self._run_files_dir / "executor.pid"
        log_file = self._run_files_dir / "executor.log"
        if os.name == "posix":
            pid = os.fork()
            if pid == 0:  # child — detach and run
                try:
                    os.setsid()
                    with open(log_file, "w") as _lf:
                        os.dup2(_lf.fileno(), sys.stdout.fileno())
                        os.dup2(_lf.fileno(), sys.stderr.fileno())
                    self._step_executor(pending_steps)
                finally:
                    os._exit(0)
            # parent
            pid_file.write_text(str(pid))
            print(f"{Fore.GREEN}Local executor running in background (PID {pid}).{Style.RESET_ALL}")
            print(f"  log : {log_file}")
            print(f"  Use 'refresh' to check status, 'cancel' to stop.")
        else:
            # Windows: no fork — run blocking
            self._step_executor(pending_steps)

    # ── Cancel ────────────────────────────────────────────────────────────────

    def cancel(self) -> None:
        """Cancel all running/pending jobs (HPC: scancel; local: SIGTERM by PID).

        HPC mode is auto-detected from slurm_job_ids in isce_jobs.json —
        no need to pass --hpc-mode on the cancel command.
        """
        _has_slurm_ids = any(
            meta.get("slurm_job_ids") or meta.get("slurm_job_id")
            for meta in self.jobs.values()
        )
        if getattr(self.config, "hpc_mode", False) or _has_slurm_ids:
            all_ids: list[str] = []
            for meta in self.jobs.values():
                ids = meta.get("slurm_job_ids") or (
                    [meta["slurm_job_id"]] if meta.get("slurm_job_id") else []
                )
                all_ids.extend(ids)
            valid_ids = [jid for jid in all_ids if jid and jid != "unknown"]
            if not valid_ids:
                print(f"{Fore.YELLOW}No SLURM job IDs found.{Style.RESET_ALL}")
                return
            result = subprocess.run(["scancel"] + valid_ids, capture_output=True, text=True)
            if result.returncode == 0:
                print(f"{Fore.GREEN}scancel: cancelled {len(valid_ids)} job(s).{Style.RESET_ALL}")
            else:
                print(f"{Fore.RED}scancel error: {result.stderr.strip()}{Style.RESET_ALL}")
            for step, meta in self.jobs.items():
                if meta.get("status") in (_PENDING, _RUNNING):
                    meta["status"] = _FAILED
                    _write_status(self._run_files_dir, step, _FAILED, "cancelled by user")
            self.save(silent=True)
        else:
            pid_file = self._run_files_dir / "executor.pid"
            if not pid_file.exists():
                print(f"{Fore.YELLOW}No local executor running (no PID file).{Style.RESET_ALL}")
                return
            try:
                pid = int(pid_file.read_text().strip())
                try:
                    os.killpg(os.getpgid(pid), signal.SIGTERM)
                    print(f"{Fore.GREEN}Sent SIGTERM to executor (PID {pid}).{Style.RESET_ALL}")
                except ProcessLookupError:
                    print(f"{Fore.YELLOW}Process already finished.{Style.RESET_ALL}")
                pid_file.unlink(missing_ok=True)
                for step, meta in self.jobs.items():
                    if meta.get("status") in (_RUNNING, _PENDING):
                        meta["status"] = _FAILED
                        _write_status(self._run_files_dir, step, _FAILED, "cancelled by user")
                self.save(silent=True)
            except Exception as e:
                print(f"{Fore.RED}Cancel error: {e}{Style.RESET_ALL}", file=sys.stderr)

    # ── Watch ─────────────────────────────────────────────────────────────────

    def watch(self, refresh_interval: int = 60) -> None:
        """Poll step status until all steps finish or one fails."""
        total = len(self.jobs)
        print(f"{Fore.GREEN}Watching {total} steps every {refresh_interval}s. "
              f"Press Ctrl+C to stop.{Style.RESET_ALL}")
        try:
            while True:
                with redirect_stdout(io.StringIO()):
                    self.refresh()

                succeeded = sum(1 for m in self.jobs.values() if m["status"] == _SUCCEEDED)
                running   = sum(1 for m in self.jobs.values() if m["status"] == _RUNNING)
                pending   = sum(1 for m in self.jobs.values() if m["status"] == _PENDING)
                failed    = sum(1 for m in self.jobs.values() if m["status"] == _FAILED)

                ts = time.strftime("%H:%M:%S")
                print(f"[{ts}] "
                      f"{Fore.GREEN}{succeeded}/{total} Done{Style.RESET_ALL}  "
                      f"{Fore.CYAN}{running} Running{Style.RESET_ALL}  "
                      f"{Fore.YELLOW}{pending} Pending{Style.RESET_ALL}  "
                      f"{Fore.RED}{failed} Failed{Style.RESET_ALL}")

                if running == 0 and pending == 0:
                    if failed:
                        print(f"\n{Fore.RED}Processing stopped at a failed step. "
                              f"Call retry() to resume.{Style.RESET_ALL}")
                    else:
                        print(f"\n{Fore.GREEN}All steps completed!{Style.RESET_ALL}")
                    break

                time.sleep(refresh_interval)
        except KeyboardInterrupt:
            print(f"\n{Fore.YELLOW}Stopped watching by user.{Style.RESET_ALL}")
