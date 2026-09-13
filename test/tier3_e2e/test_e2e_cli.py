"""Tier 3 (CLI) -- the full pipeline driven the way a user types it.

Every command runs as a real `insarhub` subprocess, not an in-process call:
tier 3 is asking whether the *installed console script* works, which includes
entry-point wiring, argument parsing and exit codes. An in-process call would
skip all three.

The argv shapes here are load-bearing and easy to get wrong:

  * `-N/--name` selects the component for all three commands
  * for `processor`/`analyzer` it belongs to the PARENT parser, so it must come
    BEFORE the subcommand -- `insarhub processor -N X submit`, never
    `insarhub processor submit -N X`, which silently submits the default
  * `--AOI` takes the WKT; `--start`, `--end` and `--relativeOrbit` are not
    declared flags but config-dataclass field names the CLI forwards through

Site: Parowan Valley, Utah -- see _harness.py.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import _backends
from _harness import assert_produced, assert_workflow_recorded, run_cli, stack_for

pytestmark = [pytest.mark.e2e, pytest.mark.needs_network, pytest.mark.needs_creds]


# ── argv builders ────────────────────────────────────────────────────────────

def search_argv(spec, stack, workdir, *extra: str) -> list[str]:
    return [
        "downloader",
        "-N", spec["downloader"],
        "-w", str(workdir),
        "--AOI", stack.aoi_wkt,
        "--start", stack.start,
        "--end", stack.end,
        "--relativeOrbit", str(stack.rel_orbit),
        *extra,
    ]


def _stack_selector(workflow: str, stack) -> list[str]:
    """`--stacks PATH:FRAME`, or PATH:BURST_ID for the burst downloader."""
    if stack.burst_ids and "burst" in workflow:
        return ["--stacks", f"{stack.rel_orbit}:{stack.burst_ids[0]}"]
    if stack.frame is not None:
        return ["--stacks", f"{stack.rel_orbit}:{stack.frame}"]
    return []


def _builds_own_network(processor_name: str) -> bool:
    """True when the processor derives its own network and takes no pairs.

    ISCE3_Burst and ISCE3_NISAR hand the downloaded stack to dolphin phase
    linking, which forms interferograms from the phase-linked SLCs using an
    index/temporal network -- there is no pair list to supply and no
    perpendicular-baseline criterion. `insarhub processor submit` skips the
    pairs file for them (cli/main.py `_needs_pairs`), so passing
    `--select-pairs` is at best wasted work and, for NISAR, produces nothing:
    ASF publishes no baseline stack for those granules.
    """
    from insarhub import Processor

    cls = Processor._registry.get(processor_name)
    return bool(getattr(cls, "builds_own_network", False))


def download(workflow, spec, stack, workdir):
    """Search, download SLCs and orbits -- and select pairs when they are used.

    `--select-pairs` writes the stack_*.json that `processor submit` reads. For
    pair-based processors it is not optional: without it the processor exits 1
    with "No pairs file found", which looks like a processor bug and is a
    missing step.

    For a `builds_own_network` processor it is skipped, matching what the CLI
    itself does -- the downloader would otherwise warn that the pair list is not
    used, and for NISAR it cannot even be built.

    `-O` fetches the orbit .EOF files. GMTSAR needs them by name, and InSARHub
    expands the downloader's 2-tuples into GMTSAR's 4-tuples itself
    (cli/main.py -> pairs_from_downloader) -- the test does not build them.
    """
    pair_args = [] if _builds_own_network(spec["processor"]) else ["--select-pairs"]
    run_cli(
        *search_argv(spec, stack, workdir,
                     *_stack_selector(workflow, stack),
                     *pair_args, "-d", "-O"),
        workdir=workdir, timeout=7200,
    )


def stack_dir(workdir, stack) -> Path:
    """The p<path>_f<frame> folder the downloader creates for the stack.

    Every later command operates on that folder, not on the parent the search
    ran in.
    """
    candidates = sorted(Path(workdir).glob("p*_f*"))
    assert candidates, f"downloader created no p*_f* stack folder under {workdir}"
    return candidates[0]


def process(spec, workdir, timeout=14400, slc_dir=None):
    """Submit, THEN wait. `submit` alone does not finish the processing.

    `insarhub processor --help` says it plainly for the local processors:
    "submit  Submit pairs to local ISCE2 processor (runs in background)". The
    command returns in seconds while the work continues in a background
    executor, which is why the CLI ships a separate `watch` subcommand.

    Running `analyzer run` straight after `submit` therefore fails with
    "No interferogram directories in .../merged/interferograms" while the
    processing is in fact running fine and goes on to succeed -- it reads like
    a broken analyzer and is a missing wait. Observed on a real ISCE2 run.
    """
    # --slc_dir is forwarded onto the processor config, so a shared SLC
    # cache is reused instead of re-downloading the same scenes per workflow.
    extra = ["--slc_dir", str(slc_dir)] if slc_dir else []
    run_cli("processor", "-N", spec["processor"], "-w", str(workdir), "submit",
            *extra, workdir=workdir, timeout=600)
    run_cli("processor", "-N", spec["processor"], "-w", str(workdir), "watch",
            workdir=workdir, timeout=timeout)


def analyze(spec, workdir, timeout=7200):
    run_cli("analyzer", "-N", spec["analyzer"], "-w", str(workdir), "run",
            workdir=workdir, timeout=timeout)


# ── Search ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("workflow", sorted(_backends.WORKFLOWS))
def test_cli_search_finds_the_minimal_stack(workflow, require_workflow, workflow_dir):
    require_workflow(workflow)
    spec, stack = _backends.WORKFLOWS[workflow], stack_for(workflow)
    workdir = workflow_dir(workflow)

    proc = run_cli(*search_argv(spec, stack, workdir), workdir=workdir, timeout=900)
    assert proc.stdout.strip(), "search printed nothing"


def test_cli_stack_selector_narrows_to_one_stack(require_workflow, workflow_dir):
    """`--stacks 20:118` must select, and must fail loudly when it matches nothing.

    Both halves were 0.4.0 bugs: burst selectors matched nothing at all, and a
    selector that matched nothing exited 0 and processed every stack.
    """
    require_workflow("hyp3_s1")
    spec, stack = _backends.WORKFLOWS["hyp3_s1"], stack_for("hyp3_s1")
    workdir = workflow_dir("hyp3_s1")

    good = run_cli(
        *search_argv(spec, stack, workdir, "--stacks", f"{stack.rel_orbit}:{stack.frame}"),
        workdir=workdir, timeout=900,
    )
    assert good.stdout.strip()

    from _harness import CliError

    with pytest.raises(CliError):
        run_cli(
            *search_argv(spec, stack, workdir, "--stacks", f"{stack.rel_orbit}:999999"),
            workdir=workdir, timeout=900,
        )


# ── Full pipeline ────────────────────────────────────────────────────────────

@pytest.mark.needs_mintpy
def test_hyp3_full_pipeline_via_cli(require_workflow, workflow_dir):
    workflow = "hyp3_s1"
    require_workflow(workflow)
    spec, stack = _backends.WORKFLOWS[workflow], stack_for(workflow)
    workdir = workflow_dir(workflow)

    download(workflow, spec, stack, workdir)
    sdir = stack_dir(workdir, stack)
    process(spec, sdir, timeout=21600)      # cloud queue time dominates
    analyze(spec, sdir)

    assert_workflow_recorded(sdir, processor=spec["processor"], analyzer=spec["analyzer"])
    assert_produced(sdir, "*timeseries*.h5")
    assert_produced(sdir, "*velocity*.h5")


@pytest.mark.needs_isce2
@pytest.mark.needs_mintpy
def test_isce2_full_pipeline_via_cli(require_workflow, workflow_dir, slc_cache):
    workflow = "isce2_s1"
    require_workflow(workflow)
    spec, stack = _backends.WORKFLOWS[workflow], stack_for(workflow)
    workdir = workflow_dir(workflow)

    download(workflow, spec, stack, workdir)
    sdir = stack_dir(workdir, stack)
    process(spec, sdir)
    analyze(spec, sdir)

    assert_workflow_recorded(sdir, processor=spec["processor"], analyzer=spec["analyzer"])
    assert_produced(sdir, "filt*.unw*", minimum=2)
    assert_produced(sdir, "*timeseries*.h5")


@pytest.mark.needs_gmtsar
@pytest.mark.needs_mintpy
def test_gmtsar_full_pipeline_via_cli(require_workflow, workflow_dir, slc_cache):
    workflow = "gmtsar_s1"
    require_workflow(workflow)
    spec, stack = _backends.WORKFLOWS[workflow], stack_for(workflow)
    workdir = workflow_dir(workflow)

    download(workflow, spec, stack, workdir)
    sdir = stack_dir(workdir, stack)
    process(spec, sdir)
    analyze(spec, sdir)

    assert_workflow_recorded(sdir, processor=spec["processor"], analyzer=spec["analyzer"])
    assert_produced(sdir, "*unwrap*.grd", minimum=2)


@pytest.mark.needs_isce3
def test_isce3_burst_full_pipeline_via_cli(require_workflow, workflow_dir):
    workflow = "isce3_burst"
    require_workflow(workflow)
    spec, stack = _backends.WORKFLOWS[workflow], stack_for(workflow)
    workdir = workflow_dir(workflow)

    download(workflow, spec, stack, workdir)
    sdir = stack_dir(workdir, stack)
    process(spec, sdir)
    analyze(spec, sdir)

    assert_workflow_recorded(sdir, processor=spec["processor"], analyzer=spec["analyzer"])


@pytest.mark.needs_isce3
def test_isce3_nisar_full_pipeline_via_cli(require_workflow, workflow_dir):
    workflow = "isce3_nisar"
    require_workflow(workflow)
    spec, stack = _backends.WORKFLOWS[workflow], stack_for(workflow)
    workdir = workflow_dir(workflow)

    download(workflow, spec, stack, workdir)
    sdir = stack_dir(workdir, stack)
    process(spec, sdir)
    analyze(spec, sdir)

    assert_workflow_recorded(sdir, processor=spec["processor"], analyzer=spec["analyzer"])
