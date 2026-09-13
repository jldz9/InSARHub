"""Tier 3 (GUI) -- the full pipeline driven through the HTTP API the browser uses.

Every call here is one the React frontend makes. Driving the real endpoints is
the only way to exercise what a GUI user actually hits: request models, the
background-job machinery, and the folder-discovery path the panels read from.
Calling the Python API instead would test none of it.

Two things this catches that the other two interfaces cannot:

  * a job submitted through /api/... that never reports completion, because the
    GUI polls /api/jobs/{id} rather than blocking
  * a workdir the CLI or Python API produced that the GUI cannot reload -- the
    folder shows up with no processor, no analyzer badge and no status, which
    has shipped before

Site: Parowan Valley, Utah -- see _harness.py.
"""

from __future__ import annotations

import time

import pytest

import _backends
from _harness import assert_produced, assert_workflow_recorded, stack_for

pytestmark = [pytest.mark.e2e, pytest.mark.needs_network, pytest.mark.needs_creds]

JOB_POLL_SECONDS = 10


def _wait_for_job(client, job_id: str, timeout: int, what: str = "job") -> dict:
    """Poll /api/jobs/{id} the way the frontend does.

    The GUI never blocks on a long operation -- it starts one and polls -- so a
    test that waited on the response would exercise a code path no user takes.

    CAREFUL: for /api/folder-process this reports "done" as soon as the work is
    SUBMITTED, not when it finishes -- routes/processor.py calls _finish_job
    with status="done", progress=100 and the message "Processing running in
    background." Use _wait_for_processing() after it; this alone is not enough.
    """
    deadline = time.monotonic() + timeout
    last = {}
    while time.monotonic() < deadline:
        response = client.get(f"/api/jobs/{job_id}")
        assert response.status_code == 200, (
            f"job {job_id} vanished from /api/jobs: {response.status_code}"
        )
        last = response.json()
        status = str(last.get("status", "")).lower()
        if status in {"done", "completed", "succeeded", "finished"}:
            return last
        if status in {"error", "failed"}:
            pytest.fail(f"{what} failed: {last}")
        time.sleep(JOB_POLL_SECONDS)
    raise TimeoutError(f"{what} did not finish within {timeout}s; last state: {last}")


def _wait_for_processing(client, workdir, processor_type: str,
                        timeout: int, poll: int = 60) -> str:
    """Wait for the actual processing, not merely its submission.

    /api/folder-process marks its job done the moment submit() returns, because
    that endpoint's job really is "submit the work" -- the real frontend gets
    per-stage progress from a different call (JobQueueDrawer.tsx polls
    /api/folder-local-jobs and /api/folder-hyp3-jobs). A test that stops at the
    submit job's "done" therefore runs the analyzer against an empty folder and
    blames the analyzer.

    POST /api/folder-local-action with action="refresh" runs the processor's own
    refresh() -- the same code path as `insarhub processor refresh` -- and
    returns its status table, which is the authoritative answer.
    """
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        started = _post(client, "/api/folder-local-action", {
            "folder_path": str(workdir),
            "processor_type": processor_type,
            "action": "refresh",
        })
        if job_id := started.get("job_id"):
            state = _wait_for_job(client, job_id, timeout=600, what="refresh")
            last = str(state.get("message", ""))
        if last and "PENDING" not in last and "RUNNING" not in last:
            return last
        time.sleep(poll)
    raise TimeoutError(
        f"{processor_type} still had PENDING/RUNNING steps after {timeout}s:\n{last}"
    )


def search_body(spec, stack, workdir) -> dict:
    """The body SearchRequest actually wants.

    It is a bounding box (west/south/east/north are the only required fields),
    not the WKT the CLI takes; `wkt` is an optional refinement and the
    downloader is named by `downloaderType`. Guessing "aoi"/"downloader" here
    produced a 422 that looked like a search failure.
    """
    west, south, east, north = stack.aoi_bbox
    return {
        "west": west, "south": south, "east": east, "north": north,
        "wkt": stack.aoi_wkt,
        "start": stack.start,
        "end": stack.end,
        "workdir": str(workdir),
        "downloaderType": spec["downloader"],
        "overrides": {"relativeOrbit": stack.rel_orbit},
    }


