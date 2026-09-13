"""Shared driver for the tier 3 workflow runs.

Each end-to-end test answers the same question for one pipeline: starting from
nothing, can InSARHub search, download, process and invert a *minimal* stack --
and does the CLI agree with the GUI at every step?

"Minimal" is the whole trick. A real SBAS stack is hundreds of gigabytes and
hours of compute; that is not a test, it is a science run. So each workflow
declares the smallest input that still exercises every stage:

  * the tightest AOI that still intersects a burst
  * the shortest date range that yields enough scenes to form pairs
  * the fewest pairs the analyzer will accept

which for every supported pipeline is 3 scenes / 2-3 interferograms. Below that
the network is degenerate and MintPy refuses to invert, so the run would pass
without testing anything.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path


# ── Minimal real inputs: Parowan Valley, Utah ────────────────────────────────
#
# Parowan Valley (Iron County, UT) is a groundwater-subsidence basin -- the same
# site the Python API walkthrough in docs/quickstart/running.md uses. It is a
# good e2e target because a completed run is *checkable*: there is a known
# subsidence signal, so "the pipeline produced numbers" and "the pipeline
# produced the right numbers" are distinguishable.
#
# The AOI below is deliberately tighter than the one in the docs. The
# documented box ([-113.05, 37.74, -112.68, 38.00]) spans 18 stacks, which is
# right for a tutorial and wrong for a test -- it would download the basin many
# times over. Narrowing to the subsiding valley floor lands inside a SINGLE
# frame, verified against ASF:
#
#   path 20 (ASC), frame 118, 12-day cadence
#   2020-01-01 .. 2020-02-15 -> 4 dates: Jan 3, 15, 27, Feb 8
#   burst level: 020_041033_IW1 and 020_041034_IW1, 4 dates each
#
# Four scenes is one above the floor: three is the minimum that forms a
# non-degenerate SBAS network (two scenes give a single interferogram, which
# every inversion rejects as underdetermined), and the fourth gives the network
# a redundant edge so the inversion is actually exercised.


@dataclass(frozen=True)
class MinimalStack:
    """The smallest real input that still exercises a full pipeline."""

    aoi_wkt: str
    start: str
    end: str
    rel_orbit: int
    frame: int | None = None
    burst_ids: tuple[str, ...] = ()
    expected_dates: tuple[str, ...] = ()
    min_scenes: int = 3
    extra_cli: tuple[str, ...] = field(default_factory=tuple)

    @property
    def aoi_bbox(self) -> list[float]:
        """[min_lon, min_lat, max_lon, max_lat] -- the Python API also takes this."""
        import re

        nums = [float(x) for x in re.findall(r"-?\d+\.?\d*", self.aoi_wkt)]
        lons, lats = nums[0::2], nums[1::2]
        return [min(lons), min(lats), max(lons), max(lats)]


# The subsiding valley floor, ~16 x 20 km. Single frame -- confirmed by query.
PAROWAN = MinimalStack(
    aoi_wkt=(
        "POLYGON((-112.98 37.82, -112.80 37.82, -112.80 38.00, "
        "-112.98 38.00, -112.98 37.82))"
    ),
    start="2020-01-01",
    end="2020-02-15",
    rel_orbit=20,
    frame=118,
    burst_ids=("020_041033_IW1", "020_041034_IW1"),
    expected_dates=("2020-01-03", "2020-01-15", "2020-01-27", "2020-02-08"),
)

# NISAR covers Parowan too, so every workflow shares one site. Different orbit
# and window though: NISAR GSLC only begins in December 2025, so the
# Sentinel-1 window above would match nothing.
#
# Verified against ASF (path 157 / frame 69 has 10 dates between 2025-12-03 and
# 2026-09-05). The window below takes the first four, spaced 12-24 days:
#
#   2025-12-03, 2025-12-27, 2026-01-08, 2026-01-20
#
# An earlier version of this fixture used a 2024 Ridgecrest window, which
# predates the mission entirely and silently matched nothing.
NISAR_AOI = MinimalStack(
    aoi_wkt=PAROWAN.aoi_wkt,
    start="2025-12-01",
    end="2026-01-25",
    rel_orbit=157,
    frame=69,
    expected_dates=("2025-12-03", "2025-12-27", "2026-01-08", "2026-01-20"),
)


def stack_for(workflow: str) -> MinimalStack:
    return NISAR_AOI if "nisar" in workflow else PAROWAN


# ── Process driver ───────────────────────────────────────────────────────────

class CliError(AssertionError):
    """A CLI step failed; carries the full output so the test report is usable."""


def run_cli(*argv: str, workdir: Path, timeout: int = 3600) -> subprocess.CompletedProcess:
    """Run the real `insarhub` console script in `workdir`.

    A subprocess rather than an in-process call, because tier 3 is asking
    whether the *installed entry point* works -- the thing a user types.
    """
    proc = subprocess.run(
        ["insarhub", *argv],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        timeout=timeout,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )
    if proc.returncode != 0:
        raise CliError(
            f"`insarhub {' '.join(argv)}` exited {proc.returncode}\n"
            f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}"
        )
    return proc


def wait_for(predicate, timeout: int, interval: int = 30, what: str = "condition"):
    """Poll until `predicate()` is truthy. Used for HPC/cloud stages."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(interval)
    raise TimeoutError(f"timed out after {timeout}s waiting for {what}")


# ── Workdir inspection ───────────────────────────────────────────────────────

def read_config(workdir: Path) -> dict:
    path = workdir / "insarhub_config.json"
    assert path.is_file(), f"no insarhub_config.json in {workdir}"
    return json.loads(path.read_text())


def assert_workflow_recorded(workdir: Path, *, processor: str = None, analyzer: str = None):
    """The workdir must be stamped with what produced it.

    0.4.0 fixed several analyzers never recording themselves, which left the GUI
    showing no analyzer badge for a folder a CLI run had just completed -- so
    this is a real failure mode, not bookkeeping.
    """
    cfg = read_config(workdir)
    if processor:
        assert cfg.get("processor", {}).get("type") == processor or cfg.get("processor") == processor, (
            f"workdir does not record processor={processor}: {cfg}"
        )
    if analyzer:
        recorded = cfg.get("analyzer")
        recorded = recorded.get("type") if isinstance(recorded, dict) else recorded
        assert recorded == analyzer, f"workdir does not record analyzer={analyzer}: {cfg}"


def find_outputs(workdir: Path, pattern: str) -> list[Path]:
    return sorted(workdir.rglob(pattern))


def assert_produced(workdir: Path, pattern: str, minimum: int = 1) -> list[Path]:
    hits = find_outputs(workdir, pattern)
    assert len(hits) >= minimum, (
        f"expected at least {minimum} file(s) matching {pattern!r} under {workdir}, "
        f"found {len(hits)}"
    )
    return hits


# ── GUI/CLI parity ───────────────────────────────────────────────────────────

def api_sees_workdir(client, workdir: Path) -> dict:
    """The GUI must see the folder a CLI run just produced.

    This is the parity check that matters: the two front ends share one
    discovery path (utils/local_processor_reload.py), and a workdir the CLI
    wrote but the GUI cannot reload is a class of bug that has shipped before.
    """
    response = client.get("/api/folder-details", params={"path": str(workdir)})
    assert response.status_code == 200, (
        f"GUI cannot read the workdir the CLI produced: "
        f"{response.status_code} {response.text[:400]}"
    )
    return response.json()
