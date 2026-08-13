"""
Tests for the `--container` feature: wrap_container_cmd() plus the
container-mode branches in ISCE2_S1/ISCE2_Base and Mintpy_SBAS_Base_Analyzer.

Does NOT require ISCE2, MintPy, or SLURM — ISCE2/topsStack discovery is
patched out (mirroring test_hpc_mock.py's approach) and mintpy/osgeo are
stubbed if not installed, since this feature is meant to work with MintPy
absent (see test_mintpy_optional.py).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


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
    "mintpy.utils.network", "mintpy.utils.plot", "mintpy.cli", "mintpy.cli.geocode",
):
    if _name not in sys.modules:
        sys.modules[_name] = _stub(_name)

from insarhub.utils.container import wrap_container_cmd


class TestWrapContainerCmd(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_sif_path_uses_apptainer_exec(self):
        sif = self.workdir / "image.sif"
        sif.touch()
        wrapped = wrap_container_cmd(str(sif), "echo hi", self.workdir)
        self.assertIn("exec", wrapped)
        self.assertIn(f"--bind {self.workdir}:{self.workdir}", wrapped)
        self.assertIn(str(sif), wrapped)
        self.assertIn("echo hi", wrapped)

    def test_docker_image_reference_uses_docker_run(self):
        wrapped = wrap_container_cmd("myregistry/isce2:latest", "echo hi", self.workdir)
        self.assertTrue(wrapped.startswith("docker run --rm"))
        self.assertIn(f"-v {self.workdir}:{self.workdir}", wrapped)
        self.assertIn(f"-w {self.workdir}", wrapped)
        self.assertIn("myregistry/isce2:latest", wrapped)
        self.assertIn("echo hi", wrapped)

    @unittest.skipUnless(
        shutil.which("docker") and subprocess.run(
            ["docker", "info"], capture_output=True
        ).returncode == 0,
        "docker not installed or not accessible (daemon permissions)",
    )
    def test_docker_wrap_actually_runs(self):
        wrapped = wrap_container_cmd("alpine:latest", "echo hi", self.workdir)
        result = subprocess.run(wrapped, shell=True, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("hi", result.stdout)


class TestIsceS1ContainerMode(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _make_isce2_s1(self, container: str | None):
        from insarhub.processor.isce2_s1 import ISCE2_S1
        from insarhub.config.defaultconfig import ISCE2_S1_Config

        fake_bin = self.workdir / "fake_isce" / "topsApp.py"
        fake_bin.parent.mkdir(parents=True, exist_ok=True)
        fake_bin.touch()

        cfg = ISCE2_S1_Config(workdir=str(self.workdir), container=container)
        with patch("insarhub.processor.isce2_base._find_topsstack",
                   return_value=(fake_bin, fake_bin.parent)), \
             patch("insarhub.processor.isce2_base._check_isce2",
                   return_value=fake_bin):
            return ISCE2_S1(pairs=[("20200101", "20200113")], config=cfg)

    def test_submit_short_circuits_to_container(self):
        proc = self._make_isce2_s1("/fake/image.sif")
        with patch.object(proc, "_reinvoke_via_container") as mock_reinvoke:
            proc.submit(steps=["run_01"])
        mock_reinvoke.assert_called_once_with("submit", ["run_01"])

    def test_submit_runs_normally_without_container(self):
        proc = self._make_isce2_s1(None)
        with patch.object(proc, "_reinvoke_via_container") as mock_reinvoke, \
             patch.object(proc, "_generate_run_files") as mock_gen:
            # No SLCs/bbox available, so submit() should raise (from DEM prep,
            # before _generate_run_files) rather than routing through the
            # container branch — confirming that branch is skipped entirely
            # when container is unset.
            with self.assertRaises(ValueError):
                proc.submit()
        mock_reinvoke.assert_not_called()
        mock_gen.assert_not_called()

    def test_retry_short_circuits_to_container(self):
        proc = self._make_isce2_s1("/fake/image.sif")
        with patch.object(proc, "_reinvoke_via_container") as mock_reinvoke:
            result = proc.retry()
        mock_reinvoke.assert_called_once_with("retry")
        self.assertEqual(result, proc.jobs)

    def test_reinvoke_via_container_builds_wrapped_command_and_excludes_container_field(self):
        proc = self._make_isce2_s1("/fake/image.sif")
        with patch("insarhub.processor.isce2_base.os.fork", return_value=999), \
             patch("insarhub.utils.container.wrap_container_cmd") as mock_wrap:
            mock_wrap.return_value = "WRAPPED_CMD"
            proc._reinvoke_via_container("submit", ["run_01", "run_02"])

        mock_wrap.assert_called_once()
        container_arg, cli_cmd, bind_dir = mock_wrap.call_args[0]
        self.assertEqual(container_arg, "/fake/image.sif")
        self.assertIn("insarhub processor -N ISCE2_S1", cli_cmd)
        self.assertIn(f"-w {proc.workdir}", cli_cmd)
        self.assertIn("submit", cli_cmd)
        self.assertIn("--step run_01 run_02", cli_cmd)
        self.assertEqual(bind_dir, proc.workdir)

        written = json.loads((proc.workdir / "insarhub_config.json").read_text())
        self.assertNotIn("container", written["processor"]["config"])
        self.assertEqual(written["processor"]["type"], "ISCE2_S1")

        pid_file = proc._run_files_dir / "executor.pid"
        self.assertEqual(pid_file.read_text(), "999")


class TestMintpyAnalyzerContainerMode(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _make_analyzer(self, container: str | None):
        # Mintpy_SBAS_Base_Analyzer itself has no `name` set (never registered
        # in Analyzer._registry) — use the concrete Hyp3_SBAS subclass, which
        # inherits run()/submit_hpc()/_run_via_container() from the base class
        # unchanged, and has its own prep_data() override with the same
        # container short-circuit added at the top.
        from insarhub.analyzer.hyp3_sbas import Hyp3_SBAS
        from insarhub.config.defaultconfig import Hyp3_SBAS_Config

        cfg = Hyp3_SBAS_Config(workdir=str(self.workdir), container=container)
        return Hyp3_SBAS(cfg)

    def test_run_short_circuits_to_container(self):
        analyzer = self._make_analyzer("/fake/image.sif")
        with patch.object(analyzer, "_run_via_container") as mock_run:
            analyzer.run(steps=["load_data"])
        mock_run.assert_called_once_with(["load_data"])

    def test_prep_data_short_circuits_to_container(self):
        analyzer = self._make_analyzer("/fake/image.sif")
        with patch.object(analyzer, "_run_via_container") as mock_run:
            analyzer.prep_data()
        mock_run.assert_called_once_with(["prep_data"])

    def test_run_via_container_builds_wrapped_command(self):
        analyzer = self._make_analyzer("/fake/image.sif")
        with patch("subprocess.run") as mock_subproc:
            mock_subproc.return_value.returncode = 0
            analyzer._run_via_container(["load_data", "modify_network"])

        mock_subproc.assert_called_once()
        wrapped_cmd = mock_subproc.call_args[0][0]
        self.assertTrue(wrapped_cmd.startswith("apptainer") or wrapped_cmd.startswith("docker"))
        self.assertIn("insarhub analyzer -N Hyp3_SBAS", wrapped_cmd)
        self.assertIn("--step load_data modify_network", wrapped_cmd)

    def test_run_via_container_excludes_container_flag_from_reinvocation(self):
        """The re-invoked CLI command must never carry --container itself,
        or the container-side process would try to launch another nested
        container."""
        analyzer = self._make_analyzer("/fake/image.sif")
        extra = analyzer._serialize_config_overrides()
        self.assertNotIn("--container", extra)

    def test_submit_hpc_wraps_body_command_when_container_set(self):
        analyzer = self._make_analyzer("/fake/image.sif")
        with patch("insarhub.processor.isce2_base.load_or_init_sbatch_options",
                   return_value={"default": {}, "17": {}}), \
             patch("subprocess.run") as mock_subproc:
            mock_subproc.return_value.returncode = 0
            mock_subproc.return_value.stdout = "12345"
            analyzer.submit_hpc()

        sbatch_script = analyzer.mintpy_dir / "mintpy_sbas.sbatch"
        body = sbatch_script.read_text()
        self.assertTrue("apptainer" in body or "docker" in body)
        self.assertIn("insarhub analyzer -N Hyp3_SBAS", body)

    def test_submit_hpc_body_unwrapped_without_container(self):
        analyzer = self._make_analyzer(None)
        with patch("insarhub.processor.isce2_base.load_or_init_sbatch_options",
                   return_value={"default": {}, "17": {}}), \
             patch("subprocess.run") as mock_subproc:
            mock_subproc.return_value.returncode = 0
            mock_subproc.return_value.stdout = "12345"
            analyzer.submit_hpc()

        sbatch_script = analyzer.mintpy_dir / "mintpy_sbas.sbatch"
        body = sbatch_script.read_text()
        self.assertNotIn("apptainer", body)
        self.assertNotIn("docker run", body)
        self.assertIn("insarhub analyzer -N Hyp3_SBAS", body)


if __name__ == "__main__":
    unittest.main()
