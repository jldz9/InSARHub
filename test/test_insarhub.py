"""
InSARHub quick-test suite
Run with:  pytest test/test_insarhub.py -v
           pytest test/test_insarhub.py -v -k "cli"      # CLI only
           pytest test/test_insarhub.py -v -k "api"      # API only
           pytest test/test_insarhub.py -v -k "downloader"
"""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _insarhub_exe():
    """Locate the `insarhub` console script.

    Prefer the script installed next to the interpreter running the tests --
    in an editable/dev env it exists there but the env's bin dir is often not
    on the subprocess PATH -- then fall back to a PATH lookup (covers the CI
    wheel install, incl. Windows' insarhub.exe), then to the bare name.
    """
    for cand in (Path(sys.executable).with_name("insarhub"),
                 Path(sys.executable).with_name("insarhub.exe")):
        if cand.exists():
            return str(cand)
    return shutil.which("insarhub") or "insarhub"


def run_cli(*args, expect_error=False):
    """Run insarhub CLI and return (returncode, stdout, stderr)."""
    result = subprocess.run(
        [_insarhub_exe(), *args],
        capture_output=True, text=True
    )
    if not expect_error:
        assert result.returncode == 0, (
            f"CLI failed: insarhub {' '.join(args)}\n"
            f"STDOUT: {result.stdout}\nSTDERR: {result.stderr}"
        )
    return result.returncode, result.stdout, result.stderr


# ===========================================================================
# 1. IMPORTS & VERSION
# ===========================================================================

class TestImports:
    def test_import_insarhub(self):
        import insarhub
        assert hasattr(insarhub, "__version__")

    def test_import_downloader(self):
        from insarhub import Downloader
        assert Downloader is not None

    def test_import_processor(self):
        from insarhub import Processor
        assert Processor is not None

    def test_import_analyzer(self):
        from insarhub import Analyzer
        assert Analyzer is not None

    def test_version_string(self):
        import insarhub
        parts = insarhub.__version__.replace(".post", ".").split(".")
        assert len(parts) >= 3


# ===========================================================================
# 2. REGISTRY
# ===========================================================================

class TestRegistry:
    def test_downloader_registry(self):
        from insarhub import Downloader
        available = Downloader.available()
        assert "S1_SLC" in available

    def test_processor_registry(self):
        from insarhub import Processor
        available = Processor.available()
        assert "Hyp3_S1" in available

    def test_analyzer_registry(self):
        from insarhub import Analyzer
        available = Analyzer.available()
        assert "Hyp3_Mintpy_SBAS" in available

    def test_create_downloader(self):
        from insarhub import Downloader
        d = Downloader.create("S1_SLC", intersectsWith="POINT(0 0)")
        assert d is not None

    def test_create_analyzer(self):
        from insarhub import Analyzer
        a = Analyzer.create("Hyp3_Mintpy_SBAS", workdir="/tmp/test_insarhub")
        assert a is not None


# ===========================================================================
# 3. CONFIG CLASSES
# ===========================================================================

class TestConfigs:
    def test_s1_slc_config_defaults(self):
        from insarhub.config import S1_SLC_Config
        cfg = S1_SLC_Config()
        assert cfg.dataset == "SENTINEL-1"

    def test_hyp3_s1_config_defaults(self):
        from insarhub.config import Hyp3_S1_Config
        cfg = Hyp3_S1_Config()
        assert cfg.looks in ("20x4", "10x2", "5x1")

    def test_hyp3_sbas_config_defaults(self):
        from insarhub.config import Hyp3_Mintpy_SBAS_Config
        cfg = Hyp3_Mintpy_SBAS_Config()
        assert hasattr(cfg, "network_coherenceBased")

    def test_mintpy_base_config_defaults(self):
        from insarhub.config import Mintpy_SBAS_Base_Config
        cfg = Mintpy_SBAS_Base_Config()
        assert hasattr(cfg, "load_processor")


# ===========================================================================
# 4. DOWNLOADER (unit, no network)
# ===========================================================================

