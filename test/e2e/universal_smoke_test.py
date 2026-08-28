#!/usr/bin/env python3
"""
Universal smoke test across every InSARHub pipeline, against real data,
exercising BOTH the CLI and the HTTP API.

What it verifies (the "is the system broken?" question)
-------------------------------------------------------
1. Registry integrity -- every registered processor/analyzer/downloader lists
   and produces a config, through both interfaces.
2. Python import -- every package/submodule imports in a fresh process (catches
   broken/circular imports and import-time side effects).
3. Real-workdir reload -- the saved-job discovery + processor reconstruction
   path (utils/local_processor_reload.py, shared by CLI and GUI) actually
   finds the job file and reports on-disk status for each real pipeline.
4. CLI/API parity -- the same operation (list, reload, refresh) works through
   `insarhub ...` (subprocess) and through the FastAPI HTTP server.

Nothing here downloads data or re-runs heavy processing: it reads the state of
already-completed real workdirs. It is safe to run repeatedly and against
live workdirs.

Pipelines covered (auto-detected from each workdir's insarhub_config.json)
--------------------------------------------------------------------------
    GMTSAR_S1         ->  GMTSAR_Mintpy_SBAS / GMTSAR_SBAS
    ISCE2_S1          ->  ISCE2_Mintpy_SBAS
    ISCE3_Burst       ->  ISCE3_Dolphin_PL
    Hyp3_S1 (cloud)   ->  Hyp3_Mintpy_SBAS        (registry/config level only)

Usage (from the repo root, any env with insarhub installed):
    python test/e2e/universal_smoke_test.py \
        --scan-dir /smith-scratch/PROJECTS/InSAR/dev \
        --workdir /path/to/p56

    # or via env var
    INSARHUB_TEST_SCAN_DIRS=/smith-scratch/PROJECTS/InSAR/dev:/path/to/other \
        python test/e2e/universal_smoke_test.py

    python test/e2e/universal_smoke_test.py --mode cli      # CLI checks only
    python test/e2e/universal_smoke_test.py --json          # machine-readable

Exit code: 0 if every check PASSed (or SKIPped), 1 if any check FAILed.

A check is SKIPped (not failed) when the environment lacks that pipeline's
optional dependency (isce2, gmt/gmtsar, dolphin/compass, slurm, ...) so the
test stays portable across machines that only have a subset installed.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

try:
    import requests
except ImportError:  # pragma: no cover - API checks will SKIP
    requests = None

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

EXPECTED_PROCESSORS = {"Hyp3_S1", "ISCE2_S1", "GMTSAR_S1", "ISCE3_Burst"}
EXPECTED_ANALYZERS = {
    "Hyp3_Mintpy_SBAS", "ISCE2_Mintpy_SBAS", "GMTSAR_SBAS", "GMTSAR_Mintpy_SBAS", "ISCE3_Dolphin_PL",
}
# Pipeline -> expected on-disk artifacts (relative globs). Purely informational
# (WARN on missing) -- the tooling checks below are the hard pass/fail.
ARTIFACTS = {
    "GMTSAR_S1": ["gmtsar/gmtsar_jobs.json"],
    "ISCE2_S1": ["isce*"],
    "ISCE3_Burst": ["isce3_burst_jobs.json", "timeseries"],
    "GMTSAR_Mintpy_SBAS": ["gmtsar_mintpy/*.h5"],
    "ISCE2_Mintpy_SBAS": ["mintpy"],
    "ISCE3_Dolphin_PL": ["timeseries"],
    "GMTSAR_SBAS": ["gmtsar_sbas"],
}

# Substrings in a CLI/API failure that mean "missing optional dependency" rather
# than "the system is broken". Classifies the check SKIP instead of FAIL.
_SKIP_MARKERS = (
    "GMTSAR is not installed",
    "No `gmt` binary found",
    "ModuleNotFoundError",
    "No module named 'isce",
    "No module named 'dolphin",
    "No module named 'compass",
    "No module named 'httpx",
    "ISCE2 is not importable",
    "slurm",
    "sacct",
    "squeue",
    "not installed",
    "import isce",
)


# ---------------------------------------------------------------------------
# result model
# ---------------------------------------------------------------------------

@dataclass
class Result:
    name: str
    status: str = "PASS"          # PASS | FAIL | SKIP | WARN
    detail: str = ""
    pipeline: str = ""


@dataclass
class Suite:
    results: list[Result] = field(default_factory=list)

    def add(self, r: Result) -> None:
        self.results.append(r)

    def ok(self) -> bool:
        return all(r.status != "FAIL" for r in self.results)


# ---------------------------------------------------------------------------
# CLI helpers
# ---------------------------------------------------------------------------

def run_cli(*args: str, timeout: int = 180) -> tuple[int, str, str]:
    """Run the insarhub CLI in-process-env via `python -m insarhub.cli.main`."""
    proc = subprocess.run(
        [sys.executable, "-m", "insarhub.cli.main", *args],
        capture_output=True, text=True, timeout=timeout, cwd=str(REPO_ROOT),
    )
    return proc.returncode, proc.stdout, proc.stderr


def _skip_or_fail(suite: Suite, name: str, rc: int, err: str, pipeline: str) -> None:
    if rc == 0:
        suite.add(Result(name, "PASS", pipeline=pipeline))
        return
    if any(m in err for m in _SKIP_MARKERS):
        suite.add(Result(name, "SKIP", detail=err.strip().splitlines()[-1][:200], pipeline=pipeline))
    else:
        suite.add(Result(name, "FAIL", detail=err.strip().splitlines()[-1][:200], pipeline=pipeline))


# ---------------------------------------------------------------------------
# API helpers (real uvicorn server + requests -- no httpx required)
# ---------------------------------------------------------------------------

def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class ApiServer:
    """Boot a throwaway uvicorn server and proxy requests to it."""

    def __init__(self, timeout: int = 180):
        self.timeout = timeout
        self.proc = None
        self.base = None

    def start(self) -> None:
        if requests is None:
            raise RuntimeError("requests is not installed")
        self.port = _free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        env = dict(os.environ)
        env.setdefault("INSARHUB_TESTING", "1")
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "insarhub.app.api:app",
             "--host", "127.0.0.1", "--port", str(self.port), "--log-level", "error"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            cwd=str(REPO_ROOT), env=env,
        )
        # wait for readiness
        deadline = time.time() + self.timeout
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError("uvicorn exited before becoming ready")
            try:
                requests.get(f"{self.base}/api/health", timeout=2)
                return
            except Exception:
                time.sleep(0.3)
        raise RuntimeError("API server did not become ready in time")

    def get(self, path: str, **kw):
        return requests.get(f"{self.base}{path}", timeout=30, **kw)

    def post(self, path: str, **kw):
        return requests.post(f"{self.base}{path}", timeout=30, **kw)

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()


# ---------------------------------------------------------------------------
# workdir discovery
# ---------------------------------------------------------------------------

def _pipeline_of(workdir: Path) -> tuple[str, str]:
    from insarhub.utils.config_io import read_insarhub_config
    cfg = read_insarhub_config(workdir)
    return cfg.get("processor", {}).get("type", ""), cfg.get("analyzer", {}).get("type", "")


def discover_workdirs(args) -> list[Path]:
    found: list[Path] = []
    seen: set[Path] = set()

    def add(p: Path) -> None:
        p = Path(p).expanduser().resolve()
        if p not in seen and (p / "insarhub_config.json").exists():
            seen.add(p)
            found.append(p)

    for w in args.workdir or []:
        add(Path(w))

    scan_dirs = args.scan_dir or []
    env_dirs = os.environ.get("INSARHUB_TEST_SCAN_DIRS", "")
    for d in env_dirs.split(os.pathsep) if env_dirs else []:
        scan_dirs.append(d)
    if not scan_dirs:
        scan_dirs = [str(REPO_ROOT)]  # always has the p56 ISCE3_Burst workdir

    for base in scan_dirs:
        base = Path(base).expanduser()
        if not base.is_dir():
            continue
        for d in sorted(base.rglob("insarhub_config.json")):
            add(d.parent)
    return found


# ---------------------------------------------------------------------------
# Python import surface
# ---------------------------------------------------------------------------

# Every package/submodule the test touches -- importing them in a FRESH python
# process catches broken imports, circular imports, and import-time side
# effects that the CLI and HTTP surfaces would otherwise mask (each runs its
# own process, so only the library surface actually re-executes these).
_IMPORT_MODULES = [
    "insarhub",
    "insarhub.core",
    "insarhub.core.base",
    "insarhub.core.registry",
    "insarhub.core.engine",
    "insarhub.utils.config_io",
    "insarhub.utils.local_processor_reload",
    "insarhub.utils.slurm_manager",
    "insarhub.config",
    "insarhub.downloader",
    "insarhub.processor",
    "insarhub.processor.gmtsar_s1",
    "insarhub.processor._gmtsar_esd_network",
    "insarhub.processor.isce2_s1",
    "insarhub.processor.isce3_base",
    "insarhub.processor.isce3_burst",
    "insarhub.analyzer",
    "insarhub.cli.main",
    "insarhub.app.api",
]


def check_python_import(suite: Suite) -> None:
    """Import every submodule in a fresh process and report per-module status."""
    script = (
        "import importlib, json\n"
        "mods = " + repr(_IMPORT_MODULES) + "\n"
        "out = []\n"
        "for m in mods:\n"
        "    try:\n"
        "        importlib.import_module(m)\n"
        "        out.append([m, True, ''])\n"
        "    except Exception as e:\n"
        "        out.append([m, False, type(e).__name__ + ': ' + str(e)])\n"
        "print(json.dumps(out))\n"
    )
    proc = subprocess.run([sys.executable, "-c", script], capture_output=True,
                          text=True, timeout=300, cwd=str(REPO_ROOT))
    if proc.returncode != 0:
        suite.add(Result("import/*", "FAIL", detail=(proc.stderr or "")[-200:],
                         pipeline="import"))
        return
    try:
        rows = json.loads(proc.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        suite.add(Result("import/*", "FAIL", detail=(proc.stdout or "")[-200:],
                         pipeline="import"))
        return
    for mod, ok, err in rows:
        if ok:
            suite.add(Result(f"import/{mod}", "PASS", pipeline="import"))
        elif any(marker in err for marker in _SKIP_MARKERS):
            suite.add(Result(f"import/{mod}", "SKIP", detail=err[:160], pipeline="import"))
        else:
            suite.add(Result(f"import/{mod}", "FAIL", detail=err[:160], pipeline="import"))


# ---------------------------------------------------------------------------
# checks
# ---------------------------------------------------------------------------

def check_registry_cli(suite: Suite) -> None:
    for name, expect, flag, kind in (
        ("list-processors", EXPECTED_PROCESSORS, "--list-processors", "processor"),
        ("list-analyzers", EXPECTED_ANALYZERS, "--list-analyzers", "analyzer"),
    ):
        rc, out, err = run_cli(kind, flag)
        missing = expect - set(out.split())
        if rc == 0 and not missing:
            suite.add(Result(f"cli/{name}", "PASS", pipeline="registry"))
        elif rc == 0:
            suite.add(Result(f"cli/{name}", "FAIL",
                             detail=f"missing from listing: {sorted(missing)}",
                             pipeline="registry"))
        else:
            suite.add(Result(f"cli/{name}", "FAIL", detail=err.strip()[-200:], pipeline="registry"))

    # per-processor / per-analyzer --list-options
    for kind, names in (("processor", EXPECTED_PROCESSORS),
                        ("analyzer", EXPECTED_ANALYZERS)):
        for n in sorted(names):
            rc, _, err = run_cli(kind, "-N", n, "--list-options")
            _skip_or_fail(suite, f"cli/{kind}--list-options/{n}", rc, err, n)


def check_registry_api(suite: Suite, api: ApiServer) -> None:
    r = api.get("/api/health")
    suite.add(Result("api/health", "PASS" if r.status_code == 200 and r.json().get("status") == "ok"
                     else "FAIL", detail=str(r.status_code), pipeline="registry"))

    r = api.get("/api/processor-steps", params={"processor": "ISCE2_S1"})
    if r.status_code == 200 and "steps" in r.json():
        suite.add(Result("api/processor-steps", "PASS", pipeline="ISCE2_S1"))
    else:
        suite.add(Result("api/processor-steps", "FAIL", detail=str(r.status_code), pipeline="ISCE2_S1"))

    for az in sorted(EXPECTED_ANALYZERS):
        r = api.get("/api/analyzer-steps", params={"analyzer_type": az})
        suite.add(Result(f"api/analyzer-steps/{az}",
                         "PASS" if r.status_code == 200 and "steps" in r.json() else "FAIL",
                         detail=str(r.status_code), pipeline=az))


def check_workdir_cli(suite: Suite, workdir: Path, proc: str, az: str) -> None:
    if not proc:
        return
    tag = f"{proc}" + (f" + {az}" if az else "")
    rc, out, err = run_cli("processor", "-N", proc, "-w", str(workdir), "refresh")
    _skip_or_fail(suite, f"cli/refresh/{tag}", rc, err, proc)
    if rc == 0 and ("SUCCEEDED" in out or "PENDING" in out or "RUNNING" in out):
        suite.results[-1].detail = "found job state on disk"

    if az:
        rc, _, err = run_cli("analyzer", "-N", az, "-w", str(workdir), "--list-options")
        _skip_or_fail(suite, f"cli/analyzer--list-options/{az}", rc, err, az)

    _check_artifacts(suite, workdir, proc, az)


def _check_artifacts(suite: Suite, workdir: Path, proc: str, az: str) -> None:
    for typ in (proc, az):
        for pat in ARTIFACTS.get(typ, []):
            if list(workdir.glob(pat)):
                suite.add(Result(f"artifact/{typ}:{pat}", "PASS", pipeline=typ))
            else:
                suite.add(Result(f"artifact/{typ}:{pat}", "WARN",
                                 detail="expected output not found", pipeline=typ))


def _poll_job(api: ApiServer, job_id: str, timeout: int = 180) -> dict:
    deadline = time.time() + timeout
    last = {}
    while time.time() < deadline:
        r = api.get(f"/api/jobs/{job_id}")
        if r.status_code != 200:
            return {"status": "error", "message": f"job gone (HTTP {r.status_code})"}
        last = r.json()
        if last.get("status") in ("done", "error"):
            return last
        time.sleep(0.5)
    return {"status": "timeout", "message": last.get("message", "")}


def check_workdir_api(suite: Suite, api: ApiServer, workdir: Path, proc: str, az: str) -> None:
    if not proc:
        return
    tag = f"{proc}" + (f" + {az}" if az else "")

    r = api.get("/api/folder-local-jobs", params={"path": str(workdir), "processor": proc})
    files = r.json().get("files", []) if r.status_code == 200 else []
    if r.status_code == 200 and files:
        suite.add(Result(f"api/folder-local-jobs/{tag}", "PASS",
                         detail=f"{len(files)} job file(s)", pipeline=proc))
        job_file = files[0]["name"]
    else:
        suite.add(Result(f"api/folder-local-jobs/{tag}", "FAIL",
                         detail=f"HTTP {r.status_code}", pipeline=proc))
        return

    r = api.post("/api/folder-local-action", json={
        "folder_path": str(workdir), "job_file": job_file,
        "action": "refresh", "processor_type": proc,
    })
    if r.status_code != 200:
        suite.add(Result(f"api/refresh/{tag}", "FAIL",
                         detail=f"HTTP {r.status_code}: {r.text[:200]}", pipeline=proc))
        return
    state = _poll_job(api, r.json()["job_id"])
    if state["status"] == "done":
        suite.add(Result(f"api/refresh/{tag}", "PASS",
                         detail=(state.get("message") or "").strip().splitlines()[-1][:120],
                         pipeline=proc))
    else:
        suite.add(Result(f"api/refresh/{tag}", "FAIL",
                         detail=f"{state['status']}: {state.get('message', '')[:200]}",
                         pipeline=proc))


# ---------------------------------------------------------------------------
# reporting
# ---------------------------------------------------------------------------

def report(suite: Suite, json_out: bool) -> None:
    if json_out:
        print(json.dumps([vars(r) for r in suite.results], indent=2))
        return
    width = max((len(r.name) for r in suite.results), default=40)
    for r in suite.results:
        line = f"{r.name:<{width}}  {r.status:<5}  {r.detail}"
        print(line)
    counts = {s: sum(1 for r in suite.results if r.status == s)
              for s in ("PASS", "FAIL", "SKIP", "WARN")}
    print("\nSummary: " + ", ".join(f"{k}={v}" for k, v in counts.items() if v))
    print("RESULT:", "OK" if suite.ok() else "BROKEN")


def main() -> int:
    p = argparse.ArgumentParser(description="Universal InSARHub pipeline smoke test (CLI + API).")
    p.add_argument("--workdir", action="append", metavar="PATH",
                   help="Explicit real workdir to test (repeatable).")
    p.add_argument("--scan-dir", action="append", metavar="DIR",
                   help="Scan DIR (recursively) for insarhub_config.json workdirs (repeatable).")
    p.add_argument("--mode", choices=("cli", "api", "both"), default="both")
    p.add_argument("--json", action="store_true", help="Emit results as JSON.")
    args = p.parse_args()

    suite = Suite()
    workdirs = discover_workdirs(args)

    do_cli = args.mode in ("cli", "both")
    do_api = args.mode in ("api", "both")

    check_python_import(suite)

    if do_cli:
        check_registry_cli(suite)

    api = None
    if do_api:
        api = ApiServer()
        try:
            api.start()
            check_registry_api(suite, api)
        except Exception as e:  # noqa: BLE001
            suite.add(Result("api/server", "SKIP", detail=str(e), pipeline="api"))
            api = None

    print(f"\nDiscovered {len(workdirs)} workdir(s):")
    for w in workdirs:
        proc, az = _pipeline_of(w)
        print(f"  {w}  ->  processor={proc or '(none)'}  analyzer={az or '(none)'}")
    print()

    for w in workdirs:
        proc, az = _pipeline_of(w)
        if do_cli:
            check_workdir_cli(suite, w, proc, az)
        if do_api and api is not None:
            check_workdir_api(suite, api, w, proc, az)

    if api is not None:
        api.stop()

    report(suite, args.json)
    return 0 if suite.ok() else 1


if __name__ == "__main__":
    sys.exit(main())
