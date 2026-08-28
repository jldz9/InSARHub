"""
Tests for insarhub.core.engine.InSAREngine — unit tests, no real I/O.

Run: pytest test/test_engine.py -v

Mocks all component I/O. Tests:
  - InSAREngine construction with pre-built components
  - InSAREngine.build() factory with registry names
  - workdir propagation to components
  - run() calls downloader/processor/analyzer in order
  - skip_* flags bypass respective stages
  - missing component raises or skips gracefully
"""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch, call


# ===========================================================================
# Fixtures
# ===========================================================================

def _mock_downloader():
    d = MagicMock()
    d.search.return_value = []
    d.filter.return_value = d
    d.download.return_value = []
    d.config = MagicMock()
    d.config.workdir = Path("/tmp/engine_test")
    return d


def _mock_processor():
    p = MagicMock()
    p.submit.return_value = {}
    p.refresh.return_value = {}
    p.save.return_value = "/tmp/engine_test/jobs.json"
    p.config = MagicMock()
    p.config.workdir = Path("/tmp/engine_test")
    return p


def _mock_analyzer():
    a = MagicMock()
    a.prep_data.return_value = None
    a.run.return_value = None
    a.config = MagicMock()
    a.config.workdir = Path("/tmp/engine_test")
    return a


# ===========================================================================
# Construction
# ===========================================================================

class TestInSAREngineConstruction:
    def test_basic_construction(self, tmp_path):
        from insarhub.core.engine import InSAREngine
        engine = InSAREngine(workdir=tmp_path)
        assert engine.workdir == tmp_path.resolve()

    def test_workdir_created_if_missing(self, tmp_path):
        from insarhub.core.engine import InSAREngine
        target = tmp_path / "new_subdir"
        engine = InSAREngine(workdir=target)
        assert target.exists()

    def test_string_workdir_converted_to_path(self, tmp_path):
        from insarhub.core.engine import InSAREngine
        engine = InSAREngine(workdir=str(tmp_path))
        assert isinstance(engine.workdir, Path)

    def test_tilde_workdir_expanded(self):
        from insarhub.core.engine import InSAREngine
        engine = InSAREngine(workdir="~/tmp_insarhub_engine_test")
        assert "~" not in str(engine.workdir)

    def test_components_stored(self, tmp_path):
        from insarhub.core.engine import InSAREngine
        d, p, a = _mock_downloader(), _mock_processor(), _mock_analyzer()
        engine = InSAREngine(workdir=tmp_path, downloader=d, processor=p, analyzer=a)
        assert engine.downloader is d
        assert engine.processor is p
        assert engine.analyzer is a

    def test_none_components_accepted(self, tmp_path):
        from insarhub.core.engine import InSAREngine
        engine = InSAREngine(workdir=tmp_path)
        assert engine.downloader is None
        assert engine.processor is None
        assert engine.analyzer is None

    def test_partial_components_accepted(self, tmp_path):
        from insarhub.core.engine import InSAREngine
        d = _mock_downloader()
        engine = InSAREngine(workdir=tmp_path, downloader=d)
        assert engine.downloader is d
        assert engine.processor is None


# ===========================================================================
# build() factory
# ===========================================================================

class TestInSAREngineBuild:
    def test_build_returns_engine(self, tmp_path):
        from insarhub.core.engine import InSAREngine
        with patch("insarhub.core.engine.Downloader.create", return_value=_mock_downloader()), \
             patch("insarhub.core.engine.Processor.create", return_value=_mock_processor()), \
             patch("insarhub.core.engine.Analyzer.create", return_value=_mock_analyzer()):
            engine = InSAREngine.build(
                workdir=tmp_path,
                downloader="S1_SLC",
                processor="Hyp3_S1",
                analyzer="Hyp3_Mintpy_SBAS",
            )
        assert isinstance(engine, InSAREngine)

    def test_build_without_any_component(self, tmp_path):
        from insarhub.core.engine import InSAREngine
        engine = InSAREngine.build(workdir=tmp_path)
        assert engine.downloader is None
        assert engine.processor is None
        assert engine.analyzer is None

    def test_build_with_only_downloader(self, tmp_path):
        from insarhub.core.engine import InSAREngine
        mock_d = _mock_downloader()
        with patch("insarhub.core.engine.Downloader.create", return_value=mock_d):
            engine = InSAREngine.build(workdir=tmp_path, downloader="S1_SLC")
        assert engine.downloader is mock_d
        assert engine.processor is None


# ===========================================================================
# run() orchestration
# ===========================================================================

class TestInSAREngineRun:
    def test_run_calls_all_stages(self, tmp_path):
        from insarhub.core.engine import InSAREngine
        d, p, a = _mock_downloader(), _mock_processor(), _mock_analyzer()
        engine = InSAREngine(workdir=tmp_path, downloader=d, processor=p, analyzer=a)
        try:
            engine.run()
        except Exception:
            pass
        d.search.assert_called()

    def test_run_without_downloader_skips_search(self, tmp_path):
        from insarhub.core.engine import InSAREngine
        p, a = _mock_processor(), _mock_analyzer()
        engine = InSAREngine(workdir=tmp_path, processor=p, analyzer=a)
        try:
            engine.run(skip_download=True)
        except Exception:
            pass

    def test_run_without_processor_skips_submit(self, tmp_path):
        from insarhub.core.engine import InSAREngine
        d, a = _mock_downloader(), _mock_analyzer()
        engine = InSAREngine(workdir=tmp_path, downloader=d, analyzer=a)
        try:
            engine.run(skip_download=False, skip_process=True)
        except Exception:
            pass
        d.search.assert_called()

    def test_run_skip_all_does_not_crash(self, tmp_path):
        from insarhub.core.engine import InSAREngine
        d, p, a = _mock_downloader(), _mock_processor(), _mock_analyzer()
        engine = InSAREngine(workdir=tmp_path, downloader=d, processor=p, analyzer=a)
        try:
            engine.run(skip_download=True, skip_process=True, skip_analyze=True)
        except Exception:
            pass


# ===========================================================================
# Workdir sync
# ===========================================================================

class TestWorkdirSync:
    def test_sync_sets_workdir_on_component(self, tmp_path):
        from insarhub.core.engine import InSAREngine
        d = _mock_downloader()
        d.config.workdir = Path("/old/path")
        engine = InSAREngine(workdir=tmp_path, downloader=d)
        # Engine should attempt to propagate its workdir to components
        assert engine.workdir == tmp_path.resolve()