def _post(client, path: str, payload: dict) -> dict:
    response = client.post(path, json=payload)
    assert response.status_code < 400, (
        f"POST {path} -> {response.status_code}: {response.text[:500]}"
    )
    return response.json()


# ── The endpoints the GUI loads with ─────────────────────────────────────────

def test_gui_starts_up_against_a_real_environment(live_api):
    """What the browser fetches on first paint. A 500 here is a blank UI."""
    assert live_api.get("/api/health").json()["status"] == "ok"

    for path in ("/api/workdir", "/api/settings", "/api/workflows",
                 "/api/auth-status", "/api/job-folders"):
        response = live_api.get(path)
        assert response.status_code < 500, f"{path} -> {response.status_code}"


def test_gui_reports_real_credentials(live_api):
    """Unlike tier 2, nothing is stubbed here -- this is the real probe.

    If the GUI cannot see credentials that the CLI can, the user is told to log
    in when they already have.
    """
    status = live_api.get("/api/auth-status").json()
    assert status.get("earthdata_connected") is True, (
        f"GUI does not see the Earthdata credentials tier 3 is running with: {status}"
    )


# ── Search ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("workflow", sorted(_backends.WORKFLOWS))
def test_gui_search_finds_the_minimal_stack(workflow, require_workflow, workflow_dir, live_api):
    """POST /api/search -- the Search panel's own call."""
    require_workflow(workflow)
    spec, stack = _backends.WORKFLOWS[workflow], stack_for(workflow)

    body = _post(live_api, "/api/search", search_body(spec, stack, workflow_dir(workflow)))
    assert body, "search returned an empty body"


def test_gui_parses_an_uploaded_aoi_file(live_api):
    """/api/parse-aoi backs the map's "load AOI from file" control.

    It takes a base64-encoded geospatial FILE (it writes the bytes to disk and
    opens them with geopandas), not a WKT string -- the map's typed WKT box goes
    straight into the search request instead. A GeoJSON of the Parowan polygon
    is the smallest thing geopandas will read.
    """
    import base64
    import json

    west, south, east, north = stack_for("hyp3_s1").aoi_bbox
    geojson = {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "properties": {"name": "Parowan Valley"},
            "geometry": {
                "type": "Polygon",
                "coordinates": [[
                    [west, south], [east, south], [east, north],
                    [west, north], [west, south],
                ]],
            },
        }],
    }
    encoded = base64.b64encode(json.dumps(geojson).encode()).decode()

    body = _post(live_api, "/api/parse-aoi",
                 {"filename": "parowan.geojson", "data": encoded})
    assert "feature" in body, f"parse-aoi returned no feature: {body}"
    assert body["feature"]["geometry"]["type"] == "Polygon"


# ── Full pipeline ────────────────────────────────────────────────────────────

@pytest.mark.needs_mintpy
def test_hyp3_full_pipeline_via_gui(require_workflow, workflow_dir, live_api):
    """Search -> download -> process -> analyze, entirely over HTTP.

    HyP3 is the pipeline a GUI user is most likely to run: no local backend, and
    the long waits are exactly the polling behaviour the panels implement.
    """
    workflow = "hyp3_s1"
    require_workflow(workflow)
    spec, stack = _backends.WORKFLOWS[workflow], stack_for(workflow)
    workdir = workflow_dir(workflow)

    _post(live_api, "/api/search", search_body(spec, stack, workdir))

    started = _post(live_api, "/api/download-stack", {
        "workdir": str(workdir),
        "relativeOrbit": stack.rel_orbit,
        "frame": stack.frame,
        "start": stack.start,
        "end": stack.end,
        "wkt": stack.aoi_wkt,
        "downloaderType": spec["downloader"],
    })
    if job_id := started.get("job_id"):
        _wait_for_job(live_api, job_id, timeout=7200, what="download")

    started = _post(live_api, "/api/folder-process", {
        "folder_path": str(workdir),
        "processor_type": spec["processor"],
    })
    if job_id := started.get("job_id"):
        # This only confirms the SUBMISSION landed.
        _wait_for_job(live_api, job_id, timeout=600, what="submission")
    # This waits for the processing itself.
    _wait_for_processing(live_api, workdir, spec["processor"], timeout=21600)

    _post(live_api, "/api/folder-init-analyzer", {
        "folder_path": str(workdir),
        "analyzer_type": spec["analyzer"],
    })
    steps = live_api.get("/api/analyzer-steps",
                         params={"analyzer": spec["analyzer"]}).json()
    started = _post(live_api, "/api/folder-run-analyzer", {
        "folder_path": str(workdir),
        "analyzer_type": spec["analyzer"],
        "steps": steps if isinstance(steps, list) else steps.get("steps", []),
    })
    if job_id := started.get("job_id"):
        _wait_for_job(live_api, job_id, timeout=7200, what="analysis")

    assert_workflow_recorded(workdir, processor=spec["processor"], analyzer=spec["analyzer"])
    assert_produced(workdir, "*timeseries*.h5")


