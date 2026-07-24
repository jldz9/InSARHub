"""
Tests for insarhub.processor.gmtsar_s1 — unit tests, no real GMTSAR install.

Run: pytest test/test_gmtsar_s1.py -v

Mocks:
  - subprocess.run (no real p2p_processing / p2p_S1_TOPS_Frame / pop_config)
  - .SAFE measurement/annotation trees are built as tmp_path fixtures, not
    real Sentinel-1 products (only the filename shape GMTSAR_S1 parses
    matters for these tests)

Covers:
  - GMTSAR_S1 registered in the processor registry
  - pairs must be 4-tuples (ref_safe, ref_eof, sec_safe, sec_eof) for both
    frame_mode settings (see gmtsar_s1.py module docstring, STATUS v2)
  - _pair_key() format
  - _extract_subswath_stem(): finds the configured IW subswath + polarization,
    symlinks .tiff/.xml/.EOF under GMTSAR's required matching-stem naming;
    raises FileNotFoundError when the subswath/pol isn't present
  - _build_cmd(): produces the real p2p_processing / p2p_S1_TOPS_Frame
    command lines (checked directly against
    gmtsar/python/tests/recipes/README_S1_Ridgecrest_EQ.txt during real
    end-to-end validation, see docs/gmtsar_s1_notes/OPEN_ISSUES.md)
  - _status_dir()/_read_status(): SUCCEEDED/FAILED/RUNNING/PENDING via
    marker files
  - _subprocess_env(): prepends gmtsar_env_bin + gmtsar_root/bin to PATH
  - submit()/refresh() with subprocess.run mocked to a no-op success
"""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


def _wait_submit(proc, timeout=5.0):
    deadline = time.monotonic() + timeout
    while proc._thread and proc._thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.05)


# ===========================================================================
# Fixtures
# ===========================================================================

REF_SAFE = "S1A_IW_SLC__1SDV_20190704T135158_20190704T135225_027968_032877_1C4D.SAFE"
REF_EOF = "S1A_OPER_AUX_RESORB_OPOD_20190704T152016_V20190704T113336_20190704T145106.EOF"
SEC_SAFE = "S1A_IW_SLC__1SDV_20190716T135159_20190716T135226_028143_032DC3_512B.SAFE"
SEC_EOF = "S1A_OPER_AUX_RESORB_OPOD_20190716T165508_V20190716T113337_20190716T145107.EOF"

REF_STEM_IW2 = "s1a-iw2-slc-vv-20190704t135158-20190704t135223-027968-032877-005"
SEC_STEM_IW2 = "s1a-iw2-slc-vv-20190716t135159-20190716t135224-028143-032dc3-005"


def _make_fake_safe(base: Path, safe_name: str, stem: str) -> None:
    """Build the minimal .SAFE/measurement + .SAFE/annotation shape
    _extract_subswath_stem() globs against -- real Sentinel-1 products
    have this same layout (confirmed against real S1_Ridgecrest_EQ test
    data, see docs/gmtsar_s1_notes/OPEN_ISSUES.md)."""
    safe_dir = base / safe_name
    (safe_dir / "measurement").mkdir(parents=True)
    (safe_dir / "annotation").mkdir(parents=True)
    (safe_dir / "measurement" / f"{stem}.tiff").write_bytes(b"fake-tiff")
    (safe_dir / "annotation" / f"{stem}.xml").write_text("<xml/>")


def _make_fake_scene(tmp_path: Path) -> Path:
    """Populate a slc_dir/orbit_dir fixture with both real-shaped .SAFE
    scenes (IW2/vv only) plus their .EOF files, matching the real
    S1_Ridgecrest_EQ fixture layout used in the real end-to-end run."""
    raw = tmp_path / "raw"
    raw.mkdir()
    _make_fake_safe(raw, REF_SAFE, REF_STEM_IW2)
    _make_fake_safe(raw, SEC_SAFE, SEC_STEM_IW2)
    (raw / REF_EOF).write_text("fake-orbit")
    (raw / SEC_EOF).write_text("fake-orbit")
    return raw


def _make_gmtsar_s1(tmp_path: Path, frame_mode: bool = False, **cfg_kwargs):
    from insarhub.config import GMTSAR_S1_Config
    from insarhub.processor.gmtsar_s1 import GMTSAR_S1

    raw = _make_fake_scene(tmp_path)
    dem = tmp_path / "dem.grd"
    dem.write_bytes(b"fake-dem")

    cfg = GMTSAR_S1_Config(
        workdir=str(tmp_path / "work"),
        slc_dir=str(raw),
        orbit_dir=str(raw),
        dem_path=str(dem),
        frame_mode=frame_mode,
        gmtsar_root="/opt/gmtsar",
        gmtsar_env_bin="/opt/conda/envs/gmtsar/bin",
        **cfg_kwargs,
    )
    pairs = [(REF_SAFE, REF_EOF, SEC_SAFE, SEC_EOF)]
    return GMTSAR_S1(pairs=pairs, config=cfg)


