"""ISCE3 + COMPASS burst-based Sentinel-1 processor.

Consumes the ``.SAFE`` directories :class:`~insarhub.downloader.S1_Burst`
assembles, and follows the COMPASS stack workflow: geocode each burst
independently onto a common UTM grid, then form interferograms directly in map
coordinates.

The ISCE3/COMPASS infrastructure -- isce3 conda env discovery, per-stage status
markers, the synchronous stage loop and job persistence -- lives in
:class:`~insarhub.processor.ISCE3_Base`; this class supplies the burst-specific
stages, paths and inputs.

What makes this different from ISCE2_S1 / GMTSAR_S1
--------------------------------------------------
There is **no reference scene and no coregistration**. Each burst is geocoded to
absolute coordinates via orbit + DEM + timing LUTs, so two acquisitions land on
the same grid because both were geocoded there -- not because one was resampled
onto the other::

    GMTSAR_S1 / ISCE2_S1   pick a master -> resample every scene into its radar
                          geometry -> interferogram in radar coords -> geocode
                          accuracy depends on RELATIVE alignment to that master

    ISCE3_Burst           geocode each burst to absolute UTM
                          -> interferogram directly in map coords -> stitch
                          accuracy depends on ABSOLUTE geolocation; there is no
                          master, so nothing can degrade with distance from one

Stages
------
Modelled on the COMPASS stack notebook, one stage per numbered section::

    dem     Copernicus DEM (sardem) + NASADEM water-body mask       section 1.3
    tec     IONEX global ionosphere maps, one per acquisition date   section 1.4
    cslc    s1_geocode_stack.py -> runconfigs -> s1_cslc.py          section 2
    static  static layers (layover/shadow, incidence, LOS)           section 2.4
    crop    cut each geocoded burst down to the AOI                  section 3.1
    ifg     pair network + per-burst interferograms                  sections 3.2-3.3
    stitch  merge bursts into one raster per pair                    section 3.4
    filt    multilook -> Goldstein -> coherence                      sections 3.5-3.7
    unwrap  snaphu + connected components                            section 3.8

``dem``/``tec``/``cslc`` drive COMPASS and ISCE3 directly. Everything from
``crop`` onwards is `dolphin <https://github.com/opera-adt/dolphin>`_ -- the
same OPERA package behind the operational DISP-S1 product -- rather than the
notebook's hand-rolled ``utils.py`` helpers; see the "dolphin helpers" section
below for the one-to-one mapping. The output is a stack of unwrapped
interferograms ready for SBAS inversion.

``static`` is not implemented yet: the static layers carry incidence angle and
LOS unit vectors, which SBAS needs to convert LOS displacement into a ground
component, but nothing in the wrapped-to-unwrapped chain depends on them.

Environment
-----------
``isce3``, ``compass``, ``s1reader`` and ``sardem`` usually live in their own
conda env, not the one InSARHub runs from. ``isce3_env_bin`` is prepended to
PATH for every child process; when unset it is auto-detected by looking for
``s1_cslc.py`` next to the running interpreter, then in sibling conda envs.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from insarhub.config import ISCE3_Burst_Config
from insarhub.config.defaultconfig import _aoi_bbox_from_folder
from insarhub.processor.isce3_base import ISCE3_Base
from insarhub.utils.slurm_manager import sbatch_template_header

logger = logging.getLogger(__name__)


class ISCE3_Burst(ISCE3_Base):
    name = "ISCE3_Burst"
    description = ("Sentinel-1 burst InSAR via ISCE3 + COMPASS: geocoded burst "
                   "SLCs (no coregistration), stitched interferograms, snaphu.")
    default_config = ISCE3_Burst_Config
    compatible_downloader = "S1_Burst"

    #: job-file name; kept burst-specific so a workdir running another ISCE3
    #: processor keeps the two job files apart.
    JOBS_FILE = "isce3_burst_jobs.json"

    #: stage order. 'static' is the only one still unimplemented.
    # `los` is LAST, not folded into `static`, because the two have different
    # dependencies: the static layers themselves come from cslc, but putting
    # them on the stack grid needs a filtered interferogram to resample onto.
    # While both lived in `static` (stage 4 of 9) the grid never existed yet,
    # so the LOS half ALWAYS deferred and geometry/ was silently never built
    # unless the user knew to re-run the stage after filt.
    STAGES = ("dem", "tec", "cslc", "static", "crop", "ifg", "stitch",
              "filt", "unwrap", "los")
    _IMPLEMENTED = ("dem", "tec", "cslc", "static", "crop", "ifg", "stitch",
                    "filt", "unwrap", "los")

    # dolphin drives crop..unwrap; compass drives cslc/static/tec. Both are
    # imported inside the stage methods, so a child job only discovers a
    # missing one after it starts -- hence the pre-submit check.
    REQUIRED_MODULES = ("dolphin", "compass")

    # Per-stage SLURM resources for a fresh workdir. Sized by what each stage
    # actually does: cslc runs COMPASS geocoding per burst-date (the long pole),
    # unwrap runs snaphu, dem/tec are network-bound and want a core and time
    # rather than memory.
    SBATCH_DEFAULT_TEMPLATE = {
        **sbatch_template_header(),
        "_stages": {
            "dem":    "Copernicus DEM download (sardem) + water mask",
            "tec":    "IONEX ionosphere maps, one per acquisition date",
            "cslc":   "COMPASS geocoding -- ONE JOB PER BURST-DATE, the long pole",
            "static": "static geometry layers, one job per burst, then LOS mosaic",
            "crop":   "cut geocoded bursts to the AOI, one job per burst",
            "ifg":    "form interferograms, one job per burst",
            "stitch": "mosaic each pair's bursts, ONE JOB PER PAIR",
            "filt":   "multilook + Goldstein + coherence, ONE JOB PER PAIR",
            "unwrap": "snaphu, ONE JOB PER PAIR (plus a 1-job water-mask prologue)",
            "los":    "LOS/incidence geometry onto the stack grid -- needs filt, "
                      "so it runs LAST rather than inside static",
        },
        # "default" is the BASE every stage inherits (see
        # ISCE3_Base._stage_slurm_kwargs), so site-wide settings -- partition
        # above all -- belong here once rather than repeated per stage.
        # No "account": not every cluster requires one, and an invalid account
        # makes sbatch reject the job outright.
        "default": {"time": "02:00:00", "cpus_per_task": 4, "mem": "16G",
                    "partition": "all"},
        # Managers are pure bookkeeping and must outlive every child they
        # supervise, so give them the longest-walltime partition available.
        # Only "partition" is read here -- sizing is fixed by slurm_manager.
        "manager": {"partition": "all"},
        "dem":     {"time": "04:00:00", "cpus_per_task": 1, "mem": "8G"},
        "tec":     {"time": "02:00:00", "cpus_per_task": 1, "mem": "2G"},
        "cslc":    {"time": "04:00:00", "cpus_per_task": 4, "mem": "32G"},
        "static":  {"time": "02:00:00", "cpus_per_task": 4, "mem": "16G"},
        "crop":    {"time": "01:00:00", "cpus_per_task": 2, "mem": "8G"},
        # Sized for the default ifg_mode=phase_link, which holds a ministack's
        # worth of SLC covariance in memory rather than multiplying two rasters
        # -- far heavier than the pairwise path. Drop it back to ~16G/2h if you
        # switch to ifg_mode=network.
        "ifg":     {"time": "08:00:00", "cpus_per_task": 8, "mem": "64G"},
        "stitch":  {"time": "01:00:00", "cpus_per_task": 2, "mem": "16G"},
        "filt":    {"time": "01:00:00", "cpus_per_task": 2, "mem": "16G"},
        "unwrap":  {"time": "04:00:00", "cpus_per_task": 4, "mem": "24G"},
        "los":     {"time": "01:00:00", "cpus_per_task": 4, "mem": "16G"},
    }

    # ------------------------------------------------------------------
    # paths
    # ------------------------------------------------------------------

    @property
    def slc_dir(self) -> Path:
        # lowercase 'slc': S1_Burst writes .SAFE and their .EOF orbits together
        # there, the same layout S1_SLC produces.
        return self._p("slc_dir", "slc")

    @property
    def orbit_dir(self) -> Path:
        v = getattr(self.config, "orbit_dir", None)
        return Path(v).expanduser() if v else self.slc_dir

    @property
    def dem_path(self) -> Path:
        return self._p("dem_path", "dem/cop_dem.tif")

    @property
    def tec_dir(self) -> Path:
        return self._p("tec_dir", "tec")

    @property
    def cslc_dir(self) -> Path:
        return self._p("cslc_dir", "cslc")

    # dolphin-stage products. Laid out per stage rather than per burst so each
    # can be inspected, deleted and re-run on its own.
    @property
    def crop_dir(self) -> Path:
        return self._p("crop_dir", "cropped_slc")

    @property
    def pl_dir(self) -> Path:
        """Phase-linked SLCs, one directory per burst (ifg_mode=phase_link)."""
        return self._p("pl_dir", "phase_linked")

    @property
    def ifg_dir(self) -> Path:
        return self._p("ifg_dir", "ifgrams")

    # Overridable so the two ifg_mode paths can be run side by side in one
    # workdir; they share filenames but not grids, so writing both to the same
    # place would silently mix them.
    @property
    def stitch_dir(self) -> Path:
        return self._p("stitch_dir", "stitched/ifgrams")

    @property
    def filt_dir(self) -> Path:
        return self._p("filt_dir", "stitched/ifgrams_filtered")

    @property
    def unwrap_dir(self) -> Path:
        return self._p("unwrap_dir", "stitched/unwrapped")

    @property
    def quality_path(self) -> Path:
        """Per-pixel quality on the stack grid, for the time-series inversion.

        Phase linking fills this with stitched temporal coherence. In network
        mode there is no equivalent single raster -- the analyzer builds a
        temporal average of the pairwise correlations instead.
        """
        return self.stitch_dir.parent / "temporal_coherence.tif"

    @property
    def water_mask_path(self) -> Path:
        """NASADEM water-body mask written next to the DEM by ``run_dem``."""
        return self.dem_path.parent / "swbd_nasadem.wbd"

    # ------------------------------------------------------------------
    # inputs
    # ------------------------------------------------------------------

    def acquisition_dates(self) -> list[str]:
        """Sorted YYYYMMDD from the .SAFE names in slc_dir.

        Read off disk rather than from ``pairs`` so the DEM and TEC stages work
        straight after download, before any pairing has been decided.
        """
        out = set()
        for s in sorted(self.slc_dir.glob("*.SAFE")):
            m = re.search(r"_(\d{8})T\d{6}_", s.name)
            if m:
                out.add(m.group(1))
        return sorted(out)

    def dem_bbox(self) -> tuple[float, float, float, float]:
        """DEM footprint: AOI grown by ``dem_buffer_deg`` and snapped outward.

        The buffer is not cosmetic. Geocoding reaches beyond the AOI -- a burst
        extends past the target area, and range-Doppler terrain correction needs
        DEM coverage wherever the radar looks. A DEM cropped to the AOI leaves
        edge bursts with no elevation and produces void output there. The stack
        notebook uses 2 degrees for the same reason.
        """
        # Goes through the same resolver as the interferogram stages so DEM and
        # processing extent can never disagree. The DEM stage normally runs
        # BEFORE any burst is geocoded, so the full-extent fallback has nothing
        # to measure -- in that case config.AOI or the downloader's
        # intersectsWith is the only available answer, and _aoi() says so.
        import math
        w, s, e, n = self._aoi()
        b = float(getattr(self.config, "dem_buffer_deg", 2.0))
        return (math.floor(w - b), math.floor(s - b),
                math.ceil(e + b), math.ceil(n + b))

    # ------------------------------------------------------------------
    # stage: dem
    # ------------------------------------------------------------------

    def run_dem(self, force: bool = False) -> bool:
        """Copernicus DEM via sardem, plus the NASADEM water-body mask.

        Reproduces the stack notebook's section 1.3::

            sardem --bbox W S E N --output-type float32 --output-format GTiff \\
                   --data-source COP -o <dem_path>
            ut.download_nasadem_water_mask(dem_wsen, dem_path.parent)
        """
        dem = self.dem_path
        dem.parent.mkdir(parents=True, exist_ok=True)
        w, s, e, n = self.dem_bbox()

        if dem.exists() and not force:
            print(f"[ISCE3_Burst] DEM already present: {dem} "
                  f"({dem.stat().st_size / 1e6:.0f} MB) -- use force=True to redo")
        else:
            cmd = ["sardem", "--bbox", str(w), str(s), str(e), str(n),
                   "--output-type", "float32", "--output-format", "GTiff",
                   "--data-source", str(getattr(self.config, "dem_source", "COP")),
                   "-o", str(dem)]
            print(f"[ISCE3_Burst] DEM bbox {w} {s} {e} {n} "
                  f"(AOI + {getattr(self.config, 'dem_buffer_deg', 2.0)} deg)")
            print(f"  $ {' '.join(cmd)}")
            p = subprocess.run(cmd, env=self._env(), text=True,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if p.returncode != 0 or not dem.exists():
                logger.error("ISCE3_Burst: sardem failed (rc=%s): %s",
                             p.returncode, (p.stdout or "")[-600:])
                return False
            print(f"  -> {dem}  ({dem.stat().st_size / 1e6:.0f} MB)")

        if getattr(self.config, "water_mask", True):
            wbd = dem.parent / "swbd_nasadem.wbd"
            if wbd.exists() and not force:
                print(f"[ISCE3_Burst] water mask already present: {wbd.name}")
            elif not self._download_water_mask((w, s, e, n), dem.parent):
                # Non-fatal: the mask only blanks water before unwrapping.
                logger.warning("ISCE3_Burst: water mask unavailable; unwrapping "
                               "will proceed without masking open water")
        return True

    def _download_water_mask(self, bbox, out_dir: Path) -> bool:
        """NASADEM water-body mask, via the COMPASS notebooks' helper.

        That helper lives in the tutorial's ``utils.py`` rather than an
        installed package, so it is imported opportunistically and its absence
        is a warning, not a failure.
        """
        try:
            from insarhub.utils.tool import download_nasadem_water_mask  # type: ignore
        except Exception:                                        # noqa: BLE001
            try:
                import utils as _nb_utils                        # notebook-local
                download_nasadem_water_mask = _nb_utils.download_nasadem_water_mask
            except Exception as exc:                             # noqa: BLE001
                logger.warning("ISCE3_Burst: no download_nasadem_water_mask "
                               "available (%s)", exc)
                return False
        try:
            download_nasadem_water_mask(bbox, out_dir)
            return (out_dir / "swbd_nasadem.wbd").exists()
        except Exception as exc:                                 # noqa: BLE001
            logger.warning("ISCE3_Burst: water mask download failed: %s", exc)
            return False

    # ------------------------------------------------------------------
    # stage: tec
    # ------------------------------------------------------------------

    def run_tec(self, force: bool = False) -> bool:
        """IONEX global ionosphere maps, one per acquisition date.

        Reproduces the stack notebook's section 1.4. COMPASS consumes these as a
        timing correction during geocoding (``los_ionospheric_delay``), so they
        must exist before the cslc stage -- a missing TEC file silently drops
        the ionospheric term rather than failing.
        """
        try:
            from compass.utils.iono import download_ionex
        except ImportError as exc:                               # noqa: BLE001
            logger.error("ISCE3_Burst: COMPASS is not importable in this "
                         "environment (%s). TEC download needs "
                         "compass.utils.iono.", exc)
            return False

        dates = self.acquisition_dates()
        if not dates:
            logger.error("ISCE3_Burst: no .SAFE found in %s -- run the S1_Burst "
                         "downloader first", self.slc_dir)
            return False

        tec = self.tec_dir
        tec.mkdir(parents=True, exist_ok=True)
        code = str(getattr(self.config, "tec_sol_code", "jpl"))
        # Analysis centres to try when the preferred one has no map for a date.
        # CDDIS coverage is NOT uniform across centres: JPL's archive has a real
        # hole from 2023-08-11 to 2023-10-10 (spanning its jplgDDD0.YYi ->
        # JPL0OPSFIN_*.INX rename), where igs/cod/esa are missing too but UPC
        # has every day. Verified live against CDDIS.
        fallbacks = [c for c in
                     (str(x) for x in (getattr(self.config, "tec_fallback_codes", None)
                                       or ("igs", "cod", "esa", "upc")))
                     if c and c != code]
        print(f"[ISCE3_Burst] TEC ({code}) for {len(dates)} date(s) -> {tec}")

        have = _ionex_dates_on_disk(tec)
        ok, used_fallback, missing = 0, [], []
        for d in dates:
            if d in have and not force:
                ok += 1
                continue
            got = None
            for i, c in enumerate([code, *fallbacks]):
                try:
                    got = download_ionex(d, str(tec), sol_code=c)
                except Exception:                                # noqa: BLE001
                    continue
                if i:
                    used_fallback.append((d, c))
                break
            if got:
                ok += 1
            else:
                missing.append(d)

        if used_fallback:
            print(f"[ISCE3_Burst] {len(used_fallback)} date(s) fell back to another "
                  f"IONEX centre: " +
                  ", ".join(f"{d}={c}" for d, c in used_fallback[:6]) +
                  (" ..." if len(used_fallback) > 6 else ""))
        print(f"[ISCE3_Burst] TEC: {ok}/{len(dates)} date(s)")

        if missing:
            # A missing IONEX map is NOT fatal. COMPASS drops the ionospheric
            # delay term for that acquisition and geocodes it anyway, so the
            # cost is a small phase offset on pairs touching these dates -- not
            # a broken stack. Failing all 99 dates because a handful of days are
            # absent from every public archive would be disproportionate, so
            # this warns loudly and continues.
            logger.warning(
                "ISCE3_Burst: no IONEX map from any centre for %d date(s): %s. "
                "Those acquisitions will be geocoded WITHOUT an ionospheric "
                "correction.", len(missing), ", ".join(missing))
            print(f"[ISCE3_Burst] WARNING: {len(missing)} date(s) have no TEC from "
                  f"any centre and will be geocoded without an ionospheric "
                  f"correction: {', '.join(missing[:8])}"
                  + (" ..." if len(missing) > 8 else ""))
        return ok > 0

    # ------------------------------------------------------------------
    # stage: cslc
    # ------------------------------------------------------------------

    @property
    def burst_db_path(self) -> Path:
        v = getattr(self.config, "burst_db_path", None)
        if v:
            return Path(v).expanduser()
        # notebook default: a sibling of work_dir, shared across projects
        return self.workdir.parent / "s1-burst-db" / "opera-burst-bbox-only.sqlite3"

    _BURST_DB_URL = ("https://github.com/opera-adt/burst_db/releases/download/"
                     "v0.10.0/opera-burst-bbox-only.sqlite3")

    def _ensure_burst_db(self) -> bool:
        db = self.burst_db_path
        if db.exists():
            return True
        db.parent.mkdir(parents=True, exist_ok=True)
        print(f"[ISCE3_Burst] fetching OPERA burst DB -> {db}")
        try:
            from urllib.request import urlretrieve
            urlretrieve(self._BURST_DB_URL, db)
        except Exception as exc:                                 # noqa: BLE001
            logger.error("ISCE3_Burst: burst DB download failed: %s", exc)
            return False
        return db.exists()

    def tec_map(self) -> dict[str, str]:
        """{YYYYMMDD: ionex_path} from the files the tec stage downloaded.

        Handles both IONEX naming conventions the notebook does: the long IGS
        product name (``JPL0OPSFIN_20242350000_01D_02H_GIM.INX``, where the date
        is year+day-of-year) and the legacy short form (``jplg2350.24i``).
        """
        from datetime import timedelta
        out: dict[str, str] = {}
        for f in sorted(self.tec_dir.glob("*GIM.INX")):
            m = re.search(r"_(\d{4})(\d{3})\d{4}_", f.name)
            if m:
                d = datetime(int(m.group(1)), 1, 1) + timedelta(days=int(m.group(2)) - 1)
                out[d.strftime("%Y%m%d")] = str(f)
        for f in sorted(self.tec_dir.glob("jplg*.*i")):
            m = re.search(r"jplg(\d{3})0\.(\d{2})i", f.name)
            if m:
                d = datetime(2000 + int(m.group(2)), 1, 1) + timedelta(days=int(m.group(1)) - 1)
                out[d.strftime("%Y%m%d")] = str(f)
        return out

    def _cslc_output_of(self, run_script: Path) -> Path:
        """The .h5 a ``run_<date>_<burst_id>.sh`` script is expected to write."""
        prefix = run_script.stem                # run_<date>_<burst_id>
        date_str = prefix.split("_")[1]
        burst_id = prefix.split(date_str, 1)[1][1:]
        return self.cslc_dir / burst_id / date_str / f"{burst_id}_{date_str}.h5"

    def run_cslc(self, force: bool = False, prepare_only: bool = False) -> bool:
        """Geocode every burst x date onto a common UTM grid.

        Three sub-steps, from the stack notebook's sections 2.1-2.3:

          1. ``s1_geocode_stack.py`` -> ``cslc/run_files/`` + ``cslc/runconfigs/``
          2. inject each date's IONEX file into its runconfig, so COMPASS applies
             the ionospheric timing correction (a runconfig with a null
             ``tec_file`` silently drops the term rather than failing)
          3. execute the run_files, skipping any whose output .h5 already exists

        ``--common-bursts-only`` keeps only bursts present on EVERY date. That
        guarantees a rectangular stack, but silently narrows coverage when one
        date is missing a burst -- so the burst count is reported here rather
        than left for the user to notice later.
        """
        if not self._ensure_burst_db():
            return False
        for p, what in ((self.slc_dir, "SLC"), (self.dem_path, "DEM"),
                        (self.orbit_dir, "orbits")):
            if not p.exists():
                logger.error("ISCE3_Burst: %s not found at %s", what, p)
                return False
        aoi = getattr(self.config, "AOI", None)
        if not aoi or len(aoi) != 4:
            logger.error("ISCE3_Burst: config.AOI (W,S,E,N) required for --bbox")
            return False

        env = self._env()
        cslc = self.cslc_dir
        cslc.mkdir(parents=True, exist_ok=True)
        run_dir, cfg_dir = cslc / "run_files", cslc / "runconfigs"

        # ── 1. generate run files + runconfigs ─────────────────────────────
        if run_dir.exists() and any(run_dir.glob("run_*.sh")) and not force:
            print(f"[ISCE3_Burst] run_files already present ({len(list(run_dir.glob('run_*.sh')))}) "
                  f"-- use force=True to regenerate")
        else:
            # Same argument order as the notebook, built explicitly rather than
            # by index arithmetic -- an insert(-5, ...) silently lands in the
            # wrong place the moment the list changes.
            cmd = ["s1_geocode_stack.py",
                   "-s", str(self.slc_dir), "-d", str(self.dem_path),
                   "-o", str(self.orbit_dir), "-w", str(cslc),
                   "-dx", str(getattr(self.config, "x_posting", 10)),
                   "-dy", str(getattr(self.config, "y_posting", 20))]
            if getattr(self.config, "common_bursts_only", True):
                cmd.append("--common-bursts-only")
            cmd += ["--burst-db-file", str(self.burst_db_path), "--unzipped",
                    "--bbox", *[str(x) for x in aoi]]
            print(f"  $ {' '.join(cmd)}")
            p = subprocess.run(cmd, env=env, text=True,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if p.returncode != 0:
                logger.error("ISCE3_Burst: s1_geocode_stack.py failed (rc=%s): %s",
                             p.returncode, (p.stdout or "")[-800:])
                return False

        runs = sorted(run_dir.glob("run_*.sh"))
        if not runs:
            logger.error("ISCE3_Burst: s1_geocode_stack.py produced no run_files in %s", run_dir)
            return False
        bursts = sorted({r.stem.split("_", 2)[2] for r in runs if len(r.stem.split("_")) > 2})
        print(f"[ISCE3_Burst] {len(runs)} burst x date job(s), {len(bursts)} burst(s): {bursts}")

        # ── 2. inject TEC ──────────────────────────────────────────────────
        tmap = self.tec_map()
        print(f"[ISCE3_Burst] IONEX map: {len(tmap)} date(s)")
        if not tmap:
            logger.warning("ISCE3_Burst: no IONEX files in %s -- CSLC will be "
                           "generated WITHOUT the ionospheric correction", self.tec_dir)
        else:
            import yaml
            n_set = n_cfg = 0
            for cfg_path in sorted(cfg_dir.glob("geo_runconfig_????????_*.yaml")):
                n_cfg += 1
                parts = cfg_path.stem.split("_")
                if len(parts) < 4 or parts[2] not in tmap:
                    continue
                cfg = yaml.safe_load(cfg_path.read_text())
                grp = cfg["runconfig"]["groups"]["dynamic_ancillary_file_group"]
                if grp.get("tec_file") is None:
                    grp["tec_file"] = tmap[parts[2]]
                    cfg_path.write_text(yaml.dump(cfg, default_flow_style=False,
                                                  sort_keys=False))
                    n_set += 1
            print(f"[ISCE3_Burst] runconfigs given a tec_file: {n_set}/{n_cfg}")

        if prepare_only:
            # HPC path: the run scripts ARE the SLURM units, so generation and
            # TEC injection must finish before submission, but nothing is
            # executed here.
            for r in runs:
                r.chmod(r.stat().st_mode | 0o111)
            print(f"[ISCE3_Burst] prepared {len(runs)} run script(s) for submission")
            return True

        # ── 3. execute ─────────────────────────────────────────────────────
        todo = []
        for r in runs:
            out_h5 = self._cslc_output_of(r)
            if out_h5.exists() and not force:
                continue
            r.chmod(r.stat().st_mode | 0o111)
            todo.append((r, out_h5))
        done = len(runs) - len(todo)
        print(f"[ISCE3_Burst] {done} already generated, {len(todo)} to run")

        failures = []
        def _one(item):
            r, out_h5 = item
            p = subprocess.run(["bash", str(r)], env=env, text=True,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            ok = p.returncode == 0 and out_h5.exists()
            if not ok:
                failures.append((r.name, (p.stdout or "")[-400:]))
            return ok

        nw = max(1, int(getattr(self.config, "max_workers", 3)))
        if todo:
            with ThreadPoolExecutor(max_workers=nw) as ex:
                for i, ok in enumerate(ex.map(_one, todo), 1):
                    print(f"  [{i}/{len(todo)}] {'ok' if ok else 'FAILED'}")
        for name, tail in failures:
            logger.error("ISCE3_Burst: %s failed: %s", name, tail)

        made = len(list(cslc.glob("t*/*/*.h5")))
        print(f"[ISCE3_Burst] CSLC .h5 on disk: {made} (expected {len(runs)})")
        if made < len(runs):
            return False
        if failures:
            # COMPASS writes the .h5 and only then renders a PNG browse image.
            # That last step needs GDAL's HDF5 driver, so on an installation
            # without it every run "fails" with a complete, correct product.
            # Judge the stage on the products, not the exit code.
            logger.warning("ISCE3_Burst: %d run script(s) exited non-zero but all "
                           "%d CSLC products exist -- treating as success; see "
                           "the logged tails for what failed after writing",
                           len(failures), made)
        return True

    # ------------------------------------------------------------------
    # stages: crop / ifg / stitch / filt / unwrap  (dolphin)
    # ------------------------------------------------------------------

    def _selected_pairs(self) -> list[tuple[str, str]]:
        """The user's pair network for ``ifg_mode = user_defined``.

        Prefers the pairs the caller was constructed with (the GUI and CLI both
        read the folder's ``stack_*.json`` and hand them over). Falls back to
        reading those files directly, so the mode also works when the processor
        is driven straight from Python with no pairs argument.
        """
        import json

        pairs = [tuple(p) for p in (getattr(self, "pairs", None) or []) if len(p) >= 2]
        if pairs:
            return pairs

        files = sorted(self.workdir.glob("stack_p*.json")) or \
                sorted(self.workdir.glob("stack_*.json"))
        for f in files:
            try:
                data = json.loads(f.read_text())
            except Exception as exc:                             # noqa: BLE001
                logger.warning("ISCE3_Burst: unreadable pair file %s: %s", f.name, exc)
                continue
            pairs.extend(tuple(p[:2]) for p in data.get("pairs", []) if len(p) >= 2)

        if not pairs:
            raise FileNotFoundError(
                f"ISCE3_Burst: ifg_mode=user_defined needs a pair "
                f"network, but no stack_*.json with pairs was found in "
                f"{self.workdir}. Run select_pairs for this folder (or the "
                f"network editor), or switch ifg_mode to 'network' to build the "
                f"graph from n_connections.")
        # A merged folder holds one pair file per path; the burst stack they
        # describe is a single date list, so dedupe across files.
        return sorted(set(pairs))

    def _aoi(self) -> list[float]:
        """Processing extent as (W, S, E, N) degrees.

        ``config.AOI`` is normally already populated -- ISCE3_Burst_Config's
        __post_init__ seeds it from the folder's downloader ``intersectsWith``
        -- so this usually just returns it. The fallbacks cover a config built
        before the folder had a downloader section, or one loaded from an older
        saved file.

        With ``process_full_extent`` set, AOI is ignored and the extent is
        measured from the geocoded bursts instead.

        The old behaviour required AOI and raised otherwise, so a folder
        downloaded by AOI -- which already recorded that AOI in the same
        directory -- still refused to process until the box was filled in by
        hand, and processing a whole burst footprint was not expressible.
        """
        if bool(getattr(self.config, "process_full_extent", False)):
            box = self._aoi_from_bursts()
            if box:
                print(f"[ISCE3_Burst] full downloaded extent: "
                      f"{['%.4f' % v for v in box]}")
                return box
            raise ValueError(
                "ISCE3_Burst: process_full_extent measures the extent from the "
                "geocoded bursts, and none exist yet. The dem and cslc stages "
                "run before anything is geocoded, so they need a starting "
                "extent -- leave process_full_extent off for those, or set AOI.")

        aoi = getattr(self.config, "AOI", None)
        if aoi and len(aoi) == 4:
            return [float(x) for x in aoi]

        box = _aoi_bbox_from_folder(self.workdir)
        if box:
            print(f"[ISCE3_Burst] AOI from the downloader's intersectsWith: "
                  f"{['%.4f' % v for v in box]}")
            return box

        box = self._aoi_from_bursts()
        if not box:
            raise ValueError(
                "ISCE3_Burst: could not determine a processing extent. No "
                "config.AOI, no intersectsWith in this folder's "
                "insarhub_config.json, and no geocoded bursts on disk to "
                "measure. Set AOI explicitly.")
        print(f"[ISCE3_Burst] AOI = full downloaded extent: "
              f"{['%.4f' % v for v in box]}")
        return box

    def _aoi_from_bursts(self) -> list[float] | None:
        """(W, S, E, N) covering every geocoded burst, in degrees.

        The CSLCs are geocoded to absolute UTM, so their bounds are reprojected
        to EPSG:4326 and unioned -- this is the true full extent of what was
        downloaded, which is what 'process the whole thing' has to mean.
        """
        try:
            from osgeo import gdal, osr
        except Exception:                                        # noqa: BLE001
            return None

        tifs = sorted(self.crop_dir.rglob("*.slc.tif")) or \
               sorted(self.cslc_dir.rglob("*.slc.tif"))
        if not tifs:
            return None

        wgs = osr.SpatialReference()
        wgs.ImportFromEPSG(4326)
        wgs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)

        w = s = e = n = None
        for t in tifs:
            ds = gdal.Open(str(t))
            if ds is None:
                continue
            gt, nx, ny = ds.GetGeoTransform(), ds.RasterXSize, ds.RasterYSize
            src = osr.SpatialReference()
            src.ImportFromWkt(ds.GetProjection())
            src.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
            tf = osr.CoordinateTransformation(src, wgs)
            xs = (gt[0], gt[0] + gt[1] * nx)
            ys = (gt[3], gt[3] + gt[5] * ny)
            for x in xs:
                for y in ys:
                    lon, lat, *_ = tf.TransformPoint(x, y)
                    w = lon if w is None else min(w, lon)
                    e = lon if e is None else max(e, lon)
                    s = lat if s is None else min(s, lat)
                    n = lat if n is None else max(n, lat)
            ds = None

        return None if w is None else [w, s, e, n]

    @property
    def geom_dir(self) -> Path:
        """LOS/geometry rasters on the interferogram grid, for SBAS."""
        return self.workdir / "stitched" / "geometry"

    def run_static(self, force: bool = False) -> bool:
        """Static geometry layers per burst, then LOS vectors on the stack grid.

        The geometry does not change with time -- it comes from the orbit, the
        DEM and the burst's own timing -- so this runs once per burst, on that
        burst's earliest runconfig, not once per acquisition.

        Two steps: COMPASS's ``s1_static_layers.py`` writes
        ``static_layers_<burst>.h5`` (los_east, los_north, local_incidence_angle,
        layover_shadow_mask), then those are cropped, mosaicked and resampled
        onto the interferogram grid so SBAS can actually use them.
        """

        cfg_dir = self.cslc_dir / "runconfigs"
        cfgs = sorted(cfg_dir.glob("geo_runconfig_????????_*.yaml"))
        if not cfgs:
            print(f"[ISCE3_Burst] no runconfigs in {cfg_dir} -- run 'cslc' first")
            return False

        # earliest runconfig per burst
        per_burst: dict[str, Path] = {}
        for f in cfgs:
            parts = f.stem.split("_")
            if len(parts) < 4:
                continue
            date_str = parts[2]
            burst_id = f.stem.split(date_str, 1)[1][1:]
            per_burst.setdefault(burst_id, f)

        env = self._env()
        todo = []
        for burst_id, cfg in sorted(per_burst.items()):
            hits = list(self.cslc_dir.glob(
                f"{burst_id}/*/static_layers_{burst_id}.h5"))
            if hits and not force:
                continue
            todo.append((burst_id, cfg))
        print(f"[ISCE3_Burst] static layers: {len(per_burst) - len(todo)} present, "
              f"{len(todo)} to generate")

        failures = []
        for burst_id, cfg in todo:
            p = subprocess.run(["s1_static_layers.py", str(cfg)], env=env,
                               text=True, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT)
            hits = list(self.cslc_dir.glob(
                f"{burst_id}/*/static_layers_{burst_id}.h5"))
            if not hits:
                failures.append((burst_id, (p.stdout or "")[-400:]))
                logger.error("ISCE3_Burst: static layers failed for %s: %s",
                             burst_id, (p.stdout or "")[-400:])
            else:
                print(f"  {burst_id}: {hits[0].name}")
        if failures:
            return False

        # put them on the grid the unwrapped products live on
        like = sorted(self.filt_dir.glob("*.int.tif"))
        if not like:
            print("[ISCE3_Burst] static layers generated, but no filtered "
                  "interferogram to define the output grid -- run 'filt' to "
                  "get los_*.tif on the stack grid")
            return True

        made = build_los_layers(
            self.cslc_dir, self.geom_dir, self._aoi(), like[0],
            buffer_deg=float(getattr(self.config, "crop_buffer_deg", 0.05)))
        print(f"[ISCE3_Burst] geometry on stack grid: "
              f"{sorted(made)} -> {self.geom_dir}")
        return "los_up" in made

    def run_los(self, force: bool = False) -> bool:
        """Put the static geometry layers on the interferogram grid.

        Separate from `static` because it needs a filtered interferogram to
        resample onto -- see the STAGES comment. Produces los_east/los_north/
        los_up and incidence in geometry/, which the analyzer needs to convert
        line-of-sight displacement into a ground component.
        """
        return self._run_static_los()

    def run_crop(self, force: bool = False) -> bool:
        """Cut each geocoded burst to the AOI (notebook 3.1)."""

        made, total = crop_cslc(
            self.cslc_dir, self.crop_dir, self._aoi(),
            buffer_deg=float(getattr(self.config, "crop_buffer_deg", 0.05)),
            max_workers=self._nw, force=force)
        print(f"[ISCE3_Burst] cropped SLCs: {made} (from {total} CSLC)")
        return total > 0 and made >= total

    def run_ifg(self, force: bool = False) -> bool:
        """Form per-burst interferograms (3.2-3.3).

        Two estimators, selected by ``config.ifg_mode``:

        ``network``      form a chosen subset of pairs directly from the
                         cropped SLCs. Cheap, and what the COMPASS notebook
                         does.
        ``phase_link``   first estimate each burst's phase history from the
                         full SLC covariance, then form the network from
                         *that*. Every pair contributes to the estimate; the
                         network afterwards only decides how the result is
                         expressed.

        Both end with :func:`form_ifgs` over a directory of ``<date>.slc.tif``,
        so the pairing code is shared -- phase linking just changes which SLCs
        it reads.
        """
        import json

        mode = str(getattr(self.config, "ifg_mode", "network")).lower()
        if mode not in ("network", "phase_link", "user_defined"):
            raise ValueError(
                f"ISCE3_Burst: ifg_mode must be 'network', 'user_defined' "
                f"or 'phase_link', got {mode!r}")

        # Products from the two modes have identical names but different grids
        # (phase linking decimates by `strides`). Leaving the previous mode's
        # rasters in place would let `stitch` mix the two without complaint, so
        # a mode change clears them.
        manifest = self.ifg_dir / "ifg_manifest.json"
        if manifest.exists():
            try:
                prev = json.loads(manifest.read_text()).get("mode")
            except Exception:                                    # noqa: BLE001
                prev = None
            if prev and prev != mode:
                print(f"[ISCE3_Burst] ifg_mode changed {prev} -> {mode}; "
                      "clearing interferograms from the previous mode")
                for f in self.ifg_dir.glob("t*_iw*/*.int.tif"):
                    f.unlink()
                force = True

        looks_applied = None
        src = self.crop_dir
        reference_idx = None
        temp_coh: dict[str, str] = {}
        if mode == "phase_link":
            lks = (int(getattr(self.config, "azlks", 2)),
                   int(getattr(self.config, "rglks", 4)))
            tc = phase_link_bursts(
                self.crop_dir, self.pl_dir,
                half_window=(int(getattr(self.config, "pl_half_window_y", 7)),
                             int(getattr(self.config, "pl_half_window_x", 14))),
                strides=lks,
                ministack_size=int(getattr(self.config, "pl_ministack_size", 15)),
                shp_method=str(getattr(self.config, "pl_shp_method", "glrt")),
                shp_alpha=float(getattr(self.config, "pl_shp_alpha", 0.001)),
                use_evd=bool(getattr(self.config, "pl_use_evd", False)),
                beta=float(getattr(self.config, "pl_beta", 0.0)),
                baseline_lag=getattr(self.config, "pl_baseline_lag", None),
                max_workers=self._nw, force=force)
            src = self.pl_dir
            looks_applied = list(lks)      # strides already decimated
            temp_coh = {k: str(v) for k, v in tc.items()}

            # After phase linking the estimator's output IS the single-reference
            # stack: the reference date's SLC has zero phase and every other
            # carries theta_i - theta_ref, so ifg(ref, i) reproduces it exactly.
            # A bandwidth network here re-derives those differences -- verified
            # identical to 1e-8 rad -- so it buys no wrapped-phase information,
            # only more unwrapping. Redundancy still helps catch UNWRAPPING
            # errors, which is why 'bandwidth' remains selectable.
            if str(getattr(self.config, "pl_ifg_network",
                           "single_reference")).lower() == "single_reference":
                reference_idx = 0

        # Explicit pair network. Selected by ifg_mode rather than a separate
        # flag so there is exactly one control deciding where pairs come from.
        want_pairs = None
        if mode == "user_defined":
            want_pairs = self._selected_pairs()
            print(f"[ISCE3_Burst] user_defined: {len(want_pairs)} pair(s) "
                  f"from the folder's pair file (n_connections ignored)")

        pairs = form_ifgs(
            src, self.ifg_dir,
            n_connections=int(getattr(self.config, "n_connections", 3)),
            max_temporal_baseline=getattr(self.config, "max_temporal_baseline", None),
            reference_idx=reference_idx, pairs=want_pairs,
            max_workers=self._nw, force=force)

        # record the network so the analyzer and any SBAS step downstream read
        # the same pair list the interferograms were actually formed from
        lst = self.ifg_dir / "ifgram_list.txt"
        lst.parent.mkdir(parents=True, exist_ok=True)
        lst.write_text("# ISCE3_Burst pair network (ref_sec)\n"
                       + "\n".join(pairs) + "\n")

        # Phase linking writes temporal coherence per burst, at burst extent.
        # The time-series inversion needs one raster on the stack grid (it is
        # the quality file used to pick the reference point), so mosaic it here
        # -- the analyzer should consume a product, not rebuild one.
        quality = None
        if mode == "phase_link" and temp_coh:
            quality = self.quality_path
            try:
                _mosaic_real([Path(v) for v in temp_coh.values()],
                             quality, self._aoi())
                print(f"[ISCE3_Burst] temporal coherence mosaic -> {quality.name}")
            except Exception as exc:                             # noqa: BLE001
                logger.error("ISCE3_Burst: temporal-coherence mosaic failed: %s", exc)
                quality = None

        info = {"mode": mode, "pairs": pairs, "looks_applied": looks_applied,
                "temporal_coherence": temp_coh,
                "quality_file": str(quality) if quality else None,
                "ifg_dir": str(self.ifg_dir)}
        manifest.write_text(json.dumps(info, indent=1))

        # Also drop it beside the stitched products. The analyzer is pointed at
        # an unwrap directory, and with both ifg_modes present in one workdir
        # there is no way to tell which ifgrams/ dir produced it -- searching by
        # convention picks whichever exists first and silently mislabels the
        # stack. Writing it into the stack's own tree keeps them tied.
        side = self.stitch_dir.parent / "stack_info.json"
        side.parent.mkdir(parents=True, exist_ok=True)
        side.write_text(json.dumps(info, indent=1))

        n_burst = len(sorted(d for d in self.ifg_dir.iterdir()
                             if d.is_dir() and d.name.startswith("t")))
        made = len(list(self.ifg_dir.glob("t*_iw*/*.int.tif")))
        want = len(pairs) * n_burst
        print(f"[ISCE3_Burst] {len(pairs)} pairs x {n_burst} bursts -> "
              f"{made}/{want} interferograms")
        return want > 0 and made >= want

    # ------------------------------------------------------------------
    # HPC decomposition
    # ------------------------------------------------------------------

    def stage_progress(self, stage: str) -> tuple[int, int]:
        """``(done, total)`` for a stage, from PRODUCTS ON DISK.

        Deliberately independent of how the stage was run. Counting a
        submission's SLURM jobs describes only the last invocation: a resume
        that repaired 19 of 192 burst-dates reported "19/19", which is true of
        that submission and useless as a description of the stack. It also made
        a finished stage look unfinished whenever leftover child scripts from a
        longer earlier list inflated the denominator.

        Products are the only measure that is the same whether a stage ran
        locally, on SLURM, in one go or across five resumes -- so this is what
        "is the workdir done?" actually means.

        ``(0, 0)`` means "no meaningful count", not "nothing done".
        """
        try:
            if stage == "dem":
                return (1 if self.dem_path.exists() else 0, 1)
            if stage == "tec":
                dates = set(self.acquisition_dates())
                have = _ionex_dates_on_disk(self.tec_dir) & dates
                return (len(have), len(dates))
            if stage == "cslc":
                runs = sorted((self.cslc_dir / "run_files").glob("run_*.sh"))
                done = sum(1 for r in runs if self._cslc_output_of(r).exists())
                return (done, len(runs))
            if stage == "static":
                n = len(self._static_runconfigs())
                have = len([f for f in self.cslc_dir.rglob("static_layers_*.h5")])
                return (have, n)
            if stage == "los":
                want = ["los_east", "los_north", "los_up",
                        "local_incidence_angle", "layover_shadow_mask"]
                have = sum(1 for w in want if (self.geom_dir / f"{w}.tif").exists())
                return (have, len(want))
            if stage == "crop":
                src = _burst_dirs(self.cslc_dir)
                want = sum(len([h for h in (b).rglob("*.h5")
                                if "static_layers" not in h.name]) for b in src)
                have = len(list(self.crop_dir.rglob("*.slc.tif")))
                return (have, want)
            if stage == "ifg":
                pairs = (self._ifg_manifest().get("pairs") or [])
                nb = len(_burst_dirs(self.crop_dir))
                return (len(list(self.ifg_dir.glob("t*_iw*/*.int.tif"))),
                        len(pairs) * nb)
            if stage == "stitch":
                pairs = (self._ifg_manifest().get("pairs") or [])
                return (len(list(self.stitch_dir.glob("*.int.tif"))), len(pairs))
            if stage == "filt":
                pairs = (self._ifg_manifest().get("pairs") or [])
                return (len(list(self.filt_dir.glob("*.int.tif"))), len(pairs))
            if stage == "unwrap":
                pairs = (self._ifg_manifest().get("pairs") or [])
                return (len(list(self.unwrap_dir.glob("*.unw.tif"))), len(pairs))
        except Exception as exc:                                 # noqa: BLE001
            logger.debug("stage_progress(%s) failed: %s", stage, exc)
        return (0, 0)

    def stage_units(self, stage: str) -> list[tuple[str, bool]]:
        """``[(unit label, done), ...]`` for every real unit of a stage.

        What ``--ls`` lists. Like :meth:`stage_progress` this is derived from
        products, not from the last submission's ``cmd_XXXX`` markers -- those
        cover only whatever that invocation happened to run, so after a resume
        that repaired 19 dates ``--ls cslc`` listed 19 lines for a 192-date
        stage. Labels are the real identifiers (date + burst, or the pair) so a
        missing unit can be acted on directly.
        """
        try:
            if stage == "cslc":
                return [(r.stem.removeprefix("run_"),
                         self._cslc_output_of(r).exists())
                        for r in sorted((self.cslc_dir / "run_files").glob("run_*.sh"))]
            if stage == "tec":
                have = _ionex_dates_on_disk(self.tec_dir)
                return [(d, d in have) for d in sorted(set(self.acquisition_dates()))]
            if stage == "static":
                return [(c.stem.split("_", 3)[-1],
                         bool(list(self.cslc_dir.rglob(
                             f"static_layers_{c.stem.split('_', 3)[-1]}.h5"))))
                        for c in self._static_runconfigs()]
            if stage == "dem":
                return [(self.dem_path.name, self.dem_path.exists())]
            if stage == "los":
                return [(w, (self.geom_dir / f"{w}.tif").exists())
                        for w in ("los_east", "los_north", "los_up",
                                  "local_incidence_angle", "layover_shadow_mask")]
            if stage == "crop":
                out = []
                for b in _burst_dirs(self.cslc_dir):
                    for h in sorted(b.rglob("*.h5")):
                        if "static_layers" in h.name:
                            continue
                        d = h.parent.name
                        out.append((f"{b.name}/{d}",
                                    (self.crop_dir / b.name / f"{d}.slc.tif").exists()))
                return out
            if stage in ("stitch", "filt", "unwrap"):
                pairs = self._ifg_manifest().get("pairs") or []
                d, suf = {"stitch": (self.stitch_dir, ".int.tif"),
                          "filt":   (self.filt_dir, ".int.tif"),
                          "unwrap": (self.unwrap_dir, ".unw.tif")}[stage]
                return [(p, (d / f"{p}{suf}").exists()) for p in pairs]
            if stage == "ifg":
                pairs = self._ifg_manifest().get("pairs") or []
                out = []
                for b in _burst_dirs(self.crop_dir):
                    for p in pairs:
                        out.append((f"{b.name}/{p}",
                                    (self.ifg_dir / b.name / f"{p}.int.tif").exists()))
                return out
        except Exception as exc:                                 # noqa: BLE001
            logger.debug("stage_units(%s) failed: %s", stage, exc)
        return []

    def _reentry(self, stage: str, index: int | None = None) -> str:
        """Shell command running ONE unit of a stage by re-entering insarhub.

        Most stages are Python methods calling dolphin in-process, so a child
        job cannot be a bare shell line the way ISCE2's run-file commands are.
        Re-entering the CLI keeps the orchestration in one place instead of
        re-implementing each stage in bash -- the alternative that produced
        drift bugs elsewhere in this codebase.
        """
        exe = f"{sys.executable} -m insarhub.cli.main"
        c = (f'{exe} processor -N {type(self).name} -w "{self.workdir}" '
             f'run-stage-unit --stage {stage}')
        return c if index is None else f"{c} --index {index}"

    def hpc_phases(self, stage: str) -> list[tuple[str, list[str]]]:
        """How each stage splits into parallel SLURM child jobs.

        Granularity follows where the time actually goes:

        ==========  ==========================================================
        dem/tec     one job each -- network-bound, minutes, nothing to split
        cslc        one job per ``run_*.sh`` COMPASS already wrote. These are
                    real shell scripts, so no re-entry is needed and this is
                    exactly ISCE2's shape. The long pole: N dates x M bursts.
        static      map over bursts, then a 1-job reduce that mosaics the LOS
                    layers onto the interferogram grid
        crop/ifg    map over bursts
        stitch      map over pairs, one job each
        filt        map over pairs
        unwrap      map over pairs -- snaphu, the second-longest stage
        ==========  ==========================================================

        `cslc` needs its runconfigs to exist before the units can be listed, so
        the generation half runs here, synchronously, before submission -- the
        analogue of ISCE2 generating run files up front.
        """
        if stage in ("dem", "tec"):
            return [("", [self._reentry(stage)])]

        if stage == "cslc":
            runs = self._prepare_cslc_runs()
            return [("", [f'bash "{r}"' for r in runs])]

        if stage == "static":
            n = len(self._static_runconfigs())
            return [("", [self._reentry("static", i) for i in range(n)])]

        if stage == "los":
            return [("", [self._reentry("los")])]

        if stage == "crop":
            bursts = [d.name for d in _burst_dirs(self.cslc_dir)]
            return [("", [self._reentry("crop", i) for i in range(len(bursts))])]

        if stage == "ifg":
            # ONE job for the whole stage, not one per burst.
            #
            # run_ifg() has no single-burst mode: phase linking estimates from
            # the full N x N covariance of every date, form_ifgs() loops every
            # burst, and both write a shared ifg_manifest.json. Emitting one
            # job per burst therefore did not split the work at all -- it ran
            # the ENTIRE stage N times concurrently, racing on the same
            # phase_linked/ and ifgrams/ outputs. Parallelism inside the stage
            # comes from max_workers threads in one process, so this is a
            # single job sized cpus_per_task=max_workers.
            return [("", [self._reentry("ifg")])]

        if stage in ("stitch", "filt"):
            pairs = self._hpc_pair_list()
            return [("", [self._reentry(stage, i) for i in range(len(pairs))])]

        if stage == "unwrap":
            # Two phases: the water mask must be built ONCE (see unwrap_ifgs'
            # prepare_only) before N per-pair jobs read it, or they race on the
            # same file.
            pairs = self._hpc_pair_list()
            return [("prep", [self._reentry("unwrap_prep")]),
                    ("pairs", [self._reentry("unwrap", i) for i in range(len(pairs))])]

        return []

    def _static_runconfigs(self) -> list[Path]:
        """One runconfig per burst -- its earliest date, geometry being static."""
        cfgs = sorted((self.cslc_dir / "runconfigs").glob("geo_runconfig_????????_*.yaml"))
        first: dict[str, Path] = {}
        for c in cfgs:
            burst = c.stem.split("_", 3)[-1]
            first.setdefault(burst, c)
        return [first[k] for k in sorted(first)]

    def _prepare_cslc_runs(self) -> list[Path]:
        """Generate runconfigs + run_*.sh and return the scripts still to run.

        Runs synchronously before any job is submitted -- the analogue of
        ISCE2 generating its run files up front. The units of the cslc stage
        ARE these scripts, so they must exist before the manager can be built.
        Already-generated products are filtered out so a resumed submission
        only queues the missing dates.
        """
        if not self.run_cslc(prepare_only=True):
            raise RuntimeError(
                "ISCE3_Burst: could not generate COMPASS run files -- see the "
                "log above. Fix that before submitting to SLURM.")
        runs = sorted((self.cslc_dir / "run_files").glob("run_*.sh"))
        todo = [r for r in runs if not self._cslc_output_of(r).exists()]
        print(f"[ISCE3_Burst] cslc: {len(runs) - len(todo)} already geocoded, "
              f"{len(todo)} to submit")
        return todo

    def _run_static_unit(self, cfg: Path) -> bool:
        """One burst's static layers -- ``s1_static_layers.py <runconfig>``."""
        p = subprocess.run(["s1_static_layers.py", str(cfg)], env=self._env(),
                           text=True, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT)
        if p.returncode != 0:
            logger.error("ISCE3_Burst: s1_static_layers.py %s failed: %s",
                         cfg.name, (p.stdout or "")[-500:])
            return False
        return True

    def _run_static_los(self) -> bool:
        """The reduce half of `static`: mosaic LOS layers onto the ifg grid.

        Deferring is NOT a failure. `static` sits before `filt` in the stage
        order, but the grid it must resample onto is defined by the filtered
        interferograms -- so on a first pass through the chain there is nothing
        to resample onto yet. run_static() has always handled that by returning
        success with a note; this mirrors it, so an HPC chain does not halt at
        stage 4 of 9. Re-run `static` once `filt` is done to get los_*.tif.
        """
        like = sorted(self.filt_dir.glob("*.int.tif"))
        if not like:
            print("[ISCE3_Burst] static layers generated, but no filtered "
                  "interferogram to define the output grid -- re-run 'static' "
                  "after 'filt' to get los_*.tif on the stack grid")
            return True
        made = build_los_layers(
            self.cslc_dir, self.geom_dir, self._aoi(), like[0],
            buffer_deg=float(getattr(self.config, "crop_buffer_deg", 0.05)))
        print(f"[ISCE3_Burst] geometry on stack grid: {sorted(made)}")
        return "los_up" in made

    def run_stage_unit(self, stage: str, index: int | None = None) -> bool:
        """Execute ONE unit of a stage. The entry point every child job calls.

        Deliberately no status-marker writes: a unit is a fraction of a stage,
        and the stage's verdict belongs to its manager, which writes SUCCEEDED
        or FAILED once every unit has finished. A unit reports via its exit
        code, which the child sbatch wrapper turns into a .done/.fail marker.
        """
        if stage == "static" and index is not None:
            return self._run_static_unit(self._static_runconfigs()[int(index)])
        if stage in ("los", "static_los"):   # static_los: pre-split alias
            return self._run_static_los()
        if stage == "unwrap_prep":
            return self._run_unwrap_prep()
        if index is not None and stage in ("crop", "ifg", "stitch", "filt", "unwrap"):
            return self._run_dolphin_unit(stage, int(index))
        runner = getattr(self, f"run_{stage}", None)
        if runner is None:
            raise ValueError(f"ISCE3_Burst: no unit runner for stage {stage!r}")
        return bool(runner())

    def _hpc_pair_list(self) -> list[str]:
        """Pairs the ifg stage actually formed, from its manifest.

        The per-pair stages index into THIS list, so it must be the network
        that was really built -- not one recomputed from config, which could
        differ if ifg_mode or n_connections changed between stages and would
        silently map index i onto a different pair in each stage.
        """
        pairs = self._ifg_manifest().get("pairs") or []
        if not pairs:
            raise RuntimeError(
                "ISCE3_Burst: no ifg_manifest.json with pairs in "
                f"{self.ifg_dir}. The per-pair stages (stitch/filt/unwrap) "
                "cannot be split into jobs until 'ifg' has run and recorded "
                "its network.")
        return list(pairs)

    def _run_unwrap_prep(self) -> bool:
        """Build the on-grid water mask once, before the per-pair unwrap jobs."""
        mask = self.water_mask_path if self.water_mask_path.exists() else None
        if mask is None:
            logger.warning("ISCE3_Burst: no water mask at %s -- unwrapping over "
                           "water, which can seed errors that leak inland",
                           self.water_mask_path)
        unwrap_ifgs(self.filt_dir, self.unwrap_dir, mask_file=mask,
                    max_workers=self._nw, prepare_only=True)
        return True

    def _run_dolphin_unit(self, stage: str, index: int) -> bool:
        """One burst (crop/ifg) or one pair (stitch/filt/unwrap)."""
        cfg = self.config
        if stage == "crop":
            burst = _burst_dirs(self.cslc_dir)[index].name
            made, total = crop_cslc(
                self.cslc_dir, self.crop_dir, self._aoi(),
                buffer_deg=float(getattr(cfg, "crop_buffer_deg", 0.05)),
                max_workers=self._nw, only=burst)
            return total > 0 and made >= total

        if stage == "ifg":
            # Whole stage in one call -- see hpc_phases, which emits exactly
            # one ifg job for this reason. `index` is ignored and should never
            # be supplied.
            return self.run_ifg()

        pair = self._hpc_pair_list()[index]
        if stage == "stitch":
            made = stitch_ifgs(self.ifg_dir, self.stitch_dir, self._aoi(),
                               max_workers=self._nw, only=pair)
            return bool(made)
        if stage == "filt":
            mf = self._ifg_manifest()
            applied = mf.get("looks_applied")
            lks_y, lks_x = (1, 1) if applied else (
                int(getattr(cfg, "azlks", 2)), int(getattr(cfg, "rglks", 4)))
            out = filter_ifgs(
                self.stitch_dir, self.filt_dir, lks_y=lks_y, lks_x=lks_x,
                alpha=float(getattr(cfg, "filt_strength", 0.5)),
                coh_window=int(getattr(cfg, "coh_window", 11)),
                max_workers=self._nw, only=pair)
            return bool(out)
        if stage == "unwrap":
            t = int(getattr(cfg, "unwrap_tiles", 2))
            mask = self.water_mask_path if self.water_mask_path.exists() else None
            unw = unwrap_ifgs(
                self.filt_dir, self.unwrap_dir,
                nlooks=float(getattr(cfg, "unwrap_nlooks", 8.0)),
                ntiles=(t, t), cost=str(getattr(cfg, "unwrap_cost", "smooth")),
                init_method=str(getattr(cfg, "unwrap_init_method", "mcf")),
                mask_file=mask, max_workers=self._nw, only=pair)
            return bool(unw)
        raise ValueError(f"ISCE3_Burst: no dolphin unit runner for {stage!r}")

    def run_stitch(self, force: bool = False) -> bool:
        """Merge each pair's bursts into a single raster (notebook 3.4)."""

        made = stitch_ifgs(self.ifg_dir, self.stitch_dir, self._aoi(),
                               max_workers=self._nw)
        print(f"[ISCE3_Burst] stitched interferograms: {len(made)}")
        return bool(made)

    def _ifg_manifest(self) -> dict:
        import json
        p = self.ifg_dir / "ifg_manifest.json"
        if not p.exists():
            return {}
        try:
            return json.loads(p.read_text())
        except Exception:                                        # noqa: BLE001
            return {}

    def run_filt(self, force: bool = False) -> bool:
        """Multilook, Goldstein-filter and estimate coherence (3.5-3.7).

        Reads the looks from the ifg stage's manifest rather than the config,
        because phase linking already decimated by ``strides``. Taking the
        configured looks again would multilook the stack twice -- silently, and
        only visible as an unexpectedly small raster.
        """
        mf = self._ifg_manifest()
        applied = mf.get("looks_applied")
        if applied:
            lks_y, lks_x = 1, 1
            print(f"[ISCE3_Burst] {mf.get('mode')} mode already applied "
                  f"{applied[0]}x{applied[1]} looks via strides; not re-looking")
        else:
            lks_y = int(getattr(self.config, "azlks", 2))
            lks_x = int(getattr(self.config, "rglks", 4))

        pairs = filter_ifgs(
            self.stitch_dir, self.filt_dir,
            lks_y=lks_y,
            lks_x=lks_x,
            alpha=float(getattr(self.config, "filt_strength", 0.5)),
            coh_window=int(getattr(self.config, "coh_window", 11)),
            max_workers=self._nw, force=force)
        n_in = len(list(self.stitch_dir.glob("*.int.tif")))
        print(f"[ISCE3_Burst] filtered + coherence: {len(pairs)}/{n_in}")
        return bool(pairs) and len(pairs) >= n_in

    def run_unwrap(self, force: bool = False) -> bool:
        """Unwrap with snaphu (notebook 3.8)."""

        t = int(getattr(self.config, "unwrap_tiles", 2))
        mask = self.water_mask_path if self.water_mask_path.exists() else None
        if mask is None:
            logger.warning("ISCE3_Burst: no water mask at %s -- unwrapping over "
                           "water, which can seed errors that leak inland",
                           self.water_mask_path)
        unw = unwrap_ifgs(
            self.filt_dir, self.unwrap_dir,
            nlooks=float(getattr(self.config, "unwrap_nlooks", 8.0)),
            ntiles=(t, t),
            cost=str(getattr(self.config, "unwrap_cost", "smooth")),
            init_method=str(getattr(self.config, "unwrap_init_method", "mcf")),
            mask_file=mask, max_workers=self._nw, force=force)
        n_in = len(list(self.filt_dir.glob("*.int.tif")))
        print(f"[ISCE3_Burst] unwrapped: {len(unw)}/{n_in} -> {self.unwrap_dir}")
        return bool(unw) and len(unw) >= n_in

# ----------------------------------------------------------------------
# dolphin helpers: interferogram stages after ``cslc``
# ----------------------------------------------------------------------
#
# COMPASS stops once every burst is a geocoded SLC (the ``cslc`` stage).
# Everything after that -- pairing, interferogram formation, stitching,
# multilooking, filtering, coherence and unwrapping -- is done here with
# `dolphin <https://github.com/opera-adt/dolphin>`_, the OPERA package behind
# the operational DISP-S1 product, rather than the COMPASS notebook's
# hand-rolled ``utils.py`` helpers. Each of those has a maintained dolphin
# equivalent:
#
#     generate_ifgram_pairs                -> dolphin.interferogram.Network(max_bandwidth=...)
#     form_single_ifgram                   -> dolphin.interferogram.VRTInterferogram
#     stitch_ifgrams                       -> _mosaic_complex
#     multilook_tif                        -> dolphin.utils.take_looks
#     filter_tif                           -> dolphin.goldstein.goldstein
#     generate_phsig_coh_tif               -> dolphin.interferogram.estimate_interferometric_correlations
#     unwrap_single_ifgram                 -> dolphin.unwrap.run (snaphu-py)
#
# Stitching is the one exception, and deliberately so: dolphin's mosaic warps
# through GDAL, which does not honour nodata on complex bands, so one burst's
# zero-fill silently erases another's data. :func:`_mosaic_complex` composites
# by explicit validity mask instead; its docstring carries the measurements.
#
# Imports are deferred into the functions on purpose: dolphin lives in the
# ``isce3`` conda env, not necessarily the one InSARHub is installed into, so
# importing at module scope would break ``import insarhub`` everywhere else.


_BURST_RE = r"^t\d+_\d+_iw\d+$"


def ensure_proj_env() -> None:
    """Point PROJ/GDAL at the running interpreter's data directories.

    Without this, writing a georeferenced raster dies inside GDAL with::

        RuntimeError: PROJ: proj_create_from_database:
                      Open of <env>/share/proj failed

    conda normally sets ``PROJ_DATA``/``GDAL_DATA`` from ``activate.d`` hooks,
    but a stage launched as a bare ``<env>/bin/python`` -- which is how the
    SLURM child jobs and any non-activated subprocess run -- never fires them.
    pyproj carries its own fallback and keeps working, which is why the failure
    shows up only at GDAL write time and looks unrelated to the environment.
    """
    import sys

    prefix = Path(sys.executable).parent.parent
    for var, sub in (("PROJ_DATA", "share/proj"), ("GDAL_DATA", "share/gdal")):
        if os.environ.get(var):
            continue
        cand = prefix / sub
        if cand.is_dir():
            os.environ[var] = str(cand)


def _burst_dirs(root: Path) -> list[Path]:
    """Burst directories under ``root``; empty when it does not exist yet.

    Returning [] rather than raising matters for HPC planning: a stage's job
    count is derived from its INPUT directory, and a full-chain submission asks
    every stage for that count before anything has run. A bare iterdir() turned
    "the previous stage has not produced its output yet" into an opaque
    FileNotFoundError from deep inside the planner.
    """
    import re

    if not root.is_dir():
        return []
    pat = re.compile(_BURST_RE)
    return sorted(d for d in root.iterdir() if d.is_dir() and pat.match(d.name))


def _map(fn, items, max_workers: int) -> list:
    """Thread-pool map that keeps ordering and does not swallow exceptions."""
    if not items:
        return []
    if max_workers <= 1:
        return [fn(i) for i in items]
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        return list(ex.map(fn, items))


def _ionex_dates_on_disk(tec_dir: Path) -> set[str]:
    """``{YYYYMMDD}`` already covered by an IONEX file in ``tec_dir``.

    IONEX filenames encode year + DAY OF YEAR, in two conventions that both
    appear in a multi-year stack because the analysis centres renamed their
    products in 2023::

        jplg2110.23i                            old: <ccc>gDDD0.YYi
        JPL0OPSFIN_20232950000_01D_02H_GIM.INX  new: <CCC>0OPSFIN_YYYYDDD....

    The previous cache check globbed for the calendar date (``*231022*``), which
    matches NEITHER form -- so every run re-downloaded every date and `force`
    made no difference. Decoding the day-of-year is the only way to answer
    "do I already have this date?", and it is centre-agnostic, so a date
    fetched from a fallback centre counts as cached too.
    """
    from datetime import date as _date, timedelta as _td

    out: set[str] = set()
    if not tec_dir.is_dir():
        return out
    for f in tec_dir.iterdir():
        m = (re.match(r"[A-Za-z]{3}\dOPSFIN_(\d{4})(\d{3})\d{4}_", f.name)
             or re.match(r"[a-z]{3}g(\d{3})\d\.(\d{2})i$", f.name))
        if not m:
            continue
        if f.name[3:4].isdigit():                    # new form: YYYY then DDD
            yr, doy = int(m.group(1)), int(m.group(2))
        else:                                        # old form: DDD then YY
            doy, yr = int(m.group(1)), 2000 + int(m.group(2))
        try:
            out.add((_date(yr, 1, 1) + _td(days=doy - 1)).strftime("%Y%m%d"))
        except ValueError:
            continue
    return out


def _intersect_window(uri: str, proj_win: list[float]) -> list[float] | None:
    """Clip a lon/lat ``[ulx, uly, lrx, lry]`` window to a raster's own extent.

    Returns None when the two do not overlap.
    """
    from osgeo import gdal
    from pyproj import Transformer

    ds = gdal.Open(uri)
    gt = ds.GetGeoTransform()
    nx, ny = ds.RasterXSize, ds.RasterYSize
    crs = ds.GetProjection()
    ds = None

    # raster extent -> lon/lat
    xs = (gt[0], gt[0] + gt[1] * nx)
    ys = (gt[3], gt[3] + gt[5] * ny)
    tf = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
    lons, lats = tf.transform([xs[0], xs[1], xs[0], xs[1]],
                              [ys[0], ys[1], ys[1], ys[0]])
    rw, re_ = min(lons), max(lons)
    rs, rn = min(lats), max(lats)

    ulx = max(proj_win[0], rw)
    uly = min(proj_win[1], rn)
    lrx = min(proj_win[2], re_)
    lry = max(proj_win[3], rs)
    if ulx >= lrx or lry >= uly:
        return None
    return [ulx, uly, lrx, lry]


# ----------------------------------------------------------------------
# stage: crop  (notebook 3.1)
# ----------------------------------------------------------------------

def crop_cslc(cslc_dir: Path, out_dir: Path, aoi, *, buffer_deg: float = 0.05,
              pol: str = "VV", max_workers: int = 3,
              force: bool = False, only: str | None = None) -> tuple[int, int]:
    """Cut each CSLC burst down to the AOI, writing ``<burst>/<date>.slc.tif``.

    A COMPASS burst is ~10000 x 2300 px of geocoded complex data; an AOI is
    usually a small part of that. Cropping first means every later stage --
    interferograms, filtering, unwrapping -- moves a fraction of the pixels.

    ``buffer_deg`` pads the AOI so filtering and unwrapping have valid data
    outside the area of interest rather than hitting a nodata edge (the
    notebook uses the same 0.05 degrees).

    Returns ``(written, total)``.
    """
    ensure_proj_env()
    from osgeo import gdal

    gdal.UseExceptions()

    w, s, e, n = (float(x) for x in aoi)
    b = float(buffer_deg)
    # gdal projWin is (ulx, uly, lrx, lry): west/north then east/south
    proj_win = [w - b, n + b, e + b, s - b]

    h5s = [f for f in sorted(cslc_dir.glob("t*_iw*/*/*.h5"))
           if "static_layers" not in f.name]
    if only:
        # One burst per SLURM job. Each CSLC crops independently into its own
        # <burst>/<date>.slc.tif, so restricting to one burst directory is the
        # whole split -- the bursts never read each other.
        h5s = [f for f in h5s if f.parent.parent.name == only]
        if not h5s:
            raise FileNotFoundError(
                f"crop_cslc: burst {only!r} has no CSLC .h5 under {cslc_dir}")
    if not h5s:
        logger.warning("crop_cslc: no CSLC .h5 under %s", cslc_dir)
        return (0, 0)

    tasks = []
    for f in h5s:
        burst_id, date_str = f.parts[-3], f.parts[-2]
        out = out_dir / burst_id / f"{date_str}.slc.tif"
        if out.exists() and not force:
            continue
        tasks.append((f, out))

    def _one(t):
        src, out = t
        out.parent.mkdir(parents=True, exist_ok=True)
        uri = f'NETCDF:"{src}":/data/{pol}'
        tmp = out.with_suffix(".tmp.tif")
        try:
            # Intersect the AOI window with the burst's own extent. A burst is a
            # slanted footprint inside a much larger bounding raster, and an AOI
            # commonly reaches past its edge; GDAL happily honours a projWin
            # beyond the source and pads the difference with nodata, which
            # inflates every downstream raster with empty rows and columns.
            win = _intersect_window(uri, proj_win)
            if win is None:
                logger.warning("crop_cslc: %s does not intersect the AOI", src.name)
                return False
            gdal.Translate(str(tmp), uri, projWin=win,
                           projWinSRS="EPSG:4326", format="GTiff",
                           creationOptions=["COMPRESS=DEFLATE", "TILED=YES"])
            tmp.replace(out)
            return True
        except Exception as exc:                                  # noqa: BLE001
            logger.error("crop_cslc: %s -> %s failed: %s", src.name, out.name, exc)
            tmp.unlink(missing_ok=True)
            return False

    results = _map(_one, tasks, max_workers)
    made = len(list(out_dir.glob("t*_iw*/*.slc.tif")))
    logger.info("crop_cslc: %d cropped this run, %d on disk (%d CSLC inputs)",
                sum(results), made, len(h5s))
    return (made, len(h5s))


# ----------------------------------------------------------------------
# stage: ifg  (notebook 3.2 + 3.3)
# ----------------------------------------------------------------------

_DATE_RE = re.compile(r"(\d{8})")


def _date_of_slc(p: Path) -> str | None:
    """``YYYYMMDD`` from a cropped-SLC filename (``20240102.slc.tif``)."""
    m = _DATE_RE.search(Path(p).name)
    return m.group(1) if m else None


def pair_indexes(slcs: list[Path], pairs) -> list[tuple[int, int]]:
    """Map ``(YYYYMMDD, YYYYMMDD)`` pairs onto positions in ``slcs``.

    dolphin's ``Network(indexes=...)`` wants ``(ref_idx, sec_idx)`` positions in
    ``slc_list``, not dates -- but a pair network is only meaningful as dates
    (``select_pairs`` returns dates precisely because one date owns many
    bursts). This is the translation.

    Pairs naming a date the stack does not contain are dropped with a warning
    rather than raising: a folder's pair file is written from the SEARCH, while
    the SLCs on disk are whatever survived download and geocoding, so a
    legitimate stack can be a subset. Ordering is normalised so the earlier date
    is always the reference, and duplicates collapse -- dolphin would otherwise
    happily form the same interferogram twice.
    """
    pos = {}
    for i, p in enumerate(slcs):
        d = _date_of_slc(p)
        if d is not None:
            pos.setdefault(d, i)

    out: list[tuple[int, int]] = []
    seen: set[tuple[int, int]] = set()
    missing: set[str] = set()
    for pair in pairs or []:
        if len(pair) < 2:
            continue
        a, b = str(pair[0])[:8], str(pair[1])[:8]
        if a not in pos or b not in pos:
            missing.update(d for d in (a, b) if d not in pos)
            continue
        i, j = pos[a], pos[b]
        if i == j:
            continue
        key = (i, j) if i < j else (j, i)
        if key not in seen:
            seen.add(key)
            out.append(key)

    if missing:
        logger.warning("pair_indexes: %d pair-date(s) absent from the cropped "
                       "stack, those pairs skipped: %s",
                       len(missing), ", ".join(sorted(missing)[:8]))
    return sorted(out)


def _network_kwargs(n_connections: int, max_temporal_baseline: float | None,
                    reference_idx: int | None,
                    indexes: list[tuple[int, int]] | None = None) -> dict:
    """Pick the one Network topology option dolphin should use.

    They are mutually exclusive, and dolphin will NOT enforce that:
    ``_make_ifg_pairs`` tests each option with a separate ``if``, so passing
    two makes it emit the UNION of both networks rather than choosing. Exactly
    one key is returned here for that reason.

    Precedence, highest first:

    ``reference_idx``  only ever set by phase linking's ``single_reference``
                       mode, where the estimator's output already IS the
                       single-reference stack; an explicit pair list cannot
                       improve on it and is ignored (with a note) in that case.
    ``indexes``        the user's own pair network, from ``config.pairs``.
    ``max_temporal_baseline`` / ``max_bandwidth``   the rule-based fallbacks.
    """
    if reference_idx is not None:
        if indexes:
            print("[ISCE3_Burst] ignoring the explicit pair network: phase "
                  "linking's single_reference output already is that network. "
                  "Set pl_ifg_network=bandwidth to form your pairs instead.")
        return {"reference_idx": int(reference_idx)}
    if indexes:
        return {"indexes": list(indexes)}
    if max_temporal_baseline:
        return {"max_temporal_baseline": float(max_temporal_baseline)}
    return {"max_bandwidth": int(n_connections)}


def build_network(crop_dir: Path, n_connections: int = 3,
                  max_temporal_baseline: float | None = None,
                  reference_idx: int | None = None,
                  pairs=None) -> list[str]:
    """Pair list as ``YYYYMMDD_YYYYMMDD``, over the dates EVERY burst shares.

    The date list is the INTERSECTION across burst directories, not the first
    burst's. ``--common-bursts-only`` drops bursts missing from some dates, but
    it does not drop a date that is missing a burst -- so when ASF simply has
    no coverage for one burst on one day, the burst stacks end up with
    different date lists.

    That happened on a real stack: ASF had burst 118970 but not 118971 on
    2025-05-20, and 118971 but not 118970 on 2026-04-21. Building from
    ``bursts[0]`` prescribed a pair the other burst could not form and never
    mentioned the one it formed instead, so the two bursts silently built
    DIFFERENT 95-pair networks and ifg_manifest.json described only one of
    them. Downstream stages read that manifest, so two pairs would have
    stitched from a single burst -- half-width products, unflagged.

    Intersecting makes the network formable on every burst by construction:
    the manifest then describes what actually exists, at the cost of dropping
    the dates that were never fully covered anyway.

    ``pairs`` is an explicit ``(YYYYMMDD, YYYYMMDD)`` network -- normally
    ``config.pairs``, as written by ``S1_Burst.select_pairs``. When given it
    replaces the rule-based topology; see :func:`_network_kwargs` for precedence.
    """
    ensure_proj_env()
    from dolphin.interferogram import Network

    bursts = _burst_dirs(crop_dir)
    if not bursts:
        raise FileNotFoundError(f"build_network: no burst dirs under {crop_dir}")

    per_burst = {b.name: {_date_of_slc(f): f for f in b.glob("*.slc.tif")
                          if _date_of_slc(f)} for b in bursts}
    shared = set.intersection(*(set(d) for d in per_burst.values()))
    dropped = {n: sorted(set(d) - shared) for n, d in per_burst.items()}
    for name, miss in dropped.items():
        if miss:
            logger.warning(
                "build_network: %s has %d date(s) no other burst covers, so "
                "they cannot form a full-width interferogram and are excluded "
                "from the network: %s", name, len(miss), ", ".join(miss))
            print(f"[ISCE3_Burst] {name}: dropped {len(miss)} date(s) absent "
                  f"from another burst ({', '.join(miss)})")

    if len(shared) < 2:
        raise ValueError(
            f"build_network: only {len(shared)} date(s) are present in every "
            f"burst ({', '.join(b.name for b in bursts)}); need at least 2. "
            f"Per-burst date counts: "
            f"{ {n: len(d) for n, d in per_burst.items()} }")

    # Any burst's files work now that the dates agree; use the first for paths.
    first = per_burst[bursts[0].name]
    slcs = [first[d] for d in sorted(shared)]

    idx = pair_indexes(slcs, pairs) if pairs else None
    if pairs and not idx:
        raise ValueError(
            f"build_network: none of the {len(pairs)} configured pair(s) match "
            f"the {len(slcs)} date(s) in {bursts[0].name}. The pair file names "
            f"dates this stack does not contain -- re-run select_pairs against "
            f"the downloaded stack, or clear config.pairs to use n_connections.")
    kw = _network_kwargs(n_connections, max_temporal_baseline, reference_idx, idx)
    net = Network(slc_list=slcs, verify_slcs=False, write=False, **kw)
    return [f"{v.ref_date:%Y%m%d}_{v.sec_date:%Y%m%d}" for v in net.ifg_list]


def phase_link_bursts(crop_dir: Path, pl_dir: Path, *,
                      half_window: tuple[int, int] = (7, 14),
                      strides: tuple[int, int] = (2, 4),
                      ministack_size: int = 15, shp_method: str = "glrt",
                      shp_alpha: float = 0.001, use_evd: bool = False,
                      beta: float = 0.0, baseline_lag: int | None = None,
                      max_workers: int = 1,
                      force: bool = False) -> dict[str, Path]:
    """Estimate each burst's phase history from the full SLC covariance.

    The alternative to forming a chosen subset of pairs. Phase linking builds
    the N x N sample covariance for every pixel neighbourhood and solves for the
    phase history explaining all of it, so every pair contributes and there is
    no network to select. ``baseline_lag`` band-limits the covariance if you
    want the StBAS behaviour instead.

    Two details make this drop into the existing chain unchanged:

    * dolphin names the phase-linked SLCs ``<date>.slc.tif``, exactly the
      convention :func:`crop_cslc` writes, so :func:`form_ifgs` runs on this
      directory with no modification -- the post-estimation network stays fully
      user-controlled via ``n_connections``.
    * ``strides`` decimates on output, so it does the job ``take_looks`` does in
      the pairwise path. The ``filt`` stage must therefore NOT multilook again;
      :func:`run_ifg` records this in the manifest so it doesn't.

    ``glrt``/``ks`` need per-pixel amplitude statistics, which dolphin's PS step
    produces; that is run first here rather than being a documented
    prerequisite, because without it the SHP estimator fails deep inside a
    worker with an unrelated-looking ``AxisError``.

    Returns ``{burst_id: temporal_coherence_path}``.
    """
    ensure_proj_env()
    from dolphin import ps
    from dolphin.io import VRTStack
    from dolphin.workflows.sequential import run_wrapped_phase_sequential

    bursts = _burst_dirs(crop_dir)
    if not bursts:
        raise FileNotFoundError(f"phase_link_bursts: no burst dirs under {crop_dir}")

    out: dict[str, Path] = {}
    for bd in bursts:
        slcs = sorted(bd.glob("*.slc.tif"))
        if len(slcs) < 2:
            logger.warning("phase_link_bursts: %s has %d SLC(s), skipping",
                           bd.name, len(slcs))
            continue
        dest = pl_dir / bd.name
        done = sorted(dest.glob("2*.slc.tif"))
        if len(done) == len(slcs) and not force:
            tc = sorted(dest.glob("temporal_coherence_*.tif"))
            if tc:
                out[bd.name] = tc[0]
                logger.info("phase_link_bursts: %s already linked, skipping", bd.name)
                continue
        dest.mkdir(parents=True, exist_ok=True)

        vrt = VRTStack(file_list=slcs, outfile=dest / "slc_stack.vrt")
        amp_mean = dest / "amp_mean.tif"
        amp_disp = dest / "amp_dispersion.tif"
        ps_mask = dest / "ps_pixels.tif"
        if not amp_disp.exists() or force:
            ps.create_ps(reader=vrt, like_filename=vrt.outfile,
                         output_file=ps_mask, output_amp_mean_file=amp_mean,
                         output_amp_dispersion_file=amp_disp)

        res = run_wrapped_phase_sequential(
            slc_vrt_stack=vrt, output_folder=dest,
            ministack_size=int(ministack_size),
            half_window={"y": int(half_window[0]), "x": int(half_window[1])},
            strides={"y": int(strides[0]), "x": int(strides[1])},
            shp_method=str(shp_method), shp_alpha=float(shp_alpha),
            use_evd=bool(use_evd), beta=float(beta),
            baseline_lag=baseline_lag,
            amp_mean_file=amp_mean, amp_dispersion_file=amp_disp,
            ps_mask_file=ps_mask,
            # dolphin's own workflow defaults (PhaseLinkingOptions), NOT the
            # looser ones in run_wrapped_phase_sequential's signature. The two
            # disagree: the function signature is the older low-level API and
            # still says max_num_compressed=100 / write_closure_phase=True,
            # while the configuration dolphin actually ships and OPERA runs
            # says 10 / False. Passing them explicitly keeps this pipeline on
            # the shipped configuration instead of silently inheriting
            # whichever value the function signature happens to carry.
            max_num_compressed=10,
            write_closure_phase=False,
            max_workers=max(1, int(max_workers)),
            disable=True,                      # tqdm option; see note below
        )
        pl_slcs, _crlb, _closure, _comp, temp_coh, _shp, _sim = res
        if temp_coh:
            out[bd.name] = Path(list(temp_coh)[0])
        logger.info("phase_link_bursts: %s -> %d phase-linked SLCs",
                    bd.name, len(list(pl_slcs)))
    return out


def form_ifgs(crop_dir: Path, out_dir: Path, *, n_connections: int = 3,
              max_temporal_baseline: float | None = None,
              reference_idx: int | None = None, pairs=None,
              max_workers: int = 3, force: bool = False) -> list[str]:
    """Form every pair for every burst: ``<burst>/<d1>_<d2>.int.tif``.

    dolphin builds each interferogram as a VRT with a ``mul`` pixel function --
    nothing is computed until read. That is ideal for a chain that stays inside
    dolphin, but the VRT points at absolute source paths and only exists as a
    recipe, so the stack would break if the CSLCs moved. These are materialised
    to real GeoTIFFs, matching what the notebook leaves on disk.

    Returns the pair list.
    """
    ensure_proj_env()
    import numpy as np
    from dolphin import io
    from dolphin.interferogram import Network

    want_pairs = pairs
    pairs = build_network(crop_dir, n_connections, max_temporal_baseline,
                          reference_idx, want_pairs)
    bursts = _burst_dirs(crop_dir)
    logger.info("form_ifgs: %d pairs x %d bursts = %d interferograms",
                len(pairs), len(bursts), len(pairs) * len(bursts))

    tasks = []
    # Restrict every burst to the dates the network was actually built over.
    # Globbing a burst's own files here would re-introduce the divergence
    # build_network just removed: a burst holding a date no other burst covers
    # would form a pair present in no other burst and in no manifest, and
    # stitch would later find one burst for that pair instead of two.
    net_dates = {d for p in pairs for d in p.split("_")}
    for bd in bursts:
        slcs = [f for f in sorted(bd.glob("*.slc.tif"))
                if _date_of_slc(f) in net_dates]
        # Indexes are positions in THIS burst's slc_list. The stack is built
        # with --common-bursts-only so every burst holds the same dates and the
        # mapping is identical, but deriving it per burst keeps a partially
        # geocoded burst from silently pairing the wrong two dates.
        kw = _network_kwargs(n_connections, max_temporal_baseline, reference_idx,
                             pair_indexes(slcs, want_pairs) if want_pairs else None)
        vrt_dir = out_dir / bd.name / ".vrt"
        vrt_dir.mkdir(parents=True, exist_ok=True)
        net = Network(slc_list=slcs, outdir=vrt_dir, verify_slcs=False,
                      write=True, **kw)
        for v in net.ifg_list:
            out = out_dir / bd.name / f"{v.ref_date:%Y%m%d}_{v.sec_date:%Y%m%d}.int.tif"
            if out.exists() and not force:
                continue
            tasks.append((Path(v.path), out))

    def _one(t):
        vrt, out = t
        try:
            arr = io.load_gdal(vrt)
            io.write_arr(arr=arr.astype(np.complex64), output_name=out,
                         driver="GTiff", like_filename=vrt,
                         options=["COMPRESS=DEFLATE", "TILED=YES"])
            return True
        except Exception as exc:                                  # noqa: BLE001
            logger.error("form_ifgs: %s failed: %s", out.name, exc)
            return False

    ok = _map(_one, tasks, max_workers)
    logger.info("form_ifgs: %d/%d formed this run", sum(ok), len(tasks))
    return pairs


# ----------------------------------------------------------------------
# stage: stitch  (notebook 3.4)
# ----------------------------------------------------------------------

def _mosaic_complex(files: list[Path], out_path: Path, aoi=None) -> Path:
    """Composite co-gridded complex bursts, keeping the first valid pixel.

    Written by hand rather than handed to ``dolphin.stitching.merge_images``
    because that path silently drops all but the last burst here. It mosaics by
    warping through GDAL, and a burst's empty region is plain ``0+0j`` rather
    than a declared nodata value -- so the last file's zeros overwrite the
    earlier files' real data. Declaring ``nodata=0`` on the rasters and passing
    ``in_nodata=0`` were both measured to make no difference: GDAL does not
    honour nodata for complex bands. On the Hawaii A124 test stack that turned a
    1103 km2 two-burst union into 583 km2, one burst's worth, with no warning.

    ``dolphin`` is not at fault for its own purpose -- DISP-S1 stitches
    unwrapped and phase-linked rasters, which are real-valued and carry proper
    nodata. Complex burst mosaicking is outside what it stitches.

    All inputs share the CSLC grid (same CRS and posting, cropped from a common
    window), so this pastes by integer offset instead of resampling, which also
    avoids interpolating across the phase discontinuity at a burst edge.
    """
    import numpy as np
    from osgeo import gdal, osr
    from pyproj import Transformer

    gdal.UseExceptions()

    metas = []
    for f in files:
        ds = gdal.Open(str(f))
        metas.append({"path": f, "gt": ds.GetGeoTransform(),
                      "nx": ds.RasterXSize, "ny": ds.RasterYSize,
                      "proj": ds.GetProjection()})
        ds = None

    gt0 = metas[0]["gt"]
    dx, dy = gt0[1], gt0[5]
    for m in metas[1:]:
        if abs(m["gt"][1] - dx) > 1e-6 or abs(m["gt"][5] - dy) > 1e-6:
            raise ValueError(
                f"_mosaic_complex: {m['path'].name} has posting "
                f"{m['gt'][1]}/{m['gt'][5]}, expected {dx}/{dy}")

    # union extent, then clip to the AOI
    x_min = min(m["gt"][0] for m in metas)
    x_max = max(m["gt"][0] + m["gt"][1] * m["nx"] for m in metas)
    y_max = max(m["gt"][3] for m in metas)
    y_min = min(m["gt"][3] + m["gt"][5] * m["ny"] for m in metas)

    if aoi is not None and len(aoi) == 4:
        tf = Transformer.from_crs("EPSG:4326", metas[0]["proj"], always_xy=True)
        w, s, e, n = (float(v) for v in aoi)
        xs, ys = tf.transform([w, e, w, e], [s, n, n, s])
        x_min = max(x_min, min(xs)); x_max = min(x_max, max(xs))
        y_min = max(y_min, min(ys)); y_max = min(y_max, max(ys))

    nx = int(round((x_max - x_min) / abs(dx)))
    ny = int(round((y_max - y_min) / abs(dy)))
    if nx <= 0 or ny <= 0:
        raise ValueError(f"_mosaic_complex: empty output extent for {out_path.name}")
    out_gt = (x_min, abs(dx), 0.0, y_max, 0.0, -abs(dy))

    acc = np.zeros((ny, nx), dtype=np.complex64)
    filled = np.zeros((ny, nx), dtype=bool)

    for m in metas:
        ds = gdal.Open(str(m["path"]))
        src = ds.GetRasterBand(1).ReadAsArray()
        ds = None
        # integer offset of this burst's origin within the output grid
        cx = (m["gt"][0] - x_min) / abs(dx)
        ry = (y_max - m["gt"][3]) / abs(dy)
        if abs(cx - round(cx)) > 0.01 or abs(ry - round(ry)) > 0.01:
            logger.warning("_mosaic_complex: %s is off-grid by (%.2f, %.2f) px; "
                           "pasting to the nearest pixel",
                           m["path"].name, cx - round(cx), ry - round(ry))
        c0, r0 = int(round(cx)), int(round(ry))

        # overlap of the source with the output window, in both index spaces
        sr0, sc0 = max(0, -r0), max(0, -c0)
        dr0, dc0 = max(0, r0), max(0, c0)
        h = min(m["ny"] - sr0, ny - dr0)
        w_ = min(m["nx"] - sc0, nx - dc0)
        if h <= 0 or w_ <= 0:
            continue

        sub = src[sr0:sr0 + h, sc0:sc0 + w_]
        dst = (slice(dr0, dr0 + h), slice(dc0, dc0 + w_))
        valid = np.isfinite(sub) & (np.abs(sub) > 1e-6)
        take = valid & ~filled[dst]
        acc[dst][take] = sub[take]
        filled[dst] |= take

    out_path.parent.mkdir(parents=True, exist_ok=True)
    srs = osr.SpatialReference()
    srs.ImportFromWkt(metas[0]["proj"])
    drv = gdal.GetDriverByName("GTiff")
    ods = drv.Create(str(out_path), nx, ny, 1, gdal.GDT_CFloat32,
                     options=["COMPRESS=DEFLATE", "TILED=YES"])
    ods.SetGeoTransform(out_gt)
    ods.SetProjection(srs.ExportToWkt())
    ods.GetRasterBand(1).WriteArray(acc)
    ods = None
    return out_path


def _mosaic_real(files: list[Path], out_path: Path, aoi=None,
                 nodata: float = 0.0) -> Path:
    """Composite co-gridded real-valued burst rasters, first valid pixel wins.

    The real-valued twin of :func:`_mosaic_complex`, used for the static
    geometry layers. Kept separate rather than folded in because "valid" is a
    different test: a complex interferogram's empty region is ``0+0j`` and
    ``|z| > 1e-6`` identifies data, whereas a LOS component is a signed number
    that legitimately passes near zero, so validity here is ``isfinite`` plus an
    explicit nodata comparison.
    """
    import numpy as np
    from osgeo import gdal, osr
    from pyproj import Transformer

    gdal.UseExceptions()

    metas = []
    for f in files:
        ds = gdal.Open(str(f))
        metas.append({"path": f, "gt": ds.GetGeoTransform(),
                      "nx": ds.RasterXSize, "ny": ds.RasterYSize,
                      "proj": ds.GetProjection()})
        ds = None

    gt0 = metas[0]["gt"]
    dx, dy = gt0[1], gt0[5]
    x_min = min(m["gt"][0] for m in metas)
    x_max = max(m["gt"][0] + m["gt"][1] * m["nx"] for m in metas)
    y_max = max(m["gt"][3] for m in metas)
    y_min = min(m["gt"][3] + m["gt"][5] * m["ny"] for m in metas)

    if aoi is not None and len(aoi) == 4:
        tf = Transformer.from_crs("EPSG:4326", metas[0]["proj"], always_xy=True)
        w, s, e, n = (float(v) for v in aoi)
        xs, ys = tf.transform([w, e, w, e], [s, n, n, s])
        x_min = max(x_min, min(xs)); x_max = min(x_max, max(xs))
        y_min = max(y_min, min(ys)); y_max = min(y_max, max(ys))

    nx = int(round((x_max - x_min) / abs(dx)))
    ny = int(round((y_max - y_min) / abs(dy)))
    acc = np.full((ny, nx), np.nan, dtype=np.float32)
    filled = np.zeros((ny, nx), dtype=bool)

    for m in metas:
        ds = gdal.Open(str(m["path"]))
        src = ds.GetRasterBand(1).ReadAsArray().astype(np.float32)
        ds = None
        c0 = int(round((m["gt"][0] - x_min) / abs(dx)))
        r0 = int(round((y_max - m["gt"][3]) / abs(dy)))
        sr0, sc0 = max(0, -r0), max(0, -c0)
        dr0, dc0 = max(0, r0), max(0, c0)
        h = min(m["ny"] - sr0, ny - dr0)
        w_ = min(m["nx"] - sc0, nx - dc0)
        if h <= 0 or w_ <= 0:
            continue
        sub = src[sr0:sr0 + h, sc0:sc0 + w_]
        dst = (slice(dr0, dr0 + h), slice(dc0, dc0 + w_))
        valid = np.isfinite(sub) & (sub != nodata)
        take = valid & ~filled[dst]
        acc[dst][take] = sub[take]
        filled[dst] |= take

    out_path.parent.mkdir(parents=True, exist_ok=True)
    srs = osr.SpatialReference()
    srs.ImportFromWkt(metas[0]["proj"])
    ods = gdal.GetDriverByName("GTiff").Create(
        str(out_path), nx, ny, 1, gdal.GDT_Float32,
        options=["COMPRESS=DEFLATE", "TILED=YES"])
    ods.SetGeoTransform((x_min, abs(dx), 0.0, y_max, 0.0, -abs(dy)))
    ods.SetProjection(srs.ExportToWkt())
    ods.GetRasterBand(1).WriteArray(acc)
    ods.GetRasterBand(1).SetNoDataValue(float("nan"))
    ods = None
    return out_path


#: static-layer datasets COMPASS writes, and the file stem each becomes
_STATIC_LAYERS = {
    "los_east": "los_east",
    "los_north": "los_north",
    "local_incidence_angle": "local_incidence_angle",
    "layover_shadow_mask": "layover_shadow_mask",
}


def build_los_layers(cslc_dir: Path, out_dir: Path, aoi, like_raster: Path,
                     buffer_deg: float = 0.05) -> dict[str, Path]:
    """Put the per-burst static geometry layers onto the stack's own grid.

    COMPASS writes static layers per burst at CSLC resolution. SBAS needs them
    on the same grid as the unwrapped interferograms, so each layer is cropped,
    mosaicked across bursts, then resampled onto ``like_raster``.

    Also derives ``los_up``. COMPASS stores only the east and north components
    of the ground-to-satellite unit vector; the up component is recovered from
    the unit-length constraint, ``u_up = sqrt(1 - u_e^2 - u_n^2)``, taking the
    positive root because the vector points from the ground up to the satellite.

    With all three, LOS displacement projects to a ground component exactly::

        d_los = d_e*u_e + d_n*u_n + d_u*u_u

    rather than via the ``d_los / cos(theta)`` shortcut, which silently assumes
    the horizontal motion is zero.
    """
    ensure_proj_env()
    import numpy as np
    from osgeo import gdal

    gdal.UseExceptions()

    statics = sorted(cslc_dir.glob("t*_iw*/*/static_layers_*.h5"))
    if not statics:
        raise FileNotFoundError(
            f"build_los_layers: no static_layers_*.h5 under {cslc_dir}; "
            "run the 'static' stage first")

    w, s, e, n = (float(x) for x in aoi)
    b = float(buffer_deg)
    proj_win = [w - b, n + b, e + b, s - b]
    tmp = out_dir / ".burst"
    tmp.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    made: dict[str, Path] = {}
    for dset, stem in _STATIC_LAYERS.items():
        pieces = []
        for h5 in statics:
            burst_id = h5.parts[-3]
            uri = f'NETCDF:"{h5}":/data/{dset}'
            try:
                win = _intersect_window(uri, proj_win)
            except Exception as exc:                              # noqa: BLE001
                logger.warning("build_los_layers: %s has no /data/%s (%s)",
                               h5.name, dset, exc)
                break
            if win is None:
                continue
            cut = tmp / f"{stem}_{burst_id}.tif"
            gdal.Translate(str(cut), uri, projWin=win, projWinSRS="EPSG:4326",
                           format="GTiff")
            pieces.append(cut)
        if not pieces:
            logger.warning("build_los_layers: nothing to mosaic for %s", dset)
            continue

        full = tmp / f"{stem}_mosaic.tif"
        _mosaic_real(pieces, full, aoi)

        # onto the interferogram grid. Nearest for the layover/shadow mask --
        # its values are category codes, so averaging them invents categories
        # that do not exist.
        alg = "near" if dset == "layover_shadow_mask" else "average"
        ref = gdal.Open(str(like_raster))
        gt = ref.GetGeoTransform()
        nx_, ny_ = ref.RasterXSize, ref.RasterYSize
        bounds = (gt[0], gt[3] + gt[5] * ny_, gt[0] + gt[1] * nx_, gt[3])
        proj = ref.GetProjection()
        ref = None
        dst = out_dir / f"{stem}.tif"
        gdal.Warp(str(dst), str(full), outputBounds=bounds, width=nx_, height=ny_,
                  dstSRS=proj, resampleAlg=alg, format="GTiff",
                  creationOptions=["COMPRESS=DEFLATE", "TILED=YES"])
        made[stem] = dst

    if "los_east" in made and "los_north" in made:
        from dolphin import io
        ue = io.load_gdal(made["los_east"]).astype(np.float64)
        un = io.load_gdal(made["los_north"]).astype(np.float64)
        with np.errstate(invalid="ignore"):
            uu = np.sqrt(np.clip(1.0 - ue ** 2 - un ** 2, 0.0, 1.0))
        uu[~np.isfinite(ue) | ~np.isfinite(un)] = np.nan
        dst = out_dir / "los_up.tif"
        io.write_arr(arr=uu.astype(np.float32), output_name=dst, driver="GTiff",
                     like_filename=made["los_east"], nodata=float("nan"),
                     options=["COMPRESS=DEFLATE", "TILED=YES"])
        made["los_up"] = dst
        logger.info("build_los_layers: LOS unit vector median "
                    "(E=%.3f, N=%.3f, U=%.3f)", np.nanmedian(ue),
                    np.nanmedian(un), np.nanmedian(uu))

    return made


def stitch_ifgs(ifg_dir: Path, out_dir: Path, aoi=None,
                max_workers: int = 3, only: str | None = None) -> list[Path]:
    """Merge the per-burst interferograms of each pair into one raster.

    ``group_by_date`` keys on every date it finds in the filename, so
    ``20240705_20240717.int.tif`` groups by the ``(ref, sec)`` tuple and all
    bursts of one pair -- and only those -- land in a single output.

    ``only`` restricts the run to one pair (``"20240705_20240717"``), which is
    what lets each pair become its own SLURM job. Mosaicking a pair reads only
    that pair's bursts and writes only its own output, so the split is safe --
    no shared state, nothing to reduce afterwards.
    """
    ensure_proj_env()
    from opera_utils import group_by_date

    files = sorted(ifg_dir.glob("t*_iw*/*.int.tif"))
    if not files:
        raise FileNotFoundError(f"stitch_ifgs: no per-burst .int.tif under {ifg_dir}")
    out_dir.mkdir(parents=True, exist_ok=True)

    groups = group_by_date(files)
    tasks = [(sorted(flist),
              out_dir / f"{k[0]:%Y%m%d}_{k[1]:%Y%m%d}.int.tif")
             for k, flist in sorted(groups.items())]
    if only:
        tasks = [t for t in tasks if t[1].name.startswith(only)]
        if not tasks:
            raise FileNotFoundError(
                f"stitch_ifgs: pair {only!r} has no per-burst interferograms "
                f"under {ifg_dir}")

    def _one(t):
        flist, out = t
        try:
            return _mosaic_complex(flist, out, aoi)
        except Exception as exc:                                  # noqa: BLE001
            logger.error("stitch_ifgs: %s failed: %s", out.name, exc)
            return None

    _map(_one, tasks, max_workers)
    made = sorted(out_dir.glob("*.int.tif"))
    logger.info("stitch_ifgs: %d per-burst (%d bursts) -> %d stitched",
                len(files), len(files) // max(1, len(tasks)), len(made))
    return made


# ----------------------------------------------------------------------
# stage: filt  (notebook 3.5 + 3.6 + 3.7)
# ----------------------------------------------------------------------

def filter_ifgs(stitch_dir: Path, out_dir: Path, *, lks_y: int = 2, lks_x: int = 4,
                alpha: float = 0.5, coh_window: int = 11, max_workers: int = 3,
                force: bool = False, only: str | None = None
                ) -> list[tuple[Path, Path]]:
    """Multilook, Goldstein-filter, then estimate coherence.

    One pass per pair rather than the notebook's three, so a full-size complex
    raster is not written and re-read twice in between.

    Coherence is estimated *after* filtering, matching the notebook. That makes
    it comparable to ISCE's ``filt_fine.cor`` (a filtered-phase quality measure),
    not to a raw normalised cross-correlation -- filtering raises it, so it must
    not be read as the raw interferometric correlation.

    Returns ``[(ifg, cor), ...]``.
    """
    ensure_proj_env()
    import numpy as np
    from dolphin import io
    from dolphin.goldstein import goldstein
    from dolphin.interferogram import estimate_interferometric_correlations
    from dolphin.utils import take_looks

    files = sorted(stitch_dir.glob("*.int.tif"))
    if only:
        # One pair per SLURM job. Filtering is strictly per-raster -- multilook,
        # Goldstein and coherence all read one interferogram and write one pair
        # of outputs -- so restricting the glob is the whole change.
        files = [f for f in files if f.name.startswith(only)]
        if not files:
            raise FileNotFoundError(
                f"filter_ifgs: pair {only!r} not stitched in {stitch_dir}")
    if not files:
        raise FileNotFoundError(f"filter_ifgs: no stitched .int.tif in {stitch_dir}")
    out_dir.mkdir(parents=True, exist_ok=True)

    todo = [f for f in files if force or not (out_dir / f.name).exists()]

    def _one(f: Path):
        out = out_dir / f.name
        try:
            arr = io.load_gdal(f)
            ml = take_looks(arr, int(lks_y), int(lks_x), func_type="nanmean")
            filt = goldstein(ml.astype(np.complex64), alpha=float(alpha))

            # multilooking changes the pixel size; the geotransform must follow
            # or every downstream product is georeferenced wrong
            gt = list(io.get_raster_gt(f))
            gt[1] *= int(lks_x)
            gt[5] *= int(lks_y)
            io.write_arr(arr=filt.astype(np.complex64), output_name=out,
                         driver="GTiff", like_filename=f, shape=filt.shape,
                         dtype=np.complex64, geotransform=tuple(gt),
                         projection=io.get_raster_crs(f).to_wkt(),
                         options=["COMPRESS=DEFLATE", "TILED=YES"])
            return True
        except Exception as exc:                                  # noqa: BLE001
            logger.error("filter_ifgs: %s failed: %s", f.name, exc)
            return False

    ok = _map(_one, todo, max_workers)
    logger.info("filter_ifgs: %d/%d multilooked+filtered", sum(ok), len(todo))

    made = sorted(out_dir.glob("*.int.tif"))
    if only:
        # Restrict the coherence step to THIS job's pair as well. Globbing the
        # output directory here was a real bug: `only` bounded the multilook /
        # Goldstein loop above but not this reduce, so every concurrent job
        # recomputed coherence for every file present and wrote the same
        # .cor.tif paths at the same time. GDAL surfaced it as
        #     TIFFResetField: <pair>.int.cor.tif: Can not read TIFF directory entry
        # on 6 of the first 8 jobs -- corrupt output, not a data problem.
        made = [f for f in made if f.name.startswith(only)]
    cors = estimate_interferometric_correlations(
        made, window_size=(int(coh_window), int(coh_window)),
        out_driver="GTiff", num_workers=max_workers)
    logger.info("filter_ifgs: %d coherence rasters", len(cors))
    return list(zip(made, [Path(c) for c in cors]))


# ----------------------------------------------------------------------
# stage: unwrap  (notebook 3.8)
# ----------------------------------------------------------------------

def water_mask_on_grid(wbd_path: Path, like_raster: Path,
                       out_path: Path) -> Path | None:
    """Warp the NASADEM ``.wbd`` water mask onto an interferogram's grid.

    Two conversions are needed and both are easy to get backwards.

    *Format*: ``swbd_nasadem.wbd`` is a headerless uint8 raster -- GDAL cannot
    open it at all ("not recognized as being in a supported file format"). Its
    geotransform lives in a sidecar ``.json`` written by sardem, so the array is
    read with numpy and wrapped in an in-memory GDAL dataset before warping.

    *Polarity*: the ``.wbd`` marks **water** with nonzero, while dolphin's mask
    convention is **1 = valid pixel, 0 = invalid**. The mask is therefore
    inverted on write. Getting this backwards silently unwraps the ocean and
    masks the land -- it does not raise, it just returns nonsense.

    Returns the written mask, or None if the ``.wbd``/``.json`` pair is missing.
    """
    ensure_proj_env()
    import json

    import numpy as np
    from osgeo import gdal, osr

    gdal.UseExceptions()

    wbd_path = Path(wbd_path)
    meta_path = wbd_path.with_suffix(".json")
    if not wbd_path.exists() or not meta_path.exists():
        logger.warning("water_mask_on_grid: need both %s and %s", wbd_path, meta_path)
        return None

    meta = json.loads(meta_path.read_text())
    wbd = np.fromfile(wbd_path, dtype=np.uint8).reshape(meta["height"], meta["width"])

    mem = gdal.GetDriverByName("MEM")
    wgs = osr.SpatialReference()
    wgs.ImportFromEPSG(4326)
    src = mem.Create("", meta["width"], meta["height"], 1, gdal.GDT_Byte)
    src.SetGeoTransform((meta["lon0"], meta["dlon"], 0, meta["lat0"], 0, meta["dlat"]))
    src.SetProjection(wgs.ExportToWkt())
    src.GetRasterBand(1).WriteArray(wbd)

    ref = gdal.Open(str(like_raster))
    dst = mem.Create("", ref.RasterXSize, ref.RasterYSize, 1, gdal.GDT_Byte)
    dst.SetGeoTransform(ref.GetGeoTransform())
    dst.SetProjection(ref.GetProjection())
    gdal.ReprojectImage(src, dst, wgs.ExportToWkt(), ref.GetProjection(),
                        gdal.GRA_NearestNeighbour)
    warped = dst.GetRasterBand(1).ReadAsArray()
    src = dst = ref = None

    valid = (warped == 0).astype(np.uint8)      # water -> 0 (invalid), land -> 1
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out = gdal.GetDriverByName("GTiff").Create(
        str(out_path), valid.shape[1], valid.shape[0], 1, gdal.GDT_Byte)
    ref2 = gdal.Open(str(like_raster))
    out.SetGeoTransform(ref2.GetGeoTransform())
    out.SetProjection(ref2.GetProjection())
    out.GetRasterBand(1).WriteArray(valid)
    out = ref2 = None

    logger.info("water_mask_on_grid: %s -- %.1f%% water masked out",
                out_path.name, 100.0 * (1.0 - valid.mean()))
    return out_path

def unwrap_ifgs(filt_dir: Path, out_dir: Path, *, nlooks: float = 8,
                ntiles: tuple[int, int] = (2, 2), tile_overlap: int = 200,
                cost: str = "smooth", init_method: str = "mcf",
                mask_file: Path | None = None, max_workers: int = 3,
                force: bool = False, only: str | None = None,
                prepare_only: bool = False) -> list[Path]:
    """snaphu-unwrap every filtered pair, writing ``<d1>_<d2>.unw.tif``.

    Also writes ``.unw.conncomp.tif`` connected-component labels. Those matter
    for the SBAS step that consumes this stack: a pair whose largest component
    covers only part of the scene has an unwrapping error somewhere, and time
    series inversion should down-weight or drop it.
    """
    ensure_proj_env()
    from dolphin import unwrap
    from dolphin.workflows.config import SnaphuOptions, UnwrapMethod, UnwrapOptions

    ifgs = sorted(filt_dir.glob("*.int.tif"))
    if not ifgs:
        raise FileNotFoundError(f"unwrap_ifgs: no filtered .int.tif in {filt_dir}")

    cors, paired = [], []
    for f in ifgs:
        c = f.with_suffix(".cor.tif")            # <stem>.int.tif -> <stem>.int.cor.tif
        if not c.exists():
            logger.error("unwrap_ifgs: no coherence for %s (expected %s)",
                         f.name, c.name)
            continue
        paired.append(f)
        cors.append(c)
    if not paired:
        raise FileNotFoundError(
            f"unwrap_ifgs: no ifg/coherence pairs in {filt_dir}; run 'filt' first")

    # unwrap.run writes straight into output_path and will not create it
    out_dir.mkdir(parents=True, exist_ok=True)

    # A raw .wbd is not a GDAL raster and uses the opposite polarity to
    # dolphin's mask; convert it onto this stack's grid first. All filtered
    # interferograms share that grid, so the first one defines it.
    #
    # This is the ONE piece of shared state in an otherwise per-pair stage, so
    # it must not run inside per-pair jobs: N of them would race to write the
    # same water_mask.tif, and a job reading it mid-write gets a truncated
    # raster. `prepare_only` builds it once (its own single-job phase); the
    # per-pair jobs then find it on disk and reuse it.
    grid_mask = out_dir / "water_mask.tif"
    if mask_file is not None:
        mask_file = Path(mask_file)
        if mask_file.suffix == ".wbd":
            if grid_mask.exists() and not force:
                mask_file = grid_mask
            else:
                mask_file = water_mask_on_grid(mask_file, paired[0], grid_mask)

    if prepare_only:
        print(f"[ISCE3_Burst] unwrap prologue done: "
              f"{'water mask ' + grid_mask.name if mask_file else 'no water mask'}")
        return []

    if only:
        keep = [i for i, f in enumerate(paired) if f.name.startswith(only)]
        if not keep:
            raise FileNotFoundError(
                f"unwrap_ifgs: pair {only!r} has no filtered interferogram in "
                f"{filt_dir}")
        paired = [paired[i] for i in keep]
        cors = [cors[i] for i in keep]

    opts = UnwrapOptions(
        unwrap_method=UnwrapMethod.SNAPHU,
        run_goldstein=False,        # the filt stage already applied Goldstein
        n_parallel_jobs=max(1, int(max_workers)),
        snaphu_options=SnaphuOptions(
            ntiles=tuple(ntiles), tile_overlap=(tile_overlap, tile_overlap),
            init_method=init_method, cost=cost),
    )
    kw = {}
    if mask_file and Path(mask_file).exists():
        kw["mask_filename"] = str(mask_file)

    unw, ccl = unwrap.run(ifg_filenames=paired, cor_filenames=cors,
                          output_path=out_dir, unwrap_options=opts,
                          nlooks=float(nlooks), overwrite=bool(force), **kw)
    logger.info("unwrap_ifgs: %d unwrapped, %d conncomp", len(unw), len(ccl))
    return [Path(u) for u in unw]
