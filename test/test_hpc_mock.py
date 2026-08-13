"""
Mock HPC test for ISCE2_Base sbatch submission.

Does NOT require ISCE2 or SLURM.  Patches:
  - _find_topsstack / _check_isce2   (ISCE2 discovery)
  - subprocess.run                    (sbatch / squeue / sacct)

Verifies:
  1. Generated .sbatch scripts have correct #SBATCH headers from Slurmjob_Config
  2. Dependency chain is correct (step N+1 depends on step N's job ID)
  3. isce_jobs.json records slurm_job_id per step
  4. refresh() reads status files correctly
  5. refresh() promotes PENDING → RUNNING via squeue mock
  6. refresh() fallback: sacct detects CANCELLED job, marks step FAILED
"""

from __future__ import annotations

import sys
import types
import unittest
from unittest.mock import MagicMock

# Stub out heavy optional dependencies before any insarhub import.
# MagicMock auto-creates attributes (e.g. TimeSeriesAnalysis) on access;
# __path__ makes Python treat it as a package so sub-module imports work.
def _stub(name: str) -> MagicMock:
    mod = MagicMock(spec=None)
    mod.__path__ = []
    mod.__name__ = name
    mod.__spec__ = None
    mod.__loader__ = None
    mod.__package__ = name
    return mod

for _name in (
    "osgeo", "osgeo.gdal", "osgeo.osr", "osgeo.ogr",
    "mintpy", "mintpy.smallbaselineApp", "mintpy.utils",
    "mintpy.utils.readfile", "mintpy.utils.utils",
    "mintpy.utils.network", "mintpy.utils.plot",
):
    if _name not in sys.modules:
        sys.modules[_name] = _stub(_name)

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import call, patch

# ── helpers ──────────────────────────────────────────────────────────────────

def _make_fake_run_script(run_files_dir: Path, step: str, n_cmds: int) -> Path:
    run_files_dir.mkdir(parents=True, exist_ok=True)
    script = run_files_dir / step
    lines = [f"echo pair_{i}" for i in range(n_cmds)]
    script.write_text("\n".join(lines))
    return script


def _make_processor(workdir: Path, sbatch_opts: dict):
    """Build an ISCE2_Base subclass instance with ISCE2 discovery patched out."""
    from insarhub.processor.isce2_base import ISCE2_Base, JOBS_FILE
    from insarhub.config.defaultconfig import ISCE2_S1_Config

    fake_bin = workdir / "fake_isce" / "topsApp.py"
    fake_bin.parent.mkdir(parents=True, exist_ok=True)
    fake_bin.touch()

    cfg = ISCE2_S1_Config(
        workdir=str(workdir),
        hpc_mode=True,
        sbatch_options_per_step=sbatch_opts,
    )

    with patch("insarhub.processor.isce2_base._find_topsstack",
               return_value=(fake_bin, fake_bin.parent)), \
         patch("insarhub.processor.isce2_base._check_isce2",
               return_value=fake_bin):
        proc = type("FakeProc", (ISCE2_Base,), {"submit": lambda self: None})(cfg)

    return proc


# ── test cases ────────────────────────────────────────────────────────────────