# ===========================================================================
# Registry
# ===========================================================================

class TestGMTSARS1Registry:
    def test_registered(self):
        from insarhub import Processor
        assert "GMTSAR_S1" in Processor.available()


# ===========================================================================
# pairs validation
# ===========================================================================

class TestGMTSARS1PairsValidation:
    def test_rejects_empty_pairs(self):
        from insarhub.config import GMTSAR_S1_Config
        from insarhub.processor.gmtsar_s1 import GMTSAR_S1
        with pytest.raises(ValueError):
            GMTSAR_S1(pairs=[], config=GMTSAR_S1_Config())

    def test_rejects_wrong_arity(self):
        from insarhub.config import GMTSAR_S1_Config
        from insarhub.processor.gmtsar_s1 import GMTSAR_S1
        with pytest.raises(ValueError):
            GMTSAR_S1(pairs=[(REF_SAFE, SEC_SAFE)], config=GMTSAR_S1_Config())

    def test_accepts_4tuple_frame_mode_false(self, tmp_path):
        proc = _make_gmtsar_s1(tmp_path, frame_mode=False)
        assert proc.pairs == [(REF_SAFE, REF_EOF, SEC_SAFE, SEC_EOF)]

    def test_accepts_4tuple_frame_mode_true(self, tmp_path):
        proc = _make_gmtsar_s1(tmp_path, frame_mode=True)
        assert proc.pairs == [(REF_SAFE, REF_EOF, SEC_SAFE, SEC_EOF)]


# ===========================================================================
# _pair_key
# ===========================================================================

class TestPairKey:
    def test_pair_key_uses_safe_names(self):
        from insarhub.processor.gmtsar_s1 import _pair_key
        key = _pair_key((REF_SAFE, REF_EOF, SEC_SAFE, SEC_EOF))
        assert key == f"{REF_SAFE}_{SEC_SAFE}"


# ===========================================================================
# _extract_subswath_stem
# ===========================================================================

class TestExtractSubswathStem:
    def test_extracts_correct_stem_and_symlinks(self, tmp_path):
        proc = _make_gmtsar_s1(tmp_path, frame_mode=False, subswath=2, polarization="vv")
        raw_dir = tmp_path / "case_raw"
        raw_dir.mkdir()
        stem = proc._extract_subswath_stem(REF_SAFE, REF_EOF, raw_dir)
        assert stem == REF_STEM_IW2
        assert (raw_dir / f"{stem}.tiff").is_symlink()
        assert (raw_dir / f"{stem}.xml").is_symlink()
        assert (raw_dir / f"{stem}.EOF").is_symlink()
        assert (raw_dir / f"{stem}.tiff").resolve().read_bytes() == b"fake-tiff"

    def test_missing_subswath_raises(self, tmp_path):
        proc = _make_gmtsar_s1(tmp_path, frame_mode=False, subswath=1, polarization="vv")
        raw_dir = tmp_path / "case_raw"
        raw_dir.mkdir()
        # Fixture only has IW2 -- IW1 should not be found.
        with pytest.raises(FileNotFoundError):
            proc._extract_subswath_stem(REF_SAFE, REF_EOF, raw_dir)

    def test_missing_polarization_raises(self, tmp_path):
        proc = _make_gmtsar_s1(tmp_path, frame_mode=False, subswath=2, polarization="vh")
        raw_dir = tmp_path / "case_raw"
        raw_dir.mkdir()
        with pytest.raises(FileNotFoundError):
            proc._extract_subswath_stem(REF_SAFE, REF_EOF, raw_dir)


# ===========================================================================
# _build_cmd
# ===========================================================================

