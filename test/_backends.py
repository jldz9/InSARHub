"""Backend / credential detection shared by every tier.

InSARHub's five workflows each need a different external stack, and none of
them are pip-installable dependencies -- ISCE2, ISCE3+COMPASS+dolphin and
GMTSAR are conda packages or source builds that a given machine may or may not
have. Tests therefore have to *ask* rather than assume, so tier 1 can report
which workflows an install can actually run and tier 3 can skip the ones it
cannot.

Every probe here is cheap (an import or a PATH lookup) and cached, because the
collection phase calls them once per parametrised case.
"""

from __future__ import annotations

import functools
import importlib.machinery
import importlib.util
import os
import shutil
from pathlib import Path


# ── Primitive probes ─────────────────────────────────────────────────────────

@functools.lru_cache(maxsize=None)
def _importable(module: str) -> bool:
    """True if `module` is really installed on sys.path.

    PathFinder rather than importlib.util.find_spec, and the difference matters:
    find_spec consults sys.modules first, and the root conftest deliberately
    puts MagicMock stubs for mintpy/osgeo/dask there so tiers 1/2/4 run without
    the geo stack. Those stubs carry __spec__ = None, so find_spec reported
    "not installed" for packages that were installed -- which made tier 1 fail
    on a correctly built environment.

    PathFinder searches sys.path directly, ignoring sys.modules, so it answers
    the question tier 1 is actually asking: is this on disk? It also avoids
    paying the (very heavy) import cost of isce2/mintpy, and their import-time
    side effects.
    """
    try:
        return importlib.machinery.PathFinder().find_spec(module) is not None
    except (ImportError, ValueError, ModuleNotFoundError, AttributeError):
        return False


@functools.lru_cache(maxsize=None)
def _on_path(exe: str) -> bool:
    return shutil.which(exe) is not None


# ── Backend availability ─────────────────────────────────────────────────────

@functools.lru_cache(maxsize=None)
def has_mintpy() -> bool:
    return _importable("mintpy")


@functools.lru_cache(maxsize=None)
def has_isce2() -> bool:
    """Mirror how InSARHub itself finds ISCE2.

    processor/isce2_base.py::_check_isce2 resolves topsApp.py in this order:
    an explicit isce_home, then $ISCE_HOME/applications, then PATH. This probe
    follows the same order, minus the config, so "the probe says yes" and "the
    processor will work" cannot disagree.

    An earlier version checked PATH only, and reported a perfectly good conda
    install as missing: conda-forge's isce2 ships an activation script that sets
    ISCE_HOME and deliberately does NOT put topsApp.py on PATH (it lives in
    site-packages/isce/applications/). That made every ISCE2 test skip on an
    environment that could actually run them -- the exact failure
    INSARHUB_EXPECT_BACKENDS exists to catch.
    """
    if not _importable("isce"):
        return False

    home = os.environ.get("ISCE_HOME")
    if home:
        base = Path(home)
        if (base / "applications" / "topsApp.py").is_file() or (base / "topsApp.py").is_file():
            return True

    return _on_path("topsApp.py")


@functools.lru_cache(maxsize=None)
def has_isce3() -> bool:
    # ISCE3_Burst needs all three: isce3 for the core, COMPASS for the S1
    # geocoded-SLC workflow, dolphin for phase linking / unwrapping.
    return _importable("isce3") and _importable("compass") and _importable("dolphin")


@functools.lru_cache(maxsize=None)
def has_dolphin() -> bool:
    return _importable("dolphin")


@functools.lru_cache(maxsize=None)
def has_gmtsar() -> bool:
    """GMTSAR ships no Python package; the processor drives its shell tools.

    Both spellings are accepted: docs/quickstart/install.md verifies the install
    with `which p2p_processing`, while GMTSAR has historically installed the
    driver as `p2p_processing.csh`. Checking only one of them would report a
    working GMTSAR as missing depending on the release.
    """
    return any(
        _on_path(exe)
        for exe in ("p2p_processing", "p2p_processing.csh", "align.csh")
    )


