"""Tier 1 -- is this installation wired up correctly?

These run against whatever InSARHub is importable in the current environment,
which is the point: CI installs the wheel into four different conda envs (base,
+isce2, +isce3/dolphin, +gmtsar) and runs this same file in each. A failure here
means the *package* is broken, before any InSAR question is asked.

What "correct" means:
  * the package imports and reports a version
  * both console entry points exist and run
  * data files the code reads at runtime actually shipped in the wheel
  * the registries are populated
  * the backend matrix is reported, so an env that claims to have ISCE2 has it

See test/README.md for how this maps onto the install matrix in CI.
"""

from __future__ import annotations

import importlib.metadata
import subprocess
from pathlib import Path

import pytest

import _backends


# ── The package itself ───────────────────────────────────────────────────────

def test_package_imports():
    import insarhub

    assert insarhub.__version__


def test_version_matches_distribution_metadata():
    """The importable version and the installed distribution must agree.

    They drift when someone bumps _version.py but the env still has an older
    wheel -- which silently makes every other test report on stale code.
    """
    import insarhub

    try:
        dist_version = importlib.metadata.version("insarhub")
    except importlib.metadata.PackageNotFoundError:
        pytest.skip("insarhub is not pip-installed (running from a source tree)")

    assert insarhub.__version__ == dist_version, (
        f"imported insarhub is {insarhub.__version__} but the installed "
        f"distribution is {dist_version} -- the environment has a stale install"
    )


# ── Console entry points ─────────────────────────────────────────────────────
#
# Driven as real subprocesses: these are what a user types, and an entry point
# can be declared in pyproject yet point at a function that does not exist.
# pyproject maps insarhub-app to app.main:main; the conda recipe has historically
# pointed it at app.main:serve, which ignores argv and would hang here.

@pytest.mark.parametrize("command", ["insarhub", "insarhub-app"])
def test_console_script_reports_version(command):
    proc = subprocess.run(
        [command, "--version"], capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, f"{command} --version failed: {proc.stderr}"

    import insarhub

    assert insarhub.__version__ in proc.stdout


def test_cli_help_lists_every_top_level_command():
    proc = subprocess.run(
        ["insarhub", "--help"], capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0
    for command in ("downloader", "processor", "analyzer", "utils"):
        assert command in proc.stdout, f"`{command}` missing from --help"


# ── Data files that must survive packaging ───────────────────────────────────

def test_public_api_names_all_resolve():
    """Every name insarhub.utils advertises must actually exist.

    This replaces two strict xfails that tracked `get_config()`: it was exported
    in __all__ but could never run -- `tomllib` was never imported, the `Config`
    class it referenced does not exist anywhere in the package, its config.toml
    was absent from the wheel, and that file was not valid TOML either. It was a
    vestige of an earlier incarnation of this repo (its [TRAIN] / COCO sections
    configure Mask R-CNN, not InSAR), so the whole path was removed rather than
    repaired. What is worth keeping is the check that caught it: a name in
    __all__ that does not resolve.
    """
    import insarhub.utils as utils

    missing = [n for n in getattr(utils, "__all__", []) if not hasattr(utils, n)]
    assert not missing, (
        f"insarhub.utils.__all__ advertises names that do not resolve: {missing}"
    )


def test_frontend_bundle_is_installed():
    """The GUI is served from the packaged dist/; without it `insarhub-app`
    starts and then 404s on every page."""
    import insarhub.app as app_pkg

    dist = Path(app_pkg.__file__).parent / "frontend" / "dist"
    if not dist.is_dir():
        pytest.skip("frontend not built (source checkout without `npm run build`)")

    assert (dist / "index.html").is_file(), "frontend dist/ has no index.html"
    assert any(dist.glob("assets/*.js")), "frontend dist/ has no JS bundle"


# ── Registries ───────────────────────────────────────────────────────────────

def test_registries_are_populated():
    """Registration happens as an import side effect; if a submodule fails to
    import, its entry silently vanishes instead of raising."""
    from insarhub import Analyzer, Downloader, Processor

    assert set(Downloader._registry) >= {"S1_SLC", "S1_Burst", "NISAR_GSLC"}
    assert set(Processor._registry) >= {"Hyp3_S1", "ISCE2_S1", "GMTSAR_S1", "ISCE3_Burst", "ISCE3_NISAR"}
    assert set(Analyzer._registry) >= {"Hyp3_Mintpy_SBAS", "ISCE2_Mintpy_SBAS", "GMTSAR_SBAS"}


# ── Backend matrix ───────────────────────────────────────────────────────────

def test_backend_report(record_property):
    """Record what this environment can run. Never fails -- it is the report CI
    reads to confirm the +isce2 env really does have ISCE2."""
    for key, value in _backends.summary().items():
        record_property(key, value)
    print("\nInstall capability report:")
    for key, value in sorted(_backends.summary().items()):
        print(f"  {'yes' if value else ' no'}  {key}")


@pytest.mark.parametrize("workflow", sorted(_backends.WORKFLOWS))
def test_declared_workflow_is_constructible(workflow):
    """Every registered workflow triple must resolve, on every install.

    This is deliberately independent of whether the backend is present: building
    the objects is pure registry/config work, so a missing GMTSAR must not stop
    `insarhub processor submit -P GMTSAR_S1 --help` from working.
    """
    from insarhub import Analyzer, Downloader, Processor

    spec = _backends.WORKFLOWS[workflow]
    assert spec["downloader"] in Downloader._registry
    assert spec["processor"] in Processor._registry
    assert spec["analyzer"] in Analyzer._registry

    processor = Processor._registry[spec["processor"]]
    assert processor.default_config is not None


def test_env_claiming_a_backend_actually_has_it():
    """Guard against a CI env that was *supposed* to install a backend but
    didn't -- `conda install` is run with continue-on-error in some jobs, so a
    silent failure would otherwise turn into a silent skip everywhere.

    Opt in by setting INSARHUB_EXPECT_BACKENDS=isce2,mintpy in the CI job.
    """
    import os

    expected = [b for b in os.environ.get("INSARHUB_EXPECT_BACKENDS", "").split(",") if b]
    if not expected:
        pytest.skip("INSARHUB_EXPECT_BACKENDS not set")

    probes = {
        "isce2": _backends.has_isce2,
        "isce3": _backends.has_isce3,
        "gmtsar": _backends.has_gmtsar,
        "mintpy": _backends.has_mintpy,
        "dolphin": _backends.has_dolphin,
    }
    missing = [b for b in expected if not probes[b]()]
    assert not missing, (
        f"this environment declares {expected} but {missing} are not importable "
        "-- the backend install step failed silently"
    )