# ── Cross-interface parity ───────────────────────────────────────────────────

@pytest.mark.parametrize("workflow", sorted(_backends.WORKFLOWS))
def test_gui_reloads_a_workdir_produced_by_another_interface(
    workflow, require_workflow, workflow_dir, live_api
):
    """The parity check that motivates splitting the tier by interface.

    All three front ends share one discovery path
    (utils/local_processor_reload.py). A workdir the Python API or CLI wrote but
    the GUI cannot reload shows up in the browser with no processor and no
    status -- a failure mode that is invisible to the other two interfaces
    because they never look.

    Reads an existing workdir rather than producing one, so it skips until one
    of the other e2e files has run this workflow.
    """
    require_workflow(workflow)
    root = workflow_dir(workflow)

    # The markers live in the per-stack p<path>_f<frame> folder the downloader
    # creates, not in the parent the search ran in.
    stacks = sorted(root.glob("p*_f*"))
    if not stacks:
        pytest.skip(f"{workflow} has not been run in {root} yet")
    workdir = stacks[0]

    # /api/folder-details is NOT the endpoint that carries the badges -- it
    # returns only {downloader_config, has_pairs}, for every folder. The
    # processor/analyzer the GUI shows come from /api/folder-config.
    details = live_api.get("/api/folder-details", params={"path": str(workdir)})
    assert details.status_code == 200, (
        f"GUI cannot read a workdir another interface produced: "
        f"{details.status_code} {details.text[:300]}"
    )

    response = live_api.get("/api/folder-config", params={"path": str(workdir)})
    assert response.status_code == 200, (
        f"/api/folder-config failed: {response.status_code} {response.text[:300]}"
    )
    cfg = response.json()
    spec = _backends.WORKFLOWS[workflow]

    def _type(entry):
        return entry.get("type") if isinstance(entry, dict) else entry

    assert _type(cfg.get("processor")) == spec["processor"], (
        f"GUI reports processor={_type(cfg.get('processor'))!r} for a workdir "
        f"produced by {spec['processor']} -- the folder would show a blank or "
        "wrong processor badge"
    )
    assert _type(cfg.get("analyzer")) == spec["analyzer"], (
        f"GUI reports analyzer={_type(cfg.get('analyzer'))!r}, expected "
        f"{spec['analyzer']}"
    )


def test_gui_lists_the_workdir_in_job_folders(require_workflow, workflow_dir, live_api):
    """The folder must appear in the panel's listing, not merely be readable
    when asked for by path."""
    require_workflow("hyp3_s1")
    workdir = workflow_dir("hyp3_s1")
    if not (workdir / "insarhub_config.json").is_file():
        pytest.skip("hyp3_s1 has not been run yet")

    response = live_api.get("/api/browse-subfolders", params={"path": str(workdir.parent)})
    if response.status_code == 400:
        pytest.skip("workdir is not configured in settings")
    assert response.status_code == 200, f"{response.status_code}: {response.text[:300]}"
    assert workdir.name in str(response.json()), (
        f"{workdir.name} is missing from the GUI folder listing"
    )