class TestSbatchScriptGeneration(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_sbatch_header_from_slurmjob_config(self):
        """Generated .sbatch file must have correct #SBATCH directives."""
        sbatch_opts = {
            "default": {"time": "02:00:00", "partition": "test_q",
                        "ntasks": 1, "cpus_per_task": 4, "mem": "8G"},
            "01": {"cpus_per_task": 2, "mem": "4G"},
        }
        proc = _make_processor(self.workdir, sbatch_opts)
        run_files = self.workdir / "run_files"
        step = "run_01_unpack_topo_reference"
        script = _make_fake_run_script(run_files, step, n_cmds=3)
        log_dir = run_files / f"{step}_logs"
        log_dir.mkdir(parents=True, exist_ok=True)

        step_cfg = proc._sbatch_opts_for_step(step)
        sbatch_file = proc._build_step_sbatch_script(step, script, log_dir, step_cfg)

        content = sbatch_file.read_text()
        print("\n--- Generated .sbatch script ---")
        print(content)

        self.assertTrue(sbatch_file.exists())
        self.assertIn("#!/bin/bash", content)
        self.assertIn(f"#SBATCH --job-name=isce_{step}", content)
        self.assertIn("#SBATCH --time=02:00:00", content)
        self.assertIn("#SBATCH --partition=test_q", content)
        self.assertIn("#SBATCH --cpus-per-task=2", content)   # step override
        self.assertIn("#SBATCH --mem=4G", content)             # step override
        self.assertIn("#SBATCH --ntasks=1", content)           # from default
        # Each command must appear with done/fail logic
        for i in range(3):
            self.assertIn(f"cmd_{i:04d}.done", content)
            self.assertIn(f"cmd_{i:04d}.fail", content)
        # Status file write at end
        self.assertIn("SUCCEEDED", content)
        self.assertIn("FAILED", content)

    def test_default_fallback_when_step_not_listed(self):
        """Step not in sbatch_options_per_step uses 'default' values."""
        sbatch_opts = {
            "default": {"time": "06:00:00", "partition": "bigmem",
                        "ntasks": 1, "cpus_per_task": 8, "mem": "32G"},
        }
        proc = _make_processor(self.workdir, sbatch_opts)
        run_files = self.workdir / "run_files"
        step = "run_10_fullBurst_resample"
        script = _make_fake_run_script(run_files, step, n_cmds=1)
        log_dir = run_files / f"{step}_logs"
        log_dir.mkdir(parents=True, exist_ok=True)

        step_cfg = proc._sbatch_opts_for_step(step)
        sbatch_file = proc._build_step_sbatch_script(step, script, log_dir, step_cfg)

        content = sbatch_file.read_text()
        self.assertIn("#SBATCH --time=06:00:00", content)
        self.assertIn("#SBATCH --partition=bigmem", content)
        self.assertIn("#SBATCH --cpus-per-task=8", content)
        self.assertIn("#SBATCH --mem=32G", content)


class TestStepExecutorHPC(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _make_proc_with_jobs(self, steps: list[str], n_cmds: int = 2):
        sbatch_opts = {
            "default": {"time": "01:00:00", "partition": "all",
                        "ntasks": 1, "cpus_per_task": 2, "mem": "4G"},
        }
        proc = _make_processor(self.workdir, sbatch_opts)
        run_files = self.workdir / "run_files"

        from insarhub.processor.isce2_base import _PENDING, _write_status
        for step in steps:
            script = _make_fake_run_script(run_files, step, n_cmds)
            log_dir = run_files / f"{step}_logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            _write_status(run_files, step, _PENDING)
            proc.jobs[step] = {
                "step": step,
                "script": str(script),
                "log_dir": str(log_dir),
                "status": _PENDING,
                "submitted_at": "2025-01-01T00:00:00+00:00",
            }
        return proc

    def test_dependency_chain(self):
        """Each step must depend on the previous step's SLURM job ID."""
        steps = ["run_01_unpack", "run_02_secondary", "run_03_baseline"]
        proc = self._make_proc_with_jobs(steps)

        job_counter = iter(range(10001, 10010))

        def fake_sbatch(cmd, **kwargs):
            r = MagicMock()
            if "sbatch" in cmd:
                jid = next(job_counter)
                r.returncode = 0
                r.stdout = f"Submitted batch job {jid}\n"
                r.stderr = ""
            else:
                r.returncode = 0
                r.stdout = ""
                r.stderr = ""
            return r

        with patch("insarhub.processor.isce2_base.subprocess.run", side_effect=fake_sbatch):
            proc._step_executor_hpc(steps)

        # Verify job IDs saved
        self.assertEqual(proc.jobs["run_01_unpack"]["slurm_job_id"], "10001")
        self.assertEqual(proc.jobs["run_02_secondary"]["slurm_job_id"], "10002")
        self.assertEqual(proc.jobs["run_03_baseline"]["slurm_job_id"], "10003")

        # Verify isce_jobs.json written with job IDs
        jobs_file = self.workdir / "isce_jobs.json"
        self.assertTrue(jobs_file.exists())
        saved = json.loads(jobs_file.read_text())
        self.assertEqual(saved["jobs"]["run_01_unpack"]["slurm_job_id"], "10001")
        self.assertEqual(saved["jobs"]["run_02_secondary"]["slurm_job_id"], "10002")

        print("\n--- isce_jobs.json ---")
        print(json.dumps(saved, indent=2))

    def test_dependency_flags_passed_correctly(self):
        """sbatch calls must carry correct --dependency=afterok:<prev_id> flags."""
        steps = ["run_01_a", "run_02_b", "run_03_c"]
        proc = self._make_proc_with_jobs(steps)

        submitted_cmds = []
        job_counter = iter(range(20001, 20010))

        def fake_sbatch(cmd, **kwargs):
            r = MagicMock()
            if "sbatch" in cmd:
                submitted_cmds.append(cmd)
                jid = next(job_counter)
                r.returncode = 0
                r.stdout = f"Submitted batch job {jid}\n"
                r.stderr = ""
            else:
                r.returncode = 0; r.stdout = ""; r.stderr = ""
            return r

        with patch("insarhub.processor.isce2_base.subprocess.run", side_effect=fake_sbatch):
            proc._step_executor_hpc(steps)

        print("\n--- sbatch commands issued ---")
        for c in submitted_cmds:
            print(" ", c)

        # Step 1: no dependency
        self.assertNotIn("dependency", submitted_cmds[0])
        # Step 2: depends on job 20001
        self.assertIn("afterok:20001", submitted_cmds[1])
        # Step 3: depends on job 20002
        self.assertIn("afterok:20002", submitted_cmds[2])

    def test_stops_on_sbatch_failure(self):
        """If sbatch fails for step N, remaining steps must not be submitted."""
        steps = ["run_01_a", "run_02_b", "run_03_c"]
        proc = self._make_proc_with_jobs(steps)

        call_count = [0]

        def fake_sbatch(cmd, **kwargs):
            r = MagicMock()
            if "sbatch" in cmd:
                call_count[0] += 1
                if call_count[0] == 2:           # fail on step 2
                    r.returncode = 1
                    r.stdout = ""
                    r.stderr = "sbatch: error: fake failure"
                else:
                    r.returncode = 0
                    r.stdout = f"Submitted batch job 3000{call_count[0]}\n"
                    r.stderr = ""
            else:
                r.returncode = 0; r.stdout = ""; r.stderr = ""
            return r

        with patch("insarhub.processor.isce2_base.subprocess.run", side_effect=fake_sbatch):
            proc._step_executor_hpc(steps)

        # Only 2 sbatch calls (step 3 never submitted)
        self.assertEqual(call_count[0], 2)
        from insarhub.processor.isce2_base import _FAILED
        self.assertEqual(proc.jobs["run_02_b"]["status"], _FAILED)


class TestRefreshWithMockedSLURM(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _make_loaded_proc(self, step_job_ids: dict[str, str]):
        """Create a processor with pre-loaded jobs (simulates post-submit state)."""
        from insarhub.processor.isce2_base import _PENDING, _write_status
        sbatch_opts = {"default": {"time": "01:00:00", "partition": "all",
                                    "ntasks": 1, "cpus_per_task": 2, "mem": "4G"}}
        proc = _make_processor(self.workdir, sbatch_opts)
        run_files = self.workdir / "run_files"
        run_files.mkdir(parents=True, exist_ok=True)

        for step, jid in step_job_ids.items():
            log_dir = run_files / f"{step}_logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            _write_status(run_files, step, _PENDING)
            proc.jobs[step] = {
                "step": step, "script": str(run_files / step),
                "log_dir": str(log_dir), "status": _PENDING,
                "slurm_job_id": jid, "submitted_at": "2025-01-01T00:00:00+00:00",
            }
        return proc

    def test_pending_promoted_to_running_via_squeue(self):
        """Steps whose job ID appears in squeue must show as RUNNING."""
        proc = self._make_loaded_proc({
            "run_01_a": "10001",
            "run_02_b": "10002",
        })

        def fake_run(cmd, **kwargs):
            r = MagicMock()
            r.returncode = 0
            if "squeue" in (cmd[0] if isinstance(cmd, list) else cmd):
                r.stdout = "10001\n"   # only job 10001 is active
            else:
                r.stdout = ""
            r.stderr = ""
            return r

        with patch("insarhub.processor.isce2_base.subprocess.run", side_effect=fake_run):
            result = proc.refresh()

        from insarhub.processor.isce2_base import _RUNNING, _PENDING
        self.assertEqual(result["run_01_a"]["status"], _RUNNING)
        self.assertEqual(result["run_02_b"]["status"], _PENDING)

    def test_sacct_fallback_marks_cancelled_job_failed(self):
        """A PENDING step whose job was CANCELLED in sacct must be marked FAILED."""
        proc = self._make_loaded_proc({
            "run_01_a": "10001",
            "run_02_b": "10002",
        })

        def fake_run(cmd, **kwargs):
            r = MagicMock()
            r.returncode = 0
            cmd_str = cmd[0] if isinstance(cmd, list) else cmd
            if "squeue" in cmd_str:
                r.stdout = ""            # nothing active
            elif "sacct" in cmd_str:
                # job 10001 CANCELLED, 10002 not in sacct yet (still pending)
                r.stdout = "10001|CANCELLED\n10001.batch|CANCELLED\n"
            else:
                r.stdout = ""
            r.stderr = ""
            return r

        with patch("insarhub.processor.isce2_base.subprocess.run", side_effect=fake_run):
            result = proc.refresh()

        from insarhub.processor.isce2_base import _FAILED, _PENDING
        self.assertEqual(result["run_01_a"]["status"], _FAILED)
        self.assertEqual(result["run_02_b"]["status"], _PENDING)

        # Status file must have been written
        sf = self.workdir / "run_files" / "run_01_a.status"
        self.assertTrue(sf.exists())
        self.assertIn("FAILED", sf.read_text())
        print(f"\n--- Status file after sacct fallback: {sf.read_text()!r} ---")

    def test_succeeded_step_reads_from_status_file(self):
        """A step that wrote SUCCEEDED to its status file must show SUCCEEDED."""
        from insarhub.processor.isce2_base import _SUCCEEDED, _write_status
        proc = self._make_loaded_proc({"run_01_a": "10001"})
        _write_status(self.workdir / "run_files", "run_01_a", _SUCCEEDED)

        def fake_run(cmd, **kwargs):
            r = MagicMock(); r.returncode = 0; r.stdout = ""; r.stderr = ""
            return r

        with patch("insarhub.processor.isce2_base.subprocess.run", side_effect=fake_run):
            result = proc.refresh()

        self.assertEqual(result["run_01_a"]["status"], _SUCCEEDED)


# ── run ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    unittest.main(verbosity=2)
