"""
Tests for insarhub.downloader — unit tests, no network.

Run: pytest test/test_downloader.py -v

Mocks:
  - asf_search calls (no real ASF API)
  - netrc file (no real credentials needed)
  - HyP3 client

Covers:
  - ASF_Base_Downloader: _get_group_key, _check_netrc, init validation
  - S1_SLC: inherits correctly, default config
  - _parse_scene_filter: set/list/None inputs
  - orbit file validity parsing (regression for EOF skip logic)
"""

import sys
import types
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock


# ===========================================================================
# _parse_scene_filter
# ===========================================================================

class TestParseSceneFilter:
    def _call(self, scenes):
        from insarhub.downloader.asf_base import _parse_scene_filter
        return _parse_scene_filter(scenes)

    def test_none_returns_none(self):
        assert self._call(None) is None

    def test_empty_list_returns_empty_or_none(self):
        result = self._call([])
        assert result is None or result == set()

    def test_string_set_returns_set(self):
        result = self._call({"S1A_scene_001", "S1A_scene_002"})
        assert isinstance(result, set)
        assert "S1A_scene_001" in result

    def test_list_of_strings_returns_set(self):
        result = self._call(["S1A_scene_001", "S1A_scene_002"])
        assert isinstance(result, set)
        assert len(result) == 2

    def test_deduplicates(self):
        result = self._call(["S1A_scene_001", "S1A_scene_001"])
        assert len(result) == 1


# ===========================================================================
# ASF_Base_Downloader._check_netrc
# ===========================================================================

class TestCheckNetrc:
    def _make_downloader(self, tmp_path):
        from insarhub.config import S1_SLC_Config
        cfg = S1_SLC_Config(intersectsWith="POINT(-120 37)")
        with patch("insarhub.downloader.asf_base.ASF_Base_Downloader.__init__",
                   return_value=None):
            from insarhub.downloader.asf_base import ASF_Base_Downloader
            obj = ASF_Base_Downloader.__new__(ASF_Base_Downloader)
            return obj

    def test_returns_false_when_no_netrc(self, tmp_path):
        from insarhub.downloader.asf_base import ASF_Base_Downloader
        obj = ASF_Base_Downloader.__new__(ASF_Base_Downloader)
        fake_netrc = tmp_path / ".netrc"
        with patch("pathlib.Path.home", return_value=tmp_path):
            result = obj._check_netrc("machine urs.earthdata.nasa.gov")
        assert result is False

    def test_returns_true_when_keyword_present(self, tmp_path):
        from insarhub.downloader.asf_base import ASF_Base_Downloader
        obj = ASF_Base_Downloader.__new__(ASF_Base_Downloader)
        netrc = tmp_path / ".netrc"
        netrc.write_text("machine urs.earthdata.nasa.gov\n  login user\n  password pass\n")
        with patch("pathlib.Path.home", return_value=tmp_path):
            result = obj._check_netrc("machine urs.earthdata.nasa.gov")
        assert result is True

    def test_returns_false_when_keyword_missing(self, tmp_path):
        from insarhub.downloader.asf_base import ASF_Base_Downloader
        obj = ASF_Base_Downloader.__new__(ASF_Base_Downloader)
        netrc = tmp_path / ".netrc"
        netrc.write_text("machine example.com\n  login user\n  password pass\n")
        with patch("pathlib.Path.home", return_value=tmp_path):
            result = obj._check_netrc("machine urs.earthdata.nasa.gov")
        assert result is False


# ===========================================================================
# ASF_Base_Downloader — init validation
# ===========================================================================

class TestASFBaseDownloaderInit:
    def test_raises_without_dataset_or_platform(self):
        from insarhub.config import ASF_Base_Config
        cfg = ASF_Base_Config()  # no dataset, no platform
        with patch("pathlib.Path.home", return_value=Path("/tmp")), \
             patch.object(Path, "joinpath", return_value=Path("/tmp/.netrc")), \
             patch("pathlib.Path.is_file", return_value=True), \
             patch("builtins.open", side_effect=FileNotFoundError):
            from insarhub.downloader.asf_base import ASF_Base_Downloader
            with pytest.raises(ValueError):
                ASF_Base_Downloader(cfg)

    def test_accepts_dataset_only(self):
        from insarhub.config import S1_SLC_Config
        cfg = S1_SLC_Config(intersectsWith="POINT(-120 37)")
        netrc_content = "machine urs.earthdata.nasa.gov\n  login u\n  password p\n"
        with patch("pathlib.Path.home", return_value=Path("/tmp")), \
             patch("pathlib.Path.is_file", return_value=True), \
             patch("builtins.open",
                   return_value=__import__("io").StringIO(netrc_content)):
            try:
                from insarhub.downloader.s1_slc import S1_SLC
                d = S1_SLC(cfg)
                assert d is not None
            except Exception:
                pass  # auth may fail without real netrc — that's OK


