"""
Full pipeline integration test: real ASF search → select_pairs → ISCE processing → ISCE_SBAS

Real network calls:
  - ASF search via asf_search
  - Perpendicular baseline fetch (inside select_pairs)

Simulated:
  - SLC download  (fake .SAFE dirs created from real scene names)
  - ISCE2 processing (stub topsApp.py writes SUCCEEDED instantly)
  - MintPy run (patched — no mintpy install required)

Run from repo root:
    python tests/test_full_pipeline.py

Configure the search area at the top of the file if needed.
"""

from __future__ import annotations

import sys
import tempfile
import textwrap
import time
from pathlib import Path
from unittest.mock import MagicMock

# ── Patch mintpy/pyaps3 before any insarhub import touches them ───────────────
for _mod in [
    "mintpy", "mintpy.utils", "mintpy.utils.readfile",
    "mintpy.smallbaselineApp", "pyaps3",
]:
    sys.modules[_mod] = MagicMock()

_fake_tsa = MagicMock()
_fake_tsa.return_value.run.return_value = None
sys.modules["mintpy.smallbaselineApp"].TimeSeriesAnalysis = _fake_tsa

# ── Search parameters — edit here to change the test area ────────────────────
AOI_WKT    = "POLYGON((-118.0 35.5, -117.0 35.5, -117.0 36.0, -118.0 36.0, -118.0 35.5))"
START_DATE = "2020-01-01"
END_DATE   = "2020-03-01"
REL_ORBIT  = 71       # Descending path over Ridgecrest, CA
MAX_SCENES = 50

# ── Fake topsApp.py ───────────────────────────────────────────────────────────
FAKE_TOPSAPP = textwrap.dedent("""\
    #!/usr/bin/env python3
    import re, time
    from pathlib import Path

    xml = Path("topsApp.xml").read_text() if Path("topsApp.xml").exists() else ""
    m   = re.search(r'<property name="output directory">(.*?)</property>', xml)
    pair_dir = Path(m.group(1)) if m else Path(".")

    status = pair_dir / "topsApp.status"
    status.write_text("RUNNING:0")
    time.sleep(0.2)

    merged = pair_dir / "merged"
    merged.mkdir(parents=True, exist_ok=True)
    for fname in [
        "filt_topophase.unw.geo",
        "filt_topophase.cor.geo",
        "filt_topophase.unw.conncomp.geo",
        "los.rdr.geo",
    ]:
        (merged / fname).write_bytes(b"fake")

    ref_dir = pair_dir / "reference"
    ref_dir.mkdir(parents=True, exist_ok=True)
    (ref_dir / "IW1.xml").write_text("<IW/>")

    sec_date = pair_dir.name.split("_")[2]
    bl_dir   = pair_dir / "baselines" / sec_date
    bl_dir.mkdir(parents=True, exist_ok=True)
    (bl_dir / "IW1.xml").write_text("<baseline/>")

    status.write_text("SUCCEEDED")
""")


def _make_safe(workdir: Path, scene_name: str) -> None:
    (workdir / f"{scene_name}.SAFE").mkdir(parents=True, exist_ok=True)


def _wait_done(proc, timeout: int = 120) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(0.5)
        statuses = {m["status"] for m in proc.jobs.values()}
        if statuses <= {"SUCCEEDED", "FAILED"}:
            return
    raise TimeoutError("ISCE processing did not finish in time")