class TestBuildCmd:
    def test_single_subswath_cmd_matches_real_recipe(self, tmp_path):
        # Real recipe (README_S1_Ridgecrest_EQ.txt):
        #   p2p_processing S1_TOPS <ref_stem> <sec_stem> config.py
        from insarhub.processor.gmtsar_s1 import _pair_key
        proc = _make_gmtsar_s1(tmp_path, frame_mode=False, subswath=2, polarization="vv")
        pair = proc.pairs[0]
        proc._stems[_pair_key(pair)] = (REF_STEM_IW2, SEC_STEM_IW2)
        cmd = proc._build_cmd(pair)
        assert cmd == ["p2p_processing", "S1_TOPS", REF_STEM_IW2, SEC_STEM_IW2, "config.py"]

    def test_frame_mode_cmd_matches_real_recipe(self, tmp_path):
        # Real recipe (README_S1A_SLC_TOPS_LA.txt):
        #   p2p_S1_TOPS_Frame ref.SAFE ref.EOF sec.SAFE sec.EOF config.py <pol> <parallel>
        proc = _make_gmtsar_s1(tmp_path, frame_mode=True, polarization="vv", parallel=True)
        pair = proc.pairs[0]
        cmd = proc._build_cmd(pair)
        assert cmd == [
            "p2p_S1_TOPS_Frame", REF_SAFE, REF_EOF, SEC_SAFE, SEC_EOF,
            "config.py", "vv", "1",
        ]

    def test_frame_mode_cmd_respects_parallel_flag(self, tmp_path):
        proc = _make_gmtsar_s1(tmp_path, frame_mode=True, parallel=False)
        cmd = proc._build_cmd(proc.pairs[0])
        assert cmd[-1] == "0"


# ===========================================================================
# status markers
# ===========================================================================

class TestStatusMarkers:
    def test_read_status_pending_when_dir_absent(self, tmp_path):
        from insarhub.processor.gmtsar_s1 import _read_status, _PENDING
        assert _read_status(tmp_path / "does_not_exist") == _PENDING

    def test_read_status_running_when_dir_exists_no_marker(self, tmp_path):
        from insarhub.processor.gmtsar_s1 import _read_status, _RUNNING
        d = tmp_path / "run"
        d.mkdir()
        assert _read_status(d) == _RUNNING

    def test_write_then_read_succeeded(self, tmp_path):
        from insarhub.processor.gmtsar_s1 import _write_status, _read_status, _SUCCEEDED
        d = tmp_path / "run"
        _write_status(d, _SUCCEEDED)
        assert _read_status(d) == _SUCCEEDED

    def test_write_then_read_failed(self, tmp_path):
        from insarhub.processor.gmtsar_s1 import _write_status, _read_status, _FAILED
        d = tmp_path / "run"
        _write_status(d, _FAILED)
        assert _read_status(d) == _FAILED

    def test_status_dir_frame_mode_is_merge(self, tmp_path):
        proc = _make_gmtsar_s1(tmp_path, frame_mode=True)
        pair = proc.pairs[0]
        assert proc._status_dir(pair) == proc.pair_case_dir(pair) / "merge"

    def test_status_dir_single_subswath_before_real_run_uses_pending_sentinel(self, tmp_path):
        # Before a real run, GMTSAR's real (Julian-date) output dir isn't
        # known yet -- _status_dir() falls back to a stem-based sentinel
        # that can never collide with a real \d{7}_\d{7} directory.
        from insarhub.processor.gmtsar_s1 import _pair_key
        proc = _make_gmtsar_s1(tmp_path, frame_mode=False)
        pair = proc.pairs[0]
        proc._stems[_pair_key(pair)] = (REF_STEM_IW2, SEC_STEM_IW2)
        expected = proc.case_dir / "intf" / f"_pending_{REF_STEM_IW2}_{SEC_STEM_IW2}"
        assert proc._status_dir(pair) == expected

    def test_status_dir_single_subswath_uses_real_julian_date_dir_post_run(self, tmp_path):
        # Real bug found via MintPy integration testing: GMTSAR names its
        # real output dir by Julian date (e.g. 2019184_2019196), not
        # ref_stem_sec_stem. _real_intf_dirs, populated post-run by
        # _run_one_pair(), must take priority once known.
        from insarhub.processor.gmtsar_s1 import _pair_key
        proc = _make_gmtsar_s1(tmp_path, frame_mode=False)
        pair = proc.pairs[0]
        proc._stems[_pair_key(pair)] = (REF_STEM_IW2, SEC_STEM_IW2)
        proc._real_intf_dirs[_pair_key(pair)] = "2019184_2019196"
        expected = proc.case_dir / "intf" / "2019184_2019196"
        assert proc._status_dir(pair) == expected


# ===========================================================================
# _subprocess_env
# ===========================================================================

