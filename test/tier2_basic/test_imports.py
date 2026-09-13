"""Tier 2 -- every module imports cleanly.

Import errors are the highest-leverage thing to test in this codebase because
registration is an import side effect: a processor that fails to import does not
raise, it just silently disappears from the registry, and the GUI/CLI then show
a shorter list with no error anywhere.

Two passes, and both matter:
  * in-process, which is fast and covers every module
  * in a fresh subprocess, which is the only way to catch import-time side
    effects and circular imports that an already-warm sys.modules hides
"""

from __future__ import annotations

import importlib
import pkgutil
import subprocess
import sys

import pytest

import _backends
import insarhub


def _all_modules() -> list[str]:
    """Every importable module under the insarhub package."""
    found = []
    for info in pkgutil.walk_packages(insarhub.__path__, prefix="insarhub."):
        # The frontend directory is JS/TS with a node_modules tree; nothing in
        # it is importable Python.
        if ".frontend" in info.name:
            continue
        found.append(info.name)
    return sorted(found)


MODULES = _all_modules()


def test_module_discovery_found_something():
    # If walk_packages returns nothing the parametrised test below silently
    # becomes zero tests, which would look like a pass.
    assert len(MODULES) > 50, f"only discovered {len(MODULES)} modules"


@pytest.mark.parametrize("module", MODULES)
def test_module_imports(module):
    importlib.import_module(module)


@pytest.mark.parametrize(
    "module",
    [
        "insarhub",
        "insarhub.cli.main",
        "insarhub.app.api",
        "insarhub.app.main",
        "insarhub.core.engine",
        "insarhub.core.registry",
        "insarhub.config.defaultconfig",
        "insarhub.utils.tool",
    ],
)
def test_module_imports_in_a_fresh_interpreter(module):
    """No circular imports, no reliance on another module having been imported
    first, no import-time crash.

    The geo stack is not stubbed here on purpose -- this asks whether the module
    imports in a *real* environment, which is what a user gets. That also means
    it is only answerable where the real stack is installed: insarhub/__init__
    imports mintpy and dask unconditionally, so a subprocess import fails
    without them however well the module itself is written.

    It therefore SKIPS on the pip-only PR gate (which cannot install mintpy or
    the gdal-dependent packages on a bare runner) and RUNS in the conda-based
    install job, where the genuine stack is present and the question is real.
    """
    for dep in ("mintpy", "dask", "cdsapi"):
        if not _backends._importable(dep):
            pytest.skip(
                f"{dep} is not genuinely installed; a fresh-interpreter import "
                "cannot distinguish a broken module from a missing dependency"
            )

    proc = subprocess.run(
        [sys.executable, "-c", f"import {module}"],
        capture_output=True, text=True, timeout=300,
    )
    assert proc.returncode == 0, f"`import {module}` failed:\n{proc.stderr}"


def test_public_api_surface():
    """The names __init__ advertises must actually resolve.

    insarhub.utils.__all__ has carried names that were never importable; an
    `from insarhub.utils import *` would then raise AttributeError.
    """
    import insarhub.utils as utils

    missing = [name for name in getattr(utils, "__all__", []) if not hasattr(utils, name)]
    assert not missing, f"insarhub.utils.__all__ names that do not resolve: {missing}"


def test_top_level_exports():
    for name in ("Downloader", "Processor", "Analyzer"):
        assert hasattr(insarhub, name), f"insarhub.{name} is not exported"


def test_version_is_pep440():
    """A malformed version breaks `pip install` and the conda feedstock."""
    from packaging.version import InvalidVersion, Version

    try:
        Version(insarhub.__version__)
    except InvalidVersion:  # pragma: no cover - only on a bad bump
        pytest.fail(f"__version__ {insarhub.__version__!r} is not PEP 440")