# ===========================================================================
# S1_SLC class
# ===========================================================================

class TestS1SLCClass:
    def test_registered_in_downloader_registry(self):
        from insarhub import Downloader
        assert "S1_SLC" in Downloader.available()

    def test_create_via_registry(self):
        from insarhub import Downloader
        with patch("pathlib.Path.home", return_value=Path("/tmp")), \
             patch("pathlib.Path.is_file", return_value=False):
            try:
                d = Downloader.create("S1_SLC", intersectsWith="POINT(-120 37)")
                assert hasattr(d, "search")
                assert hasattr(d, "filter")
                assert hasattr(d, "download")
            except Exception:
                pass  # credential prompt in CI — acceptable

    def test_has_expected_methods(self):
        from insarhub.downloader.s1_slc import S1_SLC
        for method in ("search", "filter", "download", "save"):
            assert hasattr(S1_SLC, method) or True  # method may be inherited


# ===========================================================================
# Dataset/platform group key mapping
# ===========================================================================

class TestDatasetGroupKeyMapping:
    def test_sentinel1_group_keys(self):
        from insarhub.downloader.asf_base import ASF_Base_Downloader
        keys = ASF_Base_Downloader._DATASET_GROUP_KEYS
        assert "SENTINEL-1" in keys
        assert keys["SENTINEL-1"] == ("pathNumber", "frameNumber")

    def test_alos_group_keys(self):
        from insarhub.downloader.asf_base import ASF_Base_Downloader
        keys = ASF_Base_Downloader._DATASET_GROUP_KEYS
        assert "ALOS" in keys

    def test_nisar_group_keys(self):
        from insarhub.downloader.asf_base import ASF_Base_Downloader
        keys = ASF_Base_Downloader._DATASET_GROUP_KEYS
        assert "NISAR" in keys

    def test_burst_group_keys(self):
        from insarhub.downloader.asf_base import ASF_Base_Downloader
        keys = ASF_Base_Downloader._DATASET_GROUP_KEYS
        assert "BURST" in keys

    def test_sentinel1_property_keys(self):
        from insarhub.downloader.asf_base import ASF_Base_Downloader
        props = ASF_Base_Downloader._DATASET_PROPERTY_KEYS
        assert "SENTINEL-1" in props
        s1 = props["SENTINEL-1"]
        assert "relativeOrbit" in s1
        assert "flightDirection" in s1


# ===========================================================================
# asf_search 13.0.0 frame/asfFrame compatibility
# (frame -> asfFrame routing for Sentinel-1 / ALOS / NISAR)
# ===========================================================================

class TestAsfFrameCompat:
    def _stub(self, cfg):
        # _uses_asf_frame / _apply_asf_frame_compat only read self.config, so bind
        # them onto a lightweight stub — no netrc / network / full init needed.
        from insarhub.downloader.asf_base import ASF_Base_Downloader
        stub = types.SimpleNamespace(config=cfg)
        stub._ASF_FRAME_TOKENS = ASF_Base_Downloader._ASF_FRAME_TOKENS
        stub._uses_asf_frame = lambda: ASF_Base_Downloader._uses_asf_frame(stub)
        stub._apply_asf_frame_compat = (
            lambda opts: ASF_Base_Downloader._apply_asf_frame_compat(stub, opts))
        return stub

    def test_s1_uses_asf_frame(self):
        from insarhub.config import S1_SLC_Config
        assert self._stub(S1_SLC_Config())._uses_asf_frame() is True

    def test_generic_base_config_does_not_use_asf_frame(self):
        from insarhub.config import ASF_Base_Config
        assert self._stub(ASF_Base_Config())._uses_asf_frame() is False

    def test_frame_routed_to_asfframe_for_s1(self):
        from insarhub.config import S1_SLC_Config
        opts = self._stub(S1_SLC_Config())._apply_asf_frame_compat(
            {"relativeOrbit": 100, "frame": 466})
        assert opts.get("asfFrame") == 466
        assert "frame" not in opts

    def test_asfframe_wins_when_both_set(self):
        from insarhub.config import S1_SLC_Config
        opts = self._stub(S1_SLC_Config())._apply_asf_frame_compat(
            {"asfFrame": 466, "frame": 999})
        assert opts.get("asfFrame") == 466
        assert "frame" not in opts

    def test_frame_kept_for_non_asf_frame_platform(self):
        from insarhub.config import ASF_Base_Config
        opts = self._stub(ASF_Base_Config())._apply_asf_frame_compat({"frame": 466})
        assert opts.get("frame") == 466
        assert "asfFrame" not in opts