class TestSubprocessEnv:
    def test_prepends_env_bin_and_gmtsar_root_bin(self, tmp_path):
        proc = _make_gmtsar_s1(tmp_path, frame_mode=False)
        env = proc._subprocess_env()
        assert env["GMTSAR"] == "/opt/gmtsar"
        path_entries = env["PATH"].split(":")
        assert path_entries[0] == "/opt/conda/envs/gmtsar/bin"
        assert path_entries[1] == "/opt/gmtsar/bin"


# ===========================================================================
# submit() with subprocess mocked
# ===========================================================================

class TestSubmit:
    def test_submit_single_subswath_extracts_and_runs(self, tmp_path):
        proc = _make_gmtsar_s1(tmp_path, frame_mode=False, subswath=2, polarization="vv")

        def fake_run(cmd, cwd, env, **kwargs):
            # Simulate p2p_processing succeeding by creating GMTSAR's
            # real Julian-date output dir (not ref_stem_sec_stem -- see
            # _run_one_pair()'s docstring), exercising the real
            # before/after-diff discovery logic in _run_one_pair().
            if cmd[0] == "p2p_processing":
                real_dir = proc.case_dir / "intf" / "2019184_2019196"
                real_dir.mkdir(parents=True, exist_ok=True)
            (Path(cwd) / "config.py").touch(exist_ok=True)
            return MagicMock(returncode=0)

        with patch("subprocess.run", side_effect=fake_run) as mock_run:
            proc.submit()
            _wait_submit(proc)

        assert proc._real_intf_dirs[list(proc.jobs.keys())[0]] == "2019184_2019196"
        assert (proc.case_dir / "intf" / "2019184_2019196" / ".succeeded").exists()

        # pop_config + p2p_processing should both have been invoked.
        assert mock_run.called
        cmds = [c.args[0] if c.args else c.kwargs.get("cmd") for c in mock_run.call_args_list]
        assert any(cmd[0] == "p2p_processing" for cmd in cmds if cmd)

        key = list(proc.jobs.keys())[0]
        assert proc.jobs[key]["status"] == "SUCCEEDED"

    def test_submit_dry_run_does_not_call_subprocess(self, tmp_path):
        proc = _make_gmtsar_s1(tmp_path, frame_mode=False, dry_run=True)
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            proc.submit()
        # pop_config still runs during staging (not gated by dry_run); the
        # per-pair p2p_processing call must not.
        cmds = [c.args[0] if c.args else c.kwargs.get("cmd") for c in mock_run.call_args_list]
        assert not any(cmd[0] == "p2p_processing" for cmd in cmds if cmd)

    def test_submit_nonzero_returncode_marks_failed(self, tmp_path):
        # Audit finding: the FAILED path (returncode != 0) had zero test
        # coverage -- a mutation flipping `proc.returncode == 0` to always
        # True passed the whole suite. p2p_processing genuinely fails this
        # way on bad input (nonzero exit, no status marker written).
        proc = _make_gmtsar_s1(tmp_path, frame_mode=False, subswath=2, polarization="vv")

        def fake_run(cmd, cwd, env, **kwargs):
            (Path(cwd) / "config.py").touch(exist_ok=True)
            if cmd[0] == "p2p_processing":
                return MagicMock(returncode=1)
            return MagicMock(returncode=0)

        with patch("subprocess.run", side_effect=fake_run):
            proc.submit()
            _wait_submit(proc)

        key = list(proc.jobs.keys())[0]
        assert proc.jobs[key]["status"] == "FAILED"
        assert (proc._status_dir(proc.pairs[0]) / ".failed").exists()

    def test_submit_frame_mode_stages_and_runs(self, tmp_path):
        # Audit finding: frame_mode=True staging (_symlink_dir_contents)
        # was never driven through submit() in any test.
        proc = _make_gmtsar_s1(tmp_path, frame_mode=True, polarization="vv")

        def fake_run(cmd, cwd, env, **kwargs):
            (Path(cwd) / "config.py").touch(exist_ok=True)
            if cmd[0] == "p2p_S1_TOPS_Frame":
                (Path(cwd) / "merge").mkdir(parents=True, exist_ok=True)
            return MagicMock(returncode=0)

        with patch("subprocess.run", side_effect=fake_run):
            proc.submit()
            _wait_submit(proc)

        pair_dir = proc.pair_case_dir(proc.pairs[0])
        assert (pair_dir / "raw" / REF_EOF).is_symlink()
        assert (pair_dir / "raw" / SEC_EOF).is_symlink()
        assert (pair_dir / "raw" / REF_SAFE).is_symlink()
        key = list(proc.jobs.keys())[0]
        assert proc.jobs[key]["status"] == "SUCCEEDED"