# ── Credentials ──────────────────────────────────────────────────────────────

def _netrc_has(host: str) -> bool:
    netrc = Path(os.environ.get("NETRC", Path.home() / ".netrc"))
    if not netrc.is_file():
        return False
    try:
        return f"machine {host}" in netrc.read_text()
    except OSError:
        return False


@functools.lru_cache(maxsize=None)
def has_earthdata() -> bool:
    return _netrc_has("urs.earthdata.nasa.gov")


@functools.lru_cache(maxsize=None)
def has_cdse() -> bool:
    return _netrc_has("dataspace.copernicus.eu")


@functools.lru_cache(maxsize=None)
def has_cds() -> bool:
    return (Path.home() / ".cdsapirc").is_file()


# ── Workflow matrix ──────────────────────────────────────────────────────────
#
# The five end-to-end workflows InSARHub ships, each as
# (downloader, processor, analyzer) plus what it takes to run one.
# Tier 1 reports on this table; tier 3 is parametrised over it.

WORKFLOWS = {
    "hyp3_s1": {
        "downloader": "S1_SLC",
        "processor": "Hyp3_S1",
        "analyzer": "Hyp3_Mintpy_SBAS",
        # HyP3 does the interferograms in the cloud, so the only local
        # requirement is MintPy for the time-series inversion.
        "requires": ("mintpy",),
        "creds": ("earthdata",),
    },
    "isce2_s1": {
        "downloader": "S1_SLC",
        "processor": "ISCE2_S1",
        "analyzer": "ISCE2_Mintpy_SBAS",
        "requires": ("isce2", "mintpy"),
        "creds": ("earthdata",),
    },
    "gmtsar_s1": {
        "downloader": "S1_SLC",
        "processor": "GMTSAR_S1",
        "analyzer": "GMTSAR_Mintpy_SBAS",
        "requires": ("gmtsar", "mintpy"),
        "creds": ("earthdata",),
    },
    "isce3_burst": {
        "downloader": "S1_Burst",
        "processor": "ISCE3_Burst",
        "analyzer": "ISCE3_Dolphin_S1_PL",
        "requires": ("isce3",),
        "creds": ("earthdata",),
    },
    "isce3_nisar": {
        "downloader": "NISAR_GSLC",
        "processor": "ISCE3_NISAR",
        "analyzer": "ISCE3_Dolphin_NISAR_PL",
        "requires": ("isce3",),
        "creds": ("earthdata",),
    },
}

_BACKEND_PROBES = {
    "mintpy": has_mintpy,
    "isce2": has_isce2,
    "isce3": has_isce3,
    "dolphin": has_dolphin,
    "gmtsar": has_gmtsar,
}

_CRED_PROBES = {
    "earthdata": has_earthdata,
    "cdse": has_cdse,
    "cds": has_cds,
}


def missing_backends(workflow: str) -> list[str]:
    """Backends `workflow` needs that this machine does not have."""
    return [b for b in WORKFLOWS[workflow]["requires"] if not _BACKEND_PROBES[b]()]


def missing_creds(workflow: str) -> list[str]:
    """Credentials `workflow` needs that are not configured."""
    return [c for c in WORKFLOWS[workflow]["creds"] if not _CRED_PROBES[c]()]


def can_run(workflow: str) -> bool:
    return not missing_backends(workflow) and not missing_creds(workflow)


def summary() -> dict[str, bool]:
    """Snapshot of every probe -- used by tier 1 to describe the install."""
    out = {f"backend:{k}": v() for k, v in _BACKEND_PROBES.items()}
    out.update({f"creds:{k}": v() for k, v in _CRED_PROBES.items()})
    out.update({f"workflow:{w}": can_run(w) for w in WORKFLOWS})
    return out