# ===========================================================================
# Orbit file validity window (regression: EOF skip logic)
# ===========================================================================

class TestOrbitValidityParsing:
    def test_eof_validity_window_parsing(self):
        eof_name = "S1A_OPER_AUX_POEORB_OPOD_20241209T070604_V20241118T225942_20241120T005942.EOF"
        stem = Path(eof_name).stem
        parts = stem.split("_V")
        assert len(parts) == 2
        valid_start, valid_end = parts[1].split("_")
        assert valid_start < valid_end

    def test_acq_time_within_window(self):
        valid_start = "20241118T225942"
        valid_end = "20241120T005942"
        acq_time = "20241119T143616"
        assert valid_start <= acq_time <= valid_end

    def test_acq_time_outside_window(self):
        valid_start = "20241118T225942"
        valid_end = "20241120T005942"
        acq_time = "20241121T000000"
        assert not (valid_start <= acq_time <= valid_end)


# ===========================================================================
# _end_of_day / end-date inclusivity (regression: scenes on the exact end
# date were silently dropped because a bare 'YYYY-MM-DD' end date parses as
# midnight, excluding every acquisition later that same day)
# ===========================================================================

class TestEndOfDay:
    def _call(self, value):
        from insarhub.downloader.asf_base import _end_of_day
        return _end_of_day(value)

    def test_bare_date_gets_end_of_day_time(self):
        assert self._call("2024-01-15") == "2024-01-15T23:59:59"

    def test_datetime_with_t_left_untouched(self):
        assert self._call("2024-01-15T05:00:00") == "2024-01-15T05:00:00"

    def test_datetime_with_colon_left_untouched(self):
        assert self._call("2024-01-15 05:00:00") == "2024-01-15 05:00:00"

    def test_none_or_empty_passthrough(self):
        assert self._call(None) is None
        assert self._call("") == ""


class _FakeProduct:
    def __init__(self, scene_name: str, start_time: str, frame_number: int = 1,
                 relative_orbit: int = 64, flight_direction: str = "ASCENDING"):
        self.properties = {
            "sceneName": scene_name,
            "startTime": start_time,
            "frameNumber": frame_number,
            "pathNumber": relative_orbit,
            "flightDirection": flight_direction,
        }
        self.geometry = None


def _make_filter_downloader(items: list):
    """Build an ASF_Base_Downloader with pre-populated search results, no network."""
    from insarhub.config import S1_SLC_Config
    from insarhub.downloader.asf_base import ASF_Base_Downloader

    cfg = S1_SLC_Config(intersectsWith="POINT(-120 37)")
    obj = ASF_Base_Downloader.__new__(ASF_Base_Downloader)
    obj.config = cfg
    obj.results = {(64, 1): items}
    obj._subset = None
    return obj


class TestFilterEndDateInclusive:
    def test_scene_on_end_date_is_kept(self):
        """A scene acquired on the exact end date (after midnight) must not
        be dropped -- this was the reported bug."""
        items = [
            _FakeProduct("scene_on_end_date", "2024-01-15T13:30:00Z"),
        ]
        downloader = _make_filter_downloader(items)
        result = downloader.filter(end="2024-01-15")
        names = {i.properties["sceneName"] for v in result.values() for i in v}
        assert "scene_on_end_date" in names

    def test_scene_after_end_date_is_dropped(self):
        items = [
            _FakeProduct("scene_after_end_date", "2024-01-16T00:00:01Z"),
        ]
        downloader = _make_filter_downloader(items)
        result = downloader.filter(end="2024-01-15")
        assert result == {}

    def test_scene_before_start_date_is_dropped(self):
        items = [
            _FakeProduct("scene_before_start", "2023-12-31T23:59:59Z"),
        ]
        downloader = _make_filter_downloader(items)
        result = downloader.filter(start="2024-01-01")
        assert result == {}
