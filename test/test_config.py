"""
Tests for all config dataclasses in insarhub.config.

Run: pytest test/test_config.py -v

Covers:
  - Default values and types
  - __post_init__ path conversion
  - Field inheritance
  - Hyp3_S1_Config specific fields
  - ISCE2_S1_Config specific fields
"""

import pytest
from pathlib import Path


# ===========================================================================
# ASF_Base_Config
# ===========================================================================

class TestASFBaseConfig:
    def test_defaults_are_none(self):
        from insarhub.config import ASF_Base_Config
        cfg = ASF_Base_Config()
        assert cfg.dataset is None
        assert cfg.platform is None
        assert cfg.intersectsWith is None
        assert cfg.relativeOrbit is None

    def test_name_field(self):
        from insarhub.config import ASF_Base_Config
        cfg = ASF_Base_Config()
        assert cfg.name == "ASF_Base_Config"

    def test_custom_values(self):
        from insarhub.config import ASF_Base_Config
        cfg = ASF_Base_Config(dataset="SENTINEL-1", relativeOrbit=64)
        assert cfg.dataset == "SENTINEL-1"
        assert cfg.relativeOrbit == 64

    def test_intersects_with_accepts_string(self):
        from insarhub.config import ASF_Base_Config
        wkt = "POINT(-120 37)"
        cfg = ASF_Base_Config(intersectsWith=wkt)
        assert cfg.intersectsWith == wkt


# ===========================================================================
# S1_SLC_Config
# ===========================================================================

class TestS1SLCConfig:
    def test_dataset_is_sentinel1(self):
        from insarhub.config import S1_SLC_Config
        cfg = S1_SLC_Config()
        assert cfg.dataset == "SENTINEL-1"

    def test_beam_mode_is_iw(self):
        from insarhub.config import S1_SLC_Config
        cfg = S1_SLC_Config()
        assert cfg.beamMode == "IW"

    def test_processing_level_is_slc(self):
        from insarhub.config import S1_SLC_Config
        cfg = S1_SLC_Config()
        assert cfg.processingLevel == "SLC"

    def test_polarization_is_list(self):
        from insarhub.config import S1_SLC_Config
        cfg = S1_SLC_Config()
        assert isinstance(cfg.polarization, list)
        assert len(cfg.polarization) > 0

    def test_inherits_asf_base(self):
        from insarhub.config import S1_SLC_Config, ASF_Base_Config
        cfg = S1_SLC_Config()
        assert isinstance(cfg, ASF_Base_Config)

    def test_override_relative_orbit(self):
        from insarhub.config import S1_SLC_Config
        cfg = S1_SLC_Config(relativeOrbit=64)
        assert cfg.relativeOrbit == 64
        assert cfg.dataset == "SENTINEL-1"  # inherited default preserved


# ===========================================================================
# Hyp3_Base_Config
# ===========================================================================

class TestHyp3BaseConfig:
    def test_workdir_default_is_cwd(self):
        from insarhub.config import Hyp3_Base_Config
        cfg = Hyp3_Base_Config()
        assert isinstance(cfg.workdir, Path)

    def test_string_workdir_converted_to_path(self):
        from insarhub.config import Hyp3_Base_Config
        cfg = Hyp3_Base_Config(workdir="/tmp/test_hyp3")
        assert isinstance(cfg.workdir, Path)
        assert cfg.workdir == Path("/tmp/test_hyp3")

    def test_saved_job_path_string_to_path(self):
        from insarhub.config import Hyp3_Base_Config
        cfg = Hyp3_Base_Config(saved_job_path="/tmp/jobs.json")
        assert isinstance(cfg.saved_job_path, Path)

    def test_saved_job_path_none_stays_none(self):
        from insarhub.config import Hyp3_Base_Config
        cfg = Hyp3_Base_Config()
        assert cfg.saved_job_path is None

    def test_default_skip_existing_true(self):
        from insarhub.config import Hyp3_Base_Config
        cfg = Hyp3_Base_Config()
        assert cfg.skip_existing is True

    def test_default_max_workers(self):
        from insarhub.config import Hyp3_Base_Config
        cfg = Hyp3_Base_Config()
        assert cfg.max_workers == 4

    def test_default_submission_chunk_size(self):
        from insarhub.config import Hyp3_Base_Config
        cfg = Hyp3_Base_Config()
        assert cfg.submission_chunk_size == 200

    def test_earthdata_pool_default_none(self):
        from insarhub.config import Hyp3_Base_Config
        cfg = Hyp3_Base_Config()
        assert cfg.earthdata_credentials_pool is None


# ===========================================================================
# Hyp3_S1_Config
# ===========================================================================

class TestHyp3S1Config:
    def test_looks_valid(self):
        from insarhub.config import Hyp3_S1_Config
        cfg = Hyp3_S1_Config()
        assert cfg.looks in ("20x4", "10x2", "5x1")

    def test_inherits_hyp3_base(self):
        from insarhub.config import Hyp3_S1_Config, Hyp3_Base_Config
        cfg = Hyp3_S1_Config()
        assert isinstance(cfg, Hyp3_Base_Config)

    def test_workdir_converts_to_path(self):
        from insarhub.config import Hyp3_S1_Config
        cfg = Hyp3_S1_Config(workdir="~/tmp")
        assert isinstance(cfg.workdir, Path)

    def test_pairs_default(self):
        from insarhub.config import Hyp3_S1_Config
        cfg = Hyp3_S1_Config()
        assert cfg.pairs is None or isinstance(cfg.pairs, (list, type(None)))


# ===========================================================================
# Hyp3_SBAS_Config
# ===========================================================================

class TestHyp3SBASConfig:
    def test_has_network_coherence_field(self):
        from insarhub.config import Hyp3_SBAS_Config
        cfg = Hyp3_SBAS_Config()
        assert hasattr(cfg, "network_coherenceBased")

    def test_inherits_mintpy_base(self):
        from insarhub.config import Hyp3_SBAS_Config, Mintpy_SBAS_Base_Config
        cfg = Hyp3_SBAS_Config()
        assert isinstance(cfg, Mintpy_SBAS_Base_Config)


# ===========================================================================
# ISCE2_S1_Config
# ===========================================================================

class TestISCES1Config:
    def test_workdir_converts_to_path(self):
        from insarhub.config import ISCE2_S1_Config
        cfg = ISCE2_S1_Config(workdir="/tmp/isce_test")
        assert isinstance(cfg.workdir, Path)

    def test_hpc_mode_default_false(self):
        from insarhub.config import ISCE2_S1_Config
        cfg = ISCE2_S1_Config()
        assert cfg.hpc_mode is False

    def test_max_workers_positive(self):
        from insarhub.config import ISCE2_S1_Config
        cfg = ISCE2_S1_Config()
        assert cfg.max_workers >= 1

    def test_slc_dir_default_none(self):
        from insarhub.config import ISCE2_S1_Config
        cfg = ISCE2_S1_Config()
        assert cfg.slc_dir is None or isinstance(cfg.slc_dir, (Path, type(None)))


# ===========================================================================
# Mintpy_SBAS_Base_Config
# ===========================================================================

class TestMintpySBASBaseConfig:
    def test_has_load_processor_field(self):
        from insarhub.config import Mintpy_SBAS_Base_Config
        cfg = Mintpy_SBAS_Base_Config()
        assert hasattr(cfg, "load_processor")

    def test_workdir_default_is_path(self):
        from insarhub.config import Mintpy_SBAS_Base_Config
        cfg = Mintpy_SBAS_Base_Config()
        assert isinstance(cfg.workdir, Path)