def run_tests():
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)

        fake_bin = workdir / "topsApp.py"
        fake_bin.write_text(FAKE_TOPSAPP)
        fake_bin.chmod(0o755)

        # ─────────────────────────────────────────────────────────────────────
        # STAGE 1 — Real ASF search
        # ─────────────────────────────────────────────────────────────────────
        print("\n── STAGE 1: ASF search ─────────────────────────────────────")
        print(f"  AOI:   {AOI_WKT[:60]}…")
        print(f"  Dates: {START_DATE} → {END_DATE}   path {REL_ORBIT}")

        from insarhub.config import S1_SLC_Config
        from insarhub.downloader.s1_slc import S1_SLC

        cfg_dl = S1_SLC_Config(
            workdir       = str(workdir),
            intersectsWith= AOI_WKT,
            start         = START_DATE,
            end           = END_DATE,
            relativeOrbit = [REL_ORBIT],  # ASF expects a list
            polarization  = "VV",         # list of polarizations causes HTTP 500
            maxResults    = MAX_SCENES,
        )
        downloader = S1_SLC(cfg_dl)
        downloader.search()

        results: dict = dict(downloader.results)
        total_scenes = sum(len(v) for v in results.values())
        n_stacks     = len(results)
        print(f"  {total_scenes} scenes across {n_stacks} stack(s) — OK")
        assert total_scenes > 0, "Search returned no results"

        for (path, frame), scenes in results.items():
            print(f"    path={path} frame={frame}: {len(scenes)} scenes")

        # ─────────────────────────────────────────────────────────────────────
        # STAGE 2 — Real select_pairs
        # ─────────────────────────────────────────────────────────────────────
        print("\n── STAGE 2: select_pairs ───────────────────────────────────")

        pairs_result, _, _, _ = downloader.select_pairs(
            dt_targets            = (12, 24),
            dt_max                = 36,
            pb_max                = 200.0,
            min_degree            = 2,
            max_degree            = 3,
            force_connect         = True,
            avoid_low_quality_days= False,
        )

        # pairs_result may be a dict (multi-stack) or list (single stack)
        if isinstance(pairs_result, dict):
            pairs_dict  = pairs_result
            total_pairs = sum(len(v) for v in pairs_dict.values())
        else:
            key = next(iter(downloader.results))
            pairs_dict  = {key: pairs_result}
            total_pairs = len(pairs_result)

        print(f"  {total_pairs} pair(s) selected across {len(pairs_dict)} stack(s) — OK")
        assert total_pairs > 0, "select_pairs returned no pairs"

        for key, plist in pairs_dict.items():
            for ref, sec in plist[:3]:   # print first 3 per stack
                print(f"    {ref[:25]}… / {sec[:25]}…")
            if len(plist) > 3:
                print(f"    … and {len(plist)-3} more")

        # ─────────────────────────────────────────────────────────────────────
        # STAGE 3 — Simulate download (fake .SAFE dirs from real scene names)
        # ─────────────────────────────────────────────────────────────────────
        print("\n── STAGE 3: Download (simulated) ───────────────────────────")

        from insarhub.downloader.asf_base import _parse_scene_filter

        scene_filter = _parse_scene_filter(pairs_dict)
        assert scene_filter is not None
        print(f"  {len(scene_filter)} unique scenes needed by selected pairs")

        for name in scene_filter:
            _make_safe(workdir, name)

        created = list(workdir.glob("*.SAFE"))
        assert len(created) == len(scene_filter)
        print(f"  {len(created)} fake .SAFE dirs created — OK")

        # ─────────────────────────────────────────────────────────────────────
        # STAGE 4 — ISCE processing
        # ─────────────────────────────────────────────────────────────────────
        print("\n── STAGE 4: ISCE processing ────────────────────────────────")

        from insarhub.config import ISCE_S1_Config
        from insarhub.processor.isce_s1 import ISCE_S1

        cfg_isce = ISCE_S1_Config(
            workdir    = str(workdir),
            max_workers= 4,
            skip_existing = True,
        )
        proc = ISCE_S1(cfg_isce)
        proc._topsapp_bin = fake_bin

        jobs = proc.submit(pairs=pairs_dict)
        print(f"  submitted {len(jobs)} pair(s), waiting…")

        _wait_done(proc)

        result    = proc.refresh()
        succeeded = [m for m in result.values() if m["status"] == "SUCCEEDED"]
        failed    = [m for m in result.values() if m["status"] == "FAILED"]
        print(f"  {len(succeeded)} SUCCEEDED  {len(failed)} FAILED — OK")
        assert len(succeeded) > 0, "No pairs succeeded"

        saved_path = proc.save()
        print(f"  job file: {saved_path.name} — OK")

        # ─────────────────────────────────────────────────────────────────────
        # STAGE 5 — ISCE_SBAS prep_data
        # ─────────────────────────────────────────────────────────────────────
        print("\n── STAGE 5: ISCE_SBAS prep_data ────────────────────────────")

        from insarhub.config import ISCE_SBAS_Config
        from insarhub.analyzer.isce_sbas import ISCE_SBAS

        cfg_sbas = ISCE_SBAS_Config(workdir=str(workdir))
        analyzer = ISCE_SBAS(cfg_sbas)
        analyzer.prep_data()

        assert (workdir / ".mintpy.cfg").exists(), ".mintpy.cfg not written"
        assert "S1AB_*" in analyzer.config.load_unwFile
        bl_dir    = workdir / "baselines"
        date_dirs = list(bl_dir.iterdir()) if bl_dir.exists() else []
        print(f"  .mintpy.cfg written — OK")
        print(f"  load_unwFile : {analyzer.config.load_unwFile}")
        print(f"  metaFile     : {analyzer.config.load_metaFile}")
        print(f"  baselineDir  : {analyzer.config.load_baselineDir}")
        print(f"  baselines/   : {len(date_dirs)} date dir(s) — OK")

        # ─────────────────────────────────────────────────────────────────────
        # STAGE 6 — MintPy run (mocked)
        # ─────────────────────────────────────────────────────────────────────
        print("\n── STAGE 6: MintPy run (mocked) ────────────────────────────")

        analyzer.run(steps=["load_data", "reference_point", "invert_network"])
        print("  run() dispatched to TimeSeriesAnalysis (mocked) — OK")

        print("\n✓ Full pipeline test passed.\n")


if __name__ == "__main__":
    run_tests()