class TestDownloader:
    def test_s1_slc_instantiation(self):
        from insarhub import Downloader
        d = Downloader.create("S1_SLC", intersectsWith="POINT(-120 37)")
        assert hasattr(d, "search")
        assert hasattr(d, "filter")
        assert hasattr(d, "download")
        assert hasattr(d, "download")

    def test_filter_requires_search_first(self):
        from insarhub import Downloader
        d = Downloader.create("S1_SLC", intersectsWith="POINT(-120 37)")
        # filter before search raises ValueError — that is expected behaviour
        with pytest.raises((ValueError, AttributeError)):
            d.filter(relativeOrbit=100)

    def test_select_pairs_utility(self):
        """select_pairs is a pure function — test with mock ASFSearchResults structure."""
        from insarhub.utils.tool import select_pairs
        # Minimal smoke test — just confirm it's importable and callable
        assert callable(select_pairs)

    def test_orbit_skip_logic(self):
        """EOF validity window parsing used in download_orbit skip check."""
        from pathlib import Path
        # Simulate EOF filename parsing: V{valid_start}_{valid_end}
        eof_name = "S1A_OPER_AUX_POEORB_OPOD_20241209T070604_V20241118T225942_20241120T005942.EOF"
        stem = Path(eof_name).stem
        parts = stem.split("_V")
        assert len(parts) == 2
        validity = parts[1].split("_")
        assert len(validity) == 2
        valid_start, valid_end = validity
        acq_time = "20241119T143616"
        assert valid_start <= acq_time <= valid_end


# ===========================================================================
# 5. ANALYZER (unit, no processing)
# ===========================================================================

class TestAnalyzer:
    def test_hyp3_sbas_instantiation(self):
        from insarhub import Analyzer
        a = Analyzer.create("Hyp3_Mintpy_SBAS", workdir="/tmp/test_insarhub")
        assert hasattr(a, "prep_data")
        assert hasattr(a, "run")
        assert hasattr(a, "cleanup")

    def test_mintpy_base_instantiation(self):
        from insarhub import Analyzer
        a = Analyzer.create("Hyp3_Mintpy_SBAS", workdir="/tmp/test_insarhub")
        assert a is not None


# ===========================================================================
# 6. UTILITIES
# ===========================================================================

class TestUtils:
    def test_write_workflow_marker(self, tmp_path):
        from insarhub.utils.tool import write_workflow_marker, _CONFIG_FILE
        write_workflow_marker(tmp_path, downloader="S1_SLC")
        marker = tmp_path / _CONFIG_FILE
        assert marker.exists()
        import json
        data = json.loads(marker.read_text())
        assert data.get("downloader", {}).get("type") == "S1_SLC"

    def test_plot_pair_network_importable(self):
        from insarhub.utils.tool import plot_pair_network
        assert callable(plot_pair_network)

    def test_h5_to_raster_importable(self):
        from insarhub.utils.postprocess import h5_to_raster
        assert callable(h5_to_raster)

    def test_save_footprint_importable(self):
        from insarhub.utils.postprocess import save_footprint
        assert callable(save_footprint)

    def test_clip_hyp3_s1_importable(self):
        from insarhub.utils.tool import clip_hyp3_s1
        assert callable(clip_hyp3_s1)


# ===========================================================================
# 7. COMMANDS LAYER
# ===========================================================================

class TestCommands:
    def test_search_command_importable(self):
        from insarhub.commands import SearchCommand
        assert SearchCommand is not None

    def test_filter_command_importable(self):
        from insarhub.commands import FilterCommand
        assert FilterCommand is not None

    def test_download_scenes_command_importable(self):
        from insarhub.commands import DownloadScenesCommand
        assert DownloadScenesCommand is not None

    def test_submit_command_importable(self):
        from insarhub.commands import SubmitCommand
        assert SubmitCommand is not None

    def test_analyze_command_importable(self):
        from insarhub.commands import AnalyzeCommand
        assert AnalyzeCommand is not None


# ===========================================================================
# 8. CLI — basic flags (no network)
# ===========================================================================

