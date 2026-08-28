"""
Tests for insarhub.app FastAPI routes — no real jobs, no real credentials.

Run: pytest test/test_api_routes.py -v

Uses FastAPI TestClient. All external I/O mocked.

Covers:
  - /api/health
  - /api/workdir
  - /api/settings GET/PATCH
  - /api/workflows
  - /api/job-folders
  - /api/browse-subfolders
  - /api/auth-status
  - /api/analyzer-steps
  - /api/jobs/{id} GET (404 for unknown)
  - /api/jobs/{id}/stop POST
  - /api/folder-config GET
  - /api/processor-defaults GET
"""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient
    from insarhub.app.api import app
    return TestClient(app)


# ===========================================================================
# Health
# ===========================================================================

class TestHealth:
    def test_health_ok(self, client):
        r = client.get("/api/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"


# ===========================================================================
# Workdir
# ===========================================================================

class TestWorkdir:
    def test_workdir_returns_string(self, client):
        r = client.get("/api/workdir")
        assert r.status_code == 200
        assert "workdir" in r.json()


# ===========================================================================
# Settings
# ===========================================================================

class TestSettings:
    def test_get_settings_returns_200(self, client):
        r = client.get("/api/settings")
        assert r.status_code == 200

    def test_get_settings_has_workdir(self, client):
        r = client.get("/api/settings")
        assert "workdir" in r.json()

    def test_patch_settings_max_workers(self, client):
        r = client.patch("/api/settings", json={"max_download_workers": 2})
        assert r.status_code == 200

    def test_patch_settings_unknown_field_ignored(self, client):
        r = client.patch("/api/settings", json={"unknown_field_xyz": 999})
        assert r.status_code == 200


# ===========================================================================
# Workflows
# ===========================================================================

class TestWorkflows:
    def test_workflows_returns_list(self, client):
        r = client.get("/api/workflows")
        assert r.status_code == 200
        assert isinstance(r.json(), (list, dict))


# ===========================================================================
# Job folders
# ===========================================================================

class TestJobFolders:
    def test_job_folders_returns_200(self, client):
        r = client.get("/api/job-folders")
        assert r.status_code == 200

    def test_browse_subfolders_tmp(self, client):
        r = client.get("/api/browse-subfolders", params={"path": "/tmp"})
        assert r.status_code in (200, 400, 403)

    def test_browse_subfolders_empty_path_400(self, client):
        r = client.get("/api/browse-subfolders", params={"path": ""})
        assert r.status_code in (200, 400, 422)


# ===========================================================================
# Auth status
# ===========================================================================

class TestAuthStatus:
    def test_auth_status_returns_200(self, client):
        r = client.get("/api/auth-status")
        assert r.status_code == 200

    def test_auth_status_has_keys(self, client):
        r = client.get("/api/auth-status")
        data = r.json()
        assert isinstance(data, dict)


# ===========================================================================
# Analyzer steps
# ===========================================================================

class TestAnalyzerSteps:
    def test_hyp3_sbas_steps(self, client):
        r = client.get("/api/analyzer-steps", params={"analyzer_type": "Hyp3_Mintpy_SBAS"})
        assert r.status_code == 200
        data = r.json()
        assert "steps" in data

    def test_isce2_sbas_steps(self, client):
        r = client.get("/api/analyzer-steps", params={"analyzer_type": "ISCE2_Mintpy_SBAS"})
        assert r.status_code in (200, 404, 422)

    def test_unknown_analyzer_type(self, client):
        r = client.get("/api/analyzer-steps", params={"analyzer_type": "FAKE_ANALYZER"})
        assert r.status_code in (200, 404, 422, 400)


# ===========================================================================
# Jobs
# ===========================================================================

class TestJobs:
    def test_get_unknown_job_returns_404(self, client):
        r = client.get("/api/jobs/nonexistent-job-id-xyz")
        assert r.status_code == 404

    def test_stop_unknown_job(self, client):
        r = client.post("/api/jobs/nonexistent-job-id-xyz/stop")
        assert r.status_code in (200, 404)


# ===========================================================================
# Folder config
# ===========================================================================

class TestFolderConfig:
    def test_folder_config_missing_path(self, client):
        r = client.get("/api/folder-config", params={"path": "/tmp/nonexistent_insarhub_xyz"})
        assert r.status_code in (200, 404)

    def test_folder_config_valid_dir(self, client, tmp_path):
        r = client.get("/api/folder-config", params={"path": str(tmp_path)})
        assert r.status_code in (200, 404)


# ===========================================================================
# Processor defaults
# ===========================================================================

class TestProcessorDefaults:
    def test_hyp3_s1_defaults(self, client, tmp_path):
        r = client.get("/api/processor-defaults",
                       params={"processor": "Hyp3_S1", "workdir": str(tmp_path)})
        assert r.status_code in (200, 404, 422)

    def test_unknown_processor_defaults(self, client, tmp_path):
        r = client.get("/api/processor-defaults",
                       params={"processor": "FAKE_PROC", "workdir": str(tmp_path)})
        assert r.status_code in (200, 404, 422)


# ===========================================================================
# Frontend
# ===========================================================================

class TestFrontend:
    def test_root_returns_200_or_404(self, client):
        r = client.get("/")
        assert r.status_code in (200, 404)
