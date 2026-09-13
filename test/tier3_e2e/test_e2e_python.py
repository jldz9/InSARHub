"""Tier 3 (Python API) -- the full pipeline driven the way the docs teach it.

Mirrors docs/quickstart/running.md: Downloader.create -> filter -> select_pairs
-> Processor.create -> submit -> Analyzer.create -> prep_data -> run.

This is the interface with the least insulation: the CLI and the GUI both sit on
top of it, so a break here breaks all three, and a break *only* here means one
of the other two is papering over it.

Site: Parowan Valley, Utah. See _harness.py for why, and for the single-frame
4-scene window these tests use.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import _backends
from _harness import assert_produced, assert_workflow_recorded, stack_for

pytestmark = [pytest.mark.e2e, pytest.mark.needs_network, pytest.mark.needs_creds]


# ── Search ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("workflow", sorted(_backends.WORKFLOWS))
def test_search_returns_the_expected_minimal_stack(workflow, require_workflow, workflow_dir):
    """The cheapest real check, and the one that localises blame.

    A failure here is ASF, credentials or the query -- not the processor. Worth
    separating before anything spends an hour on compute.
    """
    require_workflow(workflow)
    from insarhub import Downloader

    spec = _backends.WORKFLOWS[workflow]
    stack = stack_for(workflow)

    dl = Downloader.create(
        spec["downloader"],
        intersectsWith=stack.aoi_bbox,
        start=stack.start,
        end=stack.end,
        relativeOrbit=stack.rel_orbit,
        workdir=str(workflow_dir(workflow)),
    )
    results = dl.search()
    assert results, f"{spec['downloader']} found nothing over Parowan Valley"


def test_parowan_search_finds_exactly_the_documented_dates(require_workflow, workflow_dir):
    """Pin the fixture itself.

    The 4-date/single-frame claim in _harness.py is what makes these tests cheap.
    If ASF's holdings or the frame boundary shift, every downstream timing and
    size assumption is wrong -- better to learn it here than from a run that
    quietly downloads eight scenes.
    """
    require_workflow("hyp3_s1")
    from insarhub import Downloader

    stack = stack_for("hyp3_s1")
    dl = Downloader.create(
        "S1_SLC",
        intersectsWith=stack.aoi_bbox,
        start=stack.start,
        end=stack.end,
        relativeOrbit=stack.rel_orbit,
        workdir=str(workflow_dir("hyp3_s1")),
    )
    dl.search()

    keys = set(getattr(dl, "results", {}))
    assert len(keys) == 1, (
        f"the Parowan AOI should fall inside ONE stack, got {sorted(keys)}. "
        "Re-tighten the AOI in _harness.py or the tests will download extra data."
    )

    (only,) = keys
    assert only[0] == stack.rel_orbit, f"expected path {stack.rel_orbit}, got {only}"

    scenes = dl.results[only]
    assert len(scenes) >= stack.min_scenes, (
        f"only {len(scenes)} scenes; need >= {stack.min_scenes} for a "
        "non-degenerate SBAS network"
    )


# ── Pair selection ───────────────────────────────────────────────────────────

def test_select_pairs_builds_a_usable_network(require_workflow, workflow_dir):
    """Three scenes is the floor; this proves the window clears it."""
    require_workflow("hyp3_s1")
    from insarhub import Downloader

    stack = stack_for("hyp3_s1")
    dl = Downloader.create(
        "S1_SLC",
        intersectsWith=stack.aoi_bbox,
        start=stack.start,
        end=stack.end,
        relativeOrbit=stack.rel_orbit,
        workdir=str(workflow_dir("hyp3_s1")),
    )
    dl.search()
    dl.filter(path_frame=[(stack.rel_orbit, stack.frame)])

    out = dl.select_pairs(max_degree=2)
    pair_stacks = out[0] if isinstance(out, tuple) else out
    assert pair_stacks, "select_pairs produced no interferogram pairs"


# ── Full pipeline ────────────────────────────────────────────────────────────

def _builds_own_network(processor_name: str) -> bool:
    """True when the processor derives its own network and takes no pairs.

    ISCE3_Burst and ISCE3_NISAR are stack-download workflows: dolphin phase
    linking builds the network from whatever is in slc/, so there is nothing to
    pair up beforehand. Both the CLI (cli/main.py `_needs_pairs`) and the GUI
    (routes/processor.py `_needs_stack_file`) branch on this same attribute.

    Calling select_pairs for them is not merely redundant, it fails: NISAR GSLC
    products carry no ASF baseline stack, so the pair selector finds no
    perpendicular baselines and returns zero pairs. That looked like "the NISAR
    workflow is broken" when it was this harness imposing a pair-based
    Sentinel-1 flow on a stack-based one.
    """
    from insarhub import Processor

    cls = Processor._registry.get(processor_name)
    return bool(getattr(cls, "builds_own_network", False))


def _search_and_select(spec, stack, workdir):
    """Search -> filter -> (select_pairs, when the processor needs pairs).

    For pair-based processors this mirrors docs/quickstart/running.md.
    select_pairs returns SIX values, and the first is a dict keyed by
    (path, frame) -- not a flat list of pairs. Passing that dict straight to
    Processor.create raises "Invalid pairs format"; the docs iterate it.
    """
    from insarhub import Downloader

    dl = Downloader.create(
        spec["downloader"],
        intersectsWith=stack.aoi_bbox,
        start=stack.start,
        end=stack.end,
        relativeOrbit=stack.rel_orbit,
        workdir=str(workdir),
    )
    dl.search()
    selector = stack.burst_ids[0] if stack.burst_ids and spec["downloader"] == "S1_Burst" else stack.frame
    dl.filter(path_frame=[(stack.rel_orbit, selector)])

    if _builds_own_network(spec["processor"]):
        # One "stack" with no pairs; the processor reads slc/ itself.
        return dl, {(stack.rel_orbit, selector): []}

    pair_stacks = dl.select_pairs(max_degree=2)[0]
    assert pair_stacks, "select_pairs produced no pairs"
    return dl, pair_stacks


def _stack_workdir(root, path, frame):
    """Each stack gets its own p<path>_f<frame> folder, as the docs show."""
    return Path(root) / f"p{path}_f{frame}"


def _parallelism() -> dict:
    """Worker counts for the local processors, from the environment.

    InSARHub's defaults (max_workers=4, num_proc=4, num_proc4topo=6) are tuned
    for a modest machine. A test must not silently pick different numbers --
    throughput is a property of the host, not of the pipeline -- so this stays
    empty unless INSARHUB_E2E_WORKERS is set, and then scales the three knobs
    together:

        INSARHUB_E2E_WORKERS=8 pytest -m e2e -k isce2

    Sizing is bounded by memory, not cores: ISCE2's resample and topo steps hold
    several GB per process, so on a 16-core / 48 GB box 8 is comfortable and 16
    risks an OOM that costs hours of work.
    """
    import os

    raw = os.environ.get("INSARHUB_E2E_WORKERS")
    if not raw:
        return {}
    n = max(1, int(raw))
    return {"max_workers": n, "num_proc": n, "num_proc4topo": n}


def _local_processor(spec, stack, pairs, stack_dir, slc_dir=None):
    """Build a local processor the way its own docs do.

    The three backends do NOT take the same inputs, and a generic call fails on
    two of them:

      Hyp3_S1     pairs=<2-tuples>, no config -- processing is in the cloud
      ISCE2_S1    pairs=<2-tuples> + ISCE2_S1_Config(bbox=, slc_dir=)
      GMTSAR_S1   pairs=<4-TUPLES> (ref_safe, ref_eof, sec_safe, sec_eof)
                  + GMTSAR_S1_Config(slc_dir=, orbit_dir=)

    select_pairs returns bare ASF scene-name 2-tuples with no .SAFE suffix and
    no .EOF orbit name, so GMTSAR rejects them outright. cli/main.py expands
    them with pairs_from_downloader(); this does the same rather than
    reimplementing the mapping.
    """
    from insarhub import Processor

    name = spec["processor"]
    # Default to this stack's own slc/, but allow a shared cache so that
    # ISCE2 and GMTSAR do not each re-download the same four scenes.
    slc_dir = Path(slc_dir) if slc_dir else stack_dir / "slc"

    if name == "ISCE2_S1":
        from insarhub.config import ISCE2_S1_Config

        west, south, east, north = stack.aoi_bbox
        cfg = ISCE2_S1_Config(
            workdir=str(stack_dir),
            bbox=[south, north, west, east],      # ISCE2 wants [S, N, W, E]
            slc_dir=str(slc_dir),
            **_parallelism(),
        )
        return Processor.create(name, pairs=pairs, config=cfg)

    if name == "GMTSAR_S1":
        from insarhub.config import GMTSAR_S1_Config
        from insarhub.processor.gmtsar_s1 import pairs_from_downloader

        # GMTSAR takes 4-tuples (ref_safe, ref_eof, sec_safe, sec_eof) while the
        # downloader yields bare scene-name 2-tuples. InSARHub does that
        # expansion itself -- pairs_from_downloader is the same function
        # cli/main.py calls -- so the test delegates rather than deriving
        # .SAFE/.EOF names of its own. Everything else is left at InSARHub's
        # defaults: no subswath, no dem_path, so the processor makes its own
        # processing decisions the way it would for a user.
        cfg = GMTSAR_S1_Config(
            workdir=str(stack_dir),
            slc_dir=str(slc_dir),
            orbit_dir=str(slc_dir),
        )
        four = pairs_from_downloader(
            [tuple(str(x) for x in pr) for pr in pairs],
            slc_dir=str(slc_dir), orbit_dir=str(slc_dir),
        )
        return Processor.create(name, pairs=four, config=cfg)

    if name in ("ISCE3_Burst", "ISCE3_NISAR"):
        from insarhub.config import ISCE3_Burst_Config, ISCE3_NISAR_Config

        # ISCE3 needs an explicit processing extent. Without one the `dem` stage
        # fails immediately with "could not determine a processing extent. No
        # config.AOI, no intersectsWith in this folder's insarhub_config.json,
        # and no geocoded bursts on disk to measure."
        #
        # The insarhub_config.json fallback does not save us here: the
        # downloader writes its config to the PARENT workdir, while the
        # processor looks in its own stack folder, so that file holds no
        # intersectsWith.
        #
        # slc_dir likewise points at the parent: S1_Burst assembles the bursts
        # into .SAFE directories under <root>/slc, not under the stack folder.
        cfg_cls = ISCE3_Burst_Config if name == "ISCE3_Burst" else ISCE3_NISAR_Config
        # AOI must be the 4-element bbox (W, S, E, N), NOT a WKT string.
        # ISCE3_Burst._resolve_aoi() only handles `len(aoi) == 4`, so a WKT
        # falls straight through to "could not determine a processing extent"
        # -- even though the field is annotated `str | list[float]` and the
        # same field name on GMTSAR_S1_Config documents WKT as accepted.
        cfg = cfg_cls(
            workdir=str(stack_dir),
            AOI=stack.aoi_bbox,
            slc_dir=str(Path(stack_dir).parent / "slc"),
        )
        return Processor.create(name, pairs=pairs, config=cfg)

    return Processor.create(name, pairs=pairs, workdir=str(stack_dir))


def _wait_until_done(processor, watch, interval: int, name: str,
                     timeout: int = 21600) -> None:
    """Block until the processor's stages are finished, whatever watch() means.

    watch() is not one behaviour across processors:

      Hyp3Base / ISCE2_Base / GMTSAR_S1   loop internally until everything is
                                          SUCCEEDED or FAILED
      ISCE3_Base                          runs a single refresh() and returns
                                          ("this only reports state; call it
                                          repeatedly to poll" -- its docstring)

    So calling watch() once is right for three of them and useless for the
    fourth, with no error to say so: the analyzer then runs against an empty
    folder and reports "no unwrapped interferograms ... run the processor's
    'unwrap' stage first", blaming the analyzer for a processor that never
    finished. Polling the status ourselves works for both shapes.
    """
    import time

    deadline = time.monotonic() + timeout
    while True:
        watch(interval)                     # blocking impls return only when done
        jobs = getattr(processor, "jobs", None) or {}
        statuses = {
            (v.get("status") if isinstance(v, dict) else str(v))
            for v in jobs.values()
        }
        pending = statuses - {"SUCCEEDED", "FAILED"}
        if not pending:
            if "FAILED" in statuses:
                raise AssertionError(f"{name}: stage(s) FAILED -- {jobs}")
            return
        if time.monotonic() > deadline:
            raise TimeoutError(
                f"{name}: still {sorted(pending)} after {timeout}s: {jobs}"
            )
        time.sleep(interval)


def _run_pipeline(spec, stack, workdir, *, download_slcs: bool,
                  watch_interval: int = 60, slc_dir=None):
    """Drive one stack all the way through, returning its workdir.

    submit() is ASYNCHRONOUS for every processor, cloud and local alike -- it
    queues the work and returns. Hyp3_S1 leaves jobs in ASF's cloud; ISCE2_S1
    and GMTSAR_S1 hand their run steps to a background executor. Either way
    submit() comes back in seconds while the real work takes tens of minutes,
    so the analyzer must not be called until watch() says the processing has
    finished.

    Getting this wrong does not look like a sequencing mistake. It surfaces as
    an analyzer error about missing inputs -- "Missing rasters: ['unw_phase',
    'corr', 'dem']" for HyP3, "No interferogram directories in .../merged/
    interferograms" for ISCE2 -- which reads like a broken analyzer while the
    processing is in fact running fine in the background and goes on to
    succeed. Both were observed on real runs.
    """
    from insarhub import Analyzer

    dl, pair_stacks = _search_and_select(spec, stack, workdir)
    if download_slcs:
        # Skip the transfer when the shared cache already holds this stack --
        # the scenes are identical across every workflow using this downloader.
        cached = list(Path(slc_dir).glob("*.zip")) if slc_dir else []
        if cached:
            print(f"[e2e] reusing {len(cached)} cached SLC(s) from {slc_dir}")
        else:
            dl.download()

    produced = []
    for (path, frame), pairs in pair_stacks.items():
        stack_dir = _stack_workdir(workdir, path, frame)
        processor = _local_processor(spec, stack, pairs, stack_dir, slc_dir=slc_dir)
        processor.submit()
        processor.save()

        # Block until the processor reports everything finished. watch() polls
        # and, for HyP3, downloads each product as it lands.
        #
        # Called POSITIONALLY on purpose: the four processors spell the same
        # parameter three different ways -- Hyp3Base/ISCE2_Base take
        # `refresh_interval`, GMTSAR_S1 takes `poll_interval`, ISCE3_Base takes
        # `interval`. Passing it by keyword works for two of them and raises
        # TypeError on the others.
        watch = getattr(processor, "watch", None)
        assert callable(watch), (
            f"{spec['processor']} has no watch(); the test cannot tell when its "
            "asynchronous submit() has actually finished"
        )
        _wait_until_done(processor, watch, watch_interval, spec["processor"])

        analyzer = Analyzer.create(spec["analyzer"], workdir=str(stack_dir))
        analyzer.prep_data()
        analyzer.run()
        produced.append(stack_dir)
    return produced


@pytest.mark.needs_mintpy
def test_hyp3_full_pipeline_via_python(require_workflow, workflow_dir):
    """S1_SLC -> Hyp3_S1 -> Hyp3_Mintpy_SBAS, entirely through the Python API.

    HyP3 makes the interferograms in the cloud, so this is the pipeline that
    needs no local InSAR backend -- the one most users start with, and the only
    one this machine can run without ISCE2/GMTSAR/ISCE3.
    """
    workflow = "hyp3_s1"
    require_workflow(workflow)

    spec = _backends.WORKFLOWS[workflow]
    stack = stack_for(workflow)
    produced = _run_pipeline(spec, stack, workflow_dir(workflow),
                             download_slcs=False, watch_interval=120)

    assert produced, "no stack workdir was produced"
    for d in produced:
        assert_workflow_recorded(d, processor=spec["processor"], analyzer=spec["analyzer"])
        assert_produced(d, "*timeseries*.h5")
        assert_produced(d, "*velocity*.h5")


@pytest.mark.needs_isce2
@pytest.mark.needs_mintpy
def test_isce2_full_pipeline_via_python(require_workflow, workflow_dir, slc_cache):
    """S1_SLC -> ISCE2_S1 -> ISCE2_Mintpy_SBAS, locally."""
    workflow = "isce2_s1"
    require_workflow(workflow)

    spec = _backends.WORKFLOWS[workflow]
    stack = stack_for(workflow)
    produced = _run_pipeline(spec, stack, workflow_dir(workflow),
                             download_slcs=True, slc_dir=slc_cache(spec, stack))

    assert produced, "no stack workdir was produced"
    for d in produced:
        assert_workflow_recorded(d, processor=spec["processor"], analyzer=spec["analyzer"])
        assert_produced(d, "*timeseries*.h5")


@pytest.mark.needs_gmtsar
@pytest.mark.needs_mintpy
def test_gmtsar_full_pipeline_via_python(require_workflow, workflow_dir, slc_cache):
    """S1_SLC -> GMTSAR_S1 -> GMTSAR_Mintpy_SBAS, locally."""
    workflow = "gmtsar_s1"
    require_workflow(workflow)

    spec = _backends.WORKFLOWS[workflow]
    stack = stack_for(workflow)
    produced = _run_pipeline(spec, stack, workflow_dir(workflow),
                             download_slcs=True, slc_dir=slc_cache(spec, stack))

    assert produced, "no stack workdir was produced"
    for d in produced:
        assert_workflow_recorded(d, processor=spec["processor"], analyzer=spec["analyzer"])



@pytest.mark.needs_isce3
def test_isce3_burst_full_pipeline_via_python(require_workflow, workflow_dir, slc_cache):
    """S1_Burst -> ISCE3_Burst -> ISCE3_Dolphin_S1_PL.

    Selects an explicit burst ID rather than taking every stack -- that selector
    is where the 0.4.0 `--stacks` bug lived, and the burst IDs over Parowan are
    pinned in _harness.py.
    """
    workflow = "isce3_burst"
    require_workflow(workflow)

    spec = _backends.WORKFLOWS[workflow]
    stack = stack_for(workflow)
    produced = _run_pipeline(spec, stack, workflow_dir(workflow),
                             download_slcs=True, slc_dir=slc_cache(spec, stack))

    assert produced, "no stack workdir was produced"
    for d in produced:
        assert_workflow_recorded(d, processor=spec["processor"], analyzer=spec["analyzer"])


@pytest.mark.needs_isce3
def test_isce3_nisar_full_pipeline_via_python(require_workflow, workflow_dir, slc_cache):
    """NISAR_GSLC -> ISCE3_NISAR -> ISCE3_Dolphin_NISAR_PL.

    GSLC products are already geocoded, so this exercises the AOI-crop-first
    path rather than the burst assembly ISCE3_Burst needs.

    Uses the same Parowan Valley AOI as every other workflow, but its own orbit
    and window: NISAR GSLC only starts in December 2025, so the Sentinel-1
    window matches nothing. See NISAR_AOI in _harness.py.

    0.4.0 split the dolphin analyzer per sensor because NISAR stacks were
    silently inheriting the Sentinel-1 C-band wavelength -- a wrong number
    rather than a crash -- so a completed run here is only meaningful if the
    wavelength came from the GSLC metadata.
    """
    workflow = "isce3_nisar"
    require_workflow(workflow)

    spec = _backends.WORKFLOWS[workflow]
    stack = stack_for(workflow)
    produced = _run_pipeline(spec, stack, workflow_dir(workflow),
                             download_slcs=True, slc_dir=slc_cache(spec, stack))

    assert produced, "no stack workdir was produced"
    for d in produced:
        assert_workflow_recorded(d, processor=spec["processor"], analyzer=spec["analyzer"])