class TestCLI:
    def test_version(self):
        _, out, _ = run_cli("--version")
        assert out.strip() != ""

    def test_help(self):
        rc, out, _ = run_cli("--help")
        assert rc == 0
        assert "downloader" in out.lower() or "usage" in out.lower()

    def test_downloader_help(self):
        rc, out, _ = run_cli("downloader", "--help")
        assert rc == 0

    def test_downloader_list(self):
        _, out, _ = run_cli("downloader", "--list-downloaders")
        assert "S1_SLC" in out

    def test_downloader_list_options(self):
        _, out, _ = run_cli("downloader", "--list-options")
        assert out.strip() != ""

    def test_processor_help(self):
        rc, out, _ = run_cli("processor", "--help")
        assert rc == 0

    def test_processor_list(self):
        _, out, _ = run_cli("processor", "--list-processors")
        assert "Hyp3_S1" in out

    def test_analyzer_help(self):
        rc, out, _ = run_cli("analyzer", "--help")
        assert rc == 0

    def test_analyzer_list(self):
        _, out, _ = run_cli("analyzer", "--list-analyzers")
        assert "Hyp3_Mintpy_SBAS" in out

    def test_utils_help(self):
        rc, out, _ = run_cli("utils", "--help")
        assert rc == 0

    def test_invalid_command(self):
        rc, _, err = run_cli("nonexistent_command", expect_error=True)
        assert rc != 0

    def test_downloader_invalid_stack_token(self):
        """--stacks with bad token should exit with error."""
        rc, _, err = run_cli(
            "downloader", "--AOI", "0", "0", "1", "1",
            "--stacks", "BADTOKEN",
            expect_error=True
        )
        assert rc != 0

    def test_insarhub_app_help(self):
        for cand in (Path(sys.executable).with_name("insarhub-app"),
                     Path(sys.executable).with_name("insarhub-app.exe")):
            if cand.exists():
                exe = str(cand)
                break
        else:
            exe = shutil.which("insarhub-app") or "insarhub-app"
        result = subprocess.run(
            [exe, "--help"],
            capture_output=True, text=True
        )
        assert result.returncode == 0


# ===========================================================================
# 9. FASTAPI APP (no real jobs)
# ===========================================================================

