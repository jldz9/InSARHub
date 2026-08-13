"""
Tests for insarhub.utils.config_io — read/write insarhub_config.json.

Run: pytest test/test_utils_config_io.py -v

Covers:
  - read_insarhub_config: reads new format, legacy format, missing file, corrupt JSON
  - write_insarhub_config: creates, merges, updates timestamp
  - Legacy key rename (Hyp3_InSAR → Hyp3_S1, ISCE_InSAR → ISCE2_S1)
  - String role values promoted to {"type": value} dicts
"""

import json
import pytest
from pathlib import Path


# ===========================================================================
# read_insarhub_config
# ===========================================================================

class TestReadInsarhubConfig:
    def test_returns_empty_dict_for_missing_folder(self, tmp_path):
        from insarhub.utils.config_io import read_insarhub_config
        result = read_insarhub_config(tmp_path / "nonexistent")
        assert result == {}

    def test_reads_new_format(self, tmp_path):
        from insarhub.utils.config_io import read_insarhub_config
        cfg = {"downloader": {"type": "S1_SLC"}, "processor": {"type": "Hyp3_S1"}}
        (tmp_path / "insarhub_config.json").write_text(json.dumps(cfg))
        result = read_insarhub_config(tmp_path)
        assert result["downloader"]["type"] == "S1_SLC"
        assert result["processor"]["type"] == "Hyp3_S1"

    def test_reads_legacy_workflow_file(self, tmp_path):
        from insarhub.utils.config_io import read_insarhub_config
        cfg = {"downloader": {"type": "S1_SLC"}}
        (tmp_path / "insarhub_workflow.json").write_text(json.dumps(cfg))
        result = read_insarhub_config(tmp_path)
        assert result["downloader"]["type"] == "S1_SLC"

    def test_new_config_takes_priority_over_legacy(self, tmp_path):
        from insarhub.utils.config_io import read_insarhub_config
        new_cfg = {"downloader": {"type": "NEW_DOWNLOADER"}}
        old_cfg = {"downloader": {"type": "S1_SLC"}}
        (tmp_path / "insarhub_config.json").write_text(json.dumps(new_cfg))
        (tmp_path / "insarhub_workflow.json").write_text(json.dumps(old_cfg))
        result = read_insarhub_config(tmp_path)
        assert result["downloader"]["type"] == "NEW_DOWNLOADER"

    def test_corrupt_json_returns_empty(self, tmp_path):
        from insarhub.utils.config_io import read_insarhub_config
        (tmp_path / "insarhub_config.json").write_text("{INVALID JSON")
        result = read_insarhub_config(tmp_path)
        assert result == {}

    def test_string_role_promoted_to_dict(self, tmp_path):
        from insarhub.utils.config_io import read_insarhub_config
        cfg = {"downloader": "S1_SLC", "processor": "Hyp3_S1"}
        (tmp_path / "insarhub_config.json").write_text(json.dumps(cfg))
        result = read_insarhub_config(tmp_path)
        assert isinstance(result["downloader"], dict)
        assert result["downloader"]["type"] == "S1_SLC"
        assert isinstance(result["processor"], dict)
        assert result["processor"]["type"] == "Hyp3_S1"

    def test_legacy_hyp3_insar_renamed(self, tmp_path):
        from insarhub.utils.config_io import read_insarhub_config
        cfg = {"processor": {"type": "Hyp3_InSAR"}}
        (tmp_path / "insarhub_config.json").write_text(json.dumps(cfg))
        result = read_insarhub_config(tmp_path)
        assert result["processor"]["type"] == "Hyp3_S1"

    def test_legacy_isce_insar_renamed(self, tmp_path):
        from insarhub.utils.config_io import read_insarhub_config
        cfg = {"processor": {"type": "ISCE_InSAR"}}
        (tmp_path / "insarhub_config.json").write_text(json.dumps(cfg))
        result = read_insarhub_config(tmp_path)
        assert result["processor"]["type"] == "ISCE2_S1"

    def test_legacy_isce_s1_renamed(self, tmp_path):
        from insarhub.utils.config_io import read_insarhub_config
        cfg = {"processor": {"type": "ISCE_S1"}}
        (tmp_path / "insarhub_config.json").write_text(json.dumps(cfg))
        result = read_insarhub_config(tmp_path)
        assert result["processor"]["type"] == "ISCE2_S1"

    def test_unknown_roles_pass_through_unchanged(self, tmp_path):
        from insarhub.utils.config_io import read_insarhub_config
        cfg = {"extra_key": "some_value", "downloader": {"type": "S1_SLC"}}
        (tmp_path / "insarhub_config.json").write_text(json.dumps(cfg))
        result = read_insarhub_config(tmp_path)
        assert result.get("extra_key") == "some_value"


# ===========================================================================
# write_insarhub_config
# ===========================================================================

class TestWriteInsarhubConfig:
    def test_creates_file(self, tmp_path):
        from insarhub.utils.config_io import write_insarhub_config
        write_insarhub_config(tmp_path, {"downloader": {"type": "S1_SLC"}})
        assert (tmp_path / "insarhub_config.json").exists()

    def test_content_is_valid_json(self, tmp_path):
        from insarhub.utils.config_io import write_insarhub_config
        write_insarhub_config(tmp_path, {"processor": {"type": "Hyp3_S1"}})
        data = json.loads((tmp_path / "insarhub_config.json").read_text())
        assert data["processor"]["type"] == "Hyp3_S1"

    def test_merges_with_existing_content(self, tmp_path):
        from insarhub.utils.config_io import write_insarhub_config
        write_insarhub_config(tmp_path, {"downloader": {"type": "S1_SLC"}})
        write_insarhub_config(tmp_path, {"processor": {"type": "Hyp3_S1"}})
        data = json.loads((tmp_path / "insarhub_config.json").read_text())
        assert data["downloader"]["type"] == "S1_SLC"
        assert data["processor"]["type"] == "Hyp3_S1"

    def test_overwrites_existing_key(self, tmp_path):
        from insarhub.utils.config_io import write_insarhub_config
        write_insarhub_config(tmp_path, {"downloader": {"type": "S1_SLC"}})
        write_insarhub_config(tmp_path, {"downloader": {"type": "NEW_DOWNLOADER"}})
        data = json.loads((tmp_path / "insarhub_config.json").read_text())
        assert data["downloader"]["type"] == "NEW_DOWNLOADER"

    def test_has_updated_at_timestamp(self, tmp_path):
        from insarhub.utils.config_io import write_insarhub_config
        write_insarhub_config(tmp_path, {"downloader": {"type": "S1_SLC"}})
        data = json.loads((tmp_path / "insarhub_config.json").read_text())
        assert "updated_at" in data
        assert data["updated_at"].endswith("Z")

    def test_roundtrip_read_write(self, tmp_path):
        from insarhub.utils.config_io import read_insarhub_config, write_insarhub_config
        original = {"downloader": {"type": "S1_SLC"}, "processor": {"type": "Hyp3_S1"}}
        write_insarhub_config(tmp_path, original)
        result = read_insarhub_config(tmp_path)
        assert result["downloader"]["type"] == "S1_SLC"
        assert result["processor"]["type"] == "Hyp3_S1"
