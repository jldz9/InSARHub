"""
Real end-to-end pipeline: ISCE2/topsStack -> MintPy, via direct Python API
calls (no CLI subprocess involved) -- the library-import equivalent of
cli_e2e_isce_mintpy.sh.

Runs the actual insarhub library against real infrastructure, no mocking:
  - searches ASF and selects real interferogram pairs for path 100 / frame
    466 over the real p100_f466_isce AOI/date range
  - downloads real Sentinel-1 SLC scenes (~4GB each)
  - runs real ISCE2 stackSentinel processing for every real selected pair
  - runs ISCE_SBAS.prep_data() for real (real path discovery)
  - runs the real MintPy time-series workflow

Operates on p100_f466_isce/ (repo root) by default -- a real S1 SLC stack
(path 100 / frame 466), kept separate from api_e2e_hyp3_mintpy.py's default
p100_f466/ workdir: Hyp3_SBAS and ISCE_SBAS derive their MintPy output
directory purely from workdir (workdir/mintpy/, with no analyzer-type
awareness -- see MintPyPaths in config/paths.py), so running both e2e tests
against the same workdir would silently overwrite one pipeline's mintpy/
outputs (ifgramStack.h5, smallbaselineApp.cfg, velocity.h5, etc.) with the
other's.

Prerequisites -- see cli_e2e_isce_mintpy.sh for full details:
  - ~/.netrc (or ~/.credit_pool) with Earthdata credentials
  - A real ISCE2 + topsStack install. Build the base env from this repo's
    environment.yml, then add ISCE2 into the same env:
        mamba env create -f environment.yml -n insarhub_isce
        conda activate insarhub_isce
        mamba install -c conda-forge "numpy<2.0" isce2
        pip install -e .
  - ~50GB+ disk for the SLCs, plus processing intermediates
  - Time: real ISCE2 processing of 27 pairs on a single (non-HPC) machine
    can take many hours to multiple days.

Usage (run from the repo root, with the insarhub_isce env active):
    conda activate insarhub_isce
    python test/e2e/api_e2e_isce_mintpy.py [workdir]

This file is NOT auto-run by pytest (no test_ prefix, and everything lives
behind `if __name__ == "__main__":`) -- invoke it directly when you actually
want real ISCE2 processing to run. Safe to re-run: search/select-pairs is
skipped if a stack_*.json already exists; SLC download skips already-complete
files; ISCE2_S1.submit() skips any run-file step already marked SUCCEEDED
(skip_existing, on by default).
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# Real search AOI / date range / track for p100_f466 (path 100, frame 466) --
# matches cli_e2e_isce_mintpy.sh's --AOI/--start/--end/--stacks.
AOI = ("POLYGON ((-113.21199 37.66462, -112.47240 37.66462, "
       "-112.47240 38.15669, -113.21199 38.15669, -113.21199 37.66462))")
START = "2021-01-08"
END = "2021-06-01"
RELATIVE_ORBIT = 100
FRAME = 466


def run_pipeline(workdir: Path) -> None:
    try:
        import isce  # noqa: F401
    except ImportError:
        raise SystemExit(
            "[ERROR] Real ISCE2 is not importable in the active Python environment.\n"
            "        conda activate insarhub_isce\n"
            "        (build it first: mamba env create -f environment.yml -n insarhub_isce "
            "&& mamba install -n insarhub_isce -c conda-forge \"numpy<2.0\" isce2)"
        )

    from insarhub.config import S1_SLC_Config, ISCE2_S1_Config, ISCE_SBAS_Config
    from insarhub.downloader.s1_slc import S1_SLC
    from insarhub.processor.isce2_s1 import ISCE2_S1
    from insarhub.analyzer.isce_sbas import ISCE_SBAS

    workdir.mkdir(parents=True, exist_ok=True)

    print("== Stage 1/4: Search ASF + select real pairs " + "=" * 33)
    dl_cfg = S1_SLC_Config(
        workdir=str(workdir), intersectsWith=AOI, start=START, end=END,
        relativeOrbit=RELATIVE_ORBIT, frame=FRAME,
    )
    downloader = S1_SLC(dl_cfg)
    downloader.search()
    pairs, *_ = downloader.select_pairs()  # real ASF search is cheap/idempotent to redo

    print("== Stage 2/4: Download real Sentinel-1 SLC scenes " + "=" * 28)
    downloader.download(max_workers=4)  # skips scenes already downloaded at full size

    print("== Stage 3/4: Submit real ISCE2_S1 processing (runs detached in background) " + "=" * 5)
    proc = ISCE2_S1(pairs=pairs, config=ISCE2_S1_Config(workdir=str(workdir)))
    proc.submit()  # skip_existing (default True) makes re-running this safe

    print("== Stage 4/4: Watch real ISCE2 processing to completion " + "=" * 22)
    proc.watch()

    print("== ISCE_SBAS prep_data + real MintPy run " + "=" * 37)
    analyzer = ISCE_SBAS(ISCE_SBAS_Config(workdir=str(workdir)))
    analyzer.prep_data()
    analyzer.run()

    print(f"Done. MintPy outputs are under {workdir / 'mintpy'}/")


if __name__ == "__main__":
    import sys
    target = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else REPO_ROOT / "p100_f466_isce"
    run_pipeline(target)