class TestAPI:
    @pytest.fixture(scope="class")
    def client(self):
        from fastapi.testclient import TestClient
        from insarhub.app.api import app
        return TestClient(app)

    def test_health(self, client):
        r = client.get("/api/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_settings_get(self, client):
        r = client.get("/api/settings")
        assert r.status_code == 200
        data = r.json()
        assert "workdir" in data

    def test_settings_patch(self, client):
        r = client.patch("/api/settings", json={"max_download_workers": 4})
        assert r.status_code == 200

    def test_workflows(self, client):
        r = client.get("/api/workflows")
        assert r.status_code == 200

    def test_job_folders(self, client):
        r = client.get("/api/job-folders", params={"path": "/tmp"})
        assert r.status_code == 200

    def test_analyzer_steps(self, client):
        r = client.get("/api/analyzer-steps", params={"analyzer_type": "Hyp3_Mintpy_SBAS"})
        assert r.status_code == 200
        data = r.json()
        assert "steps" in data

    def test_workdir(self, client):
        r = client.get("/api/workdir")
        assert r.status_code == 200

    def test_auth_status(self, client):
        r = client.get("/api/auth-status")
        assert r.status_code == 200

    def test_frontend_served(self, client):
        """Index.html should be served at root if frontend is built."""
        r = client.get("/")
        # Either 200 (frontend built) or 404 (dev mode without build) is acceptable
        assert r.status_code in (200, 404)

    def test_unknown_job_status(self, client):
        r = client.get("/api/jobs/nonexistent-job-id")
        assert r.status_code == 404

    def test_stop_unknown_job(self, client):
        r = client.post("/api/jobs/nonexistent-job-id/stop")
        assert r.status_code in (200, 404)


# ===========================================================================
# 10. STACKS DEDUP (regression for duplicate download bug)
# ===========================================================================

class TestStacksDedup:
    def test_dedup_same_orbit(self):
        """--stacks 28:107 28:112 must not produce duplicate relativeOrbit entries."""
        tokens = ["28:107", "28:112"]
        parsed = []
        for token in tokens:
            parts = token.split(":")
            parsed.append((int(parts[0]), int(parts[1])))
        orbits = list(dict.fromkeys(p for p, _ in parsed))
        frames = list(dict.fromkeys(f for _, f in parsed))
        assert orbits == [28]
        assert frames == [107, 112]

    def test_dedup_different_orbits(self):
        tokens = ["28:107", "93:116"]
        parsed = [(int(t.split(":")[0]), int(t.split(":")[1])) for t in tokens]
        orbits = list(dict.fromkeys(p for p, _ in parsed))
        frames = list(dict.fromkeys(f for _, f in parsed))
        assert orbits == [28, 93]
        assert frames == [107, 116]


# ===========================================================================
# 11. BURST STACK SELECTION (--stacks PATH:BURST_ID)
# ===========================================================================

class TestBurstStackSelection:
    """ASF returns no frameNumber on SLC-BURST, so burst stacks key on
    fullBurstID and --stacks selectors are strings, not frame numbers."""

    KEY_IW2 = (124, "124_266256_IW2")
    KEY_IW3 = (124, "124_266256_IW3")
    KEY_PAD = (87, "087_185682_IW2")

    def _match(self, key, selector):
        from insarhub.downloader.s1_burst import S1_Burst
        return S1_Burst._stack_key_matches(S1_Burst, key, (key[0], selector))

    def test_label_is_burst_id_not_frame(self):
        from insarhub.downloader.asf_base import ASF_Base_Downloader
        from insarhub.downloader.s1_burst import S1_Burst
        assert S1_Burst.stack_key_label == "Burst_ID"
        assert ASF_Base_Downloader.stack_key_label == "frame"
        # capitalize() would mangle "Burst_ID" into "Burst_id"
        assert S1_Burst._stack_key_label_title(S1_Burst) == "Burst_ID"
        assert ASF_Base_Downloader._stack_key_label_title(ASF_Base_Downloader) == "Frame"

    def test_product_label_is_per_downloader(self):
        """search() said "Searching for SLCs" whatever the dataset was."""
        from insarhub.downloader.asf_base import ASF_Base_Downloader
        from insarhub.downloader import (S1_SLC, S1_Burst, NISAR_GSLC,
                                         NISAR_RSLC, NISAR_GUNW)
        assert ASF_Base_Downloader.product_label == "products"   # dataset-agnostic base
        assert S1_SLC.product_label      == "SLCs"
        assert S1_Burst.product_label    == "bursts"
        assert NISAR_GSLC.product_label  == "GSLCs"
        assert NISAR_RSLC.product_label  == "RSLCs"
        assert NISAR_GUNW.product_label  == "GUNWs"

    def test_accepts_full_burst_id(self):
        assert self._match(self.KEY_IW2, "124_266256_IW2")
        assert self._match(self.KEY_IW2, "124_266256_iw2")      # case-insensitive

    def test_accepts_index_and_subswath(self):
        assert self._match(self.KEY_IW2, "266256_IW2")
        assert not self._match(self.KEY_IW2, "266256_IW3")

    def test_bare_index_spans_subswaths(self):
        """ASF reuses a burst index across subswaths, so a bare index is
        deliberately one-to-many; naming the subswath pins one stack."""
        assert self._match(self.KEY_IW2, "266256")
        assert self._match(self.KEY_IW3, "266256")
        assert self._match(self.KEY_IW2, 266256)                # int selector

    def test_path_zero_padding_is_ignored(self):
        assert self._match(self.KEY_PAD, "87_185682_IW2")
        assert self._match(self.KEY_PAD, "087_185682_IW2")

    def test_rejects_wrong_stack(self):
        from insarhub.downloader.s1_burst import S1_Burst
        assert not self._match(self.KEY_IW2, "266257")
        assert not self._match(self.KEY_IW2, "")
        # different path, same burst index
        assert not S1_Burst._stack_key_matches(S1_Burst, self.KEY_IW2, (87, "266256"))

    def test_granule_fallback_key_matches_verbatim_only(self):
        """A product with no burst ID groups by granule name; it has no index or
        subswath to compare, so only an exact string can select it."""
        granule = "S1_266256_IW3_20240119T060058_VV_5638-BURST"
        assert self._match((124, granule), granule)
        assert not self._match((124, granule), "266256")

    def test_base_downloader_still_exact_match(self):
        from insarhub.downloader.asf_base import ASF_Base_Downloader as B
        assert B._stack_key_matches(B, (124, 56), (124, 56))
        assert not B._stack_key_matches(B, (124, 56), (124, 57))

    def test_burst_id_token_rejected_by_frame_query(self):
        """S1_Burst must not forward a frame to asf.search(); the CLI relies on
        this to keep burst IDs out of the int-range validator."""
        from insarhub.downloader.s1_burst import S1_Burst
        assert "frame" in S1_Burst._NON_SEARCH_FIELDS
        assert "asfFrame" in S1_Burst._NON_SEARCH_FIELDS

    def test_cli_rejects_non_integer_path(self):
        rc, _, err = run_cli(
            "downloader", "-N", "S1_Burst", "--AOI", "0", "0", "1", "1",
            "--stacks", "abc:124_266256_IW2",
            expect_error=True,
        )
        assert rc != 0

    def test_cli_error_names_burst_id(self):
        rc, out, err = run_cli(
            "downloader", "-N", "S1_Burst", "--AOI", "0", "0", "1", "1",
            "--stacks", "BADTOKEN",
            expect_error=True,
        )
        assert rc != 0
        assert "BURST_ID" in (out + err)


# ===========================================================================
# 12. filter(path_frame=...) END-TO-END, OFFLINE
# ===========================================================================

class _FakeResult:
    def __init__(self, start="2024-01-07T16:12:34.000000Z", **props):
        self.properties = {"startTime": start, "flightDirection": "ASCENDING", **props}


def _offline_downloader(cls):
    """A downloader with a default config and canned results, no auth or network."""
    dl = object.__new__(cls)
    dl.config = cls.default_config()
    dl._subset = None
    return dl


class TestFilterByStackKey:
    def _burst(self):
        from insarhub.downloader.s1_burst import S1_Burst
        dl = _offline_downloader(S1_Burst)
        dl.results = {
            (124, "124_266256_IW2"): [_FakeResult()],
            (124, "124_266256_IW3"): [_FakeResult()],
            (87,  "087_185682_IW2"): [_FakeResult()],
        }
        return dl

    def test_burst_full_id_selects_one_stack(self):
        dl = self._burst()
        dl.filter(path_frame=[(124, "124_266256_IW2")])
        assert set(dl.active_results) == {(124, "124_266256_IW2")}

    def test_burst_bare_index_selects_both_subswaths(self):
        dl = self._burst()
        dl.filter(path_frame=[(124, "266256")])
        assert set(dl.active_results) == {(124, "124_266256_IW2"), (124, "124_266256_IW3")}

    def test_burst_multiple_tokens_across_paths(self):
        dl = self._burst()
        dl.filter(path_frame=[(124, "266256_IW3"), (87, "87_185682_IW2")])
        assert set(dl.active_results) == {(124, "124_266256_IW3"), (87, "087_185682_IW2")}

    def test_empty_filter_does_not_fall_back_to_unfiltered(self):
        """filter() used to leave _subset as None on a total miss, so
        active_results silently returned every stack the user just excluded."""
        dl = self._burst()
        assert len(dl.active_results) == 3
        out = dl.filter(path_frame=[(124, "999999_IW2")])
        assert out == {}
        assert dl.active_results == {}, "empty filter must not fall back to all stacks"

    def test_frame_downloader_unaffected(self):
        from insarhub.downloader.s1_slc import S1_SLC
        dl = _offline_downloader(S1_SLC)
        dl.results = {(124, 56): [_FakeResult()], (87, 527): [_FakeResult()]}
        dl.filter(path_frame=[(124, 56)])
        assert set(dl.active_results) == {(124, 56)}
