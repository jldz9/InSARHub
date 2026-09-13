"""Shared pytest configuration for all four tiers.

Tier layout (see test/README.md):

    tier1_install/     the installed environment is wired up correctly
    tier2_basic/       imports, CLI and GUI contracts, no real data
    tier3_e2e/         full workflow runs against real minimal data
    tier4_regression/  one case per previously-fixed bug

Tiers 1, 2 and 4 must stay hermetic: no network, no real backends, no writes
outside tmp_path. Tier 3 is the opposite by design and is opt-in via `-m e2e`.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Make the helper modules importable as plain `_backends` / `_harness` from any
# tier directory without turning test/ into a package.
sys.path.insert(0, str(Path(__file__).parent))

import _backends  # noqa: E402


# ── Heavy optional dependencies ──────────────────────────────────────────────
#
# insarhub/__init__.py imports mintpy and dask unconditionally at package-import
# time, and several modules import osgeo at module scope. Tiers 1/2/4 must run
# on a machine with none of the geo stack installed, so those are stubbed before
# the first insarhub import.
#
# Two deliberate exceptions:
#   * h5py is NOT stubbed -- it is imported lazily inside h5_to_raster() only,
#     and the postprocess tests build real HDF5 fixtures with it.
#   * Nothing is stubbed when running tier 3, which needs the genuine packages.

_STUBS = [
    "osgeo", "osgeo.gdal", "osgeo.osr", "osgeo.ogr",
    "mintpy", "mintpy.smallbaselineApp",
    "mintpy.utils", "mintpy.utils.readfile", "mintpy.utils.utils",
    "mintpy.utils.network", "mintpy.utils.plot",
    "mintpy.cli", "mintpy.cli.geocode",
    "cdsapi",
    "dask",
]


def _stub(name: str) -> MagicMock:
    mod = MagicMock(spec=None)
    mod.__path__ = []
    mod.__name__ = name
    mod.__spec__ = None
    mod.__loader__ = None
    mod.__package__ = name
    return mod


def _running_e2e() -> bool:
    """True when this invocation is asking for tier 3.

    Checked against raw argv because it has to be decided at import time, before
    pytest builds its config.
    """
    argv = " ".join(sys.argv)
    return "e2e" in argv and "not e2e" not in argv


if not _running_e2e():
    for _name in _STUBS:
        sys.modules.setdefault(_name, _stub(_name))


# ── Marker wiring ────────────────────────────────────────────────────────────

_TIER_MARKERS = {
    "tier1_install": "install",
    "tier2_basic": "basic",
    "tier3_e2e": "e2e",
    "tier4_regression": "regression",
}


def pytest_collection_modifyitems(config, items):
    """Apply each tier's marker by directory, and skip what the machine lacks.

    Marking by location rather than by hand means a new file in tier4_regression/
    is a regression test automatically -- there is no way to add one and forget
    the marker.
    """
    for item in items:
        parts = set(Path(str(item.fspath)).parts)
        for directory, marker in _TIER_MARKERS.items():
            if directory in parts:
                item.add_marker(getattr(pytest.mark, marker))

        # needs_* markers gate on the real machine, so a developer without
        # GMTSAR sees "skipped", not a failure they cannot act on.
        for backend, probe in (
            ("needs_isce2", _backends.has_isce2),
            ("needs_isce3", _backends.has_isce3),
            ("needs_gmtsar", _backends.has_gmtsar),
            ("needs_mintpy", _backends.has_mintpy),
        ):
            if item.get_closest_marker(backend) and not probe():
                item.add_marker(pytest.mark.skip(reason=f"{backend[6:]} not installed"))

        if item.get_closest_marker("needs_creds") and not _backends.has_earthdata():
            item.add_marker(pytest.mark.skip(reason="no Earthdata credentials in ~/.netrc"))

        if item.get_closest_marker("needs_network") and os.environ.get("INSARHUB_NO_NETWORK"):
            item.add_marker(pytest.mark.skip(reason="INSARHUB_NO_NETWORK is set"))


# ── Shared fixtures ──────────────────────────────────────────────────────────

@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    """An isolated InSARHub working directory."""
    d = tmp_path / "wd"
    d.mkdir()
    return d


@pytest.fixture(autouse=True)
def _no_stray_writes(tmp_path, monkeypatch):
    """Run every test from tmp_path, never the repo checkout.

    Constructing a processor stamps insarhub_config.json into its workdir, and a
    config that defaults to "." would otherwise drop that file wherever pytest
    was started -- which is exactly how the old suite leaked an untracked
    insarhub_config.json into the repo root on every run.
    """
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def api_client(monkeypatch):
    """FastAPI TestClient over the real app, with its network calls neutered.

    The app's startup hook calls _build_auth_status(), which issues real HTTPS
    requests to the CDS, CDSE and HyP3 APIs. Left alone that makes every
    api_client test wait on the open internet -- it cost ~9s per test and made
    this file take four minutes. Tier 2 must be hermetic, so the probe is
    replaced with a static answer; the auth endpoints' own logic is covered by
    its unit tests, and the real thing is exercised in tier 3.
    """
    from fastapi.testclient import TestClient

    import insarhub.app.routes.auth as auth_routes
    import insarhub.app.state as state

    _OFFLINE_STATUS = {
        "earthdata_connected": False,
        "cdse_connected": False,
        "cds_connected": False,
        "hyp3": None,
        "credit_pool": [],
        "credit_pool_exists": False,
    }

    monkeypatch.setattr(auth_routes, "_build_auth_status", lambda: dict(_OFFLINE_STATUS))
    monkeypatch.setattr(state, "_auth_cache", dict(_OFFLINE_STATUS), raising=False)

    from insarhub.app.api import app

    with TestClient(app) as client:
        yield client


@pytest.fixture
def cli():
    """Invoke the CLI in-process and capture (exit_code, stdout, stderr).

    In-process rather than subprocess so it is fast enough to use on every
    command, and so monkeypatching still applies. Tier 3 uses the real
    subprocess entry point instead -- see tier3_e2e/_harness.py.
    """
    import contextlib
    import io

    def _run(*argv: str) -> tuple[int, str, str]:
        # cli.main.main() takes no arguments -- it calls parse_known_args(),
        # which reads sys.argv -- so the only way to drive it in-process is to
        # swap sys.argv around the call.
        from insarhub.cli.main import main

        out, err = io.StringIO(), io.StringIO()
        code = 0
        saved = sys.argv
        sys.argv = ["insarhub", *argv]
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                try:
                    main()
                except SystemExit as exc:
                    code = exc.code if isinstance(exc.code, int) else 1
        finally:
            sys.argv = saved
        return code, out.getvalue(), err.getvalue()

    return _run
