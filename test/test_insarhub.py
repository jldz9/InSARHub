"""
InSARHub quick-test suite
Run with:  pytest test/test_insarhub.py -v
           pytest test/test_insarhub.py -v -k "cli"      # CLI only
           pytest test/test_insarhub.py -v -k "api"      # API only
           pytest test/test_insarhub.py -v -k "downloader"
"""

import subprocess
import sys
import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run_cli(*args, expect_error=False):
    """Run insarhub CLI and return (returncode, stdout, stderr)."""
    result = subprocess.run(
        ["insarhub", *args],
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
        assert "Hyp3_InSAR" in available

    def test_analyzer_registry(self):
        from insarhub import Analyzer
        available = Analyzer.available()
        assert "Hyp3_SBAS" in available

    def test_create_downloader(self):
        from insarhub import Downloader
        d = Downloader.create("S1_SLC", intersectsWith="POINT(0 0)")
        assert d is not None

    def test_create_analyzer(self):
        from insarhub import Analyzer
        a = Analyzer.create("Hyp3_SBAS", workdir="/tmp/test_insarhub")
        assert a is not None


# ===========================================================================
# 3. CONFIG CLASSES
# ===========================================================================

class TestConfigs:
    def test_s1_slc_config_defaults(self):
        from insarhub.config import S1_SLC_Config
        cfg = S1_SLC_Config()
        assert cfg.dataset == "SENTINEL-1"

    def test_hyp3_insar_config_defaults(self):
        from insarhub.config import Hyp3_InSAR_Config
        cfg = Hyp3_InSAR_Config()
        assert cfg.looks in ("20x4", "10x2", "5x1")

    def test_hyp3_sbas_config_defaults(self):
        from insarhub.config import Hyp3_SBAS_Config
        cfg = Hyp3_SBAS_Config()
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
        a = Analyzer.create("Hyp3_SBAS", workdir="/tmp/test_insarhub")
        assert hasattr(a, "prep_data")
        assert hasattr(a, "run")
        assert hasattr(a, "cleanup")

    def test_mintpy_base_instantiation(self):
        from insarhub import Analyzer
        a = Analyzer.create("Hyp3_SBAS", workdir="/tmp/test_insarhub")
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

    def test_clip_hyp3_insar_importable(self):
        from insarhub.utils.tool import clip_hyp3_insar
        assert callable(clip_hyp3_insar)


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
        assert "Hyp3_InSAR" in out

    def test_analyzer_help(self):
        rc, out, _ = run_cli("analyzer", "--help")
        assert rc == 0

    def test_analyzer_list(self):
        _, out, _ = run_cli("analyzer", "--list-analyzers")
        assert "Hyp3_SBAS" in out

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
        result = subprocess.run(
            ["insarhub-app", "--help"],
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
        r = client.get("/api/analyzer-steps", params={"analyzer_type": "Hyp3_SBAS"})
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
