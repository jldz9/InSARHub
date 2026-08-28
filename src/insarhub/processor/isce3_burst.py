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
COMPASS front-end (as in the stack notebook), then dolphin's own engine::

    dem     Copernicus DEM (sardem) + water mask                section 1.3
    tec     IONEX global ionosphere maps, one per date           section 1.4
    cslc    s1_geocode_stack.py -> runconfigs -> s1_cslc.py      section 2
    static  static layers (layover/shadow, incidence, LOS)       section 2.4
    ifg     PS + phase-link + interferograms per burst           dolphin wrapped_phase.run
    stitch  mosaic bursts + correlation                          dolphin stitching_bursts.run
    unwrap  snaphu + connected components                        dolphin unwrapping.run
    los     LOS geometry onto the stack grid

``dem``/``tec``/``cslc``/``static`` drive COMPASS and ISCE3 directly. Everything
from ``ifg`` onwards delegates each stage to the exact function dolphin's own
``displacement.run`` calls -- so the wrapped/unwrapped stack and the time series
are byte-identical to ``dolphin run`` by construction. Output lands in dolphin's
native layout: ``<burst_id>/linked_phase/`` + ``interferograms/`` per burst,
then ``interferograms/``, ``unwrapped/`` and ``timeseries/`` at the workdir root.

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

    #: stage order. The dolphin engine (ifg/stitch/unwrap) delegates each stage
    #: to the exact function dolphin's own displacement.run calls, so output is
    #: byte-identical to `dolphin run` by construction.
    #: `los` is LAST: it needs the stitched interferogram grid to resample onto.
    STAGES = ("dem", "tec", "cslc", "static", "ifg", "stitch", "unwrap", "los")
    _IMPLEMENTED = ("dem", "tec", "cslc", "static", "ifg", "stitch", "unwrap", "los")

    # dolphin drives ifg..unwrap; compass drives cslc/static/tec. Both are
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
            "ifg":    "PS + phase-link + interferograms, ONE JOB PER BURST "
                      "(dolphin wrapped_phase.run)",
            "stitch": "mosaic bursts + estimate correlation, ONE JOB "
                      "(dolphin stitching_bursts.run)",
            "unwrap": "snaphu, ONE JOB (dolphin unwrapping.run)",
            "los":    "LOS/incidence geometry onto the stack grid -- runs LAST",
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
        # Sized for phase_link: holds a ministack's worth of SLC covariance in
        # memory rather than multiplying two rasters.
        "ifg":     {"time": "08:00:00", "cpus_per_task": 8, "mem": "64G"},
        "stitch":  {"time": "01:00:00", "cpus_per_task": 2, "mem": "16G"},
        "unwrap":  {"time": "04:00:00", "cpus_per_task": 4, "mem": "24G"},
        "los":     {"time": "01:00:00", "cpus_per_task": 4, "mem": "16G"},
    }

    # ------------------------------------------------------------------
    # paths
    # ------------------------------------------------------------------

    @property
    def slc_dir(self) -> Path:
        # lowercase 'slc': S1_Burst writes .SAFE and their .EOF orbits together
        # there, the same layout S1_SLC produces. The GUI/config exposes this as
        # `burst_path` (the assembled .SAFE stack); `slc_dir` is kept as a
        # back-compat alias that still wins if set explicitly.
        v = getattr(self.config, "slc_dir", None)
        if v:
            return Path(v).expanduser()
        return self._p("burst_path", "slc")

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

    # dolphin-engine products, in dolphin's native layout (identical to what
    # `dolphin run` writes): per-burst dirs under <workdir>/<burst_id>/ from the
    # ifg stage, stitched products under interferograms/ + unwrapped/.
    @property
    def stitch_dir(self) -> Path:
        """Stitched interferograms + correlation + temporal coherence."""
        return self._p("stitch_dir", "interferograms")

    @property
    def unwrap_dir(self) -> Path:
        return self._p("unwrap_dir", "unwrapped")

    def burst_dirs(self) -> list[Path]:
        """Per-burst output directories written by the ifg stage."""
        import re as _re
        if not self.workdir.is_dir():
            return []
        pat = re.compile(_BURST_RE)
        return sorted(d for d in self.workdir.iterdir()
                      if d.is_dir() and pat.match(d.name))

    def quality_file(self) -> Path | None:
        """Stitched temporal coherence (dolphin's quality raster), by glob."""
        for p in sorted(self.stitch_dir.glob("temporal_coherence*.tif")):
            return p
        return None

    @property
    def water_mask_path(self) -> Path:
        """ESA WorldCover water mask (GeoTIFF, 1=land/0=water) by ``run_dem``.

        dolphin's mask convention -- consumed directly by the unwrap stage and by
        ``displacement.run``'s ``mask_file``, both of which warp it onto the
        interferogram grid, so no ``.wbd``/sidecar handling is needed.
        """
        return self.dem_path.parent / "water_mask.tif"

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
            wbd = self.water_mask_path
            if wbd.exists() and not force:
                print(f"[ISCE3_Burst] water mask already present: {wbd.name}")
            elif not self._download_water_mask((w, s, e, n), wbd):
                # Non-fatal: the mask only blanks water before unwrapping.
                logger.warning("ISCE3_Burst: water mask unavailable; unwrapping "
                               "will proceed without masking open water")
        return True

    def _download_water_mask(self, bbox, out_path: Path) -> bool:
        """ESA WorldCover water mask (1=land/0=water) to ``out_path``.

        sardem's old ``NASA_WATER`` source (SRTM Water Body Data on
        ``e4ftl01.cr.usgs.gov``) was migrated off that host and 404s for every
        tile, so it silently produced an all-land mask. ESA WorldCover is on
        open AWS (no Earthdata/account) and marks permanent water as class 80.
        """
        try:
            from insarhub.utils.tool import download_worldcover_water_mask
        except Exception as exc:                                 # noqa: BLE001
            logger.warning("ISCE3_Burst: no water-mask downloader available (%s)",
                           exc)
            return False
        try:
            return bool(download_worldcover_water_mask(bbox, out_path))
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
        # COMPASS's own download_ionex hits CDDIS unauthenticated, which now
        # requires Earthdata Login -- it silently saves a URS login page instead
        # of the map. Use InSARHub's Earthdata-authenticated resolver, which
        # reuses COMPASS's filename logic so the files still land where the cslc
        # runconfigs expect them. (Importing get_ionex_filename here also asserts
        # COMPASS is present, as before.)
        try:
            from insarhub.utils.ionex import (download_ionex_earthdata,
                                              have_earthdata_creds)
            from compass.utils.iono import get_ionex_filename  # noqa: F401
        except ImportError as exc:                               # noqa: BLE001
            logger.error("ISCE3_Burst: TEC download needs COMPASS "
                         "(compass.utils.iono) plus insarhub.utils.ionex (%s).", exc)
            return False

        if not have_earthdata_creds():
            logger.error(
                "ISCE3_Burst: no Earthdata credentials in ~/.netrc "
                "(machine urs.earthdata.nasa.gov). CDDIS IONEX downloads require "
                "Earthdata Login -- run the S1 downloader once to store them, or "
                "add the entry by hand. Skipping the TEC stage; bursts will be "
                "geocoded without an ionospheric correction.")
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
                    got = download_ionex_earthdata(d, str(tec), sol_code=c)
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
        # TEC is non-fatal: the cslc stage geocodes WITHOUT the ionospheric
        # correction when no IONEX map exists (a small phase offset, not a
        # broken stack). Returning `ok > 0` here made an all-missing TEC stage
        # (e.g. CDDIS behind Earthdata auth) abort the whole pipeline.
        return True

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
        # Geocode extent: under process_full_extent this is the full burst
        # footprint (from the .SAFE files); otherwise config.AOI. Going through
        # _aoi() keeps the cslc --bbox, the DEM footprint and every later stage
        # measuring the SAME extent.
        try:
            aoi = self._aoi()
        except Exception as exc:                                 # noqa: BLE001
            logger.error("ISCE3_Burst: cannot determine a geocode extent for "
                         "--bbox (%s)", exc)
            return False
        if not aoi or len(aoi) != 4:
            logger.error("ISCE3_Burst: could not resolve a (W,S,E,N) extent for --bbox")
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
    # dolphin engine: ifg / stitch / unwrap
    # ------------------------------------------------------------------

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
            # Prefer geocoded bursts once they exist (exact output extent);
            # before then, measure the burst footprint straight from the .SAFE
            # files so dem/cslc actually cover the full extent rather than
            # silently shrinking to the AOI.
            box = self._aoi_from_bursts() or self._full_extent_from_safes()
            if box:
                print(f"[ISCE3_Burst] full downloaded extent: "
                      f"{['%.4f' % v for v in box]}")
                return box
            # Neither geocoded bursts nor readable .SAFE footprints -- fall back
            # to the AOI so the stage can still run rather than hard-failing.
            print(f"[ISCE3_Burst] process_full_extent set but could not measure "
                  f"the burst footprint (no geocoded bursts, no readable .SAFE) "
                  f"-- falling back to AOI for this stage")

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

        # The geocoded CSLC .h5 (written by `cslc`, before any crop) are the full
        # burst footprints on a common UTM grid. Do NOT fall back to a bare
        # cslc_dir *.slc.tif glob: the only *.slc.tif under cslc/ are COMPASS's
        # radar-coordinate SCRATCH intermediates with no map CRS.
        h5s = sorted(p for p in self.cslc_dir.glob("t*_iw*/*/*.h5")
                     if "static_layers" not in p.name)
        if not h5s:
            return None

        wgs = osr.SpatialReference()
        wgs.ImportFromEPSG(4326)
        wgs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)

        w = s = e = n = None
        for h5 in h5s:
            # A partially-written or corrupt raster (e.g. a crop that died
            # mid-write, or a stray leftover) must NOT abort extent measurement
            # -- skip it and use the rest. The ``if ds is None`` guard below only
            # catches failures when gdal.UseExceptions() is OFF; other ISCE3
            # stages turn it ON process-globally, and then gdal.Open RAISES
            # "OGR Error: Corrupt data" on a bad file instead of returning None,
            # which previously escaped here and failed the whole crop stage.
            try:
                ds = gdal.Open(f'NETCDF:"{h5}":/data/VV')
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
            except Exception as exc:                              # noqa: BLE001
                logger.warning("ISCE3_Burst: skipping unreadable CSLC %s while "
                               "measuring the full extent (%s)", h5.name, exc)
                continue
            finally:
                ds = None

        return None if w is None else [w, s, e, n]

    def _full_extent_from_safes(self) -> list[float] | None:
        """(W, S, E, N) covering every downloaded burst, from the .SAFE burst
        footprints -- the PRE-geocode source for ``process_full_extent``.

        ``_aoi_from_bursts`` can only measure geocoded output, which does not
        exist until after cslc, so before then it returns None and the extent
        would otherwise fall back to the (smaller) AOI -- silently defeating
        process_full_extent for the two stages that define the footprint (dem,
        cslc). A burst stack is the SAME burst IDs on every date, so one SAFE's
        burst borders already describe the whole stack's ground footprint: this
        reads the first readable SAFE, unions its bursts across all subswaths,
        and caches the result. Borders come from the annotation geolocation
        grid, so no orbit file is needed.
        """
        cached = getattr(self, "_full_extent_cache", None)
        if cached is not None:
            return cached or None            # a miss is cached as [] -> None
        box = None
        try:
            import warnings
            import s1reader
            from shapely.ops import unary_union
            for safe in sorted(self.slc_dir.glob("*.SAFE")):
                polys = []
                for sw in (1, 2, 3):
                    try:
                        with warnings.catch_warnings():
                            warnings.simplefilter("ignore")
                            polys += [b.border for b in
                                      s1reader.load_bursts(str(safe), None, sw)]
                    except Exception:                            # noqa: BLE001
                        continue
                if polys:
                    w, s, e, n = unary_union(polys).bounds
                    box = [float(w), float(s), float(e), float(n)]
                    break
        except Exception as exc:                                  # noqa: BLE001
            logger.warning("ISCE3_Burst: could not read burst footprints from "
                           "the .SAFE files for process_full_extent (%s)", exc)
        self._full_extent_cache = box or []
        return box

    @property
    def geom_dir(self) -> Path:
        """LOS/geometry rasters on the interferogram grid, for SBAS."""
        return self.workdir / "geometry"

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
        like = sorted(self.stitch_dir.glob("*.int.tif"))
        if not like:
            print("[ISCE3_Burst] static layers generated, but no stitched "
                  "interferogram to define the output grid -- run 'stitch' to "
                  "get los_*.tif on the stack grid")
            return True

        made = build_los_layers(
            self.cslc_dir, self.geom_dir, self._aoi(), like[0],
            buffer_deg=0.05)
        print(f"[ISCE3_Burst] geometry on stack grid: "
              f"{sorted(made)} -> {self.geom_dir}")
        return "los_up" in made

    def run_los(self, force: bool = False) -> bool:
        """Put the static geometry layers on the interferogram grid.

        Separate from `static` because it needs a stitched interferogram to
        resample onto -- see the STAGES comment. Produces los_east/los_north/
        los_up and incidence in geometry/, which the analyzer needs to convert
        line-of-sight displacement into a ground component.
        """
        return self._run_static_los()

    def _cslc_list(self) -> list[str]:
        """All geocoded CSLC ``.h5`` (excluding static layers), sorted."""
        cslc = sorted(self.cslc_dir.glob("t*_iw*/*/t*_iw*_2*.h5"))
        cslc = [str(p) for p in cslc if "static_layers" not in p.name]
        if not cslc:
            raise FileNotFoundError(
                f"ISCE3_Burst: no CSLC HDF5 under {self.cslc_dir}; "
                "run the 'cslc' stage first")
        return cslc

    #: HDF5 subdataset dolphin reads the complex SLC from. COMPASS CSLC stores
    #: it at ``/data/VV``; a NISAR-GSLC subclass overrides this with the NISAR
    #: GSLC grid path (``/science/LSAR/GSLC/grids/frequencyA/<pol>``).
    _subdataset: str = "/data/VV"

    def _dolphin_cfg(self):
        """Build dolphin's ``DisplacementWorkflow`` from this processor's config.

        The single source of truth for every dolphin-engine stage (ifg/stitch/
        unwrap). ``process_full_extent=True`` leaves ``bounds`` unset so dolphin
        uses the burst union (exactly ``dolphin run``); a specific AOI clips the
        stitched output via ``bounds``.
        """
        from dolphin.workflows.config import DisplacementWorkflow

        c = self.config
        out_opts: dict = {"strides": {"x": int(getattr(c, "rglks", 4)),
                                      "y": int(getattr(c, "azlks", 2))}}
        if not bool(getattr(c, "process_full_extent", False)):
            aoi = self._aoi()
            if aoi and len(aoi) == 4:
                out_opts["bounds"] = [float(x) for x in aoi]
                out_opts["bounds_epsg"] = 4326
        wmask = self.water_mask_path if self.water_mask_path.exists() else None
        # Full phase-linking option set, so every pl_* config field is honoured
        # (matches the native `dolphin config` defaults for the unset ones).
        phase_linking: dict = {
            "ministack_size": int(getattr(c, "pl_ministack_size", 15)),
            "half_window": {"x": int(getattr(c, "pl_half_window_x", 14)),
                            "y": int(getattr(c, "pl_half_window_y", 7))},
            "shp_method": str(getattr(c, "pl_shp_method", "glrt")),
            "shp_alpha": float(getattr(c, "pl_shp_alpha", 0.001)),
            "use_evd": bool(getattr(c, "pl_use_evd", False)),
            "beta": float(getattr(c, "pl_beta", 0.0)),
            "baseline_lag": getattr(c, "pl_baseline_lag", None),
        }
        # network after phase linking: 'single_reference' -> output_reference_idx,
        # 'bandwidth' (default, matches dolphin) -> max_bandwidth = n_connections;
        # max_temporal_baseline (days) wins over n_connections when set.
        net_opts: dict = {}
        if str(getattr(c, "pl_ifg_network", "bandwidth")).lower() == "single_reference":
            net_opts["reference_idx"] = 0
        elif getattr(c, "max_temporal_baseline", None):
            net_opts["max_temporal_baseline"] = float(c.max_temporal_baseline)
        else:
            net_opts["max_bandwidth"] = int(getattr(c, "n_connections", 3))
        t = int(getattr(c, "unwrap_tiles", 1))
        unwrap_opts: dict = {
            "run_unwrap": True,
            "unwrap_method": str(getattr(c, "unw_method", "snaphu")),
            "n_parallel_jobs": max(1, int(self._nw)),
            "snaphu_options": {
                "ntiles": (t, t),
                "tile_overlap": (0, 0),
                "init_method": str(getattr(c, "unwrap_init_method", "mcf")),
                "cost": str(getattr(c, "unwrap_cost", "smooth")),
            },
        }
        return DisplacementWorkflow(
            cslc_file_list=self._cslc_list(),
            input_options={"subdataset": self._subdataset},
            work_directory=str(self.workdir),
            mask_file=str(wmask) if wmask else None,
            output_options=out_opts,
            phase_linking=phase_linking,
            interferogram_network=net_opts,
            unwrap_options=unwrap_opts,
            worker_settings={
                "threads_per_worker": max(1, int(getattr(c, "num_threads", 4))),
                "n_parallel_bursts": 1,
            },
        )

    def _wrapped_phase_manifest_path(self) -> Path:
        return self.workdir / "interferograms" / "wrapped_phase_manifest.json"

    def run_ifg(self, force: bool = False) -> bool:
        """PS + phase-link + interferograms per burst (dolphin ``wrapped_phase.run``).

        One ``wrapped_phase.run`` call per burst, exactly as dolphin's
        ``displacement.run`` does. Outputs land in dolphin's native per-burst
        layout (``<workdir>/<burst_id>/linked_phase/``, ``interferograms/``,
        ``PS/``); the per-burst file lists are recorded to a manifest that the
        ``stitch`` stage consumes.
        """
        ensure_proj_env()
        ensure_gdal_cli()          # dolphin's stitch shells out to gdal_merge.py
        from dolphin.workflows import wrapped_phase
        from dolphin.workflows._utils import _create_burst_cfg, _remove_dir_if_empty
        from opera_utils import group_by_burst

        cfg = self._dolphin_cfg()
        grouped = group_by_burst(cfg.cslc_file_list)
        if not grouped:
            raise FileNotFoundError(
                f"ISCE3_Burst: no bursts from {len(cfg.cslc_file_list)} CSLC(s)")

        empty = {b: [] for b in grouped}
        ifg_files: list[str] = []
        temp_coh: list[str] = []
        ps: list[str] = []
        crlb: list[str] = []
        closure: list[str] = []
        amp_disp: list[str] = []
        shp: list[str] = []
        sim: list[str] = []
        for burst in sorted(grouped):
            burst_cfg = _create_burst_cfg(cfg, burst, grouped, empty, empty, empty)
            # Create the per-burst work dir (and subdirs) first -- dolphin's
            # displacement.run does this before wrapped_phase.run, and it also
            # removes the empty timeseries/unwrapped dirs re-grouping leaves.
            burst_cfg.create_dir_tree()
            _remove_dir_if_empty(burst_cfg.timeseries_options._directory)
            _remove_dir_if_empty(burst_cfg.unwrap_options._directory)
            print(f"[ISCE3_Burst] ifg: {burst} ({len(grouped[burst])} CSLC) "
                  f"-> wrapped_phase.run")
            out = wrapped_phase.run(burst_cfg, max_workers=self._nw)
            ifg_files += [str(p) for p in out.ifg_file_list]
            temp_coh += [str(p) for p in out.temp_coh_files]
            ps.append(str(out.ps_looked_file))
            crlb += [str(p) for p in out.crlb_files]
            closure += [str(p) for p in out.closure_phase_files]
            amp_disp.append(str(out.amp_disp_looked_file))
            shp += [str(p) for p in out.shp_count_files]
            sim += [str(p) for p in out.similarity_files]

        import json as _json
        manifest = self._wrapped_phase_manifest_path()
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(_json.dumps({
            "ifg_file_list": ifg_files,
            "temp_coh_file_list": temp_coh,
            "ps_file_list": ps,
            "crlb_file_list": crlb,
            "closure_phase_file_list": closure,
            "amp_dispersion_list": amp_disp,
            "shp_count_file_list": shp,
            "similarity_file_list": sim,
        }, indent=1))
        print(f"[ISCE3_Burst] ifg: {len(ifg_files)} per-burst interferogram(s) "
              f"over {len(grouped)} burst(s)")
        return bool(ifg_files)

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
            if stage == "ifg":
                bursts = self.burst_dirs()
                done = sum(1 for b in bursts if (b / "interferograms").is_dir())
                return (done, len(bursts))
            if stage == "stitch":
                n = len(self._pairs())
                return (len(list(self.stitch_dir.glob("*.int.tif"))), n)
            if stage == "unwrap":
                n = len(self._pairs())
                return (len(list(self.unwrap_dir.glob("*.unw.tif"))), n)
        except Exception as exc:                                 # noqa: BLE001
            logger.debug("stage_progress(%s) failed: %s", stage, exc)
        return (0, 0)

    def _pairs(self) -> list[str]:
        """Unique ``YYYYMMDD_YYYYMMDD`` pairs, from the ifg stage's manifest."""
        import json as _json
        m = self._wrapped_phase_manifest_path()
        if not m.exists():
            return []
        try:
            data = _json.loads(m.read_text())
        except Exception:                                        # noqa: BLE001
            return []
        pairs = set()
        for f in data.get("ifg_file_list", []):
            mm = re.search(r"(\d{8})_(\d{8})", Path(f).name)
            if mm:
                pairs.add(f"{mm.group(1)}_{mm.group(2)}")
        return sorted(pairs)

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
            if stage == "ifg":
                return [(b.name, (b / "interferograms").is_dir())
                        for b in self.burst_dirs()]
            if stage in ("stitch", "unwrap"):
                d, suf = {"stitch": (self.stitch_dir, ".int.tif"),
                          "unwrap": (self.unwrap_dir, ".unw.tif")}[stage]
                return [(p, (d / f"{p}{suf}").exists()) for p in self._pairs()]
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

        ==========  ==========================================================
        dem/tec     one job each -- network-bound, minutes, nothing to split
        cslc        one job per ``run_*.sh`` COMPASS already wrote. These are
                    real shell scripts, so no re-entry is needed and this is
                    exactly ISCE2's shape. The long pole: N dates x M bursts.
        static      map over bursts, then a 1-job reduce that mosaics the LOS
                    layers onto the interferogram grid
        ifg         one job -- dolphin's wrapped_phase.run loops all bursts
        stitch      one job -- dolphin's stitching_bursts.run is monolithic
        unwrap      one job -- dolphin's unwrapping.run is monolithic
        los         one job
        ==========  ==========================================================

        ``cslc`` needs its runconfigs to exist before the units can be listed, so
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

        if stage in ("ifg", "stitch", "unwrap"):
            # dolphin's wrapped_phase.run / stitching_bursts.run / unwrapping.run
            # are each a single monolithic call -- no safe per-burst/pair split.
            return [("", [self._reentry(stage)])]

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

        Deferring is NOT a failure. `static` sits before `stitch` in the stage
        order, but the grid it must resample onto is defined by the stitched
        interferograms -- so on a first pass through the chain there is nothing
        to resample onto yet. run_static() has always handled that by returning
        success with a note; this mirrors it. Re-run `static` once `stitch` is
        done to get los_*.tif.
        """
        like = sorted(self.stitch_dir.glob("*.int.tif"))
        if not like:
            print("[ISCE3_Burst] static layers generated, but no stitched "
                  "interferogram to define the output grid -- re-run 'static' "
                  "after 'stitch' to get los_*.tif on the stack grid")
            return True
        made = build_los_layers(
            self.cslc_dir, self.geom_dir, self._aoi(), like[0], buffer_deg=0.05)
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
        runner = getattr(self, f"run_{stage}", None)
        if runner is None:
            raise ValueError(f"ISCE3_Burst: no unit runner for stage {stage!r}")
        return bool(runner())

    def run_stitch(self, force: bool = False) -> bool:
        """Mosaic per-burst ifgs + estimate correlation (dolphin ``stitching_bursts.run``)."""
        ensure_proj_env()
        ensure_gdal_cli()          # dolphin's stitch shells out to gdal_merge.py
        import json as _json
        from dolphin.workflows import stitching_bursts

        manifest = self._wrapped_phase_manifest_path()
        if not manifest.exists():
            raise FileNotFoundError(
                f"ISCE3_Burst: {manifest} missing -- run the 'ifg' stage first")
        m = _json.loads(manifest.read_text())
        cfg = self._dolphin_cfg()
        stitched = stitching_bursts.run(
            ifg_file_list=[Path(p) for p in m["ifg_file_list"]],
            temp_coh_file_list=[Path(p) for p in m["temp_coh_file_list"]],
            ps_file_list=[Path(p) for p in m["ps_file_list"]],
            crlb_file_list=[Path(p) for p in m["crlb_file_list"]],
            closure_phase_file_list=[Path(p) for p in m["closure_phase_file_list"]],
            amp_dispersion_list=[Path(p) for p in m["amp_dispersion_list"]],
            shp_count_file_list=[Path(p) for p in m["shp_count_file_list"]],
            similarity_file_list=[Path(p) for p in m["similarity_file_list"]],
            stitched_ifg_dir=cfg.interferogram_network._directory,
            output_options=cfg.output_options,
            file_date_fmt=cfg.input_options.cslc_date_fmt,
            corr_window_size=(11, 11),
            num_workers=self._nw,
        )
        print(f"[ISCE3_Burst] stitch: {len(stitched.ifg_paths)} stitched ifgs, "
              f"{len(stitched.interferometric_corr_paths)} correlations -> "
              f"{cfg.interferogram_network._directory}")
        return (bool(stitched.ifg_paths)
                and len(stitched.interferometric_corr_paths) >= len(stitched.ifg_paths))

    def run_unwrap(self, force: bool = False) -> bool:
        """Unwrap stitched ifgs with snaphu (dolphin ``unwrapping.run``)."""
        ensure_proj_env()
        from dolphin.workflows import unwrapping

        cfg = self._dolphin_cfg()
        stitched_dir = cfg.interferogram_network._directory
        ifg_files = sorted(stitched_dir.glob("*.int.tif"))
        cor_files = sorted(stitched_dir.glob("*.int.cor.tif"))
        if not ifg_files:
            raise FileNotFoundError(
                f"ISCE3_Burst: no stitched .int.tif under {stitched_dir}; "
                "run the 'stitch' stage first")
        mask = self.water_mask_path if self.water_mask_path.exists() else None
        if mask is None:
            logger.warning("ISCE3_Burst: no water mask at %s -- unwrapping over "
                           "water, which can seed errors that leak inland",
                           self.water_mask_path)
        unwrapped, conncomp = unwrapping.run(
            ifg_file_list=ifg_files,
            cor_file_list=cor_files,
            nlooks=self._unwrap_nlooks(),
            unwrap_options=cfg.unwrap_options,
            temporal_coherence_filename=self.quality_file(),
            similarity_filename=None,
            mask_file=str(mask) if mask else None,
        )
        print(f"[ISCE3_Burst] unwrap: {len(unwrapped)} unwrapped, "
              f"{len(conncomp)} conncomp -> {cfg.unwrap_options._directory}")
        return len(unwrapped) >= len(ifg_files)

    def _unwrap_nlooks(self) -> float:
        """Effective looks handed to snaphu, derived exactly as dolphin does.

        dolphin's displacement workflow computes ``nlooks = (2*hw_y+1)*(2*hw_x+1)``
        (15*29 = 435 at the defaults) -- the SHP window *is* the adaptive
        multilook. A configured ``unwrap_nlooks`` overrides it.
        """
        v = getattr(self.config, "unwrap_nlooks", None)
        if v:
            return float(v)
        return float(
            (2 * int(getattr(self.config, "pl_half_window_y", 7)) + 1)
            * (2 * int(getattr(self.config, "pl_half_window_x", 14)) + 1))

# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------
#
# The dolphin engine (ifg/stitch/unwrap) delegates to dolphin's own
# displacement.run sub-functions, so there are no hand-rolled interferogram
# helpers left. What remains below is shared infrastructure: the PROJ/GDAL env
# fix, the gdal_merge.py CLI shim dolphin's stitch shells out to, IONEX date
# decoding, and the LOS geometry builder used by the `los`/`static` stage.
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


def ensure_gdal_cli() -> None:
    """Guarantee ``gdal_merge.py`` / ``gdal_calc.py`` are runnable.

    dolphin's stitching shells out to ``gdal_merge.py`` via subprocess (GDAL's
    nodata read crashes on complex bands in-process, so dolphin warps + calls the
    CLI). Some conda gdal builds -- including this env's -- ship libgdal without
    those console scripts, so make sure they exist in the interpreter's bin dir
    (thin wrappers onto ``osgeo_utils``) and that the dir is on PATH. Runs in
    every process that stitches, including the bare ``<env>/bin/python`` SLURM
    child jobs that never fired conda's activate hooks.
    """
    import sys
    import stat

    bindir = Path(sys.executable).parent
    parts = os.environ.get("PATH", "").split(os.pathsep)
    if str(bindir) not in parts:
        os.environ["PATH"] = str(bindir) + os.pathsep + os.environ.get("PATH", "")
    for mod in ("gdal_merge", "gdal_calc"):
        script = bindir / f"{mod}.py"
        if script.exists():
            continue
        try:
            script.write_text(
                f"#!{sys.executable}\n"
                f"import sys\n"
                f"from osgeo_utils.{mod} import main\n"
                f"sys.exit(main(sys.argv))\n")
            script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IRUSR
                         | stat.S_IWUSR)
        except Exception as exc:                                  # noqa: BLE001
            logger.warning("ensure_gdal_cli: could not create %s: %s", script, exc)


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

    from insarhub.utils.ionex import is_valid_ionex

    out: set[str] = set()
    if not tec_dir.is_dir():
        return out
    for f in tec_dir.iterdir():
        m = (re.match(r"[A-Za-z]{3}\dOPSFIN_(\d{4})(\d{3})\d{4}_", f.name)
             or re.match(r"[a-z]{3}g(\d{3})\d\.(\d{2})i$", f.name))
        if not m:
            continue
        # A matching name is not enough: an unauthenticated CDDIS fetch saves a
        # URS login PAGE under the right filename. Only count a date as cached
        # when the bytes are a real, uncompressed IONEX map -- otherwise the
        # stage would skip a date it never actually downloaded.
        if not is_valid_ionex(f):
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


def _mosaic_real(files: list[Path], out_path: Path, aoi=None,
                 nodata: float = 0.0, drop_degenerate: bool = False) -> Path:
    """Composite co-gridded real-valued burst rasters, first valid pixel wins.

    Used for the static geometry layers (build_los_layers). "valid" is
    ``isfinite`` plus an explicit nodata comparison, because a LOS component is
    a signed number that legitimately passes near zero.

    ``drop_degenerate`` (temporal-coherence mosaic only): treat coherence == 1.0
    as invalid. Phase linking emits a perfect-coherence artifact along each
    burst's edge (a window with a single/near-single sample). Where two bursts
    overlap, first-valid-wins would let burst A's degenerate edge overwrite
    burst B's real interior, so the stitched coherence -- and every mask derived
    from it -- carries a bright line across the seam. Skipping coherence == 1.0
    lets the burst with real coherence win the overlap instead. A pixel where
    both bursts are degenerate stays 1.0 (nothing better to fall back to).
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
    # priority of the value currently held at each pixel (drop_degenerate only)
    best = np.full((ny, nx), -1.0, dtype=np.float32)

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
        if drop_degenerate:
            # Quality mosaic: keep, per pixel, the HIGHEST real coherence across
            # the bursts that cover it, not the first one. In the burst overlap
            # each burst is at its tapered azimuth edge, so first-valid-wins can
            # take burst A's low/degenerate value while burst B has a good one
            # right there -- which draws the seam line. Ranking by coherence
            # (coh == 1.0 demoted below any real value, so it wins only where
            # nothing real covers) picks the good burst and the overlap ends up
            # as clean as the surrounding scene. ``best`` holds the priority of
            # whatever currently occupies each pixel.
            prio = np.where(valid & (sub < 0.999999), sub,
                            np.where(valid, 1e-4, -1.0)).astype(np.float32)
            take = valid & (prio > best[dst])
            acc[dst][take] = sub[take]
            best[dst][take] = prio[take]
        else:
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


