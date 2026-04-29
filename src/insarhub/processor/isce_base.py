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
import subprocess
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

JOBS_FILE = "stack_jobs.json"


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
        _check_isce2(_isce_home)

        self.workdir: Path = Path(self.config.workdir).expanduser().resolve()
        self.workdir.mkdir(parents=True, exist_ok=True)
        self._run_files_dir = self.workdir / "run_files"

        self.jobs: dict[str, dict] = {}
        self._executor_thread: threading.Thread | None = None

        if self.config.saved_job_path:
            self._load(Path(self.config.saved_job_path))

    # ── Persistence ───────────────────────────────────────────────────────────

    def _load(self, path: Path) -> None:
        if not path.exists():
            raise FileNotFoundError(f"Job file not found: {path}")
        self.jobs = json.loads(path.read_text()).get("jobs", {})

    def save(self, save_path: Path | str | None = None) -> Path:
        if not self.jobs:
            raise ValueError("No jobs to save. Call submit() first.")
        path = (Path(save_path).expanduser().resolve() if save_path
                else self.workdir / JOBS_FILE)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(
            {"jobs": self.jobs, "workdir": str(self.workdir)}, indent=2
        ))
        print(f"{Fore.GREEN}Job file saved to {path}.{Style.RESET_ALL}")
        return path

    # ── Sequential step executor ──────────────────────────────────────────────

    def _step_executor(self, pending_steps: list[str]) -> None:
        """Run steps in order; parallelise independent commands within each step."""
        for step in pending_steps:
            status, _ = _read_status(self._run_files_dir, step)
            if status == _SUCCEEDED and self.config.skip_existing:
                continue

            _write_status(self._run_files_dir, step, _RUNNING, str(os.getpid()))
            self.jobs[step]["status"] = _RUNNING

            script  = Path(self.jobs[step]["script"])
            log_dir = Path(self.jobs[step]["log_dir"])
            print(f"\n{Fore.CYAN}  ▶ {step}{Style.RESET_ALL}")

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

        self.save()

    def _run_step(self, script: Path, log_dir: Path) -> bool:
        """Execute all commands in a run script in parallel, return True if all pass."""
        commands = [
            line.strip() for line in script.read_text().splitlines()
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

        failed = 0
        with ThreadPoolExecutor(max_workers=w) as pool:
            futures = {
                pool.submit(self._run_cmd, cmd, log_dir, i, env): cmd
                for i, cmd in enumerate(commands)
            }
            for fut in as_completed(futures):
                rc = fut.result()
                if rc != 0:
                    failed += 1
                    snippet = futures[fut][:80]
                    print(f"  {Fore.RED}  cmd failed (rc={rc}): {snippet}…{Style.RESET_ALL}")

        return failed == 0

    def _run_cmd(self, cmd: str, log_dir: Path, idx: int, env: dict) -> int:
        log_file = log_dir / f"cmd_{idx:04d}.log"
        with open(log_file, "w") as lf:
            result = subprocess.run(
                cmd, shell=True,
                cwd=str(self.workdir),
                stdout=lf, stderr=subprocess.STDOUT,
                env=env,
            )
        return result.returncode

    # ── Refresh ───────────────────────────────────────────────────────────────

    def refresh(self) -> dict[str, dict]:
        """Read file-based status for all steps and print a coloured table."""
        if not self.jobs:
            raise ValueError("No jobs loaded. Call submit() or load a saved job file.")

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
            meta["status"] = status
            counts[status] += 1
            color  = color_map.get(status, "")
            suffix = f"  ({detail})" if detail and status == _FAILED else ""
            print(f"  - {step:<43}  {color}{status}{suffix}{Style.RESET_ALL}")

        print()
        print(f"  {Fore.GREEN}Succeeded : {counts[_SUCCEEDED]}{Style.RESET_ALL}  "
              f"{Fore.CYAN}Running : {counts[_RUNNING]}{Style.RESET_ALL}  "
              f"{Fore.YELLOW}Pending : {counts[_PENDING]}{Style.RESET_ALL}  "
              f"{Fore.RED}Failed : {counts[_FAILED]}{Style.RESET_ALL}")

        self.save()
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

        self._executor_thread = threading.Thread(
            target=self._step_executor,
            args=(to_retry,),
            daemon=True,
            name="stack-executor",
        )
        self._executor_thread.start()
        return self.jobs

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
