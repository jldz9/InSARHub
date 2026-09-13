"""Tier 3 fixtures.

Tier 3 is the opposite of the other tiers: real network, real backends, real
data on disk. It only runs when asked for (`pytest -m e2e`) and skips loudly
rather than failing when the machine cannot support a given workflow.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import _backends  # noqa: E402

# Measured on a real Parowan run: four SLCs are ~48 GB when kept as both .zip
# and extracted .SAFE (GMTSAR keeps both), and GMTSAR's per-pair case dirs
# added another ~14 GB before finishing. 60 GB was not enough -- a single
# local workflow needs roughly twice that.
MIN_FREE_GB = 150


def pytest_configure(config):
    config.addinivalue_line("markers", "workflow(name): the pipeline under test")


@pytest.fixture(scope="session")
def e2e_root() -> Path:
    """Where runs are written.

    Defaults to a temp dir, but INSARHUB_E2E_DIR lets a developer point at fast
    local storage and keep the downloaded scenes between runs -- re-downloading
    several GB per invocation makes the suite unusable otherwise.
    """
    root = os.environ.get("INSARHUB_E2E_DIR")
    if root:
        path = Path(root).expanduser().resolve()
        path.mkdir(parents=True, exist_ok=True)
    else:
        import tempfile

        path = Path(tempfile.mkdtemp(prefix="insarhub-e2e-"))

    # Local processing is disk-hungry in a way that is easy to miss: four
    # Sentinel-1 SLCs are ~16 GB before ISCE2/GMTSAR write a single
    # intermediate. On many systems /tmp is a tmpfs sized in single-digit GB,
    # so the default temp dir silently guarantees a mid-run ENOSPC hours in.
    free_gb = shutil.disk_usage(path).free / 2**30
    if free_gb < MIN_FREE_GB:
        pytest.skip(
            f"tier 3 needs ~{MIN_FREE_GB} GB free for SLCs and intermediates; "
            f"{path} has {free_gb:.1f} GB. Point INSARHUB_E2E_DIR at a larger "
            "filesystem (note /tmp is often a small tmpfs)."
        )
    return path


@pytest.fixture
def require_workflow():
    """Skip unless this machine can actually run `workflow`.

    Reports precisely what is missing, so a skip is actionable rather than
    mysterious.
    """

    def _require(workflow: str):
        missing_backends = _backends.missing_backends(workflow)
        if missing_backends:
            pytest.skip(f"{workflow}: missing backend(s) {missing_backends}")
        missing_creds = _backends.missing_creds(workflow)
        if missing_creds:
            pytest.skip(f"{workflow}: missing credential(s) {missing_creds}")

    return _require


@pytest.fixture(scope="session")
def slc_cache(e2e_root):
    """One SLC download per (downloader, stack), shared by every workflow.

    Three of the five workflows use S1_SLC over the same Parowan stack
    (hyp3_s1, isce2_s1, gmtsar_s1), and giving each its own workdir made each
    re-download the *same four scenes* -- ~48 GB and a long transfer per
    workflow, for byte-identical data. Both local processor configs take an
    explicit `slc_dir`, and the CLI accepts `--slc_dir`, so the download can be
    done once and pointed at.

    Returns the directory; populating it is the caller's job, so a workflow that
    does not need local SLCs (HyP3 processes in the cloud) never triggers one.
    """
    roots: dict[tuple, Path] = {}

    def _dir(spec, stack) -> Path:
        key = (spec["downloader"], stack.rel_orbit, stack.frame)
        if key not in roots:
            name = f"{spec['downloader']}_p{stack.rel_orbit}_f{stack.frame}"
            path = Path(e2e_root) / "_slc_cache" / name
            path.mkdir(parents=True, exist_ok=True)
            _adopt_existing_downloads(Path(e2e_root), path, stack)
            roots[key] = path
        return roots[key]

    return _dir


def _adopt_existing_downloads(e2e_root: Path, cache: Path, stack) -> None:
    """Link scenes an earlier run already downloaded into the shared cache.

    Without this the cache starts empty on a machine that already has the data
    -- from a run predating the cache, or one interrupted partway -- and every
    workflow re-downloads ~17 GB it is sitting on. Symlinks rather than copies:
    the point is to avoid a second copy, not to make one.

    Only the stack's own p<path>_f<frame>/slc directories are considered, so
    this cannot pull in scenes from a different AOI or date range.
    """
    if any(cache.glob("*.zip")) or any(cache.glob("*.SAFE")):
        return

    # .SAFE is a DIRECTORY, and GMTSAR needs it: pairs_from_downloader builds
    # (ref_safe, ref_eof, sec_safe, sec_eof) from .SAFE names, so a cache
    # holding only .zip/.EOF leaves GMTSAR with nothing to point at. ISCE2 works
    # from the .zip, so an earlier version that linked only files happened to
    # suit ISCE2 and silently broke GMTSAR.
    stack_folder = f"p{stack.rel_orbit}_f{stack.frame}"
    wanted = {".zip", ".eof", ".safe"}
    linked = 0
    for slc_dir in sorted(e2e_root.glob(f"*/{stack_folder}/slc")):
        if not slc_dir.is_dir() or cache in slc_dir.parents:
            continue
        for src in sorted(slc_dir.iterdir()):
            if src.suffix.lower() not in wanted:
                continue
            link = cache / src.name
            if not link.exists():
                link.symlink_to(src.resolve())   # works for dirs as well as files
                linked += 1
        if linked:
            break

    if linked:
        print(f"[e2e] adopted {linked} already-downloaded item(s) into {cache}")


@pytest.fixture
def workflow_dir(e2e_root):
    """A per-workflow directory, reused across runs when INSARHUB_E2E_DIR is set."""

    def _dir(workflow: str) -> Path:
        path = e2e_root / workflow
        path.mkdir(parents=True, exist_ok=True)
        return path

    return _dir


@pytest.fixture
def live_api():
    """A TestClient over the real app with nothing stubbed.

    Tier 3 deliberately does NOT neuter the auth probe the way tier 2 does --
    checking that real credentials are visible to the GUI is part of the point.
    """
    from fastapi.testclient import TestClient

    from insarhub.app.api import app

    with TestClient(app) as client:
        yield client
