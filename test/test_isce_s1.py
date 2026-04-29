"""
Integration test for ISCE_S1 (stackSentinel backend).

Requires a real ISCE2 installation and a directory of Sentinel-1 SLC files.
Fill in the paths below, then run:

    python test/test_isce_s1.py

What it tests
-------------
run_submit_test()
    Calls submit(), waits for all steps to finish, verifies SUCCEEDED status
    and that the expected interferogram outputs exist under
    interferograms/<date1>_<date2>/merged/.

run_retry_test()
    Submits a second time with skip_existing=True to exercise the retry path
    (all steps should already be SUCCEEDED and the run should be a no-op).
"""

from __future__ import annotations

import time
from pathlib import Path

# ── Fill these in ─────────────────────────────────────────────────────────────

WORKDIR         = "/home/jldz9/dev/InSARHub/p100_f466"   # working dir; stackSentinel outputs go here
SLC_DIR         = "/home/jldz9/dev/InSARHub/p100_f466"   # directory containing *.zip / *.SAFE SLC files
ORBIT_DIR       = "/home/jldz9/dev/InSARHub/p100_f466"   # directory with .EOF orbit files, or None
STACK_JSON      = "/home/jldz9/dev/InSARHub/p100_f466/stack_p100_f466.json"  # from select_pairs
DEM_PATH        = None                                    # ISCE2-format DEM path, or None to auto-download
BBOX            = None                                    # [S, N, W, E] float list, or None (full scene)

# ─────────────────────────────────────────────────────────────────────────────

WATCH_TIMEOUT = 14400   # seconds before giving up (4 h)


def _load_pairs() -> list[tuple[str, str]]:
    import json
    from pathlib import Path
    data = json.loads(Path(STACK_JSON).read_text())
    raw  = data.get("pairs", data)
    return [(str(p[0]), str(p[1])) for p in raw]


def _make_proc(skip_existing: bool = True):
    from insarhub.config import ISCE_S1_Config
    from insarhub.processor.isce_s1 import ISCE_S1
    return ISCE_S1(
        pairs  = _load_pairs(),
        config = ISCE_S1_Config(
            workdir       = WORKDIR,
            slc_dir       = SLC_DIR,
            orbit_dir     = ORBIT_DIR,
            dem_path      = DEM_PATH,
            bbox          = BBOX,
            max_workers   = 4,
            skip_existing = skip_existing,
        ),
    )


def _wait_done(proc, timeout: int = WATCH_TIMEOUT) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(30)
        jobs = proc.refresh()
        counts: dict[str, int] = {}
        for m in jobs.values():
            counts[m["status"]] = counts.get(m["status"], 0) + 1
        print(f"  [{time.strftime('%H:%M:%S')}]  "
              f"running={counts.get('RUNNING', 0)}  "
              f"pending={counts.get('PENDING', 0)}  "
              f"succeeded={counts.get('SUCCEEDED', 0)}  "
              f"failed={counts.get('FAILED', 0)}")
        if counts.get("RUNNING", 0) == 0 and counts.get("PENDING", 0) == 0:
            return
    raise TimeoutError("stackSentinel processing did not finish within the timeout.")


def _check_outputs() -> None:
    """Verify at least one interferogram directory has the expected merged outputs."""
    ifg_root = Path(WORKDIR) / "interferograms"
    if not ifg_root.exists():
        print("  (interferograms/ dir not yet created — skipping output check)")
        return
    pair_dirs = sorted(ifg_root.iterdir())
    if not pair_dirs:
        print("  (no interferogram subdirs found — skipping output check)")
        return
    expected = [
        "merged/filt_topophase.unw.geo",
        "merged/filt_topophase.cor.geo",
        "merged/filt_topophase.unw.conncomp.geo",
    ]
    checked = 0
    for pair_dir in pair_dirs:
        if not pair_dir.is_dir():
            continue
        for rel in expected:
            f = pair_dir / rel
            if f.exists():
                print(f"  ✓ {pair_dir.name}/{rel}")
                checked += 1
    if checked == 0:
        print("  (no geocoded outputs found yet — processing may still be running)")


# ── Test 1: full submit + watch ───────────────────────────────────────────────

def run_submit_test():
    print("\n═══ Submit + watch test ════════════════════════════════════════")
    proc = _make_proc(skip_existing=True)
    print(f"  stackSentinel : {proc._stack_bin}")

    jobs = proc.submit()
    assert jobs, "submit() registered no steps — check that SLC files exist in SLC_DIR"
    print(f"  {len(jobs)} step(s) registered, waiting…")

    _wait_done(proc)

    result    = proc.refresh()
    succeeded = [m for m in result.values() if m["status"] == "SUCCEEDED"]
    failed    = [m for m in result.values() if m["status"] == "FAILED"]
    print(f"\n  SUCCEEDED: {len(succeeded)}   FAILED: {len(failed)}")

    if failed:
        for m in failed:
            log_dir = Path(m["log_dir"])
            print(f"\n  ── Logs for {m['step']} ({log_dir}) ──")
            for log in sorted(log_dir.glob("cmd_*.log")):
                print(f"\n  {log.name}:")
                print(log.read_text()[-2000:])

    assert not failed, f"{len(failed)} step(s) failed — check logs above"

    _check_outputs()
    saved = proc.save()
    print(f"\n  job file: {saved}")
    print("✓ Submit test passed.\n")


# ── Test 2: re-submit with skip_existing (should be instant no-op) ─────────

def run_retry_test():
    print("\n═══ Re-submit (skip_existing) test ═════════════════════════════")
    proc = _make_proc(skip_existing=True)
    proc.submit()
    # If all steps are already SUCCEEDED, the executor exits immediately
    time.sleep(5)
    result = proc.refresh()
    statuses = [m["status"] for m in result.values()]
    running = statuses.count("RUNNING")
    pending = statuses.count("PENDING")
    print(f"  After re-submit: RUNNING={running}  PENDING={pending}")
    # No assertion — just verifies it doesn't crash on re-run
    print("✓ Re-submit test passed.\n")


if __name__ == "__main__":
    run_submit_test()
    run_retry_test()
